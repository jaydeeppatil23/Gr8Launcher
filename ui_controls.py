"""Shared launcher inputs, buttons, and anchored acrylic selectors."""

from __future__ import annotations

from typing import Callable
import flet as ft
import ui_theme as theme


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    return f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"


def format_downloads(count: int) -> str:
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if count >= threshold:
            return f"{count / threshold:.1f}".removesuffix(".0") + suffix
    return str(count)


_size = format_size
_downloads = format_downloads


class SimpleInput(ft.TextField):
    def __init__(self, hint_text="Enter text", **kwargs):
        defaults = {
            "hint_text": hint_text, "bgcolor": theme.SURFACE,
            "border": ft.NoInputBorder(), "hover_color": theme.TRANSPARENT,
            "color": theme.TEXT, "hint_style": ft.TextStyle(color=theme.TEXT_HINT),
            "cursor_color": theme.ACCENT,
        }
        if kwargs.get("password") and kwargs.get("can_reveal_password"):
            # Balance the caret beside the native reveal icon without resizing.
            defaults["text_vertical_align"] = -0.5
        defaults.update(kwargs)
        super().__init__(**defaults)


class SimpleButton(ft.Button):
    def __init__(self, content=None, on_click=None, style=None, **kwargs):
        super().__init__(
            content=content, on_click=on_click,
            style=style or ft.ButtonStyle(
                bgcolor=theme.TRANSPARENT, overlay_color=theme.TRANSPARENT,
                shadow_color=theme.TRANSPARENT, mouse_cursor=ft.MouseCursor.CLICK,
                side=ft.BorderSide(0, theme.TRANSPARENT), padding=0,
                shape=ft.RoundedRectangleBorder(radius=0),
            ), **kwargs,
        )


def launcher_button(label: str, on_click: Callable | None, *, primary: bool = False,
                    height: int = 36, **kwargs) -> SimpleButton:
    return SimpleButton(
        ft.Text(label, font_family="ps", size=15, color=theme.ON_ACCENT if primary else theme.ACCENT),
        on_click=on_click, height=height,
        style=ft.ButtonStyle(
            bgcolor=theme.ACCENT if primary else theme.TRANSPARENT,
            color=theme.ON_ACCENT if primary else theme.ACCENT,
            overlay_color=theme.TRANSPARENT, shadow_color=theme.TRANSPARENT,
            mouse_cursor=ft.MouseCursor.CLICK,
            side=ft.BorderSide(0, theme.TRANSPARENT),
            shape=ft.RoundedRectangleBorder(radius=5),
            padding=ft.Padding.symmetric(horizontal=14),
        ), **kwargs,
    )


