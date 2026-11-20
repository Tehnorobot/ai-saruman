package dev.magehome.client;

import adris.altoclef.AltoClef;
import adris.altoclef.tasksystem.Task;
import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.tree.CommandNode;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientCommandSource;
import net.minecraft.client.network.ClientPlayNetworkHandler;
import net.minecraft.util.math.BlockPos;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.lang.reflect.Field;
import java.util.Map;

/**
 * The preparation of the stand of an acceptance scenario.
 *
 * <p>The world of the stand is opened by the laboratory of the platform and not by the add-on:
 * for the dependency {@code altoclef} {@code 1.21.11-0.95.61} the adapter of the platform removes
 * the arguments of the Quick Play of Minecraft, lets Minecraft show its normal title screen and
 * lets the own mixin of UnionClef initialize the dependency there — the mixin
 * {@code adris.altoclef.mixins.EntryMixin} publishes {@code TitleScreenEntryEvent} in
 * {@code TitleScreen.init()}, the entry point of the mod subscribes a consumer to that event of
 * the own event bus of UnionClef, and the consumer calls {@code AltoClef.onInitializeLoad()},
 * which creates the task chain ({@code userTaskChain}), the slot handler ({@code slotHandler}),
 * the trackers and the behaviour of the mod. After the adapter has seen a real task chain and a
 * real slot handler, it opens the saved world. This is the one path of the world of the
 * laboratory; the preparation of the add-on does not open the world and does not ask the
 * dependency for its initialization.
 *
 * <p><strong>The preparation of the stand does not initialize the dependency itself.</strong> The
 * laboratory of the platform owns that step: its own adapter reads the metadata of the mods of
 * the real client launch, sees the dependency {@code altoclef} in the version
 * {@code 1.21.11-0.95.61}, removes the arguments of Quick Play, lets Minecraft show its normal
 * title screen and lets the own mixin of UnionClef initialize the dependency. The adapter
 * observes a real task chain and a real slot handler before it opens the saved world, and it
 * does not synthesize the events of the dependency and does not write the guard of its mixin.
 *
 * <p>The preparation of the stand therefore only <em>observes</em> the dependency: it names the
 * facts of the readiness in the log of the client (the mod is loaded, the task chain exists, the
 * slot handler is there, the chain was not replaced by a second initialization, the
 * initialization did not move the player) and it prepares the one fixture that belongs to the
 * laboratory alone: the command tree of the server without the command {@code give} for the
 * scenario of the refused delivery.
 *
 * <p>Nothing of UnionClef is replaced by a mock and no member of it is guessed: the classes, the
 * bus and the methods were read in the bytecode of the delivered jar.
 *
 * <p>The preparation belongs to the laboratory alone. It runs only when the system property
 * {@code lab.acceptance.scenario} names a scenario, and the product path of the add-on is not
 * touched by it.
 */
public final class LabStand {

    private static final Logger LOGGER = LoggerFactory.getLogger("mage_home/lab");

    /** The number of the ticks between two announcements of the waiting state. */
    private static final int ANNOUNCE_INTERVAL_TICKS = 100;

    private final UnionClefBridge bridge = new UnionClefBridge();

    /** True when the log already named the state of the chain of UnionClef. */
    private boolean chainAnnounced;
    /** True when the log already named the state of the mod of UnionClef. */
    private boolean modAnnounced;
    /** True when the preparation of the stand removed the command give from the tree. */
    private boolean giveRemoved;
    /** The tick on which the command give was removed from the tree; -1 means never. */
    private long giveRemovedTick = -1L;
    /** The identity of the chain of UnionClef that was seen the first time; 0 means never. */
    private int chainIdentity;
    /** True when the chain of UnionClef was replaced after it had been created. */
    private boolean chainReplaced;
    /** The tick on which the chain of UnionClef appeared; -1 means never. */
    private long dependencyReadyTick = -1L;
    /** The place of the player before the chain of UnionClef appeared; null when there was none. */
    private BlockPos playerBeforeDependencyReady;
    /** The place of the player after the chain of UnionClef appeared; null when there was none. */
    private BlockPos playerAfterDependencyReady;
    /** True when the tick after the chain of UnionClef appeared was already observed. */
    private boolean afterDependencyReadyRead;
    /** The task that the chain of UnionClef held right after it appeared; null when none. */
    private String chainTaskAfterDependencyReady;
    /** True when the chain of UnionClef was read right after it appeared. */
    private boolean chainKnownAfterDependencyReady;

