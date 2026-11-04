"""Launcher services shared by the current interface."""

import json
import queue
import socket
import threading
import time
from pathlib import Path

from credential_store import redact_credentials


def check_internet_connection(timeout=2.0):
    """Probe connectivity independently of the selected account type."""
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=timeout):
            return True
    except OSError:
        return False


def read_game_output(process, log_file, emit_lines, batch_size=20, flush_interval=0.1, sensitive_values=()):
    """Batch game output without delaying quiet streams until their next line."""
    if process.stdout is None:
        return
    messages = queue.Queue(maxsize=batch_size * 10)
    finished = object()
    stopped = threading.Event()

    def enqueue(value):
        while not stopped.is_set():
            try:
                messages.put(value, timeout=flush_interval)
                return
            except queue.Full:
                continue

    def read_lines():
        try:
            for line in process.stdout:
                if stopped.is_set():
                    break
                enqueue(line)
        except (OSError, ValueError):
            pass
        finally:
            enqueue(finished)

    reader = threading.Thread(target=read_lines, daemon=True)
    reader.start()
    pending = []
    deadline = None

    def flush():
        if not pending:
            return
        lines = pending.copy()
        pending.clear()
        if log_file is not None:
            try:
                log_file.writelines(lines)
                log_file.flush()
            except (OSError, ValueError):
                pass
        emit_lines(lines)

    try:
        while True:
            timeout = max(0.0, deadline - time.monotonic()) if deadline else None
            try:
                item = messages.get(timeout=timeout)
            except queue.Empty:
                flush()
                deadline = None
                continue
            if item is finished:
                flush()
                break
            pending.append(redact_credentials(item, sensitive_values))
            if deadline is None:
                deadline = time.monotonic() + flush_interval
            if len(pending) >= batch_size:
                flush()
                deadline = None
    finally:
        stopped.set()
        reader.join(timeout=1.0)


def sync_local_installations():
    """Add new local profiles while preserving existing installation settings."""
    import storage
    from launcher import (
        discover_local_instances,
        get_external_installations,
        get_installed_versions,
        get_local_version_catalog,
        import_external_installations,
        minecraft_directory,
    )

    installations = storage.load_installations()
    external = get_external_installations()
    external_ids = {str(profile["id"]) for profile in external}
    handled = storage.load_handled_external_profile_ids().intersection(external_ids)
    installed = get_installed_versions(force_refresh=True)
    catalog, _ = get_local_version_catalog()
    installations, handled, imported = import_external_installations(
        installations, handled, installed, external, catalog
    )
    installations, discovered = discover_local_instances(
        minecraft_directory, installations, installed, catalog
    )
    changed = imported or discovered
    if changed and not storage.save_installations(installations, handled):
        raise OSError("Could not save imported Minecraft installations.")

    profiles_file = Path(minecraft_directory) / "launcher_profiles.json"
    try:
        document = json.loads(profiles_file.read_text(encoding="utf-8"))
        profiles = document.get("profiles") if isinstance(document, dict) else None
        if isinstance(profiles, dict):
            storage.save_cached_launcher_profiles(profiles)
    except (OSError, ValueError):
        pass
    return changed


def installation_sources_stamp(mc_dir):
    """Watch profile files and instance metadata without rescanning their contents."""
    root = Path(mc_dir)
    instances = root / "instances"
    paths = [root / "launcher_profiles.json", instances]
    try:
        for folder in sorted(instances.iterdir()):
            if folder.is_dir():
                paths.append(folder)
                paths.extend(folder / name for name in (
                    "instance.json", "profile.json", "minecraft_instance.json", "logs/latest.log"
                ))
    except OSError:
        pass
    stamp = []
    for path in paths:
        try:
            stat = path.stat()
            stamp.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamp.append((str(path), None, None))
    return tuple(stamp)
