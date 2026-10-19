"""Gr8 Launcher - Hero Banner Engine.

Resolves and caches widescreen hero banners for a given Minecraft version using a
resilient, multi-source fallback chain. Banners are persisted inside
``<.minecraft>/gr8launcher/banners`` (alongside skins and other launcher data).

Resolution walks version keys from the most specific to the most generic
(``1.21.11`` -> ``1.21`` -> default), so sub-versions such as 1.21.1 and 1.21.11
can resolve to different artwork while older releases (1.8, 1.12.2, snapshots,
alpha/beta) still work.

For each key the following sources are tried in order, falling through to the
next on any lookup, download or validation failure:

    1. Curated knowledge base (``KNOWN_BANNERS``)
    2. Minecraft Wiki dedicated widescreen banner files
       (``File:<key> banner.<ext>``)
    3. Minecraft Wiki version page image (``Java Edition <key>``)
    4. Mojang launcher-content patch-notes artwork
    5. Minecraft Wiki file search
    6. Default banner

Every download is written atomically and validated (magic bytes, minimum size
and decodable dimensions) before it replaces the cached file. Successes and
short-lived failures are recorded in ``index.json`` to avoid hammering sources.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import storage

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

LEGACY_BANNER_CACHE_DIR = Path(__file__).resolve().parent / ".assets" / "cache" / "banners"

_BANNER_DIR_CACHE: Path | None = None
_INDEX_LOCK = threading.RLock()
_STORE_LOCK = threading.RLock()
_INDEX: dict | None = None
_KEY_LOCKS: dict[str, asyncio.Lock] = {}

# Failure markers are kept briefly so transient outages recover quickly but we
# do not re-query every source on every selection change.
NEGATIVE_TTL_SECONDS = 15 * 60
# Once a key is known to have no genuine banner/artwork we stop re-querying the
# wiki/Mojang for a while (screenshots for that key are still used as fallback).
STRONG_MISS_TTL_SECONDS = 12 * 60 * 60
# Cached patches/notes document lifetime.
PATCH_NOTES_TTL_SECONDS = 12 * 60 * 60

USER_AGENT = "Gr8Launcher/1.0 (contact@gr8launcher.local)"
WIKI_API = "https://minecraft.wiki/api.php"
MOJANG_CONTENT_BASE = "https://launchercontent.mojang.com"
MOJANG_PATCH_NOTES_URL = f"{MOJANG_CONTENT_BASE}/v2/javaPatchNotes.json"

# Minecraft Wiki category that collects per-version banner/header artwork.
WIKI_VERSION_BANNER_CATEGORY = "Category:Version banners"
VERSION_BANNER_CACHE_TTL_SECONDS = 24 * 60 * 60

# Validation thresholds. Widescreen banners are ~1170x500, wiki version-page
# images ~854x480 and Mojang patch-notes artwork 540x540, so these bounds accept
# every legitimate source while rejecting tiny icons/placeholders.
MIN_BYTES = 4096
MIN_WIDTH = 320
MIN_HEIGHT = 160

# ---------------------------------------------------------------------------
# Curated knowledge base
# ---------------------------------------------------------------------------

# Direct high-res widescreen official update banners from Minecraft Wiki & Mojang.
KNOWN_BANNERS = {
    "1.21": "https://minecraft.wiki/images/1.21_banner.jpg",
    "1.20": "https://minecraft.wiki/images/1.20_banner.jpg",
    "1.19": "https://minecraft.wiki/images/1.19_banner.jpg",
    "1.18": "https://minecraft.wiki/images/1.18.1_banner.jpg",
    "1.17": "https://minecraft.wiki/images/1.17.1_banner.jpg",
    "1.16": "https://minecraft.wiki/images/1.16.3_banner.jpg",
    "1.15": "https://minecraft.wiki/images/BuzzyBeesJava.jpg",
    "1.14": "https://minecraft.wiki/images/1.14.1_banner.jpg",
    "1.13": "https://minecraft.wiki/images/Update_Aquatic_Phase_2.png",
    "1.12": "https://minecraft.wiki/images/World_of_Color_Update.png",
    "1.11": "https://minecraft.wiki/images/ExplorationUpdateJava.png",
    "1.10": "https://minecraft.wiki/images/Frostburn_Update.png",
    "1.9": "https://minecraft.wiki/images/Combat_Update.png",
    "1.8": "https://minecraft.wiki/images/The_Bountiful_Update.png",
}

DEFAULT_BANNER_KEY = "1.21"

_IMAGE_EXTENSIONS = ("jpg", "jpeg", "png", "webp", "gif", "bmp")


def get_banner_cache_dir() -> Path:
    """Returns the persistent banner directory inside .minecraft/gr8launcher/banners."""
    global _BANNER_DIR_CACHE
    if _BANNER_DIR_CACHE is not None:
        return _BANNER_DIR_CACHE

    try:
        directory = storage.get_data_dir() / "banners"
    except Exception:
        directory = LEGACY_BANNER_CACHE_DIR

    directory.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_banners(directory)
    _BANNER_DIR_CACHE = directory
    return directory


def _migrate_legacy_banners(target_dir: Path) -> None:
    """Moves banners previously cached under .assets/cache/banners into the data dir."""
    if target_dir == LEGACY_BANNER_CACHE_DIR or not LEGACY_BANNER_CACHE_DIR.is_dir():
        return
    try:
        for item in list(LEGACY_BANNER_CACHE_DIR.iterdir()):
            if not item.is_file():
                continue
            dest = target_dir / item.name
            if dest.exists():
                continue
            try:
                # shutil.move handles cross-volume moves that os.replace rejects.
                shutil.move(str(item), str(dest))
            except OSError:
                pass
        if LEGACY_BANNER_CACHE_DIR.is_dir() and not any(LEGACY_BANNER_CACHE_DIR.iterdir()):
            try:
                LEGACY_BANNER_CACHE_DIR.rmdir()
            except OSError:
                pass
    except OSError:
        pass


BANNER_CACHE_DIR = get_banner_cache_dir()


def ensure_cache_dir() -> Path:
    return get_banner_cache_dir()


def get_cached_banner_file(key: str) -> Path:
    """Returns the default cache path for a key (extension-agnostic lookups live elsewhere)."""
    return get_banner_cache_dir() / f"{_safe_key(key)}.jpg"


def _safe_key(key: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (key or "").strip())
    return cleaned or "default"


# ---------------------------------------------------------------------------
# Version key resolution
# ---------------------------------------------------------------------------

_SNAPSHOT_RE = re.compile(r"\b(\d{2}w\d{2}[a-z])\b", re.IGNORECASE)
_DOTTED_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-\s]?(?:pre|rc)[-\s]?\d*)?)", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"^(\d+(?:\.\d+)+)[-\s]*(pre|rc)[-\s]*(\d+)$", re.IGNORECASE)
_NUMBERED_RE = re.compile(r"^(\d+(?:\.\d+)+)")


def _extract_version_token(ver_str: str) -> str | None:
    """Extracts the most Minecraft-like version token from a possibly messy string."""
    text = (ver_str or "").strip()
    if not text:
        return None

    snapshot = _SNAPSHOT_RE.search(text)
    if snapshot:
        return snapshot.group(1).lower()

    candidates = _DOTTED_RE.findall(text)
    for candidate in candidates:
        # Loader prefixes (fabric-loader-0.16.0-1.21.1) produce a leading "0.x"
        # token; real Minecraft versions have a non-zero major component.
        if candidate.split(".", 1)[0] not in ("0", ""):
            return candidate.strip()
    if candidates:
        return candidates[0].strip()
    return text


def version_key_candidates(ver_str: str) -> list[str]:
    """Ordered, de-duplicated version keys from most specific to most generic."""
    raw = (ver_str or "").strip()
    if not raw:
        return [DEFAULT_BANNER_KEY]

    ordered: list[str] = []

    def add(key: str) -> None:
        key = (key or "").strip()
        if key and key not in ordered:
            ordered.append(key)

    add(raw)

    token = _extract_version_token(raw)
    if token and token != raw:
        add(token)

    if token:
        numbered = _NUMBERED_RE.match(token)
        if numbered:
            numbered_key = numbered.group(1)
            add(numbered_key)
            segments = numbered_key.split(".")
            for length in range(len(segments) - 1, 1, -1):
                add(".".join(segments[:length]))

    low = raw.lower()
    if re.match(r"^a\d", low) or low.startswith("alpha"):
        add("Alpha")
    elif re.match(r"^b\d", low) or low.startswith("beta"):
        add("Beta")

    return ordered


def normalize_version_key(ver_str: str) -> str:
    """Returns the most specific resolvable banner key for a version string."""
    keys = version_key_candidates(ver_str)
    return keys[0] if keys else DEFAULT_BANNER_KEY


def resolve_banner_keys(target: dict | None) -> list[str]:
    if not target:
        return [DEFAULT_BANNER_KEY]

    for field in ("minecraft_version", "version_id", "name"):
        value = str(target.get(field, "") or "").strip()
        if value and value.lower() != "latest-release":
            return version_key_candidates(value)

    return [DEFAULT_BANNER_KEY]


def resolve_banner_key(target: dict | None) -> str:
    keys = resolve_banner_keys(target)
    return keys[0] if keys else DEFAULT_BANNER_KEY


def wiki_page_titles_for_key(key: str) -> list[str]:
    """Candidate Minecraft Wiki article titles that carry a version's infobox image."""
    cleaned = (key or "").strip()
    titles: list[str] = []

    def add(title: str) -> None:
        if title and title not in titles:
            titles.append(title)

    add(f"Java Edition {cleaned}")

    suffix = _SUFFIX_RE.match(cleaned)
    if suffix:
        base, kind, number = suffix.group(1), suffix.group(2).lower(), suffix.group(3)
        label = "Pre-Release" if kind == "pre" else "Release Candidate"
        add(f"Java Edition {base} {label} {number}")
        add(f"Java Edition {base}-{kind}{number}")

    low = cleaned.lower()
    if re.match(r"^a\d", low) or low.startswith("alpha"):
        add("Java Edition Alpha")
    elif re.match(r"^b\d", low) or low.startswith("beta"):
        add("Java Edition Beta")
    elif re.match(r"^c\d", low) or low.startswith("classic"):
        add("Java Edition Classic")
    elif low.startswith("inf") or "infdev" in low:
        add("Java Edition Infdev")
    elif low.startswith("indev"):
        add("Java Edition Indev")

    return titles


