"""
Skins Management Tab and 3D Model Bridge UI for Gr8 Launcher.
"""

import asyncio
import io
import math
import os
import webbrowser
from PIL import Image
import flet as ft
import skins
from storage import get_active_account
from state import player_name_state, active_page, launcher_log_state
from ui_components import (
    clickable_mouse_cursor,
    rounded_button_style,
    tooltip,
    create_ambient_gradient_orbs,
    animate_ambient_gradient_orbs,
)

def build_skins_view(page: ft.Page, theme: dict, notify, open_url=None, online_available=True, navigate=None):
    if open_url is None:
        def open_url(url, msg=""):
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
            if msg and notify:
                notify(msg, ft.Icons.OPEN_IN_NEW)

    active_skin_data = skins.load_active_skin_choice()
    skin_model_state = {"value": active_skin_data.get("model", "classic")}
    skin_view_state = {"value": "3d"}

    status_dot = ft.Container(
        width=8,
        height=8,
        border_radius=4,
        bgcolor=theme["text_secondary"],
    )
    status_text = ft.Text(
        "Default Skin",
        size=12,
        color=theme["text_secondary"],
    )
    player_label = ft.Text(
        player_name_state["value"].strip() or "Offline Player",
        size=18,
        weight=ft.FontWeight.BOLD,
        color=theme["text_primary"],
    )

    cached_skin_im = [None]
    cached_skin_key = [""]

    is_init_classic = skin_model_state["value"] == "classic"
    model_badge_icon = ft.Icon(
        ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED,
        size=15,
        color=theme["btn_primary"],
    )
    model_badge_text = ft.Text(
        "Classic Model (Steve • 4px arms)" if is_init_classic else "Slim Model (Alex • 3px arms)",
        size=12,
        weight=ft.FontWeight.W_500,
        color=theme["text_primary"],
    )
    model_badge = ft.Container(
        content=ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[model_badge_icon, model_badge_text],
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        bgcolor=ft.Colors.with_opacity(0.08, theme["btn_primary"]),
        border=ft.Border.all(1, color=ft.Colors.with_opacity(0.2, theme["btn_primary"])),
        border_radius=8,
    )

    def get_effective_skin_image() -> Image.Image:
        uname = player_name_state["value"].strip()
        model = skin_model_state["value"]
        key = f"{uname}_{model}"
        if cached_skin_key[0] == key and cached_skin_im[0] is not None:
            return cached_skin_im[0]

        raw = None
        if uname:
            cache_file = skins.get_skins_dir() / f"ely_{uname.lower()}.png"
            if cache_file.is_file():
                try:
                    raw = cache_file.read_bytes()
                except OSError:
                    pass

        im = None
        if raw:
            try:
                im = Image.open(io.BytesIO(raw))
            except Exception:
                pass
        if im is None:
            im = skins.get_default_skin(model)

        prepared = skins.ensure_64x64(im)
        cached_skin_im[0] = prepared
        cached_skin_key[0] = key

        # Automatically sync model detection from texture pixels
        if raw and im is not None:
            is_slim = skins.is_slim_skin(prepared)
            detected_model = "slim" if is_slim else "classic"
            if skin_model_state["value"] != detected_model:
                skin_model_state["value"] = detected_model
                model_badge_text.value = (
                    "Classic Model (Steve • 4px arms)"
                    if detected_model == "classic"
                    else "Slim Model (Alex • 3px arms)"
                )
                try:
                    model_badge_text.update()
                except Exception:
                    pass

        return prepared

    def invalidate_skin_cache():
        cached_skin_im[0] = None
        cached_skin_key[0] = ""

    current_rot = {"yaw": -25.0, "pitch": 10.0}
    velocity = {"yaw": 0.0, "pitch": 0.0}
    anim_id = [0]
    is_dragging = [False]

    def render_current_character() -> bytes:
        skin_im = get_effective_skin_image()
        return skins.render_3d_skin_bytes(
            skin_im,
            yaw_deg=current_rot["yaw"],
            pitch_deg=current_rot["pitch"],
            model=skin_model_state["value"],
            pose="hero",
            width=240,
            height=340,
            scale=8.5,
        )

    skin_image = ft.Image(
        src=render_current_character(),
        fit=ft.BoxFit.CONTAIN,
        width=240,
        height=340,
        filter_quality=ft.FilterQuality.NONE,
        gapless_playback=True,
    )

    def update_preview():
        skin_image.src = render_current_character()
        try:
            skin_image.update()
        except Exception:
            pass

    active_pill_style = ft.ButtonStyle(
        bgcolor={
            ft.ControlState.DEFAULT: theme["btn_primary"],
            ft.ControlState.DISABLED: theme["surface"],
        },
        color={
            ft.ControlState.DEFAULT: theme.get("btn_primary_text", "#FFFFFF"),
            ft.ControlState.DISABLED: theme.get("text_muted", theme["text_secondary"]),
        },
        side={
            ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT),
            ft.ControlState.DISABLED: ft.BorderSide(1, color=theme["border"]),
        },
        shape=ft.RoundedRectangleBorder(radius=8),
        mouse_cursor=clickable_mouse_cursor,
        padding=ft.Padding.symmetric(horizontal=14, vertical=8),
    )
    inactive_pill_style = ft.ButtonStyle(
        bgcolor={
            ft.ControlState.HOVERED: theme.get("card_hover", theme["card"]),
            ft.ControlState.DEFAULT: theme["card"],
            ft.ControlState.DISABLED: theme["surface"],
        },
        color={
            ft.ControlState.DEFAULT: theme["text_secondary"],
            ft.ControlState.DISABLED: theme.get("text_muted", theme["text_secondary"]),
        },
        side={
            ft.ControlState.HOVERED: ft.BorderSide(1, color=theme["btn_primary"]),
            ft.ControlState.DEFAULT: ft.BorderSide(1, color=theme["border"]),
            ft.ControlState.DISABLED: ft.BorderSide(1, color=theme["border"]),
        },
        shape=ft.RoundedRectangleBorder(radius=8),
        mouse_cursor=clickable_mouse_cursor,
        padding=ft.Padding.symmetric(horizontal=14, vertical=8),
    )

    def highlight_active_view(view_name):
        view_front_btn.style = active_pill_style if view_name == "front" else inactive_pill_style
        view_back_btn.style = active_pill_style if view_name == "back" else inactive_pill_style
        try:
            view_front_btn.update()
            view_back_btn.update()
        except Exception:
            pass

    async def coast_task(gen):
        while active_page.get("name") == "skins" and anim_id[0] == gen:
            if abs(velocity["yaw"]) < 0.03 and abs(velocity["pitch"]) < 0.03:
                break
            current_rot["yaw"] = (current_rot["yaw"] + velocity["yaw"]) % 360.0
            current_rot["pitch"] = max(-35.0, min(35.0, current_rot["pitch"] + velocity["pitch"]))
            velocity["yaw"] *= 0.90
            velocity["pitch"] *= 0.90
            skin_image.src = render_current_character()
            try:
                skin_image.update()
            except Exception:
                break
            await asyncio.sleep(0.001)

    async def animate_to(goal_yaw, goal_pitch, gen):
        start_yaw = current_rot["yaw"]
        start_pitch = current_rot["pitch"]
        diff_yaw = (goal_yaw - start_yaw + 180.0) % 360.0 - 180.0
        diff_pitch = goal_pitch - start_pitch
        steps = 18
        for step in range(1, steps + 1):
            if anim_id[0] != gen or active_page.get("name") != "skins":
                break
            t = step / steps
            ease = 1.0 - math.pow(1.0 - t, 3)
            current_rot["yaw"] = (start_yaw + diff_yaw * ease) % 360.0
            current_rot["pitch"] = start_pitch + diff_pitch * ease
            skin_image.src = render_current_character()
            try:
                skin_image.update()
            except Exception:
                break
            await asyncio.sleep(0.001)

    def handle_pan_start(e):
        is_dragging[0] = True
        anim_id[0] += 1
        velocity["yaw"] = 0.0
        velocity["pitch"] = 0.0

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
            dy = getattr(e.delta_y, 0.0) or 0.0

        current_rot["yaw"] = (current_rot["yaw"] + dx * 0.70) % 360.0
        current_rot["pitch"] = max(-35.0, min(35.0, current_rot["pitch"] + dy * 0.45))
        velocity["yaw"] = velocity["yaw"] * 0.35 + dx * 0.55
        velocity["pitch"] = velocity["pitch"] * 0.35 + dy * 0.35

        if skin_view_state["value"] != "3d":
            skin_view_state["value"] = "3d"
            highlight_active_view("3d")

        skin_image.src = render_current_character()
        try:
            skin_image.update()
        except Exception:
            pass

    def handle_pan_end(e):
        is_dragging[0] = False
        if abs(velocity["yaw"]) > 0.08 or abs(velocity["pitch"]) > 0.08:
            anim_id[0] += 1
            page.run_task(coast_task, anim_id[0])

    def handle_double_tap(e):
        anim_id[0] += 1
        velocity["yaw"] = 0.0
        velocity["pitch"] = 0.0
        skin_view_state["value"] = "3d"
        highlight_active_view("3d")
        page.run_task(animate_to, -25.0, 10.0, anim_id[0])

    skin_gesture = ft.GestureDetector(
        content=skin_image,
        mouse_cursor=ft.MouseCursor.GRAB,
        drag_interval=16,
        on_pan_start=handle_pan_start,
        on_pan_update=handle_pan_update,
        on_pan_end=handle_pan_end,
        on_double_tap=handle_double_tap,
    )

    def set_view(view_name):
        skin_view_state["value"] = view_name
        highlight_active_view(view_name)
        velocity["yaw"] = 0.0
        velocity["pitch"] = 0.0
        anim_id[0] += 1
        if view_name == "3d":
            page.run_task(animate_to, -25.0, 10.0, anim_id[0])
        elif view_name == "front":
            page.run_task(animate_to, 0.0, 0.0, anim_id[0])
        elif view_name == "back":
            page.run_task(animate_to, 180.0, 0.0, anim_id[0])

    view_front_btn = ft.Button(
        "Front",
        style=inactive_pill_style,
        on_click=lambda e: set_view("front"),
    )
    view_back_btn = ft.Button(
        "Back",
        style=inactive_pill_style,
        on_click=lambda e: set_view("back"),
    )

    def set_model(model_name):
        skin_model_state["value"] = model_name
        is_classic = (model_name == "classic")
        model_badge_text.value = (
            "Classic Model (Steve • 4px arms)"
            if is_classic
            else "Slim Model (Alex • 3px arms)"
        )
        try:
            model_badge_text.update()
        except Exception:
            pass
        skins.save_active_skin_choice("ely", model=model_name)
        invalidate_skin_cache()
        update_preview()
        ref_acc = launcher_log_state.get("refresh_account_picture")
        if callable(ref_acc):
            ref_acc()

    async def sync_skin_network(force=False):
        uname = player_name_state["value"].strip()
        if not uname:
            status_dot.bgcolor = theme["text_secondary"]
            status_text.value = "Default Skin"
            invalidate_skin_cache()
            update_preview()
            page.update()
            return

        if not online_available and force:
            notify("Cannot check for updated skin while offline.", ft.Icons.WIFI_OFF)
            return

        if not online_available:
            invalidate_skin_cache()
            raw_check = get_effective_skin_image()
            if raw_check != skins.get_default_skin(skin_model_state["value"]):
                status_dot.bgcolor = ft.Colors.GREEN_400
                status_text.value = "Skin Active (Offline)"
            else:
                status_dot.bgcolor = theme["text_secondary"]
                status_text.value = "Default Skin"
            update_preview()
            try:
                status_dot.update()
                status_text.update()
            except Exception:
                pass
            return

        status_text.value = "Checking skin..."
        status_dot.bgcolor = ft.Colors.AMBER_400
        try:
            status_text.update()
            status_dot.update()
        except Exception:
            pass

        skin_bytes, detected_model = await asyncio.to_thread(
            skins.fetch_ely_skin, uname, force_refresh=force, timeout=6.0
        )
        invalidate_skin_cache()
        if skin_bytes:
            status_dot.bgcolor = ft.Colors.GREEN_400
            status_text.value = "Skin Active"
            set_model(detected_model)
        else:
            status_dot.bgcolor = theme["text_secondary"]
            status_text.value = "Default Skin"
            update_preview()
        try:
            status_dot.update()
            status_text.update()
        except Exception:
            pass
        ref_acc = launcher_log_state.get("refresh_account_picture")
        if callable(ref_acc):
            ref_acc()

    page.run_task(sync_skin_network, False)

    def handle_refresh(e):
        if not online_available:
            notify("Cannot refresh skin while offline.", ft.Icons.WIFI_OFF)
            return
        notify("Checking for updated skin...", ft.Icons.REFRESH)
        page.run_task(sync_skin_network, True)

    refresh_skin_btn = ft.Button(
        "Refresh",
        icon=ft.Icons.REFRESH,
        style=inactive_pill_style,
        disabled=not online_available,
        tooltip=tooltip("Internet connection required to refresh") if not online_available else None,
        on_click=handle_refresh,
    )
    manage_skin_btn = ft.Button(
        "Manage Skin Online",
        icon=ft.Icons.OPEN_IN_NEW,
        style=active_pill_style,
        disabled=not online_available,
        tooltip=tooltip("Internet connection required") if not online_available else None,
        on_click=lambda e: open_url("https://ely.by/skins", "Opening skin manager..."),
    )
    launcher_log_state["skin_refresh_button"] = refresh_skin_btn
    launcher_log_state["skin_manage_button"] = manage_skin_btn
    launcher_log_state["skin_sync_task"] = sync_skin_network

    drag_hint = ft.Row(
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=6,
        controls=[
            ft.Icon(ft.Icons.ROTATE_RIGHT, size=13, color=theme["text_secondary"]),
            ft.Text("Drag to rotate 360° • Double-click to reset", size=11, color=theme["text_secondary"]),
        ],
    )

    left_preview_card = ft.Container(
        width=330,
        padding=22,
        bgcolor=theme["surface"],
        border=ft.Border.all(1, color=theme["border"]),
        border_radius=16,
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=14,
            controls=[
                ft.Container(
                    content=skin_gesture,
                    alignment=ft.Alignment.CENTER,
                    padding=ft.Padding.only(top=5, bottom=5),
                ),
                ft.Row(
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=8,
                    controls=[
                        view_front_btn,
                        view_back_btn,
                    ],
                ),
                drag_hint,
            ],
        ),
    )

    right_settings_card = ft.Container(
        width=460,
        padding=28,
        bgcolor=theme["surface"],
        border=ft.Border.all(1, color=theme["border"]),
        border_radius=16,
        content=ft.Column(
            spacing=22,
            controls=[
                ft.Column(
                    spacing=6,
                    controls=[
                        ft.Text("PLAYER", size=11, weight=ft.FontWeight.BOLD, color=theme["text_secondary"]),
                        player_label,
                        ft.Row(
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[status_dot, status_text],
                        ),
                        ft.Container(height=6),
                        ft.Row(
                            spacing=10,
                            controls=[
                                refresh_skin_btn,
                                manage_skin_btn,
                            ],
                        ),
                    ],
                ),
                ft.Divider(color=ft.Colors.with_opacity(0.12, theme["border"])),
                ft.Column(
                    spacing=8,
                    controls=[
                        ft.Text("CHARACTER MODEL", size=11, weight=ft.FontWeight.BOLD, color=theme["text_secondary"]),
                        model_badge,
                    ],
                ),
            ],
        ),
    )

    footer_credit = ft.Container(
        content=ft.Text(
            "Skin network powered by Ely.by",
            size=11,
            color=ft.Colors.with_opacity(0.5, theme["text_secondary"]),
            text_align=ft.TextAlign.CENTER,
        ),
        alignment=ft.Alignment.CENTER,
        padding=ft.Padding.only(top=14),
    )

    skins_orb1, skins_orb2 = create_ambient_gradient_orbs(theme)
    page.run_task(
        animate_ambient_gradient_orbs,
        skins_orb1,
        skins_orb2,
        lambda: active_page.get("name") == "skins",
    )

    active_acc = get_active_account()
    is_offline_acc = active_acc.get("type") == "offline"

    offline_locked_card = ft.Container(
        width=520,
        padding=ft.Padding.all(36),
        border_radius=18,
        bgcolor=theme["card"],
        border=ft.Border.all(1, color=theme["border"]),
        content=ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=16,
            controls=[
                ft.Container(
                    width=64,
                    height=64,
                    border_radius=32,
                    bgcolor=ft.Colors.with_opacity(0.12, theme["btn_primary"]),
                    alignment=ft.Alignment.CENTER,
                    content=ft.Icon(ft.Icons.LOCK_OUTLINED, size=32, color=theme["btn_primary"]),
                ),
                ft.Text(
                    "Skins Disabled",
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=theme["text_primary"],
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Text(
                    "Skins are disabled in offline mode. Log in to customize skins.",
                    size=13,
                    color=theme["text_secondary"],
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=10),
                ft.Row(
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=12,
                    controls=[
                        ft.Button(
                            "Log In with Ely.by",
                            icon=ft.Icons.LOGIN,
                            bgcolor=theme["btn_primary"],
                            color=theme.get("btn_primary_text", theme["text_primary"]),
                            style=rounded_button_style,
                            on_click=lambda e: navigate("options_accounts") if callable(navigate) else None,
                        ),
                        ft.Button(
                            "Create Free Account",
                            icon=ft.Icons.OPEN_IN_NEW,
                            bgcolor=theme["surface"],
                            color=theme["text_secondary"],
                            style=rounded_button_style,
                            on_click=lambda e: open_url("https://account.ely.by/register", "Opening Ely.by registration..."),
                        ),
                    ],
                ),
            ],
        ),
    )

    main_skins_content = (
        offline_locked_card
        if is_offline_acc
        else ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.START,
            spacing=24,
            controls=[
                left_preview_card,
                right_settings_card,
            ],
        )
    )

    return ft.Stack(
        expand=True,
        alignment=ft.Alignment.CENTER,
        controls=[
            skins_orb1,
            skins_orb2,
            ft.Container(
                alignment=ft.Alignment.CENTER,
                padding=ft.Padding.symmetric(horizontal=20, vertical=16),
                content=ft.Column(
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    scroll=ft.ScrollMode.AUTO,
                    controls=[
                        main_skins_content,
                        footer_credit,
                    ],
                ),
            ),
        ],
    )
