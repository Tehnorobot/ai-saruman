package dev.magehome.client;

import adris.altoclef.AltoClef;
import adris.altoclef.tasks.movement.GetToBlockTask;
import adris.altoclef.tasksystem.Task;
import dev.magehome.core.automation.ApiResponses;
import dev.magehome.core.automation.AutomationController;
import dev.magehome.core.automation.AutomationState;
import dev.magehome.core.automation.BehaviourBalance;
import dev.magehome.core.automation.ResumeOwnership;
import dev.magehome.core.automation.TaskChainOwnership;
import dev.magehome.core.automation.TaskOwnership;
import dev.magehome.core.automation.TaskProtocol;
import dev.magehome.core.placement.ActionGate;
import dev.magehome.core.registry.HouseRecord;
import dev.magehome.core.registry.HouseRegistry;
import dev.magehome.core.registry.HouseMaintenance;
import dev.magehome.core.supply.SuspensionReason;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.BlockPos;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.util.List;
import java.util.Queue;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * The runtime of the mod: it owns the state machine, the UnionClef task, the registry file and
 * the placement. Everything that changes the game happens on the client thread.
 */
public final class MageHomeRuntime implements NavigationRequest {

    private static final Logger LOGGER = LoggerFactory.getLogger("mage_home");

    private static MageHomeRuntime instance;

    private final AutomationController controller = new AutomationController();
    private final UnionClefBridge bridge = new UnionClefBridge();
    private final RegistryService registryService;
    private final MageHomeContext context;
    private final PlacementExecutor placement;
    private final MageHomeTask task;
    private final Queue<Runnable> pendingActions = new ConcurrentLinkedQueue<>();
    private final AcceptanceRunner acceptance = new AcceptanceRunner();

    /** Number of the client ticks between two observations in the log. */
    private static final int OBSERVATION_INTERVAL_TICKS = 100;
    /** Counter of the observations in the log. */
    private int observationCounter;

    private volatile String statusSnapshot = ApiResponses.status(false, AutomationState.DISABLED, null, null, null,
            0, 180, null, null);
    private volatile String homesSnapshot = ApiResponses.homes(new HouseRegistry());

    private BlockPos navigationTarget;
    private Task navigationTask;
    /**
     * The balance of the behaviour state of UnionClef.
     *
     * <p>The ledger lives in the domain core, so that the push and the pop can be checked
     * without Minecraft (ТЗ.md section 7).
     */
    private final BehaviourBalance behaviour = new BehaviourBalance();
    /** The number of the requests of the navigation that the add-on made while it was suspended. */
    private int navigationWhileSuspended;
    /** The message about the missing task chain of UnionClef was already written. */
    private boolean chainWaitAnnounced;

    private MageHomeRuntime() {
        Path gameDirectory = FabricLoader.getInstance().getGameDir();
        this.registryService = new RegistryService(gameDirectory);
        this.task = new MageHomeTask(this);
        this.placement = new PlacementExecutor(this, this::actionsAllowed,
                position -> controller.isProjectCell(position));
        this.context = new MageHomeContext(registryService, placement, gameDirectory, task, this::actionsAllowed);
    }

    public static synchronized MageHomeRuntime create() {
        if (instance == null) {
            instance = new MageHomeRuntime();
        }
        return instance;
    }

    public static MageHomeRuntime get() {
        return instance;
    }

    public AutomationController controller() {
        return controller;
    }

    public MageHomeContext context() {
        return context;
    }

    public RegistryService registryService() {
        return registryService;
    }

    public AcceptanceRunner acceptance() {
        return acceptance;
    }

    /** The placement of the mod; it counts the interactions with the world. */
    public PlacementExecutor placement() {
        return placement;
    }

    // --- client tick ----------------------------------------------------------------------------

    /** One client tick of the mod. */
    public void onClientTick() {
        MinecraftClient client = MinecraftClient.getInstance();
        // The preparation of the stand of the laboratory runs before the world is loaded: the
        // laboratory of the platform opens the world through the lifecycle of the dependency
        // (the title screen of Minecraft for the dependency altoclef 1.21.11-0.95.61, see
        // LabStand), and UnionClef initializes itself there. The preparation of the platform
        // lets that lifecycle run for the dependency; the stand of the add-on only observes the
        // facts of the readiness. Without a scenario of the laboratory nothing happens here.
        acceptance.prepareStand();
        if (client.world == null || client.player == null) {
            placement.releaseControls();
            // A scenario of the acceptance run waits for the world of the stand.
            acceptance.standWaiting();
            updateSnapshots();
            return;
        }
        // The number of the tick is written into the observations of the log.
        context.advanceTick();
        if (client.player.isDead() || client.player.getHealth() <= 0.0f) {
            // The player died: the house keeps its record and waits for an explicit resume
            // (ТЗ.md section 5).
            controller.interrupt(context, SuspensionReason.PLAYER_DEAD.wireName());
        }
        runPendingActions();
        if (controller.enabled()) {
            ensureTaskRunning();
        }
        controller.tick(context);
        if (!controller.enabled() || controller.state() == AutomationState.SUSPENDED) {
            if (placement.hasPendingWork()) placement.cancel();
        } else {
            placement.tick();
        }
        if (controller.currentHouse() == null && placement.currentTarget() == null) {
            // Nothing is built any more: the walk to the front point is over.
            clearNavigation();
        }
        updateSnapshots();
        observeRuntime();
        acceptance.tick(this);
    }

