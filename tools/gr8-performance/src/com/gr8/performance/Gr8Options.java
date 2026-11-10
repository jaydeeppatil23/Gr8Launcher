package com.gr8.performance;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Applies the Gr8 1K FPS preset to options.txt.
 *
 * <p>This class deliberately has no Minecraft or Fabric dependencies so the
 * Java agent can use it without a mod loader on the classpath.</p>
 */
final class Gr8Options {
    private static final Map<String, String> OPTIONS = new LinkedHashMap<String, String>();

    static {
        OPTIONS.put("maxFps", "1000");
        OPTIONS.put("enableVsync", "false");
        OPTIONS.put("graphicsMode", "0"); // Fast
        OPTIONS.put("fancyGraphics", "false"); // Legacy equivalent
        OPTIONS.put("renderDistance", "10");
        OPTIONS.put("simulationDistance", "8");
        OPTIONS.put("entityShadows", "false");
        OPTIONS.put("ao", "false");
        OPTIONS.put("particles", "1"); // Decreased
        OPTIONS.put("biomeBlendRadius", "2");
        OPTIONS.put("entityDistanceScaling", "0.75");
        OPTIONS.put("renderClouds", "false");
        OPTIONS.put("mipmapLevels", "0");
    }

    private Gr8Options() {
    }

    static void apply(Path gameDirectory) {
        try {
            applyOptions(gameDirectory.resolve("options.txt"));
        } catch (RuntimeException ignored) {
            // A missing or read-only game folder must never stop the launch.
        }
    }

    static void applyOptions(Path optionsPath) {
        String existing = "";
        try {
            if (Files.isRegularFile(optionsPath)) {
                existing = new String(Files.readAllBytes(optionsPath), StandardCharsets.UTF_8);
            }
        } catch (IOException ignored) {
            return;
        }

        List<String> lines = new ArrayList<String>();
        for (String line : existing.split("\\r?\\n", -1)) {
            lines.add(line);
        }
        if (!lines.isEmpty() && lines.get(lines.size() - 1).isEmpty()) {
            lines.remove(lines.size() - 1);
        }

        Map<String, Boolean> seen = new LinkedHashMap<String, Boolean>();
        for (int index = 0; index < lines.size(); index++) {
            String line = lines.get(index);
            int separator = line.indexOf(':');
            if (separator <= 0) {
                continue;
            }
            String key = line.substring(0, separator);
            if (OPTIONS.containsKey(key)) {
                lines.set(index, key + ":" + OPTIONS.get(key));
                seen.put(key, Boolean.TRUE);
            }
        }
        for (Map.Entry<String, String> option : OPTIONS.entrySet()) {
            if (!seen.containsKey(option.getKey())) {
                lines.add(option.getKey() + ":" + option.getValue());
            }
        }

        StringBuilder builder = new StringBuilder();
        for (String line : lines) {
            builder.append(line).append('\n');
        }
        String content = builder.toString();
        if (content.equals(existing)) {
            return;
        }

        Path temporary = optionsPath.resolveSibling("options.txt.gr8tmp");
        try {
            Files.write(temporary, content.getBytes(StandardCharsets.UTF_8));
            try {
                Files.move(temporary, optionsPath, StandardCopyOption.REPLACE_EXISTING,
                        StandardCopyOption.ATOMIC_MOVE);
            } catch (AtomicMoveNotSupportedException unsupported) {
                Files.move(temporary, optionsPath, StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (IOException ignored) {
            try {
                Files.deleteIfExists(temporary);
            } catch (IOException alsoIgnored) {
                // Keep the launch alive.
            }
        }
    }
}
