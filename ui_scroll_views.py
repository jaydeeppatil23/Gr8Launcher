"""Shared trailing space for vertical scrollable content."""

import math

import flet as ft


SCROLL_FADE_START = 0.85
BOTTOM_SCROLL_GAP = 12
BOTTOM_SCROLL_FRACTION = 0.09


def bottom_scroll_padding(viewport_height: float = 0, *, faded: bool = True) -> int:
    """Lift the last item into view without reserving the entire fade area."""
    trailing_height = viewport_height * BOTTOM_SCROLL_FRACTION if faded else 0
    return max(BOTTOM_SCROLL_GAP, math.ceil(trailing_height) + 4)


class ScrollColumn(ft.Column):
    """Retain bottom space when callers replace or append scrollable controls."""

    def __init__(self, controls: list[ft.Control] | None = None, *, bottom_fade: bool = False, **kwargs) -> None:
        self._bottom_space = ft.Container(height=BOTTOM_SCROLL_GAP)
        if bottom_fade:
            kwargs["on_size_change"] = self._measure_view
        super().__init__(controls=list(controls or []), **kwargs)

    def before_update(self) -> None:
        super().before_update()
        self.controls = [control for control in self.controls if control is not self._bottom_space]
        if self.controls:
            self.controls.append(self._bottom_space)

    def _measure_view(self, e: ft.LayoutSizeChangeEvent) -> None:
        if e.height <= 0:
            ft.context.disable_auto_update()
            return
        height = bottom_scroll_padding(e.height)
        if self._bottom_space.height == height:
            ft.context.disable_auto_update()
            return
        self._bottom_space.height = height
