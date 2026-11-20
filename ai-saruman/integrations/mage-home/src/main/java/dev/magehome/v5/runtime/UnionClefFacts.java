package dev.magehome.v5.runtime;

import dev.magehome.client.UnionClefBridge;
import net.fabricmc.loader.api.FabricLoader;

/**
 * The facts of the shared client dependency UnionClef.
 *
 * <p>UnionClef is a required dependency of the client. The module reads the facts of its
 * readiness and never removes it, never unloads it and never disables it globally.
 */
public final class UnionClefFacts {

    /** The facts of the dependency. */
    public record Facts(boolean loaded, boolean taskChain, boolean slotHandler, String version,
                        String currentChain, boolean userChainHeld, String currentTask) {
    }

    private static final UnionClefBridge BRIDGE = new UnionClefBridge();

    private UnionClefFacts() {
    }

    /** Reads the facts of the dependency right now. */
    public static Facts read() {
        boolean loaded = BRIDGE.available();
        boolean chain = loaded && BRIDGE.chainAvailable();
        boolean slots = loaded && BRIDGE.slotHandlerAvailable();
        String currentChain = null;
        boolean userChain = false;
        String currentTask = null;
        if (chain) {
            try {
                var mod = BRIDGE.mod();
                var runner = mod.getTaskRunner();
                var active = runner.getCurrentTaskChain();
                currentChain = active == null ? null : active.getName();
                userChain = active != null && active == mod.getUserTaskChain();
                var task = BRIDGE.currentTask();
                currentTask = task == null ? null : task.getClass().getName();
            } catch (Throwable t) {
                // An unreadable chain is reported as unknown; the builder then does not act.
            }
        }
        return new Facts(loaded, chain, slots, version(), currentChain, userChain, currentTask);
    }

    /** The version of the installed dependency; null when it is not installed. */
    public static String version() {
        try {
            var container = FabricLoader.getInstance().getModContainer("altoclef");
            if (container.isEmpty()) {
                return null;
            }
            return container.get().getMetadata().getVersion().getFriendlyString();
        } catch (Throwable t) {
            return null;
        }
    }
}
