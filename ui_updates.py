"""Launcher updates using the shared acrylic dialogs, toasts, and progress host."""

from __future__ import annotations

import asyncio
import time
from typing import Callable
import zipfile

import flet as ft

import launcher_updates as updates
import storage
from ui_controls import ACCENT, MUTED, launcher_button


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
        self.status = ft.Text("Check for the latest launcher release.", size=12, color=MUTED)
        self.check_button = launcher_button("Check for updates", lambda e: page.run_task(self.check, True))
        self.action_button = launcher_button("View update", lambda e: self.show_dialog(), primary=True, visible=False)
        self.cancel_button = launcher_button("Cancel download", lambda e: self.service.cancel.set(), visible=False)
        version = updates.version_label()
        source = not self.service.packaged
        self.version_text = ft.Text(f"Gr8 Launcher {version}" + (" · Update preview" if self.service.mock else " · Running from source" if source else ""),
                                    font_family="ps", size=18, color=ACCENT)
        automatic = ft.Switch(label="Check for updates automatically", value=bool(storage.load_settings().get("check_updates_automatically", True)),
                              active_color=ACCENT, on_change=lambda e: self._preference("check_updates_automatically", e.control.value))
        download = ft.Switch(label="Download updates automatically", value=bool(storage.load_settings().get("download_updates_automatically", False)),
                             active_color=ACCENT, disabled=source and not self.service.mock,
                             on_change=lambda e: self._preference("download_updates_automatically", e.control.value))
        self.card = ft.Container(
            bgcolor=ft.Colors.BLACK_26, border_radius=5, padding=ft.Padding.all(16),
            content=ft.Column([
                ft.Row([ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, color=ACCENT, size=22), self.version_text], spacing=10),
                self.status,
                ft.Row([self.check_button, self.action_button, self.cancel_button], spacing=8, wrap=True),
                automatic, download,
                ft.Text("Restart to apply an update. Your profiles and Minecraft files stay in place.", size=11, color=MUTED),
            ], spacing=8),
        )
        if self.release:
            self._refresh(f"v{self.release.version} is ready. Restart when convenient.")

    def _preference(self, key: str, value: bool) -> None:
        settings = storage.load_settings()
        settings[key] = bool(value)
        if not storage.save_settings(settings):
            self.notify("Could not save update preferences.", "error", "Updates")

    def _refresh(self, message: str) -> None:
        self.status.value = message
        self.check_button.disabled = self.phase in ("checking", "downloading", "handing_off")
        self.action_button.visible = self.release is not None and self.phase not in ("checking", "downloading")
        self.cancel_button.visible = self.phase == "downloading"
        self.action_button.content.value = "Restart now" if self.phase == "ready" else "View update"
        for control in (self.status, self.check_button, self.action_button, self.cancel_button):
            try:
                control.update()
            except RuntimeError:
                pass  # The About card may not be mounted on the current page.

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
        if not self.release or self.phase in ("downloading", "handing_off"):
            return
        host = self.dialogs()
        if host.visible:
            self.notify("Close the current dialog to view the launcher update.", "info", "Updates")
            return
        ready = self.phase == "ready"
        can_apply = self.service.can_apply() or self.service.mock
        details = [
            ft.Text(f"{updates.version_label()} to v{self.release.version}", font_family="ps", size=22, color=ACCENT),
            ft.Text("The update is verified and ready. Restart the launcher to finish." if ready else "Download the new release while you continue using the launcher.", size=13, color="#d7dce7"),
        ]
        if not can_apply:
            details.append(ft.Text("Running from source. Install the packaged Windows release to use automatic updates.", size=12, color=MUTED))
        if self.service.scope == "allusers":
            details.append(ft.Text("Windows will request administrator approval for setup.", size=12, color=MUTED))
        if ready and self.service.prepared:
            details.append(ft.Text(f"Recovery shortcut: {self.service.prepared.parent / 'Recover.cmd'}", size=11, color=MUTED, selectable=True))
        notes_markdown = ft.Markdown(
            self.release.notes[:10000] or "See the release page for details.",
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            auto_follow_links=False,
            on_tap_link=lambda e: self.page.run_task(self.page.launch_url, e.data) if str(e.data).startswith("https://") else None,
            md_style_sheet=ft.MarkdownStyleSheet(
                p_text_style=ft.TextStyle(size=12, color="#aeb7c8"),
                h1_text_style=ft.TextStyle(size=16, color=ACCENT, weight=ft.FontWeight.BOLD),
                h2_text_style=ft.TextStyle(size=14, color=ACCENT, weight=ft.FontWeight.BOLD),
                h3_text_style=ft.TextStyle(size=13, color=ACCENT, weight=ft.FontWeight.BOLD),
                a_text_style=ft.TextStyle(color=ACCENT),
                code_text_style=ft.TextStyle(font_family="Consolas", color="#e5ebf4", size=11),
                list_bullet_text_style=ft.TextStyle(size=12, color="#aeb7c8"),
            ),
        )
        details.extend([ft.Text("What's new", font_family="ps", size=16, color=ACCENT), notes_markdown])
        action = launcher_button("Restart now" if ready else "Download update", lambda e: self.page.run_task(self.apply if ready else self.download),
                                 primary=True, disabled=not can_apply)
        host.show(ft.Column(details, spacing=14),
                  title=ft.Row([ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, color=ACCENT, size=22),
                                ft.Text("Launcher update", font_family="ps", size=22, color=ACCENT)], spacing=10),
                  actions=[launcher_button("Open release", lambda e: self.page.run_task(self.open_release)),
                           launcher_button("Later", self.later), action],
                  on_cancel=self.later, width=540, height=520)

    async def open_release(self) -> None:
        if self.release:
            await self.page.launch_url(updates.trusted_url(self.release.page_url))

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
