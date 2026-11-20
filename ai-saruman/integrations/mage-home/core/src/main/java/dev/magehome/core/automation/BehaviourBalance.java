package dev.magehome.core.automation;

/**
 * The balance of the behaviour state of UnionClef (ТЗ.md section 7).
 *
 * <p>The behaviour state of the dependency is pushed when the own root task starts and popped
 * when it stops. Every push has to be answered by a pop on every way out: the ordinary stop,
 * the interruption and the error of the tick. The ledger keeps the count of both sides and
 * decides whether the next start really pushes and whether the next stop really pops.
 *
 * <p>The ledger lives in the domain core, free of game types, so that the balance can be checked
 * without Minecraft. The client uses exactly this ledger: the push and the pop of the behaviour
 * of UnionClef are called only when the ledger allows it.
 */
public final class BehaviourBalance {

    /** True while the behaviour state of the dependency is pushed by the add-on. */
    private boolean pushed;
    /** The number of the pushes of the add-on. */
    private int pushes;
    /** The number of the pops of the add-on. */
    private int pops;

    /**
     * The own root task starts.
     *
     * @return true when the behaviour state has to be pushed now
     */
    public boolean onStart() {
        if (pushed) {
            // A task that is re-armed without a stop in between must not push a second time:
            // the push and the pop would not be balanced any more.
            return false;
        }
        pushed = true;
        pushes++;
        return true;
    }

    /**
     * The own root task stops.
     *
     * @return true when the behaviour state has to be popped now
     */
    public boolean onStop() {
        return release();
    }

    /**
     * The tick of the own root task failed.
     *
     * <p>The error is a way out like every other: the behaviour state is released, so that the
     * dependency is not left with the behaviour of the add-on.
     *
     * @return true when the behaviour state has to be popped now
     */
    public boolean onError() {
        return release();
    }

    private boolean release() {
        if (!pushed) {
            // Nothing was pushed: a pop now would take away the behaviour of another owner.
            return false;
        }
        pushed = false;
        pops++;
        return true;
    }

    /** True while the behaviour state of the dependency is pushed by the add-on. */
    public boolean pushed() {
        return pushed;
    }

    /** The number of the pushes of the add-on. */
    public int pushes() {
        return pushes;
    }

    /** The number of the pops of the add-on. */
    public int pops() {
        return pops;
    }

    /** True when every push of the add-on was answered by a pop. */
    public boolean balanced() {
        return pushes == pops;
    }
}
