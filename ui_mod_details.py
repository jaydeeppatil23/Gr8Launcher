"""Mod overview, gallery, complete version history, and version changelogs."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Callable
from urllib.parse import urlparse
import webbrowser

import flet as ft

from mod_services import (LocalMod, ModManager, ModrinthClient, jar_base_name, newer_compatible_version,
                          version_loader_compatible, version_matches_environment)
from mod_markdown import normalize_mod_markdown
from mod_compatibility import version_supports_profile
from ui_controls import (SimpleInput, AcrylicSelector, launcher_button, launcher_back_button,
                          launcher_icon_button, rounded_artwork, ACCENT, MUTED, search_input, card_surface)
from ui_motion import StaggeredReveal
from ui_profiles import ProfileList, profile_card_surface, profile_list_surface
from ui_progress import ProgressHost, ProgressTask, track_progress
from ui_scroll_views import ScrollColumn

BODY_FONT = "Segoe UI"
DETAIL_MUTED = "#929db1"
SIDEBAR_WIDTH = 236


def text(value: str, size=13, *, pixel=False, color="#c5cedc", **kwargs) -> ft.Text:
    return ft.Text(value, size=size, color=color, font_family="ps" if pixel else None, **kwargs)


def open_link(url: str) -> None:
    if urlparse(str(url)).scheme in {"https", "http"}:
        webbrowser.open(str(url))


def markdown(value: str, *, base_url: str = "https://modrinth.com/") -> ft.Markdown:
    body = ft.TextStyle(font_family=BODY_FONT, color="#d1d7e2", size=14, height=1.55)
    def heading(size: int) -> ft.TextStyle:
        return ft.TextStyle(font_family=BODY_FONT, color="#e5ebf4", size=size, weight=ft.FontWeight.W_600, height=1.35)
    return ft.Markdown(
        value=normalize_mod_markdown(value, base_url), selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        image_error_content=text("Image unavailable", 12, color=DETAIL_MUTED),
        auto_follow_links=False, on_tap_link=lambda e: open_link(e.data),
        md_style_sheet=ft.MarkdownStyleSheet(
            p_text_style=body, a_text_style=ft.TextStyle(color=ACCENT, font_family=BODY_FONT),
            h1_text_style=heading(22), h2_text_style=heading(19), h3_text_style=heading(17),
            h4_text_style=heading(16), h5_text_style=heading(15), h6_text_style=heading(14),
            strong_text_style=ft.TextStyle(font_family=BODY_FONT, color="#e5ebf4", weight=ft.FontWeight.W_600),
            em_text_style=ft.TextStyle(font_family=BODY_FONT, italic=True),
            del_text_style=ft.TextStyle(font_family=BODY_FONT, decoration=ft.TextDecoration.LINE_THROUGH),
            list_bullet_text_style=body, table_body_text_style=body, table_head_text_style=heading(14),
            blockquote_text_style=body, code_text_style=ft.TextStyle(font_family="Consolas", color="#e5ebf4", size=13),
            block_spacing=16, list_indent=20,
            blockquote_padding=ft.Padding.all(12),
            blockquote_decoration=ft.BoxDecoration(bgcolor=ft.Colors.BLACK_26, border_radius=5),
            codeblock_padding=ft.Padding.all(12),
            codeblock_decoration=ft.BoxDecoration(bgcolor=ft.Colors.BLACK_26, border_radius=5),
            table_cells_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        ),
    )


class ModDetailsPage(ft.Container):
    def __init__(self, page, *, client: ModrinthClient, get_profile: Callable, get_manager: Callable,
                 can_change: Callable, on_back: Callable, on_install: Callable,
                 on_toggle: Callable, on_remove: Callable, progress: ProgressHost | None = None,
                 on_release_filter_changed: Callable[[], None] | None = None):
        self._page = page
        self.client = client
        self._get_profile = get_profile
        self._get_manager = get_manager
        self._can_change = can_change
        self._on_back = on_back
        self._on_install = on_install
        self._on_toggle = on_toggle
        self._on_remove = on_remove
        self._progress = progress
        self._on_release_filter_changed = on_release_filter_changed or (lambda: None)
        self._load_activity: ProgressTask | None = None
        self.item: LocalMod | dict | None = None
        self.local: LocalMod | None = None
        self.local_info: dict = {}
        self.project: dict = {}
        self.versions: list[dict] = []
        self.members: list[dict] = []
        self.section = "overview"
        self.selected_version: dict | None = None
        self.loading = False
        self.error = ""
        self._generation = 0
        self._active = False
        self._loaded = False
        self._profile_id = None
        self.version_offset = 0
        self._version_actions: list[tuple[ft.Button, dict]] = []
        self._icon_src = None
        self._source_manager = None

        self.back = launcher_back_button(lambda e: on_back(), tooltip="Back to Mods")
        self.profile_label = text("", 12, color=DETAIL_MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.back_bar = ft.Row([self.back, ft.Container(expand=True), self.profile_label])
        self.title = text("Mod details", 28, pixel=True, color=ACCENT, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.subtitle = text("", 12, color=DETAIL_MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.summary = text("", 13, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.icon_host = ft.Container(width=64, height=64, alignment=ft.Alignment.CENTER)
        self.install_button = launcher_button("Install", self._install_latest, primary=True, height=44, width=96)
        self.toggle_button = launcher_button("Disable", lambda e: self._on_toggle(self.local, self._source_manager))
        self.remove_button = launcher_button("Remove", lambda e: self._on_remove(self.local, self._source_manager))
        self.hero = profile_card_surface(ft.Row([
            self.icon_host, ft.Column([
                self.title, self.subtitle, self.summary,
            ], expand=True, spacing=5),
            self.install_button, self.toggle_button, self.remove_button,
        ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER), glow_color="#454641", horizontal_padding=16)
        self.section_buttons = {key: launcher_button(label, lambda e, name=key: self.set_section(name))
                                for key, label in (("overview", "Overview"), ("versions", "Versions"), ("gallery", "Gallery"))}
        self.tabs = ft.Row(list(self.section_buttons.values()), spacing=8)
        self.feedback = ft.Row([text("", 12, color=MUTED, expand=True), launcher_button("Retry", self._retry)], visible=False)
        self.body = profile_list_surface(ft.Container(expand=True))
        self.version_search = search_input("Search versions", on_change=self._filter_versions)
        self.release_filter = AcrylicSelector([("all", "All releases"), ("release", "Stable"), ("beta", "Beta"), ("alpha", "Alpha")], "release", self._release_filter_changed, width=160)
        self.compatible_only = ft.Switch(value=True, active_color=ACCENT, track_color="#2b3b55", on_change=self._filter_versions)
        self.version_list = ProfileList(on_order_changed=lambda ids: False)
        self.version_hint = text("", 12, color=MUTED)
        self.version_previous = launcher_icon_button(icon=ft.Icons.CHEVRON_LEFT_SHARP, tooltip="Previous versions", on_click=lambda e: self._paginate_versions(-1))
        self.version_next = launcher_icon_button(icon=ft.Icons.CHEVRON_RIGHT_SHARP, tooltip="Next versions", on_click=lambda e: self._paginate_versions(1))
        self.version_page_label = text("", 12, color=MUTED)
        self.version_footer = ft.Row([ft.Container(expand=True), self.version_previous, self.version_page_label, self.version_next], spacing=8)
        self.versions_panel = ft.Column([
            ft.Row([self.version_search, self.release_filter], spacing=12),
            ft.Row([self.compatible_only, text("Compatible with this instance", 13),
                    ft.Container(expand=True), self.version_hint], spacing=8),
            profile_list_surface(self.version_list),
            self.version_footer,
        ], expand=True, spacing=12)
        super().__init__(expand=True, content=ft.Column(
            [self.back_bar, self.hero, self.tabs, self.feedback, self.body],
            expand=True, spacing=14, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ))
        self.reveal = StaggeredReveal(page, self.body)

    @property
    def reveal_controls(self):
        return [self.back_bar, self.hero, self.tabs, self.body]

    def _update(self):
        if self._active:
            try:
                self.update()
            except RuntimeError:
                pass

    def open(self, item: LocalMod | dict) -> None:
        self._active = True
        self.item = item
        self.local = item if isinstance(item, LocalMod) else None
        self.local_info = {}
        self.project = {}
        self.versions = []
        self.members = []
        self.section = "overview"
        self.selected_version = None
        self.version_offset = 0
        self.version_search.value = ""
        self.compatible_only.value = True
        self.release_filter.refresh_options(self.release_filter.options, self.release_filter.value)
        self._profile_id = (self._get_profile() or {}).get("id")
        self._source_manager = self._get_manager()
        self._loaded = False
        self.reload()

    def suspend(self) -> None:
        self._active = False
        self._generation += 1
        if self._load_activity:
            self._load_activity.finish()
            self._load_activity = None
        self.reveal.finish(update=False)

    def resume(self) -> None:
        self._active = True
        if not self._loaded:
            self.reload()
        else:
            self._render()

    def reload(self) -> None:
        if self.item is None:
            return
        self._generation += 1
        if self._load_activity:
            self._load_activity.finish()
        self.loading = True
        self.error = ""
        self._render()
        activity = track_progress(self._progress, "Fetching mod details...", priority=40)
        self._load_activity = activity
        self._page.run_task(self._load, self._generation, self.item, self._get_manager(), activity)

    def _retry(self, e) -> None:
        clear = getattr(self.client, "clear_cache", None)
        if callable(clear):
            clear()
        self.reload()

    async def _load(self, generation: int, item: LocalMod | dict, manager: ModManager, activity: ProgressTask) -> None:
        local_info, project, versions, members = {}, {}, [], []
        matched = None
        error = ""
        try:
            if isinstance(item, LocalMod):
                local_info = await asyncio.to_thread(manager.describe_mod, item.filename)
                project_id = item.project_id
                if not project_id:
                    matched = await asyncio.to_thread(manager.identify_local, item.filename, self.client)
                    project_id = (matched or {}).get("project_id")
            else:
                project_id = item.get("project_id")
            if project_id:
                results = await asyncio.gather(
                    asyncio.to_thread(self.client.project, project_id),
                    asyncio.to_thread(self.client.project_versions, project_id),
                    asyncio.to_thread(self.client.project_members, project_id),
                    return_exceptions=True,
                )
                project, versions, members = ({} if isinstance(results[0], Exception) else results[0],
                                              [] if isinstance(results[1], Exception) else results[1],
                                              [] if isinstance(results[2], Exception) else results[2])
                error = next((str(result) for result in results if isinstance(result, Exception)), "")
                profile = (self._get_profile() or {}).copy()
                current_id = (matched or {}).get("id") or (item.version_id if isinstance(item, LocalMod) else None)
                current = next((version for version in versions if version.get("id") == current_id), None)
                if current and str(profile.get("loader", "")).lower() == "fabric" and profile.get("loader_version"):
                    def check_loader_updates() -> None:
                        providers, _ = manager.profile_providers(profile.get("minecraft_version", ""), "fabric",
                                                                  profile["loader_version"], profile.get("version_id", ""), runtime_only=True)
                        def compatible(version: dict) -> bool:
                            version["_profile_compatible"] = version_matches_environment(
                                self.client, version, "fabric", profile["loader_version"], providers)
                            return version["_profile_compatible"]
                        # Check each release type so changing the dropdown stays immediate.
                        # Stop at its first compatible update rather than probing all history.
                        for release_type in ("release", "beta", "alpha"):
                            newer_compatible_version(current, versions, profile.get("minecraft_version", ""), "fabric",
                                                     release_type=release_type, check_loader=compatible)
                    await asyncio.to_thread(check_loader_updates)
        except Exception as exc:
            error = str(exc)
        finally:
            activity.finish()
            if self._load_activity is activity:
                self._load_activity = None
        if generation != self._generation or not self._active or self._profile_id != (self._get_profile() or {}).get("id"):
            return
        self.local_info, self.project, self.versions, self.members = local_info, project, versions, members
        if matched and self.local and isinstance(item, LocalMod):
            self.local = replace(self.local, project_id=matched["project_id"], version_id=matched["id"])
        self.error = error
        self.loading = False
        self._loaded = True
        self._render()

    def sync_local(self, mods: list[LocalMod]) -> None:
        if self.item is None:
            return
        project_id = self.project.get("id") or (self.item.project_id if isinstance(self.item, LocalMod) else self.item.get("project_id"))
        filename = jar_base_name(self.item.filename).casefold() if isinstance(self.item, LocalMod) else ""
        self.local = next((mod for mod in mods if (project_id and mod.project_id == project_id)
                            or (filename and jar_base_name(mod.filename).casefold() == filename)), None)
        if self.local and isinstance(self.item, LocalMod):
            self.item = self.local
        if self._active:
            self._render_header()
            if self.section == "versions":
                self._render_versions()
            self._update()

    def set_section(self, key: str) -> None:
        self.section = key
        self.selected_version = None
        self._render(animate=True)

    def _compatible(self, version: dict) -> bool:
        profile = self._get_profile() or {}
        loader = str(profile.get("loader", "")).lower()
        return (version_supports_profile(version, profile.get("minecraft_version", ""), loader)
                and version.get("_profile_compatible") is not False
                and version_loader_compatible(version, loader, profile.get("loader_version", "")) is not False)

    def _render_header(self) -> None:
        item = self.item
        title = self.project.get("title") or (item.name if isinstance(item, LocalMod) else (item or {}).get("title", "Mod details"))
        self.title.value = title
        profile = self._get_profile() or {}
        self.profile_label.value = profile.get("name", "Instance")
        compatibility = " · ".join(str(value) for value in (
            profile.get("loader"), profile.get("minecraft_version")) if value)
        authors = [member.get("user", {}).get("username", "") for member in self.members]
        author = ", ".join(name for name in authors if name)
        self.subtitle.value = "by " + author if author else "Local JAR" if isinstance(item, LocalMod) else ""
        self.summary.value = (self.project.get("description") or self.local_info.get("description")
                              or (item.description if isinstance(item, LocalMod) else (item or {}).get("description", "")))
        self.summary.visible = bool(self.summary.value)
        if self.local:
            self.subtitle.value += f" · {self.local.version or 'Unknown version'} · {'Enabled' if self.local.enabled else 'Disabled'}"
            if self.local.pack_owned:
                self.subtitle.value += " · Pack managed"
        self.subtitle.value = " · ".join(value for value in (self.subtitle.value, compatibility) if value)
        self.subtitle.tooltip = f"Installation target: {profile.get('name', 'Instance')}"
        icon_url = ((self.local.icon if self.local else None) or self.local_info.get("icon")
                    or self.project.get("icon_url") or ((item or {}).get("icon_url") if isinstance(item, dict) else None))
        if self._icon_src != (icon_url or "sidebar/mods.png"):
            self._icon_src = icon_url or "sidebar/mods.png"
            self.icon_host.content = rounded_artwork(
                ft.Image(src=self._icon_src, width=64, height=64, fit=ft.BoxFit.CONTAIN, filter_quality=ft.FilterQuality.NONE,
                         cache_width=128, gapless_playback=True,
                         error_content=ft.Image(src="sidebar/mods.png", width=48, height=48, filter_quality=ft.FilterQuality.NONE)), size=64)
        can_change = self._can_change()
        update = self._latest_update()
        self.install_button.visible = bool(self.project) and (not self.local or update is not None)
        self.install_button.content.value = "Update" if self.local else "Install"
        self.install_button.disabled = self.loading or not can_change or not any(self._compatible(v) for v in self.versions)
        self.toggle_button.visible = self.remove_button.visible = self.local is not None
        self.toggle_button.content.value = "Disable" if self.local and self.local.enabled else "Enable"
        self.toggle_button.disabled = self.remove_button.disabled = not can_change
        for button, version in self._version_actions:
            button.disabled = (not can_change or not self._compatible(version)
                               or bool(self.local and (self.local.pack_owned or self.local.version_id == version.get("id"))))

    def _render(self, *, animate=False) -> None:
        self._render_header()
        self.feedback.visible = bool(self.error)
        self.feedback.controls[0].value = self.error
        for key, button in self.section_buttons.items():
            button.style.bgcolor = ft.Colors.BLACK_26 if key == self.section else ft.Colors.TRANSPARENT
            button.content.color = ACCENT if key == self.section else MUTED
        self.section_buttons["gallery"].visible = bool(self.project.get("gallery"))
        if self.section == "versions":
            self._render_versions()
            content = self.versions_panel
        elif self.section == "gallery":
            content = self._gallery()
        else:
            content = self._overview()
        self.reveal.show(content, content.controls if isinstance(content, ft.Column) else [content], animate=animate)
        self._update()

    def _fact(self, title: str, value: str) -> ft.Column:
        return ft.Column([text(title, 11, color=DETAIL_MUTED), text(value or "Not provided", 13, selectable=True)], spacing=4)

    def _link(self, label: str, url: str) -> ft.Button:
        button = launcher_button(label, lambda e: open_link(url), height=32, width=SIDEBAR_WIDTH - 32)
        button.style.padding = 0
        button.content = ft.Row([
            text(label, 13, color=ACCENT, expand=True),
            ft.Icon(ft.Icons.OPEN_IN_NEW_SHARP, size=15, color=DETAIL_MUTED),
        ], spacing=8)
        return button

    def _overview(self) -> ft.Row:
        description = self.project.get("body") or self.project.get("description") or self.local_info.get("description") or (self.local.description if self.local else "")
        if not description:
            description = "Loading mod details..." if self.loading else "This JAR does not include a description."
        project_url = self.project.get("project_url") or ("https://modrinth.com/mod/" + self.project["id"] if self.project.get("id") else "")
        base_url = project_url or "https://modrinth.com/"
        main = ScrollColumn([markdown(description, base_url=base_url)], bottom_fade=True,
                         expand=True, scroll=ft.ScrollMode.AUTO, spacing=16,
                         horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        facts = []
        links = []
        if self.project:
            facts.extend([
                self._fact("Downloads", f"{self.project.get('downloads', 0):,}"),
                self._fact("License", (self.project.get("license") or {}).get("name") or (self.project.get("license") or {}).get("id", "")),
                self._fact("Categories", ", ".join(self.project.get("categories", []))),
                self._fact("Environment", f"Client: {self.project.get('client_side', 'unknown').capitalize()}\nServer: {self.project.get('server_side', 'unknown').capitalize()}"),
            ])
            provider = "CurseForge" if self.project.get("provider") == "curseforge" else "Modrinth"
            for label, key in ((f"Project on {provider}", "project"), ("Source code", "source_url"), ("Report an issue", "issues_url"), ("Wiki", "wiki_url"), ("Discord", "discord_url")):
                url = project_url if key == "project" else self.project.get(key)
                if url:
                    links.append(self._link(label, url))
        if self.local:
            authors = self.local_info.get("authors", [])
            if isinstance(authors, list):
                authors = ", ".join(str(a.get("name", "")) if isinstance(a, dict) else str(a) for a in authors)
            facts.extend([self._fact("Local file", self.local.filename), self._fact("Version", self.local.version),
                          self._fact("Mod ID", self.local.mod_id), self._fact("Size", f"{self.local.size / 1024:.0f} KB")])
            if authors:
                facts.append(self._fact("Authors", str(authors)))
            if not self.project and self.local_info.get("license"):
                facts.append(self._fact("License", str(self.local_info["license"])))
            dependencies = self.local_info.get("dependencies", {})
            if isinstance(dependencies, dict) and dependencies:
                facts.append(self._fact("Declared dependencies", "\n".join(f"{key}: {value}" for key, value in dependencies.items())))
            local_links = self.local_info.get("links", {})
            if isinstance(local_links, dict):
                for key, url in local_links.items():
                    if isinstance(url, str) and urlparse(url).scheme in {"http", "https"}:
                        links.append(self._link(key.title(), url))
        sidebar = [text("About this mod", 18, pixel=True, color=ACCENT), *facts]
        if links:
            sidebar.extend([text("Links", 16, pixel=True, color=ACCENT),
                            ft.Column(links, spacing=2, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)])
        panel = ft.Container(padding=16, bgcolor=ft.Colors.BLACK_26, border_radius=5,
                             content=ft.Column(sidebar, spacing=12, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        return ft.Row([main, ft.Container(width=SIDEBAR_WIDTH, visible=bool(facts or links),
                                         content=ScrollColumn([panel], bottom_fade=True, scroll=ft.ScrollMode.AUTO, expand=True))],
                      expand=True, spacing=24, vertical_alignment=ft.CrossAxisAlignment.STRETCH)

    def _render_versions(self) -> None:
        if self.section != "versions":
            return
        if self.selected_version:
            self.reveal.show(self._version_details(self.selected_version), animate=False)
            self._update()
            return
        query = (self.version_search.value or "").strip().casefold()
        release = self.release_filter.value
        matching = []
        for version in self.versions:
            if self.compatible_only.value and not self._compatible(version):
                continue
            if release != "all" and version.get("version_type") != release:
                continue
            if query and query not in f"{version.get('name', '')} {version.get('version_number', '')} {' '.join(version.get('game_versions', []))}".casefold():
                continue
            matching.append(version)
        self.version_offset = min(self.version_offset, max(0, (len(matching) - 1) // 20 * 20))
        self.version_previous.disabled = self.version_offset == 0
        self.version_next.disabled = self.version_offset + 20 >= len(matching)
        self.version_footer.visible = len(matching) > 20
        self.version_page_label.value = f"{self.version_offset // 20 + 1} / {max(1, (len(matching) + 19) // 20)}"
        self._version_actions = []
        rows = []
        for version in matching[self.version_offset:self.version_offset + 20]:
            compatible = self._compatible(version)
            installed = bool(self.local and self.local.version_id == version.get("id"))
            pinned = bool(self.local and self.local.pack_owned)
            label = "Installed" if installed else "Pack managed" if pinned else "Install" if compatible else "Incompatible"
            install = launcher_button(label, lambda e, v=version: self._install_version(v), primary=compatible and not installed,
                                      disabled=pinned or installed or not compatible or not self._can_change())
            self._version_actions.append((install, version))
            card = profile_card_surface(ft.Row([
                ft.Container(content=ft.Column([
                    text(version.get("name") or version.get("version_number", "Version"), 17, pixel=True, color=ACCENT),
                    text(" / ".join([version.get("version_type", "release").title(), version.get("date_published", "")[:10],
                                     ", ".join(version.get("loaders", [])), "Minecraft " + ", ".join(version.get("game_versions", []))]), 11,
                         max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=5), expand=True, on_click=lambda e, v=version: self.open_version(v),
                    tooltip="View changelog"),
                install,
                launcher_icon_button(icon=ft.Icons.CHEVRON_RIGHT_SHARP, icon_color=MUTED, tooltip="Version details",
                              on_click=lambda e, v=version: self.open_version(v)),
            ], spacing=12), glow_color="#454641")
            card.data = version.get("id")
            card.key = ft.ValueKey("version-" + str(card.data))
            card.margin = ft.Margin.only(bottom=8)
            rows.append(card)
        self.version_hint.value = f"{len(matching)} versions"
        self.version_list.set_cards(rows)
        if not rows:
            message = "Loading versions..." if self.loading else "No matching versions. Try all releases or turn off the compatibility filter." if self.versions else "No online version history is available for this local JAR. Its installed version is shown in Overview."
            self.version_list.set_cards([], header=ft.Container(padding=24, content=text(message, 14)))
        self._update()

    def _filter_versions(self, e) -> None:
        self.version_offset = 0
        self._render_versions()

    def _release_filter_changed(self, e) -> None:
        self._filter_versions(e)
        self._on_release_filter_changed()

    def _paginate_versions(self, direction: int) -> None:
        self.version_offset = max(0, self.version_offset + direction * 20)
        self._render_versions()

    def open_version(self, version: dict) -> None:
        self.selected_version = version
        load_changelog = version.get("provider") == "curseforge" and not version.get("_changelog_loaded")
        if load_changelog:
            version["_changelog_loading"] = True
        self.reveal.show(self._version_details(version), animate=False)
        self._update()
        if load_changelog:
            self._page.run_task(self._load_changelog, self._generation, version)

    async def _load_changelog(self, generation: int, version: dict) -> None:
        with track_progress(self._progress, "Loading CurseForge changelog...", priority=45):
            try:
                result = await asyncio.to_thread(self.client.version, version["id"])
                if result.get("id") != version["id"] or result.get("project_id") != version["project_id"]:
                    raise ValueError("CurseForge returned a different version's changelog.")
                if result.get("_changelog_unavailable"):
                    raise ConnectionError("CurseForge changelog is unavailable.")
                version["changelog"] = result.get("changelog", "")
                version["_changelog_loaded"] = True
            except Exception:
                version["changelog"] = "The changelog is unavailable. Reopen this version to retry."
            finally:
                version["_changelog_loading"] = False
        if generation == self._generation and self._active and self.selected_version is version:
            self.reveal.show(self._version_details(version), animate=False)
            self._update()

    def _version_details(self, version: dict) -> ft.Column:
        def back(e):
            self.selected_version = None
            self.reveal.show(self.versions_panel, animate=False)
            self._render_versions()
        compatible = self._compatible(version)
        requirements = ", ".join(version.get("loaders", [])) + " / Minecraft " + ", ".join(version.get("game_versions", []))
        install = launcher_button("Install this version", lambda e: self._install_version(version), primary=True,
                                  disabled=not compatible or not self._can_change() or bool(self.local and self.local.version_id == version.get("id")))
        self._version_actions = [(install, version)]
        controls = [ft.Row([launcher_back_button(back, tooltip="Back to versions"), ft.Container(expand=True), install]),
                    text(version.get("name") or version.get("version_number", "Version"), 23, pixel=True, color=ACCENT),
                    text(requirements, 13), text("Changelog", 19, pixel=True, color=ACCENT),
                    markdown("Loading changelog..." if version.get("_changelog_loading") else
                             version.get("changelog") or "No changelog was provided for this release.",
                             base_url=self.project.get("project_url") or "https://modrinth.com/")]
        if not compatible:
            controls.insert(3, text("This version is incompatible with the selected instance. You can read its details, but it cannot be installed here.", 13, color=ACCENT))
        for file in version.get("files", []):
            controls.append(self._fact("File", f"{file.get('filename', '')} / {file.get('size', 0) / 1024:.0f} KB"))
        dependencies = [d for d in version.get("dependencies", []) if d.get("dependency_type") == "required"]
        if dependencies:
            controls.append(text(f"{len(dependencies)} required dependencies will be resolved before installation.", 13))
        return ScrollColumn(controls, bottom_fade=True, expand=True, scroll=ft.ScrollMode.AUTO, spacing=16)

    def _gallery(self) -> ft.Column:
        controls = []
        for item in self.project.get("gallery", []):
            controls.append(ft.Container(
                padding=12, border_radius=5, bgcolor=ft.Colors.BLACK_26,
                content=ft.Column([
                    ft.Image(src=item.get("url", ""), fit=ft.BoxFit.CONTAIN, height=280),
                    text(item.get("title") or "", 17, pixel=True, color=ACCENT),
                    text(item.get("description") or "", 13),
                ], spacing=10),
            ))
        return ScrollColumn(controls, bottom_fade=True, expand=True, scroll=ft.ScrollMode.AUTO, spacing=16)

    def _install_latest(self, e) -> None:
        if self.local:
            version = self._latest_update()
            if version:
                self._install_version(version)
            return
        if (self._active and self._profile_id == (self._get_profile() or {}).get("id")
                and self._can_change() and any(self._compatible(version) for version in self.versions)):
            # Automatic installation uses the backend's Loader-aware selection.
            project_id = self.project.get("id") or (self.item.project_id if isinstance(self.item, LocalMod)
                                                     else (self.item or {}).get("project_id"))
            self._on_install({"project_id": project_id,
                              "title": self.title.value}, None)

    def _latest_update(self) -> dict | None:
        if not self.local or not self.local.version_id or self.local.pack_owned:
            return None
        current = next((version for version in self.versions if version.get("id") == self.local.version_id), None)
        if not current:
            return None
        profile = self._get_profile() or {}
        try:
            return newer_compatible_version(current, self.versions, profile.get("minecraft_version", ""),
                                             str(profile.get("loader", "")).lower(), release_type=self.release_filter.value,
                                             check_loader=lambda version: version.get("_profile_compatible") is not False and (
                                                 not callable(getattr(self.client, "fabric_loader_metadata", None))
                                                 or not profile.get("loader_version") or version_loader_compatible(
                                                     version, str(profile.get("loader", "")).lower(), profile["loader_version"]) is not False))
        except (ValueError, KeyError, TypeError):
            return None

    def _install_version(self, version: dict) -> None:
        if self.local and self.local.pack_owned:
            return
        if (self._active and self._profile_id == (self._get_profile() or {}).get("id")
                and self._can_change() and self._compatible(version)):
            self._on_install({"project_id": self.project.get("id") or version["project_id"],
                              "title": self.title.value}, version)
