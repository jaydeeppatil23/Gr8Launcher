package gr8launcher.skinbridge;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Collection;
import java.util.UUID;

public class SkinBridgeHook {
    public static volatile int localPort = 0;
    public static volatile String activeUser = "";
    public static volatile String activeModel = "classic";
    public static volatile String activeHash = "";

    public static void parseArgs(String args) {
        if (args == null || args.trim().isEmpty()) {
            return;
        }
        String[] parts = args.split(",");
        for (String part : parts) {
            String[] kv = part.split("=", 2);
            if (kv.length == 2) {
                String k = kv[0].trim().toLowerCase();
                String v = kv[1].trim();
                if ("port".equals(k)) {
                    try {
                        localPort = Integer.parseInt(v);
                    } catch (Exception ignored) {}
                } else if ("user".equals(k)) {
                    activeUser = v;
                } else if ("model".equals(k)) {
                    activeModel = v;
                } else if ("hash".equals(k)) {
                    activeHash = v;
                }
            } else if (kv.length == 1) {
                try {
                    localPort = Integer.parseInt(kv[0].trim());
                } catch (Exception ignored) {}
            }
        }
    }

    public static Object getOrFillProperties(Object originalMap, Object profile) {
        if (profile == null) {
            return originalMap;
        }

        try {
            // 1. Check if originalMap already has non-empty textures
            if (originalMap != null) {
                try {
                    Method containsKey = originalMap.getClass().getMethod("containsKey", Object.class);
                    boolean exists = (Boolean) containsKey.invoke(originalMap, "textures");
                    if (exists) {
                        Method getMethod = originalMap.getClass().getMethod("get", Object.class);
                        Object col = getMethod.invoke(originalMap, "textures");
                        if (col instanceof Collection) {
                            if (!((Collection<?>) col).isEmpty()) {
                                return originalMap; // Keep genuine textures
                            }
                        }
                    }
                } catch (Exception ignored) {}
            }

            // 2. Extract player name
            String name = null;
            try {
                name = (String) profile.getClass().getMethod("name").invoke(profile);
            } catch (NoSuchMethodException e) {
                try {
                    name = (String) profile.getClass().getMethod("getName").invoke(profile);
                } catch (NoSuchMethodException e2) {
                    return originalMap;
                }
            }
            if (name == null || name.trim().isEmpty()) {
                return originalMap;
            }
            name = name.trim();

            // 3. Extract player UUID
            UUID id = null;
            try {
                id = (UUID) profile.getClass().getMethod("id").invoke(profile);
            } catch (NoSuchMethodException e) {
                try {
                    id = (UUID) profile.getClass().getMethod("getId").invoke(profile);
                } catch (NoSuchMethodException e2) {
                    id = null;
                }
            }
            if (id == null) {
                id = UUID.nameUUIDFromBytes(("OfflinePlayer:" + name).getBytes(StandardCharsets.UTF_8));
            }

            // 4. Resolve port
            int port = localPort;
            if (port <= 0) {
                String sysPort = System.getProperty("gr8skin.port");
                if (sysPort != null) {
                    try {
                        port = Integer.parseInt(sysPort.trim());
                    } catch (Exception ignored) {}
                }
            }
            if (port <= 0) {
                return originalMap;
            }

            // 5. Determine model
            String model = "classic";
            String sysUser = System.getProperty("gr8skin.user");
            String sysModel = System.getProperty("gr8skin.model");

            if (name.equalsIgnoreCase(activeUser) || (sysUser != null && name.equalsIgnoreCase(sysUser))) {
                if (sysModel != null && !sysModel.isEmpty()) {
                    model = sysModel;
                } else {
                    model = activeModel;
                }
            } else {
                if ((id.hashCode() & 1) != 0) {
                    model = "slim";
                }
            }

            // 6. Build textures JSON payload
            String rawId = id.toString().replace("-", "");
            long ts = System.currentTimeMillis();

            String skinHash = activeHash;
            if (skinHash == null || skinHash.trim().isEmpty()) {
                skinHash = System.getProperty("gr8skin.hash");
            }
            if (skinHash == null || skinHash.trim().isEmpty()) {
                skinHash = rawId;
            }

            String textureUrl;
            if (name.equalsIgnoreCase(activeUser) || (sysUser != null && name.equalsIgnoreCase(sysUser))) {
                textureUrl = "http://127.0.0.1:" + port + "/textures/" + name + "/" + skinHash + ".png";
            } else {
                textureUrl = "http://127.0.0.1:" + port + "/textures/" + name + "/" + rawId + ".png";
            }

            StringBuilder json = new StringBuilder();
            json.append("{\"timestamp\":").append(ts);
            json.append(",\"profileId\":\"").append(rawId).append("\"");
            json.append(",\"profileName\":\"").append(name).append("\"");
            json.append(",\"textures\":{\"SKIN\":{\"url\":\"").append(textureUrl).append("\"");
            if ("slim".equalsIgnoreCase(model)) {
                json.append(",\"metadata\":{\"model\":\"slim\"}");
            }
            json.append("}}}");

            String b64 = Base64.getEncoder().encodeToString(json.toString().getBytes(StandardCharsets.UTF_8));

            ClassLoader cl = profile.getClass().getClassLoader();
            if (cl == null) {
                cl = Thread.currentThread().getContextClassLoader();
            }
            if (cl == null) {
                cl = ClassLoader.getSystemClassLoader();
            }

            // 7. Instantiate Property("textures", b64, signature)
            Class<?> propClass = Class.forName("com.mojang.authlib.properties.Property", true, cl);
            Object prop = null;
            try {
                Constructor<?> propCtor3 = propClass.getConstructor(String.class, String.class, String.class);
                prop = propCtor3.newInstance("textures", b64, "gr8skin_signature");
            } catch (Throwable t) {
                Constructor<?> propCtor2 = propClass.getConstructor(String.class, String.class);
                prop = propCtor2.newInstance("textures", b64);
            }
            System.out.println("[Gr8SkinBridge] Injected custom texture property for player: " + name);

            // 8. Create PropertyMap
            Class<?> propMapClass = Class.forName("com.mojang.authlib.properties.PropertyMap", true, cl);
            Class<?> multimapInterface = Class.forName("com.google.common.collect.Multimap", true, cl);
            try {
                // Legacy Authlib < 10: has mutable no-arg constructor
                Constructor<?> noArg = propMapClass.getConstructor();
                Object newMap = noArg.newInstance();
                if (originalMap != null) {
                    try {
                        Method putAll = newMap.getClass().getMethod("putAll", multimapInterface);
                        putAll.invoke(newMap, originalMap);
                    } catch (Exception ignored) {}
                }
                Method putMethod = newMap.getClass().getMethod("put", Object.class, Object.class);
                putMethod.invoke(newMap, "textures", prop);
                return newMap;
            } catch (NoSuchMethodException e) {
                // Modern Authlib 10+: constructor takes Multimap and copies to ImmutableMultimap
                Class<?> multimapClass = Class.forName("com.google.common.collect.LinkedHashMultimap", true, cl);
                Object multimap = multimapClass.getMethod("create").invoke(null);
                if (originalMap != null) {
                    try {
                        Method putAll = multimap.getClass().getMethod("putAll", multimapInterface);
                        putAll.invoke(multimap, originalMap);
                    } catch (Exception ignored) {}
                }
                Method putMethod = multimap.getClass().getMethod("put", Object.class, Object.class);
                putMethod.invoke(multimap, "textures", prop);

                Constructor<?> mapCtor = propMapClass.getConstructor(multimapInterface);
                return mapCtor.newInstance(multimap);
            }

        } catch (Throwable t) {
            System.err.println("[Gr8SkinBridge] Error filling properties: " + t.getMessage());
            t.printStackTrace();
            return originalMap;
        }
    }
}
