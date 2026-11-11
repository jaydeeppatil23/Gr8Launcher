package com.gr8.performance;

import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Paths;
import java.security.CodeSource;

/**
 * Java-agent entrypoint used when the launcher cannot load a Fabric mod
 * (Vanilla, Forge, NeoForge, or OptiFine profiles).
 *
 * <p>The launcher passes the profile's game directory after an equals sign, for
 * example {@code -javaagent:gr8-performance.jar=C:\...\.minecraft}. The agent
 * applies the same preset before Minecraft reads {@code options.txt} and
 * installs the F3 FPS transformer.</p>
 */
public final class Gr8PerformanceAgent {
    private Gr8PerformanceAgent() {
    }

    public static void premain(String agentArguments, Instrumentation instrumentation) {
        System.setProperty("org.lwjgl.util.NoChecks", "true");
        registerTransformer(instrumentation);
        if (agentArguments == null || agentArguments.trim().isEmpty()) {
            return;
        }
        try {
            Gr8Options.applyOptions(Paths.get(agentArguments.trim()).resolve("options.txt"));
        } catch (RuntimeException ignored) {
            // Never stop the game because the preset could not be written.
        }
    }

    /**
     * Load the transformer and its bundled ASM in an isolated loader.
     *
     * <p>Mod loaders put their own ASM on the system classpath. Loading our copy
     * in a child loader keeps the transformer on the exact ASM version it was
     * built against, so the FPS patch cannot break or silently stop matching
     * when a loader ships a different version.</p>
     */
    private static void registerTransformer(Instrumentation instrumentation) {
        try {
            CodeSource source = Gr8PerformanceAgent.class.getProtectionDomain().getCodeSource();
            URL location = source == null ? null : source.getLocation();
            if (location != null) {
                ClassLoader parent = ClassLoader.getSystemClassLoader().getParent();
                URLClassLoader isolated = new URLClassLoader(new URL[]{location}, parent);
                Class<?> type = isolated.loadClass("com.gr8.performance.Gr8FpsTransformer");
                ClassFileTransformer transformer =
                        (ClassFileTransformer) type.getDeclaredConstructor().newInstance();
                instrumentation.addTransformer(transformer, false);
                return;
            }
        } catch (Throwable ignored) {
            // Fall back to the system classpath copy below.
        }
        try {
            instrumentation.addTransformer(new Gr8FpsTransformer(), false);
        } catch (Throwable ignored) {
            // Never stop the launch because the FPS patch could not attach.
        }
    }
}
