"""Cursor-driven head movement for the home-page statue."""

import io
import math
import time

from PIL import Image, ImageFilter

from skin_viewer import SkinViewer


class HomeSkinViewer(SkinViewer):
    # Reuse the single-worker frame scheduler, with head angles instead of camera angles.
    FRAME_INTERVAL = 1.0 / 30.0

    def __init__(self, page, display, is_active, *, enabled=True):
        super().__init__(page, display, is_active)
        self.enabled = enabled
        self.yaw = self.pitch = 0.0
        self._target = (0.0, 0.0)
        self._hovered = False

    def suspend(self):
        super().suspend()
        self.yaw = self.pitch = 0.0
        self._target = (0.0, 0.0)
        self._hovered = False
        self._last_frame = None

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.suspend()
        self.request_frame()

    def set_hovered(self, hovered):
        hovered = bool(hovered)
        if self._hovered == hovered:
            return
        self._hovered = hovered
        self._last_frame = None
        # Discard an in-flight frame with the old outline.
        self._generation += 1
        self.request_frame()

    def follow_cursor(self, dx, dy):
        if not self.enabled or not self.is_active():
            return
        # Keep the face within 25 degrees of the camera, accounting for the body angle.
        yaw = math.degrees(math.atan2(dx, 320.0)) * 0.45
        pitch = math.degrees(math.atan2(dy, 320.0)) * 0.45
        self._look_at(
            12.0 + max(-25.0, min(25.0, yaw)),
            max(-12.0, min(12.0, pitch)),
        )

    def reset_target(self):
        if self.enabled and self.is_active():
            self._look_at(0.0, 0.0)

    def _look_at(self, yaw, pitch):
        target = (round(yaw, 1), round(pitch, 1))
        if target == self._target:
            return
        self._target = target
        if self._mode is None:
            self._motion_tick = time.perf_counter()
        self._mode = "turn"
        # Coalesce cursor events without invalidating a render already in flight.
        self.request_frame()

    def _advance(self, now, elapsed):
        if self._mode != "turn":
            return
        yaw, pitch = self._target
        weight = 1.0 - math.exp(-14.0 * elapsed)
        self.yaw += (yaw - self.yaw) * weight
        self.pitch += (pitch - self.pitch) * weight
        if max(abs(yaw - self.yaw), abs(pitch - self.pitch)) < 0.15:
            self.yaw, self.pitch = yaw, pitch
            self._mode = None
        self._dirty = True

    def _render(self, renderer, yaw, pitch):
        rendered = renderer.render(
            -12.0, 0.0, width=170, height=230, scale=6.2,
            head_yaw_deg=yaw, head_pitch_deg=pitch,
        )
        if self._hovered:
            alpha = rendered.getchannel("A").filter(ImageFilter.MaxFilter(5))
            outline = Image.new("RGBA", rendered.size, (255, 255, 255, 0))
            outline.putalpha(alpha)
            rendered = Image.alpha_composite(outline, rendered)
        buf = io.BytesIO()
        rendered.save(buf, format="PNG", compress_level=1)
        return buf.getvalue()
