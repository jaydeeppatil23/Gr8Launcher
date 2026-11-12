"""Frosted profile cards with smooth edge scrolling and an eased drop."""

import asyncio
from collections.abc import Callable
from dataclasses import fields

import flet as ft
from flet.controls.base_control import BaseControl

from ui_scroll_views import SCROLL_FADE_START, bottom_scroll_padding


def _copy_control(value):
    """Copy the drag preview without copying sessions or bound callbacks."""
    if isinstance(value, BaseControl):
        # Flet's "skip" metadata controls wire serialization, not cloning.
        # Button styles and color/elevation overrides still need copying;
        # before_update() serializes them through the control's internals.
        return type(value)(**{
            field.name: None if field.name == "key" else _copy_control(getattr(value, field.name))
            for field in fields(value)
            if field.init and not field.name.startswith("_")
        })
    if isinstance(value, list):
        return [_copy_control(item) for item in value]
    if isinstance(value, dict):
        return {key: _copy_control(item) for key, item in value.items()}
    return value


class ProfileList(ft.Container):
    """Keep live cards in a plain list and animate a separate drag preview."""

    DROP_SETTLE_SECONDS = 0.35
    DROP_ANIMATION_MS = 300
    AUTO_SCROLL_EDGE = 48
    AUTO_SCROLL_MIN_SPEED = 120
    AUTO_SCROLL_MAX_SPEED = 420
    AUTO_SCROLL_SPEED_TOLERANCE = 60

    def __init__(self, on_order_changed: Callable[[list[str]], bool], *, cache_extent: float = 10000):
        self._cache_extent = cache_extent
        super().__init__(
            expand=True,
            on_size_change=self._measure_view,
            content=self._new_view(),
        )
        # Pointer dragging owns the slots and drop animation. A native
        # reorderable list adds its own cached slots and placeholders, which
        # can retain gaps when profiles change or the tab is remounted.
        self._cards = []
        self.header = None
        self.footer = None
        self._on_order_changed = on_order_changed
        self._dragging = False
        self._holding = False
        self._drag_card = None
        self._drag_surface = None
        self._drag_style = None
        self._generation = 0
        self._pending_cards = None
        self._pending_order = None
        self._finishing = False
        self._sizes = {}
        self._viewport_height = 0
        self._header_height = 0
        self._scroll_pixels = 0
        self._max_scroll = 0
        self._preview = None
        self._drag_layout = None
        self._drop_indices = None
        self._scroll_lock = asyncio.Lock()
        self._auto_scroll_active = None
        self._auto_scroll_started = False

    @property
    def controls(self):
        return self.content.controls

    @controls.setter
    def controls(self, controls):
        self.content.controls = controls

    def _new_view(self):
        return ft.ListView(
            expand=True, scroll=ft.ScrollMode.AUTO,
            on_scroll=self._scrolled, scroll_interval=16, cache_extent=self._cache_extent,
            padding=ft.Padding.only(bottom=bottom_scroll_padding(getattr(self, "_viewport_height", 0))),
        )

    async def scroll_to(self, **kwargs):
        return await self.content.scroll_to(**kwargs)

    @property
    def cards(self):
        """Profile rows, excluding the non-draggable header and empty state."""
        return self._cards

    @property
    def dragging(self) -> bool:
        return self._dragging

    @property
    def holding(self) -> bool:
        """Whether the pointer is still holding a profile, excluding snap-back."""
        return self._holding

    def set_cards(self, cards, *, header=None, footer=None, update=True):
        if self._dragging:
            self._pending_cards = (cards, header, footer)
            return
        existing_cards = {card.data: card for card in self._cards}
        refreshed_cards = []
        for card in cards:
            existing = existing_cards.get(card.data)
            if (existing is not None and existing is not card
                    and type(existing) is type(card) and existing.key == card.key):
                # Flet freezes new objects reconciled against an existing key.
                # Retain the mutable card and refresh its contents and handlers.
                for field in fields(card):
                    if (field.init and not field.name.startswith("_")
                            and not field.metadata.get("skip", False)):
                        setattr(existing, field.name, getattr(card, field.name))
                card = existing
            refreshed_cards.append(card)
            card.on_size_change = self._measure_card
            # Keep these wrappers mounted so hiding the source card and
            # sliding its neighbors do not dispose the active drag gesture.
            card.offset = ft.Offset(0, 0)
            card.animate_offset = ft.Animation(0)
            # A zero-duration fade preserves the gesture wrapper and reveals
            # the real card in the same frame that removes its drag preview.
            card.animate_opacity = ft.Animation(0)
        self._cards = refreshed_cards
        current_ids = {card.data for card in self._cards}
        self._sizes = {pid: size for pid, size in self._sizes.items() if pid in current_ids}
        self.header = header
        self._header_height = 0
        if header is not None:
            header.on_size_change = self._measure_header
        self.footer = footer
        self._update(update=update)

    def set_order(self, profile_ids):
        """Move existing cards without remounting their icons, text or glow."""
        if (len(set(profile_ids)) != len(profile_ids)
                or set(profile_ids) != {card.data for card in self._cards}):
            return False
        if self._pending_cards is not None:
            cards, header, footer = self._pending_cards
            if set(profile_ids) != {card.data for card in cards}:
                return False
            by_id = {card.data: card for card in cards}
            self._pending_cards = ([by_id[pid] for pid in profile_ids], header, footer)
        if self._dragging:
            self._pending_order = list(profile_ids)
        else:
            by_id = {card.data: card for card in self._cards}
            self._cards = [by_id[pid] for pid in profile_ids]
            self._update()
        return True

    def _update(self, *, update=True):
        try:
            page = self.page
        except RuntimeError:
            page = None
        mounted = page is not None and page.session.index.get(self._i) is self
        if not mounted:
            # Flet retains client controls by ID after a tab is removed. Use a
            # fresh viewport for offscreen changes so remounting cannot replay
            # an insertion against the list's previous client state.
            self.content = self._new_view()
            self._scroll_pixels = self._max_scroll = 0
        # Compose even while unmounted or batching a drop, so the next parent
        # update serializes the current rows in their actual order.
        self.controls = (
            ([self.header] if self.header is not None else [])
            + self._cards
            + ([self.footer] if self.footer is not None else [])
        )
        if self._finishing:
            return
        if mounted and update:
            # Surface update errors on mounted controls so they aren't hidden.
            self.update()

    def _start_drag(self, e: ft.OnReorderEvent):
        index = e.old_index
        if index is None or not 0 <= index < len(self._cards):
            return
        self._generation += 1
        # A second drag can begin before the previous drop has settled.
        self._restore_drag_card()
        self._dragging = True
        self._holding = True
        card = self._cards[index]
        self._drag_card = card
        surface = card.content
        self._drag_surface = surface
        self._drag_style = (card.bgcolor, card.border, card.scale, surface.content.controls[0].opacity)
        card.scale = 1
        self._update()

    def _restore_drag_card(self):
        if self._drag_card is not None and self._drag_style is not None:
            card = self._drag_card
            card.bgcolor, card.border, card.scale, glow_opacity = self._drag_style
            self._drag_surface.content.controls[0].opacity = glow_opacity
        self._drag_card = None
        self._drag_surface = None
        self._drag_style = None

    async def _settle_drag(self, generation):
        # Keep the preview alive until its drop animation has completed.
        await asyncio.sleep(self.DROP_SETTLE_SECONDS)
        if generation == self._generation:
            self._finish_drag()

    def _finish_drag(self):
        preview = self._preview
        # Reveal the original card, commit its position and remove the preview
        # in one patch. Intermediate patches can paint an empty or doubled card.
        self._finishing = True
        try:
            self._finish_drag_state()
        finally:
            self._finishing = False
        if preview is not None:
            page = self.page
            if preview in page.overlay:
                page.overlay.remove(preview)
            page.update()
        else:
            self._update()

    def _finish_drag_state(self):
        self._preview = None
        if self._drag_layout is not None:
            for card in self._cards:
                card.offset, card.animate_offset, card.opacity = self._drag_layout[card.data]
            self._drag_layout = None
        if self._drop_indices is not None:
            old_index, new_index = self._drop_indices
            self._drop_indices = None
            self._reorder(ft.OnReorderEvent(
                "reorder", self, old_index=old_index, new_index=new_index,
            ))
        self._restore_drag_card()
        self._dragging = False
        self._holding = False
        if self._pending_cards is not None:
            cards, header, footer = self._pending_cards
            self._pending_cards = None
            self.set_cards(cards, header=header, footer=footer)
        else:
            self._update()
        if self._pending_order is not None:
            profile_ids = self._pending_order
            self._pending_order = None
            self.set_order(profile_ids)

    def _measure_card(self, e):
        # Geometry notifications only update drag bookkeeping. Flet's automatic
        # update would otherwise diff the page after every resized row.
        ft.context.disable_auto_update()
        self._sizes[e.control.data] = (e.width, e.height)

    def _measure_view(self, e):
        ft.context.disable_auto_update()
        if e.height <= 0:
            return
        self._viewport_height = e.height
        if isinstance(self.content, ft.ListView):
            padding = ft.Padding.only(bottom=bottom_scroll_padding(e.height))
            if self.content.padding != padding:
                self.content.padding = padding
                try:
                    self.content.update()
                except RuntimeError:
                    pass  # The list may have been detached during navigation.

    def _measure_header(self, e):
        ft.context.disable_auto_update()
        self._header_height = e.height

    def _scrolled(self, e):
        ft.context.disable_auto_update()
        self._scroll_pixels = e.pixels
        self._max_scroll = e.max_scroll_extent
        if self._preview is not None and self._holding:
            self._move_preview()

    def _positions(self, cards):
        positions, top = {}, 0
        for card in cards:
            positions[card.data] = top
            top += self._sizes[card.data][1]
        return positions

    def start_pointer_drag(self, e):
        if self._dragging:
            return
        index = next((i for i, card in enumerate(self._cards) if card.data == e.control.data), None)
        if index is None or any(card.data not in self._sizes for card in self._cards):
            return
        self._start_drag(ft.OnReorderEvent("reorder_start", self, old_index=index))
        card = self._drag_card
        width, height = self._sizes[card.data]
        visible_height = height - (card.margin.bottom or 0)
        padding = card.content.content.controls[1].padding
        self._initial_top = e.global_position.y - e.local_position.y - (visible_height - 16) / 2
        left = e.global_position.x - e.local_position.x - padding.left - card.border.left.width
        self._pointer_start_y = self._pointer_y = e.global_position.y
        self._initial_scroll = self._scroll_pixels
        self._auto_scroll_started = self._auto_scroll_active is not None or self._scroll_lock.locked()
        self._source_index = self._target_index = index
        self._original_positions = self._positions(self._cards)
        self._target_positions = dict(self._original_positions)
        self._viewport_top = (self._initial_top - self._original_positions[card.data]
                              - self._header_height + self._initial_scroll)
        extent = (sum(self._sizes[item.data][1] for item in self._cards) + self._header_height
                  + bottom_scroll_padding(self._viewport_height))
        self._max_scroll = max(self._max_scroll, extent - self._viewport_height)
        self._drag_layout = {
            item.data: (item.offset, item.animate_offset, item.opacity) for item in self._cards
        }
        clone = _copy_control(card)
        clone.width = width
        clone.on_size_change = None
        # The overlay is outside the list's scoped theme. Carry that theme
        # with the preview so action buttons keep the same elevation shadows.
        surface = self.parent
        self._preview = ft.Container(
            left=left, top=self._initial_top, width=width, content=clone,
            theme=surface.theme if isinstance(surface, ft.Container) else None,
            dark_theme=surface.dark_theme if isinstance(surface, ft.Container) else None,
            ignore_interactions=True,
            animate_position=ft.Animation(0),
        )
        card.opacity = 0
        for item in self._cards:
            item.offset = ft.Offset(0, 0)
            item.animate_offset = ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC)
        self.page.overlay.append(self._preview)
        self.page.update()
        self.page.run_task(self._auto_scroll, self._generation)

    def update_pointer_drag(self, e):
        if self._preview is not None and self._holding:
            self._pointer_y = e.global_position.y
            self._move_preview()

    def _move_preview(self):
        card = self._drag_card
        remaining = [item for item in self._cards if item is not card]
        candidate_tops = [0]
        for item in remaining:
            candidate_tops.append(candidate_tops[-1] + self._sizes[item.data][1])
        delta = self._pointer_y - self._pointer_start_y
        top = (self._original_positions[card.data] + delta
               + self._scroll_pixels - self._initial_scroll)
        target_index = min(range(len(candidate_tops)), key=lambda i: abs(candidate_tops[i] - top))
        if target_index != self._target_index:
            self._target_index = target_index
            ordered = list(remaining)
            ordered.insert(target_index, card)
            self._target_positions = self._positions(ordered)
            for item in remaining:
                offset = self._target_positions[item.data] - self._original_positions[item.data]
                item.offset = ft.Offset(0, offset / self._sizes[item.data][1])
            self._update()
        preview_top = self._initial_top + delta
        if self._preview.top != preview_top:
            self._preview.top = preview_top
            self._preview.update()

    def _scroll_velocity(self):
        if not self._viewport_height:
            return 0, 0
        top = self._pointer_y - self._viewport_top
        bottom = self._viewport_height - top
        if top < self.AUTO_SCROLL_EDGE:
            direction, distance = -1, top
        elif bottom < self.AUTO_SCROLL_EDGE:
            direction, distance = 1, bottom
        else:
            return 0, 0
        proximity = max(0, min(1, (self.AUTO_SCROLL_EDGE - distance) / self.AUTO_SCROLL_EDGE))
        speed = self.AUTO_SCROLL_MIN_SPEED + (self.AUTO_SCROLL_MAX_SPEED - self.AUTO_SCROLL_MIN_SPEED) * proximity**2
        return direction, speed

    async def _auto_scroll(self, generation):
        try:
            while generation == self._generation and self._holding and self._preview is not None:
                direction, speed = self._scroll_velocity()
                target = self._max_scroll if direction > 0 else 0
                distance = abs(target - self._scroll_pixels)
                if direction and distance > 0.5:
                    async with self._scroll_lock:
                        if generation != self._generation or not self._holding:
                            break
                        active = (direction, speed, target)
                        previous = self._auto_scroll_active
                        if (previous is not None and previous[0] == direction
                                and previous[2] == target
                                and abs(previous[1] - speed) < self.AUTO_SCROLL_SPEED_TOLERANCE):
                            # Keep the animation running when the pointer jitters
                            # around a speed boundary; respond to deliberate moves.
                            active = previous
                        if active != self._auto_scroll_active:
                            self._auto_scroll_active = active
                            self._auto_scroll_started = True
                            # Flutter interpolates this continuously at its render
                            # rate instead of receiving repeated jump commands.
                            await self.scroll_to(
                                offset=target,
                                duration=max(50, round(distance / speed * 1000)),
                                curve=ft.AnimationCurve.LINEAR,
                            )
                else:
                    await self._stop_auto_scroll(generation)
                await asyncio.sleep(0.033)
        finally:
            await self._stop_auto_scroll(generation)

    async def _stop_auto_scroll(self, generation):
        async with self._scroll_lock:
            if generation != self._generation:
                return
            if self._auto_scroll_active is None:
                return
            self._auto_scroll_active = None
            try:
                page = self.page
            except RuntimeError:
                return
            if page.session.index.get(self._i) is not self:
                return
            try:
                # Delta zero stops at the client's actual position, avoiding a
                # backwards jump to the last position reported to Python.
                await self.scroll_to(delta=0)
            except RuntimeError:
                if page.session.index.get(self._i) is self:
                    raise

    def end_pointer_drag(self, e=None, *, cancel=False):
        if self._preview is None or not self._holding:
            return
        self._holding = False
        if self._auto_scroll_started or self._scroll_lock.locked():
            self.page.run_task(self._finish_scrolling_then_drop, self._generation, e, cancel)
        else:
            self._animate_drop(e, cancel)

    async def _finish_scrolling_then_drop(self, generation, e, cancel):
        await self._stop_auto_scroll(generation)
        # Let the final scroll notification arrive before calculating the slot.
        # The preview remains visible and stationary throughout this handoff.
        await asyncio.sleep(0.032)
        if generation == self._generation and self._preview is not None:
            self._animate_drop(e, cancel)

    def _animate_drop(self, e, cancel):
        if cancel:
            self._target_index = self._source_index
            target_top = self._original_positions[self._drag_card.data]
            for card in self._cards:
                card.offset = ft.Offset(0, 0)
            self._update()
        else:
            if e is not None:
                self._pointer_y = e.global_position.y
            self._move_preview()
            target_top = self._target_positions[self._drag_card.data]
            self._drop_indices = self._source_index, self._target_index
        self._preview.animate_position = ft.Animation(
            self.DROP_ANIMATION_MS, ft.AnimationCurve.EASE_OUT_CUBIC,
        )
        self._preview.top = (self._initial_top + target_top
                             - self._original_positions[self._drag_card.data]
                             - (self._scroll_pixels - self._initial_scroll))
        self._preview.update()
        self.page.run_task(self._settle_drag, self._generation)

    def cancel_drag(self):
        if self._dragging:
            self._generation += 1
            if self._auto_scroll_active is not None or self._scroll_lock.locked():
                # Keep the animation recorded until it is stopped. If a new
                # drag starts first, its loop must still stop or take it over.
                self.page.run_task(self._stop_auto_scroll, self._generation)
            self._finish_drag()

    def _reorder(self, e: ft.OnReorderEvent):
        old_index, new_index = e.old_index, e.new_index
        if (old_index is None or new_index is None
                or not 0 <= old_index < len(self._cards)
                or not 0 <= new_index <= len(self._cards)
                or old_index == new_index):
            return

        previous = list(self._cards)
        # The destination is the final slot after removing the source card.
        card = self._cards.pop(old_index)
        self._cards.insert(new_index, card)
        if not self._on_order_changed([item.data for item in self._cards]):
            self._cards = previous
        self._update()


