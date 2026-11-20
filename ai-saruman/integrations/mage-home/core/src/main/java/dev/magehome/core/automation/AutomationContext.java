package dev.magehome.core.automation;

import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.placement.PlacementTarget;
import dev.magehome.core.placement.PlacementSession;
import dev.magehome.core.registry.HouseRecord;
import dev.magehome.core.registry.HouseRegistry;
import dev.magehome.core.sites.WorldView;
import dev.magehome.core.supply.GiveAvailability;
import dev.magehome.core.template.CellSpec;

/**
 * Everything the automation needs from the game. The client module implements this port; the
 * domain core stays free of Minecraft types.
 */
public interface AutomationContext {

    boolean inWorld();

    boolean playerAlive();

    Vec3i playerPosition();

    String worldKey();

    String dimension();

    String playerUuid();

    /** UnionClef is paused: no movement, no give, no building. */
    boolean unionClefPaused();

    /** Another owner controls UnionClef. */
    boolean foreignTaskActive();

    /**
     * The chain of tasks of UnionClef exists.
     *
     * <p>The chain is created in {@code AltoClef.onInitializeLoad()} of the delivered UnionClef.
     * While it does not exist, the add-on starts no task of its own and performs no action.
     */
    default boolean unionClefChainAvailable() {
        return true;
    }

    long nowMillis();

    WorldView world();

    /** Number of items of the given id in the inventory. */
    int inventoryCount(String itemId);

    /**
     * The number of items of the given id that the add-on has spent itself.
     *
     * <p>The count holds the blocks of the project and the temporary supports of the climb: both
     * are placed by the add-on and both leave the inventory of the player. The core adds this
     * count to the confirmation of a delivery, so that the material that the add-on spends in the
     * same time does not hide the delivery (ТЗ.md section 4).
     *
     * @param itemId the id of the item
     * @return the number of items of the given id that the add-on has placed
     */
    default int consumedItemCount(String itemId) {
        return 0;
    }

    /** False when the inventory has no free slot left. */
    boolean inventoryHasSpace();

    /** True when the server command tree contains {@code give}. */
    boolean giveCommandAvailable();

    /**
     * What the client knows about the command {@code give} of the server.
     *
     * <p>A command tree that was not read yet is unknown, not absent: the difference keeps the
     * automation from suspending the house with a wrong reason.
     */
    default GiveAvailability giveCommandState() {
        return giveCommandAvailable() ? GiveAvailability.AVAILABLE : GiveAvailability.ABSENT;
    }

    /** Sends a command without a leading slash. */
    void sendCommand(String command);

    /** Asks the client to place the operation; the result is observed in the world. */
    boolean requestPlacement(CellSpec.Operation operation, Vec3i origin, PlacementTarget target);

    /** Passive observation; accepting a request is not a sent interaction. */
    PlacementSession.Snapshot placementProgress(CellSpec.Operation operation, Vec3i origin);

    /** Explicitly arm one retry after the previous actual send timed out. */
    boolean retryPlacement(CellSpec.Operation operation, Vec3i origin);

    /**
     * The world has the operation; the client stops the interaction and does not click again.
     */
    default void confirmPlacement(CellSpec.Operation operation) {
        // Without a client nothing has to be stopped.
    }

    /**
     * Asks the client to walk the player to the front point of the finished house. The record
     * becomes {@code ready} only when the player really stands there (C50), so the automation has
     * to leave the house itself. A client that cannot move the player simply waits.
     */
    default void requestFrontPoint(Vec3i front) {
        // Without a client the automation waits for the player.
    }

    /**
     * Removes one of the allowed plants (short grass, tall grass, fern, large fern) and reports
     * whether the removal was started. The automation checks the volume again afterwards.
     *
     * @return false when the plant cannot be removed; the candidate is skipped then
     */
    default boolean requestPlantRemoval(Vec3i position) {
        return false;
    }

    /**
     * Removes the wrong block of a cell of the volume of a registered house.
     *
     * <p>The client breaks the block with an ordinary attack of the player and reads the cell
     * again; the automation continues when the cell is empty (ТЗ.md section 4). The request is
     * the only way to make the client break a block: no other operation of the add-on breaks
     * anything.
     *
     * @param position the position of the block in the world
     * @return false when the client cannot start the operation
     */
    default boolean requestRepair(Vec3i position) {
        return false;
    }

    /**
     * True when the block at the position is a temporary support of the add-on.
     *
     * <p>The add-on builds a step for the climb when the player cannot step up to the cell of the
     * approach (ТЗ.md section 4, the temporary blocks of the 2026-11-13). The support stands in a
     * cell of the volume of the house, and the rule of the repair would take it away while the
     * climb is going on. The automation therefore asks the client before it removes a block, and
     * the client names the supports that it uses itself.
     *
     * @param position the position of the block in the world
     * @return true when the block is a temporary support of the add-on
     */
    default boolean isTemporarySupport(Vec3i position) {
        return false;
    }

    /**
     * Asks the client to hold one cell of the project open and to let the player down through it.
     *
     * <p>The closed roof of the house leaves the player on it, and the only way into the house is
     * the doorway, which stands below: the player would have to fall from the roof. The add-on
     * therefore keeps one cell of the roof open, the player walks down through it, and the cell is
     * built from inside afterwards — the block of the roof is placed on the side face of the block
     * next to it, and that face is within the ordinary range from the floor of the house
     * (ТЗ.md section 2 and the exit through the doorway, the owner of 2026-11-13).
     *
     * <p>The cell is held open only while the descent lasts; the automation builds it as soon as
     * the client reports that the player is inside.
     *
     * @param cell the cell of the project that is held open
     */
    default void requestDescent(Cell cell) {
        // Without a client the cell is not held open.
    }

    /**
     * True when the client holds a cell of the project open for the descent.
     *
     * @param cell the cell of the project
     * @return true when the cell is held open
     */
    default boolean descentHeld(Cell cell) {
        return false;
    }

    /**
     * The player has come down through the held cell; the house may build it.
     */
    default void descentFinished() {
        // Without a client nothing is released.
    }

    /** Request removal of owned supports; false means cleanup is still pending. */
    default boolean requestSupportCleanup() { return true; }

    /** A concrete cleanup failure, or null while cleanup has not failed. */
    default String supportCleanupFailure() { return null; }

    HouseRegistry loadRegistry() throws Exception;

    void saveRegistry(HouseRegistry registry) throws Exception;

    /** True when every requested write of the registry is finished. */
    default boolean registryWriteFinished() {
        return true;
    }

    /** True when the last write of the registry failed. */
    default boolean registryWriteFailed() {
        return false;
    }

    /** The cell the automation is currently working on, for the status snapshot. */
    Cell currentTargetCell();

    /**
     * Reports an observation of the automation to the log of the client.
     *
     * <p>The core has no logger of its own; the observations (the sent give command, the
     * confirmation of the delivery, the counter of the delay) are handed to the client, which
     * writes them into the log of the game.
     *
     * @param event  the name of the observation
     * @param detail the details of the observation
     */
    default void observe(String event, String detail) {
        // Without a client nothing is logged.
    }
}
