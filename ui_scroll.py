"""Smooth discrete mouse wheel input across the desktop client's native scrollables.

Synchronizes with the Windows Desktop Window Manager (DwmFlush) for true hardware
V-Sync cadence across 60Hz, 120Hz, 144Hz, and high-refresh displays. Keeps hit
testing, nested scrolling, menus, and lazy lists in Flutter without rebuilding
controls or sending animation frames over Flet's RPC.
"""

from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes as wt
import logging
import math
import os
import threading
import time


logger = logging.getLogger(__name__)

WHEEL_DELTA = 120
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WM_QUIT = 0x0012

# Natural time constant for smooth scrolling (~240ms duration)
SMOOTH_TAU = 0.065


class _SmoothMotion:
    """Sub-pixel exponential ease-out accumulator with remainder carry-over."""

    def __init__(self, tau: float = SMOOTH_TAU) -> None:
        self.tau = tau
        self.pending = 0.0
        self.remainder = 0.0
        self.last_time = 0.0

    @property
    def is_active(self) -> bool:
        return abs(self.pending + self.remainder) >= 1.0

    def add(self, delta: float, now: float) -> None:
        # Reversing direction immediately redirects without fighting opposite inertia
        if self.pending * delta < 0:
            self.pending = 0.0
            self.remainder = 0.0
        self.pending += delta
        if self.last_time == 0.0:
            self.last_time = now

    def step(self, now: float) -> int:
        total_remaining = self.pending + self.remainder
        if abs(total_remaining) < 1.0:
            final_step = round(total_remaining)
            self.pending = 0.0
            self.remainder = 0.0
            self.last_time = 0.0
            return final_step

        dt = max(0.001, min(0.1, now - self.last_time))
        self.last_time = now
        fraction = 1.0 - math.exp(-dt / self.tau)
        delta_to_apply = self.pending * fraction + self.remainder

        # Finish cleanly without microscopic 0-step tails
        if abs(total_remaining) <= 3.0:
            int_step = round(total_remaining)
            self.pending = 0.0
            self.remainder = 0.0
            self.last_time = 0.0
            return int_step

        int_step = int(delta_to_apply)
        if int_step == 0 and abs(delta_to_apply) >= 0.5:
            int_step = 1 if delta_to_apply > 0 else -1

        self.remainder = delta_to_apply - int_step
        self.pending -= int_step
        return max(-32767, min(32767, int_step))

    def cancel(self) -> None:
        self.pending = 0.0
        self.remainder = 0.0
        self.last_time = 0.0


