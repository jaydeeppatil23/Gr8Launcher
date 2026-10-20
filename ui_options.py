"""
Options Dashboard and Settings Subpages for Gr8 Launcher.
"""

from pathlib import Path
import os
import random
import sys
import subprocess
import flet as ft
from colors import get_theme, get_theme_names, get_pagecolor, DEFAULT_THEME
import json
import threading
import time
from colors import DEFAULT_THEME
from storage import (
    DEFAULT_SETTINGS,
    save_settings,
    save_theme_choice,
    save_profile_options,
    load_accounts,
    get_active_account,
    set_active_account,
    add_or_update_account,
    remove_account,
    update_offline_username,
)
import ely_auth
import skins
from ui_components import (
    rounded_button_style,
    clickable_mouse_cursor,
    tooltip,
    build_themed_switch,
    create_ambient_gradient_orbs,
    animate_ambient_gradient_orbs,
)
import state

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


def open_system_folder(folder_path: str):
    try:
        path = Path(folder_path)
        path.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def build_options_views(
    page: ft.Page,
    theme: dict,
    settings: dict,
    persist_setting,
    navigate,
    notify,
    copy_to_clipboard,
    open_url,
    theme_dropdown: ft.Dropdown,
    available_themes: list[str],
    maximum_memory_mb: int,
    minecraft_directory: str,
    account_picture_preview: ft.Control = None,
    account_picture_status: ft.Control = None,
    choose_account_picture = None,
    clear_account_picture = None,
    remove_managed_account_picture = None,
    refresh_account_picture = None,
    update_sidebar_username = None,
    play_button_ref: dict = None,
    refresh_launch_button = None,
    rebuild_page_callback = None,
    set_orbs_enabled = None,
    show_dialog_with_blur = None,
    close_dialog_with_blur = None,
) -> dict[str, ft.Control]:
    selected_theme = state.selected_theme
    active_page = state.active_page
    options_background_state = state.options_background_state
    player_name_state = state.player_name_state
    launch_selection = state.launch_selection

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
        return ft.Button(
            width=170,
            height=130,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.HOVERED: theme.get("card_hover", theme["card"]),
                    ft.ControlState.DEFAULT: theme["surface"],
                },
                side={
                    ft.ControlState.HOVERED: ft.BorderSide(1, color=theme["btn_primary"]),
                    ft.ControlState.DEFAULT: ft.BorderSide(1, color=theme["border"]),
                },
                shape=ft.RoundedRectangleBorder(radius=14),
                elevation=0,
                shadow_color=ft.Colors.TRANSPARENT,
                overlay_color={
                    ft.ControlState.PRESSED: ft.Colors.with_opacity(0.1, theme["btn_primary"]),
                    ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT,
                },
                padding=0,
                mouse_cursor=clickable_mouse_cursor,
            ),
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

    options_categories = [
        {"id": "options_ui", "title": "UI", "icon": ft.Icons.PALETTE_OUTLINED},
        {"id": "options_java", "title": "Java", "icon": ft.Icons.MEMORY},
        {"id": "options_game", "title": "Game", "icon": ft.Icons.SPORTS_ESPORTS_OUTLINED},
        {"id": "options_accounts", "title": "Accounts", "icon": ft.Icons.PERSON_OUTLINE},
        {"id": "options_launcher", "title": "Launcher", "icon": ft.Icons.TUNE},
        {"id": "options_about", "title": "About", "icon": ft.Icons.INFO_OUTLINE},
    ]

    options_orb1, options_orb2 = create_ambient_gradient_orbs(theme, settings.get("enable_orbs", True))
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
                lambda: settings.get("enable_orbs", True),
            )
        finally:
            options_background_state["animation_running"] = False

    options_background_state["animate"] = animate_options_background

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
                                        style=ft.ButtonStyle(mouse_cursor=clickable_mouse_cursor),
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
        rebuild_page_callback(selected_theme["name"], active_page["name"])
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
        settings["enable_orbs"] = DEFAULT_SETTINGS["enable_orbs"]
        save_settings(settings)
        rebuild_page_callback(default_theme, active_page["name"])
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
                "Ambient Background Orbs",
                "Display subtle animated gradient orbs in the background of screens.",
                control=build_themed_switch(
                    label="Enable background orbs",
                    value=settings.get("enable_orbs", True),
                    theme=theme,
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
                    theme=theme,
                    on_change=lambda e: persist_setting(
                        "fullscreen", e.control.value
                    ),
                ),
            ),
            build_setting_container(
                "Custom Skins",
                "Load player skins in-game using the skin network Java agent.",
                control=build_themed_switch(
                    label="Enable in-game skins",
                    value=settings.get("enable_custom_skins", True),
                    theme=theme,
                    on_change=lambda e: persist_setting(
                        "enable_custom_skins", e.control.value
                    ),
                ),
            ),
        ],
        on_reset=lambda e: reset_settings_fields(
            ("window_width", "window_height", "fullscreen", "enable_custom_skins"), "Game"
        ),
    )

    def open_custom_dialog(dlg):
        if callable(show_dialog_with_blur):
            show_dialog_with_blur(dlg)
        else:
            page.show_dialog(dlg)
            page.update()

    def close_custom_dialog(dlg):
        if callable(close_dialog_with_blur):
            close_dialog_with_blur(dlg)
        else:
            dlg.open = False
            try:
                page.close(dlg)
            except Exception:
                pass
            page.update()

    def show_ely_login_dialog():
        login_field = ft.TextField(
            label="Ely.by Username or Email",
            hint_text="e.g. steve or steve@ely.by",
            width=340,
            autofocus=True,
            border_color=theme["border"],
            focused_border_color=theme["btn_primary"],
            color=theme["text_primary"],
            label_style=ft.TextStyle(color=theme["text_secondary"], size=13),
            fill_color=theme["card"],
            border_radius=8,
        )

        password_field = ft.TextField(
            label="Password",
            password=True,
            can_reveal_password=True,
            width=340,
            border_color=theme["border"],
            focused_border_color=theme["btn_primary"],
            color=theme["text_primary"],
            label_style=ft.TextStyle(color=theme["text_secondary"], size=13),
            fill_color=theme["card"],
            border_radius=8,
        )

        totp_field = ft.TextField(
            label="2FA Authenticator Code",
            hint_text="6-digit code from authenticator app",
            width=340,
            max_length=6,
            visible=False,
            border_color=theme["btn_primary"],
            focused_border_color=theme["btn_primary"],
            color=theme["text_primary"],
            label_style=ft.TextStyle(color=theme["btn_primary"], size=13),
            fill_color=theme["card"],
            border_radius=8,
        )

        error_text = ft.Text(
            "",
            size=12,
            color=ft.Colors.RED_400,
            weight=ft.FontWeight.W_500,
            visible=False,
        )

        btn_primary_text = theme.get("btn_primary_text", theme["text_primary"])
        progress_ring = ft.ProgressRing(width=16, height=16, stroke_width=2, color=btn_primary_text, visible=False)
        submit_btn_text = ft.Text("Log In", size=13, weight=ft.FontWeight.BOLD, color=btn_primary_text)

        def on_submit(e):
            user_val = (login_field.value or "").strip()
            pass_val = (password_field.value or "").strip()
            totp_val = (totp_field.value or "").strip() if totp_field.visible else None

            if not user_val or not pass_val:
                error_text.value = "Please enter both username/email and password."
                error_text.color = ft.Colors.RED_400
                error_text.visible = True
                dlg.update()
                return

            if totp_field.visible and not totp_val:
                error_text.value = "Please enter your 6-digit authenticator code."
                error_text.color = ft.Colors.RED_400
                error_text.visible = True
                dlg.update()
                return

            progress_ring.visible = True
            submit_btn_text.value = "Authenticating..."
            submit_btn.disabled = True
            error_text.visible = False
            dlg.update()

            def do_auth():
                try:
                    auth_data = ely_auth.authenticate(user_val, pass_val, totp_val)
                    new_acc = {
                        "id": f"ely_{auth_data['uuid']}",
                        "type": "ely",
                        "username": auth_data["username"],
                        "email": user_val if "@" in user_val else "",
                        "uuid": auth_data["uuid"],
                        "access_token": auth_data["access_token"],
                        "client_token": auth_data["client_token"],
                        "created_at": time.time(),
                    }
                    add_or_update_account(new_acc, set_as_active=True)
                    player_name_state["value"] = auth_data["username"]

                    # Pre-cache skin to disk immediately
                    threading.Thread(
                        target=skins.fetch_ely_skin,
                        args=(auth_data["username"],),
                        kwargs={"force_refresh": True},
                        daemon=True,
                    ).start()

                    close_custom_dialog(dlg)
                    if callable(update_sidebar_username):
                        update_sidebar_username()
                    if callable(refresh_account_picture):
                        refresh_account_picture()
                    if play_button_ref and play_button_ref.get("control") is not None and callable(refresh_launch_button):
                        refresh_launch_button()
                    rebuild_settings_page(f"Logged in as {auth_data['username']}! Skins unlocked.")
                    notify(f"Welcome, {auth_data['username']}! Ely.by account connected.", ft.Icons.CHECK_CIRCLE)
                except ely_auth.Ely2FARequiredError as exc:
                    totp_field.visible = True
                    error_text.value = str(exc)
                    error_text.color = ft.Colors.AMBER_400
                    error_text.visible = True
                    submit_btn_text.value = "Submit 2FA Code"
                    submit_btn.disabled = False
                    progress_ring.visible = False
                    dlg.update()
                except ely_auth.ElyInvalidCredentialsError as exc:
                    error_text.value = str(exc)
                    error_text.color = ft.Colors.RED_400
                    error_text.visible = True
                    submit_btn_text.value = "Log In"
                    submit_btn.disabled = False
                    progress_ring.visible = False
                    dlg.update()
                except Exception as exc:
                    error_text.value = str(exc)
                    error_text.color = ft.Colors.RED_400
                    error_text.visible = True
                    submit_btn_text.value = "Log In"
                    submit_btn.disabled = False
                    progress_ring.visible = False
                    dlg.update()

            threading.Thread(target=do_auth, daemon=True).start()

        submit_btn = ft.Button(
            content=ft.Row(
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
                controls=[progress_ring, submit_btn_text],
            ),
            bgcolor=theme["btn_primary"],
            color=btn_primary_text,
            style=rounded_button_style,
            on_click=on_submit,
            height=38,
        )

        cancel_btn = ft.Button(
            "Cancel",
            bgcolor=theme["surface"],
            color=theme["text_primary"],
            style=rounded_button_style,
            on_click=lambda e: close_custom_dialog(dlg),
            height=38,
        )

        register_link = ft.Button(
            "Don't have an Ely.by account? Register free",
            icon=ft.Icons.OPEN_IN_NEW,
            bgcolor=ft.Colors.TRANSPARENT,
            color=theme.get("btn_primary", theme["text_primary"]),
            style=rounded_button_style,
            on_click=lambda e: open_url("https://account.ely.by/register", "Opening Ely.by registration..."),
        )

        actions_row = ft.Row(
            alignment=ft.MainAxisAlignment.END,
            spacing=10,
            controls=[cancel_btn, submit_btn],
        )

        dialog_content = ft.Container(
            width=360,
            content=ft.Column(
                tight=True,
                spacing=14,
                controls=[
                    ft.Text(
                        "Log in with Ely.by to customize skins and play online.",
                        size=12,
                        color=theme["text_secondary"],
                    ),
                    login_field,
                    password_field,
                    totp_field,
                    error_text,
                    register_link,
                    ft.Container(height=4),
                    actions_row,
                ],
            ),
        )

        dlg = ft.AlertDialog(
            modal=False,
            bgcolor=theme["card"],
            shape=ft.RoundedRectangleBorder(radius=14),
            on_dismiss=lambda e: close_custom_dialog(dlg),
            title=ft.Row(
                spacing=10,
                controls=[
                    ft.Icon(ft.Icons.LOCK_PERSON_OUTLINED, color=theme["btn_primary"], size=24),
                    ft.Text("Ely.by Login", size=18, weight=ft.FontWeight.BOLD, color=theme["text_primary"]),
                ],
            ),
            content=dialog_content,
        )

        open_custom_dialog(dlg)

    active_account = get_active_account()
    is_active_ely = active_account.get("type") == "ely"
    accounts_data = load_accounts()
    saved_accounts = accounts_data.get("accounts", [])

    offline_account = next((a for a in saved_accounts if a.get("type") == "offline"), None)
    offline_name = (offline_account.get("username") if offline_account else player_name_state["value"]) or "Player"

    def open_edit_offline_name_dialog():
        edit_name_field = ft.TextField(
            label="Offline Player Username",
            value=offline_name,
            width=320,
            max_length=16,
            autofocus=True,
            hint_text="1-16 letters, numbers, or _",
            border_color=theme["border"],
            focused_border_color=theme["btn_primary"],
            color=theme["text_primary"],
            label_style=ft.TextStyle(color=theme["text_secondary"], size=13),
            cursor_color=theme["btn_primary"],
            fill_color=theme["card"],
            border_radius=8,
        )

        name_error_text = ft.Text(
            "",
            size=12,
            color=ft.Colors.RED_400,
            weight=ft.FontWeight.W_500,
            visible=False,
        )

        def on_save_name(e):
            val = (edit_name_field.value or "").strip()
            if not val:
                name_error_text.value = "Username cannot be empty."
                name_error_text.visible = True
                edit_dlg.update()
                return

            update_offline_username(val)
            active_acc = get_active_account()
            if active_acc.get("type") == "offline":
                player_name_state["value"] = val
                if callable(update_sidebar_username):
                    update_sidebar_username()
                if callable(refresh_account_picture):
                    refresh_account_picture()
                if play_button_ref and play_button_ref.get("control") is not None and callable(refresh_launch_button):
                    refresh_launch_button()

            close_custom_dialog(edit_dlg)
            rebuild_settings_page(f"Offline username changed to '{val}'.")
            notify(f"Updated offline username to '{val}'", ft.Icons.CHECK_CIRCLE)

        btn_primary_text = theme.get("btn_primary_text", theme["text_primary"])

        save_btn = ft.Button(
            "Save",
            bgcolor=theme["btn_primary"],
            color=btn_primary_text,
            style=rounded_button_style,
            on_click=on_save_name,
            height=38,
        )

        cancel_edit_btn = ft.Button(
            "Cancel",
            bgcolor=theme["surface"],
            color=theme["text_primary"],
            style=rounded_button_style,
            on_click=lambda e: close_custom_dialog(edit_dlg),
            height=38,
        )

        edit_actions_row = ft.Row(
            alignment=ft.MainAxisAlignment.END,
            spacing=10,
            controls=[cancel_edit_btn, save_btn],
        )

        edit_dialog_content = ft.Container(
            width=340,
            content=ft.Column(
                tight=True,
                spacing=14,
                controls=[
                    ft.Text(
                        "Change your username for playing in offline mode.",
                        size=12,
                        color=theme["text_secondary"],
                    ),
                    edit_name_field,
                    name_error_text,
                    ft.Container(height=4),
                    edit_actions_row,
                ],
            ),
        )

        edit_dlg = ft.AlertDialog(
            modal=False,
            bgcolor=theme["card"],
            shape=ft.RoundedRectangleBorder(radius=14),
            on_dismiss=lambda e: close_custom_dialog(edit_dlg),
            title=ft.Row(
                spacing=10,
                controls=[
                    ft.Icon(ft.Icons.EDIT_OUTLINED, color=theme["btn_primary"], size=22),
                    ft.Text("Edit Offline Username", size=18, weight=ft.FontWeight.BOLD, color=theme["text_primary"]),
                ],
            ),
            content=edit_dialog_content,
        )

        open_custom_dialog(edit_dlg)

    # Build account cards for each saved account
    accounts_rows = []
    for acc in saved_accounts:
        acc_id = acc.get("id")
        acc_type = acc.get("type", "offline")
        acc_name = acc.get("username", "Player")
        is_current = (acc_id == active_account.get("id"))

        type_badge = ft.Container(
            content=ft.Text(
                "ELY.BY" if acc_type == "ely" else "OFFLINE",
                size=10,
                weight=ft.FontWeight.BOLD,
                color=ft.Colors.WHITE if acc_type == "ely" else theme["text_secondary"],
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
            border_radius=6,
            bgcolor=ft.Colors.GREEN_700 if acc_type == "ely" else ft.Colors.with_opacity(0.12, theme["text_secondary"]),
        )

        status_text = (
            "Skins & Ely.by multiplayer active"
            if acc_type == "ely"
            else "Skins disabled — log in to customize skins"
        )

        def make_switch_handler(target_id, target_name):
            def handler(e):
                set_active_account(target_id)
                player_name_state["value"] = target_name
                if callable(update_sidebar_username):
                    update_sidebar_username()
                if callable(refresh_account_picture):
                    refresh_account_picture()
                if play_button_ref and play_button_ref.get("control") is not None and callable(refresh_launch_button):
                    refresh_launch_button()
                rebuild_settings_page(f"Switched active account to {target_name}.")
            return handler

        def make_logout_handler(target_id, target_token, target_client, target_name):
            def handler(e):
                def do_logout():
                    if target_token and target_client:
                        try:
                            ely_auth.invalidate_token(target_token, target_client)
                        except Exception:
                            pass
                    remove_account(target_id)
                    new_active = get_active_account()
                    player_name_state["value"] = new_active.get("username", "Player")
                    if callable(update_sidebar_username):
                        update_sidebar_username()
                    if callable(refresh_account_picture):
                        refresh_account_picture()
                    if play_button_ref and play_button_ref.get("control") is not None and callable(refresh_launch_button):
                        refresh_launch_button()
                    rebuild_settings_page(f"Logged out of {target_name}.")
                threading.Thread(target=do_logout, daemon=True).start()
            return handler

        def make_sync_handler(target_name):
            def handler(e):
                def do_sync():
                    try:
                        skins.fetch_ely_skin(target_name, force_refresh=True, timeout=3.0)
                        if callable(refresh_account_picture):
                            refresh_account_picture()
                        notify(f"Synced latest skin from Ely.by for {target_name}!", ft.Icons.CHECK_CIRCLE)
                    except Exception as err:
                        notify(f"Could not sync skin: {err}", ft.Icons.ERROR_OUTLINE)
                threading.Thread(target=do_sync, daemon=True).start()
            return handler

        action_controls = []
        if is_current:
            action_controls.append(
                ft.Row(
                    spacing=6,
                    controls=[
                        ft.Icon(ft.Icons.CHECK_CIRCLE, color=theme["btn_primary"], size=16),
                        ft.Text("Active", size=12, weight=ft.FontWeight.BOLD, color=theme["btn_primary"]),
                    ],
                )
            )
        else:
            action_controls.append(
                ft.Button(
                    "Switch",
                    bgcolor=theme["surface"],
                    color=theme["text_primary"],
                    style=rounded_button_style,
                    on_click=make_switch_handler(acc_id, acc_name),
                )
            )

        if acc_type == "ely":
            action_controls.append(
                ft.IconButton(
                    icon=ft.Icons.REFRESH,
                    tooltip=tooltip("Sync skin from Ely.by"),
                    icon_color=theme["btn_primary"],
                    on_click=make_sync_handler(acc_name),
                )
            )
            action_controls.append(
                ft.IconButton(
                    icon=ft.Icons.LOGOUT,
                    tooltip=tooltip("Log out of this Ely.by account"),
                    icon_color=ft.Colors.RED_400,
                    on_click=make_logout_handler(acc_id, acc.get("access_token"), acc.get("client_token"), acc_name),
                )
            )

        accounts_rows.append(
            ft.Container(
                padding=ft.Padding.all(12),
                border_radius=10,
                bgcolor=theme["card"] if is_current else theme["surface"],
                border=ft.Border.all(
                    1,
                    color=theme["btn_primary"] if is_current else theme["border"],
                ),
                content=ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Row(
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Icon(
                                    ft.Icons.VERIFIED_USER if acc_type == "ely" else ft.Icons.PERSON_OUTLINE,
                                    size=22,
                                    color=theme["btn_primary"] if acc_type == "ely" else theme["text_secondary"],
                                ),
                                ft.Column(
                                    spacing=2,
                                    controls=[
                                        ft.Row(
                                            spacing=8,
                                            controls=[
                                                ft.Text(acc_name, size=14, weight=ft.FontWeight.BOLD, color=theme["text_primary"]),
                                                type_badge,
                                            ],
                                        ),
                                        ft.Text(status_text, size=11, color=theme["text_secondary"]),
                                    ],
                                ),
                            ],
                        ),
                        ft.Row(spacing=6, controls=action_controls),
                    ],
                ),
            )
        )

    accounts_subpage = build_settings_page(
        "Accounts",
        [
            build_setting_container(
                "Active Account Overview",
                "Your currently selected account used when starting Minecraft.",
                control=ft.Row(
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        account_picture_preview or ft.Container(),
                        ft.Column(
                            spacing=4,
                            controls=[
                                ft.Row(
                                    spacing=8,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.Text(
                                            active_account.get("username", "Player"),
                                            size=16,
                                            weight=ft.FontWeight.BOLD,
                                            color=theme["text_primary"],
                                        ),
                                        *(
                                            [
                                                ft.IconButton(
                                                    icon=ft.Icons.EDIT_OUTLINED,
                                                    icon_size=16,
                                                    icon_color=theme["btn_primary"],
                                                    tooltip=tooltip("Edit offline username"),
                                                    style=ft.ButtonStyle(
                                                        padding=ft.Padding.all(4),
                                                    ),
                                                    on_click=lambda e: open_edit_offline_name_dialog(),
                                                )
                                            ]
                                            if not is_active_ely
                                            else []
                                        ),
                                        ft.Container(
                                            content=ft.Text(
                                                "ELY.BY ACTIVE" if is_active_ely else "OFFLINE ACTIVE",
                                                size=10,
                                                weight=ft.FontWeight.BOLD,
                                                color=ft.Colors.WHITE if is_active_ely else theme["text_secondary"],
                                            ),
                                            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                                            border_radius=6,
                                            bgcolor=ft.Colors.GREEN_700 if is_active_ely else ft.Colors.with_opacity(0.12, theme["text_secondary"]),
                                        ),
                                    ],
                                ),
                                ft.Text(
                                    "Skins and Ely.by multiplayer are active."
                                    if is_active_ely
                                    else "Skins disabled — log in to customize skins.",
                                    size=12,
                                    color=theme["btn_primary"] if is_active_ely else theme["text_secondary"],
                                ),
                            ],
                        ),
                    ],
                ),
            ),
            build_setting_container(
                "Manage Accounts",
                "Switch between saved accounts or log out.",
                control=ft.Column(
                    spacing=10,
                    controls=accounts_rows,
                ),
            ),
            build_setting_container(
                "Add Ely.by Account",
                "Log in with an Ely.by account to use and customize skins.",
                control=ft.Row(
                    spacing=12,
                    controls=[
                        ft.Button(
                            "Log in with Ely.by",
                            icon=ft.Icons.LOGIN,
                            bgcolor=theme["btn_primary"],
                            color=theme.get("btn_primary_text", theme["text_primary"]),
                            style=rounded_button_style,
                            on_click=lambda e: show_ely_login_dialog(),
                        ),
                        ft.Button(
                            "Register on Ely.by",
                            icon=ft.Icons.OPEN_IN_NEW,
                            bgcolor=theme["surface"],
                            color=theme["text_secondary"],
                            style=rounded_button_style,
                            on_click=lambda e: open_url("https://account.ely.by/register", "Opening Ely.by registration..."),
                        ),
                    ],
                ),
            ),
        ],
        on_reset=lambda e: rebuild_settings_page("Accounts reloaded."),
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
                    theme=theme,
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
                                mini_badge("v1.2.0", color=theme["btn_primary"]),
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
    discord_id_pill_card = ft.Container(
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
    discord_id_pill_card.on_hover = lambda e, c=discord_id_pill_card: on_card_hover(e, c)
    discord_id_pill = ft.GestureDetector(
        mouse_cursor=ft.MouseCursor.CLICK,
        content=discord_id_pill_card,
    )

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
                                        on_click=lambda e: open_system_folder(
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

    return {
        "options": options_dashboard,
        "options_ui": ui_subpage,
        "options_java": java_subpage,
        "options_game": game_subpage,
        "options_accounts": accounts_subpage,
        "options_launcher": launcher_subpage,
        "options_about": about_subpage,
    }
