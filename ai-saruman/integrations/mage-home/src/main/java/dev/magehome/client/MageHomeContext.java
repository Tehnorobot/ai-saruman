package dev.magehome.client;

import dev.magehome.core.automation.AutomationContext;
import dev.magehome.core.automation.TaskOwnership;
import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.placement.PlacementTarget;
import dev.magehome.core.placement.PlacementSession;
import dev.magehome.core.registry.HouseRegistry;
import dev.magehome.core.registry.RealmConfig;
import dev.magehome.core.registry.StorageException;
import dev.magehome.core.registry.WorldKey;
import dev.magehome.core.sites.WorldView;
import dev.magehome.core.supply.GiveAvailability;
import dev.magehome.core.template.CellSpec;
import com.mojang.brigadier.CommandDispatcher;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayNetworkHandler;
import net.minecraft.client.network.ClientCommandSource;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.util.Identifier;
import net.minecraft.util.WorldSavePath;
import net.minecraft.util.math.BlockPos;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.function.BooleanSupplier;

/**
 * The bridge between the domain core and the game. Everything the core needs from Minecraft is
 * read through this class; the core itself stays free of game types.
 */
public final class MageHomeContext implements AutomationContext {

    private static final Logger LOGGER = LoggerFactory.getLogger("mage_home/context");

    /** The number of the observations that are kept for the checks of the scenario. */
    private static final int MAX_OBSERVATIONS = 400;

    private final RegistryService registryService;
    private final UnionClefBridge bridge = new UnionClefBridge();
    private final PlacementExecutor placement;
    private final ClientWorldView worldView;
    private final Path gameDirectory;
    private final adris.altoclef.tasksystem.Task rootTask;
    private final BooleanSupplier actionAllowed;
    private final MinecraftClient client = MinecraftClient.getInstance();

    private RealmConfig realmConfig = RealmConfig.defaultConfig();

    /** The last state of the command {@code give} that was written into the log. */
    private GiveAvailability lastGiveAvailability;
    /** The number of the client tick; it makes the observations of the log readable. */
    private long tick;
    /** The observations of the automation; the checks of the scenario read them. */
    private final List<String> observations = new ArrayList<>();

    public MageHomeContext(RegistryService registryService, PlacementExecutor placement, Path gameDirectory,
                            adris.altoclef.tasksystem.Task rootTask, BooleanSupplier actionAllowed) {
        this.registryService = registryService;
        this.placement = placement;
        this.gameDirectory = gameDirectory;
        this.rootTask = rootTask;
        this.actionAllowed = actionAllowed;
        this.worldView = new ClientWorldView(client);
    }

    /** Reads {@code config/mage_home/config.json}; the default realm id is used when it is missing. */
    public void loadConfig() {
        Path file = registryService.directory().resolve("config.json");
        try {
            if (Files.exists(file)) {
                String content = Files.readString(file);
                String realmId = extractRealmId(content);
                realmConfig = new RealmConfig(realmId);
            } else {
                realmConfig = RealmConfig.defaultConfig();
            }
        } catch (Exception e) {
            realmConfig = RealmConfig.defaultConfig();
        }
    }

    private static String extractRealmId(String json) {
        int index = json.indexOf("realm_id");
        if (index < 0) {
            return RealmConfig.DEFAULT_REALM_ID;
        }
        int colon = json.indexOf(':', index);
        int start = json.indexOf('"', colon + 1);
        int end = start < 0 ? -1 : json.indexOf('"', start + 1);
        if (start < 0 || end < 0) {
            return RealmConfig.DEFAULT_REALM_ID;
        }
        return json.substring(start + 1, end);
    }

    @Override
    public boolean inWorld() {
        return client.world != null && client.player != null;
    }

    @Override
    public boolean playerAlive() {
        return client.player != null && !client.player.isDead() && client.player.getHealth() > 0.0f;
    }

    @Override
    public Vec3i playerPosition() {
        BlockPos pos = client.player.getBlockPos();
        return new Vec3i(pos.getX(), pos.getY(), pos.getZ());
    }

    @Override
    public String worldKey() {
        if (client.getCurrentServerEntry() != null) {
            String address = client.getCurrentServerEntry().address;
            int colon = address.lastIndexOf(':');
            String host = colon > 0 ? address.substring(0, colon) : address;
            int port = colon > 0 ? Integer.parseInt(address.substring(colon + 1)) : 25565;
            return WorldKey.forServer(host, port, realmConfig.realmId());
        }
        if (client.getServer() != null) {
            // The display name may differ from the save folder (renamed worlds).
            Path save = client.getServer().getSavePath(WorldSavePath.ROOT).toAbsolutePath().normalize();
            return WorldKey.forSingleplayer(save.toString());
        }
        return WorldKey.forServer("unknown", 0, realmConfig.realmId());
    }

