"""Short, interruptible entrance animations for tab content."""

import asyncio
from concurrent.futures import Future
from dataclasses import dataclass

import flet as ft


@dataclass
class _RevealItem:
    control: ft.LayoutControl
    opacity: float
    offset: ft.Offset | tuple | None
    animate_opacity: object
    animate_offset: object

    def reveal(self):
        self.control.opacity = self.opacity
        self.control.offset = self.offset or ft.Offset(0, 0)

    def restore(self):
        self.control.opacity = self.opacity
        self.control.offset = self.offset
        self.control.animate_opacity = self.animate_opacity
        self.control.animate_offset = self.animate_offset


class StaggeredReveal:
    """Mount immediately, then reveal sections in a small number of waves.

    A new selection restores the previous controls and invalidates its task,
    so cached tabs never retain hidden cards after interrupted navigation.
    """

    FADE_MS = 220
    SLIDE_MS = 280
    MOUNT_DELAY = 0.032
    STAGGER_DELAY = 0.035
    MAX_WAVES = 5

    def __init__(self, page: ft.Page, host: ft.Container):
        self.page = page
        self.host = host
        self.generation = 0
        self._items: list[_RevealItem] = []
        self._completion: Future = Future()
        self._completion.set_result(None)

    async def wait_until_finished(self) -> None:
        """Let data work run during an entrance, then commit after it settles."""
        while not self._completion.done():
            # Navigation can finish a reveal from Flet's event worker thread.
            # Shield the shared future from cancellation of any one waiter.
            await asyncio.shield(asyncio.wrap_future(self._completion))

    def finish(self, *, update=True):
        self.generation += 1
        had_items = bool(self._items)
        for item in self._items:
            item.restore()
        self._items.clear()
        if not self._completion.done():
            self._completion.set_result(None)
        if update and had_items:
            self._update()

    def _update(self):
        try:
            self.host.update()
            return True
        except RuntimeError:
            # Rebuilds can happen before mounting or after leaving a page.
            return False

    def show(self, content: ft.Control, controls=(), *, animate=True):
        self.finish(update=False)
        self.host.content = content
        if animate:
            seen = set()
            for control in controls:
                if not control.visible or id(control) in seen:
                    continue
                seen.add(id(control))
                self._items.append(
                    _RevealItem(
                        control, control.opacity, control.offset,
                        control.animate_opacity, control.animate_offset,
                    )
                )
                control.animate_opacity = ft.Animation(
                    self.FADE_MS, ft.AnimationCurve.EASE_OUT_CUBIC
                )
                control.animate_offset = ft.Animation(
                    self.SLIDE_MS, ft.AnimationCurve.EASE_OUT_CUBIC
                )
                control.opacity = 0.0
                control.offset = ft.Offset(0, 0.035)

        if self._items:
            self._completion = Future()
        if not self._update():
            self.finish(update=False)
            return
        if self._items:
            self.page.run_task(self._play, self.generation)

    async def _play(self, generation: int):
        # Let the client mount the initial hidden state before revealing it.
        await asyncio.sleep(self.MOUNT_DELAY)
        if generation != self.generation:
            return

        waves = [[] for _ in range(min(len(self._items), self.MAX_WAVES))]
        for index, item in enumerate(self._items):
            waves[min(index, self.MAX_WAVES - 1)].append(item)

        for index, wave in enumerate(waves):
            if index:
                await asyncio.sleep(self.STAGGER_DELAY)
            if generation != self.generation:
                return
            for item in wave:
                item.reveal()
            if not self._update():
                self.finish(update=False)
                return

        await asyncio.sleep(self.SLIDE_MS / 1000)
        if generation == self.generation:
            self.finish()
