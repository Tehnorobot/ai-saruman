package dev.magehome.client;

import adris.altoclef.AltoClef;
import adris.altoclef.tasksystem.Task;
import dev.magehome.core.automation.TaskChainOwnership;
import dev.magehome.core.automation.TaskOwnership;
import net.minecraft.item.Item;

import java.util.ArrayList;
import java.util.List;

/**
 * The small part of UnionClef that the mod uses. Every member was read from the bytecode of
 * {@code libs/unionclef-1.21.11-0.95.61.jar}; nothing is guessed.
 */
public final class UnionClefBridge {

    /**
     * One call of the add-on on the task chain of UnionClef.
     *
     * <p>The call, the task that the chain held before it and the state of that task are
     * recorded, because the relation of the chain alone cannot tell whether the add-on took the
     * chain away from the task of another owner: the chain of UnionClef changes inside its own
     * task tick, so a foreign task that ends and the own task that starts in the same tick are
     * seen as one jump from one owner to the other. The record of the calls is the observation
     * of the conduct of the add-on (ТЗ.md section 5).
     *
     * @param action    what the add-on did with the chain
     * @param heldTask  the task that the chain held before the call; null when it was empty
     * @param heldState the state of the held task: alive, finished, stopped or unknown
     */
    public record ChainAction(TaskChainOwnership.Action action, Task heldTask,
                              TaskChainOwnership.OwnerState heldState) {
    }

    /** The calls of the add-on on the chain of UnionClef, in the order of their making. */
    private final List<ChainAction> actions = new ArrayList<>();

    /**
     * The blocks that the add-on removes itself with an ordinary attack of the player.
     *
     * <p>They are not protected against the break: the safe repair of a wrong cell and the
     * removal of a temporary support of the add-on happen inside the volume of the house
     * (ТЗ.md section 4).
     */
    private final java.util.Set<net.minecraft.util.math.BlockPos> ownRemovals =
            new java.util.HashSet<>();

    /** The calls of the add-on on the chain of UnionClef, in the order of their making. */
    public List<ChainAction> actions() {
        return List.copyOf(actions);
    }

    /** The UnionClef instance or null when the mod is not loaded. */
    public AltoClef mod() {
        try {
            return AltoClef.getInstance();
        } catch (Throwable t) {
            return null;
        }
    }

    public boolean available() {
        return mod() != null;
    }

    /** True while UnionClef is paused. */
    public boolean paused() {
        AltoClef mod = mod();
        return mod != null && mod.isPaused();
    }

    /**
     * The chain of tasks of UnionClef exists.
     *
     * <p>The fact comes from the bytecode of the delivered UnionClef 0.95.61:
     * {@code AltoClef.getUserTaskChain()} returns the field {@code userTaskChain} without a check
     * and the field is filled in {@code AltoClef.onInitializeLoad()}. Before that method ran, the
     * chain is {@code null}: the add-on may neither start a task of its own nor act on the world.
     */
    public boolean chainAvailable() {
        AltoClef mod = mod();
        return mod != null && mod.getUserTaskChain() != null;
    }

    /**
     * The slot handler of UnionClef exists.
     *
     * <p>The fact comes from the bytecode of the delivered UnionClef 0.95.61: the field
     * {@code slotHandler} is filled in {@code AltoClef.onInitializeLoad()}, in the same method
     * that creates the task chain. The mixin of UnionClef on the inventory of the server calls
     * {@code AltoClef.getSlotHandler().onServerSlotUpdate(...)}; without the handler that mixin
     * fails, so the handler is a fact of the readiness of the stand and not only of the
     * navigation.
     */
    public boolean slotHandlerAvailable() {
        AltoClef mod = mod();
        return mod != null && mod.getSlotHandler() != null;
    }

