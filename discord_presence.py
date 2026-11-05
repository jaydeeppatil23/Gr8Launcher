"""Optional Discord Rich Presence, isolated from the launcher's UI event loop."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
import requests
try:
    from pypresence import AioPresence
    from pypresence.exceptions import ServerError
except ImportError:
    AioPresence = None

    class ServerError(Exception):
        pass


# Public Discord application ID (not a token). Set this for release builds.
# The environment override is useful when testing another Discord application.
DISCORD_APPLICATION_ID = "1559999125200568320"
DISCORD_LOGO = (
    "https://raw.githubusercontent.com/jaydeeppatil23/Gr8Launcher/"
    "main/.assets/icons/gr8_icon.png"
)
UPDATE_INTERVAL = 15.0
RECONNECT_INTERVAL = 15.0
HEARTBEAT_INTERVAL = 60.0
IPC_TIMEOUT = 3.0
POLL_INTERVAL = 0.5
PORTRAIT_RETRY_INTERVAL = 300.0

PAGE_DETAILS = {
    "home": "On the home screen",
    "library": "Browsing the library",
    "skins": "Browsing skins",
    "mods": "Browsing mods",
    "settings": "Adjusting settings",
    "accounts": "Managing accounts",
    "login": "Signing in",
    "new_profile": "Creating a profile",
}


def _discord_text(value: str) -> str:
    """Discord activity text is limited to 128 UTF-8 bytes."""
    return value.encode("utf-8")[:128].decode("utf-8", errors="ignore")


def _valid_portrait_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https" and parsed.netloc == "files.catbox.moe"
            and re.fullmatch(r"/[A-Za-z0-9]+\.png", parsed.path) is not None
            and not parsed.query and not parsed.fragment
        )
    except ValueError:
        return False


def upload_skin_face(face_png: bytes) -> str | None:
    """Host only a face thumbnail; full skin files and account data stay local."""
    try:
        with Image.open(io.BytesIO(face_png)) as source:
            if source.format != "PNG" or source.size != (8, 8):
                return None
            face = source.convert("RGBA").resize((64, 64), Image.Resampling.NEAREST)
        buffer = io.BytesIO()
        face.save(buffer, format="PNG", optimize=True)
        response = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": ("gr8-face.png", buffer.getvalue(), "image/png")},
            timeout=(3.0, 6.0),
        )
        response.raise_for_status()
        url = response.text.strip()
        return url if _valid_portrait_url(url) else None
    except (requests.RequestException, OSError, ValueError):
        return None


class SkinPortraitCache:
    def __init__(self, path: Path | None = None, *, uploader=None):
        self.path = path
        self._uploader = uploader or upload_skin_face
        self._lock = threading.Lock()
        self._urls = {}
        self._retry_after = {}
        if path is not None:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    self._urls = {
                        key: value for key, value in saved.items()
                        if isinstance(key, str) and re.fullmatch(r"[a-f0-9]{64}", key)
                        and isinstance(value, str) and _valid_portrait_url(value)
                    }
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass

    def resolve(self, face_png: bytes) -> str | None:
        key = hashlib.sha256(face_png).hexdigest()
        with self._lock:
            if key in self._urls:
                return self._urls[key]
            now = time.monotonic()
            if now < self._retry_after.get(key, 0):
                return None
            self._retry_after[key] = now + PORTRAIT_RETRY_INTERVAL
        url = self._uploader(face_png)
        if not isinstance(url, str) or not _valid_portrait_url(url):
            return None
        with self._lock:
            self._urls[key] = url
            self._retry_after.pop(key, None)
            if self.path is not None:
                temporary = self.path.with_suffix(".tmp")
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    temporary.write_text(json.dumps(self._urls, indent=2), encoding="utf-8")
                    temporary.replace(self.path)
                except OSError:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
        return url


def _player_portrait(player_name: str, face_png: bytes | None) -> dict | None:
    if not player_name or not face_png:
        return None
    try:
        with Image.open(io.BytesIO(face_png)) as face:
            if face.format != "PNG" or face.size != (8, 8):
                return None
            face.load()
    except (OSError, ValueError):
        return None
    return {
        "_face_png": face_png,
        "small_text": _discord_text(f"{player_name}'s skin"),
    }


class DiscordPresence:
    def __init__(
        self, *, enabled: bool = True, application_id: str | None = None,
        portrait_cache: SkinPortraitCache | None = None,
    ):
        configured_id = (
            os.environ.get("GR8_DISCORD_APPLICATION_ID", DISCORD_APPLICATION_ID)
            if application_id is None else application_id
        )
        self.application_id = str(configured_id).strip()
        self._enabled = enabled
        self._page = "home"
        self._launcher_started = int(time.time())
        self._game = None
        self._player = None
        self._portrait_cache = portrait_cache or SkinPortraitCache()
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread = None
        self._loop = None
        self._task = None

    def start(self) -> None:
        if (
            AioPresence is None
            or not self.application_id.isascii()
            or not self.application_id.isdecimal()
            or self._stopped.is_set()
        ):
            return
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._worker, name="discord-presence", daemon=True,
            )
            self._thread.start()

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def set_page(self, page_name: str) -> None:
        with self._lock:
            self._page = page_name

    def set_player(self, player_name: str, face_png: bytes | None) -> None:
        portrait = _player_portrait(player_name, face_png)
        with self._lock:
            self._player = portrait

    def prepare_game(self, profile: dict, *, installing: bool = False) -> None:
        self._set_game(profile, "installing" if installing else "launching")

    def game_started(
        self, profile: dict, *, player_name: str | None = None, face_png: bytes | None = None,
    ) -> None:
        self._set_game(profile, "playing", player_name=player_name, face_png=face_png)

    def _set_game(
        self, profile: dict, phase: str, *, player_name: str | None = None,
        face_png: bytes | None = None,
    ) -> None:
        version = str(profile.get("minecraft_version") or profile.get("version_id") or "Unknown version")
        loader = str(profile.get("loader") or "Vanilla")
        loader_version = str(profile.get("loader_version") or "").strip()
        if loader.casefold() != "vanilla" and loader_version:
            loader = f"{loader} {loader_version}"
        with self._lock:
            # Keep preparation time stable when the latest release is resolved.
            started = (
                self._game["start"]
                if self._game and (self._game["phase"] == "playing") == (phase == "playing")
                else int(time.time())
            )
            portrait = (
                _player_portrait(player_name, face_png) if player_name is not None
                else self._game["player"] if self._game else self._player
            )
            self._game = {
                "phase": phase, "version": version, "loader": loader,
                "start": started, "player": portrait,
            }

    def game_finished(self) -> None:
        with self._lock:
            self._game = None

    def _activity(self) -> dict | None:
        with self._lock:
            if not self._enabled:
                return None
            activity = {
                "details": PAGE_DETAILS.get(self._page, "Browsing the launcher"),
                "state": "In the launcher",
                "start": self._launcher_started,
                "large_image": DISCORD_LOGO,
                "large_text": "Gr8 Launcher",
            }
            if self._game:
                activity.update(
                    details={
                        "installing": "Installing Minecraft",
                        "launching": "Launching Minecraft",
                        "playing": "Playing Minecraft",
                    }[self._game["phase"]],
                    state=_discord_text(f"{self._game['version']} | {self._game['loader']}"),
                    start=self._game["start"],
                )
            portrait = self._game["player"] if self._game else self._player
            if portrait:
                activity.update(portrait)
            return activity

    def stop(self) -> None:
        """Cancel pending IPC and clear presence before closing the connection."""
        with self._lock:
            was_stopped = self._stopped.is_set()
            self._stopped.set()
            loop, task, thread = self._loop, self._task, self._thread
        if not was_stopped and loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _worker(self) -> None:
        try:
            asyncio.run(self._run())
        finally:
            with self._lock:
                self._loop = self._task = None

    @staticmethod
    async def _disconnect(client, *, clear: bool = False) -> None:
        if client is None:
            return
        try:
            if clear:
                try:
                    await asyncio.wait_for(client.clear(), timeout=1.0)
                except Exception:
                    pass
        finally:
            # AioPresence.close() closes its event loop. The worker owns that
            # loop, so close just the IPC transport, including on cancellation.
            writer = getattr(client, "sock_writer", None)
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
        await asyncio.sleep(0)

    async def _update_activity(self, client, activity: dict) -> bool:
        payload = dict(activity)
        face_png = payload.pop("_face_png", None)
        if face_png:
            url = await asyncio.to_thread(self._portrait_cache.resolve, face_png)
            if self._stopped.is_set() or self._activity() != activity:
                return False
            if url:
                payload["small_image"] = url
            else:
                payload.pop("small_text", None)
        try:
            await asyncio.wait_for(client.update(**payload), timeout=IPC_TIMEOUT)
        except ServerError:
            if not payload.get("small_image"):
                raise
            # A failed optional portrait must not suppress page/game presence.
            fallback = {
                key: value for key, value in payload.items()
                if key not in ("small_image", "small_text")
            }
            await asyncio.wait_for(client.update(**fallback), timeout=IPC_TIMEOUT)
        return True

    async def _run(self) -> None:
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
        client = None
        last_activity = None
        next_connect = next_update = last_update = 0.0
        try:
            while not self._stopped.is_set():
                activity = self._activity()
                now = time.monotonic()
                if activity is None:
                    await self._disconnect(client, clear=True)
                    client = None
                    last_activity = None
                    next_connect = next_update = 0.0
                else:
                    try:
                        if client is None and now >= next_connect:
                            client = AioPresence(
                                self.application_id, loop=self._loop,
                                connection_timeout=IPC_TIMEOUT, response_timeout=IPC_TIMEOUT,
                            )
                            await asyncio.wait_for(client.connect(), timeout=IPC_TIMEOUT)
                            last_activity = None
                            next_update = 0.0
                        if client is not None and now >= next_update and (
                            activity != last_activity or now - last_update >= HEARTBEAT_INTERVAL
                        ):
                            # Read again after connecting to pick up navigation,
                            # launch changes, or a toggle during the handshake.
                            activity = self._activity()
                            if activity is not None and not self._stopped.is_set():
                                if await self._update_activity(client, activity):
                                    last_activity = activity
                                    last_update = time.monotonic()
                                    next_update = last_update + UPDATE_INTERVAL
                    except Exception:
                        # Discord may be absent, closed, or restarting. Retry
                        # quietly without interfering with Minecraft or the UI.
                        await self._disconnect(client)
                        client = None
                        last_activity = None
                        next_connect = time.monotonic() + RECONNECT_INTERVAL
                await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            pass
        finally:
            await self._disconnect(client, clear=True)
