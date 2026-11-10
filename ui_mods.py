"""Profile-scoped installed mods and Modrinth browsing."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

import flet as ft

from mod_services import LocalMod, ModManager, ModrinthClient, ModUpdate, UpdateCheck, SUPPORTED_LOADERS
from ui_profiles import ProfileList, profile_card_surface, profile_list_surface, profile_row
from ui_controls import SimpleInput, AcrylicSelector, launcher_button, launcher_back_button, launcher_icon_button, rounded_artwork
from ui_motion import StaggeredReveal
from ui_mod_list import ModList
from ui_mod_details import ModDetailsPage
from ui_mod_install import ModInstallReview
from ui_progress import ProgressHost, ProgressTask, track_progress


ACCENT = "#f5de9a"
MUTED = "#929db1"
NAVY = "#182842"


def _text(value: str, size: int = 13, *, color: str = MUTED, pixel: bool = False, **kwargs) -> ft.Text:
    return ft.Text(value, size=size, color=color, font_family="ps" if pixel else None, **kwargs)


def _button(label: str, on_click: Callable, *, primary: bool = False, **kwargs) -> ft.Button:
    return launcher_button(label, on_click, primary=primary, **kwargs)


def _dropdown(options: list[tuple[str, str]], value: str | None, on_select: Callable, *, width: int = 160) -> AcrylicSelector:
    return AcrylicSelector(options, value, on_select, width=width)


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    return f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"


def _downloads(count: int) -> str:
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if count >= threshold:
            return f"{count / threshold:.1f}".removesuffix(".0") + suffix
    return str(count)


class ModsPage(ft.Container):
    PAGE_SIZE = 20
    ROW_CACHE_SIZE = 64

    def __init__(
        self, page: ft.Page, *, get_profiles: Callable[[], list[dict]],
        selected_profile: Callable[[], dict | None], game_directory: Callable[[dict], Path],
        is_running: Callable[[], bool], notify: Callable, dialogs: Callable,
        open_folder: Callable[[Path], None], create_profile: Callable[[], None],
        profile_icon: Callable[[dict], ft.Control], on_changed: Callable[[], None],
        client: ModrinthClient | None = None,
        profile_badges: Callable[[dict], list[ft.Control]] | None = None,
        profile_details: Callable[[dict], str] | None = None,
        progress: ProgressHost | None = None,
        entrance: StaggeredReveal | None = None,
        runtime_directory: Path | None = None,
    ):
        self._page = page
        self._get_profiles = get_profiles
        self._selected_profile = selected_profile
        self._game_directory = game_directory
        self._runtime_directory = runtime_directory
        self._is_running = is_running
        self._show_notification = notify
        self._dialogs = dialogs
        self._open_folder = open_folder
        self._create_profile = create_profile
        self._profile_icon = profile_icon
        self._profile_badges = profile_badges or (lambda profile: [])
        self._profile_details = profile_details or (lambda profile: " • ".join(
            str(value) for value in (profile.get("loader"), profile.get("minecraft_version")) if value))
        self._on_changed = on_changed
        self._progress = progress
        self._entrance = entrance
        self._pending_load: tuple | None = None
        self._load_activity: ProgressTask | None = None
        self._artwork_activities: set[ProgressTask] = set()
        self.client = client or ModrinthClient()
        self.profile: dict | None = None
        self.manager: ModManager | None = None
        self._managers: dict[Path, ModManager] = {}
        self.view = "installed"
        self.queries = {"installed": "", "browse": ""}
        self.local_mods: list[LocalMod] = []
        self.hits: list[dict] = []
        self.total_hits = 0
        self.search_offset = 0
        self.busy = False
        self.loading = False
        self.error = ""
        self._generation = 0
        self._active = False
        self._undo: tuple[ModManager, str, str] | None = None
        self._row_cache: dict[tuple, tuple[object, ft.Container]] = {}
        self._project_cache: dict[str, dict] = {}
        self._project_requests: set[str] = set()
        self._details_open = False
        self._selection_open = False
        self._saved_scroll = 0.0
        self._updates: dict[str, ModUpdate] = {}
        self._update_summary = ""
        self._update_filters: dict[tuple, str] = {}
        self.checking_updates = False
        self._update_activity: ProgressTask | None = None

        self.heading = _text("Mods", 32, color=ACCENT, pixel=True)
        self.select_profile_button = _button(
            "Select Profile", lambda e: self.open_profile_selection(),
            tooltip="Select Profile", height=54, width=320,
        )
        self.context = ft.Container(content=self.select_profile_button, width=320)
        self._render_profile_header()
        self.header = ft.Row([
            self.heading, ft.Container(expand=True), self.context,
        ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.installed_tab = _button("Installed", lambda e: self.set_view("installed"))
        self.browse_tab = _button("Discover", lambda e: self.set_view("browse"))
        self.attribution = _text("Powered by Modrinth", 11)
        self.update_button = _button("Check updates", self._updates_action)
        self.tabs = ft.Row([
            self.browse_tab, self.installed_tab, ft.Container(expand=True), self.update_button, self.attribution,
        ], spacing=8)
        self.search = SimpleInput(
            hint_text="Search mods", expand=True,
            hint_style=ft.TextStyle(color=MUTED),
            border={
                state: ft.OutlineInputBorder(border_radius=5, side=ft.BorderSide(0, ft.Colors.TRANSPARENT))
                for state in (ft.ControlState.DEFAULT, ft.ControlState.FOCUSED, ft.ControlState.DISABLED)
            },
            prefix_icon=ft.Icons.SEARCH_SHARP,
            on_change=self._search_changed, on_submit=self._search_submitted,
        )
        self.filter = _dropdown([("all", "All mods"), ("enabled", "Enabled"), ("disabled", "Disabled")], "all", self._filter_changed, width=150)
        self.sort = _dropdown([("downloads", "Popular"), ("relevance", "Relevance"), ("updated", "Recently updated"), ("newest", "Newest")], "downloads", self._sort_changed, width=212)
        self.sort.content.content.controls.insert(0, _text("Sort:", 13))
        self.sort.tooltip = "Sort mods"
        self.add_button = _button("Add JARs", self._import_files, primary=True)
        self.refresh_button = launcher_icon_button(
            icon=ft.Icons.REFRESH_SHARP, icon_color=ACCENT, icon_size=20,
            tooltip="Refresh mods", on_click=self._refresh_requested,
        )
        self.toolbar = ft.Row([self.search, self.filter, self.sort, self.add_button, self.refresh_button], spacing=12)
        self.status = _text("", 12, expand=True)
        self.list_view = ModList(self._cache_live_rows)
        self.list_host = profile_list_surface(self.list_view)
        # Animate a stable body; row reconciliation resets drag animations.
        self.list_body = ft.Container(expand=True, content=self.list_view)
        self.list_host.content = self.list_body
        self.previous = launcher_icon_button(icon=ft.Icons.CHEVRON_LEFT_SHARP, tooltip="Previous page", on_click=lambda e: self._paginate(-1))
        self.next = launcher_icon_button(icon=ft.Icons.CHEVRON_RIGHT_SHARP, tooltip="Next page", on_click=lambda e: self._paginate(1))
        self.page_label = _text("")
        self.pagination = ft.Row([
            self.previous, self.page_label, self.next,
        ], spacing=6, tight=True)
        self.status_row = ft.Row([
            self.status, self.pagination,
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.file_picker = ft.FilePicker()
        page.services.append(self.file_picker)
        self.listing = ft.Container(expand=True, content=ft.Column(
                expand=True, spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[self.header, self.tabs, self.toolbar,
                          self.status_row, self.list_host],
            ))
        self.details_page = ModDetailsPage(
            page, client=self.client, get_profile=lambda: self.profile, get_manager=lambda: self.manager,
            can_change=lambda: self._compatible() and not self._operation_in_progress and not self._is_running(),
            on_back=self.close_details,
            on_install=lambda hit, version: self._page.run_task(self._prepare_install, hit, version, self.details_page._profile_id),
            on_toggle=lambda mod, manager: self._page.run_task(self._toggle, mod, not mod.enabled, manager),
            on_remove=lambda mod, manager: self._confirm_remove(mod, manager),
            progress=progress, on_release_filter_changed=lambda: self._update_release_filter(None),
        )
        self.details_host = ft.Container(expand=True, visible=False, content=self.details_page)
        self.selection_heading = ft.Row([
            launcher_back_button(lambda e: self.close_profile_selection(), tooltip="Back to Mods"),
            _text("Select Profile", 32, color=ACCENT, pixel=True),
        ], spacing=12)
        self.selection_note = _text("Choose the profile whose mods you want to manage.")
        self.selection_list = ProfileList(on_order_changed=lambda ids: False)
        self.selection_surface = profile_list_surface(self.selection_list)
        self.selection_host = ft.Container(expand=True, visible=False, content=ft.Column([
            self.selection_heading, self.selection_note, self.selection_surface,
        ], expand=True, spacing=12, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        super().__init__(expand=True, padding=ft.Padding.only(left=20, right=24, top=10, bottom=8),
                         content=ft.Stack([self.listing, self.details_host, self.selection_host], expand=True))
        self.list_reveal = StaggeredReveal(page, self.list_host)
        self.selection_reveal = StaggeredReveal(page, self.selection_host)
        self._render()

    @property
    def _operation_in_progress(self) -> bool:
        return self.busy or self.checking_updates

    @property
    def reveal_controls(self) -> list[ft.Control]:
        if self._selection_open:
            return [self.selection_heading, self.selection_note, self.selection_surface]
        if self._details_open:
            return self.details_page.reveal_controls
        return [self.header, self.tabs, self.toolbar, self.list_host]

    def did_mount(self) -> None:
        # The surrounding Home view can still be removed by a full-page route.
        # Restore a deep scroll after the client has actually mounted the list.
        if self._active and self._saved_scroll and not self._details_open and not self._selection_open:
            self._page.run_task(self._restore_scroll, self._generation, self._saved_scroll)

    def _update(self) -> None:
        if self._active:
            try:
                # Cached pages can retain a Page reference after navigation.
                # Prepare their state without patching an unmounted client ID.
                if self.page.session.index.get(self._i) is not self:
                    return
                self.update()
            except RuntimeError:
                pass

    def enter(self, profile_id: str | None = None, *, defer_load: bool = False) -> None:
        if not self._active:
            # A cached native scroll viewport can retain an empty painted
            # window after its parent fades back in. Recreate the viewport
            # on return, retaining the bounded row cache and saved scroll.
            self.list_view.reset_viewport()
        self._active = True
        profiles = self._get_profiles()
        desired = profile_id or (self.profile or {}).get("id") or (self._selected_profile() or {}).get("id")
        target = next((item for item in profiles if item.get("id") == desired), None)
        if target is None:
            target = next((item for item in profiles if str(item.get("loader", "")).lower() in SUPPORTED_LOADERS), profiles[0] if profiles else None)
        self._set_profile(target, defer_load=defer_load)
        if profile_id:
            self.close_profile_selection()
            self.close_details()
        elif self._selection_open:
            self._render_profile_selection()
        elif self._details_open:
            self.details_page.resume()
        if self._saved_scroll:
            self._page.run_task(self._restore_scroll, self._generation, self._saved_scroll)

    def leave(self) -> None:
        self._finish_update_activity()
        self._active = False
        self._generation += 1
        self.loading = False
        self._pending_load = None
        self._finish_load_activity()
        for activity in self._artwork_activities:
            activity.finish()
        self._artwork_activities.clear()
        self._saved_scroll = self.list_view._scroll_pixels
        self.list_reveal.finish(update=False)
        self.selection_reveal.finish(update=False)
        self.details_page.suspend()

    def _render_profile_header(self) -> None:
        profile = self.profile or {"name": "Choose a profile"}
        summary = " · ".join(str(value) for value in (
            profile.get("loader"), profile.get("minecraft_version")) if value)
        self.select_profile_button.content = ft.Row([
            rounded_artwork(self._profile_icon(profile), size=28),
            ft.Column([
                _text(profile.get("name", "Unnamed profile"), 16, color="#e5ebf4", pixel=True,
                      max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                _text(summary or "Select an instance", 11, max_lines=1,
                      overflow=ft.TextOverflow.ELLIPSIS),
            ], expand=True, spacing=1, tight=True),
            ft.Icon(ft.Icons.EXPAND_MORE_SHARP, size=18, color=ACCENT),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.select_profile_button.style.bgcolor = ft.Colors.BLACK_26

    def _profile_row(self, profile: dict, actions: list[ft.Control], *, selected=False,
                     on_select: Callable | None = None) -> ft.Container:
        def body(icon, info):
            return ft.GestureDetector(
                content=ft.Row([icon, info], spacing=12,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
                expand=True, mouse_cursor=ft.MouseCursor.CLICK, on_tap=on_select,
            )
        return profile_row(
            profile, icon=self._profile_icon(profile), badges=self._profile_badges(profile),
            details=self._profile_details(profile), actions=actions, glow_color=ACCENT,
            selected=selected, body_builder=body if on_select else None,
        )

    def _render_profile_selection(self) -> None:
        cards = []
        for profile in self._get_profiles():
            pid = profile["id"]
            selected = pid == (self.profile or {}).get("id")
            choose = lambda e, profile_id=pid: self.select_profile(profile_id)
            card = ft.Container(
                key=ft.ValueKey(f"mods-profile-{pid}"), data=pid,
                margin=ft.Margin.only(bottom=8),
                border=ft.Border.all(1, ACCENT if selected else ft.Colors.TRANSPARENT),
                border_radius=5,
                animate=ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC),
                content=self._profile_row(profile, [
                    _button("Selected" if selected else "Select", choose,
                            disabled=selected or self._operation_in_progress),
                ], selected=selected, on_select=choose),
            )
            cards.append(card)
        self.selection_list.set_cards(cards, footer=None if cards else ft.Container(
            padding=24, content=_text("No profiles yet. Create an instance in Library to get started.")), update=False)

    def open_profile_selection(self) -> None:
        if self._operation_in_progress or self._details_open or self._selection_open:
            return
        self._saved_scroll = self.list_view._scroll_pixels
        self._selection_open = True
        self.list_reveal.finish(update=False)
        self._render_profile_selection()
        self.listing.visible = False
        self.selection_host.visible = True
        self._update()
        self.selection_reveal.show(self.selection_host.content, self.reveal_controls)

    def close_profile_selection(self) -> None:
        was_open = self._selection_open
        self._selection_open = False
        self.selection_reveal.finish(update=False)
        self.selection_host.visible = False
        self.listing.visible = not self._details_open
        self._update()
        if was_open and self._active and self._saved_scroll:
            self._page.run_task(self._restore_scroll, self._generation, self._saved_scroll)

    def select_profile(self, profile_id: str) -> None:
        if self._operation_in_progress or not self._selection_open:
            return
        target = next((item for item in self._get_profiles() if item["id"] == profile_id), None)
        if target is None:
            self._render_profile_selection()
            self._show_notification("This profile is no longer available.", "warning")
            self._update()
            return
        if profile_id != (self.profile or {}).get("id"):
            self._saved_scroll = 0
        self.close_profile_selection()
        if target == self.profile:
            return
        self._set_profile(target)

    def _set_profile(self, profile: dict | None, *, defer_load: bool = False) -> None:
        self._generation += 1
        previous_profile = self.profile or {}
        previous_manager = self.manager
        changed = any(previous_profile.get(key) != (profile or {}).get(key)
                      for key in ("id", "loader", "loader_version", "minecraft_version", "version_id", "is_instance"))
        self.profile = profile.copy() if profile else None
        self._render_profile_header()
        self.error = ""
        self.manager = None
        if profile and profile.get("is_instance"):
            try:
                directory = self._game_directory(profile)
                if profile.get("is_instance") and not directory.is_dir():
                    raise ValueError("This instance folder is missing. Restore it or edit the profile's game folder.")
                self.manager = self._managers.get(directory)
                if self.manager is None:
                    self.manager = self._managers[directory] = ModManager(directory, runtime_directory=self._runtime_directory)
            except (OSError, ValueError) as error:
                self.error = str(error)
        if changed or previous_manager is not self.manager:
            self._updates.clear()
            self._update_summary = ""
            self.close_details()
            self._row_cache.clear()
            self._saved_scroll = 0
            self.local_mods = []
            self.hits = []
            self.search_offset = 0
            self.total_hits = 0
        if self._compatible():
            context = (self.manager.game_directory, self.profile["minecraft_version"], self.profile["loader"].lower(),
                       self.profile.get("loader_version", ""), self.profile.get("version_id", ""))
            release_type = self._update_filters.get(context)
            if release_type is None:
                release_type = self.manager.saved_update_release_type(*context[1:]) or self.details_page.release_filter.value
            self._update_filters[context] = release_type
            self.details_page.release_filter.refresh_options(self.details_page.release_filter.options, release_type)
        self.refresh(defer_load=defer_load)

    def _compatible(self) -> bool:
        return bool(self.profile and self.profile.get("is_instance") and self.manager
                    and str(self.profile.get("loader", "")).lower() in SUPPORTED_LOADERS
                    and self.profile.get("minecraft_version"))

    def set_view(self, view: str) -> None:
        if view not in self.queries or view == self.view or self._operation_in_progress:
            return
        self.queries[self.view] = self.search.value or ""
        self.view = view
        self.search.value = self.queries[view]
        self.search_offset = 0
        self._saved_scroll = 0
        self.list_view._scroll_pixels = 0
        self.refresh(animate_list=True)
        self._page.run_task(self._restore_scroll, self._generation, 0)

    def refresh(self, *, defer_load: bool = False, animate_list: bool = False) -> None:
        self._finish_update_activity()
        self._generation += 1
        self._pending_load = None
        self._finish_load_activity()
        self.error = "" if self.manager else self.error
        self.loading = bool(self.manager and self._compatible())
        self._render()
        if animate_list:
            self.list_reveal.show(self.list_body, [self.list_body])
        self._update()
        if self.loading:
            self._pending_load = (self._generation, self.profile.copy(), self.manager,
                                  self.view, self.search.value or "", self.sort.value, self.search_offset)
            if not defer_load:
                self.start_loading()

    def _refresh_requested(self, e) -> None:
        if self._operation_in_progress:
            return
        clear = getattr(self.client, "clear_cache", None)
        if callable(clear):
            clear()
        if self._compatible():
            self.manager.clear_update_check(self.profile["minecraft_version"], self.profile["loader"].lower(),
                                            loader_version=self.profile.get("loader_version", ""),
                                            release_type=self.details_page.release_filter.value)
        self._updates.clear()
        self._update_summary = ""
        self.refresh()

    def start_loading(self) -> None:
        """Start a prepared scan after the sidebar entrance has been mounted."""
        request, self._pending_load = self._pending_load, None
        if request is None:
            return
        activity = track_progress(self._progress if self._active else None,
                                  "Fetching compatible mods..." if request[3] == "browse" else "Reading installed mods...", priority=40)
        self._load_activity = activity
        self._page.run_task(self._load, *request, activity)

    def _finish_load_activity(self) -> None:
        if self._load_activity:
            self._load_activity.finish()
            self._load_activity = None

    def _finish_update_activity(self) -> None:
        if self._update_activity:
            self._update_activity.finish()
            self._update_activity = None
        self.checking_updates = False

    def _update_release_filter(self, e) -> None:
        self._finish_update_activity()
        self._updates.clear()
        self._update_summary = ""
        self._render()
        self._update()
        if self._compatible():
            context = (self.manager.game_directory, self.profile["minecraft_version"], self.profile["loader"].lower(),
                       self.profile.get("loader_version", ""), self.profile.get("version_id", ""))
            self._update_filters[context] = self.details_page.release_filter.value
            self._page.run_task(self._restore_update_cache, self._generation, self.profile.copy(), self.manager,
                                self.details_page.release_filter.value)

    def _apply_update_check(self, result: UpdateCheck | None, *, saved: bool = False) -> None:
        self._updates = {update.mod.filename: update for update in result.updates} if result else {}
        self._update_summary = ""
        if result is not None:
            count = len(result.updates)
            self._update_summary = f"{count} {'update' if count == 1 else 'updates'} available" if count else "No updates found"
            if result.warnings:
                self._update_summary += " · Some mods could not be checked"
            if saved:
                self._update_summary += " · Saved check"

    async def _restore_update_cache(self, generation: int, profile: dict, manager: ModManager, release_type: str) -> None:
        result = await asyncio.to_thread(manager.cached_update_check, profile["minecraft_version"], profile["loader"].lower(),
                                         loader_version=profile.get("loader_version", ""), release_type=release_type,
                                         version_id=profile.get("version_id", ""))
        if (generation == self._generation and manager is self.manager and self._active
                and release_type == self.details_page.release_filter.value and not self.checking_updates):
            self._apply_update_check(result, saved=True)
            self._render()
            self._update()

    async def _updates_action(self, e) -> None:
        if self._updates:
            updates = tuple(self._updates.values())
            await self._prepare_install({"project_id": updates[0].version["project_id"], "title": f"{len(updates)} mods"},
                                        updates=updates)
        else:
            await self._check_updates(e)

    async def _check_updates(self, e) -> None:
        if self.checking_updates or self.busy or self.loading or not self._compatible():
            return
        manager, profile, generation = self.manager, self.profile.copy(), self._generation
        release_type = self.details_page.release_filter.value
        activity = track_progress(self._progress, "Checking mod updates...", priority=40)
        self._update_activity = activity
        self.checking_updates = True
        self._render()
        self._update()
        try:
            result = await asyncio.to_thread(manager.check_updates, self.client,
                                             profile["minecraft_version"], profile["loader"].lower(),
                                             release_type=release_type, loader_version=profile.get("loader_version", ""),
                                             version_id=profile.get("version_id", ""))
            if (generation != self._generation or manager is not self.manager or not self._active
                    or self._update_activity is not activity
                    or release_type != self.details_page.release_filter.value):
                return
            self.local_mods = list(result.mods)
            self._apply_update_check(result)
            if result.warnings:
                self._show_notification("\n".join(result.warnings[:4]), "warning", "Mod updates")
            if self._details_open:
                self.details_page.sync_local(self.local_mods)
        except Exception as error:
            if generation == self._generation and self._active:
                self._show_notification(str(error), "error", "Could not check updates")
        finally:
            activity.finish()
            if self._update_activity is activity:
                self._update_activity = None
                self.checking_updates = False
                self._render()
                self._update()

    async def _restore_scroll(self, generation: int, offset: float) -> None:
        await asyncio.sleep(0.06)
        if (generation == self._generation and offset == self._saved_scroll
                and self._active and not self._details_open and not self._selection_open):
            if (self.list_body.content is not self.list_view
                    or self._page.session.index.get(self.list_view.content._i) is not self.list_view.content):
                return
            try:
                await self.list_view.scroll_to(offset=offset)
                if generation == self._generation and self._saved_scroll == offset:
                    self._saved_scroll = 0
            except RuntimeError:
                pass

    def open_details(self, item: LocalMod | dict, profile_id: str | None = None) -> None:
        if not self._compatible() or self._operation_in_progress or self._selection_open or (profile_id and profile_id != self.profile.get("id")):
            return
        self._saved_scroll = self.list_view._scroll_pixels
        self._details_open = True
        self.list_reveal.finish(update=False)
        self.listing.visible = False
        self.details_host.visible = True
        self.details_page.open(item)
        self._update()

    def close_details(self) -> None:
        was_open = self._details_open
        self._details_open = False
        self.listing.visible = not self._selection_open
        self.details_host.visible = False
        self.details_page.suspend()
        self._update()
        if was_open and self._active and self._saved_scroll:
            self._page.run_task(self._restore_scroll, self._generation, self._saved_scroll)

    async def _load(self, generation: int, profile: dict, manager: ModManager,
                    view: str, query: str, sort: str, offset: int, activity: ProgressTask) -> None:
        local, data, error = [], None, ""
        saved_check = None
        release_type = self.details_page.release_filter.value
        try:
            try:
                if generation != self._generation:
                    return
                local = await asyncio.to_thread(manager.list_mods)
                saved_check = await asyncio.to_thread(manager.cached_update_check, profile["minecraft_version"], profile["loader"].lower(),
                                                      loader_version=profile.get("loader_version", ""), release_type=release_type,
                                                      version_id=profile.get("version_id", ""))
                if generation != self._generation:
                    return
                if view == "browse":
                    data = await asyncio.to_thread(
                        self.client.search, query, profile["minecraft_version"], profile["loader"].lower(),
                        index=sort, offset=offset, limit=self.PAGE_SIZE,
                    )
            except Exception as exc:
                error = str(exc)
            if generation != self._generation:
                return
            # Disk/network work runs immediately. Expensive row patches wait
            # until the client has painted the entrance instead of starving it.
            if self._entrance:
                await self._entrance.wait_until_finished()
            await self.list_reveal.wait_until_finished()
            if generation != self._generation:
                return
            self.error = error
            if not error:
                self.local_mods = local
                if release_type == self.details_page.release_filter.value:
                    self._apply_update_check(saved_check, saved=True)
                if self._details_open:
                    self.details_page.sync_local(local)
                if data is not None:
                    self.hits = data["hits"]
                    self.total_hits = int(data.get("total_hits", 0))
            reveal_results = self.list_body.content is not self.list_view
            self.loading = False
            self._render()
            if reveal_results and self._active and not self._details_open and not self._selection_open:
                self.list_reveal.show(self.list_body, [self.list_body])
            self._update()
            if self._saved_scroll and self._active:
                self._page.run_task(self._restore_scroll, generation, self._saved_scroll)
            if not self.error and self._active:
                self._page.run_task(self._load_project_artwork, local)
                if any(not mod.project_id for mod in local):
                    self._page.run_task(self._identify_external_mods, generation, manager)
        finally:
            activity.finish()
            if self._load_activity is activity:
                self._load_activity = None

    async def _load_project_artwork(self, mods: list[LocalMod]) -> None:
        targets = {mod.project_id for mod in mods if mod.project_id and (not mod.icon or not mod.description)
                   and mod.project_id not in self._project_cache and mod.project_id not in self._project_requests}
        self._project_requests.update(targets)
        semaphore = asyncio.Semaphore(4)

        async def load(project_id: str) -> None:
            try:
                async with semaphore:
                    project = await asyncio.to_thread(self.client.project, project_id)
                    self._project_cache[project_id] = project
            except Exception:
                # Optional artwork must never prevent offline mod management.
                pass
            finally:
                self._project_requests.discard(project_id)

        if targets:
            activity = track_progress(self._progress if self._active else None, "Loading mod artwork...", priority=10, delay=0.3)
            self._artwork_activities.add(activity)
            try:
                await asyncio.gather(*(load(project_id) for project_id in targets))
            finally:
                activity.finish()
                self._artwork_activities.discard(activity)
            # Only rebuild current rows from the shared project cache; never
            # restore a list or action captured before navigation or searching.
            if self._entrance:
                await self._entrance.wait_until_finished()
            await self.list_reveal.wait_until_finished()
            if self._active and self.view == "installed" and any(mod.project_id in targets for mod in self.local_mods):
                self._render_list()
                self._update()

    async def _identify_external_mods(self, generation: int, manager: ModManager) -> None:
        try:
            mods, _ = await asyncio.to_thread(manager.identify_mods, self.client)
        except (ConnectionError, OSError, ValueError):
            return
        if generation != self._generation or manager is not self.manager or not self._active:
            return
        if self._entrance:
            await self._entrance.wait_until_finished()
        await self.list_reveal.wait_until_finished()
        if generation != self._generation or manager is not self.manager or not self._active:
            return
        if mods != self.local_mods:
            self.local_mods = mods
            if self._details_open:
                self.details_page.sync_local(mods)
            self._render()
            self._update()
            self._page.run_task(self._load_project_artwork, mods)

    def _search_changed(self, e) -> None:
        if self._operation_in_progress:
            return
        self.queries[self.view] = self.search.value or ""
        if self.view == "installed":
            self._saved_scroll = 0
            self._render_list()
            self._update()
        else:
            self._generation += 1
            self._finish_load_activity()
            self.loading = True
            self._render()
            self._update()
            self._page.run_task(self._debounced_search, self._generation)

    async def _debounced_search(self, generation: int) -> None:
        await asyncio.sleep(0.35)
        if generation == self._generation and self._active and not self._operation_in_progress:
            self.search_offset = 0
            self.refresh()

    def _search_submitted(self, e) -> None:
        if not self._operation_in_progress:
            self.search_offset = 0
            self.refresh()

    def _filter_changed(self, e) -> None:
        if self._operation_in_progress:
            return
        self._saved_scroll = 0
        self._render_list()
        self._update()

    def _sort_changed(self, e) -> None:
        if self._operation_in_progress:
            return
        self.search_offset = 0
        self.refresh()

    def _paginate(self, direction: int) -> None:
        if not self.loading and not self._operation_in_progress:
            self.search_offset = max(0, self.search_offset + direction * self.PAGE_SIZE)
            self.refresh()

    def _render(self) -> None:
        browse = self.view == "browse"
        controls_busy = self._operation_in_progress
        locked = controls_busy or self._is_running()
        self.select_profile_button.disabled = controls_busy
        self.installed_tab.disabled = controls_busy or not self._compatible()
        self.browse_tab.disabled = controls_busy or not self._compatible()
        for tab, selected in ((self.installed_tab, not browse), (self.browse_tab, browse)):
            tab.style.bgcolor = ft.Colors.BLACK_26 if selected and self._compatible() else ft.Colors.TRANSPARENT
            tab.content.color = ACCENT if selected and self._compatible() else MUTED
        self.installed_tab.content.value = f"Installed · {len(self.local_mods)}" if self._compatible() else "Installed"
        self.attribution.visible = browse and self._compatible()
        self.update_button.visible = not browse and self._compatible()
        self.update_button.disabled = self.busy or self.loading or self.checking_updates or not self.local_mods or bool(self._updates and self._is_running())
        self.update_button.content.value = "Checking..." if self.checking_updates else "Update all" if self._updates else "Check updates"
        self.update_button.tooltip = "Review and update all found mods" if self._updates else "Check for newer compatible mod versions"
        self.refresh_button.tooltip = "Refresh mods and clear the saved update check" if self._update_summary else "Refresh mods"
        self.details_page.release_filter.disabled = controls_busy
        self.search.hint_text = "Search compatible mods" if browse else "Search installed mods"
        self.filter.visible = not browse
        self.sort.visible = browse
        self.add_button.visible = not browse
        self.add_button.disabled = locked or not self._compatible()
        self.search.disabled = controls_busy or not self._compatible()
        self.sort.disabled = controls_busy
        self.filter.disabled = controls_busy
        self.refresh_button.visible = not browse
        self.refresh_button.disabled = controls_busy or self.loading
        self.toolbar.visible = self._compatible()
        self._render_list()
        if self._details_open:
            self.details_page._render_header()
            self.details_page._update()

    def _same_folder(self, profile: dict) -> bool:
        try:
            return self._game_directory(profile) == self.manager.game_directory
        except (OSError, ValueError):
            return False

    def _empty(self, title: str, description: str, action: ft.Control | None = None) -> None:
        self.list_view.set_items([], self._cached_row)
        controls = [ft.Image(src="sidebar/mods_muted.png", width=44, height=44, fit=ft.BoxFit.CONTAIN),
                    _text(title, 22, color=ACCENT, pixel=True),
                    _text(description, 13, text_align=ft.TextAlign.CENTER, max_lines=3)]
        if action:
            controls.append(action)
        self.list_body.content = ft.Container(
            expand=True, alignment=ft.Alignment.CENTER, padding=24,
            content=ft.Column(controls, spacing=12, tight=True,
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        )

    def _render_list(self) -> None:
        browse = self.view == "browse"
        self.previous.visible = self.next.visible = self.page_label.visible = browse and self.total_hits > 0
        self.pagination.visible = browse and self.total_hits > 0 and self._compatible() and not self.error
        self.previous.disabled = self.search_offset == 0 or self.loading or self._operation_in_progress
        self.next.disabled = self.search_offset + self.PAGE_SIZE >= self.total_hits or self.loading or self._operation_in_progress
        self.page_label.value = f"{self.search_offset // self.PAGE_SIZE + 1} / {max(1, (self.total_hits + self.PAGE_SIZE - 1) // self.PAGE_SIZE)}"
        self.status.value = "Finding compatible mods..." if self.loading and browse else "Reading mods..." if self.loading else ""
        self.status.visible = bool(self.status.value) or self.busy
        self.status_row.visible = self.status.visible or self.pagination.visible
        if self.error:
            self._empty("Could not load mods", self.error, _button("Retry", self._refresh_requested))
            return
        if not self.profile:
            self._empty("Choose a profile", "Pick a Fabric, Forge, NeoForge, or Quilt profile.", _button("Create profile", lambda e: self._create_profile(), primary=True))
            return
        if not self.profile.get("is_instance"):
            self.status_row.visible = self.status.visible = False
            self._empty("This profile is not an instance",
                        "Create an instance to keep its mods isolated from other profiles.",
                        _button("Create instance", lambda e: self._create_profile(), primary=True))
            return
        if not self._compatible():
            self._empty("This profile needs a mod loader", "Pick a modded profile with a Minecraft version.", _button("Create profile", lambda e: self._create_profile(), primary=True))
            return
        if self.loading and not (self.hits if browse else self.local_mods):
            self._empty("Finding mods" if browse else "Reading mods", "This will only take a moment.")
            return
        self.list_body.content = self.list_view
        self.status_row.visible = True
        if browse:
            noun = "mod" if self.total_hits == 1 else "mods"
            self.status.value = "Finding compatible mods..." if self.loading else f"{self.total_hits:,} compatible {noun}"
            self.status.visible = True
            self.list_view.set_items(self.hits, self._cached_row, offset=self._saved_scroll or self.list_view._scroll_pixels)
            if not self.hits:
                self._empty("No compatible mods found", "Try another search. Results match this profile's Minecraft version and loader.")
        else:
            query = (self.search.value or "").casefold().strip()
            state_filter = self.filter.value
            mods = [item for item in self.local_mods
                    if (not query or query in f"{item.name} {item.filename} {item.mod_id}".casefold())
                    and (state_filter == "all" or item.enabled == (state_filter == "enabled"))]
            enabled = sum(item.enabled for item in self.local_mods)
            disabled = len(self.local_mods) - enabled
            self.status.value = f"{enabled} enabled" + (f" · {disabled} disabled" if disabled else "")
            if query or state_filter != "all":
                self.status.value = f"{len(mods)} of {len(self.local_mods)} mods · " + self.status.value
            self.status.visible = True
            self.installed_tab.content.value = f"Installed · {len(self.local_mods)}"
            if self._update_summary:
                self.status.value += " · " + self._update_summary
            self.list_view.set_items(mods, self._cached_row, offset=self._saved_scroll or self.list_view._scroll_pixels)
            if not mods:
                self._empty("No matching mods" if self.local_mods else "Your next adventure starts here",
                            "Try another search or filter." if self.local_mods else "Browse compatible mods or add your own JAR files.",
                            None if self.local_mods else _button("Browse mods", lambda e: self.set_view("browse"), primary=True))

    def _cache_live_rows(self) -> None:
        # ProfileList reconciles by key. Cache its live mutable card, rather
        # than a replacement object that Flet can freeze during the update.
        for card in self.list_view.cards:
            signature, _ = self._row_cache[card.data]
            self._row_cache[card.data] = (signature, card)
        live = {card.data for card in self.list_view.cards}
        for key in list(self._row_cache):
            if len(self._row_cache) <= self.ROW_CACHE_SIZE:
                break
            if key not in live:
                del self._row_cache[key]

    def _cached_row(self, item: LocalMod | dict) -> ft.Container:
        key = (self.view, item.filename if isinstance(item, LocalMod) else item["project_id"])
        project = self._project_cache.get(item.project_id, {}) if isinstance(item, LocalMod) else {}
        signature = (item, self._operation_in_progress, self._is_running(), project.get("icon_url"), project.get("description"),
                     tuple((m.project_id, m.enabled) for m in self.local_mods) if isinstance(item, dict)
                     else self._updates.get(item.filename))
        cached = self._row_cache.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        card = self._local_row(item) if isinstance(item, LocalMod) else self._browse_row(item)
        card.data = key
        card.key = ft.ValueKey(f"mod-{key[0]}-{key[1]}")
        card.margin = ft.Margin.only(bottom=8)
        self._row_cache[key] = (signature, card)
        return card

    def _surface(self, content: ft.Control) -> ft.Container:
        surface = profile_card_surface(content, glow_color="#454641", horizontal_padding=16)
        surface.bgcolor = ft.Colors.with_opacity(0.18, "#000000")
        return surface

    def _mod_icon(self) -> ft.Control:
        return rounded_artwork(
            ft.Image(src="sidebar/mods.png", width=34, height=34, fit=ft.BoxFit.CONTAIN,
                     filter_quality=ft.FilterQuality.NONE), size=34)

    def _mod_info(self, title: ft.Text, description: str, details: ft.Control) -> ft.Column:
        lines = [title]
        if description:
            lines.append(_text(description, 13, color="#d1d7e2", max_lines=1,
                               overflow=ft.TextOverflow.ELLIPSIS))
        lines.append(details)
        return ft.Column(lines, expand=True, spacing=4, tight=True)

    def _local_row(self, mod: LocalMod) -> ft.Container:
        locked = self._operation_in_progress or self._is_running()
        manager = self.manager
        profile_id = self.profile.get("id")
        project = self._project_cache.get(mod.project_id, {})
        source = mod.icon or project.get("icon_url")
        update = self._updates.get(mod.filename)
        icon = ft.Image(src=source, width=48, height=48, fit=ft.BoxFit.CONTAIN,
                        cache_width=128, gapless_playback=True,
                        filter_quality=ft.FilterQuality.NONE,
                        error_content=self._mod_icon()) if source else self._mod_icon()
        return self._surface(ft.Row([
            ft.GestureDetector(content=ft.Row([
                ft.Container(rounded_artwork(icon, size=48), opacity=1 if mod.enabled else 0.45, width=48),
                self._mod_info(
                    _text(mod.name, 18, color=ACCENT if mod.enabled else MUTED, pixel=True,
                          max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    mod.description or project.get("description", ""),
                    _text(" · ".join(value for value in (mod.version, _size(mod.size)) if value),
                          12, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)),
            ], spacing=16), expand=True, on_tap=lambda e, item=mod: self.open_details(item, profile_id),
                mouse_cursor=ft.MouseCursor.CLICK, tooltip=f"View {mod.name}"),
            _text("Enabled" if mod.enabled else "Disabled", 11, width=55),
            _button("Update", lambda e, target=update: self._page.run_task(
                self._prepare_install, {"project_id": target.mod.project_id, "title": target.mod.name},
                target.version, profile_id, target), disabled=locked,
                tooltip="Review update and required dependencies") if update else ft.Container(width=0),
            ft.Switch(value=mod.enabled, active_color=ACCENT, active_track_color="#665c40",
                      inactive_thumb_color=MUTED, inactive_track_color="#26364f", disabled=locked,
                      tooltip="Disable mod" if mod.enabled else "Enable mod",
                      on_change=lambda e, item=mod: self._page.run_task(self._toggle, item, bool(e.control.value), manager)),
            launcher_icon_button(icon=ft.Icons.DELETE_OUTLINE_SHARP, icon_color=MUTED, icon_size=19,
                          tooltip="Remove mod", disabled=locked, on_click=lambda e, item=mod: self._confirm_remove(item, manager)),
        ], spacing=8))

    def _browse_row(self, hit: dict) -> ft.Container:
        profile_id = self.profile.get("id")
        project_id = hit.get("project_id", "")
        installed = next((item for item in self.local_mods if item.project_id == project_id), None)
        icon_url = hit.get("icon_url")
        icon = ft.Image(src=icon_url, width=48, height=48, fit=ft.BoxFit.CONTAIN,
                        cache_width=128, gapless_playback=True,
                        filter_quality=ft.FilterQuality.NONE,
                        error_content=self._mod_icon()) if icon_url else self._mod_icon()
        install_button = launcher_icon_button(
            icon=ft.Icons.CHECK_SHARP if installed else ft.Icons.DOWNLOAD_ROUNDED,
            icon_color=MUTED if installed else NAVY, primary=not installed,
            width=44, height=44, icon_size=22,
            on_click=lambda e, item=hit: self._page.run_task(self._prepare_install, item, None, profile_id),
            tooltip="Installed" if installed else f"Install {hit.get('title', 'mod')}",
            disabled=bool(installed) or self._operation_in_progress or self._is_running(),
        )
        return self._surface(ft.Row([
            ft.GestureDetector(content=ft.Row([
                rounded_artwork(icon, size=48),
                self._mod_info(
                    _text(hit.get("title", "Unnamed mod"), 18, color=ACCENT, pixel=True,
                          max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    hit.get("description", ""),
                    _text(f"{hit.get('author', 'Unknown')} · {_downloads(hit.get('downloads', 0))} downloads",
                          12, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)),
            ], spacing=16), expand=True, on_tap=lambda e, item=hit: self.open_details(item, profile_id),
                mouse_cursor=ft.MouseCursor.CLICK, tooltip=f"View {hit.get('title', 'mod')}"),
            install_button,
        ], spacing=20, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def _open_mods_folder(self, e) -> None:
        if self.manager:
            self._open_folder(self.manager.mods_directory)

    def _can_change(self, target_manager: ModManager | None = None) -> bool:
        if self._operation_in_progress or self._is_running():
            self._show_notification("Wait for the current operation or close Minecraft before changing mods.", "warning")
            return False
        if target_manager is not None:
            if not target_manager.game_directory.is_dir():
                self._show_notification("The original instance folder is missing. Restore it before undoing this removal.", "warning")
                return False
            return True
        return self._compatible()

    async def _run_mutation(self, manager: ModManager, operation: Callable, message: str,
                            *, success_action: tuple[str, Callable] | None = None,
                             activity: ProgressTask | None = None,
                             require_selected: bool = True) -> None:
        if ((require_selected and manager is not self.manager)
                or not self._can_change(None if require_selected else manager)):
            self._render()
            self._update()
            return
        self.busy = True
        activity = activity or track_progress(self._progress, "Updating mods...", priority=100)
        self._generation += 1
        self._finish_load_activity()
        self._finish_update_activity()
        self.loading = False
        self._render()
        self._update()
        try:
            await asyncio.to_thread(operation)
            options = {"action_label": success_action[0], "on_action": success_action[1]} if success_action else {}
            self._show_notification(message, "success", "Mods", **options)
            self._on_changed()
        except Exception as error:
            self._show_notification(str(error), "error", "Could not change mods")
        finally:
            activity.finish()
            self.busy = False
            self.refresh()

    async def _toggle(self, mod: LocalMod, enabled: bool, source_manager: ModManager | None = None) -> None:
        if source_manager is not None and source_manager is not self.manager:
            return
        if self.manager:
            manager = self.manager
            await self._run_mutation(manager, lambda: manager.set_enabled(mod.filename, enabled), f"{mod.name} {'enabled' if enabled else 'disabled'}.")

    async def _import_files(self, e) -> None:
        if not self._can_change():
            return
        manager = self.manager
        profile_id = self.profile["id"]
        try:
            files = await self.file_picker.pick_files(dialog_title="Add Minecraft mod JARs", file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["jar"], allow_multiple=True)
            if not files:
                return
            if profile_id != (self.profile or {}).get("id"):
                self._show_notification("The selected profile changed. Add the JARs again for the intended profile.", "warning")
                return
            paths = [Path(item.path) for item in files if item.path]
            if len(paths) != len(files):
                raise ValueError("Local JAR imports are available in the desktop launcher.")
            profile = self.profile.copy()
            await self._run_mutation(manager, lambda: manager.import_jars(
                paths, loader=profile["loader"].lower(), loader_version=profile.get("loader_version", "")),
                f"Added {len(paths)} {'mod' if len(paths) == 1 else 'mods'}.")
        except Exception as error:
            self._show_notification(str(error), "error", "Could not add mods")

    def _local_details(self, mod: LocalMod) -> None:
        self.open_details(mod)

    def _confirm_remove(self, mod: LocalMod, source_manager: ModManager | None = None) -> None:
        if source_manager is not None and source_manager is not self.manager:
            return
        if not self._can_change():
            return
        manager = self.manager
        profile_name = self.profile.get("name", "this profile")

        async def remove(e) -> None:
            self._dialogs().close()
            await asyncio.sleep(0.2)
            removal = None
            def operation() -> None:
                nonlocal removal
                token = manager.remove(mod.filename)
                removal = self._undo = (manager, token, mod.name)

            async def undo(e) -> None:
                if removal:
                    await self._restore_removed(e, removal)

            await self._run_mutation(manager, operation, f"Removed {mod.name}.", success_action=("Undo", undo))

        self._dialogs().show(
            ft.Column([_text(f"Remove {mod.name} from {profile_name}?", 15, color=ft.Colors.WHITE),
                       _text("The JAR will move to launcher trash. Use Undo in the notification to restore it.", 13),
                       _text("Mods in the same game folder are shared by its profiles.", 12)], spacing=14),
            title=_text("Remove mod", 22, color=ACCENT, pixel=True),
            actions=[_button("Cancel", lambda e: self._dialogs().close()), _button("Remove", remove, primary=True)],
            on_cancel=lambda e: self._dialogs().close(), width=510, height=320,
        )

    async def _restore_removed(self, e, removal: tuple[ModManager, str, str] | None = None) -> None:
        removal = removal or self._undo
        if not removal:
            return
        manager, token, name = removal
        def operation() -> None:
            manager.restore(token)
            if self._undo == removal:
                self._undo = None
        await self._run_mutation(manager, operation, f"Restored {name}.", require_selected=False)

    async def _prepare_install(self, hit: dict, selected_version: dict | None = None, profile_id: str | None = None,
                               expected_update: ModUpdate | None = None, updates: tuple[ModUpdate, ...] = ()) -> None:
        if profile_id and profile_id != (self.profile or {}).get("id"):
            return
        if not self._can_change():
            return
        manager = self.manager
        profile = self.profile.copy()
        generation = self._generation
        self.busy = True
        activity = track_progress(self._progress, "Checking mod version and dependencies...", priority=80)
        self._render()
        self.status.value = "Checking version and required dependencies..."
        self._update()
        try:
            kwargs = {"loader_version": profile.get("loader_version", ""), "version_id": profile.get("version_id", "")}
            if selected_version:
                kwargs["selected_version"] = selected_version
            if updates or expected_update:
                plan = await asyncio.to_thread(manager.plan_updates, self.client, updates or (expected_update,), profile["minecraft_version"],
                                               profile["loader"].lower(), loader_version=profile.get("loader_version", ""),
                                               release_type=self.details_page.release_filter.value, version_id=profile.get("version_id", ""))
            else:
                plan = await asyncio.to_thread(manager.plan_install, self.client, hit["project_id"], profile["minecraft_version"], profile["loader"].lower(), **kwargs)
            if generation != self._generation or not self._active:
                return
            if expected_update and dict(plan.snapshot or ()).get(expected_update.mod.filename) != expected_update.sha512:
                raise ValueError("This mod changed since the update check. Refresh and check updates again.")
            if not plan.versions:
                self._show_notification("This mod is already installed.", "info", "Mods")
                return
            closed = False
            reviewed_plans = {frozenset(): plan}

            def close(e) -> None:
                nonlocal closed
                closed = True
                self._dialogs().close()

            async def optional_change(e, project: str) -> None:
                if closed or review.checking:
                    return
                previous = review.optional_projects.copy()
                if e.control.value:
                    review.optional_projects.add(project)
                else:
                    review.optional_projects.discard(project)
                selection = frozenset(review.optional_projects)
                if selection in reviewed_plans:
                    # Every cached plan keeps the original file snapshot; install
                    # still hashes and validates that snapshot before committing.
                    review.plan = reviewed_plans[selection]
                    review.error = ""
                    review.render()
                    review.update()
                    return
                review.checking = True
                review.error = ""
                install_button.disabled = True
                review.render()
                review.update()
                install_button.update()
                check = track_progress(self._progress, "Checking optional dependencies...", priority=80)
                try:
                    updated = await asyncio.to_thread(
                        manager.plan_install, self.client, hit["project_id"], profile["minecraft_version"], profile["loader"].lower(),
                        selected_version=plan.root_version or selected_version,
                        loader_version=profile.get("loader_version", ""), optional_projects=tuple(review.optional_projects),
                        version_id=profile.get("version_id", ""),
                        **({"selected_versions": plan.root_versions} if updates else {}),
                    )
                    if closed:
                        return
                    if updated.snapshot != plan.snapshot:
                        raise ValueError("Installed mods changed. Cancel and review this installation again.")
                    review.plan = updated
                    reviewed_plans[selection] = updated
                except Exception as error:
                    review.optional_projects = previous
                    review.error = str(error)
                finally:
                    check.finish()
                    review.checking = False
                    if not closed:
                        install_button.disabled = False
                        review.render()
                        review.update()
                        install_button.update()

            async def install(e) -> None:
                if closed or review.checking:
                    return
                close(e)
                await asyncio.sleep(0.2)
                current_profile = self.profile or {}
                if (manager is not self.manager or any(current_profile.get(key) != profile.get(key)
                        for key in ("id", "loader", "loader_version", "minecraft_version", "version_id"))):
                    self._show_notification("The profile changed. Review the mod installation again for its current version.", "warning", "Mods")
                    return
                with track_progress(self._progress, f"Downloading {hit.get('title', 'mod')}...", priority=100) as download:
                    await self._run_mutation(
                        manager, lambda: manager.install(self.client, review.plan, download, can_commit=lambda: not self._is_running()),
                        f"Updated {len(updates)} {'mod' if len(updates) == 1 else 'mods'}." if updates else f"Installed {hit.get('title', 'mod')}.", activity=download,
                    )

            review = ModInstallReview(hit, profile, plan, profile_icon=self._profile_icon(profile),
                                      on_optional_change=optional_change, updates=updates)
            install_button = _button("Update all" if updates else "Install", install, primary=True)
            self.busy = False
            self._render()
            self._update()
            self._dialogs().show(
                review, title=review.title,
                actions=[_button("Cancel", close), install_button],
                on_cancel=close, width=580, height=review.preferred_height,
            )
        except Exception as error:
            logging.getLogger(__name__).exception("Could not prepare mod installation")
            self._show_notification(str(error), "error", "Cannot install mod")
        finally:
            activity.finish()
            self.busy = False
            if not self._dialogs().visible:
                self._render()
                self._update()

