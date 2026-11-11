"""Build a Windows x64 portable ZIP and Inno Setup installer."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "Gr8Launcher"
VERSION_PATTERN = re.compile(
    r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


def parse_version(value: str) -> tuple[str, str]:
    """Return the release version and its numeric Windows version."""
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("Use a version such as 1.0.0 or v1.0.0-beta.1.")
    if any(int(part) > 65535 for part in match.group(1, 2, 3)):
        raise ValueError("Windows version components must be at most 65535.")
    if match[4] and any(
        part.isdigit() and len(part) > 1 and part.startswith("0")
        for part in match[4].split(".")
    ):
        raise ValueError("Numeric prerelease identifiers cannot have leading zeros.")
    return value.removeprefix("v"), ".".join(match.group(1, 2, 3))


def read_project_version(path: Path | None = None) -> str:
    """Read the version selected for the next release, including UTF-8 BOM files."""
    path = path if path is not None else PROJECT_ROOT / "VERSION"
    return parse_version(path.read_text(encoding="utf-8-sig").strip())[0]


def find_compiler(explicit_path: str | None) -> Path:
    candidates = [explicit_path, os.environ.get("ISCC_PATH"), shutil.which("ISCC")]
    for directory in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(directory):
            for version in ("7", "6"):
                candidates.append(
                    str(Path(os.environ[directory]) / f"Inno Setup {version}" / "ISCC.exe")
                )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise RuntimeError(
        "Install Inno Setup and pass --iscc PATH, set ISCC_PATH, "
        "or use --portable-only to build just the ZIP."
    )


def check_build_directory(path: Path) -> None:
    # Flet replaces its build/dist directories. Refuse redirected paths before
    # allowing it to clean these dedicated directories inside the workspace.
    if path.resolve() != path or not path.is_relative_to(PROJECT_ROOT):
        raise RuntimeError(f"Build directory must stay inside the workspace: {path}")


def build(version: str, compiler: Path | None) -> list[Path]:
    version, numeric_version = parse_version(version)
    if os.name != "nt" or struct.calcsize("P") != 8 or platform.machine() != "AMD64":
        raise RuntimeError("Build Windows x64 packages with a Windows x64 Python installation.")

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from tools.build_updater import build_updater
    updater = build_updater()

    assets = PROJECT_ROOT / ".assets"
    bridge = PROJECT_ROOT / "tools" / "gr8-skin-bridge" / "gr8-skin-bridge.jar"
    for required in (assets / "icons" / "gr8_icon.ico", bridge):
        if not required.is_file():
            raise RuntimeError(f"Required release asset is missing: {required}")

    work_dir = PROJECT_ROOT / "build" / "windows"
    output_dir = PROJECT_ROOT / "dist"
    for directory in (work_dir, work_dir / "build", work_dir / "dist", output_dir):
        check_build_directory(directory)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Flet's pack command includes and brands its desktop client, then invokes
    # PyInstaller. Keep its generated spec and temporary files under build/.
    command = [
        sys.executable, "-m", "flet.cli", "pack", str(PROJECT_ROOT / "main.py"),
        "--yes", "--onedir", "--name", APP_NAME,
        "--icon", str(assets / "icons" / "gr8_icon.ico"),
        "--product-name", "Gr8 Launcher",
        "--file-description", "Minecraft Java Edition launcher",
        "--product-version", version,
        "--file-version", f"{numeric_version}.0",
        "--company-name", "Gr8 Projects",
        "--add-data", f"{assets}{os.pathsep}.assets",
        "--add-data", f"{bridge}{os.pathsep}tools/gr8-skin-bridge",
        "--", "--clean",
        "--paths", str(PROJECT_ROOT),
        "--collect-submodules", "minecraft_launcher_lib",
        "--collect-submodules", "keyring.backends",
        "--copy-metadata", "keyring",
    ]
    subprocess.run(command, cwd=work_dir, check=True)

    app_dir = work_dir / "dist" / APP_NAME
    if not (app_dir / f"{APP_NAME}.exe").is_file():
        raise RuntimeError("The pack command did not produce the launcher executable.")
    for filename in ("LICENSE", "README.md"):
        shutil.copy2(PROJECT_ROOT / filename, app_dir / filename)
    (app_dir / "VERSION").write_text(f"{version}\n", encoding="utf-8", newline="\n")
    shutil.copy2(updater, app_dir / "updater.exe")
    # Import the UI-independent inventory writer from the project source.
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from launcher_updates import generate_manifest
    generate_manifest(app_dir, version)

    archive_name = f"{APP_NAME}-{version}-windows-x64"
    # zipfile (used by make_archive) also includes dot directories and files.
    archive = Path(shutil.make_archive(
        str(output_dir / archive_name), "zip",
        root_dir=app_dir.parent, base_dir=app_dir.name,
    ))
    packages = [archive]

    if compiler:
        subprocess.run([
            str(compiler),
            f"/DAppVersion={version}",
            f"/DNumericVersion={numeric_version}",
            f"/DPackageDir={app_dir}",
            f"/DOutputDir={output_dir}",
            str(PROJECT_ROOT / "packaging" / "windows.iss"),
        ], cwd=PROJECT_ROOT, check=True)
        installer = output_dir / f"{archive_name}-setup.exe"
        if not installer.is_file():
            raise RuntimeError("Inno Setup did not produce the expected installer.")
        packages.append(installer)

    checksum_lines = []
    for package in packages:
        with package.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        checksum_lines.append(f"{digest}  {package.name}\n")
    (output_dir / "SHA256SUMS.txt").write_text("".join(checksum_lines), encoding="utf-8", newline="\n")
    return packages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Package version (defaults to the VERSION file)")
    parser.add_argument("--iscc", help="Path to Inno Setup's ISCC.exe compiler")
    parser.add_argument("--portable-only", action="store_true")
    args = parser.parse_args()
    try:
        version = args.version if args.version is not None else read_project_version()
        parse_version(version)
        compiler = None if args.portable_only else find_compiler(args.iscc)
        packages = build(version, compiler)
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Build failed: {error}\n")
    for package in packages:
        print(f"Built: {package}")
    print(f"Checksums: {PROJECT_ROOT / 'dist' / 'SHA256SUMS.txt'}")


if __name__ == "__main__":
    main()
