package dev.magehome.core.automation;

import java.util.List;

/**
 * The judgement about the ownership of the task chain of UnionClef during an observation
 * (ТЗ.md section 5).
 *
 * <p>The facts come from the bytecode of the delivered UnionClef 0.95.61:
 *
 * <ul>
 *   <li>{@code SingleTaskChain.setTask(task)} stops the root task that the chain holds when the
 *       new task is a different one, and then puts the new task into the chain;</li>
 *   <li>{@code AltoClef.runUserTask(task)} calls {@code setTask(task)};</li>
 *   <li>{@code SingleTaskChain.getCurrentTask()} returns the root task that the chain holds,
 *       also when that task is stopped already;</li>
 *   <li>{@code TaskChain.stop()} stops the root task that the chain holds;</li>
 *   <li>{@code Task.stopped()} is true only after {@code Task.stop(..)} was called, and
 *       {@code Task.isFinished()} is the decision of the task itself that it is done.</li>
 * </ul>
 *
 * <p>The rule therefore judges two things at once, because neither of them alone is enough:
 *
 * <ul>
 *   <li>the <em>conduct</em> of the add-on: every call that the add-on makes on the chain of
 *       UnionClef is recorded together with the task that the chain held at that moment and with
 *       the state of that task. A call while the chain holds the living task of another owner is
 *       forbidden: {@code setTask} would stop that task. This is the only way the add-on could
 *       take the chain away from another owner, so the record of the calls decides that question.
 *       A task that had already ended is not a living owner, and the add-on harms nobody by
 *       taking the chain from it;</li>
 *   <li>the <em>state</em> of the chain in every tick of the window: the add-on has to hold its
 *       own root task at some point of the window, and it has to start it when the chain is
 *       empty. The state alone cannot tell whether the add-on took the chain from another
 *       owner: the chain of UnionClef changes inside its own task tick, so a foreign task that
 *       ends and the own task that starts in the same tick are seen as a jump from one owner to
 *       the other without an empty chain in between. Such a jump is not a failure, but it is
 *       only lawful when the record of the calls explains it.</li>
 * </ul>
 *
 * <p>The decision lives here, free of game types, so that it can be checked without Minecraft.
 */
public final class TaskChainOwnership {

    /** The state of the task that owns the chain of UnionClef. */
    public enum OwnerState {
        /** The task is neither finished nor stopped. */
        ALIVE,
        /** The task says itself that it is done. */
        FINISHED,
        /** The task was stopped by {@code Task.stop(..)}. */
        STOPPED,
        /** The state could not be read. */
        UNKNOWN
    }

    /** What the chain of UnionClef held when the add-on acted on it. */
    public enum Held {
        /** The chain held no task at all. */
        CHAIN_EMPTY,
        /** The chain held the own root task of the add-on. */
        OWN_ROOT,
        /** The chain held the task of another owner. */
        FOREIGN
    }

    /** What the add-on did with the chain of UnionClef. */
    public enum Action {
        /** The add-on started its own root task. */
        START_OWN_TASK,
        /** The add-on re-armed its own root task. */
        REARM_OWN_ROOT,
        /** The add-on stopped the chain of UnionClef. */
        STOP_CHAIN
    }

    /** One observed state of the chain of UnionClef. */
    public record Sample(TaskOwnership.Relation relation, OwnerState ownerState) {

        public Sample {
            if (relation == null) {
                throw new IllegalArgumentException("the relation of a sample is never null");
            }
            ownerState = ownerState == null ? OwnerState.UNKNOWN : ownerState;
        }
    }

    /**
     * One call of the add-on on the chain of UnionClef.
     *
     * <p>The state of the task that the chain held at that moment belongs to the record: a call
     * while the chain holds the task of another owner would stop that task, and that is
     * forbidden (ТЗ.md section 5). A task that had already ended is not a living owner any more,
     * so taking the chain from it harms nobody. The chain of UnionClef changes inside its own
     * task tick, so a foreign task that ends and the own task that starts in the same tick are
     * seen as one jump from one owner to the other; the state of the held task tells such a jump
     * from a real taking of the chain away from a living owner.
     *
     * <p>A state that could not be read is unknown. An unknown state is judged like a living
     * one, because the harm of the add-on must not be assumed away.
     *
     * @param action    what the add-on did with the chain
     * @param held      what the chain held when the add-on acted on it
     * @param heldState the state of the task that the chain held
     */
    public record AddOnAction(Action action, Held held, OwnerState heldState) {

        /** A call whose held task has no known state. */
        public AddOnAction(Action action, Held held) {
            this(action, held, OwnerState.UNKNOWN);
        }

