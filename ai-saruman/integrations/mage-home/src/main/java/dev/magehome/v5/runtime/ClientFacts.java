package dev.magehome.v5.runtime;

import dev.magehome.core.registry.WorldKey;
import net.minecraft.client.MinecraftClient;
import net.minecraft.server.integrated.IntegratedServer;
import net.minecraft.util.WorldSavePath;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/**
 * The facts of the client that the public contract needs.
 *
 * <p>Every value is read from the live client. A fact that cannot be read is reported as absent
 * instead of being guessed: a run whose context names another world has to be rejected before a
 * mutation, and a guessed world would break that rule.
 */
public final class ClientFacts {

    private ClientFacts() {
    }

    private static IntegratedServer cachedServer;
    private static String cachedLocalId;
    private static String cachedSaveName;

    /** The world identity is scoped to the dimension and the current server or local save. */
    public static String worldUid() {
        var client = MinecraftClient.getInstance();
        String dimension = dimension();
        if (client == null || client.player == null || dimension == null) return null;
        IntegratedServer integrated = client.getServer();
        if (integrated != cachedServer) {
            cachedServer = integrated;
            cachedLocalId = null;
            cachedSaveName = null;
            if (integrated != null) {
                try {
                    Path save = integrated.getSavePath(WorldSavePath.ROOT).toRealPath();
                    BasicFileAttributes attributes = Files.readAttributes(save, BasicFileAttributes.class);
                    cachedLocalId = localIdentity(save, attributes);
                    cachedSaveName = save.getFileName().toString();
                } catch (IOException | SecurityException missing) {
                    // A local world with an unreadable identity cannot authorize a mutation.
                }
            }
        }
        String owner = integrated == null ? serverAddress() : cachedLocalId;
        return owner == null ? null : owner + "|" + dimension;
    }

    static String localIdentity(Path save, BasicFileAttributes attributes) {
        String identity = WorldKey.forSingleplayer(save.toString()) + "|" + attributes.creationTime();
        try {
            return "local:" + HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(identity.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    public static String localSaveName() {
        return cachedSaveName;
    }

    /** The address of the server of the client; null in a single player world. */
    public static String serverAddress() {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client == null) {
            return null;
        }
        try {
            var entry = client.getCurrentServerEntry();
            if (entry == null || entry.address == null || entry.address.isBlank()) {
                return null;
            }
            return entry.address;
        } catch (Throwable t) {
            return null;
        }
    }

    /** The dimension of the world of the client; null when there is no world. */
    public static String dimension() {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client == null || client.world == null) {
            return null;
        }
        try {
            var key = client.world.getRegistryKey();
            return key == null ? null : key.getValue().toString();
        } catch (Throwable t) {
            return null;
        }
    }

    /** The name of the player of the client; null when there is no player. */
    public static String playerName() {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client == null || client.player == null) {
            return null;
        }
        try {
            return client.player.getName().getString();
        } catch (Throwable t) {
            return null;
        }
    }

    /** True when the client is in a world and may act. */
    public static boolean inWorld() {
        MinecraftClient client = MinecraftClient.getInstance();
        return client != null && client.world != null && client.player != null;
    }

    /** True when the player exists in the world of the client. */
    public static boolean playerPresent() {
        MinecraftClient client = MinecraftClient.getInstance();
        return client != null && client.player != null;
    }

    /**
     * The name of the screen that is open in the client, or null when no screen is open.
     *
     * <p>The name is a diagnostic fact of the interface state. The module pauses while a screen
     * is open, so a controller has to be able to see why the work did not start. The method only
     * reads the state; it never shows a screen.
     */
    public static String currentScreenName() {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client == null || client.currentScreen == null) {
            return null;
        }
        return client.currentScreen.getClass().getSimpleName();
    }
}