    /**
     * The gate of the background actions of the client (ТЗ.md sections 3 and 5).
     *
     * <p>The rule lives in the domain core ({@link ActionGate}); the client uses exactly this
     * rule for the interactions with the world, for the navigation and for the give command.
     */
    public boolean actionsAllowed() {
        return ActionGate.mayAct(controller.enabled(), controller.state() == AutomationState.SUSPENDED,
                context.unionClefPaused(), context.foreignTaskActive(), bridge.chainAvailable());
    }

    /**
     * The relation of the task chain of UnionClef to the own root task.
     *
     * <p>The relation is read from the real UnionClef, so that the ownership of the chain can be
     * observed in the log of the game and in the report of a scenario.
     */
    public TaskOwnership.Relation taskChainRelation() {
        return bridge.relationToOwnRoot(task);
    }

    /** The class of the task that owns the chain of UnionClef; null when the chain is empty. */
    public String currentTaskName() {
        Task current = bridge.currentTask();
        return current == null ? null : current.getClass().getName();
    }

    /**
     * The task that owns the chain of UnionClef; null when the chain is empty.
     *
     * <p>The identity of the task is needed to tell the own root task from the task of
     * another owner and to see whether the task of another owner is still the same task.
     */
    public Task currentTaskObject() {
        return bridge.currentTask();
    }

    /** The own root task of the add-on. */
    public Task ownRootTask() {
        return task;
    }

    /**
     * The calls that the add-on made on the chain of UnionClef, in the order of their making.
     *
     * <p>The record is the observation of the conduct of the add-on: it shows on which task the
     * chain of UnionClef was when the add-on acted on it (ТЗ.md section 5).
     */
    public List<UnionClefBridge.ChainAction> chainActions() {
        return bridge.actions();
    }

    /**
     * Writes the observations of the runtime into the log of the client.
     *
     * <p>The identity of the current task of UnionClef and of the root task of the add-on, the
     * state of the automation and the counter of the delay of 1200 ticks are visible in the log
     * of the game, so that they can be followed without a debugger.
     */
    private void observeRuntime() {
        observationCounter++;
        if (observationCounter % OBSERVATION_INTERVAL_TICKS != 0) {
            return;
        }
        Task current = bridge.currentTask();
        LOGGER.info("LAB_OBSERVE task current={} own_root={} relation={} state={} reason={} delay_ticks={}",
                current == null ? "none" : current.getClass().getName(),
                task.getClass().getName(),
                bridge.relationToOwnRoot(task),
                controller.state().wireName(),
                controller.reason(),
                controller.delayTicks());
    }

    private void runPendingActions() {
        Runnable action;
        while ((action = pendingActions.poll()) != null) {
            try {
                action.run();
            } catch (RuntimeException e) {
                LOGGER.error("a pending action failed", e);
            }
        }
    }

    private void ensureTaskRunning() {
        if (controller.state() == AutomationState.SUSPENDED) {
            // A suspended automation waits for an explicit resume and does not take the control.
            return;
        }
        AltoClef mod = bridge.mod();
        if (mod == null || mod.isPaused()) {
            return;
        }
        ResumeOwnership.Action action = ResumeOwnership.decide(bridge.relationToOwnRoot(task),
                bridge.currentTaskStopped());
        switch (action) {
            case START_OWN_TASK, REARM_OWN_ROOT -> {
                chainWaitAnnounced = false;
                bridge.runTask(task, action == ResumeOwnership.Action.START_OWN_TASK
                        ? TaskChainOwnership.Action.START_OWN_TASK
                        : TaskChainOwnership.Action.REARM_OWN_ROOT);
            }
            case OWN_TASK_RUNNING -> {
                // The own task is running already.
            }
            case KEEP_FOREIGN -> {
                // The task of the other owner is not touched.
            }
            case WAIT_FOR_CHAIN -> {
                // The chain of UnionClef does not exist yet: the own task is not started.
                if (!chainWaitAnnounced) {
                    chainWaitAnnounced = true;
                    LOGGER.info("the task chain of UnionClef does not exist yet, the own task is not started");
                }
            }
        }
    }

