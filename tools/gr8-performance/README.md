# Gr8 1K FPS

The built-in performance JAR shipped with Gr8 Launcher. It applies the same
preset as **1K FPS Mode** before Minecraft reads `options.txt`:

- `maxFps:1000` and VSync off
- Fast graphics, fewer particles, no entity shadows or smooth lighting
- Lower render/simulation distance, cloud, and mipmap settings
- `org.lwjgl.util.NoChecks=true` to skip LWJGL validation overhead

It also patches the F3 debug HUD so the FPS line always reads `1000 fps`.

The JAR is deliberately version-independent. It depends only on the Fabric
Loader API, so the same file works on every Minecraft version supported by
Fabric. A `Premain-Class` entrypoint lets the launcher use it as a Java agent
on Vanilla, Forge, NeoForge, and OptiFine profiles, where Fabric mods cannot
load.

## How the FPS line is patched

`Gr8FpsTransformer` is an ASM `ClassFileTransformer`. It looks for the
`String.format("%d fps ...", ...)` call that builds the debug HUD line, then
replaces the value feeding the first `%d` with the constant `1000`. Finding the
pattern in bytecode means no version-specific mappings are needed, so it works
on obfuscated and unobfuscated jars. Every other value on the line (coordinates,
chunk updates, TPS) is left alone.

The transformer only changes the displayed number. Real frame pacing still
depends on the CPU and GPU; the preset removes caps and expensive settings so
the game can render as fast as the hardware allows.

## Files

- `src/com/gr8/performance/Gr8Options.java`: loader-free preset logic
- `src/com/gr8/performance/Gr8PerformanceMod.java`: Fabric `preLaunch` entrypoint
- `src/com/gr8/performance/Gr8PerformanceAgent.java`: Java agent entrypoint
- `src/com/gr8/performance/Gr8FpsTransformer.java`: F3 FPS bytecode patch
- `build_mod.py`: compiles the sources and packages the JAR
- `gr8-performance.jar`: built artifact, also copied to `.assets/mods/`

## Build

Use a JDK 8 or newer, any Fabric Loader 0.14+ JAR, and ASM 9.11.1:

```powershell
python tools/gr8-performance/build_mod.py `
  --javac "C:\path\to\jdk\bin\javac.exe" `
  --loader "C:\path\to\fabric-loader-0.14.24.jar" `
  --asm-core "C:\path\to\asm-9.11.1.jar" `
  --asm-tree "C:\path\to\asm-tree-9.11.1.jar"
```

ASM is bundled into the JAR, so the agent needs no extra libraries at runtime.
The build copies the JAR to `.assets/mods/gr8-performance.jar`, which is the
copy the packaged launcher uses.
