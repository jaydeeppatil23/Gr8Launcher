"""Paced, on-demand motion for the Flet software skin viewer."""

import asyncio
import math
import time


class SkinViewer:
    FRAME_INTERVAL = 1.0 / 60.0
    INERTIA_DECAY = 8.0
    STOP_SPEED = 5.0  # End the glide before sub-pixel steps produce a crawling tail.
    TURN_DURATION = 0.28

    def __init__(self, page, display, is_active):
        self.page = page
        self.display = display
        self.is_active = is_active
        self.renderer = None
        self.yaw, self.pitch = -25.0, 10.0
        self._velocity = (0.0, 0.0)
        self._mode = None
        self._dirty = False
        self._running = False
        self._generation = 0
        self._last_drag = None
        self._last_frame = None
        self._turn = None
        self._next_frame = 0.0
        self._motion_tick = time.perf_counter()

    def set_renderer(self, renderer):
        self.suspend()
        self.renderer = renderer
        self._last_frame = None
        self._dirty = True

    def suspend(self):
        # An in-flight worker may finish, but its result will not be published.
        self._generation += 1
        self._mode = None
        self._velocity = (0.0, 0.0)
        self._last_drag = None
        self._dirty = False

    def _wake(self):
        has_work = self._dirty or self._mode in ("coast", "turn")
        if has_work and not self._running and self.renderer is not None and self.is_active():
            self._running = True
            self.page.run_task(self._run)

    def request_frame(self):
        self._dirty = True
        self._wake()

    def render_still(self):
        if self.renderer is None:
            return b""
        key = (id(self.renderer), self.yaw, self.pitch)
        if self._last_frame is None or self._last_frame[0] != key:
            self._last_frame = (key, self._render(self.renderer, self.yaw, self.pitch))
        self._dirty = False
        return self._last_frame[1]

    @staticmethod
    def _render(renderer, yaw, pitch):
        return renderer.render_bytes(
            yaw, pitch, width=250, height=350, scale=8.8
        )

    def begin_drag(self):
        self.suspend()
        self._mode = "drag"
        self._last_drag = time.perf_counter()
        self._motion_tick = self._last_drag

    def drag(self, dx, dy):
        if self._mode != "drag":
            return
        now = time.perf_counter()
        elapsed = max(1.0 / 240.0, min(0.1, now - self._last_drag))
        self._last_drag = now
        yaw_delta, pitch_delta = dx * 0.70, dy * 0.45
        self.yaw = (self.yaw + yaw_delta) % 360.0
        old_pitch = self.pitch
        self.pitch = max(-35.0, min(35.0, old_pitch + pitch_delta))
        weight = 1.0 - math.exp(-elapsed / 0.045)
        vy, vp = self._velocity
        self._velocity = (
            vy + weight * (yaw_delta / elapsed - vy),
            vp + weight * ((self.pitch - old_pitch) / elapsed - vp),
        )
        if yaw_delta or self.pitch != old_pitch:
            self.request_frame()

    def end_drag(self):
        if self._mode != "drag":
            return
        # Holding still before releasing should not restart an old fling.
        if self._last_drag is None or time.perf_counter() - self._last_drag > 0.12:
            self._velocity = (0.0, 0.0)
        self._mode = "coast" if max(map(abs, self._velocity)) > self.STOP_SPEED else None
        # Inertia starts at release, even if a drag frame is still being rendered.
        self._motion_tick = time.perf_counter()
        self._wake()

    def turn_to(self, yaw, pitch):
        self.suspend()
        self._turn = (
            time.perf_counter(), self.yaw, self.pitch,
            (yaw - self.yaw + 180.0) % 360.0 - 180.0,
            max(-35.0, min(35.0, pitch)) - self.pitch,
        )
        self._mode = "turn"
        self.request_frame()

    def _advance(self, now, elapsed):
        if self._mode == "turn":
            started, yaw, pitch, yaw_delta, pitch_delta = self._turn
            progress = min(1.0, (now - started) / self.TURN_DURATION)
            eased = 1.0 - (1.0 - progress) ** 3
            self.yaw = (yaw + yaw_delta * eased) % 360.0
            self.pitch = pitch + pitch_delta * eased
            if progress == 1.0:
                self._mode = None
            self._dirty = True
        elif self._mode == "coast":
            vy, vp = self._velocity
            decay = math.exp(-self.INERTIA_DECAY * elapsed)
            travel = (1.0 - decay) / self.INERTIA_DECAY
            self.yaw = (self.yaw + vy * travel) % 360.0
            pitch = self.pitch + vp * travel
            self.pitch = max(-35.0, min(35.0, pitch))
            self._velocity = (vy * decay, vp * decay if pitch == self.pitch else 0.0)
            if max(map(abs, self._velocity)) <= self.STOP_SPEED:
                self._mode = None
            self._dirty = True

    @staticmethod
    def _wait_until(deadline):
        remaining = deadline - time.perf_counter()
        if remaining > 0:
            # Python's thread sleep uses a precise waitable timer on Windows.
            # asyncio's event-loop timer can round a short wait up by a tick.
            time.sleep(remaining)

    def _render_timed(self, renderer, yaw, pitch):
        started = time.perf_counter()
        return started, self._render(renderer, yaw, pitch)

    async def _run(self):
        try:
            while self.is_active() and self.renderer is not None:
                if not self._dirty and self._mode not in ("coast", "turn"):
                    break
                deadline = max(self._next_frame, time.perf_counter())
                if deadline > time.perf_counter():
                    await asyncio.to_thread(self._wait_until, deadline)
                if not self.is_active():
                    break
                started = time.perf_counter()
                self._advance(started, max(0.0, started - self._motion_tick))
                self._motion_tick = started
                # Anchor to the deadline so timer overshoot does not accumulate.
                # Late frames start a new schedule instead of building a backlog.
                self._next_frame = deadline + self.FRAME_INTERVAL
                if not self._dirty:
                    continue
                self._dirty = False
                generation = self._generation
                renderer, yaw, pitch = self.renderer, self.yaw, self.pitch
                # Only one render is in flight; drag events update the next pose.
                rendered_at, frame = await asyncio.to_thread(
                    self._render_timed, renderer, yaw, pitch
                )
                # A late worker must not be followed by a compressed catch-up frame.
                self._next_frame = max(
                    self._next_frame, rendered_at + self.FRAME_INTERVAL * 0.96
                )
                if generation != self._generation or not self.is_active():
                    continue
                self._last_frame = ((id(renderer), yaw, pitch), frame)
                self.display.src = frame
                try:
                    self.display.update()
                except RuntimeError:
                    self.suspend()
                    break
        finally:
            self._running = False
