package dev.magehome.v5.runtime;

/**
 * The states of one run of the building module (contract {@code mage_home:local_no_jev_v5}).
 *
 * <p>The names are the states of the contract. A state is terminal for the generation that
 * reached it: a stopped, blocked, failed or ready run is never revived by a late answer of the
 * model, by the end of a fight or by a reconnect. Continuing a stopped project needs a new
 * explicit start.
 */
public enum RunState {

    /** No run was accepted yet. */
    IDLE,
    /** The blueprint is compiled or validated. */
    PLANNING,
    /** The site is surveyed and prepared. */
    PREPARING,
    /** The cells of the house are placed. */
    BUILDING,
    /** The world is compared with the blueprint. */
    VERIFYING,
    /** The temporary infrastructure is removed. */
    CLEANUP,
    /** The player walks to the exit. */
    EXITING,
    /** The house was verified and the cleanup is closed. */
    READY,
    /** The run is paused and keeps its progress, its plan and its equipment. */
    PAUSED,
    /** The run is terminal; the cleanup debt is kept. */
    STOPPED,
    /** The run cannot continue and names the concrete reason. */
    BLOCKED,
    /** The run failed and names the concrete reason. */
    FAILED;

    /** True when the state ends the run of that generation. */
    public boolean terminal() {
        return this == READY || this == STOPPED || this == BLOCKED || this == FAILED;
    }

    /** True when the run is working and may issue new game actions. */
    public boolean working() {
        return this == PLANNING || this == PREPARING || this == BUILDING
                || this == VERIFYING || this == CLEANUP || this == EXITING;
    }
}
