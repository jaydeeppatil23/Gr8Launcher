"""
State management module for Gr8 Launcher.
Holds shared runtime state to avoid closure-nesting and prevent control de-synchronization.
"""

from typing import Any, Dict

# Central launch and logging state
launcher_log_state: Dict[str, Any] = {
    "lines": [],
    "list": None,
    "open": False,
    "running": False,
    "downloading": False,
    "game_started": False,
    "cancelling": False,
    "cancel_event": None,
    "play_button": None,
    "refresh_play_button": None,
    "target_version_id": None,
    "target_profile_id": None,
    "status": None,
    "progress": 0,
    "eta": "",
    "status_control": None,
    "progress_control": None,
    "eta_control": None,
    "white_beam": None,
    "set_track_progress": None,
    "floating_logs_window": None,
    "bottom_log_toggle_button": None,
    "animation_running": False,
    "new_installation_button": None,
}

# Navigation and page state
active_page: Dict[str, Any] = {
    "name": "home",
    "profile_id": None,
}

navigation_state: Dict[str, Any] = {
    "navigate": None,
}

notification_state: Dict[str, Any] = {
    "show": None,
    "pending": None,
    "epoch": 0,
}

selected_theme: Dict[str, Any] = {
    "name": "Dark",
}

refresh_state: Dict[str, Any] = {
    "running": False,
}

connectivity_known: Dict[str, Any] = {
    "value": False,
}

intro_state: Dict[str, Any] = {
    "pending": True,
    "running": False,
    "targets": [],
}

options_background_state: Dict[str, Any] = {
    "animation_running": False,
}

danger_zone_state: Dict[str, Any] = {
    "expanded": False,
}

player_name_state: Dict[str, Any] = {
    "value": "Player",
}

profile_picture_state: Dict[str, Any] = {
    "path": "",
}

launch_selection: Dict[str, Any] = {
    "key": None,
}