    @Override
    public String dimension() {
        if (client.world == null) {
            return "minecraft:overworld";
        }
        return client.world.getRegistryKey().getValue().toString();
    }

    @Override
    public String playerUuid() {
        return client.player == null ? "" : client.player.getUuidAsString();
    }

    @Override
    public boolean unionClefPaused() {
        return bridge.paused() || client.isPaused() || client.currentScreen != null;
    }

    /**
     * True when another owner controls the UnionClef task chain.
     *
     * <p>The facts come from the bytecode of the delivered UnionClef:
     * {@code SingleTaskChain.getCurrentTask()} returns the root task of the chain
     * ({@code mainTask}) and {@code Task.thisOrChildSatisfies} tests that task and its children
     * ({@code sub}). {@code AltoClef.runUserTask(task)} puts the own root task into
     * {@code mainTask}, so the own task and every task below it are never a foreign task.
     */
    @Override
    public boolean foreignTaskActive() {
        TaskOwnership.Relation relation = bridge.relationToOwnRoot(rootTask);
        if (TaskOwnership.foreign(relation)) {
            LOGGER.debug("the UnionClef task chain belongs to another owner: {}", bridge.currentTask());
        }
        return TaskOwnership.foreign(relation);
    }

    /**
     * The chain of tasks of UnionClef exists.
     *
     * <p>The chain is created in {@code AltoClef.onInitializeLoad()} of the delivered UnionClef;
     * while it does not exist, the add-on starts no task of its own and acts on nothing.
     */
    @Override
    public boolean unionClefChainAvailable() {
        return bridge.chainAvailable();
    }

    @Override
    public long nowMillis() {
        return System.currentTimeMillis();
    }

    @Override
    public WorldView world() {
        return worldView;
    }

    @Override
    public int inventoryCount(String itemId) {
        Item item = Registries.ITEM.get(Identifier.of(itemId));
        int viaUnionClef = bridge.inventoryCount(item);
        if (viaUnionClef > 0) {
            return viaUnionClef;
        }
        return countInInventory(item);
    }

    @Override
    public int consumedItemCount(String itemId) {
        return placement.consumedItemCount(itemId);
    }

    private int countInInventory(Item item) {
        if (client.player == null) {
            return 0;
        }
        int count = 0;
        PlayerInventory inventory = client.player.getInventory();
        for (ItemStack stack : inventory.getMainStacks()) {
            if (!stack.isEmpty() && stack.getItem() == item) {
                count += stack.getCount();
            }
        }
        return count;
    }

    @Override
    public boolean inventoryHasSpace() {
        if (client.player == null) {
            return false;
        }
        PlayerInventory inventory = client.player.getInventory();
        for (ItemStack stack : inventory.getMainStacks()) {
            if (stack.isEmpty() || stack.getCount() < stack.getMaxCount()) {
                return true;
            }
        }
        return false;
    }

    @Override
    public boolean giveCommandAvailable() {
        return giveCommandState() == GiveAvailability.AVAILABLE;
    }

    /**
     * What the client knows about the command {@code give} of the server.
     *
     * <p>The facts come from the bytecode of Minecraft 1.21.11 (Yarn):
     * {@code ClientPlayNetworkHandler.onCommandTree(CommandTreeS2CPacket)} replaces the field
     * {@code commandDispatcher} with a dispatcher that is built from the command tree of the
     * server, and the constructor of the handler fills the field with an empty dispatcher. The
     * root of the empty dispatcher has no child, while the root of the tree of a server always
     * has children. A tree that was not read yet is therefore unknown and not a missing command:
     * the automation may not suspend the house with the reason {@code materials_missing} because
     * of a tree that never arrived.
     */
    @Override
    public GiveAvailability giveCommandState() {
        ClientPlayNetworkHandler handler = client.getNetworkHandler();
        if (handler == null) {
            return remember(GiveAvailability.UNKNOWN, "there is no connection to the server");
        }
        CommandDispatcher<ClientCommandSource> dispatcher;
        try {
            dispatcher = handler.getCommandDispatcher();
        } catch (Throwable t) {
            return remember(GiveAvailability.UNKNOWN, "the command tree could not be read: " + t);
        }
        if (dispatcher == null || dispatcher.getRoot() == null) {
            return remember(GiveAvailability.UNKNOWN, "the command tree of the server is not there");
        }
        if (dispatcher.getRoot().getChildren().isEmpty()) {
            // The dispatcher of the constructor: the tree of the server was not read yet.
            return remember(GiveAvailability.UNKNOWN, "the command tree of the server was not read yet");
        }
        boolean give = dispatcher.getRoot().getChild("give") != null;
        return remember(give ? GiveAvailability.AVAILABLE : GiveAvailability.ABSENT,
                "the command tree of the server holds " + dispatcher.getRoot().getChildren().size()
                        + " commands, give is " + (give ? "there" : "not there"));
    }

