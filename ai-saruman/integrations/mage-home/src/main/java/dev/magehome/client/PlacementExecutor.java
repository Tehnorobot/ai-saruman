package dev.magehome.client;

import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.placement.ArrivalPolicy;
import dev.magehome.core.placement.AimMatch;
import dev.magehome.core.placement.AimPolicy;
import dev.magehome.core.placement.InteractionPolicy;
import dev.magehome.core.placement.ItemSelection;
import dev.magehome.core.placement.PlacementAttempt;
import dev.magehome.core.placement.PlacementDiagnostics;
import dev.magehome.core.placement.PlacementFacts;
import dev.magehome.core.placement.PlacementPreconditions;
import dev.magehome.core.placement.PlacementTarget;

import java.util.function.Predicate;
import dev.magehome.core.placement.PlacementSession;
import dev.magehome.core.placement.ReachPolicy;
import dev.magehome.core.placement.Support;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.template.HouseTemplate;
import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.block.DoorBlock;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerInteractionManager;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.BlockItem;
import net.minecraft.item.Item;
import net.minecraft.item.ItemPlacementContext;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.Slot;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.util.Hand;
import net.minecraft.util.ActionResult;
import net.minecraft.util.Identifier;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.HashMap;
import java.util.function.BooleanSupplier;

/**
 * The placement of one operation of the project.
 *
 * <p>The block is placed with the ordinary client interaction: the player walks to a free side of
 * the cell, the item is selected in the hotbar, the view is turned to the face of the support
 * block and the interaction manager performs the placement. Nothing is written into the world
 * directly, no temporary block is used and no wrong block is broken.
 *
 * <p>The only exception is the safe repair of a wrong cell of the volume of a registered house
 * (ТЗ.md section 4): the wrong block is broken with an ordinary attack of the player, the cell
 * is read again and only then the building continues. The repair is a chosen operation: it is
 * asked for by the domain core for one cell at a time and it never touches another block.
 *
 * <p>The support, the face and the place of the player come from the planner of the domain core.
 * Before the click the client asks the real vanilla placement API which state the block would get
 * and compares it with the project (ТЗ.md section 7). A block is only placed when the state, the
 * position and the reach are correct; otherwise the operation is not performed at all.
 */
public final class PlacementExecutor {

    private static final Logger LOGGER = LoggerFactory.getLogger("mage_home/placement");

    /** The reach of the player; the server rejects a click that is further away. */
    private static final double REACH = ReachPolicy.ORDINARY_REACH;
    /**
     * The reserve of the reach.
     *
     * <p>The player does not stand exactly in the middle of the cell of the approach, and the
     * server measures the distance from the real eyes of the player: a cell that is exactly at the
     * limit of the ordinary range is refused. A cell within this reserve of the limit is tried
     * after the cells that are clearly inside the range (2026-11-14).
     */
    private static final double REACH_MARGIN = 0.6;
    /**
     * The number of the ticks that the point of the click may stay out of the ordinary range.
     *
     * <p>The player stands still in that time, and the dependency stops a task that stands still
     * for ten seconds: the cell of the approach is left before that moment comes (2026-11-14).
     */
    private static final int REACH_WAIT_TICKS = 40;
    /** The number of the ticks that the point of the click was out of the ordinary range. */
    private int reachMissTicks = 0;
    /** The hit position has to stay close to the centre of the clicked block. */
    private static final double HIT_LIMIT = 2.0;
    /** The height of the eyes above the feet. */
    private static final double EYE_HEIGHT = 1.62;
    /**
     * The number of the ticks between two publications of the facts of the attempt.
     *
     * <p>The budget of the wait of the view is not kept here: it lives in the domain core
     * ({@link ArrivalPolicy#AIM_WAIT_TICKS} and {@link ArrivalPolicy#mayLeaveAccessOnAim}), and the
     * client asks that rule instead of keeping a second copy of it.
     */
    private static final int FACTS_INTERVAL_TICKS = 20;

    private final NavigationRequest navigation;
    private final BooleanSupplier actionAllowed;
    private final MinecraftClient client = MinecraftClient.getInstance();

    private CellSpec.Operation operation;
    private Vec3i origin;
    private PlacementTarget target;
    private Solution solution;
    private Item item;
    private int attempts;
    /**
     * The view was already turned to the support of the cell.
     *
     * <p>The click waits for the next tick: the direction of the view reaches the server with
     * the movement packet, and the block is only placed when the server knows where the player
     * looks at (ТЗ.md section 4).
     */
    private boolean aimSent;
    /** The number of ticks since the view was turned to the support. */
    private int aimTicks;
    /** The verdict of the comparison of the view with the support. */
    private String aimVerdict = "none";
    /** The first precondition of the ordinary placement that did not hold. */
    private String preconditionStep = "none";
    /** The interaction of this attempt was already sent; it is not repeated every tick. */
    private boolean clickSent;
    /** The world has confirmed the operation; no further interaction is sent. */
    private boolean confirmed;
    /**
     * The number of the cell of the approach that is tried.
     *
     * <p>The cells of the approach are tried one after another: when the player does not stand in
     * the current one and nothing was sent for it, the next one is used. When no cell is left,
     * the house is suspended with a concrete reason (ТЗ.md section 4).
     */
    private int solutionSlot;
    /** The number of ticks the player needed for the current cell of the approach. */
    private int arrivalTicks;
    /** The number of interactions that were sent for the current cell of the approach. */
    private int clicksAtAccess;
    /** Every cell of the approach was tried and none of them was reached. */
    private boolean accessExhausted;
    /** What the call of the last interaction returned. */
    private String lastActionResult = "none";
    /** The plant that has to be removed before the volume is checked again. */
    private BlockPos removalTarget;
    private boolean removalSent;

    /**
     * The greatest horizontal distance of one leg of the walk of the player.
     *
     * <p>The pathfinder of the dependency searches a wide area when the goal is far away or has to
     * be reached the long way round, and the player walks away from the house while the search
     * runs. The walk is therefore broken into short legs along the house (2026-11-13).
     */
    private static final int MAX_WALK_LEG = 2;

    /**
     * The number of temporary blocks under a cell of the approach that the add-on may build.
     *
     * <p>The wall of the third level and the ring of the roof stand two levels above the floor of
     * the house: the player stands on a column of two temporary blocks and clicks the upper face of
     * the wall from there. The player stays inside the house, and no fall of three or four blocks
     * can happen (2026-11-13).
     */
    private static final int MAX_TOWER = 2;

    /** The cell of the roof that the add-on holds open for the descent of the player. */
    private BlockPos descentHold;
    /**
     * The wrong block of a cell of the volume that is removed with an ordinary attack.
     *
     * <p>The block is broken only when the domain core has asked for the repair of this cell
     * (ТЗ.md section 4); no other operation of the add-on breaks a block.
     */
    private BlockPos repairTarget;
    /** The cell of the approach from which the wrong block is attacked. */
    private BlockPos repairAccess;
    /** The number of the ticks since the view was turned to the wrong block. */
    private int repairAimTicks;
    /** The number of the attacks that were sent for the wrong block. */
    private int repairAttacks;
    /** True when the ordinary destruction of the wrong block was started. */
    private boolean repairBreakStarted;
    /** The progress of the destruction of the wrong block that the game reported last. */
    private int repairProgress = -1;
    /** The number of ticks in which the destruction of the wrong block runs. */
    private int repairBreakTicks = 0;
    /** The number of the ticks the player needed to reach the current cell of the approach. */
    private int repairWaitTicks = 0;
    /** The number of the cells of the approach of the repair that were already tried. */
    private int repairAccessRound = 0;
    /** The number of the points of the view that are tried for the wrong block. */
    private int repairAimRound = 0;
    /**
     * The number of the ticks the player may need for one cell of the approach of the repair.
     *
     * <p>The wrong block can lie in the room of the house while the player stands outside of it,
     * and the way around the house takes longer than the budget of one cell. The player walks
     * during that time, so the dependency does not stop the task (ТЗ.md section 4).
     */
    private static final int REPAIR_ACCESS_TICKS = 180;
    /** The cell of the temporary support that the add-on places for the climb. */
    private BlockPos scaffoldCell = null;
    /** The block that carries the temporary support. */
    private BlockPos scaffoldSupport = null;
    /** The point of the click that places the temporary support. */
    private Vec3d scaffoldHit = null;
    /** True when the click of the temporary support was sent. */
    private boolean scaffoldSent = false;
    private boolean scaffoldClicked = false;
    private int scaffoldWaitTicks = 0;
    private boolean ownedAttackKey = false;
    private boolean ownedForwardKey = false;
    /** The facts of the temporary supports; the checks of the scenario read them. */
    private final List<String> scaffoldEvents = new ArrayList<>();
    /** The core tells the add-on which cells of the world the project occupies. */
    private final Predicate<Vec3i> projectCell;
    /** The temporary supports that stand in the world and that the climb still uses. */
    private final List<BlockPos> scaffolds = new ArrayList<>();

    /**
     * The cells that the add-on has reserved for its own temporary supports.
     *
     * <p>A reserved cell belongs to the add-on even when the block is not confirmed yet: the rule
     * of the repair waits for it, and the player may stand on it in the next tick (ТЗ.md section 4).
     */
    private final List<BlockPos> reservedSupports = new ArrayList<>();
    /**
     * The blocks of the dependency that serve as a temporary support.
     *
     * <p>The add-on does not break them, but the rule of the repair must not take them away while
     * the player climbs on them, so the cells stand under the protection of the client
     * (ТЗ.md section 4).
     */
    private final List<BlockPos> foreignSupports = new ArrayList<>();
    /** True while the material of the operation is on its way from the server. */
    private boolean itemPending = false;
    /** Number of the refusals of the game because of the body of the player. */
    private int collisionRetries = 0;
    /** The position of the player when the body reached into the cell of the project last time. */
    private double lastCollisionX = Double.NaN;
    private double lastCollisionZ = Double.NaN;
    /** Ticks the player may stand on the temporary support while it is to be removed. */
    private int scaffoldStandTicks = 0;
    /**
     * The face of the block that the temporary support is placed against.
     *
     * <p>A column grows from the floor of the house (the face UP). The block under a cell of the
     * roof is placed against the side of a column of the room when the line to the upper face of
     * the block below is blocked by another column (ТЗ.md section 4).
     */
    private Direction scaffoldFace = Direction.UP;
    /**
     * Ticks the building of a temporary support may wait for the view of the game.
     *
     * <p>The crosshair of the game has to name the upper face of the block under the support. When
     * the line is blocked by another column of the room, the add-on waits a short moment and tries
     * another cell of the approach instead of standing still for a long time (2026-11-14).
     */
    private static final int SCAFFOLD_WAIT_TICKS = 20;
    /** Ticks the walk to the temporary support of the removal may take. */
    private int scaffoldAccessWaitTicks = 0;
    /** Number of the ticks after which the support stays where it is. */
    private static final int SCAFFOLD_STAND_TICKS = 20;
    /**
     * Number of the ticks after which the walk to a temporary support is given up.
     *
     * <p>The navigation of the dependency does not reach every cell of the approach. The player
     * would stand still for the ten seconds in which the dependency stops the task, and the house
     * would be suspended with the reason of the interruption (2026-11-14).
     */
    private static final int SCAFFOLD_ACCESS_TICKS = 40;
    /** Number of the refusals after which the cell of the approach is left. */
    private static final int COLLISION_RETRIES = 2;
    /** The temporary supports that the add-on removes itself when the climb over them is over. */
    private final List<BlockPos> scaffoldsToRemove = new ArrayList<>();
    /** Only confirmed block states are eligible for automatic support removal. */
    private final Map<BlockPos, BlockState> scaffoldStates = new HashMap<>();
    /**
     * The items that the add-on placed itself, counted by the id of the item.
     *
     * <p>The count holds the blocks of the project and the temporary supports of the climb: both
     * leave the inventory of the player. The core adds this count to the confirmation of a
     * delivery, so that the spending of the add-on does not hide the delivery that arrives in
     * the same time (ТЗ.md section 4).
     */
    private final Map<String, Integer> consumedItems = new HashMap<>();
    /** The id of the item that the temporary support of the current climb was built from. */
    private String scaffoldItemId;
    private Block expectedScaffoldBlock;
    private String scaffoldFailure;
    private boolean supportCleanupActive;
    private int previousFeetY = Integer.MIN_VALUE;
    private int scaffoldMissTicks;
    private int scaffoldAccessRound;
    private BlockPos scaffoldRemovalAccess;
    private BlockPos cleanupTarget;
    /** Supports belong to one live client world; never apply them to another dimension or session. */
    private net.minecraft.client.world.ClientWorld scaffoldWorld;
    /** The height that the player reached by the climb of the current cell of the approach. */
    private int climbHeight = Integer.MIN_VALUE;
    /**
     * The number of the ticks that the player may stand still before the cell is left.
     *
     * <p>The wait is shorter than the ten seconds in which the dependency stops a task that
     * stands still. The add-on leaves the cell of the approach and tries the next one before
     * that, so that the reason of the suspension is always named by the add-on and never by the
     * interruption of the dependency (ТЗ.md section 4).
     */
    private static final int STUCK_TICKS = 90;
    /**
     * The number of the ticks that the add-on waits for the material of the operation.
     *
     * <p>The wait is shorter than the ten seconds in which the dependency stops a task that
     * stands still: the add-on has to name the reason itself before that moment comes.
     */
    private static final int ITEM_WAIT_TICKS = 100;
    /** The number of the ticks that the add-on waited for the material of the operation. */
    private int itemWaitTicks = 0;
    /** The number of the ticks that the add-on still moves the player aside. */
    private int nudgeTicks = 0;
    /** The volume of the house that the dependency must not break. */
    private Vec3i avoidedOrigin = null;
    /** The operation of the previous request; a new operation starts a fresh search. */
    private CellSpec.Operation previousOperation = null;
    /** The place of the player in the last tick in which the player moved. */
    private Vec3d lastMovePosition = null;
    /** The number of the ticks that the player stands still. */
    private int idleTicks = 0;
    /**
     * The number of the ticks that the player may stand still before the add-on names the reason.
     *
     * <p>The dependency stops a task that stands still for ten seconds; the add-on names the
     * reason a moment before that, so that the house is suspended with a concrete reason and not
     * with an interruption (ТЗ.md section 4).
     */
    private static final int IDLE_TICKS = 120;
    /** The direction in which the add-on moves the player aside. */
    private Direction nudgeDirection = null;
    /**
     * The number of the ticks of the step aside.
     *
     * <p>The step is a fraction of a cell: the player has to stay in the cell of the approach and
     * only has to leave the volume of the cell of the project (ТЗ.md section 4).
     */
    private static final int NUDGE_TICKS = 3;
    /** The number of the ticks that the add-on tried to remove a temporary support. */
    private int scaffoldRemoveTicks = 0;
    /** The number of the points of the view that are tried for a temporary support. */
    private int scaffoldAimRound = 0;
    /**
     * The number of the ticks that the add-on tries to remove one temporary support.
     *
     * <p>After that the rule of the repair takes the support over: the house does not wait for
     * ever at one block (ТЗ.md section 4).
     */
    private static final int SCAFFOLD_REMOVE_TICKS = 60;
    /** The place of the player in the last tick of the approach. */
    private BlockPos lastPosition = null;
    /** The number of the ticks that the player stands still on the same place. */
    private int stillTicks = 0;

    /**
     * The temporary supports that the add-on uses for the climb.
     *
     * <p>The automation asks the client before it removes a block of the volume of the house: the
     * supports of the climb stand there until the climb is over (ТЗ.md section 4, the temporary
     * blocks of the 2026-11-13).
     *
     * @return the positions of the supports that stand in the world
     */
    public List<BlockPos> scaffoldPositions() {
        List<BlockPos> positions = new ArrayList<>(scaffolds);
        for (BlockPos reserved : reservedSupports) {
            if (!positions.contains(reserved)) {
                positions.add(reserved);
            }
        }
        for (BlockPos foreign : foreignSupports) {
            if (!positions.contains(foreign)) {
                positions.add(foreign);
            }
        }
        if (scaffoldCell != null && !positions.contains(scaffoldCell)) {
            positions.add(scaffoldCell);
        }
        return List.copyOf(positions);
    }
    /** The facts of the repair of the wrong block; the checks of the scenario read them. */
    private final List<String> repairEvents = new ArrayList<>();
    /** Number of interactions that were sent for the current operation. */
    private final PlacementSession session = new PlacementSession();
    /** Last operation's diagnostic count survives cancellation; never used to schedule sends. */
    private int operationInteractions;
    /** Number of interactions that were sent in total. */
    private int totalInteractions;
    /** Number of interactions that were sent for the exit door. */
    private int doorInteractions;
    /** The facts of the interactions that were sent; the acceptance check reads them. */
    private final List<Interaction> interactions = new ArrayList<>();
    /** The facts of the last attempt, as one line of text. */
    private String lastFacts = "none";
    /** The reasons why the last search for a way to place the operation found nothing. */
    private String solveRejection = null;
    /** The cells of the approach of the last search and the reasons of their rejection. */
    private List<String> solveRejections = List.of();
    /** The state that the game would place instead of the state of the project. */
    private String stateMismatch = null;
    /** The cells of the approach of the current operation, in the order in which they are tried. */
    private List<AccessChoice> accessChoices = List.of();
    /** The stage of the last publication of the facts. */
    private String lastStage = "";
    /** The tick of the last publication of the facts. */
    private int lastFactsTick;
    /** The number of the ticks of the placement; it limits the frequency of the facts. */
    private int tickCounter;

    /** The way out of the finished house. */
    private enum ExitPhase {
        /** Nothing to do. */
        NONE,
        /** The player walks to the cell in front of the exit door. */
        TO_DOOR,
        /** The exit door is opened with one ordinary interaction. */
        OPEN_DOOR,
        /** The player walks to the front point A+(3,0,-1). */
        TO_FRONT
    }

    private ExitPhase exitPhase = ExitPhase.NONE;
    /** The front point of the house. */
    private BlockPos exitFront;
    /** The lower half of the exit door. */
    private BlockPos exitDoor;
    /** The cell in front of the exit door inside the house. */
    private BlockPos exitAccess;
    /** The view was already turned to the exit door. */
    private boolean exitDoorAimed;
    /** Number of interactions that were sent for the exit door. */
    private int exitDoorSends;

    public PlacementExecutor(NavigationRequest navigation, BooleanSupplier actionAllowed,
                             Predicate<Vec3i> projectCell) {
        this.navigation = navigation;
        this.actionAllowed = actionAllowed;
        this.projectCell = projectCell;
    }

