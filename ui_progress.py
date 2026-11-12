"""Shared, adaptive progress for launcher operations on any thread."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import threading
import time
from typing import Callable

import flet as ft
import ui_theme as theme

from credential_store import redact_credentials


_UNCHANGED = object()


def _ratio(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return max(0.0, min(1.0, number)) if math.isfinite(number) else None


@dataclass
class _Operation:
    label: str
    value: float | None
    detail: str
    priority: int
    ready_at: float
    show_percentage: bool


@dataclass(frozen=True)
class ProgressSnapshot:
    label: str
    value: float | None
    detail: str
    active_count: int


class ProgressTask:
    """An operation owns only its progress; late updates cannot revive it."""

    def __init__(self, host: ProgressHost | None, token: int = 0):
        self._host = host
        self._token = token

    def update(self, label=_UNCHANGED, *, value=_UNCHANGED, detail=_UNCHANGED) -> None:
        if self._host:
            self._host._change(self._token, label, value, detail)

    def report(self, completed: float, total: float | None, *, label=_UNCHANGED) -> None:
        value = completed / total if total and total > 0 else None
        self.update(label, value=value)

    def on_bytes(self, completed: int, total: int | None) -> None:
        detail = f"{completed / 1024 / 1024:.1f} MB"
        if total and total > 0:
            detail += f" / {total / 1024 / 1024:.1f} MB"
        self.update(value=completed / total if total and total > 0 else None, detail=detail)

    def __call__(self, message: str) -> None:
        self.update(message, value=None, detail="")

    def finish(self) -> None:
        if self._host:
            self._host._finish(self._token)

    def __enter__(self) -> ProgressTask:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.finish()


def track_progress(host: ProgressHost | None, label: str, **kwargs) -> ProgressTask:
    """Use the same context manager with or without a mounted progress host."""
    return host.begin(label, **kwargs) if host else ProgressTask(None)


class _SmoothProgressBar(ft.Container):
    """Animate measured changes on the client, independently of callback timing."""

    TRANSITION_MS = 240
    BEAM_WIDTH = 90
    BEAM_SPEED = 320  # Pixels per second, matching the v1.2.0 shimmer.

    def __init__(self, page):
        self._page = page
        self._value: float | None = 0.0
        self._track_width = 0.0
        self._closed = False
        self._shimmer_active = False
        self._shimmer_generation = 0
        self._shimmer_future = None
        self._shimmer = ft.Container(
            left=-self.BEAM_WIDTH, top=0, bottom=0, width=self.BEAM_WIDTH,
            opacity=0, visible=False,
            # Reuse v1.2.0's feathered beam, tinted with the launcher background.
            gradient=ft.LinearGradient(
                begin=ft.Alignment.CENTER_LEFT, end=ft.Alignment.CENTER_RIGHT,
                colors=[ft.Colors.with_opacity(alpha, theme.BACKGROUND)
                        for alpha in (0.0, 0.08, 0.22, 0.40, 0.22, 0.08, 0.0)],
                stops=[0.0, 0.20, 0.38, 0.50, 0.62, 0.80, 1.0],
            ),
            animate_position=ft.Animation(0, ft.AnimationCurve.LINEAR),
        )
        self._fill = ft.Container(
            bgcolor=theme.ACCENT, border_radius=5,
            content=ft.Stack([self._shimmer], fit=ft.StackFit.EXPAND),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            # Slide a full-width fill under the clipped track. Fractional offsets
            # stay accurate across window resizes without measuring pixel widths.
            offset=ft.Offset(-1, 0),
            animate_offset=ft.Animation(0, ft.AnimationCurve.EASE_OUT_CUBIC),
        )
        self._unknown = ft.ProgressBar(
            value=None, bar_height=6, color=theme.ACCENT,
            bgcolor=theme.TRANSPARENT, border_radius=5, visible=False,
        )
        self._semantics = ft.Semantics(
            label="Progress", value="0%", container=True, exclude_semantics=True,
            content=ft.Stack([self._fill, self._unknown], fit=ft.StackFit.EXPAND,
                             on_size_change=self._measure_width),
        )
        super().__init__(
            height=6, bgcolor=theme.SURFACE, border_radius=5,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS, content=self._semantics,
        )

    @property
    def value(self) -> float | None:
        return self._value

    @value.setter
    def value(self, value: float | None) -> None:
        value = _ratio(value)
        if value == self._value:
            return
        # Mode changes and resets must cancel an old animation immediately.
        animate = value is not None and value > 0 and (
            self._value is None or value >= self._value
        )
        self._fill.animate_offset = ft.Animation(
            self.TRANSITION_MS if animate else 0, ft.AnimationCurve.EASE_OUT_CUBIC,
        )
        self._fill.offset = ft.Offset((value if value is not None else 0) - 1, 0)
        self._fill.opacity = 0 if value is None else 1
        self._unknown.visible = value is None
        self._semantics.value = "Loading" if value is None else f"{value:.0%}"
        restart = value is not None and self._value is not None and value < self._value
        self._value = value
        self._sync_shimmer(restart=restart)

    async def _measure_width(self, event) -> None:
        width = max(0.0, float(event.width))
        if self._closed or width == self._track_width:
            return
        self._track_width = width
        self._sync_shimmer(restart=True)
        try:
            self.update()
        except RuntimeError:
            pass

    def _sync_shimmer(self, *, restart: bool = False) -> None:
        active = not self._closed and self._track_width > 0 and self._value is not None and 0 < self._value < 1
        if active == self._shimmer_active and not restart:
            return
        self._shimmer_generation += 1
        self._shimmer_active = active
        if self._shimmer_future is not None:
            self._shimmer_future.cancel()
            self._shimmer_future = None
        self._shimmer.visible = active
        self._reset_beam()
        if active:
            self._shimmer_future = self._page.run_task(self._play_shimmer, self._shimmer_generation)

    def _reset_beam(self) -> None:
        self._shimmer.opacity = 0
        self._shimmer.animate_position = ft.Animation(0, ft.AnimationCurve.LINEAR)
        # The fill is translated under the track; start just outside its visible left edge.
        self._shimmer.left = self._track_width * (1 - (self._value or 0)) - self.BEAM_WIDTH

    async def _play_shimmer(self, generation: int) -> None:
        while self._shimmer_active and generation == self._shimmer_generation:
            # Commit the hidden reset before starting another forward sweep.
            await asyncio.sleep(0.06)
            if not self._shimmer_active or generation != self._shimmer_generation:
                return
            distance = self._track_width - self._shimmer.left
            duration_ms = max(700, min(2200, int(distance / self.BEAM_SPEED * 1000)))
            self._shimmer.animate_position = ft.Animation(duration_ms, ft.AnimationCurve.LINEAR)
            self._shimmer.opacity = 1
            self._shimmer.left = self._track_width
            try:
                self._shimmer.update()
            except RuntimeError:
                return
            await asyncio.sleep(duration_ms / 1000)
            if not self._shimmer_active or generation != self._shimmer_generation:
                return
            self._reset_beam()
            try:
                self._shimmer.update()
            except RuntimeError:
                return

    def close(self) -> None:
        self._closed = True
        self._sync_shimmer()


class ProgressHost(ft.Container):
    """Display the highest-priority operation, retaining every active task.

    Only the page loop changes Flet controls. Worker updates are coalesced,
    and quick operations finish before their delayed animation appears.
    """

    BAR_AREA_HEIGHT = 18  # 6 px bar plus 4 px top and 8 px bottom padding.
    STATUS_AREA_HEIGHT = 22  # 18 px status row plus 4 px spacing.

    def __init__(self, page, *, show_text: bool = True, clock: Callable[[], float] = time.monotonic):
        self._page = page
        self._clock = clock
        self._lock = threading.RLock()
        self._operations: dict[int, _Operation] = {}
        self._serial = 0
        self._scheduled = False
        self._ready = False
        self._closed = False
        self._show_text = show_text
        self._idle = ProgressSnapshot("Ready to play", 0.0, "", 0)
        self._last_rendered: tuple[ProgressSnapshot, bool] | None = None
        self.status_text = ft.Text("Ready to play", font_family="ps", size=13, color=theme.ACCENT,
                                   expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.detail_text = ft.Text("", size=12, color=theme.MUTED, text_align=ft.TextAlign.RIGHT,
                                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.bar = _SmoothProgressBar(page)
        self.text_row = ft.Row([self.status_text, self.detail_text], spacing=12,
                               height=18, vertical_alignment=ft.CrossAxisAlignment.CENTER, visible=False)
        super().__init__(padding=ft.Padding.only(left=20, right=24, top=4, bottom=8),
                         content=ft.Column([
                             self.text_row,
                             self.bar,
                         ], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))

    def begin(self, label: str, *, value: float | None = None, detail: str = "",
              priority: int = 40, delay: float = 0.18, show_percentage: bool = True) -> ProgressTask:
        with self._lock:
            if self._closed:
                return ProgressTask(None)
            self._serial += 1
            token = self._serial
            self._operations[token] = _Operation(redact_credentials(str(label)), _ratio(value),
                                                 redact_credentials(str(detail)), priority,
                                                 self._clock() + max(0, delay), show_percentage)
            self._schedule()
        if delay > 0:
            self._page.run_task(self._activate_after, token, delay)
        return ProgressTask(self, token)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._operations)

    @property
    def snapshot(self) -> ProgressSnapshot:
        with self._lock:
            ready = [(token, op) for token, op in self._operations.items() if op.ready_at <= self._clock()]
            if not ready:
                return self._idle
            _, operation = max(ready, key=lambda item: (item[1].priority, item[0]))
            parts = []
            if operation.value is not None and operation.show_percentage:
                parts.append(f"{operation.value:.0%}")
            if operation.detail:
                parts.append(operation.detail)
            if len(ready) > 1:
                parts.append(f"+{len(ready) - 1} more")
            return ProgressSnapshot(operation.label, operation.value, " · ".join(parts), len(ready))

    def set_idle(self, label: str = "Ready to play", *, detail: str = "") -> None:
        with self._lock:
            self._idle = ProgressSnapshot(redact_credentials(str(label)), 0.0, redact_credentials(str(detail)), 0)
            self._schedule()

    def set_show_text(self, show: bool) -> None:
        """Hide the text row on pages that only need the progress bar."""
        with self._lock:
            if self._show_text != show:
                self._show_text = show
                self._schedule()

    def _change(self, token, label, value, detail) -> None:
        with self._lock:
            operation = self._operations.get(token)
            if not operation or self._closed:
                return
            if label is not _UNCHANGED:
                operation.label = redact_credentials(str(label))
            if value is not _UNCHANGED:
                operation.value = _ratio(value)
            if detail is not _UNCHANGED:
                operation.detail = redact_credentials(str(detail))
            self._schedule()

    def _finish(self, token: int) -> None:
        with self._lock:
            if self._operations.pop(token, None) is not None:
                self._schedule()

    def _schedule(self) -> None:
        if self._closed or not self._ready or self._scheduled:
            return
        self._scheduled = True
        self._page.run_task(self._flush)

    async def _activate_after(self, token: int, delay: float) -> None:
        await asyncio.sleep(delay)
        with self._lock:
            if token in self._operations:
                self._schedule()

    async def _flush(self) -> None:
        await asyncio.sleep(0.025)
        with self._lock:
            self._scheduled = False
            if self._closed or not self._ready:
                return
            snapshot = self.snapshot
            show_text = self._show_text
            ready = [(token, op) for token, op in self._operations.items() if op.ready_at <= self._clock()]
            active_op = max(ready, key=lambda item: (item[1].priority, item[0]))[1] if ready else None
            # Only show text for installation/active operation events; not for page loading or idle
            is_installation = active_op is not None and (
                active_op.priority >= 50 or
                any(keyword in active_op.label.lower() for keyword in (
                    "install", "download", "updating", "unpack", "verifying", "launch", "repair", "saving", "signing"
                ))
            )
        rendered = (snapshot, show_text, is_installation)
        if rendered == self._last_rendered:
            return
        # Display the text row ONLY during real operations/installations: no idle text, and no page-loading text.
        self.text_row.visible = show_text and is_installation
        self.status_text.value = snapshot.label
        self.detail_text.value = snapshot.detail if is_installation else ""
        self.bar.value = snapshot.value
        try:
            self.update()
            self._last_rendered = rendered
        except RuntimeError:
            pass

    def start(self) -> None:
        with self._lock:
            self._ready = True
            self._schedule()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._operations.clear()
        self.bar.close()