    /**
     * Writes the state of the command into the log of the client when it changed.
     *
     * <p>The log tells which tree was read and what it holds, so that the game run shows whether
     * the command {@code give} was available at all. The number of the tick is written with it:
     * a tree that is read late has to be distinguishable from a tree that never arrives.
     */
    private GiveAvailability remember(GiveAvailability availability, String detail) {
        if (availability != lastGiveAvailability) {
            lastGiveAvailability = availability;
            LOGGER.info("LAB_OBSERVE give_command tick={} availability={} detail={}", tick, availability, detail);
        }
        return availability;
    }

    /**
     * Counts the client tick.
     *
     * <p>The number of the tick is written into the observations of the log, so that a wait can
     * be read against the facts of the game.
     */
    public void advanceTick() {
        tick++;
    }

    @Override
    public void sendCommand(String command) {
        if (client.player == null || client.getNetworkHandler() == null) {
            return;
        }
        if (!actionAllowed.getAsBoolean()) {
            // The gate of the background actions is closed: no material is asked (ТЗ.md section 3).
            LOGGER.debug("the command {} is not sent, the gate of the actions is closed", command);
            return;
        }
        client.getNetworkHandler().sendChatCommand(command);
    }

    @Override
    public boolean requestPlacement(CellSpec.Operation operation, Vec3i origin, PlacementTarget target) {
        return placement.request(operation, origin, target);
    }

    @Override
    public PlacementSession.Snapshot placementProgress(CellSpec.Operation operation, Vec3i origin) {
        return placement.progress(operation, origin);
    }

    @Override
    public boolean retryPlacement(CellSpec.Operation operation, Vec3i origin) {
        return placement.retry(operation, origin);
    }

    @Override
    public void confirmPlacement(CellSpec.Operation operation) {
        placement.confirm(operation);
    }

    @Override
    public void requestFrontPoint(Vec3i front) {
        placement.requestNavigationTo(front);
    }

    @Override
    public void requestDescent(Cell cell) {
        // The cell of the roof is held open and the player walks down through it (ТЗ.md section 2).
        placement.requestDescent(cell);
    }

    @Override
    public boolean descentHeld(Cell cell) {
        return placement.descentHeld(cell);
    }

    @Override
    public boolean requestPlantRemoval(Vec3i position) {
        return placement.requestPlantRemoval(position);
    }

    @Override
    public boolean requestRepair(Vec3i position) {
        // The safe repair of a wrong cell of the volume: the block is broken with an ordinary
        // attack of the player (ТЗ.md section 4).
        return placement.requestRepair(position);
    }

    @Override
    public boolean isTemporarySupport(Vec3i position) {
        // The steps of the climb stand in the volume of the house until the climb is over: the
        // rule of the repair waits for them (ТЗ.md section 4, the temporary blocks of 2026-11-13).
        for (BlockPos support : placement.scaffoldPositions()) {
            if (support.getX() == position.x() && support.getY() == position.y()
                    && support.getZ() == position.z()) {
                return true;
            }
        }
        return false;
    }

    @Override
    public boolean requestSupportCleanup() {
        return placement.requestSupportCleanup();
    }

    @Override
    public String supportCleanupFailure() {
        return placement.supportCleanupFailure();
    }

    @Override
    public HouseRegistry loadRegistry() throws StorageException {
        if (registryService.broken()) {
            throw new StorageException("the registry file is damaged");
        }
        return registryService.registry();
    }

    @Override
    public void saveRegistry(HouseRegistry registry) {
        registryService.save(registry);
    }

    @Override
    public boolean registryWriteFinished() {
        return !registryService.writePending();
    }

    @Override
    public boolean registryWriteFailed() {
        return registryService.writeFailed();
    }

    @Override
    public Cell currentTargetCell() {
        return null;
    }

    /**
     * Writes an observation of the automation into the log of the client.
     *
     * <p>The domain core has no logger of its own; the observations of the give command, of its
     * confirmation and of the delay of 1200 ticks arrive here and are visible in the log of the
     * game.
     *
     * <p>The observations are also kept, so that a check of the scenario can read the facts of
     * the delivery instead of the marker "the inventory is not empty". The list is bounded: the
     * oldest observation is dropped when the list is full.
     */
    @Override
    public void observe(String event, String detail) {
        observations.add(event + " " + detail);
        while (observations.size() > MAX_OBSERVATIONS) {
            observations.remove(0);
        }
        LOGGER.info("LAB_OBSERVE {} {}", event, detail);
    }

    /**
     * The observations of the automation of this run.
     *
     * @return the observations in the order of their appearance
     */
    public List<String> observations() {
        return List.copyOf(observations);
    }

}
