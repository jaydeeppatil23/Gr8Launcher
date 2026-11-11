"""Compile the small Windows GUI updater using the system .NET Framework compiler."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def build_updater(output: Path | None = None) -> Path:
    if os.name != "nt":
        raise RuntimeError("The updater must be built on Windows.")
    output = (output or ROOT / "build" / "updater" / "updater.exe").absolute()
    if output.resolve() != output or not output.is_relative_to(ROOT):
        raise RuntimeError("Updater build output must stay inside the workspace.")
    compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not compiler.is_file():
        raise RuntimeError("The Windows .NET Framework C# compiler is unavailable.")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
        "/warnaserror+", f"/out:{output}",
        f"/win32icon:{ROOT / '.assets' / 'icons' / 'gr8_icon.ico'}",
        f"/win32manifest:{ROOT / 'tools' / 'updater' / 'app.manifest'}",
        "/reference:System.dll", "/reference:System.Core.dll", "/reference:System.Drawing.dll",
        "/reference:System.Windows.Forms.dll", "/reference:System.Web.Extensions.dll",
        str(ROOT / "tools" / "updater" / "Program.cs"),
    ], cwd=ROOT, check=True)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(build_updater(args.output))
