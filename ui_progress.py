"""Shared, adaptive progress for launcher operations on any thread."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import threading
import time
from typing import Callable

import flet as ft

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
        self.status_text = ft.Text("Ready to play", font_family="ps", size=13, color="#f5de9a",
                                   expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.detail_text = ft.Text("", size=12, color="#929db1", text_align=ft.TextAlign.RIGHT,
                                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.bar = ft.ProgressBar(value=0.0, bar_height=6, color="#f5de9a",
                                  bgcolor=ft.Colors.BLACK_26, border_radius=5, expand=True)
        self.text_row = ft.Row([self.status_text, self.detail_text], spacing=12,
                               height=18, vertical_alignment=ft.CrossAxisAlignment.CENTER, visible=show_text)
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
        rendered = (snapshot, show_text)
        if rendered == self._last_rendered:
            return
        self.text_row.visible = show_text
        self.status_text.value = snapshot.label
        self.detail_text.value = snapshot.detail
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