    /** The task that currently owns the user task chain. */
    public Task currentTask() {
        AltoClef mod = mod();
        if (mod == null) {
            return null;
        }
        var chain = mod.getUserTaskChain();
        if (chain == null) {
            // The chain of UnionClef does not exist yet: there is no current task.
            return null;
        }
        return chain.getCurrentTask();
    }

    /**
     * The relation of the current task of the chain to the own root task.
     *
     * <p>The facts come from the bytecode of the delivered UnionClef:
     * {@code SingleTaskChain.getCurrentTask()} returns the root task of the chain and
     * {@code Task.thisOrChildSatisfies} tests that task and its children.
     *
     * <p>A chain that does not exist yet is its own relation: it is not a foreign owner, but the
     * add-on may not own a task while UnionClef cannot hold one.
     */
    public TaskOwnership.Relation relationToOwnRoot(Task ownRoot) {
        AltoClef mod = mod();
        if (mod == null || mod.getUserTaskChain() == null) {
            return TaskOwnership.unavailableChain();
        }
        Task current = currentTask();
        if (current == null) {
            return TaskOwnership.noTask();
        }
        boolean ownRootIsCurrent = current == ownRoot;
        boolean ownRootIsBelowCurrent = current.thisOrChildSatisfies(task -> task == ownRoot);
        return TaskOwnership.relation(ownRootIsCurrent, ownRootIsBelowCurrent);
    }

    /** The task that owns the chain is stopped. */
    public boolean currentTaskStopped() {
        Task current = currentTask();
        return current != null && current.stopped();
    }

    /**
     * Starts the own root task in the chain of UnionClef.
     *
     * <p>The call is recorded with the task that the chain held at that moment: a call while
     * the chain holds the task of another owner would stop that task, and that is forbidden
     * (ТЗ.md section 5). The add-on itself refuses such a call (see
     * {@code ResumeOwnership.decide}), and the record lets the harness of the laboratory see it.
     *
     * @param task   the own root task of the add-on
     * @param action what the add-on does with the chain
     */
    public void runTask(Task task, TaskChainOwnership.Action action) {
        AltoClef mod = mod();
        if (mod == null || mod.getUserTaskChain() == null) {
            // The chain of UnionClef does not exist yet: the task cannot be started.
            return;
        }
        Task held = currentTask();
        actions.add(new ChainAction(action, held, state(held)));
        mod.runUserTask(task);
    }

    /**
     * The state of a task of UnionClef.
     *
     * <p>The facts come from the bytecode of the delivered UnionClef: {@code Task.stopped()} is
     * true only after {@code Task.stop(..)} was called, and {@code Task.isFinished()} is the
     * decision of the task itself that it is done. A task that cannot be read has the unknown
     * state: the rule judges it like a living task, because the harm of the add-on must not be
     * assumed away.
     */
    private static TaskChainOwnership.OwnerState state(Task task) {
        if (task == null) {
            return TaskChainOwnership.OwnerState.UNKNOWN;
        }
        try {
            if (task.stopped()) {
                return TaskChainOwnership.OwnerState.STOPPED;
            }
            if (task.isFinished()) {
                return TaskChainOwnership.OwnerState.FINISHED;
            }
        } catch (Throwable t) {
            return TaskChainOwnership.OwnerState.UNKNOWN;
        }
        return TaskChainOwnership.OwnerState.ALIVE;
    }

    /**
     * Stops the user task chain; this is the ordinary Stop of UnionClef.
     *
     * <p>The mod keeps its own record and waits for an explicit resume. This is not a call of
     * the add-on: the add-on never stops the chain of UnionClef, and the call is therefore not
     * recorded in the record of the calls of the add-on.
     */
    public void stopTaskChain() {
        AltoClef mod = mod();
        if (mod != null && mod.getUserTaskChain() != null) {
            mod.getUserTaskChain().stop();
        }
    }

