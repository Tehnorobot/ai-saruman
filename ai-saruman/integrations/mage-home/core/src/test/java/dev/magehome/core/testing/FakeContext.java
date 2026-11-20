package dev.magehome.core.testing;

import dev.magehome.core.automation.AutomationContext;
import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.placement.PlacementTarget;
import dev.magehome.core.placement.PlacementSession;
import dev.magehome.core.registry.HouseRegistry;
import dev.magehome.core.sites.WorldView;
import dev.magehome.core.supply.GiveAvailability;
import dev.magehome.core.template.CellSpec;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * A context that answers the questions of the automation without a game. It records the commands
 * and the placement requests so that the checks can look at them.
 */
public final class FakeContext implements AutomationContext {

    public FakeWorld world = new FakeWorld();
    public Vec3i player = new Vec3i(0, 64, 0);
    public boolean alive = true;
    public boolean inWorld = true;
    public boolean paused = false;
    public boolean foreign = false;
    /** False while the chain of tasks of UnionClef does not exist yet. */
    public boolean chainAvailable = true;
    /** What the client knows about the command {@code give}; null means the boolean is used. */
    public GiveAvailability giveState;
    public long now = 1_000_000L;
    public final Map<String, Integer> inventory = new HashMap<>();
    /** The items that the add-on spent itself: the blocks of the project and the supports. */
    public final Map<String, Integer> consumed = new HashMap<>();
    public boolean inventorySpace = true;
    public boolean giveAvailable = true;
    public final List<String> sentCommands = new ArrayList<>();
    public final List<String> placementRequests = new ArrayList<>();
    /** The operations that the automation asked for. */
    public final List<CellSpec.Operation> placementOperations = new ArrayList<>();
    public HouseRegistry registry = new HouseRegistry();
    public boolean failSave = false;
    public boolean failLoad = false;
    public String worldKey = "server|localhost:25565|default";
    public String dimension = "minecraft:overworld";
    public String playerUuid = "player-uuid";
    public int saves;
    /** The plant removals that the automation asked for. */
    public final List<Vec3i> plantRemovals = new ArrayList<>();
    /** False when the client cannot remove a plant. */
    public boolean plantRemovalPossible = true;
    /** The repairs that the automation asked for. */
    public final List<Vec3i> repairRequests = new ArrayList<>();
    /** False when the client cannot remove the wrong block. */
    public boolean repairPossible = true;
    /** False when the wrong block stays in the world, as it would when the player cannot reach it. */
    public boolean repairRemovesBlock = true;
    /** The front points that the automation asked for. */
    public final List<Vec3i> frontPointRequests = new ArrayList<>();
    /** The operations that the world confirmed. */
    public final List<String> confirmedPlacements = new ArrayList<>();
    /** The observations that the automation wrote into the log of the client. */
    public final List<String> observations = new ArrayList<>();
    /** False while the write of the registry is still running. */
    public boolean writeFinished = true;
    /** True when the last write of the registry failed. */
    public boolean writeFailed = false;
    /** False when the client finds no admissible way to place the operation. */
    public boolean placementPossible = true;
    /** Existing tests simulate immediate interactions; timing tests control sends separately. */
    public boolean immediatePlacementSend = true;
    public final PlacementSession placementSession = new PlacementSession();
    public int placementRetryRequests;

    @Override
    public boolean inWorld() {
        return inWorld;
    }

    @Override
    public boolean playerAlive() {
        return alive;
    }

    @Override
    public Vec3i playerPosition() {
        return player;
    }

    @Override
    public String worldKey() {
        return worldKey;
    }

    @Override
    public String dimension() {
        return dimension;
    }

    @Override
    public String playerUuid() {
        return playerUuid;
    }

    @Override
    public boolean unionClefPaused() {
        return paused;
    }

    @Override
    public boolean foreignTaskActive() {
        return foreign;
    }

    @Override
    public boolean unionClefChainAvailable() {
        return chainAvailable;
    }

    @Override
    public long nowMillis() {
        return now;
    }

    @Override
    public WorldView world() {
        return world;
    }

    @Override
    public int inventoryCount(String itemId) {
        return inventory.getOrDefault(itemId, 0);
    }

    @Override
    public int consumedItemCount(String itemId) {
        return consumed.getOrDefault(itemId, 0);
    }

    /** The add-on placed an item of the given id itself; the item left the inventory. */
    public void consume(String itemId, int count) {
        consumed.merge(itemId, count, Integer::sum);
    }

    @Override
    public boolean inventoryHasSpace() {
        return inventorySpace;
    }

    @Override
    public boolean giveCommandAvailable() {
        return giveAvailable;
    }

    @Override
    public GiveAvailability giveCommandState() {
        if (giveState != null) {
            return giveState;
        }
        return giveAvailable ? GiveAvailability.AVAILABLE : GiveAvailability.ABSENT;
    }

    @Override
    public void sendCommand(String command) {
        sentCommands.add(command);
    }

    @Override
    public boolean requestPlacement(CellSpec.Operation operation, Vec3i origin, PlacementTarget target) {
        if (!placementPossible) {
            // The client found no admissible way to place the operation with the blocks that are
            // there (the player cannot reach the cell, for example).
            return false;
        }
        if (!placementSession.begin(operation, origin, target.cell())) return true;
        if (immediatePlacementSend) placementSession.sent();
        placementRequests.add(operation.itemId() + "@" + operation.cells().get(0));
        placementOperations.add(operation);
        return true;
    }

    @Override
    public PlacementSession.Snapshot placementProgress(CellSpec.Operation operation, Vec3i origin) {
        return placementSession.progress(operation, origin);
    }

    @Override
    public boolean retryPlacement(CellSpec.Operation operation, Vec3i origin) {
        if (!placementSession.armRetry()) return false;
        placementRetryRequests++;
        if (immediatePlacementSend) placementSession.sent();
        placementRequests.add(operation.itemId() + "@" + operation.cells().get(0));
        return true;
    }

    @Override
    public void confirmPlacement(CellSpec.Operation operation) {
        confirmedPlacements.add(operation.itemId() + "@" + operation.cells().get(0));
    }

    @Override
    public void requestFrontPoint(Vec3i front) {
        frontPointRequests.add(front);
    }

    @Override
    public boolean requestPlantRemoval(Vec3i position) {
        if (!plantRemovalPossible) {
            return false;
        }
        plantRemovals.add(position);
        // The plant is gone in the next tick, as the game would do it.
        world.air(position);
        return true;
    }

    @Override
    public boolean requestRepair(Vec3i position) {
        if (!repairPossible) {
            return false;
        }
        repairRequests.add(position);
        if (repairRemovesBlock) {
            // The block is gone in the next tick, as the game would do it after the ordinary
            // attack of the player.
            world.air(position);
        }
        return true;
    }

    @Override
    public boolean registryWriteFinished() {
        return writeFinished;
    }

    @Override
    public boolean registryWriteFailed() {
        return writeFailed;
    }

    @Override
    public HouseRegistry loadRegistry() throws Exception {
        if (failLoad) {
            throw new IllegalStateException("registry is broken");
        }
        return registry;
    }

    @Override
    public void saveRegistry(HouseRegistry registry) throws Exception {
        if (failSave) {
            throw new IllegalStateException("cannot write the registry");
        }
        this.registry = registry;
        saves++;
    }

    @Override
    public Cell currentTargetCell() {
        return null;
    }

    @Override
    public void observe(String event, String detail) {
        observations.add(event + "|" + detail);
    }

    /** Advances the clock by the given milliseconds. */
    public void advance(long millis) {
        now += millis;
    }
}
