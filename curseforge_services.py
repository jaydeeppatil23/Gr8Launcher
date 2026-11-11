"""CurseForge API adapter, verified file transfers, and manual restricted files."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import struct
import threading
from typing import Callable
from urllib.parse import urlparse
import zipfile

import requests

from credential_store import CredentialStore
from mod_compatibility import supported_mod_loaders
from mod_services import (MAX_JAR_BYTES, MAX_PACK_BYTES, MetadataUnavailable, ModrinthClient,
                          _RangeJar, _digest, _fabric_loader_metadata,
                          primary_file, safe_archive_name, safe_jar_name, validate_mod_download_url)


LOADER_TYPES = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
RELATIONS = {1: "embedded", 2: "optional", 3: "required", 4: "tool", 5: "incompatible", 6: "include"}
CONTENT_CLASSES = {"mod": (6, "mc-mods"), "modpack": (4471, "modpacks"),
                   "resourcepack": (12, "texture-packs"), "shader": (6552, "shaders")}


def curseforge_project_id(value: int | str) -> str:
    if not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError("Invalid CurseForge project ID.")
    return "cf:" + str(value)


def curseforge_file_ids(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"cf:([1-9][0-9]*):([1-9][0-9]*)", value)
    if not match:
        raise ValueError("Invalid CurseForge file ID.")
    return int(match[1]), int(match[2])


def curseforge_fingerprint(path: Path) -> int:
    """MurmurHash2 with seed 1, excluding TAB/LF/CR/space, as required by CF."""
    if path.stat().st_size > MAX_JAR_BYTES:
        raise ValueError("The mod exceeds the fingerprint size limit.")
    excluded = b"\t\n\r "
    with path.open("rb") as stream:
        size = sum(len(chunk.translate(None, excluded)) for chunk in iter(lambda: stream.read(1024 * 1024), b""))
    value = 1 ^ size
    tail = b""
    mask = 0xFFFFFFFF
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            data = tail + chunk.translate(None, excluded)
            aligned = len(data) - len(data) % 4
            for (word,) in struct.iter_unpack("<I", memoryview(data)[:aligned]):
                word = (word * 0x5BD1E995) & mask
                word ^= word >> 24
                word = (word * 0x5BD1E995) & mask
                value = ((value * 0x5BD1E995) & mask) ^ word
            tail = data[aligned:]
    if tail:
        value ^= int.from_bytes(tail, "little")
        value = (value * 0x5BD1E995) & mask
    value ^= value >> 13
    value = (value * 0x5BD1E995) & mask
    return (value ^ (value >> 15)) & mask


def verify_curseforge_file(file: dict, path: Path) -> bool:
    """Fingerprints/names never substitute for a full checksum and size check."""
    hashes = file.get("hashes", {})
    algorithm = next((key for key in ("sha512", "sha1", "md5") if hashes.get(key)), None)
    return bool(algorithm and path.is_file() and not path.is_symlink()
                and path.stat().st_size == file.get("size")
                and path.stat().st_size <= (MAX_PACK_BYTES if file.get("content_type") == "modpack" else MAX_JAR_BYTES)
                and _digest(path, algorithm) == str(hashes[algorithm]).lower())


class CurseForgeClient(ModrinthClient):
    API = "https://api.curseforge.com/v1"
    HEADERS = {"User-Agent": "Gr8Launcher (Minecraft Java mod manager)", "Accept": "application/json"}

    def __init__(self, api_key: str | None = None):
        super().__init__()
        self._credentials = CredentialStore("curseforge-api", service_prefix="Gr8Launcher.API")
        self._api_key = api_key
        self._manual_files: dict[str, Path] = {}
        self._manual_lock = threading.RLock()
        self._project_urls: dict[str, str] = {}

    @property
    def configured(self) -> bool:
        return bool(self._key())

    def _key(self) -> str:
        if self._api_key is not None:
            return self._api_key
        return os.environ.get("GR8_CURSEFORGE_API_KEY", "") or self._credentials.get("api-key").get("access_token", "")

    def set_api_key(self, value: str) -> bool:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) > 126 for char in value):
            raise ValueError("Enter a valid CurseForge API key.")
        self._api_key = value
        saved = self._credentials.put("api-key", {"access_token": value})
        self.clear_cache()
        return saved

    def clear_api_key(self) -> None:
        self._api_key = ""
        self._credentials.delete("api-key")
        self.clear_cache()

    def _request(self, endpoint: str, params: dict | None = None, *, body: dict | None = None) -> dict:
        api_key = self._key()
        if not api_key:
            raise ConnectionError("Set up a CurseForge API key in Discover to browse or install CurseForge content.")
        key = ("curseforge", endpoint, tuple(sorted((params or {}).items())), repr(body))

        def fetch() -> dict:
            try:
                headers = {**self.HEADERS, "x-api-key": api_key}
                kwargs = {"headers": headers, "timeout": (5, 25), "allow_redirects": False}
                operation = self._session().post if body is not None else self._session().get
                kwargs.update({"json": body} if body is not None else {"params": params})
                # Use the documented route: the trailing-slash variant can
                # serve CDN-cached results from a different search query.
                with operation(self.API + endpoint, **kwargs) as response:
                    if response.status_code == 401:
                        raise ConnectionError("CurseForge rejected the API key. Update it in Discover.")
                    if response.status_code == 403:
                        raise ConnectionError("CurseForge denied API access (HTTP 403). Check the key and its application access in the CurseForge console.")
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ValueError("Unexpected CurseForge response.")
                    data = response.json()
                if not isinstance(data, dict) or "data" not in data:
                    raise ValueError("Invalid CurseForge response.")
                return data
            except (requests.RequestException, ValueError) as error:
                raise ConnectionError("CurseForge is unavailable. Check your connection and try again.") from error

        return self._cached_request(key, fetch, ttl=30 if endpoint == "/mods/search" else self.CACHE_TTL)

    @staticmethod
    def _project_number(project_id: str) -> int:
        match = re.fullmatch(r"cf:([1-9][0-9]*)", project_id)
        if not match:
            raise ValueError("Invalid CurseForge project ID.")
        return int(match[1])

    def _project(self, data: dict, *, content_type: str = "mod") -> dict:
        project = curseforge_project_id(data["id"])
        actual_type = next((kind for kind, (number, _) in CONTENT_CLASSES.items()
                            if number == data.get("classId", 6)), None)
        if data.get("gameId", 432) != 432 or actual_type is None or (content_type != "file" and actual_type != content_type):
            raise ValueError(f"This CurseForge project is not Minecraft {content_type} content.")
        links = data.get("links") or {}
        website = links.get("websiteUrl", "")
        parsed = urlparse(website)
        if parsed.scheme != "https" or parsed.hostname not in {"www.curseforge.com", "curseforge.com"}:
            website = "https://www.curseforge.com/minecraft/" + CONTENT_CLASSES[actual_type][1] + "/" + str(data["slug"])
        self._project_urls[project] = website.rstrip("/")
        authors = data.get("authors") or []
        return {"id": project, "project_id": project, "provider": "curseforge", "project_type": actual_type, "title": data["name"],
                "description": data.get("summary", ""), "slug": data.get("slug", ""),
                "icon_url": (data.get("logo") or {}).get("thumbnailUrl") or (data.get("logo") or {}).get("url"),
                "downloads": data.get("downloadCount", 0), "author": ", ".join(author.get("name", "") for author in authors),
                "members": [{"user": {"username": author.get("name", "")}, "role": "Author"} for author in authors],
                "categories": [category.get("name", "") for category in data.get("categories", [])],
                "project_url": website, "source_url": links.get("sourceUrl"), "issues_url": links.get("issuesUrl"),
                "wiki_url": links.get("wikiUrl"), "license": {"name": "See CurseForge project"},
                "client_side": "unknown", "server_side": "unknown",
                "gallery": [{"url": item.get("url"), "title": item.get("title", ""),
                             "description": item.get("description", "")} for item in data.get("screenshots", [])]}

    def _file(self, data: dict, *, expected_project: str | None = None, content_type: str = "mod") -> dict:
        project = curseforge_project_id(data["modId"])
        if expected_project and project != expected_project:
            raise ValueError("CurseForge returned a file for a different project.")
        file_id = int(data["id"])
        if file_id <= 0:
            raise ValueError("Invalid CurseForge file ID.")
        filename = safe_jar_name(data["fileName"]) if content_type == "mod" else safe_archive_name(data["fileName"])
        hashes = {}
        for item in data.get("hashes", []):
            algorithm = {1: "sha1", 2: "md5"}.get(item.get("algo"))
            value = str(item.get("value", "")).lower()
            if algorithm and re.fullmatch(r"[a-f0-9]{40}" if algorithm == "sha1" else r"[a-f0-9]{32}", value):
                hashes[algorithm] = value
        size_limit = MAX_PACK_BYTES if content_type == "modpack" else MAX_JAR_BYTES
        if not hashes or type(data.get("fileLength")) is not int or not 0 < data["fileLength"] <= size_limit:
            raise ValueError("CurseForge returned a file without a valid size and checksum.")
        tags = [str(tag) for tag in data.get("gameVersions", [])]
        loaders = [tag.lower() for tag in tags if tag.lower() in LOADER_TYPES]
        url = data.get("downloadUrl") or ""
        manual_url = self._project_urls.get(project, "")
        file = {"filename": filename, "url": url, "size": data["fileLength"], "hashes": hashes,
                "primary": True, "provider": "curseforge", "manual_required": not bool(url),
                "manual_url": manual_url + "/download/" + str(file_id) if manual_url else "",
                "id": f"cf:{data['modId']}:{file_id}"}
        if content_type != "mod":
            file["content_type"] = content_type
        if url:
            validate_mod_download_url(file)
        return {"id": file["id"], "project_id": project, "provider": "curseforge",
                "name": data.get("displayName") or filename, "version_number": data.get("displayName") or filename,
                "date_published": data["fileDate"], "version_type": {1: "release", 2: "beta", 3: "alpha"}.get(data.get("releaseType"), "unknown"),
                "game_versions": [tag for tag in tags if tag.lower() not in LOADER_TYPES and tag.lower() not in {"client", "server"}],
                "loaders": loaders, "_server_only": "server" in [tag.lower() for tag in tags] and "client" not in [tag.lower() for tag in tags],
                "dependencies": [{"project_id": curseforge_project_id(dep["modId"]),
                                  "dependency_type": RELATIONS.get(dep.get("relationType"), "unknown")}
                                 for dep in data.get("dependencies", [])], "files": [file], "changelog": ""}

    def _ensure_project_url(self, project_id: str) -> None:
        if project_id not in self._project_urls:
            data = self._request(f"/mods/{self._project_number(project_id)}")["data"]
            if curseforge_project_id(data["id"]) != project_id:
                raise ValueError("CurseForge returned a different project.")
            self._project(data)

    def search(self, query: str, minecraft_version: str, loader: str, *, index: str = "relevance", offset: int = 0, limit: int = 20) -> dict:
        params = {"gameId": 432, "classId": 6, "searchFilter": query, "gameVersion": minecraft_version,
                  "modLoaderTypes": "[" + ",".join(str(LOADER_TYPES[tag]) for tag in supported_mod_loaders(minecraft_version, loader)) + "]",
                  "sortField": {"downloads": 6, "relevance": 2, "updated": 3, "newest": 11}.get(index, 2),
                  "sortOrder": "desc", "index": offset, "pageSize": min(50, limit)}
        result = self._request("/mods/search", params)
        return {"hits": [self._project(item) for item in result["data"]],
                "total_hits": result.get("pagination", {}).get("totalCount", len(result["data"]))}

    def project(self, project_id: str) -> dict:
        number = self._project_number(project_id)
        data = self._request(f"/mods/{number}")["data"]
        if curseforge_project_id(data["id"]) != project_id:
            raise ValueError("CurseForge returned a different project.")
        result = self._project(data)
        try:
            result["body"] = self._request(f"/mods/{number}/description")["data"]
        except ConnectionError:
            result["body"] = result["description"]
        return result

    def project_members(self, project_id: str) -> list[dict]:
        number = self._project_number(project_id)
        return self._project(self._request(f"/mods/{number}")["data"])["members"]

    def project_versions(self, project_id: str, minecraft_version: str | None = None, loader: str | None = None) -> list[dict]:
        number = self._project_number(project_id)
        self._ensure_project_url(project_id)
        versions = []
        for offset in range(0, 10000, 50):
            params = {"index": offset, "pageSize": 50}
            if minecraft_version:
                params["gameVersion"] = minecraft_version
            tags = supported_mod_loaders(minecraft_version or "", loader or "")
            if len(tags) == 1:
                params["modLoaderType"] = LOADER_TYPES[tags[0]]
            result = self._request(f"/mods/{number}/files", params)
            data = result["data"]
            if not isinstance(data, list):
                raise ValueError("CurseForge returned an invalid file list.")
            for item in data:
                version = self._file(item, expected_project=project_id)
                if ((not minecraft_version or minecraft_version in version["game_versions"])
                        and (not loader or bool(set(tags).intersection(version["loaders"])))
                        and not version.get("_server_only")):
                    versions.append(version)
            total = result.get("pagination", {}).get("totalCount")
            if len(data) < 50 or isinstance(total, int) and offset + len(data) >= total:
                break
        else:
            raise ValueError("CurseForge returned too many versions. Search within a Minecraft version.")
        return sorted(versions, key=lambda version: version["date_published"], reverse=True)

    def compatible_version(self, project_id: str, minecraft_version: str, loader: str) -> dict:
        versions = self.project_versions(project_id, minecraft_version, loader)
        if not versions:
            raise ValueError("No compatible CurseForge version is available for this profile.")
        return next((version for version in versions if version["version_type"] == "release"), versions[0])

    def search_modpacks(self, query: str, *, index: str = "downloads", offset: int = 0, limit: int = 20) -> dict:
        params = {"gameId": 432, "classId": CONTENT_CLASSES["modpack"][0], "searchFilter": query,
                  "sortField": {"downloads": 6, "relevance": 2, "updated": 3, "newest": 11}.get(index, 6),
                  "sortOrder": "desc", "index": offset, "pageSize": min(50, limit)}
        result = self._request("/mods/search", params)
        if not isinstance(result["data"], list):
            raise ValueError("CurseForge returned an invalid modpack list.")
        return {"hits": [self._project(item, content_type="modpack") for item in result["data"]],
                "total_hits": result.get("pagination", {}).get("totalCount", len(result["data"]))}

    def modpack_project(self, project_id: str) -> dict:
        number = self._project_number(project_id)
        data = self._request(f"/mods/{number}")["data"]
        if curseforge_project_id(data["id"]) != project_id:
            raise ValueError("CurseForge returned a different modpack.")
        result = self._project(data, content_type="modpack")
        try:
            result["body"] = self._request(f"/mods/{number}/description")["data"]
        except ConnectionError:
            result["body"] = result["description"]
        return result

    def modpack_versions(self, project_id: str) -> list[dict]:
        number = self._project_number(project_id)
        self.modpack_project(project_id)
        versions = []
        for offset in range(0, 10000, 50):
            result = self._request(f"/mods/{number}/files", {"index": offset, "pageSize": 50})
            data = result["data"]
            if not isinstance(data, list):
                raise ValueError("CurseForge returned an invalid modpack version list.")
            for item in data:
                version = self._file(item, expected_project=project_id, content_type="modpack")
                if not version.get("_server_only"):
                    versions.append(version)
            total = result.get("pagination", {}).get("totalCount")
            if len(data) < 50 or isinstance(total, int) and offset + len(data) >= total:
                break
        else:
            raise ValueError("CurseForge returned too many modpack versions.")
        return sorted(versions, key=lambda version: version["date_published"], reverse=True)

    def modpack_version(self, version_id: str) -> dict:
        project, file_id = curseforge_file_ids(version_id)
        project_id = curseforge_project_id(project)
        self.modpack_project(project_id)
        data = self._request(f"/mods/{project}/files/{file_id}")["data"]
        if int(data["id"]) != file_id:
            raise ValueError("CurseForge returned a different modpack version.")
        return self._file(data, expected_project=project_id, content_type="modpack")

    def modpack_files(self, pins: list[tuple[int, int]], *, check_cancel: Callable = lambda: None) -> list[dict]:
        """Resolve the exact manifest files in bounded API batches, including bundled packs."""
        project_numbers = sorted({project for project, _ in pins})
        projects = {}
        for offset in range(0, len(project_numbers), 50):
            check_cancel()
            batch = project_numbers[offset:offset + 50]
            data = self._request("/mods", body={"modIds": batch})["data"]
            if not isinstance(data, list):
                raise ValueError("CurseForge returned invalid modpack projects.")
            for item in data:
                if item.get("id") not in batch or item["id"] in projects:
                    raise ValueError("CurseForge returned an unexpected modpack project.")
                projects[item["id"]] = self._project(item, content_type="file")
        if set(projects) != set(project_numbers):
            raise ValueError("A modpack project is no longer available on CurseForge.")
        by_file = {file_id: project for project, file_id in pins}
        if len(by_file) != len(pins):
            raise ValueError("The modpack lists a file more than once.")
        resolved = {}
        numbers = list(by_file)
        for offset in range(0, len(numbers), 50):
            check_cancel()
            batch = numbers[offset:offset + 50]
            data = self._request("/mods/files", body={"fileIds": batch})["data"]
            if not isinstance(data, list):
                raise ValueError("CurseForge returned invalid modpack files.")
            for item in data:
                file_id = item.get("id")
                if file_id not in batch or file_id in resolved or item.get("modId") != by_file[file_id]:
                    raise ValueError("CurseForge returned a different file than the modpack requested.")
                project = projects[by_file[file_id]]
                kind = project["project_type"]
                if kind not in {"mod", "resourcepack", "shader"}:
                    raise ValueError("A modpack file is not a mod, resource pack, or shader.")
                version = self._file(item, expected_project=project["id"], content_type=kind)
                version["_pack_folder"] = {"mod": "mods", "resourcepack": "resourcepacks", "shader": "shaderpacks"}[kind]
                version["_project_name"] = project["title"]
                resolved[file_id] = version
        if set(resolved) != set(numbers):
            raise ValueError("A required modpack file is no longer available on CurseForge.")
        return [resolved[file_id] for _, file_id in pins]

    def version(self, version_id: str) -> dict:
        project, file_id = curseforge_file_ids(version_id)
        project_id = curseforge_project_id(project)
        self._ensure_project_url(project_id)
        data = self._request(f"/mods/{project}/files/{file_id}")["data"]
        if int(data["id"]) != file_id:
            raise ValueError("CurseForge returned a different file.")
        result = self._file(data, expected_project=project_id)
        try:
            result["changelog"] = self._request(f"/mods/{project}/files/{file_id}/changelog")["data"]
        except ConnectionError:
            result["_changelog_unavailable"] = True
        return result

    def fabric_loader_metadata(self, version: dict) -> list[dict]:
        file = primary_file(version)
        manual = self.manual_file(file)
        if manual:
            return _fabric_loader_metadata(manual)
        if file.get("manual_required"):
            raise MetadataUnavailable("This file requires a manual CurseForge download.")
        key = ("cf-fabric-metadata", file["id"], file["size"], repr(file["hashes"]))
        def fetch() -> list[dict]:
            with _RangeJar(self._session(), file, {"User-Agent": self.HEADERS["User-Agent"]}) as source:
                return _fabric_loader_metadata(source)
        return self._cached_request(key, fetch)

    def identify_files(self, files: dict[str, Path]) -> dict[str, dict]:
        if not self.configured or not files:
            return {}
        fingerprints = {digest: self._cached_request(("cf-fingerprint", digest), lambda path=path: curseforge_fingerprint(path))
                        for digest, path in files.items()}
        result = self._request("/fingerprints/432", body={"fingerprints": list(set(fingerprints.values()))})["data"]
        matches = {item["file"]["fileFingerprint"]: item["file"] for item in result.get("exactMatches", [])}
        identified = {}
        for digest, fingerprint in fingerprints.items():
            data = matches.get(fingerprint)
            if not data:
                continue
            project = curseforge_project_id(data["modId"])
            self._ensure_project_url(project)
            version = self._file(data)
            file = primary_file(version)
            if verify_curseforge_file(file, files[digest]) and _digest(files[digest]) == digest:
                # Save a full local checksum so the existing identity cache can
                # validate CF identities independently of Murmur2 collisions.
                file["hashes"]["sha512"] = digest
                identified[digest] = version
        return identified

    def manual_file(self, file: dict) -> Path | None:
        with self._manual_lock:
            path = self._manual_files.get(file.get("id", ""))
        try:
            return path if path and verify_curseforge_file(file, path) else None
        except OSError:
            return None

    def register_manual_files(self, files: list[dict], paths: list[Path]) -> int:
        matched = {}
        for file in files:
            if not file.get("manual_required"):
                continue
            for path in paths:
                if verify_curseforge_file(file, path) and zipfile.is_zipfile(path):
                    matched[file["id"]] = path.resolve()
                    break
        if not matched:
            raise ValueError("These files do not match the required CurseForge versions. Download the files linked in this review.")
        with self._manual_lock:
            self._manual_files.update(matched)
        return len(matched)

    def download(self, file: dict, destination: Path, progress) -> None:
        if file.get("manual_required"):
            source = self.manual_file(file)
            if source is None:
                raise ValueError("Select the required manual CurseForge download in the installation review.")
            with source.open("rb") as input_stream, destination.open("xb") as output:
                size = 0
                for chunk in iter(lambda: input_stream.read(128 * 1024), b""):
                    size += len(chunk)
                    if size > file["size"]:
                        raise ValueError("The manually downloaded mod changed. Select the correct file again.")
                    output.write(chunk)
            if not verify_curseforge_file(file, destination) or not zipfile.is_zipfile(destination):
                raise ValueError("The manually downloaded mod changed. Select the correct file again.")
            on_bytes = getattr(progress, "on_bytes", None)
            if callable(on_bytes):
                on_bytes(destination.stat().st_size, file["size"])
            progress(f"Verified {file['filename']}")
            return
        url = validate_mod_download_url(file)
        hashes = file["hashes"]
        algorithm = next(key for key in ("sha512", "sha1", "md5") if hashes.get(key))
        digest = hashlib.new(algorithm)
        size = 0
        try:
            # No API key is sent to a CDN, including redirects.
            for redirects in range(4):
                with self._session().get(url, headers={"User-Agent": self.HEADERS["User-Agent"]},
                                         stream=True, timeout=(5, 30), allow_redirects=False) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        from urllib.parse import urljoin
                        url = validate_mod_download_url({**file, "url": urljoin(url, response.headers.get("Location", ""))})
                        continue
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ValueError("Unexpected CurseForge download response.")
                    with destination.open("xb") as output:
                        for chunk in response.iter_content(128 * 1024):
                            size += len(chunk)
                            if size > MAX_JAR_BYTES or size > file["size"]:
                                raise ValueError("The CurseForge download exceeds its expected size.")
                            output.write(chunk)
                            digest.update(chunk)
                            on_bytes = getattr(progress, "on_bytes", None)
                            if callable(on_bytes):
                                on_bytes(size, file["size"])
                    break
            else:
                raise ValueError("Too many CurseForge download redirects.")
        except requests.RequestException as error:
            raise ConnectionError("The CurseForge mod download failed. Check your connection and retry.") from error
        if size != file["size"] or digest.hexdigest() != str(hashes[algorithm]).lower() or not zipfile.is_zipfile(destination):
            raise ValueError("CurseForge mod verification failed. Retry the download.")
        progress(f"Verified {file['filename']}")
