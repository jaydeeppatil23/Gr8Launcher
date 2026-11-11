import json
import os
import shutil
import tempfile
import threading
import uuid
from functools import wraps
from pathlib import Path

import minecraft_launcher_lib

from credential_store import CredentialStore, _tokens

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
INSTALLATIONS_FILE = DATA_DIR / "installations.json"
PROFILE_ORDER_FILE = DATA_DIR / "profile_order.json"
PROFILE_OPTIONS_FILE = DATA_DIR / "profile_n_options.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
PENDING_INSTALLATIONS_FILE = DATA_DIR / "pending_installations.json"
ACCOUNT_PICTURES_DIR = get_account_pictures_dir()
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
LAUNCHER_PROFILES_CACHE_FILE = DATA_DIR / "launcher_profiles_cache.json"

DEFAULT_SETTINGS: dict[str, object] = {
    "ram_mb": 4096,
    "java_path": "",
    "window_width": 854,
    "window_height": 480,
    "fullscreen": False,
    "launch_action": "Hide launcher",
    "capture_logs": True,
    "enable_orbs": True,
    "enable_head_tracking": True,
    "enable_custom_skins": True,
    "discord_rpc": True,
    "check_updates_automatically": True,
    "download_updates_automatically": False,
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
        "enable_head_tracking",
        "enable_custom_skins",
        "discord_rpc",
        "check_updates_automatically",
        "download_updates_automatically",
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


def load_profile_order() -> list[str]:
    try:
        profile_ids = json.loads(PROFILE_ORDER_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(profile_ids, list):
        return []
    return list(dict.fromkeys(
        profile_id for profile_id in profile_ids
        if isinstance(profile_id, str) and profile_id
    ))


def save_profile_order(profile_ids: list[str]) -> bool:
    temporary_file = PROFILE_ORDER_FILE.with_suffix(".tmp")
    try:
        PROFILE_ORDER_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file.write_text(json.dumps(profile_ids, indent=2), encoding="utf-8")
        temporary_file.replace(PROFILE_ORDER_FILE)
    except (OSError, TypeError, ValueError):
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def sort_profiles(
    profiles: list[dict[str, object]],
    profile_order: list[str] | None = None,
) -> list[dict[str, object]]:
    if profile_order is None:
        profile_order = load_profile_order()
    positions = {profile_id: index for index, profile_id in enumerate(profile_order)}
    return sorted(
        profiles,
        key=lambda profile: positions.get(profile.get("id"), len(positions)),
    )


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
    return sort_profiles([
        record
        for record in records
        if isinstance(record, dict)
        and all(isinstance(record.get(field), str) for field in required_fields)
    ])


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


def load_latest_release_options() -> dict[str, str]:
    try:
        document = json.loads(INSTALLATIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}

    if not isinstance(document, dict):
        return {}
    options = document.get("latest_release_options")
    if not isinstance(options, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in options.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def save_latest_release_options(options: dict[str, str]) -> bool:
    return save_installations(
        load_installations(),
        latest_release_options=options,
    )


def save_installations(
    installations: list[dict[str, object]],
    handled_external_profile_ids: set[str] | None = None,
    latest_release_id: str | None = None,
    latest_release_options: dict[str, str] | None = None,
) -> bool:
    temporary_file = INSTALLATIONS_FILE.with_suffix(".tmp")
    if handled_external_profile_ids is None:
        handled_external_profile_ids = load_handled_external_profile_ids()
    if latest_release_id is None:
        latest_release_id = load_latest_release_id()
    if latest_release_options is None:
        latest_release_options = load_latest_release_options()
    document = {
        "schema_version": 1,
        "installations": installations,
        "handled_external_profile_ids": sorted(handled_external_profile_ids),
        "latest_release_id": latest_release_id,
        "latest_release_options": latest_release_options,
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


def load_cached_launcher_profiles() -> dict[str, object]:
    """Loads the last saved snapshot of profiles from launcher_profiles.json."""
    try:
        data = json.loads(LAUNCHER_PROFILES_CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def save_cached_launcher_profiles(profiles_data: dict[str, object]) -> bool:
    """Saves the current snapshot of profiles from launcher_profiles.json to our gr8launcher folder."""
    temporary_file = LAUNCHER_PROFILES_CACHE_FILE.with_suffix(".tmp")
    try:
        LAUNCHER_PROFILES_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file.write_text(
            json.dumps(profiles_data, indent=2),
            encoding="utf-8",
        )
        temporary_file.replace(LAUNCHER_PROFILES_CACHE_FILE)
        return True
    except (OSError, TypeError, ValueError):
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# =============================================================================
# Account Storage and Management (Offline & Ely.by Accounts)
# =============================================================================

_ACCOUNTS_LOCK = threading.RLock()
_CREDENTIAL_STORES: dict[str, CredentialStore] = {}
_ACCOUNT_FIELDS = ("id", "type", "username", "email", "uuid", "created_at", "credential_id")


def _account_transaction(function):
    @wraps(function)
    def transaction(*args, **kwargs):
        with _ACCOUNTS_LOCK:
            return function(*args, **kwargs)
    return transaction


def _credential_store() -> CredentialStore:
    namespace = os.path.normcase(str(ACCOUNTS_FILE.resolve()))
    if namespace not in _CREDENTIAL_STORES:
        _CREDENTIAL_STORES[namespace] = CredentialStore(namespace)
    return _CREDENTIAL_STORES[namespace]


def _account_metadata(account) -> dict | None:
    if not isinstance(account, dict) or not isinstance(account.get("id"), str) or not account["id"]:
        return None
    # An allowlist keeps passwords, tokens, and future API response fields out
    # of both the JSON document and its temporary files.
    result = {name: account[name] for name in _ACCOUNT_FIELDS if name in account}
    result["type"] = "ely" if result.get("type") == "ely" else "offline"
    name = result.get("username")
    result["username"] = name.strip() or "Player" if isinstance(name, str) else "Player"
    for name in ("email", "uuid"):
        if name in result and not isinstance(result[name], str):
            result.pop(name)
    if not isinstance(result.get("created_at", 0), (int, float)):
        result.pop("created_at", None)
    reference = result.get("credential_id")
    if (result["type"] != "ely" or not isinstance(reference, str)
            or len(reference) != 32 or any(c not in "0123456789abcdef" for c in reference)):
        result.pop("credential_id", None)
    return result


def _read_account_document():
    try:
        return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _default_account() -> dict:
    name = load_profile_options().get("player_name", "")
    return {
        "id": "offline_default", "type": "offline",
        "username": name.strip() or "Player" if isinstance(name, str) else "Player",
        "created_at": 0,
    }


def _hydrate_account(account: dict) -> dict:
    result = account.copy()
    if result.get("type") == "ely":
        reference = result.get("credential_id")
        result.update(_credential_store().get(reference) if reference else {})
    return result


@_account_transaction
def get_account_storage_status(account: dict) -> str:
    """Inspect cached session status without opening or unlocking a vault."""
    if account.get("type") != "ely":
        return "offline"
    reference = account.get("credential_id")
    return _credential_store().status(reference) if reference else "missing"


def load_accounts(include_credentials: bool = False) -> dict[str, object]:
    """Load metadata, migrating legacy plaintext sessions on first use.

    UI callers do not need secrets. Launch/logout can request credentials;
    missing or locked credential storage simply returns an unsigned session.
    """
    with _ACCOUNTS_LOCK:
        document = _read_account_document()
        accounts = []
        legacy_tokens = {}
        seen_ids = set()
        seen_uuids = set()
        if isinstance(document, dict) and isinstance(document.get("accounts"), list):
            for raw in document["accounts"]:
                account = _account_metadata(raw)
                if account is None or account["id"] in seen_ids:
                    continue
                ely_uuid = account.get("uuid", "").replace("-", "").lower() if account["type"] == "ely" else ""
                if ely_uuid and ely_uuid in seen_uuids:
                    continue
                seen_ids.add(account["id"])
                if ely_uuid:
                    seen_uuids.add(ely_uuid)
                accounts.append(account)
                if account["type"] == "ely" and _tokens(raw):
                    legacy_tokens[account["id"]] = _tokens(raw)

        if not accounts:
            accounts = [_default_account()]
        active_id = document.get("active_account_id") if isinstance(document, dict) else None
        if not any(account["id"] == active_id for account in accounts):
            active_id = accounts[0]["id"]
        data = {"active_account_id": active_id, "accounts": accounts}
        canonical = {"schema_version": 2, **data}
        if document != canonical:
            migration = {
                "active_account_id": active_id,
                "accounts": [account | legacy_tokens.get(account["id"], {}) for account in accounts],
            }
            if save_accounts(migration):
                return load_accounts(include_credentials)
            # A read-only data directory must not erase a still usable session.
            # The original file stays intact and migration is retried later.
            if include_credentials:
                data["accounts"] = [
                    _hydrate_account(account) | legacy_tokens.get(account["id"], {})
                    for account in accounts
                ]
            return data

        if include_credentials:
            data["accounts"] = [_hydrate_account(account) for account in accounts]
        return data


def save_accounts(accounts_data: dict[str, object]) -> bool:
    """Atomically save metadata; tokens go only to a vault or session memory."""
    with _ACCOUNTS_LOCK:
        if not isinstance(accounts_data, dict) or not isinstance(accounts_data.get("accounts"), list):
            return False
        accounts = []
        pending_sessions = {}
        for raw in accounts_data["accounts"]:
            account = _account_metadata(raw)
            if account is None or any(item["id"] == account["id"] for item in accounts):
                return False
            tokens = _tokens(raw) if account["type"] == "ely" else {}
            if tokens:
                # New references prevent failed deletion or a later re-login
                # from accidentally restoring a removed session.
                reference = uuid.uuid4().hex
                account["credential_id"] = reference
                pending_sessions[reference] = tokens
            accounts.append(account)
        if not accounts:
            accounts = [_default_account()]
        active_id = accounts_data.get("active_account_id")
        if not any(account["id"] == active_id for account in accounts):
            active_id = accounts[0]["id"]
        document = {"schema_version": 2, "active_account_id": active_id, "accounts": accounts}
        previous = _read_account_document()
        old_references = {
            account["credential_id"]
            for raw in (previous.get("accounts", []) if isinstance(previous, dict) and isinstance(previous.get("accounts"), list) else [])
            if (account := _account_metadata(raw)) and account.get("credential_id")
        }
        temporary_file = None
        try:
            payload = json.dumps(document, indent=2)
            ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=ACCOUNTS_FILE.parent,
                prefix=".accounts-", suffix=".tmp", delete=False,
            ) as stream:
                temporary_file = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            for reference, tokens in pending_sessions.items():
                _credential_store().put(reference, tokens)
            temporary_file.replace(ACCOUNTS_FILE)
        except (OSError, TypeError, ValueError):
            for reference in pending_sessions:
                _credential_store().delete(reference)
            if temporary_file is not None:
                try:
                    temporary_file.unlink(missing_ok=True)
                except OSError:
                    pass
            return False
        new_references = {account["credential_id"] for account in accounts if account.get("credential_id")}
        for reference in old_references - new_references:
            _credential_store().delete(reference)
        # Older releases used accounts.tmp and could leave plaintext behind.
        try:
            ACCOUNTS_FILE.with_suffix(".tmp").unlink(missing_ok=True)
        except OSError:
            pass
        return True


def get_active_account(include_credentials: bool = False) -> dict[str, object]:
    """Returns the currently active account dictionary."""
    data = load_accounts(include_credentials)
    active_id = data.get("active_account_id")
    accounts = data.get("accounts", [])
    for acc in accounts:
        if acc.get("id") == active_id:
            return acc
    if accounts:
        return accounts[0]
    return {"id": "offline_default", "type": "offline", "username": "Player"}


@_account_transaction
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


@_account_transaction
def add_or_update_account(account: dict[str, object], set_as_active: bool = True) -> bool:
    """Adds a new account or updates an existing one (matched by ID or Ely UUID)."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    if _account_metadata(account) is None:
        return False
    account = account.copy()
    acc_id = account["id"]

    updated = False
    for i, existing in enumerate(accounts):
        if existing.get("id") == acc_id or (
            existing.get("type") == "ely"
            and account.get("type") == "ely"
            and existing.get("uuid")
            and str(existing.get("uuid")).replace("-", "").lower() == str(account.get("uuid")).replace("-", "").lower()
        ):
            account = existing | account
            account["id"] = existing["id"]
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


@_account_transaction
def remove_account(account_id: str) -> bool:
    """Removes an account. Preserves at least one offline account."""
    data = load_accounts()
    accounts = data.get("accounts", [])
    if not any(acc.get("id") == account_id for acc in accounts):
        return False

    remaining = [acc for acc in accounts if acc.get("id") != account_id]
    if not remaining:
        remaining = [{"id": "offline_default", "type": "offline", "username": "Player", "created_at": 0}]

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


@_account_transaction
def update_account_session(account_id: str, previous_token: str, refreshed: dict) -> bool:
    """Refresh an existing session without re-adding or selecting an account.

    An account may have been removed or signed in again while the HTTP request
    was running. Only the session that initiated that request can be updated.
    """
    if not previous_token or not _tokens(refreshed):
        return False
    data = load_accounts()
    for account in data["accounts"]:
        if account["id"] != account_id or account["type"] != "ely":
            continue
        current = _hydrate_account(account)
        if current.get("access_token") != previous_token:
            return False
        account.update(_tokens(current) | _tokens(refreshed))
        for field in ("username", "uuid"):
            if isinstance(refreshed.get(field), str) and refreshed[field]:
                account[field] = refreshed[field]
        return save_accounts(data)
    return False
