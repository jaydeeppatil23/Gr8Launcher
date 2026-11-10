package com.gr8.performance;

import java.lang.instrument.Instrumentation;
import java.nio.file.Paths;

/**
 * Java-agent entrypoint used when the launcher cannot load a Fabric mod
 * (Vanilla, Forge, NeoForge, or OptiFine profiles).
 *
 * <p>The launcher passes the profile's game directory after an equals sign, for
 * example {@code -javaagent:gr8-performance.jar=C:\...\.minecraft}. The agent
 * applies the same preset before Minecraft reads {@code options.txt}.</p>
 */
public final class Gr8PerformanceAgent {
    private Gr8PerformanceAgent() {
    }

    public static void premain(String agentArguments, Instrumentation instrumentation) {
        System.setProperty("org.lwjgl.util.NoChecks", "true");
        if (agentArguments == null || agentArguments.trim().isEmpty()) {
            return;
        }
        try {
            Gr8Options.applyOptions(Paths.get(agentArguments.trim()).resolve("options.txt"));
        } catch (RuntimeException ignored) {
            // Never stop the game because the preset could not be written.
        }
    }
}
