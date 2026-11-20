package dev.magehome.core.automation;

/**
 * The ownership of the UnionClef task chain (ТЗ.md section 5).
 *
 * <p>The facts come from the bytecode of the delivered UnionClef 0.95.61 and not from a guess:
 *
 * <ul>
 *   <li>{@code adris.altoclef.tasksystem.Task.thisOrChildSatisfies(Predicate)} tests the task
 *       itself and then its children (the field {@code sub}); it walks down the chain;</li>
 *   <li>{@code adris.altoclef.chains.SingleTaskChain.getCurrentTask()} returns the field
 *       {@code mainTask}, the root task of the chain;</li>
 *   <li>{@code AltoClef.runUserTask(task)} puts the own root task into {@code mainTask}.</li>
 * </ul>
 *
 * <p>Therefore the own task and every task below it are never a foreign task, and the chain of
 * another owner is a foreign task. The decision is kept here, free of game types, so that it can
 * be checked without Minecraft.
 */
public final class TaskOwnership {

    /** How the task that currently owns the chain relates to the own root task. */
    public enum Relation {
        /** The chain is empty; nobody owns it. */
        NONE,
        /** The current task is the own root task. */
        OWN_ROOT,
        /** The own root task is a child of the current task. */
        OWN_DESCENDANT,
        /** The chain belongs to another owner. */
        FOREIGN,
        /**
         * The chain of UnionClef does not exist yet.
         *
         * <p>The fact comes from the bytecode of the delivered UnionClef 0.95.61:
         * {@code AltoClef.getUserTaskChain()} returns the field {@code userTaskChain} without a
         * check, and the field is filled in {@code AltoClef.onInitializeLoad()}. Before that
         * method ran, the chain is {@code null}: the chain is not empty, it does not exist at all.
         * That is a fact of the environment and not a foreign owner, but the add-on may not own a
         * task while UnionClef cannot hold one (ТЗ.md section 5).
         */
        CHAIN_UNAVAILABLE
    }

    private TaskOwnership() {
    }

    /**
     * The relation between the current task of the chain and the own root task.
     *
     * @param ownRootIsCurrent      the current task is the own root task
     * @param ownRootIsBelowCurrent the own root task is the current task or one of its children
     *                             (the result of {@code current.thisOrChildSatisfies(t -> t == root)})
     */
    public static Relation relation(boolean ownRootIsCurrent, boolean ownRootIsBelowCurrent) {
        if (!ownRootIsBelowCurrent) {
            return Relation.FOREIGN;
        }
        return ownRootIsCurrent ? Relation.OWN_ROOT : Relation.OWN_DESCENDANT;
    }

    /** The relation of a chain that holds no task at all. */
    public static Relation noTask() {
        return Relation.NONE;
    }

    /**
     * The relation of a chain that does not exist yet.
     *
     * <p>The chain of UnionClef is created in {@code AltoClef.onInitializeLoad()}; before that
     * the field {@code userTaskChain} of the mod is {@code null}. The add-on waits in that case
     * and starts no task of its own.
     */
    public static Relation unavailableChain() {
        return Relation.CHAIN_UNAVAILABLE;
    }

    /** True when another owner controls the chain. */
    public static boolean foreign(Relation relation) {
        return relation == Relation.FOREIGN;
    }

    /** True when the chain of UnionClef does not exist yet. */
    public static boolean chainUnavailable(Relation relation) {
        return relation == Relation.CHAIN_UNAVAILABLE;
    }
}