class SmoothWheel:
    """Scope native input smoothing to this launcher's Flet window with V-Sync cadence."""

    def __init__(self, title: str) -> None:
        self.title = title
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._hook_thread: threading.Thread | None = None
        self._anim_thread: threading.Thread | None = None
        self._hook_thread_id = 0
        self._api = None
        self._winmm = None

    def start(self) -> None:
        if os.name != "nt" or self._hook_thread is not None or self._stop.is_set():
            return
        self._hook_thread = threading.Thread(target=self._run_hook, name="smooth-wheel-hook", daemon=True)
        self._anim_thread = threading.Thread(target=self._run_anim, name="smooth-wheel-anim", daemon=True)
        self._hook_thread.start()
        self._anim_thread.start()
        atexit.register(self.close)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._api is not None and self._hook_thread_id:
            self._api.PostThreadMessageW(self._hook_thread_id, WM_QUIT, 0, 0)
        if self._hook_thread is not None and self._hook_thread is not threading.current_thread():
            self._hook_thread.join(timeout=0.2)
        if self._anim_thread is not None and self._anim_thread is not threading.current_thread():
            self._anim_thread.join(timeout=0.2)
        if self._winmm is not None:
            try:
                self._winmm.timeEndPeriod(1)
            except Exception:
                pass
            self._winmm = None
        atexit.unregister(self.close)

    def _run_hook(self) -> None:
        hook = None
        try:
            api, kernel, winmm, dwmapi, hook_type, mouse_type, enum_type = _windows_api()
            self._api = api
            self._winmm = winmm
            self._hook_thread_id = threading.get_native_id()

            # Set 1ms timer precision on Windows for ultra-low latency wakeups
            winmm.timeBeginPeriod(1)

            message = wt.MSG()
            api.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)

            root = None
            while not self._stop.is_set() and root is None:
                root = _find_client_window(api, kernel, enum_type, self.title)
                if root is None:
                    self._stop.wait(0.25)
            if root is None or self._stop.is_set():
                return

            self._root = root
            self._dwmapi = dwmapi
            self._motion = _SmoothMotion()
            self._lock = threading.Lock()
            self._target = None
            self._axis = WM_MOUSEWHEEL
            self._anchor = [0, 0]
            self._modifiers = 0

            def window_at(point: wt.POINT):
                window = api.WindowFromPoint(point)
                if not window or api.GetAncestor(window, 2) != root:
                    return None
                return window

            def blocked() -> bool:
                # Ctrl/Alt zoom or mouse drags: leave native
                return any(api.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0x12, 1, 2, 4, 5, 6))

            @hook_type
            def handle(code, kind, data):
                try:
                    if code >= 0 and kind in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                        event = ctypes.cast(data, ctypes.POINTER(mouse_type)).contents
                        delta = ctypes.c_short(event.mouseData >> 16).value
                        window = window_at(event.pt)

                        # Smooth standard discrete wheel notches over our launcher window
                        if window and delta and delta % WHEEL_DELTA == 0 and not blocked():
                            shift = 4 if api.GetAsyncKeyState(0x10) & 0x8000 else 0
                            with self._lock:
                                if window != self._target or kind != self._axis or shift != self._modifiers:
                                    self._motion.cancel()
                                self._target = window
                                self._axis = kind
                                self._anchor[0], self._anchor[1] = event.pt.x, event.pt.y
                                self._modifiers = shift
                                self._motion.add(delta, time.monotonic())
                            self._wake.set()
                            return 1  # Swallowed native notch; animation thread will replay smoothly
                except Exception:
                    logger.exception("Hook callback encountered an error")
                return api.CallNextHookEx(hook, code, kind, data)

            hook = api.SetWindowsHookExW(14, handle, kernel.GetModuleHandleW(None), 0)
            if not hook:
                raise ctypes.WinError(ctypes.get_last_error())

            while not self._stop.is_set():
                if api.GetMessageW(ctypes.byref(message), None, 0, 0) <= 0:
                    break
                api.TranslateMessage(ctypes.byref(message))
                api.DispatchMessageW(ctypes.byref(message))

        except Exception:
            logger.exception("Smooth wheel hook could not start; native scrolling remains active")
        finally:
            if hook and self._api:
                self._api.UnhookWindowsHookEx(hook)
            self._hook_thread_id = 0

    def _run_anim(self) -> None:
        try:
            api, kernel, winmm, dwmapi, hook_type, mouse_type, enum_type = _windows_api()

            # Wait until hook thread discovers root launcher window
            while not self._stop.is_set() and not hasattr(self, "_root"):
                self._stop.wait(0.1)

            if self._stop.is_set():
                return

            root = self._root

            def window_at(point: wt.POINT):
                window = api.WindowFromPoint(point)
                if not window or api.GetAncestor(window, 2) != root:
                    return None
                return window

            def blocked() -> bool:
                return any(api.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0x12, 1, 2, 4, 5, 6))

            dwm_available = hasattr(dwmapi, "DwmFlush")

            while not self._stop.is_set():
                if not self._wake.wait(timeout=0.2):
                    continue

                while not self._stop.is_set():
                    is_active = False
                    with self._lock:
                        is_active = self._motion.is_active

                    if not is_active:
                        self._wake.clear()
                        break

                    point = wt.POINT()
                    api.GetCursorPos(ctypes.byref(point))
                    curr_win = window_at(point)

                    if curr_win != self._target or blocked() or not api.IsWindow(root):
                        with self._lock:
                            self._motion.cancel()
                        self._wake.clear()
                        break

                    # Update cursor anchor to follow hand movement smoothly
                    self._anchor[0], self._anchor[1] = point.x, point.y

                    step = 0
                    with self._lock:
                        step = self._motion.step(time.monotonic())

                    if step:
                        api.PostMessageW(
                            self._target,
                            self._axis,
                            _wheel_wparam(step, self._modifiers),
                            _point_lparam(self._anchor[0], self._anchor[1]),
                        )

                    # Hardware V-Sync: blocks until the monitor's next vertical refresh
                    if dwm_available:
                        try:
                            if dwmapi.DwmFlush() != 0:
                                time.sleep(0.008)
                        except Exception:
                            time.sleep(0.008)
                    else:
                        time.sleep(0.008)

        except Exception:
            logger.exception("Smooth wheel animation thread encountered an error")