# ---------------------------------------------------------------------------
# Image inspection / validation
# ---------------------------------------------------------------------------

def _sniff_extension(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:2] == b"BM":
        return "bmp"
    return None


def _probe_dimensions(data: bytes) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:
        return None


def _probe_file_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:
        return None


def is_valid_banner_file(path: Path) -> bool:
    """True when the file exists and looks like a real, adequately sized image."""
    try:
        if not path.is_file() or path.stat().st_size < MIN_BYTES:
            return False
        with open(path, "rb") as handle:
            head = handle.read(32)
        if _sniff_extension(head) is None:
            return False
        dimensions = _probe_file_dimensions(path)
        if dimensions and (dimensions[0] < MIN_WIDTH or dimensions[1] < MIN_HEIGHT):
            return False
        return True
    except OSError:
        return False


def _data_is_usable(data: bytes) -> bool:
    if len(data) < MIN_BYTES:
        return False
    if _sniff_extension(data[:32]) is None:
        return False
    dimensions = _probe_dimensions(data)
    if dimensions and (dimensions[0] < MIN_WIDTH or dimensions[1] < MIN_HEIGHT):
        return False
    return True


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: float = 15.0, max_bytes: int = 32 * 1024 * 1024) -> bytes | None:
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "image/*,application/json,*/*"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except Exception:
        return None


