import asyncio
import json
import os
import random
import re
import socket
import shutil
import subprocess
import sys
import threading
import time
import uuid
import warnings
import webbrowser
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)

import flet as ft
from colors import get_theme, get_theme_names, get_pagecolor, DEFAULT_THEME
from launcher import (
    discover_local_instances,
    get_external_installations,
    get_installed_versions,
    invalidate_installed_versions_cache,
    get_local_version_catalog,
    get_mod_loader,
    get_version_catalog,
    DownloadCancelled,
    import_external_installations,
    minecraft_directory,
    get_system_memory_mb,
    remove_installed_version,
    start_game,
    estimate_installation_total,
    InstallationProgressTracker,
)
from storage import (
    DEFAULT_SETTINGS,
    load_handled_external_profile_ids,
    load_installations,
    load_latest_release_id,
    load_profile_options,
    load_settings,
    load_theme_choice,
    save_installations,
    save_profile_options,
    save_settings,
    save_theme_choice,
)

ASSETS_DIR = Path(__file__).resolve().parent / ".assets"
THOUGHTS_FILE = ASSETS_DIR / "thoughts.json"

DEFAULT_THOUGHTS = [
    "Don't dig straight down in Minecraft or in life; pause, look ahead, and choose a safer next step.",
    "Every great Minecraft build begins with one block, and every big change begins with one small action.",
    "Keep exploring: the diamonds you find in a cave are a reminder that hard seasons can still hold hope.",
    "Place a torch where it's dark, and offer a little kindness where someone needs encouragement.",
    "Your Minecraft inventory has limits; make room in life for what matters most.",
    "A world is built one block at a time, and a good life is shaped by small choices made with care.",
    "It's okay to wander off the map; unexpected paths can lead to your favorite discoveries.",
]


