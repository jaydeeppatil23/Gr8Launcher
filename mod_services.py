"""Local mod management and compatible Modrinth downloads, independent of the UI."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from typing import Callable
from urllib.parse import urlparse
import uuid
import zipfile
import weakref

try:
    import tomllib
except ImportError:  # Python 3.10
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

import requests
from PIL import Image, UnidentifiedImageError

from mod_compatibility import fabric_loader_satisfies, mod_version_satisfies


SUPPORTED_LOADERS = {"fabric", "forge", "neoforge", "quilt"}
MAX_JAR_BYTES = 512 * 1024 * 1024
MAX_ICON_BYTES = 2 * 1024 * 1024
ICON_THUMBNAIL_SIZE = 128
MAX_ICON_PIXELS = 16 * 1024 * 1024
MAX_BUNDLED_JAR_BYTES = 32 * 1024 * 1024
MAX_BUNDLED_TOTAL_BYTES = 64 * 1024 * 1024
MAX_BUNDLED_FILES = 128
_LOCKS_GUARD = threading.Lock()
_DIRECTORY_LOCKS = weakref.WeakValueDictionary()
_CACHE_MISS = object()
_MOD_METADATA_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mod-metadata")


class _DirectoryLock:
    """Reentrant folder lock; Windows also serializes separate launcher processes."""

    def __init__(self, key: str):
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._handle = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self._kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
            self._kernel.CreateMutexW.restype = wintypes.HANDLE
            self._kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            self._kernel.WaitForSingleObject.restype = wintypes.DWORD
            self._kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
            self._kernel.ReleaseMutex.restype = wintypes.BOOL
            self._kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            self._kernel.CloseHandle.restype = wintypes.BOOL
            name = "Local\\Gr8Launcher-Mods-" + hashlib.sha256(key.encode("utf-8")).hexdigest()
            self._handle = self._kernel.CreateMutexW(None, False, name)
            if not self._handle:
                raise ctypes.WinError(ctypes.get_last_error())

    def __enter__(self):
        self._thread_lock.acquire()
        if self._depth == 0 and self._handle:
            status = self._kernel.WaitForSingleObject(self._handle, 0xFFFFFFFF)
            if status not in (0, 0x80):  # acquired, or abandoned by an exited process
                self._thread_lock.release()
                raise OSError("Could not lock the instance's mods folder.")
        self._depth += 1
        return self

    def __exit__(self, *_):
        self._depth -= 1
        if self._depth == 0 and self._handle:
            self._kernel.ReleaseMutex(self._handle)
        self._thread_lock.release()

    def __del__(self):
        if self._handle:
            self._kernel.CloseHandle(self._handle)


def jar_base_name(filename: str) -> str:
    """Strip the disabled suffix using the same rules as the directory scan."""
    return filename[:-9] if filename.lower().endswith(".disabled") else filename


def _directory_lock(directory: Path) -> _DirectoryLock:
    key = os.path.normcase(str(directory))
    with _LOCKS_GUARD:
        lock = _DIRECTORY_LOCKS.get(key)
        if lock is None:
            lock = _DirectoryLock(key)
            _DIRECTORY_LOCKS[key] = lock
        return lock


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


@dataclass(frozen=True)
class LocalMod:
    filename: str
    name: str
    version: str
    description: str
    enabled: bool
    size: int
    mod_id: str = ""
    project_id: str = ""
    version_id: str = ""
    icon: bytes | None = None
    mod_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstallDependency:
    project_id: str
    name: str
    version: dict | None
    required: bool
    installed: bool = False
    bundled: bool = False
    issue: str = ""


@dataclass(frozen=True)
class InstallPlan:
    versions: tuple[dict, ...]
    already_installed: tuple[str, ...]
    replacements: tuple[str, ...] = ()
    disabled_projects: tuple[str, ...] = ()
    snapshot: tuple[tuple[str, str], ...] | None = None
    identifications: tuple[tuple[str, dict], ...] = ()
    loader: str = ""
    loader_version: str = ""
    warnings: tuple[str, ...] = ()
    dependencies: tuple[InstallDependency, ...] = ()
    minecraft_version: str = ""
    root_version: dict | None = None
    root_versions: tuple[dict, ...] = ()
    version_id: str = ""
    runtime_mods: tuple[dict, ...] = ()
    runtime_snapshot: tuple[tuple[str, str], ...] | None = None


@dataclass(frozen=True)
class ModUpdate:
    mod: LocalMod
    version: dict
    sha512: str


@dataclass(frozen=True)
class UpdateCheck:
    mods: tuple[LocalMod, ...]
    updates: tuple[ModUpdate, ...]
    warnings: tuple[str, ...] = ()
    snapshot: tuple[tuple[str, str], ...] | None = None
    version_id: str = ""
    runtime_snapshot: tuple[tuple[str, str], ...] | None = None


def newer_compatible_version(current: dict, versions: list[dict], minecraft_version: str, loader: str, *,
                             release_only: bool = True, release_type: str | None = None,
                             loader_version: str = "", check_loader: Callable[[dict], bool] | None = None) -> dict | None:
    """Find a newer matching publication, optionally excluding prereleases."""
    selected_type = release_type if release_type is not None else "release" if release_only else "all"
    if selected_type not in {"all", "release", "beta", "alpha"}:
        raise ValueError("Choose All releases, Stable, Beta, or Alpha.")
    def published(version: dict) -> datetime:
        value = datetime.fromisoformat(version["date_published"].replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError("Missing mod publication timezone.")
        return value

    compatible = sorted((version for version in versions
                         if version.get("project_id") == current.get("project_id")
                         and minecraft_version in version.get("game_versions", [])
                         and loader in version.get("loaders", [])
                         and (selected_type == "all" or version.get("version_type") == selected_type)),
                        key=published, reverse=True)
    for version in compatible:
        if version.get("id") != current.get("id") and published(version) > published(current):
            if check_loader is not None:
                if not check_loader(version):
                    continue
            elif loader_version and version_loader_compatible(version, loader, loader_version) is not True:
                continue
            return version
    return None


def safe_jar_name(name: str) -> str:
    """Reject path separators and Windows aliases before using API filenames."""
    if (not isinstance(name, str) or not name.lower().endswith(".jar")
            or re.search(r'[\\/:*?"<>|\x00-\x1f]', name)
            or name.startswith(".") or len(name) > 200):
        raise ValueError("The mod has an invalid JAR filename.")
    stem = name.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *(f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10))}:
        raise ValueError("The mod filename is reserved by Windows.")
    return name


def _digest(path: Path, algorithm: str = "sha512") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_icon(jar: zipfile.ZipFile, reference) -> bytes | None:
    """Read a bounded raster icon from the JAR without extracting files."""
    if isinstance(reference, dict):
        choices = [(abs(int(size) - 128), path) for size, path in reference.items()
                   if re.fullmatch(r"\d{1,5}", str(size)) and isinstance(path, str)]
        reference = min(choices)[1] if choices else None
    if not isinstance(reference, str):
        return None
    name = reference.removeprefix("./")
    if name.startswith(("/", "\\")) or ".." in name.split("/"):
        return None
    try:
        info = jar.getinfo(name)
        if info.is_dir() or info.file_size > MAX_ICON_BYTES:
            return None
        data = jar.read(info)
        if (data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a"))
                or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")):
            # This runs in the scan worker. Sending a full-resolution logo to
            # Flutter repeatedly decodes it whenever the mod row is mounted.
            with Image.open(io.BytesIO(data)) as icon:
                if icon.width * icon.height > MAX_ICON_PIXELS:
                    return None
                if max(icon.size) <= ICON_THUMBNAIL_SIZE and not getattr(icon, "is_animated", False):
                    return data
                icon.thumbnail((ICON_THUMBNAIL_SIZE, ICON_THUMBNAIL_SIZE), Image.Resampling.LANCZOS)
                thumbnail = icon.convert("RGBA")
                output = io.BytesIO()
                thumbnail.save(output, format="PNG")
                return output.getvalue()
    except (KeyError, OSError, ValueError, RuntimeError, zipfile.BadZipFile,
            UnidentifiedImageError, Image.DecompressionBombError):
        pass
    return None


def _metadata(path: Path | io.BytesIO) -> dict:
    """Read small metadata entries without extracting an archive."""
    result = {}
    mod_ids = []
    with zipfile.ZipFile(path) as jar:
        for name in ("fabric.mod.json", "quilt.mod.json", "mcmod.info"):
            if name not in jar.namelist():
                continue
            info = jar.getinfo(name)
            if info.file_size > 1024 * 1024:
                continue
            try:
                data = json.loads(jar.read(name))
                if name == "quilt.mod.json":
                    loader = data.get("quilt_loader", {})
                    data = {**loader, **loader.get("metadata", {})}
                if name == "mcmod.info" and isinstance(data, dict) and "modList" in data:
                    data = data["modList"]
                entries = data if isinstance(data, list) else [data]
                entries = [entry for entry in entries if isinstance(entry, dict)]
                mod_ids.extend(str(entry.get("id") or entry.get("modid"))
                               for entry in entries if entry.get("id") or entry.get("modid"))
                if entries and not result:
                    data = entries[0]
                    result = {
                        "name": str(data.get("name") or ""),
                        "version": str(data.get("version") or ""),
                        "description": str(data.get("description") or ""),
                        "mod_id": str(data.get("id") or data.get("modid") or ""),
                        "authors": data.get("authors") or data.get("authorList") or data.get("contributors") or [],
                        "license": data.get("license") or "",
                        "links": data.get("contact") or {},
                        "dependencies": data.get("depends") or data.get("dependencies") or {},
                        "icon": _archive_icon(jar, data.get("icon") or data.get("logoFile")),
                    }
                if name == "fabric.mod.json" and entries:
                    dependencies = entries[0].get("depends", {})
                    if isinstance(dependencies, dict) and "fabricloader" in dependencies:
                        result["fabric_loader_requirement"] = dependencies["fabricloader"]
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        # Restrict IDs to actual mod tables; dependency tables also contain modId.
        for name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
            if name not in jar.namelist() or jar.getinfo(name).file_size > 1024 * 1024:
                continue
            text = jar.read(name).decode("utf-8", errors="replace")
            block_pattern = r"(?ms)^[ \t]*\[\[[ \t]*mods[ \t]*\]\][ \t]*(?:#[^\r\n]*)?\r?\n(.*?)(?=^[ \t]*\[|\Z)"
            blocks = re.findall(block_pattern, text)
            try:
                entries = tomllib.loads(text).get("mods", []) if tomllib else None
            except ValueError:
                entries = None
            if isinstance(entries, list):
                mod_ids.extend(str(entry["modId"]) for entry in entries
                               if isinstance(entry, dict) and entry.get("modId"))
            else:
                # Multiline descriptions can contain TOML examples. They are
                # display content, not additional tables or declared mod IDs.
                structural = re.sub(r"(?s)('''|\"\"\").*?\1", "''", text)
                for block in re.findall(block_pattern, structural):
                    match = re.search(r'(?m)^\s*modId\s*=\s*["\']([^"\'\r\n]*)["\']', block)
                    if match:
                        mod_ids.append(match[1])
            if result:
                continue
            display = blocks[0] if blocks else text
            for source, target in (("displayName", "name"), ("modId", "mod_id"), ("version", "version"), ("authors", "authors"), ("license", "license"), ("displayURL", "website")):
                match = re.search(rf'(?m)^\s*{source}\s*=\s*[\"\']([^\"\'\r\n]*)[\"\']', text if source == "license" else display)
                if match and "${" not in match[1]:
                    result[target] = match[1]
            logo = re.search(r'(?m)^\s*logoFile\s*=\s*["\']([^"\'\r\n]*)["\']', display)
            result["icon"] = _archive_icon(jar, logo[1] if logo else None)
            description = re.search(r"(?ms)^\s*description\s*=\s*'''(.*?)'''", display)
            if description:
                result["description"] = description[1].strip()
    if result.get("mod_id"):
        mod_ids.append(result["mod_id"])
    result["mod_ids"] = tuple(dict.fromkeys(mod_ids))
    return result


def _fabric_native_mods(data: dict) -> list[dict]:
    rules = []
    for key in ("depends", "breaks"):
        for target, requirement in (data.get(key) or {}).items():
            rules.append({"target": target, "requirement": requirement, "conflict": key == "breaks"})
    mod = {"id": data.get("id"), "name": data.get("name") or data.get("id"),
           "version": str(data.get("version", "")), "rules": rules}
    return [mod] + [{**mod, "id": alias, "rules": []} for alias in data.get("provides", []) if isinstance(alias, str)]


def _native_mods(path: Path | io.BytesIO) -> list[dict]:
    """Keep each mod's native version constraints, including multi-mod Forge JARs."""
    with zipfile.ZipFile(path) as archive:
        def read(name: str) -> bytes | None:
            try:
                entry = archive.getinfo(name)
                return archive.read(entry) if entry.file_size <= 1024 * 1024 else None
            except KeyError:
                return None

        raw = read("fabric.mod.json")
        if raw:
            return _fabric_native_mods(json.loads(raw))
        raw = read("quilt.mod.json")
        if raw:
            data = json.loads(raw).get("quilt_loader", {})
            rules = []
            for key in ("depends", "breaks"):
                for dependency in data.get(key, []):
                    if isinstance(dependency, str):
                        dependency = {"id": dependency, "versions": "*"}
                    if not isinstance(dependency, dict) or not dependency.get("id") or dependency.get("unless"):
                        rules.append({"unsupported": True})
                        continue
                    rules.append({"target": dependency["id"], "requirement": dependency.get("versions", "*"),
                                  "optional": dependency.get("optional", False), "conflict": key == "breaks"})
            mod = {"id": data.get("id"), "name": data.get("metadata", {}).get("name") or data.get("id"),
                   "version": str(data.get("version", "")), "rules": rules}
            aliases = []
            for alias in data.get("provides", []):
                if isinstance(alias, str):
                    aliases.append({**mod, "id": alias, "rules": []})
                elif isinstance(alias, dict) and alias.get("id"):
                    aliases.append({**mod, "id": alias["id"], "version": str(alias.get("version", mod["version"])), "rules": []})
            return [mod] + aliases
        for name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
            raw = read(name)
            if not raw:
                continue
            if tomllib is None:
                raise ValueError("Native Forge requirements cannot be checked. Repair the launcher's Python dependencies before installing mods.")
            data = tomllib.loads(raw.decode("utf-8"))
            mods = []
            for mod in data.get("mods", []):
                rules = []
                for dependency in data.get("dependencies", {}).get(mod.get("modId"), []):
                    if dependency.get("side", "BOTH").upper() == "SERVER":
                        continue
                    kind = dependency.get("type", "required" if dependency.get("mandatory", True) else "optional")
                    if kind in ("discouraged",):
                        continue
                    rules.append({"target": dependency.get("modId"), "requirement": dependency.get("versionRange", "*"),
                                  "optional": kind == "optional", "conflict": kind == "incompatible", "maven": True})
                version = str(mod.get("version", ""))
                if "${" in version:
                    manifest = read("META-INF/MANIFEST.MF") or b""
                    match = re.search(rb"(?im)^Implementation-Version:\s*([^\r\n]+)", manifest)
                    version = match[1].decode("utf-8").strip() if match else ""
                mods.append({"id": mod.get("modId"), "name": mod.get("displayName") or mod.get("modId"),
                             "version": version, "rules": rules})
            return mods
    return []


