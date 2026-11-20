package dev.magehome.v5.runtime;

import com.google.gson.JsonObject;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The registry of the runs of the module.
 *
 * <p>The registry owns the stable identities: a repeated {@code requestId} with an identical
 * payload preserves the existing run and reports {@code duplicate=true}; the same
 * {@code requestId} with another payload is a conflict and is rejected before any mutation.
 */
public final class RunRegistry {

    /** The runs by their identity, in the order of their creation. */
    private final Map<String, RunRecord> runs = new LinkedHashMap<>();
    /** The run of every accepted request identity. */
    private final Map<String, String> runIdByRequestId = new LinkedHashMap<>();
    /** The fingerprint of every accepted request identity. */
    private final Map<String, String> fingerprintByRequestId = new LinkedHashMap<>();
    private final Map<String, String> selectedByWorld = new LinkedHashMap<>();

    /** The result of an accepted or rejected request. */
    public record Admission(RunRecord run, boolean duplicate) {
    }

    /** The reason why a request was rejected. */
    public enum Conflict {
        /** The request identity exists with another payload. */
        DIFFERENT_PAYLOAD,
        /** The request identity exists but its run is not readable. */
        UNREADABLE
    }

    /**
     * Registers a request.
     *
     * <p>When the request identity is known, the existing run is returned and the fingerprint is
     * compared: an equal fingerprint keeps the run, another one is a conflict.
     *
     * @param requestId   the identity of the request
     * @param fingerprint the fingerprint of the accepted payload
     * @param factory     the factory of a new run; it is called only for an unknown request
     * @return the admission of the request; null when the request conflicts
     */
    public synchronized Admission admit(String requestId, String fingerprint,
                                         java.util.function.Supplier<RunRecord> factory) {
        String known = runIdByRequestId.get(requestId);
        if (known != null) {
            RunRecord existing = runs.get(known);
            if (existing == null) {
                return null;
            }
            String knownFingerprint = fingerprintByRequestId.get(requestId);
            if (!knownFingerprint.equals(fingerprint)) {
                return null;
            }
            return new Admission(existing, true);
        }
        RunRecord created = factory.get();
        runs.put(created.runId(), created);
        select(created);
        runIdByRequestId.put(requestId, created.runId());
        fingerprintByRequestId.put(requestId, fingerprint);
        return new Admission(created, false);
    }

    /** The run of the given identity; null when there is none. */
    public synchronized RunRecord run(String runId) {
        if (runId == null || runId.isBlank()) {
            return null;
        }
        return runs.get(runId);
    }

    /** The run already admitted for this logical request. */
    public synchronized RunRecord byRequestId(String requestId) {
        return runs.get(runIdByRequestId.get(requestId));
    }

    /** The newest run; null when there is none. */
    public synchronized RunRecord currentRun() {
        RunRecord last = null;
        for (RunRecord run : runs.values()) {
            last = run;
        }
        return last;
    }

    /** The last project explicitly started or resumed in this world. */
    public synchronized RunRecord currentRun(String worldUid) {
        return runs.get(selectedByWorld.get(worldUid));
    }

    public synchronized void select(RunRecord run) {
        selectedByWorld.put(run.worldUid(), run.runId());
    }

    public synchronized boolean busy(RunRecord except) {
        return runs.values().stream().anyMatch(run -> run != except
                && (run.state().working() || !run.controlReleased()));
    }

    /** All runs in the order of their creation. */
    public synchronized List<RunRecord> allRuns() {
        return new ArrayList<>(runs.values());
    }

    /** The runs that may still issue game actions. */
    public synchronized List<RunRecord> workingRuns() {
        List<RunRecord> result = new ArrayList<>();
        for (RunRecord run : runs.values()) {
            if (run.state().working()) {
                result.add(run);
            }
        }
        return result;
    }

    /** Forgets a run identity; it is used when the client leaves the world. */
    public synchronized void forget(String runId) {
        RunRecord run = runs.remove(runId);
        if (run != null) {
            selectedByWorld.remove(run.worldUid(), runId);
            runIdByRequestId.remove(run.requestId());
            fingerprintByRequestId.remove(run.requestId());
        }
    }

    /** The number of the known runs. */
    public synchronized int size() {
        return runs.size();
    }

    /**
     * The identity of the world of a context.
     *
     * <p>The identity is the stable identity of the world when the context names one; otherwise
     * it is derived from the server address and the dimension, so that two worlds with the same
     * coordinates never conflict and a run of another world is rejected.
     *
     * @param context the context of a request
     * @return the identity of the world; null when the context does not name one
     */
    public static String worldUidOf(JsonObject context) {
        if (context == null) {
            return null;
        }
        JsonObject world = context.has("world") && context.get("world").isJsonObject()
                ? context.getAsJsonObject("world") : null;
        if (world == null) {
            return null;
        }
        if (world.has("worldUid") && world.get("worldUid").isJsonPrimitive()
                && world.get("worldUid").getAsJsonPrimitive().isString()) {
            String uid = world.get("worldUid").getAsString();
            return uid.isBlank() ? null : uid;
        }
        String server = world.has("server") && !world.get("server").isJsonNull()
                ? world.get("server").getAsString() : "";
        String dimension = world.has("dimension") && !world.get("dimension").isJsonNull()
                ? world.get("dimension").getAsString() : "";
        if (server.isBlank() || dimension.isBlank()) {
            return null;
        }
        return server + "|" + dimension;
    }

    /** The reason why a request was rejected; it is used for the error envelope. */
    public enum Reason {
        /** The request identity exists with another payload. */
        DUPLICATE_CONFLICT,
        /** The context names a world that is not the world of the client. */
        STALE_WORLD,
        /** The request asks for something the module does not do. */
        UNSUPPORTED_OPERATION,
        /** A required field is missing or malformed. */
        INVALID_REQUEST,
        /** The run is unknown. */
        UNKNOWN_RUN,
        /** The run is terminal. */
        RUN_TERMINAL,
        /** The module is disabled. */
        MODULE_DISABLED,
        /** The client is not ready. */
        NOT_INITIALIZED,
        /** The world of the run changed. */
        WORLD_MISMATCH
    }
}
