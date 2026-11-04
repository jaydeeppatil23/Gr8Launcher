"""Create profiles with exclusive instance folders and preserve existing data."""

import os
from pathlib import Path
import re
import threading


_CREATE_LOCK = threading.Lock()


def instance_folder_name(folder_name, default_name):
    """Use the same single directory name for suggestions, checks and creation."""
    raw = str(folder_name or "").strip()
    if raw and ("/" in raw or "\\" in raw):
        raise ValueError("Enter a folder name without a directory path.")
    name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", raw or str(default_name or "").strip())
    name = name.rstrip(".")
    if not name:
        raise ValueError("Enter a valid instance folder name other than '.' or '..'.")
    # Windows treats trailing dots and device names as aliases, rather than
    # ordinary directory names. Check the exact name mkdir will receive.
    devices = {"CON", "PRN", "AUX", "NUL"}
    devices.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10))
    if os.name == "nt" and name.split(".", 1)[0].upper() in devices:
        raise ValueError(f"'{name}' is reserved by Windows. Choose another folder name.")
    return name


def folder_is_referenced(folder, profiles, minecraft_directory, *, exclude_id=None):
    """Include explicit paths and older profiles that store only a folder name."""
    target = str(Path(folder).resolve()).casefold()
    root = Path(minecraft_directory).resolve()
    for profile in profiles:
        if exclude_id is not None and profile.get("id") == exclude_id:
            continue
        game_dir = str(profile.get("game_directory") or "").strip()
        if not game_dir and profile.get("is_instance") and profile.get("instance_name"):
            game_dir = str(root / "instances" / str(profile["instance_name"]))
        if game_dir and str(Path(game_dir).resolve()).casefold() == target:
            return True
    return False


def new_instance_directory(minecraft_directory, folder_name, default_name, profiles):
    """Resolve a fresh destination, including the fallback for an empty field."""
    name = instance_folder_name(folder_name, default_name)
    root = (Path(minecraft_directory) / "instances").resolve()
    folder = root / name
    if os.path.lexists(folder) or folder_is_referenced(folder, profiles, minecraft_directory):
        raise FileExistsError(
            f"An instance with folder '{name}' already exists. Choose a different folder name."
        )
    if folder.resolve().parent != root:
        raise ValueError("The instance folder must be inside the instances directory.")
    return folder


def save_new_profile(profile, minecraft_directory, folder_name, storage):
    """Reserve a new folder atomically before saving or selecting the profile.

    An existing directory is never adopted. If saving fails, only the empty
    directory created by this call may be removed; its contents are never deleted.
    """
    with _CREATE_LOCK:
        profiles = storage.load_installations()
        if any(item.get("id") == profile.get("id") for item in profiles):
            raise ValueError("A profile with this ID already exists.")
        record = dict(profile)
        created_folder = None
        if record.get("is_instance"):
            folder = new_instance_directory(
                minecraft_directory, folder_name, record.get("name"), profiles,
            )
            folder.parent.mkdir(parents=True, exist_ok=True)
            try:
                folder.mkdir(exist_ok=False)
            except FileExistsError as error:
                raise FileExistsError(
                    f"An instance with folder '{folder.name}' already exists. Choose a different folder name."
                ) from error
            created_folder = folder
            record["instance_name"] = folder.name
            record["game_directory"] = str(folder)
        else:
            record["instance_name"] = ""
            record["game_directory"] = str(Path(minecraft_directory).resolve())

        try:
            if not storage.save_installations([*profiles, record]):
                raise OSError("Could not save the profile. Please try again.")
        except Exception:
            if created_folder is not None:
                try:
                    created_folder.rmdir()
                except OSError:
                    # Another process may already have put files here. Keep them.
                    pass
            raise
        return record