def _http_get_json(url: str, timeout: float = 15.0) -> dict | None:
    raw = _http_get(url, timeout=timeout, max_bytes=8 * 1024 * 1024)
    if not raw:
        return None
    try:
        document = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeError):
        return None
    return document if isinstance(document, dict) else None


def _normalized_title(title: str) -> str:
    return title.replace("_", " ").strip().lower()


def _wiki_query(params: dict) -> dict | None:
    query = {**params, "format": "json", "formatversion": 1}
    url = f"{WIKI_API}?{urllib.parse.urlencode(query)}"
    return _http_get_json(url)


def _wiki_imageinfo(titles: list[str]) -> dict[str, dict]:
    """Maps normalized title -> imageinfo dict for the requested File: titles."""
    if not titles:
        return {}
    data = _wiki_query(
        {
            "action": "query",
            "titles": "|".join(titles),
            "prop": "imageinfo",
            "iiprop": "url|size|mime",
            "redirects": 1,
        }
    )
    if not data:
        return {}

    query = data.get("query", {}) if isinstance(data, dict) else {}
    aliases: dict[str, str] = {}
    for item in query.get("normalized", []) or []:
        if isinstance(item, dict) and "from" in item and "to" in item:
            aliases[_normalized_title(item["from"])] = item["to"]
    for item in query.get("redirects", []) or []:
        if isinstance(item, dict) and "from" in item and "to" in item:
            aliases[_normalized_title(item["from"])] = item["to"]

    results: dict[str, dict] = {}
    for page in query.get("pages", {}).values():
        if not isinstance(page, dict):
            continue
        info = page.get("imageinfo") or []
        if not info or not isinstance(info[0], dict) or not info[0].get("url"):
            continue
        title = page.get("title", "")
        results[_normalized_title(title)] = info[0]
        for original, resolved in aliases.items():
            if _normalized_title(resolved) == _normalized_title(title):
                results[original] = info[0]
    return results