    /**
     * Plans the placement of an operation.
     *
     * @return false when there is no admissible way to place the operation; the automation then
     *         suspends with {@code no_placement_support}.
     */
    public boolean request(CellSpec.Operation operation, Vec3i origin, PlacementTarget target) {
        if (!session.begin(operation, origin, target.cell())) {
            // Repeated accepted requests are passive: keep navigation, aim and send history.
            return !session.progress(operation, origin).failed();
        }
        this.operation = operation;
        this.origin = origin;
        this.target = target;
        // A new operation starts from the first cell of the approach.
        this.solution = null;
        this.solutionSlot = 0;
        this.arrivalTicks = 0;
        this.clicksAtAccess = 0;
        this.accessExhausted = false;
        this.attempts = 0;
        this.aimSent = false;
        this.aimTicks = 0;
        this.aimVerdict = "none";
        this.preconditionStep = "none";
        this.clickSent = false;
        this.confirmed = false;
        this.operationInteractions = 0;
        // The climb of the new operation starts at the height that the player has now.
        this.climbHeight = client.player == null ? Integer.MIN_VALUE : client.player.getBlockPos().getY();

        // A new operation means that the house is built again: the way out is not prepared.
        this.exitPhase = ExitPhase.NONE;
        this.exitDoorAimed = false;
        this.exitDoorSends = 0;
        this.item = Registries.ITEM.get(Identifier.of(operation.itemId()));
        if (client.world == null || client.player == null) {
            session.update(PlacementSession.Stage.NO_SUPPORT, "no client world or player");
            return false;
        }
        // The cells of the approach are ordered once for this operation: the number of a cell is
        // its place in this list, and the list does not change while the operation runs.
        this.accessChoices = orderAccessChoices();
        if (operation == null || !operation.equals(previousOperation)) {
            // A new operation starts with a fresh search: an operation that waited for a later
            // try is tried from the beginning (ТЗ.md section 4).
            accessExhausted = false;
            solutionSlot = 0;
            solution = null;
        }
        previousOperation = operation;
        if (origin != null && !origin.equals(avoidedOrigin)) {
            // The dependency is told once for the house that it must not break the blocks of the
            // volume: a dug block is a wrong block, and the rule of the repair has to remove it
            // afterwards (ТЗ.md section 4, T-A12-03).
            avoidedOrigin = origin;
            navigation.avoidBreaking(origin, HouseTemplate.SIZE_X, HouseTemplate.SIZE_Y,
                    HouseTemplate.SIZE_Z);
        }
        if (solution == null) {
            this.solution = solve(solutionSlot);
        }
        if (solution == null) {
            LOGGER.warn("no admissible placement for {} at {} ({})", operation.itemId(), target.cell(),
                    solveRejection == null ? "no_cell_of_the_approach" : solveRejection);
            session.update(PlacementSession.Stage.NO_SUPPORT, "no admissible placement");
            return false;
        }
        this.solutionSlot = solution.slot();
        return true;
    }

    /**
     * Counts an item that the add-on spent from the inventory of the player.
     *
     * <p>The blocks of the project and the temporary supports of the climb leave the inventory.
     * The core adds this count to the confirmation of a delivery, so that the spending of the
     * add-on does not hide the delivery that arrives in the same time (ТЗ.md section 4).
     *
     * @param itemId the id of the item that the add-on placed
     */
    private void countConsumed(String itemId) {
        if (itemId == null) {
            return;
        }
        consumedItems.merge(itemId, 1, Integer::sum);
    }

    /**
     * The number of items of the given id that the add-on spent from the inventory.
     *
     * @param itemId the id of the item
     * @return the number of items that the add-on placed itself
     */
    public int consumedItemCount(String itemId) {
        return consumedItems.getOrDefault(itemId, 0);
    }

    /**
     * The world has the operation: the interaction stops and no further click is sent.
     *
     * @param operation the operation that the world confirms
     */
    public PlacementSession.Snapshot progress(CellSpec.Operation operation, Vec3i origin) {
        return session.progress(operation, origin);
    }

    public boolean retry(CellSpec.Operation operation, Vec3i origin) {
        if (this.operation == null || !this.operation.equals(operation) || !origin.equals(this.origin)
                || confirmed || !session.armRetry()) return false;
        aimSent = false;
        aimTicks = 0;
        clickSent = false;
        return true;
    }

    public void confirm(CellSpec.Operation operation) {
        if (this.operation == null || !this.operation.equals(operation)) {
            return;
        }
        // The world has the operation and the add-on sent the interaction for it: the item of the
        // operation left the inventory of the player. The count of the spending lets the core
        // tell the delivery of the server apart from the material that the add-on used itself
        // (ТЗ.md section 4).
        if (attempts > 0) {
            countConsumed(operation.itemId());
        }
        this.operation = null;
        this.target = null;
        this.solution = null;
        this.aimSent = false;
        this.aimTicks = 0;
        this.confirmed = true;
        session.update(PlacementSession.Stage.CONFIRMED, "all operation cells match the world");
        this.clickSent = true;
        navigation.clearNavigation();
        // The temporary column of the climb has done its work: the add-on takes it away right
        // away, so the columns do not pile up in the room of the house and do not block the cells
        // of the approach of the next operation (ТЗ.md section 4).
        queueSupportCleanup();
    }

    /**
     * Asks for the removal of one of the allowed plants.
     *
     * <p>Only short grass, tall grass, fern and large fern are removed; every other block stays
     * untouched. The plant is broken with an ordinary attack after the player walked to it.
     *
     * @return false when the block is not one of the allowed plants
     */
    public boolean requestPlantRemoval(Vec3i position) {
        if (client.world == null || client.player == null) {
            return false;
        }
        BlockPos pos = new BlockPos(position.x(), position.y(), position.z());
        String id = Registries.BLOCK.getId(client.world.getBlockState(pos).getBlock()).toString();
        if (!ALLOWED_PLANTS.contains(id)) {
            // Trees, crops and every other block are never touched.
            return false;
        }
        this.removalTarget = pos;
        this.removalSent = false;
        return true;
    }

    /** Number of interactions that were sent for the last operation. */
    public int lastOperationInteractions() {
        return operationInteractions;
    }

    /** Number of interactions that were sent in total. */
    public int totalInteractions() {
        return totalInteractions;
    }

    public boolean hasPendingWork() {
        return operation != null || removalTarget != null || repairTarget != null || exitPhase != ExitPhase.NONE
                || supportCleanupActive || ownedAttackKey || ownedForwardKey;
    }

    public void cancel() {
        // Cancelling Mage Home must not cancel a human player's unrelated block breaking.
        boolean ownedBreaking = repairBreakStarted || ownedAttackKey || (removalTarget != null && removalSent);
        releaseOwnedKeys();
        supportCleanupActive = false;
        cleanupTarget = null;
        scaffoldRemovalAccess = null;
        avoidedOrigin = null; // behaviour state is popped on stop and must be installed again
        scaffoldFailure = null;
        scaffoldRemoveTicks = 0;
        previousFeetY = Integer.MIN_VALUE;
        nudgeTicks = 0;
        nudgeDirection = null;
        session.clear();
        operation = null;
        target = null;
        solution = null;
        solutionSlot = 0;
        arrivalTicks = 0;
        clicksAtAccess = 0;
        accessExhausted = false;
        aimSent = false;
        aimTicks = 0;
        clickSent = false;
        confirmed = false;
        removalTarget = null;
        removalSent = false;
        repairTarget = null;
        repairAccess = null;
        repairAimTicks = 0;
        repairAttacks = 0;
        repairBreakStarted = false;
        repairProgress = -1;
        if (ownedBreaking && client.interactionManager != null) {
            // The destruction of the block is given up: the game is told that the player stopped
            // the attack, exactly as the ordinary client does when the button is released.
            client.interactionManager.cancelBlockBreaking();
        }
        exitPhase = ExitPhase.NONE;
        exitFront = null;
        exitDoor = null;
        exitAccess = null;
        exitDoorAimed = false;
        exitDoorSends = 0;
        navigation.clearNavigation();
    }

    /**
     * Asks the player to walk to the given position. The add-on uses it to leave the finished
     * house through the front point; the record becomes ready only when the player stands there.
     *
     * <p>The exit door is opened with one ordinary interaction before the walk: a closed door
     * would keep the player inside the house. ТЗ.md section 4 allows the exit door to be opened
     * and closed again; nothing else is touched.
     */
    public void requestNavigationTo(Vec3i target) {
        if (exitPhase != ExitPhase.NONE) {
            // The way out is already prepared.
            return;
        }
        BlockPos front = new BlockPos(target.x(), target.y(), target.z());
        exitFront = front;
        // The lower half of the door is A+(3,1,0) and the cell in front of it inside the house is
        // A+(3,1,1); both are derived from the front point A+(3,0,-1).
        exitDoor = front.up().south();
        exitAccess = front.up().south().south();
        // The door is often placed from outside. Going back through the closed door to the
        // interior approach would trap the navigator; head straight for the front point instead.
        exitPhase = client.player != null && client.player.getBlockPos().getZ() <= front.getZ()
                ? ExitPhase.TO_FRONT : ExitPhase.TO_DOOR;
    }

    /** Number of interactions that were sent for the exit door. */
    public int doorInteractions() {
        return doorInteractions;
    }

    /**
     * The facts of one interaction of the player with the world.
     *
     * <p>The record shows that the block came from an ordinary interaction of the player: the
     * player stood in the world, the item of the project was in his hand and the clicked block
     * was within his reach. The cell of the approach and the cell that is filled are part of the
     * record: the click has to leave the cell of the approach and must not leave the cell that
     * is to be filled.
     *
     * @param cell       the cell of the project that the interaction fills
     * @param player     the place of the player when the interaction was sent
     * @param support    the block that was clicked
     * @param itemId     the item that the project wants for the cell
     * @param heldItemId the item that the player held in his hand
     * @param reach      the distance between the eyes of the player and the clicked block
     * @param access     the cell of the approach the player had to stand in
     * @param target     the cell that the interaction fills, in the world
     * @param actionResult what the call of the interaction returned
     * @param aimVerdict   the verdict of the aim that the client published for this interaction
     * @param precondition the first precondition of the ordinary placement that did not hold
     */
    public record Interaction(Cell cell, BlockPos player, BlockPos support, String itemId, String heldItemId,
                              double reach, BlockPos access, BlockPos target, String actionResult,
                              String aimVerdict, String precondition) {
    }

    /** The facts of the interactions that were sent. */
    public List<Interaction> interactions() {
        return List.copyOf(interactions);
    }

    public CellPosInfo currentTarget() {
        if (operation == null) {
            return null;
        }
        Cell cell = target != null ? target.cell() : operation.cells().get(0);
        if (solution == null) {
            return new CellPosInfo(cell, null, null, null);
        }
        return new CellPosInfo(cell, solution.support, solution.face, solution.access);
    }

    /** Information about the placement that is currently in progress. */
    public record CellPosInfo(Cell cell, BlockPos support, Direction face, BlockPos access) {
    }

    /** One way to perform the placement: the block, the face and the place of the player. */
    private record Solution(BlockPos support, Direction face, Vec3d hit, BlockPos access, Float yaw, int slot) {
    }

    // --- the solution ----------------------------------------------------------------------------

    /**
     * Searches a way to place the operation.
     *
     * <p>Every attempt of the planner is tried: the support has to be solid, the player has to
     * stand on a free cell that reaches the clicked face and the vanilla placement has to produce
     * the state of the project.
     *
     * @param fromSlot the number of the first cell of the approach that may be used
     * @return the way to place the operation or null when there is none
     */
    private Solution solve(int fromSlot) {
        List<String> rejected = new ArrayList<>();
        solveRejections = rejected;
        for (int index = 0; index < accessChoices.size(); index++) {
            if (index < fromSlot) {
                // This cell of the approach was already tried and was not reachable.
                continue;
            }
            AccessChoice choice = accessChoices.get(index);
            PlacementAttempt attempt = choice.attempt();
            Cell candidate = choice.candidate();
            BlockPos support = world(attempt.supportCell());
            if (!solid(support)) {
                rejected.add(candidate + ":support_is_not_solid");
                continue;
            }
            Direction face = faceOf(attempt.support());
            Vec3d hit = hitPosition(support, face);
            BlockPos access = world(candidate);
            if (!supportable(access)) {
                // The cell of the approach has no floor of its own: the add-on may build the
                // temporary column under it, and the player climbs that column (ТЗ.md section 4).
                rejected.add(candidate + ":not_standable");
                continue;
            }
            if (!reaches(access, hit)) {
                rejected.add(candidate + ":out_of_reach");
                continue;
            }
            Float yaw = yawAtSupport(access, hit);
            if (yaw == null) {
                rejected.add(candidate + ":no_direction");
                continue;
            }
            if (!placementCellMatches(yaw, hit, face, support)) {
                // The block would stand in another cell than the cell of the project. The
                // properties of the state are not asked here: the state of a door follows the
                // place of the player, and the player is not yet in the cell of the approach
                // while the search runs. The properties are asked before the click, when the
                // player really stands in the cell (ТЗ.md section 4).
                rejected.add(candidate + ":other_cell");
                continue;
            }
            solveRejection = null;
            return new Solution(support, face, hit, access, yaw, index);
        }
        // The reasons of the last search are kept: a house that waits for ever needs a concrete
        // reason, not a guess (ТЗ.md section 4).
        solveRejection = rejected.isEmpty() ? "no_cell_of_the_approach" : String.join(",", rejected);
        return null;
    }

    /**
     * The cells of the approach, ordered by the height difference to the player.
     *
     * <p>The player climbs one block with a jump. A cell that is two levels higher cannot be
     * reached that way: the navigation of UnionClef builds a temporary block for it, and the
     * temporary blocks are forbidden (ТЗ.md section 4). The cells of the approach are therefore
     * tried from the closest height to the player first, so that the navigation only ever asks
     * for a step that the player can take.
     *
     * <p>The order is built once for the operation and does not change while it runs: the number
     * of the cell of the approach is the index in this list, and a cell that was already tried
     * keeps its place.
     *
     * @return the cells of the approach in the order in which they are tried
     */
    private List<AccessChoice> orderAccessChoices() {
        List<AccessChoice> choices = new ArrayList<>();
        int feet = client.player == null ? 0 : client.player.getBlockPos().getY();
        double playerX = client.player == null ? 0 : client.player.getX();
        double playerZ = client.player == null ? 0 : client.player.getZ();
        int order = 0;
        int attemptIndex = 0;
        // The cells of the approach that the add-on finds in the world itself: they stand next to
        // the cell of the project and on the top of a temporary column (ТЗ.md section 4).
        List<Cell> worldCandidates = worldAccessCandidates();
        for (PlacementAttempt attempt : target.attempts()) {
            int current = attemptIndex++;
            List<Cell> candidates = new ArrayList<>(attempt.accessCandidates());
            for (Cell candidate : worldCandidates) {
                if (!candidates.contains(candidate)) {
                    candidates.add(candidate);
                }
            }
            for (Cell candidate : candidates) {
                if (candidate.y() > HouseTemplate.SIZE_Y - 2) {
                    // The player never stands above the roof of the house: the roof is built from
                    // the room, and a walk over a roof that is not finished is a walk into the
                    // void. The navigation would dig the room for such a walk, and the dig of the
                    // dependency ends the task of the house (2026-11-14).
                    continue;
                }
                if (sealedAccess(candidate)) {
                    // The cell of the approach lies under a block of the project that already
                    // stands: the player would step under the roof of the house and could not come
                    // out of it again (ТЗ.md section 4).
                    continue;
                }
                int worldY = origin.y() + candidate.y();
                double worldX = origin.x() + candidate.x();
                double worldZ = origin.z() + candidate.z();
                // The face of the support has to be visible from the cell of the approach: the
                // eyes of the player have to be above the face, otherwise the crosshair of the
                // game names another block and the click never reaches the cell.
                double eyeY = worldY + EYE_HEIGHT;
                double faceY = origin.y() + attempt.supportCell().y() + faceHeight(attempt.support());
                boolean visible = eyeY >= faceY - 0.001;
                // The distance of the cell to the player: the cell that is near the player is
                // reached faster, and the walk of the player has to fit into the budget of the
                // approach (ТЗ.md section 4).
                double distance = Math.hypot(worldX - playerX, worldZ - playerZ);
                // The crest of the wall is one block wide and stands three or four blocks above
                // the ground: the player does not walk there, the room of the house is used
                // instead (ТЗ.md section 4).
                boolean onRing = candidate.x() == 0 || candidate.x() == HouseTemplate.SIZE_X - 1
                        || candidate.z() == 0 || candidate.z() == HouseTemplate.SIZE_Z - 1;
                // The distance from the eyes of the player in the cell of the approach to the
                // clicked face: a cell at the very edge of the ordinary range is the last one that
                // is tried. The player does not stand exactly in the middle of the cell, and the
                // server measures the distance from the real eyes: a cell that is exactly at the
                // limit of the range is refused by the server (2026-11-14).
                double reach = nominalReach(candidate, attempt);
                boolean borderline = reach > REACH - REACH_MARGIN;
                choices.add(new AccessChoice(attempt, candidate, current, order++,
                        Math.abs(worldY - feet), visible ? 0 : 1, distance, onRing ? 1 : 0,
                        borderline ? 1 : 0));
            }
        }
        // The order of the supports is kept: the support below the cell comes first, because a
        // block that is placed on the side of another block gets another orientation (a log that
        // stands on a block has the axis y, a log that is placed against a side face does not).
        // The order of the cells of the approach inside one support: the cell whose face the
        // player can see comes first, then the cell that is closest in height to the player and
        // then the cell that is closest in distance (ТЗ.md section 4).
        choices.sort(Comparator.comparingInt(AccessChoice::attemptIndex)
                .thenComparingInt(AccessChoice::hidden)
                .thenComparingInt(AccessChoice::onRing)
                .thenComparingInt(AccessChoice::borderline)
                .thenComparingInt(AccessChoice::heightDifference)
                .thenComparingDouble(AccessChoice::distance)
                .thenComparingInt(AccessChoice::order));
        return List.copyOf(choices);
    }

    /**
     * Builds the temporary column under the cell of the project.
     *
     * <p>The inner cells of the roof stand above the free cells of the room, and the player has to
     * click the upper face of a block that is there. The add-on builds that block as a temporary
     * column of the climb: the column grows from the floor of the house, and the click on its
     * upper face fills the cell of the roof (ТЗ.md section 4, the temporary blocks of the
     * 2026-11-13). The column is taken away with the other supports when the room of the house is
     * empty again.
     *
     * @return true when the tick belongs to the temporary column
     */
    private boolean prepareOperationSupport() {
        if (client.world == null || client.player == null || target == null || origin == null) {
            return false;
        }
        if (scaffoldCell != null) {
            // A column is already on its way: the tick belongs to it.
            return true;
        }
        if (target.cell().y() != HouseTemplate.SIZE_Y - 1) {
            // Only the cells of the roof stand above the free cells of the room.
            return false;
        }
        BlockPos below = world(target.cell()).down();
        if (!insideRoom(below) || onRing(below)) {
            return false;
        }
        if (!client.world.getBlockState(below).isAir()) {
            // The block is there: the planner of the core uses it as the support of the cell.
            return false;
        }
        if (!canBuildSupport(below)) {
            return false;
        }
        // The block is placed against the side of a column of the room when there is one at the same
        // level: the line to the upper face of the block below is often blocked by another column,
        // and the column under the cell would grow higher and higher (ТЗ.md section 4).
        for (Direction direction : Direction.Type.HORIZONTAL) {
            BlockPos neighbour = below.offset(direction);
            if (!client.world.getBlockState(neighbour).isSolidBlock(client.world, neighbour)) {
                continue;
            }
            Direction face = opposite(direction);
            scaffoldCell = below;
            scaffoldSupport = neighbour;
            scaffoldFace = face;
            scaffoldHit = hitPosition(neighbour, face);
            scaffoldSent = false;
            scaffoldClicked = false;
            scaffoldWaitTicks = 0;
            scaffoldFailure = null;
            if (!reservedSupports.contains(below)) {
                reservedSupports.add(below);
            }
            publishFacts("the temporary support under the cell of the project is prepared"
                    + " (against the side of a column of the room)");
            return true;
        }
        BlockPos next = nextTowerBlock(below);
        if (next == null) {
            return false;
        }
        BlockPos support = next.down();
        if (!client.world.getBlockState(support).isSolidBlock(client.world, support)) {
            return false;
        }
        scaffoldCell = next;
        scaffoldSupport = support;
        scaffoldFace = Direction.UP;
        scaffoldHit = new Vec3d(support.getX() + 0.5, support.getY() + 1.0, support.getZ() + 0.5);
        scaffoldSent = false;
        scaffoldClicked = false;
        scaffoldWaitTicks = 0;
        scaffoldFailure = null;
        if (!reservedSupports.contains(next)) {
            reservedSupports.add(next);
        }
        publishFacts("the temporary column under the cell of the project is prepared");
        return true;
    }

