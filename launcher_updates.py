"""GitHub release discovery and the compiled Windows updater handoff.

This module deliberately has no UI or account-storage dependencies. Source and
mock runs never download executables or modify an application installation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import zipfile


REPOSITORY = "jaydeeppatil23/Gr8Launcher"
RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHECK_INTERVAL = 6 * 60 * 60
MAX_PACKAGE_SIZE = 1024 * 1024 * 1024
MAX_EXPANDED_SIZE = 3 * MAX_PACKAGE_SIZE
VERSION_RE = re.compile(
    r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)
HASH_RE = re.compile(r"[0-9a-fA-F]{64}")
# Trust the exact GitHub release bucket in the commercial AWS partition.
RELEASE_S3_HOST_RE = re.compile(
    r"github-production-release-asset-2e65be\.s3"
    r"(?:[.-][a-z]{2}-[a-z]+-[0-9]+|\.dualstack\.[a-z]{2}-[a-z]+-[0-9]+)?"
    r"\.amazonaws\.com"
)
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(UpdateError):
    pass


def normalize_version(value: str) -> str:
    if not isinstance(value, str):
        raise UpdateError("The launcher version is invalid.")
    match = VERSION_RE.fullmatch(value.strip())
    if not match or any(int(part) > 65535 for part in match.group(1, 2, 3)):
        raise UpdateError("The launcher version is invalid.")
    if match[4] and any(part.isdigit() and len(part) > 1 and part[0] == "0"
                        for part in match[4].split(".")):
        raise UpdateError("The prerelease version is invalid.")
    return value.strip().removeprefix("v")


def compare_versions(left: str, right: str) -> int:
    a, b = (VERSION_RE.fullmatch(normalize_version(value)) for value in (left, right))
    numbers_a, numbers_b = (tuple(int(v) for v in m.group(1, 2, 3)) for m in (a, b))
    if numbers_a != numbers_b:
        return 1 if numbers_a > numbers_b else -1
    if a[4] is None or b[4] is None:
        return (a[4] is None) - (b[4] is None)
    parts_a, parts_b = a[4].split("."), b[4].split(".")
    for x, y in zip(parts_a, parts_b):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return 1 if int(x) > int(y) else -1
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return 1 if x > y else -1
    return (len(parts_a) > len(parts_b)) - (len(parts_a) < len(parts_b))


def application_root() -> Path:
    return (Path(sys.executable).parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent).resolve()


def read_version(root: Path | None = None) -> str:
    try:
        return normalize_version(((root or application_root()) / "VERSION")
                                 .read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        raise UpdateError("Could not read the launcher VERSION file.") from None


def version_label() -> str:
    try:
        return f"v{read_version()}"
    except UpdateError:
        return "Version unavailable"


def read_json(path: Path) -> dict:
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise UpdateError("The update contains an unsafe file path.")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(
        part in (".", "..") or part.endswith((".", " "))
        or part.split(".", 1)[0].upper() in RESERVED_NAMES
        or any(ord(c) < 32 or c in '<>"|?*' for c in part)
        for part in path.parts
    ):
        raise UpdateError("The update contains an unsafe file path.")
    return value


def managed_path(root: Path, relative: str) -> Path:
    path = root / safe_relative(relative)
    if not path.resolve().is_relative_to(root.resolve()):
        raise UpdateError("An update path points outside its application folder.")
    cursor = path
    while cursor != root:
        if cursor.is_symlink() or (cursor.exists() and getattr(cursor.stat(), "st_file_attributes", 0) & 0x400):
            raise UpdateError("Update paths must not contain links or junctions.")
        cursor = cursor.parent
    return path


def load_manifest(root: Path) -> dict:
    manifest = read_json(root / "bundle-manifest.json")
    if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), dict):
        raise UpdateError("This copy has no supported bundle inventory. Install the latest release manually.")
    normalize_version(manifest.get("version", ""))
    names = set()
    for relative, entry in manifest["files"].items():
        safe_relative(relative)
        folded = relative.casefold()
        if folded in names or folded == "bundle-manifest.json" or not isinstance(entry, dict):
            raise UpdateError("The update bundle inventory is invalid.")
        names.add(folded)
        if (not isinstance(entry.get("size"), int) or isinstance(entry["size"], bool)
                or entry["size"] < 0 or not HASH_RE.fullmatch(str(entry.get("sha256", "")))):
            raise UpdateError("The update bundle inventory is invalid.")
    if not {"Gr8Launcher.exe", "VERSION", "updater.exe"} <= manifest["files"].keys():
        raise UpdateError("The update bundle is missing required application files.")
    return manifest


def verify_manifest(root: Path, manifest: dict) -> None:
    for relative, entry in manifest["files"].items():
        path = managed_path(root, relative)
        if not path.is_file() or path.stat().st_size != entry["size"] or file_hash(path) != entry["sha256"].lower():
            raise UpdateError(f"The application file {relative} is missing or modified. Use manual installation.")


def generate_manifest(root: Path, version: str) -> None:
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "bundle-manifest.json":
            continue
        relative = path.relative_to(root).as_posix()
        managed_path(root, relative)
        files[relative] = {"size": path.stat().st_size, "sha256": file_hash(path)}
    atomic_json(root / "bundle-manifest.json", {"schema": 1, "version": normalize_version(version), "files": files})


def extract_bundle(package: Path, destination: Path, version: str, cancel: threading.Event) -> dict:
    if destination.exists():
        raise UpdateError("Update staging already exists.")
    with zipfile.ZipFile(package) as archive:
        entries = archive.infolist()
        if len(entries) > 30000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_SIZE:
            raise UpdateError("The update archive is too large.")
        names = set()
        files = set()
        directories = set()
        for entry in entries:
            name = entry.filename.rstrip("/")
            safe_relative(name)
            if name != "Gr8Launcher" and not name.startswith("Gr8Launcher/"):
                raise UpdateError("The ZIP is not a Gr8 Launcher application bundle.")
            key = name.casefold()
            if key in names or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise UpdateError("The update archive contains duplicate files or links.")
            names.add(key)
            (directories if entry.is_dir() else files).add(key)
            if entry.file_size > 1024 * 1024 and entry.file_size > max(1, entry.compress_size) * 1000:
                raise UpdateError("The update archive expands beyond the allowed limit.")
        for name in files:
            if name in directories or any(parent.as_posix().casefold() in files for parent in PurePosixPath(name).parents):
                raise UpdateError("The update archive has conflicting file paths.")
        needed = sum(entry.file_size for entry in entries)
        if shutil.disk_usage(destination.parent).free < needed + 32 * 1024 * 1024:
            raise UpdateError("There is not enough space to stage the update.")
        destination.mkdir()
        for entry in entries:
            if cancel.is_set():
                raise UpdateCancelled("Update download cancelled.")
            path = managed_path(destination, entry.filename.rstrip("/"))
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, path.open("xb") as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
    root = destination / "Gr8Launcher"
    manifest = load_manifest(root)
    if manifest["version"] != normalize_version(version) or read_version(root) != normalize_version(version):
        raise UpdateError("The downloaded bundle does not match the selected release.")
    verify_manifest(root, manifest)
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != set(manifest["files"]) | {"bundle-manifest.json"}:
        raise UpdateError("The update contains files missing from its inventory.")
    return manifest


def trusted_url(value: str, *, api: bool = False) -> str:
    if not isinstance(value, str):
        raise UpdateError("The release download address is invalid.")
    try:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        raise UpdateError("The release download address is invalid.") from None
    if api:
        valid_host = hostname == "api.github.com"
    else:
        valid_host = (
            hostname == "github.com"
            or hostname == "githubusercontent.com"
            or hostname.endswith(".githubusercontent.com")
            or RELEASE_S3_HOST_RE.fullmatch(hostname) is not None
        )
    if (parsed.scheme != "https" or not valid_host or parsed.username
            or parsed.password or port not in (None, 443)):
        raise UpdateError("The release contains an unsupported download address.")
    if hostname == "github.com" and not parsed.path.startswith(f"/{REPOSITORY}/releases/"):
        raise UpdateError("The download is outside the launcher repository.")
    return value


class _ReleaseRedirect(HTTPRedirectHandler):
    def __init__(self, *, api: bool):
        self.api = api
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        trusted_url(newurl, api=self.api)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def timestamp(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else 0.0


@dataclass(frozen=True)
class Release:
    version: str
    release_id: int
    page_url: str
    notes: str
    package_name: str
    package_id: int
    package_url: str
    package_size: int
    checksum_id: int
    checksum_url: str


def parse_release(document: dict, mode: str) -> Release:
    if document.get("draft") or document.get("prerelease"):
        raise UpdateError("The release is not a stable update.")
    version = normalize_version(document.get("tag_name", ""))
    if "-" in version:
        raise UpdateError("The release is not stable.")
    filename = f"Gr8Launcher-{version}-windows-x64" + ("-setup.exe" if mode == "installed" else ".zip")
    assets = document.get("assets", [])
    if not isinstance(assets, list):
        raise UpdateError("The release assets are invalid.")
    def asset(name: str) -> dict:
        matches = [item for item in assets if isinstance(item, dict) and item.get("name") == name]
        if len(matches) != 1 or matches[0].get("state", "uploaded") != "uploaded":
            raise UpdateError("The latest release is still being published. Try again later.")
        item = matches[0]
        if not isinstance(item.get("id"), int) or not isinstance(item.get("size"), int) or not 0 < item["size"] <= MAX_PACKAGE_SIZE:
            raise UpdateError("The release asset is invalid.")
        trusted_url(item.get("browser_download_url", ""))
        return item
    package, checksums = asset(filename), asset("SHA256SUMS.txt")
    if not isinstance(document.get("id"), int):
        raise UpdateError("The release identity is invalid.")
    return Release(version, document["id"], trusted_url(document.get("html_url", "")),
                   clean_release_notes(str(document.get("body") or ""))[:100000], filename, package["id"],
                   package["browser_download_url"], package["size"], checksums["id"], checksums["browser_download_url"])


def clean_release_notes(text: str) -> str:
    """Preserve Markdown formatting while hiding generated release metadata."""
    text = re.sub(r"<!-- gr8-launcher-preview:start -->.*?<!-- gr8-launcher-preview:end -->", "", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def checksum_for(text: str, filename: str) -> str:
    matches = []
    for line in text.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].lstrip("*") == filename:
            if not HASH_RE.fullmatch(parts[0]):
                raise UpdateError("The package checksum is invalid.")
            matches.append(parts[0].lower())
    if len(matches) != 1:
        raise UpdateError("The release has no unique checksum for this package.")
    return matches[0]


def installation_mode(root: Path) -> tuple[str, str]:
    if os.name != "nt":
        return "source", ""
    import winreg
    subkey = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{449A7BDC-D374-4AAB-8D31-D699214AC088}_is1"
    matches = []
    for hive, scope in ((winreg.HKEY_CURRENT_USER, "currentuser"), (winreg.HKEY_LOCAL_MACHINE, "allusers")):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                    try:
                        location = winreg.QueryValueEx(key, "InstallLocation")[0]
                    except OSError:
                        return "unknown", ""
                if not isinstance(location, str) or not location.strip():
                    return "unknown", ""
                if os.path.normcase(str(Path(location).resolve())) == os.path.normcase(str(root.resolve())):
                    matches.append(scope)
            except FileNotFoundError:
                pass
            except OSError:
                return "unknown", ""
    if len(set(matches)) > 1:
        return "unknown", ""
    return ("installed", matches[0]) if matches else ("portable", "")


def process_identity(pid: int | None = None) -> dict:
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    pid = pid or os.getpid()
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise UpdateError("Could not identify the launcher process.")
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            raise UpdateError("Could not identify the launcher process.")
        started = values[0].dwHighDateTime << 32 | values[0].dwLowDateTime
        return {"pid": pid, "started": str(started)}
    finally:
        kernel.CloseHandle(handle)


def process_alive(identity: dict) -> bool:
    try:
        return process_identity(int(identity["pid"])) == identity
    except (UpdateError, OSError, KeyError, ValueError, TypeError):
        return False


class UpdateService:
    def __init__(self, *, root: Path | None = None, cache: Path | None = None):
        self.root = (root or application_root()).resolve()
        self.mock = os.environ.get("GR8_UPDATE_MOCK") == "1"
        self.packaged = bool(getattr(sys, "frozen", False)) and os.name == "nt" and not self.mock
        self.mode, self.scope = installation_mode(self.root) if self.packaged else ("source", "")
        base = cache or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Gr8Launcher" / "updates"
        self.cache = base / "mock" if self.mock else base
        self.installation_id = hashlib.sha256(os.path.normcase(str(self.root)).encode()).hexdigest()
        self.state_path = self.cache / "installations" / self.installation_id / "state.json"
        self.discovery_path = self.cache / "discovery.json"
        self.cancel = threading.Event()
        self.prepared: Path | None = None
        self.prepared_release: Release | None = None
        self._lock = threading.Lock()
        saved = read_json(self.state_path).get("prepared")
        if self.packaged and isinstance(saved, str):
            candidate = Path(saved)
            if candidate.resolve().parent.parent == (self.cache / "operations").resolve():
                handoff = read_json(candidate)
                try:
                    release = Release(**handoff.get("release", {}))
                    trusted_url(release.page_url)
                    if (handoff.get("app_dir") == str(self.root) and handoff.get("old_version") == read_version(self.root)
                            and compare_versions(release.version, read_version(self.root)) > 0
                            and not read_json(candidate.parent / "result.json")
                            and Path(handoff.get("package", "")).is_file()):
                        self.prepared, self.prepared_release = candidate, release
                except (UpdateError, TypeError, ValueError, OSError):
                    pass

    def can_apply(self) -> bool:
        return self.packaged and self.mode in ("installed", "portable") and (self.root / "updater.exe").is_file()

    def _response(self, url: str, *, etag: str = "", api: bool = False):
        trusted_url(url, api=api)
        headers = {"User-Agent": f"Gr8Launcher-Updater/{version_label()}", "Accept": "application/vnd.github+json" if api else "application/octet-stream"}
        if etag:
            headers["If-None-Match"] = etag
        response = build_opener(_ReleaseRedirect(api=api)).open(Request(url, headers=headers), timeout=10)
        trusted_url(response.geturl(), api=api)
        return response

    def check(self, *, manual: bool = False) -> Release | None:
        current = read_version(self.root)
        if self.mock:
            major, minor, patch = current.split("-", 1)[0].split(".")
            version = f"{major}.{minor}.{int(patch) + 1}"
            return Release(version, 1, f"https://github.com/{REPOSITORY}/releases", "Preview of the launcher update flow. No application files will be changed.", "preview.zip", 1, "", 100, 2, "")
        cache = read_json(self.discovery_path)
        now = time.time()
        if now < timestamp(cache.get("backoff_until")):
            raise UpdateError("GitHub is limiting update checks. Try again later.")
        document = cache.get("release")
        if manual or now - timestamp(cache.get("last_success")) >= CHECK_INTERVAL or not isinstance(document, dict):
            try:
                with self._response(RELEASE_URL, etag=str(cache.get("etag", "")), api=True) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
                    if len(raw) > 2 * 1024 * 1024:
                        raise UpdateError("The update response is too large.")
                    document = json.loads(raw)
                    if not isinstance(document, dict):
                        raise UpdateError("The update response is invalid.")
                    parse_release(document, self.mode)
                    cache = {"release": document, "etag": response.headers.get("ETag", ""), "last_success": now}
            except HTTPError as error:
                if error.code == 304 and isinstance(document, dict):
                    parse_release(document, self.mode)
                    cache["last_success"] = now
                elif error.code == 304:
                    cache.pop("etag", None)
                    atomic_json(self.discovery_path, cache)
                    return self.check(manual=True)
                elif error.code in (403, 429):
                    try:
                        backoff = max(now + float(error.headers.get("Retry-After", "300")), float(error.headers.get("X-RateLimit-Reset", "0")))
                    except ValueError:
                        backoff = now + 300
                    cache["backoff_until"] = backoff
                    atomic_json(self.discovery_path, cache)
                    raise UpdateError("GitHub is limiting update checks. Try again later.") from None
                else:
                    raise UpdateError("Could not check for launcher updates. Check your connection and try again.") from None
            except (OSError, ValueError):
                raise UpdateError("Could not check for launcher updates. Check your connection and try again.") from None
            atomic_json(self.discovery_path, cache)
        release = parse_release(document, self.mode)
        return release if compare_versions(release.version, current) > 0 else None

    def should_notify(self, version: str) -> bool:
        state = read_json(self.state_path)
        return state.get("snoozed_version") != version or time.time() >= timestamp(state.get("snoozed_until"))

    def _verify_release_identity(self, release: Release) -> None:
        with self._response(f"https://api.github.com/repos/{REPOSITORY}/releases/{release.release_id}", api=True) as response:
            content = response.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise UpdateError("The release response is too large.")
        document = json.loads(content)
        if not isinstance(document, dict):
            raise UpdateError("The release response is invalid.")
        current = parse_release(document, self.mode)
        if (current.release_id, current.version, current.package_id, current.package_size, current.checksum_id) != (
                release.release_id, release.version, release.package_id, release.package_size, release.checksum_id):
            raise UpdateError("The release changed while downloading. Check for updates and try again.")

    def snooze(self, version: str) -> None:
        state = read_json(self.state_path)
        state.update(snoozed_version=version, snoozed_until=time.time() + 24 * 60 * 60)
        atomic_json(self.state_path, state)

    def download(self, release: Release, progress) -> Path | None:
        if self.mock:
            self.cancel.clear()
            for completed in range(0, 101, 5):
                if self.cancel.wait(0.04):
                    raise UpdateCancelled("Update preview cancelled.")
                progress(completed, 100)
            return None
        if not self.can_apply():
            raise UpdateError("Automatic installation is available only in packaged Windows copies.")
        if not self._lock.acquire(blocking=False):
            raise UpdateError("An update download is already in progress.")
        operation = self.cache / "operations" / uuid.uuid4().hex
        staging = None
        try:
            if self.pending_live():
                raise UpdateError("Another update is already being applied.")
            self.cancel.clear()
            operation.mkdir(parents=True)
            self._verify_release_identity(release)
            with self._response(release.checksum_url) as response:
                content = response.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    raise UpdateError("The checksum file is too large.")
            expected = checksum_for(content.decode("utf-8-sig"), release.package_name)
            if shutil.disk_usage(operation).free < release.package_size + 32 * 1024 * 1024:
                raise UpdateError("There is not enough space to download the update.")
            partial = operation / "package.part"
            completed = 0
            deadline = time.monotonic() + 30 * 60
            with self._response(release.package_url) as response, partial.open("xb") as stream:
                while True:
                    if self.cancel.is_set():
                        raise UpdateCancelled("Update download cancelled.")
                    if time.monotonic() > deadline:
                        raise UpdateError("The download took too long. Try again.")
                    block = response.read(128 * 1024)
                    if not block:
                        break
                    completed += len(block)
                    if completed > release.package_size:
                        raise UpdateError("The downloaded package size is invalid.")
                    stream.write(block)
                    progress(completed, release.package_size)
            if completed != release.package_size or file_hash(partial) != expected:
                raise UpdateError("The update failed its integrity check. Download it again.")
            self._verify_release_identity(release)
            package = operation / release.package_name
            partial.rename(package)
            old_manifest = load_manifest(self.root)
            if old_manifest["version"] != read_version(self.root):
                raise UpdateError("The installed bundle inventory does not match VERSION.")
            verify_manifest(self.root, old_manifest)
            if self.mode == "portable":
                staging = self.root.parent / f".gr8-update-{operation.name}"
                staging.mkdir()
                extract_bundle(package, staging / "payload", release.version, self.cancel)
            shutil.copy2(self.root / "updater.exe", operation / "updater.exe")
            for font in (self.root / "_internal" / ".assets" / "fonts" / "PixelifySans.ttf",
                         self.root / ".assets" / "fonts" / "PixelifySans.ttf"):
                if font.is_file():
                    shutil.copy2(font, operation / "PixelifySans.ttf")
                    break
            handoff = {"schema": 1, "operation_id": operation.name, "nonce": uuid.uuid4().hex,
                       "app_dir": str(self.root), "mode": self.mode, "scope": self.scope,
                       "old_version": read_version(self.root), "new_version": release.version,
                       "package": str(package), "package_hash": expected,
                       "staging": str(staging) if staging else "", "state_path": str(self.state_path), "release": asdict(release)}
            atomic_json(operation / "handoff.json", handoff)
            (operation / "Recover.cmd").write_text('@echo off\n"%~dp0updater.exe" --recover "%~dp0handoff.json"\n', encoding="utf-8")
            self.prepared = operation / "handoff.json"
            self.prepared_release = release
            state = read_json(self.state_path)
            state["prepared"] = str(self.prepared)
            atomic_json(self.state_path, state)
            return self.prepared
        except Exception:
            # Only remove this attempt's generated trees, after containment checks.
            if staging and staging.resolve() == self.root.parent / f".gr8-update-{operation.name}":
                shutil.rmtree(staging, ignore_errors=True)
            if operation.resolve().parent == (self.cache / "operations").resolve():
                shutil.rmtree(operation, ignore_errors=True)
            raise
        finally:
            self._lock.release()

    def pending_live(self) -> bool:
        state = read_json(self.state_path)
        operation = state.get("pending")
        if not operation:
            return False
        handoff = Path(operation)
        if handoff.resolve().parent.parent != (self.cache / "operations").resolve():
            raise UpdateError("The pending update state is invalid.")
        result = read_json(handoff.parent / "result.json")
        if result:
            return False
        helper = read_json(handoff.parent / "ready.json").get("process", {})
        return process_alive(helper) if helper else True

    def start_helper(self) -> subprocess.Popen:
        if not self.can_apply() or not self.prepared:
            raise UpdateError("No verified update is ready to apply.")
        if self.pending_live():
            raise UpdateError("Another update is already being applied.")
        handoff = read_json(self.prepared)
        if handoff.get("app_dir") != str(self.root):
            raise UpdateError("The update target is invalid.")
        handoff["launcher"] = process_identity()
        atomic_json(self.prepared, handoff)
        state = read_json(self.state_path)
        state["pending"] = str(self.prepared)
        atomic_json(self.state_path, state)
        try:
            return subprocess.Popen([str(self.prepared.parent / "updater.exe"), "--apply", str(self.prepared)],
                                    cwd=self.prepared.parent, creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError:
            state.pop("pending", None)
            atomic_json(self.state_path, state)
            raise UpdateError("Could not start the updater. Use the release page to update manually.") from None

    def result(self) -> dict:
        pending = read_json(self.state_path).get("pending")
        if not pending:
            return {}
        path = Path(pending)
        if path.resolve().parent.parent != (self.cache / "operations").resolve():
            return {}
        return read_json(path.parent / "result.json")


def startup_guard() -> None:
    """Run before importing persistence or initializing the normal UI."""
    if not getattr(sys, "frozen", False) or os.name != "nt" or os.environ.get("GR8_UPDATE_MOCK") == "1":
        return
    service = UpdateService()
    state = read_json(service.state_path)
    pending = state.get("pending")
    if not pending:
        return
    handoff_path = Path(pending)
    if handoff_path.resolve().parent.parent != (service.cache / "operations").resolve():
        raise UpdateError("The pending update state is invalid.")
    handoff = read_json(handoff_path)
    if handoff.get("app_dir") != str(service.root):
        raise UpdateError("The pending update target is invalid.")
    session = os.environ.get("GR8_UPDATE_HANDOFF", "")
    if session == pending and os.environ.get("GR8_UPDATE_NONCE") == handoff.get("nonce"):
        return
    result = read_json(handoff_path.parent / "result.json")
    if result.get("status") in ("succeeded", "rolled_back", "recovered", "failed"):
        return
    ready = read_json(handoff_path.parent / "ready.json")
    if process_alive(ready.get("process", {})):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, "An update is already in progress. Please wait for the launcher to reopen.", "Gr8 Launcher", 0x40)
    else:
        subprocess.Popen([str(handoff_path.parent / "updater.exe"), "--recover", str(handoff_path)], creationflags=subprocess.CREATE_NO_WINDOW)
    raise SystemExit(0)


def acknowledge_startup() -> None:
    path = os.environ.pop("GR8_UPDATE_HANDOFF", "")
    nonce = os.environ.pop("GR8_UPDATE_NONCE", "")
    if not path or not getattr(sys, "frozen", False) or os.name != "nt" or os.environ.get("GR8_UPDATE_MOCK") == "1":
        return
    handoff_path = Path(path)
    handoff = read_json(handoff_path)
    if handoff.get("app_dir") != str(application_root()) or nonce != handoff.get("nonce"):
        raise UpdateError("The update startup acknowledgement is invalid.")
    atomic_json(handoff_path.parent / "ack.json", {"operation_id": handoff["operation_id"],
                "nonce": nonce, "version": read_version(), "process": process_identity()})