def _check_native_mod_set(files: list[tuple[Path, str]], installed: list[LocalMod],
                          directory: Path, plan: InstallPlan) -> None:
    """Check staged mods and existing dependents before touching installed files."""
    mods = []
    changed = set()
    replacing = set(plan.replacements)
    for path, is_new in ([(directory / mod.filename, False) for mod in installed
                           if mod.enabled and mod.filename not in replacing]
                          + [(path, True) for path, name in files if not name.endswith(".disabled")]):
        entries = [{**entry, "bundled": False} for entry in _native_mods(path)]
        entries.extend({**entry, "bundled": True} for bundled in _bundled_mod_files(path) for entry in bundled.get("native", []))
        for entry in entries:
            if not entry.get("id"):
                continue
            entry = {**entry, "new": is_new}
            mods.append(entry)
            if is_new:
                changed.add(entry["id"])
    # Removed bundled libraries also affect existing dependents.
    for mod in installed:
        if mod.enabled and mod.filename in replacing:
            path = directory / mod.filename
            changed.update(entry["id"] for entry in _native_mods(path) if entry.get("id"))
            changed.update(mod_id for child in _bundled_mod_files(path) for mod_id in child["mod_ids"])
    _check_native_requirements(mods, changed, plan)


def _native_providers(mods: list[dict], plan: InstallPlan) -> dict[str, list[dict]]:
    providers = {}
    for mod in [*plan.runtime_mods, *mods]:
        providers.setdefault(mod["id"], []).append(mod)
    for target, candidates in providers.items():
        # A bundled copy must not mask an incompatible standalone mod version.
        standalone = [mod for mod in candidates if not mod.get("bundled")]
        providers[target] = standalone or candidates
    system = {"minecraft": plan.minecraft_version,
              {"fabric": "fabricloader", "quilt": "quilt_loader", "forge": "forge", "neoforge": "neoforge"}.get(plan.loader): plan.loader_version}
    for target, version in system.items():
        if target and version:
            providers[target] = [{"version": version, "name": target}]
    return providers


def _check_native_requirements(mods: list[dict], changed: set[str], plan: InstallPlan) -> None:
    providers = _native_providers(mods, plan)
    for mod in [*[{**mod, "new": False} for mod in plan.runtime_mods], *mods]:
        for rule in mod["rules"]:
            if rule.get("unsupported"):
                if mod["new"]:
                    raise ValueError(f"Cannot verify {mod['name']}'s native dependency declaration. Choose another mod version.")
                continue
            target = rule.get("target")
            if target in {"java", "fabricloader", "quilt_loader", "forge", "neoforge", "minecraft"} and target not in providers:
                continue  # Java is chosen at launch; loader checks also run separately.
            if not mod["new"] and target not in changed:
                continue
            candidates = providers.get(target, [])
            if not candidates and (rule.get("optional") or rule.get("conflict")):
                continue
            requirement = rule["requirement"]
            results = [mod_version_satisfies(item["version"], requirement, maven=rule.get("maven", False)) for item in candidates]
            if rule.get("conflict") and True not in results and None not in results:
                continue
            if not rule.get("conflict") and True in results:
                continue
            actual = ", ".join(item["version"] or "unknown version" for item in candidates) or "not installed"
            if None in results:
                raise ValueError(f"Cannot verify {mod['name']}'s requirement for {target} {requirement} against {actual}. Choose a compatible mod version.")
            action = "conflicts with" if rule.get("conflict") else "requires"
            raise ValueError(f"{mod['name']} {action} {target} {requirement}; the resulting version is {actual}. Existing mods were kept. Choose another mod version or review the dependency in Installed.")


def _bundled_mod_files(path: Path) -> list[dict]:
    """Inspect declared loader JARs in memory with bounded depth and read size."""
    result = []
    seen = set()
    remaining = MAX_BUNDLED_TOTAL_BYTES
    count = 0

    def scan(archive: zipfile.ZipFile, depth: int) -> None:
        nonlocal remaining, count
        if depth >= 3:
            return
        references = []
        for manifest in ("fabric.mod.json", "quilt.mod.json", "META-INF/jarjar/metadata.json"):
            try:
                info = archive.getinfo(manifest)
                if info.file_size > 1024 * 1024:
                    continue
                data = json.loads(archive.read(info))
                if not isinstance(data, dict):
                    continue
                if manifest == "quilt.mod.json":
                    loader_data = data.get("quilt_loader", {})
                    data = {"jars": loader_data.get("jars", data.get("jars", []))}
                entries = data.get("jars", [])
                if isinstance(entries, list):
                    references.extend(entry if isinstance(entry, str) else entry.get("file") or entry.get("path")
                                      for entry in entries if isinstance(entry, (dict, str)))
            except (KeyError, ValueError, TypeError, AttributeError, OSError, zipfile.BadZipFile, RuntimeError):
                continue
        for reference in references:
            if count >= MAX_BUNDLED_FILES:
                return
            count += 1
            if not isinstance(reference, str):
                continue
            name = reference.removeprefix("./")
            if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/") or not name.lower().endswith(".jar"):
                continue
            try:
                info = archive.getinfo(name)
                if info.is_dir() or info.file_size > min(remaining, MAX_BUNDLED_JAR_BYTES):
                    continue
                remaining -= info.file_size
                contents = archive.read(info)
                digest = hashlib.sha512(contents).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                with zipfile.ZipFile(io.BytesIO(contents)) as child:
                    metadata = _metadata(io.BytesIO(contents))
                    if metadata.get("mod_ids"):
                        result.append({"sha512": digest, "name": metadata.get("name") or name,
                                       "mod_ids": metadata["mod_ids"], "metadata": metadata,
                                       "native": _native_mods(io.BytesIO(contents))})
                    scan(child, depth + 1)
            except (KeyError, ValueError, TypeError, AttributeError, OSError, zipfile.BadZipFile, RuntimeError):
                continue

    try:
        with zipfile.ZipFile(path) as archive:
            scan(archive, 0)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        pass
    return result


def _check_loader_metadata(metadata: dict, loader: str, loader_version: str, label: str) -> None:
    if loader != "fabric" or "fabric_loader_requirement" not in metadata:
        return
    requirement = metadata["fabric_loader_requirement"]
    if fabric_loader_satisfies(loader_version, requirement) is True:
        return
    if not loader_version:
        raise ValueError(f"{label} requires Fabric Loader {requirement}. Set the profile's Fabric Loader version before continuing.")
    compatible = fabric_loader_satisfies(loader_version, requirement)
    if compatible is None:
        raise ValueError(f"Cannot verify {label}'s Fabric Loader requirement {requirement} against {loader_version}. Choose another mod version.")
    if not compatible:
        raise ValueError(f"{label} requires Fabric Loader {requirement}; this profile uses {loader_version}. Update the profile's Fabric Loader or choose an older compatible mod version.")


def _check_jar_loader(path: Path, loader: str, loader_version: str) -> None:
    if loader != "fabric":
        return
    metadata = _metadata(path)
    _check_loader_metadata(metadata, loader, loader_version, metadata.get("name") or path.name)
    for bundled in _bundled_mod_files(path):
        _check_loader_metadata(bundled["metadata"], loader, loader_version, bundled["name"])


