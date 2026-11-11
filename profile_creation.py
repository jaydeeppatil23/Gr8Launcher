"""Create profiles with exclusive instance folders and preserve existing data."""

import os
import json
from pathlib import Path
import re
import shutil
import threading
from typing import Callable
import uuid


_CREATE_LOCK = threading.RLock()
PACK_PENDING_MARKER = ".gr8launcher-pack-installing"


def instance_folder_name(folder_name, default_name):
    """Use the same single directory name for suggestions, checks and creation."""
    raw = str(folder_name or "").strip()
    if raw and ("/" in raw or "\\" in raw):
        raise ValueError("Enter a folder name without a directory path.")
    name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", raw or str(default_name or "").strip())
    name = name.rstrip(".")
    if not name:
        raise ValueError("Enter a valid instance folder name other than '.' or '..'.")
    # Windows treats trailing dots and device names as aliases, rather than
    # ordinary directory names. Check the exact name mkdir will receive.
    devices = {"CON", "PRN", "AUX", "NUL"}
    devices.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10))
    if os.name == "nt" and name.split(".", 1)[0].upper() in devices:
        raise ValueError(f"'{name}' is reserved by Windows. Choose another folder name.")
    return name


def folder_is_referenced(folder, profiles, minecraft_directory, *, exclude_id=None):
    """Include explicit paths and older profiles that store only a folder name."""
    target = str(Path(folder).resolve()).casefold()
    root = Path(minecraft_directory).resolve()
    for profile in profiles:
        if exclude_id is not None and profile.get("id") == exclude_id:
            continue
        game_dir = str(profile.get("game_directory") or "").strip()
        if not game_dir and profile.get("is_instance") and profile.get("instance_name"):
            game_dir = str(root / "instances" / str(profile["instance_name"]))
        if game_dir and str(Path(game_dir).resolve()).casefold() == target:
            return True
    return False


def new_instance_directory(minecraft_directory, folder_name, default_name, profiles):
    """Resolve a fresh destination, including the fallback for an empty field."""
    name = instance_folder_name(folder_name, default_name)
    root = (Path(minecraft_directory) / "instances").resolve()
    folder = root / name
    if os.path.lexists(folder) or folder_is_referenced(folder, profiles, minecraft_directory):
        raise FileExistsError(
            f"An instance with folder '{name}' already exists. Choose a different folder name."
        )
    if folder.resolve().parent != root:
        raise ValueError("The instance folder must be inside the instances directory.")
    return folder


def save_new_profile(profile, minecraft_directory, folder_name, storage):
    """Reserve a new folder atomically before saving or selecting the profile.

    An existing directory is never adopted. If saving fails, only the empty
    directory created by this call may be removed; its contents are never deleted.
    """
    with _CREATE_LOCK:
        profiles = storage.load_installations()
        if any(item.get("id") == profile.get("id") for item in profiles):
            raise ValueError("A profile with this ID already exists.")
        record = dict(profile)
        created_folder = None
        if record.get("is_instance"):
            folder = new_instance_directory(
                minecraft_directory, folder_name, record.get("name"), profiles,
            )
            folder.parent.mkdir(parents=True, exist_ok=True)
            try:
                folder.mkdir(exist_ok=False)
            except FileExistsError as error:
                raise FileExistsError(
                    f"An instance with folder '{folder.name}' already exists. Choose a different folder name."
                ) from error
            created_folder = folder
            record["instance_name"] = folder.name
            record["game_directory"] = str(folder)
        else:
            record["instance_name"] = ""
            record["game_directory"] = str(Path(minecraft_directory).resolve())

        try:
            if not storage.save_installations([*profiles, record]):
                raise OSError("Could not save the profile. Please try again.")
        except Exception:
            if created_folder is not None:
                try:
                    created_folder.rmdir()
                except OSError:
                    # Another process may already have put files here. Keep them.
                    pass
            raise
        return record


