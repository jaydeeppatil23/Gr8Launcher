package gr8launcher.skinbridge;

import java.io.File;
import java.lang.instrument.Instrumentation;
import java.util.jar.JarFile;

public class SkinBridgeAgent {
    public static void premain(String agentArgs, Instrumentation inst) {
        try {
            System.out.println("[Gr8SkinBridge] Initializing skin bridge companion agent...");

            // 1. Append our JAR to the Bootstrap ClassLoader so ALL game classloaders
            // (including Fabric's KnotClassLoader, Forge LaunchClassLoader, etc.)
            // can load SkinBridgeHook and helper classes seamlessly.
            try {
                File agentJar = new File(
                    SkinBridgeAgent.class.getProtectionDomain().getCodeSource().getLocation().toURI()
                );
                inst.appendToBootstrapClassLoaderSearch(new JarFile(agentJar));
                System.out.println("[Gr8SkinBridge] Appended bridge agent to bootstrap classloader search path.");
            } catch (Throwable t) {
                System.err.println("[Gr8SkinBridge] Warning: could not append to bootstrap classloader: " + t);
            }

            if (agentArgs != null && !agentArgs.trim().isEmpty()) {
                SkinBridgeHook.parseArgs(agentArgs.trim());
            }

            try {
                Class<?> clazz = Class.forName("moe.yushi.authlibinjector.transform.support.SkinWhitelistTransformUnit");
                java.lang.reflect.Method m = clazz.getMethod("getWhitelistedDomains");
                @SuppressWarnings("unchecked")
                java.util.List<String> list = (java.util.List<String>) m.invoke(null);
                list.add("127.0.0.1");
                list.add("localhost");
            } catch (Throwable ignored) {}

            inst.addTransformer(new SkinBridgeTransformer(), false);
            System.out.println("[Gr8SkinBridge] Skin bridge transformer registered successfully.");
        } catch (Throwable t) {
            System.err.println("[Gr8SkinBridge] Warning: initialization error: " + t.getMessage());
        }
    }
}