    /** The side of a block that points into the opposite direction. */
    private Direction opposite(Direction direction) {
        return switch (direction) {
            case NORTH -> Direction.SOUTH;
            case SOUTH -> Direction.NORTH;
            case WEST -> Direction.EAST;
            case EAST -> Direction.WEST;
            default -> direction;
        };
    }

    /**
     * The cells of the approach that the add-on finds in the world itself.
     *
     * <p>The planner of the core names the cells of the approach from the cells of the project: the
     * neighbours of the cell of the project and the free cells of the floor of the house. When the
     * room is full of temporary columns, the neighbours of the cell of the project stand occupied by
     * the columns of the add-on itself, and the free cells of the floor are too far away to click
     * the cell of the project (2026-11-14). The add-on therefore offers the empty cells of the room
     * as well: the player stands there, the column under the cell is built when it is needed, and the
     * cell is close enough to the support of the cell of the project (ТЗ.md section 4).
     *
     * @return the cells of the room that the player can stand in
     */
    private List<Cell> worldAccessCandidates() {
        List<Cell> candidates = new ArrayList<>();
        if (origin == null || client.world == null) {
            return candidates;
        }
        for (int x = 1; x < HouseTemplate.SIZE_X - 1; x++) {
            for (int z = 1; z < HouseTemplate.SIZE_Z - 1; z++) {
                for (int y = 1; y <= HouseTemplate.SIZE_Y - 2; y++) {
                    Cell cell = new Cell(x, y, z);
                    if (HouseTemplate.forVariant(0).cells().containsKey(cell)) {
                        // The project occupies the cell: the player cannot stand in it.
                        continue;
                    }
                    if (!client.world.getBlockState(world(cell)).isAir()) {
                        // A block stands in the cell: a column of the add-on or a block of the
                        // project that is not yet known by the planner.
                        continue;
                    }
                    if (target != null && target.cell().equals(cell)) {
                        // The cell of the operation itself: the player would stand in the cell
                        // that has to be filled, and the game refuses the placement (ТЗ.md
                        // section 4).
                        continue;
                    }
                    if (target != null && new Cell(target.cell().x(), target.cell().y() - 1,
                            target.cell().z()).equals(cell)) {
                        // The cell under the cell of the project: the player would stand where the
                        // temporary column of the cell has to be built, and the game refuses the
                        // placement into the body of the player (ТЗ.md section 4).
                        continue;
                    }
                    candidates.add(cell);
                }
            }
        }
        return candidates;
    }

    /**
     * True when the cell of the approach is closed from above by a block of the project.
     *
     * <p>A cell under the roof of the house is useless as a place to stand: the player steps in
     * and cannot come out again, because the roof cell above it is already built. A temporary
     * column of the add-on does not close the cell: it is taken away again (ТЗ.md section 4).
     *
     * @param candidate the cell of the approach, relative to the origin of the house
     * @return true when the cell cannot be used
     */
    private boolean sealedAccess(Cell candidate) {
        if (origin == null || client.world == null) {
            return false;
        }
        Cell above = new Cell(candidate.x(), candidate.y() + 1, candidate.z());
        if (!HouseTemplate.forVariant(0).cells().containsKey(above)) {
            // Nothing of the project stands above the cell.
            return false;
        }
        BlockPos abovePos = world(above);
        if (scaffoldPositions().contains(abovePos)) {
            // A temporary column of the add-on is above: it will be taken away again.
            return false;
        }
        return !client.world.getBlockState(abovePos).isAir();
    }

    /** The height of the face of the support that the operation clicks. */
    private static double faceHeight(Support support) {
        return support == Support.BELOW ? 1.0 : 0.5;
    }

    /** One cell of the approach and the attempt of the planner that offered it. */
    private record AccessChoice(PlacementAttempt attempt, Cell candidate, int attemptIndex, int order,
                                int heightDifference, int hidden, double distance, int onRing,
                                int borderline) {
    }

    private BlockPos world(Cell cell) {
        return new BlockPos(origin.x() + cell.x(), origin.y() + cell.y(), origin.z() + cell.z());
    }

    private boolean solid(BlockPos pos) {
        if (client.world == null) {
            return false;
        }
        return client.world.getBlockState(pos).isSolidBlock(client.world, pos);
    }

    /**
     * True when the body of the player keeps a small distance from the cell of the project.
     *
     * <p>The server sees the player a little further on than the client does, and the game then
     * refuses the placement silently: the interaction is sent, but no block appears. The box of
     * the player is 0,6 of a cell wide, and the check of the game uses the position of the server,
     * so the client keeps a small margin to the cell of the project (ТЗ.md section 4).
     *
     * @param targetPos the cell that is to be filled
     * @return true when the body of the player does not reach into the cell
     */
    private boolean bodyClear(BlockPos targetPos) {
        if (client.player == null) {
            return false;
        }
        double margin = 0.08;
        double half = 0.3 + margin;
        double x = client.player.getX();
        double z = client.player.getZ();
        double y = client.player.getY();
        return x + half <= targetPos.getX() || x - half >= targetPos.getX() + 1
                || z + half <= targetPos.getZ() || z - half >= targetPos.getZ() + 1
                || y + 1.8 <= targetPos.getY() || y >= targetPos.getY() + 1;
    }

    private boolean standable(BlockPos pos) {
        if (client.world == null) {
            return false;
        }        if (!client.world.getBlockState(pos).isAir()) {
            return false;
        }
        BlockPos below = pos.down();
        return client.world.getBlockState(below).isSolidBlock(client.world, below);
    }

    /**
     * True when the player can stand in the cell of the approach.
     *
     * <p>The cell of the approach is a cell of the room of the house. It has a floor of its own
     * when the block below it is there. Otherwise the add-on may build a column of temporary
     * blocks under the cell, and the player climbs that column: the wall of the third level and the
     * ring of the roof are two levels above the floor of the house, and the player cannot stand
     * on the crest of the wall without a fall of three or four blocks (2026-11-13).
     *
     * <p>The blocks of the column stand inside the room of the house, where the rule of the repair
     * takes them away again, and the add-on removes its own blocks as well (ТЗ.md section 4).
     *
     * @param access the cell of the approach
     * @return true when the player can stand in the cell
     */
    private boolean supportable(BlockPos access) {
        if (access == null || client.world == null || origin == null) {
            return false;
        }
        if (!client.world.getBlockState(access).isAir()) {
            // A block stands in the cell of the approach: the player cannot stand in it, and the
            // navigation would wait for ever for a cell that cannot be reached. The dependency
            // stops a task that stands still for ten seconds (2026-11-14).
            return false;
        }
        if (standable(access)) {
            return true;
        }
        BlockPos cell = access.down();
        for (int depth = 0; depth <= MAX_TOWER; depth++) {
            if (!client.world.getBlockState(cell).isAir()) {
                // Something else stands under the cell: the add-on does not build over it.
                return false;
            }
            if (!canBuildSupport(cell)) {
                return false;
            }
            BlockPos support = cell.down();
            if (client.world.getBlockState(support).isSolidBlock(client.world, support)) {
                // The block of the column can be built on this support.
                return true;
            }
            cell = support;
        }
        return false;
    }

    /**
     * True when the cell above the cell of the approach is free.
     *
     * <p>The body of the player is one block and a half high: the head of the player stands in the
     * cell above the cell of the approach. A roof that is already built there would push the player
     * out of the cell (ТЗ.md section 4).
     *
     * @param access the cell of the approach
     * @return true when the cell above the cell of the approach is free
     */
    private boolean headFree(BlockPos access) {
        if (access == null || client.world == null) {
            return false;
        }
        return client.world.getBlockState(access.up()).isAir();
    }

    /**
     * True when the add-on may put a temporary block into the cell.
     *
     * <p>The block stands inside the room of the house: the rule of the repair takes it away again,
     * and nothing stands in the way of the house itself (ТЗ.md section 4).
     *
     * @param cell the cell of the temporary block
     * @return true when the cell may carry a temporary block
     */
    private boolean canBuildSupport(BlockPos cell) {
        if (!insideVolume(cell) || !insideRoom(cell) || onRing(cell)) {
            return false;
        }
        return !projectCell.test(new Vec3i(cell.getX(), cell.getY(), cell.getZ()));
    }

    /**
     * The next temporary block of the column under the cell of the approach.
     *
     * <p>The column grows from the floor of the house: the lowest empty cell that stands on a solid
     * block is built first, and the cell above it comes next.
     *
     * @param access the cell of the approach
     * @return the cell of the next temporary block, or null when there is nothing to build
     */
    private BlockPos nextTowerBlock(BlockPos access) {
        if (access == null || client.world == null) {
            return null;
        }
        BlockPos cell = access.down();
        for (int depth = 0; depth <= MAX_TOWER; depth++) {
            if (!client.world.getBlockState(cell).isAir()) {
                // The column is already standing up to this cell.
                return null;
            }
            if (!canBuildSupport(cell)) {
                return null;
            }
            BlockPos support = cell.down();
            if (client.world.getBlockState(support).isSolidBlock(client.world, support)) {
                return cell;
            }
            cell = support;
        }
        return null;
    }

    /** The distance from the eyes of the player at the access cell to the clicked face. */
    private boolean reaches(BlockPos access, Vec3d hit) {
        Vec3d eye = new Vec3d(access.getX() + 0.5, access.getY() + EYE_HEIGHT, access.getZ() + 0.5);
        return eye.distanceTo(hit) <= REACH;
    }

    /**
     * The distance from the eyes of the player in the cell of the approach to the clicked face.
     *
     * <p>The distance is computed from the middle of the cell, while the player stands where the
     * navigation leaves him: the real distance can be a little larger. A cell that is exactly at
     * the limit of the ordinary range is therefore refused by the server, and the cell of the
     * approach is tried again and again while the player stands still (2026-11-14).
     *
     * @param candidate the cell of the approach, relative to the origin of the house
     * @param attempt   the attempt of the planner that offered the cell
     * @return the distance from the eyes of the player to the clicked face
     */
    private double nominalReach(Cell candidate, PlacementAttempt attempt) {
        if (origin == null) {
            return Double.MAX_VALUE;
        }
        BlockPos support = world(attempt.supportCell());
        Vec3d hit = hitPosition(support, faceOf(attempt.support()));
        Vec3d eye = new Vec3d(origin.x() + candidate.x() + 0.5,
                origin.y() + candidate.y() + EYE_HEIGHT,
                origin.z() + candidate.z() + 0.5);
        return eye.distanceTo(hit);
    }

    /** The face of the support block that is clicked. */
    private Direction faceOf(Support support) {
        return switch (support) {
            case BELOW -> Direction.UP;
            case NORTH -> Direction.SOUTH;
            case SOUTH -> Direction.NORTH;
            case WEST -> Direction.EAST;
            case EAST -> Direction.WEST;
        };
    }

    /** The centre of the clicked face of the support block. */
    private Vec3d hitPosition(BlockPos support, Direction face) {
        return switch (face) {
            case UP -> new Vec3d(support.getX() + 0.5, support.getY() + 1.0, support.getZ() + 0.5);
            case DOWN -> new Vec3d(support.getX() + 0.5, support.getY(), support.getZ() + 0.5);
            case NORTH -> new Vec3d(support.getX() + 0.5, support.getY() + 0.5, support.getZ());
            case SOUTH -> new Vec3d(support.getX() + 0.5, support.getY() + 0.5, support.getZ() + 1.0);
            case WEST -> new Vec3d(support.getX(), support.getY() + 0.5, support.getZ() + 0.5);
            case EAST -> new Vec3d(support.getX() + 1.0, support.getY() + 0.5, support.getZ() + 0.5);
        };
    }

    // --- the direction of the player --------------------------------------------------------------

    /**
     * Takes one step to the side towards the cell of the approach.
     *
     * <p>The navigation of the dependency reports that it has arrived while the player stands in
     * the neighbouring cell, and it does not move him then. The player would stand still for the
     * ten seconds in which the dependency stops the task (2026-11-14). The add-on takes one step
     * with the ordinary key of the movement of the game and releases the key right away
     * (ТЗ.md section 4).
     *
     * @param access the cell of the approach
     * @return true when the step was prepared
     */
    private boolean stepTowards(BlockPos access) {
        if (client.player == null || client.options == null || access == null) {
            return false;
        }
        BlockPos playerPos = client.player.getBlockPos();
        if (playerPos.getY() != access.getY()) {
            // A step up or down needs a floor under the cell of the approach, and the column of
            // the climb builds that floor.
            return false;
        }
        int dx = access.getX() - playerPos.getX();
        int dz = access.getZ() - playerPos.getZ();
        if (Math.abs(dx) + Math.abs(dz) != 1) {
            // Only the cell next to the player: a longer walk is the work of the navigation.
            return false;
        }
        if (!standable(access)) {
            // The cell of the approach has no floor: the player would fall into the room.
            return false;
        }
        nudgeDirection = dx == 1 ? Direction.EAST : dx == -1 ? Direction.WEST
                : dz == 1 ? Direction.SOUTH : Direction.NORTH;
        nudgeTicks = NUDGE_TICKS;
        return true;
    }

    /**
     * The direction of the view that points at the point of the click.
     *
     * <p>The direction is the geometry of the cell of the approach: the player looks at the face
     * of the support that the operation needs. It is not searched over four directions any more:
     * the direction of the view decides the orientation of a door, a bed, a furnace or a lantern,
     * so a direction that does not look at the support would name another block with the
     * crosshair and the block would never be placed.
     *
     * @param access the cell where the player stands for the click
     * @param hit    the point of the click on the face of the support
     * @return the direction of the view or null when it cannot be computed
     */
    private Float yawAtSupport(BlockPos access, Vec3d hit) {
        if (access == null || hit == null) {
            return null;
        }
        double dx = hit.x - (access.getX() + 0.5);
        double dz = hit.z - (access.getZ() + 0.5);
        return (float) Math.toDegrees(Math.atan2(-dx, dz));
    }

    /**
     * True when the game would place the state of the project with the given direction of the view.
     *
     * <p>The question is asked of the game itself: the state comes from the real vanilla
     * placement API, the cell comes from the hit result, and the direction of the view is the one
     * the player has when he looks at the support. Nothing about the orientation of a special
     * block is guessed here.
     *
     * @param yaw     the direction of the view of the player
     * @param hit     the point of the click
     * @param face    the face of the support that the operation needs
     * @param support the block that is clicked
     * @return true only when the game places the state of the project in the cell of the project
     */
    /**
     * True when the game would put the block into the cell of the project.
     *
     * <p>The question is asked of the game itself: the cell comes from the hit result and the
     * direction of the view. The properties of the state are not asked here, because the state
     * of a door follows the place of the player, and the player is not yet in the cell of the
     * approach while the search runs. The properties are asked before the click.
     *
     * @param yaw     the direction of the view of the player
     * @param hit     the point of the click
     * @param face    the face of the support that the operation needs
     * @param support the block that is clicked
     * @return true only when the block would stand in the cell of the project
     */
    private boolean placementCellMatches(float yaw, Vec3d hit, Direction face, BlockPos support) {
        if (client.world == null || client.player == null || target == null) {
            return false;
        }
        float saved = client.player.getYaw();
        try {
            client.player.setYaw(yaw);
            ItemPlacementContext context = new ItemPlacementContext(client.player, Hand.MAIN_HAND,
                    new ItemStack(item), new BlockHitResult(hit, face, support, false));
            return context.getBlockPos().equals(world(target.cell()));
        } finally {
            client.player.setYaw(saved);
        }
    }

    private boolean stateMatchesProject(float yaw, Vec3d hit, Direction face, BlockPos support,
                                        BlockPos access) {
        if (client.world == null || client.player == null || target == null) {
            return false;
        }
        float saved = client.player.getYaw();
        Vec3d savedPos = client.player.getEntityPos();
        try {
            client.player.setYaw(yaw);
            if (access != null) {
                // The state of a door depends on the place of the player: the hinge follows the
                // side of the door on which the player stands. The state is therefore asked for
                // the place of the cell of the approach and not for the place that the player
                // happens to occupy while the search runs.
                client.player.setPos(access.getX() + 0.5, access.getY(), access.getZ() + 0.5);
            }
            ItemPlacementContext context = new ItemPlacementContext(client.player, Hand.MAIN_HAND,
                    new ItemStack(item), new BlockHitResult(hit, face, support, false));
            BlockPos wanted = world(target.cell());
            if (!context.getBlockPos().equals(wanted)) {
                // The block would stand in another cell than the cell of the project.
                return false;
            }
            Block block = Registries.BLOCK.get(Identifier.of(target.blockId()));
            BlockState state = block.getPlacementState(context);
            if (state == null) {
                stateMismatch = "no_state";
                return false;
            }
            boolean matched = matches(state, target.requiredProperties());
            if (!matched) {
                stateMismatch = describe(state);
            }
            return matched;
        } finally {
            client.player.setYaw(saved);
            client.player.setPos(savedPos.x, savedPos.y, savedPos.z);
        }
    }

    /** True when the state has every property that the project requires. */
    private boolean matches(BlockState state, Map<String, String> required) {
        for (Map.Entry<String, String> entry : required.entrySet()) {
            String actual = null;
            for (net.minecraft.state.property.Property<?> property : state.getProperties()) {
                if (property.getName().equals(entry.getKey())) {
                    actual = String.valueOf(state.get(property));
                    break;
                }
            }
            if (!entry.getValue().equals(actual)) {
                return false;
            }
        }
        return true;
    }

    // --- the aim and the click ---------------------------------------------------------------------

    /**
     * Turns the view of the player to the support of the cell.
     *
     * <p>The yaw and the pitch are set in the same tick, so that both of them reach the server
     * with the same movement packet. The direction is computed from the real eyes of the player
     * and not from the cell of the approach: the player does not stand exactly in the middle of
     * it. The direction is never replaced by another one: the direction of the view decides the
     * orientation of a door, a bed, a furnace or a lantern, and a direction that does not look at
     * the support would name another block with the crosshair of the game.
     */
    private void aimAtSupport() {
        if (solution == null) {
            return;
        }
        aimAt(solution.hit());
    }

    /**
     * Turns the view of the player to a point of the world.
     *
     * @param point the point of the world that the crosshair has to name
     */
    private void aimAt(Vec3d point) {
        if (point == null || client.player == null) {
            return;
        }
        Vec3d eye = client.player.getEyePos();
        double dx = point.x - eye.x;
        double dy = point.y - eye.y;
        double dz = point.z - eye.z;
        float yaw = (float) Math.toDegrees(Math.atan2(-dx, dz));
        float pitch = (float) -Math.toDegrees(Math.atan2(dy, Math.sqrt(dx * dx + dz * dz)));
        client.player.setYaw(yaw);
        client.player.setPitch(pitch);
    }

    /**
     * The hit result that the game itself computes under the crosshair.
     *
     * <p>The click is sent with the answer of the game and not with a hand-made one: the game
     * refuses an interaction whose hit result does not belong to the world it knows.
     *
     * @return the hit result of the crosshair or null when the crosshair has no block
     */
    private BlockHitResult crosshairTarget() {
        if (client.crosshairTarget instanceof BlockHitResult hit) {
            return hit;
        }
        return null;
    }

    /**
     * The verdict of the comparison of the view of the player with the support of the cell.
     *
     * @param crosshair the hit result of the game
     * @return the verdict of the comparison
     */
    private AimMatch.Verdict aimVerdict(BlockHitResult crosshair) {
        if (crosshair == null || solution == null) {
            return AimMatch.Verdict.NO_TARGET;
        }
        return AimMatch.of(true,
                crosshair.getBlockPos().equals(solution.support()),
                crosshair.getSide() == solution.face());
    }