    /** Pushes the behaviour state; it has to be balanced with {@link #popBehaviour()}. */
    public void pushBehaviour() {
        AltoClef mod = mod();
        if (mod != null && mod.getBehaviour() != null) {
            mod.getBehaviour().push();
        }
    }

    public void popBehaviour() {
        AltoClef mod = mod();
        if (mod != null && mod.getBehaviour() != null) {
            mod.getBehaviour().pop();
        }
    }

    /**
     * The blocks of the volume of the house must not be broken by the navigation.
     *
     * <p>The dependency digs when it cannot find a way, and a dug block inside the volume of the
     * house is a wrong block that the rule of the repair has to remove afterwards. The behaviour
     * of the dependency is told about the volume, and the rule holds until the automation stops
     * (ТЗ.md section 4, T-A12-03).
     *
     * @param origin the beginning of the volume of the house in the world
     * @param sizeX the length of the volume along x
     * @param sizeY the height of the volume
     * @param sizeZ the length of the volume along z
     */
    public void avoidBreaking(dev.magehome.core.model.Vec3i origin, int sizeX, int sizeY, int sizeZ) {
        AltoClef mod = mod();
        if (mod == null || mod.getBehaviour() == null || origin == null) {
            return;
        }
        // Only the cells of the project are protected: the navigation may dig the ground next to
        // the house, otherwise it gets stuck at the platform and the task is stopped. The empty
        // cells of the room of the house are not protected either: the temporary supports of the
        // add-on stand there, and the dependency has to be able to dig them out. Otherwise its own
        // task fails with "private area", the bot stands still and the house is interrupted
        // (2026-11-14).
        dev.magehome.core.model.Cell[] relative = new dev.magehome.core.model.Cell[1];
        java.util.function.Predicate<net.minecraft.util.math.BlockPos> projectCell = position -> {
            int x = position.getX() - origin.x();
            int y = position.getY() - origin.y();
            int z = position.getZ() - origin.z();
            if (x < 0 || x >= sizeX || y < 0 || y >= sizeY || z < 0 || z >= sizeZ) {
                return false;
            }
            relative[0] = new dev.magehome.core.model.Cell(x, y, z);
            return dev.magehome.core.template.HouseTemplate.forVariant(0).cells()
                    .containsKey(relative[0]);
        };
        // The blocks that the add-on removes itself are not protected: the safe repair breaks a
        // wrong block inside the volume with an ordinary attack of the player (ТЗ.md section 4),
        // and the dependency must not refuse that attack as a private area.
        mod.getBehaviour().avoidBlockBreaking(position -> projectCell.test(position)
                && !ownRemovals.contains(position));
        // Prevent navigation's pillaring/bridging from continually creating new repair work.
        // Direct, verified player interactions by this add-on are not navigation placements.
        mod.getBehaviour().avoidBlockPlacing(position -> {
            int x = position.getX() - origin.x();
            int y = position.getY() - origin.y();
            int z = position.getZ() - origin.z();
            return x >= 0 && x < sizeX && y >= 0 && y < sizeY && z >= 0 && z < sizeZ;
        });
    }

    /**
     * The blocks that the add-on removes itself with an ordinary attack of the player.
     *
     * <p>The safe repair of a wrong cell and the removal of a temporary support of the add-on are
     * ordinary attacks of the player on blocks inside the volume of the house. The dependency must
     * not refuse them, otherwise the repair of the cell never happens (ТЗ.md section 4).
     *
     * @param positions the positions that the add-on removes itself
     */
    public void allowBreaking(java.util.Collection<net.minecraft.util.math.BlockPos> positions) {
        ownRemovals.clear();
        if (positions != null) {
            ownRemovals.addAll(positions);
        }
    }

    /** Number of items of the given type in the inventory of the player. */
    public int inventoryCount(Item item) {
        AltoClef mod = mod();
        if (mod == null || mod.getItemStorage() == null) {
            return 0;
        }
        return mod.getItemStorage().getItemCountInventoryOnly(item);
    }
}
