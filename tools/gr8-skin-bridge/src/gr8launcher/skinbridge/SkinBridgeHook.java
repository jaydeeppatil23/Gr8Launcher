package gr8launcher.skinbridge;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Collection;
import java.util.Collections;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

public class SkinBridgeHook {
    public static volatile int localPort = 0;
    public static volatile String activeUser = "";
    public static volatile String activeModel = "classic";
    public static volatile String activeHash = "";

    private static final Set<String> loggedPlayers = Collections.newSetFromMap(new ConcurrentHashMap<String, Boolean>());
    private static volatile ReflectionCache cachedReflect = null;

    private static class ReflectionCache {
        final ClassLoader loader;
        final Constructor<?> propCtor3;
        final Constructor<?> propCtor2;
        final Class<?> propMapClass;
        final Class<?> multimapInterface;
        final Constructor<?> mapNoArgCtor;
        final Method putAll;
        final Method putMethod;
        final Method multimapCreate;
        final Constructor<?> mapCtorWithMultimap;

        ReflectionCache(ClassLoader cl) throws Exception {
            this.loader = cl;
            Class<?> propClass = Class.forName("com.mojang.authlib.properties.Property", true, cl);
            Constructor<?> c3 = null;
            Constructor<?> c2 = null;
            try {
                c3 = propClass.getConstructor(String.class, String.class, String.class);
            } catch (Throwable ignored) {}
            try {
                c2 = propClass.getConstructor(String.class, String.class);
            } catch (Throwable ignored) {}
            this.propCtor3 = c3;
            this.propCtor2 = c2;

            this.propMapClass = Class.forName("com.mojang.authlib.properties.PropertyMap", true, cl);
            this.multimapInterface = Class.forName("com.google.common.collect.Multimap", true, cl);

            Constructor<?> noArg = null;
            Method pAll = null;
            Method pMeth = null;
            Method mmCreate = null;
            Constructor<?> mCtor = null;

            try {
                noArg = propMapClass.getConstructor();
                pAll = noArg.getDeclaringClass().getMethod("putAll", multimapInterface);
                pMeth = noArg.getDeclaringClass().getMethod("put", Object.class, Object.class);
            } catch (NoSuchMethodException e) {
                Class<?> mmCls = Class.forName("com.google.common.collect.LinkedHashMultimap", true, cl);
                mmCreate = mmCls.getMethod("create");
                pAll = mmCls.getMethod("putAll", multimapInterface);
                pMeth = mmCls.getMethod("put", Object.class, Object.class);
                mCtor = propMapClass.getConstructor(multimapInterface);
            }

            this.mapNoArgCtor = noArg;
            this.putAll = pAll;
            this.putMethod = pMeth;
            this.multimapCreate = mmCreate;
            this.mapCtorWithMultimap = mCtor;
        }

        Object createProperty(String name, String value, String signature) throws Exception {
            if (propCtor3 != null) {
                return propCtor3.newInstance(name, value, signature);
            }
            return propCtor2.newInstance(name, value);
        }

        Object createPropertyMap(Object originalMap, Object prop) throws Exception {
            if (mapNoArgCtor != null) {
                Object newMap = mapNoArgCtor.newInstance();
                if (originalMap != null && putAll != null) {
                    try {
                        putAll.invoke(newMap, originalMap);
                    } catch (Exception ignored) {}
                }
                if (putMethod != null) {
                    putMethod.invoke(newMap, "textures", prop);
                }
                return newMap;
            } else {
                Object multimap = multimapCreate.invoke(null);
                if (originalMap != null && putAll != null) {
                    try {
                        putAll.invoke(multimap, originalMap);
                    } catch (Exception ignored) {}
                }
                if (putMethod != null) {
                    putMethod.invoke(multimap, "textures", prop);
                }
                return mapCtorWithMultimap.newInstance(multimap);
            }
        }
    }

    private static ReflectionCache getOrCreateCache(ClassLoader cl) throws Exception {
        ReflectionCache current = cachedReflect;
        if (current != null && current.loader == cl) {
            return current;
        }
        ReflectionCache fresh = new ReflectionCache(cl);
        cachedReflect = fresh;
        return fresh;
    }

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

            // 7. Instantiate Property and PropertyMap via cached reflection handles
            ReflectionCache cache = getOrCreateCache(cl);
            Object prop = cache.createProperty("textures", b64, "gr8skin_signature");

            if (loggedPlayers.add(name)) {
                System.out.println("[Gr8SkinBridge] Activated skin profile for player: " + name);
            }

            return cache.createPropertyMap(originalMap, prop);

        } catch (Throwable t) {
            System.err.println("[Gr8SkinBridge] Error filling properties: " + t.getMessage());
            t.printStackTrace();
            return originalMap;
        }
    }
}
