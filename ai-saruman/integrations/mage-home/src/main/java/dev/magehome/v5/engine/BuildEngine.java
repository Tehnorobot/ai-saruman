package dev.magehome.v5.engine;

import com.google.gson.JsonObject;
import dev.magehome.v5.runtime.RunRecord;

/**
 * The engine that carries out a run.
 *
 * <p>The interface is the boundary between the public contract and the execution. The contract
 * owns the identities, the generations, the states and the release of the control; the engine
 * owns the survey of the site, the preparation, the placement, the verification, the cleanup and
 * the exit. Every method is called on the client thread.
 */
public interface BuildEngine {

    /** The name of the engine; it is written into the report. */
    String name();

    /** Reject malformed/unsupported input before admitting a run or performing an action. */
    default void validateStart(JsonObject request) {
    }

    /**
     * Starts the work of a run.
     *
     * <p>The call happens on the client thread right after the request was accepted. The engine
     * may not block here: the request is accepted quickly and the work happens in the ticks.
     *
     * @param run the accepted run
     */
    void onStart(RunRecord run);

    /**
     * Advances a run by one client tick.
     *
     * <p>The call happens only for a run that is working and that holds the control of the module.
     * The engine may not block here.
     *
     * @param run  the run
     * @param tick the number of the client tick
     */
    void onTick(RunRecord run, long tick);

    /**
     * Revokes the permission of the module to act and releases the controls it owns.
     *
     * <p>The call happens on the client thread and must not wait for a task, for a cleanup, for an
     * exit, for a disk write or for a network answer. The global control of the other addons
     * belongs to the caller and is not touched here.
     *
     * @param run    the run
     * @param reason the structured reason of the release
     */
    void releaseControl(RunRecord run, String reason);

    /**
     * Ends the work of a stopped run.
     *
     * <p>The cleanup debt of the run is kept; the cleanup starts only after an explicit action or
     * after a new run of the project.
     *
     * @param run    the run
     * @param reason the structured reason of the stop
     */
    void onStop(RunRecord run, String reason);

    /**
     * Continues a run after the world and the control availability were checked again.
     *
     * @param run the run
     */
    void onResume(RunRecord run);

    /** Admit a locally scoped, fully validated design revision before it can affect the world. */
    default void onReplan(RunRecord run, JsonObject blueprint, JsonObject recovery) {
        throw new UnsupportedOperationException("this engine does not support design revisions");
    }

    default boolean canAutoResume(RunRecord run) { return false; }

    /** The progress of the preparation of the site; never null. */
    JsonObject sitePreparation(RunRecord run);

    /** The progress of the run; never null. */
    JsonObject progress(RunRecord run);

    /** The state of the expert of the run; never null. */
    JsonObject expert(RunRecord run);
}