        public AddOnAction {
            if (action == null) {
                throw new IllegalArgumentException("the action of a call is never null");
            }
            held = held == null ? Held.FOREIGN : held;
            heldState = heldState == null ? OwnerState.UNKNOWN : heldState;
        }

        /**
         * True when the chain held the task of another owner that had already ended.
         *
         * <p>Such a task is not a living owner: taking the chain from it harms nobody and is no
         * violation of ТЗ.md section 5.
         */
        public boolean heldTaskHadEnded() {
            return held == Held.FOREIGN
                    && (heldState == OwnerState.FINISHED || heldState == OwnerState.STOPPED);
        }
    }

    /** The verdict of an observation of the chain. */
    public enum Verdict {
        /**
         * The add-on held its own root task and never took the chain from another owner.
         */
        OWN_ROOT_HELD("the chain of UnionClef belongs to the add-on", false, true),
        /**
         * The chain belonged to a task of another owner and the add-on did not take it.
         *
         * <p>This is no failure of the add-on: it has to wait while another owner holds the
         * chain (ТЗ.md section 5). The stand cannot show the work of the house in that window,
         * so the own root task of the add-on was not observed either.
         */
        WAITING_FOR_FOREIGN("the chain of UnionClef belonged to another owner and the add-on did not touch it",
                false, false),
        /**
         * The chain was empty and the add-on did not start its own task.
         *
         * <p>An empty chain is the one case in which the add-on has to act: it starts its own
         * root task there.
         */
        EMPTY_CHAIN_IGNORED("the chain of UnionClef was empty and the add-on did not start its own task",
                true, false),
        /**
         * The add-on took the chain from the task of another owner.
         *
         * <p>The task of the other owner would have been stopped by {@code setTask}; that is
         * forbidden (ТЗ.md section 5).
         */
        FOREIGN_DISPLACED("the add-on took the chain of UnionClef from the task of another owner",
                true, false),
        /**
         * The add-on stopped the chain while it held the task of another owner.
         *
         * <p>{@code TaskChain.stop()} stops the root task that the chain holds; a foreign task
         * must not be stopped (ТЗ.md section 5).
         */
        FOREIGN_TASK_STOPPED("the add-on stopped the chain of UnionClef that held the task of another owner",
                true, false),
        /**
         * The own root task was never the root of the chain of UnionClef.
         *
         * <p>All the actions of the add-on are one root task of UnionClef (ТЗ.md section 5); a
         * chain that holds another task above the own root task does not satisfy that.
         */
        OWN_ROOT_NOT_HELD("the own root task of the add-on was never the root of the chain of UnionClef",
                true, false),
        /**
         * The chain of UnionClef was empty at the end of the window.
         *
         * <p>The add-on held its own root task at some point of the window, and the chain was
         * empty at the end of it. The observation proves then neither that the add-on kept the
         * chain nor that it took the chain away from a living owner of another task.
         *
         * <p>An interruption empties the chain ({@code SingleTaskChain.onStop} stops the root
         * task and clears it), and after such an interruption the add-on waits for an explicit
         * resume (ТЗ.md section 5). The empty chain is therefore no violation of the add-on, but
         * the own root task was not held at the end of the window: the case stays unverified.
         */
        OWN_ROOT_EMPTY_AT_THE_END("the chain of UnionClef was empty at the end of the window although the add-on "
                + "had held its own root task", false, false),
        /**
         * The observation does not explain how the owner of the chain changed.
         *
         * <p>The change of the owner was seen, but the record of the calls of the add-on does
         * not explain it. Nothing may be concluded from such an observation: it is not a
         * failure of the add-on and not a proof of its behaviour either.
         */
        OBSERVATION_INCOMPLETE("the observation of the chain does not explain the change of its owner",
                false, false),
        /**
         * The chain of UnionClef did not exist during the observation.
         *
         * <p>That is a fact of the environment and not a behaviour of the add-on.
         */
        CHAIN_UNAVAILABLE("the chain of UnionClef did not exist during the observation", false, false);

        private final String description;
        private final boolean violation;
        private final boolean verified;

        Verdict(String description, boolean violation, boolean verified) {
            this.description = description;
            this.violation = violation;
            this.verified = verified;
        }

        public String description() {
            return description;
        }

        /** True when the add-on broke the rule of the ownership of the chain. */
        public boolean violation() {
            return violation;
        }

        /**
         * True when the observation proves that the own root task of the add-on was the root of
         * the chain of UnionClef.
         */
        public boolean verified() {
            return verified;
        }

