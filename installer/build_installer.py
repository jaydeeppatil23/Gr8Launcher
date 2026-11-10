"""Build the branded Gr8 Launcher setup.exe.

The installer is a custom .NET WinForms stub (Setup.cs) with the whole
packaged application appended as a single ZIP payload:

    [setup stub][zip payload][GR8PAY01][offset: int64][length: int64]

Run from the repository root after a PyInstaller/Flet build:

    python installer/build_installer.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_DIR = ROOT / "installer"
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
ICON = ROOT / ".assets" / "icons" / "gr8_icon.ico"
LOGO = ROOT / ".assets" / "icons" / "gr8_icon.png"
FONT = ROOT / ".assets" / "fonts" / "PixelifySans.ttf"
PAYLOAD_MAGIC = b"GR8PAY01"

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<assembly manifestVersion="1.0" xmlns="urn:schemas-microsoft-com:asm.v1">
  <assemblyIdentity version="1.0.0.0" name="Gr8.Launcher.Setup"/>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true</dpiAware>
    </windowsSettings>
  </application>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
</assembly>
"""


def read_version() -> str:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit("VERSION is empty")
    return version


def version_parts(version: str) -> list[int]:
    parts: list[int] = []
    for chunk in version.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return parts[:4]


def write_support_files(work: Path, version: str) -> tuple[Path, Path]:
    parts = version_parts(version)
    version_cs = work / "GeneratedVersion.cs"
    version_cs.write_text(
        "using System.Reflection;\n"
        f'[assembly: AssemblyVersion("{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}")]\n'
        f'[assembly: AssemblyFileVersion("{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}")]\n'
        f'[assembly: AssemblyInformationalVersion("{version}")]\n',
        encoding="utf-8",
    )
    manifest = work / "app.manifest"
    manifest.write_text(MANIFEST, encoding="utf-8")
    return version_cs, manifest


def compile_exe(source: Path, output: Path, version_cs: Path, manifest: Path, icon: Path | None = None) -> None:
    command = [
        str(CSC),
        "/nologo",
        "/target:winexe",
        "/optimize+",
        "/platform:anycpu",
        f"/out:{output}",
        f"/win32manifest:{manifest}",
        "/r:System.dll",
        "/r:System.Core.dll",
        "/r:System.Windows.Forms.dll",
        "/r:System.Drawing.dll",
        "/r:System.Management.dll",
        "/r:System.IO.Compression.dll",
        "/r:System.IO.Compression.FileSystem.dll",
        f"/resource:{LOGO},Gr8Setup.icon.png",
        f"/resource:{FONT},Gr8Setup.font.ttf",
        str(source),
        str(version_cs),
    ]
    if icon is not None:
        command.insert(4, f"/win32icon:{icon}")
    print("Compiling", output.name, "...")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not output.exists():
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"csc failed while compiling {output.name}")
    for line in (result.stdout or "").splitlines():
        if line.strip():
            print(" ", line.strip())


def zip_payload(app_dir: Path, destination: Path) -> int:
    print("Packaging", app_dir, "...")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app_dir.rglob("*")):
            if path.is_dir():
                continue
            relative = path.relative_to(app_dir).as_posix()
            archive.write(path, relative)
    return destination.stat().st_size


def append_payload(stub: Path, payload: Path, destination: Path) -> None:
    offset = stub.stat().st_size
    length = payload.stat().st_size
    with open(destination, "wb") as output:
        with open(stub, "rb") as source:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        with open(payload, "rb") as source:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        output.write(PAYLOAD_MAGIC)
        output.write(struct.pack("<qq", offset, length))
    print(f"Wrote {destination} ({destination.stat().st_size / 1024 / 1024:.1f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Gr8Launcher-Setup.exe")
    parser.add_argument(
        "--app-dir",
        type=Path,
        default=ROOT / "build" / "release" / "dist" / "Gr8Launcher",
        help="Packaged onedir application folder",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist",
        help="Output folder for the setup executable",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Keep the intermediate payload zip",
    )
    args = parser.parse_args()

    app_dir = args.app_dir.resolve()
    if not (app_dir / "Gr8Launcher.exe").is_file():
        raise SystemExit(f"Gr8Launcher.exe not found in {app_dir}")
    if not CSC.is_file():
        raise SystemExit(f"csc.exe not found at {CSC}")

    version = read_version()
    print(f"Building Gr8 Launcher Setup {version}")

    for name in ("LICENSE", "README.md"):
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, app_dir / name)
    shutil.copy2(ROOT / "VERSION", app_dir / "VERSION")

    work = Path(tempfile.mkdtemp(prefix="gr8build-", dir=str(ROOT / "build")))
    try:
        version_cs, manifest = write_support_files(work, version)

        uninstall_exe = app_dir / "Uninstall.exe"
        compile_exe(INSTALLER_DIR / "Uninstall.cs", uninstall_exe, version_cs, manifest, ICON)

        payload = work / f"Gr8Launcher-{version}.zip"
        zip_size = zip_payload(app_dir, payload)
        print(f"Payload: {zip_size / 1024 / 1024:.1f} MB")

        stub = work / "setup-stub.exe"
        compile_exe(INSTALLER_DIR / "Setup.cs", stub, version_cs, manifest, ICON)

        args.output.mkdir(parents=True, exist_ok=True)
        final = args.output / f"Gr8Launcher-Setup-{version}.exe"
        if final.exists():
            final.unlink()
        append_payload(stub, payload, final)
        print(f"Deliverable: {final}")
    finally:
        if args.no_clean:
            print(f"Intermediate files kept in {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
