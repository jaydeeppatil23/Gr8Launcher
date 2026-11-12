"""Launcher updates using the shared acrylic dialogs, toasts, and progress host."""

from __future__ import annotations

import asyncio
import time
from typing import Callable
from urllib.parse import urlparse
import webbrowser
import zipfile

import flet as ft

import launcher_updates as updates
import storage
from ui_controls import ACCENT, MUTED, launcher_button, launcher_pill
from ui_scroll_views import ScrollColumn


def open_url(url: str) -> None:
    try:
        raw = str(url).strip()
        parsed = urlparse(raw)
        if parsed.scheme in {"https", "http"}:
            webbrowser.open(raw)
    except Exception:
        pass


class UpdateController:
    def __init__(self, page: ft.Page, *, notify: Callable, progress, dialogs: Callable,
                 busy: Callable[[], bool], close: Callable, service: updates.UpdateService | None = None):
        self.page = page
        self.notify = notify
        self.progress = progress
        self.dialogs = dialogs
        self.busy = busy
        self.close = close
        self.service = service or updates.UpdateService()
        if page.web:
            self.service.packaged = False
            self.service.mode = "source"
        self.release: updates.Release | None = self.service.prepared_release
        self.phase = "ready" if self.release else "idle"
        self.applying = False
        self.closed = False
        self._notified: set[str] = set()
        self._result_seen = ""
        self.on_state_change: Callable[[], None] | None = None

        version = updates.version_label()
        source = not self.service.packaged
        preview = self.service.mock

        # Status & version indicators
        self.version_badge_text = ft.Text(
            f"Gr8 Launcher {version}",
            font_family="ps",
            size=14,
            weight=ft.FontWeight.BOLD,
            color=ACCENT,
        )
        self.channel_badge_text = ft.Text(
            "Update preview" if preview else ("Running from source" if source else "Stable Release"),
            size=11,
            color="#747e95",
        )
        self.status_badge_text = ft.Text(
            "Ready to install" if self.release else "Up to date",
            font_family="ps",
            size=12,
            color="#a7c9ff" if self.release else "#747e95",
        )
        self.status_badge = ft.Container(
            bgcolor=ft.Colors.BLACK_26,
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
            content=self.status_badge_text,
        )
        self.spinner = ft.ProgressRing(width=14, height=14, stroke_width=2, color=ACCENT, visible=False)
        self.status = ft.Text("Check for the latest launcher release.", size=12, color=MUTED)

        # Buttons (Strictly adhering to design_theme.txt: 5px rounded, BLACK_26 surfaces, ps font)
        self.check_button = launcher_pill(
            "Check for Updates",
            lambda e: page.run_task(self.check, True),
            icon=ft.Icons.REFRESH_SHARP,
        )
        self.notes_button = launcher_pill(
            "What's New",
            lambda e: self.show_release_notes(),
            icon=ft.Icons.AUTO_AWESOME_SHARP,
        )
        self.action_button_text = ft.Text(
            "View Update",
            font_family="ps",
            color="#182842",
            size=13,
            weight=ft.FontWeight.BOLD,
        )
        self.action_button_icon = ft.Icon(ft.Icons.UPDATE_SHARP, size=15, color="#182842")
        self.action_button = ft.Container(
            bgcolor=ACCENT,
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=14, vertical=8),
            ink=True,
            visible=bool(self.release),
            content=ft.Row([
                self.action_button_icon,
                self.action_button_text,
            ], spacing=6, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            on_click=lambda e: self.show_dialog(),
        )
        self.cancel_button = launcher_pill(
            "Cancel Download",
            lambda e: self.service.cancel.set(),
            icon=ft.Icons.CLOSE_SHARP,
        )
        self.cancel_button.visible = False

        # Ready notice banner (visible only when an update is downloaded & prepared)
        self.ready_banner = ft.Container(
            bgcolor=ft.Colors.with_opacity(0.12, ACCENT),
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            visible=bool(self.release),
            content=ft.Row([
                ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, color=ACCENT, size=18),
                ft.Text(
                    f"v{self.release.version if self.release else ''} is ready. Restart when convenient to apply.",
                    size=12,
                    color=ACCENT,
                    expand=True,
                ),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

        # Switches
        self.automatic_switch = ft.Switch(
            value=bool(storage.load_settings().get("check_updates_automatically", True)),
            active_color=ACCENT,
            track_color="#2b3b55",
            on_change=lambda e: self._preference("check_updates_automatically", e.control.value),
        )
        self.download_switch = ft.Switch(
            value=bool(storage.load_settings().get("download_updates_automatically", False)),
            active_color=ACCENT,
            track_color="#2b3b55",
            disabled=source and not preview,
            on_change=lambda e: self._preference("download_updates_automatically", e.control.value),
        )

        # Primary Status Card
        self.status_card = ft.Container(
            bgcolor=ft.Colors.BLACK_26,
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            content=ft.Column([
                ft.Row([
                    ft.Row([
                        ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, color=ACCENT, size=20),
                        ft.Column([
                            ft.Text("Launcher Updates", font_family="ps", color=ACCENT, size=15, weight=ft.FontWeight.BOLD),
                            ft.Row([self.version_badge_text, ft.Text("•", color="#747e95", size=10), self.channel_badge_text], spacing=6),
                        ], spacing=1),
                    ], spacing=10, expand=True),
                    self.status_badge,
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=4),
                ft.Row([self.spinner, self.status], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self.ready_banner,
                ft.Row([self.check_button, self.notes_button, self.action_button, self.cancel_button], spacing=8, wrap=True),
            ], spacing=10),
        )

        # Backward compatibility alias
        self.card = self.status_card

        if self.release:
            self._refresh(f"v{self.release.version} is ready. Restart when convenient.")

    def _preference(self, key: str, value: bool) -> None:
        settings = storage.load_settings()
        settings[key] = bool(value)
        if not storage.save_settings(settings):
            self.notify("Could not save update preferences.", "error", "Updates")

    def _refresh(self, message: str) -> None:
        self.status.value = message
        self.spinner.visible = self.phase == "checking"
        self.check_button.disabled = self.phase in ("checking", "downloading", "handing_off")
        self.check_button.opacity = 0.5 if self.check_button.disabled else 1.0
        self.action_button.visible = self.release is not None and self.phase != "checking"
        self.cancel_button.visible = self.phase == "downloading"
        self.action_button_text.value = "Restart Now" if self.phase == "ready" else "View Update"
        self.action_button_icon.name = ft.Icons.RESTART_ALT_SHARP if self.phase == "ready" else ft.Icons.SYSTEM_UPDATE_ALT_SHARP

        if self.phase == "ready" and self.release:
            self.status_badge_text.value = "Ready to install"
            self.status_badge_text.color = ACCENT
            self.ready_banner.visible = True
            if len(self.ready_banner.content.controls) > 1:
                self.ready_banner.content.controls[1].value = f"v{self.release.version} is verified and ready. Restart when convenient to apply."
        elif self.phase == "available" and self.release:
            self.status_badge_text.value = f"Update available: v{self.release.version}"
            self.status_badge_text.color = "#a7c9ff"
            self.ready_banner.visible = False
        elif self.phase == "downloading":
            self.status_badge_text.value = "Downloading..."
            self.status_badge_text.color = "#a7c9ff"
            self.ready_banner.visible = False
        elif self.phase == "checking":
            self.status_badge_text.value = "Checking..."
            self.status_badge_text.color = "#747e95"
            self.ready_banner.visible = False
        else:
            self.status_badge_text.value = "Up to date"
            self.status_badge_text.color = "#747e95"
            self.ready_banner.visible = False

        controls_to_update = [
            self.status, self.spinner, self.check_button, self.action_button,
            self.cancel_button, self.status_badge, self.ready_banner,
        ]
        for control in controls_to_update:
            try:
                control.update()
            except RuntimeError:
                pass

        if self.on_state_change:
            try:
                self.on_state_change()
            except Exception:
                pass

    async def check(self, manual: bool = False) -> None:
        if self.phase in ("checking", "downloading", "handing_off") or self.closed:
            return
        if self.phase == "ready":
            if manual:
                self.show_dialog()
            return
        self.phase = "checking"
        self._refresh("Checking for launcher updates...")
        try:
            self.release = await asyncio.to_thread(self.service.check, manual=manual)
            self.phase = "available" if self.release else "idle"
            self._refresh(f"Update available: v{self.release.version}" if self.release else "You're up to date.")
            if self.release:
                if manual or (self.release.version not in self._notified and self.service.should_notify(self.release.version)):
                    self._notified.add(self.release.version)
                    self.notify(f"Gr8 Launcher v{self.release.version} is available.", "update", "Launcher update",
                                key="launcher-update", action_label="View update", on_action=lambda e: self.show_dialog())
                if (self.service.can_apply() or self.service.mock) and storage.load_settings().get("download_updates_automatically", False):
                    await self.download()
            elif manual:
                self.notify("You're using the latest stable launcher release.", "success", "Updates")
        except (updates.UpdateError, OSError, ValueError, TypeError) as error:
            self.phase = "idle"
            self._refresh(str(error) if manual else "Update check unavailable. You can try again manually.")
            if manual:
                self.notify(str(error), "warning", "Could not check for updates")

    def later(self, e=None) -> None:
        if self.release:
            try:
                self.service.snooze(self.release.version)
            except OSError:
                self.notify("Could not save the update reminder.", "warning", "Updates")
        self.dialogs().close()

    def show_dialog(self) -> None:
        """Open the update review dialog (when an update is found or prepared)."""
        if not self.release or self.phase == "handing_off":
            return
        host = self.dialogs()
        if host.visible:
            self.notify("Close the current dialog to view the launcher update.", "info", "Updates")
            return

        ready = self.phase == "ready"
        downloading = self.phase == "downloading"
        can_apply = self.service.can_apply() or self.service.mock

        # Sleek version comparison card
        version_card = ft.Container(
            bgcolor=ft.Colors.BLACK_26,
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            content=ft.Row([
                ft.Column([
                    ft.Text("INSTALLED", size=10, color="#747e95", weight=ft.FontWeight.BOLD),
                    ft.Text(updates.version_label(), font_family="ps", size=16, color="#d1d7e2"),
                ], spacing=2),
                ft.Icon(ft.Icons.ARROW_FORWARD, size=18, color=ACCENT),
                ft.Column([
                    ft.Text("NEW RELEASE", size=10, color=ACCENT, weight=ft.FontWeight.BOLD),
                    ft.Text(f"v{self.release.version}", font_family="ps", size=16, color=ACCENT, weight=ft.FontWeight.BOLD),
                ], spacing=2),
            ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

        status_desc = (
            "The update is verified and ready. Restart the launcher to finish." if ready
            else "Downloading the update in the background... Release notes are shown below." if downloading
            else "Download the new release while you continue using the launcher."
        )

        details: list[ft.Control] = [
            version_card,
            ft.Text(status_desc, size=12, color="#d7dce7"),
        ]

        if not can_apply:
            details.append(ft.Container(
                bgcolor=ft.Colors.BLACK_26,
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                content=ft.Row([
                    ft.Icon(ft.Icons.INFO_OUTLINE, size=15, color=MUTED),
                    ft.Text("Running from source. Install the packaged Windows release to use automatic updates.", size=12, color=MUTED, expand=True),
                ], spacing=8),
            ))
        if self.service.scope == "allusers":
            details.append(ft.Container(
                bgcolor=ft.Colors.BLACK_26,
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                content=ft.Row([
                    ft.Icon(ft.Icons.SHIELD_OUTLINED, size=15, color=MUTED),
                    ft.Text("Windows will request administrator approval for setup.", size=12, color=MUTED, expand=True),
                ], spacing=8),
            ))

        notes_markdown = ft.Markdown(
            self.release.notes[:10000] or "See the release page for details.",
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            auto_follow_links=False,
            on_tap_link=lambda e: open_url(e.data) if str(e.data).startswith("http") else None,
            md_style_sheet=ft.MarkdownStyleSheet(
                p_text_style=ft.TextStyle(size=12, color="#d1d7e2", height=1.45),
                h1_text_style=ft.TextStyle(size=16, color=ACCENT, weight=ft.FontWeight.BOLD),
                h2_text_style=ft.TextStyle(size=14, color=ACCENT, weight=ft.FontWeight.BOLD),
                h3_text_style=ft.TextStyle(size=13, color=ACCENT, weight=ft.FontWeight.BOLD),
                a_text_style=ft.TextStyle(color=ACCENT),
                code_text_style=ft.TextStyle(font_family="Consolas", color="#e5ebf4", size=11),
                list_bullet_text_style=ft.TextStyle(size=12, color="#d1d7e2"),
                blockquote_decoration=ft.BoxDecoration(bgcolor="#1b2b45", border_radius=5),
                blockquote_padding=ft.Padding.all(10),
                codeblock_decoration=ft.BoxDecoration(bgcolor="#121e33", border_radius=5),
                codeblock_padding=ft.Padding.all(10),
                table_cells_padding=ft.Padding.symmetric(horizontal=8, vertical=6),
            ),
        )

        details.extend([
            ft.Container(
                bgcolor=ft.Colors.BLACK_26,
                border_radius=5,
                padding=ft.Padding.all(12),
                content=notes_markdown,
            ),
        ])

        actions = [
            launcher_button("View on GitHub", lambda e: self.open_release()),
            launcher_button("Later", self.later),
        ]

        if ready:
            actions.append(launcher_button("Restart now", lambda e: self.page.run_task(self.apply), primary=True, disabled=not can_apply))
        elif downloading:
            actions.append(launcher_button("Cancel download", lambda e: (self.service.cancel.set(), self.dialogs().close())))
        else:
            actions.append(launcher_button("Download update", lambda e: self.page.run_task(self.download), primary=True, disabled=not can_apply))

        host.show(
            ft.Column(details, spacing=12),
            title=ft.Row([
                ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, color=ACCENT, size=22),
                ft.Text("Launcher Update", font_family="ps", size=20, color=ACCENT),
            ], spacing=10),
            actions=actions,
            on_cancel=self.later,
            width=540,
            height=520,
        )

    def show_release_notes(self) -> None:
        """Open the What's New / Release Notes dialog for the current or latest release."""
        host = self.dialogs()
        if host.visible:
            self.notify("Close the current dialog to view release notes.", "info", "Updates")
            return

        if self.release and self.release.notes:
            version_str = f"v{self.release.version}"
            notes_text = self.release.notes
            page_url = self.release.page_url
            badge_label = "Update Ready" if self.phase == "ready" else "Update Available"
        else:
            cache = updates.read_json(self.service.discovery_path)
            doc = cache.get("release") if isinstance(cache, dict) else None
            if doc and doc.get("body"):
                version_str = doc.get("name") or doc.get("tag_name") or updates.version_label()
                notes_text = updates.clean_release_notes(str(doc["body"]))
                page_url = doc.get("html_url", f"https://github.com/{updates.REPOSITORY}/releases")
                badge_label = "Latest Release"
            else:
                version_str = updates.version_label()
                page_url = f"https://github.com/{updates.REPOSITORY}/releases"
                badge_label = "Current Version"
                notes_text = (
                    "### What's New in Gr8 Launcher\n\n"
                    "- **In-Launcher Updater:** Automatic background updates, verify integrity, and one-click restart.\n"
                    "- **Modpacks & Smooth Scrolling:** Browse, download, and install Modrinth & CurseForge packs.\n"
                    "- **Mod Compatibility Checks:** Automatic checks for Minecraft versions and mod loaders.\n"
                    "- **Refined Acrylic Interface:** Seamless frosted dark theme with 3D skin viewer and Discord Rich Presence."
                )

        notes_markdown = ft.Markdown(
            notes_text[:10000] or "See the release page for full details.",
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            auto_follow_links=False,
            on_tap_link=lambda e: open_url(e.data) if str(e.data).startswith("http") else None,
            md_style_sheet=ft.MarkdownStyleSheet(
                p_text_style=ft.TextStyle(size=12, color="#d1d7e2", height=1.45),
                h1_text_style=ft.TextStyle(size=16, color=ACCENT, weight=ft.FontWeight.BOLD),
                h2_text_style=ft.TextStyle(size=14, color=ACCENT, weight=ft.FontWeight.BOLD),
                h3_text_style=ft.TextStyle(size=13, color=ACCENT, weight=ft.FontWeight.BOLD),
                a_text_style=ft.TextStyle(color=ACCENT),
                code_text_style=ft.TextStyle(font_family="Consolas", color="#e5ebf4", size=11),
                list_bullet_text_style=ft.TextStyle(size=12, color="#d1d7e2"),
                blockquote_decoration=ft.BoxDecoration(bgcolor="#1b2b45", border_radius=5),
                blockquote_padding=ft.Padding.all(10),
                codeblock_decoration=ft.BoxDecoration(bgcolor="#121e33", border_radius=5),
                codeblock_padding=ft.Padding.all(10),
                table_cells_padding=ft.Padding.symmetric(horizontal=8, vertical=6),
            ),
        )

        header_container = ft.Container(
            bgcolor=ft.Colors.BLACK_26,
            border_radius=5,
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            content=ft.Row([
                ft.Row([
                    ft.Icon(ft.Icons.AUTO_AWESOME_SHARP, size=16, color=ACCENT),
                    ft.Text(f"Gr8 Launcher {version_str}", font_family="ps", size=14, color=ACCENT, weight=ft.FontWeight.BOLD),
                ], spacing=8),
                ft.Text(badge_label, size=11, color="#747e95"),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

        details = [
            header_container,
            ft.Container(
                bgcolor=ft.Colors.BLACK_26,
                border_radius=5,
                padding=ft.Padding.all(12),
                content=notes_markdown,
            ),
        ]

        actions = [
            launcher_button("View on GitHub", lambda e: open_url(page_url)),
            launcher_button("Close", lambda e: self.dialogs().close(), primary=True),
        ]

        host.show(
            ft.Column(details, spacing=12),
            title=ft.Row([
                ft.Icon(ft.Icons.NEW_RELEASES_SHARP, color=ACCENT, size=20),
                ft.Text("Release Notes", font_family="ps", size=20, color=ACCENT),
            ], spacing=10),
            actions=actions,
            on_cancel=lambda e: self.dialogs().close(),
            width=540,
            height=520,
        )

    def open_release(self) -> None:
        if self.release:
            open_url(self.release.page_url)

    async def download(self) -> None:
        if not self.release or self.phase in ("downloading", "handing_off"):
            return
        self.dialogs().close()
        self.phase = "downloading"
        self._refresh("Downloading launcher update...")
        activity = self.progress.begin("Downloading launcher update...", priority=35)
        try:
            await asyncio.to_thread(self.service.download, self.release, activity.on_bytes)
            self.phase = "ready"
            self._refresh(f"v{self.release.version} is ready. Restart when convenient.")
            self.notify("The launcher update is verified and ready to apply.", "update", "Update ready",
                        key="launcher-update-ready", action_label="View update", on_action=lambda e: self.show_dialog())
        except updates.UpdateCancelled:
            self.phase = "available"
            self._refresh("Download cancelled. You can try again whenever you're ready.")
            self.notify("Launcher update download cancelled.", "info", "Updates")
        except (updates.UpdateError, OSError, ValueError, zipfile.BadZipFile) as error:
            self.phase = "available"
            self._refresh("Could not prepare the update. Try again or use the release page.")
            self.notify(str(error), "error", "Update download failed", action_label="View update", on_action=lambda e: self.show_dialog())
        finally:
            activity.finish()

    async def apply(self) -> None:
        if self.phase != "ready" or self.applying:
            return
        if self.service.mock:
            self.dialogs().close()
            self.notify("Update preview complete. No files were changed and the launcher stays open.", "success", "Update preview")
            return
        if self.busy():
            self.notify("Close Minecraft and wait for installation or mod operations to finish before updating.", "warning", "Update waiting")
            return
        self.dialogs().close()
        self.applying = True
        self.phase = "handing_off"
        self._refresh("Starting the updater...")
        process = None
        try:
            process = await asyncio.to_thread(self.service.start_helper)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise updates.UpdateError("The updater closed before it was ready.")
                result = updates.read_json(self.service.prepared.parent / "result.json")
                if result:
                    raise updates.UpdateError(result.get("message", "The updater could not prepare the update."))
                ready = updates.read_json(self.service.prepared.parent / "ready.json")
                handoff = updates.read_json(self.service.prepared)
                if ready.get("nonce") == handoff.get("nonce") and ready.get("operation_id") == handoff.get("operation_id"):
                    self.dialogs().close()
                    await self.close(None)
                    return
                await asyncio.sleep(0.1)
            raise updates.UpdateError("The updater did not become ready. Use the release page to update manually.")
        except (updates.UpdateError, OSError) as error:
            # Original launcher is still alive: the helper cannot have begun replacement.
            if process and process.poll() is None:
                process.terminate()
                await asyncio.to_thread(process.wait)
            state = updates.read_json(self.service.state_path)
            state.pop("pending", None)
            state.pop("prepared", None)
            updates.atomic_json(self.service.state_path, state)
            self.service.prepared = None
            self.service.prepared_release = None
            self.applying = False
            self.phase = "available"
            self._refresh("Could not apply the update. Download again or use the release page.")
            self.notify(str(error), "error", "Update could not start")

    async def startup(self) -> None:
        # A client round-trip confirms the UI is mounted, rather than just alive.
        if not self.page.web:
            await asyncio.wait_for(self.page.get_device_info(), timeout=10)
            updates.acknowledge_startup()
        await asyncio.sleep(1)
        for _ in range(20):
            result = self.service.result()
            if result:
                identity = result.get("operation_id", "")
                if identity != self._result_seen:
                    self._result_seen = identity
                    kind = "success" if result.get("status") in ("succeeded", "recovered") else "warning"
                    self.notify(result.get("message", "The launcher update has finished."), kind, "Launcher update")
                    state = updates.read_json(self.service.state_path)
                    state.pop("pending", None)
                    state.pop("prepared", None)
                    updates.atomic_json(self.service.state_path, state)
                break
            if not updates.read_json(self.service.state_path).get("pending"):
                break
            await asyncio.sleep(0.2)
        await asyncio.sleep(4)
        while not self.closed:
            if (self.service.packaged or self.service.mock) and storage.load_settings().get("check_updates_automatically", True):
                await self.check()
            await asyncio.sleep(updates.CHECK_INTERVAL)

    def stop(self) -> None:
        self.closed = True
        self.service.cancel.set()

    def build_section(self, make_setting_card_fn: Callable) -> ft.Column:
        """Construct the dedicated Settings > Updates view."""
        notes_btn = launcher_pill(
            "View Release Notes",
            lambda e: self.show_release_notes(),
            icon=ft.Icons.ARTICLE_SHARP,
        )
        notes_card = make_setting_card_fn(
            "Release Notes & Changelog",
            "Read recent additions, changes, and bug fixes in Gr8 Launcher.",
            notes_btn,
        )

        auto_check_card = make_setting_card_fn(
            "Automatic Update Checks",
            "Periodically check GitHub Releases in the background when the launcher starts.",
            self.automatic_switch,
        )
        source = not self.service.packaged and not self.service.mock
        download_desc = (
            "Download and verify launcher updates in the background automatically."
            if not source else
            "Automatic downloads are unavailable when running from source."
        )
        auto_download_card = make_setting_card_fn(
            "Automatic Background Downloads",
            download_desc,
            self.download_switch,
        )

        releases_btn = launcher_pill(
            "Open Releases",
            lambda e: open_url(f"https://github.com/{updates.REPOSITORY}/releases"),
            icon=ft.Icons.OPEN_IN_NEW_SHARP,
        )
        github_card = make_setting_card_fn(
            "Official Release Archive",
            "Browse changelogs, full commit history, and portable archives on GitHub.",
            releases_btn,
        )

        cards = [
            self.status_card,
            notes_card,
            auto_check_card,
            auto_download_card,
            github_card,
        ]
        return ScrollColumn(spacing=14, scroll=ft.ScrollMode.ADAPTIVE, expand=True, controls=cards, bottom_fade=True)