        /** True when the add-on behaved as the specification demands. */
        public boolean accepted() {
            return !violation;
        }
    }

    /** What happened to the task of another owner. */
    public enum ForeignFate {
        /** No task of another owner was seen in the window. */
        NOT_OBSERVED("no task of another owner was seen"),
        /** The task of another owner was alive in the last tick of the window. */
        ALIVE("the task of the other owner was alive"),
        /** The task of another owner ended by itself. */
        ENDED_BY_ITSELF("the task of the other owner ended by itself"),
        /** The task of another owner was stopped by somebody else. */
        STOPPED_BY_ANOTHER_ACTOR("the task of the other owner was stopped, but not by the add-on"),
        /** The add-on took the chain away from the task of another owner and stopped it. */
        STOPPED_BY_THE_ADD_ON("the add-on stopped the task of the other owner");

        private final String description;

        ForeignFate(String description) {
            this.description = description;
        }

        public String description() {
            return description;
        }

        /** True when the add-on harmed the task of another owner. */
        public boolean harmedByTheAddOn() {
            return this == STOPPED_BY_THE_ADD_ON;
        }
    }

    /**
     * What the observation of the task of another owner shows.
     *
     * <p>The three answers are kept apart on purpose: a task of another owner that was never
     * seen proves nothing. The laboratory cannot create such a task, so the norm of ТЗ.md
     * section 5 cannot be observed in the game; the case is then unverified and never a proven
     * norm.
     */
    public enum ForeignTaskVerdict {
        /** The add-on took the chain away from the task of another owner and stopped it. */
        HARMED("the add-on took the chain away from the task of another owner and stopped it"),
        /** The task of another owner was observed and the add-on did not touch it. */
        UNTOUCHED("the task of another owner was observed and the add-on did not touch it"),
        /** No task of another owner was observed: the norm was not observed either. */
        NOT_OBSERVED("no task of another owner was observed, so the norm of the add-on was not observed either");

        private final String description;

        ForeignTaskVerdict(String description) {
            this.description = description;
        }

        public String description() {
            return description;
        }

        /** True when the add-on broke the rule of the ownership of the chain. */
        public boolean violation() {
            return this == HARMED;
        }

        /** True when the observation proves that the task of another owner was not touched. */
        public boolean verified() {
            return this == UNTOUCHED;
        }
    }

    private TaskChainOwnership() {
    }

    /**
     * The verdict of the observed states of the chain and of the calls of the add-on.
     *
     * @param samples the state of the chain in the order of the ticks of the observation
     * @param actions the calls that the add-on made on the chain inside the window
     */
    public static Verdict judge(List<Sample> samples, List<AddOnAction> actions) {
        if (samples == null || samples.isEmpty()) {
            return Verdict.OBSERVATION_INCOMPLETE;
        }
        List<AddOnAction> calls = actions == null ? List.of() : actions;

        // The conduct of the add-on comes first: a call on a chain that holds the task of
        // another owner is a violation whatever the states of the chain say. The exception is a
        // task that had already ended: it is not a living owner, and the add-on harms nobody by
        // taking the chain from it.
        for (AddOnAction call : calls) {
            if (call == null) {
                continue;
            }
            if (call.held() != Held.FOREIGN) {
                continue;
            }
            if (call.heldTaskHadEnded()) {
                continue;
            }
            if (call.action() == Action.STOP_CHAIN) {
                return Verdict.FOREIGN_TASK_STOPPED;
            }
            return Verdict.FOREIGN_DISPLACED;
        }

        boolean ownRootSeen = false;
        boolean descendantSeen = false;
        boolean foreignSeen = false;
        boolean emptySeen = false;
        boolean unavailableSeen = false;
        int changesOfTheOwner = 0;
        TaskOwnership.Relation previous = null;
        for (Sample sample : samples) {
            if (sample == null) {
                continue;
            }
            TaskOwnership.Relation relation = sample.relation();
            switch (relation) {
                case OWN_ROOT -> {
                    ownRootSeen = true;
                    if (previous != null && previous != TaskOwnership.Relation.OWN_ROOT) {
                        changesOfTheOwner++;
                    }
                }
                case OWN_DESCENDANT -> descendantSeen = true;
                case FOREIGN -> foreignSeen = true;
                case NONE -> emptySeen = true;
                case CHAIN_UNAVAILABLE -> unavailableSeen = true;
                default -> {
                    // every relation of the enumeration is named above
                }
            }
            previous = relation;
        }

        if (descendantSeen) {
            // The chain held a task above the own root task: the actions of the add-on ran
            // under a root task that is not its own (ТЗ.md section 5).
            return Verdict.OWN_ROOT_NOT_HELD;
        }

        if (!ownRootSeen) {
            // The own root task was never the root of the chain. An empty chain is the duty of
            // the add-on, a chain of another owner is a reason to wait, and a chain that does
            // not exist is a fact of the environment.
            if (emptySeen) {
                return Verdict.EMPTY_CHAIN_IGNORED;
            }
            if (foreignSeen) {
                return Verdict.WAITING_FOR_FOREIGN;
            }
            if (unavailableSeen) {
                return Verdict.CHAIN_UNAVAILABLE;
            }
            return Verdict.OBSERVATION_INCOMPLETE;
        }

        // The own root task was seen. The change of the owner has to be explained by a call of
        // the add-on: without the record of the calls the observation proves nothing.
        int starts = 0;
        for (AddOnAction call : calls) {
            if (call == null) {
                continue;
            }
            if (call.action() == Action.START_OWN_TASK || call.action() == Action.REARM_OWN_ROOT) {
                starts++;
            }
        }
        if (changesOfTheOwner > starts) {
            return Verdict.OBSERVATION_INCOMPLETE;
        }
        if (previous == TaskOwnership.Relation.NONE) {
            // The add-on held its own root task, and the chain was empty at the end of the
            // window. The own root task was not held then, so the window proves nothing about
            // the ownership of the chain; it is no violation either, because an interruption of
            // the add-on empties the chain and the add-on waits for an explicit resume then
            // (ТЗ.md section 5).
            return Verdict.OWN_ROOT_EMPTY_AT_THE_END;
        }
        return Verdict.OWN_ROOT_HELD;
    }