class _RangeJar(io.RawIOBase):
    """Seekable JAR metadata reads using bounded, cached CDN byte ranges."""

    BLOCK_SIZE = 64 * 1024
    MAX_TRANSFER = MAX_BUNDLED_TOTAL_BYTES + 4 * 1024 * 1024

    def __init__(self, session: requests.Session, file: dict, headers: dict):
        self.session, self.file, self.headers = session, file, headers
        self.length = file.get("size")
        self.position = 0
        self.blocks: dict[int, bytes] = {}
        self.full: bytes | None = None
        self.transferred = 0
        parsed = urlparse(str(file.get("url", "")))
        if parsed.scheme != "https" or parsed.hostname != "cdn.modrinth.com":
            raise ValueError("The mod download is not hosted on Modrinth.")
        if not isinstance(self.length, int) or not 0 < self.length <= MAX_JAR_BYTES:
            raise ValueError("Cannot verify this mod's metadata without a valid JAR size.")

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        position = offset if whence == io.SEEK_SET else self.position + offset if whence == io.SEEK_CUR else self.length + offset
        if whence not in (io.SEEK_SET, io.SEEK_CUR, io.SEEK_END) or position < 0:
            raise ValueError("Invalid JAR metadata offset.")
        self.position = position
        return position

    def _block(self, index: int) -> bytes:
        start = index * self.BLOCK_SIZE
        end = min(start + self.BLOCK_SIZE, self.length) - 1
        if self.full is not None:
            return self.full[start:end + 1]
        if index in self.blocks:
            return self.blocks[index]
        headers = {**self.headers, "Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}
        try:
            with self.session.get(self.file["url"], headers=headers, timeout=(5, 25),
                                  stream=True, allow_redirects=False) as response:
                response.raise_for_status()
                if response.status_code == 206:
                    if response.headers.get("Content-Range") != f"bytes {start}-{end}/{self.length}":
                        raise ValueError("Modrinth returned an invalid JAR metadata range.")
                    expected = end - start + 1
                elif response.status_code == 200 and self.length <= 2 * 1024 * 1024:
                    # Small archives still work with mirrors that ignore Range.
                    expected = self.length
                else:
                    raise ValueError("Cannot read this mod's metadata from the download server.")
                data = bytearray()
                for chunk in response.iter_content(self.BLOCK_SIZE):
                    data.extend(chunk)
                    self.transferred += len(chunk)
                    if len(data) > expected or self.transferred > self.MAX_TRANSFER:
                        raise ValueError("The mod exceeds the metadata size limit.")
                if len(data) != expected:
                    raise ValueError("The mod's metadata transfer was incomplete.")
                payload = bytes(data)
                if response.status_code == 200:
                    hashes = self.file.get("hashes", {})
                    algorithm = next((name for name in ("sha512", "sha1") if hashes.get(name)), None)
                    if not algorithm or hashlib.new(algorithm, payload).hexdigest() != str(hashes[algorithm]).lower():
                        raise ValueError("Mod verification failed. Retry the download.")
                    self.full = payload
                    return payload[start:end + 1]
                self.blocks[index] = payload
                return payload
        except requests.RequestException as error:
            raise ConnectionError("Could not verify the mod's Fabric Loader requirements. Retry when the connection is restored.") from error

    def read(self, size: int = -1) -> bytes:
        size = max(0, min(self.length - self.position, self.length if size < 0 else size))
        if size > self.MAX_TRANSFER:
            raise ValueError("The mod exceeds the metadata size limit.")
        chunks = []
        while size:
            index, offset = divmod(self.position, self.BLOCK_SIZE)
            block = self._block(index)
            amount = min(size, len(block) - offset)
            chunks.append(block[offset:offset + amount])
            self.position += amount
            size -= amount
        return b"".join(chunks)


def _fabric_loader_metadata(source: Path | io.BytesIO | _RangeJar) -> list[dict]:
    """Read Loader predicates from Fabric metadata and declared bundled mods."""
    result = []
    remaining = MAX_BUNDLED_TOTAL_BYTES
    count = 0

    def scan(archive: zipfile.ZipFile, depth: int = 0) -> None:
        nonlocal remaining, count
        try:
            info = archive.getinfo("fabric.mod.json")
        except KeyError:
            if depth == 0:
                raise ValueError("This Fabric mod has no readable Fabric metadata.")
            return
        if info.file_size > 1024 * 1024:
            raise ValueError("The mod exceeds the metadata size limit.")
        data = json.loads(archive.read(info))
        if not isinstance(data, dict) or not isinstance(data.get("depends", {}), dict):
            raise ValueError("This mod has invalid Fabric metadata.")
        metadata = {"name": data.get("name") or data.get("id") or "Bundled mod"}
        metadata["native"] = _fabric_native_mods(data)
        metadata["bundled"] = depth > 0
        if "fabricloader" in data.get("depends", {}):
            metadata["fabric_loader_requirement"] = data["depends"]["fabricloader"]
        result.append(metadata)
        entries = data.get("jars", [])
        if not isinstance(entries, list):
            raise ValueError("This mod has invalid bundled mod metadata.")
        for entry in entries:
            reference = entry.get("file") if isinstance(entry, dict) else None
            if (not isinstance(reference, str) or reference.startswith(("/", "\\"))
                    or ".." in reference.replace("\\", "/").split("/")):
                raise ValueError("This mod has an invalid bundled JAR reference.")
            count += 1
            info = archive.getinfo(reference.removeprefix("./"))
            if depth >= 4 or count > MAX_BUNDLED_FILES or info.file_size > min(remaining, MAX_BUNDLED_JAR_BYTES):
                raise ValueError("The mod exceeds the bundled metadata size limit.")
            remaining -= info.file_size
            with zipfile.ZipFile(io.BytesIO(archive.read(info))) as child:
                scan(child, depth + 1)

    try:
        with zipfile.ZipFile(source) as archive:
            scan(archive)
    except (zipfile.BadZipFile, KeyError, UnicodeError, RuntimeError) as error:
        raise ValueError("Could not read the mod's Fabric Loader metadata.") from error
    return result


def version_loader_compatible(version: dict, loader: str, loader_version: str) -> bool | None:
    """Check previously fetched predicates without network calls during rendering."""
    if loader != "fabric":
        return True
    metadata = version.get("_fabric_loader_metadata")
    if not isinstance(metadata, list) or not metadata:
        return None
    for item in metadata:
        if not isinstance(item, dict):
            return None
        if "fabric_loader_requirement" in item:
            compatible = fabric_loader_satisfies(loader_version, item["fabric_loader_requirement"])
            if compatible is not True:
                return compatible
    return True


def version_matches_loader(client: ModrinthClient, version: dict, loader: str, loader_version: str) -> bool:
    probe = getattr(client, "fabric_loader_metadata", None)
    if loader != "fabric" or not loader_version or not callable(probe):
        return True
    version["_fabric_loader_metadata"] = probe(version)
    return version_loader_compatible(version, loader, loader_version) is True


def version_matches_environment(client: ModrinthClient, version: dict, loader: str, loader_version: str,
                                providers: dict[str, list[dict]]) -> bool:
    if not version_matches_loader(client, version, loader, loader_version):
        return False
    for item in version.get("_fabric_loader_metadata", []):
        for mod in item.get("native", []):
            for rule in mod.get("rules", []):
                target = rule.get("target")
                if target not in providers:
                    continue  # Missing mod dependencies are resolved by the plan.
                results = [mod_version_satisfies(candidate["version"], rule["requirement"])
                           for candidate in providers[target]]
                if rule.get("conflict"):
                    if True in results or None in results:
                        return False
                elif True not in results:
                    return False
    return True


def _select_compatible_version(client: ModrinthClient, project: str, minecraft_version: str, loader: str, loader_version: str,
                               providers: dict[str, list[dict]] | None = None) -> dict:
    if loader != "fabric" or not loader_version or not callable(getattr(client, "fabric_loader_metadata", None)):
        return client.compatible_version(project, minecraft_version, loader)
    versions = sorted(client.project_versions(project, minecraft_version, loader),
                      key=lambda version: version.get("date_published", ""), reverse=True)
    versions.sort(key=lambda version: version.get("version_type") != "release")
    last_incompatible = None
    for version in versions:
        if minecraft_version in version.get("game_versions", []) and loader in version.get("loaders", []):
            if version_matches_environment(client, version, loader, loader_version, providers or {}):
                return version
            last_incompatible = version
    if last_incompatible:
        for metadata in last_incompatible.get("_fabric_loader_metadata", []):
            _check_loader_metadata(metadata, loader, loader_version, metadata.get("name") or project)
    raise ValueError(f"No mod version is compatible with Minecraft {minecraft_version} and Fabric Loader {loader_version}.")


def primary_file(version: dict) -> dict:
    candidates = [item for item in version.get("files", [])
                  if str(item.get("filename", "")).lower().endswith(".jar")
                  and not item.get("file_type")]
    if not candidates:
        raise ValueError("This version has no installable mod JAR.")
    result = next((item for item in candidates if item.get("primary")), candidates[0])
    safe_jar_name(result["filename"])
    return result


class ModrinthClient:
    API = "https://api.modrinth.com/v2"
    HEADERS = {"User-Agent": "Gr8Launcher (Minecraft Java mod manager)"}
    CACHE_TTL = 300
    CACHE_SIZE = 128

    def __init__(self):
        self._cache = OrderedDict()
        self._cache_lock = threading.RLock()
        self._cache_generation = 0
        self._inflight: dict[tuple, Future] = {}
        self._sessions = threading.local()
        self._bulk_unavailable_until = 0.0

    def _session(self) -> requests.Session:
        # Worker threads reuse connections without sharing mutable Session state.
        if not hasattr(self._sessions, "http"):
            self._sessions.http = requests.Session()
        return self._sessions.http

    @staticmethod
    def _get_key(endpoint: str, params: dict | None, missing_ok: bool) -> tuple:
        return (endpoint, json.dumps(params or {}, sort_keys=True, separators=(",", ":")), missing_ok)

    def clear_cache(self) -> None:
        """Explicit refreshes invalidate cached responses and older pending fetches."""
        with self._cache_lock:
            self._cache_generation += 1
            self._cache.clear()
            self._bulk_unavailable_until = 0.0

    def _cached_value(self, key: tuple) -> object:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return _CACHE_MISS
            expires, value = entry
            if expires <= time.monotonic():
                self._cache.pop(key)
                return _CACHE_MISS
            self._cache.move_to_end(key)
            return deepcopy(value)

    def _store_response(self, key: tuple, value: object, ttl: float, generation: int) -> None:
        with self._cache_lock:
            if generation != self._cache_generation:
                return
            self._cache[key] = (time.monotonic() + ttl, deepcopy(value))
            self._cache.move_to_end(key)
            while len(self._cache) > self.CACHE_SIZE:
                self._cache.popitem(last=False)

    def _cached_request(self, key: tuple, fetch: Callable, *, ttl: float = CACHE_TTL) -> object:
        with self._cache_lock:
            value = self._cached_value(key)
            if value is not _CACHE_MISS:
                return value
            generation = self._cache_generation
            pending_key = (generation, key)
            future = self._inflight.get(pending_key)
            owner = future is None
            if owner:
                future = self._inflight[pending_key] = Future()
        if not owner:
            return deepcopy(future.result())
        try:
            value = fetch()
            self._store_response(key, value, ttl, generation)
            future.set_result(value)
            return deepcopy(value)
        except Exception as error:
            future.set_exception(error)
            raise
        finally:
            with self._cache_lock:
                self._inflight.pop(pending_key, None)

    def _get(self, endpoint: str, params: dict | None = None, *, missing_ok=False) -> object:
        def fetch() -> object:
            try:
                with self._session().get(self.API + endpoint, params=params, headers=self.HEADERS, timeout=(5, 25)) as response:
                    if missing_ok and response.status_code == 404:
                        return None
                    response.raise_for_status()
                    return response.json()
            except (requests.RequestException, ValueError) as error:
                raise ConnectionError("Could not reach Modrinth. Check your connection and try again.") from error
        return self._cached_request(self._get_key(endpoint, params, missing_ok), fetch,
                                    ttl=30 if endpoint == "/search" else self.CACHE_TTL)

    @staticmethod
    def _id(value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", str(value)):
            raise ValueError("Invalid Modrinth project or version ID.")
        return str(value)

    def search(self, query: str, minecraft_version: str, loader: str, *, index: str = "relevance", offset: int = 0, limit: int = 20) -> dict:
        facets = [["project_type:mod"], [f"versions:{minecraft_version}"],
                  [f"categories:{loader}"], ["client_side:required", "client_side:optional"]]
        data = self._get("/search", {
            "query": query, "facets": json.dumps(facets), "index": index,
            "offset": offset, "limit": limit,
        })
        if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
            raise ValueError("Modrinth returned an invalid search result.")
        return data

    def compatible_version(self, project_id: str, minecraft_version: str, loader: str) -> dict:
        versions = self.project_versions(project_id, minecraft_version, loader)
        if not versions:
            raise ValueError("No compatible version is available for this profile.")
        # Prefer a stable release; prereleases are used only if no release exists.
        return next((item for item in versions if item.get("version_type") == "release"), versions[0])

    def project(self, project_id: str) -> dict:
        data = self._get(f"/project/{self._id(project_id)}")
        if not isinstance(data, dict) or not data.get("id"):
            raise ValueError("Modrinth returned an invalid project.")
        return data

    def project_versions(self, project_id: str, minecraft_version: str | None = None, loader: str | None = None) -> list[dict]:
        endpoint = f"/project/{self._id(project_id)}/version"
        if minecraft_version or loader:
            history = self._cached_value(self._get_key(endpoint, {}, False))
            if isinstance(history, list) and all(isinstance(version, dict) for version in history):
                return sorted((version for version in history
                        if (not minecraft_version or minecraft_version in version.get("game_versions", []))
                        and (not loader or loader in version.get("loaders", []))),
                        key=lambda version: version.get("date_published", ""), reverse=True)
        params = {}
        if minecraft_version:
            params["game_versions"] = json.dumps([minecraft_version])
        if loader:
            params["loaders"] = json.dumps([loader])
        data = self._get(endpoint, params)
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError("Modrinth returned an invalid version list.")
        return sorted(data, key=lambda item: item.get("date_published", ""), reverse=True)

    def project_members(self, project_id: str) -> list[dict]:
        data = self._get(f"/project/{self._id(project_id)}/members")
        return data if isinstance(data, list) else []

    def version_for_file(self, digest: str) -> dict | None:
        if not re.fullmatch(r"[a-f0-9]{128}", digest):
            raise ValueError("Invalid mod file checksum.")
        data = self._get(f"/version_file/{digest}", {"algorithm": "sha512"}, missing_ok=True)
        return data if isinstance(data, dict) else None

    def versions_for_files(self, digests: list[str]) -> dict[str, dict]:
        """Batch unknown hashes, sharing positive/negative results with single lookups."""
        if any(not re.fullmatch(r"[a-f0-9]{128}", digest) for digest in digests):
            raise ValueError("Invalid mod file checksum.")
        result = {}
        missing = []
        with self._cache_lock:
            generation = self._cache_generation
        for digest in sorted(set(digests)):
            cached = self._cached_value(self._get_key(f"/version_file/{digest}", {"algorithm": "sha512"}, True))
            if cached is _CACHE_MISS:
                missing.append(digest)
            elif isinstance(cached, dict):
                result[digest] = cached
        for offset in range(0, len(missing), 100):
            batch = missing[offset:offset + 100]
            def fetch() -> dict:
                try:
                    if self._bulk_unavailable_until > time.monotonic():
                        return {digest: version for digest in batch if (version := self.version_for_file(digest))}
                    with self._session().post(self.API + "/version_files", headers=self.HEADERS,
                                              json={"hashes": batch, "algorithm": "sha512"}, timeout=(5, 25)) as response:
                        if response.status_code in (404, 405, 501):
                            with self._cache_lock:
                                if generation == self._cache_generation:
                                    self._bulk_unavailable_until = time.monotonic() + self.CACHE_TTL
                            return {digest: version for digest in batch if (version := self.version_for_file(digest))}
                        response.raise_for_status()
                        data = response.json()
                    if not isinstance(data, dict) or any(digest not in batch or not isinstance(version, dict) for digest, version in data.items()):
                        raise ValueError("Invalid bundled mod identities.")
                    return data
                except (requests.RequestException, ValueError) as error:
                    raise ConnectionError("Could not identify bundled mods on Modrinth. Check your connection and try again.") from error
            data = self._cached_request(("version_files", tuple(batch)), fetch)
            result.update(data)
            for digest in batch:
                self._store_response(self._get_key(f"/version_file/{digest}", {"algorithm": "sha512"}, True),
                                     data.get(digest), self.CACHE_TTL, generation)
        return result

    def version(self, version_id: str) -> dict:
        data = self._get(f"/version/{self._id(version_id)}")
        if not isinstance(data, dict):
            raise ValueError("Modrinth returned an invalid mod version.")
        return data

    def fabric_loader_metadata(self, version: dict) -> list[dict]:
        file = primary_file(version)
        key = ("fabric-loader-metadata", file.get("url"), file.get("size"), json.dumps(file.get("hashes", {}), sort_keys=True))
        def fetch() -> list[dict]:
            with _RangeJar(self._session(), file, self.HEADERS) as source:
                return _fabric_loader_metadata(source)
        return self._cached_request(key, fetch)

    def native_dependency(self, target: str, requirement: object, minecraft_version: str, loader: str,
                          loader_version: str, providers: dict[str, list[dict]]) -> dict | None:
        """Resolve an omitted Fabric dependency only after verifying its native ID."""
        def candidate(project: str) -> dict | None:
            versions = self.project_versions(project, minecraft_version, loader)
            versions = sorted(versions, key=lambda version: version.get("date_published", ""), reverse=True)
            versions.sort(key=lambda version: version.get("version_type") != "release")
            for version in versions[:32]:
                if (version.get("project_id") != project or minecraft_version not in version.get("game_versions", [])
                        or loader not in version.get("loaders", [])):
                    continue
                version["_fabric_loader_metadata"] = self.fabric_loader_metadata(version)
                provided = [mod for metadata in version["_fabric_loader_metadata"] for mod in metadata.get("native", [])
                            if mod.get("id") == target and mod_version_satisfies(mod["version"], requirement) is True]
                if provided and version_matches_environment(self, version, loader, loader_version, providers):
                    return version
            return None

        tried = set()
        if re.fullmatch(r"[a-zA-Z0-9_-]+", target):
            project = self._get(f"/project/{target}", missing_ok=True)
            if isinstance(project, dict) and project.get("id"):
                tried.add(project["id"])
                found = candidate(project["id"])
                if found:
                    return found
        matches = []
        results = self.search(target, minecraft_version, loader, limit=5)
        for hit in results.get("hits", [])[:5]:
            project = hit.get("project_id")
            if not project or project in tried:
                continue
            tried.add(project)
            found = candidate(project)
            if found:
                matches.append(found)
        if len(matches) > 1:
            raise ValueError(f"Multiple projects provide {target}. Choose the dependency explicitly before installing this mod.")
        return matches[0] if matches else None

    def download(self, file: dict, destination: Path, progress: Callable[[str], None]) -> None:
        url = str(file.get("url", ""))
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "cdn.modrinth.com":
            raise ValueError("The mod download is not hosted on Modrinth.")
        hashes = file.get("hashes", {})
        algorithm = next((key for key in ("sha512", "sha1") if hashes.get(key)), None)
        if algorithm is None:
            raise ValueError("The mod has no download checksum.")
        size = 0
        digest = hashlib.new(algorithm)
        try:
            with requests.get(url, headers=self.HEADERS, timeout=(5, 30), stream=True, allow_redirects=False) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("Modrinth returned an unexpected download response.")
                with destination.open("xb") as output:
                    for chunk in response.iter_content(128 * 1024):
                        size += len(chunk)
                        if size > MAX_JAR_BYTES:
                            raise ValueError("The mod exceeds the download size limit.")
                        output.write(chunk)
                        digest.update(chunk)
                        on_bytes = getattr(progress, "on_bytes", None)
                        if callable(on_bytes):
                            on_bytes(size, file.get("size"))
            progress(f"Verifying {file['filename']}")
            if (digest.hexdigest().lower() != str(hashes[algorithm]).lower()
                    or (file.get("size") is not None and size != file["size"])):
                raise ValueError("Mod verification failed. Retry the download.")
            if not zipfile.is_zipfile(destination):
                raise ValueError("The downloaded file is not a valid JAR.")
            progress(f"Verified {file['filename']}")
        except requests.RequestException as error:
            raise ConnectionError("The mod download failed. Check your connection and retry.") from error


class ModManager:
    def __init__(self, game_directory: Path, *, runtime_directory: Path | None = None):
        self.game_directory = Path(game_directory).resolve()
        self.runtime_directory = Path(runtime_directory).resolve() if runtime_directory is not None else None
        self.mods_directory = self.game_directory / "mods"
        self.data_directory = self.game_directory / "gr8launcher"
        self.index_path = self.data_directory / "mods.json"
        self.transaction_path = self.data_directory / "mod-transaction.json"
        self.identities_path = self.data_directory / "mod-identities.json"
        self.updates_path = self.data_directory / "mod-updates.json"
        self._lock = _directory_lock(self.game_directory)
        self._metadata_cache: dict[tuple, dict] = {}

    def runtime_mods(self, version_id: str) -> tuple[tuple[dict, ...], tuple[tuple[str, str], ...] | None]:
        """Read mod providers from this version's actual inherited launch classpath."""
        if self.runtime_directory is None or not version_id:
            return (), None
        from minecraft_launcher_lib._helper import get_library_path, parse_rule_list

        root = self.runtime_directory
        snapshot = {}
        libraries = {}
        visited = set()

        def load(identifier: str) -> None:
            if (not isinstance(identifier, str) or not identifier or re.search(r'[\\/:\x00]', identifier)
                    or identifier in {".", ".."} or identifier in visited or len(visited) >= 16):
                raise ValueError("The profile has invalid or cyclic version metadata. Repair the profile before installing mods.")
            visited.add(identifier)
            path = root / "versions" / identifier / (identifier + ".json")
            try:
                if path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("The profile's version metadata is too large.")
                payload = path.read_bytes()
                data = json.loads(payload)
                if not isinstance(data, dict) or data.get("id") != identifier or not isinstance(data.get("libraries", []), list):
                    raise ValueError("The profile's version metadata is invalid.")
            except (OSError, ValueError) as error:
                raise ValueError("The profile's installed version metadata is missing or unreadable. Repair the profile before installing mods.") from error
            snapshot[str(path)] = hashlib.sha512(payload).hexdigest()
            if data.get("inheritsFrom"):
                load(data["inheritsFrom"])
            for library in data.get("libraries", []):
                name = library.get("name", "") if isinstance(library, dict) else ""
                pieces = name.split(":")
                if len(pieces) not in (3, 4) or any(not piece or re.search(r'[\\/\x00]', piece) or piece in {".", ".."} for piece in pieces):
                    raise ValueError("The profile has invalid library coordinates.")
                # A child version replaces the same Maven artifact from its parent.
                libraries[tuple(pieces[:-1])] = library

        load(version_id)
        mods = []
        library_root = (root / "libraries").resolve()
        for library in libraries.values():
            if library.get("rules") and not parse_rule_list(library["rules"], {}):
                continue
            path = Path(get_library_path(library["name"], str(root))).resolve()
            if not path.is_relative_to(library_root):
                raise ValueError("The profile's library path is outside its libraries folder.")
            if not path.is_file():
                # Missing files cannot supply a dependency. Normal launch repair
                # owns library installation; mod management never creates them.
                continue
            before = path.stat()
            try:
                entries = [{**mod, "bundled": False} for mod in _native_mods(path)]
                entries.extend({**mod, "bundled": True} for child in _bundled_mod_files(path) for mod in child.get("native", []))
            except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
                raise ValueError(f"Could not read the installed library {library['name']}. Repair the profile before installing mods.") from error
            if entries:
                snapshot[str(path)] = _digest(path)
                mods.extend({**mod, "runtime": True} for mod in entries if mod.get("id"))
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("The profile's runtime libraries changed while reading them. Review the installation again.")
        return tuple(mods), tuple(sorted(snapshot.items()))

    def _check_runtime_snapshot(self, plan: InstallPlan) -> None:
        if plan.runtime_snapshot is not None:
            _, snapshot = self.runtime_mods(plan.version_id)
            if snapshot != plan.runtime_snapshot:
                raise ValueError("The profile's Loader or runtime libraries changed. Review the installation again.")

    def profile_providers(self, minecraft_version: str, loader: str, loader_version: str,
                          version_id: str = "", *, runtime_only: bool = False) -> tuple[dict[str, list[dict]], tuple[tuple[str, str], ...] | None]:
        runtime_mods, runtime_snapshot = self.runtime_mods(version_id)
        mods = []
        for local in self.list_mods():
            if not local.enabled:
                continue
            path = self._path(local.filename)
            mods.extend({**mod, "bundled": False} for mod in _native_mods(path) if mod.get("id"))
            mods.extend({**mod, "bundled": True} for child in _bundled_mod_files(path)
                        for mod in child.get("native", []) if mod.get("id"))
        environment = InstallPlan((), (), loader=loader, loader_version=loader_version,
                                  minecraft_version=minecraft_version, runtime_mods=runtime_mods)
        providers = _native_providers(mods, environment)
        if runtime_only:
            # Installed mods can be upgraded together by Update all. Only fixed
            # runtime providers constrain discovery of each individual update.
            system = {"minecraft", "fabricloader", "quilt_loader", "forge", "neoforge"}
            providers = {target: candidates for target, candidates in providers.items()
                         if target in system or all(candidate.get("runtime") for candidate in candidates)}
        return providers, runtime_snapshot

    def _check_native_plan(self, client: ModrinthClient, plan: InstallPlan, installed: list[LocalMod]) -> tuple[InstallDependency, ...]:
        probe = getattr(client, "fabric_loader_metadata", None)
        if plan.loader != "fabric" or not callable(probe):
            return ()
        mods = []
        changed = set()
        for mod in installed:
            if not mod.enabled:
                continue
            path = self._path(mod.filename)
            entries = [{**entry, "bundled": False} for entry in _native_mods(path)]
            entries.extend({**entry, "bundled": True} for child in _bundled_mod_files(path) for entry in child.get("native", []))
            if mod.filename in plan.replacements:
                changed.update(entry["id"] for entry in entries if entry.get("id"))
            else:
                mods.extend({**entry, "new": False} for entry in entries if entry.get("id"))
        for version in plan.versions:
            if version["project_id"] in plan.disabled_projects:
                continue
            metadata = probe(version)
            for item in metadata:
                _check_loader_metadata(item, plan.loader, plan.loader_version, item.get("name") or version.get("name", "Mod"))
                for entry in item.get("native", []):
                    if entry.get("id"):
                        mods.append({**entry, "new": True, "bundled": item.get("bundled", False)})
                        changed.add(entry["id"])
        _check_native_requirements(mods, changed, plan)
        providers = _native_providers(mods, plan)
        reused = {}
        for mod in mods:
            if not mod["new"]:
                continue
            for rule in mod["rules"]:
                target = rule.get("target")
                if rule.get("optional") or rule.get("conflict") or target in {"fabricloader", "minecraft", "java"}:
                    continue
                for provider in providers.get(target, []):
                    if provider.get("runtime") and mod_version_satisfies(provider["version"], rule["requirement"]) is True:
                        reused[target] = InstallDependency("runtime:" + target, provider["name"],
                            {"version_number": provider["version"], "runtime": True}, True, True, True)
                        break
        return tuple(reused.values())

    @staticmethod
    def _valid_identity(version: object, digest: str) -> bool:
        return (isinstance(version, dict) and isinstance(version.get("id"), str)
                and bool(version["id"]) and isinstance(version.get("project_id"), str)
                and bool(version["project_id"]) and isinstance(version.get("files"), list)
                and any(isinstance(file, dict) and isinstance(file.get("hashes"), dict)
                        and str(file["hashes"].get("sha512", "")).lower() == digest
                        for file in version["files"]))

    def _identities(self) -> dict[str, dict]:
        """Optional checksum cache; a broken cache must not block local file actions."""
        try:
            if self.identities_path.stat().st_size > 8 * 1024 * 1024:
                return {}
            data = json.loads(self.identities_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {digest: version for digest, version in list(data.items())[-512:]
                        if re.fullmatch(r"[a-f0-9]{128}", digest) and self._valid_identity(version, digest)}
        except (OSError, ValueError, TypeError):
            pass
        return {}

    def _remember_identities(self, identities: dict[str, dict]) -> None:
        if not identities:
            return
        with self._lock:
            try:
                if not self.game_directory.is_dir():
                    return
                cached = self._identities()
                fields = {"id", "project_id", "name", "version_number", "version_type", "date_published",
                          "game_versions", "loaders", "dependencies", "files"}
                cached.update((digest, {key: value for key, value in version.items() if key in fields})
                              for digest, version in identities.items()
                              if self._valid_identity(version, digest))
                cached = dict(list(cached.items())[-512:])
                payload = _json_bytes(cached)
                if len(payload) <= 8 * 1024 * 1024:
                    self.data_directory.mkdir(parents=True, exist_ok=True)
                    _atomic_write(self.identities_path, payload)
            except (OSError, ValueError, TypeError):
                pass

    @staticmethod
    def _identity_record(version: dict, digest: str) -> dict:
        return {"project_id": version["project_id"], "version_id": version["id"],
                "name": version.get("name", ""), "version": version.get("version_number", ""),
                "sha512": digest}

    def identify_mods(self, client: ModrinthClient) -> tuple[list[LocalMod], tuple[str, ...]]:
        """Enrich external JARs by checksum, keeping local management usable offline."""
        with self._lock:
            mods = self.list_mods()
            snapshot = self._snapshot(mods)
        hashes = dict(snapshot)
        unknown = [mod for mod in mods if not mod.project_id]
        warnings = []
        matched = {}
        try:
            bulk = getattr(client, "versions_for_files", None)
            lookup = getattr(client, "version_for_file", None)
            if unknown and callable(bulk):
                matched = bulk(list(dict.fromkeys(hashes[mod.filename] for mod in unknown)))
            elif unknown and callable(lookup):
                for mod in unknown:
                    version = lookup(hashes[mod.filename])
                    if version:
                        matched[hashes[mod.filename]] = version
        except (ConnectionError, ValueError):
            warnings.append("Modrinth identification is unavailable. Local JAR metadata and cached identities are still available.")
        valid = {}
        for digest, version in matched.items():
            if not self._valid_identity(version, digest):
                warnings.append("Modrinth returned a mismatched local JAR identity; it was ignored.")
                continue
            valid[digest] = version
        with self._lock:
            self._check_snapshot(snapshot)
            self._remember_identities(valid)
        return [replace(mod, project_id=valid[hashes[mod.filename]]["project_id"],
                        version_id=valid[hashes[mod.filename]]["id"])
                if not mod.project_id and hashes[mod.filename] in valid else mod for mod in mods], tuple(warnings)

    def identify_local(self, filename: str, client: ModrinthClient) -> dict | None:
        digest = self.describe_mod(filename)["sha512"]
        cached = self._identities().get(digest)
        if cached:
            return cached
        version = client.version_for_file(digest)
        if version and not self._valid_identity(version, digest):
            raise ValueError("Modrinth returned a mismatched identity for a local mod.")
        if version:
            with self._lock:
                if self.describe_mod(filename)["sha512"] != digest:
                    raise ValueError("This mod changed during identification. Refresh and try again.")
                self._remember_identities({digest: version})
        return version

    @staticmethod
    def _update_context(minecraft_version: str, loader: str, loader_version: str, release_type: str) -> str:
        return json.dumps([minecraft_version, loader, loader_version, release_type], separators=(",", ":"))

    def _update_checks(self) -> dict:
        try:
            if self.updates_path.stat().st_size <= 8 * 1024 * 1024:
                data = json.loads(self.updates_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("format") == 3 and isinstance(data.get("checks"), dict):
                    return data["checks"]
        except (OSError, ValueError, TypeError):
            pass
        return {}

    def clear_update_check(self, minecraft_version: str, loader: str, *, loader_version: str = "", release_type: str = "release") -> None:
        with self._lock:
            checks = self._update_checks()
            key = self._update_context(minecraft_version, loader, loader_version, release_type)
            if key not in checks:
                return
            checks.pop(key)
            try:
                _atomic_write(self.updates_path, _json_bytes({"format": 3, "checks": checks}))
            except OSError:
                pass

    def saved_update_release_type(self, minecraft_version: str, loader: str, loader_version: str, version_id: str = "") -> str | None:
        """Remember the last checked release filter when reopening an instance."""
        with self._lock:
            checks = self._update_checks()
            for key in reversed(checks):
                try:
                    context = json.loads(key)
                    if (isinstance(context, list) and len(context) == 4 and context[:3] == [minecraft_version, loader, loader_version]
                            and context[3] in {"release", "all", "beta", "alpha"}
                            and checks[key].get("version_id", "") == version_id):
                        return context[3]
                except (TypeError, ValueError):
                    continue
        return None

    def _save_update_check(self, result: UpdateCheck, minecraft_version: str, loader: str,
                           loader_version: str, release_type: str) -> None:
        fields = {"id", "project_id", "name", "version_number", "version_type", "date_published",
                  "game_versions", "loaders", "dependencies", "files", "_fabric_loader_metadata"}
        try:
            with self._lock:
                self._check_snapshot(result.snapshot)
                if not self.game_directory.is_dir():
                    return
                checks = self._update_checks()
                key = self._update_context(minecraft_version, loader, loader_version, release_type)
                checks.pop(key, None)
                checks[key] = {
                    "checked_at": time.time(), "snapshot": result.snapshot, "warnings": result.warnings,
                    "version_id": result.version_id, "runtime_snapshot": result.runtime_snapshot,
                    "updates": [{"filename": update.mod.filename, "sha512": update.sha512,
                                 "project_id": update.mod.project_id, "version_id": update.mod.version_id,
                                 "version": {key: value for key, value in update.version.items() if key in fields}}
                                for update in result.updates],
                }
                payload = _json_bytes({"format": 3, "checks": dict(list(checks.items())[-16:])})
                if len(payload) <= 8 * 1024 * 1024:
                    self.data_directory.mkdir(parents=True, exist_ok=True)
                    _atomic_write(self.updates_path, payload)
        except (OSError, ValueError, TypeError):
            # An optional cache cannot turn a successful check into an error.
            pass

    def cached_update_check(self, minecraft_version: str, loader: str, *, loader_version: str = "",
                            release_type: str = "release", version_id: str = "") -> UpdateCheck | None:
        """Restore a saved result only for the same profile context and JAR bytes."""
        try:
            with self._lock:
                data = self._update_checks().get(self._update_context(minecraft_version, loader, loader_version, release_type))
                if not isinstance(data, dict) or not isinstance(data.get("snapshot"), list):
                    return None
                if data.get("version_id", "") != version_id:
                    return None
                runtime_snapshot = data.get("runtime_snapshot")
                runtime_snapshot = tuple((path, digest) for path, digest in runtime_snapshot) if runtime_snapshot is not None else None
                if self.runtime_mods(version_id)[1] != runtime_snapshot:
                    return None
                snapshot = tuple((name, digest) for name, digest in data["snapshot"])
                if any(not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{128}", digest)
                       for name, digest in snapshot):
                    return None
                self._check_snapshot(snapshot)
                mods = self.list_mods()
                local = {mod.filename: mod for mod in mods}
                updates = []
                seen = set()
                records = data.get("updates")
                warnings = data.get("warnings", [])
                if not isinstance(records, list) or len(records) > 512 or not isinstance(warnings, list) or any(not isinstance(warning, str) for warning in warnings):
                    return None
                for record in records:
                    mod = local[record["filename"]]
                    target = record["version"]
                    if (mod.filename in seen or dict(snapshot).get(mod.filename) != record["sha512"]
                            or not isinstance(record["project_id"], str) or not record["project_id"]
                            or not isinstance(record["version_id"], str) or not record["version_id"]
                            or not isinstance(target, dict) or target.get("project_id") != record["project_id"]
                            or target.get("id") == record["version_id"] or not isinstance(target.get("id"), str)
                            or minecraft_version not in target.get("game_versions", []) or loader not in target.get("loaders", [])
                            or loader == "fabric" and loader_version and version_loader_compatible(target, loader, loader_version) is not True
                            or release_type != "all" and target.get("version_type") != release_type
                            or mod.project_id and mod.project_id != record["project_id"]
                            or mod.version_id and mod.version_id != record["version_id"]):
                        return None
                    file = primary_file(target)
                    parsed = urlparse(str(file.get("url", "")))
                    hashes = file.get("hashes", {})
                    if parsed.scheme != "https" or parsed.hostname != "cdn.modrinth.com" or not any(
                            re.fullmatch(pattern, str(hashes.get(algorithm, "")))
                            for algorithm, pattern in (("sha512", r"[a-fA-F0-9]{128}"), ("sha1", r"[a-fA-F0-9]{40}"))):
                        return None
                    seen.add(mod.filename)
                    updates.append(ModUpdate(replace(mod, project_id=record["project_id"], version_id=record["version_id"]),
                                             target, record["sha512"]))
                return UpdateCheck(tuple(mods), tuple(updates), tuple(warnings), snapshot, version_id, runtime_snapshot)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def check_updates(self, client: ModrinthClient, minecraft_version: str, loader: str, *,
                      release_only: bool = True, release_type: str | None = None, loader_version: str = "", version_id: str = "") -> UpdateCheck:
        if loader not in SUPPORTED_LOADERS or not minecraft_version:
            raise ValueError("Choose a profile with a supported mod loader and Minecraft version.")
        refresh = getattr(client, "clear_cache", None)
        if callable(refresh):
            refresh()
        with self._lock:
            snapshot = self._snapshot()
        mods, identification_warnings = self.identify_mods(client)
        providers, runtime_snapshot = self.profile_providers(minecraft_version, loader, loader_version, version_id, runtime_only=True)
        with self._lock:
            self._check_snapshot(snapshot)
            identities = self._identities()
        hashes = dict(snapshot)
        updates = []
        warnings = list(identification_warnings)
        failed = any("identification is unavailable" in warning.lower() for warning in identification_warnings)
        for mod in mods:
            if not mod.project_id:
                warnings.append(f"{mod.name}: this JAR is not identified on Modrinth; no update check is available.")
                continue
            try:
                current = identities.get(hashes[mod.filename])
                if not current or current.get("id") != mod.version_id or current.get("project_id") != mod.project_id:
                    current = client.version(mod.version_id)
                if current.get("id") != mod.version_id or current.get("project_id") != mod.project_id:
                    raise ValueError("The installed mod's identity does not match.")
                versions = client.project_versions(mod.project_id, minecraft_version, loader)
                candidate = newer_compatible_version(current, versions, minecraft_version, loader,
                                                     release_only=release_only, release_type=release_type,
                                                     check_loader=lambda version: version_matches_environment(client, version, loader, loader_version, providers))
                if candidate:
                    primary_file(candidate)
                    updates.append(ModUpdate(mod, candidate, hashes[mod.filename]))
            except ConnectionError:
                failed = True
                warnings.append("Modrinth is unavailable. Some update checks could not finish; retry when the connection is restored.")
                break
            except (ValueError, KeyError, TypeError) as error:
                failed = True
                warnings.append(f"{mod.name}: update check could not finish ({error}).")
        with self._lock:
            self._check_snapshot(snapshot)
        if self.runtime_mods(version_id)[1] != runtime_snapshot:
            raise ValueError("The profile's runtime changed during the update check. Check updates again.")
        result = UpdateCheck(tuple(mods), tuple(updates), tuple(warnings), snapshot, version_id, runtime_snapshot)
        if not failed:
            self._save_update_check(result, minecraft_version, loader, loader_version,
                                    release_type if release_type is not None else "release" if release_only else "all")
        return result

    def _index(self) -> dict:
        if not self.index_path.exists():
            return {}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and all(isinstance(item, dict) for item in data.values()):
                return data
        except (OSError, ValueError):
            pass
        raise ValueError("The mod index could not be read. Restore gr8launcher/mods.json before changing mods.")

    def _save_index(self, index: dict) -> None:
        self.data_directory.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.index_path, _json_bytes(index))

    def _path(self, filename: str) -> Path:
        base = jar_base_name(filename)
        safe_jar_name(base)
        path = self.mods_directory / filename
        if path.is_symlink() or path.resolve().parent != self.mods_directory.resolve():
            raise ValueError("The mod file points outside the mods folder.")
        return path

    def _snapshot(self, mods: list[LocalMod] | None = None) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((mod.filename, _digest(self._path(mod.filename)))
                            for mod in (self.list_mods() if mods is None else mods)))

    def _check_snapshot(self, snapshot: tuple[tuple[str, str], ...] | None) -> None:
        if snapshot is not None and snapshot != self._snapshot():
            raise ValueError("The installed mods changed. Refresh and confirm the installation again.")

    def _check_mod_ids(self, files: list[tuple[Path, str]], installed: list[LocalMod], *, allow_unreadable: bool = False) -> None:
        ids = {mod_id for mod in installed for mod_id in (mod.mod_ids or ((mod.mod_id,) if mod.mod_id else ()))}
        for staged, name in files:
            try:
                metadata = _metadata(staged)
            except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
                if not allow_unreadable:
                    raise
                metadata = {}
            incoming = set(metadata.get("mod_ids", ()))
            duplicates = incoming & ids
            if duplicates:
                raise FileExistsError(f"A copy of {sorted(duplicates)[0]} already exists. Remove it before adding {name}.")
            ids.update(incoming)

    def _backup_path(self, token: str, filename: str) -> Path:
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise ValueError("Invalid mod transaction backup.")
        self._path(filename)
        folder = self.data_directory / "mod-trash" / token
        path = folder / filename
        if folder.is_symlink() or path.is_symlink():
            raise ValueError("Invalid mod transaction backup.")
        return path

    def recover_pending(self) -> None:
        """Roll back a process-interrupted commit before reading or launching mods."""
        with self._lock:
            if not self.transaction_path.exists():
                return
            try:
                transaction = json.loads(self.transaction_path.read_text(encoding="utf-8"))
                if transaction["format"] != 1 or transaction["state"] not in ("pending", "committed"):
                    raise ValueError("Invalid transaction format.")
                files = transaction["files"]
                backups = transaction["backups"]
                if not isinstance(files, list) or not isinstance(backups, list):
                    raise ValueError("Invalid transaction files.")
                for item in files + backups:
                    self._path(item["filename"])
                    if not re.fullmatch(r"[a-f0-9]{128}", item["sha512"]):
                        raise ValueError("Invalid transaction checksum.")
                for item in backups:
                    self._backup_path(item["token"], item["filename"])
                old_index, new_index = transaction["index_before"], transaction["index_after"]
                for contents in (old_index, new_index):
                    if contents is not None:
                        document = json.loads(contents)
                        if not isinstance(document, dict) or any(not isinstance(value, dict) for value in document.values()):
                            raise ValueError("Invalid transaction index.")
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                raise ValueError("The interrupted mod transaction could not be read. Restore gr8launcher/mod-transaction.json before changing mods.") from error
            if transaction["state"] == "committed":
                self.transaction_path.unlink()
                self._metadata_cache.clear()
                return

            # Check every affected path before deleting anything. An externally
            # changed file is kept for manual recovery rather than overwritten.
            unmoved = set()
            restore = []
            for item in backups:
                original = self._path(item["filename"])
                backup = self._backup_path(item["token"], item["filename"])
                if backup.is_file() and _digest(backup) == item["sha512"]:
                    restore.append((original, backup))
                elif not backup.exists() and original.is_file() and _digest(original) == item["sha512"]:
                    unmoved.add(item["filename"].casefold())
                else:
                    raise ValueError("An interrupted mod replacement changed on disk. Recover its JAR from launcher trash before continuing.")
            remove = []
            for item in files:
                target = self._path(item["filename"])
                if item["filename"].casefold() in unmoved:
                    continue
                if target.exists():
                    if not target.is_file() or _digest(target) != item["sha512"]:
                        raise ValueError("An interrupted mod installation changed on disk. Existing files were kept for manual recovery.")
                    remove.append(target)
            removing = set(remove)
            if any(original.exists() and original not in removing for original, _ in restore):
                raise ValueError("A file blocks recovery of the previous mod. Existing files were kept.")
            if new_index is not None:
                current_index = self.index_path.read_bytes().decode("utf-8") if self.index_path.exists() else None
                if current_index not in (old_index, new_index):
                    raise ValueError("The mod index changed after an interrupted installation. Existing files were kept for manual recovery.")
            for path in remove:
                path.unlink()
            for original, backup in restore:
                backup.rename(original)
            for item in backups:
                folder = self._backup_path(item["token"], item["filename"]).parent
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            if new_index is not None and current_index != old_index:
                if old_index is None:
                    self.index_path.unlink(missing_ok=True)
                else:
                    _atomic_write(self.index_path, old_index.encode("utf-8"))
            self.transaction_path.unlink()
            self._metadata_cache.clear()

    def list_mods(self) -> list[LocalMod]:
        with self._lock:
            self.recover_pending()
            index = {key.casefold(): value for key, value in self._index().items()}
            identities = self._identities()
            if not self.mods_directory.exists():
                self._metadata_cache.clear()
                return []
            result = []
            current_keys = set()
            for path in self.mods_directory.iterdir():
                if not path.is_file() or path.is_symlink() or not path.name.lower().endswith((".jar", ".jar.disabled")):
                    continue
                stat = path.stat()
                base = jar_base_name(path.name)
                managed = index.get(base.casefold(), {})
                key = (str(path), stat.st_mtime_ns, stat.st_size)
                current_keys.add(key)
                if key not in self._metadata_cache:
                    try:
                        self._metadata_cache[key] = _metadata(path)
                    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
                        self._metadata_cache[key] = {"description": "Unreadable JAR; replace it before launching."}
                metadata = self._metadata_cache[key]
                if managed or identities:
                    digest = _digest(path)
                    if metadata.get("_sha512") and metadata["_sha512"] != digest:
                        try:
                            metadata = _metadata(path)
                        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
                            metadata = {"description": "Unreadable JAR; replace it before launching."}
                        self._metadata_cache[key] = metadata
                    metadata["_sha512"] = digest
                if managed:
                    if metadata["_sha512"] != managed.get("sha512"):
                        managed = {}
                if not managed and identities:
                    version = identities.get(metadata["_sha512"])
                    if version:
                        managed = self._identity_record(version, metadata["_sha512"])
                result.append(LocalMod(
                    filename=path.name, name=metadata.get("name") or managed.get("name") or base[:-4],
                    version=metadata.get("version") or managed.get("version") or "",
                    description=metadata.get("description", ""), enabled=not path.name.lower().endswith(".disabled"),
                    size=stat.st_size, mod_id=metadata.get("mod_id", ""),
                    project_id=managed.get("project_id", ""), version_id=managed.get("version_id", ""),
                    icon=metadata.get("icon"),
                    mod_ids=metadata.get("mod_ids", ()),
                ))
            if len(self._metadata_cache) > len(current_keys):
                self._metadata_cache = {
                    key: metadata for key, metadata in self._metadata_cache.items()
                    if key in current_keys
                }
            return sorted(result, key=lambda item: (item.name.casefold(), item.filename.casefold()))

    def set_enabled(self, filename: str, enabled: bool) -> None:
        with self._lock:
            self.recover_pending()
            source = self._path(filename)
            if enabled == (not filename.lower().endswith(".disabled")):
                return
            target_name = jar_base_name(filename) + ("" if enabled else ".disabled")
            target = self._path(target_name)
            if any(path.name.casefold() == target_name.casefold() for path in self.mods_directory.iterdir()):
                raise FileExistsError("Both enabled and disabled copies exist. Remove the duplicate first.")
            source.rename(target)

    def describe_mod(self, filename: str) -> dict:
        with self._lock:
            self.recover_pending()
            path = self._path(filename)
            return {**_metadata(path), "filename": filename, "size": path.stat().st_size,
                    "sha512": _digest(path), "enabled": not filename.lower().endswith(".disabled")}

    def remove(self, filename: str) -> str:
        """Move a JAR to launcher trash; removal is reversible and never recursive."""
        with self._lock:
            self.recover_pending()
            source = self._path(filename)
            trash = self.data_directory / "mod-trash" / uuid.uuid4().hex
            trash.mkdir(parents=True)
            source.rename(trash / filename)
            return trash.name

    def restore(self, token: str) -> None:
        with self._lock:
            self.recover_pending()
            if not re.fullmatch(r"[a-f0-9]{32}", token):
                raise ValueError("Invalid mod restore token.")
            trash = self.data_directory / "mod-trash" / token
            if trash.is_symlink():
                raise ValueError("Invalid mod trash folder.")
            files = list(trash.iterdir())
            if len(files) != 1 or files[0].is_symlink() or not files[0].is_file():
                raise ValueError("The removed mod could not be restored.")
            destination = self._path(files[0].name)
            base = jar_base_name(files[0].name)
            installed = self.list_mods()
            if any(jar_base_name(mod.filename).casefold() == base.casefold() for mod in installed):
                raise FileExistsError("A copy of this mod already exists. Remove it before restoring.")
            self._check_mod_ids([(files[0], files[0].name)], installed, allow_unreadable=True)
            self.mods_directory.mkdir(parents=True, exist_ok=True)
            files[0].rename(destination)
            trash.rmdir()

    def import_jars(self, sources: list[Path], *, loader: str = "", loader_version: str = "") -> int:
        with self._lock:
            self.recover_pending()
            self.data_directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="mod-import-", dir=self.data_directory) as stage:
                files = []
                for source in sources:
                    source = Path(source)
                    name = safe_jar_name(source.name)
                    if source.stat().st_size > MAX_JAR_BYTES or not zipfile.is_zipfile(source):
                        raise ValueError(f"{name} is not a valid mod JAR.")
                    _metadata(source)
                    target = Path(stage) / name
                    if target.exists():
                        raise FileExistsError(f"{name} was selected more than once.")
                    shutil.copyfile(source, target)
                    _check_jar_loader(target, loader, loader_version)
                    files.append((target, name))
                self._commit(files)
            return len(sources)

    def _commit(self, files: list[tuple[Path, str]], index: dict | None = None, *, replacements: tuple[str, ...] = ()) -> None:
        """Publish whole staged JARs with a journal for exception/crash rollback."""
        with self._lock:
            self.recover_pending()
            self.mods_directory.mkdir(parents=True, exist_ok=True)
            self.data_directory.mkdir(parents=True, exist_ok=True)
            replacing = {self._path(name) for name in replacements}
            for path in replacing:
                if not path.is_file():
                    raise FileNotFoundError("The installed mod changed. Refresh before replacing its version.")
            installed = [mod for mod in self.list_mods() if self._path(mod.filename) not in replacing]
            names = set()
            for staged, name in files:
                self._path(name)
                base = jar_base_name(name).casefold()
                if base in names or any(jar_base_name(mod.filename).casefold() == base for mod in installed):
                    raise FileExistsError(f"{name} already exists. Existing mods were kept.")
                names.add(base)
                if staged.is_symlink() or not staged.is_file():
                    raise ValueError("The staged mod is not a regular file.")
                # Both import/download staging directories are on the game volume.
                with staged.open("rb+") as stream:
                    os.fsync(stream.fileno())
            self._check_mod_ids(files, installed)
            transaction = {
                "format": 1, "state": "pending",
                "files": [{"filename": name, "sha512": _digest(staged)} for staged, name in files],
                "backups": [{"filename": path.name, "token": uuid.uuid4().hex, "sha512": _digest(path)}
                            for path in sorted(replacing)],
                "index_before": self.index_path.read_bytes().decode("utf-8") if self.index_path.exists() else None,
                "index_after": _json_bytes(index).decode("utf-8") if index is not None else None,
            }
            _atomic_write(self.transaction_path, _json_bytes(transaction))
            try:
                for item in transaction["backups"]:
                    backup = self._backup_path(item["token"], item["filename"])
                    backup.parent.mkdir(parents=True)
                    self._path(item["filename"]).rename(backup)
                for staged, name in files:
                    destination = self._path(name)
                    if os.name == "nt":
                        # Windows rename fails if the destination already exists.
                        staged.rename(destination)
                    else:
                        # link publishes a complete file and never overwrites on POSIX.
                        os.link(staged, destination)
                if index is not None:
                    self._save_index(index)
                transaction["state"] = "committed"
                _atomic_write(self.transaction_path, _json_bytes(transaction))
            except Exception:
                self.recover_pending()
                raise
            self.transaction_path.unlink()
            self._metadata_cache.clear()

    def plan_updates(self, client: ModrinthClient, updates: tuple[ModUpdate, ...], minecraft_version: str,
                     loader: str, *, loader_version: str = "", release_type: str = "release", version_id: str = "") -> InstallPlan:
        """Revalidate checked versions and resolve all replacements as one graph."""
        if not updates:
            raise ValueError("There are no updates to install.")
        with self._lock:
            snapshot = dict(self._snapshot())
            identities = self._identities()
            if any(snapshot.get(update.mod.filename) != update.sha512 for update in updates):
                raise ValueError("A mod changed since the update check. Refresh and check updates again.")
        versions = tuple(_MOD_METADATA_POOL.map(client.version, [update.version["id"] for update in updates]))
        for update, version in zip(updates, versions):
            if version.get("id") != update.version["id"] or version.get("project_id") != update.mod.project_id:
                raise ValueError("An update's project or version identity changed. Check updates again.")
            current = identities.get(update.sha512) or client.version(update.mod.version_id)
            if (current.get("id") != update.mod.version_id or current.get("project_id") != update.mod.project_id
                    or newer_compatible_version(current, [version], minecraft_version, loader, release_type=release_type,
                                                check_loader=lambda candidate: version_matches_loader(client, candidate, loader, loader_version)) is None):
                raise ValueError("A checked update is no longer newer or compatible. Check updates again.")
        plan = self.plan_install(client, versions[0]["project_id"], minecraft_version, loader,
                                 selected_versions=versions, loader_version=loader_version, version_id=version_id)
        if dict(plan.snapshot or ()) != snapshot:
            raise ValueError("Installed mods changed while preparing updates. Check updates again.")
        return plan

    def plan_install(self, client: ModrinthClient, project_id: str, minecraft_version: str, loader: str, *, selected_version: dict | None = None, loader_version: str = "", optional_projects: tuple[str, ...] = (), selected_versions: tuple[dict, ...] = (), version_id: str = "") -> InstallPlan:
        if loader not in SUPPORTED_LOADERS or not minecraft_version:
            raise ValueError("Choose a profile with a supported mod loader and Minecraft version.")
        with self._lock:
            installed = self.list_mods()
            snapshot = self._snapshot(installed)
        runtime_mods, runtime_snapshot = self.runtime_mods(version_id)
        native_installed = []
        for local in installed:
            if local.enabled:
                path = self._path(local.filename)
                native_installed.extend({**mod, "bundled": False, "filename": local.filename} for mod in _native_mods(path) if mod.get("id"))
                native_installed.extend({**mod, "bundled": True, "filename": local.filename} for child in _bundled_mod_files(path)
                                        for mod in child.get("native", []) if mod.get("id"))
        environment = InstallPlan((), (), loader=loader, loader_version=loader_version, minecraft_version=minecraft_version,
                                  runtime_mods=runtime_mods)
        providers = _native_providers(native_installed, environment)

        def select_version(project: str) -> dict:
            return _select_compatible_version(client, project, minecraft_version, loader, loader_version, providers)
        checksums = dict(snapshot)
        projects: dict[str, LocalMod] = {}
        version_cache: dict[str, dict] = {}
        identifications = {}
        planned: dict[str, dict] = {}
        already = []
        replacements = []
        disabled_projects = []
        resolved: dict[str, dict] = {}
        provided: dict[str, dict] | None = None
        provided_names: dict[str, str] = {}
        provided_metadata: dict[str, dict] = {}
        warnings = []
        dependency_records: dict[str, InstallDependency] = {}
        optional_declarations: list[dict] = []
        labels: dict[str, str] = {}
        base_required: set[str] | None = None
        optional_candidates: dict[str, dict] = {}
        optional_errors: dict[tuple, Exception] = {}
        native_planned: dict[str, list[dict]] = {}
        identities = self._identities()
        for version in identities.values():
            version_cache[version["id"]] = version

        def remember(version: dict) -> dict:
            if not isinstance(version, dict) or not version.get("project_id") or not version.get("id"):
                raise ValueError("Modrinth returned an invalid mod version.")
            version_cache[version["id"]] = version
            return version

        def get_version(version_id: str) -> dict:
            version = version_cache.get(version_id)
            if version is None:
                version = remember(client.version(version_id))
            if version["id"] != version_id:
                raise ValueError("Modrinth returned a different mod version than requested.")
            return version

        root_versions = tuple(remember(version) for version in selected_versions) if selected_versions else (
            remember(selected_version or select_version(project_id)),)
        root_version = root_versions[0]
        if root_version["project_id"] != project_id and (selected_version or selected_versions):
            raise ValueError("The selected version belongs to a different mod.")
        selected_roots = {version["project_id"]: version for version in root_versions} if selected_version or selected_versions else {}
        if len({version["project_id"] for version in root_versions}) != len(root_versions):
            raise ValueError("Multiple updates request the same mod project.")

        def register(local: LocalMod, version: dict | None = None) -> LocalMod:
            if version:
                remember(version)
                local = replace(local, project_id=version["project_id"], version_id=version["id"])
                identifications[jar_base_name(local.filename)] = {
                    "project_id": local.project_id, "version_id": local.version_id,
                    "name": version.get("name", local.name), "version": version.get("version_number", local.version),
                    "sha512": checksums[local.filename],
                }
            prior = projects.get(local.project_id)
            if prior and prior.filename != local.filename:
                raise ValueError(f"Multiple copies of {local.name} are installed. Remove the duplicate before continuing.")
            projects[local.project_id] = local
            return local

        def matches(local: LocalMod, file: dict) -> bool:
            hashes = file.get("hashes", {})
            if hashes.get("sha512"):
                return checksums[local.filename] == str(hashes["sha512"]).lower()
            return bool(hashes.get("sha1") and _digest(self._path(local.filename), "sha1") == str(hashes["sha1"]).lower())

        # Identification is read-only during planning. It becomes index metadata
        # only after a confirmed installation, including imported/renamed JARs.
        lookup = getattr(client, "version_for_file", None)
        identification_available = True
        bulk_lookup = getattr(client, "versions_for_files", None)
        unknown_hashes = list(dict.fromkeys(checksums[local.filename] for local in installed if not local.project_id))
        matched_external = None
        if unknown_hashes and callable(bulk_lookup):
            try:
                matched_external = bulk_lookup(unknown_hashes)
            except ConnectionError:
                identification_available = False
                warnings.append("Some external JARs could not be identified online. Their local metadata remains available; online dependency checks for them are limited.")
        for local in installed:
            if local.project_id:
                register(local)
            elif (matched_external is not None or callable(lookup)) and identification_available:
                try:
                    version = matched_external.get(checksums[local.filename]) if matched_external is not None else lookup(checksums[local.filename])
                except ConnectionError:
                    identification_available = False
                    warnings.append("Some external JARs could not be identified online. Their local metadata remains available; online dependency checks for them are limited.")
                    continue
                if version:
                    remember(version)
                    if not any(matches(local, file) for file in version.get("files", [])):
                        raise ValueError("Modrinth returned a mismatched identity for a local mod.")
                    register(local, version)

        # Exclude every old replacement's bundled libraries before traversing
        # any root, including roots reached through another root's requirements.
        replacements.extend(local.filename for project, local in projects.items()
                            if project in selected_roots and local.version_id != selected_roots[project]["id"])

        def installed_version(local: LocalMod) -> dict:
            version = get_version(local.version_id)
            if version["project_id"] != local.project_id:
                raise ValueError("The installed mod's project identity is inconsistent. Refresh before continuing.")
            return version

        def bundled_versions() -> dict[str, dict]:
            nonlocal provided
            if provided is not None:
                return provided
            provided = {}
            files = {}
            for local in installed:
                if local.enabled and local.filename not in replacements:
                    for file in _bundled_mod_files(self._path(local.filename)):
                        files[file["sha512"]] = file
            if not files:
                return provided
            bulk_lookup = getattr(client, "versions_for_files", None)
            matched = {digest: identities[digest] for digest in files if digest in identities}
            missing = [digest for digest in files if digest not in matched]
            try:
                if missing and callable(bulk_lookup):
                    matched.update(bulk_lookup(missing))
                elif missing and callable(lookup):
                    for digest in missing:
                        version = lookup(digest)
                        if version:
                            matched[digest] = version
            except ConnectionError:
                warnings.append("Some bundled libraries could not be identified online. Only verified dependencies can be reused.")
            for digest, file in files.items():
                version = matched.get(digest)
                if not version:
                    continue
                remember(version)
                if not any(item.get("hashes", {}).get("sha512") == digest for item in version.get("files", [])):
                    raise ValueError("Modrinth returned a mismatched identity for a bundled mod.")
                project = version["project_id"]
                prior = provided.get(project)
                if prior is None or version.get("date_published", "") > prior.get("date_published", ""):
                    provided[project] = version
                    provided_names[project] = file["name"]
                    provided_metadata[project] = file["metadata"]
            return provided

        def project_label(project: str) -> str:
            if project in projects:
                return projects[project].name
            if project in provided_names:
                return provided_names[project]
            if project in labels:
                return labels[project]
            fetch = getattr(client, "project", None)
            if callable(fetch):
                try:
                    data = fetch(project)
                    if isinstance(data, dict) and data.get("title"):
                        labels[project] = data["title"]
                        return labels[project]
                except (ConnectionError, ValueError, KeyError):
                    pass
            return project

        def dependency_version(dependency: dict) -> tuple[dict, bool]:
            if dependency.get("version_id"):
                child = get_version(dependency["version_id"])
                if dependency.get("project_id") and child["project_id"] != dependency["project_id"]:
                    raise ValueError("Modrinth returned a different dependency than requested.")
                local_child = projects.get(child["project_id"])
                candidate = bundled_versions().get(child["project_id"]) if not local_child or not local_child.enabled else None
                return child, bool(candidate and candidate["id"] == child["id"])
            dependency_project = dependency.get("project_id")
            if not dependency_project:
                raise ValueError("This dependency cannot be resolved automatically.")
            child_bundled = False
            if dependency_project in selected_roots:
                child = selected_roots[dependency_project]
            elif dependency_project in resolved:
                child = resolved[dependency_project]
            elif dependency_project in projects and projects[dependency_project].enabled:
                child = installed_version(projects[dependency_project])
            elif dependency_project in bundled_versions():
                child = bundled_versions()[dependency_project]
                child_bundled = True
            elif dependency_project in projects:
                child = installed_version(projects[dependency_project])
            else:
                child = remember(optional_candidates.get(dependency_project) or select_version(dependency_project))
            if child["project_id"] != dependency_project:
                raise ValueError("Modrinth returned a different dependency than requested.")
            return child, child_bundled

        def record_dependency(child: dict, *, required: bool, bundled: bool, issue: str = "") -> None:
            project = child["project_id"]
            if base_required is not None and project in optional_projects and project not in base_required:
                required = False
            prior = dependency_records.get(project)
            if prior and prior.required and not required:
                return
            local = projects.get(project)
            present = bool(bundled or local and local.enabled and local.version_id == child["id"])
            name = local.name if local else provided_names.get(project) or labels.get(project) or child.get("name") or project
            dependency_records[project] = InstallDependency(project, name, child, required, present, bundled, issue)

        def visit(version: dict, *, root=False, bundled=False) -> None:
            remember(version)
            if not bundled and (minecraft_version not in version.get("game_versions", []) or loader not in version.get("loaders", [])):
                raise ValueError("A required mod version is incompatible with this profile.")
            project = version["project_id"]
            if project in selected_roots and version["id"] != selected_roots[project]["id"]:
                raise ValueError(f"{project_label(project)} is pinned to a different version by another mod in this update batch.")
            if project in resolved:
                if resolved[project]["id"] != version["id"]:
                    raise ValueError("Required dependencies request conflicting mod versions.")
                return
            file = primary_file(version)
            existing = None if bundled else projects.get(project)
            if existing is None and not bundled:
                for local in installed:
                    if local.project_id and local.project_id != project:
                        continue
                    if matches(local, file):
                        existing = register(local, version)
                        break
                    if jar_base_name(local.filename).casefold() == file["filename"].casefold():
                        raise FileExistsError(f"{file['filename']} already exists with different contents.")
            replacing = False
            if existing:
                replacing = bool(project in selected_roots and existing.version_id != version["id"])
                if not existing.enabled and not replacing:
                    raise ValueError(f"Enable {existing.name} in Installed before installing this mod.")
                if existing.version_id != version["id"] and not replacing:
                    raise ValueError(f"{existing.name} {existing.version} is installed, but this mod requests {version.get('version_number') or version['id']}. The dependency will not be replaced automatically because other mods may need it. Choose another mod version or review its dependents before updating it in Installed.")
                if replacing:
                    if existing.filename not in replacements:
                        replacements.append(existing.filename)
                    if not existing.enabled:
                        disabled_projects.append(project)
                else:
                    already.append(existing.name)
                    _check_jar_loader(self._path(existing.filename), loader, loader_version)
            elif bundled:
                _check_loader_metadata(provided_metadata.get(project, {}), loader, loader_version,
                                       provided_names.get(project, project))
                already.append(f"{provided_names.get(project, version.get('name') or project)} (bundled)")
            if len(resolved) >= (max(60, len(root_versions) + 60) if selected_versions else 60):
                raise ValueError("This mod has too many required dependencies.")
            resolved[project] = version
            # Reused versions still need their complete dependency graph checked.
            for dependency in version.get("dependencies", []):
                if dependency.get("dependency_type") == "optional":
                    optional_declarations.append(dependency)
                    continue
                if dependency.get("dependency_type") != "required":
                    continue
                child, child_bundled = dependency_version(dependency)
                record_dependency(child, required=True, bundled=child_bundled)
                visit(child, bundled=child_bundled)
            if (existing is None and not bundled) or replacing:
                probe = getattr(client, "fabric_loader_metadata", None)
                resolver = getattr(client, "native_dependency", None)
                if loader == "fabric" and callable(probe) and callable(resolver):
                    metadata = probe(version)
                    native_planned[project] = [{**mod, "bundled": item.get("bundled", False)}
                        for item in metadata for mod in item.get("native", []) if mod.get("id")]
                    for native in native_planned[project]:
                        for rule in native["rules"]:
                            target = rule.get("target")
                            if (rule.get("optional") or rule.get("conflict")
                                    or target in {"minecraft", "java", "fabricloader", "quilt_loader", "forge", "neoforge"}):
                                continue
                            available = _native_providers(
                                [mod for mod in native_installed if mod["filename"] not in replacements]
                                + [mod for entries in native_planned.values() for mod in entries], environment)
                            if target in available:
                                continue  # Final preflight checks the required range.
                            disabled = next((local for local in installed if not local.enabled and any(
                                mod.get("id") == target for mod in _native_mods(self._path(local.filename)))), None)
                            if disabled:
                                raise ValueError(f"Enable {disabled.name} in Installed before installing this mod.")
                            child = resolver(target, rule["requirement"], minecraft_version, loader, loader_version, available)
                            if child:
                                record_dependency(child, required=True, bundled=False)
                                visit(child)
                planned[project] = version

        for version in root_versions:
            visit(version, root=True)
        base_required = set(resolved)
        # Optional candidates are independent until selected. Fetch their metadata
        # together, then visit selected graphs sequentially to enforce shared pins.
        provided_versions = bundled_versions()
        candidate_requests = {}
        for dependency in optional_declarations:
            key = (dependency.get("project_id"), dependency.get("version_id"))
            if key in candidate_requests or len(candidate_requests) >= 60:
                continue
            version_id, project = dependency.get("version_id"), dependency.get("project_id")
            if version_id and version_id not in version_cache:
                candidate_requests[key] = (client.version, (version_id,))
            elif not version_id and project and project not in projects and project not in resolved and project not in provided_versions:
                candidate_requests[key] = (select_version, (project,))
        if candidate_requests:
            pending = {key: _MOD_METADATA_POOL.submit(fetch, *args) for key, (fetch, args) in candidate_requests.items()}
            for key, future in pending.items():
                try:
                    candidate = remember(future.result())
                    if not key[1]:
                        optional_candidates[key[0]] = candidate
                except Exception as error:
                    optional_errors[key] = error
        # Optional dependencies never enter the install graph without selection.
        # Resolve their rows after required mods so shared libraries stay required.
        checked_optional = set()
        for dependency in optional_declarations:
            key = (dependency.get("project_id"), dependency.get("version_id"))
            if key in checked_optional:
                continue
            checked_optional.add(key)
            if len(checked_optional) > 60:
                warnings.append("Additional optional dependencies were omitted from this review.")
                break
            selected_optional = False
            try:
                if key in optional_errors:
                    raise optional_errors[key]
                child, child_bundled = dependency_version(dependency)
                project = child["project_id"]
                if project in {version["project_id"] for version in root_versions}:
                    continue
                if project in optional_projects:
                    selected_optional = True
                    record_dependency(child, required=False, bundled=child_bundled)
                    visit(child, bundled=child_bundled)
                else:
                    issue = ""
                    local = projects.get(project)
                    if local and not local.enabled and not child_bundled:
                        issue = "Enable this dependency in Installed first."
                    elif local and local.enabled and local.version_id != child["id"]:
                        issue = "A different version is installed; review it in Installed."
                    elif not child_bundled and (minecraft_version not in child.get("game_versions", []) or loader not in child.get("loaders", [])):
                        issue = "No compatible version for this profile."
                    elif not local and not child_bundled:
                        filename = primary_file(child)["filename"].casefold()
                        if any(jar_base_name(item.filename).casefold() == filename and not matches(item, primary_file(child)) for item in installed):
                            issue = "A local JAR already uses this filename. Review it in Installed."
                    record_dependency(child, required=False, bundled=child_bundled, issue=issue)
            except (ConnectionError, ValueError, LookupError, StopIteration) as error:
                project = dependency.get("project_id") or dependency.get("version_id") or dependency.get("file_name") or "Unknown dependency"
                if selected_optional or project in optional_projects:
                    raise ValueError(f"Cannot include optional dependency {project_label(project)}: {error}") from error
                if project not in dependency_records:
                    dependency_records[project] = InstallDependency(project, labels.get(project) or project, None, False,
                                                                   issue="No compatible version could be verified.")
        unnamed = [project for project in dependency_records if project not in projects and project not in provided_names]
        if unnamed:
            for project, name in zip(unnamed, _MOD_METADATA_POOL.map(project_label, unnamed)):
                dependency_records[project] = replace(dependency_records[project], name=name)
        unidentified = [local.name for local in installed if not local.project_id
                        and jar_base_name(local.filename) not in identifications]
        if unidentified:
            warnings.append("Online dependency checks are unavailable for these local JARs: "
                            + ", ".join(unidentified[:5]) + ("…" if len(unidentified) > 5 else "") + ".")
        active = dict(bundled_versions())
        active.update((project, installed_version(local)) for project, local in projects.items()
                      if local.enabled and local.filename not in replacements)
        active.update((project, version) for project, version in resolved.items() if project not in disabled_projects)
        # Check both directions: new declarations and requirements/conflicts from
        # existing enabled mods against the complete resulting set of versions.
        for version in active.values():
            for dependency in version.get("dependencies", []):
                kind = dependency.get("dependency_type")
                if kind not in ("required", "incompatible"):
                    continue
                if dependency.get("version_id"):
                    target = next((item for item in active.values() if item["id"] == dependency["version_id"]), None)
                    if target and dependency.get("project_id") and target["project_id"] != dependency["project_id"]:
                        target = None
                else:
                    target = active.get(dependency.get("project_id"))
                if kind == "required" and target is None:
                    dependency_project = dependency.get("project_id")
                    if not dependency_project and dependency.get("version_id"):
                        dependency_project = get_version(dependency["version_id"])["project_id"]
                    # An unrelated mod's pre-existing missing requirement must
                    # not block every install. Requirements of the visited graph
                    # and dependents affected by this batch remain enforced.
                    if version["project_id"] in resolved or dependency_project in planned:
                        required = project_label(dependency_project) if dependency_project else dependency.get("file_name") or "another mod"
                        if dependency.get("version_id"):
                            required += f" ({get_version(dependency['version_id']).get('version_number') or dependency['version_id']})"
                        raise ValueError(f"{project_label(version['project_id'])} requires {required}. Install or enable the required version before continuing.")
                if kind == "incompatible" and target is not None:
                    raise ValueError(f"{project_label(version['project_id'])} conflicts with {project_label(target['project_id'])}. Remove or disable the conflicting mod first.")
        names = [primary_file(item)["filename"].casefold() for item in planned.values()]
        if len(names) != len(set(names)):
            raise ValueError("Two required mods have the same filename.")
        with self._lock:
            self._check_snapshot(snapshot)
        plan = InstallPlan(tuple(planned.values()), tuple(dict.fromkeys(already)), tuple(replacements),
                           tuple(disabled_projects), snapshot, tuple(identifications.items()),
                           loader, loader_version, tuple(warnings),
                           tuple(item for project, item in dependency_records.items() if project not in {version["project_id"] for version in root_versions}),
                           minecraft_version, root_version, root_versions, version_id, runtime_mods, runtime_snapshot)
        runtime_dependencies = self._check_native_plan(client, plan, installed)
        if runtime_dependencies:
            plan = replace(plan, dependencies=plan.dependencies + runtime_dependencies)
        self._check_runtime_snapshot(plan)
        return plan

    def install(self, client: ModrinthClient, plan: InstallPlan, progress: Callable[[str], None] = lambda message: None, *, can_commit: Callable[[], bool] = lambda: True) -> int:
        if not plan.versions:
            return 0
        self._check_runtime_snapshot(plan)
        with self._lock:
            self.recover_pending()
            snapshot = plan.snapshot
            if snapshot is None:
                snapshot = self._snapshot()
            else:
                self._check_snapshot(snapshot)
            self._index()
            self.data_directory.mkdir(parents=True, exist_ok=True)
        # Network transfers never hold a folder lock. Revalidate the confirmed
        # snapshot and merge the current index only when the batch is ready.
        with tempfile.TemporaryDirectory(prefix="mod-download-", dir=self.data_directory) as stage:
            files = []
            records = {}
            downloaded_identities = {}
            sizes = [primary_file(version).get("size") for version in plan.versions]
            total_bytes = sum(sizes) if all(isinstance(size, int) and size > 0 for size in sizes) else None
            completed_bytes = 0
            on_bytes = getattr(progress, "on_bytes", None)

            class FileProgress:
                def __call__(self, message: str) -> None:
                    progress(message)

                def on_bytes(self, current: int, total: int | None) -> None:
                    if callable(on_bytes):
                        on_bytes(completed_bytes + current, total_bytes)

            for version in plan.versions:
                file = primary_file(version)
                name = file["filename"]
                progress(f"Downloading {name}")
                target = Path(stage) / name
                client.download(file, target, FileProgress())
                _check_jar_loader(target, plan.loader, plan.loader_version)
                completed_bytes += target.stat().st_size
                if callable(on_bytes):
                    on_bytes(completed_bytes, total_bytes)
                installed_name = name + (".disabled" if version["project_id"] in plan.disabled_projects else "")
                files.append((target, installed_name))
                records[name] = {
                    "project_id": version["project_id"], "version_id": version["id"],
                    "name": version.get("name", name[:-4]), "version": version.get("version_number", ""),
                    "sha512": _digest(target),
                }
                downloaded_identities[records[name]["sha512"]] = version
            with self._lock:
                self._check_snapshot(snapshot)
                if not can_commit():
                    raise RuntimeError("Minecraft is starting or running. Close it before changing mods.")
                self._check_runtime_snapshot(plan)
                _check_native_mod_set(files, self.list_mods(), self.mods_directory, plan)
                index = self._index()
                replacements = {jar_base_name(name).casefold() for name in plan.replacements}
                updates = dict(plan.identifications)
                updates = {name: value for name, value in updates.items() if name.casefold() not in replacements}
                updates.update(records)
                replacing_keys = replacements | {name.casefold() for name in updates}
                index = {name: value for name, value in index.items() if name.casefold() not in replacing_keys}
                index.update(updates)
                progress("Installing mods...")
                self._commit(files, index, replacements=plan.replacements)
                self._remember_identities(downloaded_identities)
        return len(files)