def _pick_image(info: dict | None) -> str | None:
    if not isinstance(info, dict):
        return None
    url = info.get("url")
    width = info.get("width")
    height = info.get("height")
    if not url:
        return None
    if isinstance(width, int) and isinstance(height, int) and (width < MIN_WIDTH or height < MIN_HEIGHT):
        return None
    return url


# ---------------------------------------------------------------------------
# Individual sources
# ---------------------------------------------------------------------------

def _wiki_banner_file_url(key: str) -> str | None:
    """Minecraft Wiki dedicated widescreen banner file for a version key."""
    names = [f"File:{key} banner.{ext}" for ext in ("jpg", "jpeg", "png", "webp")]
    names.append(f"File:Java Edition {key} banner.jpg")
    info_map = _wiki_imageinfo(names)
    for name in names:
        url = _pick_image(info_map.get(_normalized_title(name)))
        if url:
            return url
    return None


def _wiki_version_page_image(key: str) -> str | None:
    """Infobox/lead image of the version's wiki article (sub-version specific)."""
    titles = wiki_page_titles_for_key(key)
    if not titles:
        return None
    data = _wiki_query(
        {
            "action": "query",
            "titles": "|".join(titles),
            "prop": "pageimages",
            "piprop": "original",
            "redirects": 1,
        }
    )
    if not data:
        return None
    query = data.get("query", {}) if isinstance(data, dict) else {}
    pages = query.get("pages", {})
    if isinstance(pages, dict):
        pages = list(pages.values())
    by_title: dict[str, str] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        original = (page.get("original") or {}).get("source")
        if original:
            by_title[_normalized_title(page.get("title", ""))] = original
    for title in titles:
        url = by_title.get(_normalized_title(title))
        if url:
            return url
    # Fall back to any returned page image (redirects may rename the page).
    return next(iter(by_title.values()), None)


def _wiki_search_file_image(key: str) -> str | None:
    data = _wiki_query(
        {
            "action": "query",
            "list": "search",
            "srsearch": f"{key} banner",
            "srnamespace": 6,
            "srlimit": 5,
        }
    )
    if not data:
        return None
    results = (data.get("query", {}) or {}).get("search", []) or []
    titles = [item.get("title") for item in results if isinstance(item, dict) and item.get("title")]
    if not titles:
        return None
    # Only trust results whose file name actually references the version, so a
    # generic "banner" match cannot leak an unrelated image into the cache.
    needle = _normalized_title(key).replace(" ", "")
    titles = [t for t in titles if needle and needle in _normalized_title(t).replace(" ", "")]
    if not titles:
        return None
    info_map = _wiki_imageinfo(titles)
    for title in titles:
        url = _pick_image(info_map.get(_normalized_title(title)))
        if url:
            return url
    return None