def load_thoughts() -> list[str]:
    if THOUGHTS_FILE.is_file():
        try:
            with open(THOUGHTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    parsed = [str(item).strip() for item in data if str(item).strip()]
                    if parsed:
                        return parsed
        except Exception:
            pass
    return DEFAULT_THOUGHTS


def get_random_thought() -> str:
    thoughts = load_thoughts()
    return random.choice(thoughts) if thoughts else DEFAULT_THOUGHTS[0]


def main(page: ft.Page):
    page.window.icon = str(ASSETS_DIR / "icons" / "gr8_icon_r_256.ico")
    available_themes = get_theme_names()
    installations = load_installations()
    profile_options = load_profile_options()
    settings = load_settings()
    maximum_memory_mb = get_system_memory_mb()
    settings["ram_mb"] = min(
        maximum_memory_mb, max(256, int(settings.get("ram_mb", 4096)))
    )
    player_name_state = {"value": profile_options["player_name"]}
    launch_selection = {"key": profile_options["last_selected_button"]}
    profile_pictures_directory = (
        Path(__file__).resolve().parent / ".data" / "account_pictures"
    )
    saved_picture_path = profile_options["profile_picture_path"]
    profile_picture_state = {
        "path": saved_picture_path if saved_picture_path and Path(saved_picture_path).is_file() else ""
    }
    version_catalog, local_latest_release = get_local_version_catalog()
    latest_release_id = load_latest_release_id() or local_latest_release
    online_available = False
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=0.25):
            online_available = True
    except OSError:
        online_available = False
    handled_external_profile_ids = load_handled_external_profile_ids()
    installations, handled_external_profile_ids, imports_changed = (
        import_external_installations(
            installations,
            handled_external_profile_ids,
            get_installed_versions(),
            get_external_installations(),
            version_catalog,
        )
    )
    installations, discovered_changed = discover_local_instances(
        minecraft_directory,
        installations,
        get_installed_versions(),
        version_catalog,
    )
    if (imports_changed or discovered_changed) and not save_installations(
        installations, handled_external_profile_ids, latest_release_id
    ):
        installations = load_installations()
        handled_external_profile_ids = load_handled_external_profile_ids()

    theme_choice = load_theme_choice(available_themes)
    theme = get_theme(theme_choice)
    selected_theme = {"name": theme_choice}
    active_page = {"name": "home"}
    refresh_state = {"running": False}
    connectivity_known = {"value": False}
    navigation_state = {"navigate": None}
    notification_state = {"show": None, "pending": None, "epoch": 0}
    # Startup ripple: controls registered here start hidden and pop in one by one.
    intro_state = {"pending": True, "running": False, "targets": []}
    options_background_state = {"animation_running": False}
    danger_zone_state = {"expanded": False}
    launcher_log_state = {
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
        "animation_running": False,
        "new_installation_button": None,
    }
    page.title = "Gr8 Launcher"
    page.window.width = 1000
    page.window.height = 650
    page.window.min_width = 900
    page.window.min_height = 600
    page.window.resizable = True
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.padding = 0
    page.spacing = 0

    page.window.prevent_close = False
    page.window.title_bar_hidden = True
    page.fonts = {"mojangles": str(ASSETS_DIR / "font" / "mojangles-v2.otf")}
    account_picture_picker = ft.FilePicker()
    page.services.append(account_picture_picker)
    clipboard_service = ft.Clipboard()
    page.services.append(clipboard_service)

    # imp functions----

    def minimize_window(e):
        page.window.minimized = True
        page.update()

    def maximize_window(e):
        page.window.maximized = not page.window.maximized
        page.update()

    def close_window(e):
        page.run_task(page.window.close)

    def notify(message, icon=ft.Icons.INFO_OUTLINE):
        notification_state["pending"] = (message, icon)
        show_notification = notification_state.get("show")
        if show_notification is not None:
            show_notification()

    def persist_setting(key, value):
        settings[key] = value
        save_settings(settings)

    def change_theme(e):
        new_choice = e.control.value
        if new_choice not in available_themes:
            return

        save_theme_choice(new_choice)
        selected_theme["name"] = new_choice
        page.controls.clear()
        build_page(new_choice, active_page.get("name", "options"))

    def get_themes_options() -> list[ft.DropdownOption]:
        return [
            ft.DropdownOption(
                key=theme,
                text=theme,
            )
            for theme in available_themes
        ]


    TAB_BTN_WIDTH = 58
    TAB_BTN_HEIGHT = 38
    TAB_GAP = 4
    TAB_PITCH = TAB_BTN_WIDTH + TAB_GAP

    class NavigationTabs(ft.Container):
        def __init__(self, tab_names, initial_index, on_tab_select, app_theme, tooltip_func):
            self.tab_names = tab_names
            self._selected_index = initial_index
            self.app_theme = app_theme

            self.tab_highlight = ft.Container(
                left=initial_index * TAB_PITCH,
                top=0,
                width=TAB_BTN_WIDTH,
                height=TAB_BTN_HEIGHT,
                border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.18, app_theme["btn_primary"]),
                animate_position=ft.Animation(200, ft.AnimationCurve.FAST_OUT_SLOWIN),
            )

            tab_defs = [
                ("home", ft.Icons.HOME, "Home"),
                ("installations", ft.Icons.FILE_DOWNLOAD_OUTLINED, "Profiles"),
                ("options", ft.Icons.TUNE, "Options"),
            ]

            self.buttons = []
            for i, (t_name, t_icon, t_tip) in enumerate(tab_defs):
                is_active = (i == initial_index)
                btn = ft.IconButton(
                    icon=t_icon,
                    icon_size=23,
                    width=TAB_BTN_WIDTH,
                    height=TAB_BTN_HEIGHT,
                    icon_color=(
                        app_theme["btn_primary"]
                        if is_active
                        else app_theme["text_secondary"]
                    ),
                    tooltip=tooltip_func(t_tip),
                    hover_color=ft.Colors.with_opacity(0.06, app_theme["btn_primary"]),
                    style=ft.ButtonStyle(
                        shape=ft.RoundedRectangleBorder(radius=8),
                        overlay_color=ft.Colors.TRANSPARENT,
                    ),
                    on_click=lambda e, name=t_name: on_tab_select(name),
                )
                self.buttons.append(btn)

            inner_stack = ft.Stack(
                controls=[
                    self.tab_highlight,
                    ft.Row(
                        controls=self.buttons,
                        spacing=TAB_GAP,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                width=len(tab_names) * TAB_BTN_WIDTH + (len(tab_names) - 1) * TAB_GAP,
                height=TAB_BTN_HEIGHT,
            )

            super().__init__(
                content=inner_stack,
                height=TAB_BTN_HEIGHT + 6,
                border_radius=11,
                bgcolor=ft.Colors.with_opacity(0.16, app_theme["surface"]),
                padding=3,
            )

        @property
        def selected_index(self):
            return self._selected_index

        @selected_index.setter
        def selected_index(self, new_index):
            if not (0 <= new_index < len(self.tab_names)):
                return
            self._selected_index = new_index
            self.tab_highlight.left = new_index * TAB_PITCH
            for i, btn in enumerate(self.buttons):
                btn.icon_color = (
                    self.app_theme["btn_primary"]
                    if i == new_index
                    else self.app_theme["text_secondary"]
                )
                try:
                    btn.update()
                except Exception:
                    pass
            try:
                self.tab_highlight.update()
            except Exception:
                pass

    def build_page(theme_select, tab="home"):
        theme = get_theme(theme_select)
        page.bgcolor = get_pagecolor()
        rounded_button_style = ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))

        def build_themed_switch(label: str, value: bool, on_change=None):
            return ft.Switch(
                label=label,
                value=value,
                active_color=theme["btn_primary"],
                active_track_color=ft.Colors.with_opacity(0.38, theme["btn_primary"]),
                inactive_thumb_color=theme["text_secondary"],
                inactive_track_color=theme["surface"],
                track_outline_color=ft.Colors.with_opacity(0.4, theme["text_secondary"]),
                on_change=on_change,
            )

        dialog_blur_overlay = ft.Container(
            left=0,
            top=0,
            right=0,
            bottom=0,
            expand=True,
            blur=ft.Blur(4, 4),
            bgcolor=ft.Colors.with_opacity(0.35, "#000000"),
            visible=False,
            animate_opacity=ft.Animation(160, ft.AnimationCurve.EASE_OUT),
        )
        page.overlay.clear()
        page.overlay.append(dialog_blur_overlay)

        def show_dialog_with_blur(dlg: ft.AlertDialog):
            dialog_blur_overlay.visible = True
            page.show_dialog(dlg)
            page.update()

        def hide_dialog_blur(e=None):
            if dialog_blur_overlay.visible:
                dialog_blur_overlay.visible = False
                page.update()

        def close_dialog_with_blur(dlg: ft.AlertDialog):
            dlg.open = False
            hide_dialog_blur()

        def build_themed_alert_dialog(
            title: ft.Control,
            content: ft.Control,
            actions: list[ft.Control],
            modal: bool = True,
            on_dismiss=None,
        ) -> ft.AlertDialog:
            def handle_dismiss(e):
                hide_dialog_blur()
                if on_dismiss:
                    on_dismiss(e)

            return ft.AlertDialog(
                modal=modal,
                title=title,
                content=content,
                actions=actions,
                bgcolor=theme["surface"],
                elevation=0,
                shape=ft.RoundedRectangleBorder(radius=14),
                barrier_color=ft.Colors.TRANSPARENT,
                on_dismiss=handle_dismiss,
            )

        def tooltip(msg):
            return ft.Tooltip(
                message=msg,
                bgcolor=theme["card"],
                text_style=ft.TextStyle(color=theme["text_primary"], size=12),
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                wait_duration=350,
            )

        def copy_to_clipboard(text: str, message: str = "Copied to clipboard!"):
            try:
                page.run_task(clipboard_service.set, text)
            except Exception:
                pass
            if os.name == "nt":
                try:
                    p = subprocess.Popen(
                        ["clip"],
                        stdin=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    p.communicate(input=text.encode("utf-16le"))
                except Exception:
                    pass
            notify(message, ft.Icons.CHECK_CIRCLE_OUTLINE)

        def open_url(url: str, message: str = ""):
            try:
                if hasattr(os, "startfile"):
                    os.startfile(url)
                else:
                    webbrowser.open(url)
            except Exception:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            if message:
                notify(message, ft.Icons.OPEN_IN_NEW)

        func_buttons = [
            ft.IconButton(
                icon=ft.Icons.HORIZONTAL_RULE,
                on_click=minimize_window,
                icon_color=theme["text_primary"],
                hover_color=ft.Colors.with_opacity(
                    0.14, theme["btn_primary"]
                ),
                icon_size=17,
                style=rounded_button_style,
            ),
            ft.IconButton(
                icon=ft.Icons.CROP_SQUARE,
                on_click=maximize_window,
                icon_color=theme["text_primary"],
                hover_color=ft.Colors.with_opacity(
                    0.14, theme["btn_primary"]
                ),
                icon_size=17,
                style=rounded_button_style,
            ),
            ft.IconButton(
                icon=ft.Icons.CLOSE,
                on_click=close_window,
                icon_color=theme["btn_primary"],
                hover_color=ft.Colors.with_opacity(
                    0.14, theme["btn_primary"]
                ),
                icon_size=21,
                style=rounded_button_style,
            ),
        ]

        theme_dropdown = ft.Dropdown(
            label="Choose a Theme",
            value=theme_select,
            label_style=ft.TextStyle(size=13, color=theme["text_secondary"]),
            on_select=change_theme,
            options=get_themes_options(),
            border=ft.OutlineInputBorder(side=ft.BorderSide(color=theme["border"])),
        )

        notification_text = ft.Text(
            size=13,
            color=theme["text_primary"],
            expand=True,
            max_lines=3,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        notification_icon = ft.Icon(
            ft.Icons.INFO_OUTLINE, color=theme["btn_primary"], size=19
        )
        notification_timer = ft.ProgressBar(
            value=0,
            bar_height=3,
            bgcolor=theme["border"],
            color=theme["btn_primary"],
            expand=True,
            rtl=False,
        )
        notification_banner = ft.Container(
            width=380,
            visible=False,
            opacity=0,
            offset=ft.Offset(1, -1),
            animate_opacity=ft.Animation(380, ft.AnimationCurve.EASE_OUT_CUBIC),
            animate_offset=ft.Animation(460, ft.AnimationCurve.EASE_OUT_CUBIC),
            bgcolor=ft.Colors.with_opacity(0.9, theme["surface"]),
            border=ft.Border.all(1, color=theme["border"]),
            border_radius=8,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            shadow=ft.BoxShadow(
                blur_radius=18,
                color=ft.Colors.with_opacity(0.2, "#000000"),
                offset=ft.Offset(0, 5),
            ),
            top=12,
            right=12,
            content=ft.Column(
                spacing=0,
                controls=[
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=14, vertical=12),
                        content=ft.Row(
                            controls=[notification_icon, notification_text],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=10,
                        ),
                    ),
                    ft.Container(
                        padding=ft.Padding.only(left=12, right=12, bottom=9),
                        content=notification_timer,
                    ),
                ],
            ),
        )

        async def animate_notification(message, icon, epoch):
            notification_text.value = message
            notification_icon.name = icon
            notification_timer.value = 0
            notification_banner.visible = True
            notification_banner.opacity = 0
            notification_banner.offset = ft.Offset(1, -1)
            page.update()
            await asyncio.sleep(0.05)
            if notification_state["epoch"] != epoch:
                return
            notification_banner.opacity = 1
            notification_banner.offset = ft.Offset(0, 0)
            page.update()
            loop = asyncio.get_running_loop()
            started_at = loop.time()
            while True:
                await asyncio.sleep(1 / 30)
                if notification_state["epoch"] != epoch:
                    return
                elapsed = loop.time() - started_at
                notification_timer.value = min(1, elapsed / 5)
                notification_timer.update()
                if elapsed >= 5:
                    break
            if notification_state["epoch"] != epoch:
                return
            notification_banner.opacity = 0
            notification_banner.offset = ft.Offset(1, -1)
            page.update()
            await asyncio.sleep(0.5)
            if notification_state["epoch"] == epoch:
                notification_banner.visible = False
                page.update()

        def show_notification():
            pending = notification_state.get("pending")
            if pending is None:
                return
            notification_state["pending"] = None
            notification_state["epoch"] += 1
            page.run_task(
                animate_notification,
                pending[0],
                pending[1],
                notification_state["epoch"],
            )

        notification_state["show"] = show_notification

        # ---- animation overhaul: startup ripple & navigation transitions ----
        def prepare_intro(control):
            """Hide a control so the startup ripple can reveal it with a pop."""
            if not intro_state["pending"]:
                return control
            control.opacity = 0
            control.scale = 0.92
            control.offset = ft.Offset(0, 0.06)
            control.animate_opacity = ft.Animation(340, ft.AnimationCurve.EASE_OUT_CUBIC)
            control.animate_scale = ft.Animation(460, ft.AnimationCurve.EASE_OUT_BACK)
            control.animate_offset = ft.Animation(460, ft.AnimationCurve.EASE_OUT_BACK)
            intro_state["targets"].append(control)
            return control

        def reveal_intro(control):
            control.opacity = 1
            control.scale = 1.0
            control.offset = ft.Offset(0, 0)

        async def play_intro_ripple():
            """Reveal every registered control one by one for a ripple entrance."""
            if not intro_state["pending"]:
                return
            intro_state["pending"] = False
            intro_state["running"] = True
            raw_targets = list(intro_state["targets"])
            # Reorder navbar targets to the front so the ripple cascades from top to bottom
            navbar_set = set()
            try:
                navbar_set.update([title_wrap, tabs_pop_wrap, func_buttons_wrap])
            except Exception:
                pass
            targets = [t for t in raw_targets if t in navbar_set] + [
                t for t in raw_targets if t not in navbar_set
            ]
            await asyncio.sleep(0.12)
            try:
                for control in targets:
                    reveal_intro(control)
                    try:
                        control.update()
                    except Exception:
                        pass
                    await asyncio.sleep(0.045)
            finally:
                # Safety net: nothing may stay hidden, even if a control got detached.
                for control in targets:
                    reveal_intro(control)
                try:
                    page.update()
                except Exception:
                    pass
                intro_state["running"] = False

        # ---- content area: this is what swaps when you navigate ----
        page_content = ft.Container(
            expand=True,
            opacity=1.0,
            animate_opacity=ft.Animation(160, ft.AnimationCurve.EASE_IN),
        )

        content_area = ft.Container(
            content=ft.Stack(
                expand=True,
                controls=[page_content, notification_banner],
                clip_behavior=ft.ClipBehavior.NONE,
            ),
            expand=True,
            bgcolor=theme["bg"],
            # border=ft.Border.all(1, color=theme["border"]),
            border_radius=10,
            margin=ft.Margin.only(left=10, top=0, right=10, bottom=10),
        )

        transition_id = 0
        current_page = None

        async def transition_page(name, content, request_id):
            nonlocal tab
            page_content.animate_opacity = ft.Animation(90, ft.AnimationCurve.EASE_OUT)
            page_content.opacity = 0
            page.update()
            await asyncio.sleep(0.08)

            if request_id != transition_id:
                return

            page_content.content = content
            if name in tab_names:
                tab = name
            elif name.startswith("options"):
                tab = "options"
            else:
                tab = "installations"
            previous_tab_index = tabs_buttons.selected_index
            new_tab_index = tab_names.index(tab)
            tabs_buttons.selected_index = new_tab_index
            page_content.animate_opacity = ft.Animation(160, ft.AnimationCurve.EASE_IN)
            page_content.opacity = 1
            page.update()

        loader_catalog_cache = {}

        def get_profile_version_id(profile):
            version_id = profile.get("version_id")
            if version_id:
                return version_id
            if profile.get("loader") == "Vanilla":
                return profile.get("minecraft_version", "")
            try:
                return get_mod_loader(profile["loader"]).get_installed_version(
                    profile["minecraft_version"], profile.get("loader_version", "")
                )
            except (KeyError, ValueError):
                return ""

        def is_active_launch_version(version_id, profile_id=None):
            if not launcher_log_state["running"]:
                return False
            return (
                version_id == launcher_log_state["target_version_id"]
                or profile_id == launcher_log_state["target_profile_id"]
            )

        all_ambient_orbs = []

        def create_ambient_gradient_orbs():
            enabled = bool(settings.get("enable_orbs", True))
            first_orb = ft.Container(
                width=450,
                height=450,
                border_radius=225,
                gradient=ft.RadialGradient(
                    colors=[
                        ft.Colors.with_opacity(0.24, theme["btn_primary"]),
                        ft.Colors.with_opacity(0.08, theme["btn_primary"]),
                        ft.Colors.TRANSPARENT,
                    ],
                    stops=[0.0, 0.55, 1.0],
                ),
                left=random.uniform(-120, 260),
                top=random.uniform(-100, 190),
                scale=random.uniform(0.88, 1.16),
                opacity=random.uniform(0.72, 1.0),
                animate_position=ft.Animation(10000, ft.AnimationCurve.EASE_IN_OUT),
                animate_scale=ft.Animation(10000, ft.AnimationCurve.EASE_IN_OUT),
                animate_opacity=ft.Animation(10000, ft.AnimationCurve.EASE_IN_OUT),
                visible=enabled,
            )
            second_orb = ft.Container(
                width=420,
                height=420,
                border_radius=210,
                gradient=ft.RadialGradient(
                    colors=[
                        ft.Colors.with_opacity(
                            0.20, theme.get("btn_primary_hover", theme["btn_primary"])
                        ),
                        ft.Colors.with_opacity(
                            0.06, theme.get("btn_primary_hover", theme["btn_primary"])
                        ),
                        ft.Colors.TRANSPARENT,
                    ],
                    stops=[0.0, 0.55, 1.0],
                ),
                right=random.uniform(-120, 260),
                bottom=random.uniform(-100, 190),
                scale=random.uniform(0.88, 1.16),
                opacity=random.uniform(0.72, 1.0),
                animate_position=ft.Animation(11000, ft.AnimationCurve.EASE_IN_OUT),
                animate_scale=ft.Animation(11000, ft.AnimationCurve.EASE_IN_OUT),
                animate_opacity=ft.Animation(11000, ft.AnimationCurve.EASE_IN_OUT),
                visible=enabled,
            )
            all_ambient_orbs.append((first_orb, second_orb))
            return first_orb, second_orb

        async def _animate_single_orb(
            orb, is_active, coord_mode, min_x, max_x, min_y, max_y
        ):
            await asyncio.sleep(random.uniform(0.1, 0.4))
            while is_active() and settings.get("enable_orbs", True):
                duration = random.randint(8000, 16000)
                anim = ft.Animation(duration, ft.AnimationCurve.EASE_IN_OUT)
                orb.animate_position = anim
                orb.animate_scale = anim
                orb.animate_opacity = anim

                target_x = random.uniform(min_x, max_x)
                target_y = random.uniform(min_y, max_y)
                target_scale = random.uniform(0.88, 1.16)
                target_opacity = random.uniform(0.72, 1.0)

                if coord_mode == "top_left":
                    orb.left = target_x
                    orb.top = target_y
                else:
                    orb.right = target_x
                    orb.bottom = target_y
                orb.scale = target_scale
                orb.opacity = target_opacity

                try:
                    orb.update()
                except Exception:
                    return

                await asyncio.sleep(duration / 1000)

        async def animate_ambient_gradient_orbs(first_orb, second_orb, is_active):
            if not settings.get("enable_orbs", True):
                return
            await asyncio.sleep(0.1)
            t1 = asyncio.create_task(
                _animate_single_orb(first_orb, is_active, "top_left", -120, 260, -100, 190)
            )
            t2 = asyncio.create_task(
                _animate_single_orb(second_orb, is_active, "bottom_right", -120, 260, -100, 190)
            )
            try:
                await asyncio.gather(t1, t2)
            except Exception:
                pass

        def start_active_page_orb_animation():
            page_name = active_page.get("name")
            if page_name == "home":
                callback = globals().get("animate_launcher_log_background")
                if callable(callback):
                    page.run_task(callback)
            elif page_name == "options":
                callback = globals().get("animate_options_background")
                if callable(callback):
                    page.run_task(callback)

        def set_orbs_enabled(enabled: bool):
            persist_setting("enable_orbs", enabled)
            for o1, o2 in all_ambient_orbs:
                o1.visible = enabled
                o2.visible = enabled
                try:
                    o1.update()
                except Exception:
                    pass
                try:
                    o2.update()
                except Exception:
                    pass
            if enabled:
                start_active_page_orb_animation()

        def build_installation_editor(profile=None, name_only=False):
            existing_profile = profile or {}
            loader_names = ["Vanilla", "Fabric", "Forge", "Quilt", "NeoForge"]
            initial_loader = existing_profile.get("loader", "Vanilla")
            if initial_loader not in loader_names:
                initial_loader = "Vanilla"

            installation_name_is_default = {
                "value": not bool((existing_profile.get("name") or "").strip())
            }
            name_field = ft.TextField(
                label="Profile Name",
                value=existing_profile.get("name", ""),
                hint_text="Defaults to the selected version",
                border_color=theme["border"],
                focused_border_color=theme["btn_primary"],
                color=theme["text_primary"],
                label_style=ft.TextStyle(color=theme["text_secondary"], size=13),
                cursor_color=theme["btn_primary"],
                fill_color=theme["card"],
                border_radius=8,
                expand=True,
                on_change=lambda e: installation_name_is_default.update(
                    value=False
                ),
            )
            loader_field = ft.Dropdown(
                label="Type",
                value=initial_loader,
                options=[
                    ft.DropdownOption(key=name, text=name) for name in loader_names
                ],
                border_color=theme["border"],
                focused_border_color=theme["btn_primary"],
                color=theme["text_primary"],
                label_style=ft.TextStyle(color=theme["text_secondary"], size=13),
                fill_color=theme["card"],
                border_radius=8,
                expand=True,
            )
            channel_field = ft.Dropdown(
                label="Version channel",
                value=existing_profile.get("channel", "Release") or "Release",
                options=[
                    ft.DropdownOption(key="Release", text="Release"),
                    ft.DropdownOption(key="Snapshot", text="Snapshot"),
                ],
                border_color=theme["border"],
                focused_border_color=theme["btn_primary"],
                color=theme["text_primary"],
                label_style=ft.TextStyle(color=theme["text_secondary"], size=13),
                fill_color=theme["card"],
                border_radius=8,
                expand=True,
            )

            def get_minecraft_version_ids(loader_name, channel_name):
                if loader_name == "Vanilla":
                    version_type = channel_name.casefold()
                    return [
                        version["id"]
                        for version in version_catalog
                        if version.get("type") == version_type
                    ]

                cache_key = (loader_name.casefold(), "minecraft")
                if cache_key not in loader_catalog_cache:
                    try:
                        loader_catalog_cache[cache_key] = get_mod_loader(
                            loader_name
                        ).get_minecraft_versions(False)
                    except Exception:
                        loader_catalog_cache[cache_key] = []
                return loader_catalog_cache[cache_key]

            def get_loader_version_ids(loader_name, minecraft_version):
                if not minecraft_version:
                    return []
                cache_key = (loader_name.casefold(), minecraft_version)
                if cache_key not in loader_catalog_cache:
                    try:
                        loader_catalog_cache[cache_key] = get_mod_loader(
                            loader_name
                        ).get_loader_versions(minecraft_version, False)
                    except Exception:
                        loader_catalog_cache[cache_key] = []
                return loader_catalog_cache[cache_key]

            selected_version = existing_profile.get("minecraft_version", "")
            initial_mc_versions = (
                []
                if name_only
                else get_minecraft_version_ids(
                    initial_loader,
                    channel_field.value or "Release",
                )
            )
            if selected_version and selected_version not in initial_mc_versions:
                initial_mc_versions.insert(0, selected_version)
            version_field = ft.Dropdown(
                label="Minecraft version",
                value=selected_version
                or (initial_mc_versions[0] if initial_mc_versions else None),
                options=[
                    ft.DropdownOption(key=version, text=version)
                    for version in initial_mc_versions
                ],
                enable_filter=True,
                on_select=lambda e: update_loader_versions(),
                border_color=theme["border"],
                focused_border_color=theme["btn_primary"],
                color=theme["text_primary"],
                label_style=ft.TextStyle(color=theme["text_secondary"], size=13),
                fill_color=theme["card"],
                border_radius=8,
                expand=True,
            )

            def sync_default_installation_name():
                if installation_name_is_default["value"]:
                    name_field.value = (version_field.value or "").strip()

            sync_default_installation_name()
            initial_loader_versions = (
                get_loader_version_ids(initial_loader, version_field.value)
                if initial_loader != "Vanilla" and not name_only
                else []
            )
            selected_loader_version = existing_profile.get("loader_version", "")
            if (
                selected_loader_version
                and selected_loader_version not in initial_loader_versions
            ):
                initial_loader_versions.insert(0, selected_loader_version)
            loader_version_field = ft.Dropdown(
                label="Loader version",
                value=selected_loader_version
                or (initial_loader_versions[0] if initial_loader_versions else None),
                options=[
                    ft.DropdownOption(key=version, text=version)
                    for version in initial_loader_versions
                ],
                visible=initial_loader != "Vanilla",
                border_color=theme["border"],
                focused_border_color=theme["btn_primary"],
                color=theme["text_primary"],
                label_style=ft.TextStyle(color=theme["text_secondary"], size=13),
                fill_color=theme["card"],
                border_radius=8,
                expand=True,
            )
            form_error = ft.Text(color=theme["btn_primary"])

            def set_form_error(message):
                form_error.value = message
                notify(message, ft.Icons.ERROR_OUTLINE)

            is_instance_switch = build_themed_switch(
                label="Separate Instance (Isolated Environment)",
                value=bool(existing_profile.get("is_instance", False)),
                on_change=lambda e: toggle_instance_mode(),
            )
            instance_hint = ft.Text(
                "Gives this installation its own isolated folder for worlds, texture packs, options, and mods.",
                size=12,
                color=theme["text_secondary"],
            )
            instance_folder_field = ft.TextField(
                label=" Instance Folder Name (Optional)",
                value=existing_profile.get("instance_name", ""),
                hint_text="e.g. My_Modded_MC",
                border_color=theme["border"],
                focused_border_color=theme["btn_primary"],
                color=theme["text_primary"],
                label_style=ft.TextStyle(color=theme["text_secondary"], size=13),
                fill_color=theme["card"],
                border_radius=8,
                expand=True,
                visible=bool(existing_profile.get("is_instance", False)),
            )

            def toggle_instance_mode(e=None):
                instance_folder_field.visible = bool(is_instance_switch.value)
                if is_instance_switch.value and not instance_folder_field.value:
                    clean_name = re.sub(
                        r"[^a-zA-Z0-9_\-]", "_", (name_field.value or "").strip()
                    )
                    if clean_name:
                        instance_folder_field.value = clean_name
                page.update()

            def update_loader_versions(e=None):
                selected_loader = loader_field.value or "Vanilla"
                selected_mc_version = version_field.value or ""
                loader_versions = (
                    get_loader_version_ids(selected_loader, selected_mc_version)
                    if selected_loader != "Vanilla"
                    else []
                )
                current_loader_version = loader_version_field.value
                if (
                    current_loader_version
                    and current_loader_version not in loader_versions
                ):
                    current_loader_version = None
                loader_version_field.options = [
                    ft.DropdownOption(key=version, text=version)
                    for version in loader_versions
                ]
                loader_version_field.value = current_loader_version or (
                    loader_versions[0] if loader_versions else None
                )
                sync_default_installation_name()
                if selected_loader != "Vanilla" and not loader_versions:
                    set_form_error(
                        "No loader versions found for this Minecraft version."
                    )
                elif not form_error.value or form_error.value.startswith(
                    "No loader versions"
                ):
                    form_error.value = ""
                if e is not None:
                    page.update()

            def update_editor_fields(e=None):
                if name_only:
                    return

                selected_loader = loader_field.value or "Vanilla"
                selected_channel = channel_field.value or "Release"
                is_vanilla = selected_loader == "Vanilla"
                channel_field.visible = is_vanilla
                loader_version_field.visible = not is_vanilla
                selected_value = version_field.value or ""
                minecraft_versions = get_minecraft_version_ids(
                    selected_loader,
                    selected_channel,
                )
                if (
                    e is None
                    and selected_value
                    and selected_value not in minecraft_versions
                ):
                    minecraft_versions.insert(0, selected_value)
                version_field.options = [
                    ft.DropdownOption(key=version, text=version)
                    for version in minecraft_versions
                ]
                if selected_value not in minecraft_versions:
                    version_field.value = (
                        minecraft_versions[0] if minecraft_versions else None
                    )
                sync_default_installation_name()
                if is_vanilla:
                    loader_version_field.options = []
                    loader_version_field.value = None
                else:
                    update_loader_versions()
                if e is not None:
                    page.update()

            loader_field.on_select = update_editor_fields
            channel_field.on_select = update_editor_fields
            version_field.on_select = update_loader_versions

            def save_profile(e):
                name = (name_field.value or "").strip()
                if name_only:
                    if not name:
                        name = (
                            existing_profile.get("version_id")
                            or existing_profile.get("minecraft_version")
                            or "Minecraft profile"
                        )

                    saved_profile = dict(existing_profile)
                    saved_profile["id"] = saved_profile.get("id", uuid.uuid4().hex)
                    saved_profile["name"] = name
                    updated_installations = list(installations)
                    existing_index = next(
                        (
                            index
                            for index, item in enumerate(updated_installations)
                            if item["id"] == saved_profile["id"]
                        ),
                        None,
                    )
                    if existing_index is None:
                        updated_installations.append(saved_profile)
                    else:
                        updated_installations[existing_index] = saved_profile
                    if save_installations(updated_installations):
                        installations[:] = updated_installations
                        navigate("installations")
                        return
                    set_form_error("Could not save profile data.")
                    page.update()
                    return

                if not online_available:
                    set_form_error(
                        "No internet connection. Try creating the profile again when online."
                    )
                    page.update()
                    return

                loader_name = loader_field.value or "Vanilla"
                channel_name = channel_field.value or "Release"
                mc_version = (version_field.value or "").strip()
                mod_version = (loader_version_field.value or "").strip()
                is_instance = bool(is_instance_switch.value)

                if not mc_version:
                    set_form_error("Please choose a Minecraft version.")
                    page.update()
                    return
                if not name:
                    name = mc_version
                if loader_name != "Vanilla" and not mod_version:
                    set_form_error("Please choose a loader version.")
                    page.update()
                    return

                profile_id = existing_profile.get("id", uuid.uuid4().hex)
                target_version_id = (
                    mc_version
                    if loader_name == "Vanilla"
                    else get_mod_loader(loader_name).get_installed_version(
                        mc_version, mod_version
                    )
                )

                # Validation: Prevent duplicate default/shared configurations for the same version
                if not is_instance:
                    conflicting_profile = next(
                        (
                            item
                            for item in installations
                            if item["id"] != profile_id
                            and not item.get("is_instance")
                            and get_profile_version_id(item) == target_version_id
                        ),
                        None,
                    )
                    if conflicting_profile:
                        notify(
                            f"{target_version_id} already has a configured profile.",
                            ft.Icons.INFO_OUTLINE,
                        )

                        def close_already_installed_dialog(dlg):
                            close_dialog_with_blur(dlg)

                        def enable_instance_mode(dlg):
                            close_dialog_with_blur(dlg)
                            is_instance_switch.value = True
                            instance_folder_field.visible = True
                            if not instance_folder_field.value:
                                instance_folder_field.value = re.sub(
                                    r"[^a-zA-Z0-9_\-]", "_", name
                                )
                            form_error.value = ""
                            page.update()

                        already_installed_dialog = build_themed_alert_dialog(
                            title=ft.Row(
                                spacing=8,
                                controls=[
                                    ft.Icon(
                                        ft.Icons.INFO_OUTLINED,
                                        color=theme["btn_primary"],
                                        size=22,
                                    ),
                                    ft.Text(
                                        "This version is already installed",
                                        weight=ft.FontWeight.BOLD,
                                        color=theme["text_primary"],
                                    ),
                                ],
                            ),
                            content=ft.Text(
                                f"A profile for '{target_version_id}' is already configured in Gr8 Launcher ('{conflicting_profile['name']}').\n\n"
                                "Creating another standard configuration for the same version will just launch into the same shared environment.\n\n"
                                "To create a new environment with separate worlds, texture packs, and settings, make it a Separate Instance.",
                                color=theme["text_primary"],
                                size=14,
                            ),
                            actions=[
                                ft.TextButton(
                                    "Cancel",
                                    style=ft.ButtonStyle(
                                        color=theme["text_secondary"]
                                    ),
                                    on_click=lambda e: close_already_installed_dialog(
                                        already_installed_dialog
                                    ),
                                ),
                                ft.Button(
                                    "Make Instance",
                                    bgcolor=theme["btn_primary"],
                                    color=theme["btn_primary_text"],
                                    style=rounded_button_style,
                                    on_click=lambda e: enable_instance_mode(
                                        already_installed_dialog
                                    ),
                                ),
                            ],
                        )
                        show_dialog_with_blur(already_installed_dialog)
                        return

                instance_folder_name = ""
                game_dir = ""
                if is_instance:
                    clean_folder = re.sub(
                        r"[^a-zA-Z0-9_\-]",
                        "_",
                        (instance_folder_field.value or name).strip(),
                    )
                    if not clean_folder:
                        clean_folder = f"instance_{uuid.uuid4().hex[:6]}"
                    instance_folder_name = clean_folder
                    instances_dir = Path(minecraft_directory) / "instances" / clean_folder
                    game_dir = str(instances_dir)

                    duplicate_instance = next(
                        (
                            item
                            for item in installations
                            if item["id"] != profile_id
                            and item.get("is_instance")
                            and item.get("game_directory") == game_dir
                        ),
                        None,
                    )
                    if duplicate_instance:
                        set_form_error(
                            f"An instance using folder '{clean_folder}' already exists. Choose a different folder name."
                        )
                        page.update()
                        return

                    Path(game_dir).mkdir(parents=True, exist_ok=True)

                saved_profile = {
                    "id": profile_id,
                    "name": name,
                    "loader": loader_name,
                    "channel": channel_name if loader_name == "Vanilla" else "",
                    "minecraft_version": mc_version,
                    "loader_version": (
                        mod_version if loader_name != "Vanilla" else ""
                    ),
                    "version_id": target_version_id,
                    "is_instance": is_instance,
                    "instance_name": instance_folder_name,
                    "game_directory": game_dir,
                }
                updated_installations = list(installations)
                existing_index = next(
                    (
                        index
                        for index, item in enumerate(updated_installations)
                        if item["id"] == profile_id
                    ),
                    None,
                )
                if existing_index is None:
                    updated_installations.append(saved_profile)
                else:
                    updated_installations[existing_index] = saved_profile

                if save_installations(updated_installations):
                    installations[:] = updated_installations
                    navigate("installations")
                    return
                set_form_error("Could not save profile data.")
                page.update()

            update_editor_fields()

            bg_orb1, bg_orb2 = create_ambient_gradient_orbs()
            page.run_task(
                animate_ambient_gradient_orbs,
                bg_orb1,
                bg_orb2,
                lambda: active_page.get("name")
                in ("new_installation", "edit_installation", "configure_installed"),
            )

            editor_card = ft.Container(
                width=520,
                padding=ft.Padding.symmetric(horizontal=32, vertical=28),
                bgcolor=theme["surface"],
                border=ft.Border.all(1, color=theme["border"]),
                border_radius=16,
                shadow=ft.BoxShadow(
                    spread_radius=0,
                    blur_radius=16,
                    color=ft.Colors.with_opacity(0.25, "#000000"),
                    offset=ft.Offset(0, 6),
                ),
                content=ft.Column(
                    spacing=16,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Row(
                                    spacing=10,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.IconButton(
                                            icon=ft.Icons.ARROW_BACK,
                                            tooltip=tooltip("Back to profiles"),
                                            icon_color=theme["text_secondary"],
                                            style=rounded_button_style,
                                            on_click=lambda e: navigate("installations"),
                                        ),
                                        ft.Text(
                                            (
                                                "Edit Profile"
                                                if profile
                                                else "New Profile"
                                            ),
                                            size=22,
                                            weight=ft.FontWeight.BOLD,
                                            color=theme["text_primary"],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        name_field,
                        ft.Container(loader_field, visible=not name_only),
                        ft.Container(channel_field, visible=not name_only),
                        ft.Container(version_field, visible=not name_only),
                        ft.Container(loader_version_field, visible=not name_only),
                        ft.Container(
                            content=ft.Column(
                                spacing=6,
                                controls=[
                                    is_instance_switch,
                                    instance_hint,ft.Divider(color=ft.Colors.TRANSPARENT),
                                    
                                    instance_folder_field,
                                ],
                            ),
                            visible=not name_only,
                        ),
                        form_error,
                        ft.Row(
                            alignment=ft.MainAxisAlignment.END,
                            spacing=12,
                            controls=[
                                ft.TextButton(
                                    "Cancel",
                                    style=ft.ButtonStyle(
                                        color=theme["text_secondary"],
                                        shape=ft.RoundedRectangleBorder(radius=8),
                                    ),
                                    on_click=lambda e: navigate("installations"),
                                ),
                                ft.Button(
                                    "Save Profile",
                                    bgcolor=theme["btn_primary"],
                                    color=theme["btn_primary_text"],
                                    style=rounded_button_style,
                                    on_click=save_profile,
                                ),
                            ],
                        ),
                    ],
                ),
            )

            return ft.Stack(
                expand=True,
                alignment=ft.Alignment.CENTER,
                controls=[
                    bg_orb1,
                    bg_orb2,
                    ft.Container(
                        alignment=ft.Alignment.CENTER,
                        padding=ft.Padding.symmetric(vertical=20),
                        content=ft.Column(
                            alignment=ft.MainAxisAlignment.CENTER,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            scroll=ft.ScrollMode.AUTO,
                            controls=[editor_card],
                        ),
                    ),
                ],
            )

        def navigate(name, profile_id=None, force=False):
            nonlocal current_page, transition_id, tab, handled_external_profile_ids
            if name == "profiles":
                name = "options_accounts"
            if name == "edit_installation":
                profile = next(
                    (item for item in installations if item["id"] == profile_id),
                    None,
                )
                if profile and is_active_launch_version(
                    get_profile_version_id(profile), profile_id
                ):
                    notify("This profile is busy launching.", ft.Icons.INFO_OUTLINE)
                    return
            if name == "configure_installed" and is_active_launch_version(profile_id):
                notify("This version is busy installing or launching.", ft.Icons.INFO_OUTLINE)
                return
            if name == "new_installation" and (
                not online_available or launcher_log_state["running"]
            ):
                return
            active_page["name"] = name
            active_page["profile_id"] = profile_id

            installed_versions = get_installed_versions(force_refresh=force)
            if force:
                installations[:], handled_external_profile_ids, imp_changed = (
                    import_external_installations(
                        installations,
                        handled_external_profile_ids,
                        installed_versions,
                        get_external_installations(),
                        version_catalog,
                    )
                )
                installations[:], disc_changed = discover_local_instances(
                    minecraft_directory,
                    installations,
                    installed_versions,
                    version_catalog,
                )
                if imp_changed or disc_changed:
                    save_installations(
                        installations,
                        handled_external_profile_ids,
                        latest_release_id,
                    )

            def profile_summary(installation):
                summary = (
                    f"{installation.get('loader', 'Vanilla')} | "
                    f"{installation.get('minecraft_version', 'Unknown version')}"
                )
                if installation.get("loader") != "Vanilla" and installation.get(
                    "loader_version"
                ):
                    summary += f" | {installation['loader_version']}"
                elif installation.get("channel"):
                    summary += f" | {installation['channel']}"
                if installation.get("is_instance"):
                    summary += f" • Instance: {installation.get('instance_name', 'Custom')}"
                return summary

            def open_game_folder(folder_path):
                try:
                    path = Path(folder_path or minecraft_directory)
                    path.mkdir(parents=True, exist_ok=True)
                    if hasattr(os, "startfile"):
                        os.startfile(str(path))
                    elif sys.platform == "darwin":
                        subprocess.Popen(
                            ["open", str(path)],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    else:
                        subprocess.Popen(
                            ["xdg-open", str(path)],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                except Exception:
                    pass

            profiles_by_version = {
                get_profile_version_id(profile): profile
                for profile in installations
                if get_profile_version_id(profile)
            }
            local_version_ids = {
                version["version_id"] for version in installed_versions
            }
            pending_profiles = [
                profile
                for profile in installations
                if get_profile_version_id(profile) not in local_version_ids
            ]

            def confirm_delete(profile_id):
                profile = next(
                    (item for item in installations if item["id"] == profile_id),
                    None,
                )
                if profile is None:
                    return
                if is_active_launch_version(
                    get_profile_version_id(profile), profile_id
                ):
                    notify("This profile is busy launching.", ft.Icons.INFO_OUTLINE)
                    return

                is_inst = bool(profile.get("is_instance"))
                if is_inst:
                    game_dir = profile.get("game_directory")
                    if not game_dir and profile.get("instance_name"):
                        game_dir = str(
                            Path(minecraft_directory)
                            / "instances"
                            / profile["instance_name"]
                        )
                    dialog = build_themed_alert_dialog(
                        title=ft.Row(
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Icon(
                                    ft.Icons.WARNING_ROUNDED,
                                    color=ft.Colors.RED_400,
                                    size=26,
                                ),
                                ft.Text(
                                    "Delete Instance & Files?",
                                    color=ft.Colors.RED_400,
                                    weight=ft.FontWeight.BOLD,
                                    size=17,
                                ),
                            ],
                        ),
                        content=ft.Column(
                            spacing=12,
                            tight=True,
                            controls=[
                                ft.Text(
                                    f"Are you sure you want to permanently delete the instance '{profile['name']}'?",
                                    color=theme["text_primary"],
                                    size=14,
                                    weight=ft.FontWeight.W_500,
                                ),
                                ft.Container(
                                    padding=ft.Padding.all(12),
                                    bgcolor=ft.Colors.with_opacity(
                                        0.12, ft.Colors.RED_900
                                    ),
                                    border=ft.Border.all(
                                        1,
                                        color=ft.Colors.with_opacity(
                                            0.35, ft.Colors.RED_400
                                        ),
                                    ),
                                    border_radius=8,
                                    content=ft.Column(
                                        spacing=6,
                                        tight=True,
                                        controls=[
                                            ft.Text(
                                                "⚠️ DANGER: This is an isolated instance directory!",
                                                color=ft.Colors.RED_300,
                                                size=12,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                            ft.Text(
                                                "Deleting this instance will permanently delete all its files:\n"
                                                "• All singleplayer Worlds and Saves\n"
                                                "• Installed Resource Packs, Texture Packs & Shader Packs\n"
                                                "• Installed Mods, Configurations & custom Options\n"
                                                "• Screenshots, Logs, and Instance data",
                                                color=theme["text_primary"],
                                                size=12,
                                            ),
                                            ft.Text(
                                                "This action cannot be undone!",
                                                color=ft.Colors.RED_300,
                                                size=11,
                                                italic=True,
                                            ),
                                        ],
                                    ),
                                ),
                                *(
                                    [
                                        ft.Text(
                                            f"Instance Directory:\n{game_dir}",
                                            size=11,
                                            color=theme["text_muted"],
                                            selectable=True,
                                        )
                                    ]
                                    if game_dir
                                    else []
                                ),
                            ],
                        ),
                        actions=[
                            ft.TextButton(
                                "Cancel",
                                on_click=lambda e: close_delete_dialog(dialog),
                                style=ft.ButtonStyle(
                                    color=theme["text_secondary"]
                                ),
                            ),
                            ft.Button(
                                "Delete Instance",
                                bgcolor=ft.Colors.RED_700,
                                color=ft.Colors.WHITE,
                                style=rounded_button_style,
                                on_click=lambda e: delete_profile(
                                    profile_id, dialog
                                ),
                            ),
                        ],
                    )
                else:
                    dialog = build_themed_alert_dialog(
                        title=ft.Row(
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Icon(
                                    ft.Icons.REMOVE_CIRCLE_OUTLINE,
                                    color=theme["btn_primary"],
                                    size=22,
                                ),
                                ft.Text(
                                    "Remove Profile Configuration?",
                                    color=theme["btn_primary"],
                                    weight=ft.FontWeight.BOLD,
                                    size=16,
                                ),
                            ],
                        ),
                        content=ft.Column(
                            spacing=10,
                            tight=True,
                            controls=[
                                ft.Text(
                                    f"Are you sure you want to remove '{profile['name']}' from Gr8 Launcher?",
                                    color=theme["text_primary"],
                                    size=14,
                                ),
                                ft.Container(
                                    padding=ft.Padding.all(12),
                                    bgcolor=ft.Colors.with_opacity(
                                        0.08, theme["btn_primary"]
                                    ),
                                    border=ft.Border.all(
                                        1,
                                        color=ft.Colors.with_opacity(
                                            0.25, theme["btn_primary"]
                                        ),
                                    ),
                                    border_radius=8,
                                    content=ft.Column(
                                        spacing=6,
                                        tight=True,
                                        controls=[
                                            ft.Row(
                                                spacing=8,
                                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                controls=[
                                                    ft.Icon(
                                                        ft.Icons.INFO_OUTLINE,
                                                        color=theme["btn_primary"],
                                                        size=18,
                                                    ),
                                                    ft.Text(
                                                        "Profile Configuration Removal",
                                                        color=theme["btn_primary"],
                                                        weight=ft.FontWeight.BOLD,
                                                        size=12,
                                                    ),
                                                ],
                                            ),
                                            ft.Text(
                                                "• This will only remove its configuration from your profiles list.\n"
                                                "• Your Minecraft game files, worlds, packs, and saves will NOT be deleted.\n"
                                                "• This version will now be shown under the Unconfigured Versions section at the bottom of the Installations tab.",
                                                color=theme["text_secondary"],
                                                size=12,
                                            ),
                                        ],
                                    ),
                                ),
                            ],
                        ),
                        actions=[
                            ft.TextButton(
                                "Cancel",
                                on_click=lambda e: close_delete_dialog(dialog),
                                style=ft.ButtonStyle(
                                    color=theme["text_secondary"]
                                ),
                            ),
                            ft.Button(
                                "Remove Profile",
                                bgcolor=theme["btn_primary"],
                                color=theme["btn_primary_text"],
                                style=rounded_button_style,
                                on_click=lambda e: delete_profile(
                                    profile_id, dialog
                                ),
                            ),
                        ],
                    )
                show_dialog_with_blur(dialog)

            def confirm_delete_installed_version(version_id):
                if is_active_launch_version(version_id):
                    notify("This version is busy installing or launching.", ft.Icons.INFO_OUTLINE)
                    return
                dialog = build_themed_alert_dialog(
                    title=ft.Text(
                        "Delete installed version?",
                        color=theme["btn_primary"],
                        weight=ft.FontWeight.BOLD,
                    ),
                    content=ft.Text(
                        f"Permanently remove '{version_id}' from Minecraft and its launcher profile?",
                        color=theme["text_primary"],
                    ),
                    actions=[
                        ft.TextButton(
                            "Cancel",
                            on_click=lambda e: close_delete_dialog(dialog),
                            style=ft.ButtonStyle(color=theme["text_secondary"]),
                        ),
                        ft.Button(
                            "Delete version",
                            bgcolor=theme["btn_primary"],
                            color=theme["btn_primary_text"],
                            style=rounded_button_style,
                            on_click=lambda e: delete_installed_version(
                                version_id, dialog
                            ),
                        ),
                    ],
                )
                show_dialog_with_blur(dialog)

            def show_unconfigured_version_info(version_id):
                dialog = build_themed_alert_dialog(
                    title=ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(ft.Icons.INFO_OUTLINE, color=theme["btn_primary"], size=22),
                            ft.Text("Unconfigured Version", color=theme["text_primary"], weight=ft.FontWeight.BOLD),
                        ],
                    ),
                    content=ft.Column(
                        spacing=8,
                        tight=True,
                        controls=[
                            ft.Text(
                                f"'{version_id}' is saved on your disk in Minecraft's versions folder, but is not configured as a profile in the launcher.",
                                size=13,
                                color=theme["text_primary"],
                            ),
                            ft.Text(
                                "• To add this version to your launcher, click the '+' button on the right.\n"
                                "• To permanently delete this version from your disk, click the delete (trash) button on the right.",
                                size=12,
                                color=theme["text_secondary"],
                            ),
                        ],
                    ),
                    actions=[
                        ft.Button(
                            "Got it",
                            bgcolor=theme["btn_primary"],
                            color=theme["btn_primary_text"],
                            style=rounded_button_style,
                            on_click=lambda e: close_delete_dialog(dialog),
                        ),
                    ],
                )
                show_dialog_with_blur(dialog)

            def delete_installed_version(version_id, dialog):
                matching_ids = {
                    item["id"]
                    for item in installations
                    if get_profile_version_id(item) == version_id
                }
                updated_installations = [
                    item for item in installations if item["id"] not in matching_ids
                ]
                if not save_installations(
                    updated_installations,
                    handled_external_profile_ids,
                    latest_release_id,
                ):
                    dialog.content = ft.Text("Could not update Launcher profiles.")
                    page.update()
                    return
                if not remove_installed_version(version_id):
                    save_installations(
                        installations, handled_external_profile_ids, latest_release_id
                    )
                    dialog.content = ft.Text(
                        "Could not delete the Minecraft version or update its launcher profile."
                    )
                    page.update()
                    return

                installations[:] = updated_installations
                if launch_selection["key"] in matching_ids:
                    launch_selection["key"] = (
                        "latest-release" if latest_release_id else ""
                    )
                    save_profile_options(
                        player_name_state["value"],
                        launch_selection["key"],
                        profile_picture_state["path"],
                    )
                close_dialog_with_blur(dialog)
                navigate("installations", force=True)
                notify(f"Deleted Minecraft version {version_id}.", ft.Icons.DELETE_OUTLINE)

            def close_delete_dialog(dialog):
                close_dialog_with_blur(dialog)

            def delete_profile(profile_id, dialog):
                target_profile = next(
                    (item for item in installations if item["id"] == profile_id),
                    None,
                )
                if target_profile and target_profile.get("is_instance"):
                    game_dir = target_profile.get("game_directory")
                    if not game_dir and target_profile.get("instance_name"):
                        game_dir = str(
                            Path(minecraft_directory)
                            / "instances"
                            / target_profile["instance_name"]
                        )
                    if game_dir:
                        try:
                            resolved_dir = Path(game_dir).resolve()
                            instances_root = (
                                Path(minecraft_directory) / "instances"
                            ).resolve()
                            if (
                                resolved_dir.is_relative_to(instances_root)
                                and resolved_dir != instances_root
                            ):
                                shutil.rmtree(resolved_dir, ignore_errors=True)
                        except Exception:
                            pass

                if target_profile and target_profile.get("external_profile_id"):
                    handled_external_profile_ids.add(str(target_profile["external_profile_id"]))

                updated_installations = [
                    item for item in installations if item["id"] != profile_id
                ]
                if save_installations(
                    updated_installations,
                    handled_external_profile_ids,
                    latest_release_id,
                ):
                    installations[:] = updated_installations
                    if launch_selection["key"] == profile_id:
                        launch_selection["key"] = (
                            "latest-release" if latest_release_id else ""
                        )
                        save_profile_options(
                            player_name_state["value"],
                            launch_selection["key"],
                            profile_picture_state["path"],
                        )
                    close_dialog_with_blur(dialog)
                    navigate("installations", force=True)
                    if target_profile:
                        notify(f"Removed '{target_profile['name']}'.", ft.Icons.DELETE_OUTLINE)
                else:
                    dialog.content = ft.Text("Could not save profile changes.")
                    page.update()

            launch_targets = {}
            if latest_release_id:
                launch_targets["latest-release"] = {
                    "key": "latest-release",
                    "name": "Latest Release",
                    "loader": "Vanilla",
                    "minecraft_version": latest_release_id,
                    "loader_version": "",
                    "version_id": latest_release_id,
                }
            for installation in installations:
                version_id = get_profile_version_id(installation)
                if not version_id:
                    continue
                launch_targets[installation["id"]] = {
                    **installation,
                    "key": installation["id"],
                    "version_id": version_id,
                }

            if launch_selection["key"] not in launch_targets:
                installed_ids = {
                    version["version_id"] for version in installed_versions
                }
                launch_selection["key"] = next(
                    (
                        key
                        for key, target in launch_targets.items()
                        if target["version_id"] in installed_ids
                    ),
                    next(iter(launch_targets), ""),
                )
                save_profile_options(
                    player_name_state["value"],
                    launch_selection["key"],
                    profile_picture_state["path"],
                )

            selection_rows = []
            play_button_ref = {}
            initial_uname = player_name_state["value"].strip() or "Player"

            account_username_text = ft.Text(
                initial_uname,
                size=15,
                weight=ft.FontWeight.W_600,
                color=theme["text_primary"],
                expand=True,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )

            def build_account_picture(size):
                picture_path = profile_picture_state["path"]
                if picture_path and Path(picture_path).is_file():
                    return ft.Image(
                        src=picture_path,
                        width=size,
                        height=size,
                        fit=ft.BoxFit.COVER,
                        border_radius=9,
                    )
                return ft.Icon(
                    ft.Icons.PERSON_ROUNDED,
                    size=size * 0.62,
                    color=theme["btn_primary"],
                )

            account_avatar = ft.Container(
                width=36,
                height=36,
                border_radius=9,
                bgcolor=ft.Colors.with_opacity(0.14, theme["btn_primary"]),
                # border=ft.Border.all(1.5, color=theme["btn_primary"]),
                alignment=ft.Alignment.CENTER,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=build_account_picture(36),
            )
            account_picture_preview = ft.Container(
                width=48,
                height=48,
                border_radius=9,
                bgcolor=ft.Colors.with_opacity(0.14, theme["btn_primary"]),
                # border=ft.Border.all(1.5, color=theme["btn_primary"]),
                alignment=ft.Alignment.CENTER,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=build_account_picture(48),
            )
            account_picture_status = ft.Text(
                size=12,
                color=theme["text_secondary"],
            )
            remove_account_picture_button_ref = {}

            def refresh_account_picture():
                account_avatar.content = build_account_picture(36)
                account_picture_preview.content = build_account_picture(48)
                for avatar_control in (account_avatar, account_picture_preview):
                    try:
                        avatar_control.update()
                    except Exception:
                        pass
                remove_button = remove_account_picture_button_ref.get("control")
                if remove_button is not None:
                    remove_button.disabled = not bool(profile_picture_state["path"])

            def remove_managed_account_picture(picture_path):
                if not picture_path:
                    return
                old_path = Path(picture_path)
                try:
                    if (
                        old_path.name.startswith("avatar-")
                        and old_path.resolve().parent
                        == profile_pictures_directory.resolve()
                    ):
                        old_path.unlink(missing_ok=True)
                except OSError:
                    pass

            async def choose_account_picture():
                selected_files = await account_picture_picker.pick_files(
                    dialog_title="Choose an account picture",
                    file_type=ft.FilePickerFileType.IMAGE,
                    allowed_extensions=["png", "jpg", "jpeg", "webp", "gif"],
                    allow_multiple=False,
                )
                if not selected_files or not selected_files[0].path:
                    return

                source_path = Path(selected_files[0].path)
                if not source_path.is_file():
                    account_picture_status.value = "The selected image could not be opened."
                    page.update()
                    return

                old_picture_path = profile_picture_state["path"]
                destination = profile_pictures_directory / (
                    f"avatar-{uuid.uuid4().hex}{source_path.suffix.lower()}"
                )
                try:
                    profile_pictures_directory.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source_path, destination)
                except OSError:
                    account_picture_status.value = "Could not copy the selected image."
                    page.update()
                    return

                if not save_profile_options(
                    player_name_state["value"],
                    launch_selection["key"],
                    str(destination),
                ):
                    destination.unlink(missing_ok=True)
                    account_picture_status.value = "Could not save the profile picture."
                    page.update()
                    return

                profile_picture_state["path"] = str(destination)
                refresh_account_picture()
                remove_managed_account_picture(old_picture_path)
                account_picture_status.value = "Profile picture updated."
                page.update()

            def clear_account_picture(e):
                old_picture_path = profile_picture_state["path"]
                if not save_profile_options(
                    player_name_state["value"], launch_selection["key"], ""
                ):
                    account_picture_status.value = "Could not remove the profile picture."
                    page.update()
                    return

                profile_picture_state["path"] = ""
                refresh_account_picture()
                remove_managed_account_picture(old_picture_path)
                account_picture_status.value = "Profile picture removed."
                page.update()

            def update_sidebar_username():
                uname = player_name_state["value"].strip() or "Player"
                account_username_text.value = uname
                try:
                    account_username_text.update()
                except Exception:
                    pass

            def on_account_card_hover(e, container):
                is_hovered = e.data == "true"
                container.bgcolor = (
                    theme.get("card_hover", theme["card"])
                    if is_hovered
                    else theme["card"]
                )
                container.border = ft.Border.all(
                    1,
                    color=theme["btn_primary"] if is_hovered else theme["border"],
                )
                container.update()

            account_card = ft.Container(
                width=210,
                bgcolor=theme["card"],
                border=ft.Border.all(1, color=theme["border"]),
                border_radius=12,
                padding=ft.Padding.symmetric(horizontal=8, vertical=8),
                ink=True,
                tooltip=tooltip("Go to Profiles & Account settings"),
                on_click=lambda e: navigate("options_accounts"),
                content=ft.Column(
                    spacing=8,
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_AROUND,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Text(
                                    "ACCOUNT",
                                    size=11,
                                    weight=ft.FontWeight.BOLD,
                                    color=theme["text_secondary"],
                                ),
                            ],
                        ),
                        ft.Container(
                            padding=ft.Padding.only(left=4, right=4, bottom=4),
                            alignment=ft.Alignment.CENTER,
                            expand=True,
                            content=ft.Row(
                                spacing=10,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    account_avatar,
                                    account_username_text,
                                ],
                            ),
                        ),
                    ],
                ),
            )
            account_card.on_hover = lambda e, c=account_card: on_account_card_hover(e, c)

            launch_status = ft.Text(
                launcher_log_state["status"]
                or (
                    f"Selected {launch_targets[launch_selection['key']]['name']}"
                    if launch_selection["key"] in launch_targets
                    else "Choose a profile to play."
                ),
                size=12,
                color=theme["text_secondary"],
                overflow=ft.TextOverflow.ELLIPSIS,
                expand=True,
            )
            launch_eta = ft.Text(
                launcher_log_state.get("eta", ""),
                size=12,
                color=theme["text_muted"],
                text_align=ft.TextAlign.RIGHT,
            )
            selected_target = launch_targets.get(launch_selection["key"])
            selected_version_name = ft.Text(
                selected_target["name"] if selected_target else "Choose a profile",
                size=25,
                weight=ft.FontWeight.BOLD,
                color=theme["text_primary"],
                overflow=ft.TextOverflow.ELLIPSIS,
                font_family="mojangles"
            )
            selected_version_instance_badge = ft.Container(
                content=ft.Text(
                    "INSTANCE",
                    size=10,
                    weight=ft.FontWeight.BOLD,
                    color=theme["btn_primary"],
                ),
                bgcolor=ft.Colors.with_opacity(
                    0.14, theme["btn_primary"]
                ),
                padding=ft.Padding.symmetric(
                    horizontal=6, vertical=2
                ),
                border_radius=4,
                visible=bool(selected_target and selected_target.get("is_instance")),
            )
            selected_version_title_row = ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    selected_version_name,
                    selected_version_instance_badge,
                ],
            )
            selected_version_summary = ft.Text(
                profile_summary(selected_target) if selected_target else "Your installed versions will appear here.",
                size=14,
                color=theme["text_secondary"],
            )

            def get_selected_version_details(target):
                if target is None:
                    return "No profile selected."
                install_state = (
                    "Installed"
                    if target["version_id"] in local_version_ids
                    else "Will install when you play"
                )
                details = f"Version ID: {target['version_id']}  |  {install_state}"
                if settings.get("show_installation_directory", True):
                    game_directory = target.get("game_directory") or minecraft_directory
                    details += f"\nGame directory: {game_directory}"
                return details

            selected_version_details = ft.Text(
                get_selected_version_details(selected_target),
                size=12,
                color=theme["text_muted"],
                selectable=True,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
            launch_progress = ft.ProgressBar(
                value=launcher_log_state["progress"],
                height=8,
                bgcolor=theme["surface"],
                color=theme["btn_primary"],
                border_radius=8,
                expand=True,
            )

            launcher_log_list = ft.ListView(
                controls=[
                    ft.Text(
                        line,
                        size=11,
                        color=theme["text_primary"],
                        font_family="Consolas",
                        selectable=True,
                    )
                    for line in launcher_log_state["lines"][-300:]
                ]
                or [
                    ft.Text(
                        "No launcher output yet. Launch a game to view its logs.",
                        size=12,
                        color=theme["text_secondary"],
                    )
                ],
                expand=True,
                spacing=3,
                auto_scroll=True,
            )
            launcher_log_state["list"] = launcher_log_list
            launcher_log_orb1, launcher_log_orb2 = create_ambient_gradient_orbs()
            log_back_button = ft.IconButton(
                hover_color=ft.Colors.TRANSPARENT,
                icon=ft.Icons.CLOSE,
                icon_size=15,
                tooltip=tooltip("Close Logs"),
                icon_color=theme["text_secondary"],
                visible=launcher_log_state["open"],
                on_click=lambda e: return_to_selected_installation(),
            )
            open_logs_button = ft.TextButton(
                "Open logs",
                icon=ft.Icons.TERMINAL,
                visible=not launcher_log_state["open"],
                style=ft.ButtonStyle(color=theme["text_secondary"],overlay_color=ft.Colors.TRANSPARENT),
                on_click=lambda e: set_launcher_log_visible(True),
            )
            launcher_log_panel = ft.Column(
                expand=True,
                spacing=10,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        spacing=8,
                        controls=[
                            ft.Icon(
                                ft.Icons.TERMINAL,
                                size=17,
                                color=theme["btn_primary"],
                            ),
                            ft.Text(
                                "MINECRAFT LOG",
                                size=11,
                                weight=ft.FontWeight.BOLD,
                                color=theme["text_secondary"],
                            ),
                            log_back_button,
                        ],
                    ),
                    launcher_log_list,
                ],
            )
            selected_installation_view_ref = {"control": None}
            selected_version_elements_ref = {"control": None}

            def set_launcher_log_visible(visible):
                launcher_log_state["open"] = visible
                log_back_button.visible = visible
                open_logs_button.visible = not visible
                view = selected_installation_view_ref["control"]
                if view is None:
                    page.update()
                    return
                view.content = (
                    launcher_log_panel if visible else selected_installation_content
                )
                view.update()

            def return_to_selected_installation():
                set_launcher_log_visible(False)
                page.update()

            def append_launcher_logs(lines):
                active_log_list = launcher_log_state["list"]
                can_update_ui = (
                    active_log_list is not None
                    and active_page.get("name") == "home"
                    and launcher_log_state["open"]
                )
                for line in lines:
                    message = line.rstrip("\r\n")
                    if not message:
                        continue
                    if not launcher_log_state["lines"] and active_log_list is not None:
                        active_log_list.controls.clear()
                    launcher_log_state["lines"].append(message)
                    if can_update_ui:
                        active_log_list.controls.append(
                            ft.Text(
                                message,
                                size=11,
                                color=theme["text_primary"],
                                font_family="Consolas",
                                selectable=True,
                            )
                        )
                if len(launcher_log_state["lines"]) > 300:
                    del launcher_log_state["lines"][:-300]
                if active_log_list is not None and len(active_log_list.controls) > 300:
                    del active_log_list.controls[:-300]
                if can_update_ui:
                    try:
                        active_log_list.update()
                    except Exception:
                        pass

            async def animate_launcher_log_background():
                if launcher_log_state["animation_running"]:
                    return
                launcher_log_state["animation_running"] = True
                try:
                    await animate_ambient_gradient_orbs(
                        launcher_log_orb1,
                        launcher_log_orb2,
                        lambda: active_page.get("name") == "home",
                    )
                finally:
                    launcher_log_state["animation_running"] = False

            globals()["animate_launcher_log_background"] = animate_launcher_log_background

            def read_game_output(process, log_file, loop):
                pending_lines = []
                last_flush = time.monotonic()
                if process.stdout is None:
                    return
                for line in process.stdout:
                    pending_lines.append(line)
                    if log_file is not None:
                        log_file.write(line)
                    if (
                        len(pending_lines) >= 20
                        or time.monotonic() - last_flush >= 0.1
                    ):
                        loop.call_soon_threadsafe(
                            append_launcher_logs, pending_lines.copy()
                        )
                        pending_lines.clear()
                        last_flush = time.monotonic()
                        if log_file is not None:
                            log_file.flush()
                if pending_lines:
                    loop.call_soon_threadsafe(append_launcher_logs, pending_lines)
                if log_file is not None:
                    log_file.flush()

            def launch_is_ready():
                uname = player_name_state["value"].strip() or "Player"
                return bool(
                    launch_selection["key"] in launch_targets
                    and re.fullmatch(
                        r"[A-Za-z0-9_]{1,16}",
                        uname,
                    )
                )

            card_transition_token = 0

            async def animate_card_selection(target):
                nonlocal card_transition_token
                card_transition_token += 1
                token = card_transition_token

                elem_box = selected_version_elements_ref.get("control")
                if elem_box is None or launcher_log_state["open"]:
                    selected_version_name.value = target["name"]
                    selected_version_instance_badge.visible = bool(target.get("is_instance"))
                    selected_version_summary.value = profile_summary(target)
                    selected_version_details.value = get_selected_version_details(target)
                    try:
                        page.update()
                    except Exception:
                        pass
                    return

                try:
                    elem_box.animate_opacity = ft.Animation(75, ft.AnimationCurve.EASE_OUT)
                    elem_box.opacity = 0.0
                    elem_box.update()
                except Exception:
                    return

                await asyncio.sleep(0.075)
                if card_transition_token != token:
                    return

                selected_version_name.value = target["name"]
                selected_version_instance_badge.visible = bool(target.get("is_instance"))
                selected_version_summary.value = profile_summary(target)
                selected_version_details.value = get_selected_version_details(target)

                try:
                    elem_box.animate_opacity = ft.Animation(150, ft.AnimationCurve.EASE_IN_OUT)
                    elem_box.opacity = 1.0
                    elem_box.update()
                except Exception:
                    pass

            def select_launch_target(key):
                if launcher_log_state["running"]:
                    return
                if launch_selection["key"] == key:
                    return
                launch_selection["key"] = key
                save_profile_options(
                    player_name_state["value"],
                    launch_selection["key"],
                    profile_picture_state["path"],
                )
                selected_target = launch_targets[key]
                for row_key, row, marker, is_installed in selection_rows:
                    is_selected = row_key == key
                    if is_installed:
                        marker.color = (
                            theme["btn_primary"]
                            if is_selected
                            else theme["text_muted"]
                        )
                highlighted_index = selection_index_of(key)
                selection_highlight.visible = highlighted_index >= 0
                if highlighted_index >= 0:
                    selection_highlight.top = highlighted_index * SELECTION_ROW_PITCH
                refresh_launch_button()
                launch_status.value = f"Selected {selected_target['name']}"
                launcher_log_state["status"] = launch_status.value
                page.update()
                page.run_task(animate_card_selection, selected_target)

            def build_launch_row(key, target, icon):
                is_selected = launch_selection["key"] == key
                is_installed = target["version_id"] in local_version_ids
                installation_marker = ft.Icon(
                    ft.Icons.CHECK if is_installed else ft.Icons.DOWNLOAD_OUTLINED,
                    size=16,
                    color=(
                        theme["btn_primary"]
                        if (is_installed and is_selected)
                        else theme["text_muted"]
                    ),
                    tooltip="Installed" if is_installed else "Not installed",
                )
                row = ft.Container(
                    height=38,
                    disabled=launcher_log_state["running"],
                    opacity=0.5 if launcher_log_state["running"] else 1,
                    # The selection glow lives in `selection_highlight` so it can
                    # slide smoothly between rows instead of snapping.
                    bgcolor=ft.Colors.TRANSPARENT,
                    border=ft.Border.only(
                        left=ft.BorderSide(
                            3,
                            ft.Colors.TRANSPARENT,
                        )
                    ),
                    border_radius=6,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=0),
                    on_click=lambda e: select_launch_target(key),
                    content=ft.Row(
                        controls=[
                            ft.Icon(icon, color=theme["text_secondary"], size=18),
                            ft.Text(
                                target["name"],
                                size=14,
                                color=theme["text_primary"],
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                expand=True,
                            ),
                            ft.Text(
                                target.get("minecraft_version", ""),
                                visible=key != "latest-release",
                                size=11,
                                color=theme["text_secondary"],
                                width=46,
                                text_align=ft.TextAlign.RIGHT,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            installation_marker,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
                selection_rows.append((key, row, installation_marker, is_installed))
                return row

            version_rows = []
            if "latest-release" in launch_targets:
                version_rows.append(
                    build_launch_row(
                        "latest-release",
                        launch_targets["latest-release"],
                        ft.Icons.STAR_HALF,
                    )
                )
            version_rows.extend(
                build_launch_row(
                    installation["id"],
                    launch_targets[installation["id"]],
                    (
                        ft.Icons.FOLDER_SPECIAL
                        if installation.get("is_instance")
                        else ft.Icons.INVENTORY_2
                    ),
                )
                for installation in installations
                if installation["id"] in launch_targets
            )

            if launch_selection["key"] not in launch_targets:
                launch_selection["key"] = ""

            SELECTION_ROW_HEIGHT = 38
            SELECTION_ROW_GAP = 3
            SELECTION_ROW_PITCH = SELECTION_ROW_HEIGHT + SELECTION_ROW_GAP

            def selection_index_of(key):
                for index, row_entry in enumerate(selection_rows):
                    if row_entry[0] == key:
                        return index
                return -1

            initial_selection_index = selection_index_of(launch_selection["key"])
            # Sliding glow that sits behind the profile rows and glides
            # to the selected one using an iOS-style animation.
            selection_highlight = ft.Container(
                left=0,
                right=0,
                top=max(initial_selection_index, 0) * SELECTION_ROW_PITCH,
                height=SELECTION_ROW_HEIGHT,
                border_radius=5,
                bgcolor=ft.Colors.with_opacity(0.14, theme["btn_primary"]),
                border=ft.Border.only(
                    left=ft.BorderSide(3, theme["btn_primary"])
                ),
                visible=initial_selection_index >= 0,
                animate_position=ft.Animation(
                    260, ft.AnimationCurve.FAST_OUT_SLOWIN
                ),
            )

            launch_progress_state = {"maximum": 0}

            def refresh_launch_button():
                button = launcher_log_state.get("play_button")
                if button is None:
                    return
                is_running = launcher_log_state["running"]
                is_downloading = launcher_log_state["downloading"]
                is_game_started = launcher_log_state["game_started"]
                is_cancelling = launcher_log_state["cancelling"]
                button.content = (
                    "CANCELLING..."
                    if is_cancelling
                    else "CANCEL"
                    if is_running and is_downloading
                    else "RUNNING..."
                    if is_running and is_game_started
                    else "STARTING..."
                    if is_running
                    else "PLAY"
                )
                button.icon = (
                    ft.Icons.HOURGLASS_TOP
                    if is_cancelling
                    else ft.Icons.CLOSE
                    if is_running and is_downloading
                    else ft.Icons.SPORTS_ESPORTS_OUTLINED
                    if is_running and is_game_started
                    else ft.Icons.HOURGLASS_TOP
                    if is_running
                    else ft.Icons.PLAY_ARROW
                )
                button.disabled = (
                    is_cancelling
                    or (is_running and not is_downloading)
                    or (not is_running and not launch_is_ready())
                )
                try:
                    button.update()
                except Exception:
                    pass

            def update_launch_progress(status=None, value=None, eta=None):
                status_changed = False
                if status is not None:
                    status_str = str(status)
                    status_changed = (launcher_log_state.get("status") != status_str)
                    launcher_log_state["status"] = status_str
                    if launcher_log_state["running"]:
                        if status_str.startswith("Download ") or "Download" in status_str:
                            launcher_log_state["downloading"] = True
                        elif status_str in (
                            "Installation complete",
                            "Building launch command...",
                            "Starting Minecraft...",
                        ):
                            launcher_log_state["downloading"] = False
                            launcher_log_state["eta"] = ""
                if value is not None:
                    launcher_log_state["progress"] = value
                if eta is not None:
                    launcher_log_state["eta"] = eta

                status_control = launcher_log_state.get("status_control")
                progress_control = launcher_log_state.get("progress_control")
                eta_control = launcher_log_state.get("eta_control")
                is_on_home = active_page.get("name") == "home"

                if status is not None and status_control is not None:
                    status_control.value = launcher_log_state["status"]
                    if is_on_home and status_changed:
                        try:
                            status_control.update()
                        except Exception:
                            pass
                if value is not None and progress_control is not None:
                    progress_control.value = value
                    if is_on_home:
                        try:
                            progress_control.update()
                        except Exception:
                            pass
                if eta_control is not None and (eta is not None or status_changed):
                    eta_control.value = launcher_log_state.get("eta", "")
                    if is_on_home:
                        try:
                            eta_control.update()
                        except Exception:
                            pass

                if status_changed:
                    refresh_launch_button()

            async def launch_selected_game():
                if launcher_log_state["running"]:
                    return

                username = player_name_state["value"].strip() or "Player"
                if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", username):
                    launch_status.value = (
                        "Enter a 1-16 character username using letters, numbers, or _."
                    )
                    page.update()
                    return

                target = launch_targets.get(launch_selection["key"])
                if target is None:
                    launch_status.value = "Select a profile from the sidebar first."
                    page.update()
                    return

                player_name_state["value"] = username
                save_profile_options(
                    username,
                    launch_selection["key"],
                    profile_picture_state["path"],
                )
                update_sidebar_username()
                launcher_log_state["running"] = True
                launcher_log_state["game_started"] = False
                launcher_log_state["downloading"] = (
                    target["version_id"] not in local_version_ids
                )
                launcher_log_state["cancelling"] = False
                launcher_log_state["cancel_event"] = threading.Event()
                launcher_log_state["target_version_id"] = target["version_id"]
                launcher_log_state["target_profile_id"] = target.get("id")
                refresh_launch_button()
                new_inst_btn = launcher_log_state.get("new_installation_button")
                if new_inst_btn is not None:
                    new_inst_btn.disabled = True
                    try:
                        new_inst_btn.update()
                    except Exception:
                        pass
                for item in selection_rows:
                    row = item[1]
                    row.disabled = True
                    row.opacity = 0.5
                log_back_button.visible = launcher_log_state["open"]
                launcher_log_state["progress"] = None
                launcher_log_state["status"] = "Preparing Minecraft..."
                launch_progress.value = None
                launcher_log_state["lines"].clear()
                launcher_log_list.controls.clear()
                append_launcher_logs([f"Launching {target['name']}..."])
                if launcher_log_state["downloading"]:
                    set_launcher_log_visible(True)
                update_launch_progress("Preparing Minecraft...")
                loop = asyncio.get_running_loop()
                launch_action = settings["launch_action"]
                log_file = None
                output_task = None

                est_total = 0
                if launcher_log_state["downloading"]:
                    try:
                        est_total = await asyncio.to_thread(
                            estimate_installation_total, target, minecraft_directory
                        )
                    except Exception:
                        est_total = 0

                tracker = InstallationProgressTracker(estimated_total=est_total)
                last_progress_time = 0.0
                download_activity = {
                    "last_event": time.monotonic(),
                    "indeterminate": False,
                }

                def report_status(status):
                    status_str = str(status)
                    loop.call_soon_threadsafe(
                        append_launcher_logs, [f"[Launcher] {status_str}"]
                    )
                    tracker.on_status(status_str)
                    is_complete = status_str == "Installation complete"
                    if status_str.startswith("Download "):
                        download_activity["last_event"] = time.monotonic()
                    ratio = (
                        1.0
                        if is_complete
                        else tracker.highest_ratio
                        if tracker.highest_ratio > 0
                        else None
                    )
                    loop.call_soon_threadsafe(
                        update_launch_progress,
                        status_str,
                        ratio,
                        None if is_complete else tracker.last_eta,
                    )

                def set_progress_maximum(maximum):
                    tracker.on_max(maximum)
                    ratio = tracker.highest_ratio if tracker.highest_ratio > 0 else None
                    loop.call_soon_threadsafe(
                        update_launch_progress, None, ratio, tracker.last_eta
                    )

                def report_progress(current):
                    nonlocal last_progress_time
                    ratio, status_text, eta_text = tracker.on_progress(current)
                    now = time.monotonic()
                    download_activity["last_event"] = now
                    if (now - last_progress_time >= 0.05) or (ratio >= 1.0):
                        last_progress_time = now
                        download_activity["indeterminate"] = False
                        loop.call_soon_threadsafe(
                            update_launch_progress, status_text, ratio, eta_text
                        )

                async def monitor_download_activity():
                    while (
                        launcher_log_state["running"]
                        and launcher_log_state["downloading"]
                    ):
                        await asyncio.sleep(0.5)
                        if (
                            launcher_log_state["downloading"]
                            and not download_activity["indeterminate"]
                            and time.monotonic() - download_activity["last_event"] >= 1.5
                        ):
                            progress_control = launcher_log_state.get(
                                "progress_control"
                            )
                            if progress_control is not None:
                                progress_control.value = None
                                if active_page.get("name") == "home":
                                    try:
                                        progress_control.update()
                                    except Exception:
                                        pass
                                download_activity["indeterminate"] = True

                callback = {
                    "setStatus": report_status,
                    "setMax": set_progress_maximum,
                    "setProgress": report_progress,
                }
                progress_monitor_task = None
                try:
                    progress_monitor_task = asyncio.create_task(
                        monitor_download_activity()
                    )
                    process, log_file = await start_game(
                        target,
                        username,
                        online_available,
                        callback,
                        settings,
                        launcher_log_state["cancel_event"],
                    )
                    launcher_log_state["game_started"] = True
                    launcher_log_state["downloading"] = False
                    launcher_log_state["eta"] = ""
                    update_launch_progress("Starting Minecraft...", 1.0, eta="")
                    refresh_launch_button()
                    output_task = asyncio.create_task(
                        asyncio.to_thread(
                            read_game_output, process, log_file, loop
                        )
                    )
                    if launch_action == "Hide launcher":
                        page.window.visible = False
                        page.update()
                    elif launch_action == "Close launcher":
                        page.run_task(page.window.close)
                    exit_code = await asyncio.to_thread(process.wait)
                    if output_task is not None:
                        await output_task
                    if launch_action == "Hide launcher":
                        page.window.visible = True
                    if exit_code != 0:
                        set_launcher_log_visible(True)
                    update_launch_progress(
                        "Minecraft closed."
                        if exit_code == 0
                        else f"Minecraft exited with code {exit_code}.",
                        0,
                        eta="",
                    )
                    append_launcher_logs(
                        [f"[Launcher] Minecraft exited with code {exit_code}."]
                    )
                except DownloadCancelled:
                    update_launch_progress("Version download cancelled.", 0, eta="")
                    append_launcher_logs(["[Launcher] Version download cancelled."])
                except Exception as error:
                    set_launcher_log_visible(True)
                    if launch_action == "Hide launcher":
                        page.window.visible = True
                    update_launch_progress(f"Could not launch Minecraft: {error}", 0, eta="")
                    append_launcher_logs([f"[Launcher] Launch failed: {error}"])
                finally:
                    if progress_monitor_task is not None:
                        progress_monitor_task.cancel()
                        try:
                            await progress_monitor_task
                        except asyncio.CancelledError:
                            pass
                    if log_file is not None:
                        log_file.close()
                    launcher_log_state["running"] = False
                    launcher_log_state["downloading"] = False
                    launcher_log_state["game_started"] = False
                    launcher_log_state["cancelling"] = False
                    launcher_log_state["cancel_event"] = None
                    launcher_log_state["target_version_id"] = None
                    launcher_log_state["target_profile_id"] = None
                    log_back_button.visible = True
                    refresh_launch_button()
                    new_inst_btn = launcher_log_state.get("new_installation_button")
                    if new_inst_btn is not None:
                        new_inst_btn.disabled = not online_available
                        try:
                            new_inst_btn.update()
                        except Exception:
                            pass
                    for item in selection_rows:
                        row = item[1]
                        row.disabled = False
                        row.opacity = 1
                    if launch_action != "Close launcher":
                        page.update()
                        navigate(
                            active_page["name"],
                            active_page.get("profile_id"),
                            force=True,
                        )

            async def handle_play_button_action():
                if launcher_log_state["running"]:
                    if launcher_log_state["downloading"]:
                        launcher_log_state["cancelling"] = True
                        cancel_event = launcher_log_state["cancel_event"]
                        if cancel_event is not None:
                            cancel_event.set()
                        append_launcher_logs(
                            ["[Launcher] Cancellation requested; finishing the current file..."]
                        )
                        update_launch_progress(
                            "Cancelling after the current file...", None
                        )
                    return
                await launch_selected_game()

            play_button = ft.Button(
                "CANCEL" if launcher_log_state["running"] and launcher_log_state["downloading"] else "RUNNING..." if launcher_log_state["running"] and launcher_log_state["game_started"] else "STARTING..." if launcher_log_state["running"] else "PLAY",
                icon=(
                    ft.Icons.CLOSE
                    if launcher_log_state["running"] and launcher_log_state["downloading"]
                    else ft.Icons.SPORTS_ESPORTS_OUTLINED
                    if launcher_log_state["running"] and launcher_log_state["game_started"]
                    else ft.Icons.HOURGLASS_TOP
                    if launcher_log_state["running"]
                    else ft.Icons.PLAY_ARROW
                ),
                style=ft.ButtonStyle(
                    bgcolor={
                        ft.ControlState.DEFAULT: theme["btn_primary"],
                        ft.ControlState.DISABLED: theme["surface"],
                        
                    },
                    icon_size=20,
                    color={
                        ft.ControlState.DEFAULT: theme["btn_primary_text"],
                        ft.ControlState.DISABLED: theme["text_muted"],
                    },
                    shape=ft.RoundedRectangleBorder(radius=8),
                    text_style=ft.TextStyle(
                        weight=ft.FontWeight.BOLD,
                        size=15,
                        letter_spacing=0.5,
                    ),
                ),
                disabled=(
                    launcher_log_state["cancelling"]
                    or (
                        launcher_log_state["running"]
                        and not launcher_log_state["downloading"]
                    )
                    or (not launcher_log_state["running"] and not launch_is_ready())
                ),
                width=220,
                height=52,
                on_click=lambda e: page.run_task(handle_play_button_action),
            )
            play_button_ref["control"] = play_button
            launcher_log_state["play_button"] = play_button
            launcher_log_state["refresh_play_button"] = refresh_launch_button
            refresh_launch_button()

            # Installed configurations vs unconfigured local versions
            installed_profiles = [
                p
                for p in installations
                if get_profile_version_id(p) in local_version_ids
            ]
            configured_version_ids = {
                get_profile_version_id(p) for p in installations
            }
            unconfigured_versions = [
                v
                for v in installed_versions
                if v["version_id"] not in configured_version_ids
            ]

            installed_rows = []
            for idx, profile in enumerate(installed_profiles):
                is_last = idx == len(installed_profiles) - 1
                is_inst = bool(profile.get("is_instance"))
                icon = (
                    ft.Icons.FOLDER_SPECIAL
                    if is_inst
                    else ft.Icons.INVENTORY_2
                )
                title_row = [
                    ft.Text(
                        profile["name"],
                        size=14,
                        weight=ft.FontWeight.W_500,
                        color=theme["text_primary"],
                    ),
                ]
                if is_inst:
                    title_row.append(
                        ft.Container(
                            content=ft.Text(
                                "INSTANCE",
                                size=10,
                                weight=ft.FontWeight.BOLD,
                                color=theme["btn_primary"],
                            ),
                            bgcolor=ft.Colors.with_opacity(
                                0.14, theme["btn_primary"]
                            ),
                            padding=ft.Padding.symmetric(
                                horizontal=6, vertical=2
                            ),
                            border_radius=4,
                        )
                    )

                row_controls = [
                    ft.Icon(
                        icon,
                        color=theme["text_secondary"],
                        size=24,
                    ),
                    ft.Column(
                        spacing=2,
                        controls=[
                            ft.Row(
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=title_row,
                            ),
                            ft.Text(
                                profile_summary(profile),
                                size=12,
                                color=theme["text_secondary"],
                            ),
                        ],
                        expand=True,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                        tooltip=tooltip("Open game directory"),
                        icon_color=theme["text_secondary"],
                        style=rounded_button_style,
                        on_click=lambda e, d=profile.get(
                            "game_directory"
                        ): open_game_folder(d),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.EDIT_OUTLINED,
                        tooltip=tooltip(f"Edit {profile['name']}"),
                        icon_color=theme["text_secondary"],
                        style=rounded_button_style,
                        disabled=is_active_launch_version(
                            get_profile_version_id(profile), profile["id"]
                        ),
                        on_click=lambda e, selected_id=profile["id"]: navigate(
                            "edit_installation", selected_id
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE if is_inst else ft.Icons.CLOSE,
                        tooltip=tooltip(
                            f"Delete instance {profile['name']} (permanent)"
                            if is_inst
                            else f"Remove {profile['name']} from profiles"
                        ),
                        icon_color=theme["text_secondary"],
                        style=rounded_button_style,
                        disabled=is_active_launch_version(
                            get_profile_version_id(profile), profile["id"]
                        ),
                        on_click=lambda e, selected_id=profile[
                            "id"
                        ]: confirm_delete(selected_id),
                    ),
                ]
                installed_rows.append(
                    ft.Container(
                        padding=ft.Padding.symmetric(vertical=12, horizontal=16),
                        border=None if is_last else ft.Border.only(
                            bottom=ft.BorderSide(1, color=theme["border"])
                        ),
                        content=ft.Row(
                            controls=row_controls,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    )
                )

            if not installed_rows:
                installed_rows.append(
                    ft.Container(
                        padding=ft.Padding.symmetric(vertical=24, horizontal=20),
                        alignment=ft.Alignment.CENTER,
                        content=ft.Text(
                            "No local profiles or instances found",
                            color=theme["text_muted"],
                            size=13,
                        ),
                    )
                )

            unconfigured_rows = []
            for idx, version in enumerate(unconfigured_versions):
                is_last = idx == len(unconfigured_versions) - 1
                row_controls = [
                    ft.Icon(
                        ft.Icons.WARNING_AMBER_ROUNDED,
                        color=ft.Colors.ORANGE_400,
                        size=24,
                    ),
                    ft.Column(
                        spacing=2,
                        controls=[
                            ft.Row(
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Text(
                                        version["version_id"],
                                        size=14,
                                        weight=ft.FontWeight.W_500,
                                        color=theme["text_primary"],
                                    ),
                                    ft.Container(
                                        content=ft.Text(
                                            "RAW VERSION",
                                            size=9,
                                            weight=ft.FontWeight.BOLD,
                                            color=ft.Colors.ORANGE_300,
                                        ),
                                        bgcolor=ft.Colors.with_opacity(
                                            0.14, ft.Colors.ORANGE_400
                                        ),
                                        padding=ft.Padding.symmetric(
                                            horizontal=5, vertical=1
                                        ),
                                        border_radius=4,
                                    ),
                                ],
                            ),
                            ft.Text(
                                profile_summary(version),
                                size=12,
                                color=theme["text_secondary"],
                            ),
                        ],
                        expand=True,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.INFO_OUTLINE,
                        icon_size=15,
                        tooltip=tooltip(f"About unconfigured version {version['version_id']}"),
                        icon_color=theme["text_muted"],
                        style=rounded_button_style,
                        hover_color=ft.Colors.TRANSPARENT,
                        on_click=lambda e, vid=version[
                            "version_id"
                        ]: show_unconfigured_version_info(vid),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.ADD_CIRCLE_OUTLINE,
                        tooltip=tooltip(f"Configure {version['version_id']} as a profile"),
                        icon_color=theme["btn_primary"],
                        style=rounded_button_style,
                        disabled=is_active_launch_version(version["version_id"]),
                        on_click=lambda e, selected_version=version: navigate(
                            "configure_installed", selected_version["version_id"]
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_FOREVER,
                        tooltip=tooltip(f"Permanently delete {version['version_id']} from Minecraft"),
                        icon_color=ft.Colors.RED_400,
                        style=rounded_button_style,
                        disabled=is_active_launch_version(version["version_id"]),
                        on_click=lambda e, version_id=version[
                            "version_id"
                        ]: confirm_delete_installed_version(version_id),
                    ),
                ]
                unconfigured_rows.append(
                    ft.Container(
                        padding=ft.Padding.symmetric(vertical=12, horizontal=16),
                        border=None if is_last else ft.Border.only(
                            bottom=ft.BorderSide(1, color=theme["border"])
                        ),
                        content=ft.Row(
                            controls=row_controls,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    )
                )

            pending_rows = []
            for idx, profile in enumerate(pending_profiles):
                is_last = idx == len(pending_profiles) - 1
                pending_rows.append(
                    ft.Container(
                        padding=ft.Padding.symmetric(vertical=12, horizontal=16),
                        border=None if is_last else ft.Border.only(
                            bottom=ft.BorderSide(1, color=theme["border"])
                        ),
                        content=ft.Row(
                            controls=[
                                ft.Icon(
                                    ft.Icons.FOLDER_SPECIAL
                                    if profile.get("is_instance")
                                    else ft.Icons.DOWNLOAD,
                                    color=theme["text_muted"],
                                    size=24,
                                ),
                                ft.Column(
                                    spacing=2,
                                    controls=[
                                        ft.Row(
                                            spacing=8,
                                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                            controls=[
                                                ft.Text(
                                                    profile["name"],
                                                    color=theme["text_primary"],
                                                    size=14,
                                                    weight=ft.FontWeight.W_500,
                                                ),
                                                *(
                                                    [
                                                        ft.Container(
                                                            content=ft.Text(
                                                                "INSTANCE",
                                                                size=10,
                                                                weight=ft.FontWeight.BOLD,
                                                                color=theme["btn_primary"],
                                                            ),
                                                            bgcolor=ft.Colors.with_opacity(
                                                                0.14, theme["btn_primary"]
                                                            ),
                                                            padding=ft.Padding.symmetric(
                                                                horizontal=6, vertical=2
                                                            ),
                                                            border_radius=4,
                                                        )
                                                    ]
                                                    if profile.get("is_instance")
                                                    else []
                                                ),
                                            ],
                                        ),
                                        ft.Text(
                                            profile_summary(profile),
                                            color=theme["text_secondary"],
                                            size=12,
                                        ),
                                    ],
                                    expand=True,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                                    tooltip=tooltip("Open game directory"),
                                    icon_color=theme["text_secondary"],
                                    style=rounded_button_style,
                                    on_click=lambda e, d=profile.get(
                                        "game_directory"
                                    ): open_game_folder(d),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.EDIT_OUTLINED,
                                    tooltip=tooltip(f"Edit {profile['name']}"),
                                    icon_color=theme["text_secondary"],
                                    style=rounded_button_style,
                                    disabled=is_active_launch_version(
                                        get_profile_version_id(profile), profile["id"]
                                    ),
                                    on_click=lambda e, selected_id=profile[
                                        "id"
                                    ]: navigate("edit_installation", selected_id),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE_OUTLINE if profile.get("is_instance") else ft.Icons.CLOSE,
                                    tooltip=tooltip(
                                        f"Delete instance {profile['name']} (permanent)"
                                        if profile.get("is_instance")
                                        else f"Remove {profile['name']} from profiles"
                                    ),
                                    icon_color=theme["text_secondary"],
                                    style=rounded_button_style,
                                    disabled=is_active_launch_version(
                                        get_profile_version_id(profile), profile["id"]
                                    ),
                                    on_click=lambda e, selected_id=profile[
                                        "id"
                                    ]: confirm_delete(selected_id),
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    )
                )
            def on_setting_card_hover(e, container):
                is_hovered = e.data == "true"
                container.bgcolor = (
                    theme.get("card_hover", theme["card"])
                    if is_hovered
                    else theme["surface"]
                )
                container.border = ft.Border.all(
                    1,
                    color=theme["btn_primary"] if is_hovered else theme["border"],
                )
                container.update()

            def build_option_card(category):
                card = ft.Container(
                    width=170,
                    height=130,
                    bgcolor=theme["surface"],
                    border=ft.Border.all(1, color=theme["border"]),
                    border_radius=14,
                    ink=True,
                    alignment=ft.Alignment.CENTER,
                    on_click=lambda e, opt_id=category["id"]: navigate(opt_id),
                    content=ft.Column(
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=12,
                        controls=[
                            ft.Icon(
                                category["icon"],
                                size=34,
                                color=theme["btn_primary"],
                            ),
                            ft.Text(
                                category["title"],
                                size=16,
                                weight=ft.FontWeight.BOLD,
                                color=theme["text_primary"],
                            ),
                        ],
                    ),
                )
                card.on_hover = lambda e, c=card: on_setting_card_hover(e, c)
                return card

            options_categories = [
                {
                    "id": "options_ui",
                    "title": "UI",
                    "icon": ft.Icons.PALETTE_OUTLINED,
                },
                {
                    "id": "options_java",
                    "title": "Java",
                    "icon": ft.Icons.MEMORY,
                },
                {
                    "id": "options_game",
                    "title": "Game",
                    "icon": ft.Icons.SPORTS_ESPORTS_OUTLINED,
                },
                {
                    "id": "options_accounts",
                    "title": "Accounts",
                    "icon": ft.Icons.PERSON_OUTLINE,
                },
                {
                    "id": "options_launcher",
                    "title": "Launcher",
                    "icon": ft.Icons.TUNE,
                },
                {
                    "id": "options_about",
                    "title": "About",
                    "icon": ft.Icons.INFO_OUTLINE,
                },
            ]

            options_orb1, options_orb2 = create_ambient_gradient_orbs()
            options_dashboard = ft.Stack(
                expand=True,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                controls=[
                    options_orb1,
                    options_orb2,
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment.CENTER,
                        content=ft.Column(
                            alignment=ft.MainAxisAlignment.CENTER,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=20,
                            controls=[
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.CENTER,
                                    spacing=20,
                                    controls=[
                                        build_option_card(category)
                                        for category in options_categories[:3]
                                    ],
                                ),
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.CENTER,
                                    spacing=20,
                                    controls=[
                                        build_option_card(category)
                                        for category in options_categories[3:]
                                    ],
                                ),
                                ft.Container(
                                    margin=ft.Margin.only(top=16),
                                    padding=ft.Padding.symmetric(horizontal=32),
                                    content=ft.Text(
                                        f"\"{get_random_thought()}\"",
                                        size=12,
                                        italic=True,
                                        color=theme["text_muted"],
                                        text_align=ft.TextAlign.CENTER,
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
            )

            async def animate_options_background():
                if options_background_state["animation_running"]:
                    return
                options_background_state["animation_running"] = True
                try:
                    await animate_ambient_gradient_orbs(
                        options_orb1,
                        options_orb2,
                        lambda: active_page.get("name") == "options",
                    )
                finally:
                    options_background_state["animation_running"] = False

            globals()["animate_options_background"] = animate_options_background

            def build_setting_container(
                title: str,
                explanation: str,
                control: ft.Control | None = None,
                extra_content: ft.Control | None = None,
            ) -> ft.Container:
                items = [
                    ft.Text(
                        title,
                        size=15,
                        weight=ft.FontWeight.BOLD,
                        color=theme["text_primary"],
                    ),
                    ft.Text(
                        explanation,
                        size=12,
                        color=theme["text_secondary"],
                    ),
                ]
                if control is not None:
                    items.append(
                        ft.Container(
                            content=control,
                            padding=ft.Padding.only(top=4),
                        )
                    )
                if extra_content is not None:
                    items.append(
                        ft.Container(
                            content=extra_content,
                            padding=ft.Padding.only(top=6),
                        )
                    )

                return ft.Container(
                    bgcolor=theme["surface"],
                    border=ft.Border.all(1, color=theme["border"]),
                    border_radius=12,
                    padding=ft.Padding.symmetric(horizontal=20, vertical=16),
                    content=ft.Column(
                        spacing=8,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                        controls=items,
                    ),
                )

            def build_settings_page(
                title: str,
                setting_items: list[ft.Container],
                on_reset=None,
            ) -> ft.Container:
                return ft.Container(
                    expand=True,
                    padding=ft.Padding.only(left=28, top=20, right=28, bottom=20),
                    content=ft.Column(
                        expand=True,
                        spacing=16,
                        scroll=ft.ScrollMode.AUTO,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.START,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                spacing=8,
                                controls=[
                                    ft.IconButton(
                                        icon=ft.Icons.ARROW_BACK,
                                        tooltip=tooltip("Back to Options"),
                                        icon_color=theme["text_secondary"],
                                        hover_color=theme["card_hover"],
                                        icon_size=20,
                                        style=rounded_button_style,
                                        on_click=lambda e: navigate("options"),
                                    ),
                                    ft.Text(
                                        title,
                                        size=24,
                                        weight=ft.FontWeight.BOLD,
                                        color=theme["text_primary"],
                                    ),
                                    *(
                                        [
                                            ft.IconButton(
                                                icon=ft.Icons.RESTORE,
                                                tooltip="Reset this page to defaults",
                                                icon_color=theme["text_secondary"],
                                                on_click=on_reset,
                                            )
                                        ]
                                        if on_reset is not None
                                        else []
                                    ),
                                ],
                            ),
                            *setting_items,
                        ],
                    ),
                )

            theme_palette_preview = ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(
                        width=28,
                        height=28,
                        border_radius=6,
                        bgcolor=theme["btn_primary"],
                        border=ft.Border.all(1, theme["border"]),
                        tooltip="Accent",
                    ),
                    ft.Container(
                        width=28,
                        height=28,
                        border_radius=6,
                        bgcolor=theme["card"],
                        border=ft.Border.all(1, theme["border"]),
                        tooltip="Card",
                    ),
                    ft.Container(
                        width=28,
                        height=28,
                        border_radius=6,
                        bgcolor=theme["surface"],
                        border=ft.Border.all(1, theme["border"]),
                        tooltip="Surface",
                    ),
                    ft.Container(
                        width=28,
                        height=28,
                        border_radius=6,
                        bgcolor=theme["bg"],
                        border=ft.Border.all(1, theme["border"]),
                        tooltip="Background",
                    ),
                    ft.Text(
                        f"Active Palette: {selected_theme['name']}",
                        size=11,
                        color=theme["text_muted"],
                    ),
                ],
            )

            def rebuild_settings_page(message):
                page.controls.clear()
                build_page(selected_theme["name"], active_page["name"])
                notify(message, ft.Icons.RESTORE)

            def reset_settings_fields(field_names, section_name):
                for field_name in field_names:
                    settings[field_name] = DEFAULT_SETTINGS[field_name]
                save_settings(settings)
                rebuild_settings_page(f"{section_name} settings restored to defaults.")

            def reset_ui_settings(e):
                default_theme = (
                    DEFAULT_THEME
                    if DEFAULT_THEME in available_themes
                    else available_themes[0]
                )
                save_theme_choice(default_theme)
                selected_theme["name"] = default_theme
                settings["show_installation_directory"] = DEFAULT_SETTINGS[
                    "show_installation_directory"
                ]
                settings["enable_orbs"] = DEFAULT_SETTINGS["enable_orbs"]
                save_settings(settings)
                page.controls.clear()
                build_page(default_theme, active_page["name"])
                notify("UI settings restored to defaults.", ft.Icons.RESTORE)

            ui_subpage = build_settings_page(
                "UI",
                [
                    build_setting_container(
                        "Theme Selection",
                        "Choose the color palette for the launcher interface. Updates immediately.",
                        control=theme_dropdown,
                        extra_content=theme_palette_preview,
                    ),
                    build_setting_container(
                        "Show Profile Directory",
                        "Display the game directory path in the selected profile card on the home page.",
                        control=build_themed_switch(
                            label="Show profile directory",
                            value=settings.get("show_installation_directory", True),
                            on_change=lambda e: persist_setting(
                                "show_installation_directory", e.control.value
                            ),
                        ),
                    ),
                    build_setting_container(
                        "Ambient Background Orbs",
                        "Display subtle animated gradient orbs in the background of screens.",
                        control=build_themed_switch(
                            label="Enable background orbs",
                            value=settings.get("enable_orbs", True),
                            on_change=lambda e: set_orbs_enabled(e.control.value),
                        ),
                    ),
                ],
                on_reset=reset_ui_settings,
            )

            ram_input_ref = {}

            def set_ram_value(value):
                value = min(maximum_memory_mb, max(256, int(value)))
                settings["ram_mb"] = value
                save_settings(settings)
                field = ram_input_ref.get("control")
                if field is not None and field.value != str(value):
                    field.value = str(value)
                    field.update()

            def on_ram_slider_change(e):
                set_ram_value(round(e.control.value))

            def on_ram_input_change(e):
                try:
                    value = int(e.control.value)
                except (TypeError, ValueError):
                    return
                if 256 <= value <= maximum_memory_mb:
                    settings["ram_mb"] = value
                    save_settings(settings)
                    ram_slider.value = value
                    ram_slider.update()

            def on_ram_input_blur(e):
                try:
                    value = int(e.control.value)
                except (TypeError, ValueError):
                    value = int(settings["ram_mb"])
                set_ram_value(value)

            ram_slider = ft.Slider(
                min=256,
                max=max(512, maximum_memory_mb),
                divisions=max(1, (max(512, maximum_memory_mb) - 256) // 256),
                value=settings["ram_mb"],
                active_color=theme["btn_primary"],
                inactive_color=theme["border"],
                expand=True,
                on_change=on_ram_slider_change,
            )
            ram_input = ft.TextField(
                value=str(settings["ram_mb"]),
                width=132,
                suffix=ft.Text("MB", color=theme["text_secondary"]),
                keyboard_type=ft.KeyboardType.NUMBER,
                border_color=theme["border"],
                focused_border_color=theme["btn_primary"],
                color=theme["text_primary"],
                fill_color=theme["card"],
                border_radius=8,
                on_change=on_ram_input_change,
                on_blur=on_ram_input_blur,
            )
            ram_input_ref["control"] = ram_input

            def on_java_path_change(e):
                persist_setting("java_path", e.control.value or "")

            def on_positive_integer_change(setting_key):
                def handler(e):
                    try:
                        value = int(e.control.value)
                    except (TypeError, ValueError):
                        return
                    if value > 0:
                        persist_setting(setting_key, value)

                return handler

            java_subpage = build_settings_page(
                "Java",
                [
                    build_setting_container(
                        "Allocated Memory (RAM)",
                        f"Allocate between 256 MB and {maximum_memory_mb:,} MB of detected system memory.",
                        control=ft.Row(
                            spacing=16,
                            controls=[
                                ram_slider,
                                ram_input,
                            ],
                        ),
                    ),
                    build_setting_container(
                        "Java Executable",
                        "Specify a custom path to a Java binary (java.exe). Leave empty to use system default Java.",
                        control=ft.TextField(
                            hint_text="e.g. C:\\Program Files\\Java\\jdk-21\\bin\\java.exe",
                            border_color=theme["border"],
                            focused_border_color=theme["btn_primary"],
                            color=theme["text_primary"],
                            fill_color=theme["card"],
                            border_radius=8,
                            expand=True,
                            value=settings["java_path"],
                            on_change=on_java_path_change,
                        ),
                    ),
                ],
                on_reset=lambda e: reset_settings_fields(
                    ("ram_mb", "java_path"), "Java"
                ),
            )

            game_subpage = build_settings_page(
                "Game",
                [
                    build_setting_container(
                        "Default Window Resolution",
                        "The window resolution applied when Minecraft starts up.",
                        control=ft.Row(
                            spacing=12,
                            controls=[
                                ft.TextField(
                                    value=str(settings["window_width"]),
                                    width=110,
                                    prefix=ft.Text("W: ", color=theme["text_secondary"]),
                                    border_color=theme["border"],
                                    focused_border_color=theme["btn_primary"],
                                    color=theme["text_primary"],
                                    fill_color=theme["card"],
                                    border_radius=8,
                                    on_change=on_positive_integer_change("window_width"),
                                ),
                                ft.TextField(
                                    value=str(settings["window_height"]),
                                    width=110,
                                    prefix=ft.Text("H: ", color=theme["text_secondary"]),
                                    border_color=theme["border"],
                                    focused_border_color=theme["btn_primary"],
                                    color=theme["text_primary"],
                                    fill_color=theme["card"],
                                    border_radius=8,
                                    on_change=on_positive_integer_change("window_height"),
                                ),
                            ],
                        ),
                    ),
                    build_setting_container(
                        "Fullscreen",
                        "Launch Minecraft directly in fullscreen mode.",
                        control=build_themed_switch(
                            label="Start in fullscreen",
                            value=settings["fullscreen"],
                            on_change=lambda e: persist_setting(
                                "fullscreen", e.control.value
                            ),
                        ),
                    ),
                ],
                on_reset=lambda e: reset_settings_fields(
                    ("window_width", "window_height", "fullscreen"), "Game"
                ),
            )

            def on_accounts_username_change(e):
                val = (e.control.value or "").strip()
                player_name_state["value"] = val
                save_profile_options(
                    val,
                    launch_selection["key"],
                    profile_picture_state["path"],
                )
                update_sidebar_username()
                if play_button_ref.get("control") is not None:
                    refresh_launch_button()

            accounts_player_field = ft.TextField(
                label="Player username",
                value=player_name_state["value"] or "Player",
                width=280,
                max_length=16,
                hint_text="1-16 letters, numbers, or _",
                border_color=theme["border"],
                focused_border_color=theme["btn_primary"],
                color=theme["text_primary"],
                label_style=ft.TextStyle(color=theme["text_secondary"], size=13),
                cursor_color=theme["btn_primary"],
                fill_color=theme["card"],
                border_radius=8,
                on_change=on_accounts_username_change,
            )
            remove_account_picture_button = ft.TextButton(
                "Remove picture",
                disabled=not bool(profile_picture_state["path"]),
                style=ft.ButtonStyle(color=theme["text_secondary"],overlay_color=ft.Colors.TRANSPARENT),
                on_click=clear_account_picture,
            )
            remove_account_picture_button_ref["control"] = (
                remove_account_picture_button
            )

            def reset_account_settings(e):
                old_picture_path = profile_picture_state["path"]
                if not save_profile_options(
                    "", launch_selection["key"], ""
                ):
                    notify("Could not reset account settings.", ft.Icons.ERROR_OUTLINE)
                    return
                player_name_state["value"] = ""
                profile_picture_state["path"] = ""
                remove_managed_account_picture(old_picture_path)
                refresh_account_picture()
                rebuild_settings_page("Account settings restored to defaults.")

            accounts_subpage = build_settings_page(
                "Accounts",
                [
                    build_setting_container(
                        "Active Offline Username",
                        "The username used when launching in offline mode.",
                        control=accounts_player_field,
                    ),
                    build_setting_container(
                        "Profile Picture",
                        "Choose an image to show in the account card.",
                        control=ft.Row(
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                account_picture_preview,
                                ft.Column(
                                    spacing=4,
                                    controls=[ft.Container(
                                        ft.Button(
                                            "Choose picture",
                                            icon=ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED,
                                            bgcolor=theme["btn_primary"],
                                            color=theme["btn_primary_text"],
                                            style=rounded_button_style,
                                            on_click=lambda e: page.run_task(
                                                choose_account_picture
                                            ),),padding=ft.Padding.only(left=12),expand = True
                                        ),
                                        ft.Container(
                                        remove_account_picture_button,expand=True,alignment=ft.Alignment.CENTER,padding=ft.Padding.only(left=27))
                                    ],
                                ),
                            ],
                        ),
                        extra_content=account_picture_status,
                    ),
                    build_setting_container(
                        "Microsoft Account Authentication",
                        "Microsoft sign-in is not available during the current development stage.",
                        control=ft.Button(
                            "Not available yet",
                            icon=ft.Icons.ACCOUNT_CIRCLE_OUTLINED,
                            bgcolor=theme["surface"],
                            color=theme["text_muted"],
                            style=rounded_button_style,
                            disabled=True,
                        ),
                    ),
                ],
                on_reset=reset_account_settings,
            )

            launcher_subpage = build_settings_page(
                "Launcher",
                [
                    build_setting_container(
                        "Action on Launch",
                        "Choose what Gr8 Launcher does when Minecraft starts.",
                        control=ft.Dropdown(
                            value=settings["launch_action"],
                            options=[
                                ft.DropdownOption(
                                    key="Hide launcher",
                                    text="Hide launcher (Default)",
                                ),
                                ft.DropdownOption(
                                    key="Keep launcher open",
                                    text="Keep launcher open",
                                ),
                                ft.DropdownOption(
                                    key="Close launcher",
                                    text="Close launcher",
                                ),
                            ],
                            width=260,
                            border_color=theme["border"],
                            focused_border_color=theme["btn_primary"],
                            color=theme["text_primary"],
                            fill_color=theme["card"],
                            border_radius=8,
                            on_select=lambda e: persist_setting(
                                "launch_action", e.control.value
                            ),
                        ),
                    ),
                    build_setting_container(
                        "Log Capture",
                        "Capture standard output to log files for crash inspection.",
                        control=build_themed_switch(
                            label="Record logs to disk",
                            value=settings["capture_logs"],
                            on_change=lambda e: persist_setting(
                                "capture_logs", e.control.value
                            ),
                        ),
                    ),
                ],
                on_reset=lambda e: reset_settings_fields(
                    ("launch_action", "capture_logs"), "Launcher"
                ),
            )

            def on_card_hover(e, container):
                is_hovered = e.data == "true"
                container.border = ft.Border.all(
                    1,
                    color=theme["btn_primary"] if is_hovered else theme["border"],
                )
                container.update()

            def mini_badge(text, color=None):
                return ft.Container(
                    bgcolor=theme["card"],
                    border=ft.Border.all(1, color=theme["border"]),
                    border_radius=6,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                    content=ft.Text(
                        text,
                        size=11,
                        weight=ft.FontWeight.W_500,
                        color=color or theme["text_secondary"],
                    ),
                )

            # 1. Header Card (Minimal branding & vision)
            about_hero_card = ft.Container(
                bgcolor=theme["surface"],
                border=ft.Border.all(1, color=theme["border"]),
                border_radius=12,
                padding=ft.Padding.all(20),
                content=ft.Column(
                    spacing=12,
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Row(
                                    spacing=0,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Text(
                                            "Gr",
                                            font_family="Mojangles",
                                            size=22,
                                            weight=ft.FontWeight.BOLD,
                                            color=theme["text_primary"],
                                        ),
                                        ft.Text(
                                            "8",
                                            font_family="Mojangles",
                                            size=22,
                                            weight=ft.FontWeight.BOLD,
                                            color=theme["btn_primary"],
                                        ),
                                        ft.Text(
                                            " Launcher",
                                            font_family="Mojangles",
                                            size=22,
                                            weight=ft.FontWeight.BOLD,
                                            color=theme["text_primary"],
                                        ),
                                        ft.Container(width=8),
                                        mini_badge("v1.1.0", color=theme["btn_primary"]),
                                    ],
                                ),
                                mini_badge("Stable", color=theme["btn_primary"]),
                            ],
                        ),
                        ft.Text(
                            "The Gr8 Launcher is a minimal, sleek, functionality-based launcher which targets productivity, less distractions and provides a clean user experience.",
                            size=13,
                            color=theme["text_secondary"],
                        ),
                        ft.Row(
                            spacing=8,
                            controls=[
                                mini_badge("Minimalism"),
                                mini_badge("Functionality"),
                                mini_badge("Clean UX"),
                            ],
                        ),
                    ],
                ),
            )
            about_hero_card.on_hover = lambda e, c=about_hero_card: on_card_hover(e, c)

            # 2. Creator & Links Card (Unified, clean)
            discord_id_pill = ft.Container(
                bgcolor=theme["card"],
                border=ft.Border.all(1, color=theme["border"]),
                border_radius=8,
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                ink=True,
                tooltip=tooltip("Click to copy Discord ID (@jaydeeppatil23)"),
                on_click=lambda e: copy_to_clipboard(
                    "@jaydeeppatil23",
                    "Copied Discord ID to clipboard: @jaydeeppatil23",
                ),
                content=ft.Row(
                    spacing=6,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Icon(
                            ft.Icons.DISCORD,
                            size=16,
                            color=theme["btn_primary"],
                        ),
                        ft.Text(
                            "@jaydeeppatil23",
                            size=12,
                            weight=ft.FontWeight.BOLD,
                            color=theme["text_primary"],
                        ),
                        ft.Icon(
                            ft.Icons.CONTENT_COPY_ROUNDED,
                            size=13,
                            color=theme["text_muted"],
                        ),
                    ],
                ),
            )
            discord_id_pill.on_hover = lambda e, c=discord_id_pill: on_card_hover(e, c)

            creator_social_card = ft.Container(
                bgcolor=theme["surface"],
                border=ft.Border.all(1, color=theme["border"]),
                border_radius=12,
                padding=ft.Padding.all(20),
                content=ft.Column(
                    spacing=16,
                    controls=[
                        # Developer info row
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Row(
                                    spacing=10,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Container(
                                            width=36,
                                            height=36,
                                            border_radius=8,
                                            bgcolor=theme["card"],
                                            border=ft.Border.all(1, color=theme["border"]),
                                            alignment=ft.Alignment.CENTER,
                                            content=ft.Icon(
                                                ft.Icons.CODE_ROUNDED,
                                                size=18,
                                                color=theme["btn_primary"],
                                            ),
                                        ),
                                        ft.Column(
                                            spacing=2,
                                            controls=[
                                                ft.Text(
                                                    "Made with ❤️ by Jaydeep Patil",
                                                    size=14,
                                                    weight=ft.FontWeight.BOLD,
                                                    color=theme["text_primary"],
                                                ),
                                                ft.Text(
                                                    "Creator & Developer",
                                                    size=11,
                                                    color=theme["text_muted"]
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                                discord_id_pill,
                            ],
                        ),
                        ft.Divider(height=1, color=theme["border"]),
                        # Connect & Support links row
                        ft.Row(
                            spacing=12,
                            controls=[
                                # Discord Server item
                                ft.Container(
                                    expand=True,
                                    bgcolor=theme["card"],
                                    border=ft.Border.all(1, color=theme["border"]),
                                    border_radius=10,
                                    padding=ft.Padding.symmetric(horizontal=14, vertical=12),
                                    content=ft.Row(
                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                        controls=[
                                            ft.Row(
                                                spacing=10,
                                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                controls=[
                                                    ft.Icon(
                                                        ft.Icons.DISCORD,
                                                        size=20,
                                                        color=theme["btn_primary"],
                                                    ),
                                                    ft.Column(
                                                        spacing=1,
                                                        controls=[
                                                            ft.Text(
                                                                "Discord Community",
                                                                size=13,
                                                                weight=ft.FontWeight.BOLD,
                                                                color=theme["text_primary"],
                                                            ),
                                                            ft.Text(
                                                                "dsc.gg/_gr8",
                                                                size=11,
                                                                color=theme["text_muted"],
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            ft.Row(
                                                spacing=4,
                                                tight=True,
                                                controls=[
                                                    ft.Button(
                                                        "Join",
                                                        icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                                                        bgcolor=theme["btn_primary"],
                                                        color=theme["btn_primary_text"],
                                                        style=rounded_button_style,
                                                        on_click=lambda e: open_url(
                                                            "https://dsc.gg/_gr8",
                                                            "Opening Discord server: dsc.gg/_gr8",
                                                        ),
                                                    ),
                                                    ft.IconButton(
                                                        icon=ft.Icons.CONTENT_COPY_ROUNDED,
                                                        icon_size=16,
                                                        icon_color=theme["text_secondary"],
                                                        hover_color=theme["card_hover"],
                                                        tooltip=tooltip("Copy invite link"),
                                                        style=rounded_button_style,
                                                        on_click=lambda e: copy_to_clipboard(
                                                            "https://dsc.gg/_gr8",
                                                            "Copied Discord invite: https://dsc.gg/_gr8",
                                                        ),
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ),
                                # Buy Me a Coffee item
                                ft.Container(
                                    expand=True,
                                    bgcolor=theme["card"],
                                    border=ft.Border.all(1, color=theme["border"]),
                                    border_radius=10,
                                    padding=ft.Padding.symmetric(horizontal=14, vertical=12),
                                    content=ft.Row(
                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                        controls=[
                                            ft.Row(
                                                spacing=10,
                                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                controls=[
                                                    ft.Icon(
                                                        ft.Icons.COFFEE_ROUNDED,
                                                        size=20,
                                                        color=theme["btn_primary"],
                                                    ),
                                                    ft.Column(
                                                        spacing=1,
                                                        controls=[
                                                            ft.Text(
                                                                "Buy Me a Coffee",
                                                                size=13,
                                                                weight=ft.FontWeight.BOLD,
                                                                color=theme["text_primary"],
                                                            ),
                                                            ft.Text(
                                                                "buymeacoffee.com/jaydeeeep",
                                                                size=11,
                                                                color=theme["text_muted"],
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            ft.Row(
                                                spacing=4,
                                                tight=True,
                                                controls=[
                                                    ft.Button(
                                                        "Support",
                                                        icon=ft.Icons.FAVORITE_ROUNDED,
                                                        bgcolor=theme["btn_secondary"],
                                                        color=theme["btn_secondary_text"],
                                                        style=rounded_button_style,
                                                        on_click=lambda e: open_url(
                                                            "https://buymeacoffee.com/jaydeeeep",
                                                            "Opening Buy Me a Coffee...",
                                                        ),
                                                    ),
                                                    ft.IconButton(
                                                        icon=ft.Icons.CONTENT_COPY_ROUNDED,
                                                        icon_size=16,
                                                        icon_color=theme["text_secondary"],
                                                        hover_color=theme["card_hover"],
                                                        tooltip=tooltip("Copy donation link"),
                                                        style=rounded_button_style,
                                                        on_click=lambda e: copy_to_clipboard(
                                                            "https://buymeacoffee.com/jaydeeeep",
                                                            "Copied donation link to clipboard!",
                                                        ),
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ),
                            ],
                        ),
                    ],
                ),
            )
            creator_social_card.on_hover = lambda e, c=creator_social_card: on_card_hover(e, c)

            # 3. Game Directory & Specs Card (Clean, minimal)
            storage_specs_card = ft.Container(
                bgcolor=theme["surface"],
                border=ft.Border.all(1, color=theme["border"]),
                border_radius=12,
                padding=ft.Padding.all(20),
                content=ft.Column(
                    spacing=12,
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Row(
                                    spacing=8,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Icon(
                                            ft.Icons.FOLDER_OPEN_OUTLINED,
                                            size=16,
                                            color=theme["btn_primary"],
                                        ),
                                        ft.Text(
                                            "Game Directory",
                                            size=14,
                                            weight=ft.FontWeight.BOLD,
                                            color=theme["text_primary"],
                                        ),
                                    ],
                                ),
                                ft.Text(
                                    f"Palette: {selected_theme['name']}  •  RAM: {settings.get('ram_mb', 4096)} MB",
                                    size=11,
                                    color=theme["text_muted"],
                                ),
                            ],
                        ),
                        ft.Container(
                            bgcolor=theme["card"],
                            border=ft.Border.all(1, color=theme["border"]),
                            border_radius=8,
                            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                            content=ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Text(
                                        str(minecraft_directory),
                                        size=12,
                                        color=theme["text_secondary"],
                                        selectable=True,
                                        expand=True,
                                    ),
                                    ft.Row(
                                        spacing=6,
                                        tight=True,
                                        controls=[
                                            ft.Button(
                                                "Open Folder",
                                                icon=ft.Icons.FOLDER_ROUNDED,
                                                bgcolor=theme["btn_secondary"],
                                                color=theme["btn_secondary_text"],
                                                style=rounded_button_style,
                                                on_click=lambda e: open_game_folder(
                                                    minecraft_directory
                                                ),
                                            ),
                                            ft.IconButton(
                                                icon=ft.Icons.CONTENT_COPY_ROUNDED,
                                                icon_size=16,
                                                icon_color=theme["text_secondary"],
                                                hover_color=theme["card_hover"],
                                                tooltip=tooltip("Copy path"),
                                                style=rounded_button_style,
                                                on_click=lambda e: copy_to_clipboard(
                                                    str(minecraft_directory),
                                                    "Copied game data path to clipboard!",
                                                ),
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ),
                    ],
                ),
            )
            storage_specs_card.on_hover = lambda e, c=storage_specs_card: on_card_hover(e, c)

            about_subpage = build_settings_page(
                "About",
                [
                    about_hero_card,
                    creator_social_card,
                    storage_specs_card,
                ],
            )

            selected_version_elements = ft.Container(
                content=ft.Column(
                    controls=[
                        selected_version_title_row,
                        selected_version_summary,
                        selected_version_details,
                    ],
                    spacing=14,
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                ),
                opacity=1.0,
                animate_opacity=ft.Animation(150, ft.AnimationCurve.EASE_IN_OUT),
            )
            selected_version_elements_ref["control"] = selected_version_elements

            selected_installation_content = ft.Column(
                expand=True,
                alignment=ft.MainAxisAlignment.START,
                horizontal_alignment=ft.CrossAxisAlignment.START,
                spacing=14,
                controls=[
                    ft.Row([open_logs_button], alignment=ft.MainAxisAlignment.END),
                    ft.Row(
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Container(
                                width=40,
                                height=4,
                                bgcolor=theme["btn_primary"],
                                border_radius=2,
                            ),
                            ft.Text(
                                "SELECTED PROFILE",
                                size=11,
                                weight=ft.FontWeight.BOLD,
                                color=theme["text_secondary"],
                            ),
                            ft.Container(
                                expand=True,
                                height=4,
                                bgcolor=theme["btn_primary"],
                                border_radius=2,
                            ),
                        ],
                    ),
                    selected_version_elements,
                ],
            )
            selected_installation_view = ft.Container(
                left=0,
                top=0,
                right=0,
                bottom=0,
                bgcolor=ft.Colors.with_opacity(0.72, theme["surface"]),
                padding=28,
                content=(
                    launcher_log_panel
                    if launcher_log_state["open"]
                    else selected_installation_content
                ),
            )
            selected_installation_card = ft.Container(
                expand=True,
                # border=ft.Border.all(1, theme["border"]),
                border_radius=25,
                bgcolor=ft.Colors.TRANSPARENT,
                padding=1,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=ft.Container(
                    border_radius=24,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                    content=ft.Stack(
                        expand=True,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        controls=[
                            launcher_log_orb1,
                            launcher_log_orb2,
                            selected_installation_view,
                        ],
                    ),
                ),
            )
            selected_installation_view_ref["control"] = selected_installation_view

            new_installation_button = ft.TextButton(
                "New Profile",
                icon=ft.Icons.ADD,
                icon_color={
                    ft.ControlState.DEFAULT: theme["btn_primary_text"],
                    ft.ControlState.DISABLED: theme["text_muted"],
                },
                disabled=(
                    not online_available
                    or launcher_log_state["running"]
                ),
                tooltip=(
                    "Cannot create new profile while a version is installing or launching."
                    if launcher_log_state["running"]
                    else None
                ),
                style=ft.ButtonStyle(
                    bgcolor={
                        ft.ControlState.DEFAULT: theme["btn_primary"],
                        ft.ControlState.DISABLED: theme["surface"],
                    },
                    color={
                        ft.ControlState.DEFAULT: theme["btn_primary_text"],
                        ft.ControlState.DISABLED: theme["text_muted"],
                    },
                    side={
                        ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT),
                        ft.ControlState.DISABLED: ft.BorderSide(1, theme["border"]),
                    },
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
                on_click=lambda e: navigate("new_installation"),
            )
            launcher_log_state["new_installation_button"] = new_installation_button

            danger_zone_controls = []
            if unconfigured_rows:
                danger_zone_content = ft.Column(
                    visible=danger_zone_state["expanded"],
                    spacing=8,
                    controls=[
                        ft.Text(
                            "These raw version folders were detected on your disk in Minecraft's versions directory, but are not configured into Gr8 Launcher profiles. Deleting a version here permanently removes its core jar and json files from your disk.",
                            size=11,
                            color=theme["text_muted"],
                        ),
                        ft.Container(
                            bgcolor=theme["surface"],
                            border=ft.Border.all(
                                1,
                                color=theme["border"],
                            ),
                            border_radius=12,
                            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                            content=ft.Column(
                                spacing=0,
                                controls=unconfigured_rows,
                            ),
                        ),
                    ],
                )

                danger_chevron = ft.Icon(
                    ft.Icons.KEYBOARD_ARROW_UP_ROUNDED
                    if danger_zone_state["expanded"]
                    else ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED,
                    color=theme["text_secondary"],
                    size=20,
                )

                danger_count_badge = ft.Container(
                    content=ft.Text(
                        f"{len(unconfigured_rows)} raw {'version' if len(unconfigured_rows) == 1 else 'versions'}",
                        size=10,
                        weight=ft.FontWeight.W_500,
                        color=theme["text_secondary"],
                    ),
                    bgcolor=theme["card"],
                    border=ft.Border.all(
                        1,
                        color=theme["border"],
                    ),
                    padding=ft.Padding.symmetric(horizontal=7, vertical=2),
                    border_radius=5,
                )

                def toggle_danger_zone(e):
                    danger_zone_state["expanded"] = not danger_zone_state["expanded"]
                    danger_zone_content.visible = danger_zone_state["expanded"]
                    danger_chevron.icon = (
                        ft.Icons.KEYBOARD_ARROW_UP_ROUNDED
                        if danger_zone_state["expanded"]
                        else ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
                    )
                    page.update()

                danger_zone_header = ft.Container(
                    bgcolor=theme["surface"],
                    border=ft.Border.all(
                        1,
                        color=theme["border"],
                    ),
                    border_radius=10,
                    padding=ft.Padding.symmetric(horizontal=14, vertical=12),
                    ink=True,
                    on_click=toggle_danger_zone,
                    tooltip=tooltip("Click to expand or collapse unconfigured raw versions"),
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Row(
                                spacing=10,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Icon(
                                        ft.Icons.UNARCHIVE_ROUNDED,
                                        color=theme["text_secondary"],
                                        size=20,
                                    ),
                                    ft.Column(
                                        spacing=2,
                                        tight=True,
                                        controls=[
                                            ft.Row(
                                                spacing=8,
                                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                controls=[
                                                    ft.Text(
                                                        "UNCONFIGURED RAW VERSIONS",
                                                        size=12,
                                                        color=theme["text_primary"],
                                                        weight=ft.FontWeight.BOLD,
                                                    ),
                                                    danger_count_badge,
                                                ],
                                            ),
                                            ft.Text(
                                                "Hidden by default. Click to expand and manage unconfigured version folders on disk.",
                                                size=11,
                                                color=theme["text_muted"],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            danger_chevron,
                        ],
                    ),
                )

                def on_danger_header_hover(e):
                    is_h = e.data == "true"
                    danger_zone_header.bgcolor = (
                        theme.get("card_hover", theme["card"])
                        if is_h
                        else theme["surface"]
                    )
                    danger_zone_header.border = ft.Border.all(
                        1,
                        color=theme["btn_primary"] if is_h else theme["border"],
                    )
                    danger_zone_header.update()

                danger_zone_header.on_hover = on_danger_header_hover

                danger_zone_controls = [
                    ft.Container(
                        margin=ft.Margin.only(top=16),
                        content=ft.Column(
                            spacing=10,
                            tight=True,
                            controls=[
                                danger_zone_header,
                                danger_zone_content,
                            ],
                        ),
                    )
                ]

            pages = {
                "home": ft.Row(
                    expand=True,
                    spacing=0,
                    controls=[
                        ft.Container(
                            width=230,
                            bgcolor=theme["surface"],
                            # border=ft.Border.only(
                            #     right=ft.BorderSide(1, color=theme["border"])
                            # ),
                            padding=10,
                            content=ft.Column(
                                expand=True,
                                spacing=8,
                                controls=[
                                    profiles_header := ft.Container(
                                        ft.Text(
                                            "PROFILES",
                                            size=12,
                                            weight=ft.FontWeight.BOLD,
                                            color=theme["text_secondary"],
                                        ),
                                        padding=ft.Padding.all(4),
                                        alignment=ft.Alignment.CENTER,
                                    ),
                                    ft.Column(
                                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                                        scroll=ft.ScrollMode.HIDDEN,
                                        expand=True,
                                        controls=[
                                            ft.Stack(
                                                controls=[
                                                    selection_highlight,
                                                    ft.Column(
                                                        controls=version_rows,
                                                        spacing=3,
                                                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                                                    ),
                                                ]
                                            )
                                        ],
                                    ),
                                    manage_profiles_button := ft.Button(
                                        "Manage Profiles",
                                        icon=ft.Icons.SETTINGS,
                                        bgcolor=theme["btn_primary"],
                                        color=theme["btn_primary_text"],
                                        width=210,
                                        style=rounded_button_style,
                                        on_click=lambda e: navigate("installations"),
                                    ),
                                    sidebar_divider := ft.Container(
                                        height=1,
                                        bgcolor=theme["border"],
                                        margin=ft.Margin.symmetric(vertical=4),
                                    ),
                                    account_card,
                                ],
                            ),
                        ),
                        ft.Container(
                            expand=True,
                            padding=ft.Padding(left=28, top=24, right=28, bottom=20),
                            content=ft.Column(
                                expand=True,
                                spacing=20,
                                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                welcome_row := ft.Row(
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Column(
                                            spacing=4,
                                            controls=[
                                                ft.Text(
                                                    "WELCOME BACK",
                                                    size=11,
                                                    weight=ft.FontWeight.BOLD,
                                                    color=theme["text_muted"],
                                                ),
                                                ft.Text(
                                                    "Ready to play?",
                                                    size=25,
                                                    weight=ft.FontWeight.BOLD,
                                                    color=theme["text_secondary"],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                                selected_installation_card,
                                home_status_row := ft.Row(
                                    spacing=24,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Column(
                                            expand=True,
                                            spacing=6,
                                            controls=[
                                                ft.Row(
                                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                    controls=[
                                                        launch_status,
                                                        launch_eta,
                                                    ],
                                                ),
                                                launch_progress,
                                            ],
                                        ),
                                        play_button,
                                    ],
                                ),
                                ],
                            ),
                        ),
                    ],
                ),
                "installations": ft.Container(
                    expand=True,
                    padding=ft.Padding.symmetric(horizontal=26, vertical=18),
                    content=ft.Column(
                        expand=True,
                        spacing=16,
                        controls=[
                            ft.Row(
                                controls=[new_installation_button],
                            ),
                            ft.Column(
                                expand=True,
                                spacing=14,
                                scroll=ft.ScrollMode.AUTO,
                                controls=[
                                    ft.Container(
                                        padding=ft.Padding.only(
                                            left=4, top=4, bottom=0
                                        ),
                                        content=ft.Text(
                                            "INSTALLED PROFILES",
                                            size=12,
                                            color=theme["text_secondary"],
                                            weight=ft.FontWeight.BOLD,
                                        ),
                                    ),
                                    ft.Container(
                                        bgcolor=theme["surface"],
                                        border=ft.Border.all(1, color=theme["border"]),
                                        border_radius=12,
                                        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                                        content=ft.Column(
                                            spacing=0,
                                            controls=installed_rows,
                                        ),
                                    ),
                                    *(
                                        [
                                            ft.Container(
                                                padding=ft.Padding.only(
                                                    left=4, top=14, bottom=0
                                                ),
                                                content=ft.Text(
                                                    "CONFIGURED, NOT INSTALLED",
                                                    size=12,
                                                    color=theme["text_secondary"],
                                                    weight=ft.FontWeight.BOLD,
                                                ),
                                            ),
                                            ft.Container(
                                                bgcolor=theme["surface"],
                                                border=ft.Border.all(1, color=theme["border"]),
                                                border_radius=12,
                                                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                                                content=ft.Column(
                                                    spacing=0,
                                                    controls=pending_rows,
                                                ),
                                            ),
                                        ]
                                        if pending_rows
                                        else []
                                    ),
                                    *danger_zone_controls,
                                ],
                            ),
                        ],
                    ),
                ),
                "options": options_dashboard,
                "options_ui": ui_subpage,
                "options_java": java_subpage,
                "options_game": game_subpage,
                "options_accounts": accounts_subpage,
                "options_launcher": launcher_subpage,
                "options_about": about_subpage,
            }

            # Startup ripple: register the home sections in visual order so
            # they pop in one by one after the first paint.
            prepare_intro(profiles_header)
            prepare_intro(selection_highlight)
            for version_row in version_rows:
                prepare_intro(version_row)
            prepare_intro(manage_profiles_button)
            prepare_intro(sidebar_divider)
            prepare_intro(account_card)
            prepare_intro(welcome_row)
            prepare_intro(selected_installation_card)
            prepare_intro(home_status_row)

            if name == "new_installation":
                pages[name] = build_installation_editor()
            elif name == "edit_installation":
                profile = next(
                    (item for item in installations if item["id"] == profile_id),
                    None,
                )
                if profile is None:
                    return
                pages[name] = build_installation_editor(profile, name_only=True)
            elif name == "configure_installed":
                version = next(
                    (
                        item
                        for item in installed_versions
                        if item["version_id"] == profile_id
                    ),
                    None,
                )
                if version is None:
                    return
                profile = profiles_by_version.get(profile_id) or {
                    "id": uuid.uuid4().hex,
                    "name": version["version_id"],
                    "loader": version["loader"],
                    "channel": "",
                    "minecraft_version": version["minecraft_version"],
                    "loader_version": version["loader_version"],
                    "version_id": version["version_id"],
                }
                pages[name] = build_installation_editor(profile, name_only=True)

            if name == "home":
                launcher_log_state["status_control"] = launch_status
                launcher_log_state["progress_control"] = launch_progress
                launcher_log_state["eta_control"] = launch_eta
                page.run_task(animate_launcher_log_background)
            elif name == "options":
                page.run_task(animate_options_background)

            if page_content.content is None:
                page_content.content = pages[name]
                current_page = name
                if name in tab_names:
                    tab = name
                elif name.startswith("options"):
                    tab = "options"
                else:
                    tab = "installations"
                tabs_buttons.selected_index = tab_names.index(tab)
                page.update()
                return

            if current_page == name and not force:
                return

            transition_id += 1
            current_page = name
            page.run_task(transition_page, name, pages[name], transition_id)

        navigation_state["navigate"] = navigate

        tab_names = ["home", "installations", "options"]

        def on_tab_select(selected_tab):
            current_tab = (
                current_page
                if current_page in tab_names
                else ("options" if current_page and current_page.startswith("options") else "installations")
            )
            if selected_tab == current_tab and current_page != selected_tab:
                return
            if selected_tab != current_page:
                navigate(selected_tab)

        title_letters = []
        title_controls = []
        for character in "Gr8 Launcher":
            if character == " ":
                title_controls.append(ft.Container(width=6))
                continue

            letter = ft.Container(
                content=ft.Text(
                    character,
                    font_family="Mojangles",
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=(
                        theme["btn_primary"]
                        if character == "8"
                        else theme["text_primary"]
                    ),
                ),
                offset=ft.Offset(0, 0),
                animate_offset=ft.Animation(110, ft.AnimationCurve.EASE_OUT),
            )
            title_letters.append(letter)
            title_controls.append(letter)

        is_bouncing = False

        async def bounce_title():
            nonlocal is_bouncing
            if is_bouncing:
                return

            is_bouncing = True
            try:

                async def bounce_letter(letter, delay):
                    await asyncio.sleep(delay)
                    letter.offset = ft.Offset(0, -0.25)
                    page.update()
                    await asyncio.sleep(0.08)

                    letter.offset = ft.Offset(0, 0)
                    page.update()

                await asyncio.gather(
                    *(
                        bounce_letter(letter, index * 0.03)
                        for index, letter in enumerate(title_letters)
                    )
                )
                await asyncio.sleep(0.11)
            finally:
                is_bouncing = False

        def start_title_animation(e):
            page.run_task(bounce_title)

        root_tab = (
            tab
            if tab in tab_names
            else ("options" if tab.startswith("options") else "installations")
        )
        tabs_buttons = NavigationTabs(
            tab_names=tab_names,
            initial_index=tab_names.index(root_tab),
            on_tab_select=on_tab_select,
            app_theme=theme,
            tooltip_func=tooltip,
        )

        title_wrap = prepare_intro(
            ft.Container(
                content=ft.Row(controls=title_controls, spacing=0, tight=True),
                padding=ft.Padding.only(left=15),
                on_click=start_title_animation,
            )
        )
        tabs_pop_wrap = prepare_intro(
            ft.Container(
                content=tabs_buttons,
                alignment=ft.Alignment.CENTER,
            )
        )
        func_buttons_wrap = prepare_intro(
            ft.Container(
                padding=ft.Padding.only(left=8),
                border=ft.Border.only(
                    left=ft.BorderSide(1, color=theme["border"])
                ),
                content=ft.Row(controls=func_buttons, spacing=0, tight=True),
            )
        )

        NAV_SIDE_WIDTH = 200

        navbar_content = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=title_wrap,
                        width=NAV_SIDE_WIDTH,
                        alignment=ft.Alignment.CENTER_LEFT,
                    ),
                    tabs_pop_wrap,
                    ft.Container(
                        content=func_buttons_wrap,
                        width=NAV_SIDE_WIDTH,
                        alignment=ft.Alignment.CENTER_RIGHT,
                    ),
                ],
                spacing=0,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=ft.Colors.TRANSPARENT,
            padding=ft.Padding.only(top=10, bottom=10, left=10, right=10),
        )

        navbar = ft.WindowDragArea(content=navbar_content)

        navigate(tab)  # default page shown on load

        page.add(
            navbar,
            content_area,
        )
        # Kick off the ripple entrance now that the first frame is committed.
        page.run_task(play_intro_ripple)

    async def refresh_version_catalog():
        nonlocal latest_release_id, online_available
        if refresh_state["running"]:
            return

        was_online = online_available
        was_known = connectivity_known["value"]
        old_latest_release_id = latest_release_id
        refresh_state["running"] = True
        try:
            versions, latest, is_online = await asyncio.to_thread(get_version_catalog)
        except Exception:
            versions, latest = get_local_version_catalog()
            is_online = False

        refresh_state["running"] = False
        if is_online:
            version_catalog[:] = versions
            latest_release_id = latest
            save_installations(
                installations, handled_external_profile_ids, latest
            )
            online_available = True
            connectivity_known["value"] = True
            new_inst_btn = launcher_log_state.get("new_installation_button")
            if new_inst_btn is not None:
                new_inst_btn.disabled = False
                try:
                    new_inst_btn.update()
                except Exception:
                    pass
            if was_known and not was_online:
                notify("Internet connection restored.", ft.Icons.WIFI)
                if active_page.get("name") not in ("home", None):
                    while intro_state.get("running"):
                        await asyncio.sleep(0.05)
                    page.controls.clear()
                    build_page(selected_theme["name"], active_page["name"])
            if active_page.get("name") == "home" and (
                not old_latest_release_id
                or old_latest_release_id != latest
                or (was_known and not was_online)
            ):
                while intro_state.get("pending") or intro_state.get("running"):
                    await asyncio.sleep(0.05)
                if navigation_state.get("navigate") and active_page.get("name") == "home":
                    navigation_state["navigate"]("home", force=True)
            return

        online_available = False
        connectivity_known["value"] = True
        new_inst_btn = launcher_log_state.get("new_installation_button")
        if new_inst_btn is not None:
            new_inst_btn.disabled = True
            try:
                new_inst_btn.update()
            except Exception:
                pass
        if was_known and was_online:
            notify(
                "No internet connection. Installed versions remain available.",
                ft.Icons.WIFI_OFF,
            )
            if active_page.get("name") not in ("home", None):
                while intro_state.get("running"):
                    await asyncio.sleep(0.05)
                page.controls.clear()
                build_page(selected_theme["name"], active_page["name"])
        elif not was_known:
            notify(
                "No internet connection. Installed versions remain available.",
                ft.Icons.WIFI_OFF,
            )

    def check_internet_connection():
        try:
            with socket.create_connection(("1.1.1.1", 443), timeout=2):
                return True
        except OSError:
            return False

    async def monitor_connectivity():
        nonlocal online_available
        while True:
            connected = await asyncio.to_thread(check_internet_connection)
            if connected and not online_available:
                await refresh_version_catalog()
            elif not connected and online_available:
                online_available = False
                connectivity_known["value"] = True
                new_inst_btn = launcher_log_state.get("new_installation_button")
                if new_inst_btn is not None:
                    new_inst_btn.disabled = True
                    try:
                        new_inst_btn.update()
                    except Exception:
                        pass
                if active_page.get("name") not in ("home", None):
                    while intro_state.get("running"):
                        await asyncio.sleep(0.05)
                    page.controls.clear()
                    build_page(selected_theme["name"], active_page["name"])
                notify(
                    "No internet connection. Installed versions remain available.",
                    ft.Icons.WIFI_OFF,
                )
            await asyncio.sleep(30)

    build_page(theme_choice)
    page.run_task(refresh_version_catalog)
    page.run_task(monitor_connectivity)

if __name__ == "__main__":
    ft.run(main, assets_dir=str(ASSETS_DIR))