def launcher_pill(label: str, on_click: Callable | None, *, icon=None, primary: bool = False,
                  tooltip: str | None = None, **kwargs) -> ft.Container:
    """Standard frosted pill button matching Gr8 Launcher cards and toolbars."""
    fg = theme.ON_ACCENT if primary else theme.ACCENT
    bg = theme.ACCENT if primary else theme.SURFACE
    controls: list[ft.Control] = []
    if icon is not None:
        controls.append(ft.Icon(icon, size=14, color=fg))
    controls.append(ft.Text(label, font_family="ps", color=fg, size=13, weight=ft.FontWeight.BOLD if primary else ft.FontWeight.NORMAL))
    return ft.Container(
        bgcolor=bg,
        border_radius=5,
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        ink=True,
        tooltip=tooltip,
        content=ft.Row(
            controls,
            spacing=6,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        on_click=on_click,
        **kwargs,
    )


def rounded_artwork(content: ft.Control, *, size: int) -> ft.Container:
    """Clip artwork to the launcher's 5 px corners on every renderer."""
    return ft.Container(
        content=content, width=size, height=size, border_radius=5,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS, alignment=ft.Alignment.CENTER,
    )


def launcher_asset_icon(src: str, *, color: str | None = None, **kwargs) -> ft.Image:
    """Tint a bundled UI icon's silhouette while retaining its alpha channel."""
    return ft.Image(
        src=src, color=theme.ACCENT if color is None else color,
        color_blend_mode=ft.BlendMode.SRC_IN, **kwargs,
    )


def launcher_back_button(on_click: Callable, *, label: str = "Back", tooltip: str | None = None,
                         **kwargs) -> SimpleButton:
    """Use the compact chevron and label from the Add Account page."""
    return SimpleButton(
        ft.Row([
            ft.Icon(ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED, color=theme.ACCENT, size=14),
            ft.Text(label, font_family="ps", color=theme.ACCENT, size=15),
        ], spacing=6, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        on_click=on_click, tooltip=tooltip,
        style=ft.ButtonStyle(
            bgcolor=theme.TRANSPARENT, overlay_color=theme.TRANSPARENT,
            shadow_color=theme.TRANSPARENT, mouse_cursor=ft.MouseCursor.CLICK,
            side=ft.BorderSide(0, theme.TRANSPARENT),
            shape=ft.RoundedRectangleBorder(radius=5),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        ), **kwargs,
    )


def launcher_icon_button(*, icon, on_click: Callable, tooltip: str, primary: bool = False, **kwargs) -> ft.IconButton:
    """Compact launcher actions with no hover fill and a consistent cursor."""
    defaults = {
        "icon": icon, "on_click": on_click, "tooltip": tooltip,
        "icon_color": theme.ON_ACCENT if primary else theme.ACCENT,
        "bgcolor": theme.ACCENT if primary else theme.TRANSPARENT,
        "icon_size": 20, "width": 36, "height": 36,
        "mouse_cursor": ft.MouseCursor.CLICK,
        "style": ft.ButtonStyle(
            bgcolor=theme.ACCENT if primary else theme.TRANSPARENT, overlay_color=theme.TRANSPARENT,
            shadow_color=theme.TRANSPARENT, mouse_cursor=ft.MouseCursor.CLICK,
            shape=ft.RoundedRectangleBorder(radius=5), padding=6,
        ),
    }
    defaults.update(kwargs)
    return ft.IconButton(**defaults)


def search_input(hint_text: str = "Search", *, on_change: Callable | None = None,
                 on_submit: Callable | None = None, **kwargs) -> SimpleInput:
    """Standard launcher search bar with uniform 5px corners and muted hint."""
    defaults = {
        "hint_text": hint_text,
        "expand": True,
        "hint_style": ft.TextStyle(color=theme.MUTED),
        "border": {
            state: ft.OutlineInputBorder(border_radius=5, side=ft.BorderSide(0, theme.TRANSPARENT))
            for state in (ft.ControlState.DEFAULT, ft.ControlState.FOCUSED, ft.ControlState.DISABLED)
        },
        "prefix_icon": ft.Icons.SEARCH_SHARP,
        "on_change": on_change,
        "on_submit": on_submit,
    }
    defaults.update(kwargs)
    return SimpleInput(**defaults)


def card_surface(content: ft.Control, *, glow_color: str = theme.GLOW, horizontal_padding: int = 16,
                 margin_bottom: int = 8, key: ft.ValueKey | None = None, data=None) -> ft.Container:
    """Standardized acrylic card surface matching profile cards and mod rows."""
    from ui_profiles import profile_card_surface
    surface = profile_card_surface(content, glow_color=glow_color, horizontal_padding=horizontal_padding)
    surface.bgcolor = ft.Colors.with_opacity(0.18, theme.BLACK)
    if margin_bottom:
        surface.margin = ft.Margin.only(bottom=margin_bottom)
    if key is not None:
        surface.key = key
    if data is not None:
        surface.data = data
    return surface


class AcrylicSelector(ft.PopupMenuButton):
    """Dark launcher field with an anchored, keyboard-accessible acrylic menu."""

    def __init__(self, options: list[tuple[str, str]], value: str | None, on_select: Callable, *,
                 width=160, pixel=False, hint="Choose", tooltip=None, prefix: str | None = None):
        self.value = value
        self.options = options
        self._choose = on_select
        self._hint = hint
        self._pixel = pixel
        self._prefix = prefix
        self._label = ft.Text(hint, size=16 if pixel else 13, font_family="ps" if pixel else None,
                              color=theme.ACCENT, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        row_controls: list[ft.Control] = []
        if prefix:
            row_controls.append(ft.Text(prefix, size=16 if pixel else 13, font_family="ps" if pixel else None, color=theme.MUTED))
        row_controls.extend([self._label, ft.Icon(ft.Icons.EXPAND_MORE_SHARP, color=theme.ACCENT, size=18)])
        super().__init__(
            content=ft.Container(
                height=48, bgcolor=theme.SURFACE, border_radius=5,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                content=ft.Row(row_controls, spacing=8),
            ), width=width, height=48, padding=0, menu_padding=0,
            bgcolor=theme.TRANSPARENT, shadow_color=theme.SURFACE, elevation=0,
            menu_position=ft.PopupMenuPosition.UNDER,
            size_constraints=ft.BoxConstraints(min_width=width, max_width=width),
            shape=ft.RoundedRectangleBorder(radius=5), clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            style=ft.ButtonStyle(overlay_color=theme.TRANSPARENT, mouse_cursor=ft.MouseCursor.CLICK),
            tooltip=tooltip or hint,
        )
        self.refresh_options(options, value)

    def refresh_options(self, options: list[tuple[str, str]], value: str | None) -> None:
        self.options = options
        self.value = value
        self._label.value = next((label for key, label in options if key == value), self._hint)
        self.items = [
            ft.PopupMenuItem(
                key=ft.ValueKey(key), padding=0, height=46,
                content=ft.Container(
                    width=self.width, height=46, padding=ft.Padding.symmetric(horizontal=14),
                    bgcolor=ft.Colors.with_opacity(0.58, theme.BACKGROUND), blur=ft.Blur(14, 14),
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    content=ft.Row([
                        ft.Text(label, color=theme.ACCENT if key == value else theme.TEXT_SECONDARY, size=14,
                                expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Icon(ft.Icons.CHECK_SHARP, color=theme.ACCENT, size=16, visible=key == value),
                    ], spacing=8),
                ), on_click=lambda e, selected=key: self._select(selected, e),
            ) for key, label in options
        ]

    def _select(self, value: str, e) -> None:
        self.refresh_options(self.options, value)
        self._choose(e)