_PATCH_NOTES_CACHE: dict | None = None
_PATCH_NOTES_LOCK = threading.Lock()


def _load_patch_notes() -> list[dict]:
    """Loads Mojang's launcher patch-notes document (memory + on-disk cache)."""
    global _PATCH_NOTES_CACHE
    with _PATCH_NOTES_LOCK:
        if _PATCH_NOTES_CACHE is not None:
            entries = _PATCH_NOTES_CACHE.get("entries", [])
            if time.time() - _PATCH_NOTES_CACHE.get("_loaded_at", 0) < PATCH_NOTES_TTL_SECONDS:
                return entries

        cache_file = get_banner_cache_dir() / "mojang_patch_notes.json"
        entries: list[dict] = []
        try:
            if cache_file.is_file() and time.time() - cache_file.stat().st_mtime < PATCH_NOTES_TTL_SECONDS:
                document = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(document, dict) and isinstance(document.get("entries"), list):
                    entries = document["entries"]
        except (OSError, ValueError):
            entries = []

        if not entries:
            document = _http_get_json(MOJANG_PATCH_NOTES_URL)
            if isinstance(document, dict) and isinstance(document.get("entries"), list):
                entries = document["entries"]
                try:
                    cache_file.write_text(json.dumps({"entries": entries}), encoding="utf-8")
                except OSError:
                    pass

        _PATCH_NOTES_CACHE = {"entries": entries, "_loaded_at": time.time()}
        return entries


def _mojang_patch_notes_url(key: str) -> str | None:
    target = (key or "").strip().lower()
    if not target:
        return None
    for entry in _load_patch_notes():
        if not isinstance(entry, dict):
            continue
        version = str(entry.get("version", "")).strip().lower()
        if version != target:
            continue
        image = entry.get("image") or {}
        url = image.get("url") if isinstance(image, dict) else None
        if url:
            if url.startswith("http"):
                return url
            return f"{MOJANG_CONTENT_BASE}{url if url.startswith('/') else '/' + url}"
    return None


_VERSION_BANNER_OTHER_RE = re.compile(
    r"\b(bedrock|pocket|education|playstation|xbox|nintendo|switch|wii|3ds|android|ios|"
    r"chinese|china|legacy|alpha|beta|classic|indev|infdev)\b",
    re.IGNORECASE,
)
_VERSION_BANNER_PENALTY_RE = re.compile(
    r"(?i)(^|[^a-z])(?:pre|rc|snapshot|experimental)[a-z]?\d*|preview|panorama|screens?hot|menuscreen"
)
_VERSION_TOKEN_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?|\d{2}w\d{2}[a-z])", re.IGNORECASE)

_VERSION_BANNER_INDEX: dict | None = None
_VERSION_BANNER_LOCK = threading.Lock()


def _score_version_banner_name(name: str) -> int:
    low = name.lower()
    score = 0
    if "banner" in low:
        score += 40
    if "header" in low:
        score += 35
    if "update" in low:
        score += 20
    if "art" in low:
        score += 15
    if "logo" in low:
        score += 10
    if _VERSION_BANNER_PENALTY_RE.search(name):
        score -= 30
    return score


def _fetch_version_banner_index() -> dict:
    """Enumerates Category:Version banners into {version_token: {title, score}}."""
    tokens: dict[str, dict] = {}
    continuation: dict | None = None
    for _ in range(8):
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": WIKI_VERSION_BANNER_CATEGORY,
            "cmtype": "file",
            "cmlimit": 500,
        }
        if continuation:
            params.update(continuation)
        data = _wiki_query(params)
        if not data:
            break
        query = data.get("query", {}) if isinstance(data, dict) else {}
        for member in query.get("categorymembers", []) or []:
            title = member.get("title") if isinstance(member, dict) else None
            if not title:
                continue
            name = title.split(":", 1)[-1]
            if _VERSION_BANNER_OTHER_RE.search(name):
                continue
            match = _VERSION_TOKEN_RE.search(name)
            if not match:
                continue
            token = match.group(1).lower()
            score = _score_version_banner_name(name)
            previous = tokens.get(token)
            if previous is None or score > previous.get("score", -999):
                tokens[token] = {"title": title, "score": score}
        continuation = data.get("continue") if isinstance(data, dict) else None
        if not continuation:
            break
    return tokens