    /**
     * The verdict of the aim as it is published in the facts of the attempt.
     *
     * <p>SUPPORT means that the crosshair of the game names the support of the operation and
     * that the game would place the state of the project with it: the interaction is sent then.
     * STATE_MISMATCH means that the crosshair names the support, but the state that the game
     * would place is not the state of the project (the orientation of a special block does not
     * follow from this direction of the view).
     *
     * @param verdict      the verdict of the comparison of the view with the support
     * @param stateMatches the game would place the state of the project with the crosshair
     * @return the verdict as it is published
     */
    private String publishedVerdict(AimMatch.Verdict verdict, boolean stateMatches) {
        return switch (verdict) {
            case NO_TARGET -> "NO_TARGET";
            case WRONG_BLOCK -> "WRONG_BLOCK";
            case WRONG_FACE -> "WRONG_FACE";
            case MATCH -> stateMatches ? "SUPPORT" : "STATE_MISMATCH";
        };
    }

    /**
     * The state that the game would place for the hit result of the crosshair.
     *
     * @param hit the hit result of the crosshair
     * @return the state of the project or null when the game places nothing
     */
    private BlockState placementState(BlockHitResult hit) {
        if (client.world == null || client.player == null || target == null || hit == null) {
            return null;
        }
        Block block = Registries.BLOCK.get(Identifier.of(target.blockId()));
        ItemStack stack = client.player.getMainHandStack();
        ItemPlacementContext context = new ItemPlacementContext(client.player, Hand.MAIN_HAND, stack, hit);
        return block.getPlacementState(context);
    }

    /**
     * True when the game would place the state of the project with the given hit result.
     *
     * <p>The question is asked of the game with the hit result that the game itself computes
     * under the crosshair: the state comes from the real vanilla placement API, and the cell
     * comes from the hit result. The direction of the view of the player is the one he really
     * has, so the orientation of a special block is the one the game would use.
     *
     * @param hit the hit result of the crosshair
     * @return true only when the game places the state of the project in the cell of the project
     */
    private boolean placementStateMatches(BlockHitResult hit) {
        if (solution == null || target == null || hit == null) {
            return false;
        }
        if (client.world == null || client.player == null) {
            return false;
        }
        ItemPlacementContext context = new ItemPlacementContext(client.player, Hand.MAIN_HAND,
                client.player.getMainHandStack(), hit);
        BlockPos wanted = world(target.cell());
        if (!context.getBlockPos().equals(wanted)) {
            // The block would stand in another cell than the cell of the project.
            stateMismatch = "cell=" + context.getBlockPos().toShortString();
            return false;
        }
        Block block = Registries.BLOCK.get(Identifier.of(target.blockId()));
        BlockState state = block.getPlacementState(context);
        if (state == null) {
            stateMismatch = "no_state";
            return false;
        }
        boolean matched = matches(state, target.requiredProperties());
        if (!matched) {
            // The state that the game would place is published: a run in which the orientation of
            // a block is wrong has to be read, not guessed (ТЗ.md section 7).
            stateMismatch = describe(state);
        }
        return matched;
    }

    /** The properties of a state as one line of text. */
    private static String describe(BlockState state) {
        StringBuilder text = new StringBuilder();
        for (net.minecraft.state.property.Property<?> property : state.getProperties()) {
            if (text.length() > 0) {
                text.append(',');
            }
            text.append(property.getName()).append('=').append(state.get(property));
        }
        return text.toString();
    }

    /**
     * The first precondition of the ordinary placement that did not hold.
     *
     * <p>The questions are the questions of the game itself: the item in the hand, the features
     * that the item needs, the cell that may be filled, the state that the game produces, the
     * place of that state and the ordinary range of the click. The rule that names the first
     * precondition that did not hold lives in the domain core
     * ({@link PlacementPreconditions}).
     *
     * @param hit the hit result of the crosshair
     * @return the name of the first precondition that did not hold or null when all of them hold
     */
    private String preconditionFailure(BlockHitResult hit) {
        if (client.world == null || client.player == null || target == null || hit == null) {
            return PlacementPreconditions.Step.STACK_IN_HAND.name();
        }
        Map<PlacementPreconditions.Step, Boolean> observed =
                new EnumMap<>(PlacementPreconditions.Step.class);
        ItemStack stack = client.player.getMainHandStack();
        String wanted = operation == null ? null : operation.itemId();
        String held = stack.isEmpty() ? "" : Registries.ITEM.getId(stack.getItem()).toString();
        observed.put(PlacementPreconditions.Step.STACK_IN_HAND,
                !stack.isEmpty() && wanted != null && wanted.equals(held));
        observed.put(PlacementPreconditions.Step.ITEM_ENABLED,
                stack.isItemEnabled(client.world.getEnabledFeatures()));
        ItemPlacementContext context = new ItemPlacementContext(client.player, Hand.MAIN_HAND, stack, hit);
        observed.put(PlacementPreconditions.Step.CAN_PLACE, context.canPlace());
        BlockState state = placementState(hit);
        observed.put(PlacementPreconditions.Step.PLACEMENT_STATE,
                state != null && matches(state, target.requiredProperties()));
        BlockPos targetPos = context.getBlockPos();
        observed.put(PlacementPreconditions.Step.TARGET_CELL,
                targetPos.equals(world(target.cell())));
        observed.put(PlacementPreconditions.Step.STATE_AT,
                state != null && state.canPlaceAt(client.world, targetPos));
        boolean collisionFree = state != null && client.world.canPlace(state, targetPos,
                net.minecraft.block.ShapeContext.of(client.player));
        observed.put(PlacementPreconditions.Step.NO_COLLISION, collisionFree && bodyClear(targetPos));
        Vec3d eye = client.player.getEyePos();
        Vec3d point = hit.getPos();
        observed.put(PlacementPreconditions.Step.REACH,
                ReachPolicy.mayInteract(eye.x, eye.y, eye.z, point.x, point.y, point.z));
        return PlacementPreconditions.firstFailure(observed);
    }

    // --- the tick --------------------------------------------------------------------------------

    /** One tick of the placement: walk, aim, place. */
    public void tick() {
        tickCounter++;
        if (client.player == null || client.world == null) {
            releaseOwnedKeys();
            return;
        }
        if (closeOwnScreen()) {
            // The click of the add-on has opened a window of the game (a barrel, a crafting table
            // or a furnace of the room of the house). The world of the single player stands still
            // while such a window is open, the dependency is paused and the house waits in the
            // state busy for minutes. The add-on closes the window that its own click has opened
            // and continues (2026-11-14).
            releaseOwnedKeys();
            return;
        }
        if (scaffoldWorld != null && client.world != scaffoldWorld) {
            if (!scaffolds.isEmpty() || scaffoldCell != null) {
                releaseOwnedKeys();
                scaffoldFailure = "unreconciled temporary supports from a different world or session";
                return;
            }
            scaffoldWorld = null;
        }
        if (!actionAllowed.getAsBoolean()) {
            releaseOwnedKeys();
            // The gate of the background actions is closed: UnionClef is paused, a task of another
            // owner runs or the automation is suspended. The operation that was requested before
            // is not performed now (ТЗ.md sections 3 and 5).
            return;
        }
        // The watchdog of the standing player. The dependency stops a task that stands still for
        // ten seconds and calls it a stuck bot; the add-on names the reason itself a moment
        // before that happens, so that the house is suspended with a concrete reason and not with
        // an interruption (ТЗ.md section 4).
        BlockPos playerBlock = client.player.getBlockPos();
        Vec3d playerPosition = client.player.getEntityPos();
        if (lastMovePosition != null && lastMovePosition.distanceTo(playerPosition) > 0.02) {
            // The player moved: the counter of the standing starts again. The distance is
            // measured and not the cell: a small step inside the cell is a movement for the
            // dependency, and the add-on must count it as one.
            idleTicks = 0;
        } else {
            idleTicks++;
        }
        lastMovePosition = playerPosition;
        // The list of the blocks that the dependency may dig is kept up to date in every tick:
        // the temporary supports of the add-on change while the house is built, and the dependency
        // must not be stopped by the rule that protects the volume of the house (2026-11-14).
        declareOwnRemovals();
        if (idleTicks > IDLE_TICKS && operation != null && !clickSent && !confirmed
                && !supportCleanupActive && !itemPending) {
            // The player does not come to the cell of the approach. The navigation of the
            // dependency does not climb a wall of one block ("Wall too high to jump"), so the
            // add-on builds the step itself and tries again (ТЗ.md section 4, the temporary
            // blocks of the 2026-11-13). The wait for the item is not a stall of the player: the
            // material is on its way from the server, and the player stands still meanwhile.
            if (solution != null && prepareScaffold() && scaffoldStep()) {
                idleTicks = 0;
                return;
            }
            if (solution != null && standable(solution.access)) {
                // The support of the cell of the approach is there after all: a block of the
                // dependency or the column itself. The player goes on (ТЗ.md section 4).
                idleTicks = 0;
                return;
            }
            session.update(PlacementSession.Stage.NO_SUPPORT,
                    "the player stands still and does not come to the cell of the approach");
            idleTicks = 0;
            // The cell of the approach is left for another one, and the operation waits for a
            // later try: the dependency stops a task that stands still for ten seconds, and the
            // house would not get any further (ТЗ.md section 4).
            leaveAccess();
            publishFacts("the player stands still; the cell of the approach is left");
            return;
        }
        if (idleTicks > IDLE_TICKS && supportCleanupActive && !scaffoldsToRemove.isEmpty()) {
            // The removal of a temporary support waits for a line that stays blocked: the player
            // stands still and the dependency stops the task after ten seconds. The support is
            // handed over to the safe repair of the core, which breaks a wrong cell of the room
            // with an ordinary attack of the player, and the build goes on (ТЗ.md section 4).
            handOverCleanupTarget();
            return;
        }
        int feetY = client.player.getBlockPos().getY();
        if (origin != null && previousFeetY > feetY && feetY <= origin.y() + 1) {
            queueSupportCleanup();
        }
        previousFeetY = feetY;
        if (repairTarget != null) {
            // The safe repair of a wrong cell runs before everything else: the cell is read
            // again and only then the building continues (ТЗ.md section 4).
            declareOwnRemovals();
            repairTick();
            return;
        }
        if (nudgeTicks > 0) {
            // The player steps aside from the cell that is to be filled: the ordinary key of the
            // movement of the game, released at the end of the step (ТЗ.md section 4).
            applyNudge();
            return;
        }
        if (furnitureAhead() && scaffoldsToRemove.isEmpty() && !scaffolds.isEmpty()) {
            // The furniture stands into the room of the house, and the temporary columns of the
            // climb stand there as well: the add-on takes its own blocks away before it puts the
            // furniture into the room (ТЗ.md section 4).
            queueSupportCleanup();
        }
        if (!scaffoldsToRemove.isEmpty() && (supportCleanupActive || viewIsFree())) {
            // Once cleanup starts, it owns movement and the view until it finishes or fails.
            // Resuming the placement midway would restart navigation and move the crosshair.
            supportCleanupActive = true;
            declareOwnRemovals();
            if (removeScaffoldTick()) return;
            if (scaffoldsToRemove.isEmpty()) {
                supportCleanupActive = false;
                navigation.clearNavigation();
            } else {
                return;
            }
        }
        if (descentHold != null && descentTick()) {
            // The player walks down through the cell of the roof that is held open: the house
            // waits until the player is inside (ТЗ.md section 2).
            return;
        }
        if (removalTarget != null) {
            removalTick();
            return;
        }
        if (operation == null || target == null) {
            if (exitPhase != ExitPhase.NONE) {
                // Nothing is built any more: the way out of the finished house is prepared.
                exitTick();
            }
            return;
        }
        if (exitPhase != ExitPhase.NONE) {
            // A new operation means that the house is built again: the way out is not prepared.
            exitPhase = ExitPhase.NONE;
        }
        if (operationMatchesWorld()) {
            confirm(operation);
            return;
        }
        // After a send, only the world read and an explicit retry may advance this operation.
        if (clickSent || confirmed) return;
        if (accessExhausted) return;
        // No approach/view/item/precondition branch can wait forever before a send. These are
        // the existing arrival and aim budgets; a different approach does not count as a send.
        if (session.preparationExpired(ArrivalPolicy.ACCESS_WAIT_TICKS + ArrivalPolicy.AIM_WAIT_TICKS)) {
            leaveAccess();
            return;
        }
        if (solution == null) {
            solution = solve(solutionSlot);
            aimSent = false;
            aimTicks = 0;
            if (solution == null) {
                publishFacts((accessExhausted
                        ? "the operation is admissible, but no cell of the approach gave a "
                                + "confirmed placement"
                        : "there is no admissible way to place the operation")
                        + " (" + solveRejection + ")");
                return;
            }
            solutionSlot = solution.slot();
            publishFacts("the placement is planned");
        }
        if (prepareOperationSupport()) {
            // The temporary column under the cell of the project comes first: the player clicks
            // the upper face of its last block, and the planner of the core needs that block to
            // name it as the support of the cell (ТЗ.md section 4).
            scaffoldStep();
            return;
        }
        BlockPos playerPos = client.player.getBlockPos();
        if (!arrived(playerPos)) {
            if (playerPos.getY() > climbHeight) {
                // The player has climbed: the wait for the cell of the approach starts again. The
                // climb of the ring of the roof takes longer than the budget of one cell, and the
                // cell of the approach is not a failure of the player (ТЗ.md section 4).
                climbHeight = playerPos.getY();
                arrivalTicks = 0;
                session.holdPreparation();
                // The supports of the climb stay under the rule of the repair while the climb
                // lasts: the repair must not take a support away while the player climbs with
                // it. The add-on keeps its own supports until the room of the house is empty
                // again: the player climbs the same column again and again, and a support that
                // is taken away in the middle of the build stands in the way of the next cell of
                // the approach (ТЗ.md section 4).
            }
            arrivalTicks++;
            if (lastPosition != null && lastPosition.equals(playerPos)) {
                stillTicks++;
            } else {
                lastPosition = playerPos;
                stillTicks = 0;
            }
            if (stillTicks >= STUCK_TICKS && !itemPending && scaffoldCell == null) {
                // The player stands still and does not come to the cell of the approach. The check
                // stands before the temporary column and before the temporary step: both of them
                // are a work of the add-on that needs the player to step aside first, and a stall
                // inside that work would keep the player still for the ten seconds in which the
                // dependency stops the task (2026-11-14).
                leaveAccess();
                return;
            }
            if (stillTicks >= 20 && !itemPending && scaffoldCell == null && solution != null
                    && stepTowards(solution.access)) {
                // The navigation of the dependency reports that it has arrived, but the player
                // stands in the neighbouring cell and does not move. One step with the ordinary
                // key of the movement of the game keeps the player moving, and the key is
                // released right away (2026-11-14).
                return;
            }
            if (scaffoldStep()) {
                // The temporary support of the climb is placed or waits for its confirmation.
                return;
            }
            if (towerStep(solution.access)) {
                // The cell of the approach has no floor: the add-on builds the temporary column
                // under it and the player climbs that column (ТЗ.md section 4).
                return;
            }
            if (climbBlocked(solution.access) && prepareScaffold() && scaffoldStep()) {
                // The player cannot step up to the cell of the approach: the add-on builds the
                // step itself (ТЗ.md section 4, the temporary blocks of the 2026-11-13).
                return;
            }
            if (scaffoldCell != null || !standable(solution.access)) {
                // The add-on builds the temporary column under the cell of the approach itself:
                // the player cannot arrive before the column is there, and the budget of the stall
                // does not run while the column is built (ТЗ.md section 4).
                stillTicks = 0;
                arrivalTicks = 0;
            }
            if (stillTicks >= STUCK_TICKS && !itemPending) {
                // The player stands still and does not come to the cell of the approach: the
                // cell is left for the next one, without waiting for the whole budget. The
                // dependency stops a task that stands still for ten seconds, and the house
                // would not get any further (ТЗ.md section 4). The wait for the material is not
                // a stall of the player.
                leaveAccess();
                return;
            }
            if (ArrivalPolicy.mayLeaveAccess(false, true, arrivalTicks, clicksAtAccess)) {
                // The player did not stand in the cell of the approach and nothing was sent for
                // it: the next cell of the approach is tried. When no cell is left, the house is
                // suspended with a concrete reason and the coordinate of the cell.
                leaveAccess();
                return;
            }
            // The view is not turned while the player walks: the navigation of the dependency
            // uses the view of the player for its own tasks, and the turn of the view during
            // the walk made the walk itself slower (measured: the walking grew from 122 s to
            // 360 s for the same number of cells).
            navigation.requestNavigation(navigationWaypoint(solution.access));
            session.update(PlacementSession.Stage.APPROACHING, "walking to approach");
            publishFacts("the player walks to the cell of the approach");
            return;
        }
        arrivalTicks = 0;
        stillTicks = 0;
        if (!mayClick(playerPos)) {
            // The player stands in the cell that is to be filled: the server would refuse the
            // interaction, because a block cannot be placed into the cell of the player. The cell
            // of the approach is left for the next one — waiting does not help, the player is
            // already there. When no cell is left, the house is suspended with a concrete reason
            // and the coordinate of the cell.
            if (ArrivalPolicy.mayLeaveAccess(true, false, 0, clicksAtAccess)) {
                leaveAccess();
                return;
            }
            navigation.requestNavigation(solution.access);
            publishFacts("the player stands in the cell of the project");
            return;
        }
        // The distance is measured from the eyes of the player and not from the cell of the
        // approach: the server measures it the same way and rejects a click that is too far
        // without leaving a trace in the log of the game (ТЗ.md section 4).
        if (!insideReach()) {
            // The point of the click is out of the ordinary range: the player stands in the cell
            // of the approach, but the distance from his eyes is larger than the server allows.
            // The cell of the approach is left after a short budget: the player would stand still
            // otherwise, and the dependency stops a task that stands still for ten seconds
            // (2026-11-14).
            reachMissTicks++;
            if (reachMissTicks >= REACH_WAIT_TICKS) {
                reachMissTicks = 0;
                publishFacts("the point of the click stays out of the ordinary range;"
                        + " the cell of the approach is left");
                leaveAccess();
                return;
            }
            navigation.requestNavigation(navigationWaypoint(solution.access));
            publishFacts("the point of the click is out of the ordinary range");
            return;
        }
        reachMissTicks = 0;
        navigation.clearNavigation();
        if (!ensureItemInMainHand()) {
            itemPending = true;
            session.update(PlacementSession.Stage.SYNCHRONIZING, "waiting for the required item");
            // The wait for the material is not a failure of the cell of the approach: the budget
            // of the approach is not spent while the item is on its way from the server.
            session.holdPreparation();
            itemWaitTicks++;
            if (itemWaitTicks > ITEM_WAIT_TICKS) {
                // The item did not arrive. The dependency stops a task that stands still for ten
                // seconds and calls it a stuck bot, and the house would be suspended without a
                // reason. The add-on gives the reason itself before that moment comes
                // (ТЗ.md section 4).
                session.update(PlacementSession.Stage.EXHAUSTED,
                        "the item of the project did not arrive: " + operation.itemId());
                accessExhausted = true;
                itemWaitTicks = 0;
                publishFacts("the item of the project did not arrive in the time of the wait");
                return;
            }
            publishFacts("the item of the project is not in the main hand");
            return;
        }
        itemWaitTicks = 0;
        itemPending = false;
        if (!aimSent) {
            // The yaw and the pitch are turned in the same tick, so that both of them reach the
            // server with the same movement packet. The direction is computed from the real eyes
            // of the player: the player does not stand exactly in the middle of the cell of the
            // approach.
            aimAtSupport();
            aimSent = true;
            session.update(PlacementSession.Stage.AIMING, "synchronizing view");
            aimTicks = 0;
            publishFacts("the view is turned to the support");
            return;
        }
        aimTicks++;
        if (!InteractionPolicy.maySend(clickSent, confirmed)) {
            // The interaction of this attempt was already sent, or the world has confirmed the
            // operation. It is not repeated every tick; the world has to confirm the block or the
            // automation asks for a new attempt.
            return;
        }
        BlockHitResult crosshair = crosshairTarget();
        AimMatch.Verdict verdict = aimVerdict(crosshair);
        // The crosshair has to name the support of the operation, and the state that the game
        // would place with that hit result has to be the state of the project. The orientation
        // of a door, a bed, a furnace or a lantern depends on the direction of the view, so
        // both answers are asked of the game and no orientation is guessed.
        boolean supportNamed = verdict == AimMatch.Verdict.MATCH || verdict == AimMatch.Verdict.WRONG_FACE;
        boolean stateMatches = supportNamed && placementStateMatches(crosshair);
        aimVerdict = publishedVerdict(verdict, stateMatches);
        if (!AimPolicy.mayClick(aimSent, aimTicks, supportNamed, stateMatches)) {
            // The view does not name the support of the cell yet, or the game would place another
            // state: nothing is sent, the view is turned again and the client waits. A cell of
            // the approach that never gets the view is left for the next one, so that the house
            // does not wait for ever. The budget of the wait lives in the core.
            aimAtSupport();
            if (ArrivalPolicy.mayLeaveAccessOnAim(aimTicks, clicksAtAccess)) {
                leaveAccess();
                return;
            }
            publishFacts("the view does not name the support (" + aimVerdict + ")"
                    + (stateMismatch == null ? "" : " state=" + stateMismatch));
            return;
        }
        String failure = preconditionFailure(crosshair);
        preconditionStep = failure == null ? "none" : failure;
        if (failure != null) {
            // The game itself would refuse the interaction: nothing is sent and the facts name
            // the precondition that did not hold.
            if (PlacementPreconditions.Step.REACH.name().equals(failure)) {
                navigation.requestNavigation(solution.access);
            }
            if (PlacementPreconditions.Step.NO_COLLISION.name().equals(failure)) {
                // The body of the player reaches into the cell that is to be filled: the game
                // refuses the placement. The add-on moves the player a little away from the cell
                // with the ordinary keys of the game and tries again. The cell of the approach is
                // not given up: the player stands in it, but not in the way of the block
                // (ТЗ.md section 4).
                collisionRetries++;
                double playerX = client.player == null ? 0.0 : client.player.getX();
                double playerZ = client.player == null ? 0.0 : client.player.getZ();
                boolean stepped = !Double.isNaN(lastCollisionX)
                        && (Math.abs(playerX - lastCollisionX) > 0.02 || Math.abs(playerZ - lastCollisionZ) > 0.02);
                lastCollisionX = playerX;
                lastCollisionZ = playerZ;
                if (collisionRetries >= COLLISION_RETRIES && !stepped) {
                    // The step to the side did not move the player: it stands on a narrow column
                    // and every step back ends in the same place. The cell of the approach is
                    // left, because the dependency stops a task that stands still for ten
                    // seconds, and the house would not get any further (2026-11-14).
                    collisionRetries = 0;
                    lastCollisionX = Double.NaN;
                    leaveAccess();
                    publishFacts("the player does not step aside; the cell of the approach is left");
                    return;
                }
                nudgeAway();
                if (nudgeDirection == null) {
                    // There is no free cell next to the player: it stands on a narrow column or in
                    // a corner, and a step into the void would drop it. The cell of the approach is
                    // left at once, another one is tried (ТЗ.md section 4).
                    collisionRetries = 0;
                    leaveAccess();
                    publishFacts("the player cannot step aside; the cell of the approach is left");
                    return;
                }
                publishFacts("the body of the player reaches into the cell; the player steps aside");
                return;
            }
            publishFacts("the ordinary placement refuses the cell (" + failure + ")");
            return;
        }
        ClientPlayerInteractionManager manager = client.interactionManager;
        if (manager != null && session.maySend()) {
            Vec3d eye = client.player.getEyePos();
            Vec3d point = crosshair.getPos();
            double reach = ReachPolicy.distance(eye.x, eye.y, eye.z, point.x, point.y, point.z);
            // The item in the hand is read before the interaction: the placement consumes the item
            // of the project, and the last item of a stack is gone right after the click. The
            // record has to name the item that the player really held when the click was sent.
            String heldBefore = Registries.ITEM.getId(client.player.getMainHandStack().getItem()).toString();
            ActionResult result = manager.interactBlock(client.player, Hand.MAIN_HAND, crosshair);
            // The result of the call is kept: it tells whether the client refused the
            // interaction or the server did (the difference is the difference between a wrong
            // approach of the player and a refusal of the world).
            lastActionResult = result == null ? "none" : result.getClass().getSimpleName();
            clickSent = true;
            attempts++;
            session.sent();
            operationInteractions++;
            totalInteractions++;
            clicksAtAccess++;
            // The facts of the interaction are kept: they show that the block came from an
            // ordinary interaction of the player, who stood in the world with the right item.
            interactions.add(new Interaction(target != null ? target.cell() : null,
                    client.player.getBlockPos(), solution.support(), operation.itemId(),
                    heldBefore,
                    reach, solution.access(), targetWorld(), lastActionResult, aimVerdict,
                    preconditionStep));
            publishFacts("the interaction of the attempt was sent");
            LOGGER.debug("placement attempt {} for {} on {}", attempts, operation.itemId(), solution.support());
        }
    }

