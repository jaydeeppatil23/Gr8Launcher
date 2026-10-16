"""Gr8 Launcher - Skin Engine
Handles Minecraft skin rendering (2D front/back composite with pixel-art scaling),
validation of 64x64 and legacy 64x32 skin formats, Ely.by skin synchronization,
local skin preset library, and automatic management of authlib-injector.jar.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import io
import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

from PIL import Image
import requests

from storage import get_data_dir

ASSETS_DIR = Path(__file__).resolve().parent / ".assets"
DEFAULT_STEVE_PATH = ASSETS_DIR / "skins" / "default_steve.png"
DEFAULT_ALEX_PATH = ASSETS_DIR / "skins" / "default_alex.png"

AUTHLIB_INJECTOR_URL = (
    "https://github.com/yushijinhun/authlib-injector/releases/download/"
    "v1.2.8/authlib-injector-1.2.8.jar"
)
AUTHLIB_INJECTOR_EXPECTED_MIN_BYTES = 250_000
ELY_BY_AUTH_SERVER = "https://authserver.ely.by/api/authlib-injector"

DEFAULT_ELY_METADATA_JSON = json.dumps({
    "meta": {
        "serverName": "Ely.by",
        "implementationName": "Account Ely.by adapter for the authlib-injector library",
        "implementationVersion": "1.0.0",
        "feature.no_mojang_namespace": True,
        "feature.enable_profile_key": True,
        "links": {
            "homepage": "https://ely.by",
            "register": "https://account.ely.by/register",
        },
    },
    "skinDomains": [
        "ely.by",
        ".ely.by",
        "127.0.0.1",
        "localhost",
    ],
    "signaturePublickey": (
        "-----BEGIN PUBLIC KEY-----\n"
        "MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAxgFJRb0e9fRyVG5+JlCg\n"
        "h0hccRIcgO5yxEVkMJajAI12Ev/Pc7lpTt6OtKTEcUNfjYgBnEhIKbdLD0Z+B5Bx\n"
        "Sg9DQmozgzZcesScpASQb4Kt6P8itowdbgbUm4v+6x1QUKJjjmhHq93m9OIEbxQL\n"
        "Cq+SrEMZpDrXRgd9DhNPjZv/95ximP8otvh7+bmEl8jwINgfJx0PAeJFYlceQcsh\n"
        "iYh+LHtaIwzbTTqkDibDm7QiEc+/qGab3mABtVTpqw/refwFoR0M8+xkWF+1/D8k\n"
        "H0WFa+rBhdjLyLG+2hdOpKXoH/fMH0tQMPHU78J17JVKWwIWCwEWXp8HiWSbIt3a\n"
        "cmBYtyW3tqarFFMMECx2wmJP6FVOvYVThZxq9qc9/f3yeTGz3g7zU1YljHSVRP16\n"
        "iEbEnHQBKxmrj2cdZgosJej4YppV7f3iZ8o8PF6UY51LSqvaCteXuWeYSJJESGAs\n"
        "UoV7ihJfWL8DymHamywB2Cahx7EiDGS3/iBcQUmpk4TTg2FrZPuKGItn1QfIRieO\n"
        "knnj9CPKiWdfOtJBr3i1FXLEfExgcJhQ00Y6B08QVvgiCzUF3t+VAG3Ef2YINYyG\n"
        "AXcW0TIgMalwwgGzdhQRhItODXptWigy0DNTUAgKQT9PS8N09yPBGxIq64T9A3/z\n"
        "FqC/k2bMLWUSVtIlilIItn0CAwEAAQ==\n"
        "-----END PUBLIC KEY-----\n"
    ),
})


def is_skin_server_reachable(timeout: float = 0.5) -> bool:
    """Checks whether the remote Ely.by skin server is currently reachable."""
    try:
        with socket.create_connection(("authserver.ely.by", 443), timeout=timeout):
            return True
    except OSError:
        return False


def get_cached_metadata() -> str:
    """Returns the cached Ely.by metadata JSON, saving default metadata if missing."""
    meta_path = get_skins_dir() / "ely_metadata.json"
    if meta_path.is_file():
        try:
            content = meta_path.read_text(encoding="utf-8")
            if content.strip():
                return content
        except OSError:
            pass
    try:
        meta_path.write_text(DEFAULT_ELY_METADATA_JSON, encoding="utf-8")
    except OSError:
        pass
    return DEFAULT_ELY_METADATA_JSON


def get_skins_dir() -> Path:
    """Returns the persistent skins directory inside .minecraft/gr8launcher/skins."""
    directory = get_data_dir() / "skins"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def get_tools_dir() -> Path:
    """Returns the persistent tools directory inside .minecraft/gr8launcher/tools."""
    directory = get_data_dir() / "tools"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def get_authlib_jar_path() -> Path:
    """Returns the target path for authlib-injector.jar."""
    return get_tools_dir() / "authlib-injector.jar"


def ensure_authlib_injector(progress_callback=None) -> Path | None:
    """Ensures authlib-injector.jar exists locally and is valid.
    Downloads it from official GitHub releases if missing or corrupt.
    """
    jar_path = get_authlib_jar_path()
    if jar_path.is_file() and jar_path.stat().st_size >= AUTHLIB_INJECTOR_EXPECTED_MIN_BYTES:
        return jar_path

    temporary_file = jar_path.with_suffix(".tmp")
    try:
        get_tools_dir().mkdir(parents=True, exist_ok=True)
        if progress_callback:
            progress_callback("Downloading authlib-injector for skins...")
        response = requests.get(AUTHLIB_INJECTOR_URL, stream=True, timeout=15)
        response.raise_for_status()
        with open(temporary_file, "wb") as file_out:
            for chunk in response.iter_content(chunk_size=16384):
                if chunk:
                    file_out.write(chunk)

        if temporary_file.stat().st_size >= AUTHLIB_INJECTOR_EXPECTED_MIN_BYTES:
            temporary_file.replace(jar_path)
            return jar_path
        temporary_file.unlink(missing_ok=True)
    except Exception as error:
        print(f"Could not download authlib-injector.jar: {error}")
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass
    return jar_path if jar_path.is_file() else None


def get_skin_bridge_jar_path() -> Path:
    """Returns the target path for gr8-skin-bridge.jar."""
    return get_tools_dir() / "gr8-skin-bridge.jar"


def ensure_skin_bridge() -> Path | None:
    """Ensures gr8-skin-bridge.jar exists locally and is valid."""
    target_jar = get_skin_bridge_jar_path()
    if target_jar.is_file() and target_jar.stat().st_size > 10_000:
        return target_jar

    local_proj_jar = Path(__file__).resolve().parent / "tools" / "gr8-skin-bridge" / "gr8-skin-bridge.jar"
    if local_proj_jar.is_file():
        try:
            target_jar.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_proj_jar, target_jar)
            return target_jar
        except OSError:
            pass

    try:
        build_script = Path(__file__).resolve().parent / "tools" / "gr8-skin-bridge" / "build_bridge.py"
        if build_script.is_file():
            import subprocess
            subprocess.run([sys.executable, str(build_script)], capture_output=True, timeout=15)
            if target_jar.is_file():
                return target_jar
    except Exception as error:
        print(f"Notice: could not auto-build gr8-skin-bridge: {error}")

    return target_jar if target_jar.is_file() else None


def validate_skin_file(file_path: Path | str) -> tuple[bool, str, tuple[int, int] | None]:
    """Validates that a file is a valid Minecraft skin (PNG format, 64x64 or 64x32)."""
    target = Path(file_path)
    if not target.is_file():
        return False, "File does not exist.", None
    if target.stat().st_size > 2 * 1024 * 1024:
        return False, "Skin file is too large (must be under 2MB).", None

    try:
        with Image.open(target) as img:
            format_name = (img.format or "").upper()
            if format_name != "PNG":
                return False, f"File format is {format_name}; Minecraft skins must be PNG.", None
            width, height = img.size
            if (width, height) not in ((64, 64), (64, 32)):
                return (
                    False,
                    f"Invalid dimensions {width}x{height}. Minecraft skins must be 64x64 or 64x32.",
                    (width, height),
                )
            return True, "Valid Minecraft skin.", (width, height)
    except Exception as error:
        return False, f"Could not read image: {error}", None


def ensure_64x64(skin: Image.Image) -> Image.Image:
    """Converts a legacy 64x32 skin to modern 64x64 format by mirroring right limbs."""
    if skin.size == (64, 64):
        return skin.convert("RGBA")

    skin_rgba = skin.convert("RGBA")
    converted = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    converted.paste(skin_rgba.crop((0, 0, 64, 32)), (0, 0))

    # Mirror right leg (0, 16, 16, 32) -> left leg (16, 48, 32, 64)
    right_leg = skin_rgba.crop((0, 16, 16, 32))
    mirrored_leg = right_leg.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    converted.paste(mirrored_leg, (16, 48))

    # Mirror right arm (40, 16, 56, 32) -> left arm (32, 48, 48, 64)
    right_arm = skin_rgba.crop((40, 16, 56, 32))
    mirrored_arm = right_arm.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    converted.paste(mirrored_arm, (32, 48))

    return converted


def is_slim_skin(skin: Image.Image) -> bool:
    """Detects whether a 64x64 skin is likely a Slim (Alex / 3-pixel arm) model.
    Checks if the right arm 4th column (pixel x=55, y=20..31) and sleeve column (x=55, y=36..47)
    have transparent pixels.
    """
    skin_64 = ensure_64x64(skin)
    try:
        alpha_sum = 0
        for y in range(20, 32):
            _, _, _, a = skin_64.getpixel((55, y))
            alpha_sum += a
        for y in range(36, 48):
            _, _, _, a = skin_64.getpixel((55, y))
            alpha_sum += a
        return alpha_sum == 0
    except Exception:
        return False


def get_default_skin(model: str = "classic") -> Image.Image:
    """Returns the default Steve or Alex skin."""
    target_path = DEFAULT_ALEX_PATH if model == "slim" else DEFAULT_STEVE_PATH
    if target_path.is_file():
        try:
            return Image.open(target_path).convert("RGBA")
        except Exception:
            pass
    # Fallback blank canvas
    return Image.new("RGBA", (64, 64), (100, 100, 100, 255))


def get_default_skin_bytes(model: str = "classic") -> bytes:
    target_path = DEFAULT_ALEX_PATH if model == "slim" else DEFAULT_STEVE_PATH
    if target_path.is_file():
        try:
            return target_path.read_bytes()
        except OSError:
            pass
    buf = io.BytesIO()
    get_default_skin(model).save(buf, format="PNG")
    return buf.getvalue()


def render_character(
    skin: Image.Image,
    view: str = "front",
    model: str = "classic",
    scale: int = 8,
) -> Image.Image:
    """Renders a pixel-perfect 2D character preview (front or back) scaled by `scale`."""
    skin_64 = ensure_64x64(skin)
    is_slim = model.lower() == "slim"

    canvas = Image.new("RGBA", (16, 32), (0, 0, 0, 0))

    if view.lower() == "front":
        # 1. Head & Hat (8x8) at (4, 0)
        canvas.alpha_composite(skin_64.crop((8, 8, 16, 16)), (4, 0))
        canvas.alpha_composite(skin_64.crop((40, 8, 48, 16)), (4, 0))

        # 2. Torso & Jacket (8x12) at (4, 8)
        canvas.alpha_composite(skin_64.crop((20, 20, 28, 32)), (4, 8))
        canvas.alpha_composite(skin_64.crop((20, 36, 28, 48)), (4, 8))

        # 3. Right Arm & Sleeve (viewer's left)
        rx = 1 if is_slim else 0
        r_box = (44, 20, 47, 32) if is_slim else (44, 20, 48, 32)
        r_over = (44, 36, 47, 48) if is_slim else (44, 36, 48, 48)
        canvas.alpha_composite(skin_64.crop(r_box), (rx, 8))
        canvas.alpha_composite(skin_64.crop(r_over), (rx, 8))

        # 4. Left Arm & Sleeve (viewer's right)
        lx = 12
        l_box = (36, 52, 39, 64) if is_slim else (36, 52, 40, 64)
        l_over = (52, 52, 55, 64) if is_slim else (52, 52, 56, 64)
        canvas.alpha_composite(skin_64.crop(l_box), (lx, 8))
        canvas.alpha_composite(skin_64.crop(l_over), (lx, 8))

        # 5. Right Leg & Pants (viewer's left) at (4, 20)
        canvas.alpha_composite(skin_64.crop((4, 20, 8, 32)), (4, 20))
        canvas.alpha_composite(skin_64.crop((4, 36, 8, 48)), (4, 20))

        # 6. Left Leg & Pants (viewer's right) at (8, 20)
        canvas.alpha_composite(skin_64.crop((20, 52, 24, 64)), (8, 20))
        canvas.alpha_composite(skin_64.crop((4, 52, 8, 64)), (8, 20))

    else:
        # Back view (character rotated 180 degrees)
        # 1. Head Back (8x8) at (4, 0)
        canvas.alpha_composite(skin_64.crop((24, 8, 32, 16)), (4, 0))
        canvas.alpha_composite(skin_64.crop((56, 8, 64, 16)), (4, 0))

        # 2. Torso Back (8x12) at (4, 8)
        canvas.alpha_composite(skin_64.crop((32, 20, 40, 32)), (4, 8))
        canvas.alpha_composite(skin_64.crop((32, 36, 40, 48)), (4, 8))

        # 3. Left Arm Back (viewer's left)
        lx = 1 if is_slim else 0
        l_box = (43, 52, 46, 64) if is_slim else (44, 52, 48, 64)
        l_over = (59, 52, 62, 64) if is_slim else (60, 52, 64, 64)
        canvas.alpha_composite(skin_64.crop(l_box), (lx, 8))
        canvas.alpha_composite(skin_64.crop(l_over), (lx, 8))

        # 4. Right Arm Back (viewer's right)
        rx = 12
        r_box = (51, 20, 54, 32) if is_slim else (52, 20, 56, 32)
        r_over = (51, 36, 54, 48) if is_slim else (52, 36, 56, 48)
        canvas.alpha_composite(skin_64.crop(r_box), (rx, 8))
        canvas.alpha_composite(skin_64.crop(r_over), (rx, 8))

        # 5. Left Leg Back (viewer's left) at (4, 20)
        canvas.alpha_composite(skin_64.crop((28, 52, 32, 64)), (4, 20))
        canvas.alpha_composite(skin_64.crop((12, 52, 16, 64)), (4, 20))

        # 6. Right Leg Back (viewer's right) at (8, 20)
        canvas.alpha_composite(skin_64.crop((12, 20, 16, 32)), (8, 20))
        canvas.alpha_composite(skin_64.crop((12, 36, 16, 48)), (8, 20))

    target_size = (16 * scale, 32 * scale)
    return canvas.resize(target_size, Image.Resampling.NEAREST)


def render_character_bytes(
    skin_input: Path | str | bytes | Image.Image,
    view: str = "front",
    model: str = "classic",
    scale: int = 8,
) -> bytes:
    """Renders a character preview and returns the PNG encoded bytes."""
    img = None
    if isinstance(skin_input, Image.Image):
        img = skin_input
    elif isinstance(skin_input, (str, Path)):
        path = Path(skin_input)
        if path.is_file():
            img = Image.open(path)
    elif isinstance(skin_input, (bytes, bytearray)):
        img = Image.open(io.BytesIO(skin_input))

    if img is None:
        img = get_default_skin(model)

    rendered = render_character(img, view=view, model=model, scale=scale)
    buf = io.BytesIO()
    rendered.save(buf, format="PNG")
    return buf.getvalue()


def render_head_avatar(skin: Image.Image, scale: int = 8) -> Image.Image:
    """Renders the 8x8 player head with outer hat overlay scaled by `scale`."""
    skin_64 = ensure_64x64(skin)
    head = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    head.alpha_composite(skin_64.crop((8, 8, 16, 16)), (0, 0))
    head.alpha_composite(skin_64.crop((40, 8, 48, 16)), (0, 0))
    return head.resize((8 * scale, 8 * scale), Image.Resampling.NEAREST)


def render_head_avatar_bytes(
    skin_input: Path | str | bytes | Image.Image,
    scale: int = 8,
) -> bytes:
    """Renders the player head avatar and returns the PNG encoded bytes."""
    img = None
    if isinstance(skin_input, Image.Image):
        img = skin_input
    elif isinstance(skin_input, (str, Path)):
        path = Path(skin_input)
        if path.is_file():
            img = Image.open(path)
    elif isinstance(skin_input, (bytes, bytearray)):
        img = Image.open(io.BytesIO(skin_input))

    if img is None:
        img = get_default_skin("classic")

    avatar = render_head_avatar(img, scale=scale)
    buf = io.BytesIO()
    avatar.save(buf, format="PNG")
    return buf.getvalue()


def fetch_ely_skin(
    username: str,
    force_refresh: bool = False,
    timeout: float = 6.0,
) -> tuple[bytes | None, str]:
    """Fetches a player's skin PNG bytes and model type from Ely.by.
    Returns (png_bytes, model_type) or (None, 'classic') on failure.
    Caches the downloaded skin locally in .minecraft/gr8launcher/skins/ely_<username>.png.
    """
    clean_name = username.strip()
    if not clean_name:
        return None, "classic"

    cache_file = get_skins_dir() / f"ely_{clean_name.lower()}.png"
    meta_file = get_skins_dir() / f"ely_{clean_name.lower()}.json"

    # Use cache if fresh (< 1 hour) and not forced
    if not force_refresh and cache_file.is_file() and meta_file.is_file():
        try:
            cache_age = time.time() - cache_file.stat().st_mtime
            if cache_age < 3600:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                model = meta.get("model", "classic")
                return cache_file.read_bytes(), model
        except Exception:
            pass

    try:
        url_profile = f"{ELY_BY_AUTH_SERVER}/api/profiles/minecraft"
        resp = requests.post(url_profile, json=[clean_name], timeout=timeout)
        if resp.status_code == 200 and resp.json():
            uuid_val = resp.json()[0].get("id")
            if uuid_val:
                url_session = (
                    f"{ELY_BY_AUTH_SERVER}/sessionserver/session/minecraft/profile/"
                    f"{uuid_val}?unsigned=false"
                )
                session_resp = requests.get(url_session, timeout=timeout)
                if session_resp.status_code == 200:
                    data = session_resp.json()
                    for prop in data.get("properties", []):
                        if prop.get("name") == "textures":
                            val = json.loads(
                                base64.b64decode(prop["value"]).decode("utf-8")
                            )
                            skin_info = val.get("textures", {}).get("SKIN")
                            if skin_info and skin_info.get("url"):
                                skin_url = skin_info["url"]
                                model = (
                                    skin_info.get("metadata", {}).get("model", "classic")
                                )
                                skin_resp = requests.get(skin_url, timeout=timeout)
                                if skin_resp.status_code == 200:
                                    skin_bytes = skin_resp.content
                                    cache_file.write_bytes(skin_bytes)
                                    formatted_uuid = str(uuid.UUID(hex=uuid_val))
                                    meta_file.write_text(
                                        json.dumps({
                                            "model": model,
                                            "uuid": formatted_uuid,
                                            "raw_uuid": uuid_val,
                                            "fetched_at": time.time(),
                                        }),
                                        encoding="utf-8",
                                    )
                                    return skin_bytes, model
    except Exception as error:
        print(f"Notice: Ely.by skin lookup for '{clean_name}': {error}")

    # Fall back to existing cached file if network failed
    if cache_file.is_file():
        try:
            model = "classic"
            if meta_file.is_file():
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                model = meta.get("model", "classic")
            return cache_file.read_bytes(), model
        except Exception:
            pass

    return None, "classic"


def get_ely_uuid(username: str, timeout: float = 4.0) -> str | None:
    """Returns the formatted Ely.by UUID string for a player name if registered on Ely.by.
    Checks local cache first. Returns None if not found or query fails.
    """
    clean_name = username.strip()
    if not clean_name:
        return None

    meta_file = get_skins_dir() / f"ely_{clean_name.lower()}.json"
    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            cached_uuid = meta.get("uuid")
            fetched_at = meta.get("fetched_at", 0)
            if cached_uuid and (time.time() - fetched_at < 86400):
                return cached_uuid
        except Exception:
            pass

    try:
        url_profile = f"{ELY_BY_AUTH_SERVER}/api/profiles/minecraft"
        resp = requests.post(url_profile, json=[clean_name], timeout=timeout)
        if resp.status_code == 200 and resp.json():
            raw_id = resp.json()[0].get("id")
            if raw_id:
                formatted_uuid = str(uuid.UUID(hex=raw_id))
                try:
                    existing = {}
                    if meta_file.is_file():
                        existing = json.loads(meta_file.read_text(encoding="utf-8"))
                    existing.update({
                        "uuid": formatted_uuid,
                        "raw_uuid": raw_id,
                        "fetched_at": time.time(),
                    })
                    meta_file.write_text(json.dumps(existing), encoding="utf-8")
                except Exception:
                    pass
                return formatted_uuid
    except Exception as error:
        print(f"Notice: Ely.by UUID lookup for '{clean_name}': {error}")

    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            return meta.get("uuid")
        except Exception:
            pass

    return None


def sync_playerdata_uuids(game_dir: Path | str, source_uuid: str, target_uuid: str) -> None:
    """Synchronizes world player save files (.dat, stats, advancements) between two UUIDs
    (e.g., offline player UUID and Ely.by account UUID) across all worlds in game_dir/saves.
    Ensures the user preserves full inventory, level, position, stats, and achievements.
    """
    if not source_uuid or not target_uuid or source_uuid.lower() == target_uuid.lower():
        return
    saves_dir = Path(game_dir) / "saves"
    if not saves_dir.is_dir():
        return

    target_subdirs = [
        ("playerdata", ".dat"),
        ("playerdata", ".dat_old"),
        ("stats", ".json"),
        ("advancements", ".json"),
        ("players/data", ".dat"),
        ("players/data", ".dat_old"),
        ("players/stats", ".json"),
        ("players/advancements", ".json"),
    ]

    try:
        for world_dir in saves_dir.iterdir():
            if not world_dir.is_dir():
                continue
            for rel_sub, ext in target_subdirs:
                folder = world_dir / rel_sub
                if not folder.is_dir():
                    continue
                file_src = folder / f"{source_uuid}{ext}"
                file_tgt = folder / f"{target_uuid}{ext}"

                src_exists = file_src.is_file()
                tgt_exists = file_tgt.is_file()

                if src_exists and not tgt_exists:
                    try:
                        shutil.copy2(file_src, file_tgt)
                    except OSError:
                        pass
                elif tgt_exists and not src_exists:
                    try:
                        shutil.copy2(file_tgt, file_src)
                    except OSError:
                        pass
                elif src_exists and tgt_exists:
                    try:
                        src_mtime = file_src.stat().st_mtime
                        tgt_mtime = file_tgt.stat().st_mtime
                        if src_mtime > tgt_mtime:
                            shutil.copy2(file_src, file_tgt)
                        elif tgt_mtime > src_mtime:
                            shutil.copy2(file_tgt, file_src)
                    except OSError:
                        pass
    except Exception as error:
        print(f"Notice: sync_playerdata_uuids failed: {error}")


SKIN_CONFIG_FILE = get_skins_dir() / "active_skin.json"


def load_active_skin_choice() -> dict[str, str]:
    """Loads active skin configuration (skin_type: 'ely' | 'custom' | 'default', model, custom_path)."""
    default_config = {
        "skin_type": "ely",
        "model": "classic",
        "custom_path": "",
    }
    if not SKIN_CONFIG_FILE.is_file():
        return default_config
    try:
        data = json.loads(SKIN_CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "skin_type": str(data.get("skin_type", "ely")),
                "model": "slim" if data.get("model") == "slim" else "classic",
                "custom_path": str(data.get("custom_path", "")),
            }
    except Exception:
        pass
    return default_config


def save_active_skin_choice(skin_type: str, model: str = "classic", custom_path: str = "") -> bool:
    """Saves active skin choice to .minecraft/gr8launcher/skins/active_skin.json."""
    temporary_file = SKIN_CONFIG_FILE.with_suffix(".tmp")
    data = {
        "skin_type": skin_type if skin_type in ("ely", "custom", "default") else "ely",
        "model": "slim" if model == "slim" else "classic",
        "custom_path": custom_path,
    }
    try:
        SKIN_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary_file.replace(SKIN_CONFIG_FILE)
        return True
    except OSError:
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False


class LocalSkinServer:
    """A lightweight local HTTP server running on 127.0.0.1 that serves cached player skins
    to authlib-injector when playing offline, completely bypassing external network checks.
    """
    def __init__(
        self,
        username: str,
        uuid_str: str,
        skin_png_path: Path,
        model: str = "classic",
        skin_hash: str = "",
    ):
        self.username = username
        self.uuid_str = uuid_str
        self.skin_png_path = Path(skin_png_path)
        self.model = model
        self.skin_hash = skin_hash
        if not self.skin_hash:
            try:
                if self.skin_png_path.is_file():
                    self.skin_hash = hashlib.sha1(self.skin_png_path.read_bytes()).hexdigest()
            except Exception:
                pass
            if not self.skin_hash:
                self.skin_hash = self.uuid_str.replace("-", "")
        self.server: http.server.HTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int = 0

    def start(self) -> int:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # Suppress console logging

            def do_GET(self):
                print(f"[LocalSkinServer] Request: {self.path}", flush=True)
                parsed = urllib.parse.urlparse(self.path)
                p = parsed.path.rstrip("/")
                if not p or p == "/api/authlib-injector":
                    meta = {
                        "meta": {
                            "serverName": "Gr8LauncherOffline",
                            "implementationName": "Gr8 Launcher Offline Skin Server",
                            "implementationVersion": "1.0.0",
                        },
                        "skinDomains": ["127.0.0.1", "localhost", "ely.by", ".ely.by"],
                    }
                    body = json.dumps(meta).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=UTF-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if "/sessionserver/session/minecraft/profile/" in parsed.path:
                    # Extract the requested UUID from the path: .../profile/<uuid>
                    req_uuid = parsed.path.split("/sessionserver/session/minecraft/profile/")[-1].split("?")[0].replace("-", "")
                    raw_id = req_uuid or outer.uuid_str.replace("-", "")
                    tex_data = {
                        "timestamp": int(time.time() * 1000),
                        "profileId": raw_id,
                        "profileName": outer.username,
                        "textures": {
                            "SKIN": {
                                "url": f"http://127.0.0.1:{outer.port}/textures/{outer.username}/{outer.skin_hash}.png",
                                "metadata": {"model": outer.model},
                            }
                        },
                    }
                    tex_b64 = base64.b64encode(json.dumps(tex_data).encode("utf-8")).decode("ascii")
                    profile = {
                        "id": raw_id,
                        "name": outer.username,
                        "properties": [{"name": "textures", "value": tex_b64}],
                    }
                    body = json.dumps(profile).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=UTF-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if p == "/api/profiles/minecraft":
                    raw_id = outer.uuid_str.replace("-", "")
                    profile_list = [{"id": raw_id, "name": outer.username}]
                    body = json.dumps(profile_list).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=UTF-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if p.startswith("/textures") or p.endswith(".png"):
                    parts = [seg for seg in p.split("/") if seg]
                    req_name = ""
                    if len(parts) >= 3 and parts[0] == "textures":
                        req_name = parts[1].strip()
                    elif len(parts) >= 2 and parts[0] == "textures":
                        req_name = parts[1].replace(".png", "").strip()
                    else:
                        req_name = p.split("/")[-1].replace(".png", "").strip()

                    if not req_name or req_name.lower() == outer.username.lower():
                        if outer.skin_png_path.is_file():
                            data = outer.skin_png_path.read_bytes()
                            try:
                                with Image.open(io.BytesIO(data)) as im:
                                    im_rgba = ensure_64x64(im)
                                    buf = io.BytesIO()
                                    im_rgba.save(buf, format="PNG")
                                    data = buf.getvalue()
                            except Exception:
                                pass
                            self.send_response(200)
                            self.send_header("Content-Type", "image/png")
                            self.send_header("Content-Length", str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                            return
                    else:
                        # Multiplayer: requested skin for another player on the server
                        user_cache = get_skins_dir() / f"ely_{req_name.lower()}.png"
                        if user_cache.is_file():
                            data = user_cache.read_bytes()
                            self.send_response(200)
                            self.send_header("Content-Type", "image/png")
                            self.send_header("Content-Length", str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                            return
                        # Try fetching from Ely.by
                        try:
                            skin_bytes, _ = fetch_ely_skin(req_name, timeout=2.0)
                            if skin_bytes:
                                self.send_response(200)
                                self.send_header("Content-Type", "image/png")
                                self.send_header("Content-Length", str(len(skin_bytes)))
                                self.end_headers()
                                self.wfile.write(skin_bytes)
                                return
                        except Exception:
                            pass
                        # Fallback: check Minotar (Mojang premium account skin for cracked servers)
                        try:
                            resp = requests.get(f"https://minotar.net/skin/{req_name}", timeout=2.0)
                            if resp.status_code == 200 and len(resp.content) > 100:
                                self.send_response(200)
                                self.send_header("Content-Type", "image/png")
                                self.send_header("Content-Length", str(len(resp.content)))
                                self.end_headers()
                                self.wfile.write(resp.content)
                                return
                        except Exception:
                            pass

                    self.send_response(404)
                    self.end_headers()
                    return

                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                p = parsed.path.rstrip("/")
                if p == "/api/profiles/minecraft":
                    raw_id = outer.uuid_str.replace("-", "")
                    profile_list = [{"id": raw_id, "name": outer.username}]
                    body = json.dumps(profile_list).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=UTF-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.port

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            self.server = None


def prepare_skin_launch(
    username: str,
    game_dir: Path | str,
    minecraft_dir: Path | str,
    is_online: bool = True,
) -> tuple[str, list[str], LocalSkinServer | None]:
    """Prepares the skin environment for Minecraft launch.
    Returns: (active_uuid, jvm_arguments, local_skin_server_instance)

    - Starts LocalSkinServer on 127.0.0.1 to serve custom and multiplayer skins locally
      with zero internet latency and instant texture decoding.
    - Connects authlib-injector to Ely.by if online, or to LocalSkinServer if offline.
    - Attaches gr8-skin-bridge.jar companion agent to dynamically populate GameProfile
      properties and bypass domain restrictions on cracked/offline multiplayer servers.
    """
    clean_name = username.strip()
    offline_digest = hashlib.md5(f"OfflinePlayer:{clean_name}".encode("utf-8")).digest()
    offline_uuid = str(uuid.UUID(bytes=offline_digest, version=3))
    active_uuid = offline_uuid
    jvm_args: list[str] = []
    local_server: LocalSkinServer | None = None

    jar_path = ensure_authlib_injector()
    if not jar_path or not jar_path.is_file():
        return active_uuid, jvm_args, None

    can_reach_remote = is_online and is_skin_server_reachable(timeout=0.6)

    # Clean legacy non-hash cache file from Minecraft's assets/skins so client never gets poisoned
    try:
        if len(clean_name) >= 2:
            legacy_cache = Path(minecraft_dir) / "assets" / "skins" / clean_name[:2] / clean_name
            if legacy_cache.is_file():
                legacy_cache.unlink(missing_ok=True)
    except Exception:
        pass

    # Resolve active skin choice from active_skin.json
    active_choice = load_active_skin_choice()
    skin_type = active_choice.get("skin_type", "ely")
    active_model = active_choice.get("model", "classic")
    custom_path = active_choice.get("custom_path", "")

    cached_skin = get_skins_dir() / f"ely_{clean_name.lower()}.png"
    cached_meta_user = get_skins_dir() / f"ely_{clean_name.lower()}.json"
    cached_ely_uuid = None

    if cached_meta_user.is_file():
        try:
            m = json.loads(cached_meta_user.read_text(encoding="utf-8"))
            cached_ely_uuid = m.get("uuid")
            if skin_type == "ely":
                active_model = m.get("model", active_model)
        except Exception:
            pass

    if skin_type == "custom" and custom_path and Path(custom_path).is_file():
        active_skin_file = Path(custom_path)
    elif skin_type == "default":
        active_skin_file = get_skins_dir() / f"default_{active_model}.png"
        if not active_skin_file.is_file():
            try:
                active_skin_file.write_bytes(get_default_skin_bytes(active_model))
            except OSError:
                pass
    else:
        # Default to Ely.by skin synchronization
        if can_reach_remote:
            try:
                skin_bytes, fresh_model = fetch_ely_skin(clean_name, force_refresh=True, timeout=2.5)
                if skin_bytes:
                    active_model = fresh_model
            except Exception as error:
                print(f"Notice: pre-launch Ely.by skin sync: {error}")

        if not cached_skin.is_file():
            try:
                cached_skin.write_bytes(get_default_skin_bytes(active_model))
            except OSError:
                pass
        active_skin_file = cached_skin

    if can_reach_remote:
        ely_uuid = get_ely_uuid(clean_name, timeout=3.0) or cached_ely_uuid
        if ely_uuid:
            active_uuid = ely_uuid
            sync_playerdata_uuids(game_dir, offline_uuid, ely_uuid)
            if str(game_dir) != str(minecraft_dir):
                sync_playerdata_uuids(minecraft_dir, offline_uuid, ely_uuid)

    # Compute content SHA1 hash of the active skin file
    try:
        active_bytes = active_skin_file.read_bytes() if active_skin_file.is_file() else get_default_skin_bytes(active_model)
    except OSError:
        active_bytes = get_default_skin_bytes(active_model)
    skin_hash = hashlib.sha1(active_bytes).hexdigest()

    # Start LocalSkinServer to serve local and multiplayer skins
    local_server = LocalSkinServer(
        username=clean_name,
        uuid_str=active_uuid,
        skin_png_path=active_skin_file,
        model=active_model,
        skin_hash=skin_hash,
    )
    port = local_server.start()

    if can_reach_remote:
        meta_json = get_cached_metadata()
        meta_b64 = base64.b64encode(meta_json.encode("utf-8")).decode("ascii")
        jvm_args.append(f"-javaagent:{jar_path}={ELY_BY_AUTH_SERVER}")
        jvm_args.append(f"-Dauthlibinjector.yggdrasil.prefetched={meta_b64}")
        jvm_args.append(f"-Dorg.to2mbn.authlibinjector.config.prefetched={meta_b64}")
        jvm_args.append("-Dauthlibinjector.noShowServerName=true")
        jvm_args.append("-Dauthlibinjector.side=client")
    else:
        local_meta = json.dumps({
            "meta": {
                "serverName": "Gr8LauncherOffline",
                "implementationName": "Gr8 Launcher Offline Skin Server",
                "implementationVersion": "1.0.0",
                "feature.no_mojang_namespace": True,
            },
            "skinDomains": ["127.0.0.1", "localhost", "ely.by", ".ely.by"],
        })
        meta_b64 = base64.b64encode(local_meta.encode("utf-8")).decode("ascii")
        jvm_args.append(f"-javaagent:{jar_path}=http://127.0.0.1:{port}")
        jvm_args.append(f"-Dauthlibinjector.yggdrasil.prefetched={meta_b64}")
        jvm_args.append(f"-Dorg.to2mbn.authlibinjector.config.prefetched={meta_b64}")
        jvm_args.append("-Dauthlibinjector.noShowServerName=true")
        jvm_args.append("-Dauthlibinjector.side=client")

    # Attach gr8-skin-bridge companion agent with skin hash
    bridge_jar = ensure_skin_bridge()
    if bridge_jar and bridge_jar.is_file():
        jvm_args.append(f"-javaagent:{bridge_jar}=port={port},user={clean_name},model={active_model},hash={skin_hash}")
        jvm_args.append(f"-Dgr8skin.port={port}")
        jvm_args.append(f"-Dgr8skin.user={clean_name}")
        jvm_args.append(f"-Dgr8skin.model={active_model}")
        jvm_args.append(f"-Dgr8skin.hash={skin_hash}")
        jvm_args.append("-Dfabric.debug.disableClassPathIsolation=true")

    return active_uuid, jvm_args, local_server