def profile_card_surface(content, *, glow_color, selected=False, horizontal_padding=14):
    """Use the same clipped frosted glass for resting and dragged cards.

    Paint the tint inside the backdrop filter so opacity layers do not sample
    and composite the card's own dark background twice. Keep the outer margin
    outside the filter and draw the selected glow independently of the blur.
    """
    padding = ft.Padding.symmetric(horizontal=horizontal_padding, vertical=10)

    # Fixed-depth edge gradients stretch with the client layout. A canvas
    # resize callback queues redraws with old dimensions during window drags,
    # even on profiles whose glow is hidden.
    def glow_edge(begin, end, **bounds):
        return ft.Container(
            **bounds,
            gradient=ft.LinearGradient(
                begin=begin, end=end,
                colors=[
                    ft.Colors.with_opacity(alpha, glow_color)
                    for alpha in (0.16, 0.14, 0.09, 0.04, 0.01, 0)
                ],
                stops=[0, 0.2, 0.4, 0.6, 0.8, 1],
            ),
        )

    content = ft.Stack([
        ft.Stack(
            left=0, top=0, right=0, bottom=0,
            opacity=1 if selected else 0,
            controls=[
                glow_edge(ft.Alignment.TOP_CENTER, ft.Alignment.BOTTOM_CENTER,
                          left=0, top=0, right=0, height=15),
                glow_edge(ft.Alignment.BOTTOM_CENTER, ft.Alignment.TOP_CENTER,
                          left=0, bottom=0, right=0, height=15),
                glow_edge(ft.Alignment.CENTER_LEFT, ft.Alignment.CENTER_RIGHT,
                          left=0, top=0, bottom=0, width=15),
                glow_edge(ft.Alignment.CENTER_RIGHT, ft.Alignment.CENTER_LEFT,
                          right=0, top=0, bottom=0, width=15),
            ] if selected else [],
        ),
        ft.Container(padding=padding, content=content),
    ])
    return ft.Container(
        border_radius=5,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        bgcolor=ft.Colors.BLACK_26,
        blur=ft.Blur(5, 5),
        content=content,
    )


