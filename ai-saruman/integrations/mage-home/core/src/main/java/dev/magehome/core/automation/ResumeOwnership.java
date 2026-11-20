package dev.magehome.core.automation;

/**
 * The decision of the resume about the task chain of UnionClef (ТЗ.md section 5).
 *
 * <p>The facts come from the bytecode of the delivered UnionClef 0.95.61:
 *
 * <ul>
 *   <li>{@code SingleTaskChain.getCurrentTask()} returns the root task of the chain;</li>
 *   <li>{@code SingleTaskChain.setTask(task)} stops the old root task only when it is a different
 *       task and resets the new one;</li>
 *   <li>{@code AltoClef.runUserTask(task)} calls {@code setTask(task)}.</li>
 * </ul>
 *
 * <p>Therefore a resume may re-arm the own root task, but it must never displace the root task
 * of another owner: {@code setTask} would stop that task. The decision is kept here, free of game
 * types, so that it can be checked without Minecraft.
 */
public final class ResumeOwnership {

    /** What the resume does with the task chain of UnionClef. */
    public enum Action {
        /** The chain is empty: the own task is started. */
        START_OWN_TASK("the own task is started"),
        /** The chain holds the own root task: it is re-armed and nothing else is touched. */
        REARM_OWN_ROOT("the own root task is re-armed"),
        /** The chain holds the own root task and it runs already: nothing is done. */
        OWN_TASK_RUNNING("the own task is running"),
        /** The chain holds the task of another owner: it is not touched at all. */
        KEEP_FOREIGN("the task of the other owner is kept"),
        /**
         * The chain of UnionClef does not exist yet: the own task is not started.
         *
         * <p>The chain is created in {@code AltoClef.onInitializeLoad()} of the delivered
         * UnionClef; before that the mod cannot hold a task at all. The add-on waits instead of
         * starting a task that nobody can run (ТЗ.md section 5).
         */
        WAIT_FOR_CHAIN("the chain of UnionClef does not exist yet, the own task is not started");

        private final String description;

        Action(String description) {
            this.description = description;
        }

        public String description() {
            return description;
        }

        /** True when the chain of the other owner stays untouched. */
        public boolean keepsForeignTask() {
            return this == KEEP_FOREIGN;
        }
    }

    private ResumeOwnership() {
    }

    /**
     * The action of the resume.
     *
     * @param relation       the relation of the current task of the chain to the own root task
     * @param ownRootStopped the own root task of the chain is stopped
     */
    public static Action decide(TaskOwnership.Relation relation, boolean ownRootStopped) {
        if (relation == null) {
            return Action.KEEP_FOREIGN;
        }
        return switch (relation) {
            case NONE -> Action.START_OWN_TASK;
            case OWN_ROOT -> ownRootStopped ? Action.REARM_OWN_ROOT : Action.OWN_TASK_RUNNING;
            // A task of another owner is above the own task: the chain is not touched.
            case OWN_DESCENDANT, FOREIGN -> Action.KEEP_FOREIGN;
            // The chain of UnionClef does not exist yet: no task of the add-on is started.
            case CHAIN_UNAVAILABLE -> Action.WAIT_FOR_CHAIN;
        };
    }
}
