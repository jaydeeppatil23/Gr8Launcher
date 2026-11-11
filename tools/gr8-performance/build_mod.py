"""Build the version-independent Gr8 1K FPS Fabric mod.

The JAR doubles as a Java agent, so it also works on Vanilla, Forge, NeoForge
and OptiFine profiles. ASM is bundled because the agent rewrites the F3 FPS
line at runtime.

Requirements:
  * A JDK 8+ with javac (set JAVA_HOME or pass --javac).
  * fabric-loader-0.14.24.jar (or newer) for the Fabric entrypoint.
  * asm-9.11.1.jar and asm-tree-9.11.1.jar for the FPS transformer.

Example:
  python tools/gr8-performance/build_mod.py --javac C:/jdk8/bin/javac.exe \
      --loader temp/modbuild/fabric-loader-0.14.24.jar \
      --asm-core temp/modbuild/asm-9.11.1.jar \
      --asm-tree temp/modbuild/asm-tree-9.11.1.jar
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD_ROOT = Path(__file__).resolve().parent
SOURCE = MOD_ROOT / "src"
OUTPUT = MOD_ROOT / "gr8-performance.jar"
ASSET_OUTPUT = ROOT / ".assets" / "mods" / "gr8-performance.jar"
BUILD_DIR = MOD_ROOT / "build"
MANIFEST = """Manifest-Version: 1.0
Implementation-Title: Gr8 1K FPS
Implementation-Version: 1.0.0
Premain-Class: com.gr8.performance.Gr8PerformanceAgent
Can-Redefine-Classes: false

"""


def find_javac(explicit: str | None) -> Path:
    candidates = [explicit, os.environ.get("JAVAC")]
    if os.environ.get("JAVA_HOME"):
        candidates.append(str(Path(os.environ["JAVA_HOME"]) / "bin" / "javac.exe"))
    found = shutil.which("javac")
    if found:
        candidates.append(found)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise SystemExit("javac was not found. Set JAVA_HOME or pass --javac.")


def find_loader(explicit: str | None) -> Path:
    candidates = [explicit]
    candidates.append(str(ROOT / "temp" / "modbuild" / "fabric-loader-0.14.24.jar"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise SystemExit("fabric-loader JAR was not found. Pass --loader PATH.")


def find_asm(explicit: str | None, default_name: str) -> Path:
    candidates = [explicit]
    candidates.append(str(ROOT / "temp" / "modbuild" / default_name))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise SystemExit(f"{default_name} was not found. Pass --asm-core/--asm-tree.")


def build(javac: Path, loader: Path, asm_core: Path, asm_tree: Path) -> Path:
    if BUILD_DIR.is_dir():
        shutil.rmtree(BUILD_DIR)
    classes = BUILD_DIR / "classes"
    classes.mkdir(parents=True)
    sources = sorted(SOURCE.rglob("*.java"))
    command = [
        str(javac),
        "-source", "8",
        "-target", "8",
        "-Xlint:-options",
        "-encoding", "UTF-8",
        "-cp", os.pathsep.join((str(loader), str(asm_core), str(asm_tree))),
        "-d", str(classes),
        *map(str, sources),
    ]
    subprocess.run(command, check=True)
    shutil.copy2(SOURCE / "fabric.mod.json", classes / "fabric.mod.json")

    # Bundle ASM so the Java agent can rewrite the debug HUD without needing
    # libraries on Minecraft's classpath.
    for library in (asm_core, asm_tree):
        with zipfile.ZipFile(library) as archive:
            for entry in archive.namelist():
                if not entry.startswith("org/objectweb/asm/") or entry.endswith("/"):
                    continue
                target = classes / entry
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/MANIFEST.MF", MANIFEST)
        for entry in sorted(classes.rglob("*")):
            if entry.is_file():
                archive.write(entry, entry.relative_to(classes).as_posix())

    ASSET_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUTPUT, ASSET_OUTPUT)
    return OUTPUT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--javac", help="Path to javac.exe")
    parser.add_argument("--loader", help="Path to fabric-loader JAR")
    parser.add_argument("--asm-core", help="Path to asm-9.11.1.jar")
    parser.add_argument("--asm-tree", help="Path to asm-tree-9.11.1.jar")
    args = parser.parse_args()
    output = build(
        find_javac(args.javac),
        find_loader(args.loader),
        find_asm(args.asm_core, "asm-9.11.1.jar"),
        find_asm(args.asm_tree, "asm-tree-9.11.1.jar"),
    )
    print(f"Built {output} ({output.stat().st_size:,} bytes)")
    print(f"Copied to {ASSET_OUTPUT}")


if __name__ == "__main__":
    main()