def profile_list_surface(view: ft.Control):
    """Let the profile viewport fill the remaining panel height with a smooth bottom fade."""
    return ft.ShaderMask(
        content=view,
        expand=True,
        blend_mode=ft.BlendMode.DST_IN,
        shader=ft.LinearGradient(
            begin=ft.Alignment.TOP_CENTER,
            end=ft.Alignment.BOTTOM_CENTER,
            colors=["#ffffffff", "#00ffffff"],
            stops=[SCROLL_FADE_START, 1.0],
        ),
    )


def profile_row(profile: dict, *, icon: ft.Control, badges: list[ft.Control],
                details: str, actions: list[ft.Control], glow_color: str,
                selected: bool = False, body_builder: Callable | None = None) -> ft.Container:
    """Build the Library profile row, with actions supplied by its screen."""
    icon_box = ft.Container(
        width=36, height=36, border_radius=0, bgcolor=ft.Colors.BLACK_38,
        alignment=ft.Alignment.CENTER, content=icon,
    )
    info = ft.Column([
        ft.Row([
            ft.Text(profile.get("name", "Unnamed Profile"), font_family="ps",
                    color="#ffffff", size=16, weight=ft.FontWeight.W_500,
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            *badges,
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Text(details, size=12, color="#747e95", max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS),
    ], spacing=2, expand=True)
    body = body_builder(icon_box, info) if body_builder else ft.Row(
        [icon_box, info], expand=True, spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return profile_card_surface(
        glow_color=glow_color, selected=selected, horizontal_padding=16,
        content=ft.Row([
            body,
            ft.Row(actions, spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
    )


def profile_drag_region(icon, info, *, color: str, view: ProfileList, profile_id, on_select=None):
    """Drag using the grip; select using the profile's icon and text."""
    body = ft.Row(
        [icon, info],
        expand=True,
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    if on_select is not None:
        body = ft.GestureDetector(
            key=ft.ValueKey(f"select-profile-{profile_id}"),
            data=profile_id,
            content=body,
            expand=True,
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap=on_select,
        )
    return ft.Row(
        [
            ft.GestureDetector(
                key=ft.ValueKey(f"drag-profile-{profile_id}"),
                data=profile_id,
                content=ft.Icon(ft.Icons.DRAG_INDICATOR, size=16, color=color),
                mouse_cursor=ft.MouseCursor.GRAB,
                tooltip="Drag to reorder",
                drag_interval=16,
                on_pan_start=view.start_pointer_drag,
                on_pan_update=view.update_pointer_drag,
                on_pan_end=view.end_pointer_drag,
                on_pan_cancel=lambda e: view.end_pointer_drag(e, cancel=True),
            ),
            body,
        ],
        expand=True,
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
