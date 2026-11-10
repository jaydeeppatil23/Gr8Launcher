package com.gr8.performance;

import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.entrypoint.PreLaunchEntrypoint;

/**
 * Gr8 1K FPS: the Fabric mod entrypoint.
 *
 * <p>It runs before Minecraft reads {@code options.txt} and forces the settings
 * that remove the common frame-rate bottlenecks: the FPS cap is raised to 1000,
 * VSync is disabled, graphics are switched to Fast, and expensive effects are
 * reduced. It only touches launcher-controlled video options and never changes
 * worlds, accounts, or installed mods.</p>
 *
 * <p>The mod depends only on the Fabric Loader API, so the same JAR works on
 * every Minecraft version supported by Fabric (Java 8 and newer).</p>
 */
public final class Gr8PerformanceMod implements PreLaunchEntrypoint {
    @Override
    public void onPreLaunch() {
        // Remove LWJGL's per-call validation overhead before it initializes.
        System.setProperty("org.lwjgl.util.NoChecks", "true");
        Gr8Options.apply(FabricLoader.getInstance().getGameDir());
    }
}
