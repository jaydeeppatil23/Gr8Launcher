import asyncio
import io
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog
import uuid
import webbrowser
from pathlib import Path

import minecraft_launcher_lib
from PIL import Image
import flet as ft

import ely_auth
from launcher import (
    start_game,
    InstallationProgressTracker,
    DownloadCancelled,
    minecraft_directory,
    get_installed_versions,
    get_mod_loader,
    get_version_catalog,
    get_latest_release_version,
    invalidate_installed_versions_cache,
    get_system_memory_mb,
    get_profile_version_id,
    get_profile_game_directory,
    prepare_launch_profile,
    delete_external_profile,
)
import skins
import state
import storage
import profile_creation
from credential_store import redact_credentials
from discord_presence import DiscordPresence, SkinPortraitCache
from launcher_services import (
    check_internet_connection,
    read_game_output,
    sync_local_installations,
    installation_sources_stamp,
)
from ui_motion import StaggeredReveal
from ui_dialogs import ProfileDialogHost
from ui_toasts import ToastHost
from ui_progress import ProgressHost, track_progress
from ui_profiles import ProfileList, profile_card_surface, profile_drag_region, profile_list_surface, profile_row
from ui_mods import ModsPage
from ui_controls import SimpleInput, SimpleButton, AcrylicSelector, launcher_back_button
from skin_viewer import SkinViewer
from home_skin_viewer import HomeSkinViewer

bg_color_main = "#182842"
bg_glow_color = "#454641"  # Warm lively ambient highlight from top-right corner
accent_color = "#f5de9a"
input_bg_color = ft.Colors.BLACK_26
ASSETS_DIR = Path(__file__).resolve().parent / ".assets"
LATEST_RELEASE_PROFILE_ID = "latest-release"
_PROGRESS_UNCHANGED = object()


AVAILABLE_BLOCK_ICONS = [
    ("icon_blocks/grass_block.png", "Grass Block"),
    ("icon_blocks/crafting_table.png", "Crafting Table"),
    ("icon_blocks/furnace.png", "Furnace"),
    ("icon_blocks/chest.png", "Chest"),
    ("icon_blocks/diamond_ore.png", "Diamond Ore"),
    ("icon_blocks/tnt.png", "TNT"),
    ("icon_blocks/netherrack.png", "Netherrack"),
    ("icon_blocks/glass.png", "Glass"),
]

AVAILABLE_FACE_ICONS = [
    ("icon_faces/creeper.png", "Creeper"),
    ("icon_faces/zombie.png", "Zombie"),
    ("icon_faces/skeleton.png", "Skeleton"),
    ("icon_faces/enderman.png", "Enderman"),
    ("icon_faces/pig.png", "Pig"),
    ("icon_faces/sheep.png", "Sheep"),
    ("icon_faces/wolf.png", "Wolf"),
    ("icon_faces/bee.png", "Bee"),
]


DEFAULT_PROFILE_ICON = "icon_blocks/grass_block.png"


def read_app_version() -> str:
    """Best-effort version label for the title bar and About surfaces."""
    candidates = [Path(__file__).resolve().with_name("VERSION")]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().with_name("VERSION"))
    candidates.append(Path.cwd() / "VERSION")
    for candidate in candidates:
        try:
            version = candidate.read_text(encoding="utf-8-sig").strip()
        except OSError:
            continue
        if version:
            return version
    return "dev"


APP_VERSION = read_app_version()


def resolve_profile_icon_src(icon_val: str | None) -> str | None:
    target = icon_val or DEFAULT_PROFILE_ICON
    p = Path(target)
    if p.is_file():
        return str(p)
    icons_dir = ASSETS_DIR / "icons"
    candidate = icons_dir / target
    if candidate.is_file():
        return str(candidate)
    for ext in (".png", ".jpg", ".jpeg"):
        candidate_ext = icons_dir / f"{target}{ext}"
        if candidate_ext.is_file():
            return str(candidate_ext)
    # If custom path failed, fallback to default grass block
    default_candidate = icons_dir / DEFAULT_PROFILE_ICON
    if default_candidate.is_file():
        return str(default_candidate)
    return None


def build_profile_icon_content(
    profile: dict | None,
    size: int = 32,
    default_icon_size: int = 28,
    color: str = accent_color,
) -> ft.Control:
    icon_src = resolve_profile_icon_src((profile or {}).get("icon"))
    if icon_src:
        return ft.Image(
            src=icon_src,
            width=size,
            height=size,
            fit=ft.BoxFit.CONTAIN,
            filter_quality=ft.FilterQuality.NONE,
        )
    return ft.Icon(
        ft.Icons.VIEW_IN_AR_ROUNDED,
        color=color,
        size=default_icon_size,
    )


