"""Build script for gr8-skin-bridge.jar companion Java agent.
Compiles the agent with Java 8 bytecode compatibility (--release 8) and bundles
ASM shaded into a private package (gr8launcher.skinbridge.asm) so Fabric Loader's
Knot classpath validator never throws duplicate ASM class exceptions.
"""

import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
BUILD_DIR = BASE_DIR / "build"
CLASSES_DIR = BUILD_DIR / "classes"
OUTPUT_JAR = BASE_DIR / "gr8-skin-bridge.jar"

# Look for asm jar in .minecraft libraries
APPDATA = os.environ.get("APPDATA", "")
MC_LIBRARIES = Path(APPDATA) / ".minecraft" / "libraries"


def find_asm_jar() -> Path | None:
    candidates = list(MC_LIBRARIES.glob("**/asm-[0-9]*.jar"))
    # Filter out asm-tree, asm-analysis, etc.
    valid = [
        p for p in candidates
        if p.name.startswith("asm-") and not any(x in p.name for x in ("tree", "analysis", "commons", "util", "all"))
    ]
    if valid:
        return sorted(valid, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return None


def find_javac() -> str:
    javac = shutil.which("javac")
    if javac:
        return javac
    for jdk in Path("C:/Program Files/Java").glob("jdk*"):
        candidate = jdk / "bin" / "javac.exe"
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("Could not find javac executable.")


def find_jar() -> str:
    jar = shutil.which("jar")
    if jar:
        return jar
    for jdk in Path("C:/Program Files/Java").glob("jdk*"):
        candidate = jdk / "bin" / "jar.exe"
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("Could not find jar executable.")


def relocate_class(data: bytes, old_pkg: bytes, new_pkg: bytes) -> bytes:
    """Relocates package references in a Java .class file constant pool."""
    if not data.startswith(b"\xCA\xFE\xBA\xBE"):
        return data
    magic, minor, major, cp_count = struct.unpack(">IHHH", data[:10])
    pos = 10
    new_cp = bytearray()
    i = 1
    while i < cp_count:
        tag = data[pos]
        pos += 1
        if tag == 1:  # CONSTANT_Utf8
            length = struct.unpack(">H", data[pos:pos + 2])[0]
            pos += 2
            val = data[pos:pos + length]
            pos += length
            if old_pkg in val:
                val = val.replace(old_pkg, new_pkg)
            new_cp.append(1)
            new_cp.extend(struct.pack(">H", len(val)))
            new_cp.extend(val)
        elif tag in (3, 4):  # Integer, Float
            new_cp.append(tag)
            new_cp.extend(data[pos:pos + 4])
            pos += 4
        elif tag in (5, 6):  # Long, Double (occupies 2 CP indices)
            new_cp.append(tag)
            new_cp.extend(data[pos:pos + 8])
            pos += 8
            i += 1
        elif tag in (7, 8, 16, 19, 20):  # Class, String, MethodType, Module, Package
            new_cp.append(tag)
            new_cp.extend(data[pos:pos + 2])
            pos += 2
        elif tag in (9, 10, 11, 12, 17, 18):  # Ref, NameAndType, Dynamic, InvokeDynamic
            new_cp.append(tag)
            new_cp.extend(data[pos:pos + 4])
            pos += 4
        elif tag == 15:  # MethodHandle
            new_cp.append(tag)
            new_cp.extend(data[pos:pos + 3])
            pos += 3
        else:
            raise ValueError(f"Unknown constant pool tag: {tag}")
        i += 1

    rest = data[pos:]
    return data[:8] + struct.pack(">H", cp_count) + bytes(new_cp) + rest


def build() -> Path:
    print("[Gr8SkinBridge Build] Starting build...")
    asm_jar = find_asm_jar()
    if not asm_jar:
        raise RuntimeError("Could not find asm jar in .minecraft libraries.")
    print(f"[Gr8SkinBridge Build] Using ASM from: {asm_jar}")

    javac = find_javac()
    jar_tool = find_jar()

    # Clean build dirs
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    CLASSES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Extract and relocate ASM classes into gr8launcher/skinbridge/asm/
    print("[Gr8SkinBridge Build] Unpacking & shading ASM to gr8launcher/skinbridge/asm/...")
    old_pkg = b"org/objectweb/asm"
    new_pkg = b"gr8launcher/skinbridge/asm"
    with zipfile.ZipFile(asm_jar, "r") as z:
        for member in z.infolist():
            if member.filename.startswith("org/objectweb/asm/") and member.filename.endswith(".class"):
                raw_bytes = z.read(member)
                relocated = relocate_class(raw_bytes, old_pkg, new_pkg)
                rel_path = member.filename.replace("org/objectweb/asm", "gr8launcher/skinbridge/asm")
                target_file = CLASSES_DIR / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_bytes(relocated)

    # 2. Compile SkinBridge sources with --release 8
    print("[Gr8SkinBridge Build] Compiling Java sources (--release 8)...")
    sources = list(SRC_DIR.glob("**/*.java"))
    compile_cmd = [
        javac,
        "--release", "8",
        "-cp", str(CLASSES_DIR),
        "-d", str(CLASSES_DIR),
    ] + [str(s) for s in sources]
    res = subprocess.run(compile_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Compilation error:\n{res.stderr}\n{res.stdout}")
        raise RuntimeError(f"javac failed with code {res.returncode}")

    # 3. Create Manifest
    manifest_path = BUILD_DIR / "MANIFEST.MF"
    manifest_content = (
        "Manifest-Version: 1.0\n"
        "Premain-Class: gr8launcher.skinbridge.SkinBridgeAgent\n"
        "Can-Redefine-Classes: true\n"
        "Can-Retransform-Classes: true\n"
        "\n"
    )
    manifest_path.write_bytes(manifest_content.encode("utf-8"))

    # 4. Package jar
    print(f"[Gr8SkinBridge Build] Packaging {OUTPUT_JAR}...")
    jar_cmd = [
        jar_tool,
        "cfm",
        str(OUTPUT_JAR),
        str(manifest_path),
        "-C", str(CLASSES_DIR),
        "."
    ]
    res = subprocess.run(jar_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"jar packaging error:\n{res.stderr}\n{res.stdout}")
        raise RuntimeError(f"jar failed with code {res.returncode}")

    # 5. Also copy to .minecraft/gr8launcher/tools
    tools_dir = Path(APPDATA) / ".minecraft" / "gr8launcher" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    target_in_tools = tools_dir / "gr8-skin-bridge.jar"
    shutil.copy2(OUTPUT_JAR, target_in_tools)
    print(f"[Gr8SkinBridge Build] Success! Deployed to: {target_in_tools} ({target_in_tools.stat().st_size} bytes)")
    return target_in_tools


if __name__ == "__main__":
    build()
