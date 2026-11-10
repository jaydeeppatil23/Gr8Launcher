"""Capture the real launcher with sample data in a headless Chromium browser."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1100, 650


def find_browser(explicit: str | None) -> Path:
    candidates = [explicit, os.environ.get("SCREENSHOT_BROWSER")]
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if directory := os.environ.get(variable):
            candidates.extend(str(Path(directory) / suffix) for suffix in (
                "Microsoft/Edge/Application/msedge.exe",
                "Google/Chrome/Application/chrome.exe",
            ))
    candidates.extend(shutil.which(name) for name in (
        "msedge", "google-chrome", "chromium", "chromium-browser",
    ))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise RuntimeError("Install Edge or Chrome, or pass --browser PATH.")


def serve_preview(directory: Path, port: int) -> None:
    source = directory / "source"
    source.mkdir()
    for module in PROJECT_ROOT.glob("*.py"):
        shutil.copy2(module, source / module.name)
    version_file = PROJECT_ROOT / "VERSION"
    if version_file.is_file():
        shutil.copy2(version_file, source / version_file.name)
    shutil.copytree(PROJECT_ROOT / ".assets", source / ".assets",
                    ignore=shutil.ignore_patterns("cache", "__pycache__"))
    sys.path.insert(0, str(source))
    # Patch the data directory before importing any launcher modules. The copied
    # source also prevents legacy .data migration from touching local settings.
    import minecraft_launcher_lib
    minecraft_root = directory / "minecraft"
    minecraft_launcher_lib.utils.get_minecraft_directory = lambda: str(minecraft_root)

    import storage
    assert storage.DATA_DIR.resolve().is_relative_to(directory.resolve())
    if not storage.save_accounts({
        "active_account_id": "screenshot-player",
        "accounts": [{"id": "screenshot-player", "type": "offline",
                      "username": "Player", "created_at": 1}],
    }):
        raise RuntimeError("Could not create the screenshot account.")
    profiles = [
        {"id": "screenshot-survival", "name": "Survival", "loader": "vanilla",
         "minecraft_version": "1.21.4", "loader_version": "", "version_id": "1.21.4",
         "game_directory": str(minecraft_root / "instances" / "Survival"),
         "icon": "icon_blocks/grass_block.png"},
        {"id": "screenshot-fabric", "name": "Fabric Adventure", "loader": "fabric",
         "minecraft_version": "1.21.1", "loader_version": "0.16.10",
         "version_id": "fabric-loader-0.16.10-1.21.1",
         "game_directory": str(minecraft_root / "instances" / "Fabric Adventure"),
         "icon": "icon_blocks/diamond_ore.png"},
    ]
    if not storage.save_installations(profiles, latest_release_id="1.21.4"):
        raise RuntimeError("Could not create screenshot profiles.")
    if not storage.save_settings({**storage.DEFAULT_SETTINGS, "discord_rpc": False,
                                  "enable_orbs": False, "enable_head_tracking": False}):
        raise RuntimeError("Could not create screenshot settings.")

    import main as launcher_ui
    import flet as ft
    import uvicorn
    # Use the same bundled assets as the desktop app, with web-relative paths.
    os.chdir(source / ".assets")
    launcher_ui.ASSETS_DIR = Path(".")
    launcher_ui.get_latest_release_version = lambda: ("1.21.4", True)
    launcher_ui.check_internet_connection = lambda *args, **kwargs: True
    random.seed(0)

    def preview(page):
        launcher_ui.main(page)

        async def ready():
            # Wait for the launcher's entrance animations before checking paint.
            await asyncio.sleep(2)
            (directory / "ready").write_text("ready", encoding="utf-8")
        page.run_task(ready)

    app = ft.run(preview, assets_dir=str(source / ".assets"), no_cdn=True,
                 export_asgi_app=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


class DevTools:
    def __init__(self, connection):
        self.connection = connection
        self.counter = 0
        self.errors = []

    def call(self, method: str, params: dict | None = None) -> dict:
        self.counter += 1
        self.connection.send(json.dumps({"id": self.counter, "method": method,
                                         "params": params or {}}))
        while True:
            result = json.loads(self.connection.recv(timeout=20))
            if result.get("method") == "Runtime.exceptionThrown":
                self.errors.append(result["params"])
            if result.get("id") == self.counter:
                if "error" in result:
                    raise RuntimeError(f"{method}: {result['error']}")
                return result.get("result", {})

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {"expression": expression,
                                                "returnByValue": True})
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result["result"].get("value")


def validate_screenshot(data: bytes) -> None:
    from PIL import Image, ImageStat
    with Image.open(io.BytesIO(data)) as picture:
        if picture.format != "PNG" or picture.size != (WIDTH, HEIGHT):
            raise RuntimeError("The screenshot has an unexpected format or size.")
        # A blank Flutter startup canvas must never replace a published preview.
        for box in ((70, 20, 1050, 290), (70, 290, 760, 640), (800, 290, 1080, 640)):
            deviation = ImageStat.Stat(picture.crop(box).convert("RGB")).stddev
            if max(deviation) < 12:
                raise RuntimeError("The screenshot contains an unrendered launcher panel.")


# Sidebar geometry at the preview window size: 190px sidebar, 40px title bar,
# 16px vertical padding and 48px nav rows.
SIDEBAR_X = 95
SIDEBAR_ROWS = {
    "home": 209, "library": 257, "mods": 305, "skins": 353,
    "settings": 547, "accounts": 595,
}


def press_shortcut(devtools: "DevTools", label: str) -> None:
    """Click the sidebar item for the given tab in the preview."""
    y = SIDEBAR_ROWS.get(label.lower())
    if y is None:
        raise RuntimeError(f"Unknown sidebar item: {label}")
    devtools.call("Input.dispatchMouseEvent", {
        "type": "mouseMoved", "x": SIDEBAR_X, "y": y,
        "buttons": 0, "pointerType": "mouse",
    })
    for event_type, buttons in (("mousePressed", 1), ("mouseReleased", 0)):
        devtools.call("Input.dispatchMouseEvent", {
            "type": event_type,
            "x": SIDEBAR_X, "y": y,
            "button": "left", "buttons": buttons, "clickCount": 1,
            "pointerType": "mouse",
        })


def capture(output: Path, browser: Path, tab: str = "home") -> None:
    from websockets.sync.client import connect
    temporary_root = (PROJECT_ROOT / "temp").resolve()
    if not temporary_root.is_relative_to(PROJECT_ROOT):
        raise RuntimeError("Screenshot temporary files must stay inside the workspace.")
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="screenshot-", dir=temporary_root,
                                     ignore_cleanup_errors=True) as temporary:
        directory = Path(temporary).resolve()
        if not directory.is_relative_to(temporary_root):
            raise RuntimeError("Screenshot cleanup must stay inside its temporary directory.")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        browser_process = None
        with (directory / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen([
                sys.executable, str(Path(__file__).resolve()), "--serve", str(directory),
                "--port", str(port),
            ], stdout=log, stderr=log, **process_options)
            try:
                deadline = time.monotonic() + 75
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("The screenshot server exited before starting.")
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/", timeout=1):
                            break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise RuntimeError("The screenshot server did not start.")
                        time.sleep(0.2)
                browser_profile = directory / "browser"
                browser_process = subprocess.Popen([
                    str(browser), "--headless=new", "--no-first-run", "--no-default-browser-check",
                    "--disable-background-networking", "--hide-scrollbars", "--force-device-scale-factor=1",
                    "--enable-unsafe-swiftshader", f"--window-size={WIDTH},{HEIGHT}",
                    "--remote-debugging-port=0", f"--user-data-dir={browser_profile}", "about:blank",
                ], stdout=log, stderr=log, **process_options)
                active_port = browser_profile / "DevToolsActivePort"
                while not active_port.is_file():
                    if browser_process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("The headless browser did not start.")
                    time.sleep(0.2)
                debug_port = int(active_port.read_text().splitlines()[0])
                with urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=5) as response:
                    target = next(item for item in json.load(response) if item["type"] == "page")
                with connect(target["webSocketDebuggerUrl"], max_size=20_000_000) as connection:
                    devtools = DevTools(connection)
                    devtools.call("Page.enable")
                    devtools.call("Runtime.enable")
                    devtools.call("Emulation.setDeviceMetricsOverride", {
                        "width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1, "mobile": False,
                    })
                    devtools.call("Page.navigate", {"url": f"http://127.0.0.1:{port}/"})
                    while True:
                        # Flutter exposes its rendered labels when semantics is enabled.
                        labels = devtools.evaluate("""(() => {
                            document.querySelector('flt-semantics-placeholder')?.click();
                            return document.body?.innerText || '';
                        })()""") or ""
                        if (directory / "ready").is_file() and all(
                            label in labels for label in ("Survival", "Fabric Adventure", "PLAY")
                        ):
                            break
                        if devtools.errors or time.monotonic() > deadline:
                            raise RuntimeError(f"Launcher did not render: {labels[:500]} {devtools.errors}")
                        time.sleep(0.25)
                    time.sleep(0.5)
                    if tab != "home":
                        press_shortcut(devtools, tab.capitalize())
                        marker = {
                            "library": "New Profile",
                            "mods": "Installed",
                            "skins": "Skins",
                            "settings": "Launch Behavior",
                            "accounts": "Add Account",
                        }[tab]
                        marker_deadline = time.monotonic() + 30
                        while True:
                            text = devtools.evaluate(
                                "document.body?.innerText || ''"
                            ) or ""
                            if marker in text:
                                break
                            if time.monotonic() > marker_deadline:
                                raise RuntimeError(
                                    f"The {tab} tab did not render: {text[:300]}"
                                )
                            time.sleep(0.25)
                        time.sleep(0.8)
                        devtools.call("Input.dispatchMouseEvent", {
                            "type": "mouseMoved", "x": 1040, "y": 620,
                            "buttons": 0, "pointerType": "mouse",
                        })
                        time.sleep(0.4)
                    screenshot = devtools.call("Page.captureScreenshot", {
                        "format": "png", "captureBeyondViewport": False,
                    })
                    if devtools.errors:
                        raise RuntimeError(f"Browser rendering errors: {devtools.errors}")
                    data = base64.b64decode(screenshot["data"])
                    validate_screenshot(data)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(data)
                    devtools.call("Browser.close")
            except Exception:
                log.flush()
                print((directory / "server.log").read_text(encoding="utf-8")[-6000:], file=sys.stderr)
                raise
            finally:
                for process in (browser_process, server):
                    if process is None:
                        continue
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
    print(f"Screenshot saved: {output} ({output.stat().st_size:,} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "dist/gr8-launcher-home.png")
    parser.add_argument("--browser", help="Edge or Chrome executable (auto-detected by default)")
    parser.add_argument("--tab", choices=["home", "library", "mods", "skins", "settings", "accounts"],
                        default="home", help="Launcher tab to capture (default: home)")
    parser.add_argument("--checksum-file", type=Path, help="Add the PNG to an existing SHA256SUMS.txt")
    parser.add_argument("--serve", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve_preview(args.serve.resolve(), args.port)
        return
    capture(args.output.resolve(), find_browser(args.browser), args.tab)
    if args.checksum_file:
        lines = args.checksum_file.read_text(encoding="utf-8").splitlines()
        lines = [line for line in lines if line.split("  ", 1)[-1] != args.output.name]
        digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
        lines.append(f"{digest}  {args.output.name}")
        args.checksum_file.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
