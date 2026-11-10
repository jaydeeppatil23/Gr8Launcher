"""Built-in 1K FPS performance preset.

The preset only touches launcher-controlled launch settings: it writes a
small set of video options for the selected profile and adds tuned JVM flags
when the game starts. Worlds, accounts, and installed mods are left alone.
"""

from __future__ import annotations

import shutil
from pathlib import Path

BUNDLED_MOD_FILENAME = "gr8-performance.jar"
BUNDLED_MOD_PATH = (
    Path(__file__).resolve().parent / ".assets" / "mods" / BUNDLED_MOD_FILENAME
)

# Video options that remove the common frame-time bottlenecks. Unknown keys in
# options.txt are preserved, and values are only replaced (never reordered).
PERFORMANCE_OPTIONS: dict[str, str] = {
    "maxFps": "1000",
    "enableVsync": "false",
    "graphicsMode": "0",  # Fast: disables fancy leaves, clouds, and fog.
    "fancyGraphics": "false",  # Legacy equivalent of graphicsMode.
    "renderDistance": "10",
    "simulationDistance": "8",
    "entityShadows": "false",
    "ao": "false",
    "particles": "1",  # Decreased rather than minimal, so effects stay readable.
    "biomeBlendRadius": "2",
    "entityDistanceScaling": "0.75",
    "renderClouds": "false",
    "mipmapLevels": "0",
}


def performance_jvm_args() -> list[str]:
    """Return G1GC flags tuned for a fixed-size Minecraft heap."""
    return [
        "-XX:+UseG1GC",
        "-XX:+ParallelRefProcEnabled",
        "-XX:MaxGCPauseMillis=200",
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:+DisableExplicitGC",
        "-XX:+AlwaysPreTouch",
        "-XX:G1NewSizePercent=30",
        "-XX:G1MaxNewSizePercent=40",
        "-XX:G1HeapRegionSize=8M",
        "-XX:G1ReservePercent=20",
        "-XX:G1HeapWastePercent=5",
        "-XX:G1MixedGCCountTarget=4",
        "-XX:InitiatingHeapOccupancyPercent=15",
        "-XX:G1MixedGCLiveThresholdPercent=90",
        "-XX:G1RSetUpdatingPauseTimePercent=5",
        "-XX:SurvivorRatio=32",
        "-XX:+PerfDisableSharedMem",
        "-XX:MaxTenuringThreshold=1",
    ]


def apply_performance_options(game_directory: Path | str) -> bool:
    """Merge the performance preset into the profile's options.txt.

    Returns True when the file changed. Failures are intentionally swallowed
    so a read-only game folder can never stop the launcher from starting.
    """
    options_path = Path(game_directory) / "options.txt"
    try:
        existing = options_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    except OSError:
        return False

    lines = existing.splitlines()
    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        key, separator, _ = line.partition(":")
        if separator and key in PERFORMANCE_OPTIONS:
            updated.append(f"{key}:{PERFORMANCE_OPTIONS[key]}")
            seen.add(key)
        else:
            updated.append(line)

    for key, value in PERFORMANCE_OPTIONS.items():
        if key not in seen:
            updated.append(f"{key}:{value}")

    content = "\n".join(updated).rstrip("\n") + "\n"
    if content == existing:
        return False

    temporary_path = options_path.with_suffix(".txt.tmp")
    try:
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(options_path)
    except OSError:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def install_performance_mod(game_directory: Path | str) -> bool:
    """Copy the bundled mod JAR into a Fabric/Quilt instance's mods folder."""
    try:
        if not BUNDLED_MOD_PATH.is_file():
            return False
        mods_directory = Path(game_directory) / "mods"
        mods_directory.mkdir(parents=True, exist_ok=True)
        target = mods_directory / BUNDLED_MOD_FILENAME
        if target.is_file():
            source_stat = BUNDLED_MOD_PATH.stat()
            target_stat = target.stat()
            if (
                source_stat.st_size == target_stat.st_size
                and source_stat.st_mtime <= target_stat.st_mtime
            ):
                return True
        shutil.copy2(BUNDLED_MOD_PATH, target)
        return True
    except OSError:
        return False


def remove_performance_mod(game_directory: Path | str) -> bool:
    """Remove the bundled mod JAR when 1K FPS Mode is switched off."""
    try:
        (Path(game_directory) / "mods" / BUNDLED_MOD_FILENAME).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def performance_agent_argument(game_directory: Path | str) -> str | None:
    """Return the -javaagent argument for loaders that cannot run Fabric mods.

    The same JAR contains a Java agent entrypoint, so Vanilla, Forge, NeoForge,
    and OptiFine profiles get the preset without installing a mod loader.
    """
    if not BUNDLED_MOD_PATH.is_file():
        return None
    return f"-javaagent:{BUNDLED_MOD_PATH}={Path(game_directory).resolve()}"