def _write_transaction(path: Path, document: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
    temporary.replace(path)


def _owned_pack_folder(folder: Path, root: Path, token: str) -> bool:
    if folder.is_symlink() or folder.resolve().parent != root.resolve():
        return False
    try:
        marker = folder / PACK_PENDING_MARKER
        return not marker.is_symlink() and marker.read_text(encoding="ascii") == token
    except (OSError, UnicodeError):
        return False


def save_prepared_profile(profile: dict, minecraft_directory: str | Path, folder_name: str,
                          storage, prepared: Path, *, before_commit: Callable[[], None] = lambda: None) -> dict:
    """Publish a verified pack into an exclusively reserved, journaled instance."""
    from mod_services import _directory_lock

    mc_root = Path(minecraft_directory).resolve()
    stage_root = (mc_root / "gr8launcher" / "pack-staging").resolve()
    prepared = Path(prepared).resolve()
    if prepared.name != "game" or prepared.parent.parent != stage_root or not prepared.is_dir():
        raise ValueError("The prepared pack must be in the launcher's pack staging folder.")
    instances = (mc_root / "instances").resolve()
    with _CREATE_LOCK, _directory_lock(instances):
        profiles = storage.load_installations()
        if any(item.get("id") == profile.get("id") for item in profiles):
            raise ValueError("A profile with this ID already exists.")
        folder = new_instance_directory(mc_root, folder_name, profile.get("name"), profiles)
        record = {**profile, "is_instance": True, "instance_name": folder.name, "game_directory": str(folder)}
        token = uuid.uuid4().hex
        journal_root = mc_root / "gr8launcher" / "pack-transactions"
        journal_root.mkdir(parents=True, exist_ok=True)
        journal = journal_root / (token + ".json")
        _write_transaction(journal, {"format": 1, "token": token, "target": str(folder),
                                     "staging": str(prepared), "profile": record})
        created = False
        saved = False
        try:
            before_commit()
            folder.parent.mkdir(parents=True, exist_ok=True)
            folder.mkdir(exist_ok=False)
            created = True
            (folder / PACK_PENDING_MARKER).write_text(token, encoding="ascii")
            for source in prepared.iterdir():
                before_commit()
                if source.is_symlink() or source.name == PACK_PENDING_MARKER:
                    raise ValueError("The prepared pack contains an unsafe entry.")
                target = folder / source.name
                if os.path.lexists(target):
                    raise FileExistsError("The new instance folder changed while installing the pack.")
                source.rename(target)
            before_commit()
            # Merge with the latest saved list after the filesystem work.
            profiles = storage.load_installations()
            if any(item.get("id") == record["id"] for item in profiles):
                raise ValueError("A profile with this ID already exists.")
            if folder_is_referenced(folder, profiles, mc_root):
                raise ValueError("Another profile adopted this instance folder during installation.")
            if not storage.save_installations([*profiles, record]):
                raise OSError("Could not save the modpack instance. Please try again.")
            saved = True
        finally:
            if saved:
                try:
                    (folder / PACK_PENDING_MARKER).unlink(missing_ok=True)
                    journal.unlink(missing_ok=True)
                except OSError:
                    # Recovery can finish cleanup after the durable profile save.
                    pass
            elif created:
                if (not folder_is_referenced(folder, storage.load_installations(), mc_root)
                        and _owned_pack_folder(folder, instances, token)):
                    try:
                        shutil.rmtree(folder)
                        journal.unlink(missing_ok=True)
                    except OSError:
                        pass
                else:
                    try:
                        folder.rmdir()  # Only a still-empty reservation can lack its marker.
                        journal.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                journal.unlink(missing_ok=True)
        return record


def recover_prepared_profiles(minecraft_directory: str | Path, storage) -> None:
    """Finish committed pack installs or discard only their owned, unpublished folders."""
    from mod_services import _directory_lock

    mc_root = Path(minecraft_directory).resolve()
    journal_root = mc_root / "gr8launcher" / "pack-transactions"
    if not journal_root.is_dir() or journal_root.is_symlink():
        return
    instances = (mc_root / "instances").resolve()
    stage_root = (mc_root / "gr8launcher" / "pack-staging").resolve()
    with _CREATE_LOCK, _directory_lock(instances):
        for journal in journal_root.glob("*.json"):
            try:
                if journal.is_symlink() or journal.stat().st_size > 1024 * 1024:
                    continue
                data = json.loads(journal.read_text(encoding="utf-8"))
                token = data.get("token", "")
                if data.get("format") != 1 or not re.fullmatch(r"[a-f0-9]{32}", token) or journal.stem != token:
                    continue
                folder = Path(data["target"])
                if folder.is_symlink() or folder.resolve().parent != instances:
                    continue
                profiles = storage.load_installations()
                published = any(item.get("id") == data["profile"]["id"]
                                and folder_is_referenced(folder, [item], mc_root) for item in profiles)
                if _owned_pack_folder(folder, instances, token):
                    if published:
                        (folder / PACK_PENDING_MARKER).unlink()
                    elif not folder_is_referenced(folder, profiles, mc_root):
                        shutil.rmtree(folder)
                    else:
                        continue
                elif folder.exists() and not published:
                    # No ownership marker: never delete an unrelated folder.
                    continue
                prepared = Path(data["staging"])
                if (prepared.name == "game" and not prepared.parent.is_symlink()
                        and prepared.resolve().parent.parent == stage_root
                        and prepared.parent.name.startswith("pack-") and prepared.parent.is_dir()):
                    shutil.rmtree(prepared.parent)
                journal.unlink()
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