    /**
     * Prepares the stand of the scenario; it is called in every client tick.
     *
     * @param scenarioId the id of the scenario of the laboratory
     * @param tick       the number of the client tick
     */
    public void prepare(String scenarioId, long tick) {
        if (scenarioId == null || scenarioId.isBlank()) {
            return;
        }
        observeUnionClef(scenarioId, tick);
        if ("give-refused".equals(scenarioId)) {
            removeGiveFromTheCommandTree(scenarioId, tick);
        }
        if (dependencyReadyTick >= 0L && !afterDependencyReadyRead) {
            // The tick after the chain of UnionClef appeared: the place of the player shows
            // whether the initialization of the dependency moved the player. When there was no
            // world at that moment, the initialization could not have changed the world either.
            afterDependencyReadyRead = true;
            playerAfterDependencyReady = playerPosition();
            // The task that the chain of UnionClef holds right after its appearance is a fact of
            // the environment: UnionClef starts a task of its own in the title screen only when
            // the player asks for it, and a task of its own would make the add-on wait for a busy
            // chain. The fact is observed and written into the log, so that a run that waits can
            // be read instead of guessed at.
            chainTaskAfterDependencyReady = chainTaskName();
            chainKnownAfterDependencyReady = true;
            LOGGER.info("LAB_ACCEPTANCE_STAND:{}: tick={} the player after the initialization of UnionClef is at {}, "
                            + "before the initialization it was at {}, the world was loaded at the initialization: {}, "
                            + "the chain of UnionClef holds the task {} right after the initialization",
                    scenarioId, tick, playerAfterDependencyReady, playerBeforeDependencyReady,
                    playerBeforeDependencyReady != null, chainTaskAfterDependencyReady);
        }
    }

    /**
     * The task that the chain of UnionClef holds; null when the chain holds no task.
     */
    private String chainTaskName() {
        try {
            Task current = bridge.currentTask();
            return current == null ? null : current.getClass().getName();
        } catch (Throwable t) {
            return null;
        }
    }

    /**
     * Observes the readiness of the dependency of the scenario.
     *
     * <p>The preparation of the stand asks nothing of UnionClef: the initialization of the
     * dependency belongs to the lifecycle of the dependency and is done by the laboratory of the
     * platform, which lets Minecraft show its title screen for the dependency
     * {@code altoclef} {@code 1.21.11-0.95.61}. The stand only reads the facts of the
     * readiness and writes them into the log, so that a run in which the dependency was not
     * initialized can be read instead of guessed at.
     */
    private void observeUnionClef(String scenarioId, long tick) {
        AltoClef mod = bridge.mod();
        if (mod == null) {
            if (!modAnnounced) {
                modAnnounced = true;
                LOGGER.info("LAB_ACCEPTANCE_STAND:{}: tick={} the mod of UnionClef is not loaded, "
                        + "the stand cannot show the situation of the scenario", scenarioId, tick);
            }
            return;
        }
        if (mod.getUserTaskChain() == null) {
            // The dependency was not initialized yet. The stand does not initialize it: the
            // lifecycle of the dependency is the lifecycle of the title screen of Minecraft, and
            // the laboratory of the platform opens the title screen for the dependency.
            if (!chainAnnounced && tick % ANNOUNCE_INTERVAL_TICKS == 0) {
                chainAnnounced = true;
                LOGGER.info("LAB_ACCEPTANCE_STAND:{}: tick={} the task chain of UnionClef does not exist yet; "
                        + "the stand waits for the initialization of the dependency and does not ask for it",
                        scenarioId, tick);
            }
            return;
        }
        int identity = System.identityHashCode(mod.getUserTaskChain());
        if (chainIdentity == 0) {
            chainIdentity = identity;
            dependencyReadyTick = tick;
            playerBeforeDependencyReady = playerPosition();
        } else if (chainIdentity != identity) {
            // The chain of UnionClef was replaced: the mod was initialized a second time.
            chainReplaced = true;
            LOGGER.warn("LAB_ACCEPTANCE_STAND:{}: tick={} the chain of UnionClef was replaced: the mod was "
                    + "initialized a second time", scenarioId, tick);
        }
        if (!chainAnnounced) {
            chainAnnounced = true;
            LOGGER.info("LAB_ACCEPTANCE_STAND:{}: tick={} the task chain of UnionClef exists, "
                    + "the slot handler is {}", scenarioId, tick,
                    mod.getSlotHandler() != null ? "there" : "not there");
        }
    }