def _version_banner_index() -> dict:
    global _VERSION_BANNER_INDEX
    with _VERSION_BANNER_LOCK:
        if _VERSION_BANNER_INDEX is not None:
            return _VERSION_BANNER_INDEX

        cache_file = get_banner_cache_dir() / "version_banners.json"
        tokens: dict = {}
        try:
            if cache_file.is_file() and time.time() - cache_file.stat().st_mtime < VERSION_BANNER_CACHE_TTL_SECONDS:
                document = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(document, dict) and isinstance(document.get("tokens"), dict):
                    tokens = document["tokens"]
        except (OSError, ValueError):
            tokens = {}

        if not tokens:
            tokens = _fetch_version_banner_index()
            if tokens:
                try:
                    cache_file.write_text(json.dumps({"tokens": tokens}), encoding="utf-8")
                except OSError:
                    pass

        _VERSION_BANNER_INDEX = tokens
        return tokens


def _version_banner_url(key: str) -> str | None:
    """Best-matching banner from Category:Version banners for a version key."""
    index = _version_banner_index()
    target = (key or "").strip().lower()
    if not index or not target:
        return None

    best: tuple | None = None
    for token, meta in index.items():
        if token == target:
            distance = 0
        elif token.startswith(target + "."):
            distance = token.count(".") - target.count(".")
        else:
            continue
        candidate = (meta.get("score", 0), -distance, meta.get("title", ""))
        if best is None or candidate > best[0]:
            best = (candidate, meta.get("title"))

    if not best or not best[1]:
        return None
    title = best[1]
    info_map = _wiki_imageinfo([title])
    return _pick_image(info_map.get(_normalized_title(title)))


def _strong_sources(key: str):
    """Genuine key art / update banners for a key, most preferred first."""
    curated = KNOWN_BANNERS.get(key)
    if curated:
        yield curated, "curated"

    for resolver, label in (
        (_wiki_banner_file_url, "wiki-banner-file"),
        (_version_banner_url, "wiki-version-banner"),
        (_mojang_patch_notes_url, "mojang-patch-notes"),
    ):
        try:
            url = resolver(key)
        except Exception:
            url = None
        if url:
            yield url, label


def _weak_sources(key: str):
    """Last-resort imagery (wiki article screenshots / file search)."""
    for resolver, label in (
        (_wiki_version_page_image, "wiki-version-page"),
        (_wiki_search_file_image, "wiki-search"),
    ):
        try:
            url = resolver(key)
        except Exception:
            url = None
        if url:
            yield url, label


def _candidate_sources(key: str):
    """Yields (url, source_label) candidates for a key, strong sources first."""
    yield from _strong_sources(key)
    yield from _weak_sources(key)


WEAK_SOURCE_LABELS = {"wiki-version-page", "wiki-search"}


# ---------------------------------------------------------------------------
# Cache index
# ---------------------------------------------------------------------------

def _index_path() -> Path:
    return get_banner_cache_dir() / "index.json"


def _load_index() -> dict:
    global _INDEX
    with _INDEX_LOCK:
        if _INDEX is not None:
            return _INDEX
        document: dict = {"schema": 2, "entries": {}, "failures": {}, "strong_misses": {}}
        try:
            path = _index_path()
            if path.is_file():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    if isinstance(loaded.get("entries"), dict):
                        document["entries"] = loaded["entries"]
                    if isinstance(loaded.get("failures"), dict):
                        document["failures"] = loaded["failures"]
                    if isinstance(loaded.get("strong_misses"), dict):
                        document["strong_misses"] = loaded["strong_misses"]
        except (OSError, ValueError):
            pass
        _INDEX = document
        return document


def _save_index() -> None:
    with _INDEX_LOCK:
        document = _INDEX
        if document is None:
            return
        path = _index_path()
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _index_entry(key: str) -> dict | None:
    entry = _load_index()["entries"].get(key)
    return entry if isinstance(entry, dict) else None


