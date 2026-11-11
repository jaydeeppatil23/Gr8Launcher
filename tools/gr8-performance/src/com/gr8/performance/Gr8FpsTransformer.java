package com.gr8.performance;

import java.lang.instrument.ClassFileTransformer;
import java.security.ProtectionDomain;
import java.util.Locale;

import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.tree.AbstractInsnNode;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.FieldInsnNode;
import org.objectweb.asm.tree.InsnList;
import org.objectweb.asm.tree.IntInsnNode;
import org.objectweb.asm.tree.LdcInsnNode;
import org.objectweb.asm.tree.MethodInsnNode;
import org.objectweb.asm.tree.MethodNode;
import org.objectweb.asm.tree.TypeInsnNode;

/**
 * Rewrites only the F3 FPS line so it always reads {@code 1000 fps}.
 *
 * <p>The transformer looks for the {@code String.format("%d fps ...", ...)}
 * call in the debug HUD, then replaces the single value that feeds the first
 * {@code %d} with the constant 1000. It does not touch the real frame pacing,
 * so the game keeps rendering as fast as the hardware allows.</p>
 *
 * <p>This avoids version-specific mappings: the pattern is found from the
 * bytecode itself and works on obfuscated and unobfuscated Minecraft builds
 * that format the FPS line with {@code String.format}.</p>
 */
public final class Gr8FpsTransformer implements ClassFileTransformer {
    private static final String TRANSFORMER_PACKAGE = "com/gr8/performance/";

    @Override
    public byte[] transform(
            ClassLoader loader,
            String className,
            Class<?> classBeingRedefined,
            ProtectionDomain protectionDomain,
            byte[] classfileBuffer) {
        if (className == null || className.startsWith(TRANSFORMER_PACKAGE)) {
            return null;
        }
        if (!containsFps(classfileBuffer)) {
            return null;
        }
        try {
            ClassNode node = new ClassNode();
            new ClassReader(classfileBuffer).accept(node, 0);
            boolean changed = false;
            for (MethodNode method : node.methods) {
                if (patchMethod(method)) {
                    changed = true;
                }
            }
            if (!changed) {
                return null;
            }
            ClassWriter writer = new ClassWriter(0);
            node.accept(writer);
            return writer.toByteArray();
        } catch (Throwable ignored) {
            // Never break class loading because a pattern did not match.
            return null;
        }
    }

    private static boolean containsFps(byte[] bytes) {
        for (int index = 0; index + 2 < bytes.length; index++) {
            if (bytes[index] == 'f' && bytes[index + 1] == 'p' && bytes[index + 2] == 's') {
                return true;
            }
        }
        return false;
    }

    private static boolean patchMethod(MethodNode method) {
        InsnList instructions = method.instructions;
        int fpsString = findFpsString(instructions);
        if (fpsString < 0) {
            return false;
        }

        int array = -1;
        for (int index = fpsString + 1; index < instructions.size(); index++) {
            AbstractInsnNode node = instructions.get(index);
            if (node instanceof TypeInsnNode && node.getOpcode() == Opcodes.ANEWARRAY) {
                TypeInsnNode type = (TypeInsnNode) node;
                if ("java/lang/Object".equals(type.desc)) {
                    array = index;
                    break;
                }
            }
        }
        if (array < 0) {
            return false;
        }

        for (int index = array + 1; index + 1 < instructions.size(); index++) {
            if (instructions.get(index).getOpcode() != Opcodes.DUP
                    || instructions.get(index + 1).getOpcode() != Opcodes.ICONST_0) {
                continue;
            }
            int valueStart = index + 2;
            int valueEnd = findIntegerValueOf(instructions, valueStart);
            if (valueEnd > valueStart && patchValueRange(instructions, valueStart, valueEnd)) {
                return true;
            }
        }
        return false;
    }

    private static int findFpsString(InsnList instructions) {
        for (int index = 0; index < instructions.size(); index++) {
            AbstractInsnNode node = instructions.get(index);
            if (!(node instanceof LdcInsnNode)) {
                continue;
            }
            Object constant = ((LdcInsnNode) node).cst;
            if (!(constant instanceof String)) {
                continue;
            }
            String text = ((String) constant).toLowerCase(Locale.ROOT);
            if (text.contains("%d") && text.contains("fps")) {
                return index;
            }
        }
        return -1;
    }

    private static int findIntegerValueOf(InsnList instructions, int start) {
        int limit = Math.min(instructions.size(), start + 8);
        for (int index = start; index < limit; index++) {
            AbstractInsnNode node = instructions.get(index);
            if (node.getOpcode() == Opcodes.AASTORE) {
                return -1;
            }
            if (node instanceof MethodInsnNode) {
                MethodInsnNode call = (MethodInsnNode) node;
                if (call.getOpcode() == Opcodes.INVOKESTATIC
                        && "java/lang/Integer".equals(call.owner)
                        && "valueOf".equals(call.name)
                        && "(I)Ljava/lang/Integer;".equals(call.desc)) {
                    return index;
                }
            }
        }
        return -1;
    }

    private static boolean patchValueRange(InsnList instructions, int start, int end) {
        int count = end - start;
        if (count <= 0 || count > 4) {
            return false;
        }

        AbstractInsnNode last = instructions.get(end - 1);
        if (last instanceof MethodInsnNode) {
            MethodInsnNode call = (MethodInsnNode) last;
            if (!call.desc.endsWith(")I")) {
                return false;
            }
            if (call.getOpcode() != Opcodes.INVOKESTATIC
                    && call.getOpcode() != Opcodes.INVOKEVIRTUAL
                    && call.getOpcode() != Opcodes.INVOKEINTERFACE
                    && call.getOpcode() != Opcodes.INVOKESPECIAL) {
                return false;
            }
        } else if (last instanceof FieldInsnNode) {
            FieldInsnNode field = (FieldInsnNode) last;
            if (!"I".equals(field.desc)
                    || (field.getOpcode() != Opcodes.GETSTATIC
                    && field.getOpcode() != Opcodes.GETFIELD)) {
                return false;
            }
        } else {
            return false;
        }

        for (int index = start; index < end; index++) {
            int opcode = instructions.get(index).getOpcode();
            if (opcode == Opcodes.GOTO || opcode == Opcodes.ATHROW
                    || (opcode >= Opcodes.IFEQ && opcode <= Opcodes.IF_ACMPNE)
                    || (opcode >= Opcodes.IRETURN && opcode <= Opcodes.RETURN)) {
                return false;
            }
        }

        AbstractInsnNode first = instructions.get(start);
        instructions.set(first, new IntInsnNode(Opcodes.SIPUSH, 1000));
        for (int index = end - 1; index > start; index--) {
            instructions.remove(instructions.get(index));
        }
        return true;
    }
}
