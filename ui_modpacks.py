"""Modpack catalogue, archive imports, and reviewed instance installation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import threading
from typing import Callable

import flet as ft

from modpack_services import PackCancelled, PackInstaller, PackPlan
from mod_platform import ModPlatformClient
import profile_creation
from ui_controls import (ACCENT, MUTED, NAVY, AcrylicSelector, SimpleInput, launcher_back_button,
                          launcher_button, launcher_icon_button, rounded_artwork,
                          format_size as _size, format_downloads as _downloads,
                          search_input, card_surface)
from ui_mod_details import markdown, open_link
from ui_mod_list import ModList
from ui_motion import StaggeredReveal
from ui_profiles import ProfileList, profile_card_surface, profile_list_surface
from ui_progress import ProgressHost, track_progress


CONTENT_OPTIONS = [("mods", "Mods"), ("modpacks", "Modpacks")]
PROVIDER_OPTIONS = [("modrinth", "Modrinth"), ("curseforge", "CurseForge")]
SORT_OPTIONS = [("downloads", "Popular"), ("relevance", "Relevance"),
                ("updated", "Recently updated"), ("newest", "Newest")]


def _text(value: str, size: int = 13, *, pixel: bool = False, color: str = MUTED, **kwargs) -> ft.Text:
    return ft.Text(value, size=size, color=color, font_family="ps" if pixel else None, **kwargs)


def _update(control: ft.Control) -> None:
    try:
        control.update()
    except RuntimeError:
        pass


def _artwork(source: str | None, size: int = 48) -> ft.Control:
    fallback = ft.Image(src="sidebar/mods.png", width=34, height=34, fit=ft.BoxFit.CONTAIN, filter_quality=ft.FilterQuality.NONE)
    content = ft.Image(src=source, width=size, height=size, fit=ft.BoxFit.CONTAIN,
                       cache_width=size * 2, cache_height=size * 2, filter_quality=ft.FilterQuality.NONE,
                       error_content=fallback) if source else fallback
    return rounded_artwork(content, size=size)


class ModpackInstallReview(ft.Column):
    def __init__(self, plan: PackPlan, folder_name: str, *, on_optional_change: Callable,
                 on_manual_files: Callable, manual_file_ready: Callable):
        self.plan = plan
        self.optional_paths: set[str] = set()
        self.on_optional_change = on_optional_change
        self.on_manual_files = on_manual_files
        self.manual_file_ready = manual_file_ready
        self.checking = False
        self.error = ""
        self.title = _text("Install modpack", 22, pixel=True, color=ACCENT)
        self.name_input = SimpleInput(value=plan.name, label="Instance name", hint_text="Instance name")
        self.folder_input = SimpleInput(value=folder_name, label="Instance folder", hint_text="Folder name")
        self.name_input.expand = self.folder_input.expand = True
        super().__init__(spacing=16, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.render()

    @property
    def preferred_height(self) -> int:
        return min(550, 360 + len(self.plan.optional_files) * 42 + len(self.plan.manual_files()) * 48)

    def render(self) -> None:
        selected = tuple(self.optional_paths)
        loader = self.plan.loader + (" " + self.plan.loader_version if self.plan.loader_version else "")
        self.controls = [
            _text(self.plan.name, 20, pixel=True, color=ACCENT, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
            _text(f"Pack {self.plan.pack_version} · Minecraft {self.plan.minecraft_version} · {loader}", 13, color="#d1d7e2"),
            ft.Row([self.name_input, self.folder_input], spacing=12),
            _text(f"{len(self.plan.selected_files(selected))} files · {_size(self.plan.download_size(selected))} to download", 13),
            _text("Creates a separate instance. Play installs any missing Minecraft or loader files.", 12),
        ]
        if self.plan.optional_files:
            self.controls.append(_text("Optional client files", 15, pixel=True, color=ACCENT))
            self.controls.extend(ft.Checkbox(
                label=file.label or file.path, value=file.path in self.optional_paths,
                disabled=self.checking, active_color=ACCENT,
                on_change=lambda e, path=file.path: self.on_optional_change(e, path),
            ) for file in self.plan.optional_files)
        manual = self.plan.manual_files(selected)
        if manual:
            self.controls.append(_text("Manual CurseForge downloads", 15, pixel=True, color=ACCENT))
            self.controls.append(_text("Download these exact files, then select them below to verify them.", 12))
            for file in manual:
                ready = self.manual_file_ready(file)
                self.controls.append(ft.Row([
                    launcher_button(file["filename"], lambda e, url=file.get("manual_url", ""): open_link(url),
                                    expand=True, tooltip="Download the exact official CurseForge file"),
                    _text("Selected" if ready else "Required", 12, color=ACCENT if ready else MUTED),
                ], spacing=8))
            self.controls.append(launcher_button("Select downloaded files", self.on_manual_files, disabled=self.checking))
        if self.error:
            self.controls.append(_text(self.error, 13, color="#e57373"))


class ModpackDetailsPage(ft.Container):
    PAGE_SIZE = 20

    def __init__(self, page: ft.Page, *, client, on_back: Callable, on_install: Callable,
                 can_install: Callable, on_cancel: Callable | None = None, progress: ProgressHost | None = None):
        self._page = page
        self.client = client
        self._on_install = on_install
        self._can_install = can_install
        self._progress = progress
        self._generation = 0
        self._active = False
        self._loaded = False
        self._activity = None
        self.project: dict = {}
        self.versions: list[dict] = []
        self.section = "overview"
        self.loading = False
        self.error = ""
        self.version_offset = 0
        self.operation_phase = "idle"
        self.back_bar = ft.Row([launcher_back_button(lambda e: on_back(), tooltip="Back to Modpacks")])
        self.title = _text("Modpack", 28, pixel=True, color=ACCENT, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.subtitle = _text("", 12)
        self.summary = _text("", 13, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.icon = ft.Container(width=64, height=64)
        self.install_button = launcher_button("Install", self._install_latest, primary=True, height=44, width=96)
        self.cancel_button = launcher_button("Cancel", on_cancel, visible=False, height=44)
        self.hero = profile_card_surface(ft.Row([
            self.icon, ft.Column([self.title, self.subtitle, self.summary], expand=True, spacing=5),
            self.install_button, self.cancel_button,
        ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER), glow_color="#454641", horizontal_padding=16)
        self.hero.bgcolor = ft.Colors.with_opacity(0.18, "#000000")
        self.section_buttons = {key: launcher_button(label, lambda e, name=key: self.set_section(name))
                                for key, label in (("overview", "Overview"), ("versions", "Versions"), ("gallery", "Gallery"))}
        self.tabs = ft.Row(list(self.section_buttons.values()), spacing=8)
        self.body = profile_list_surface(ft.Container(expand=True))
        self.version_search = search_input("Search pack versions", on_change=self._filter_versions)
        self.release_filter = AcrylicSelector([( "all", "All releases"), ("release", "Stable"), ("beta", "Beta"), ("alpha", "Alpha")],
                                              "release", self._filter_versions, width=160)
        self.version_list = ProfileList(on_order_changed=lambda ids: False)
        self.version_previous = launcher_icon_button(icon=ft.Icons.CHEVRON_LEFT_SHARP, tooltip="Previous versions",
                                                     on_click=lambda e: self._paginate_versions(-1))
        self.version_next = launcher_icon_button(icon=ft.Icons.CHEVRON_RIGHT_SHARP, tooltip="Next versions",
                                                 on_click=lambda e: self._paginate_versions(1))
        self.version_label = _text("", 12)
        self.version_footer = ft.Row([ft.Container(expand=True), self.version_previous, self.version_label, self.version_next], spacing=8)
        self.versions_panel = ft.Column([ft.Row([self.version_search, self.release_filter], spacing=12),
                                        profile_list_surface(self.version_list), self.version_footer], expand=True, spacing=12)
        super().__init__(expand=True, content=ft.Column([self.back_bar, self.hero, self.tabs, self.body],
                            expand=True, spacing=14, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        self.reveal = StaggeredReveal(page, self.body)

    @property
    def reveal_controls(self) -> list[ft.Control]:
        return [self.back_bar, self.hero, self.tabs, self.body]

    def open(self, hit: dict) -> None:
        self._generation += 1
        self.project = deepcopy(hit)
        self.versions = []
        self.section = "overview"
        self.version_offset = 0
        self.version_search.value = ""
        self._active = True
        self._loaded = False
        self.loading = True
        self.error = ""
        self.render()
        self._start_load()

    def _start_load(self) -> None:
        if self._activity:
            self._activity.finish()
        activity = track_progress(self._progress, "Loading modpack details...", priority=45)
        self._activity = activity
        self._page.run_task(self._load, self._generation, self.project.get("project_id") or self.project["id"], activity)

    async def _load(self, generation: int, project_id: str, activity) -> None:
        try:
            project, versions = await asyncio.gather(asyncio.to_thread(self.client.modpack_project, project_id),
                                                      asyncio.to_thread(self.client.modpack_versions, project_id))
            if generation != self._generation or not self._active:
                return
            self.project = project
            self.versions = versions
            self._loaded = True
            self.loading = False
            self.render()
            _update(self)
            self.reveal.show(self.body.content, [self.body.content])
        except Exception as error:
            if generation == self._generation and self._active:
                self.loading = False
                self.error = str(error)
                self.render()
                _update(self)
        finally:
            activity.finish()
            if self._activity is activity:
                self._activity = None

    def leave(self) -> None:
        self._active = False
        self._generation += 1
        if self._activity:
            self._activity.finish()
            self._activity = None
        self.reveal.finish(update=False)

    def resume(self) -> None:
        self._active = True
        if not self._loaded:
            self.loading = True
            self.error = ""
            self._start_load()
        self.render()

    def set_section(self, section: str) -> None:
        if section not in self.section_buttons or section == self.section:
            return
        self.section = section
        self.render()
        _update(self)
        self.reveal.show(self.body.content, [self.body.content])

    def _install_latest(self, e) -> None:
        version = next((item for item in self.versions if item.get("version_type") == "release"),
                       self.versions[0] if self.versions else None)
        if version and self._can_install():
            self._on_install(self.project, version)

    def _retry(self, e) -> None:
        self.loading = True
        self.error = ""
        self.render()
        self._start_load()

    def render(self) -> None:
        self.title.value = self.project.get("title") or self.project.get("name", "Modpack")
        self.summary.value = self.project.get("description", "")
        self.subtitle.value = " · ".join(value for value in (
            "CurseForge" if str(self.project.get("project_id") or self.project.get("id", "")).startswith("cf:") else "Modrinth",
            self.project.get("author", ""), f"{len(self.versions)} versions" if self._loaded else "") if value)
        self.icon.content = _artwork(self.project.get("icon_url"), 64)
        self.install_button.disabled = self.loading or bool(self.error) or not self.versions or not self._can_install()
        self.cancel_button.visible = self.operation_phase in {"preparing", "installing", "cancelling"}
        self.cancel_button.disabled = self.operation_phase == "cancelling"
        self.install_button.visible = not self.cancel_button.visible
        for key, button in self.section_buttons.items():
            button.style.bgcolor = ft.Colors.BLACK_26 if key == self.section else ft.Colors.TRANSPARENT
            button.content.color = ACCENT if key == self.section else MUTED
        if self.loading or self.error:
            controls = [_text(self.error or "Loading pack details...", 14, color="#e57373" if self.error else MUTED)]
            if self.error:
                controls.append(launcher_button("Retry", self._retry))
            self.body.content = ft.Column(controls, spacing=12, scroll=ft.ScrollMode.AUTO)
        elif self.section == "versions":
            self._render_versions()
            self.body.content = self.versions_panel
        elif self.section == "gallery":
            gallery = self.project.get("gallery") or []
            self.body.content = ft.Column([
                ft.Container(content=ft.Image(src=item["url"], fit=ft.BoxFit.CONTAIN, height=220,
                             error_content=_text("Image unavailable", 12)), border_radius=5,
                             clip_behavior=ft.ClipBehavior.ANTI_ALIAS)
                for item in gallery if isinstance(item, dict) and item.get("url")
            ] or [_text("No screenshots supplied for this pack.", 14)], spacing=16, scroll=ft.ScrollMode.AUTO,
               horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        else:
            base_url = "https://www.curseforge.com/" if self.project.get("provider") == "curseforge" else "https://modrinth.com/"
            links = [("Pack website", self.project.get("project_url")), ("Source", self.project.get("source_url")),
                     ("Issues", self.project.get("issues_url")), ("Wiki", self.project.get("wiki_url"))]
            self.body.content = ft.Column([
                ft.Row([launcher_button(label, lambda e, link=url: open_link(link)) for label, url in links if url], wrap=True, spacing=8),
                markdown(self.project.get("body") or self.project.get("description", ""), base_url=base_url),
            ], spacing=16, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def _filter_versions(self, e) -> None:
        self.version_offset = 0
        self._render_versions()
        _update(self)

    def _paginate_versions(self, direction: int) -> None:
        self.version_offset = max(0, self.version_offset + direction * self.PAGE_SIZE)
        self._render_versions()
        _update(self)

    def _render_versions(self) -> None:
        query = (self.version_search.value or "").strip().casefold()
        versions = [version for version in self.versions
                    if (self.release_filter.value == "all" or version.get("version_type") == self.release_filter.value)
                    and (not query or query in (str(version.get("name", "")) + " " + str(version.get("version_number", ""))).casefold())]
        maximum = max(0, (len(versions) - 1) // self.PAGE_SIZE * self.PAGE_SIZE)
        self.version_offset = min(self.version_offset, maximum)
        rows = []
        for version in versions[self.version_offset:self.version_offset + self.PAGE_SIZE]:
            details = " · ".join(filter(None, [str(version.get("version_type", "")),
                ", ".join(version.get("game_versions", [])), ", ".join(version.get("loaders", [])),
                str(version.get("date_published", ""))[:10]]))
            rows.append(card_surface(ft.Row([
                ft.Column([_text(version.get("name") or version.get("version_number", "Version"), 17, pixel=True, color=ACCENT,
                                 max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                           _text(details, 12, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)], expand=True, spacing=4),
                launcher_button("Install", lambda e, selected=version: self._on_install(self.project, selected),
                                primary=True, disabled=not self._can_install()),
            ], spacing=12), glow_color="#454641", horizontal_padding=16,
            data=version["id"], key=ft.ValueKey("pack-version:" + version["id"])))
        self.version_list.set_cards(rows, footer=_text("No matching pack versions. Try All releases or another search.", 13) if not rows else None)
        self.version_previous.disabled = self.version_offset == 0
        self.version_next.disabled = self.version_offset + self.PAGE_SIZE >= len(versions)
        self.version_footer.visible = bool(versions)
        self.version_label.value = f"{self.version_offset // self.PAGE_SIZE + 1} / {max(1, (len(versions) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)}"


class ModpacksPage(ft.Container):
    PAGE_SIZE = 20

    def __init__(self, page: ft.Page, *, get_profiles: Callable, is_running: Callable,
                 notify: Callable, dialogs: Callable, open_folder: Callable, on_changed: Callable,
                 on_manage_mods: Callable, on_content_change: Callable, configure_curseforge: Callable,
                 on_play: Callable | None = None, client=None, runtime_directory: Path | None = None,
                 other_busy: Callable = lambda: False, progress: ProgressHost | None = None,
                 entrance: StaggeredReveal | None = None, installer: PackInstaller | None = None):
        self._page = page
        self._get_profiles = get_profiles
        self._is_running = is_running
        self._show_notification = notify
        self._dialogs = dialogs
        self._open_folder = open_folder
        self._on_changed = on_changed
        self._on_manage_mods = on_manage_mods
        self._on_play = on_play
        self._other_busy = other_busy
        self._progress = progress
        self._entrance = entrance
        self._runtime_directory = runtime_directory
        self.client = client or ModPlatformClient()
        self._installer = installer
        self._active = False
        self._generation = 0
        self._operation_token = 0
        self._pending_load = None
        self._load_activity = None
        self._operation_activity = None
        self._cancel_event: threading.Event | None = None
        self._plan: PackPlan | None = None
        self._phase = "idle"
        self.busy = False
        self.loading = False
        self.error = ""
        self.view = "browse"
        self.provider = "modrinth"
        self.queries = {"browse": "", "installed": ""}
        self._scrolls = {"browse": 0.0, "installed": 0.0}
        self._provider_states = {}
        self.hits = []
        self.total_hits = 0
        self.search_offset = 0
        self._details_open = False
        self._row_cache = {}
        self.heading = _text("Modpacks", 32, pixel=True, color=ACCENT)
        self.content_selector = AcrylicSelector(CONTENT_OPTIONS, "modpacks", on_content_change, width=160, tooltip="Content type")
        self.import_button = launcher_button("Import pack", self._import_archive, primary=True)
        self.header = ft.Row([self.heading, self.content_selector, ft.Container(expand=True)],
                             spacing=16, height=54, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.browse_tab = launcher_button("Discover", lambda e: self.set_view("browse"))
        self.installed_tab = launcher_button("Installed", lambda e: self.set_view("installed"))
        self.attribution = _text("Powered by Modrinth", 11)
        self.cancel_button = launcher_button("Cancel", self._cancel_operation, visible=False)
        self.tabs = ft.Row([self.browse_tab, self.installed_tab, ft.Container(expand=True), self.cancel_button, self.attribution], spacing=8)
        self.search = search_input("Search modpacks", on_change=self._search_changed, on_submit=self._search_submitted)
        self.provider_selector = AcrylicSelector(PROVIDER_OPTIONS, "modrinth", self._provider_changed, width=160, tooltip="Pack provider")
        self.sort = AcrylicSelector(SORT_OPTIONS, "downloads", self._sort_changed, width=212, tooltip="Sort modpacks", prefix="Sort:")
        self.key_button = launcher_icon_button(icon=ft.Icons.KEY_SHARP, icon_color=ACCENT, icon_size=20, tooltip="Set up CurseForge API key", on_click=configure_curseforge)
        self.refresh_button = launcher_icon_button(icon=ft.Icons.REFRESH_SHARP, icon_color=ACCENT, icon_size=20, tooltip="Refresh modpacks", on_click=self._refresh_requested)
        self.toolbar = ft.Row([self.search, self.provider_selector, self.sort, self.key_button, self.import_button, self.refresh_button], spacing=12)
        self.status = _text("", 12, expand=True)
        self.previous = launcher_icon_button(icon=ft.Icons.CHEVRON_LEFT_SHARP, tooltip="Previous page", on_click=lambda e: self._paginate(-1))
        self.next = launcher_icon_button(icon=ft.Icons.CHEVRON_RIGHT_SHARP, tooltip="Next page", on_click=lambda e: self._paginate(1))
        self.page_label = _text("", 12)
        self.pagination = ft.Row([self.previous, self.page_label, self.next], spacing=6, tight=True)
        self.status_row = ft.Row([self.status, self.pagination], spacing=12)
        self.list_view = ModList(self._cache_live_rows)
        self.list_body = ft.Container(expand=True, content=self.list_view)
        self.list_host = profile_list_surface(self.list_body)
        self.listing = ft.Container(expand=True, content=ft.Column([self.header, self.tabs, self.toolbar, self.status_row, self.list_host],
                                   expand=True, spacing=12, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        self.details_page = ModpackDetailsPage(page, client=self.client, on_back=self.close_details,
                                             on_install=lambda project, version: self._page.run_task(self._prepare_install, project, version),
                                             can_install=self._can_operate, on_cancel=self._cancel_operation, progress=progress)
        self.details_host = ft.Container(expand=True, visible=False, content=self.details_page)
        self.file_picker = ft.FilePicker()
        page.services.append(self.file_picker)
        super().__init__(expand=True, visible=False, content=ft.Stack([self.listing, self.details_host], expand=True))
        self.reveal = StaggeredReveal(page, self.list_host)
        self._render()

    @property
    def installer(self) -> PackInstaller:
        if self._installer is None:
            root = self._runtime_directory
            if root is None:
                from launcher import minecraft_directory
                root = Path(minecraft_directory)
            self._installer = PackInstaller(root, client=self.client)
        return self._installer

    @property
    def reveal_controls(self) -> list[ft.Control]:
        return self.details_page.reveal_controls if self._details_open else [self.header, self.tabs, self.toolbar, self.list_host]

    def _can_operate(self) -> bool:
        return not self.busy and not self._other_busy() and not self._is_running()

    def _update(self) -> None:
        if self._active:
            _update(self)

    def enter(self, *, defer_load: bool = False) -> None:
        if not self._active:
            self.list_view.reset_viewport()
        self._active = True
        if self._details_open:
            self.details_page.resume()
            self._render()
        else:
            self.refresh(defer_load=defer_load)

    def leave(self) -> None:
        self._active = False
        self._generation += 1
        self._scrolls[self.view] = self.list_view._scroll_pixels
        self._pending_load = None
        if self._load_activity:
            self._load_activity.finish()
            self._load_activity = None
        self.loading = False
        self.reveal.finish(update=False)
        self.details_page.leave()
        if self._phase in {"review", "archive-review"}:
            self._close_review()
        elif self._phase in {"preparing", "cancelling"} and self._plan is None:
            self._cancel_event.set()
            self._operation_token += 1
            self.busy = False
            self._phase = "idle"
            if self._operation_activity:
                self._operation_activity.finish()
                self._operation_activity = None

    def refresh(self, *, defer_load: bool = False) -> None:
        if self.busy:
            self._render()
            self._update()
            return
        self._generation += 1
        if self._load_activity:
            self._load_activity.finish()
            self._load_activity = None
        self._pending_load = None
        self.error = ""
        self.loading = self.view == "browse"
        self._render()
        self._update()
        if self.loading:
            self._pending_load = (self._generation, self.provider, self.search.value or "", self.sort.value, self.search_offset)
            if not defer_load:
                self.start_loading()
        elif self._scrolls[self.view]:
            self._page.run_task(self._restore_scroll, self._generation)

    def start_loading(self) -> None:
        pending, self._pending_load = self._pending_load, None
        if pending is not None:
            activity = track_progress(self._progress, "Finding modpacks...", priority=40)
            self._load_activity = activity
            self._page.run_task(self._load, *pending, activity)

    async def _load(self, generation: int, provider: str, query: str, sort: str, offset: int, activity) -> None:
        try:
            data = await asyncio.to_thread(self.client.search_modpacks, query, provider=provider, index=sort, offset=offset, limit=self.PAGE_SIZE)
            if self._entrance:
                await self._entrance.wait_until_finished()
            if generation != self._generation or not self._active:
                return
            self.hits = data["hits"]
            self.total_hits = int(data.get("total_hits", len(self.hits)))
            self.loading = False
            empty = self.list_body.content is not self.list_view
            self._render()
            self._update()
            if empty and not self._details_open:
                self.reveal.show(self.list_body, [self.list_body])
            if self._scrolls[self.view]:
                self._page.run_task(self._restore_scroll, generation)
        except Exception as error:
            if generation == self._generation and self._active:
                self.loading = False
                self.error = str(error)
                self._render()
                self._update()
        finally:
            activity.finish()
            if self._load_activity is activity:
                self._load_activity = None

    async def _restore_scroll(self, generation: int) -> None:
        await asyncio.sleep(0.06)
        if generation == self._generation and self._active and not self._details_open:
            try:
                await self.list_view.scroll_to(offset=self._scrolls[self.view], duration=0)
            except RuntimeError:
                pass

    def set_view(self, view: str) -> None:
        if view not in self.queries or view == self.view or self.busy:
            return
        self.queries[self.view] = self.search.value or ""
        self._scrolls[self.view] = self.list_view._scroll_pixels
        self.view = view
        self.search.value = self.queries[view]
        self.refresh()
        self.reveal.show(self.list_body, [self.list_body])

    def _provider_changed(self, e) -> None:
        selected = self.provider_selector.value
        if selected == self.provider or self.busy:
            return
        self._provider_states[self.provider] = (self.search.value or "", self.sort.value, self.search_offset,
                                                self.hits, self.total_hits, self.list_view._scroll_pixels)
        self.provider = selected
        query, sort, self.search_offset, self.hits, self.total_hits, scroll = self._provider_states.get(selected, ("", "downloads", 0, [], 0, 0.0))
        self.queries["browse"] = query
        self.search.value = query
        self.sort.refresh_options(SORT_OPTIONS, sort)
        self._scrolls["browse"] = scroll
        self.refresh()

    def _sort_changed(self, e) -> None:
        self.search_offset = 0
        self._scrolls["browse"] = 0
        self.refresh()

    def _search_changed(self, e) -> None:
        if self.busy:
            return
        self.queries[self.view] = self.search.value or ""
        self._scrolls[self.view] = 0
        if self.view == "installed":
            self._render()
            self._update()
            return
        self._generation += 1
        if self._load_activity:
            self._load_activity.finish()
            self._load_activity = None
        self._page.run_task(self._debounced_search, self._generation)

    async def _debounced_search(self, generation: int) -> None:
        await asyncio.sleep(0.35)
        if generation == self._generation and self._active and not self.busy:
            self.search_offset = 0
            self.refresh()

    def _search_submitted(self, e) -> None:
        if not self.busy:
            self.search_offset = 0
            self._scrolls[self.view] = 0
            self.refresh()

    def _paginate(self, direction: int) -> None:
        if not self.loading and not self.busy:
            self.search_offset = max(0, self.search_offset + direction * self.PAGE_SIZE)
            self._scrolls["browse"] = 0
            self.refresh()

    def _refresh_requested(self, e) -> None:
        if self.busy:
            return
        clear = getattr(self.client, "clear_cache", None)
        if callable(clear):
            clear()
        self.refresh()

    def open_details(self, hit: dict) -> None:
        if self.busy:
            return
        self._scrolls[self.view] = self.list_view._scroll_pixels
        self._details_open = True
        self.listing.visible = False
        self.details_host.visible = True
        self.details_page.open(hit)
        self._update()

    def close_details(self) -> None:
        self.details_page.leave()
        self._details_open = False
        self.listing.visible = True
        self.details_host.visible = False
        self._render()
        self._update()
        self._page.run_task(self._restore_scroll, self._generation)

    def _installed_profiles(self) -> list[dict]:
        return [profile for profile in self._get_profiles()
                if profile.get("is_instance") and isinstance(profile.get("modpack"), dict)]

    def _empty(self, title: str, description: str, action: ft.Control | None = None) -> None:
        controls = [ft.Icon(ft.Icons.INVENTORY_2_OUTLINED, size=44, color=ACCENT),
                    _text(title, 22, pixel=True, color=ACCENT), _text(description, 13, text_align=ft.TextAlign.CENTER)]
        if action:
            controls.append(action)
        self.list_view.set_items([], self._row)
        self.list_body.content = ft.Container(expand=True, alignment=ft.Alignment.CENTER, padding=24,
            content=ft.Column(controls, spacing=12, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER))

    def _render(self) -> None:
        browse = self.view == "browse"
        packs = self._installed_profiles()
        locked = self.busy or self._other_busy()
        self.content_selector.disabled = locked
        self.import_button.disabled = locked or self._is_running()
        self.import_button.visible = not browse
        self.import_button.disabled = locked or self._is_running()
        self.browse_tab.disabled = self.installed_tab.disabled = locked
        self.search.disabled = self.provider_selector.disabled = self.sort.disabled = locked
        self.key_button.disabled = locked
        self.refresh_button.disabled = locked or self.loading
        self.provider_selector.visible = self.sort.visible = browse
        self.key_button.visible = browse and self.provider == "curseforge"
        self.search.hint_text = "Search modpacks" if browse else "Search installed packs"
        self.attribution.visible = browse
        self.attribution.value = "Powered by CurseForge" if self.provider == "curseforge" else "Powered by Modrinth"
        self.installed_tab.content.value = f"Installed · {len(packs)}" if packs else "Installed"
        for button, selected in ((self.browse_tab, browse), (self.installed_tab, not browse)):
            button.style.bgcolor = ft.Colors.BLACK_26 if selected else ft.Colors.TRANSPARENT
            button.content.color = ACCENT if selected else MUTED
        self.cancel_button.visible = self.busy and self._phase in {"preparing", "installing", "cancelling"}
        self.cancel_button.disabled = self._phase == "cancelling"
        self.status.value = "Canceling modpack installation..." if self._phase == "cancelling" else "Installing modpack..." if self._phase == "installing" else "Reading modpack..." if self._phase == "preparing" else "Finding modpacks..." if self.loading else f"{self.total_hits:,} modpacks" if browse else f"{len(packs)} installed packs"
        self.pagination.visible = browse and self.total_hits > self.PAGE_SIZE and not self.error
        self.previous.disabled = locked or self.loading or self.search_offset == 0
        self.next.disabled = locked or self.loading or self.search_offset + self.PAGE_SIZE >= self.total_hits
        self.page_label.value = f"{self.search_offset // self.PAGE_SIZE + 1} / {max(1, (self.total_hits + self.PAGE_SIZE - 1) // self.PAGE_SIZE)}"
        if self.error:
            self._empty("Could not load modpacks", self.error, launcher_button("Retry", self._refresh_requested))
        elif browse and self.loading and not self.hits:
            self._empty("Finding modpacks", "Choose a pack for your next adventure.")
        else:
            query = (self.search.value or "").strip().casefold()
            items = self.hits if browse else [profile for profile in packs
                      if not query or query in (profile["name"] + " " + str(profile["modpack"].get("name", ""))).casefold()]
            self.list_body.content = self.list_view
            self.list_view.set_items(items, self._row, offset=self._scrolls[self.view] or self.list_view._scroll_pixels)
            if not items:
                self._empty("No modpacks found" if browse else "Your next adventure starts here",
                            "Try another search or provider." if browse else "Discover a modpack or import a .mrpack / CurseForge ZIP.",
                            None if browse else launcher_button("Discover modpacks", lambda e: self.set_view("browse")))
        if self._details_open:
            self.details_page.operation_phase = self._phase
            self.details_page.render()

    def _cache_live_rows(self) -> None:
        # ProfileList retains the mutable row when Flet reconciles an existing key.
        # Cache that live object so later busy-state changes never reuse a frozen copy.
        live = {card.data for card in self.list_view.cards}
        for card in self.list_view.cards:
            signature, _ = self._row_cache[card.data]
            self._row_cache[card.data] = (signature, card)
        for key in list(self._row_cache):
            if len(self._row_cache) <= 64:
                break
            if key not in live:
                del self._row_cache[key]

    def _row(self, item: dict) -> ft.Container:
        installed = self.view == "installed"
        identifier = item["id"] if installed else item.get("project_id") or item["id"]
        key = (self.view, identifier)
        packs = self._installed_profiles()
        matching = None
        if not installed:
            project_id = item.get("project_id") or item.get("id")
            matching = next((p for p in packs if p.get("modpack", {}).get("project_id") == project_id), None)
            matching_id = matching.get("id") if matching else None
            signature = (identifier, item.get("title"), item.get("description"), item.get("icon_url"),
                         item.get("author"), item.get("downloads"), matching_id, self.busy,
                         self._is_running(), self._other_busy())
        else:
            pack = item.get("modpack", {})
            signature = (identifier, item.get("name"), pack.get("version"), item.get("minecraft_version"),
                         item.get("loader"), item.get("loader_version"), item.get("icon"), self.busy,
                         self._is_running(), self._other_busy())
        cached = self._row_cache.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        if installed:
            pack = item["modpack"]
            title = item["name"]
            description = f"{pack.get('name', title)} · Pack {pack.get('version', 'Imported')}"
            detail = " · ".join(str(value) for value in (item.get("minecraft_version"), item.get("loader"), item.get("loader_version")) if value)
            image = item.get("icon")
            actions = [launcher_icon_button(icon=ft.Icons.PLAY_ARROW_SHARP, tooltip="Play modpack",
                        on_click=lambda e: self._on_play(item), disabled=not self._can_operate() or self._on_play is None)]
            if str(item.get("loader", "")).lower() in {"fabric", "forge", "neoforge", "quilt"}:
                actions.append(launcher_icon_button(icon=ft.Icons.EXTENSION_SHARP, tooltip="Manage pack mods",
                    on_click=lambda e: self._on_manage_mods(item), disabled=self.busy))
            actions.append(launcher_icon_button(icon=ft.Icons.FOLDER_OPEN_SHARP, tooltip="Open instance folder",
                            on_click=lambda e: self._open_folder(Path(item["game_directory"]))))
            on_open = (lambda e: self.open_details({"project_id": pack["project_id"], "title": pack.get("name", title), "icon_url": image})) if pack.get("project_id") else None
        else:
            title = item.get("title") or item.get("name", "Modpack")
            description = item.get("description", "")
            detail = " · ".join(filter(None, [item.get("author", ""), f"{_downloads(int(item.get('downloads', 0)))} downloads"]))
            image = item.get("icon_url")
            if matching:
                actions = [launcher_icon_button(
                    icon=ft.Icons.CHECK_SHARP, icon_color=MUTED, primary=False,
                    width=44, height=44, icon_size=22,
                    tooltip=f"Installed as '{matching.get('name')}'", disabled=True,
                    on_click=lambda e: None,
                )]
            else:
                actions = [launcher_icon_button(
                    icon=ft.Icons.DOWNLOAD_ROUNDED, icon_color=NAVY, primary=True,
                    tooltip="Install modpack into a new instance", width=44, height=44, icon_size=22,
                    disabled=not self._can_operate(),
                    on_click=lambda e: self._page.run_task(self._prepare_install, item),
                )]
            on_open = lambda e: self.open_details(item)
        info = ft.Column([
            _text(title, 18, pixel=True, color=ACCENT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            _text(description, 13, color="#d1d7e2", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            _text(detail, 12, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        ], spacing=4, expand=True)
        body = ft.GestureDetector(
            content=ft.Row([_artwork(image, size=48), info], spacing=16),
            expand=True, on_tap=on_open,
            tooltip="View modpack" if on_open else "Imported modpack",
            mouse_cursor=ft.MouseCursor.CLICK if on_open else ft.MouseCursor.BASIC,
        )
        row = card_surface(
            ft.Row([body, *actions], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            key=ft.ValueKey(f"modpack:{self.view}:{identifier}"),
            data=key,
        )
        self._row_cache[key] = (signature, row)
        return row

    def _begin_operation(self):
        if not self._can_operate():
            self._show_notification("Wait for the current operation or close Minecraft before installing a pack.", "warning", "Modpacks")
            return None
        self._operation_token += 1
        self._cancel_event = threading.Event()
        self._phase = "preparing"
        self.busy = True
        self._generation += 1
        if self._load_activity:
            self._load_activity.finish()
            self._load_activity = None
        self._pending_load = None
        self.loading = False
        self._operation_activity = track_progress(self._progress, "Reading modpack...", priority=80)
        self._render()
        self._update()
        return self._operation_token, self._cancel_event, self._operation_activity

    def _current_operation(self, token: int) -> bool:
        return token == self._operation_token and self._active

    async def _import_archive(self, e) -> None:
        if not self._can_operate():
            return
        try:
            files = await self.file_picker.pick_files(dialog_title="Import Minecraft modpack", allow_multiple=False,
                file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["mrpack", "zip"])
            if not files or not self._active:
                return
            if not files[0].path:
                raise ValueError("Importing local modpacks requires the desktop launcher.")
            operation = self._begin_operation()
            if operation is None:
                return
            token, cancel_event, activity = operation
            plan = None
            try:
                plan = await asyncio.to_thread(self.installer.prepare_local, Path(files[0].path),
                                               progress=activity, cancel_event=cancel_event)
                if self._current_operation(token):
                    await self._show_review(plan, token)
                else:
                    self.installer.discard(plan)
            except PackCancelled:
                if self._current_operation(token):
                    self._show_notification("Modpack import canceled.", "info", "Modpacks")
            except Exception as error:
                if self._current_operation(token):
                    self._show_notification(str(error), "error", "Cannot import modpack")
            finally:
                activity.finish()
                if token == self._operation_token and self._phase in {"preparing", "cancelling"}:
                    self.busy = False
                    self._phase = "idle"
                    self._render()
                    self._update()
        except Exception as error:
            self._show_notification(str(error), "error", "Cannot import modpack")

    async def _prepare_install(self, project: dict, version: dict | None = None) -> None:
        operation = self._begin_operation()
        if operation is None:
            return
        token, cancel_event, activity = operation
        plan = None
        try:
            project_id = project.get("project_id") or project["id"]
            if version is None:
                versions = await asyncio.to_thread(self.client.modpack_versions, project_id)
                version = next((item for item in versions if item.get("version_type") == "release"), versions[0] if versions else None)
                if version is None:
                    raise ValueError("This pack has no supported client release.")
            if not self._current_operation(token):
                return
            full_project, selected = await asyncio.gather(asyncio.to_thread(self.client.modpack_project, project_id),
                                                         asyncio.to_thread(self.client.modpack_version, version["id"]))
            if not self._current_operation(token):
                return
            provider = "curseforge" if str(project_id).startswith("cf:") else "modrinth"
            file = self.installer.archive_file(selected, provider)
            if file.get("manual_required") and await asyncio.to_thread(self.client.curseforge.manual_file, file) is None:
                self._show_manual_archive(full_project, selected, file, token)
                return
            plan = await asyncio.to_thread(self.installer.prepare_remote, full_project, selected,
                                           progress=activity, cancel_event=cancel_event)
            if self._current_operation(token):
                await self._show_review(plan, token)
            else:
                self.installer.discard(plan)
        except PackCancelled:
            if self._current_operation(token):
                self._show_notification("Modpack preparation canceled.", "info", "Modpacks")
        except Exception as error:
            if self._current_operation(token):
                self._show_notification(str(error), "error", "Cannot install modpack")
            if plan:
                self.installer.discard(plan)
        finally:
            activity.finish()
            if token == self._operation_token and self._phase in {"preparing", "cancelling"}:
                self.busy = False
                self._phase = "idle"
                self._render()
                self._update()

    def _close_review(self, e=None) -> None:
        self._operation_token += 1
        if self._cancel_event:
            self._cancel_event.set()
        if self._plan:
            self.installer.discard(self._plan)
            self._plan = None
        self._phase = "idle"
        self.busy = False
        self._dialogs().close()
        self._render()
        self._update()

    def _show_manual_archive(self, project: dict, version: dict, file: dict, token: int) -> None:
        self._phase = "archive-review"
        self.busy = False
        self._render()
        self._update()

        async def select(e) -> None:
            try:
                files = await self.file_picker.pick_files(dialog_title="Select the downloaded CurseForge pack ZIP",
                    file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["zip"], allow_multiple=False)
                if not files or not self._current_operation(token) or self._phase != "archive-review":
                    return
                if not files[0].path:
                    raise ValueError("Selecting manual downloads requires the desktop launcher.")
                await asyncio.to_thread(self.client.curseforge.register_manual_files, [file], [Path(files[0].path)])
                if self._current_operation(token):
                    self._dialogs().close()
                    self._phase = "idle"
                    await asyncio.sleep(0.2)
                    await self._prepare_install(project, version)
            except Exception as error:
                if self._current_operation(token):
                    self._show_notification(str(error), "error", "Cannot verify pack archive")

        self._dialogs().show(ft.Column([
            _text("This pack archive requires an official CurseForge download.", 14, color="#d1d7e2"),
            launcher_button("Download " + file["filename"], lambda e: open_link(file.get("manual_url", ""))),
            _text("Select the downloaded ZIP to verify it and review the pack installation.", 13),
        ], spacing=16), title=_text("Download pack archive", 22, pixel=True, color=ACCENT),
            actions=[launcher_button("Cancel", self._close_review), launcher_button("Select downloaded ZIP", select, primary=True)],
            on_cancel=self._close_review, width=600, height=350)

    def _suggest_folder(self, plan: PackPlan) -> str:
        try:
            base = profile_creation.instance_folder_name("", plan.name)[:100]
        except ValueError:
            base = "Modpack"
        profiles = self.installer.storage.load_installations()
        for index in range(1, 10001):
            name = base if index == 1 else f"{base}_{index}"
            try:
                profile_creation.new_instance_directory(self.installer.minecraft_directory, name, name, profiles)
                return name
            except FileExistsError:
                continue
        raise ValueError("Choose a different instance folder name for this pack.")

    def _ready_manual_ids(self, files: list[dict]) -> set[str]:
        return {file["id"] for file in files if self.client.curseforge.manual_file(file) is not None}

    async def _show_review(self, plan: PackPlan, token: int) -> None:
        folder, ready_ids = await asyncio.gather(asyncio.to_thread(self._suggest_folder, plan),
                                                 asyncio.to_thread(self._ready_manual_ids, plan.manual_files()))
        if not self._current_operation(token):
            self.installer.discard(plan)
            return
        self._plan = plan
        self._phase = "review"
        self.busy = False

        def current() -> bool:
            return self._current_operation(token) and self._phase == "review" and self._plan is plan

        def manual_ready() -> bool:
            return all(file["id"] in ready_ids for file in plan.manual_files(tuple(review.optional_paths)))

        def render_review() -> None:
            review.render()
            install_button.disabled = review.checking or not manual_ready()
            _update(review)
            _update(install_button)

        async def optional_change(e, path: str) -> None:
            nonlocal ready_ids
            if not current() or review.checking:
                return
            if e.control.value:
                review.optional_paths.add(path)
            else:
                review.optional_paths.discard(path)
            review.checking = True
            render_review()
            try:
                ready_ids = await asyncio.to_thread(self._ready_manual_ids, plan.manual_files(tuple(review.optional_paths)))
            finally:
                review.checking = False
                if current():
                    render_review()

        async def select_manual(e) -> None:
            nonlocal ready_ids
            if not current() or review.checking:
                return
            review.checking = True
            render_review()
            try:
                files = await self.file_picker.pick_files(dialog_title="Select required CurseForge pack files",
                    file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["jar", "zip"], allow_multiple=True)
                if files and current():
                    paths = [Path(file.path) for file in files if file.path]
                    if len(paths) != len(files):
                        raise ValueError("Selecting manual downloads requires the desktop launcher.")
                    manual = plan.manual_files(tuple(review.optional_paths))
                    await asyncio.to_thread(self.client.curseforge.register_manual_files, manual, paths)
                    ready_ids = await asyncio.to_thread(self._ready_manual_ids, manual)
                    review.error = ""
            except Exception as error:
                review.error = str(error)
            finally:
                review.checking = False
                if current():
                    render_review()

        async def install(e) -> None:
            if not current() or review.checking or not manual_ready() or not self._can_operate():
                return
            name = (review.name_input.value or "").strip() or plan.name
            folder_name = (review.folder_input.value or "").strip()
            try:
                profile_creation.new_instance_directory(self.installer.minecraft_directory, folder_name, name,
                                                         self.installer.storage.load_installations())
            except (ValueError, FileExistsError) as error:
                review.error = str(error)
                render_review()
                return
            self._phase = "installing"
            self.busy = True
            self._cancel_event = threading.Event()
            cancel_event = self._cancel_event
            self._dialogs().close()
            await asyncio.sleep(0.2)
            self._render()
            self._update()
            activity = track_progress(self._progress, "Installing " + plan.name + "...", priority=100)
            self._operation_activity = activity
            try:
                profile = await asyncio.to_thread(self.installer.install, plan, name=name, folder_name=folder_name,
                    optional_paths=tuple(review.optional_paths), progress=activity, cancel_event=cancel_event,
                    can_commit=lambda: not self._is_running() and not self._other_busy())
                self._on_changed()
                options = {"action_label": "Play", "on_action": lambda e: self._on_play(profile)} if self._on_play else {}
                self._show_notification("Installed " + name + ".", "success", "Modpacks", **options)
                self.queries[self.view] = self.search.value or ""
                self.view = "installed"
                self.search.value = self.queries["installed"]
                self._scrolls["installed"] = 0
                self.close_details()
            except PackCancelled:
                self._show_notification("Modpack installation canceled.", "info", "Modpacks")
            except Exception as error:
                self._show_notification(str(error), "error", "Cannot install modpack")
            finally:
                activity.finish()
                self.installer.discard(plan)
                self._plan = None
                self.busy = False
                self._phase = "idle"
                self._operation_activity = None
                self._cancel_event = None
                self.refresh(defer_load=not self._active)

        review = ModpackInstallReview(plan, folder, on_optional_change=optional_change,
                                      on_manual_files=select_manual, manual_file_ready=lambda file: file["id"] in ready_ids)
        install_button = launcher_button("Install", install, primary=True, disabled=not manual_ready())
        self._render()
        self._update()
        self._dialogs().show(review, title=review.title, actions=[launcher_button("Cancel", self._close_review), install_button],
                             on_cancel=self._close_review, width=640, height=review.preferred_height)

    def _cancel_operation(self, e) -> None:
        if self._cancel_event and self.busy:
            self._cancel_event.set()
            self._phase = "cancelling"
            self._render()
            self._update()
