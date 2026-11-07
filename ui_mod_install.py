"""Compact mod install review with explicit required and optional dependencies."""

from __future__ import annotations

from typing import Callable

import flet as ft

from mod_services import InstallPlan, ModUpdate, primary_file
from ui_controls import ACCENT, rounded_artwork


MUTED = "#929db1"
NAVY = "#182842"


def _text(value: str, size: int = 13, *, color: str = MUTED, **kwargs) -> ft.Text:
    return ft.Text(value, size=size, color=color, **kwargs)


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    return f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"


class ModInstallReview(ft.Column):
    def __init__(self, hit: dict, profile: dict, plan: InstallPlan, *,
                 profile_icon: ft.Control, on_optional_change: Callable, updates: tuple[ModUpdate, ...] = ()):
        super().__init__(spacing=16, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.hit = hit
        self.profile = profile
        self.plan = plan
        self.updates = updates
        self.on_optional_change = on_optional_change
        self.optional_projects: set[str] = set()
        self.checkboxes: dict[str, ft.Checkbox] = {}
        self.checking = False
        self.error = ""
        fallback = ft.Image(src="sidebar/mods.png", width=32, height=32, fit=ft.BoxFit.CONTAIN)
        artwork = ft.Image(src=hit["icon_url"], width=48, height=48, fit=ft.BoxFit.CONTAIN,
                           cache_width=96, cache_height=96, error_content=fallback) if hit.get("icon_url") else fallback
        self.title = ft.Row([
            rounded_artwork(artwork, size=48),
            ft.Column([
                ft.Text(f"Update {len(updates)} {'mod' if len(updates) == 1 else 'mods'}" if updates else f"Install {hit.get('title', 'mod')}", font_family="ps", size=22, color=ACCENT,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                _text("Review your updates" if updates else "Review your installation", 12),
            ], spacing=2, expand=True),
        ], spacing=14)
        self.destination = ft.Container(
            bgcolor=ft.Colors.BLACK_26, border_radius=5, padding=12,
            content=ft.Row([
                rounded_artwork(profile_icon, size=32),
                ft.Column([
                    _text(profile.get("name", "This profile"), 15, color=ft.Colors.WHITE,
                          max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    _text(f"Minecraft {profile['minecraft_version']}  /  {profile['loader']}", 12),
                ], spacing=2, expand=True),
                _text("Install to", 12),
            ], spacing=12),
        )
        self.render()

    @property
    def preferred_height(self) -> int:
        # Fit short reviews to their content; the shared host bounds longer ones.
        return min(550, 296 + (48 + 64 * len(self.plan.dependencies) if self.plan.dependencies else 0 if self.updates else 38)
                   + (40 if self.plan.replacements else 0)
                   + (26 if self.plan.disabled_projects else 0)
                   + (26 if self.plan.dependencies and all(item.required for item in self.plan.dependencies) else 0)
                   + (24 if len(self.hit.get("title", "")) > 35 else 0)
                   + (24 + 56 * len(self.updates) if self.updates else 0))

    def render(self) -> None:
        plan = self.plan
        root = plan.root_version or plan.versions[-1]
        sizes = [primary_file(item).get("size") for item in plan.versions]
        total = _size(sum(sizes)) if all(isinstance(size, int) and size >= 0 for size in sizes) else "Size unavailable"
        count = len(plan.versions)
        self.controls = [
            self.destination,
            ft.Row([
                ft.Column([
                    _text("Mod updates" if self.updates else "Selected version", 12),
                    _text(f"{len(self.updates)} selected" if self.updates else root.get("version_number") or root.get("name", "Unknown"), 15, color=ACCENT,
                          max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, tooltip=root.get("name")),
                ], spacing=4, expand=True),
                ft.Column([
                    _text(f"{count} {'download' if count == 1 else 'downloads'}", 12),
                    _text(total, 15, color=ft.Colors.WHITE),
                ], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.END),
            ], spacing=16),
        ]
        if self.updates:
            targets = {version["project_id"]: version for version in plan.root_versions}
            self.controls.append(ft.Container(
                bgcolor=ft.Colors.BLACK_26, border_radius=5, padding=12,
                content=ft.Column([
                    ft.Column([
                        _text(update.mod.name, 14, color=ft.Colors.WHITE, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        _text(f"{update.mod.version}  to  {targets.get(update.mod.project_id, update.version).get('version_number', '')}"
                              + ("  /  Disabled" if not update.mod.enabled else ""), 12),
                    ], spacing=3) for update in self.updates
                ], spacing=14),
            ))
        self.checkboxes = {}
        if plan.dependencies:
            rows = []
            for item in sorted(plan.dependencies, key=lambda dependency: (not dependency.required, dependency.name.casefold())):
                status = "Installed" if item.installed else "Required" if item.required else "Optional"
                if item.required or item.installed:
                    leading = ft.Container(
                        ft.Icon(ft.Icons.LOCK_SHARP, size=18, color=MUTED if item.installed else ACCENT,
                                semantics_label=f"{status} dependency: {item.name}"),
                        width=36, height=40, alignment=ft.Alignment.CENTER,
                    )
                else:
                    leading = ft.Checkbox(
                        value=item.project_id in self.optional_projects, disabled=self.checking or bool(item.issue),
                        width=36, height=40, active_color=ACCENT, check_color=NAVY,
                        fill_color={ft.ControlState.SELECTED: ACCENT},
                        overlay_color=ft.Colors.TRANSPARENT, mouse_cursor=ft.MouseCursor.CLICK,
                        semantics_label=f"Optional dependency: {item.name}",
                        on_change=self._optional_handler(item.project_id),
                    )
                    self.checkboxes[item.project_id] = leading
                version = (item.version or {}).get("version_number", "")
                detail = item.issue or (f"{version}  /  Bundled" if item.bundled else version if item.installed
                                        else f"{version}  /  {_size(primary_file(item.version).get('size', 0))}" if item.version else "Version unavailable")
                rows.append(ft.Row([
                    leading,
                    ft.Column([
                        _text(item.name, 14, color=ft.Colors.WHITE, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        _text(detail, 12, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    ], spacing=3, expand=True),
                    _text(status, 11, color=ACCENT if status == "Required" else MUTED),
                ], spacing=8))
            self.controls.extend([
                ft.Text("Dependencies", font_family="ps", size=17, color=ACCENT),
                ft.Container(ft.Column(rows, spacing=10), bgcolor=ft.Colors.BLACK_26, border_radius=5, padding=10),
            ])
        elif not self.updates:
            self.controls.append(_text("No dependencies listed for this version.", 13))
        if plan.dependencies and not any(not item.required for item in plan.dependencies):
            self.controls.append(_text("This version lists no optional dependencies.", 12))
        if plan.replacements:
            self.controls.append(_text("Previous files stay in launcher trash." if self.updates else
                                       "Replaces " + ", ".join(plan.replacements) + ". Previous files stay in launcher trash.", 12))
        if plan.disabled_projects:
            self.controls.append(_text("Disabled mods stay disabled after updating." if self.updates else "This mod will stay disabled after the update.", 12))
        if self.checking:
            self.controls.append(_text("Checking dependency compatibility…", 12, color=ACCENT))
        if self.error:
            self.controls.append(_text(self.error, 12, color=ACCENT))

    def _optional_handler(self, project: str) -> Callable:
        async def changed(e) -> None:
            await self.on_optional_change(e, project)
        return changed
