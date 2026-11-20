package dev.magehome.core.automation;

/**
 * The protocol of the root task of the add-on inside the task chain of UnionClef (ТЗ.md
 * section 5).
 *
 * <p>The facts come from the bytecode of the delivered UnionClef 0.95.61:
 *
 * <ul>
 *   <li>{@code Task.tick(TaskChain)} calls {@code onTick()} and takes the task that it returns as
 *       the child of the ticking task, when the ticking task has no child yet:
 *       {@code canBeInterrupted(null, task)} answers {@code true} for every task;</li>
 *   <li>the child is ticked right after that, so the tick of the chain walks down the children;
 *       a task that returns <em>itself</em> becomes its own child and the tick recurses without
 *       an end until the stack of the client is gone ({@code StackOverflowError});</li>
 *   <li>a task that returns {@code null} holds no child: the child that is there is stopped when
 *       it may be interrupted.</li>
 *   <li>the frames of that crash repeat exactly: the assignment of the child is skipped when the
 *       returned task is equal to the child that is already there, and the child is ticked again
 *       ({@code Task.tick}, line 68, called from line 34). The report of the run of the round 6
 *       shows these frames repeated and nothing else.</li>
 * </ul>
 *
 * <p>The root task of the add-on may therefore return the navigation task or nothing, but never
 * itself. The decision lives here, free of game types, so that it can be checked without
 * Minecraft and without UnionClef.
 */
public final class TaskProtocol {

    /** What the root task of the add-on does in one tick. */
    public enum Decision {
        /**
         * The root task holds no child task.
         *
         * <p>The task returns {@code null}. A child that is still there is released, and the
         * chain of UnionClef holds the root task of the add-on alone.
         */
        NO_CHILD("the root task holds no child task"),
        /**
         * The root task holds the navigation task as its child.
         *
         * <p>The navigation is a child task of the root task of the add-on and not a root task
         * of its own (ТЗ.md section 5).
         */
        NAVIGATION_CHILD("the root task holds the navigation task as its child");

        private final String description;

        Decision(String description) {
            this.description = description;
        }

        public String description() {
            return description;
        }
    }

    private TaskProtocol() {
    }

    /**
     * The decision of the root task of the add-on in one tick.
     *
     * @param enabled             the automation is enabled
     * @param suspended           the automation waits for an explicit resume
     * @param navigationRequested the add-on wants the player to walk to a target
     */
    public static Decision decide(boolean enabled, boolean suspended, boolean navigationRequested) {
        if (!enabled || suspended) {
            // A disabled or suspended automation holds no child: it does not take the chain of
            // UnionClef away from anybody and it does not move the player.
            return Decision.NO_CHILD;
        }
        if (navigationRequested) {
            return Decision.NAVIGATION_CHILD;
        }
        return Decision.NO_CHILD;
    }

    /**
     * True when the task that the root task would return is the root task itself.
     *
     * <p>Such a task must never be returned: the task would become its own child and the tick
     * of the chain of UnionClef would recurse without an end (see the class comment). The
     * method exists so that the client can name the case instead of relying on the reader of
     * the code.
     *
     * @param candidate the task that the root task would return; null when it returns nothing
     * @param ownRoot   the root task of the add-on
     */
    public static boolean isOwnRoot(Object candidate, Object ownRoot) {
        return candidate != null && ownRoot != null && candidate == ownRoot;
    }
}
