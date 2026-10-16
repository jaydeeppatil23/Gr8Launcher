package gr8launcher.skinbridge;

import java.lang.instrument.ClassFileTransformer;
import java.security.ProtectionDomain;
import gr8launcher.skinbridge.asm.ClassReader;
import gr8launcher.skinbridge.asm.ClassVisitor;
import gr8launcher.skinbridge.asm.ClassWriter;
import gr8launcher.skinbridge.asm.MethodVisitor;
import gr8launcher.skinbridge.asm.Opcodes;

public class SkinBridgeTransformer implements ClassFileTransformer {

    @Override
    public byte[] transform(ClassLoader loader, String className, Class<?> classBeingRedefined,
                            ProtectionDomain protectionDomain, byte[] classfileBuffer) {
        if (className == null) {
            return null;
        }

        try {
            if ("com/mojang/authlib/GameProfile".equals(className)) {
                return transformGameProfile(classfileBuffer);
            }
            if ("com/mojang/authlib/properties/Property".equals(className)) {
                return transformProperty(classfileBuffer);
            }
            if ("com/mojang/authlib/yggdrasil/TextureUrlChecker".equals(className)) {
                return transformTextureUrlChecker(classfileBuffer);
            }
            if ("com/mojang/authlib/services/MinecraftServicesDiscoveryService".equals(className)) {
                return transformDiscoveryService(classfileBuffer);
            }
            if ("com/mojang/authlib/services/MinecraftServicesSessionService".equals(className)) {
                return transformServicesSessionService(classfileBuffer);
            }
            if ("com/mojang/authlib/minecraft/MinecraftProfileTextures".equals(className)) {
                return transformProfileTextures(classfileBuffer);
            }
            if ("net/minecraft/world/entity/player/PlayerSkin".equals(className)) {
                return transformPlayerSkin(classfileBuffer);
            }
            if ("com/mojang/authlib/yggdrasil/YggdrasilMinecraftSessionService".equals(className)) {
                return transformLegacySessionService(classfileBuffer);
            }
            if ("moe/yushi/authlibinjector/transform/support/SkinWhitelistTransformUnit".equals(className)) {
                return transformAuthlibInjectorWhitelist(classfileBuffer);
            }
        } catch (Throwable t) {
            System.err.println("[Gr8SkinBridge] Error transforming " + className + ": " + t.getMessage());
        }

        return null;
    }