    /**
     * What the observation of the task of another owner shows.
     *
     * <p>The decision lives here, so that the harness of the game and the executable checks of
     * the core judge the task of another owner by one criterion.
     *
     * @param fate what happened to the task of another owner inside the window
     */
    public static ForeignTaskVerdict foreignTaskUntouched(ForeignFate fate) {
        if (fate == null || fate == ForeignFate.NOT_OBSERVED) {
            // No task of another owner was seen: the add-on harmed nobody, but the norm was not
            // observed either, and the case is not a proven norm.
            return ForeignTaskVerdict.NOT_OBSERVED;
        }
        if (fate.harmedByTheAddOn()) {
            return ForeignTaskVerdict.HARMED;
        }
        return ForeignTaskVerdict.UNTOUCHED;
    }

    /**
     * What happened to the task of another owner inside the window.
     *
     * <p>The add-on may not take such a task away and may not stop it (ТЗ.md section 5). The
     * record of the calls decides whether the add-on did it: a call on the chain while it held
     * the task of another owner would have stopped that task. A task that had already ended when
     * the add-on acted was not harmed: its fate is then its own end, not the work of the add-on.
     *
     * @param samples the state of the chain in the order of the ticks of the observation
     * @param actions the calls that the add-on made on the chain inside the window
     */
    public static ForeignFate foreignFate(List<Sample> samples, List<AddOnAction> actions) {
        if (samples == null || samples.isEmpty()) {
            return ForeignFate.NOT_OBSERVED;
        }
        List<AddOnAction> calls = actions == null ? List.of() : actions;
        OwnerState last = null;
        boolean foreignSeen = false;
        for (Sample sample : samples) {
            if (sample == null || sample.relation() != TaskOwnership.Relation.FOREIGN) {
                continue;
            }
            foreignSeen = true;
            last = sample.ownerState();
        }
        if (!foreignSeen) {
            return ForeignFate.NOT_OBSERVED;
        }
        for (AddOnAction call : calls) {
            if (call == null || call.held() != Held.FOREIGN) {
                continue;
            }
            if (call.heldState() == OwnerState.ALIVE || call.heldState() == OwnerState.UNKNOWN) {
                // The add-on called the chain while it held the living task of another owner:
                // the call would have stopped that task.
                return ForeignFate.STOPPED_BY_THE_ADD_ON;
            }
            if (call.heldState() == OwnerState.FINISHED) {
                // The task of the other owner had already ended by itself when the add-on acted.
                return ForeignFate.ENDED_BY_ITSELF;
            }
            // The task of the other owner had been stopped by somebody else.
            return ForeignFate.STOPPED_BY_ANOTHER_ACTOR;
        }
        if (last == OwnerState.STOPPED) {
            return ForeignFate.STOPPED_BY_ANOTHER_ACTOR;
        }
        if (last == OwnerState.FINISHED) {
            return ForeignFate.ENDED_BY_ITSELF;
        }
        return ForeignFate.ALIVE;
    }
}
