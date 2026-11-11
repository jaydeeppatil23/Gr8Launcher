"""Read, verify, and install complete modpacks into fresh launcher instances."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
from typing import Callable
from urllib.parse import urljoin, urlparse
import uuid
import zipfile

import requests

from mod_platform import ModPlatformClient
from mod_services import MAX_PACK_BYTES, MAX_PACK_METADATA_BYTES, safe_archive_name
import profile_creation


MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_PACK_FILES = 20000
MAX_EXPANDED_BYTES = 8 * 1024 * 1024 * 1024
MAX_CONTENT_BYTES = 1024 * 1024 * 1024
PACK_METADATA = "modpack.json"
_HASH_LENGTHS = {"sha512": 128, "sha1": 40, "md5": 32}
_PROTECTED_NAMES = {"gr8launcher", "instance.json", "profile.json", "minecraft_instance.json",
                    profile_creation.PACK_PENDING_MARKER}
_MODRINTH_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com",
                   "release-assets.githubusercontent.com", "objects.githubusercontent.com",
                   "github-releases.githubusercontent.com"}
_CURSEFORGE_HOSTS = {"edge.forgecdn.net", "mediafilez.forgecdn.net", "media.forgecdn.net"}


class PackCancelled(Exception):
    """A pack operation was canceled before publishing its instance."""


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise PackCancelled("Modpack installation canceled.")


def safe_pack_path(value: object, *, destination: bool = True, directory: bool = False) -> str:
    """Reject traversal, Windows aliases, and launcher metadata before any extraction."""
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("The modpack contains an invalid file path.")
    value = value.replace("\\", "/")
    if directory and value.endswith("/"):
        value = value[:-1]
    parts = value.split("/")
    devices = {"CON", "PRN", "AUX", "NUL", *(f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10))}
    if any(not part or part in {".", ".."} or part.endswith((".", " ")) or len(part) > 240
           or re.search(r'[:*?"<>|\x00-\x1f]', part) or part.split(".", 1)[0].upper() in devices for part in parts):
        raise ValueError("The modpack contains an unsafe or nonportable file path.")
    if destination and parts[0].casefold() in _PROTECTED_NAMES:
        raise ValueError("A modpack cannot replace the launcher's instance metadata.")
    return "/".join(parts)


def _text(value: object, label: str, *, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip() or len(value) > 500 or any(ord(char) < 32 for char in value):
        raise ValueError(f"The modpack has an invalid {label}.")
    return value.strip()


def _version(value: object, label: str) -> str:
    value = _text(value, label)
    if not re.fullmatch(r"[a-zA-Z0-9_.+\-]+", value) or len(value) > 100 or value in {".", ".."} or value.endswith("."):
        raise ValueError(f"The modpack has an invalid {label}.")
    return value


def _hashes(value: object, *, allow_md5: bool = False) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("A modpack file has no verification checksum.")
    hashes = {}
    for algorithm in ("sha512", "sha1", "md5") if allow_md5 else ("sha512", "sha1"):
        if algorithm in value:
            digest = value[algorithm]
            if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{" + str(_HASH_LENGTHS[algorithm]) + "}", digest):
                raise ValueError("A modpack file has an invalid checksum.")
            hashes[algorithm] = digest.lower()
    if not hashes:
        raise ValueError("A modpack file has no supported verification checksum.")
    return hashes


def validate_pack_url(url: object, provider: str = "modrinth") -> str:
    if not isinstance(url, str):
        raise ValueError("A modpack file has an invalid download URL.")
    try:
        parsed = urlparse(url)
        hosts = _CURSEFORGE_HOSTS if provider == "curseforge" else _MODRINTH_HOSTS | _CURSEFORGE_HOSTS
        valid = (parsed.scheme == "https" and parsed.hostname in hosts and not parsed.username and not parsed.password
                 and parsed.port in (None, 443) and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("A modpack download must use an approved HTTPS content host.")
    return url


def _content_file(version: dict) -> dict:
    files = version.get("files", [])
    if not isinstance(files, list) or not files or any(not isinstance(file, dict) for file in files):
        raise ValueError("A pinned pack version has no installable content file.")
    selected = [file for file in files if file.get("primary")]
    if len(selected) == 1:
        return selected[0]
    if len(files) == 1:
        return files[0]
    raise ValueError("A pinned pack version has an ambiguous content file.")


@dataclass(frozen=True)
class PackFile:
    path: str
    size: int
    hashes: dict[str, str]
    urls: tuple[str, ...]
    required: bool = True
    provider: str = "modrinth"
    version: dict | None = None
    label: str = ""

    def download_record(self) -> dict:
        record = deepcopy(_content_file(self.version)) if self.version else {}
        return {**record, "filename": self.path.rsplit("/", 1)[-1], "size": self.size,
                "hashes": dict(self.hashes), "url": self.urls[0] if self.urls else "",
                "provider": self.provider}


@dataclass(frozen=True)
class PackOverride:
    member: str
    path: str
    size: int


@dataclass(frozen=True)
class PackPlan:
    archive: Path
    archive_sha512: str
    provider: str
    name: str
    pack_version: str
    minecraft_version: str
    loader: str
    loader_version: str
    files: tuple[PackFile, ...]
    overrides: tuple[PackOverride, ...]
    manifest: dict
    project: dict | None = None
    source_version_id: str = ""
    owned_archive: Path | None = None

    @property
    def optional_files(self) -> tuple[PackFile, ...]:
        return tuple(file for file in self.files if not file.required)

    def selected_files(self, optional_paths: tuple[str, ...] = ()) -> tuple[PackFile, ...]:
        selected = set(optional_paths)
        if not selected.issubset({file.path for file in self.optional_files}):
            raise ValueError("The optional modpack selection changed. Review the pack again.")
        return tuple(file for file in self.files if file.required or file.path in selected)

    def download_size(self, optional_paths: tuple[str, ...] = ()) -> int:
        return sum(file.size for file in self.selected_files(optional_paths))

    def manual_files(self, optional_paths: tuple[str, ...] = ()) -> list[dict]:
        return [file.download_record() for file in self.selected_files(optional_paths)
                if file.version and _content_file(file.version).get("manual_required")]


def _archive_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries = {}
    seen = {}
    total = 0
    infos = archive.infolist()
    if len(infos) > MAX_PACK_FILES:
        raise ValueError("The modpack archive contains too many entries.")
    for info in infos:
        if info.orig_filename != info.filename:
            raise ValueError("The modpack archive contains an invalid filename.")
        path = safe_pack_path(info.filename, destination=False, directory=info.is_dir())
        key = path.casefold()
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or kind == stat.S_IFDIR and not info.is_dir() or info.flag_bits & 1:
            raise ValueError("The modpack archive contains a link, special file, or encrypted entry.")
        if key in seen:
            raise ValueError("The modpack archive contains duplicate or case-conflicting paths.")
        seen[key] = info.is_dir()
        for index in range(1, len(path.split("/"))):
            ancestor = "/".join(path.split("/")[:index]).casefold()
            if ancestor in seen and not seen[ancestor]:
                raise ValueError("The modpack archive uses a file as a directory.")
        if not info.is_dir():
            if info.file_size < 0 or info.file_size > MAX_CONTENT_BYTES:
                raise ValueError("A bundled modpack file exceeds the size limit.")
            total += info.file_size
            entries[path] = info
    # Detect an ancestor listed after its children as well.
    file_keys = {path.casefold() for path in entries}
    if any("/".join(path.split("/")[:index]).casefold() in file_keys
           for path in entries for index in range(1, len(path.split("/")))):
        raise ValueError("The modpack archive uses a file as a directory.")
    if total > MAX_EXPANDED_BYTES:
        raise ValueError("The expanded modpack exceeds the installation size limit.")
    return entries


def _read_manifest(archive: zipfile.ZipFile, entry: zipfile.ZipInfo) -> dict:
    if entry.file_size > MAX_MANIFEST_BYTES:
        raise ValueError("The modpack manifest exceeds the size limit.")
    try:
        document = json.loads(archive.read(entry).decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("The modpack manifest is not valid JSON.") from error
    if not isinstance(document, dict):
        raise ValueError("The modpack manifest must be an object.")
    return document


def _overrides(entries: dict[str, zipfile.ZipInfo], folders: tuple[str, ...]) -> tuple[PackOverride, ...]:
    overrides = []
    casing = {}
    for folder in folders:
        prefix = safe_pack_path(folder, destination=False) + "/"
        for member, info in entries.items():
            if member.startswith(prefix):
                path = safe_pack_path(member[len(prefix):])
                previous = casing.setdefault(path.casefold(), path)
                if previous != path:
                    raise ValueError("The pack overrides contain case-conflicting paths.")
                overrides.append(PackOverride(info.filename, path, info.file_size))
    return tuple(overrides)


def _validate_destinations(files: tuple[PackFile, ...], overrides: tuple[PackOverride, ...]) -> None:
    paths = {}
    for item in (*files, *overrides):
        key = item.path.casefold()
        previous = paths.setdefault(key, item.path)
        if previous != item.path:
            raise ValueError("The modpack contains case-conflicting installation paths.")
    if any("/".join(path.split("/")[:index]).casefold() in paths
           for path in paths.values() for index in range(1, len(path.split("/")))):
        raise ValueError("The modpack uses an installed file as a directory.")


def _modrinth_manifest(document: dict) -> tuple[str, str, str, tuple[PackFile, ...]]:
    if type(document.get("formatVersion")) is not int or document["formatVersion"] != 1 or document.get("game") != "minecraft":
        raise ValueError("This is not a supported Minecraft Modrinth pack (format 1).")
    dependencies = document.get("dependencies")
    if not isinstance(dependencies, dict) or "minecraft" not in dependencies:
        raise ValueError("The modpack does not specify its Minecraft version.")
    loaders = {"fabric-loader": "Fabric", "quilt-loader": "Quilt", "forge": "Forge", "neoforge": "NeoForge"}
    if any(key not in {"minecraft", *loaders} for key in dependencies):
        raise ValueError("This pack requires an unsupported game component.")
    selected = [key for key in loaders if key in dependencies]
    if len(selected) > 1:
        raise ValueError("The modpack requires multiple loaders; this launcher supports one loader per instance.")
    loader = loaders[selected[0]] if selected else "Vanilla"
    loader_version = _version(dependencies[selected[0]], "loader version") if selected else ""
    minecraft_version = _version(dependencies["minecraft"], "Minecraft version")
    records = document.get("files")
    if not isinstance(records, list) or len(records) > MAX_PACK_FILES:
        raise ValueError("The modpack has an invalid file list.")
    files = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("The modpack has an invalid file entry.")
        path = safe_pack_path(record.get("path"))
        if path.casefold() in seen:
            raise ValueError("The modpack lists the same installation path more than once.")
        seen.add(path.casefold())
        env = record.get("env", {})
        if not isinstance(env, dict) or env.get("client", "required") not in {"required", "optional", "unsupported"}:
            raise ValueError("The modpack has an invalid client environment rule.")
        if env.get("client") == "unsupported":
            continue
        size = record.get("fileSize")
        if type(size) is not int or not 0 <= size <= MAX_CONTENT_BYTES:
            raise ValueError("A modpack file has an invalid size.")
        downloads = record.get("downloads")
        if not isinstance(downloads, list) or not downloads or len(downloads) > 16:
            raise ValueError("A modpack file has no supported download URL.")
        urls = tuple(validate_pack_url(url) for url in downloads)
        files.append(PackFile(path, size, _hashes(record.get("hashes")), urls,
                              required=env.get("client", "required") == "required"))
    return minecraft_version, loader, loader_version, tuple(files)


def _curseforge_manifest(document: dict, client: ModPlatformClient,
                         cancel_event: threading.Event | None = None) -> tuple[str, str, str, tuple[PackFile, ...]]:
    if document.get("manifestType") != "minecraftModpack" or type(document.get("manifestVersion")) is not int or document["manifestVersion"] != 1:
        raise ValueError("This is not a supported CurseForge Minecraft modpack (manifest 1).")
    minecraft = document.get("minecraft")
    if not isinstance(minecraft, dict):
        raise ValueError("The modpack does not specify its Minecraft configuration.")
    minecraft_version = _version(minecraft.get("version"), "Minecraft version")
    loaders = minecraft.get("modLoaders", [])
    if not isinstance(loaders, list) or any(not isinstance(item, dict) for item in loaders):
        raise ValueError("The modpack has an invalid loader list.")
    primary = [item for item in loaders if item.get("primary") is True]
    if len(primary) > 1 or not primary and len(loaders) > 1:
        raise ValueError("The modpack does not specify one primary loader.")
    chosen = primary[0] if primary else loaders[0] if loaders else None
    loader, loader_version = "Vanilla", ""
    if chosen:
        loader_id = _text(chosen.get("id"), "loader ID")
        kind, separator, loader_version = loader_id.partition("-")
        if not separator or kind not in {"fabric", "quilt", "forge", "neoforge"}:
            raise ValueError("This CurseForge pack requires an unsupported loader.")
        loader = {"fabric": "Fabric", "quilt": "Quilt", "forge": "Forge", "neoforge": "NeoForge"}[kind]
        if kind in {"forge", "neoforge"} and loader_version.startswith(minecraft_version + "-"):
            loader_version = loader_version[len(minecraft_version) + 1:]
        loader_version = _version(loader_version, "loader version")
    records = document.get("files")
    if not isinstance(records, list) or len(records) > MAX_PACK_FILES:
        raise ValueError("The CurseForge pack has an invalid file list.")
    pins = []
    required = []
    for record in records:
        if not isinstance(record, dict) or any(type(record.get(key)) is not int or record[key] <= 0 for key in ("projectID", "fileID")):
            raise ValueError("The CurseForge pack has an invalid project or file ID.")
        if type(record.get("required", True)) is not bool:
            raise ValueError("The CurseForge pack has an invalid optional-file rule.")
        pins.append((record["projectID"], record["fileID"]))
        required.append(record.get("required", True))
    if len({file_id for _, file_id in pins}) != len(pins):
        raise ValueError("The CurseForge pack lists a file more than once.")
    versions = client.curseforge.modpack_files(pins, check_cancel=lambda: _check_cancel(cancel_event)) if pins else []
    if len(versions) != len(pins):
        raise ValueError("CurseForge did not resolve all the pack's pinned files.")
    files = []
    for (project_id, file_id), needed, version in zip(pins, required, versions):
        if version.get("id") != f"cf:{project_id}:{file_id}" or version.get("project_id") != f"cf:{project_id}":
            raise ValueError("CurseForge resolved a different version than the pack specified.")
        file = _content_file(version)
        path = safe_pack_path(str(version["_pack_folder"]) + "/" + file["filename"])
        urls = (validate_pack_url(file["url"], "curseforge"),) if file.get("url") else ()
        if not urls and not file.get("manual_required"):
            raise ValueError("A CurseForge pack file has no download or manual source.")
        files.append(PackFile(path, file["size"], _hashes(file["hashes"], allow_md5=True), urls,
                              needed, "curseforge", deepcopy(version), version.get("_project_name", "")))
    if len({file.path.casefold() for file in files}) != len(files):
        raise ValueError("The CurseForge pack contains conflicting filenames.")
    return minecraft_version, loader, loader_version, tuple(files)


def _sha512(path: Path, cancel_event: threading.Event | None = None) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(256 * 1024), b""):
            _check_cancel(cancel_event)
            digest.update(chunk)
    return digest.hexdigest()


class PackInstaller:
    def __init__(self, minecraft_directory: str | Path, *, client: ModPlatformClient | None = None,
                 storage_service=None, version_resolver: Callable[[dict], str] | None = None):
        self.minecraft_directory = Path(minecraft_directory).resolve()
        self.client = client or ModPlatformClient()
        if storage_service is None:
            import storage as storage_service
        self.storage = storage_service
        self._version_resolver = version_resolver
        self.stage_root = self.minecraft_directory / "gr8launcher" / "pack-staging"
        self.archive_root = self.minecraft_directory / "gr8launcher" / "pack-downloads"

    def prepare_local(self, path: str | Path, *, progress: Callable = lambda message: None,
                      cancel_event: threading.Event | None = None) -> PackPlan:
        source = Path(path)
        if source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= MAX_PACK_BYTES:
            raise ValueError("Choose a regular modpack file within the 2 GB archive size limit.")
        safe_archive_name(source.name, ("mrpack", "zip"))
        _check_cancel(cancel_event)
        progress("Reading modpack manifest...")
        checksum = _sha512(source, cancel_event)
        try:
            with zipfile.ZipFile(source) as archive:
                entries = _archive_entries(archive)
                manifests = [name for name in ("modrinth.index.json", "manifest.json") if name in entries]
                if len(manifests) != 1:
                    raise ValueError("Choose a Modrinth .mrpack or CurseForge export ZIP containing one pack manifest.")
                document = _read_manifest(archive, entries[manifests[0]])
                if manifests[0] == "modrinth.index.json":
                    provider = "modrinth"
                    overrides = _overrides(entries, ("overrides", "client-overrides"))
                    mc_version, loader, loader_version, files = _modrinth_manifest(document)
                    pack_version = _text(document.get("versionId"), "pack version", default="Imported")
                else:
                    provider = "curseforge"
                    override_folder = document.get("overrides", "overrides")
                    if not isinstance(override_folder, str):
                        raise ValueError("The CurseForge pack has an invalid overrides folder.")
                    overrides = _overrides(entries, (override_folder,)) if override_folder else ()
                    _check_cancel(cancel_event)
                    progress("Resolving the pack's pinned CurseForge files...")
                    mc_version, loader, loader_version, files = _curseforge_manifest(document, self.client, cancel_event)
                    pack_version = _text(document.get("version"), "pack version", default="Imported")
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
            raise ValueError("The modpack archive is damaged or uses an unsupported ZIP format.") from error
        _check_cancel(cancel_event)
        _validate_destinations(files, overrides)
        if sum(file.size for file in files) + sum(item.size for item in overrides) > MAX_EXPANDED_BYTES:
            raise ValueError("The pack's files exceed the 8 GB installation size limit.")
        return PackPlan(source.resolve(), checksum, provider,
                        _text(document.get("name"), "pack name", default=source.stem), pack_version,
                        mc_version, loader, loader_version, files, overrides, deepcopy(document))

    @staticmethod
    def archive_file(version: dict, provider: str) -> dict:
        extension = "zip" if provider == "curseforge" else "mrpack"
        candidates = [file for file in version.get("files", [])
                      if isinstance(file, dict) and str(file.get("filename", "")).lower().endswith("." + extension)]
        if not candidates:
            raise ValueError("This pack version has no supported client archive.")
        selected = next((file for file in candidates if file.get("primary")), candidates[0])
        file = {**deepcopy(selected), "provider": provider, "content_type": "modpack"}
        safe_archive_name(file["filename"], (extension,))
        file["hashes"] = _hashes(file.get("hashes"), allow_md5=provider == "curseforge")
        if type(file.get("size")) is not int or not 0 < file["size"] <= MAX_PACK_BYTES:
            raise ValueError("The modpack archive has an invalid download size.")
        if file.get("manual_required"):
            if provider != "curseforge":
                raise ValueError("This pack does not provide a supported download source.")
        else:
            validate_pack_url(file.get("url"), provider)
        return file

    def prepare_remote(self, project: dict, version: dict, *, progress: Callable = lambda message: None,
                       cancel_event: threading.Event | None = None) -> PackPlan:
        provider = "curseforge" if str(version.get("project_id", "")).startswith("cf:") else "modrinth"
        if version.get("project_id") != (project.get("project_id") or project.get("id")):
            raise ValueError("The selected version belongs to a different modpack.")
        file = self.archive_file(version, provider)
        self.archive_root.mkdir(parents=True, exist_ok=True)
        holder = Path(tempfile.mkdtemp(prefix="archive-", dir=self.archive_root))
        try:
            source = holder / file["filename"]
            progress("Downloading the pack manifest archive...")
            self.download_file(file, source, progress, cancel_event=cancel_event)
            plan = self.prepare_local(source, progress=progress, cancel_event=cancel_event)
            if plan.provider != provider:
                raise ValueError("The archive format does not match the selected provider.")
            return replace(plan, project=deepcopy(project), source_version_id=version["id"], owned_archive=holder)
        except BaseException:
            shutil.rmtree(holder, ignore_errors=True)
            raise

    def discard(self, plan: PackPlan | None) -> None:
        """Remove only a temporary archive downloaded by this installer."""
        holder = plan.owned_archive if plan else None
        if (holder is not None and not holder.is_symlink() and holder.resolve().parent == self.archive_root.resolve()
                and holder.name.startswith("archive-")):
            shutil.rmtree(holder, ignore_errors=True)

    def _transfer(self, file: dict, destination: Path, chunks, progress: Callable,
                  cancel_event: threading.Event | None) -> str:
        hashes = _hashes(file.get("hashes"), allow_md5=file.get("provider") == "curseforge")
        size_limit = MAX_PACK_BYTES if file.get("content_type") == "modpack" else MAX_CONTENT_BYTES
        if type(file.get("size")) is not int or not 0 <= file["size"] <= size_limit:
            raise ValueError("The content has an invalid download size.")
        digests = {key: hashlib.new(key) for key in {*hashes, "sha512"}}
        size = 0
        created = False
        try:
            with destination.open("xb") as output:
                created = True
                for chunk in chunks:
                    _check_cancel(cancel_event)
                    size += len(chunk)
                    if size > file["size"]:
                        raise ValueError("A modpack file exceeds its declared size.")
                    output.write(chunk)
                    for digest in digests.values():
                        digest.update(chunk)
                    on_bytes = getattr(progress, "on_bytes", None)
                    if callable(on_bytes):
                        on_bytes(size, file["size"])
            _check_cancel(cancel_event)
            if size != file["size"] or any(digests[key].hexdigest() != expected for key, expected in hashes.items()):
                raise ValueError("A modpack file failed checksum or size verification.")
            if destination.suffix.lower() in {".jar", ".zip", ".mrpack"} and not zipfile.is_zipfile(destination):
                raise ValueError("A downloaded modpack archive or mod is not a valid ZIP/JAR.")
            return digests["sha512"].hexdigest()
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise

    def download_file(self, file: dict, destination: Path, progress: Callable = lambda message: None, *,
                      cancel_event: threading.Event | None = None, urls: tuple[str, ...] = ()) -> str:
        _check_cancel(cancel_event)
        if file.get("manual_required"):
            source = self.client.curseforge.manual_file(file)
            if source is None:
                raise ValueError("Select the required manual CurseForge file in the installation review.")
            with source.open("rb") as stream:
                return self._transfer(file, destination, iter(lambda: stream.read(128 * 1024), b""), progress, cancel_event)
        urls = urls or (file.get("url", ""),)
        error = None
        for initial_url in urls:
            url = validate_pack_url(initial_url, file.get("provider", "modrinth"))
            try:
                for _ in range(5):
                    _check_cancel(cancel_event)
                    with self.client._session().get(url, headers={"User-Agent": "Gr8Launcher (Minecraft Java content manager)"},
                                                    stream=True, timeout=(5, 30), allow_redirects=False) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            url = validate_pack_url(urljoin(url, response.headers.get("Location", "")), file.get("provider", "modrinth"))
                            continue
                        response.raise_for_status()
                        if response.status_code != 200:
                            raise ValueError("The content host returned an unexpected download response.")
                        return self._transfer(file, destination, response.iter_content(128 * 1024), progress, cancel_event)
                raise ValueError("The modpack download redirected too many times.")
            except (requests.RequestException, ValueError) as failure:
                error = failure
        raise ConnectionError("Could not download a verified modpack file. Check the connection and try again.") from error

    def _profile(self, plan: PackPlan, name: str) -> dict:
        project = plan.project or {}
        record = {"id": "modpack:" + uuid.uuid4().hex, "name": name, "loader": plan.loader,
                  "channel": "Release", "minecraft_version": plan.minecraft_version,
                  "loader_version": plan.loader_version, "source": "modpack", "is_instance": True,
                  "icon": project.get("icon_url") or "", "jvm_arguments": "",
                  "modpack": {"provider": plan.provider, "name": plan.name, "version": plan.pack_version,
                              "project_id": project.get("project_id") or project.get("id", ""),
                              "version_id": plan.source_version_id, "project_url": project.get("project_url", "")}}
        resolver = self._version_resolver
        if resolver is None:
            from launcher import get_profile_version_id
            resolver = get_profile_version_id
        record["version_id"] = resolver(record)
        if not record["version_id"]:
            raise ValueError("The pack's exact Minecraft and loader versions could not be resolved.")
        return record

    def install(self, plan: PackPlan, *, name: str = "", folder_name: str = "", optional_paths: tuple[str, ...] = (),
                progress: Callable = lambda message: None, cancel_event: threading.Event | None = None,
                can_commit: Callable[[], bool] = lambda: True) -> dict:
        _check_cancel(cancel_event)
        files = plan.selected_files(optional_paths)
        name = _text(name or plan.name, "instance name")
        profile = self._profile(plan, name)
        profile_creation.new_instance_directory(self.minecraft_directory, folder_name, name, self.storage.load_installations())
        if (plan.archive.is_symlink() or not plan.archive.is_file()
                or _sha512(plan.archive, cancel_event) != plan.archive_sha512):
            raise ValueError("The modpack archive changed since the review. Import or select it again.")
        for file in plan.manual_files(optional_paths):
            if self.client.curseforge.manual_file(file) is None:
                raise ValueError("A required manual CurseForge file is missing or changed. Select it again.")
        self.stage_root.mkdir(parents=True, exist_ok=True)
        required_bytes = plan.archive.stat().st_size + sum(file.size for file in files) + sum(item.size for item in plan.overrides)
        if shutil.disk_usage(self.stage_root).free < required_bytes + 64 * 1024 * 1024:
            raise OSError("There is not enough free disk space to install this modpack.")
        total_bytes = sum(file.size for file in files)
        completed = 0
        on_bytes = getattr(progress, "on_bytes", None)

        class FileProgress:
            def __call__(self, message: str) -> None:
                _check_cancel(cancel_event)
                progress(message)

            def on_bytes(self, current: int, total: int | None) -> None:
                _check_cancel(cancel_event)
                if callable(on_bytes):
                    on_bytes(completed + current, total_bytes)

        with tempfile.TemporaryDirectory(prefix="pack-", dir=self.stage_root) as holder:
            # Extract from an owned snapshot so the user's archive cannot change
            # underneath a long download or override extraction.
            snapshot = Path(holder) / "source.zip"
            digest = hashlib.sha512()
            with plan.archive.open("rb") as source, snapshot.open("xb") as output:
                for chunk in iter(lambda: source.read(256 * 1024), b""):
                    _check_cancel(cancel_event)
                    output.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != plan.archive_sha512:
                raise ValueError("The modpack archive changed since the review. Import or select it again.")
            game = Path(holder) / "game"
            game.mkdir()
            records = {}
            for file in files:
                _check_cancel(cancel_event)
                target = game.joinpath(*file.path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                progress("Downloading " + (file.label or target.name) + "...")
                checksum = self.download_file(file.download_record(), target, FileProgress(),
                                              cancel_event=cancel_event, urls=file.urls)
                completed += file.size
                if callable(on_bytes):
                    on_bytes(completed, total_bytes)
                records[file.path] = {"sha512": checksum, "size": file.size, "source": "download"}
                if file.version:
                    records[file.path].update({"project_id": file.version["project_id"], "version_id": file.version["id"],
                                               "name": file.label or file.version.get("name", target.stem),
                                               "version": file.version.get("version_number", "")})
            progress("Applying bundled configs and client files...")
            with zipfile.ZipFile(snapshot) as archive:
                for item in plan.overrides:
                    _check_cancel(cancel_event)
                    target = game.joinpath(*item.path.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha512()
                    size = 0
                    with archive.open(item.member) as source, target.open("wb") as output:
                        for chunk in iter(lambda: source.read(128 * 1024), b""):
                            _check_cancel(cancel_event)
                            size += len(chunk)
                            if size > item.size:
                                raise ValueError("A bundled modpack file exceeds its declared size.")
                            output.write(chunk)
                            digest.update(chunk)
                    if size != item.size:
                        raise ValueError("A bundled modpack file is damaged.")
                    records[item.path] = {"sha512": digest.hexdigest(), "size": size, "source": "override"}
            data_dir = game / "gr8launcher"
            data_dir.mkdir()
            metadata = {"format": 1, **profile["modpack"], "minecraft_version": plan.minecraft_version,
                        "loader": plan.loader, "loader_version": plan.loader_version, "files": records}
            metadata_text = json.dumps(metadata, ensure_ascii=False, indent=2)
            if len(metadata_text.encode("utf-8")) > MAX_PACK_METADATA_BYTES:
                raise ValueError("The pack's file metadata exceeds the supported size limit.")
            (data_dir / PACK_METADATA).write_text(metadata_text, encoding="utf-8")
            (data_dir / "modpack-manifest.json").write_text(json.dumps(plan.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            mod_index = {path[5:]: {key: record[key] for key in ("project_id", "version_id", "name", "version", "sha512")}
                         for path, record in records.items() if path.startswith("mods/") and "/" not in path[5:]
                         and path.lower().endswith(".jar") and record.get("project_id")}
            if mod_index:
                (data_dir / "mods.json").write_text(json.dumps(mod_index, ensure_ascii=False, indent=2), encoding="utf-8")
            (game / "instance.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

            def before_commit() -> None:
                _check_cancel(cancel_event)
                if not can_commit():
                    raise RuntimeError("Wait for Minecraft or the current launcher operation to finish before installing this pack.")

            progress("Adding the modpack instance to your Library...")
            record = profile_creation.save_prepared_profile(profile, self.minecraft_directory, folder_name, self.storage,
                                                             game, before_commit=before_commit)
        progress("Modpack installed.")
        return record