    /**
     * Closes the window of the game that the own click has opened.
     *
     * <p>The room of the house holds a barrel, a crafting table and a furnace: a click on such a
     * block opens the window of the block, and the world of the single player stands still while
     * the window is open. The dependency is paused in that time, the automation waits in the state
     * busy, and the house does not get any further. The add-on closes the window that its own
     * click has opened and continues with the operation (2026-11-14).
     *
     * @return true when a window was closed in this tick
     */
    private boolean closeOwnScreen() {
        if (client.currentScreen == null) {
            return false;
        }
        if (client.currentScreen instanceof net.minecraft.client.gui.screen.ChatScreen) {
            // The chat is not a window of the game that a click on a block opens.
            return false;
        }
        LOGGER.info("the window {} of the game is closed, the click of the add-on has opened it",
                client.currentScreen.getClass().getSimpleName());
        publishFacts("the window of the game that the click of the add-on opened is closed");
        client.currentScreen.close();
        return true;
    }

    /**
     * Leaves the current cell of the approach for the next one.
     *
     * <p>The rule of the leaving is one of the rules of the core
     * ({@link ArrivalPolicy#mayLeaveAccess}): a cell that already received an interaction is not
     * left, so that it keeps its two sends (ТЗ.md section 4). When no cell of the approach is
     * left, the client remembers it: the next request of the operation answers that there is no
     * admissible way to place it, and the controller suspends the house with the reason
     * no_placement_support and the coordinate of the cell.
     */
    private boolean operationMatchesWorld() {
        if (operation == null || target == null || origin == null || client.world == null) return false;
        for (Cell cell : operation.cells()) {
            BlockState state = client.world.getBlockState(new BlockPos(origin.x() + cell.x(),
                    origin.y() + cell.y(), origin.z() + cell.z()));
            java.util.Map<String, String> required = new java.util.HashMap<>(target.requiredProperties());
            if (operation.placement() == CellSpec.Placement.DOOR) {
                int bottom = operation.cells().stream().mapToInt(Cell::y).min().orElse(cell.y());
                required.put("half", cell.y() == bottom ? "lower" : "upper");
            } else if (operation.placement() == CellSpec.Placement.BED && !cell.equals(target.cell())) {
                required.put("part", "head".equals(required.get("part")) ? "foot" : "head");
            }
            if (!Registries.BLOCK.getId(state.getBlock()).toString().equals(target.blockId())
                    || !matches(state, required)) return false;
        }
        return true;
    }

    private void leaveAccess() {
        collisionRetries = 0;
        lastCollisionX = Double.NaN;
        lastCollisionZ = Double.NaN;
        Solution next = solve(solutionSlot + 1);
        if (next == null) {
            if (target != null && target.cell().y() == HouseTemplate.SIZE_Y - 1 && !scaffolds.isEmpty()) {
                // The room of the house is full of the temporary columns of the climb, and no cell
                // of the approach is free for the cell of the roof. The add-on takes its own
                // columns away and tries the cell again after the next one (ТЗ.md section 4).
                queueSupportCleanup();
            }
            accessExhausted = true;
            solution = null;
            navigation.clearNavigation();
            // The operation waits for a later try: one cell that has no admissible way of
            // approach does not stop the whole house. The automation tries the operation again
            // after the next one, and suspends the house only when the cell fails again and
            // again (ТЗ.md section 4).
            session.update(PlacementSession.Stage.DEFERRED,
                    "approaches exhausted before placement: aim=" + aimVerdict + ", precondition=" + preconditionStep);
            publishFacts("no cell of the approach was reachable ("
                    + (solveRejection == null ? "no_cell_of_the_approach" : solveRejection)
                    + "); the rejected cells: " + rejectedCells()
                    + "; the operation waits for a later try");
            LOGGER.warn("no cell of the approach was reachable for {} at {} ({})", operation.itemId(),
                    target.cell());
            return;
        }
        solution = next;
        session.nextAccess();
        solutionSlot = next.slot();
        arrivalTicks = 0;
        clicksAtAccess = 0;
        stillTicks = 0;
        reachMissTicks = 0;
        lastPosition = null;
        aimSent = false;
        aimTicks = 0;
        navigation.requestNavigation(next.access);
        publishFacts("the next cell of the approach is tried");
    }

    /**
     * True when the player stands in the cell of the approach.
     *
     * <p>The rule lives in the domain core ({@link ArrivalPolicy}); the client uses exactly this
     * rule and keeps no second copy of it. The arrival is exact: the player stands in the cell
     * itself, like the navigation task of the dependency ends.
     */
    private boolean arrived(BlockPos playerPos) {
        if (solution == null || solution.access() == null) {
            return true;
        }
        return ArrivalPolicy.arrived(playerPos.getX(), playerPos.getY(), playerPos.getZ(),
                solution.access().getX(), solution.access().getY(), solution.access().getZ());
    }

    /**
     * True when the interaction of the cell may be sent.
     *
     * <p>The rule lives in the domain core ({@link ArrivalPolicy}): the player stands in the cell
     * of the approach and does not stand in the cell that is to be filled.
     */
    private boolean mayClick(BlockPos playerPos) {
        if (solution == null || solution.access() == null) {
            return false;
        }
        for (Cell cell : operation.cells()) {
            BlockPos position = world(cell);
            if (ArrivalPolicy.occupies(playerPos.getX(), playerPos.getY(), playerPos.getZ(),
                    position.getX(), position.getY(), position.getZ())) {
                return false;
            }
        }
        return true;
    }

    /**
     * True when the player cannot reach the cell of the approach with one step.
     *
     * <p>The cell of the approach is more than one level above the player and there is no cell
     * that the player can step on: the navigation of the dependency does not climb there.
     *
     * @param access the cell of the approach
     * @return true when the climb is blocked
     */
    private boolean climbBlocked(BlockPos access) {
        if (access == null || client.player == null) {
            return false;
        }
        int feet = client.player.getBlockPos().getY();
        if (access.getY() - feet <= 1) {
            // Ordinary one-block steps are navigable in many situations. Do not fill the interior
            // with temporary blocks speculatively; the idle recovery can still handle a real stall.
            return false;
        }
        // For larger height differences consider a temporary support only when no intermediate
        // waypoint is available.
        return navigationWaypoint(access).equals(access);
    }

    /**
     * True when the view of the player is not needed for the click of the operation.
     *
     * <p>The view is needed only in the last step before the click: while the player walks to
     * the cell of the approach, while the world confirms the block and while nothing is built,
     * the view is free for the removal of a temporary support (ТЗ.md section 4).
     *
     * @return true when the view of the player is free
     */
    private boolean viewIsFree() {
        if (operation == null || solution == null || clickSent || confirmed) {
            return true;
        }
        return client.player == null || !arrived(client.player.getBlockPos());
    }

    /**
     * Removes a temporary support of the climb with an ordinary attack of the player.
     *
     * <p>The add-on removes its own support, as soon as the climb over it is done. The rule of
     * the repair does not have to touch it, and the navigation does not have to walk around a
     * block that is not there any more (ТЗ.md section 4, the temporary blocks of the 2026-11-13).
     *
     * @return true when the tick belongs to the removal of the support
     */
    /** Schedule confirmed supports for cleanup without forgetting blocks still in the world. */
    public boolean requestSupportCleanup() {
        queueSupportCleanup();
        return scaffolds.isEmpty() && scaffoldCell == null;
    }

    public String supportCleanupFailure() {
        if (scaffoldWorld != null && client.world != null && client.world != scaffoldWorld
                && (!scaffolds.isEmpty() || scaffoldCell != null)) {
            return "unreconciled temporary supports from a different world or session";
        }
        return scaffoldFailure;
    }

    /**
     * Hands the temporary support of the moment over to the safe repair of the core.
     *
     * <p>The removal of a support needs a line to the support and a player that stands next to
     * it. When the line stays blocked, the player stands still, and the dependency stops a task
     * that stands still for ten seconds (2026-11-14). The support is then left to the safe repair
     * of the core, which breaks a wrong cell of the room with an ordinary attack of the player,
     * and the build of the house goes on (ТЗ.md section 4).
     */
    private void handOverCleanupTarget() {
        releaseOwnedAttack();
        BlockPos position = cleanupTarget != null ? cleanupTarget : scaffoldsToRemove.get(0);
        // Every support of the list is handed over at once: one support after the other would keep
        // the player standing still, and the dependency stops a task that stands still for ten
        // seconds (2026-11-14).
        for (BlockPos pending : new ArrayList<>(scaffoldsToRemove)) {
            scaffoldsToRemove.remove(pending);
            scaffolds.remove(pending);
            scaffoldStates.remove(pending);
        }
        cleanupTarget = null;
        scaffoldRemoveTicks = 0;
        scaffoldMissTicks = 0;
        scaffoldAccessRound = 0;
        scaffoldRemovalAccess = null;
        scaffoldStandTicks = 0;
        supportCleanupActive = false;
        publishFacts("the temporary support cannot be taken away here; the safe repair takes it away");
    }

    private void queueSupportCleanup() {
        BlockPos access = solution == null ? null : solution.access;
        for (BlockPos position : scaffolds) {
            if (access != null && (position.equals(access.down())
                    || position.equals(access.down().down()))) {
                // The column of the operation of the moment stays: the player climbs on it, and a
                // column that is taken away in the middle of the climb puts the player on the
                // floor again (ТЗ.md section 4).
                continue;
            }
            if (scaffoldStates.containsKey(position) && !scaffoldsToRemove.contains(position)) {
                scaffoldsToRemove.add(position);
            }
        }
    }

    private boolean removeScaffoldTick() {        if (scaffoldsToRemove.isEmpty() || client.world == null || client.player == null) {
            return false;
        }
        // A lower step can be hidden by a second support above it. Remove the highest confirmed
        // support first. The supports themselves are only queued when the room of the house is
        // empty again: the player climbs the same column again and again, and a support that is
        // taken away in the middle of the build stands in the way of the next cell of the
        // approach (ТЗ.md section 4).
        scaffoldsToRemove.sort(Comparator.comparingInt(BlockPos::getY).reversed());
        BlockPos position = scaffoldsToRemove.get(0);
        if (!position.equals(cleanupTarget)) {
            releaseOwnedAttack();
            cleanupTarget = position;
            scaffoldRemoveTicks = 0;
            scaffoldAimRound = 0;
            scaffoldMissTicks = 0;
            scaffoldAccessRound = 0;
            scaffoldRemovalAccess = null;
        }
        if (client.world.getBlockState(position).isAir()) {
            // The support is gone: the list of the supports shrinks with it.
            scaffoldsToRemove.remove(0);
            scaffolds.remove(position);
            scaffoldStates.remove(position);
            scaffoldRemoveTicks = 0;
            scaffoldMissTicks = 0;
            scaffoldAccessRound = 0;
            scaffoldRemovalAccess = null;
            releaseOwnedAttack();
            return false;
        }
        BlockState expected = scaffoldStates.get(position);
        if (expected == null || !expected.equals(client.world.getBlockState(position))) {
            releaseOwnedAttack();
            scaffoldFailure = "temporary support changed or ownership unknown at " + position.toShortString();
            return true;
        }
        if (scaffoldRemovalAccess != null && !client.player.getBlockPos().equals(scaffoldRemovalAccess)) {
            releaseOwnedAttack();
            if (++scaffoldAccessWaitTicks > SCAFFOLD_ACCESS_TICKS) {
                // The navigation of the dependency does not reach the cell of the approach: the
                // player would stand still for the ten seconds in which the dependency stops the
                // task (2026-11-14). The support is handed over to the safe repair of the core,
                // which breaks a wrong cell of the room with an ordinary attack of the player,
                // and the build of the house goes on (ТЗ.md section 4).
                scaffoldAccessWaitTicks = 0;
                handOverCleanupTarget();
                return true;
            }
            navigation.requestNavigation(scaffoldRemovalAccess);
            publishFacts("moving to another temporary support approach");
            return true;
        }
        scaffoldAccessWaitTicks = 0;
        if (scaffoldRemovalAccess != null) {
            navigation.clearNavigation();
            scaffoldRemovalAccess = null;
        }
        if (client.player.getBlockPos().down().equals(position)) {
            releaseOwnedAttack();
            BlockPos access = scaffoldAccess(position);
            if (access != null && scaffoldStandTicks < SCAFFOLD_STAND_TICKS) {
                scaffoldStandTicks++;
                navigation.requestNavigation(access);
                publishFacts("move off temporary support before removal");
            } else {
                // The player does not step off the support: it stands on a narrow column, and the
                // navigation of the dependency cannot path it. The support stays where it is, the
                // build goes on, and the room is cleaned up before the furniture stands there
                // (ТЗ.md section 4). The add-on must not wait here: the dependency stops a task
                // that stands still for ten seconds.
                //
                // Every support of the list is handed over at once: one support after the other
                // would keep the player standing on the same column for many seconds, and the
                // dependency would stop the task in the middle of the cleanup (2026-11-14).
                handOverCleanupTarget();
                publishFacts("the player stands on the temporary support; the support stays");
            }
            return true;
        }
        scaffoldStandTicks = 0;
        Vec3d eye = client.player.getEyePos();
        Vec3d centre = new Vec3d(position.getX() + 0.5, position.getY() + 0.5, position.getZ() + 0.5);
        if (!ReachPolicy.mayInteract(eye.x, eye.y, eye.z, centre.x, centre.y, centre.z)) {
            releaseOwnedAttack();
            // The player walks to the support: the cell of the approach is the free cell of the
            // room next to it.
            BlockPos access = scaffoldAccess(position);
            if (access != null) {
                navigation.requestNavigation(access);
            }
            publishFacts("the player walks to the temporary support");
            return true;
        }
        // The budget counts the time of the destruction itself: the walk to the support is not a
        // failure of the support (ТЗ.md section 4).
        if (++scaffoldRemoveTicks > SCAFFOLD_REMOVE_TICKS) {
            releaseOwnedAttack();
            // The support cannot be broken from where the player stands: the walls of the house
            // stand between the player and it. The support is handed over to the safe repair of
            // the core, which breaks a wrong cell of the room with an ordinary attack of the
            // player, and the build goes on. A support that cannot be seen must not suspend the
            // house (ТЗ.md section 4).
            scaffoldsToRemove.remove(position);
            scaffolds.remove(position);
            scaffoldStates.remove(position);
            scaffoldRemoveTicks = 0;
            scaffoldMissTicks = 0;
            scaffoldAccessRound = 0;
            scaffoldRemovalAccess = null;
            publishFacts("the temporary support cannot be seen; the safe repair takes it away");
            return true;
        }
        // Stop the existing navigation child before aiming; otherwise its walker and our raycast
        // fight for player movement and yaw throughout the break attempt.
        navigation.clearNavigation();
        // The view is turned to the support and the key of the attack is held: the destruction
        // follows only when the game itself names the block under the crosshair, and the client
        // of the game cancels the destruction in every tick in which the key is not pressed.
        // The point of the click: the upper face of the support when the player stands clearly
        // above it, otherwise the side face that faces the player. The ray to the upper face of a
        // block that stands on the same level as the player grazes the edge of the block, and the
        // game then names the neighbour of the support (ТЗ.md section 4).
        double aimX = centre.x;
        double aimY = centre.y;
        double aimZ = centre.z;
        if (eye.y > position.getY() + 1.6) {
            // The player stands above the support: the upper face is the point of the click.
            aimY = position.getY() + 1.0;
        } else {
            // The player stands on the level of the support: the four side faces are tried one
            // after the other, and the face that faces the player is the first one.
            switch (scaffoldAimRound % 4) {
                case 0 -> aimX = position.getX() + 1.0;
                case 1 -> aimX = position.getX();
                case 2 -> aimZ = position.getZ() + 1.0;
                default -> aimZ = position.getZ();
            }
        }
        aimAt(new Vec3d(aimX, aimY, aimZ));
        if (client.interactionManager != null
                && client.crosshairTarget instanceof BlockHitResult hit
                && hit.getBlockPos().equals(position)) {
            client.options.attackKey.setPressed(true);
            ownedAttackKey = true;
            scaffoldMissTicks = 0;
            boolean destroying = client.interactionManager.updateBlockBreakingProgress(position, hit.getSide());
            if (destroying) {
                client.player.swingHand(Hand.MAIN_HAND);
                scaffoldEvents.add("scaffold_removed:" + position.toShortString());
            }
        } else {
            // Never keep attacking while the actual crosshair names a different block.
            releaseOwnedAttack();
            scaffoldAimRound++;
            if (++scaffoldMissTicks >= 16) {
                scaffoldMissTicks = 0;
                scaffoldAccessRound++;
                BlockPos alternative = scaffoldAccess(position);
                if (alternative != null && !alternative.equals(client.player.getBlockPos())) {
                    scaffoldRemovalAccess = alternative;
                } else {
                    // There is no place from which the player sees the support without a block of
                    // the house in between. The support stays where it is, the build goes on, and
                    // the room is cleaned up before the furniture stands there (ТЗ.md section 4).
                    scaffoldsToRemove.remove(position);
                    scaffoldRemoveTicks = 0;
                    publishFacts("the line to the temporary support is blocked; the removal waits");
                }
            }
        }
        publishFacts("the temporary support is removed with an ordinary attack");
        return true;
    }

