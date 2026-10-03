import json
from pathlib import Path

from colors import DEFAULT_THEME


THEME_FILE = Path(__file__).resolve().parent / ".data" / "theme.txt"
INSTALLATIONS_FILE = Path(__file__).resolve().parent / ".data" / "installations.json"
PROFILE_OPTIONS_FILE = Path(__file__).resolve().parent / ".data" / "profile_n_options.json"
SETTINGS_FILE = Path(__file__).resolve().parent / ".data" / "settings.json"
PENDING_INSTALLATIONS_FILE = Path(__file__).resolve().parent / ".data" / "pending_installations.json"

DEFAULT_SETTINGS: dict[str, object] = {
    "ram_mb": 4096,
    "java_path": "",
    "window_width": 854,
    "window_height": 480,
    "fullscreen": False,
    "launch_action": "Hide launcher",
    "capture_logs": True,
    "show_installation_directory": True,
    "enable_orbs": True,
}


def load_settings() -> dict[str, object]:
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return DEFAULT_SETTINGS.copy()

    if not isinstance(saved, dict):
        return DEFAULT_SETTINGS.copy()

    settings = DEFAULT_SETTINGS.copy()
    for key in ("ram_mb", "window_width", "window_height"):
        value = saved.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            settings[key] = value
    for key in ("java_path", "launch_action"):
        value = saved.get(key)
        if isinstance(value, str):
            settings[key] = value
    for key in (
        "fullscreen",
        "capture_logs",
        "show_installation_directory",
        "enable_orbs",
    ):
        value = saved.get(key)
        if isinstance(value, bool):
            settings[key] = value

    if settings["launch_action"] not in (
        "Hide launcher",
        "Keep launcher open",
        "Close launcher",
    ):
        settings["launch_action"] = DEFAULT_SETTINGS["launch_action"]
    return settings


def save_settings(settings: dict[str, object]) -> bool:
    temporary_file = SETTINGS_FILE.with_suffix(".tmp")
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        temporary_file.replace(SETTINGS_FILE)
    except (OSError, TypeError, ValueError):
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def load_theme_choice(available_themes: list[str]) -> str:
    fallback = (
        DEFAULT_THEME if DEFAULT_THEME in available_themes else available_themes[0]
    )
    try:
        saved_theme = THEME_FILE.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return fallback

    return saved_theme if saved_theme in available_themes else fallback


def save_theme_choice(theme_name: str) -> None:
    try:
        THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
        THEME_FILE.write_text(theme_name, encoding="utf-8")
    except OSError as error:
        print(f"Could not save selected theme: {error}")


def load_installations() -> list[dict[str, object]]:
    try:
        document = json.loads(INSTALLATIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []

    if not isinstance(document, dict):
        return []

    records = document.get("installations")
    if not isinstance(records, list):
        return []

    required_fields = ("id", "name", "loader", "minecraft_version")
    return [
        record
        for record in records
        if isinstance(record, dict)
        and all(isinstance(record.get(field), str) for field in required_fields)
    ]


def load_handled_external_profile_ids() -> set[str]:
    try:
        document = json.loads(INSTALLATIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()

    profile_ids = document.get("handled_external_profile_ids", [])
    if not isinstance(profile_ids, list):
        return set()
    return {profile_id for profile_id in profile_ids if isinstance(profile_id, str)}


def load_latest_release_id() -> str | None:
    try:
        document = json.loads(INSTALLATIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    if not isinstance(document, dict):
        return None
    latest_release_id = document.get("latest_release_id")
    return latest_release_id if isinstance(latest_release_id, str) and latest_release_id else None


def save_installations(
    installations: list[dict[str, object]],
    handled_external_profile_ids: set[str] | None = None,
    latest_release_id: str | None = None,
) -> bool:
    temporary_file = INSTALLATIONS_FILE.with_suffix(".tmp")
    if handled_external_profile_ids is None:
        handled_external_profile_ids = load_handled_external_profile_ids()
    if latest_release_id is None:
        latest_release_id = load_latest_release_id()
    document = {
        "schema_version": 1,
        "installations": installations,
        "handled_external_profile_ids": sorted(handled_external_profile_ids),
        "latest_release_id": latest_release_id,
    }
    try:
        INSTALLATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file.write_text(
            json.dumps(document, indent=2),
            encoding="utf-8",
        )
        temporary_file.replace(INSTALLATIONS_FILE)
    except (OSError, TypeError, ValueError):
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def load_profile_options() -> dict[str, str]:
    try:
        options = json.loads(PROFILE_OPTIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "player_name": "",
            "last_selected_button": "",
            "profile_picture_path": "",
        }

    if not isinstance(options, dict):
        return {
            "player_name": "",
            "last_selected_button": "",
            "profile_picture_path": "",
        }

    player_name = options.get("player_name", "")
    if not isinstance(player_name, str) or not player_name:
        try:
            player_name = (
                Path(__file__).resolve().parent / ".data" / "player_name.txt"
            ).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            player_name = ""

    return {
        "player_name": player_name if isinstance(player_name, str) else "",
        "last_selected_button": str(options.get("last_selected_button", "")),
        "profile_picture_path": (
            options.get("profile_picture_path", "")
            if isinstance(options.get("profile_picture_path", ""), str)
            else ""
        ),
    }


def save_profile_options(
    player_name: str,
    last_selected_button: str,
    profile_picture_path: str = "",
) -> bool:
    temporary_file = PROFILE_OPTIONS_FILE.with_suffix(".tmp")
    document = {
        "player_name": player_name,
        "last_selected_button": last_selected_button,
        "profile_picture_path": profile_picture_path,
    }
    try:
        PROFILE_OPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file.write_text(json.dumps(document, indent=2), encoding="utf-8")
        temporary_file.replace(PROFILE_OPTIONS_FILE)
    except OSError:
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def load_pending_installations() -> set[str]:
    try:
        document = json.loads(PENDING_INSTALLATIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()

    pending = document.get("pending", []) if isinstance(document, dict) else []
    if not isinstance(pending, list):
        return set()
    return {version_id for version_id in pending if isinstance(version_id, str)}


def save_pending_installations(version_ids: set[str]) -> bool:
    temporary_file = PENDING_INSTALLATIONS_FILE.with_suffix(".tmp")
    try:
        PENDING_INSTALLATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file.write_text(
            json.dumps({"pending": sorted(version_ids)}, indent=2),
            encoding="utf-8",
        )
        temporary_file.replace(PENDING_INSTALLATIONS_FILE)
    except (OSError, TypeError, ValueError):
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True