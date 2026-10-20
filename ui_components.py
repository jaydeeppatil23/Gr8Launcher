"""
Reusable UI components and widget helpers for Gr8 Launcher.
"""

import asyncio
import random
import flet as ft

clickable_mouse_cursor = {
    ft.ControlState.DISABLED: ft.MouseCursor.BASIC,
    ft.ControlState.DEFAULT: ft.MouseCursor.CLICK,
}

window_button_style = ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))
rounded_button_style = ft.ButtonStyle(
    shape=ft.RoundedRectangleBorder(radius=8),
    mouse_cursor=clickable_mouse_cursor,
)


def tooltip(msg: str, theme: dict = None) -> ft.Tooltip:
    if theme is None:
        try:
            import colors
            from state import selected_theme
            theme = colors.THEMES.get(
                selected_theme.get("name"),
                colors.THEMES.get(colors.DEFAULT_THEME),
            )
        except Exception:
            theme = None

    card_bg = theme.get("card", "#1F1B17") if isinstance(theme, dict) else "#1F1B17"
    text_color = theme.get("text_primary", ft.Colors.WHITE) if isinstance(theme, dict) else ft.Colors.WHITE

    return ft.Tooltip(
        message=msg,
        wait_duration=350,
        bgcolor=card_bg,
        text_style=ft.TextStyle(size=12, color=text_color),
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
    )


def build_themed_switch(label: str, value: bool, theme: dict = None, on_change=None) -> ft.Switch:
    if theme is None:
        import colors
        from state import selected_theme
        theme = colors.THEMES.get(selected_theme.get("name", "Dark"), colors.DEFAULT_THEME)
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


def build_themed_alert_dialog(
    title: str,
    content: ft.Control,
    actions: list[ft.Control],
    theme: dict = None,
    on_dismiss=None,
    dialog_blur_overlay=None,
    modal: bool = False,
) -> ft.AlertDialog:
    if theme is None:
        import colors
        from state import selected_theme
        theme = colors.THEMES.get(selected_theme.get("name", "Dark"), colors.DEFAULT_THEME)
    def handle_dismiss(e):
        if dialog_blur_overlay:
            dialog_blur_overlay.visible = False
            try:
                dialog_blur_overlay.update()
            except Exception:
                pass
        if on_dismiss:
            on_dismiss(e)

    return ft.AlertDialog(
        modal=modal,
        bgcolor=theme["surface"],
        shape=ft.RoundedRectangleBorder(radius=12),
        title=ft.Text(
            title,
            size=18,
            weight=ft.FontWeight.BOLD,
            color=theme["text_primary"],
        ) if isinstance(title, str) else title,
        content=content,
        actions=actions,
        on_dismiss=handle_dismiss,
    )


TAB_BTN_WIDTH = 58
TAB_BTN_HEIGHT = 38
TAB_GAP = 4
TAB_PITCH = TAB_BTN_WIDTH + TAB_GAP


class NavigationTabs(ft.Container):
    def __init__(self, tab_names, initial_index, on_tab_select, app_theme, tooltip_func=tooltip):
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
            ("skins", ft.Icons.CHECKROOM, "Skins"),
            ("options", ft.Icons.TUNE, "Options"),
        ]

        self.buttons = []
        for i, (t_name, t_icon, t_tip) in enumerate(tab_defs):
            is_active = (i == initial_index)
            btn_width = (
                TAB_BTN_WIDTH + TAB_GAP // 2
                if i == 0 or i == len(tab_defs) - 1
                else TAB_BTN_WIDTH + TAB_GAP
            )
            btn = ft.IconButton(
                icon=t_icon,
                icon_size=23,
                width=btn_width,
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
                    mouse_cursor=clickable_mouse_cursor,
                ),
                on_click=lambda e, name=t_name: on_tab_select(name),
            )
            self.buttons.append(btn)

        inner_stack = ft.Stack(
            controls=[
                self.tab_highlight,
                ft.Row(
                    controls=self.buttons,
                    spacing=0,
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


def create_ambient_gradient_orbs(theme: dict = None, enabled: bool = True):
    if theme is None:
        import colors
        from state import selected_theme
        theme = colors.THEMES.get(selected_theme.get("name", "Dark"), colors.DEFAULT_THEME)
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
    return first_orb, second_orb


async def _animate_single_orb(orb, is_active, coord_mode, min_x, max_x, min_y, max_y, is_enabled_fn=None):
    await asyncio.sleep(random.uniform(0.1, 0.4))
    while is_active() and (is_enabled_fn() if is_enabled_fn else True):
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


async def animate_ambient_gradient_orbs(first_orb, second_orb, is_active, is_enabled_fn=None):
    if is_enabled_fn and not is_enabled_fn():
        return
    await asyncio.sleep(0.1)
    t1 = asyncio.create_task(
        _animate_single_orb(first_orb, is_active, "top_left", -120, 260, -100, 190, is_enabled_fn)
    )
    t2 = asyncio.create_task(
        _animate_single_orb(second_orb, is_active, "bottom_right", -120, 260, -100, 190, is_enabled_fn)
    )
    try:
        await asyncio.gather(t1, t2)
    except Exception:
        pass