def _record_success(key: str, file_name: str, source: str, url: str | None, dimensions, tier: str = "strong") -> None:
    with _INDEX_LOCK:
        document = _load_index()
        document["entries"][key] = {
            "file": file_name,
            "source": source,
            "tier": tier,
            "url": url,
            "width": dimensions[0] if dimensions else None,
            "height": dimensions[1] if dimensions else None,
            "fetched_at": time.time(),
        }
        document["failures"].pop(key, None)
    _save_index()


def _record_failure(key: str) -> None:
    with _INDEX_LOCK:
        document = _load_index()
        document["failures"][key] = time.time()
    _save_index()


def _is_recent_failure(key: str) -> bool:
    failure = _load_index()["failures"].get(key)
    if not isinstance(failure, (int, float)):
        return False
    if time.time() - failure > NEGATIVE_TTL_SECONDS:
        return False
    return True


def _record_strong_miss(key: str) -> None:
    with _INDEX_LOCK:
        document = _load_index()
        document["strong_misses"][key] = time.time()
    _save_index()


def _is_recent_strong_miss(key: str) -> bool:
    miss = _load_index()["strong_misses"].get(key)
    if not isinstance(miss, (int, float)):
        return False
    if time.time() - miss > STRONG_MISS_TTL_SECONDS:
        return False
    return True


def find_cached_banner(key: str) -> Path | None:
    """Returns a validated cached banner file for a key, if present."""
    cached = _cached_banner(key)
    return cached[0] if cached else None


def find_cached_banner_tiered(key: str) -> tuple[Path, str] | None:
    """Returns (path, tier) for a cached banner, where tier is 'strong' or 'weak'."""
    return _cached_banner(key)


def _cached_banner(key: str) -> tuple[Path, str] | None:
    directory = get_banner_cache_dir()
    entry = _index_entry(key)
    if entry and isinstance(entry.get("file"), str):
        candidate = directory / entry["file"]
        if is_valid_banner_file(candidate):
            source = entry.get("source")
            tier = entry.get("tier")
            if tier not in ("strong", "weak"):
                tier = "weak" if source in WEAK_SOURCE_LABELS else "strong"
            return candidate, tier

    safe = _safe_key(key)
    for extension in _IMAGE_EXTENSIONS:
        candidate = directory / f"{safe}.{extension}"
        if is_valid_banner_file(candidate):
            # Loose file with no index metadata: assume it is real artwork.
            return candidate, "strong"
    return None


def _best_cached(keys, want_tier: str) -> Path | None:
    """First cached banner (most specific key first) matching the wanted tier."""
    for key in keys:
        cached = _cached_banner(key)
        if not cached:
            continue
        path, tier = cached
        if want_tier == "strong" and tier != "strong":
            continue
        return path
    return None


def is_valid_banner_key(key: str) -> bool:
    return find_cached_banner(key) is not None


# ---------------------------------------------------------------------------
# Custom instance banners
# ---------------------------------------------------------------------------

def get_custom_instance_banner(target: dict | None, minecraft_directory: str | None = None) -> str | None:
    if not target or not target.get("is_instance"):
        return None

    custom_banner = target.get("custom_banner")
    if custom_banner and Path(custom_banner).is_file():
        return custom_banner

    directories: list[Path] = []
    game_directory = target.get("game_directory")
    instance_name = target.get("instance_name")
    if game_directory and Path(game_directory).is_dir():
        directories.append(Path(game_directory))
    if minecraft_directory and instance_name:
        directories.append(Path(minecraft_directory) / "instances" / instance_name)

    for base in directories:
        for extension in _IMAGE_EXTENSIONS:
            banner_path = base / f"banner.{extension}"
            if banner_path.is_file() and banner_path.stat().st_size > 0:
                return str(banner_path)
    return None


def get_local_banner_for_target(target: dict | None, minecraft_directory: str | None = None) -> str | None:
    """Returns an already-available local banner path for a target, if any.

    Genuine banners/artwork are preferred over last-resort wiki screenshots,
    even when the screenshot belongs to a more specific version key.
    """
    custom = get_custom_instance_banner(target, minecraft_directory)
    if custom:
        return custom

    keys = resolve_banner_keys(target)
    strong = _best_cached(keys, "strong")
    if strong:
        return str(strong)
    weak = _best_cached(keys, "weak")
    return str(weak) if weak else None


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------