def _wheel_wparam(delta: int, modifiers: int = 0) -> int:
    return ((delta & 0xFFFF) << 16) | (modifiers & 0xFFFF)


def _point_lparam(x: int, y: int) -> int:
    return (x & 0xFFFF) | ((y & 0xFFFF) << 16)


def _windows_api():
    """Declare pointer-sized Win32 signatures without truncating 64-bit handles."""
    api = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    winmm = ctypes.WinDLL("winmm", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

    hook_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
    enum_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    class Mouse(ctypes.Structure):
        _fields_ = [
            ("pt", wt.POINT),
            ("mouseData", wt.DWORD),
            ("flags", wt.DWORD),
            ("time", wt.DWORD),
            ("extra", ctypes.c_size_t),
        ]

    signatures = {
        "SetWindowsHookExW": ([ctypes.c_int, hook_type, wt.HINSTANCE, wt.DWORD], wt.HANDLE),
        "UnhookWindowsHookEx": ([wt.HANDLE], wt.BOOL),
        "CallNextHookEx": ([wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM], ctypes.c_ssize_t),
        "PostMessageW": ([wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL),
        "PostThreadMessageW": ([wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL),
        "PeekMessageW": ([ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT], wt.BOOL),
        "GetMessageW": ([ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT], ctypes.c_int),
        "TranslateMessage": ([ctypes.POINTER(wt.MSG)], wt.BOOL),
        "DispatchMessageW": ([ctypes.POINTER(wt.MSG)], ctypes.c_ssize_t),
        "WindowFromPoint": ([wt.POINT], wt.HWND),
        "GetAncestor": ([wt.HWND, wt.UINT], wt.HWND),
        "GetCursorPos": ([ctypes.POINTER(wt.POINT)], wt.BOOL),
        "GetAsyncKeyState": ([ctypes.c_int], wt.SHORT),
        "IsWindow": ([wt.HWND], wt.BOOL),
        "EnumWindows": ([enum_type, wt.LPARAM], wt.BOOL),
        "GetWindowTextW": ([wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
        "GetClassNameW": ([wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
        "GetWindowThreadProcessId": ([wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
    }
    for name, (args, result) in signatures.items():
        method = getattr(api, name)
        method.argtypes, method.restype = args, result

    kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [wt.LPCWSTR], wt.HMODULE
    kernel.CreateToolhelp32Snapshot.argtypes, kernel.CreateToolhelp32Snapshot.restype = [wt.DWORD, wt.DWORD], wt.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [wt.HANDLE], wt.BOOL
    dwmapi.DwmFlush.restype = ctypes.c_long
    winmm.timeBeginPeriod.argtypes, winmm.timeBeginPeriod.restype = [wt.UINT], wt.UINT
    winmm.timeEndPeriod.argtypes, winmm.timeEndPeriod.restype = [wt.UINT], wt.UINT

    return api, kernel, winmm, dwmapi, hook_type, Mouse, enum_type


def _find_client_window(api, kernel, enum_type, title: str):
    """Discover only the desktop process started by this Python launcher."""
    class Process(ctypes.Structure):
        _fields_ = [
            ("size", wt.DWORD),
            ("usage", wt.DWORD),
            ("pid", wt.DWORD),
            ("heap", ctypes.c_size_t),
            ("module", wt.DWORD),
            ("threads", wt.DWORD),
            ("parent", wt.DWORD),
            ("priority", wt.LONG),
            ("flags", wt.DWORD),
            ("exe", wt.WCHAR * 260),
        ]

    kernel.Process32FirstW.argtypes, kernel.Process32FirstW.restype = [wt.HANDLE, ctypes.POINTER(Process)], wt.BOOL
    kernel.Process32NextW.argtypes, kernel.Process32NextW.restype = [wt.HANDLE, ctypes.POINTER(Process)], wt.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return None
    own_pid = os.getpid()
    client_pids = {own_pid}
    try:
        entry = Process()
        entry.size = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.parent == own_pid:
                client_pids.add(entry.pid)
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    root = None

    @enum_type
    def visit(window, data):
        nonlocal root
        pid = wt.DWORD()
        api.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value in client_pids:
            name = ctypes.create_unicode_buffer(256)
            api.GetWindowTextW(window, name, len(name))
            if name.value == title:
                api.GetClassNameW(window, name, len(name))
                if name.value == "FLUTTER_RUNNER_WIN32_WINDOW":
                    root = window
                    return False
        return True

    api.EnumWindows(visit, 0)
    return root