    private void updateSnapshots() {
        try {
            statusSnapshot = controller.statusJson();
        } catch (RuntimeException e) {
            LOGGER.debug("the status snapshot is not available", e);
        }
        try {
            homesSnapshot = controller.homesJson(registryService.registry());
        } catch (RuntimeException e) {
            LOGGER.debug("the homes snapshot is not available", e);
        }
    }

    // --- world events ---------------------------------------------------------------------------

    /** The world was entered: the registry is read and interrupted houses become suspended. */
    public void onWorldJoined() {
        registryService.loadFromDisk();
        context.loadConfig();
        HouseRegistry registry = registryService.registry();
        boolean changed = false;
        for (HouseRecord record : registry.houses()) {
            if (record.state() == dev.magehome.core.registry.HouseState.BUILDING) {
                HouseMaintenance.restartSuspendedBuilding(record);
                changed = true;
            }
        }
        if (changed) {
            registryService.save(registry);
        }
        LOGGER.info("the registry of the world was read, {} records", registry.houses().size());
    }

    public void onWorldLeft() {
        registryService.awaitPendingWrites();
        // The world was left: the house keeps its record and waits for an explicit resume
        // (ТЗ.md section 5).
        controller.interrupt(context, SuspensionReason.WORLD_CHANGED.wireName());
        placement.cancel();
    }

    // --- UnionClef task -------------------------------------------------------------------------

    public void onTaskStarted() {
        LOGGER.info("the UnionClef task of mage_home started");
        if (behaviour.onStart()) {
            // The behaviour of UnionClef is pushed once for a start. A task that is re-armed
            // without a stop in between must not push it a second time, otherwise the push and
            // the pop would not be balanced any more (ТЗ.md section 7).
            bridge.pushBehaviour();
        }
    }

    /**
     * One tick of the root task of the add-on.
     *
     * <p>The facts come from the bytecode of the delivered UnionClef 0.95.61: {@code Task.tick}
     * takes the task that {@code onTick()} returns as the child of the ticking task, when the
     * ticking task has no child yet, and ticks that child right after. A task that returns
     * <em>itself</em> becomes therefore its own child, and the tick of the chain of UnionClef
     * recurses without an end until the stack of the client is gone. The root task of the add-on
     * returns the navigation task or nothing, but never itself.
     *
     * <p>The rule itself lives in the domain core ({@link TaskProtocol}), so that it can be
     * checked without Minecraft.
     */
    public Task onTaskTick() {
        try {
            TaskProtocol.Decision decision = TaskProtocol.decide(controller.enabled(),
                    controller.state() == AutomationState.SUSPENDED, navigationTarget != null);
            if (decision == TaskProtocol.Decision.NAVIGATION_CHILD && navigationTarget != null) {
                if (navigationTask == null) {
                    navigationTask = new GetToBlockTask(navigationTarget);
                }
                return navigationChild();
            }
            // No child: the root task of the add-on holds the chain of UnionClef alone and the
            // navigation that is not needed any more is released.
            return null;
        } catch (RuntimeException e) {
            // The error is a way out of the own work: the behaviour state of UnionClef is
            // released, so that every push has its pop, and the house waits for an explicit
            // resume (ТЗ.md sections 5 and 7).
            LOGGER.error("the tick of the own root task of mage_home failed", e);
            if (behaviour.onError()) {
                bridge.popBehaviour();
            }
            controller.interrupt(context, SuspensionReason.INTERRUPTED.wireName());
            return null;
        }
    }


    /**
     * The navigation task as the child of the root task.
     *
     * <p>The child of the root task is never the root task itself: a task that returns itself
     * would become its own child and the tick of the chain of UnionClef would recurse without an
     * end. The case is named here instead of being left to the reader of the code.
     */
    private Task navigationChild() {
        if (TaskProtocol.isOwnRoot(navigationTask, task)) {
            LOGGER.error("the navigation task of the add-on is the root task itself; the root task holds no child");
            navigationTask = null;
            return null;
        }
        return navigationTask;
    }

    public void onTaskStopped() {
        LOGGER.info("the UnionClef task of mage_home stopped");
        placement.cancel();
        navigationTarget = null;
        navigationTask = null;
        if (behaviour.onStop()) {
            // Every push is answered by a pop, also when the task is interrupted.
            bridge.popBehaviour();
        }
        if (controller.enabled()) {
            // An external interruption keeps the house and waits for an explicit resume.
            controller.interrupt(context);
        }
    }

    /**
     * The balance of the behaviour state of UnionClef.
     *
     * <p>The numbers are the observation of the add-on: every push has to be answered by a pop.
     *
     * @return the ledger of the behaviour state
     */
    public BehaviourBalance behaviourBalance() {
        return behaviour;
    }

