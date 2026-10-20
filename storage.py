import json
import os
import shutil
from pathlib import Path

import minecraft_launcher_lib

from colors import DEFAULT_THEME

LEGACY_DATA_DIR = Path(__file__).resolve().parent / ".data"


def get_data_dir() -> Path:
    """Returns the persistent launcher data directory inside .minecraft/gr8launcher across all OSes.
    Automatically migrates any legacy .data folder from the application directory on first run.
    """
    mc_dir = Path(minecraft_launcher_lib.utils.get_minecraft_directory())
    data_dir = mc_dir / "gr8launcher"
    data_dir.mkdir(parents=True, exist_ok=True)
    migrate_legacy_data(LEGACY_DATA_DIR, data_dir)
    return data_dir


def migrate_legacy_data(legacy_dir: Path, target_dir: Path) -> None:
    """Safely migrates files and directories from the legacy .data folder to target_dir.
    Moves files if target does not already have them, and removes legacy_dir once empty.
    """
    if not legacy_dir.is_dir():
        return
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for item in list(legacy_dir.iterdir()):
            dest = target_dir / item.name
            if item.is_file():
                if not dest.exists():
                    shutil.move(str(item), str(dest))
                else:
                    try:
                        item.unlink()
                    except OSError:
                        pass
            elif item.is_dir():
                if not dest.exists():
                    shutil.move(str(item), str(dest))
                else:
                    for subitem in list(item.iterdir()):
                        subdest = dest / subitem.name
                        if not subdest.exists():
                            shutil.move(str(subitem), str(subdest))
                        else:
                            try:
                                if subitem.is_file():
                                    subitem.unlink()
                                elif subitem.is_dir():
                                    shutil.rmtree(str(subitem), ignore_errors=True)
                            except OSError:
                                pass
                    try:
                        item.rmdir()
                    except OSError:
                        pass

        if legacy_dir.is_dir() and not any(legacy_dir.iterdir()):
            legacy_dir.rmdir()
    except Exception as error:
        print(f"Notice: Legacy data migration encountered an issue: {error}")


def get_account_pictures_dir() -> Path:
    directory = get_data_dir() / "account_pictures"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


DATA_DIR = get_data_dir()
THEME_FILE = DATA_DIR / "theme.txt"
INSTALLATIONS_FILE = DATA_DIR / "installations.json"
PROFILE_OPTIONS_FILE = DATA_DIR / "profile_n_options.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
PENDING_INSTALLATIONS_FILE = DATA_DIR / "pending_installations.json"
ACCOUNT_PICTURES_DIR = get_account_pictures_dir()
ACCOUNTS_FILE = DATA_DIR / "accounts.json"