def query_live_wiki_banner(key: str) -> str | None:
    """Returns the best remote banner URL for a key using the live source chain."""
    for url, _source in _candidate_sources(key):
        return url
    return None


def _store_banner(key: str, data: bytes, extension: str, source: str, url: str | None, tier: str) -> Path | None:
    with _STORE_LOCK:
        directory = get_banner_cache_dir()
        safe = _safe_key(key)
        destination = directory / f"{safe}.{extension}"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            temporary.write_bytes(data)
            temporary.replace(destination)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return None

        # Drop stale copies of the same key stored with a different extension.
        for other in _IMAGE_EXTENSIONS:
            if other == extension:
                continue
            stale = directory / f"{safe}.{other}"
            if stale.is_file():
                try:
                    stale.unlink()
                except OSError:
                    pass

        _record_success(key, destination.name, source, url, _probe_dimensions(data), tier)
        return destination


def _try_sources(key: str, sources_fn, tier: str, accept_weak_cache: bool) -> Path | None:
    """Tries one tier's sources for a key; returns a cached or freshly stored banner."""
    cached = _cached_banner(key)
    if cached:
        path, cached_tier = cached
        if cached_tier == "strong" or accept_weak_cache:
            return path

    if tier == "strong":
        if _is_recent_strong_miss(key):
            return None
    elif _is_recent_failure(key):
        return None

    for url, source in sources_fn(key):
        data = _http_get(url)
        if not data or not _data_is_usable(data):
            continue
        extension = _sniff_extension(data[:32]) or "jpg"
        stored = _store_banner(key, data, extension, source, url, tier)
        if stored:
            return stored

    if tier == "strong":
        _record_strong_miss(key)
    return None


def _resolve_for_keys(keys: list[str]) -> str | None:
    """Resolves banners across version keys in tiers.

    Tier A: genuine banners/artwork (curated, wiki banner files, Mojang art),
    most specific key first.
    Tier B: last-resort wiki article screenshots, most specific key first.
    Tier C: the curated default banner.
    """
    with _STORE_LOCK:
        for key in keys:
            path = _try_sources(key, _strong_sources, "strong", accept_weak_cache=False)
            if path:
                return str(path)

        for key in keys:
            path = _try_sources(key, _weak_sources, "weak", accept_weak_cache=True)
            if path:
                return str(path)

        path = _try_sources(DEFAULT_BANNER_KEY, _strong_sources, "strong", accept_weak_cache=False)
        if path:
            return str(path)

        for key in keys:
            if not _cached_banner(key):
                _record_failure(key)
        return None


def _key_lock(key: str) -> asyncio.Lock:
    lock = _KEY_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _KEY_LOCKS[key] = lock
    return lock


async def ensure_banner_downloaded(key: str) -> str | None:
    """Ensures a banner for a single key is cached locally, returning its path."""
    key = (key or DEFAULT_BANNER_KEY).strip() or DEFAULT_BANNER_KEY

    cached = find_cached_banner(key)
    if cached:
        return str(cached)

    async with _key_lock(key):
        cached = find_cached_banner(key)
        if cached:
            return str(cached)
        return await asyncio.to_thread(_resolve_for_keys, [key])


async def ensure_banner_for_target(target: dict | None, minecraft_directory: str | None = None) -> str | None:
    """Resolves the best banner for a target through the tiered multi-source chain."""
    custom = get_custom_instance_banner(target, minecraft_directory)
    if custom:
        return custom

    keys = resolve_banner_keys(target)
    resolved = await asyncio.to_thread(_resolve_for_keys, keys)
    if resolved:
        return resolved

    # Offline / all sources unavailable: use whatever screenshot was cached.
    weak = _best_cached(keys, "weak")
    if weak:
        return str(weak)
    default = _best_cached([DEFAULT_BANNER_KEY], "strong")
    return str(default) if default else None