def main(page: ft.Page):
    discord_presence = DiscordPresence(
        enabled=bool(storage.load_settings().get("discord_rpc", True)),
        portrait_cache=SkinPortraitCache(storage.DATA_DIR / "discord_portraits.json"),
    )
    page.title = "Gr8 Launcher"
    page.window.icon = str(ASSETS_DIR / "icons" / "gr8_icon.ico")
    page.fonts = {
        "ps": str(ASSETS_DIR / "fonts" / "PixelifySans.ttf"),
        "ssr": str(ASSETS_DIR / "fonts" / "Silkscreen-Regular.ttf"),
        "vt323": str(ASSETS_DIR / "fonts" / "VT323-Regular.ttf"),
    }
    page.window.min_width=1100
    page.window.width = 1100
    page.window.min_height=650
    page.window.height = 650
    page.padding = 0
    page.spacing = 0
    page.bgcolor = bg_color_main
    page.window.title_bar_hidden = True
    page.window.title_bar_buttons_hidden = True
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.horizontal_alignment = ft.CrossAxisAlignment.START

    toasts = ToastHost(page)
    progress_host = ProgressHost(page, show_text=False)
    state.progress_state["host"] = progress_host

    def notify(message, kind="info", title=None, **kwargs):
        toasts.show(redact_credentials(str(message)), kind, title, **kwargs)

    state.notification_state["show"] = notify

    def save_settings_with_notification(settings, *, message="Settings saved."):
        if settings == storage.load_settings():
            return True
        if not storage.save_settings(settings):
            notify("Could not save settings. Check the launcher data folder permissions and try again.", "error")
            return False
        notify(message, "success", "Settings", key="settings", delay=0.6)
        return True

    def minimize_window(e):
        page.window.minimized = True
        page.update()

    def maximize_window(e):
        page.window.maximized = not page.window.maximized
        page.update()

    async def close_window(e):
        toasts.close()
        progress_host.close()
        await asyncio.to_thread(discord_presence.stop)
        await page.window.close()

    async def stop_discord_presence(e):
        toasts.close()
        progress_host.close()
        await asyncio.to_thread(discord_presence.stop)

    page.on_disconnect = stop_discord_presence
    page.on_close = stop_discord_presence

    def window_button_hover(e, *, danger: bool = False):
        control = e.control
        control.bgcolor = (
            ft.Colors.with_opacity(0.82, "#d9534f")
            if danger and e.data == "true"
            else ft.Colors.with_opacity(0.14, "#ffffff")
            if e.data == "true"
            else ft.Colors.TRANSPARENT
        )
        try:
            control.update()
        except RuntimeError:
            pass

    minimize_btn = ft.Container(
        content=ft.Text(
            "-",
            font_family="ps",
            color=accent_color,
            size=18,
            text_align=ft.TextAlign.CENTER,
        ),
        width=36,
        height=32,
        border_radius=4,
        alignment=ft.Alignment.CENTER,
        on_click=minimize_window,
        on_hover=lambda e: window_button_hover(e),
        tooltip="Minimize",
    )

    maximize_btn = ft.Container(
        content=ft.Container(
            width=10,
            height=10,
            border=ft.Border.all(1.5, color=accent_color),
            border_radius=1,
        ),
        width=36,
        height=32,
        border_radius=4,
        alignment=ft.Alignment.CENTER,
        on_click=maximize_window,
        on_hover=lambda e: window_button_hover(e),
        tooltip="Maximize",
    )

    close_btn = ft.Container(
        content=ft.Text(
            "X",
            font_family="ps",
            color=accent_color,
            size=14,
            text_align=ft.TextAlign.CENTER,
        ),
        width=36,
        height=32,
        border_radius=4,
        alignment=ft.Alignment.CENTER,
        on_click=close_window,
        on_hover=lambda e: window_button_hover(e, danger=True),
        tooltip="Close",
    )

    title_net_dot = ft.Container(
        width=7, height=7, border_radius=4, bgcolor="#747e95",
        tooltip="Checking connection",
    )
    title_net_text = ft.Text(
        "Checking", font_family="ps", size=11, color="#8b97ab",
    )
    title_net_chip = ft.Container(
        content=ft.Row(
            [title_net_dot, title_net_text],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=ft.Colors.BLACK_26,
        border_radius=4,
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
    )
    custom_title_bar = ft.Container(
        height=40,
        padding=0,
        margin=0,
        opacity=0.0,
        offset=ft.Offset(0, -0.06),
        animate_opacity=ft.Animation(300, ft.AnimationCurve.EASE_OUT_CUBIC),
        animate_offset=ft.Animation(320, ft.AnimationCurve.EASE_OUT_CUBIC),
        content=ft.Row(
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.WindowDragArea(
                    expand=True,
                    content=ft.Container(
                        padding=0,
                        margin=0,
                        alignment=ft.Alignment.CENTER_LEFT,
                        content=ft.Row(
                            spacing=0,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Container(
                                    ft.Row(
                                        [
                                            ft.Image(
                                                src="icons/gr8_icon.png",
                                                width=28,
                                                height=28,
                                            ),
                                            ft.Text(
                                                "Gr8 Launcher",
                                                font_family="ps",
                                                color=accent_color,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                            ft.Container(width=10),
                                            title_net_chip,
                                        ],
                                        spacing=0,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    padding=ft.Padding.only(left=14),
                                ),
                            ],
                        ),
                    ),
                ),
                ft.Container(
                    content=ft.Row(
                        spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                        controls=[
                            minimize_btn,
                            maximize_btn,
                            close_btn,
                        ],
                    ),
                    padding=ft.Padding.only(right=10),
                ),
            ],
        ),
    )

    def update_connection_chip(*, online: bool | None = None, checking: bool = False):
        """Reflect internet connectivity in the custom title bar.

        This describes network access, not the signed-in player: a local
        (offline-mode) account can still be connected to the internet.
        """
        if checking:
            dot_color, label = "#8b97ab", "Checking"
            hint = "Checking internet connection"
        elif online is True:
            dot_color, label = "#81c784", "Connected"
            hint = "Internet available — updates and mod downloads work"
        elif online is False:
            dot_color, label = "#ffb74d", "No Internet"
            hint = "No internet — installed versions still launch"
        else:
            dot_color, label = "#8b97ab", "Unknown"
            hint = "Internet connection status unknown"
        title_net_dot.bgcolor = dot_color
        title_net_dot.tooltip = hint
        title_net_chip.tooltip = hint
        title_net_text.value = label
        title_net_text.color = dot_color if online is not None or checking else "#8b97ab"
        try:
            title_net_chip.update()
        except RuntimeError:
            pass

    def has_saved_account() -> bool:
        if not storage.ACCOUNTS_FILE.exists():
            return False
        try:
            data = storage.load_accounts()
            accounts = data.get("accounts", [])
            if not accounts:
                return False
            if (
                len(accounts) == 1
                and accounts[0].get("id") == "offline_default"
                and accounts[0].get("username") in ("", "Player")
                and accounts[0].get("created_at") == 0
            ):
                return False
            return True
        except Exception:
            return False

    auth_mode = "offline"

    username_field = SimpleInput(hint_text="Username", width=400)
    pass_field = SimpleInput(
        hint_text="Password",
        width=400,
        password=True,
        can_reveal_password=True,
        visible=(auth_mode == "ely_by"),
    )
    totp_field = SimpleInput(
        hint_text="2FA Code",
        width=400,
        visible=False,
    )
    login_error_text = ft.Text(
        "",
        font_family="ps",
        color="#f87171",
        size=13,
        text_align=ft.TextAlign.CENTER,
        visible=False,
    )
    title_icon = ft.Container(
        ft.Image("titles/Gr8_title.png", fit=ft.BoxFit.COVER, width=300),
        alignment=ft.Alignment.CENTER,
    )
    login_title_text = ft.Text("Login", font_family="ps", color=accent_color, size=55)
    login_button_label = ft.Text(
        "Login", font_family="ps", color=bg_color_main, size=20
    )
    login_button = ft.Button(
        login_button_label,
        height=45,
        width=400,
        style=ft.ButtonStyle(
            bgcolor=accent_color,
            overlay_color=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(radius=5),
        ),
    )

    ely_register_btn = ft.TextButton(
        content=ft.Text(
            "Don't have an account?",
            font_family="ps",
            size=13,
        ),
        style=ft.ButtonStyle(
            color={
                ft.ControlState.HOVERED: accent_color,
                ft.ControlState.DEFAULT: "#747e95",
            },
            bgcolor=ft.Colors.TRANSPARENT,
            overlay_color=ft.Colors.TRANSPARENT,
            padding=ft.Padding.symmetric(horizontal=8, vertical=0),
        ),
        visible=(auth_mode == "ely_by"),
        on_click=lambda e: webbrowser.open("https://account.ely.by/register"),
    )

    back_btn = launcher_back_button(
        lambda e: navigate("home"), label="Cancel",
        visible=False,
    )

    ely_indicator = ft.Container(
        width=13,
        height=13,
        border_radius=2,
        bgcolor="#0e1c32" if auth_mode != "ely_by" else accent_color,
    )

    offline_indicator = ft.Container(
        width=13,
        height=13,
        border_radius=2,
        bgcolor=accent_color if auth_mode == "offline" else "#0e1c32",
    )

    def select_auth(mode, update_controls: bool = True):
        nonlocal auth_mode
        auth_mode = mode
        ely_indicator.bgcolor = accent_color if auth_mode == "ely_by" else "#0e1c32"
        offline_indicator.bgcolor = (
            accent_color if auth_mode == "offline" else "#0e1c32"
        )
        pass_field.visible = auth_mode == "ely_by"
        ely_register_btn.visible = auth_mode == "ely_by"
        totp_field.visible = False
        login_error_text.visible = False
        if not update_controls:
            return
        try:
            ely_indicator.update()
            offline_indicator.update()
            pass_field.update()
            totp_field.update()
            login_error_text.update()
            ely_register_btn.update()
        except RuntimeError:
            pass

    ely_option = ft.Container(
        content=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ely_indicator,
                ft.Text("Ely.by", font_family="ps", color=accent_color, size=15),
            ],
        ),
        on_click=lambda e: select_auth("ely_by"),
    )

    offline_option = ft.Container(
        content=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                offline_indicator,
                ft.Text("Offline", font_family="ps", color=accent_color, size=15),
            ],
        ),
        on_click=lambda e: select_auth("offline"),
    )

    login_options = ft.Column(
        spacing=8,
        controls=[
            ely_option,
            offline_option,
        ],
    )

    async def handle_login(e=None):
        nonlocal auth_mode
        if login_button.disabled:
            return
        mode = auth_mode
        uname = (username_field.value or "").strip()
        login_error_text.visible = False
        login_error_text.value = ""
        login_error_text.update()

        if not uname:
            login_error_text.value = "Username cannot be empty."
            login_error_text.visible = True
            login_error_text.update()
            notify(login_error_text.value, "warning", "Sign in")
            return

        if mode == "offline":
            offline_acc = {
                "id": f"offline_{uname.lower()}",
                "type": "offline",
                "username": uname,
                "created_at": time.time(),
            }
            login_button.disabled = True
            login_button.update()
            account_activity = progress_host.begin("Saving account...", priority=60)
            try:
                saved = await asyncio.to_thread(storage.add_or_update_account, offline_acc, set_as_active=True)
            finally:
                account_activity.finish()
                login_button.disabled = False
                login_button.update()
            if not saved:
                login_error_text.value = "Could not save the account. Check the launcher data folder permissions and try again."
                login_error_text.visible = True
                login_error_text.update()
                notify(login_error_text.value, "error", "Account could not be saved")
                return
            rebuild_accounts_view()
            username_field.value = ""
            pass_field.value = ""
            totp_field.value = ""
            totp_field.visible = False
            login_error_text.visible = False
            navigate("home")
            notify(f"Signed in as {uname}.", "success", "Welcome")
            return

        # Ely.by auth
        pwd = pass_field.value or ""
        if not pwd:
            login_error_text.value = "Please enter your password."
            login_error_text.visible = True
            login_error_text.update()
            notify(login_error_text.value, "warning", "Sign in")
            return

        login_button.disabled = True
        login_button_label.value = "Logging in..."
        login_button.update()

        totp = (totp_field.value or "").strip() if totp_field.visible else None
        auth_activity = progress_host.begin("Signing in to Ely.by...", priority=80)

        try:
            auth_data = await asyncio.to_thread(ely_auth.authenticate, uname, pwd, totp)
            new_acc = {
                "id": f"ely_{auth_data['uuid']}",
                "type": "ely",
                "username": auth_data["username"],
                "email": uname if "@" in uname else "",
                "uuid": auth_data["uuid"],
                "access_token": auth_data["access_token"],
                "client_token": auth_data["client_token"],
                "created_at": time.time(),
            }
            saved = await asyncio.to_thread(storage.add_or_update_account, new_acc, set_as_active=True)
            if not saved:
                await asyncio.to_thread(
                    ely_auth.invalidate_token, auth_data["access_token"], auth_data["client_token"],
                )
                raise RuntimeError("Could not save the account. Check the launcher data folder permissions and try again.")
            rebuild_accounts_view()
            page.run_task(sync_active_skin)
            username_field.value = ""
            pass_field.value = ""
            totp_field.value = ""
            totp_field.visible = False
            login_error_text.visible = False
            navigate("home")
            if storage.get_account_storage_status(storage.get_active_account()) == "session":
                notify("Signed in for this session. You'll need to sign in again after closing the launcher.", "warning", "Session sign-in")
            else:
                notify(f"Signed in as {new_acc['username']}.", "success", "Welcome")
        except ely_auth.Ely2FARequiredError:
            totp_field.visible = True
            login_error_text.value = "Two-Factor Auth required. Enter code above."
            login_error_text.visible = True
            totp_field.update()
            login_error_text.update()
            notify(login_error_text.value, "info", "Two-factor authentication")
        except ely_auth.ElyInvalidCredentialsError:
            login_error_text.value = "Invalid username or password."
            login_error_text.visible = True
            login_error_text.update()
            notify(login_error_text.value, "error", "Sign-in failed")
        except Exception as exc:
            login_error_text.value = str(exc)
            login_error_text.visible = True
            login_error_text.update()
            notify(login_error_text.value, "error", "Sign-in failed")
        finally:
            auth_activity.finish()
            login_button.disabled = False
            login_button_label.value = "Login"
            login_button.update()

    username_field.on_submit = handle_login
    pass_field.on_submit = handle_login
    totp_field.on_submit = handle_login
    login_button.on_click = handle_login

    login_form_container = ft.Container(
        ft.Column(
            [
                ft.Container(
                    login_title_text,
                    padding=ft.Padding.only(bottom=5),
                ),
                username_field,
                pass_field,
                totp_field,
                login_error_text,
                login_button,
                ely_register_btn,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16,
        ),
        alignment=ft.Alignment.CENTER,
        padding=10,
        expand=True,
        opacity=0.0,
        scale=0.90,
        offset=ft.Offset(-0.04, 0.04),
        animate_opacity=ft.Animation(360, ft.AnimationCurve.EASE_OUT_CUBIC),
        animate_scale=ft.Animation(440, ft.AnimationCurve.EASE_OUT_BACK),
        animate_offset=ft.Animation(380, ft.AnimationCurve.EASE_OUT_CUBIC),
    )

    login_branding_container = ft.Container(
        ft.Column(
            [
                title_icon,
                ft.Container(
                    ft.Text(
                        "Launcher",
                        font_family="ps",
                        color=accent_color,
                        size=55,
                    ),
                    alignment=ft.Alignment.CENTER,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
        opacity=0.0,
        scale=0.90,
        offset=ft.Offset(0.04, 0.04),
        animate_opacity=ft.Animation(360, ft.AnimationCurve.EASE_OUT_CUBIC),
        animate_scale=ft.Animation(440, ft.AnimationCurve.EASE_OUT_BACK),
        animate_offset=ft.Animation(380, ft.AnimationCurve.EASE_OUT_CUBIC),
    )

    login_options_container = ft.Container(
        right=40,
        bottom=35,
        content=login_options,
        opacity=0.0,
        animate_opacity=ft.Animation(360, ft.AnimationCurve.EASE_OUT_CUBIC),
    )

    login_view = ft.Stack(
        expand=True,
        controls=[
            ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                content=ft.Row(
                    [
                        login_form_container,
                        login_branding_container,
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    expand=True,
                ),
            ),
            ft.Container(
                top=16,
                left=20,
                content=back_btn,
            ),
            login_options_container,
        ],
    )

    # ---------------------------
    # HOMEPAGE SIDEBAR & TABS
    # ---------------------------
    active_tab = "home"
    icon_controls = {}
    text_controls = {}
    sidebar_btn_controls = {}
    nav_indicator_controls = {}

    tabs = [
        ("home", "Home", "home"),
        ("library", "Library", "library"),
        ("mods", "Mods", "mods"),
        ("skins", "Skins", "skins"),
        ("settings", "Settings", "settings"),
        ("accounts", "Accounts", "accounts"),
    ]

    main_content_area = ft.Container(
        content=ft.Container(expand=True),
        expand=True,
        padding=ft.Padding.only(bottom=ProgressHost.STATUS_AREA_HEIGHT),
    )

    tab_reveal = StaggeredReveal(page, main_content_area)

    def tab_reveal_controls(key: str):
        if key == "home":
            return [hero_section, profiles_panel_container, flying_model_card]
        if key == "library":
            return [
                control for control in (
                    library_content.content.controls[0],
                    library_list_view.header,
                    *library_list_view.cards,
                    library_list_view.footer,
                ) if control is not None
            ]
        if key == "accounts":
            return [accounts_content.content.controls[0], *accounts_list_column.controls]
        if key == "skins":
            return skins_reveal_controls
        if key == "settings":
            return [
                settings_content.content.controls[0],
                settings_sub_sidebar,
                *settings_section_container.content.controls,
            ]
        if key == "mods":
            return mods_content.reveal_controls
        return []

    rebuild_settings_view_fn = None

    def rebuild_settings_view():
        if rebuild_settings_view_fn:
            rebuild_settings_view_fn()

    settings_content = ft.Container(expand=True)

    def set_active_tab(key: str, force_refresh: bool = False, *, mods_profile_id: str | None = None):
        nonlocal active_tab
        # If user clicks the tab they are already on and not forcing a refresh, ignore to prevent redundant work
        if key == active_tab and not force_refresh:
            if key == "mods" and mods_profile_id:
                mods_content.enter(mods_profile_id)
            return

        tab_reveal.finish(update=False)
        settings_reveal.finish(update=False)
        profiles_list_view.cancel_drag()
        library_list_view.cancel_drag()
        skin_viewer.suspend()
        home_skin_viewer.suspend()
        if active_tab == "mods":
            mods_content.leave()
        # A tab can be selected while the first home entrance is still playing.
        for control in tab_reveal_controls("home"):
            control.opacity = 1.0
            control.scale = 1.0
            control.offset = ft.Offset(0, 0)

        active_tab = key
        main_content_area.padding = ft.Padding.only(
            bottom=ProgressHost.STATUS_AREA_HEIGHT if key == "home" else 0,
        )
        progress_host.set_show_text(key == "home" and content_area.content is home_view)
        discord_presence.set_page(key)
        for k, _, icon_name in tabs:
            is_active = k == active_tab
            icon_controls[k].src = (
                f"sidebar/{icon_name}.png"
                if is_active
                else f"sidebar/{icon_name}_muted.png"
            )
            text_controls[k].color = accent_color if is_active else "#747e95"
            sidebar_btn_controls[k].bgcolor = (
                ft.Colors.BLACK_26 if is_active else ft.Colors.TRANSPARENT
            )
            if k in nav_indicator_controls:
                nav_indicator_controls[k].bgcolor = (
                    accent_color if is_active else ft.Colors.TRANSPARENT
                )
                try:
                    nav_indicator_controls[k].update()
                except RuntimeError:
                    pass
            try:
                sidebar_btn_controls[k].update()
            except RuntimeError:
                pass

        if key == "home":
            try:
                update_home_skin_model(render_immediately=False)
            except Exception:
                pass
            target_content = home_content
        elif key == "library":
            rebuild_library_view()
            target_content = library_content
        elif key == "mods":
            mods_content.enter(mods_profile_id, defer_load=True)
            target_content = mods_content
        elif key == "accounts":
            rebuild_accounts_view()
            target_content = accounts_content
        elif key == "skins":
            rebuild_skins_view()
            target_content = skins_content
        elif key == "settings":
            rebuild_settings_view()
            target_content = settings_content
        else:
            target_content = ft.Container(expand=True)

        # Keep Mods mounted: remounting its image providers and frosted rows
        # on every sidebar visit stalls the first frames of the entrance.
        mods_tab_slot.opacity = 1 if key == "mods" else 0
        mods_tab_slot.ignore_interactions = key != "mods"
        mods_tab_slot.disabled = key != "mods"
        ordinary_tab_host.visible = key != "mods"
        if key != "mods":
            ordinary_tab_host.content = target_content
        tab_reveal.show(main_tab_stack, tab_reveal_controls(key), animate=not force_refresh)
        if key == "mods":
            mods_content.start_loading()
        if key == "home":
            home_skin_viewer.request_frame()
        if key == "skins":
            page.run_task(sync_active_skin)

    def make_nav_btn(key: str, label: str, icon_name: str):
        is_active = key == active_tab
        img = ft.Image(
            src=(
                f"sidebar/{icon_name}.png"
                if is_active
                else f"sidebar/{icon_name}_muted.png"
            ),
            width=28,
            height=28,
            fit=ft.BoxFit.CONTAIN,
        )
        txt = ft.Text(
            label,
            font_family="ps",
            size=16,
            color=accent_color if is_active else "#747e95",
        )
        icon_controls[key] = img
        text_controls[key] = txt
        indicator = ft.Container(
            width=3,
            height=18,
            border_radius=2,
            bgcolor=accent_color if is_active else ft.Colors.TRANSPARENT,
            animate=ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC),
        )
        nav_indicator_controls[key] = indicator

        def nav_hover(e):
            if key == active_tab:
                return
            hovered = e.data is True or str(e.data).lower() in ("true", "1")
            btn.bgcolor = (
                ft.Colors.with_opacity(0.10, "#ffffff")
                if hovered
                else ft.Colors.TRANSPARENT
            )
            txt.color = accent_color if hovered else "#747e95"
            try:
                btn.update()
            except RuntimeError:
                pass

        shortcut = next(
            (f"Ctrl+{index}" for index, (tab_key, _, _) in enumerate(tabs, start=1)
             if tab_key == key),
            "",
        )
        btn = ft.Container(
            content=ft.Row(
                spacing=11,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[indicator, img, txt],
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=5,
            bgcolor=ft.Colors.BLACK_26 if is_active else ft.Colors.TRANSPARENT,
            on_click=lambda e, k=key: set_active_tab(k),
            on_hover=nav_hover,
            tooltip=f"{label}  ·  {shortcut}" if shortcut else label,
            opacity=0.0,
            scale=0.86,
            offset=ft.Offset(-0.06, 0.02),
            animate_opacity=ft.Animation(340, ft.AnimationCurve.EASE_OUT_CUBIC),
            animate_scale=ft.Animation(420, ft.AnimationCurve.EASE_OUT_BACK),
            animate_offset=ft.Animation(360, ft.AnimationCurve.EASE_OUT_CUBIC),
        )
        sidebar_btn_controls[key] = btn
        return btn

    for key, label, icon_name in tabs:
        make_nav_btn(key, label, icon_name)

    sidebar = ft.Container(
        width=190,
        # bgcolor=ft.Colors.BLACK_26,
        # border=ft.Border(right=ft.BorderSide(1, ft.Colors.WHITE_10)),
        padding=ft.Padding.symmetric(vertical=16, horizontal=8),
        content=ft.Column(
            expand=True,
            # alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                ft.Column(
                    alignment=ft.CrossAxisAlignment.CENTER,
                    expand=True,
                    spacing=0,
                    controls=[
                        sidebar_btn_controls["home"],
                        sidebar_btn_controls["library"],
                        sidebar_btn_controls["mods"],
                        sidebar_btn_controls["skins"],
                    ],
                ),
                ft.Column(
                    spacing=0,
                    controls=[
                        sidebar_btn_controls["settings"],
                        sidebar_btn_controls["accounts"],
                    ],
                ),
            ],
        ),
    )

    # ---------------------------
    # HOMEPAGE CONTENT (Hero Banner & Play Button)
    # ---------------------------
    HERO_BANNER_HEIGHT = 280
    PLAY_BTN_HEIGHT = 50

    play_button_icon = ft.Icon(ft.Icons.PLAY_ARROW_SHARP, color=bg_color_main, size=24)
    play_button_text = ft.Text(
        "PLAY",
        font_family="ps",
        color=bg_color_main,
        size=20,
        weight=ft.FontWeight.BOLD,
    )

    def on_main_play_click(e):
        pass  # Connected to launch logic below

    def toggle_logs_window(e=None):
        pass  # Connected to logs window logic below

    play_button = ft.Button(
        content=ft.Row(
            [
                play_button_icon,
                play_button_text,
                ft.Container(
                    content=ft.Text(
                        "ENTER",
                        font_family="ps",
                        color=bg_color_main,
                        size=8,
                        weight=ft.FontWeight.BOLD,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.14, bg_color_main),
                    border_radius=3,
                    padding=ft.Padding.symmetric(horizontal=6, vertical=3),
                    tooltip="Ctrl+Enter",
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8,
        ),
        width=236,
        height=PLAY_BTN_HEIGHT,
        tooltip="Launch the selected profile (Ctrl+Enter)",
        style=ft.ButtonStyle(
            bgcolor=accent_color,
            overlay_color=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(
                radius=0
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=0),
        ),
        on_click=lambda e: on_main_play_click(e),
    )

    logs_action_btn = ft.Container(
        height=PLAY_BTN_HEIGHT,
        padding=ft.Padding.symmetric(horizontal=18, vertical=0),
        alignment=ft.Alignment.CENTER,
        content=ft.Row(
            [
                ft.Icon(ft.Icons.TERMINAL, color=bg_color_main, size=20),
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        tooltip="Toggle Game Logs",
        on_click=lambda e: toggle_logs_window(e),
    )

    play_bar_pill = ft.Container(
        height=PLAY_BTN_HEIGHT,
        # bgcolor=ft.Colors.BLACK_38,
        # blur=ft.Blur(3, 3),
        border_radius=5,
        bgcolor=accent_color,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.Row(
            [
                play_button,
                ft.Container(
                    width=1,
                    height=28,
                    bgcolor=ft.Colors.with_opacity(1, bg_color_main),
                ),
                logs_action_btn,
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        ),
    )

    # ---------------------------
    # BADGE HELPERS & HERO BANNER CONTROLS
    # ---------------------------
    def get_loader_badge_info(loader_name: str) -> tuple[str, str]:
        """Returns (badge_text, color) for a loader, or ('', '') for Vanilla."""
        clean = (loader_name or "").strip()
        low = clean.lower()
        if low == "fabric":
            return "FABRIC", "#82b1ff"
        elif low == "forge":
            return "FORGE", "#ffb74d"
        elif low == "neoforge":
            return "NEOFORGE", "#ff8a65"
        elif low == "quilt":
            return "QUILT", "#ce93d8"
        elif low in ("vanilla", ""):
            return "", ""
        else:
            return "MODDED", "#b0bec5"

    def build_profile_badges(profile: dict) -> list[ft.Control]:
        badges = []
        if profile.get("is_instance"):
            badges.append(
                ft.Container(
                    content=ft.Text(
                        "INSTANCE",
                        size=9,
                        color=accent_color,
                        weight=ft.FontWeight.BOLD,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
                    border_radius=4,
                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                )
            )

        loader = str(profile.get("loader") or "Vanilla").strip()
        badge_text, badge_color = get_loader_badge_info(loader)
        if badge_text:
            badges.append(
                ft.Container(
                    content=ft.Text(
                        badge_text,
                        size=9,
                        color=badge_color,
                        weight=ft.FontWeight.BOLD,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
                    border_radius=4,
                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                )
            )
        return badges

    hero_profile_icon_box = ft.Container(
        width=52,
        height=52,
        border_radius=0,
        bgcolor=ft.Colors.with_opacity(0.40, ft.Colors.BLACK),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.3, accent_color)),
        alignment=ft.Alignment.CENTER,
        content=build_profile_icon_content(
            None,
            size=44,
            default_icon_size=32,
            color=accent_color,
        ),
    )

    hero_profile_name = ft.Text(
        "Loading...",
        font_family="ps",
        color=ft.Colors.WHITE,
        size=26,
        weight=ft.FontWeight.BOLD,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )

    hero_instance_badge_text = ft.Text(
        "INSTANCE",
        size=9,
        color=accent_color,
        weight=ft.FontWeight.BOLD,
    )

    hero_instance_badge = ft.Container(
        content=hero_instance_badge_text,
        bgcolor=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
        border_radius=4,
        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
        visible=False,
    )

    hero_loader_badge_text = ft.Text(
        "",
        size=9,
        color=ft.Colors.WHITE,
        weight=ft.FontWeight.BOLD,
    )

    hero_loader_badge = ft.Container(
        content=hero_loader_badge_text,
        bgcolor=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
        border_radius=4,
        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
        visible=False,
    )

    hero_title_row = ft.Row(
        controls=[hero_profile_name, hero_instance_badge, hero_loader_badge],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    hero_details_text = ft.Text(
        "",
        size=13,
        color="#d6dfea",
        weight=ft.FontWeight.W_400,
    )

    hero_status_dot = ft.Container(
        width=8,
        height=8,
        border_radius=4,
        bgcolor="#81c784",
    )

    hero_status_text = ft.Text(
        "Ready to Play",
        size=12,
        color=ft.Colors.WHITE,
        weight=ft.FontWeight.W_500,
    )

    hero_mods_separator = ft.Text(
        "•",
        size=12,
        color=ft.Colors.WHITE_70,
        visible=False,
    )

    hero_mods_icon = ft.Icon(
        ft.Icons.EXTENSION_ROUNDED,
        size=13,
        color=accent_color,
        visible=False,
    )

    hero_mods_text = ft.Text(
        "0 Mods",
        size=12,
        color=ft.Colors.WHITE,
        weight=ft.FontWeight.W_500,
        visible=False,
    )

    hero_status_row = ft.Row(
        controls=[
            hero_status_dot,
            hero_status_text,
            hero_mods_separator,
            hero_mods_icon,
            hero_mods_text,
        ],
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    def refresh_connection_status():
        update_connection_chip(
            online=latest_release_state.get("online"),
            checking=bool(latest_release_state.get("loading")),
        )

    hero_info_column = ft.Column(
        spacing=4,
        tight=True,
        alignment=ft.MainAxisAlignment.CENTER,
        controls=[
            hero_title_row,
            hero_details_text,
            hero_status_row,
        ],
    )

    hero_info_card = ft.Container(
        bgcolor=ft.Colors.with_opacity(0.58, "#0d1728"),
        blur=ft.Blur(12, 12),
        border=ft.Border(left=ft.BorderSide(3, accent_color)),
        border_radius=5,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        shadow=ft.BoxShadow(
            blur_radius=16, spread_radius=0,
            color=ft.Colors.with_opacity(0.24, "#000000"),
            offset=ft.Offset(0, 4),
        ),
        content=ft.Row(
            tight=True,
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                hero_profile_icon_box,
                hero_info_column,
            ],
        ),
    )

    hero_banner = ft.Container(
        height=HERO_BANNER_HEIGHT,
        border_radius=10,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        image=ft.DecorationImage(
            src="banners/hero_bg.png",
            fit=ft.BoxFit.COVER,
            filter_quality=ft.FilterQuality.NONE,
        ),
        content=ft.Container(
            expand=True,
            gradient=ft.LinearGradient(
                begin=ft.Alignment.BOTTOM_LEFT,
                end=ft.Alignment.TOP_RIGHT,
                colors=[
                    ft.Colors.with_opacity(0.90, "#182842"),
                    ft.Colors.with_opacity(0.20, "#182842"),
                    ft.Colors.with_opacity(0.0, "#182842"),
                ],
                stops=[0.0, 0.52, 1.0],
            ),
        ),
    )

    hero_section = ft.Stack(
        clip_behavior=ft.ClipBehavior.NONE,
        opacity=0.0,
        scale=0.92,
        offset=ft.Offset(-0.03, 0.03),
        animate_opacity=ft.Animation(380, ft.AnimationCurve.EASE_OUT_CUBIC),
        animate_scale=ft.Animation(460, ft.AnimationCurve.EASE_OUT_BACK),
        animate_offset=ft.Animation(400, ft.AnimationCurve.EASE_OUT_CUBIC),
        controls=[
            ft.Container(
                content=hero_banner,
                margin=ft.Padding.only(bottom=PLAY_BTN_HEIGHT / 2),
            ),
            ft.Container(
                top=77,
                left=36,
                content=hero_info_card,
            ),
            ft.Container(
                bottom=0,
                left=36,
                content=play_bar_pill,
            ),
        ],
    )

    # ---------------------------
    # FLOATING LOGS WINDOW CONTROLS
    # ---------------------------
    LOGS_BUFFER_MAX = 500
    logs_data_buffer = []

    logs_list_view = ft.ListView(
        expand=True,
        spacing=2,
        auto_scroll=True,
        padding=ft.Padding.all(10),
    )

    logs_status_indicator = ft.Container(
        width=8,
        height=8,
        border_radius=4,
        bgcolor="#747e95",
        tooltip="Idle",
    )

    def do_copy_logs(e=None):
        if not logs_data_buffer:
            notify("There are no logs to copy yet.", "info", "Logs")
            return
        full_text = "\n".join(logs_data_buffer)
        try:
            r = tk.Tk()
            r.withdraw()
            r.clipboard_clear()
            r.clipboard_append(full_text)
            r.update()
            r.destroy()
            notify("Copied logs to clipboard.", "success", "Logs copied")
        except Exception as err:
            print(f"Failed to copy logs: {err}")
            notify(f"Could not copy logs: {err}", "error")

    def do_clear_logs(e=None):
        logs_data_buffer.clear()
        logs_list_view.controls.clear()
        try:
            logs_list_view.update()
        except RuntimeError:
            pass

        notify("The log view has been cleared.", "info", "Logs cleared")

    def add_log_lines(lines):
        for line_str in lines:
            cleaned = redact_credentials(line_str.rstrip("\r\n"))
            if not cleaned:
                continue
            logs_data_buffer.append(cleaned)
            text_color = "#cfd8dc"
            low = cleaned.lower()
            if "error" in low or "exception" in low or "fatal" in low or "crash" in low:
                text_color = "#ef5350"
            elif "warn" in low:
                text_color = "#ffca28"
            elif "info" in low:
                text_color = "#90caf9"
            logs_list_view.controls.append(
                ft.Text(
                    cleaned, size=11, color=text_color,
                    font_family="Consolas, monospace", selectable=True,
                )
            )
        del logs_data_buffer[:-LOGS_BUFFER_MAX]
        del logs_list_view.controls[:-LOGS_BUFFER_MAX]
        if logs_panel_container.visible:
            try:
                logs_list_view.update()
            except RuntimeError:
                pass

    def add_log_line(line_str: str, level: str = "info"):
        add_log_lines([line_str])

    def set_logs_view_visible(val: bool):
        profiles_panel_container.visible = not val
        logs_panel_container.visible = val
        try:
            profiles_panel_container.update()
            logs_panel_container.update()
            logs_action_btn.update()
        except RuntimeError:
            pass

    def do_toggle_logs(e=None):
        set_logs_view_visible(not logs_panel_container.visible)

    toggle_logs_window = do_toggle_logs

    logs_panel_container = ft.Container(
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.25, ft.Colors.BLACK),
        blur=ft.Blur(10, 10),
        border_radius=8,
        border=ft.Border.all(1, ft.Colors.with_opacity(0.18, "#ffffff")),
        padding=0,
        visible=False,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.Column(
            spacing=0,
            expand=True,
            controls=[
                # Terminal Title Bar
                ft.Container(
                    height=36,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=0),
                    bgcolor=ft.Colors.with_opacity(0.30, ft.Colors.BLACK),
                    border=ft.Border(
                        bottom=ft.BorderSide(1, ft.Colors.with_opacity(0.12, "#ffffff"))
                    ),
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Row(
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Icon(
                                        ft.Icons.TERMINAL, color=accent_color, size=16
                                    ),
                                    ft.Text(
                                        "Logs",
                                        font_family="ps",
                                        color=accent_color,
                                        size=14,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    logs_status_indicator,
                                ],
                            ),
                            ft.Row(
                                spacing=4,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.IconButton(
                                        icon=ft.Icons.COPY_ALL_SHARP,
                                        icon_size=15,
                                        icon_color="#8a94a6",
                                        tooltip="Copy all logs to clipboard",
                                        style=ft.ButtonStyle(
                                            overlay_color=ft.Colors.TRANSPARENT,
                                            padding=2,
                                        ),
                                        on_click=do_copy_logs,
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.DELETE_SWEEP_SHARP,
                                        icon_size=16,
                                        icon_color="#8a94a6",
                                        tooltip="Clear logs",
                                        style=ft.ButtonStyle(
                                            overlay_color=ft.Colors.TRANSPARENT,
                                            padding=2,
                                        ),
                                        on_click=do_clear_logs,
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.CLOSE_SHARP,
                                        icon_size=16,
                                        icon_color="#ffffff",
                                        tooltip="Close Logs",
                                        style=ft.ButtonStyle(
                                            overlay_color=ft.Colors.TRANSPARENT,
                                            padding=2,
                                        ),
                                        on_click=lambda e: set_logs_view_visible(False),
                                    ),
                                ],
                            ),
                        ],
                    ),
                ),
                # Terminal Body
                ft.Container(
                    expand=True,
                    bgcolor=ft.Colors.with_opacity(0.20, ft.Colors.BLACK),
                    content=logs_list_view,
                ),
            ],
        ),
    )

    # ---------------------------
    # PROFILES & LAUNCH SYSTEM
    # ---------------------------
    profiles_list_view = ProfileList(
        on_order_changed=lambda profile_ids: save_profile_order(profile_ids),
    )

    profiles_manage_btn = ft.TextButton(
        content=ft.Text(
            "Manage",
            font_family="ps",
            size=13,
        ),
        style=ft.ButtonStyle(
            color={
                ft.ControlState.HOVERED: accent_color,
                ft.ControlState.DEFAULT: "#747e95",
            },
            bgcolor=ft.Colors.TRANSPARENT,
            overlay_color=ft.Colors.TRANSPARENT,
            padding=ft.Padding.symmetric(horizontal=8, vertical=0),
        ),
        on_click=lambda e: set_active_tab("library"),
    )

    profiles_count_text = ft.Text(
        "0", font_family="ps", size=11, color="#747e95",
        weight=ft.FontWeight.BOLD,
    )
    profiles_count_chip = ft.Container(
        content=profiles_count_text,
        bgcolor=ft.Colors.BLACK_26,
        border_radius=4,
        padding=ft.Padding.symmetric(horizontal=7, vertical=2),
    )
    profiles_new_btn = SimpleButton(
        ft.Row(
            [
                ft.Icon(ft.Icons.ADD_SHARP, color=accent_color, size=16),
                ft.Text("New", font_family="ps", color=accent_color, size=13),
            ],
            spacing=4,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        height=30,
        tooltip="Create a new profile (Ctrl+N)",
        style=ft.ButtonStyle(
            bgcolor=ft.Colors.BLACK_26,
            overlay_color=ft.Colors.TRANSPARENT,
            shadow_color=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(radius=4),
            padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            side=ft.BorderSide(0, ft.Colors.TRANSPARENT),
        ),
        on_click=lambda e: open_new_profile_page(),
    )

    profiles_panel_container = ft.Container(
        expand=True,
        opacity=0.0,
        scale=0.93,
        offset=ft.Offset(-0.03, 0.03),
        animate_opacity=ft.Animation(380, ft.AnimationCurve.EASE_OUT_CUBIC),
        animate_scale=ft.Animation(460, ft.AnimationCurve.EASE_OUT_BACK),
        animate_offset=ft.Animation(400, ft.AnimationCurve.EASE_OUT_CUBIC),
        content=ft.Column(
            expand=True,
            spacing=8,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Row(
                            [
                                ft.Text(
                                    "Profiles",
                                    font_family="ps",
                                    color=accent_color,
                                    size=16,
                                    weight=ft.FontWeight.BOLD,
                                ),
                                profiles_count_chip,
                            ],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            [profiles_new_btn, profiles_manage_btn],
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                ),
                profile_list_surface(profiles_list_view),
            ],
        ),
    )

    # ---------------------------
    # ACCOUNTS TAB VIEW
    # ---------------------------
    def get_account_avatar_bytes(acc: dict, *, scale: int = 4) -> bytes | None:
        acc_type = acc.get("type", "offline")
        username = acc.get("username", "Player")
        skin_img = None
        if acc_type == "ely":
            skin_path = skins.get_skins_dir() / f"ely_{username.lower()}.png"
            if skin_path.exists():
                try:
                    skin_img = Image.open(skin_path)
                except Exception:
                    pass
        if skin_img is None:
            skin_img = skins.get_default_skin("classic")
        try:
            return skins.render_head_avatar_bytes(skin_img, scale=scale)
        except Exception:
            return None

    def refresh_discord_player(account: dict | None = None):
        if not has_saved_account():
            discord_presence.set_player("", None)
            return
        account = account if account is not None else storage.get_active_account()
        username = str(account.get("username") or "Player").strip()
        discord_presence.set_player(
            username, get_account_avatar_bytes(account, scale=1),
        )

    accounts_list_column = ft.Column(
        spacing=8,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    async def select_active_account(acc_id: str):
        if not await asyncio.to_thread(storage.set_active_account, acc_id):
            notify("Could not switch accounts. Please try again.", "error")
            return
        account = storage.get_active_account()
        if account.get("type") == "ely":
            account = await asyncio.to_thread(storage.get_active_account, include_credentials=True)
            if not account.get("access_token"):
                notify("Sign in again to use this Ely.by account.", "warning", "Sign-in required")
                relogin_account(account)
                return
        rebuild_accounts_view()
        page.run_task(sync_active_skin)
        try:
            update_home_skin_model()
        except Exception:
            pass

        notify(f"Switched to {account.get('username', 'Player')}.", "success", "Account switched")

    async def delete_account(acc_id: str):
        accounts_data = await asyncio.to_thread(storage.load_accounts, include_credentials=True)
        account = next(
            (item for item in accounts_data.get("accounts", [])
             if item.get("id") == acc_id),
            {},
        )
        if not await asyncio.to_thread(storage.remove_account, acc_id):
            notify("Could not remove the account. Please try again.", "error")
            return
        if not has_saved_account():
            open_login(is_add_account=False)
        else:
            rebuild_accounts_view()
        page.run_task(sync_active_skin)
        try:
            update_home_skin_model()
        except Exception:
            pass
        notify(f"Removed {account.get('username', 'the account')}.", "success", "Account removed")
        if account.get("type") == "ely":
            await asyncio.to_thread(
                ely_auth.invalidate_token,
                str(account.get("access_token") or ""),
                str(account.get("client_token") or ""),
            )

    def relogin_account(account: dict):
        open_login(is_add_account=True, account=account)

    def rebuild_accounts_view():
        refresh_discord_player()
        accounts_data = storage.load_accounts()
        saved_accounts = accounts_data.get("accounts", [])
        active_id = accounts_data.get("active_account_id")

        rows = []
        for acc in saved_accounts:
            acc_id = acc.get("id")
            acc_type = acc.get("type", "offline")
            username = acc.get("username", "Player")
            is_active = acc_id == active_id
            needs_login = acc_type == "ely" and storage.get_account_storage_status(acc) == "missing"

            avatar_bytes = get_account_avatar_bytes(acc)
            avatar_control = (
                ft.Image(
                    src=avatar_bytes,
                    width=34,
                    height=34,
                    fit=ft.BoxFit.CONTAIN,
                )
                if avatar_bytes
                else ft.Icon(ft.Icons.PERSON_SHARP, color=accent_color, size=30)
            )

            # Select button / active indicator
            if is_active and not needs_login:
                select_btn = ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.CHECK_SHARP, color=bg_color_main, size=15),
                            ft.Text(
                                "Selected",
                                font_family="ps",
                                color=bg_color_main,
                                size=13,
                            ),
                        ],
                        spacing=4,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    bgcolor=accent_color,
                    border_radius=5,
                    padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                )
            else:
                select_btn = ft.Button(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.CHECK_SHARP, color=accent_color, size=15),
                            ft.Text(
                                "Sign in" if needs_login else "Select",
                                font_family="ps", color=accent_color, size=13
                            ),
                        ],
                        spacing=4,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    height=30,
                    style=ft.ButtonStyle(
                        bgcolor=ft.Colors.TRANSPARENT,
                        overlay_color=ft.Colors.TRANSPARENT,
                        shape=ft.RoundedRectangleBorder(radius=5),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=0),
                    ),
                    on_click=(
                        (lambda e, account=acc.copy(): relogin_account(account))
                        if needs_login else
                        (lambda e, target_id=acc_id: page.run_task(select_active_account, target_id))
                    ),
                )

            # Remove button
            remove_btn = ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE_SHARP,
                icon_color="#e57373",
                icon_size=18,
                style=ft.ButtonStyle(
                    overlay_color=ft.Colors.TRANSPARENT,
                    padding=0,
                ),
                tooltip="Remove Account",
                on_click=lambda e, target_id=acc_id: page.run_task(delete_account, target_id),
            )

            account_actions = [select_btn, remove_btn]
            account_label = "Ely.by Account" if acc_type == "ely" else "Offline Account"

            card = ft.Container(
                bgcolor=ft.Colors.BLACK_26,
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                content=ft.Row(
                    [
                        # Left: Skin face
                        avatar_control,
                        # Username & Account Type
                        ft.Column(
                            [
                                ft.Text(
                                    username,
                                    font_family="ps",
                                    color=accent_color,
                                    size=18,
                                ),
                                ft.Text(
                                    account_label,
                                    size=11,
                                    color="#747e95",
                                ),
                            ],
                            spacing=1,
                            expand=True,
                        ),
                        # Right actions: Select & Remove
                        ft.Row(
                            account_actions,
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
            rows.append(card)

        accounts_list_column.controls = rows
        try:
            accounts_list_column.update()
        except RuntimeError:
            pass

    add_account_btn = ft.Button(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.ADD_SHARP, color=bg_color_main, size=18),
                ft.Text("Add Account", font_family="ps", color=bg_color_main, size=15),
            ],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        height=38,
        style=ft.ButtonStyle(
            bgcolor=accent_color,
            overlay_color=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(radius=5),
        ),
        on_click=lambda e: open_login(is_add_account=True),
    )

    accounts_content = ft.Container(
        expand=True,
        padding=ft.Padding.only(left=20, right=24, top=10, bottom=16),
        content=ft.Column(
            expand=True,
            spacing=16,
            controls=[
                ft.Row(
                    [
                        ft.Text(
                            "Accounts", font_family="ps", color=accent_color, size=32
                        ),
                        add_account_btn,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                accounts_list_column,
            ],
        ),
    )

    # ---------------------------
    # SKINS TAB VIEW
    # ---------------------------
    skins_content = ft.Container(expand=True)
    skins_reveal_controls = []

    current_skin_model = ["classic"]
    current_skin_image = [None]
    loaded_skin_key = [None]

    skin_display_img = ft.Image(
        src=b"",
        width=250,
        height=350,
        fit=ft.BoxFit.CONTAIN,
        filter_quality=ft.FilterQuality.NONE,
        gapless_playback=True,
    )
    skin_viewer = SkinViewer(page, skin_display_img, lambda: active_tab == "skins")

    def load_active_ely_skin(force=False):
        active_acc = storage.get_active_account()
        uname = active_acc.get("username", "Player")
        skin_file = skins.get_skins_dir() / f"ely_{uname.lower()}.png"
        try:
            stat = skin_file.stat()
            skin_key = (uname, stat.st_mtime_ns, stat.st_size)
        except OSError:
            skin_key = (uname, None, None)
        if not force and loaded_skin_key[0] == skin_key and current_skin_image[0] is not None:
            return current_skin_image[0]
        raw = None
        if skin_file.is_file():
            try:
                raw = skin_file.read_bytes()
            except OSError:
                pass
        im = None
        if raw:
            try:
                im = Image.open(io.BytesIO(raw))
            except Exception:
                pass
        if im is None:
            im = skins.get_default_skin(current_skin_model[0])

        prepared = skins.ensure_64x64(im)
        current_skin_image[0] = prepared
        is_slim = skins.is_slim_skin(prepared)
        current_skin_model[0] = "slim" if is_slim else "classic"
        skin_viewer.set_renderer(skins.SkinRenderer(prepared, current_skin_model[0], "hero"))
        loaded_skin_key[0] = skin_key
        return prepared

    def render_skin_preview() -> bytes:
        if current_skin_image[0] is None:
            load_active_ely_skin()
        return skin_viewer.render_still()

    def update_skin_model_preview():
        skin_viewer.request_frame()

    def handle_pan_start(e):
        skin_viewer.begin_drag()

    def handle_pan_update(e: ft.DragUpdateEvent):
        dx = 0.0
        dy = 0.0
        if getattr(e, "local_delta", None) is not None:
            dx = getattr(e.local_delta, "x", 0.0) or 0.0
            dy = getattr(e.local_delta, "y", 0.0) or 0.0
        elif getattr(e, "global_delta", None) is not None:
            dx = getattr(e.global_delta, "x", 0.0) or 0.0
            dy = getattr(e.global_delta, "y", 0.0) or 0.0
        elif hasattr(e, "delta_x"):
            dx = getattr(e, "delta_x", 0.0) or 0.0
            dy = getattr(e, "delta_y", 0.0) or 0.0
        skin_viewer.drag(dx, dy)

    def handle_pan_end(e):
        skin_viewer.end_drag()

    def handle_double_tap(e):
        skin_viewer.turn_to(-25.0, 10.0)

    def trigger_view_angle(yaw: float, pitch: float):
        skin_viewer.turn_to(yaw, pitch)

    skin_gesture = ft.GestureDetector(
        content=skin_display_img,
        mouse_cursor=ft.MouseCursor.MOVE,
        drag_interval=16,
        on_pan_start=handle_pan_start,
        on_pan_update=handle_pan_update,
        on_pan_end=handle_pan_end,
        on_double_tap=handle_double_tap,
    )

    skin_player_name_text = ft.Text("", font_family="ps", color=accent_color, size=32)
    skin_model_info_text = ft.Text("", font_family="ps", color=accent_color, size=13)
    skin_status_text = ft.Text("", size=12, color="#747e95")
    skin_account_avatar = ft.Image(
        src=b"", width=36, height=36, filter_quality=ft.FilterQuality.NONE
    )
    skin_sync_lock = asyncio.Lock()

    async def sync_active_skin(force=False):
        async with skin_sync_lock:
            account = storage.get_active_account()
            if account.get("type") != "ely":
                return
            username = str(account.get("username") or "").strip()
            if not username:
                return
            with track_progress(progress_host, "Refreshing skin..." if force else "Syncing skin...", priority=50 if force else 20):
                skin_bytes, _ = await asyncio.to_thread(
                    skins.fetch_ely_skin, username, force_refresh=force, timeout=6.0
                )
                if storage.get_active_account().get("id") != account.get("id"):
                    return
                update_home_skin_model()
                if active_tab == "skins":
                    load_active_ely_skin(force=force)
                    update_skin_model_preview()
                    skin_model_info_text.value = f"{current_skin_model[0].capitalize()} Model"
                    skin_account_avatar.src = skins.render_head_avatar_bytes(
                        current_skin_image[0], scale=4
                    )
                    try:
                        skin_model_info_text.update()
                        skin_account_avatar.update()
                    except RuntimeError:
                        pass
                return skin_bytes is not None

    async def handle_refresh_ely_skin(e):
        refresh_skin_btn_text.value = "Refreshing..."
        refresh_skin_btn.disabled = True
        try:
            refresh_skin_btn.update()
        except RuntimeError:
            pass

        try:
            if await sync_active_skin(force=True):
                skin_status_text.value = "Synced with Ely.by"
                notify("Your skin is now synced with Ely.by.", "success", "Skin refreshed")
            else:
                skin_status_text.value = "Could not sync with Ely.by"
                notify("Could not refresh your skin. Check your connection and try again.", "warning", "Skin refresh failed")
        except Exception as error:
            skin_status_text.value = "Could not sync with Ely.by"
            notify(f"Could not refresh your skin: {error}", "error")
        finally:
            refresh_skin_btn_text.value = "Refresh"
            refresh_skin_btn.disabled = False
            try:
                refresh_skin_btn.update()
                skin_status_text.update()
            except RuntimeError:
                pass

    refresh_skin_btn_text = ft.Text(
        "Refresh", font_family="ps", color=accent_color, size=13
    )
    refresh_skin_btn = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.REFRESH_SHARP, color=accent_color, size=15),
                refresh_skin_btn_text,
            ],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        height=34,
        bgcolor=ft.Colors.TRANSPARENT,
        border_radius=5,
        padding=ft.Padding.symmetric(horizontal=12, vertical=0),
        alignment=ft.Alignment.CENTER,
        on_click=handle_refresh_ely_skin,
    )

    manage_skin_online_btn = ft.Button(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.OPEN_IN_NEW_SHARP, color=bg_color_main, size=15),
                ft.Text(
                    "Manage on Ely.by", font_family="ps", color=bg_color_main, size=15
                ),

            ],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        height=40,
        width=300,
        style=ft.ButtonStyle(
            bgcolor=accent_color,
            overlay_color=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(radius=5),
            padding=ft.Padding.symmetric(horizontal=14, vertical=0),
        ),
        on_click=lambda e: webbrowser.open("https://ely.by/skins"),
    )

    switch_account_btn = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.MANAGE_ACCOUNTS_SHARP, color=accent_color, size=15),
                ft.Text("Accounts", font_family="ps", color=accent_color, size=13),
            ],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        height=34,
        bgcolor=ft.Colors.TRANSPARENT,
        border_radius=5,
        padding=ft.Padding.symmetric(horizontal=12, vertical=0),
        alignment=ft.Alignment.CENTER,
        on_click=lambda e: set_active_tab("accounts"),
    )

    def rebuild_skins_view():
        nonlocal skins_reveal_controls
        active_acc = storage.get_active_account()
        is_ely = active_acc.get("type") == "ely"

        if not is_ely:
            skins_content.content = ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                padding=20,
                content=ft.Container(
                    # bgcolor=ft.Colors.BLACK_26,
                    border_radius=5,
                    padding=ft.Padding.symmetric(horizontal=36, vertical=32),
                    content=ft.Column(
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=16,
                        controls=[
                            ft.Icon(
                                ft.Icons.PERSON_OFF_SHARP, color=accent_color, size=48
                            ),
                            ft.Text(
                                "Ely.by Account Required",
                                font_family="ps",
                                color=accent_color,
                                size=24,
                            ),
                            ft.Text(
                                "Please select/add an Ely.by account in order to use skins.",
                                size=14,
                                color="#a0aec0",
                                text_align=ft.TextAlign.CENTER,
                            ),
                            ft.Container(height=4),
                            ft.Button(
                                content=ft.Row(
                                    [
                                        ft.Icon(
                                            ft.Icons.MANAGE_ACCOUNTS_SHARP,
                                            color=bg_color_main,
                                            size=18,
                                        ),
                                        ft.Text(
                                            "Go to Accounts",
                                            font_family="ps",
                                            color=bg_color_main,
                                            size=15,
                                        ),
                                    ],
                                    spacing=6,
                                    alignment=ft.MainAxisAlignment.CENTER,
                                ),
                                height=42,
                                width=180,
                                style=ft.ButtonStyle(
                                    bgcolor=accent_color,
                                    overlay_color=ft.Colors.TRANSPARENT,
                                    shape=ft.RoundedRectangleBorder(radius=5),
                                ),
                                on_click=lambda e: set_active_tab("accounts"),
                            ),
                        ],
                    ),
                ),
            )
            prompt_controls = skins_content.content.content.content.controls
            skins_reveal_controls = [prompt_controls[i] for i in (0, 1, 2, 4)]
        else:
            uname = active_acc.get("username", "Player")
            skin_player_name_text.value = uname
            load_active_ely_skin()
            skin_model_info_text.value = (
                f"{current_skin_model[0].capitalize()} Model "
                # f"({'4px' if current_skin_model[0] == 'classic' else '3px'} arms)"
            )
            skin_status_text.value = "Synced with Ely.by"
            skin_display_img.src = render_skin_preview()
            skin_account_avatar.src = skins.render_head_avatar_bytes(
                current_skin_image[0], scale=4
            )

            angle_btns = ft.Row(
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
                controls=[
                    ft.Button(
                        "Front",
                        height=28,
                        style=ft.ButtonStyle(
                            bgcolor=ft.Colors.TRANSPARENT,
                            color=accent_color,
                            overlay_color=ft.Colors.TRANSPARENT,
                            shape=ft.RoundedRectangleBorder(radius=5),
                            padding=ft.Padding.symmetric(horizontal=12, vertical=0),
                        ),
                        on_click=lambda _: trigger_view_angle(0.0, 0.0),
                    ),
                    ft.Button(
                        "Back",
                        height=28,
                        style=ft.ButtonStyle(
                            bgcolor=ft.Colors.TRANSPARENT,
                            color=accent_color,
                            overlay_color=ft.Colors.TRANSPARENT,
                            shape=ft.RoundedRectangleBorder(radius=5),
                            padding=ft.Padding.symmetric(horizontal=12, vertical=0),
                        ),
                        on_click=lambda _: trigger_view_angle(180.0, 0.0),
                    ),
                    ft.Button(
                        "Reset",
                        height=28,
                        style=ft.ButtonStyle(
                            bgcolor=ft.Colors.TRANSPARENT,
                            color=accent_color,
                            overlay_color=ft.Colors.TRANSPARENT,
                            shape=ft.RoundedRectangleBorder(radius=5),
                            padding=ft.Padding.symmetric(horizontal=12, vertical=0),
                        ),
                        on_click=lambda _: trigger_view_angle(-25.0, 10.0),
                    ),
                ],
            )

            skin_stage = ft.Stack(
                width=340,
                height=350,
                controls=[
                    # ft.Container(                         #not a gud idea
                    #     width=340,
                    #     height=350,
                    #     left=20,
                    #     top=18,
                    #     ignore_interactions=True,
                    #     gradient=ft.RadialGradient(
                    #         center=ft.Alignment(0, 0.18),
                    #         radius=0.68,
                    #         colors=[
                    #             ft.Colors.with_opacity(0.09, accent_color),
                    #             ft.Colors.with_opacity(0.025, accent_color),
                    #             ft.Colors.TRANSPARENT,
                    #         ],
                    #         stops=[0.0, 0.6, 1.0],
                    #     ),
                    # ),
                    # ft.Container(                       #looks awful
                    #     width=124,
                    #     height=1,
                    #     left=108,
                    #     bottom=12,
                    #     ignore_interactions=True,
                    #     gradient=ft.LinearGradient(
                    #         begin=ft.Alignment.CENTER_LEFT,
                    #         end=ft.Alignment.CENTER_RIGHT,
                    #         colors=[
                    #             ft.Colors.TRANSPARENT,
                    #             ft.Colors.with_opacity(1, accent_color),
                    #             ft.Colors.TRANSPARENT,
                    #         ],
                    #     ),
                    # ),
                    ft.Container(
                        content=skin_gesture,
                        width=340,
                        height=350,
                        alignment=ft.Alignment.CENTER,
                    ),
                ],
            )

            left_preview = ft.Container(
                expand=6,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=12,
                    controls=[
                        skin_stage,
                        angle_btns,
                        ft.Text(
                            "Drag to rotate · double-click to reset",
                            size=11,
                            color="#747e95",
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                ),
            )

            right_info = ft.Container(
                expand=5,
                alignment=ft.Alignment.CENTER,
                content=ft.Container(
                    width=300,
                    content=ft.Column(
                        tight=True,
                        spacing=18,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Row(
                                spacing=12,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Container(
                                        width=44,
                                        height=44,
                                        border_radius=5,
                                        bgcolor=ft.Colors.BLACK_26,
                                        alignment=ft.Alignment.CENTER,
                                        content=skin_account_avatar,
                                    ),
                                    ft.Column(
                                        expand=True,
                                        spacing=5,
                                        controls=[
                                            skin_player_name_text,
                                            skin_status_text,
                                        ],
                                    ),
                                ],
                            ),
                            ft.Container(
                                bgcolor=ft.Colors.BLACK_26,
                                border_radius=5,
                                padding=ft.Padding.symmetric(horizontal=10, vertical=7),
                                content=ft.Row(
                                    [
                                        ft.Icon(
                                            ft.Icons.CHECK_SHARP,
                                            color=accent_color,
                                            size=13,
                                        ),
                                        skin_model_info_text,
                                    ],
                                    tight=True,
                                    spacing=6,
                                ),
                            ),
                            ft.Column(
                                spacing=8,
                                controls=[
                                    manage_skin_online_btn,
                                    ft.Row(
                                        [refresh_skin_btn, switch_account_btn],
                                        spacing=12,
                                        alignment=ft.MainAxisAlignment.CENTER,
                                        wrap=True,
                                    ),
                                ],
                            ),
                        ],
                    ),
                ),
            )

            skins_content.content = ft.Container(
                expand=True,
                padding=ft.Padding.only(left=20, right=24, top=10, bottom=16),
                content=ft.Column(
                    expand=True,
                    spacing=16,
                    controls=[
                        ft.Text("Skins", font_family="ps", color=accent_color, size=32),
                        ft.Container(
                            expand=True,
                            alignment=ft.Alignment.CENTER,
                            content=ft.Container(
                                width=820,
                                content=ft.Row(
                                    spacing=36,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[left_preview, right_info],
                                ),
                            ),
                        ),
                    ],
                ),
            )
            skins_reveal_controls = [
                skins_content.content.content.controls[0], left_preview, right_info
            ]

        try:
            skins_content.update()
        except RuntimeError:
            pass

    # ---------------------------
    # HOME SIDEBAR & FLYING SKIN MODEL
    # ---------------------------
    flying_skin_img = ft.Image(
        src=b"",
        width=170,
        height=230,
        fit=ft.BoxFit.CONTAIN,
        filter_quality=ft.FilterQuality.NONE,
        gapless_playback=True,
    )

    home_skin_viewer = HomeSkinViewer(
        page, flying_skin_img,
        lambda: active_tab == "home" and content_area.content is home_view
        and not content_area.disabled,
        enabled=bool(storage.load_settings().get("enable_head_tracking", True)),
    )
    home_pointer_layout = {"width": 1100.0, "gamertag_height": 20.0}

    def on_home_cursor_move(e):
        # Head motion renders in HomeSkinViewer; tracking the pointer needs
        # no automatic update of the entire launcher control tree.
        ft.context.disable_auto_update()
        # The fixed-width statue card sits at the top of the right home sidebar.
        head_x = home_pointer_layout["width"] - 20.0 - home_sidebar.width / 2.0
        label_height = home_pointer_layout["gamertag_height"]
        image_top = (flying_model_card.height - flying_skin_img.height - label_height) / 2.0 + label_height
        head_y = custom_title_bar.height + 10.0 + image_top + 123.0 - 12.0 * 6.2
        home_skin_viewer.follow_cursor(
            e.local_position.x - head_x, e.local_position.y - head_y,
        )

    def on_home_pointer_resize(e):
        ft.context.disable_auto_update()
        home_pointer_layout["width"] = e.width

    def on_gamertag_resize(e):
        ft.context.disable_auto_update()
        home_pointer_layout["gamertag_height"] = e.height

    home_gamertag_text = ft.Text(
        "",
        font_family="ps",
        color="#ffffff",
        size=13,
        weight=ft.FontWeight.W_500,
    )
    home_account_badge_text = ft.Text(
        "LOCAL", font_family="ps", size=8, color=accent_color,
        weight=ft.FontWeight.BOLD,
    )
    home_account_badge = ft.Container(
        content=home_account_badge_text,
        bgcolor=ft.Colors.with_opacity(0.30, accent_color),
        border_radius=3,
        padding=ft.Padding.symmetric(horizontal=5, vertical=1),
    )

    home_gamertag_container = ft.Container(
        content=ft.Row(
            [home_gamertag_text, home_account_badge],
            spacing=6,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=ft.Colors.BLACK_54,
        padding=ft.Padding.symmetric(horizontal=8, vertical=2),
        border_radius=3,
        on_size_change=on_gamertag_resize,
        tooltip="Click to manage accounts",
        on_click=lambda e: set_active_tab("accounts"),
    )

    edit_icon_overlay = ft.Container(
        content=ft.Icon(
            ft.Icons.EDIT_SHARP,
            color="#ffffff",
            size=28,
            shadows=[
                ft.BoxShadow(
                    spread_radius=1,
                    blur_radius=8,
                    color=ft.Colors.WHITE,
                )
            ],
        ),
        opacity=0.0,
        animate_opacity=150,
    )

    model_box = ft.Container(
        content=ft.Stack(
            alignment=ft.Alignment.CENTER,
            controls=[
                flying_skin_img,
                edit_icon_overlay,
            ],
        ),
    )

    def reset_model_hover_state():
        edit_icon_overlay.opacity = 0.0
        home_skin_viewer.set_hovered(False)
        try:
            edit_icon_overlay.update()
        except RuntimeError:
            pass

    def on_model_click(e):
        reset_model_hover_state()
        set_active_tab("skins")

    flying_model_card = ft.Container(
        image=ft.DecorationImage(
            f"{ASSETS_DIR}/banners/skin_bg.png",
            fit=ft.BoxFit.FIT_HEIGHT,
            filter_quality=ft.FilterQuality.NONE,
        ),
        bgcolor=input_bg_color,
        border_radius=10,
        # padding=ft.Padding.only(top=10,bottom=10),
        height=280,
        width=0,
        opacity=0.0,
        scale=0.90,
        offset=ft.Offset(0.03, 0.03),
        animate_opacity=ft.Animation(380, ft.AnimationCurve.EASE_OUT_CUBIC),
        animate_scale=ft.Animation(460, ft.AnimationCurve.EASE_OUT_BACK),
        animate_offset=ft.Animation(400, ft.AnimationCurve.EASE_OUT_CUBIC),
        content=ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=0,
            controls=[
                home_gamertag_container,
                model_box,
            ],
        ),
        on_click=on_model_click,
    )

    def on_model_hover(e: ft.HoverEvent):
        is_hovered = (e.data is True) or (str(e.data).lower() in ("true", "1"))
        edit_icon_overlay.opacity = 1.0 if is_hovered else 0.0
        home_skin_viewer.set_hovered(is_hovered)
        try:
            edit_icon_overlay.update()
        except RuntimeError:
            pass

    flying_model_card.on_hover = on_model_hover

    def load_home_skin_renderer():
        active_acc = storage.get_active_account()
        uname = active_acc.get("username", "Player")
        acc_type = active_acc.get("type", "offline")
        skin_path = skins.get_skins_dir() / f"ely_{uname.lower()}.png"
        raw = None
        if acc_type == "ely" and skin_path.is_file():
            try:
                raw = skin_path.read_bytes()
            except OSError:
                pass
        im = None
        if raw:
            try:
                im = Image.open(io.BytesIO(raw))
            except Exception:
                pass
        if im is None:
            im = skins.get_default_skin("classic")

        prepared = skins.ensure_64x64(im)
        is_slim = skins.is_slim_skin(prepared)
        model = "slim" if is_slim else "classic"

        return skins.SkinRenderer(prepared, model, "flying")

    def update_home_skin_model(*, render_immediately=True):
        active_acc = storage.get_active_account()
        refresh_discord_player(active_acc)
        uname = active_acc.get("username", "Player")
        home_gamertag_text.value = uname
        is_ely = active_acc.get("type") == "ely"
        home_account_badge_text.value = "ELY.BY" if is_ely else "LOCAL"
        home_account_badge_text.color = "#9ecbff" if is_ely else accent_color
        home_account_badge.tooltip = (
            "Signed in with Ely.by"
            if is_ely
            else "Local account — plays offline, no Ely.by sign-in"
        )
        home_account_badge.bgcolor = (
            ft.Colors.with_opacity(0.24, "#82b1ff")
            if is_ely
            else ft.Colors.with_opacity(0.24, accent_color)
        )
        home_skin_viewer.set_renderer(load_home_skin_renderer())
        if render_immediately:
            flying_skin_img.src = home_skin_viewer.render_still()
        edit_icon_overlay.opacity = 0.0
        try:
            home_gamertag_text.update()
            home_account_badge.update()
            flying_skin_img.update()
            edit_icon_overlay.update()
        except RuntimeError:
            pass

    update_home_skin_model()

    # ---------------------------
    # SHARED PROGRESS & LEGACY LAUNCH STATUS
    # ---------------------------
    launch_status_text = progress_host.status_text
    launch_eta_text = progress_host.detail_text
    launch_progress_bar = progress_host.bar
    launch_activity = {"task": None}

    def finish_launch_progress():
        if launch_activity["task"]:
            launch_activity["task"].finish()
            launch_activity["task"] = None
        progress_host.set_idle(state.launcher_log_state.get("status") or "Ready to play")

    def update_launch_progress(status=None, value=_PROGRESS_UNCHANGED, eta=None):
        if status is not None:
            state.launcher_log_state["status"] = redact_credentials(str(status))
        if value is not _PROGRESS_UNCHANGED:
            state.launcher_log_state["progress"] = value
        if eta is not None:
            state.launcher_log_state["eta"] = str(eta)
        if state.launcher_log_state.get("running") and not state.launcher_log_state.get("game_started"):
            if launch_activity["task"] is None:
                launch_activity["task"] = progress_host.begin(
                    state.launcher_log_state.get("status") or "Preparing Minecraft...",
                    priority=100, delay=0, show_percentage=False,
                )
            launch_activity["task"].update(
                state.launcher_log_state.get("status") or "Preparing Minecraft...",
                value=state.launcher_log_state.get("progress"), detail=state.launcher_log_state.get("eta", ""),
            )
        else:
            finish_launch_progress()

    state.launcher_log_state["status_control"] = launch_status_text
    state.launcher_log_state["eta_control"] = launch_eta_text
    state.launcher_log_state["progress_control"] = launch_progress_bar
    state.launcher_log_state["set_track_progress"] = lambda v: update_launch_progress(
        value=v
    )


    # ---------------------------
    # PROFILES & LAUNCH SYSTEM
    # ---------------------------
    latest_release_state = {
        "version_id": storage.load_latest_release_id() or "",
        "online": False,
        "loading": True,
        "checked_at": None,
    }
    latest_release_lock = asyncio.Lock()

    def get_latest_release_profile() -> dict:
        version_id = latest_release_state["version_id"]
        latest_options = storage.load_latest_release_options()
        return {
            "id": LATEST_RELEASE_PROFILE_ID,
            "name": "Latest Release",
            "loader": "Vanilla",
            "channel": "Release",
            "minecraft_version": version_id,
            "loader_version": "",
            "version_id": version_id,
            "game_directory": minecraft_directory,
            "icon": latest_options.get("icon", ""),
            "jvm_arguments": latest_options.get("jvm_arguments", ""),
        }

    def get_launch_profiles() -> list[dict]:
        profiles = [get_latest_release_profile()]
        for profile in storage.load_installations():
            if profile.get("id") != LATEST_RELEASE_PROFILE_ID:
                profile = dict(profile)
                profile["version_id"] = get_profile_version_id(profile)
                profiles.append(profile)
        return storage.sort_profiles(profiles)

    def save_profile_order(profile_ids: list[str]) -> bool:
        current_ids = [profile["id"] for profile in get_launch_profiles()]
        visible_ids = set(profile_ids)
        if (len(visible_ids) != len(profile_ids)
                or not visible_ids.issubset(current_ids)):
            notify("Profiles changed while dragging. Please try again.", "warning")
            rebuild_profiles_view()
            rebuild_library_view()
            return False

        # Replace only the visible slots, retaining Latest Release's position
        # when ordering from Library and any profiles imported during the drag.
        reordered = iter(profile_ids)
        profile_order = [
            next(reordered) if profile_id in visible_ids else profile_id
            for profile_id in current_ids
        ]
        saved = storage.save_profile_order(profile_order)
        if not saved:
            notify("Could not save the profile order. Please try again.", "error")
            rebuild_profiles_view()
            rebuild_library_view()
        else:
            if not profiles_list_view.set_order(profile_order):
                rebuild_profiles_view()
            library_order = [pid for pid in profile_order if pid != LATEST_RELEASE_PROFILE_ID]
            if not library_list_view.set_order(library_order):
                rebuild_library_view()
            notify("Your profile order has been saved.", "success", "Profiles reordered")
        return saved

    async def refresh_latest_release(force: bool = False) -> bool:
        async with latest_release_lock:
            checked_at = latest_release_state["checked_at"]
            if (
                not force and checked_at is not None
                and time.monotonic() - checked_at < 60
            ):
                return latest_release_state["online"]
            was_online = latest_release_state["online"]
            previous_version = latest_release_state["version_id"]
            latest_release_state["loading"] = True
            release_activity = progress_host.begin("Checking latest Minecraft release...", priority=40 if force else 10)
            try:
                version_id, online = await asyncio.to_thread(get_latest_release_version)
                latest_release_state["online"] = online
                if version_id:
                    latest_release_state["version_id"] = version_id
                    if online:
                        storage.save_installations(
                            storage.load_installations(), latest_release_id=version_id
                        )
            except Exception as error:
                latest_release_state["online"] = False
                print(f"Could not refresh latest release: {error}")
            finally:
                release_activity.finish()
                latest_release_state["loading"] = False
                latest_release_state["checked_at"] = time.monotonic()
            online = latest_release_state["online"]
            if checked_at is None and not online:
                notify("You're offline. Installed versions can still be played.", "info", "Offline mode")
            elif checked_at is not None and online != was_online:
                if online:
                    notify("Your internet connection is back.", "success", "Back online")
                else:
                    notify("You're offline. Installed versions can still be played.", "warning", "Connection lost")
            if online and checked_at is not None and latest_release_state["version_id"] != previous_version:
                notify(f"Minecraft {latest_release_state['version_id']} is available in Latest Release.", "update", "New Minecraft release")
            rebuild_profiles_view()
            return latest_release_state["online"]

    def format_profile_details(profile: dict) -> str:
        if profile.get("id") == LATEST_RELEASE_PROFILE_ID and not profile.get("version_id"):
            return (
                "Checking latest release..." if latest_release_state["loading"]
                else "Connect to the internet to find the latest release"
            )
        loader = str(profile.get("loader") or "Vanilla").strip()
        mc_ver = str(
            profile.get("minecraft_version") or profile.get("version_id") or ""
        ).strip()
        loader_lower = loader.lower()
        known_loaders = {
            "vanilla": "Vanilla",
            "fabric": "Fabric",
            "forge": "Forge",
            "neoforge": "NeoForge",
            "quilt": "Quilt",
        }
        loader_label = known_loaders.get(loader_lower, "Modded")
        details = f"{loader_label} {mc_ver}".strip()
        loader_ver = str(profile.get("loader_version") or "").strip()
        if loader_lower != "vanilla" and loader_ver:
            details += f" • Loader {loader_ver}"
        return details

    def open_profile_folder(profile: dict):
        try:
            target_dir = get_profile_game_directory(profile)
            if profile.get("is_instance"):
                if not target_dir.is_dir():
                    raise FileNotFoundError(f"Instance folder is missing: {target_dir}")
            else:
                target_dir.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(str(target_dir))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target_dir)])
            else:
                subprocess.Popen(["xdg-open", str(target_dir)])
        except Exception as err:
            update_launch_progress(f"Could not open folder: {err}")
            notify(f"Could not open folder: {err}", "error")

    def select_profile(profile_id: str):
        if (not profile_id or profile_id == state.launch_selection.get("key")
                or state.launcher_log_state.get("running")
                or profiles_list_view.holding or library_list_view.holding):
            return
        state.launch_selection["key"] = profile_id
        active_acc = storage.get_active_account()
        uname = active_acc.get("username", "Player")
        opts = storage.load_profile_options()
        storage.save_profile_options(
            uname, profile_id, opts.get("profile_picture_path", "")
        )
        rebuild_profiles_view()

    def get_selected_profile() -> dict | None:
        selected_id = state.launch_selection.get("key")
        installations = get_launch_profiles()
        for p in installations:
            if p.get("id") == selected_id:
                return p
        if selected_id:
            return None
        if installations:
            return installations[0]
        return None

    def get_profile_mods_count(profile: dict) -> int:
        if not profile:
            return 0
        try:
            mods_path = get_profile_game_directory(profile) / "mods"
            if not mods_path.is_dir():
                return 0
            jar_files = [f for f in mods_path.glob("*.jar") if f.is_file()]
            return len(jar_files)
        except Exception:
            return 0

    def get_hero_profile_details(profile: dict) -> str:
        if not profile:
            return ""
        if profile.get("id") == LATEST_RELEASE_PROFILE_ID and not profile.get("version_id"):
            return format_profile_details(profile)
        loader = str(profile.get("loader") or "Vanilla").strip()
        mc_ver = str(
            profile.get("minecraft_version") or profile.get("version_id") or ""
        ).strip()
        loader_ver = str(profile.get("loader_version") or "").strip()
        channel = str(profile.get("channel") or "").strip()

        if loader.lower() == "vanilla":
            prefix = f"{channel} " if channel and channel.lower() != "release" else ""
            return f"{prefix}Minecraft {mc_ver}".strip()
        else:
            if loader_ver:
                return f"{loader} {mc_ver} • Loader {loader_ver}".strip()
            return f"{loader} {mc_ver}".strip()

    def update_hero_banner_info(profile: dict | None = None):
        if profile is None:
            profile = get_selected_profile()

        if not profile:
            hero_profile_name.value = "No Profile Selected"
            hero_instance_badge.visible = False
            hero_loader_badge.visible = False
            hero_profile_icon_box.content = build_profile_icon_content(
                None,
                size=44,
                default_icon_size=32,
                color=accent_color,
            )
            hero_details_text.value = "Select or create a profile to start playing"
            hero_status_dot.bgcolor = "#747e95"
            hero_status_text.value = "No Installation"
            hero_mods_separator.visible = False
            hero_mods_icon.visible = False
            hero_mods_text.visible = False
        else:
            name = profile.get("name", "Unnamed Profile")
            is_inst = bool(profile.get("is_instance"))

            hero_profile_name.value = name
            hero_profile_icon_box.content = build_profile_icon_content(
                profile, size=44, default_icon_size=32, color=accent_color
            )

            if is_inst:
                hero_instance_badge.visible = True
                hero_instance_badge_text.value = "INSTANCE"
            else:
                hero_instance_badge.visible = False

            # Loader badge
            loader = str(profile.get("loader") or "Vanilla").strip()
            badge_text, badge_color = get_loader_badge_info(loader)
            if badge_text:
                hero_loader_badge.visible = True
                hero_loader_badge_text.value = badge_text
                hero_loader_badge_text.color = badge_color
            else:
                hero_loader_badge.visible = False

            hero_details_text.value = get_hero_profile_details(profile)

            # Installation / Run status
            is_running = state.launcher_log_state.get("running", False)
            is_downloading = state.launcher_log_state.get("downloading", False)
            is_game_started = state.launcher_log_state.get("game_started", False)

            if is_running and is_downloading:
                hero_status_dot.bgcolor = "#64b5f6"
                hero_status_text.value = "Installing..."
            elif is_running and is_game_started:
                hero_status_dot.bgcolor = "#81c784"
                hero_status_text.value = "Game Running"
            elif is_running:
                hero_status_dot.bgcolor = "#ffb74d"
                hero_status_text.value = "Starting..."
            else:
                target_vid = profile.get("version_id") or profile.get(
                    "minecraft_version"
                )
                try:
                    installed_ids = {
                        item.get("version_id") or item.get("id")
                        for item in get_installed_versions()
                    }
                    is_installed = bool(target_vid and target_vid in installed_ids)
                except Exception:
                    is_installed = False

                if profile.get("id") == LATEST_RELEASE_PROFILE_ID and not target_vid:
                    hero_status_dot.bgcolor = "#747e95"
                    hero_status_text.value = (
                        "Checking for updates..." if latest_release_state["loading"]
                        else "Internet Required"
                    )
                elif is_installed:
                    hero_status_dot.bgcolor = "#81c784"
                    hero_status_text.value = "Ready to Play"
                else:
                    hero_status_dot.bgcolor = "#ffa726"
                    hero_status_text.value = "Not Installed"

            # Mods (if is a modloader and instance)
            loader = str(profile.get("loader") or "Vanilla").strip()
            is_modloader = loader.lower() != "vanilla"

            if is_inst and is_modloader:
                count = get_profile_mods_count(profile)
                hero_mods_separator.visible = True
                hero_mods_icon.visible = True
                hero_mods_text.visible = True
                hero_mods_text.value = f"{count} {'Mod' if count == 1 else 'Mods'}"
            else:
                hero_mods_separator.visible = False
                hero_mods_icon.visible = False
                hero_mods_text.visible = False

        try:
            hero_info_card.update()
        except RuntimeError:
            pass

    def refresh_launch_button():
        is_running = state.launcher_log_state.get("running", False)
        is_downloading = state.launcher_log_state.get("downloading", False)
        is_game_started = state.launcher_log_state.get("game_started", False)
        is_cancelling = state.launcher_log_state.get("cancelling", False)

        if is_cancelling:
            play_button_text.value = "CANCELLING..."
            play_button_icon.name = ft.Icons.HOURGLASS_EMPTY_SHARP
            play_button.disabled = True
            play_button.style.bgcolor = accent_color
            play_button_text.color = bg_color_main
            play_button_icon.color = bg_color_main
        elif is_running and is_downloading:
            play_button_text.value = "CANCEL"
            play_button_icon.name = ft.Icons.CLOSE_SHARP
            play_button.disabled = False
            play_button.style.bgcolor = "#e57373"
            play_button_text.color = "#ffffff"
            play_button_icon.color = "#ffffff"
        elif is_running and is_game_started:
            play_button_text.value = "RUNNING"
            play_button_icon.name = ft.Icons.SPORTS_ESPORTS_SHARP
            play_button.disabled = True
            play_button.style.bgcolor = accent_color
            play_button_text.color = bg_color_main
            play_button_icon.color = bg_color_main
        elif is_running:
            play_button_text.value = "STARTING..."
            play_button_icon.name = ft.Icons.HOURGLASS_EMPTY_SHARP
            play_button.disabled = True
            play_button.style.bgcolor = accent_color
            play_button_text.color = bg_color_main
            play_button_icon.color = bg_color_main
        else:
            play_button_text.value = "PLAY"
            play_button_icon.name = ft.Icons.PLAY_ARROW_SHARP
            play_button.disabled = False
            play_button.style.bgcolor = accent_color
            play_button_text.color = bg_color_main
            play_button_icon.color = bg_color_main

        # Update floating logs indicator state
        if is_running and is_game_started:
            logs_status_indicator.bgcolor = "#81c784"
            logs_status_indicator.tooltip = "Game Running"
        elif is_running:
            logs_status_indicator.bgcolor = "#ffb74d"
            logs_status_indicator.tooltip = "Launching / Installing"
        else:
            logs_status_indicator.bgcolor = "#747e95"
            logs_status_indicator.tooltip = "Idle"

        try:
            play_button.update()
            if logs_panel_container.visible:
                logs_status_indicator.update()
        except RuntimeError:
            pass
        update_hero_banner_info()

    async def launch_profile(target: dict):
        if state.launcher_log_state.get("running"):
            return
        if mods_content.busy:
            notify("Wait for the mod operation to finish before launching Minecraft.", "warning", "Mods")
            return

        active_acc = storage.get_active_account()
        username = active_acc.get("username", "Player").strip() or "Player"
        online_available = False

        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", username):
            update_launch_progress(
                "Invalid username: must be 1-16 letters, numbers, or _."
            )
            notify("Your username must contain 1–16 letters, numbers, or underscores.", "warning", "Invalid username")
            return

        discord_presence.set_player(
            username, get_account_avatar_bytes(active_acc, scale=1),
        )

        if not target.get("loader"):
            target["loader"] = "Vanilla"
        if not target.get("minecraft_version"):
            target["minecraft_version"] = target.get("version_id", "1.21.1")

        installed_ids = {item["version_id"] for item in get_installed_versions()}
        is_downloading = target.get("version_id") not in installed_ids

        state.launcher_log_state["running"] = True
        state.launcher_log_state["game_started"] = False
        state.launcher_log_state["downloading"] = is_downloading
        state.launcher_log_state["cancelling"] = False
        state.launcher_log_state["cancel_event"] = threading.Event()
        state.launcher_log_state["target_version_id"] = target.get("version_id")
        state.launcher_log_state["target_profile_id"] = target.get("id")
        discord_presence.prepare_game(target, installing=is_downloading)

        refresh_launch_button()
        rebuild_profiles_view()
        rebuild_library_view()

        update_launch_progress(
            f"Preparing {target.get('name', 'Minecraft')}...", value=None, eta=""
        )
        notify(f"Preparing {target.get('name', 'Minecraft')}…", "info", "Getting ready")

        is_modded = (target.get("loader") or "Vanilla").lower() != "vanilla"
        tracker = InstallationProgressTracker(is_modded=is_modded)

        def report_status(status):
            status_str = str(status)
            add_log_line(f"[*] {status_str}")
            tracker.on_status(status_str)
            lower = status_str.lower()
            if (
                "download" in lower
                or "librar" in lower
                or "asset" in lower
                or "install" in lower
                or "unpack" in lower
            ):
                if not state.launcher_log_state.get("downloading"):
                    state.launcher_log_state["downloading"] = True
                    if not state.launcher_log_state.get("game_started"):
                        discord_presence.prepare_game(target, installing=True)
                    refresh_launch_button()
            elif status_str in (
                "Building launch command...",
                "Starting Minecraft...",
                "Minecraft is running...",
            ):
                if state.launcher_log_state.get("downloading") and not state.launcher_log_state.get("cancelling"):
                    state.launcher_log_state["downloading"] = False
                    if not state.launcher_log_state.get("game_started"):
                        discord_presence.prepare_game(target)
                    refresh_launch_button()

            # During installation, only the UI loop publishes tracker snapshots.
            # Per-file statuses must not reset measured progress to indeterminate.
            if not state.launcher_log_state.get("downloading") and not state.launcher_log_state.get("cancelling"):
                update_launch_progress(status_str, value=None, eta="")

        def set_progress_maximum(maximum):
            tracker.on_max(maximum)

        def report_progress(current):
            tracker.on_progress(current)

        callback = {
            "setStatus": report_status,
            "setMax": set_progress_maximum,
            "setProgress": report_progress,
        }

        async def progress_ui_loop():
            while state.launcher_log_state.get(
                "running"
            ) and not state.launcher_log_state.get("game_started"):
                if state.launcher_log_state.get("downloading") and not state.launcher_log_state.get("cancelling"):
                    ratio, status_text, eta_text = tracker.get_display_state()
                    update_launch_progress(status_text, ratio, eta_text)
                await asyncio.sleep(0.05)

        progress_task = None
        output_task = None
        log_file = None
        settings = storage.load_settings()
        launch_action = settings.get("launch_action", "Keep launcher open")

        try:
            online_available = await refresh_latest_release()
            if online_available:
                account = await asyncio.to_thread(storage.get_active_account, include_credentials=True)
                if account.get("type") == "ely" and not account.get("access_token"):
                    update_launch_progress("Sign in to continue.", value=0.0, eta="")
                    notify("Sign in again to launch Minecraft with your Ely.by account.", "warning", "Sign-in required")
                    relogin_account(account)
                    return
            if target.get("id") == LATEST_RELEASE_PROFILE_ID:
                update_launch_progress("Checking latest release...", value=None, eta="")
                target = get_latest_release_profile()
                if state.launcher_log_state["cancel_event"].is_set():
                    raise DownloadCancelled("Version download cancelled.")
                if not target["version_id"]:
                    raise RuntimeError("Connect to the internet to find the latest release.")
                state.launcher_log_state["target_version_id"] = target["version_id"]
                state.launcher_log_state["downloading"] = (
                    target["version_id"] not in installed_ids
                )
                discord_presence.prepare_game(
                    target, installing=state.launcher_log_state["downloading"],
                )
                refresh_launch_button()
            progress_task = asyncio.create_task(progress_ui_loop())
            process, log_file = await start_game(
                target,
                username,
                online_available,
                callback,
                settings,
                state.launcher_log_state["cancel_event"],
            )
            tracker.finish()
            state.launcher_log_state["game_started"] = True
            state.launcher_log_state["downloading"] = False
            game_account = getattr(process, "gr8_presence_player", active_acc)
            game_face = getattr(process, "gr8_presence_face", None)
            if game_face is None:
                game_face = get_account_avatar_bytes(game_account, scale=1)
            discord_presence.game_started(
                target,
                player_name=str(game_account.get("username") or username),
                face_png=game_face,
            )
            update_launch_progress("Minecraft is running...", value=1.0, eta="")
            notify(f"{target.get('name', 'Minecraft')} is running.", "success", "Minecraft started")
            refresh_launch_button()

            loop = asyncio.get_running_loop()
            output_task = asyncio.create_task(
                asyncio.to_thread(
                    read_game_output, process, log_file,
                    lambda lines: loop.call_soon_threadsafe(add_log_lines, lines),
                    sensitive_values=getattr(process, "gr8_sensitive_values", ()),
                )
            )

            if launch_action == "Hide launcher":
                page.window.visible = False
                page.update()
            elif launch_action == "Close launcher":
                page.run_task(close_window, None)

            exit_code = await asyncio.to_thread(process.wait)
            if output_task is not None:
                await output_task

            if launch_action == "Hide launcher":
                page.window.visible = True
                page.update()

            update_launch_progress(
                (
                    "Ready to play"
                    if exit_code == 0
                    else f"Minecraft exited with code {exit_code}."
                ),
                value=0.0,
                eta="",
            )
            if exit_code == 0:
                notify("Minecraft has closed. You're ready to play again.", "info", "Game closed")
            else:
                notify(f"Minecraft exited with code {exit_code}. Open the logs for details.", "error", "Minecraft stopped")
        except DownloadCancelled:
            update_launch_progress("Version download cancelled.", value=0.0, eta="")
            notify("The version download was cancelled.", "info", "Installation cancelled")
        except Exception as error:
            if launch_action == "Hide launcher":
                try:
                    page.window.visible = True
                    page.update()
                except Exception:
                    pass
            update_launch_progress(f"Launch failed: {error}", value=0.0, eta="")
            notify(str(error), "error", "Launch failed")
        finally:
            discord_presence.game_finished()
            if progress_task is not None:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass
            if log_file is not None:
                try:
                    log_file.close()
                except Exception:
                    pass

            state.launcher_log_state["running"] = False
            state.launcher_log_state["game_started"] = False
            state.launcher_log_state["downloading"] = False
            state.launcher_log_state["cancelling"] = False
            state.launcher_log_state["cancel_event"] = None
            state.launcher_log_state["target_version_id"] = None
            state.launcher_log_state["target_profile_id"] = None
            finish_launch_progress()
            refresh_launch_button()
            rebuild_profiles_view()
            rebuild_library_view()

    def start_launch(target: dict):
        if not state.launcher_log_state.get("running"):
            page.run_task(launch_profile, target)

    def handle_main_play_click(e):
        if state.launcher_log_state.get("running"):
            if state.launcher_log_state.get(
                "downloading"
            ) and not state.launcher_log_state.get("cancelling"):
                state.launcher_log_state["cancelling"] = True
                cancel_ev = state.launcher_log_state.get("cancel_event")
                if cancel_ev is not None:
                    cancel_ev.set()
                update_launch_progress("Cancelling installation...", value=None, eta="")
                notify("Cancelling the installation…", "info")
                refresh_launch_button()
            return

        target = get_selected_profile()
        if target:
            start_launch(target)
        else:
            update_launch_progress("No installation selected to play.")
            notify("Select an installation to play.", "warning")

    on_main_play_click = handle_main_play_click

    def on_quick_play_click(profile: dict):
        select_profile(profile.get("id"))
        start_launch(profile)

    def open_edit_profile_dialog(profile: dict):
        if not profile or state.launcher_log_state.get("running"):
            return

        p_id = profile.get("id")
        is_latest_release = p_id == LATEST_RELEASE_PROFILE_ID
        initial_name = profile.get("name", "")
        initial_icon = profile.get("icon", "")
        initial_jvm = profile.get("jvm_arguments", "")

        selected_icon = {"value": initial_icon}

        def do_cancel(e):
            profile_dialogs.close()

        name_field = SimpleInput(
            hint_text="Profile name",
            value=initial_name,
            height=48,
            content_padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            autofocus=not is_latest_release,
            read_only=is_latest_release,
            color="#747e95" if is_latest_release else "#ffffff",
        )

        jvm_field = SimpleInput(
            hint_text="e.g. -Xmx4G -XX:+UseG1GC",
            value=initial_jvm,
            height=48,
            content_padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            autofocus=is_latest_release,
        )

        preview_icon_container = ft.Container(
            width=48,
            height=48,
            border_radius=6,
            bgcolor=ft.Colors.BLACK_38,
            border=ft.Border.all(1.5, accent_color),
            alignment=ft.Alignment.CENTER,
            content=build_profile_icon_content(
                {"icon": selected_icon["value"]},
                size=40,
                default_icon_size=32,
                color=accent_color,
            ),
        )

        tile_containers = {}

        def select_icon(icon_path: str):
            selected_icon["value"] = icon_path
            preview_icon_container.content = build_profile_icon_content(
                {"icon": icon_path},
                size=40,
                default_icon_size=32,
                color=accent_color,
            )
            try:
                preview_icon_container.update()
            except Exception:
                pass

            for path_key, c in tile_containers.items():
                is_active = (selected_icon["value"] or "") == (path_key or "")
                c.border = ft.Border.all(
                    1.5, accent_color if is_active else ft.Colors.TRANSPARENT
                )
                c.bgcolor = (
                    ft.Colors.with_opacity(0.30, accent_color)
                    if is_active
                    else ft.Colors.BLACK_38
                )
                try:
                    c.update()
                except Exception:
                    pass

        def pick_external_icon(e=None):
            def _choose():
                try:
                    root = tk.Tk()
                    root.withdraw()
                    root.attributes("-topmost", True)
                    selected_file = filedialog.askopenfilename(
                        title="Select Profile Icon",
                        filetypes=[
                            ("Image files", "*.png *.jpg *.jpeg *.webp *.bmp *.ico"),
                            ("All files", "*.*"),
                        ],
                    )
                    root.destroy()
                    if selected_file and Path(selected_file).is_file():
                        select_icon(selected_file)
                except Exception as err:
                    print(f"Failed to pick external icon: {err}")
                    notify(f"Could not select an icon: {err}", "error")

            threading.Thread(target=_choose, daemon=True).start()

        def make_icon_tile(icon_path: str, tooltip_text: str) -> ft.Container:
            is_active = (selected_icon["value"] or "") == (icon_path or "")
            tile_content = ft.Image(
                src=resolve_profile_icon_src(icon_path or DEFAULT_PROFILE_ICON),
                width=32,
                height=32,
                fit=ft.BoxFit.CONTAIN,
                filter_quality=ft.FilterQuality.NONE,
            )
            c = ft.Container(
                width=42,
                height=42,
                border_radius=6,
                bgcolor=(
                    ft.Colors.with_opacity(0.30, accent_color)
                    if is_active
                    else ft.Colors.BLACK_38
                ),
                border=ft.Border.all(
                    1.5, accent_color if is_active else ft.Colors.TRANSPARENT
                ),
                alignment=ft.Alignment.CENTER,
                tooltip=tooltip_text,
                content=tile_content,
                on_click=lambda e, p=icon_path: select_icon(p),
            )
            tile_containers[icon_path] = c
            return c

        icon_action_style = ft.ButtonStyle(
            bgcolor=ft.Colors.BLACK_38,
            overlay_color=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(radius=5),
            padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            side=ft.BorderSide(1, ft.Colors.with_opacity(0.35, accent_color)),
        )
        reset_icon_button = ft.IconButton(
            ft.Icons.RESTART_ALT_SHARP,
            icon_color=accent_color,
            icon_size=20,
            width=36,
            height=36,
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.BLACK_38,
                overlay_color=ft.Colors.TRANSPARENT,
                shape=ft.RoundedRectangleBorder(radius=5),
                padding=0,
                side=ft.BorderSide(1, ft.Colors.with_opacity(0.35, accent_color)),
            ),
            tooltip="Reset to default icon",
            on_click=lambda e: select_icon(""),
        )
        block_tiles = [
            make_icon_tile(path, name) for path, name in AVAILABLE_BLOCK_ICONS
        ]
        face_tiles = [make_icon_tile(path, name) for path, name in AVAILABLE_FACE_ICONS]

        def do_save(e):
            if is_latest_release:
                new_name = "Latest Release"
            else:
                new_name = (name_field.value or "").strip()
                if not new_name:
                    new_name = initial_name or "Unnamed Profile"
            new_icon = (selected_icon["value"] or "").strip()
            new_jvm = (jvm_field.value or "").strip()

            if is_latest_release:
                saved = storage.save_latest_release_options({
                    "icon": new_icon,
                    "jvm_arguments": new_jvm,
                })
            else:
                all_insts = storage.load_installations()
                for p in all_insts:
                    if p.get("id") == p_id:
                        p["name"] = new_name
                        p["icon"] = new_icon
                        p["jvm_arguments"] = new_jvm
                        break
                saved = storage.save_installations(all_insts)

            if not saved:
                notify("Could not save profile changes. Please try again.", "error")
                return

            profile["name"] = new_name
            profile["icon"] = new_icon
            profile["jvm_arguments"] = new_jvm

            profile_dialogs.close()

            rebuild_profiles_view()
            rebuild_library_view()
            update_hero_banner_info()
            update_launch_progress(f"Saved changes to '{new_name}'.", value=0.0, eta="")
            notify(f"Saved changes to {new_name}.", "success", "Profile updated")

        dialog_content = ft.Column(
            spacing=16,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.Column(
                    spacing=6,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Text(
                                    "Profile Name", size=13,
                                    weight=ft.FontWeight.W_500, color="#ffffff",
                                ),
                                *(
                                    [ft.Text(
                                        "(cannot be changed)", size=11, color="#747e95",
                                    )]
                                    if is_latest_release else []
                                ),
                            ],
                            spacing=6,
                            wrap=True,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        name_field,
                    ],
                ),
                ft.Column(
                    spacing=6,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Row(
                                    expand=True,
                                    spacing=12,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        preview_icon_container,
                                        ft.Text(
                                            "Profile Icon", size=13,
                                            weight=ft.FontWeight.W_500, color="#ffffff",
                                            expand=True,
                                        ),
                                    ],
                                ),
                                ft.Row(
                                    spacing=8,
                                    tight=True,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Button(
                                            content=ft.Row(
                                                controls=[
                                                    ft.Icon(
                                                        ft.Icons.UPLOAD_FILE_SHARP,
                                                        color=accent_color, size=16,
                                                    ),
                                                    ft.Text(
                                                        "Import...", color=accent_color, size=11,
                                                    ),
                                                ],
                                                spacing=6,
                                                tight=True,
                                                alignment=ft.MainAxisAlignment.CENTER,
                                            ),
                                            height=36,
                                            style=icon_action_style,
                                            tooltip="Import custom icon (.png, .jpg, etc.)",
                                            on_click=pick_external_icon,
                                        ),
                                        reset_icon_button,
                                    ],
                                ),
                            ],
                        ),
                        ft.Column(
                            spacing=6,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            controls=[
                                ft.Text(
                                    "Blocks", size=11, color="#747e95",
                                    weight=ft.FontWeight.BOLD,
                                ),
                                ft.Row(controls=block_tiles, spacing=6, run_spacing=6, wrap=True),
                            ],
                        ),
                        ft.Column(
                            spacing=6,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            controls=[
                                ft.Text(
                                    "Mobs & Characters", size=11, color="#747e95",
                                    weight=ft.FontWeight.BOLD,
                                ),
                                ft.Row(controls=face_tiles, spacing=6, run_spacing=6, wrap=True),
                            ],
                        ),
                    ],
                ),
                ft.Column(
                    spacing=4,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[
                        ft.Text(
                            "JVM Arguments", size=13,
                            weight=ft.FontWeight.W_500, color="#ffffff",
                        ),
                        jvm_field,
                        ft.Text(
                            "Custom arguments passed to Java when launching this profile.",
                            size=11, color="#747e95",
                        ),
                    ],
                ),
            ],
        )

        profile_dialogs.show(
            dialog_content,
            title=ft.Column(
                spacing=6,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.EDIT_SHARP, color=accent_color, size=22),
                            ft.Text(
                                "Edit Profile", font_family="ps", color=accent_color,
                                size=18, weight=ft.FontWeight.BOLD,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(format_profile_details(profile), size=12, color="#747e95"),
                ],
            ),
            actions=[
                ft.TextButton(
                    "Cancel",
                    height=36,
                    style=ft.ButtonStyle(
                        color="#747e95",
                        overlay_color=ft.Colors.TRANSPARENT,
                        shape=ft.RoundedRectangleBorder(radius=5),
                        padding=ft.Padding.symmetric(horizontal=16, vertical=0),
                    ),
                    on_click=do_cancel,
                ),
                ft.Button(
                    "Save Changes",
                    height=36,
                    style=ft.ButtonStyle(
                        bgcolor=accent_color,
                        color=bg_color_main,
                        overlay_color=ft.Colors.TRANSPARENT,
                        shape=ft.RoundedRectangleBorder(radius=5),
                        padding=ft.Padding.symmetric(horizontal=20, vertical=0),
                    ),
                    on_click=do_save,
                ),
            ],
            on_cancel=do_cancel,
        )

    def rebuild_profiles_view():
        installations = get_launch_profiles()
        selected_id = state.launch_selection.get("key")
        is_game_running = state.launcher_log_state.get("running", False)

        rows = []
        empty_state = None
        if not installations:
            empty_state = ft.Container(
                padding=ft.Padding.symmetric(vertical=30),
                alignment=ft.Alignment.CENTER,
                content=ft.Text(
                    "No installations found.",
                    font_family="ps",
                    color="#747e95",
                    size=14,
                ),
            )
        else:
            for profile in installations:
                pid = profile.get("id")
                is_selected = pid == selected_id

                icon_box = ft.Container(
                    width=36,
                    height=36,
                    border_radius=0,
                    bgcolor=ft.Colors.BLACK_38,
                    alignment=ft.Alignment.CENTER,
                    content=build_profile_icon_content(
                        profile,
                        size=32,
                        default_icon_size=24,
                        # color=accent_color if is_selected else "#8a94a6",
                    ),
                )

                title_row_controls = [
                    ft.Text(
                        profile.get("name", "Unnamed Profile"),
                        font_family="ps",
                        color=accent_color if is_selected else "#ffffff",
                        size=16,
                        weight=ft.FontWeight.W_500,
                    ),
                ]
                title_row_controls.extend(build_profile_badges(profile))

                details_text = ft.Text(
                    format_profile_details(profile),
                    size=12,
                    color="#747e95",
                )

                info_column = ft.Column(
                    [
                        ft.Row(
                            title_row_controls,
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        details_text,
                    ],
                    spacing=2,
                    expand=True,
                )

                actions = []
                if not is_selected:
                    quick_play_btn = ft.Button(
                        content=ft.Row(
                            [
                                ft.Icon(
                                    ft.Icons.PLAY_ARROW_SHARP,
                                    color=bg_color_main,
                                    size=16,
                                ),
                            ],
                            spacing=0,
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        height=30,
                        disabled=is_game_running,
                        style=ft.ButtonStyle(
                            bgcolor=accent_color,
                            overlay_color=ft.Colors.TRANSPARENT,
                            shape=ft.RoundedRectangleBorder(radius=5),
                            padding=ft.Padding.symmetric(horizontal=10, vertical=0),
                        ),
                        tooltip="Quick Play",
                        on_click=lambda e, p=profile: on_quick_play_click(p),
                    )
                    actions.append(quick_play_btn)

                folder_btn = ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN_SHARP,
                    icon_color=accent_color,
                    icon_size=20,
                    tooltip="Open Game Folder",
                    style=ft.ButtonStyle(
                        overlay_color=ft.Colors.TRANSPARENT,
                        padding=4,
                    ),
                    on_click=lambda e, p=profile: open_profile_folder(p),
                )
                actions.append(folder_btn)

                edit_btn = ft.IconButton(
                    icon=ft.Icons.EDIT_SHARP,
                    icon_color=accent_color,
                    icon_size=18,
                    tooltip="Edit Profile",
                    disabled=is_game_running,
                    style=ft.ButtonStyle(
                        overlay_color=ft.Colors.TRANSPARENT,
                        padding=4,
                    ),
                    on_click=lambda e, p=profile: open_edit_profile_dialog(p),
                )
                actions.append(edit_btn)

                actions_row = ft.Row(
                    actions,
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )

                card = ft.Container(
                    key=ft.ValueKey(f"home-profile-{pid}"),
                    data=pid,
                    margin=ft.Padding.only(bottom=8),
                    border=ft.Border.all(
                        1, accent_color if is_selected else ft.Colors.TRANSPARENT
                    ),
                    border_radius=5,
                    animate=ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC),
                    animate_scale=ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC),
                    content=profile_card_surface(
                        glow_color=accent_color,
                        selected=is_selected,
                        content=ft.Row(
                            [
                                profile_drag_region(
                                    icon_box, info_column,
                                    color=accent_color if is_selected else "#747e95",
                                    view=profiles_list_view,
                                    profile_id=pid,
                                    on_select=lambda e: select_profile(e.control.data),
                                ),
                                actions_row,
                            ],
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ),
                    on_click=lambda e: select_profile(e.control.data),
                )
                rows.append(card)

        profiles_list_view.set_cards(rows, footer=empty_state)
        profiles_count_text.value = str(len(installations))
        try:
            profiles_count_text.update()
        except RuntimeError:
            pass
        refresh_connection_status()
        update_hero_banner_info()

    # Initialize profile selection
    profile_opts = storage.load_profile_options()
    saved_key = profile_opts.get("last_selected_button", "")
    all_profiles = get_launch_profiles()
    if saved_key:
        state.launch_selection["key"] = saved_key
    elif all_profiles:
        state.launch_selection["key"] = all_profiles[0].get("id")
    else:
        state.launch_selection["key"] = None

    rebuild_profiles_view()

    home_main_area = ft.Column(
        expand=True,
        spacing=12,
        controls=[
            hero_section,
            profiles_panel_container,
            logs_panel_container,
        ],
    )

    home_sidebar = ft.Container(
        width=210,
        # padding=ft.Padding.only(top=6, bottom=6),
        content=ft.Column(
            expand=True,
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[flying_model_card],
        ),
    )

    home_content = ft.Container(
        expand=True,
        padding=ft.Padding.only(left=10, right=20, top=10, bottom=4),
        content=ft.Row(
            expand=True,
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.START,
            controls=[
                home_main_area,
                home_sidebar,
            ],
        ),
    )

    # ---------------------------
    # LIBRARY & MODS TAB VIEWS
    # ---------------------------
    library_filter = {"query": "", "loader": "all"}

    def refresh_library_filter():
        rebuild_library_view()

    library_search_field = SimpleInput(
        hint_text="Search profiles...",
        width=250,
        height=48,
        text_size=13,
        prefix_icon=ft.Icons.SEARCH_ROUNDED,
        prefix_style=ft.TextStyle(color="#747e95", size=18),
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        on_change=lambda e: (
            library_filter.__setitem__("query", e.control.value or ""),
            refresh_library_filter(),
        ),
        tooltip="Search by name, version, or loader (Ctrl+F)",
    )
    library_loader_filter = AcrylicSelector(
        [
            ("all", "All loaders"),
            ("vanilla", "Vanilla"),
            ("modded", "Modded"),
            ("instances", "Instances"),
        ],
        "all",
        lambda e: (
            library_filter.__setitem__("loader", library_loader_filter.value),
            refresh_library_filter(),
        ),
        width=164,
        hint="All loaders",
        tooltip="Filter the library",
    )
    library_new_btn = SimpleButton(
        ft.Row(
            [
                ft.Icon(ft.Icons.ADD_SHARP, color=bg_color_main, size=17),
                ft.Text("New Profile", font_family="ps", color=bg_color_main, size=13),
            ],
            spacing=5,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        height=48,
        tooltip="Create a new profile (Ctrl+N)",
        style=ft.ButtonStyle(
            bgcolor=accent_color,
            overlay_color=ft.Colors.TRANSPARENT,
            shadow_color=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(radius=5),
            padding=ft.Padding.symmetric(horizontal=14, vertical=0),
            side=ft.BorderSide(0, ft.Colors.TRANSPARENT),
        ),
        on_click=lambda e: open_new_profile_page(),
    )

    library_list_view = ProfileList(
        on_order_changed=lambda profile_ids: save_profile_order(profile_ids),
    )

    library_content = ft.Container(
        expand=True,
        padding=ft.Padding.only(left=20, right=24, top=10, bottom=4),
        content=ft.Column(
            expand=True,
            spacing=14,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Row(
                            [
                                ft.Text(
                                    "Library", font_family="ps",
                                    color=accent_color, size=32,
                                ),
                            ],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            [
                                library_search_field,
                                library_loader_filter,
                                library_new_btn,
                            ],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                ),
                profile_list_surface(library_list_view),
            ],
        ),
    )

    mods_content = ModsPage(
        page,
        get_profiles=lambda: get_launch_profiles(),
        selected_profile=lambda: get_selected_profile(),
        game_directory=lambda profile: get_profile_game_directory(profile),
        runtime_directory=Path(minecraft_directory),
        is_running=lambda: bool(state.launcher_log_state.get("running")),
        notify=notify,
        dialogs=lambda: profile_dialogs,
        open_folder=lambda folder: open_dir_safely(folder),
        create_profile=lambda: open_new_profile_page(as_instance=True),
        profile_icon=lambda profile: build_profile_icon_content(profile, size=32, default_icon_size=24),
        profile_badges=build_profile_badges,
        profile_details=format_profile_details,
        progress=progress_host,
        entrance=tab_reveal,
        on_changed=lambda: (rebuild_profiles_view(), rebuild_library_view()),
    )
    mods_tab_slot = ft.Container(expand=True, content=mods_content, opacity=0, ignore_interactions=True, disabled=True)
    ordinary_tab_host = ft.Container(expand=True)
    main_tab_stack = ft.Stack([mods_tab_slot, ordinary_tab_host], expand=True)

    def open_profile_mods(profile: dict):
        set_active_tab("mods", mods_profile_id=profile["id"])

    def remove_profile(profile_id: str):
        if mods_content.busy:
            notify("Wait for the mod operation to finish before removing a profile.", "warning", "Mods")
            return
        all_insts = storage.load_installations()
        target_profile = next((p for p in all_insts if p.get("id") == profile_id), None)
        if not target_profile:
            return

        p_name = target_profile.get("name", "Installation")
        is_inst = bool(target_profile.get("is_instance"))
        kept_shared_folder = False

        if is_inst:
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
                    instances_root = (Path(minecraft_directory) / "instances").resolve()
                    if (
                        resolved_dir.is_relative_to(instances_root)
                        and resolved_dir != instances_root
                    ):
                        kept_shared_folder = profile_creation.folder_is_referenced(
                            resolved_dir, all_insts, minecraft_directory,
                            exclude_id=profile_id,
                        )
                        if not kept_shared_folder:
                            shutil.rmtree(resolved_dir, ignore_errors=True)
                except Exception as err:
                    print(f"Error deleting instance directory: {err}")
                    notify(f"Could not delete the instance folder: {err}", "warning")

        updated = [p for p in all_insts if p.get("id") != profile_id]

        ext_pid = target_profile.get("external_profile_id")
        if not ext_pid and profile_id.startswith("minecraft-launcher:"):
            candidate = profile_id.split("minecraft-launcher:", 1)[1]
            ext_pid = candidate

        # Clean handled_external_profile_ids so re-installing later can be re-imported
        handled_ids = storage.load_handled_external_profile_ids()
        ids_to_remove = {profile_id}
        if ext_pid:
            ids_to_remove.add(ext_pid)
        if target_profile.get("id"):
            ids_to_remove.add(target_profile["id"])
        updated_handled = handled_ids - ids_to_remove
        if not storage.save_installations(updated, updated_handled):
            notify("Could not remove the profile from the library. Please try again.", "error")
            return

        if ext_pid:
            delete_external_profile(ext_pid)
            # Also keep our cached copy in gr8launcher in sync
            cached = storage.load_cached_launcher_profiles()
            if ext_pid in cached:
                del cached[ext_pid]
                storage.save_cached_launcher_profiles(cached)

        if state.launch_selection.get("key") == profile_id:
            new_key = LATEST_RELEASE_PROFILE_ID
            state.launch_selection["key"] = new_key
            active_acc = storage.get_active_account()
            uname = active_acc.get("username", "Player")
            opts = storage.load_profile_options()
            storage.save_profile_options(
                uname, new_key or "", opts.get("profile_picture_path", "")
            )

        removal_message = f"Removed '{p_name}'."
        if kept_shared_folder:
            removal_message += " Its folder is used by another profile and was kept."
        update_launch_progress(removal_message, value=0.0, eta="")
        notify(removal_message, "success", "Profile removed")
        rebuild_profiles_view()
        rebuild_library_view()

    def confirm_remove_profile(profile: dict):
        if mods_content.busy:
            notify("Wait for the mod operation to finish before removing a profile.", "warning", "Mods")
            return
        p_name = profile.get("name", "Profile")
        is_inst = bool(profile.get("is_instance"))

        def do_delete(e):
            profile_dialogs.close()
            remove_profile(profile.get("id"))

        def do_cancel(e):
            profile_dialogs.close()

        profile_dialogs.show(
            content=ft.Text(
                (
                    f"Are you sure you want to permanently delete the instance '{p_name}' and all its files?"
                    if is_inst
                    else f"Are you sure you want to remove '{p_name}' from your profiles?"
                ),
                # font_family="ps",
                color="#ffffff",
                size=13,
            ),
            height=200,
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.DELETE_OUTLINE_SHARP, color="#e57373", size=22),
                    ft.Text(
                        f"Delete {'Instance' if is_inst else 'Profile'}?",
                        font_family="ps",
                        color=accent_color,
                        size=18,
                        weight=ft.FontWeight.BOLD,
                    ),
                ],
                spacing=8,
            ),
            actions=[
                ft.TextButton(
                    "Cancel",
                    height=36,
                    autofocus=True,
                    style=ft.ButtonStyle(
                        color="#747e95",
                        overlay_color=ft.Colors.TRANSPARENT,
                        shape=ft.RoundedRectangleBorder(radius=5),
                        padding=ft.Padding.symmetric(horizontal=16, vertical=0),
                    ),
                    on_click=do_cancel,
                ),
                ft.Button(
                    "Delete Instance" if is_inst else "Remove Profile",
                    height=36,
                    style=ft.ButtonStyle(
                        bgcolor="#e57373",
                        color=bg_color_main,
                        overlay_color=ft.Colors.TRANSPARENT,
                        shape=ft.RoundedRectangleBorder(radius=5),
                        padding=ft.Padding.symmetric(horizontal=20, vertical=0),
                    ),
                    on_click=do_delete,
                ),
            ],
            on_cancel=do_cancel,
        )

    async def repair_profile(target: dict):
        if state.launcher_log_state.get("running"):
            return
        if mods_content.busy:
            notify("Wait for the mod operation to finish before repairing Minecraft.", "warning", "Mods")
            return

        try:
            target = prepare_launch_profile(target, get_installed_versions(force_refresh=True))
        except (KeyError, ValueError) as error:
            update_launch_progress(f"Cannot repair this profile: {error}", value=0.0, eta="")
            notify(str(error), "error", "Cannot repair profile")
            return

        loader = str(target.get("loader") or "Vanilla").strip()
        mc_ver = str(
            target.get("minecraft_version") or target.get("version_id") or ""
        ).strip()
        loader_ver = str(target.get("loader_version") or "").strip()

        if not mc_ver:
            update_launch_progress(
                f"Cannot repair {target.get('name', 'Installation')}: missing Minecraft version.",
                value=0.0,
                eta="",
            )
            notify("This profile is missing its Minecraft version.", "error", "Cannot repair profile")
            return

        state.launcher_log_state["running"] = True
        state.launcher_log_state["downloading"] = True
        state.launcher_log_state["cancelling"] = False
        state.launcher_log_state["cancel_event"] = threading.Event()
        refresh_launch_button()
        rebuild_profiles_view()
        rebuild_library_view()

        update_launch_progress(
            f"Repairing & verifying {target.get('name', 'Installation')}...",
            value=None,
            eta="",
        )
        notify(f"Repairing and verifying {target.get('name', 'Installation')}…", "info", "Repair started")

        is_modded = loader.lower() != "vanilla"
        tracker = InstallationProgressTracker(is_modded=is_modded)

        def report_status(status):
            status_str = str(status)
            add_log_line(f"[*] {status_str}")
            # Keep raw file statuses in the log; the UI loop renders progress.
            tracker.on_status(status_str)

        def set_progress_maximum(maximum):
            tracker.on_max(maximum)

        def report_progress(current):
            tracker.on_progress(current)

        def install_callback_handler(name):
            def handler(*args):
                cancel_event = state.launcher_log_state.get("cancel_event")
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelled("Repair cancelled.")
                cb_map = {
                    "setStatus": report_status,
                    "setProgress": report_progress,
                    "setMax": set_progress_maximum,
                }
                cb_map.get(name, lambda *_: None)(*args)
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelled("Repair cancelled.")

            return handler

        install_callback = {
            name: install_callback_handler(name)
            for name in ("setStatus", "setProgress", "setMax")
        }

        async def progress_ui_loop():
            while state.launcher_log_state.get(
                "running"
            ) and state.launcher_log_state.get("downloading"):
                if not state.launcher_log_state.get("cancelling"):
                    ratio, status_text, eta_text = tracker.get_display_state()
                    update_launch_progress(status_text, ratio, eta_text)
                await asyncio.sleep(0.05)

        progress_task = None
        try:
            progress_task = asyncio.create_task(progress_ui_loop())
            if loader.lower() == "vanilla":
                await asyncio.to_thread(
                    minecraft_launcher_lib.install.install_minecraft_version,
                    mc_ver,
                    minecraft_directory,
                    install_callback,
                )
            else:
                mod_loader = get_mod_loader(loader)
                repaired_version = await asyncio.to_thread(
                    mod_loader.install,
                    mc_ver,
                    minecraft_directory,
                    loader_version=loader_ver,
                    callback=install_callback,
                )
                if repaired_version != target["version_id"]:
                    raise RuntimeError("The installer returned a different version than the pinned profile.")
            invalidate_installed_versions_cache()
            tracker.finish()
            update_launch_progress(
                f"Successfully repaired {target.get('name')}.", value=0.0, eta=""
            )
            notify(f"Successfully repaired {target.get('name', 'the profile')}.", "success", "Repair complete")
        except DownloadCancelled:
            update_launch_progress("Repair cancelled.", value=0.0, eta="")
            notify("The profile repair was cancelled.", "info", "Repair cancelled")
        except Exception as error:
            update_launch_progress(f"Repair failed: {error}", value=0.0, eta="")
            notify(str(error), "error", "Repair failed")
        finally:
            if progress_task is not None:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass
            state.launcher_log_state["running"] = False
            state.launcher_log_state["downloading"] = False
            state.launcher_log_state["cancelling"] = False
            state.launcher_log_state["cancel_event"] = None
            finish_launch_progress()
            refresh_launch_button()
            rebuild_profiles_view()
            rebuild_library_view()

    def open_new_profile_page(*, as_instance=False):
        if as_instance:
            instance_switch.value = True
            np_folder_field.visible = True
        update_profile_inputs_defaults()
        navigate("new_profile")

    def rebuild_library_view():
        installations = storage.load_installations()
        is_game_running = state.launcher_log_state.get("running", False)
        query = library_filter["query"].strip().lower()
        loader_filter = library_filter["loader"]

        filtered = []
        for profile in installations:
            loader_val = (profile.get("loader") or "Vanilla").strip().lower()
            if loader_filter == "vanilla" and loader_val not in ("vanilla", ""):
                continue
            if loader_filter == "modded" and loader_val not in {
                "fabric", "forge", "neoforge", "quilt",
            }:
                continue
            if loader_filter == "instances" and not profile.get("is_instance"):
                continue
            if query:
                haystack = " ".join((
                    str(profile.get("name") or ""),
                    str(profile.get("minecraft_version") or profile.get("version_id") or ""),
                    loader_val,
                    str(profile.get("loader_version") or ""),
                )).lower()
                if query not in haystack:
                    continue
            filtered.append(profile)

        rows = []
        empty_state = None
        if not installations:
            empty_state = ft.Container(
                padding=ft.Padding.symmetric(vertical=28),
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.INVENTORY_2_OUTLINED, color="#747e95", size=34),
                        ft.Text(
                            "Your library is empty.",
                            font_family="ps",
                            color="#c8d2e0",
                            size=15,
                        ),
                        ft.Text(
                            "Create a profile to install a Minecraft version.",
                            size=12,
                            color="#747e95",
                        ),
                        library_new_btn,
                    ],
                    spacing=8,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
        elif not filtered:
            def clear_library_filters(e=None):
                library_filter["query"] = ""
                library_filter["loader"] = "all"
                library_search_field.value = ""
                library_loader_filter.refresh_options(library_loader_filter.options, "all")
                try:
                    library_search_field.update()
                    library_loader_filter.update()
                except RuntimeError:
                    pass
                rebuild_library_view()

            empty_state = ft.Container(
                padding=ft.Padding.symmetric(vertical=28),
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.SEARCH_OFF_ROUNDED, color="#747e95", size=34),
                        ft.Text(
                            "No profiles match your filters.",
                            font_family="ps",
                            color="#c8d2e0",
                            size=15,
                        ),
                        SimpleButton(
                            ft.Text("Clear filters", font_family="ps", color=accent_color, size=13),
                            on_click=clear_library_filters,
                            height=32,
                            style=ft.ButtonStyle(
                                bgcolor=ft.Colors.BLACK_26,
                                overlay_color=ft.Colors.TRANSPARENT,
                                shape=ft.RoundedRectangleBorder(radius=4),
                                padding=ft.Padding.symmetric(horizontal=12, vertical=0),
                            ),
                        ),
                    ],
                    spacing=8,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
        else:
            for profile in filtered:
                pid = profile.get("id")
                is_inst = bool(profile.get("is_instance"))
                loader_val = (profile.get("loader") or "Vanilla").strip().lower()
                is_modded_profile = loader_val in {"fabric", "forge", "neoforge", "quilt"}

                actions = []

                if is_modded_profile:
                    mods_btn = ft.IconButton(
                        icon=ft.Icons.EXTENSION_SHARP,
                        icon_color=accent_color,
                        icon_size=20,
                        tooltip="Manage Mods",
                        style=ft.ButtonStyle(
                            overlay_color=ft.Colors.TRANSPARENT,
                            padding=4,
                        ),
                        on_click=lambda e, p=profile: open_profile_mods(p),
                    )
                    actions.append(mods_btn)

                folder_btn = ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN_SHARP,
                    icon_color=accent_color,
                    icon_size=20,
                    tooltip="Open Game Folder",
                    style=ft.ButtonStyle(
                        overlay_color=ft.Colors.TRANSPARENT,
                        padding=4,
                    ),
                    on_click=lambda e, p=profile: open_profile_folder(p),
                )
                actions.append(folder_btn)

                edit_btn = ft.IconButton(
                    icon=ft.Icons.EDIT_SHARP,
                    icon_color=accent_color,
                    icon_size=19,
                    tooltip="Edit Profile",
                    disabled=is_game_running,
                    style=ft.ButtonStyle(
                        overlay_color=ft.Colors.TRANSPARENT,
                        padding=4,
                    ),
                    on_click=lambda e, p=profile: open_edit_profile_dialog(p),
                )
                actions.append(edit_btn)

                repair_btn = ft.IconButton(
                    icon=ft.Icons.BUILD_SHARP,
                    icon_color=accent_color,
                    icon_size=18,
                    tooltip="Repair & Verify Files",
                    disabled=is_game_running,
                    style=ft.ButtonStyle(
                        overlay_color=ft.Colors.TRANSPARENT,
                        padding=4,
                    ),
                    on_click=lambda e, p=profile: page.run_task(repair_profile, p),
                )
                actions.append(repair_btn)

                remove_btn = ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE_SHARP,
                    icon_color="#e57373",
                    icon_size=20,
                    tooltip="Delete Instance" if is_inst else "Remove Profile",
                    disabled=is_game_running,
                    style=ft.ButtonStyle(
                        overlay_color=ft.Colors.TRANSPARENT,
                        padding=4,
                    ),
                    on_click=lambda e, p=profile: confirm_remove_profile(p),
                )
                actions.append(remove_btn)

                card = ft.Container(
                    key=ft.ValueKey(f"library-profile-{pid}"),
                    data=pid,
                    margin=ft.Padding.only(bottom=8),
                    border=ft.Border.all(1, ft.Colors.TRANSPARENT),
                    border_radius=5,
                    animate=ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC),
                    animate_scale=ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC),
                    content=profile_row(
                        profile,
                        icon=build_profile_icon_content(profile, size=32, default_icon_size=24),
                        badges=build_profile_badges(profile),
                        details=format_profile_details(profile),
                        actions=actions,
                        glow_color=accent_color,
                        body_builder=lambda icon, info, profile_id=pid: profile_drag_region(
                            icon, info, color="#747e95",
                            view=library_list_view, profile_id=profile_id,
                        ),
                    ),
                )
                rows.append(card)

        library_list_view.set_cards(
            rows,
            header=None,
            footer=empty_state,
        )

    rebuild_library_view()

    # ==========================================
    # SETTINGS VIEW IMPLEMENTATION
    # ==========================================
    active_settings_sub_tab = ["general"]
    settings_section_container = ft.Container(expand=True)
    settings_reveal = StaggeredReveal(page, settings_section_container)

    def on_java_file_picked(e: ft.FilePickerResultEvent):
        if e.files and len(e.files) > 0:
            picked_path = e.files[0].path
            if picked_path:
                s = storage.load_settings()
                s["java_path"] = picked_path
                if not save_settings_with_notification(s):
                    return
                rebuild_settings_view()

    java_file_picker = ft.FilePicker(on_result=on_java_file_picked)
    page.services.append(java_file_picker)

    def open_dir_safely(target_path: Path | str):
        p = Path(target_path)
        try:
            p.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(str(p))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as err:
            print(f"Failed to open directory {p}: {err}")
            notify(f"Could not open the folder: {err}", "error")

    def make_setting_card(
        title: str,
        description: str = "",
        control: ft.Control | None = None,
        bottom_control: ft.Control | None = None,
    ) -> ft.Container:
        header_controls: list[ft.Control] = [
            ft.Text(
                title,
                font_family="ps",
                color=accent_color,
                size=15,
                weight=ft.FontWeight.BOLD,
            ),
        ]
        if description:
            header_controls.append(
                ft.Text(description, font_family="ps", color="#747e95", size=12)
            )
        header_col = ft.Column(
            spacing=3,
            expand=True if control else False,
            controls=header_controls,
        )
        if control:
            top_content = ft.Row(
                controls=[header_col, control],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        else:
            top_content = header_col

        card_children = [top_content]
        if bottom_control:
            card_children.append(ft.Container(height=10))
            card_children.append(bottom_control)

        return ft.Container(
            bgcolor=ft.Colors.BLACK_26,
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            content=ft.Column(spacing=0, controls=card_children),
        )

    def show_reset_settings_dialog():
        def do_cancel(e):
            page.pop_dialog()

        def do_reset(e):
            page.pop_dialog()
            if not save_settings_with_notification(storage.DEFAULT_SETTINGS.copy(), message="All settings have been restored to their defaults."):
                return
            discord_presence.set_enabled(storage.DEFAULT_SETTINGS["discord_rpc"])
            home_skin_viewer.set_enabled(storage.DEFAULT_SETTINGS["enable_head_tracking"])
            bg_gradient_layer.opacity = 1.0
            try:
                bg_gradient_layer.update()
            except RuntimeError:
                pass
            rebuild_settings_view()

        dlg = ft.AlertDialog(
            bgcolor="#182842",
            shape=ft.RoundedRectangleBorder(radius=5),
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.RESTORE_SHARP, color="#e57373", size=22),
                    ft.Text(
                        "Reset All Settings?",
                        font_family="ps",
                        color=accent_color,
                        size=18,
                        weight=ft.FontWeight.BOLD,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Text(
                "Are you sure you want to restore all launcher settings to default values? Custom memory limits, resolution preferences, and launch behaviors will be reset. Your accounts and instances will remain untouched.",
                font_family="ps",
                color="#ffffff",
                size=13,
            ),
            actions=[
                ft.TextButton(
                    "Cancel",
                    style=ft.ButtonStyle(
                        color="#747e95",
                        overlay_color=ft.Colors.TRANSPARENT,
                    ),
                    on_click=do_cancel,
                ),
                ft.Button(
                    "Reset to Defaults",
                    style=ft.ButtonStyle(
                        bgcolor="#e57373",
                        color="#ffffff",
                        overlay_color=ft.Colors.TRANSPARENT,
                        shape=ft.RoundedRectangleBorder(radius=5),
                    ),
                    on_click=do_reset,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)
        page.update()

    def build_general_section() -> ft.Control:
        settings = storage.load_settings()

        def on_launch_action_change(e):
            s = storage.load_settings()
            s["launch_action"] = launch_action_dd.value
            save_settings_with_notification(s)

        launch_action_dd = AcrylicSelector(
            [("Hide launcher", "Hide Launcher"), ("Keep launcher open", "Keep Launcher Open"),
             ("Close launcher", "Close Launcher")],
            str(settings.get("launch_action", "Hide launcher")), on_launch_action_change,
            width=240, pixel=True, hint="After launch",
        )

        def on_enable_orbs_change(e):
            s = storage.load_settings()
            val = bool(e.control.value)
            s["enable_orbs"] = val
            if not save_settings_with_notification(s):
                return
            bg_gradient_layer.opacity = 1.0 if val else 0.0
            try:
                bg_gradient_layer.update()
            except RuntimeError:
                pass

        orbs_switch = ft.Switch(
            value=bool(settings.get("enable_orbs", True)),
            active_color=accent_color,
            track_color="#2b3b55",
            on_change=on_enable_orbs_change,
        )

        def on_head_tracking_change(e):
            s = storage.load_settings()
            enabled = bool(e.control.value)
            s["enable_head_tracking"] = enabled
            if not save_settings_with_notification(s):
                return
            home_skin_viewer.set_enabled(enabled)

        head_tracking_switch = ft.Switch(
            value=bool(settings.get("enable_head_tracking", True)),
            active_color=accent_color,
            track_color="#2b3b55",
            on_change=on_head_tracking_change,
        )

        def on_capture_logs_change(e):
            s = storage.load_settings()
            s["capture_logs"] = bool(e.control.value)
            save_settings_with_notification(s)

        logs_switch = ft.Switch(
            value=bool(settings.get("capture_logs", True)),
            active_color=accent_color,
            track_color="#2b3b55",
            on_change=on_capture_logs_change,
        )

        def on_discord_rpc_change(e):
            s = storage.load_settings()
            enabled = bool(e.control.value)
            s["discord_rpc"] = enabled
            if not save_settings_with_notification(s):
                return
            discord_presence.set_enabled(enabled)

        discord_rpc_switch = ft.Switch(
            value=bool(settings.get("discord_rpc", True)),
            active_color=accent_color,
            track_color="#2b3b55",
            on_change=on_discord_rpc_change,
        )

        cards = [

            make_setting_card(
                title="Launch Behavior",
                control=launch_action_dd,
            ),
            make_setting_card(
                title="Discord Rich Presence",
                control=discord_rpc_switch,
            ),
            make_setting_card(
                title="Background Glow",
                control=orbs_switch,
            ),
            make_setting_card(
                title="Head Follows Cursor",
                control=head_tracking_switch,
            ),
            make_setting_card(
                title="Capture Game Logs",
                control=logs_switch,
            ),
        ]
        return ft.Column(
            spacing=14, scroll=ft.ScrollMode.ADAPTIVE, expand=True, controls=cards
        )

    def build_java_section() -> ft.Control:
        settings = storage.load_settings()
        sys_ram_mb = get_system_memory_mb()
        max_ram_mb = max(sys_ram_mb, 4096)
        curr_ram = int(settings.get("ram_mb", 4096))
        curr_ram = max(1024, min(curr_ram, max_ram_mb))

        ram_field = SimpleInput(
            value=str(curr_ram),
            width=100,
            # height=36,
            text_size=13,
            # text_style=ft.TextStyle(font_family="ps", color=accent_color),
            # border_color="#2b3b55",
            # bgcolor="#182842",
            # border_radius=5,
            suffix="MB",
            suffix_style=ft.TextStyle(font_family="ps", color="#747e95", size=11),
            text_align=ft.TextAlign.CENTER,
            # content_padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )

        divisions_count = max(1, (max_ram_mb - 1024) // 512)
        ram_slider = ft.Slider(
            min=1024,
            max=max_ram_mb,
            divisions=divisions_count,
            value=curr_ram,
            active_color=accent_color,
            inactive_color="#2b3b55",
            expand=True,
        )

        def on_slider_change(e):
            val = int(round(e.control.value / 128.0) * 128)
            val = max(1024, min(val, max_ram_mb))
            ram_field.value = str(val)
            try:
                ram_field.update()
            except RuntimeError:
                pass
            s = storage.load_settings()
            s["ram_mb"] = val
            save_settings_with_notification(s)

        ram_slider.on_change = on_slider_change

        def on_field_change(e):
            val_str = e.control.value.strip()
            if not val_str.isdigit():
                return
            val = int(val_str)
            if 1024 <= val <= max_ram_mb:
                ram_slider.value = val
                try:
                    ram_slider.update()
                except RuntimeError:
                    pass
                s = storage.load_settings()
                s["ram_mb"] = val
                save_settings_with_notification(s)

        ram_field.on_change = on_field_change

        def apply_ram_preset(mb: int):
            target_mb = max(1024, min(mb, max_ram_mb))
            ram_slider.value = target_mb
            ram_field.value = str(target_mb)
            try:
                ram_slider.update()
                ram_field.update()
            except RuntimeError:
                pass
            s = storage.load_settings()
            s["ram_mb"] = target_mb
            save_settings_with_notification(s)

        preset_chips = []
        ram_options = [
            ("2 GB", 2048),
            ("4 GB", 4096),
            ("6 GB", 6144),
            ("8 GB", 8192),
            ("Max Safe", max(1024, sys_ram_mb - 2048)),
        ]
        for label, val in ram_options:
            if val <= max_ram_mb:
                preset_chips.append(
                    ft.Container(
                        bgcolor=ft.Colors.BLACK_26,
                        border_radius=5,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                        content=ft.Text(
                            label, font_family="ps", color=accent_color, size=12
                        ),
                        on_click=lambda e, m=val: apply_ram_preset(m),
                    )
                )

        ram_bottom = ft.Column(
            spacing=8,
            controls=[
                ft.Row(
                    [
                        ram_slider,
                        ram_field,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    preset_chips,
                    spacing=8,
                ),
            ],
        )

        curr_java = str(settings.get("java_path", "")).strip()

        def reset_java_auto(e):
            s = storage.load_settings()
            s["java_path"] = ""
            if not save_settings_with_notification(s):
                return
            rebuild_settings_view()

        async def pick_java_binary(e):
            picked_files = await java_file_picker.pick_files(
                dialog_title="Select Java Executable (java.exe / javaw.exe)",
                allowed_extensions=["exe"] if sys.platform == "win32" else None,
            )
            if picked_files and len(picked_files) > 0:
                picked_path = picked_files[0].path
                if picked_path:
                    s = storage.load_settings()
                    s["java_path"] = picked_path
                    if not save_settings_with_notification(s):
                        return
                    rebuild_settings_view()

        java_buttons = [
            ft.Container(
                bgcolor=ft.Colors.BLACK_26,
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                content=ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.FOLDER_OPEN_SHARP, size=16, color=accent_color
                        ),
                        ft.Text(
                            "Browse...", font_family="ps", color=accent_color, size=12
                        ),
                    ],
                    spacing=6,
                ),
                on_click=pick_java_binary,
            )
        ]
        if curr_java:
            java_buttons.append(
                ft.Container(
                    bgcolor=ft.Colors.BLACK_26,
                    border_radius=5,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.REFRESH_SHARP, size=16, color="#747e95"),
                            ft.Text(
                                "Auto-detect",
                                font_family="ps",
                                color="#747e95",
                                size=12,
                            ),
                        ],
                        spacing=6,
                    ),
                    on_click=reset_java_auto,
                )
            )

        java_bottom = ft.Row(
            [
                ft.Container(
                    expand=True,
                    bgcolor="#182842",
                    border_radius=5,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                    content=ft.Text(
                        (
                            curr_java
                            if curr_java
                            else "Auto-detect (System & bundled JVM)"
                        ),
                        font_family="ps",
                        color=accent_color if curr_java else "#747e95",
                        size=12,
                        no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ),
                ft.Row(java_buttons, spacing=8),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        cards = [
            make_setting_card(
                title="Memory Allocation",
                description=f"{round(sys_ram_mb / 1024, 1)} GB system memory available.",
                bottom_control=ram_bottom,
            ),
            make_setting_card(
                title="Java Executable",
                bottom_control=java_bottom,
            ),
        ]
        return ft.Column(
            spacing=14, scroll=ft.ScrollMode.ADAPTIVE, expand=True, controls=cards
        )

    def build_display_section() -> ft.Control:
        settings = storage.load_settings()

        w_val = settings.get("window_width", 854)
        h_val = settings.get("window_height", 480)

        w_field = SimpleInput(
            value=str(w_val),
            width=90,
            # height=36,
            text_size=13,
            # text_style=ft.TextStyle(font_family="ps", color=accent_color),
            # border_color="#2b3b55",
            # bgcolor="#182842",
            # border_radius=5,
            prefix="W: ",
            prefix_style=ft.TextStyle(font_family="ps", color="#747e95", size=11),
            content_padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )

        h_field = SimpleInput(
            value=str(h_val),
            width=90,
            # height=36,
            text_size=13,
            # text_style=ft.TextStyle(font_family="ps", color=accent_color),
            # border_color="#2b3b55",
            # bgcolor="#182842",
            # border_radius=5,
            prefix="H: ",
            prefix_style=ft.TextStyle(font_family="ps", color="#747e95", size=11),
            content_padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )

        def save_resolution():
            ws = w_field.value.strip()
            hs = h_field.value.strip()
            if ws.isdigit() and hs.isdigit():
                w = int(ws)
                h = int(hs)
                if w >= 320 and h >= 240:
                    s = storage.load_settings()
                    s["window_width"] = w
                    s["window_height"] = h
                    save_settings_with_notification(s)

        w_field.on_change = lambda e: save_resolution()
        h_field.on_change = lambda e: save_resolution()

        def apply_res_preset(w: int, h: int):
            w_field.value = str(w)
            h_field.value = str(h)
            try:
                w_field.update()
                h_field.update()
            except RuntimeError:
                pass
            s = storage.load_settings()
            s["window_width"] = w
            s["window_height"] = h
            save_settings_with_notification(s)

        res_presets = [
            ("854 × 480", 854, 480),
            ("1024 × 768", 1024, 768),
            ("720p HD", 1280, 720),
            ("1080p FHD", 1920, 1080),
        ]
        res_chips = [
            ft.Container(
                bgcolor=ft.Colors.BLACK_26,
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                content=ft.Text(lbl, font_family="ps", color=accent_color, size=12),
                on_click=lambda e, pw=pw, ph=ph: apply_res_preset(pw, ph),
            )
            for lbl, pw, ph in res_presets
        ]

        res_bottom = ft.Column(
            spacing=8,
            controls=[
                ft.Row(
                    [
                        w_field,
                        ft.Text("×", font_family="ps", color="#747e95", size=16),
                        h_field,
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(res_chips, spacing=8),
            ],
        )

        def on_fullscreen_change(e):
            s = storage.load_settings()
            s["fullscreen"] = bool(e.control.value)
            save_settings_with_notification(s)

        fullscreen_switch = ft.Switch(
            value=bool(settings.get("fullscreen", False)),
            active_color=accent_color,
            track_color="#2b3b55",
            on_change=on_fullscreen_change,
        )

        def on_skins_change(e):
            s = storage.load_settings()
            s["enable_custom_skins"] = bool(e.control.value)
            save_settings_with_notification(s)

        skins_switch = ft.Switch(
            value=bool(settings.get("enable_custom_skins", True)),
            active_color=accent_color,
            track_color="#2b3b55",
            on_change=on_skins_change,
        )

        cards = [
            make_setting_card(
                title="Default Window Resolution",
                bottom_control=res_bottom,
            ),
            make_setting_card(
                title="Launch in Fullscreen",
                control=fullscreen_switch,
            ),
            make_setting_card(
                title="Ely.by Custom Skin Bridge",
                control=skins_switch,
            ),
        ]
        return ft.Column(
            spacing=14, scroll=ft.ScrollMode.ADAPTIVE, expand=True, controls=cards
        )

    def build_about_section() -> ft.Control:
        mc_root = Path(minecraft_launcher_lib.utils.get_minecraft_directory())
        inst_dir = Path(minecraft_directory) / "instances"
        data_dir = storage.get_data_dir()

        def copy_text(text: str):
            try:
                r = tk.Tk()
                r.withdraw()
                r.clipboard_clear()
                r.clipboard_append(text)
                r.update()
                r.destroy()
                notify("Copied to clipboard.", "success", "Copied")
                return True
            except Exception as error:
                notify(f"Could not copy to the clipboard: {error}", "error")
                return False

        def revert_pill(target_btn, original_content):
            time.sleep(1.8)
            target_btn.content = original_content
            try:
                target_btn.update()
            except Exception:
                pass

        def on_pill_copy(btn, text_to_copy, success_label="Copied!"):
            if not copy_text(text_to_copy):
                return
            orig_content = btn.content
            btn.content = ft.Row(
                [
                    ft.Icon(ft.Icons.CHECK_SHARP, size=13, color="#34d399"),
                    ft.Text(
                        success_label,
                        color="#34d399",
                        size=11,
                        weight=ft.FontWeight.BOLD,
                    ),
                ],
                spacing=5,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            try:
                btn.update()
            except Exception:
                pass
            threading.Thread(target=revert_pill, args=(btn, orig_content), daemon=True).start()

        # ==========================================
        # 1. COMMUNITY & CREATOR CARD
        # ==========================================
        discord_tag_pill = ft.Container(
            bgcolor=ft.Colors.BLACK_26,
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            tooltip="Click to copy Discord handle",
            ink=True,
            content=ft.Row(
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.DISCORD, size=15, color=accent_color),
                    ft.Text(
                        "@jaydeeppatil23",
                        color="#d6e1f0",
                        size=12,
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.Icon(ft.Icons.CONTENT_COPY_SHARP, size=12, color="#747e95"),
                ],
            ),
        )
        discord_tag_pill.on_click = lambda e, b=discord_tag_pill: on_pill_copy(
            b, "@jaydeeppatil23", "Copied!"
        )

        # Buttons with pixel textures and FilterQuality.NONE
        discord_img = ft.Image(
            src="buttons/discord_button_normal.png",
            filter_quality=ft.FilterQuality.NONE,
            width=200,
            fit=ft.BoxFit.CONTAIN,

        )
        discord_btn = ft.Container(
            content=discord_img,
            tooltip="Join the Discord Community",
            ink=True,
            on_click=lambda e: webbrowser.open("https://dsc.gg/_gr8"),
        )

        github_img = ft.Image(
            src="buttons/github_button_normal.png",
            filter_quality=ft.FilterQuality.NONE,

            width=200,

            fit=ft.BoxFit.CONTAIN,
        )
        github_btn = ft.Container(
            content=github_img,
            tooltip="View GitHub Repository",
            ink=True,
            on_click=lambda e: webbrowser.open("https://github.com/jaydeeppatil23/Gr8Launcher"),
        )

        coffee_img = ft.Image(
            src="buttons/coffee_button_normal.png",
            width=200,

            fit=ft.BoxFit.CONTAIN,
            filter_quality=ft.FilterQuality.NONE,
        )
        coffee_btn = ft.Container(
            content=coffee_img,
            tooltip="Support developer on Buy Me a Coffee",
            ink=True,
            on_click=lambda e: webbrowser.open("https://buymeacoffee.com/jaydeeeep"),
        )

        creator_card = ft.Container(
            bgcolor=ft.Colors.BLACK_26,
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=16, vertical=16),
            content=ft.Column(expand=True,
                spacing=12,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Column(
                                spacing=3,
                                controls=[
                                    ft.Text(
                                        "Made with ❤️ by Jaydeep.",
                                        font_family="ps",
                                        color=accent_color,
                                        size=15,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                ],
                            ),
                            discord_tag_pill,
                        ],
                    ),
                    ft.Row(
                        spacing=12,
                        # wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        alignment=ft.MainAxisAlignment.CENTER,
                        expand=True,
                        controls=[
                            ft.Row([
                            discord_btn,
                            github_btn,
                            coffee_btn,])
                        ],
                    ),
                ],
            ),
        )
        # ==========================================
        # 3. STORAGE & DIRECTORIES CARD
        # ==========================================
        def make_dir_entry(label: str, target: Path, icon_data):
            copy_pill = ft.Container(
                bgcolor=ft.Colors.BLACK_26,
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                tooltip="Copy path to clipboard",
                ink=True,
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.CONTENT_COPY_SHARP, size=13, color=accent_color),
                        ft.Text("Copy", color=accent_color, size=12),
                    ],
                    spacing=5,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
            copy_pill.on_click = lambda e, b=copy_pill, p=str(target): on_pill_copy(b, p)

            open_pill = ft.Container(
                bgcolor=ft.Colors.BLACK_26,
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                tooltip="Open in file explorer",
                ink=True,
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.FOLDER_OPEN_SHARP, size=14, color=accent_color),
                        ft.Text("Open", color=accent_color, size=12),
                    ],
                    spacing=5,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                on_click=lambda e, p=target: open_dir_safely(p),
            )

            return ft.Container(
                bgcolor=ft.Colors.BLACK_26,
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                content=ft.Row(
                    [
                        ft.Icon(icon_data, size=18, color=accent_color),
                        ft.Column(
                            spacing=2,
                            expand=True,
                            controls=[
                                ft.Text(
                                    label,
                                    font_family="ps",
                                    color=accent_color,
                                    size=13,
                                    weight=ft.FontWeight.BOLD,
                                ),
                                ft.Text(
                                    str(target),
                                    color="#9ba3b4",
                                    size=12,
                                    no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    selectable=True,
                                ),
                            ],
                        ),
                        ft.Row(
                            spacing=6,
                            controls=[
                                copy_pill,
                                open_pill,
                            ],
                        ),
                    ],
                    spacing=12,
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        storage_card = ft.Container(
            bgcolor=ft.Colors.BLACK_26,
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Column(
                        spacing=3,
                        controls=[
                            ft.Text(
                                "Storage Directories",
                                font_family="ps",
                                color=accent_color,
                                size=15,
                                weight=ft.FontWeight.BOLD,
                            ),
                        ],
                    ),
                    make_dir_entry("Minecraft Game Root", mc_root, ft.Icons.FOLDER_ROUNDED),
                    make_dir_entry("Isolated Instances", inst_dir, ft.Icons.LAYERS_ROUNDED),
                    make_dir_entry("Launcher Data & Configs", data_dir, ft.Icons.SETTINGS_SUGGEST_ROUNDED),
                ],
            ),
        )

        # ==========================================
        # 4. RESET SETTINGS CARD
        # ==========================================
        reset_btn = ft.Container(
            bgcolor="#e57373",
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=14, vertical=8),
            ink=True,
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.RESTORE_SHARP, size=15, color=bg_color_main),
                    ft.Text(
                        "Reset to Default Values",
                        color=bg_color_main,
                        size=12,
                        weight=ft.FontWeight.BOLD,
                    ),
                ],
                spacing=6,
                tight=True,
            ),
            on_click=lambda e: show_reset_settings_dialog(),
        )

        reset_card = ft.Container(
            bgcolor=ft.Colors.BLACK_26,
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Column(
                        spacing=3,
                        expand=True,
                        controls=[
                            ft.Text(
                                "Reset Launcher Settings",
                                font_family="ps",
                                color=accent_color,
                                size=15,
                                weight=ft.FontWeight.BOLD,
                            ),
                        ],
                    ),
                    reset_btn,
                ],
            ),
        )

        # ==========================================
        # 5. FOOTER DISCLAIMER
        # ==========================================
        footer = ft.Container(
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.only(top=6, bottom=14),
            content=ft.Text(
                "Gr8 Launcher • Not an official Minecraft product. Not approved by or associated with Mojang or Microsoft.",
                color="#50617a",
                size=11,
                text_align=ft.TextAlign.CENTER,
            ),
        )

        cards = [
            creator_card,
            storage_card,
            reset_card,
            footer,
        ]
        return ft.Column(
            spacing=14, scroll=ft.ScrollMode.ADAPTIVE, expand=True, controls=cards
        )

    sub_nav_items = [
        ("general", "General", ft.Icons.TUNE_SHARP),
        ("java", "Java & Memory", ft.Icons.MEMORY_SHARP),
        ("display", "Display", ft.Icons.DESKTOP_WINDOWS_SHARP),
        ("about", "About", ft.Icons.INFO_SHARP),
    ]

    sub_nav_buttons: dict[str, ft.Container] = {}

    def switch_settings_sub_tab(key: str, *, animate: bool = True):
        if key == active_settings_sub_tab[0] and animate:
            return
        if animate:
            tab_reveal.finish()
        active_settings_sub_tab[0] = key
        for k, btn in sub_nav_buttons.items():
            is_sel = k == key
            btn.bgcolor = ft.Colors.BLACK_26 if is_sel else ft.Colors.TRANSPARENT
            row = btn.content
            if isinstance(row, ft.Row) and len(row.controls) >= 2:
                row.controls[0].color = accent_color if is_sel else "#747e95"
                row.controls[1].color = accent_color if is_sel else "#747e95"
                row.controls[1].weight = (
                    ft.FontWeight.BOLD if is_sel else ft.FontWeight.NORMAL
                )
            try:
                btn.update()
            except RuntimeError:
                pass

        if key == "general":
            section = build_general_section()
        elif key == "java":
            section = build_java_section()
        elif key == "display":
            section = build_display_section()
        elif key == "about":
            section = build_about_section()
        else:
            section = ft.Column()

        settings_reveal.show(section, section.controls, animate=animate)

    sub_sidebar_controls = []
    for k, label, icon in sub_nav_items:
        is_sel = k == active_settings_sub_tab[0]
        btn = ft.Container(
            bgcolor=ft.Colors.BLACK_26 if is_sel else ft.Colors.TRANSPARENT,
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            content=ft.Row(
                [
                    ft.Icon(icon, size=18, color=accent_color if is_sel else "#747e95"),
                    ft.Text(
                        label,
                        font_family="ps",
                        color=accent_color if is_sel else "#747e95",
                        size=13,
                        weight=ft.FontWeight.BOLD if is_sel else ft.FontWeight.NORMAL,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_click=lambda e, sub_k=k: switch_settings_sub_tab(sub_k),
        )
        sub_nav_buttons[k] = btn
        sub_sidebar_controls.append(btn)

    settings_sub_sidebar = ft.Container(
        width=180,
        content=ft.Column(
            spacing=4,
            controls=sub_sidebar_controls,
        ),
    )

    settings_content.padding = ft.Padding.only(left=20, right=24, top=10, bottom=14)
    settings_content.content = ft.Column(
        expand=True,
        spacing=16,
        controls=[
            ft.Text("Settings", font_family="ps", color=accent_color, size=32),
            ft.Row(
                expand=True,
                spacing=16,
                controls=[
                    settings_sub_sidebar,
                    settings_section_container,
                ],
            ),
        ],
    )

    def do_rebuild_settings_view():
        switch_settings_sub_tab(active_settings_sub_tab[0], animate=False)

    rebuild_settings_view_fn = do_rebuild_settings_view
    do_rebuild_settings_view()

    ordinary_tab_host.content = home_content
    main_content_area.content = main_tab_stack

    main_layout = ft.Column(
        expand=True,
        spacing=0,
        controls=[
            main_content_area,
        ],
    )

    home_view = ft.Row(
        expand=True,
        spacing=0,
        controls=[
            sidebar,
            main_layout,
        ],
    )

    new_profile_back_btn = launcher_back_button(
        lambda e: navigate("home"), label="Cancel",
    )

    np_state = {
        "active_panel": "icon",
        "icon": "",
        "loader": "Vanilla",
        "mc_version": storage.load_latest_release_id() or "1.21.4",
        "loader_version": "",
    }

    _cached_mc_versions = {"list": [], "latest": "", "loading": False}
    _cached_loader_versions = {}

    np_tile_containers = {}

    np_avatar_container = ft.Container(
        content=build_profile_icon_content(
            {"icon": np_state["icon"]},
            size=34,
            default_icon_size=22,
            color=accent_color,
        ),
        alignment=ft.Alignment.CENTER,
    )

    np_icon_edit_overlay = ft.Container(
        content=ft.Icon(
            ft.Icons.EDIT_SHARP,
            color="#ffffff",
            size=18,
            shadows=[
                ft.BoxShadow(
                    spread_radius=1,
                    blur_radius=8,
                    color=ft.Colors.WHITE,
                )
            ],
        ),
        bgcolor=ft.Colors.with_opacity(0.40, "#000000"),
        border_radius=5,
        alignment=ft.Alignment.CENTER,
        opacity=0.0,
        animate_opacity=150,
    )

    def on_np_icon_hover(e: ft.HoverEvent):
        is_hovered = (e.data is True) or (str(e.data).lower() in ("true", "1"))
        np_icon_edit_overlay.opacity = 1.0 if is_hovered else 0.0
        try:
            np_icon_edit_overlay.update()
        except RuntimeError:
            pass

    np_icon_btn_container = ft.Container(
        width=45,
        height=45,
        bgcolor=input_bg_color,
        border_radius=5,
        border=ft.Border.all(1.5, accent_color),
        alignment=ft.Alignment.CENTER,
        tooltip="Change Profile Icon",
        content=ft.Stack(
            alignment=ft.Alignment.CENTER,
            controls=[
                np_avatar_container,
                np_icon_edit_overlay,
            ],
        ),
        on_hover=on_np_icon_hover,
    )
    np_icon_btn = SimpleButton(
        content=np_icon_btn_container,
        on_click=lambda e: switch_np_panel("icon"),
    )

    np_name_field = SimpleInput(hint_text="Profile Name", width=330, height=45)

    np_name_row = ft.Row(
        [np_icon_btn, np_name_field],
        spacing=10,
        width=400,
        alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    np_loader_text = ft.Text("Mod Loader: Vanilla", color="#ffffff", size=14)
    np_loader_btn_container = ft.Container(
        width=400,
        height=45,
        bgcolor=input_bg_color,
        border_radius=5,
        border=ft.Border.all(1.5, ft.Colors.TRANSPARENT),
        padding=ft.Padding.symmetric(horizontal=14),
        content=ft.Row(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.TUNE_SHARP, color=accent_color, size=18),
                        np_loader_text,
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Icon(ft.Icons.CHEVRON_RIGHT_SHARP, color="#747e95", size=18),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
    np_loader_btn = SimpleButton(
        content=np_loader_btn_container,
        on_click=lambda e: switch_np_panel("loader"),
    )

    np_mc_version_text = ft.Text(
        f"MC: {np_state['mc_version']}",
        color="#ffffff",
        size=13,
        weight=ft.FontWeight.W_500,
    )
    np_mc_version_btn_container = ft.Container(
        width=195,
        height=45,
        bgcolor=input_bg_color,
        border_radius=5,
        border=ft.Border.all(1.5, ft.Colors.TRANSPARENT),
        padding=ft.Padding.symmetric(horizontal=12),
        content=ft.Row(
            [
                ft.Icon(ft.Icons.SPORTS_ESPORTS_SHARP, color=accent_color, size=16),
                np_mc_version_text,
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
    np_mc_version_btn = SimpleButton(
        content=np_mc_version_btn_container,
        on_click=lambda e: switch_np_panel("mc_version"),
    )

    np_loader_version_text = ft.Text(
        "Loader: N/A", color="#ffffff", size=13, weight=ft.FontWeight.W_500
    )
    np_loader_version_btn_container = ft.Container(
        width=195,
        height=45,
        bgcolor=input_bg_color,
        border_radius=5,
        border=ft.Border.all(1.5, ft.Colors.TRANSPARENT),
        padding=ft.Padding.symmetric(horizontal=12),
        content=ft.Row(
            [
                ft.Icon(ft.Icons.LAYERS_SHARP, color=accent_color, size=16),
                np_loader_version_text,
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
    np_loader_version_btn = SimpleButton(
        content=np_loader_version_btn_container,
        on_click=lambda e: switch_np_panel("loader_version"),
    )

    def on_instance_toggle(e):
        np_folder_field.visible = bool(instance_switch.value)
        update_profile_inputs_defaults()

    instance_switch = ft.Switch(
        value=True,
        active_color=accent_color,
        track_color="#2b3b55",
        on_change=on_instance_toggle,
    )

    instance_toggle_row = ft.Container(
        width=400,
        bgcolor=input_bg_color,
        border_radius=5,
        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
        content=ft.Row(
            [
                ft.Column(
                    [
                        ft.Text(
                            "Separate Instance Folder",
                            color="#ffffff",
                            size=14,
                        ),
                        ft.Text(
                            "Keep mods, saves, and configs isolated in a custom folder",
                            size=11,
                            color="#747e95",
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
                instance_switch,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    np_folder_field = SimpleInput(hint_text="Instance Folder Name", width=400)
    np_folder_error_text = ft.Text(
        "",
        color="#e57373",
        size=11,
        visible=False,
    )

    np_user_edited_name = [False]
    np_user_edited_folder = [False]

    def compute_default_profile_name() -> str:
        loader = np_state["loader"]
        mc_ver = np_state["mc_version"]
        return f"{loader} {mc_ver}" if loader != "Vanilla" else f"Vanilla {mc_ver}"

    def show_profile_creation_error(message=""):
        np_folder_error_text.value = str(message)
        np_folder_error_text.visible = bool(message)
        try:
            np_folder_error_text.update()
        except RuntimeError:
            pass

    def update_instance_folder_default():
        if instance_switch.value and (
            not np_user_edited_folder[0] or not (np_folder_field.value or "").strip()
        ):
            curr_name = (np_name_field.value or "").strip() or compute_default_profile_name()
            try:
                np_folder_field.value = profile_creation.instance_folder_name("", curr_name)
            except ValueError:
                np_folder_field.value = ""
        validate_instance_folder()
        try:
            np_folder_field.update()
        except RuntimeError:
            pass

    def update_profile_inputs_defaults():
        def_name = compute_default_profile_name()
        if not np_user_edited_name[0]:
            np_name_field.value = def_name
            try:
                np_name_field.update()
            except RuntimeError:
                pass

        update_instance_folder_default()

    def validate_instance_folder():
        if not instance_switch.value:
            show_profile_creation_error()
            return True

        try:
            profile_creation.new_instance_directory(
                minecraft_directory, np_folder_field.value,
                (np_name_field.value or "").strip() or compute_default_profile_name(),
                storage.load_installations(),
            )
        except (OSError, ValueError) as error:
            show_profile_creation_error(str(error))
            return False
        show_profile_creation_error()
        return True

    def on_name_change(e):
        np_user_edited_name[0] = bool((np_name_field.value or "").strip())
        update_instance_folder_default()

    def on_folder_change(e):
        np_user_edited_folder[0] = bool((np_folder_field.value or "").strip())
        validate_instance_folder()

    np_name_field.on_change = on_name_change
    np_folder_field.on_change = on_folder_change

    update_profile_inputs_defaults()

    np_jvm_field = SimpleInput(
        hint_text="JVM Arguments (e.g. -Xmx4G -XX:+UseG1GC)", width=400
    )

    def do_create_profile(e):
        name = (np_name_field.value or "").strip()
        if not name:
            name = compute_default_profile_name()

        is_instance = bool(instance_switch.value)
        folder_raw = (np_folder_field.value or "").strip()
        if is_instance and not validate_instance_folder():
            notify(np_folder_error_text.value, "warning", "Check the instance folder")
            return

        loader = np_state["loader"]
        mc_ver = np_state["mc_version"]
        loader_ver = np_state["loader_version"] or ""

        if loader == "Vanilla":
            version_id = mc_ver
        elif loader == "Fabric":
            version_id = (
                f"fabric-loader-{loader_ver}-{mc_ver}" if loader_ver else mc_ver
            )
        elif loader == "Forge":
            version_id = f"{mc_ver}-forge-{loader_ver}" if loader_ver else mc_ver
        elif loader == "Quilt":
            version_id = f"quilt-loader-{loader_ver}-{mc_ver}" if loader_ver else mc_ver
        elif loader == "NeoForge":
            version_id = f"neoforge-{loader_ver}" if loader_ver else mc_ver
        else:
            version_id = mc_ver

        new_pid = f"minecraft-launcher:{uuid.uuid4().hex}"
        new_record = {
            "id": new_pid,
            "name": name,
            "loader": loader,
            "channel": "Release",
            "minecraft_version": mc_ver,
            "loader_version": loader_ver,
            "version_id": version_id,
            "source": "local",
            "is_instance": is_instance,
            "icon": np_state["icon"],
            "jvm_arguments": (np_jvm_field.value or "").strip(),
        }

        try:
            profile_creation.save_new_profile(
                new_record, minecraft_directory, folder_raw, storage,
            )
        except (OSError, ValueError) as error:
            show_profile_creation_error(str(error))
            notify(str(error), "error", "Could not create profile")
            return

        select_profile(new_pid)
        rebuild_library_view()

        np_user_edited_name[0] = False
        np_user_edited_folder[0] = False
        np_name_field.value = ""
        np_folder_field.value = ""
        np_jvm_field.value = ""
        np_folder_error_text.visible = False

        navigate("home")
        notify(f"Created {name}.", "success", "Profile created")

    np_create_btn = SimpleButton(
        content=ft.Container(
            width=400,
            height=45,
            bgcolor=accent_color,
            border_radius=5,
            alignment=ft.Alignment.CENTER,
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.ADD_SHARP, color=bg_color_main, size=18),
                    ft.Text(
                        "Create Profile",
                        font_family="ps",
                        color=bg_color_main,
                        size=15,
                        weight=ft.FontWeight.BOLD,
                    ),
                ],
                spacing=6,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        ),
        on_click=do_create_profile,
    )

    first_column = ft.Container(
        expand=True,
        alignment=ft.Alignment.CENTER,
        padding=10,
        content=ft.Column(
            [
                ft.Container(
                    content=ft.Text(
                        "New Profile",
                        font_family="ps",
                        color=accent_color,
                        size=36,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    alignment=ft.Alignment.CENTER,
                    padding=ft.Padding.only(bottom=6),
                ),
                np_name_row,
                np_loader_btn,
                ft.Row(
                    [np_mc_version_btn, np_loader_version_btn],
                    spacing=10,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                instance_toggle_row,
                np_folder_field,
                np_folder_error_text,
                np_jvm_field,
                np_create_btn,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10,
        ),
    )

    # ------------------ ADAPTIVE RIGHT COLUMN ------------------

    # 1. Icon Selector Helpers
    np_preview_icon_container = ft.Container(
        width=56,
        height=56,
        border_radius=6,
        bgcolor=ft.Colors.BLACK_38,
        border=ft.Border.all(1.5, accent_color),
        alignment=ft.Alignment.CENTER,
        content=build_profile_icon_content(
            {"icon": np_state["icon"]},
            size=44,
            default_icon_size=32,
            color=accent_color,
        ),
    )

    def np_select_icon(icon_path: str):
        np_state["icon"] = icon_path
        np_preview_icon_container.content = build_profile_icon_content(
            {"icon": icon_path},
            size=44,
            default_icon_size=32,
            color=accent_color,
        )
        np_avatar_container.content = build_profile_icon_content(
            {"icon": icon_path},
            size=34,
            default_icon_size=22,
            color=accent_color,
        )
        try:
            np_preview_icon_container.update()
            np_avatar_container.update()
        except RuntimeError:
            pass

        for path_key, c in np_tile_containers.items():
            is_active = (np_state["icon"] or "") == (path_key or "")
            c.border = ft.Border.all(
                1.5, accent_color if is_active else ft.Colors.TRANSPARENT
            )
            c.bgcolor = (
                ft.Colors.with_opacity(0.30, accent_color)
                if is_active
                else ft.Colors.BLACK_38
            )
            try:
                c.update()
            except RuntimeError:
                pass

    def np_pick_external_icon(e=None):
        def _choose():
            try:
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                selected_file = filedialog.askopenfilename(
                    title="Select Profile Icon",
                    filetypes=[
                        ("Image files", "*.png *.jpg *.jpeg *.webp *.bmp *.ico"),
                        ("All files", "*.*"),
                    ],
                )
                root.destroy()
                if selected_file and Path(selected_file).is_file():
                    np_select_icon(selected_file)
            except Exception as err:
                print(f"Failed to pick external icon: {err}")
                notify(f"Could not select an icon: {err}", "error")

        threading.Thread(target=_choose, daemon=True).start()

    def np_make_icon_tile(
        icon_path: str, tooltip_text: str, is_default: bool = False
    ) -> ft.Container:
        is_active = (np_state["icon"] or "") == (icon_path or "")
        tile_content = ft.Image(
            src=resolve_profile_icon_src(icon_path or DEFAULT_PROFILE_ICON),
            width=32,
            height=32,
            fit=ft.BoxFit.CONTAIN,
            filter_quality=ft.FilterQuality.NONE,
        )
        c = ft.Container(
            width=42,
            height=42,
            border_radius=6,
            bgcolor=(
                ft.Colors.with_opacity(0.30, accent_color)
                if is_active
                else ft.Colors.BLACK_38
            ),
            border=ft.Border.all(
                1.5, accent_color if is_active else ft.Colors.TRANSPARENT
            ),
            alignment=ft.Alignment.CENTER,
            tooltip=tooltip_text,
            content=tile_content,
        )
        np_tile_containers[icon_path] = c
        return SimpleButton(
            content=c,
            on_click=lambda e, p=icon_path: np_select_icon(p),
        )

    np_default_tile = np_make_icon_tile("", "Reset to Default (Grass)", is_default=True)
    np_block_tiles = [
        np_make_icon_tile(path, name) for path, name in AVAILABLE_BLOCK_ICONS
    ]
    np_face_tiles = [
        np_make_icon_tile(path, name) for path, name in AVAILABLE_FACE_ICONS
    ]

    def build_icon_panel() -> ft.Control:
        return ft.Column(
            [
                ft.Container(
                    width=400,
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Row(
                                spacing=12,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    np_preview_icon_container,
                                    ft.Column(
                                        spacing=2,
                                        controls=[
                                            ft.Text(
                                                "Profile Icon",
                                                size=13,
                                                weight=ft.FontWeight.W_500,
                                                color="#ffffff",
                                            ),
                                            ft.Text(
                                                "Select an icon or import custom",
                                                size=11,
                                                color="#747e95",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            ft.Row(
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Button(
                                        content=ft.Row(
                                            [
                                                ft.Icon(
                                                    ft.Icons.UPLOAD_FILE_SHARP,
                                                    color=accent_color,
                                                    size=16,
                                                ),
                                                ft.Text(
                                                    "Import...",
                                                    color=accent_color,
                                                    size=11,
                                                ),
                                            ],
                                            spacing=4,
                                            alignment=ft.MainAxisAlignment.CENTER,
                                        ),
                                        height=32,
                                        style=ft.ButtonStyle(
                                            bgcolor=ft.Colors.BLACK_38,
                                            overlay_color=ft.Colors.TRANSPARENT,
                                            shape=ft.RoundedRectangleBorder(radius=5),
                                            padding=ft.Padding.symmetric(
                                                horizontal=8, vertical=0
                                            ),
                                            side=ft.BorderSide(
                                                1,
                                                ft.Colors.with_opacity(
                                                    0.35, accent_color
                                                ),
                                            ),
                                        ),
                                        tooltip="Import custom icon (.png, .jpg, etc.)",
                                        on_click=np_pick_external_icon,
                                    ),
                                    np_default_tile,
                                ],
                            ),
                        ],
                    ),
                ),
                ft.Container(
                    width=400,
                    content=ft.Text(
                        "Blocks", size=11, color="#747e95", weight=ft.FontWeight.BOLD
                    ),
                ),
                ft.Container(
                    width=400,
                    content=ft.Row(
                        controls=np_block_tiles,
                        spacing=6,
                        wrap=True,
                    ),
                ),
                ft.Container(
                    width=400,
                    content=ft.Text(
                        "Mobs & Characters",
                        size=11,
                        color="#747e95",
                        weight=ft.FontWeight.BOLD,
                    ),
                ),
                ft.Container(
                    width=400,
                    content=ft.Row(
                        controls=np_face_tiles,
                        spacing=6,
                        wrap=True,
                    ),
                ),
            ],
            spacing=10,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # 2. Mod Loader Panel Helpers
    LOADER_DEFINITIONS = [
        (
            "Vanilla",
            "Vanilla",
            "Official untouched Minecraft release",
            ft.Icons.VERIFIED_ROUNDED,
        ),
        (
            "Fabric",
            "Fabric",
            "Fast, lightweight & modern mod loader",
            ft.Icons.AUTO_AWESOME_ROUNDED,
        ),
        (
            "Forge",
            "Forge",
            "Classic & extensive Minecraft modding platform",
            ft.Icons.CONSTRUCTION_ROUNDED,
        ),
        (
            "NeoForge",
            "NeoForge",
            "Next-gen community fork of Minecraft Forge",
            ft.Icons.BOLT_ROUNDED,
        ),
        (
            "Quilt",
            "Quilt",
            "Modular ecosystem with Fabric mod support",
            ft.Icons.HEXAGON_ROUNDED,
        ),
    ]

    def on_select_loader(loader_key: str):
        np_state["loader"] = loader_key
        np_loader_text.value = f"Mod Loader: {loader_key}"
        np_state["loader_version"] = ""
        if loader_key == "Vanilla":
            np_loader_version_text.value = "Loader: N/A"
        else:
            np_loader_version_text.value = "Loader: Latest"
            trigger_fetch_loader_versions(loader_key, np_state["mc_version"])

        try:
            np_loader_text.update()
            np_loader_version_text.update()
        except RuntimeError:
            pass

        update_profile_inputs_defaults()
        switch_np_panel("loader")

    def build_loader_panel() -> ft.Control:
        cards = []
        for key, name, desc, icon in LOADER_DEFINITIONS:
            is_active = np_state["loader"] == key
            card = SimpleButton(
                content=ft.Container(
                    width=400,
                    height=52,
                    border_radius=6,
                    bgcolor=input_bg_color,
                    border=ft.Border.all(
                        1, accent_color if is_active else ft.Colors.TRANSPARENT
                    ),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                    content=ft.Row(
                        [
                            ft.Row(
                                [
                                    ft.Icon(
                                        icon,
                                        color=accent_color if is_active else "#ffffff",
                                        size=20,
                                    ),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                name,
                                                size=13,
                                                weight=ft.FontWeight.W_500,
                                                color="#ffffff",
                                            ),
                                            ft.Text(desc, size=10, color="#747e95"),
                                        ],
                                        spacing=1,
                                        alignment=ft.MainAxisAlignment.CENTER,
                                    ),
                                ],
                                spacing=10,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Icon(
                                (
                                    ft.Icons.CHECK_CIRCLE_ROUNDED
                                    if is_active
                                    else ft.Icons.RADIO_BUTTON_UNCHECKED
                                ),
                                color=accent_color if is_active else "#747e95",
                                size=16,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
                on_click=lambda e, k=key: on_select_loader(k),
            )
            cards.append(card)

        return ft.Column(
            [
                ft.Container(
                    width=400,
                    content=ft.Row(
                        [
                            ft.Text(
                                "Select Mod Loader",
                                size=20,
                                # weight=ft.FontWeight.BOLD,
                                color=accent_color,
                                font_family="ps"

                            ),
                            ft.Text("5 options", size=11, color="#747e95"),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
                *cards,
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # 3. Minecraft Version Panel Helpers
    mc_version_search_field = SimpleInput(
        hint_text="Search version", width=270
    )
    mc_releases_only_switch = ft.Switch(
        value=True, active_color=accent_color, track_color="#2b3b55"
    )
    mc_version_list_view = ft.ListView(expand=True, spacing=4)

    def on_select_mc_version(vid: str):
        np_state["mc_version"] = vid
        np_mc_version_text.value = f"MC: {vid}"
        try:
            np_mc_version_text.update()
        except RuntimeError:
            pass

        if np_state["loader"] != "Vanilla":
            np_state["loader_version"] = ""
            np_loader_version_text.value = "Loader: Latest"
            try:
                np_loader_version_text.update()
            except RuntimeError:
                pass
            trigger_fetch_loader_versions(np_state["loader"], vid, force=True)

        update_profile_inputs_defaults()
        update_mc_version_list_ui()

    def update_mc_version_list_ui():
        if _cached_mc_versions["loading"]:
            mc_version_list_view.controls = [
                ft.Container(
                    padding=40,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Row(
                        [
                            ft.Text(
                                "Loading Minecraft versions...",
                                size=13,
                                color="#ffffff",
                            ),
                        ],
                        spacing=10,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                )
            ]
            try:
                mc_version_list_view.update()
            except RuntimeError:
                pass
            return

        search_query = (mc_version_search_field.value or "").strip().lower()
        releases_only = bool(mc_releases_only_switch.value)

        rows = []
        for v in _cached_mc_versions["list"]:
            vid = v.get("id", "")
            vtype = v.get("type", "release")
            if releases_only and vtype != "release":
                continue
            if search_query and search_query not in vid.lower():
                continue

            is_sel = vid == np_state["mc_version"]
            is_latest = vid == _cached_mc_versions.get("latest")
            row_card = SimpleButton(
                content=ft.Container(
                    height=38,
                    border_radius=4,
                    bgcolor=(
                        ft.Colors.with_opacity(0.25, accent_color)
                        if is_sel
                        else ft.Colors.BLACK_26
                    ),
                    border=ft.Border.all(
                        1, accent_color if is_sel else ft.Colors.TRANSPARENT
                    ),
                    padding=ft.Padding.symmetric(horizontal=12),
                    content=ft.Row(
                        [
                            ft.Row(
                                [
                                    ft.Icon(
                                        (
                                            ft.Icons.CHECK_CIRCLE_ROUNDED
                                            if is_sel
                                            else ft.Icons.RADIO_BUTTON_UNCHECKED
                                        ),
                                        size=14,
                                        color=accent_color if is_sel else "#747e95",
                                    ),
                                    ft.Text(
                                        vid,
                                        size=13,
                                        weight=(
                                            ft.FontWeight.W_500
                                            if is_sel
                                            else ft.FontWeight.NORMAL
                                        ),
                                        color="#ffffff",
                                    ),
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Row(
                                [
                                    ft.Container(
                                        content=ft.Text(
                                            "Latest" if is_latest else vtype.capitalize(),
                                            size=10,
                                            color=(
                                                accent_color
                                                if (is_latest or vtype == "release")
                                                 else "#747e95"
                                            ),
                                        ),
                                        bgcolor=(
                                            ft.Colors.with_opacity(0.12, accent_color)
                                            if (is_latest or vtype == "release")
                                            else ft.Colors.BLACK_38
                                        ),
                                        padding=ft.Padding.symmetric(
                                            horizontal=6, vertical=2
                                        ),
                                        border_radius=3,
                                    ),
                                ],
                                spacing=4,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
                on_click=lambda e, v_id=vid: on_select_mc_version(v_id),
            )
            rows.append(row_card)

        if not rows:
            rows.append(
                ft.Container(
                    padding=20,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text(
                        "No Minecraft versions found.", size=12, color="#747e95"
                    ),
                )
            )

        mc_version_list_view.controls = rows
        try:
            mc_version_list_view.update()
        except RuntimeError:
            pass

    def ensure_mc_versions_loaded():
        if _cached_mc_versions["list"] or _cached_mc_versions["loading"]:
            return

        _cached_mc_versions["loading"] = True
        catalog_activity = progress_host.begin("Fetching Minecraft versions...", priority=15)
        update_mc_version_list_ui()

        def _fetch():
            try:
                vlist, latest, _ = get_version_catalog()
                _cached_mc_versions["list"] = vlist
                _cached_mc_versions["latest"] = latest or ""
                valid_ids = {v.get("id") for v in vlist}
                if latest and np_state["mc_version"] not in valid_ids:
                    np_state["mc_version"] = latest
                    np_mc_version_text.value = f"MC: {latest}"
                    try:
                        np_mc_version_text.update()
                    except RuntimeError:
                        pass
                    update_profile_inputs_defaults()
                    if np_state["loader"] != "Vanilla":
                        trigger_fetch_loader_versions(np_state["loader"], latest, force=True)
            except Exception as err:
                print(f"Failed to fetch version catalog: {err}")
                notify("Could not load Minecraft versions. Check your connection and try again.", "warning", "Versions unavailable")
            finally:
                catalog_activity.finish()
                _cached_mc_versions["loading"] = False
                update_mc_version_list_ui()

        threading.Thread(target=_fetch, daemon=True).start()

    mc_version_search_field.on_change = lambda e: update_mc_version_list_ui()
    mc_releases_only_switch.on_change = lambda e: update_mc_version_list_ui()

    # Pre-fetch MC versions in background immediately
    ensure_mc_versions_loaded()

    mc_version_panel = ft.Column(
        [   ft.Text("Select a Version",font_family="ps",size=20,color=accent_color),
            ft.Container(
                width=400,
                content=ft.Row(
                    [
                        mc_version_search_field,
                        ft.Row(
                            [
                                ft.Text("Releases", size=11, color="#747e95"),
                                mc_releases_only_switch,
                            ],
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
            ft.Container(
                width=400,
                height=360,
                bgcolor=input_bg_color,
                border_radius=6,
                padding=6,
                content=mc_version_list_view,
            ),
        ],
        spacing=8,
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # 4. Loader Version Panel Helpers
    loader_version_search_field = SimpleInput(
        hint_text="Search loader build...", width=400
    )
    loader_version_list_view = ft.ListView(expand=True, spacing=4)

    def trigger_fetch_loader_versions(loader_key: str, mc_ver: str, force: bool = False):
        if loader_key == "Vanilla":
            return
        cache_key = (loader_key.lower(), mc_ver)
        if not force and cache_key in _cached_loader_versions and _cached_loader_versions[cache_key]:
            vers = _cached_loader_versions[cache_key]
            if not np_state["loader_version"] and vers:
                np_state["loader_version"] = vers[0]
                np_loader_version_text.value = f"Loader: {vers[0]}"
                try:
                    np_loader_version_text.update()
                except RuntimeError:
                    pass
            update_loader_version_list_ui()
            return

        # Mark as actively loading
        _cached_loader_versions[cache_key] = None
        loader_activity = progress_host.begin(f"Fetching {loader_key} versions...", priority=15)
        update_loader_version_list_ui()

        def _fetch():
            try:
                mod_loader = minecraft_launcher_lib.mod_loader.get_mod_loader(
                    loader_key.lower()
                )
                vers = mod_loader.get_loader_versions(mc_ver, False)
            except Exception as err:
                print(
                    f"Failed to fetch loader versions for {loader_key} {mc_ver}: {err}"
                )
                notify(f"Could not load {loader_key} versions for Minecraft {mc_ver}.", "warning", "Loader versions unavailable")
                vers = []
            finally:
                loader_activity.finish()
            _cached_loader_versions[cache_key] = vers
            if np_state["loader"] == loader_key and np_state["mc_version"] == mc_ver:
                if not np_state["loader_version"]:
                    if vers:
                        np_state["loader_version"] = vers[0]
                        np_loader_version_text.value = f"Loader: {vers[0]}"
                    else:
                        np_loader_version_text.value = "Loader: None"
                try:
                    np_loader_version_text.update()
                except RuntimeError:
                    pass
                update_loader_version_list_ui()

        threading.Thread(target=_fetch, daemon=True).start()

    def on_select_loader_version(l_ver: str):
        np_state["loader_version"] = l_ver
        np_loader_version_text.value = f"Loader: {l_ver}"
        try:
            np_loader_version_text.update()
        except RuntimeError:
            pass
        update_loader_version_list_ui()

    def update_loader_version_list_ui():
        loader_key = np_state["loader"]
        if loader_key == "Vanilla":
            return

        cache_key = (loader_key.lower(), np_state["mc_version"])
        vers = _cached_loader_versions.get(cache_key, None)
        query = (loader_version_search_field.value or "").strip().lower()

        rows = []
        if vers is None:
            rows.append(
                ft.Container(
                    padding=40,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Row(
                        [
                            ft.Text(
                                f"Loading {loader_key} versions...",
                                size=13,
                                color="#ffffff",
                            ),
                        ],
                        spacing=10,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                )
            )
        elif not vers:
            rows.append(
                ft.Container(
                    padding=30,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Column(
                        [
                            ft.Icon(
                                ft.Icons.WARNING_AMBER_ROUNDED,
                                size=32,
                                color=accent_color,
                            ),
                            ft.Text(
                                f"No {loader_key} builds found for Minecraft {np_state['mc_version']}.",
                                size=12,
                                color="#747e95",
                                text_align=ft.TextAlign.CENTER,
                            ),
                            SimpleButton(
                                content=ft.Container(
                                    content=ft.Text(
                                        "Retry",
                                        color=bg_color_main,
                                        size=11,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    bgcolor=accent_color,
                                    border_radius=4,
                                    padding=ft.Padding.symmetric(
                                        horizontal=14, vertical=6
                                    ),
                                ),
                                on_click=lambda e: trigger_fetch_loader_versions(
                                    loader_key, np_state["mc_version"], force=True
                                ),
                            ),
                        ],
                        spacing=8,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                )
            )
        else:
            for idx, ver in enumerate(vers):
                if query and query not in str(ver).lower():
                    continue
                is_sel = ver == np_state["loader_version"]
                is_latest = idx == 0
                row_card = SimpleButton(
                    content=ft.Container(
                        height=38,
                        border_radius=4,
                        bgcolor=(
                            ft.Colors.with_opacity(0.25, accent_color)
                            if is_sel
                            else ft.Colors.BLACK_26
                        ),
                        border=ft.Border.all(
                            1.5 if is_sel else 1,
                            accent_color if is_sel else ft.Colors.TRANSPARENT,
                        ),
                        padding=ft.Padding.symmetric(horizontal=12),
                        content=ft.Row(
                            [
                                ft.Row(
                                    [
                                        ft.Icon(
                                            (
                                                ft.Icons.CHECK_CIRCLE_ROUNDED
                                                if is_sel
                                                else ft.Icons.RADIO_BUTTON_UNCHECKED
                                            ),
                                            size=14,
                                            color=accent_color if is_sel else "#747e95",
                                        ),
                                        ft.Text(
                                            ver,
                                            size=13,
                                            weight=(
                                                ft.FontWeight.W_500
                                                if is_sel
                                                else ft.FontWeight.NORMAL
                                            ),
                                            color="#ffffff",
                                        ),
                                    ],
                                    spacing=8,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Container(
                                    content=ft.Text(
                                        "Latest" if is_latest else "Build",
                                        size=10,
                                        color=accent_color if is_latest else "#747e95",
                                    ),
                                    bgcolor=(
                                        ft.Colors.with_opacity(0.12, accent_color)
                                        if is_latest
                                        else ft.Colors.BLACK_38
                                    ),
                                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                    border_radius=3,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ),
                    on_click=lambda e, lv=ver: on_select_loader_version(lv),
                )
                rows.append(row_card)

        loader_version_list_view.controls = rows
        try:
            loader_version_list_view.update()
        except RuntimeError:
            pass

    loader_version_search_field.on_change = lambda e: update_loader_version_list_ui()

    loader_version_panel = ft.Column(
        [ft.Text("Select a Version",font_family="ps",size=20,color=accent_color),
            loader_version_search_field,
            ft.Container(
                width=400,
                height=360,
                bgcolor=input_bg_color,
                border_radius=6,
                padding=6,
                content=loader_version_list_view,
            ),
        ],
        spacing=8,
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    vanilla_info_panel = ft.Container(
        width=400,
        height=360,
        bgcolor=input_bg_color,
        border_radius=6,
        padding=20,
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            [
                ft.Icon(
                    ft.Icons.INFO_OUTLINE_ROUNDED, size=40, color=accent_color
                ),
                ft.Text(
                    "Vanilla Minecraft",
                    size=16,
                    weight=ft.FontWeight.BOLD,
                    color="#ffffff",
                ),
                ft.Text(
                    "Vanilla does not use a mod loader. Choose Fabric, Forge, NeoForge, or Quilt to install mods.",
                    size=12,
                    color="#747e95",
                    text_align=ft.TextAlign.CENTER,
                ),
                SimpleButton(
                    content=ft.Container(
                        content=ft.Text(
                            "Select Mod Loader",
                            color=bg_color_main,
                            size=12,
                            font_family="ps",
                        ),
                        bgcolor=accent_color,
                        border_radius=5,
                        padding=ft.Padding.symmetric(horizontal=14, vertical=8),
                    ),
                    on_click=lambda e: switch_np_panel("loader"),
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10,
        ),
    )

    # Adaptive panel host
    adaptive_panel_container = ft.Container(
        width=400,
        alignment=ft.Alignment.CENTER,
        content=build_icon_panel(),
    )

    def switch_np_panel(panel_name: str):
        np_state["active_panel"] = panel_name

        np_icon_btn_container.border = ft.Border.all(
            1.5, accent_color if panel_name == "icon" else ft.Colors.TRANSPARENT
        )
        np_loader_btn_container.border = ft.Border.all(
            1.5, accent_color if panel_name == "loader" else ft.Colors.TRANSPARENT
        )
        np_mc_version_btn_container.border = ft.Border.all(
            1.5, accent_color if panel_name == "mc_version" else ft.Colors.TRANSPARENT
        )
        np_loader_version_btn_container.border = ft.Border.all(
            1.5,
            accent_color if panel_name == "loader_version" else ft.Colors.TRANSPARENT,
        )

        try:
            np_icon_btn_container.update()
            np_loader_btn_container.update()
            np_mc_version_btn_container.update()
            np_loader_version_btn_container.update()
        except RuntimeError:
            pass

        if panel_name == "icon":
            adaptive_panel_container.content = build_icon_panel()
        elif panel_name == "loader":
            adaptive_panel_container.content = build_loader_panel()
        elif panel_name == "mc_version":
            adaptive_panel_container.content = mc_version_panel
            ensure_mc_versions_loaded()
            update_mc_version_list_ui()
        elif panel_name == "loader_version":
            if np_state["loader"] == "Vanilla":
                adaptive_panel_container.content = vanilla_info_panel
            else:
                adaptive_panel_container.content = loader_version_panel
                trigger_fetch_loader_versions(np_state["loader"], np_state["mc_version"])
                update_loader_version_list_ui()

        try:
            adaptive_panel_container.update()
        except RuntimeError:
            pass

    second_column = ft.Container(
        expand=True,
        alignment=ft.Alignment.CENTER,
        padding=10,
        content=ft.Column(
            [
                ft.Container(
                    width=400,
                    content=adaptive_panel_container,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    new_profile_view = ft.Stack(
        expand=True,
        controls=[
            ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                content=ft.Row(
                    [
                        first_column,
                        second_column,
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    expand=True,
                ),
            ),
            ft.Container(
                top=16,
                left=20,
                content=new_profile_back_btn,
            ),
        ],
    )

    nav_page_transition_id = [0]

    async def _transition_page(target_view, seq: int):
        content_area.opacity = 0.0
        try:
            content_area.update()
        except RuntimeError:
            pass

        await asyncio.sleep(0.12)
        if nav_page_transition_id[0] != seq:
            return

        content_area.content = target_view
        content_area.opacity = 1.0
        try:
            content_area.update()
        except RuntimeError:
            pass

    has_home_animated = [False]

    async def run_home_entrance_animation():
        if has_home_animated[0]:
            return
        has_home_animated[0] = True

        await asyncio.sleep(0.08)

        # Diagonal ripple cascade waves (top-left to bottom-right)
        waves = [
            # Wave 0: Window custom title bar
            [custom_title_bar],
            # Wave 1: First sidebar button (Home)
            [sidebar_btn_controls.get("home")],
            # Wave 2: Second sidebar button (Library) + Top content banner (Hero Section)
            [sidebar_btn_controls.get("library"), hero_section],
            # Wave 3: Third sidebar button (Mods) + Center content (Profiles panel)
            [sidebar_btn_controls.get("mods"), profiles_panel_container],
            # Wave 4: Fourth sidebar button (Skins) + Right column top (3D skin model)
            [sidebar_btn_controls.get("skins"), flying_model_card],
            # Wave 5: Fifth sidebar button (Settings)
            [sidebar_btn_controls.get("settings")],
            # Wave 6: Sixth sidebar button (Accounts)
            [sidebar_btn_controls.get("accounts")],
        ]

        for wave in waves:
            for ctrl in wave:
                if ctrl is not None:
                    if tab_reveal.generation and any(
                        ctrl is item for item in tab_reveal_controls("home")
                    ):
                        continue
                    ctrl.opacity = 1.0
                    ctrl.scale = 1.0
                    ctrl.offset = ft.Offset(0, 0)
                    try:
                        ctrl.update()
                    except Exception:
                        pass
            await asyncio.sleep(0.065)

    async def play_login_entrance_animation():
        await asyncio.sleep(0.08)
        login_waves = [
            [custom_title_bar],
            [login_form_container],
            [login_branding_container, login_options_container],
        ]
        for wave in login_waves:
            for ctrl in wave:
                if ctrl is not None:
                    ctrl.opacity = 1.0
                    ctrl.scale = 1.0
                    ctrl.offset = ft.Offset(0, 0)
                    try:
                        ctrl.update()
                    except Exception:
                        pass
            await asyncio.sleep(0.08)

    def navigate(route_name: str):
        if active_tab == "mods":
            if route_name == "home":
                mods_content.enter()
            else:
                mods_content.leave()
        if route_name == "home":
            target = home_view
            if not has_home_animated[0]:
                page.run_task(run_home_entrance_animation)
        elif route_name == "login":
            target = login_view
        elif route_name == "new_profile":
            target = new_profile_view
        else:
            return

        progress_host.padding = ft.Padding.only(left=210 if route_name == "home" else 20, right=24, top=4, bottom=8)
        progress_host.set_show_text(route_name == "home" and active_tab == "home")
        try:
            progress_host.update()
        except RuntimeError:
            pass
        discord_presence.set_page(active_tab if route_name == "home" else route_name)
        if content_area.content != target:
            home_skin_viewer.suspend()
            nav_page_transition_id[0] += 1
            page.run_task(_transition_page, target, nav_page_transition_id[0])

    def open_login(is_add_account: bool = False, account: dict | None = None):
        # Prepare the entire form before navigation mounts it. Updating an
        # individual input here races the page's animated transition.
        if account is not None:
            select_auth("ely_by", update_controls=False)
        login_title_text.value = "Add Account" if is_add_account and account is None else "Login"
        back_btn.visible = is_add_account
        username_field.value = (account.get("email") or account.get("username", "")) if account else ""
        pass_field.value = ""
        totp_field.value = ""
        totp_field.visible = False
        login_error_text.visible = False
        ely_register_btn.visible = auth_mode == "ely_by"
        login_form_container.opacity = 1.0
        login_form_container.scale = 1.0
        login_form_container.offset = ft.Offset(0, 0)
        login_branding_container.opacity = 1.0
        login_branding_container.scale = 1.0
        login_branding_container.offset = ft.Offset(0, 0)
        login_options_container.opacity = 1.0
        navigate("login")
        if content_area.content is login_view:
            content_area.update()

    is_initial_logged_in = has_saved_account()
    content_area = ft.Container(
        content=home_view if is_initial_logged_in else login_view,
        expand=True,
        opacity=1.0,
        animate_opacity=150,
    )
    profile_dialogs = ProfileDialogHost(
        content_area, title_bar_height=custom_title_bar.height,
    )

    async def focus_library_search():
        await asyncio.sleep(0.08)
        library_search_field.focus()
        try:
            page.update()
        except RuntimeError:
            pass

    def handle_keyboard_event(e: ft.KeyboardEvent):
        profile_dialogs.handle_keyboard_event(e)
        if profile_dialogs.visible or e.alt or e.meta:
            return
        key = (e.key or "").lower()
        if e.ctrl:
            if key in {"1", "2", "3", "4", "5", "6"}:
                if content_area.content is home_view:
                    set_active_tab(tabs[int(key) - 1][0])
            elif key == "f":
                if content_area.content is home_view:
                    set_active_tab("library")
                    page.run_task(focus_library_search)
            elif key == "n":
                if content_area.content is home_view:
                    open_new_profile_page()
            elif key == "enter":
                if content_area.content is home_view and not state.launcher_log_state.get("running"):
                    start_launch(get_selected_profile())
            return
        if key in {"f5"} and content_area.content is home_view:
            refresh_launch_button()

    page.on_keyboard_event = handle_keyboard_event

    # Dedicated background layer for the roaming gradient (isolated to prevent UI redraw flickers)
    initial_settings = storage.load_settings()
    bg_gradient_layer = ft.Container(
        expand=True,
        opacity=1.0 if initial_settings.get("enable_orbs", True) else 0.0,
        animate=ft.Animation(duration=14000, curve=ft.AnimationCurve.EASE_IN_OUT),
        gradient=ft.RadialGradient(
            center=ft.Alignment.TOP_RIGHT,
            radius=0.9,
            colors=[
                bg_glow_color,
                bg_color_main,
            ],
        ),
    )

    # Static UI layer over the background
    ui_layer = ft.Column(
        expand=True,
        spacing=0,
        controls=[
            custom_title_bar,
            ft.Stack(
                expand=True,
                controls=[
                    # The sidebar reserves only the bar, matching the Library layout.
                    ft.Container(
                        left=0, top=0, right=0, bottom=ProgressHost.BAR_AREA_HEIGHT,
                        content=content_area,
                    ),
                    ft.Container(
                        left=0, right=0, bottom=0,
                        content=progress_host,
                    ),
                ],
            ),
        ],
    )

    # Root stack isolating the background animation from the UI elements
    root_layout = ft.Stack(
        expand=True,
        on_size_change=toasts.resize,
        controls=[
            bg_gradient_layer,
            ft.GestureDetector(
                content=ui_layer,
                expand=True,
                hover_interval=33,
                on_hover=on_home_cursor_move,
                on_enter=on_home_cursor_move,
                on_exit=lambda e: home_skin_viewer.reset_target(),
                on_size_change=on_home_pointer_resize,
            ),
            profile_dialogs,
            toasts,
        ],
    )
    page.add(root_layout)
    toasts.start()
    progress_host.padding = ft.Padding.only(left=210 if is_initial_logged_in else 20, right=24, top=4, bottom=8)
    progress_host.set_show_text(is_initial_logged_in and active_tab == "home")
    progress_host.start()
    refresh_discord_player()
    discord_presence.set_page(active_tab if is_initial_logged_in else "login")
    discord_presence.start()

    if is_initial_logged_in:
        page.run_task(run_home_entrance_animation)
    else:
        page.run_task(play_login_entrance_animation)

    async def roam_gradient():
        curr_x, curr_y = 0.85, -0.85
        heading = random.uniform(math.pi * 0.7, math.pi * 1.3)
        await asyncio.sleep(4)  # Initial calm pause on launch

        while True:
            try:
                current_settings = storage.load_settings()
                if not current_settings.get("enable_orbs", True):
                    if bg_gradient_layer.opacity != 0.0:
                        bg_gradient_layer.opacity = 0.0
                        try:
                            bg_gradient_layer.update()
                        except RuntimeError:
                            pass
                    await asyncio.sleep(2)
                    continue
                else:
                    if bg_gradient_layer.opacity != 1.0:
                        bg_gradient_layer.opacity = 1.0
                        try:
                            bg_gradient_layer.update()
                        except RuntimeError:
                            pass

                # 1. Gentle random turn (-45° to +45°)
                heading += random.uniform(-math.pi / 4, math.pi / 4)

                # 2. Small incremental drift distance (very subtle meander)
                step_dist = random.uniform(0.20, 0.38)
                target_x = curr_x + math.cos(heading) * step_dist
                target_y = curr_y + math.sin(heading) * step_dist

                # 3. Soft reflection if approaching window edges
                if target_x < -0.8 or target_x > 0.85:
                    heading = math.pi - heading
                    target_x = max(
                        -0.8, min(0.85, curr_x + math.cos(heading) * step_dist)
                    )
                if target_y < -0.85 or target_y > 0.75:
                    heading = -heading
                    target_y = max(
                        -0.85, min(0.75, curr_y + math.sin(heading) * step_dist)
                    )

                curr_x, curr_y = round(target_x, 3), round(target_y, 3)

                # 4. Very slow, steady movement (12 to 18 seconds)
                move_duration = random.randint(12, 18)
                bg_gradient_layer.animate = ft.Animation(
                    duration=move_duration * 1000,
                    curve=ft.AnimationCurve.EASE_IN_OUT,
                )
                bg_gradient_layer.gradient = ft.RadialGradient(
                    center=ft.Alignment(curr_x, curr_y),
                    radius=round(random.uniform(0.88, 1.05), 2),
                    colors=[bg_glow_color, bg_color_main],
                )
                bg_gradient_layer.update()

                # Glide smoothly for the duration of the move
                await asyncio.sleep(move_duration)

                # 5. Take a calm break / pause where the gradient stays completely still (4 to 8 seconds)
                break_duration = random.randint(4, 8)
                await asyncio.sleep(break_duration)

            except Exception:
                break

    page.run_task(roam_gradient)

    def sync_external_launcher_profiles():
        try:
            if sync_local_installations():
                rebuild_profiles_view()
                rebuild_library_view()
                notify("Your library is synced with local Minecraft installations.", "update", "Library updated")
        except Exception as err:
            print(f"Error syncing local Minecraft installations: {err}")
            notify("Could not sync local Minecraft installations.", "warning", "Library sync failed")

    sync_external_launcher_profiles()

    last_sources_stamp = [installation_sources_stamp(minecraft_directory)]

    async def watch_external_profiles():
        while True:
            await asyncio.sleep(2.5)
            if state.launcher_log_state.get("running"):
                continue
            try:
                stamp = installation_sources_stamp(minecraft_directory)
                if stamp != last_sources_stamp[0]:
                    last_sources_stamp[0] = stamp
                    sync_external_launcher_profiles()
            except Exception:
                pass

    async def monitor_connectivity():
        while True:
            await asyncio.sleep(30)
            connected = await asyncio.to_thread(check_internet_connection)
            if connected != latest_release_state["online"]:
                was_online = latest_release_state["online"]
                online = await refresh_latest_release(force=True)
                if online and not was_online:
                    _cached_mc_versions["list"] = []
                    ensure_mc_versions_loaded()
                    if active_tab == "skins":
                        page.run_task(sync_active_skin)

    page.run_task(watch_external_profiles)
    page.run_task(refresh_latest_release)
    page.run_task(monitor_connectivity)
    page.run_task(asyncio.to_thread, skins.prune_skin_cache)


if __name__ == "__main__":
    ft.run(main, assets_dir=str(ASSETS_DIR))