DEFAULT_SETTINGS: dict[str, object] = {
    "ram_mb": 4096,
    "java_path": "",
    "window_width": 854,
    "window_height": 480,
    "fullscreen": False,
    "launch_action": "Hide launcher",
    "capture_logs": True,
    "enable_orbs": True,
    "enable_custom_skins": True,
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
        "enable_orbs",
        "enable_custom_skins",
    ):
        value = saved.get(key)
        if isinstance(value, bool):
            settings[key] = value

    if "active_skin" in saved and isinstance(saved["active_skin"], dict):
        settings["active_skin"] = saved["active_skin"]

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
                DATA_DIR / "player_name.txt"
            ).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            player_name = ""

    picture_path = options.get("profile_picture_path", "")
    if isinstance(picture_path, str) and picture_path:
        pic = Path(picture_path)
        if not pic.is_file():
            candidate = ACCOUNT_PICTURES_DIR / pic.name
            if candidate.is_file():
                picture_path = str(candidate)
    else:
        picture_path = ""

    return {
        "player_name": player_name if isinstance(player_name, str) else "",
        "last_selected_button": str(options.get("last_selected_button", "")),
        "profile_picture_path": picture_path,
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


# =============================================================================
# Account Storage and Management (Offline & Ely.by Accounts)
# =============================================================================

def load_accounts() -> dict[str, object]:
    """Loads accounts from accounts.json.
    If missing or invalid, initializes from legacy player_name and creates an offline account.
    """
    try:
        document = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        document = None

    if isinstance(document, dict) and isinstance(document.get("accounts"), list):
        accounts = [acc for acc in document["accounts"] if isinstance(acc, dict) and "id" in acc]
        active_id = document.get("active_account_id")
        if accounts:
            if not active_id or not any(acc.get("id") == active_id for acc in accounts):
                active_id = accounts[0]["id"]
            return {"active_account_id": active_id, "accounts": accounts}

    # Initialize from existing profile options
    current_name = load_profile_options().get("player_name", "").strip() or "Player"
    default_account = {
        "id": "offline_default",
        "type": "offline",
        "username": current_name,
        "created_at": 0,
    }
    initial_data = {
        "active_account_id": "offline_default",
        "accounts": [default_account],
    }
    save_accounts(initial_data)
    return initial_data


def save_accounts(accounts_data: dict[str, object]) -> bool:
    """Saves accounts data dictionary to accounts.json."""
    temporary_file = ACCOUNTS_FILE.with_suffix(".tmp")
    try:
        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file.write_text(json.dumps(accounts_data, indent=2), encoding="utf-8")
        temporary_file.replace(ACCOUNTS_FILE)
    except (OSError, TypeError, ValueError):
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def get_active_account() -> dict[str, object]:
    """Returns the currently active account dictionary."""
    data = load_accounts()
    active_id = data.get("active_account_id")
    accounts = data.get("accounts", [])
    for acc in accounts:
        if acc.get("id") == active_id:
            return acc
    if accounts:
        return accounts[0]
    return {"id": "offline_default", "type": "offline", "username": "Player"}


def set_active_account(account_id: str) -> bool:
    """Sets the active account by account ID and syncs legacy player_name."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    matched = None
    for acc in accounts:
        if acc.get("id") == account_id:
            matched = acc
            break
    if not matched:
        return False

    data["active_account_id"] = account_id
    success = save_accounts(data)
    if success:
        uname = matched.get("username", "")
        prof = load_profile_options()
        save_profile_options(uname, prof.get("last_selected_button", ""), prof.get("profile_picture_path", ""))
    return success


def add_or_update_account(account: dict[str, object], set_as_active: bool = True) -> bool:
    """Adds a new account or updates an existing one (matched by ID or Ely UUID)."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    acc_id = account.get("id")
    if not acc_id:
        return False

    updated = False
    for i, existing in enumerate(accounts):
        if existing.get("id") == acc_id or (
            existing.get("type") == "ely"
            and account.get("type") == "ely"
            and existing.get("uuid")
            and existing.get("uuid") == account.get("uuid")
        ):
            accounts[i] = account
            updated = True
            break

    if not updated:
        accounts.append(account)

    data["accounts"] = accounts
    if set_as_active:
        data["active_account_id"] = account.get("id")

    success = save_accounts(data)
    if success and set_as_active:
        prof = load_profile_options()
        save_profile_options(
            account.get("username", ""),
            prof.get("last_selected_button", ""),
            prof.get("profile_picture_path", ""),
        )
    return success


def remove_account(account_id: str) -> bool:
    """Removes an account. Preserves at least one offline account."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    if not any(acc.get("id") == account_id for acc in accounts):
        return False

    remaining = [acc for acc in accounts if acc.get("id") != account_id]
    if not remaining:
        remaining = [{"id": "offline_default", "type": "offline", "username": "Player"}]

    active_id = data.get("active_account_id")
    if active_id == account_id:
        active_id = remaining[0].get("id")

    data["active_account_id"] = active_id
    data["accounts"] = remaining
    success = save_accounts(data)
    if success:
        for acc in remaining:
            if acc.get("id") == active_id:
                prof = load_profile_options()
                save_profile_options(
                    acc.get("username", ""),
                    prof.get("last_selected_button", ""),
                    prof.get("profile_picture_path", ""),
                )
                break
    return success


def update_offline_username(username: str) -> bool:
    """Updates the offline account username."""
    clean_name = username.strip() or "Player"
    data = load_accounts()
    accounts = data.get("accounts", [])
    found = False
    for acc in accounts:
        if acc.get("type") == "offline":
            acc["username"] = clean_name
            found = True
            break
    if not found:
        accounts.append({"id": "offline_default", "type": "offline", "username": clean_name})

    data["accounts"] = accounts
    success = save_accounts(data)
    if success and get_active_account().get("type") == "offline":
        prof = load_profile_options()
        save_profile_options(
            clean_name,
            prof.get("last_selected_button", ""),
            prof.get("profile_picture_path", ""),
        )
    return success