    /**
     * The cell of the approach from which the temporary support is broken.
     *
     * @param position the position of the support
     * @return the cell where the player stands, or null when there is none
     */
    private BlockPos scaffoldAccess(BlockPos position) {
        Vec3d centre = new Vec3d(position.getX() + 0.5, position.getY() + 0.5, position.getZ() + 0.5);
        List<BlockPos> candidates = new ArrayList<>();
        for (Direction direction : Direction.Type.HORIZONTAL) {
            BlockPos candidate = position.offset(direction);
            if (!standable(candidate)) continue;
            Vec3d eye = new Vec3d(candidate.getX() + 0.5, candidate.getY() + EYE_HEIGHT,
                    candidate.getZ() + 0.5);
            if (ReachPolicy.mayInteract(eye.x, eye.y, eye.z, centre.x, centre.y, centre.z)) {
                candidates.add(candidate);
            }
        }
        return candidates.isEmpty() ? null : candidates.get(scaffoldAccessRound % candidates.size());
    }

    /**
     * Moves the player a little away from the cell that is to be filled.
     *
     * <p>The box of the player is wider than the gap between two cells, so the player who
     * stands exactly in the cell of the approach reaches with his body into the cell of the
     * project, and the game refuses the placement. The add-on moves the player a fraction of a
     * cell away from that cell, with the ordinary key of the movement of the game, and the view
     * is turned again afterwards (ТЗ.md section 4).
     */
    private void nudgeAway() {
        if (client.player == null || target == null || origin == null || client.world == null) {
            return;
        }
        nudgeAway(world(target.cell()));
    }

    /**
     * Moves the player a little away from the given cell.
     *
     * @param avoidPos the cell that the body of the player must not reach into
     */
    private void nudgeAway(BlockPos avoidPos) {
        if (client.player == null || origin == null || client.world == null) {
            return;
        }
        BlockPos targetPos = avoidPos;
        double dx = client.player.getX() - (targetPos.getX() + 0.5);
        double dz = client.player.getZ() - (targetPos.getZ() + 0.5);
        // The step to the side must not throw the player off a narrow ledge: the cell of the step
        // has to be free and to have a solid block below it. On the top of a wall the ledge is one
        // block wide, and a step into the void drops the player from the house (the chain of the
        // fall of the dependency takes the task away then). The directions are tried in the order
        // of the distance to the cell that is filled, and the first safe one is taken.
        List<Direction> directions = new ArrayList<>();
        if (Math.abs(dx) >= Math.abs(dz)) {
            directions.add(dx >= 0 ? Direction.EAST : Direction.WEST);
            directions.add(dz >= 0 ? Direction.SOUTH : Direction.NORTH);
        } else {
            directions.add(dz >= 0 ? Direction.SOUTH : Direction.NORTH);
            directions.add(dx >= 0 ? Direction.EAST : Direction.WEST);
        }
        BlockPos playerPos = client.player.getBlockPos();
        for (Direction direction : directions) {
            BlockPos cell = playerPos.offset(direction);
            if (cell.equals(targetPos)) {
                // The cell that is to be filled: the player must not step into it.
                continue;
            }
            if (!standable(cell)) {
                continue;
            }
            nudgeDirection = direction;
            nudgeTicks = NUDGE_TICKS;
            return;
        }
        // No safe cell to step to: the player stays where it is and the cell of the approach is
        // changed instead. A step into the void would drop the player from the wall.
        nudgeDirection = null;
        nudgeTicks = 0;
    }

    /**
     * Holds the key of the movement while the player steps aside.
     *
     * <p>The key is released at the end of the step: the add-on does not hold a movement of its
     * own while it waits or after it has stopped (ТЗ.md section 4).
     */
    private void releaseOwnedAttack() {
        if (ownedAttackKey) {
            if (client.options != null) client.options.attackKey.setPressed(false);
            if (client.interactionManager != null) client.interactionManager.cancelBlockBreaking();
            ownedAttackKey = false;
        }
    }

    /** Release only inputs acquired by this executor when the world is not available. */
    public void releaseControls() {
        releaseOwnedKeys();
    }

    private void releaseOwnedKeys() {
        releaseOwnedAttack();
        if (ownedForwardKey && client.options != null) client.options.forwardKey.setPressed(false);
        ownedForwardKey = false;
        nudgeTicks = 0;
        nudgeDirection = null;
    }

    private void applyNudge() {
        if (client.player == null || client.options == null || client.options.forwardKey == null) {
            nudgeTicks = 0;
            return;
        }
        if (nudgeDirection != null) {
            float yaw = switch (nudgeDirection) {
                case NORTH -> 180.0f;
                case SOUTH -> 0.0f;
                case WEST -> 90.0f;
                default -> -90.0f;
            };
            client.player.setYaw(yaw);
        }
        client.options.forwardKey.setPressed(true);
        ownedForwardKey = true;
        nudgeTicks--;
        if (nudgeTicks <= 0) {
            client.options.forwardKey.setPressed(false);
            ownedForwardKey = false;
            nudgeDirection = null;
            // The view is turned again before the next click: the step changed the direction.
            aimSent = false;
        }
    }

    /**
     * The names of the cells of the approach that were rejected and the reasons of the rejection.
     *
     * <p>The list is the evidence of the search: it shows which cell of the approach was not
     * suitable and why (no support, not standable, out of reach, another cell, no direction), so
     * that the reason of a suspended house is not a guess (ТЗ.md section 4).
     *
     * @return the names of the rejected cells and their reasons
     */
    private String rejectedCells() {
        List<String> rejected = solveRejections;
        if (rejected == null || rejected.isEmpty()) {
            return "none";
        }
        return String.join(", ", rejected);
    }

    /**
     * The place where the navigation has to bring the player.
     *
     * <p>The player climbs one block at a time. When the cell of the approach is more than one
     * level above the player, the navigation is asked for a cell below it first: the player
     * climbs there and goes on from that place. The intermediate cell is a waypoint of the
     * navigation and not a cell of the approach, so it does not use up the budget of the
     * approach (ТЗ.md section 4).
     *
     * @param access the cell of the approach
     * @return the cell that the navigation has to reach first
     */
    private BlockPos navigationWaypoint(BlockPos access) {
        if (access == null || client.player == null) {
            return access;
        }
        BlockPos playerPos = client.player.getBlockPos();
        int feet = playerPos.getY();
        // The walk is broken into short legs along the house. The pathfinder of the dependency
        // searches a wide area when the goal is far away or has to be reached the long way round,
        // and the player walks away from the house while the search runs (2026-11-13).
        if (origin != null && insideVolume(access)
                && playerPos.getY() >= origin.y() + HouseTemplate.SIZE_Y - 1
                && access.getY() <= playerPos.getY() - 1) {
            // The player stands on the roof of the house and the cell of the approach lies inside
            // it: the walk goes to the point in front of the house first, because the doorway is
            // the only way into the house. The goal of the dependency is a short leg along the
            // house, and the search of the path stays in the neighbourhood of the house.
            BlockPos front = frontPointWorld();
            if (front != null) {
                BlockPos leg = walkLeg(playerPos, front);
                if (leg != null) {
                    return leg;
                }
            }
        }
        if (access.getY() - feet <= 1) {
            return access;
        }
        // The cell below the cell of the approach: the player climbs to it and goes on from there.
        BlockPos below = access.down();
        if (below.getY() == feet + 1 && standable(below)) {
            return below;
        }
        // The step of the climb: a cell that is one level above the player and that the player
        // can stand on. The nearest of them is taken, so that the climb does not wander.
        List<BlockPos> steps = new ArrayList<>();
        for (Direction direction : Direction.Type.HORIZONTAL) {
            BlockPos step = playerPos.up().offset(direction);
            if (standable(step)) {
                steps.add(step);
            }
        }
        if (steps.isEmpty()) {
            return access;
        }
        steps.sort(Comparator.comparingDouble(step -> step.getSquaredDistance(access.getX(), access.getY(),
                access.getZ())));
        return steps.get(0);
    }

    /**
     * The next cell of the walk towards the goal of the walk.
     *
     * <p>The goal of the dependency is never farther than {@link #MAX_WALK_LEG} blocks: the
     * pathfinder of the dependency then searches only the neighbourhood of the house and the
     * player does not walk away from the house while the search runs. When there is no cell to
     * step on, the goal itself is returned and the dependency decides (2026-11-13).
     *
     * @param playerPos the position of the player
     * @param goal      the cell of the walk
     * @return the cell of the next leg, the goal itself when it is near, or null
     */
    private BlockPos walkLeg(BlockPos playerPos, BlockPos goal) {
        int dx = goal.getX() - playerPos.getX();
        int dz = goal.getZ() - playerPos.getZ();
        if (Math.abs(dx) <= MAX_WALK_LEG && Math.abs(dz) <= MAX_WALK_LEG) {
            return goal;
        }
        List<BlockPos> candidates = new ArrayList<>();
        int stepX = Integer.signum(dx);
        int stepZ = Integer.signum(dz);
        if (stepX != 0 && stepZ != 0) {
            candidates.add(playerPos.add(stepX, 0, stepZ));
        }
        if (stepX != 0) {
            candidates.add(playerPos.add(stepX, 0, 0));
        }
        if (stepZ != 0) {
            candidates.add(playerPos.add(0, 0, stepZ));
        }
        for (BlockPos candidate : candidates) {
            if (standable(candidate)) {
                return candidate;
            }
        }
        return null;
    }

    /** The horizontal distance between two positions. */
    private static int horizontalDistance(BlockPos first, BlockPos second) {
        return Math.max(Math.abs(first.getX() - second.getX()), Math.abs(first.getZ() - second.getZ()));
    }

    /**
     * The goal of the navigation, broken into a short leg along the house.
     *
     * <p>The pathfinder of the dependency must not search the world around for a goal that is far
     * away: the goal of every request is at most {@link #MAX_WALK_LEG} blocks from the player
     * (2026-11-13).
     *
     * @param goal the cell of the walk
     * @return the cell of the next leg or the goal itself
     */
    private BlockPos walkLegTo(BlockPos goal) {
        if (goal == null || client.player == null) {
            return goal;
        }
        BlockPos leg = walkLeg(client.player.getBlockPos(), goal);
        return leg == null ? goal : leg;
    }

    /** The point in front of the house, in the world. */
    private BlockPos frontPointWorld() {
        if (origin == null) {
            return null;
        }
        dev.magehome.core.model.Vec3i front = dev.magehome.core.sites.SiteRules.frontPoint(origin);
        return new BlockPos(front.x(), front.y(), front.z());
    }

    /**
     * Holds the cell of the roof open and lets the player walk down through it.
     *
     * <p>The roof of the house is closed, and the player who built it stands on it: the only way
     * into the house is the doorway, which stands three levels below. The add-on keeps the last
     * cell of the roof open, the player walks down through it, and the cell is built from inside
     * afterwards — the block of the roof is placed on the side face of the block next to it
     * (ТЗ.md section 2, the owner of 2026-11-13).
     *
     * @param cell the cell of the project that is held open
     */
    public void requestDescent(dev.magehome.core.model.Cell cell) {
        if (origin == null || cell == null) {
            return;
        }
        BlockPos position = world(cell);
        if (!position.equals(descentHold)) {
            descentHold = position;
            LOGGER.info("the cell {} of the roof is held open for the descent of the player",
                    position.toShortString());
        }
    }

    /** True while the add-on holds the cell of the roof open. */
    public boolean descentHeld(dev.magehome.core.model.Cell cell) {
        return descentHold != null && origin != null && descentHold.equals(world(cell));
    }

    /**
     * The descent of the player from the roof of the house.
     *
     * @return true when the tick belongs to the descent
     */
    private boolean descentTick() {
        BlockPos hold = descentHold;
        if (hold == null || client.player == null || origin == null) {
            return false;
        }
        BlockPos playerPos = client.player.getBlockPos();
        if (playerPos.getY() <= origin.y() + 1) {
            // The player is on the floor of the house: the cell of the roof is built from inside
            // now. The supports of the climb stay until the room of the house is empty again
            // (ТЗ.md section 4).
            descentHold = null;
            LOGGER.info("the player is inside the house; the cell {} of the roof is built now",
                    hold.toShortString());
            return false;
        }
        BlockPos goal = hold;
        if (playerPos.getY() <= origin.y() + HouseTemplate.SIZE_Y - 1) {
            // The player is in the open cell of the roof: it walks down the supports of the climb
            // one block at a time, and does not fall into the room — the chain of the fall of the
            // dependency takes the task away when the player falls (2026-11-13).
            BlockPos step = descentStepTarget();
            if (step != null) {
                goal = step;
            }
        }
        // The player walks to the open cell and steps down through it. The goal of the navigation
        // is the cell itself: it is a short walk along the roof.
        navigation.requestNavigation(goal);
        publishFacts("the player walks down through the open cell of the roof");
        return true;
    }

    /**
     * The topmost support of the climb: the player walks down the chain of them.
     *
     * @return the cell that the player stands on, or null when there is no support
     */
    private BlockPos descentStepTarget() {
        BlockPos top = null;
        for (BlockPos support : scaffolds) {
            if (top == null || support.getY() > top.getY()) {
                top = support;
            }
        }
        return top == null ? null : top.up();
    }

    /** True when every cell of the roof of the house is built. */
    private boolean roofComplete() {
        if (origin == null || client.world == null) {
            return true;
        }
        int roofLevel = HouseTemplate.SIZE_Y - 1;
        for (dev.magehome.core.model.Cell cell : HouseTemplate.forVariant(0).volumeCells()) {
            if (cell.y() != roofLevel) {
                continue;
            }
            if (client.world.getBlockState(world(cell)).isAir()) {
                return false;
            }
        }
        return true;
    }