    private byte[] transformGameProfile(byte[] classfileBuffer) {
        ClassReader cr = new ClassReader(classfileBuffer);
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        ClassVisitor cv = new ClassVisitor(Opcodes.ASM9, cw) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor, String signature, String[] exceptions) {
                MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                // Hook getProperties() (Authlib < 10) and properties() (Authlib 10+ Record)
                if (("getProperties".equals(name) || "properties".equals(name)) && descriptor.endsWith("PropertyMap;")) {
                    return new MethodVisitor(Opcodes.ASM9, mv) {
                        @Override
                        public void visitInsn(int opcode) {
                            if (opcode == Opcodes.ARETURN) {
                                // Stack has: [PropertyMap]
                                super.visitVarInsn(Opcodes.ALOAD, 0); // push [GameProfile]
                                super.visitMethodInsn(Opcodes.INVOKESTATIC,
                                        "gr8launcher/skinbridge/SkinBridgeHook",
                                        "getOrFillProperties",
                                        "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                                        false);
                                super.visitTypeInsn(Opcodes.CHECKCAST, "com/mojang/authlib/properties/PropertyMap");
                            }
                            super.visitInsn(opcode);
                        }
                    };
                }
                return mv;
            }
        };
        cr.accept(cv, 0);
        System.out.println("[Gr8SkinBridge] Successfully transformed GameProfile properties hook.");
        return cw.toByteArray();
    }

    private byte[] transformProperty(byte[] classfileBuffer) {
        ClassReader cr = new ClassReader(classfileBuffer);
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        ClassVisitor cv = new ClassVisitor(Opcodes.ASM9, cw) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor, String signature, String[] exceptions) {
                if ("isSignatureValid".equals(name) && "(Ljava/security/PublicKey;)Z".equals(descriptor)) {
                    MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                    mv.visitCode();
                    mv.visitInsn(Opcodes.ICONST_1);
                    mv.visitInsn(Opcodes.IRETURN);
                    mv.visitMaxs(1, 2);
                    mv.visitEnd();
                    return null;
                }
                if ("hasSignature".equals(name) && "()Z".equals(descriptor)) {
                    MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                    mv.visitCode();
                    mv.visitInsn(Opcodes.ICONST_1);
                    mv.visitInsn(Opcodes.IRETURN);
                    mv.visitMaxs(1, 1);
                    mv.visitEnd();
                    return null;
                }
                return super.visitMethod(access, name, descriptor, signature, exceptions);
            }
        };
        cr.accept(cv, 0);
        System.out.println("[Gr8SkinBridge] Successfully transformed Property signature check.");
        return cw.toByteArray();
    }

    private byte[] transformTextureUrlChecker(byte[] classfileBuffer) {
        ClassReader cr = new ClassReader(classfileBuffer);
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        ClassVisitor cv = new ClassVisitor(Opcodes.ASM9, cw) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor, String signature, String[] exceptions) {
                if ("isAllowedTextureDomain".equals(name) && "(Ljava/lang/String;)Z".equals(descriptor)) {
                    MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                    mv.visitCode();
                    mv.visitInsn(Opcodes.ICONST_1);
                    mv.visitInsn(Opcodes.IRETURN);
                    mv.visitMaxs(1, 1);
                    mv.visitEnd();
                    return null;
                }
                return super.visitMethod(access, name, descriptor, signature, exceptions);
            }
        };
        cr.accept(cv, 0);
        System.out.println("[Gr8SkinBridge] Successfully transformed TextureUrlChecker domain whitelist.");
        return cw.toByteArray();
    }

    private byte[] transformDiscoveryService(byte[] classfileBuffer) {
        ClassReader cr = new ClassReader(classfileBuffer);
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        ClassVisitor cv = new ClassVisitor(Opcodes.ASM9, cw) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor, String signature, String[] exceptions) {
                if ("isAllowedTextureDomain".equals(name) && "(Ljava/lang/String;)Z".equals(descriptor)) {
                    MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                    mv.visitCode();
                    mv.visitInsn(Opcodes.ICONST_1);
                    mv.visitInsn(Opcodes.IRETURN);
                    mv.visitMaxs(1, 2);
                    mv.visitEnd();
                    return null;
                }
                return super.visitMethod(access, name, descriptor, signature, exceptions);
            }
        };
        cr.accept(cv, 0);
        System.out.println("[Gr8SkinBridge] Successfully transformed MinecraftServicesDiscoveryService domain whitelist.");
        return cw.toByteArray();
    }

    private byte[] transformServicesSessionService(byte[] classfileBuffer) {
        ClassReader cr = new ClassReader(classfileBuffer);
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        ClassVisitor cv = new ClassVisitor(Opcodes.ASM9, cw) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor, String signature, String[] exceptions) {
                if ("getPropertySignatureState".equals(name) && descriptor.endsWith("Lcom/mojang/authlib/SignatureState;")) {
                    MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                    mv.visitCode();
                    mv.visitFieldInsn(Opcodes.GETSTATIC, "com/mojang/authlib/SignatureState", "SIGNED", "Lcom/mojang/authlib/SignatureState;");
                    mv.visitInsn(Opcodes.ARETURN);
                    mv.visitMaxs(1, 2);
                    mv.visitEnd();
                    return null;
                }
                return super.visitMethod(access, name, descriptor, signature, exceptions);
            }
        };
        cr.accept(cv, 0);
        System.out.println("[Gr8SkinBridge] Successfully transformed MinecraftServicesSessionService signature bypass.");
        return cw.toByteArray();
    }

    private byte[] transformProfileTextures(byte[] classfileBuffer) {
        ClassReader cr = new ClassReader(classfileBuffer);
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        ClassVisitor cv = new ClassVisitor(Opcodes.ASM9, cw) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor, String signature, String[] exceptions) {
                if ("signatureState".equals(name) && "()Lcom/mojang/authlib/SignatureState;".equals(descriptor)) {
                    MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                    mv.visitCode();
                    mv.visitFieldInsn(Opcodes.GETSTATIC, "com/mojang/authlib/SignatureState", "SIGNED", "Lcom/mojang/authlib/SignatureState;");
                    mv.visitInsn(Opcodes.ARETURN);
                    mv.visitMaxs(1, 1);
                    mv.visitEnd();
                    return null;
                }
                return super.visitMethod(access, name, descriptor, signature, exceptions);
            }
        };
        cr.accept(cv, 0);
        System.out.println("[Gr8SkinBridge] Successfully transformed MinecraftProfileTextures signature bypass.");
        return cw.toByteArray();
    }

    private byte[] transformPlayerSkin(byte[] classfileBuffer) {
        ClassReader cr = new ClassReader(classfileBuffer);
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        ClassVisitor cv = new ClassVisitor(Opcodes.ASM9, cw) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor, String signature, String[] exceptions) {
                if ("secure".equals(name) && "()Z".equals(descriptor)) {
                    MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                    mv.visitCode();
                    mv.visitInsn(Opcodes.ICONST_1);
                    mv.visitInsn(Opcodes.IRETURN);
                    mv.visitMaxs(1, 1);
                    mv.visitEnd();
                    return null;
                }
                return super.visitMethod(access, name, descriptor, signature, exceptions);
            }
        };
        cr.accept(cv, 0);
        System.out.println("[Gr8SkinBridge] Successfully transformed PlayerSkin secure check.");
        return cw.toByteArray();
    }

    private byte[] transformLegacySessionService(byte[] classfileBuffer) {
        ClassReader cr = new ClassReader(classfileBuffer);
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        ClassVisitor cv = new ClassVisitor(Opcodes.ASM9, cw) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor, String signature, String[] exceptions) {
                if ("isWhitelistedDomain".equals(name) && "(Ljava/lang/String;)Z".equals(descriptor)) {
                    MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                    mv.visitCode();
                    mv.visitInsn(Opcodes.ICONST_1);
                    mv.visitInsn(Opcodes.IRETURN);
                    mv.visitMaxs(1, 1);
                    mv.visitEnd();
                    return null;
                }
                if ("getPropertySignatureState".equals(name) && descriptor.endsWith("Lcom/mojang/authlib/SignatureState;")) {
                    MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                    mv.visitCode();
                    mv.visitFieldInsn(Opcodes.GETSTATIC, "com/mojang/authlib/SignatureState", "SIGNED", "Lcom/mojang/authlib/SignatureState;");
                    mv.visitInsn(Opcodes.ARETURN);
                    mv.visitMaxs(1, 2);
                    mv.visitEnd();
                    return null;
                }
                if ("getTextures".equals(name) && descriptor.startsWith("(Lcom/mojang/authlib/GameProfile;Z)")) {
                    MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                    return new MethodVisitor(Opcodes.ASM9, mv) {
                        @Override
                        public void visitCode() {
                            super.visitCode();
                            super.visitInsn(Opcodes.ICONST_0);
                            super.visitVarInsn(Opcodes.ISTORE, 2);
                        }
                    };
                }
                return super.visitMethod(access, name, descriptor, signature, exceptions);
            }
        };
        cr.accept(cv, 0);
        System.out.println("[Gr8SkinBridge] Successfully transformed YggdrasilMinecraftSessionService.");
        return cw.toByteArray();
    }

    private byte[] transformAuthlibInjectorWhitelist(byte[] classfileBuffer) {
        ClassReader cr = new ClassReader(classfileBuffer);
        ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
        ClassVisitor cv = new ClassVisitor(Opcodes.ASM9, cw) {
            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor, String signature, String[] exceptions) {
                if ("isWhitelistedDomain".equals(name) && "(Ljava/lang/String;)Z".equals(descriptor)) {
                    MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
                    mv.visitCode();
                    mv.visitInsn(Opcodes.ICONST_1);
                    mv.visitInsn(Opcodes.IRETURN);
                    mv.visitMaxs(1, 1);
                    mv.visitEnd();
                    return null;
                }
                return super.visitMethod(access, name, descriptor, signature, exceptions);
            }
        };
        cr.accept(cv, 0);
        System.out.println("[Gr8SkinBridge] Successfully transformed Authlib-Injector whitelist.");
        return cw.toByteArray();
    }
}
