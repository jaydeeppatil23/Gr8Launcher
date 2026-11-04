"""Profile dialogs scoped to the launcher body, leaving window controls usable."""

import asyncio
from dataclasses import fields

import flet as ft


class ProfileDialogHost(ft.Container):
    MARGIN = 20
    ANIMATION_MS = 180
    MOUNT_DELAY = 0.032

    def __init__(self, body: ft.Container, *, title_bar_height=40):
        super().__init__(
            left=0, top=title_bar_height, right=0, bottom=0,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            # The full-size host must pass clicks through while no dialog is open.
            ignore_interactions=True,
            visible=False,
            # Measure inside the positioned container. Flet's size listener
            # must not sit between a Positioned widget and its parent Stack.
            content=ft.Stack(expand=True, on_size_change=self._resize),
        )
        self._body = body
        self._disabled_states = {}
        self._panel = None
        self._on_cancel = None
        self._generation = 0
        self._closing = False
        self._available_width = 1100
        self._available_height = 650 - title_bar_height

    @property
    def controls(self):
        return self.content.controls

    @controls.setter
    def controls(self, controls):
        self.content.controls = controls

    def show(self, content, *, title, actions, on_cancel, width=508, height=584):
        # Disabling the body blocks keyboard focus as well as pointer input.
        # Only the body is disabled; the custom title bar stays interactive.
        self._disable_body()
        self._generation += 1
        self._closing = False
        self._on_cancel = on_cancel
        self._preferred_width = width
        self._preferred_height = height
        self._panel = ft.Container(
            bgcolor="#182842",
            border_radius=8,
            padding=24,
            opacity=0,
            scale=0.98,
            animate_opacity=ft.Animation(self.ANIMATION_MS, ft.AnimationCurve.EASE_OUT_CUBIC),
            animate_scale=ft.Animation(self.ANIMATION_MS, ft.AnimationCurve.EASE_OUT_CUBIC),
            # Absorb taps on the panel itself; only the surrounding area dismisses it.
            on_click=lambda e: None,
            shadow=ft.BoxShadow(
                blur_radius=18, spread_radius=0,
                color=ft.Colors.BLACK_26, offset=ft.Offset(0, 6),
            ),
            content=ft.Column(
                spacing=16,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    title,
                    ft.Column(
                        controls=[content],
                        expand=True,
                        spacing=0,
                        scroll=ft.ScrollMode.AUTO,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    ft.Row(
                        controls=actions,
                        spacing=8,
                        alignment=ft.MainAxisAlignment.END,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
            ),
        )
        self._fit_panel()
        self._backdrop = ft.Container(
            left=0, top=0, right=0, bottom=0,
            # Clip the filter to the body so the title bar stays sharp.
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            blur=ft.Blur(6, 6),
            bgcolor=ft.Colors.with_opacity(0.28, ft.Colors.BLACK),
            opacity=0,
            animate_opacity=ft.Animation(self.ANIMATION_MS, ft.AnimationCurve.EASE_OUT_CUBIC),
            on_click=self._dismiss,
        )
        self.controls = [
            self._backdrop,
            ft.Container(
                left=0, top=0, right=0, bottom=0,
                padding=self.MARGIN,
                alignment=ft.Alignment.CENTER,
                content=self._panel,
                on_click=self._dismiss,
            ),
        ]
        self.ignore_interactions = False
        self.visible = True
        self.page.update()
        self.page.run_task(self._play_open, self._generation)

    def close(self):
        if self._panel is None or self._closing:
            return
        self._generation += 1
        self._closing = True
        self._on_cancel = None
        # Keep background controls blocked until the fading panel is removed.
        self._panel.ignore_interactions = True
        self._panel.opacity = 0
        self._panel.scale = 0.98
        self._backdrop.opacity = 0
        self.page.update()
        self.page.run_task(self._play_close, self._generation)

    async def _play_open(self, generation):
        # Mount the initial hidden state before starting the transition.
        await asyncio.sleep(self.MOUNT_DELAY)
        if generation != self._generation or self._panel is None:
            return
        self._panel.opacity = 1
        self._panel.scale = 1
        self._backdrop.opacity = 1
        self.page.update()

    async def _play_close(self, generation):
        await asyncio.sleep(self.ANIMATION_MS / 1000)
        if generation != self._generation:
            return
        self._finish_close()

    def _finish_close(self):
        # Background refreshes may have mounted new controls while open.
        # Give them explicit disabled flags before restoring their own state.
        if self._disable_body():
            self.page.update()
        for control, was_disabled in self._disabled_states.values():
            control.disabled = was_disabled
        self._disabled_states.clear()
        self.controls = []
        self.ignore_interactions = True
        self.visible = False
        self._panel = None
        self._closing = False
        self._on_cancel = None
        self.page.update()

    def _dismiss(self, e):
        if self._on_cancel is not None and not self._closing:
            self._on_cancel(e)

    def _disable_body(self):
        """Remember each control's own state and avoid stale inherited flags.

        Flet does not consistently rebuild descendant buttons when only their
        parent's disabled flag changes. Explicit flags also restore their
        callbacks when the modal closes, preserving already disabled controls.
        """
        changed = False
        pending = [self._body]
        visited = set()
        while pending:
            control = pending.pop()
            if control._i in visited:
                continue
            visited.add(control._i)
            if control._i not in self._disabled_states:
                self._disabled_states[control._i] = (control, control.disabled)
                if not control.disabled:
                    control.disabled = True
                    changed = True
            for field in fields(control):
                if field.name.startswith("_") or field.metadata.get("skip", False):
                    continue
                value = getattr(control, field.name)
                if isinstance(value, ft.Control):
                    pending.append(value)
                elif isinstance(value, (list, tuple)):
                    pending.extend(child for child in value if isinstance(child, ft.Control))
        return changed

    def handle_keyboard_event(self, e: ft.KeyboardEvent):
        if e.key == "Escape":
            self._dismiss(e)

    def _fit_panel(self):
        self._panel.width = max(0, min(
            self._preferred_width, self._available_width - 2 * self.MARGIN,
        ))
        self._panel.height = max(0, min(
            self._preferred_height, self._available_height - 2 * self.MARGIN,
        ))

    def _resize(self, e):
        self._available_width = e.width
        self._available_height = e.height
        if self._panel is not None:
            self._fit_panel()
            self._panel.update()