    /** The place of the player; null when there is no world yet. */
    private BlockPos playerPosition() {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.player == null) {
            return null;
        }
        return client.player.getBlockPos();
    }

    /**
     * True when the mod of UnionClef was initialized a second time.
     *
     * <p>The fact is read from the identity of the chain of UnionClef: a second initialization
     * creates a new chain.
     */
    public boolean chainReplaced() {
        return chainReplaced;
    }

    /**
     * The tick on which the chain of UnionClef appeared; -1 means never.
     *
     * <p>The chain is created in {@code AltoClef.onInitializeLoad()}; the tick is the moment at
     * which the dependency was initialized, not a moment at which the stand asked for it.
     */
    public long dependencyReadyTick() {
        return dependencyReadyTick;
    }

    /** The place of the player before the chain of UnionClef appeared; null when there was none. */
    public BlockPos playerBeforeDependencyReady() {
        return playerBeforeDependencyReady;
    }

    /** The place of the player after the chain of UnionClef appeared; null when there was none. */
    public BlockPos playerAfterDependencyReady() {
        return playerAfterDependencyReady;
    }

    /**
     * The task that the chain of UnionClef held right after it appeared.
     *
     * <p>The fact is a part of the environment of the scenario: a task of UnionClef's own would
     * make the add-on wait for a busy chain, and the scenario would not show the product. Null
     * means that the chain held no task at all; the reading did not happen when the chain never
     * appeared.
     */
    public String chainTaskAfterDependencyReady() {
        return chainTaskAfterDependencyReady;
    }

    /** True when the chain of UnionClef was read right after it appeared. */
    public boolean chainKnownAfterDependencyReady() {
        return chainKnownAfterDependencyReady;
    }

    /**
     * True when the initialization of UnionClef moved the player.
     *
     * <p>The fact can only be read when the world was loaded at the moment of the
     * initialization; without a world there was nothing that the initialization could move.
     */
    public boolean playerMovedByTheInitialization() {
        if (playerBeforeDependencyReady == null || playerAfterDependencyReady == null) {
            return false;
        }
        return !playerBeforeDependencyReady.equals(playerAfterDependencyReady);
    }

    /**
     * Removes the command {@code give} from the tree of the server that the client received.
     *
     * <p>The scenario of the refused delivery needs a tree that was read and that holds no
     * {@code give}: a tree that was not read yet stays unknown and is not a tree without the
     * command. The single player server always offers {@code give}, so the preparation of the
     * stand removes that node from the tree of the client and writes into the log what it did.
     *
     * <p>The node is removed from the children of the root of the tree, which is the map that
     * {@code CommandNode.getChild(String)} reads, and from the literals of the root, which is the
     * map that the parser of Brigadier reads. Both maps are private; the second one is
     * reached by reflection, and a failure of it is written into the log instead of being
     * hidden.
     */
    private void removeGiveFromTheCommandTree(String scenarioId, long tick) {
        ClientPlayNetworkHandler handler = MinecraftClient.getInstance().getNetworkHandler();
        if (handler == null) {
            return;
        }
        CommandDispatcher<ClientCommandSource> dispatcher;
        try {
            dispatcher = handler.getCommandDispatcher();
        } catch (Throwable t) {
            return;
        }
        if (dispatcher == null || dispatcher.getRoot() == null) {
            return;
        }
        CommandNode<ClientCommandSource> root = dispatcher.getRoot();
        if (root.getChildren().isEmpty()) {
            // The tree of the server was not read yet: it is unknown and not a tree without the
            // command give.
            return;
        }
        CommandNode<ClientCommandSource> give = root.getChild("give");
        if (give == null) {
            return;
        }
        boolean removedFromChildren = root.getChildren().remove(give);
        boolean removedFromLiterals = removeFromLiterals(root, "give");
        giveRemoved = true;
        giveRemovedTick = tick;
        LOGGER.info("LAB_ACCEPTANCE_FIXTURE:{}: tick={} the command give was removed from the tree of the "
                + "server by the preparation of the stand: children={}, literals={}, the tree holds {} commands",
                scenarioId, tick, removedFromChildren, removedFromLiterals, root.getChildren().size());
    }

    /**
     * True when the preparation of the stand removed the command give from the tree of the server.
     *
     * <p>The fact belongs to the preparation of the stand and not to the product: the scenario of
     * the refused delivery needs a tree that was read and that holds no give, and the single
     * player server always offers the command. The setup check of the scenario names the fact, so
     * that the observation of the product cannot be read as a fact about the server.
     */
    public boolean giveRemoved() {
        return giveRemoved;
    }

    /** The tick on which the command give was removed from the tree of the server. */
    public long giveRemovedTick() {
        return giveRemovedTick;
    }

    /**
     * Removes a literal of the root of the tree of the server.
     *
     * @return true when the literal was removed
     */
    @SuppressWarnings("unchecked")
    private boolean removeFromLiterals(CommandNode<ClientCommandSource> root, String name) {
        try {
            Field field = CommandNode.class.getDeclaredField("literals");
            field.setAccessible(true);
            Map<String, ?> literals = (Map<String, ?>) field.get(root);
            return literals.remove(name) != null;
        } catch (Throwable t) {
            LOGGER.warn("the literal {} could not be removed from the tree of the server: {}", name, t);
            return false;
        }
    }
}
