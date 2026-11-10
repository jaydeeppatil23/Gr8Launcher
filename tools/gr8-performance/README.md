# Gr8 1K FPS

The built-in performance JAR shipped with Gr8 Launcher. It applies the same
preset as **1K FPS Mode** before Minecraft reads `options.txt`:

- `maxFps:1000` and VSync off
- Fast graphics, fewer particles, no entity shadows or smooth lighting
- Lower render/simulation distance, cloud, and mipmap settings
- `org.lwjgl.util.NoChecks=true` to skip LWJGL validation overhead

The JAR is deliberately version-independent. It depends only on the Fabric
Loader API, so the same file works on every Minecraft version supported by
Fabric. A `Premain-Class` entrypoint lets the launcher use it as a Java agent
on Vanilla, Forge, NeoForge, and OptiFine profiles, where Fabric mods cannot
load.

The actual frame rate still depends on the CPU and GPU. The mod removes caps
and expensive settings; it does not fake the in-game F3 counter.

## Files

- `src/com/gr8/performance/Gr8Options.java`: loader-free preset logic
- `src/com/gr8/performance/Gr8PerformanceMod.java`: Fabric `preLaunch` entrypoint
- `src/com/gr8/performance/Gr8PerformanceAgent.java`: Java agent entrypoint
- `build_mod.py`: compiles the sources and packages the JAR
- `gr8-performance.jar`: built artifact, also copied to `.assets/mods/`

## Build

Use a JDK 8 or newer and any Fabric Loader 0.14+ JAR:

```powershell
python tools/gr8-performance/build_mod.py `
  --javac "C:\path\to\jdk\bin\javac.exe" `
  --loader "C:\path\to\fabric-loader-0.14.24.jar"
```

The build copies the JAR to `.assets/mods/gr8-performance.jar`, which is the
copy the packaged launcher uses.
