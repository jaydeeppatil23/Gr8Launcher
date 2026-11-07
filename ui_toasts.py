"""Frosted-glass notifications shared by every launcher page."""

import asyncio
import inspect
from collections import deque
from dataclasses import dataclass
from typing import Callable

import flet as ft
from ui_controls import launcher_button


@dataclass
class _Toast:
    message: str
    kind: str
    title: str
    panel: ft.Container
    progress: ft.Container
    action_label: str | None = None
    on_action: Callable | None = None
    action_button: ft.Button | None = None
    closing: bool = False


class ToastHost(ft.Container):
    """Queue notifications on the page loop, including calls from workers.

    Each visible card gets its own five-second lifetime. Extra notifications
    wait for a slot, and repeated messages are coalesced while still visible.
    The progress animation runs on the client rather than sending timer ticks.
    """

    DURATION = 5.0
    ENTER_MS = 320
    EXIT_MS = 260
    MOUNT_DELAY = 0.04
    MAX_VISIBLE = 3
    WIDTH = 392
    MARGIN = 16
    TONES = {
        "info": ("Information", "chest.png", "#f5de9a"),
        "success": ("Success", "diamond_ore.png", "#f5de9a"),
        "warning": ("Attention", "furnace.png", "#f5cf83"),
        "error": ("Something went wrong", "tnt.png", "#ff9292"),
        "update": ("Update", "grass_block.png", "#a7c9ff"),
    }

    def __init__(self, page: ft.Page, *, title_bar_height=40):
        self._page = page
        self._active: list[_Toast] = []
        self._pending = deque()
        self._delayed = {}
        self._closed = False
        self._ready = False
        self._column = ft.Column(
            tight=True,
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            scroll=ft.ScrollMode.AUTO,
        )
        super().__init__(
            top=title_bar_height,
            right=0,
            width=self.WIDTH,
            padding=self.MARGIN,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            visible=False,
            ignore_interactions=True,
            content=self._column,
        )

    def show(self, message, kind="info", title=None, *, key=None, delay=0,
             action_label: str | None = None, on_action: Callable | None = None):
        """Show a notification; a key and delay can debounce rapid edits."""
        message = str(message).strip()
        if not message or self._closed:
            return
        kind = kind if kind in self.TONES else "info"
        self._page.run_task(
            self._enqueue, message, kind, title or self.TONES[kind][0], key, delay,
            action_label, on_action,
        )

    async def _enqueue(self, message, kind, title, key, delay, action_label, on_action):
        if key is not None:
            token = object()
            self._delayed[key] = token
            if delay:
                await asyncio.sleep(delay)
            if self._delayed.get(key) is not token:
                return
            self._delayed.pop(key, None)
        if self._closed:
            return
        request = (message, kind, title, action_label, on_action)
        if request in self._pending or any(
            (toast.message, toast.kind, toast.title, toast.action_label, toast.on_action) == request and not toast.closing
            for toast in self._active
        ):
            return
        self._pending.append(request)
        self._drain()

    def _drain(self):
        if not self._ready or self._closed:
            return
        while self._pending and len(self._active) < self.MAX_VISIBLE:
            toast = self._build(*self._pending.popleft())
            self._active.append(toast)
            self._column.controls.append(toast.panel)
            self.visible = True
            self.ignore_interactions = False
            if not self._update():
                return
            self._page.run_task(self._play, toast)

    def _build(self, message, kind, title, action_label=None, on_action=None):
        _, icon, color = self.TONES[kind]
        progress = ft.Container(
            width=0,
            height=3,
            bgcolor=color,
            animate=ft.Animation(int(self.DURATION * 1000), ft.AnimationCurve.LINEAR),
        )
        close_button = ft.IconButton(
            icon=ft.Icons.CLOSE_SHARP,
            icon_color="#bac6d8",
            icon_size=16,
            width=24,
            height=24,
            padding=0,
            tooltip="Dismiss notification",
            style=ft.ButtonStyle(
                overlay_color=ft.Colors.TRANSPARENT,
                padding=0,
                shape=ft.RoundedRectangleBorder(radius=2),
            ),
        )
        action_button = launcher_button(action_label, None, height=30) if action_label and on_action else None
        glass = ft.Container(
            border_radius=8,
            # border=ft.Border.all(1,"#f5de9a"),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            blur=ft.Blur(10, 10),
            bgcolor=ft.Colors.with_opacity(0.7,"#3D5377"),
            # gradient=ft.LinearGradient(
            #     begin=ft.Alignment.TOP_LEFT,
            #     end=ft.Alignment.BOTTOM_RIGHT,
            #     colors=["#1cffffff", "#06ffffff"],
            # ),
            # border=ft.Border(
            #     top=ft.BorderSide(1, ft.Colors.with_opacity(0.24, "#f5de9a")),
            #     left=ft.BorderSide(1, ft.Colors.with_opacity(0.16, "#f5de9a")),
            #     right=ft.BorderSide(1, ft.Colors.with_opacity(0.35, "#000000")),
            #     bottom=ft.BorderSide(1, ft.Colors.with_opacity(0.35, "#000000")),
            # ),
            content=ft.Column(
                tight=True,
                spacing=0,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    ft.Container(
                        padding=ft.Padding.only(left=14, right=10, top=13, bottom=14),
                        content=ft.Row(
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                            controls=[
                                ft.Container(
                                    width=36,
                                    height=36,
                                    border_radius=2,
                                    bgcolor=ft.Colors.BLACK_26,
                                    alignment=ft.Alignment.CENTER,
                                    content=ft.Image(
                                        src=f"icons/icon_blocks/{icon}",
                                        width=28,
                                        height=28,
                                        fit=ft.BoxFit.CONTAIN,
                                        filter_quality=ft.FilterQuality.NONE,
                                    ),
                                ),
                                ft.Column(
                                    expand=True,
                                    tight=True,
                                    spacing=4,
                                    controls=[
                                        ft.Text(
                                            title, font_family="ps", size=16,
                                            weight=ft.FontWeight.BOLD, color=color,
                                        ),
                                        ft.Text(
                                            message, font_family="ps", size=14,
                                            color="#e5ebf4", selectable=True,
                                        ),
                                        *([action_button] if action_button else []),
                                    ],
                                ),
                                close_button,
                            ],
                        ),
                    ),
                    ft.Container(
                        bgcolor=ft.Colors.BLACK_26,
                        content=ft.Stack(height=3, controls=[progress]),
                    ),
                ],
            ),
        )
        panel = ft.Container(
            content=ft.Semantics(content=glass, live_region=True, container=True),
            border_radius=3,
            shadow=ft.BoxShadow(
                blur_radius=8,
                spread_radius=0,
                color=ft.Colors.with_opacity(0.22, "#000000"),
                offset=ft.Offset(0, 5),
            ),
            # An opacity layer isolates BackdropFilter from the page while it
            # animates. Keep the panel at full opacity and move it beyond the
            # clipped rail instead, so it continues sampling the background.
            offset=ft.Offset(1.12, 0),
            animate_offset=ft.Animation(self.ENTER_MS, ft.AnimationCurve.EASE_OUT_CUBIC),
        )
        toast = _Toast(message, kind, title, panel, progress, action_label, on_action, action_button)

        async def dismiss(e):
            await self._dismiss(toast)

        close_button.on_click = dismiss
        if action_button:
            async def activate(e):
                if self._closed or toast.closing or toast not in self._active:
                    return
                # Claim the action before yielding so double clicks cannot run it twice.
                action_button.disabled = True
                await self._dismiss(toast)
                if not self._closed:
                    result = on_action(e)
                    if inspect.isawaitable(result):
                        await result
            action_button.on_click = activate
        return toast

    async def _play(self, toast):
        await asyncio.sleep(self.MOUNT_DELAY)
        if self._closed or toast.closing:
            return
        toast.panel.offset = ft.Offset(0, 0)
        if not self._update():
            return
        await asyncio.sleep(self.ENTER_MS / 1000)
        if self._closed or toast.closing:
            return
        # Flet's AnimatedScale only supports a uniform scale. AnimatedContainer
        # width keeps the bar three pixels high while it fills from the left.
        toast.progress.width = max(0, self.width - 2 * self.MARGIN - 2)
        if not self._update():
            return
        await asyncio.sleep(self.DURATION)
        await self._dismiss(toast)

    async def _dismiss(self, toast):
        if self._closed or toast.closing or toast not in self._active:
            return
        toast.closing = True
        toast.panel.ignore_interactions = True
        toast.panel.animate_offset = ft.Animation(self.EXIT_MS, ft.AnimationCurve.EASE_IN_CUBIC)
        toast.panel.offset = ft.Offset(1.12, 0)
        if not self._update():
            return
        await asyncio.sleep(self.EXIT_MS / 1000)
        if self._closed:
            return
        self._active.remove(toast)
        self._column.controls.remove(toast.panel)
        self.visible = bool(self._active)
        self.ignore_interactions = not self.visible
        if self._update():
            self._drain()

    def resize(self, e):
        self.width = min(self.WIDTH, e.width)
        self._update()

    def start(self):
        """Release notifications queued while the launcher was mounting."""
        self._ready = True
        self._drain()

    def close(self):
        self._closed = True
        self._pending.clear()
        self._delayed.clear()
        self._active.clear()
        self._column.controls.clear()
        self.visible = False
        self.ignore_interactions = True

    def _update(self):
        try:
            self.update()
            return True
        except RuntimeError:
            self.close()
            return False
