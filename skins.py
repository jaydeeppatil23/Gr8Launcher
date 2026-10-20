"""Gr8 Launcher - Skin Engine
Handles Minecraft skin rendering (3d/front/back),
validation of 64x64 and legacy 64x32 skin formats, Ely.by skin synchronization,
local skin preset library, and automatic management of authlib-injector.jar.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import io
import json
import math
import os
import shutil
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance
import requests

from storage import get_data_dir, load_settings, save_settings

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

    if view.lower() == "3d":
        return render_3d_skin_bytes(img, model=model)

    rendered = render_character(img, view=view, model=model, scale=scale)
    buf = io.BytesIO()
    rendered.save(buf, format="PNG")
    return buf.getvalue()


def _find_affine_coeffs(dst_pts, src_pts):
    """Calculates 6-element affine matrix mapping destination bounding points to source points."""
    (x0, y0), (x1, y1), (x2, y2) = dst_pts
    (u0, v0), (u1, v1), (u2, v2) = src_pts
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(det) < 1e-9:
        return None
    a = ((u1 - u0) * (y2 - y0) - (u2 - u0) * (y1 - y0)) / det
    b = ((x1 - x0) * (u2 - u0) - (x2 - x0) * (u1 - u0)) / det
    c = u0 - a * x0 - b * y0
    d = ((v1 - v0) * (y2 - y0) - (v2 - v0) * (y1 - y0)) / det
    e = ((x1 - x0) * (v2 - v0) - (x2 - x0) * (v1 - v0)) / det
    f = v0 - d * x0 - e * y0
    return (a, b, c, d, e, f)


def _rotate_pt_euler(x, y, z, rx, ry, rz):
    """Rotates a 3D point by pitch (rx), yaw (ry), and roll (rz) radians."""
    if rz != 0:
        cz, sz = math.cos(rz), math.sin(rz)
        x, y = x * cz - y * sz, x * sz + y * cz
    if rx != 0:
        cx, sx = math.cos(rx), math.sin(rx)
        y, z = y * cx - z * sx, y * sx + z * cx
    if ry != 0:
        cy, sy = math.cos(ry), math.sin(ry)
        x, z = x * cy + z * sy, -x * sy + z * cy
    return x, y, z


def render_3d_skin(
    skin: Image.Image,
    yaw_deg: float = -25.0,
    pitch_deg: float = 10.0,
    model: str = "classic",
    pose: str = "hero",
    width: int = 240,
    height: int = 340,
    scale: float = 8.5,
) -> Image.Image:
    """Renders an interactive, lighting-shaded 3D Minecraft player model.
    Supports 64x64/64x32 skins, Classic (Steve) and Slim (Alex) models,
    full overlay layers (hat, jacket, sleeves, pants), and directional shading.
    """
    if skin.mode != "RGBA":
        skin = skin.convert("RGBA")
    if skin.size[1] == 32:
        skin = ensure_64x64(skin)

    is_slim = model.lower() == "slim"
    arm_w = 3.0 if is_slim else 4.0

    yaw_rad = math.radians(yaw_deg)
    pitch_rad = math.radians(pitch_deg)
    cy, sy = math.cos(yaw_rad), math.sin(yaw_rad)
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)

    # Directional key light vector (top-left-front)
    lx, ly, lz = 0.35, 0.75, 0.55
    l_mag = math.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / l_mag, ly / l_mag, lz / l_mag

    cx = width / 2.0
    cy_scr = height / 2.0 + 8.0

    if pose == "hero":
        poses = {
            "head": (0.0, -5.0, 0.0),
            "torso": (0.0, 0.0, 0.0),
            "right_arm": (14.0, 0.0, 2.0),
            "left_arm": (-14.0, 0.0, -2.0),
            "right_leg": (-10.0, 0.0, 0.0),
            "left_leg": (10.0, 0.0, 0.0),
        }
    else:
        poses = {
            "head": (0.0, 0.0, 0.0),
            "torso": (0.0, 0.0, 0.0),
            "right_arm": (0.0, 0.0, 0.0),
            "left_arm": (0.0, 0.0, 0.0),
            "right_leg": (0.0, 0.0, 0.0),
            "left_leg": (0.0, 0.0, 0.0),
        }

    pivots = {
        "head": (0.0, 8.0, 0.0),
        "torso": (0.0, 0.0, 0.0),
        "right_arm": (-4.0 - arm_w / 2.0, 6.0, 0.0),
        "left_arm": (4.0 + arm_w / 2.0, 6.0, 0.0),
        "right_leg": (-2.0, -4.0, 0.0),
        "left_leg": (2.0, -4.0, 0.0),
    }

    boxes = [
        {
            "name": "head",
            "bounds": (-4, 4, 8, 16, -4, 4),
            "uv": {
                "top": (8, 0, 16, 8),
                "bottom": (16, 0, 24, 8),
                "right": (0, 8, 8, 16),
                "front": (8, 8, 16, 16),
                "left": (16, 8, 24, 16),
                "back": (24, 8, 32, 16),
            },
            "overlay": {
                "top": (40, 0, 48, 8),
                "bottom": (48, 0, 56, 8),
                "right": (32, 8, 40, 16),
                "front": (40, 8, 48, 16),
                "left": (48, 8, 56, 16),
                "back": (56, 8, 64, 16),
            },
            "expand": 0.5,
        },
        {
            "name": "torso",
            "bounds": (-4, 4, -4, 8, -2, 2),
            "uv": {
                "top": (20, 16, 28, 20),
                "bottom": (28, 16, 36, 20),
                "right": (16, 20, 20, 32),
                "front": (20, 20, 28, 32),
                "left": (28, 20, 32, 32),
                "back": (32, 20, 40, 32),
            },
            "overlay": {
                "top": (20, 32, 28, 36),
                "bottom": (28, 32, 36, 36),
                "right": (16, 36, 20, 48),
                "front": (20, 36, 28, 48),
                "left": (28, 36, 32, 48),
                "back": (32, 36, 40, 48),
            },
            "expand": 0.35,
        },
        {
            "name": "right_arm",
            "bounds": (-4 - arm_w, -4, -4, 8, -2, 2),
            "uv": {
                "top": (44, 16, 47 if is_slim else 48, 20),
                "bottom": (47 if is_slim else 48, 16, 50 if is_slim else 52, 20),
                "right": (40, 20, 44, 32),
                "front": (44, 20, 47 if is_slim else 48, 32),
                "left": (47 if is_slim else 48, 20, 51 if is_slim else 52, 32),
                "back": (51 if is_slim else 52, 20, 54 if is_slim else 56, 32),
            },
            "overlay": {
                "top": (44, 32, 47 if is_slim else 48, 36),
                "bottom": (47 if is_slim else 48, 32, 50 if is_slim else 52, 36),
                "right": (40, 36, 44, 48),
                "front": (44, 36, 47 if is_slim else 48, 48),
                "left": (47 if is_slim else 48, 36, 51 if is_slim else 52, 48),
                "back": (51 if is_slim else 52, 36, 54 if is_slim else 56, 48),
            },
            "expand": 0.35,
        },
        {
            "name": "left_arm",
            "bounds": (4, 4 + arm_w, -4, 8, -2, 2),
            "uv": {
                "top": (36, 48, 39 if is_slim else 40, 52),
                "bottom": (39 if is_slim else 40, 48, 42 if is_slim else 44, 52),
                "right": (32, 52, 36, 64),
                "front": (36, 52, 39 if is_slim else 40, 64),
                "left": (39 if is_slim else 40, 52, 43 if is_slim else 44, 64),
                "back": (43 if is_slim else 44, 52, 46 if is_slim else 48, 64),
            },
            "overlay": {
                "top": (52, 48, 55 if is_slim else 56, 52),
                "bottom": (55 if is_slim else 56, 48, 58 if is_slim else 60, 52),
                "right": (48, 52, 52, 64),
                "front": (52, 52, 55 if is_slim else 56, 64),
                "left": (55 if is_slim else 56, 52, 59 if is_slim else 60, 64),
                "back": (59 if is_slim else 60, 52, 62 if is_slim else 64, 64),
            },
            "expand": 0.35,
        },
        {
            "name": "right_leg",
            "bounds": (-4, 0, -16, -4, -2, 2),
            "uv": {
                "top": (4, 16, 8, 20),
                "bottom": (8, 16, 12, 20),
                "right": (0, 20, 4, 32),
                "front": (4, 20, 8, 32),
                "left": (8, 20, 12, 32),
                "back": (12, 20, 16, 32),
            },
            "overlay": {
                "top": (4, 32, 8, 36),
                "bottom": (8, 32, 12, 36),
                "right": (0, 36, 4, 48),
                "front": (4, 36, 8, 48),
                "left": (8, 36, 12, 48),
                "back": (12, 36, 16, 48),
            },
            "expand": 0.35,
        },
        {
            "name": "left_leg",
            "bounds": (0, 4, -16, -4, -2, 2),
            "uv": {
                "top": (20, 48, 24, 52),
                "bottom": (24, 48, 28, 52),
                "right": (16, 52, 20, 64),
                "front": (20, 52, 24, 64),
                "left": (24, 52, 28, 64),
                "back": (28, 52, 32, 64),
            },
            "overlay": {
                "top": (4, 48, 8, 52),
                "bottom": (8, 48, 12, 52),
                "right": (0, 52, 4, 64),
                "front": (4, 52, 8, 64),
                "left": (8, 52, 12, 64),
                "back": (12, 52, 16, 64),
            },
            "expand": 0.35,
        },
    ]

    faces_to_draw = []

    def transform_pt(pt, part_name):
        px, py, pz = pivots[part_name]
        rx_deg, ry_deg, rz_deg = poses[part_name]
        lx, ly, lz = pt[0] - px, pt[1] - py, pt[2] - pz
        if rx_deg != 0 or ry_deg != 0 or rz_deg != 0:
            lx, ly, lz = _rotate_pt_euler(
                lx, ly, lz,
                math.radians(rx_deg),
                math.radians(ry_deg),
                math.radians(rz_deg)
            )
        world_x, world_y, world_z = lx + px, ly + py, lz + pz
        x1 = world_x * cy + world_z * sy
        y1 = world_y
        z1 = -world_x * sy + world_z * cy
        x2 = x1
        y2 = y1 * cp - z1 * sp
        z2 = y1 * sp + z1 * cp
        return x2, y2, z2

    def process_box(b_info, is_overlay=False):
        part_name = b_info["name"]
        exp = b_info.get("expand", 0.35) if is_overlay else 0.0
        x0, x1, y0, y1, z0, z1 = b_info["bounds"]
        x0, x1 = x0 - exp, x1 + exp
        y0, y1 = y0 - exp, y1 + exp
        z0, z1 = z0 - exp, z1 + exp
        uv_dict = b_info["overlay"] if is_overlay else b_info["uv"]

        face_defs = {
            "front": {
                "pts": [(x0, y1, z1), (x1, y1, z1), (x1, y0, z1), (x0, y0, z1)],
                "normal": (0, 0, 1),
            },
            "back": {
                "pts": [(x1, y1, z0), (x0, y1, z0), (x0, y0, z0), (x1, y0, z0)],
                "normal": (0, 0, -1),
            },
            "top": {
                "pts": [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)],
                "normal": (0, 1, 0),
            },
            "bottom": {
                "pts": [(x0, y0, z1), (x1, y0, z1), (x1, y0, z0), (x0, y0, z0)],
                "normal": (0, -1, 0),
            },
            "right": {
                "pts": [(x0, y1, z0), (x0, y1, z1), (x0, y0, z1), (x0, y0, z0)],
                "normal": (-1, 0, 0),
            },
            "left": {
                "pts": [(x1, y1, z1), (x1, y1, z0), (x1, y0, z0), (x1, y0, z1)],
                "normal": (1, 0, 0),
            },
        }

        for face_name, f_info in face_defs.items():
            uv_rect = uv_dict.get(face_name)
            if not uv_rect:
                continue

            cam_pts = [transform_pt(pt, part_name) for pt in f_info["pts"]]
            p0, p1, p3 = cam_pts[0], cam_pts[1], cam_pts[3]
            v1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
            v2 = (p3[0] - p0[0], p3[1] - p0[1], p3[2] - p0[2])
            nx = v2[1] * v1[2] - v2[2] * v1[1]
            ny = v2[2] * v1[0] - v2[0] * v1[2]
            nz = v2[0] * v1[1] - v2[1] * v1[0]
            n_len = math.sqrt(nx * nx + ny * ny + nz * nz)
            if n_len < 1e-6:
                continue
            nx, ny, nz = nx / n_len, ny / n_len, nz / n_len

            if nz <= 0.001:
                continue

            screen_pts = []
            z_sum = 0.0
            for pt in cam_pts:
                sx = cx + pt[0] * scale
                sy_pos = cy_scr - pt[1] * scale
                screen_pts.append((sx, sy_pos))
                z_sum += pt[2]

            avg_z = z_sum / 4.0
            if is_overlay:
                avg_z += 0.05

            dot = max(0.0, nx * lx + ny * ly + nz * lz)
            light_mult = 0.55 + 0.45 * dot
            if face_name == "top":
                light_mult *= 1.15
            elif face_name == "bottom":
                light_mult *= 0.6
            elif face_name in ("right", "left"):
                light_mult *= 0.88
            light_mult = min(1.25, max(0.4, light_mult))

            faces_to_draw.append({
                "screen_pts": screen_pts,
                "avg_z": avg_z,
                "uv_rect": uv_rect,
                "light_mult": light_mult,
                "is_overlay": is_overlay,
            })

    for b in boxes:
        process_box(b, is_overlay=False)
        process_box(b, is_overlay=True)

    faces_to_draw.sort(key=lambda f: f["avg_z"])

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # Soft ambient pedestal shadow at character's feet
    shadow_y = cy_scr - (-16.0) * scale
    draw.ellipse(
        [cx - 42, shadow_y - 10, cx + 42, shadow_y + 10],
        fill=(0, 0, 0, 40)
    )

    for f in faces_to_draw:
        uv = f["uv_rect"]
        tex = skin.crop(uv)
        if f["is_overlay"] and tex.getextrema()[3][1] == 0:
            continue

        mult = f["light_mult"]
        if abs(mult - 1.0) > 0.03:
            enhancer = ImageEnhance.Brightness(tex)
            tex = enhancer.enhance(mult)

        s_pts = f["screen_pts"]
        dst_p0, dst_p1, dst_p2, dst_p3 = s_pts

        xs = [p[0] for p in s_pts]
        ys = [p[1] for p in s_pts]
        min_x = int(math.floor(min(xs)))
        max_x = int(math.ceil(max(xs)))
        min_y = int(math.floor(min(ys)))
        max_y = int(math.ceil(max(ys)))

        bw = max_x - min_x + 1
        bh = max_y - min_y + 1
        if bw <= 0 or bh <= 0:
            continue

        rel_dst = [
            (dst_p0[0] - min_x, dst_p0[1] - min_y),
            (dst_p1[0] - min_x, dst_p1[1] - min_y),
            (dst_p3[0] - min_x, dst_p3[1] - min_y),
        ]

        tw, th = tex.size
        src_corners = [(0, 0), (tw, 0), (0, th)]

        coeffs = _find_affine_coeffs(rel_dst, src_corners)
        if not coeffs:
            continue

        warped = tex.transform((bw, bh), Image.Transform.AFFINE, coeffs, resample=Image.Resampling.NEAREST)

        mask = Image.new("L", (bw, bh), 0)
        mask_draw = ImageDraw.Draw(mask)
        poly_pts = [(p[0] - min_x, p[1] - min_y) for p in s_pts]
        mask_draw.polygon(poly_pts, fill=255)

        r, g, b_ch, a_ch = warped.split()
        final_alpha = Image.composite(a_ch, Image.new("L", (bw, bh), 0), mask)
        warped.putalpha(final_alpha)

        canvas.alpha_composite(warped, (min_x, min_y))

    return canvas


def render_3d_skin_bytes(
    skin_input: Path | str | bytes | Image.Image,
    yaw_deg: float = -25.0,
    pitch_deg: float = 10.0,
    model: str = "classic",
    pose: str = "hero",
    width: int = 240,
    height: int = 340,
    scale: float = 8.5,
) -> bytes:
    """Renders a 3D character preview and returns the PNG encoded bytes."""
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

    rendered = render_3d_skin(
        img,
        yaw_deg=yaw_deg,
        pitch_deg=pitch_deg,
        model=model,
        pose=pose,
        width=width,
        height=height,
        scale=scale,
    )
    buf = io.BytesIO()
    rendered.save(buf, format="PNG", compress_level=1)
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
    """Loads active skin configuration (skin_type: 'ely' | 'custom' | 'default', model, custom_path).
    Automatically self-heals from settings.json if active_skin.json was deleted.
    """
    default_config = {
        "skin_type": "ely",
        "model": "classic",
        "custom_path": "",
    }
    if not SKIN_CONFIG_FILE.is_file():
        # Check settings.json for backup configuration if skins folder was deleted
        try:
            settings = load_settings()
            if "active_skin" in settings and isinstance(settings["active_skin"], dict):
                backup = settings["active_skin"]
                cfg = {
                    "skin_type": str(backup.get("skin_type", "ely")),
                    "model": "slim" if backup.get("model") == "slim" else "classic",
                    "custom_path": str(backup.get("custom_path", "")),
                }
                save_active_skin_choice(cfg["skin_type"], model=cfg["model"], custom_path=cfg["custom_path"])
                return cfg
        except Exception:
            pass
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
    """Saves active skin choice to .minecraft/gr8launcher/skins/active_skin.json and mirrors to settings.json."""
    temporary_file = SKIN_CONFIG_FILE.with_suffix(".tmp")
    data = {
        "skin_type": skin_type if skin_type in ("ely", "custom", "default") else "ely",
        "model": "slim" if model == "slim" else "classic",
        "custom_path": custom_path,
    }

    # Mirror into settings.json as persistent safety backup
    try:
        settings = load_settings()
        settings["active_skin"] = data
        save_settings(settings)
    except Exception:
        pass

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


def prune_skin_cache(max_cached_skins: int = 150, max_age_days: int = 14) -> int:
    """Automatically cleans up old and unused multiplayer skins from .minecraft/gr8launcher/skins.
    Protects the active player's skin and configuration files.
    Returns the number of pruned files.
    """
    skins_dir = get_skins_dir()
    if not skins_dir.is_dir():
        return 0

    now = time.time()
    max_age_seconds = max_age_days * 86400

    # Get active player name to protect their skin files
    protected_prefixes = {"active_skin", "ely_metadata", "default_classic", "default_slim"}
    try:
        from storage import PROFILE_OPTIONS_FILE
        if PROFILE_OPTIONS_FILE.is_file():
            pdata = json.loads(PROFILE_OPTIONS_FILE.read_text(encoding="utf-8"))
            active_name = pdata.get("player_name", "").strip().lower()
            if active_name:
                protected_prefixes.add(f"ely_{active_name}")
    except Exception:
        pass

    # Collect other player cache files
    candidate_files: list[tuple[float, Path]] = []
    for item in skins_dir.iterdir():
        if not item.is_file():
            continue
        stem = item.stem.lower()
        if any(stem == p or stem.startswith(p) for p in protected_prefixes):
            continue

        # Instantly purge generic Steve/Alex fallback duplicates
        if item.suffix.lower() == ".png" and item.name.startswith("ely_"):
            try:
                h = hashlib.md5(item.read_bytes()).hexdigest()
                if h in {"6b751644d13c7c7a1fb320042a1e0e9e", "e5a2e67dba2c2d6bb80817a1c9211036"}:
                    item.unlink(missing_ok=True)
                    continue
            except OSError:
                pass

        try:
            mtime = item.stat().st_mtime
            candidate_files.append((mtime, item))
        except OSError:
            pass

    pruned = 0
    # 1. Delete expired files (> max_age_days old)
    remaining: list[tuple[float, Path]] = []
    for mtime, fpath in candidate_files:
        if now - mtime > max_age_seconds:
            try:
                fpath.unlink(missing_ok=True)
                pruned += 1
            except OSError:
                pass
        else:
            remaining.append((mtime, fpath))

    # 2. Enforce max_cached_skins cap (LRU: sort by oldest mtime first)
    max_files = max_cached_skins * 2
    if len(remaining) > max_files:
        remaining.sort(key=lambda x: x[0])
        excess = len(remaining) - max_files
        for _, fpath in remaining[:excess]:
            try:
                fpath.unlink(missing_ok=True)
                pruned += 1
            except OSError:
                pass

    return pruned


clean_old_skins_cache = prune_skin_cache


class LocalSkinServer:
    """A lightweight multi-threaded local HTTP server running on 127.0.0.1 that serves cached player skins
    to authlib-injector and Minecraft client with zero external latency and non-blocking I/O.
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
        self.cached_bytes: bytes | None = None
        self._negative_cache: dict[str, float] = {}
        self._cache_lock = threading.Lock()

        # Pre-process active skin into memory once
        try:
            if self.skin_png_path.is_file():
                raw_bytes = self.skin_png_path.read_bytes()
                with Image.open(io.BytesIO(raw_bytes)) as im:
                    im_rgba = ensure_64x64(im)
                    buf = io.BytesIO()
                    im_rgba.save(buf, format="PNG")
                    self.cached_bytes = buf.getvalue()
        except Exception:
            self.cached_bytes = None

        if not self.skin_hash:
            if self.cached_bytes:
                self.skin_hash = hashlib.sha1(self.cached_bytes).hexdigest()
            else:
                self.skin_hash = self.uuid_str.replace("-", "")

        self.server: http.server.ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int = 0

    def start(self) -> int:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # Suppress console logging

            def do_GET(self):
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

                    # 1. Active player skin (served straight from pre-cached memory)
                    if not req_name or req_name.lower() == outer.username.lower():
                        data = outer.cached_bytes
                        if data is None and outer.skin_png_path.is_file():
                            try:
                                data = outer.skin_png_path.read_bytes()
                            except OSError:
                                pass
                        if data:
                            self.send_response(200)
                            self.send_header("Content-Type", "image/png")
                            self.send_header("Content-Length", str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                            return

                    # 2. Other player skins on multiplayer server
                    req_clean = req_name.lower()

                    # Check negative cache to prevent stalling server on non-existent usernames
                    now = time.time()
                    with outer._cache_lock:
                        last_failed = outer._negative_cache.get(req_clean, 0)
                        if now - last_failed < 60.0:
                            self.send_response(404)
                            self.end_headers()
                            return

                    # Check disk cache
                    user_cache = get_skins_dir() / f"ely_{req_clean}.png"
                    if user_cache.is_file():
                        try:
                            data = user_cache.read_bytes()
                            self.send_response(200)
                            self.send_header("Content-Type", "image/png")
                            self.send_header("Content-Length", str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                            return
                        except OSError:
                            pass

                    # Try fetching from Ely.by
                    try:
                        skin_bytes, _ = fetch_ely_skin(req_name, timeout=1.8)
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
                        resp = requests.get(f"https://minotar.net/skin/{req_name}", timeout=1.5)
                        if resp.status_code == 200 and len(resp.content) > 100:
                            h = hashlib.md5(resp.content).hexdigest()
                            # Do not pollute disk with duplicate default Steve fallbacks
                            if h not in {"6b751644d13c7c7a1fb320042a1e0e9e", "e5a2e67dba2c2d6bb80817a1c9211036"}:
                                try:
                                    user_cache.write_bytes(resp.content)
                                except OSError:
                                    pass
                            else:
                                with outer._cache_lock:
                                    outer._negative_cache[req_clean] = now + 86400.0
                            self.send_response(200)
                            self.send_header("Content-Type", "image/png")
                            self.send_header("Content-Length", str(len(resp.content)))
                            self.end_headers()
                            self.wfile.write(resp.content)
                            return
                    except Exception:
                        pass

                    # Record negative cache so we don't spam requests repeatedly
                    with outer._cache_lock:
                        outer._negative_cache[req_clean] = now

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

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
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

    can_reach_remote = is_online and is_skin_server_reachable(timeout=0.5)

    # Clean legacy non-hash cache file from Minecraft's assets/skins so client never gets poisoned
    try:
        if len(clean_name) >= 2:
            legacy_cache = Path(minecraft_dir) / "assets" / "skins" / clean_name[:2] / clean_name
            if legacy_cache.is_file():
                legacy_cache.unlink(missing_ok=True)
    except Exception:
        pass

    try:
        threading.Thread(target=clean_old_skins_cache, daemon=True).start()
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
    last_fetched_at = 0.0

    if cached_meta_user.is_file():
        try:
            m = json.loads(cached_meta_user.read_text(encoding="utf-8"))
            cached_ely_uuid = m.get("uuid")
            last_fetched_at = float(m.get("fetched_at", 0))
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
        # If cache was deleted or older than 5 minutes, sync from Ely.by
        is_fresh = (time.time() - last_fetched_at < 300) and cached_skin.is_file()
        if can_reach_remote and (not cached_skin.is_file() or not is_fresh):
            try:
                skin_bytes, fresh_model = fetch_ely_skin(clean_name, force_refresh=True, timeout=2.5)
                if skin_bytes:
                    active_model = fresh_model
                    # Reload cached UUID from updated meta file
                    if cached_meta_user.is_file():
                        try:
                            m = json.loads(cached_meta_user.read_text(encoding="utf-8"))
                            cached_ely_uuid = m.get("uuid", cached_ely_uuid)
                        except Exception:
                            pass
            except Exception as error:
                print(f"Notice: pre-launch Ely.by skin sync: {error}")

        active_skin_file = cached_skin

    if can_reach_remote:
        ely_uuid = cached_ely_uuid or get_ely_uuid(clean_name, timeout=1.8)
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

    # Clean up stale multiplayer skins in background (LRU cache management)
    threading.Thread(target=prune_skin_cache, daemon=True).start()

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