    /**
     * True while the automation of the add-on is enabled.
     *
     * <p>The own root task uses it for {@code isFinished()}: a task of a disabled automation has
     * nothing to do and may end (ТЗ.md section 7).
     *
     * @return true when the automation is enabled
     */
    public boolean automationEnabled() {
        return controller.enabled();
    }

    @Override
    public void requestNavigation(BlockPos target) {
        if (target == null) {
            return;
        }
        if (controller.state() == AutomationState.SUSPENDED) {
            // A suspended automation asks for nothing: the counter shows it in the log of the
            // scenario, so that the behaviour of the dependency can be told apart from the
            // behaviour of the add-on.
            navigationWhileSuspended++;
            LOGGER.warn("the suspended automation asked for the navigation to {}", target);
        }
        if (!target.equals(navigationTarget)) {
            navigationTarget = target;
            navigationTask = null;
        }
    }

    /**
     * The number of the requests of the navigation that the add-on made while it was suspended.
     *
     * @return the number of the requests
     */
    public int navigationWhileSuspended() {
        return navigationWhileSuspended;
    }

    @Override
    public void clearNavigation() {
        navigationTarget = null;
        navigationTask = null;
    }

    /**
     * The dependency must not break the blocks of the volume of the house.
     *
     * <p>The navigation digs when it cannot find a way, and a dug block inside the volume of the
     * house is a wrong block that the rule of the repair has to remove afterwards. The behaviour
     * of the dependency is told about the volume once for the house (ТЗ.md section 4, T-A12-03).
     *
     * @param origin the beginning of the volume of the house in the world
     * @param sizeX the length of the volume along x
     * @param sizeY the height of the volume
     * @param sizeZ the length of the volume along z
     */
    @Override
    public void avoidBreaking(dev.magehome.core.model.Vec3i origin, int sizeX, int sizeY, int sizeZ) {
        bridge.avoidBreaking(origin, sizeX, sizeY, sizeZ);
    }

    @Override
    public void allowBreaking(java.util.Collection<net.minecraft.util.math.BlockPos> positions) {
        bridge.allowBreaking(positions);
    }

    // --- public API -----------------------------------------------------------------------------

    public String statusJson() {
        return statusSnapshot;
    }

    public String homesJson() {
        return homesSnapshot;
    }

    /** Enables or disables the automation; the change happens on the client thread. */
    public String setEnabled(boolean enabled) {
        pendingActions.add(() -> {
            if (enabled) {
                controller.enable();
            } else {
                controller.disable();
            }
        });
        return ApiResponses.accepted();
    }

    /** Resumes a house; the work itself happens on the client thread. */
    public String resume(String houseId) {
        if (houseId == null || registryService.registry().byId(houseId) == null) {
            return ApiResponses.rejected("unknown_house");
        }
        pendingActions.add(() -> {
            ResumeOwnership.Action action = ResumeOwnership.decide(bridge.relationToOwnRoot(task),
                    bridge.currentTaskStopped());
            if (action.keepsForeignTask()) {
                // The task of the other owner is neither stopped nor removed; the automation
                // waits (ТЗ.md section 5).
                LOGGER.info("the resume waits, the task chain belongs to another owner");
                controller.busy();
                return;
            }
            if (action == ResumeOwnership.Action.WAIT_FOR_CHAIN) {
                // The chain of UnionClef does not exist yet: the house is resumed, but the work
                // starts as soon as the chain is there (ТЗ.md section 5).
                LOGGER.info("the resume waits, the task chain of UnionClef does not exist yet");
            }
            if (action == ResumeOwnership.Action.REARM_OWN_ROOT) {
                // Only the own stopped root task is re-armed; no other task is touched.
                LOGGER.info("the own stopped root task of mage_home is re-armed");
                bridge.runTask(task, TaskChainOwnership.Action.REARM_OWN_ROOT);
            }
            controller.resume(houseId, context);
        });
        return ApiResponses.accepted();
    }

    public String retrySearch() {
        pendingActions.add(controller::retrySearch);
        return ApiResponses.accepted();
    }

    /**
     * Verifies the loaded houses in batches (ТЗ.md section 5).
     *
     * <p>The command {@code status} calls it; the reading of the API does not, so a read never
     * starts a verification of the world.
     */
    public void verifyHomes() {
        controller.verifyHomes(context);
    }

    /** Runs an action on the client thread; used by the commands and by the tests. */
    public void onClientThread(Runnable action) {
        pendingActions.add(action);
    }

    public boolean knownHouse(String houseId) {
        return houseId != null && registryService.registry().byId(houseId) != null;
    }
}
