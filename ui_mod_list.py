"""Keep only the visible mod rows and a small scroll buffer mounted."""

from __future__ import annotations

import math
from typing import Callable, Sequence

import flet as ft

from ui_profiles import ProfileList


class ModList(ProfileList):
    ROW_HEIGHT = 92
    ROW_EXTENT = ROW_HEIGHT + 8
    OVERSCAN = 2

    def __init__(self, on_window_changed: Callable[[], None]):
        self._items: Sequence = ()
        self._card_builder: Callable | None = None
        self._window: tuple[int, int] | None = None
        self._on_window_changed = on_window_changed
        self._top = ft.Container(height=0, key=ft.ValueKey("mods-before"))
        self._bottom = ft.Container(height=0, key=ft.ValueKey("mods-after"))
        super().__init__(on_order_changed=lambda ids: False, cache_extent=0)

    def _new_view(self) -> ft.Column:
        # Only a few rows exist here. A scrollable Column measures both
        # spacers immediately, so the scrollbar knows the full list extent.
        # A second lazy ListView would estimate from the small visible window
        # and make large scroll jumps stop short of their destination.
        return ft.Column(
            expand=True, spacing=0, scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            on_scroll=self._scrolled, scroll_interval=16,
        )

    def set_items(self, items: Sequence, card_builder: Callable, *, offset: float = 0) -> None:
        self._items = items
        self._card_builder = card_builder
        self._scroll_pixels = max(0, offset)
        self._render_window(force=True, update=False)

    def reset_viewport(self) -> None:
        """Recreate the cached scroll viewport while retaining rows and position."""
        self.content = self._new_view()
        self._window = None
        self._max_scroll = 0

    def _render_window(self, *, force=False, update=True) -> None:
        count = len(self._items)
        viewport = self._viewport_height or 400
        # Clamp after removals/filtering, before asking the client to restore
        # an offset whose row might no longer exist.
        offset = min(self._scroll_pixels, max(0, count * self.ROW_EXTENT - viewport))
        self._scroll_pixels = offset
        first = max(0, int(offset // self.ROW_EXTENT) - self.OVERSCAN)
        last = min(count, math.ceil((offset + viewport) / self.ROW_EXTENT) + self.OVERSCAN)
        if not force and self._window == (first, last):
            return
        self._window = (first, last)
        self._top.height = first * self.ROW_EXTENT
        self._bottom.height = (count - last) * self.ROW_EXTENT
        cards = [self._card_builder(item) for item in self._items[first:last]]
        for card in cards:
            card.height = self.ROW_HEIGHT
        super().set_cards(cards, header=self._top, footer=self._bottom, update=False)
        # ProfileList resets an unmounted viewport's scroll position. This
        # window already contains spacers for offset; keep its matching state
        # so the parent can restore that position after mounting.
        self._scroll_pixels = offset
        for card in self.cards:
            # These rows cannot be dragged. Avoid the per-row geometry events
            # and animation wrappers needed by the Library's reorderable list.
            card.on_size_change = None
            card.offset = None
            card.animate_offset = None
            card.animate_opacity = None
        self._on_window_changed()
        if update:
            self._update()

    def _scrolled(self, e) -> None:
        source = getattr(e, "control", None)
        if source is not None and source._i != self.content._i:
            ft.context.disable_auto_update()
            return
        super()._scrolled(e)
        self._render_window()

    def _measure_view(self, e) -> None:
        if e.height <= 0:
            return
        super()._measure_view(e)
        self._render_window()