    /**
     * Places a temporary support for the climb when the player cannot step up.
     *
     * <p>The navigation of the dependency does not climb in every place: it reports that the wall
     * is too high and builds a block under its own feet, and the player stands still while that
     * happens. The owner allowed the temporary blocks on 2026-11-13 (ТЗ.md section 4), so the
     * add-on builds the step itself: an ordinary interaction puts a block next to the player, and
     * the player steps on it and goes on.
     *
     * <p>The block is not a part of the project: it stands inside the volume of the house, where
     * the rule of the repair takes it away again after the climb (ТЗ.md section 4).
     *
     * @return true when the tick belongs to the temporary support
     */
    private boolean scaffoldStep() {
        if (scaffoldCell == null) {
            return false;
        }
        if (!client.world.getBlockState(scaffoldCell).isAir()) {
            BlockState observed = client.world.getBlockState(scaffoldCell);
            // A mismatched block is not proof of ownership. Never remove it as a support.
            if (!scaffoldClicked || expectedScaffoldBlock == null || observed.getBlock() != expectedScaffoldBlock) {
                // A foreign block stands in the cell of the temporary support: the player can
                // stand on it as well. The add-on does not break a block that is not its own, but
                // it keeps the cell under the protection of the rule of the repair: the block of
                // the dependency serves as the temporary support while the player climbs on it,
                // and the rule of the repair would take it away otherwise (ТЗ.md section 4).
                BlockPos foreign = scaffoldCell;
                scaffolds.remove(foreign);
                reservedSupports.remove(foreign);
                if (!foreignSupports.contains(foreign)) {
                    foreignSupports.add(foreign);
                }
                scaffoldCell = null;
                scaffoldClicked = false;
                scaffoldSent = false;
                scaffoldSupport = null;
                scaffoldHit = null;
                scaffoldWaitTicks = 0;
                publishFacts("a foreign block serves as the temporary support at "
                        + foreign.toShortString());
                return true;
            }
            if (!scaffolds.contains(scaffoldCell)) scaffolds.add(scaffoldCell);
            scaffoldStates.put(scaffoldCell, observed);
            scaffoldEvents.add("scaffold_confirmed:" + scaffoldCell.toShortString());
            // The temporary support is a block of the inventory of the player: it leaves the
            // inventory when the world confirms it, and the core has to know this to tell the
            // delivery of the server apart from the spending of the add-on (ТЗ.md section 4).
            countConsumed(scaffoldItemId);
            scaffoldCell = null;
            scaffoldSupport = null;
            scaffoldHit = null;
            scaffoldSent = false;
            scaffoldClicked = false;
            scaffoldWaitTicks = 0;
            return false;
        }
        if (++scaffoldWaitTicks > SCAFFOLD_WAIT_TICKS) {
            BlockPos failedCell = scaffoldCell;
            // No confirmed block was observed. A later foreign block must not inherit ownership.
            scaffolds.remove(failedCell);
            scaffoldCell = null;
            scaffoldSupport = null;
            scaffoldHit = null;
            scaffoldSent = false;
            scaffoldClicked = false;
            scaffoldWaitTicks = 0;
            publishFacts("temporary support not confirmed at " + failedCell.toShortString());
            leaveAccess();
            return true;
        }
        if (scaffoldClicked) {
            publishFacts("waiting for temporary support confirmation");
            return true;
        }
        if (!ensureItemInMainHand()) {
            session.holdPreparation();
            publishFacts("the item for the temporary support is not in the main hand");
            return true;
        }
        if (!scaffoldSent) {
            aimAt(scaffoldHit);
            scaffoldSent = true;
            publishFacts("the view is turned to the place of the temporary support");
            return true;
        }
        // Only the game's actual raycast may authorize the click; never fabricate a hit.
        if (!(client.crosshairTarget instanceof BlockHitResult hit)
                || !hit.getBlockPos().equals(scaffoldSupport) || hit.getSide() != scaffoldFace
                || !hit.getBlockPos().offset(hit.getSide()).equals(scaffoldCell)
                || !ReachPolicy.mayInteract(client.player.getEyePos().x, client.player.getEyePos().y,
                        client.player.getEyePos().z, hit.getPos().x, hit.getPos().y, hit.getPos().z)
                || client.player.getMainHandStack().getItem() != item) {
            aimAt(scaffoldHit);
            publishFacts("waiting for a valid temporary support raycast and item");
            return true;
        }
        if (!(item instanceof BlockItem blockItem)
                || !blockItem.getBlock().getDefaultState().isFullCube(client.world, scaffoldCell)
                || !client.world.canPlace(blockItem.getBlock().getDefaultState(), scaffoldCell,
                        net.minecraft.block.ShapeContext.of(client.player))) {
            publishFacts("temporary support item or collision is unsafe");
            return true;
        }
        if (client.interactionManager != null && client.player != null && scaffoldSupport != null) {
            expectedScaffoldBlock = blockItem.getBlock();
            // The support is a block of the inventory of the player: the id of the item is kept,
            // so that the spending can be counted when the world confirms the support.
            scaffoldItemId = Registries.ITEM.getId(item).toString();
            scaffoldWorld = client.world;
            client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hit);
            scaffoldClicked = true;
            scaffoldWaitTicks = 0;
            // Reserve the pending cell against repair until the server confirms or rejects it.
            if (!scaffolds.contains(scaffoldCell)) scaffolds.add(scaffoldCell);
            scaffoldEvents.add("scaffold_sent:" + scaffoldCell.toShortString());
        }
        publishFacts("temporary support interaction sent; awaiting world confirmation");
        return true;
    }

    /**
     * Chooses the place of the temporary support and prepares the click.
     *
     * <p>The support stands next to the player, on the level of the player, so that the player
     * steps on it. The cell is inside the volume of the house, where the rule of the repair takes
     * the block away again.
     *
     * @return true when a temporary support is prepared
     */
    private boolean prepareScaffold() {
        if (client.player == null || client.world == null) {
            return false;
        }
        BlockPos playerPos = client.player.getBlockPos();
        for (Direction direction : Direction.Type.HORIZONTAL) {
            BlockPos cell = playerPos.offset(direction);
            if (!client.world.getBlockState(cell).isAir()) {
                continue;
            }
            BlockPos support = cell.down();
            if (!solid(support)) {
                continue;
            }
            if (!insideVolume(cell)) {
                // Only the blocks inside the volume of the house are used: the rule of the repair
                // takes them away again, and nothing stands around the house afterwards.
                continue;
            }
            if (!insideRoom(cell)) {
                // Only the cells that the project keeps empty are used: a support in the wall or
                // in the roof would stand in the way of the house itself.
                continue;
            }
            if (onRing(cell)) {
                // The ring of the house belongs to the project: a support there would stand in
                // the way of a wall and the rule of the repair would fight the add-on for it.
                continue;
            }
            if (projectCell.test(new Vec3i(cell.getX(), cell.getY(), cell.getZ()))) {
                // The core knows the cells of the project: a support in a cell of the project
                // would stand in the way of a wall, of a piece of furniture or of the roof, and
                // the rule of the repair would fight the add-on for that cell (ТЗ.md section 4).
                continue;
            }
            scaffoldCell = cell;
            scaffoldSupport = support;
            scaffoldFace = Direction.UP;
            scaffoldHit = new Vec3d(support.getX() + 0.5, support.getY() + 1.0, support.getZ() + 0.5);
            scaffoldSent = false;
            if (!reservedSupports.contains(cell)) {
                reservedSupports.add(cell);
            }
            return true;
        }
        return false;
    }

    /**
     * Builds the temporary column under the cell of the approach.
     *
     * <p>The cell of the approach has no floor of its own: the add-on builds the column under it and
     * the player climbs it. The blocks stand inside the room of the house, where the rule of the
     * repair takes them away again, and the add-on removes its own blocks when the climb is over
     * (ТЗ.md section 4, the temporary blocks of the 2026-11-13).
     *
     * @param access the cell of the approach
     * @return true when the tick belongs to the temporary column
     */
    private boolean towerStep(BlockPos access) {
        if (scaffoldCell != null) {
            // A temporary block is already on its way: the tick belongs to it.
            return false;
        }
        if (access == null || standable(access) || !supportable(access)) {
            return false;
        }
        BlockPos cell = nextTowerBlock(access);
        if (cell == null) {
            return false;
        }
        if (!(item instanceof BlockItem blockItem)
                || !blockItem.getBlock().getDefaultState().isFullCube(client.world, cell)) {
            // The material of the operation cannot carry a temporary block: a door or a bed is no
            // column for the player. The add-on leaves the cell to the next approach.
            return false;
        }
        BlockPos support = cell.down();
        if (scaffoldsToRemove.contains(cell)) {
            // The cell is on its way out, but the climb of this operation needs it: the add-on
            // keeps the column and cancels the removal (ТЗ.md section 4).
            scaffoldsToRemove.remove(cell);
            if (cell.equals(cleanupTarget)) {
                releaseOwnedAttack();
                cleanupTarget = null;
                scaffoldRemoveTicks = 0;
                scaffoldMissTicks = 0;
                scaffoldAccessRound = 0;
                scaffoldRemovalAccess = null;
            }
        }
        if (client.player != null && client.player.getBlockPos().equals(cell)) {
            // The player stands in the cell of the column: the game refuses the placement of a
            // block into the body of the player. The add-on moves the player a little to the side
            // and builds the column on the next tick (ТЗ.md section 4).
            nudgeAway(cell);
            if (nudgeDirection == null) {
                // The player cannot step aside: the cell is narrow, and the column is not built
                // here. The cell of the approach is left for the next one — the add-on must not
                // keep the player still in a cell that it cannot leave, because the dependency
                // stops a task that stands still for ten seconds (2026-11-14).
                publishFacts("the player cannot step aside; the cell of the approach is left");
                leaveAccess();
                return true;
            }
            publishFacts("the player stands in the cell of the temporary support; the player steps aside");
            return true;
        }
        scaffoldCell = cell;
        scaffoldSupport = support;
        scaffoldFace = Direction.UP;
        scaffoldHit = new Vec3d(support.getX() + 0.5, support.getY() + 1.0, support.getZ() + 0.5);
        scaffoldSent = false;
        scaffoldClicked = false;
        scaffoldWaitTicks = 0;
        scaffoldFailure = null;
        if (!reservedSupports.contains(cell)) {
            reservedSupports.add(cell);
        }
        publishFacts("the temporary support under the cell of the approach is prepared");
        return true;
    }

    /**
     * True when the operation of the moment stands in the room of the house.
     *
     * <p>The floor, the walls and the roof stand on the border of the house, and the furniture
     * stands in the room: the temporary columns of the climb stand in the room as well, and the
     * add-on takes its own blocks away before it puts the furniture into the room (ТЗ.md section 4).
     *
     * @return true when the operation stands in the room of the house
     */
    private boolean furnitureAhead() {
        if (target == null || origin == null) {
            return false;
        }
        Cell cell = target.cell();
        int x = cell.x() - origin.x();
        int y = cell.y() - origin.y();
        int z = cell.z() - origin.z();
        boolean onBorder = x == 0 || x == HouseTemplate.SIZE_X - 1
                || z == 0 || z == HouseTemplate.SIZE_Z - 1;
        return !onBorder && y >= 1;
    }

    /** True when the position lies inside the volume of the house. */
    private boolean insideVolume(BlockPos position) {
        if (origin == null) {
            return false;
        }
        int x = position.getX() - origin.x();
        int y = position.getY() - origin.y();
        int z = position.getZ() - origin.z();
        return x >= 0 && x < HouseTemplate.SIZE_X && y >= 0 && y < HouseTemplate.SIZE_Y
                && z >= 0 && z < HouseTemplate.SIZE_Z;
    }

    /**
     * True when the position lies inside the room of the house.
     *
     * <p>The room is the part of the volume that the project keeps empty: the walls and the roof
     * stand at its border. A temporary support stands in the room, so that it does not stand in
     * the way of the house itself and the rule of the repair takes it away again (ТЗ.md section 4).
     *
     * @param position the position in the world
     * @return true when the cell is inside the room
     */
    private boolean insideRoom(BlockPos position) {
        if (origin == null) {
            return false;
        }
        int x = position.getX() - origin.x();
        int z = position.getZ() - origin.z();
        return x >= 1 && x <= HouseTemplate.SIZE_X - 2 && z >= 1 && z <= HouseTemplate.SIZE_Z - 2;
    }

    /**
     * True when the position lies on the ring of the house.
     *
     * <p>The ring is the wall of the house and it belongs to the project: a temporary support
     * never stands there (ТЗ.md section 4).
     *
     * @param position the position in the world
     * @return true when the cell is on the ring
     */
    private boolean onRing(BlockPos position) {
        if (origin == null) {
            return false;
        }
        int x = position.getX() - origin.x();
        int z = position.getZ() - origin.z();
        return x == 0 || x == HouseTemplate.SIZE_X - 1 || z == 0 || z == HouseTemplate.SIZE_Z - 1;
    }

    /** The cell of the project that is filled, in the world. */
    private BlockPos targetWorld() {
        if (target == null || origin == null) {
            return null;
        }
        return world(target.cell());
    }

    /**
     * True when the point of the click is inside the ordinary range of the player.
     *
     * <p>The rule lives in the domain core ({@link ReachPolicy}); the client uses exactly this
     * rule, so that the interaction is never sent outside the range that the server accepts.
     */
    private boolean insideReach() {
        if (solution == null) {
            return false;
        }
        Vec3d eye = client.player.getEyePos();
        return ReachPolicy.mayInteract(eye.x, eye.y, eye.z,
                solution.hit().x, solution.hit().y, solution.hit().z);
    }

    /**
     * Asks for the removal of the wrong block of a cell of the volume of a registered house.
     *
     * <p>The player walks to the cell, turns the view to the block and breaks it with an
     * ordinary attack. The cell is read again afterwards and the building continues when the
     * block is gone (ТЗ.md section 4). Only the block of the requested cell is touched.
     *
     * @param position the position of the wrong block in the world
     * @return false when the client cannot start the operation
     */
    public boolean requestRepair(Vec3i position) {
        if (client.world == null || client.player == null) {
            return false;
        }
        BlockPos pos = new BlockPos(position.x(), position.y(), position.z());
        if (client.world.getBlockState(pos).isAir()) {
            // The block is already gone: the cell is read again by the automation.
            repairEvents.add("repair_nothing_to_remove:" + pos.toShortString());
            return true;
        }
        if (scaffoldStates.containsKey(pos) || scaffolds.contains(pos)
                || reservedSupports.contains(pos)) {
            // The block is a temporary support of the add-on itself. The rule of the repair of the
            // core skips these cells, and the add-on builds the support again when it needs it
            // (ТЗ.md section 4).
            repairEvents.add("repair_own_support_kept:" + pos.toShortString());
            publishFacts("the wrong block is a temporary support of the add-on");
            return true;
        }
        this.repairTarget = pos;
        this.repairAccess = null;
        this.repairAccessRound = 0;
        this.repairWaitTicks = 0;
        this.repairAimTicks = 0;
        this.repairAttacks = 0;
        this.repairBreakStarted = false;
        this.repairProgress = -1;
        repairEvents.add("repair_requested:" + pos.toShortString());
        LOGGER.info("the wrong block at {} is removed with an ordinary attack of the player", pos);
        return true;
    }

    /** The facts of the repair of the wrong block; the checks of the scenario read them. */
    public List<String> repairEvents() {
        return List.copyOf(repairEvents);
    }

    /** The number of the attacks that were sent for the wrong block. */
    public int repairAttacks() {
        return repairAttacks;
    }

    /**
     * Breaks the wrong block of the cell with an ordinary attack of the player.
     *
     * <p>The player walks to the cell of the approach, the view is turned to the block and the
     * ordinary destruction of the game is used: the client starts the destruction of the block
     * and keeps its progress alive in every tick, until the game itself reports that the block
     * is gone. The cell is read again in every tick; when the block is gone, the repair is
     * finished and the automation continues.
     *
     * <p>The protocol is the one of the vanilla client
     * ({@code MinecraftClient.handleBlockBreaking}): {@code updateBlockBreakingProgress} starts
     * the destruction of the block (the packet that starts it), advances the progress of the
     * destruction with the speed of the item in the hand of the player and sends the packet that
     * finishes it when the progress reaches the whole block. A repeated
     * {@code attackBlock} on the same block sends nothing at all: the game keeps the state of
     * the destruction itself.
     */
    private void repairTick() {
        BlockPos position = repairTarget;
        if (position == null) {
            return;
        }
        if (client.world.getBlockState(position).isAir()) {
            // The wrong block is gone: the cell is read again and the building continues.
            repairEvents.add("repair_finished:" + position.toShortString());
            LOGGER.info("the wrong block at {} is removed", position);
            publishFacts("the wrong block is removed");
            repairTarget = null;
            repairAccess = null;
            repairAimTicks = 0;
            repairBreakStarted = false;
            repairProgress = -1;
            if (client.interactionManager != null) {
                client.interactionManager.cancelBlockBreaking();
            }
            releaseOwnedAttack();
            return;
        }
        BlockPos access = repairAccess();
        if (access == null) {
            releaseOwnedAttack();
            publishFacts("no cell of the approach reaches the wrong block");
            return;
        }
        if (!client.player.getBlockPos().equals(access)) {
            releaseOwnedAttack();
            navigation.requestNavigation(access);
            repairWaitTicks++;
            if (repairWaitTicks > REPAIR_ACCESS_TICKS) {
                // The player did not reach this cell of the approach: the next one is tried. The
                // repair does not wait for ever at one place (ТЗ.md section 4).
                repairWaitTicks = 0;
                repairAccess = null;
                repairAccessRound++;
                repairAimTicks = 0;
                repairBreakStarted = false;
                repairProgress = -1;
                publishFacts("the player walks to the wrong block (the next cell of the approach)");
                return;
            }
            publishFacts("the player walks to the wrong block");
            return;
        }
        repairWaitTicks = 0;
        navigation.clearNavigation();
        // The view is turned to the block in every tick: the direction reaches the server with the
        // next movement packet, and the destruction follows only when the game itself names the
        // block under the crosshair.
        aimAtRepairTarget(position);
        repairAimTicks++;
        if (repairAimTicks < 2) {
            publishFacts("the view is turned to the wrong block");
            return;
        }
        if (!(client.crosshairTarget instanceof BlockHitResult hit) || !hit.getBlockPos().equals(position)) {
            releaseOwnedAttack();
            // The view does not name the block yet: the next point of the cube is tried, and the
            // next cell of the approach after that. The repair must not wait for ever at one
            // place (ТЗ.md section 4).
            if (repairAimTicks > 20) {
                repairAimTicks = 0;
                repairAimRound++;
                if (repairAimRound > 6) {
                    repairAimRound = 0;
                    repairAccess = null;
                    repairAccessRound++;
                    repairBreakStarted = false;
                    repairProgress = -1;
                    publishFacts("the player walks to the wrong block (the next cell of the approach)");
                    return;
                }
            }
            publishFacts("the view does not name the wrong block");
            return;
        }
        if (client.interactionManager == null) {
            return;
        }
        // The key of the attack is held while the destruction runs. The client of the game cancels
        // the destruction in every tick in which the key is not pressed
        // (MinecraftClient.handleBlockBreaking), and the block would never be gone.
        if (client.options != null && client.options.attackKey != null) {
            client.options.attackKey.setPressed(true);
            ownedAttackKey = true;
        }
        String id = Registries.BLOCK.getId(client.world.getBlockState(position).getBlock()).toString();
        // The ordinary destruction of the game: the first call starts it, the next calls keep the
        // progress of the destruction alive until the block is gone.
        boolean destroying = client.interactionManager.updateBlockBreakingProgress(position, hit.getSide());
        int progress = client.interactionManager.getBlockBreakingProgress();
        if (destroying) {
            client.player.swingHand(Hand.MAIN_HAND);
            if (!repairBreakStarted) {
                // One attack per attempt: the game starts the destruction of the block once and
                // keeps it alive, the add-on does not send a second attack for the same block.
                repairBreakStarted = true;
                repairAttacks++;
                repairEvents.add("repair_attack:" + position.toShortString());
                LOGGER.info("the wrong block {} at {} is broken with an ordinary attack of the player",
                        id, position);
            }
        }
        if (progress != repairProgress) {
            repairProgress = progress;
            repairEvents.add("repair_progress:" + position.toShortString() + ":" + progress);
            LOGGER.info("the destruction of the wrong block {} at {} stands at {}", id, position, progress);
        }
        if (++repairBreakTicks % 10 == 0) {
            // The trace of the destruction: the name of the block under the crosshair, the distance
            // to the block and the state of the block on the client. The server does not send the
            // state of the block of another player, the client is the only view of it.
            var crosshair = client.crosshairTarget instanceof BlockHitResult named ? named.getBlockPos() : null;
            double distance = client.player == null ? -1.0
                    : client.player.getEyePos().distanceTo(new Vec3d(position.getX() + 0.5,
                            position.getY() + 0.5, position.getZ() + 0.5));
            LOGGER.info("the destruction of the wrong block at {} continues: ticks={} progress={} "
                            + "crosshair={} distance={} client={}",
                    position, repairBreakTicks, progress, crosshair, distance,
                    Registries.BLOCK.getId(client.world.getBlockState(position).getBlock()).toString());
        }
        publishFacts("the wrong block is broken with an ordinary attack of the player");
    }

    /**
     * The cell of the approach from which the wrong block is attacked.
     *
     * <p>The cell has to be free, the player has to stand on a solid block there and the eyes of
     * the player have to reach the wrong block. The cell of the approach is not the cell that is
     * repaired: the player never stands in the block that is broken.
     *
     * @return the cell of the approach or null when no cell reaches the block
     */
    private BlockPos repairAccess() {
        if (repairAccess != null) {
            return repairAccess;
        }
        BlockPos target = repairTarget;
        if (target == null) {
            return null;
        }
        Vec3d centre = new Vec3d(target.getX() + 0.5, target.getY() + 0.5, target.getZ() + 0.5);
        int feet = client.player == null ? target.getY() : client.player.getBlockPos().getY();
        List<BlockPos> candidates = new ArrayList<>();
        List<BlockPos> outside = new ArrayList<>();
        for (Direction direction : Direction.Type.HORIZONTAL) {
            for (int level : new int[]{0, 1, -1}) {
                BlockPos candidate = target.offset(direction).up(level);
                if (!standable(candidate)) {
                    continue;
                }
                Vec3d eye = new Vec3d(candidate.getX() + 0.5, candidate.getY() + EYE_HEIGHT,
                        candidate.getZ() + 0.5);
                if (!ReachPolicy.mayInteract(eye.x, eye.y, eye.z, centre.x, centre.y, centre.z)) {
                    continue;
                }
                candidates.add(candidate);
                if (!insideHouseVolume(candidate)) {
                    // The player breaks the wrong block from outside of the volume: the house is
                    // not entered, and the navigation does not have to dig a way into it (the
                    // dependency must not break the blocks of the volume, ТЗ.md section 4).
                    outside.add(candidate);
                }
            }
        }
        List<BlockPos> chosen = outside.isEmpty() ? candidates : outside;
        if (chosen.isEmpty()) {
            return null;
        }
        // The cell of the approach that is closest to the player is chosen: a cell that is two
        // levels higher cannot be reached with a jump, and the navigation of UnionClef builds a
        // temporary block for it. The temporary blocks are forbidden (ТЗ.md section 4).
        chosen.sort(Comparator.comparingInt(candidate -> Math.abs(candidate.getY() - feet)));
        repairAccess = chosen.get(Math.floorMod(repairAccessRound, chosen.size()));
        return repairAccess;
    }

    /** True when the position belongs to the volume of the house. */
    private boolean insideHouseVolume(BlockPos position) {
        if (avoidedOrigin == null) {
            return false;
        }
        int x = position.getX() - avoidedOrigin.x();
        int y = position.getY() - avoidedOrigin.y();
        int z = position.getZ() - avoidedOrigin.z();
        return x >= 0 && x < HouseTemplate.SIZE_X && y >= 0 && y < HouseTemplate.SIZE_Y
                && z >= 0 && z < HouseTemplate.SIZE_Z;
    }

    /**
     * Tells the dependency which blocks the add-on removes itself.
     *
     * <p>The safe repair of a wrong cell and the removal of a temporary support are ordinary
     * attacks of the player on blocks inside the volume of the house. The dependency must not
     * refuse them as a private area, otherwise the cell is never repaired (ТЗ.md section 4).
     */
    private void declareOwnRemovals() {
        java.util.List<BlockPos> removals = new java.util.ArrayList<>();
        if (repairTarget != null) {
            removals.add(repairTarget);
        }
        removals.addAll(scaffoldsToRemove);
        // The temporary supports of the add-on are temporary blocks: the dependency may dig them
        // when its path runs through the room of the house, and it must not be stopped by the rule
        // that protects the volume of the house (2026-11-14). The support under the player stays
        // protected: the player would fall (ТЗ.md section 4).
        BlockPos feet = client.player == null ? null : client.player.getBlockPos();
        for (BlockPos support : scaffoldPositions()) {
            if (feet != null && (support.equals(feet) || support.equals(feet.down()))) {
                continue;
            }
            removals.add(support);
        }
        navigation.allowBreaking(removals);
    }

    /** Turns the view of the player to the wrong block. */
    private void aimAtRepairTarget(BlockPos position) {
        if (client.player == null) {
            return;
        }
        Vec3d eye = client.player.getEyePos();
        Vec3d centre = new Vec3d(position.getX() + 0.5, position.getY() + 0.5, position.getZ() + 0.5);
        // The block is not always a full cube: the lower half of a door and the parts of a bed
        // fill only the lower part of their cell, and the ray to the middle of the cell slips
        // over them and names the block behind. The points of the cell are tried one after the
        // other, in the middle and in the lower and the upper part of the cell, so that the
        // crosshair names the block itself.
        double offsetX = switch (repairAimRound % 6) {
            case 2 -> 0.25;
            case 3 -> -0.25;
            default -> 0.0;
        };
        double offsetY = switch (repairAimRound % 6) {
            case 1, 5 -> -0.25;
            case 4 -> 0.25;
            default -> 0.0;
        };
        double offsetZ = switch (repairAimRound % 6) {
            case 5 -> 0.25;
            default -> 0.0;
        };
        Vec3d point = new Vec3d(centre.x + offsetX, centre.y + offsetY, centre.z + offsetZ);
        double dx = point.x - eye.x;
        double dy = point.y - eye.y;
        double dz = point.z - eye.z;
        client.player.setYaw((float) Math.toDegrees(Math.atan2(-dx, dz)));
        client.player.setPitch((float) -Math.toDegrees(Math.atan2(dy, Math.sqrt(dx * dx + dz * dz))));
    }

    /**
     * Removes the plant that the automation asked for.
     *
     * <p>The player walks to the plant and breaks it with an ordinary attack. A block that is not
     * one of the allowed plants is never touched.
     */
    private void removalTick() {
        BlockPos position = removalTarget;
        if (position == null) {
            return;
        }
        String id = Registries.BLOCK.getId(client.world.getBlockState(position).getBlock()).toString();
        if (!ALLOWED_PLANTS.contains(id)) {
            // The plant is gone or another block stands there; nothing is broken.
            removalTarget = null;
            removalSent = false;
            return;
        }
        BlockPos playerPos = client.player.getBlockPos();
        if (!near(playerPos, position)) {
            navigation.requestNavigation(position);
            return;
        }
        navigation.clearNavigation();
        if (removalSent) {
            return;
        }
        removalSent = true;
        if (client.interactionManager != null) {
            client.interactionManager.attackBlock(position, Direction.UP);
            client.player.swingHand(Hand.MAIN_HAND);
            LOGGER.debug("the plant {} at {} is removed", id, position);
        }
    }

    /**
     * The way out of the finished house.
     *
     * <p>The player walks to the cell in front of the exit door, opens the door with one ordinary
     * interaction and walks to the front point A+(3,0,-1). The door is opened at most twice; no
     * other block is touched and nothing is broken.
     */
    private void exitTick() {
        switch (exitPhase) {
            case TO_DOOR -> {
                navigation.requestNavigation(walkLegTo(exitAccess));
                if (client.player.getBlockPos().equals(exitAccess)) {
                    exitPhase = ExitPhase.OPEN_DOOR;
                }
            }
            case OPEN_DOOR -> {
                navigation.clearNavigation();
                if (!exitDoorAimed) {
                    // The rotation of the player reaches the server with the next movement packet;
                    // the click follows in the next tick.
                    aimAtExitDoor();
                    exitDoorAimed = true;
                    return;
                }
                if (openExitDoor()) {
                    exitPhase = ExitPhase.TO_FRONT;
                }
            }
            case TO_FRONT -> navigation.requestNavigation(walkLegTo(exitFront));
            default -> {
                // Nothing to do.
            }
        }
    }

    /**
     * Opens the exit door with one interaction.
     *
     * @return true when the door is open, when there is no door or when the door did not open
     *         after two attempts; the way out is tried in every case
     */
    private boolean openExitDoor() {
        if (exitDoor == null) {
            return true;
        }
        BlockState state = client.world.getBlockState(exitDoor);
        if (!state.isOf(Blocks.SPRUCE_DOOR)) {
            // There is no door at the exit: the way out is free.
            return true;
        }
        if (state.get(DoorBlock.OPEN)) {
            return true;
        }
        if (exitDoorSends >= 2) {
            // The door did not open; the player tries to leave the house anyway.
            return true;
        }
        ClientPlayerInteractionManager manager = client.interactionManager;
        if (manager == null) {
            return false;
        }
        BlockHitResult hit = client.crosshairTarget instanceof BlockHitResult block ? block : null;
        if (hit == null || (!hit.getBlockPos().equals(exitDoor)
                && !hit.getBlockPos().equals(exitDoor.up()))) {
            // Do not invent a hit on the thin panel when the game's ray names another block.
            aimAtExitDoor();
            return false;
        }
        manager.interactBlock(client.player, Hand.MAIN_HAND, hit);
        exitDoorSends++;
        doorInteractions++;
        LOGGER.info("the exit door at {} was opened with the interaction {}", exitDoor, exitDoorSends);
        return false;
    }

    /** Turns the view of the player to the exit door. */
    private void aimAtExitDoor() {
        Vec3d eye = client.player.getEyePos();
        Vec3d hit = exitDoorHit();
        double dx = hit.x - eye.x;
        double dy = hit.y - eye.y;
        double dz = hit.z - eye.z;
        client.player.setYaw((float) Math.toDegrees(Math.atan2(-dx, dz)));
        client.player.setPitch((float) -Math.toDegrees(Math.atan2(dy, Math.sqrt(dx * dx + dz * dz))));
    }

    /**
     * The point of the door that is clicked.
     *
     * <p>The door of the project has {@code facing=south}: its panel lies on the south side of
     * the block, so the player clicks it from inside the house.
     */
    private Vec3d exitDoorHit() {
        return new Vec3d(exitDoor.getX() + 0.5, exitDoor.getY() + 0.5, exitDoor.getZ() + 0.9);
    }

    /** True when the player stands close enough to the position. */
    private boolean near(BlockPos playerPos, BlockPos position) {
        double dx = playerPos.getX() - position.getX();
        double dz = playerPos.getZ() - position.getZ();
        return Math.sqrt(dx * dx + dz * dz) <= 2.0 && Math.abs(playerPos.getY() - position.getY()) <= 2.0;
    }

    /**
     * Makes sure that the item of the project is in the main hand of the player.
     *
     * <p>The index of the slot of the screen handler is never computed by hand: the slots of the
     * screen handler of the player are read and the slot that holds the wanted item is used. The
     * facts come from the bytecode of Minecraft 1.21.11 (Yarn): the handler of the player holds
     * the slots of the inventory at the indices {@code PlayerScreenHandler.INVENTORY_START}..35
     * and the slots of the hotbar at the indices 36..{@code HOTBAR_END}, and the click of the
     * client is applied to the slots of the handler and sent to the server
     * ({@code ClientPlayerInteractionManager.clickSlot}).
     *
     * <p>Before the interaction is sent, the main hand is read again: an interaction with another
     * item in the hand would place a wrong block or nothing at all (ТЗ.md section 4).
     *
     * @return true when the main hand holds the item of the project
     */
    private boolean ensureItemInMainHand() {
        if (item == null || client.player == null) {
            return false;
        }
        String wanted = Registries.ITEM.getId(item).toString();
        String held = Registries.ITEM.getId(client.player.getMainHandStack().getItem()).toString();
        if (ItemSelection.mayClick(wanted, held)) {
            return true;
        }
        PlayerInventory inventory = client.player.getInventory();
        for (int slot = 0; slot < 9; slot++) {
            ItemStack stack = inventory.getMainStacks().get(slot);
            if (!stack.isEmpty() && stack.getItem() == item) {
                // The item is in the hotbar of the player: the slot of the hotbar is chosen.
                inventory.setSelectedSlot(slot);
                return true;
            }
        }
        int handlerSlot = ItemSelection.slotWithItem(screenSlots(), wanted);
        if (handlerSlot < 0) {
            // The item is not in the inventory of the player at all.
            return false;
        }
        ClientPlayerInteractionManager manager = client.interactionManager;
        if (manager == null) {
            return false;
        }
        manager.clickSlot(client.player.currentScreenHandler.syncId, handlerSlot, inventory.getSelectedSlot(),
                SlotActionType.SWAP, client.player);
        String heldNow = Registries.ITEM.getId(client.player.getMainHandStack().getItem()).toString();
        return ItemSelection.mayClick(wanted, heldNow);
    }

    /**
     * The slots of the screen handler of the player, as the rule of the core wants to see them.
     *
     * <p>Only the slots of the inventory and of the hotbar belong to the inventory of the
     * player: the armour, the off hand and the table of the craft are not part of it.
     */
    private List<ItemSelection.SlotView> screenSlots() {
        List<ItemSelection.SlotView> views = new ArrayList<>();
        if (client.player == null) {
            return views;
        }
        ScreenHandler handler = client.player.currentScreenHandler;
        PlayerInventory inventory = client.player.getInventory();
        for (int index = 0; index < handler.slots.size(); index++) {
            Slot slot = handler.slots.get(index);
            boolean main = slot.inventory == inventory
                    && index >= net.minecraft.screen.PlayerScreenHandler.INVENTORY_START
                    && index < net.minecraft.screen.PlayerScreenHandler.HOTBAR_END;
            ItemStack stack = slot.getStack();
            String itemId = stack.isEmpty() ? "" : Registries.ITEM.getId(stack.getItem()).toString();
            views.add(new ItemSelection.SlotView(index, itemId, main));
        }
        return views;
    }

    /**
     * Writes the facts of the attempt into the log of the client.
     *
     * <p>The facts answer the question "what happened with this cell": without them a run that
     * does not confirm a cell cannot be read, because the world does not tell why the click did
     * not change anything. The facts are written when the stage of the attempt changes and at
     * least every second, so that the log of a long run stays readable.
     *
     * @param stage what the add-on was doing with the cell
     */
    private void publishFacts(String stage) {
        PlacementFacts facts = currentFacts(stage);
        if (stage.equals(lastStage) && tickCounter - lastFactsTick < FACTS_INTERVAL_TICKS) {
            return;
        }
        lastStage = stage;
        lastFactsTick = tickCounter;
        lastFacts = facts.describe();
        LOGGER.info("LAB_OBSERVE placement {}", lastFacts);
    }

    /**
     * The facts of the attempt that is in progress.
     *
     * @param stage what the add-on was doing with the cell
     * @return the facts of the attempt
     */
    public PlacementFacts currentFacts(String stage) {
        Cell cell = target != null ? target.cell() : (operation == null ? null : operation.cells().get(0));
        String relative = cell == null ? null : cell.x() + "," + cell.y() + "," + cell.z();
        String world = cell == null || origin == null ? null
                : (origin.x() + cell.x()) + "," + (origin.y() + cell.y()) + "," + (origin.z() + cell.z());
        String support = solution == null ? null : solution.support().toShortString();
        String face = solution == null ? null : solution.face().name();
        String access = solution == null ? null : solution.access().toShortString();
        String player = client.player == null ? null : client.player.getBlockPos().toShortString();
        String wanted = operation == null ? null : operation.itemId();
        String held = client.player == null ? null
                : Registries.ITEM.getId(client.player.getMainHandStack().getItem()).toString();
        double reach = 0.0;
        double yaw = 0.0;
        double pitch = 0.0;
        String click = null;
        if (solution != null && client.player != null) {
            Vec3d eye = client.player.getEyePos();
            reach = ReachPolicy.distance(eye.x, eye.y, eye.z,
                    solution.hit().x, solution.hit().y, solution.hit().z);
            yaw = client.player.getYaw();
            pitch = client.player.getPitch();
            click = String.format(java.util.Locale.ROOT, "%.2f,%.2f,%.2f",
                    solution.hit().x, solution.hit().y, solution.hit().z);
        }
        boolean arrived = arrived();
        String observed = observedCell();
        return new PlacementFacts(relative, world, support, face, access, player, wanted, held,
                clickSent, reach, observed == null ? stage : observed + " (" + stage + ")",
                arrived, lastActionResult, yaw, pitch, click, diagnostics());
    }

    /**
     * The diagnostics of the attempt that is in progress.
     *
     * <p>They name the hit result of the game, the verdict of the comparison of the view with
     * the support, the slot of the screen handler that holds the item of the project, the place
     * and the view of the player on the client and on the server, the first precondition of the
     * ordinary placement that did not hold and the states of the cell of the project and of the
     * cells around it.
     *
     * @return the diagnostics of the attempt
     */
    private PlacementDiagnostics diagnostics() {
        BlockHitResult crosshair = crosshairTarget();
        String aimBlock = crosshair == null ? null : crosshair.getBlockPos().toShortString();
        String aimFace = crosshair == null ? null : crosshair.getSide().name();
        String aimVector = crosshair == null ? null : vector(crosshair.getPos());
        String slotItem = client.player == null ? null
                : Registries.ITEM.getId(client.player.getMainHandStack().getItem()).toString();
        int slot = -1;
        if (operation != null) {
            slot = ItemSelection.slotWithItem(screenSlots(), operation.itemId());
        }
        return new PlacementDiagnostics(crosshair == null ? "none" : "crosshair", aimBlock, aimFace,
                aimVector, aimVerdict, slot < 0 ? null : String.valueOf(slot), slotItem,
                clientImage(), serverImage(), preconditionStep, aimTicks, neighbours());
    }

    /**
     * The place and the view of the player on the client.
     *
     * <p>The state of the window is a part of the image: when the game is paused or when the
     * window has lost the focus, the server does not receive the movement packets of the player,
     * and the failure of the placement belongs to the environment and not to the add-on.
     *
     * @return the image of the player on the client
     */
    private String clientImage() {
        if (client.player == null) {
            return null;
        }
        Vec3d pos = client.player.getEntityPos();
        return String.format(java.util.Locale.ROOT,
                "%.2f,%.2f,%.2f yaw=%.2f pitch=%.2f paused=%s focused=%s",
                pos.x, pos.y, pos.z, client.player.getYaw(), client.player.getPitch(),
                client.isPaused(), client.isWindowFocused());
    }

    /**
     * The place and the view of the player on the server of the game.
     *
     * <p>The server knows the place and the direction of the view of the player only with the
     * movement packets: the image of the server says whether the direction of the view and the
     * place of the player already reached the server.
     *
     * <p>The image names also the item in the hand of the server and whether that item is the
     * item in the hand of the client. The slot of the client is changed locally; only the game
     * itself sends the change of the slot to the server. The comparison of the two items is
     * therefore the sign that the slot has reached the server.
     *
     * @return the image of the player on the server or null when the server has no player
     */
    private String serverImage() {
        try {
            MinecraftServer server = client.getServer();
            if (server == null || client.player == null) {
                return null;
            }
            ServerPlayerEntity player = server.getPlayerManager().getPlayer(client.player.getUuid());
            if (player == null) {
                return null;
            }
            String held = Registries.ITEM.getId(player.getMainHandStack().getItem()).toString();
            String inHand = Registries.ITEM.getId(client.player.getMainHandStack().getItem()).toString();
            return String.format(java.util.Locale.ROOT,
                    "%.2f,%.2f,%.2f yaw=%.2f pitch=%.2f held=%s slot_sync=%s",
                    player.getX(), player.getY(), player.getZ(), player.getYaw(), player.getPitch(),
                    held, inHand.equals(held) ? "yes" : "no");
        } catch (RuntimeException failure) {
            return null;
        }
    }

    /** The states of the cell of the project and of the cells around it. */
    private String neighbours() {
        if (client.world == null || target == null || origin == null) {
            return null;
        }
        BlockPos cell = world(target.cell());
        StringBuilder text = new StringBuilder();
        for (Direction direction : Direction.values()) {
            BlockPos pos = cell.offset(direction);
            text.append(direction.name().charAt(0)).append('=')
                    .append(Registries.BLOCK.getId(client.world.getBlockState(pos).getBlock()).toString())
                    .append(' ');
        }
        return text.toString().trim();
    }

    /** A point of the world as one line of text. */
    private static String vector(Vec3d value) {
        return String.format(java.util.Locale.ROOT, "%.2f,%.2f,%.2f", value.x, value.y, value.z);
    }

    /**
     * True when the player stands in the cell of the approach.
     *
     * @return true when the player is in the cell of the approach of the current operation
     */
    private boolean arrived() {
        if (solution == null || client.player == null) {
            return false;
        }
        return arrived(client.player.getBlockPos());
    }

    /**
     * What the world shows in the cell of the operation that is in progress.
     *
     * @return the identifier of the block in the cell or null when there is no cell
     */
    private String observedCell() {
        if (client.world == null || target == null || origin == null) {
            return null;
        }
        Cell cell = target.cell();
        BlockPos pos = new BlockPos(origin.x() + cell.x(), origin.y() + cell.y(), origin.z() + cell.z());
        return Registries.BLOCK.getId(client.world.getBlockState(pos).getBlock()).toString();
    }

    /** The facts of the last attempt, as one line of text. */
    public String lastFacts() {
        return lastFacts;
    }

    /** The only plants that the add-on is allowed to remove (ТЗ.md section 3). */
    private static final java.util.Set<String> ALLOWED_PLANTS = java.util.Set.of(
            "minecraft:short_grass", "minecraft:tall_grass", "minecraft:fern", "minecraft:large_fern");
}
