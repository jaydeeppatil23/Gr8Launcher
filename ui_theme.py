"""Shared launcher palette. Edit these variables, then restart the launcher.

BACKGROUND, ACCENT, GLOW, and TEXT are the main palette controls. Background
and active-switch shades below follow them; each named role can also be set
to its own color. Status and loader colors are deliberately separate roles.
"""

import flet as ft


def _shade(color: str, red: int, green: int, blue: int) -> str:
    """Offset RGB channels, preserving Classic's existing surface shades."""
    channels = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    return "#" + "".join(
        f"{max(0, min(255, channel + offset)):02x}"
        for channel, offset in zip(channels, (red, green, blue))
    )


# Main palette: start here when trying a different look.
BACKGROUND = "#182842"
ACCENT = "#f5de9a"
GLOW = "#454641"
TEXT = "#ffffff"
ON_ACCENT = BACKGROUND

# Text and icons. Separate roles preserve the current visual hierarchy.
MUTED = "#929db1"
NAV_MUTED = "#747e95"
TEXT_SECONDARY = "#d1d7e2"
TEXT_HEADING = "#e5ebf4"
TEXT_DETAIL = "#c5cedc"
TEXT_STATUS = "#d6dfea"
TEXT_CODE = "#d6e1f0"
TEXT_UPDATE = "#d7dce7"
TEXT_LOG = "#cfd8dc"
TEXT_SUPPORT = "#a0aec0"
TEXT_PLACEHOLDER = "#8b97ab"
TEXT_CAPTION = "#9ba3b4"
TEXT_FOOTNOTE = "#50617a"
ICON_MUTED = "#8a94a6"
ICON_CLOSE = "#bac6d8"
TEXT_DIM = ft.Colors.with_opacity(0.70, TEXT)
TEXT_HINT = ft.Colors.with_opacity(0.24, TEXT)

# Surfaces and effects. Transparent fills naturally show the page palette.
BLACK = "#000000"
TRANSPARENT = ft.Colors.TRANSPARENT
SURFACE = ft.Colors.BLACK_26
SURFACE_STRONG = ft.Colors.BLACK_38
SCRIM = ft.Colors.BLACK_54
AUTH_INACTIVE = _shade(BACKGROUND, -10, -12, -16)
SWITCH_TRACK = _shade(BACKGROUND, 19, 19, 19)
SWITCH_INACTIVE_TRACK = _shade(BACKGROUND, 14, 14, 13)
SWITCH_ACTIVE_TRACK = _shade(ACCENT, -143, -130, -90)
TOAST_BACKGROUND = _shade(BACKGROUND, 37, 43, 53)
QUOTE_BACKGROUND = _shade(BACKGROUND, 3, 3, 3)
CODE_BACKGROUND = _shade(BACKGROUND, -6, -10, -15)

# Feedback retains its meaning across palette experiments.
SUCCESS = "#81c784"
SUCCESS_BRIGHT = "#34d399"
DANGER = "#e57373"
ERROR = "#f87171"
WARNING = "#ffb74d"
WARNING_ACTIVE = "#ffa726"
INFO = "#64b5f6"
UPDATE = "#a7c9ff"
TOAST_WARNING = "#f5cf83"
TOAST_ERROR = "#ff9292"
LOG_ERROR = "#ef5350"
LOG_WARNING = "#ffca28"
LOG_INFO = "#90caf9"

# Minecraft loader badges.
LOADER_FABRIC = "#82b1ff"
LOADER_FORGE = WARNING
LOADER_NEOFORGE = "#ff8a65"
LOADER_QUILT = "#ce93d8"
LOADER_OTHER = "#b0bec5"

# Alpha masks control visibility rather than the visible theme palette.
MASK_OPAQUE = "#ffffffff"
MASK_TRANSPARENT = "#00ffffff"
