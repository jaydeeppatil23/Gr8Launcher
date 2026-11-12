# Trying launcher palettes

The launcher's colors live in `ui_theme.py`. Edit the variables there, then
fully close and restart `python main.py` to see the new palette.

Start with these main variables:

| Variable | Controls |
| --- | --- |
| `BACKGROUND` | Page background, dialogs, menus, and related surface shades |
| `ACCENT` | Primary buttons, headings, selected navigation icons, and progress |
| `GLOW` | Roaming background glow and selected card highlights |
| `TEXT` | Main body text, input text, and the skin hover outline |
| `ON_ACCENT` | Text and icons on accent buttons; follows `BACKGROUND` by default |
| `MUTED` / `NAV_MUTED` | Secondary details and inactive navigation |

Use six-digit RGB hex colors for `BACKGROUND`, `ACCENT`, and `TEXT`. The derived
shades and translucent text depend on those values. For example, try replacing
the main background and accent with deep purple and pale lime:

```python
BACKGROUND = "#291c3d"
ACCENT = "#e5f2a5"
```

The remaining variables expose individual text, surface, notification, status,
and loader-badge colors. You can override a derived shade with a hex color if
you want to tune it separately. Keep error and warning colors distinct and
check text on both the page background and acrylic surfaces.

Bundled navigation and fallback UI icons follow the palette through tinting.
Minecraft artwork, skins, the brand logo, and illustrated promotional images
retain their original colors.

Palette changes apply on startup. There is no theme chooser or automatic live
reload in this change.
