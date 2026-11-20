package dev.magehome.v5.runtime;

import com.google.gson.JsonObject;

/**
 * One run of the building module.
 *
 * <p>The record carries the identity of the run, the generation of its permit and the state that
 * the status operation publishes. It is created on the client thread and read from the thread of
 * the control server, therefore every field that the other thread observes is volatile.
 */
public final class RunRecord {

    /** The stable identity of the project. Several runs may belong to one project. */
    private final String projectId;
    /** The stable identity of this run. */
    private final String runId;
    /** The identity of the request that created this run. */
    private final String requestId;
    /** The fingerprint of the accepted request; a repeated requestId with another payload is a conflict. */
    private final String fingerprint;
    /** The identity of the world this run is bound to. */
    private final String worldUid;
    /** The accepted context of the request. */
    private final JsonObject context;
    /** One approved blueprint for this run, absent until text design commits. */
    private volatile JsonObject blueprint;
    private final String description;
    private final JsonObject options;
    private boolean engineStarted;
    private JsonObject recovery;
    private JsonObject designFeedback;
    private final java.util.Set<String> rejectedDesigns = new java.util.HashSet<>();

    /** The generation of the permit. It grows with every resume. */
    private volatile int generation;
    /** The state of the run. */
    private volatile RunState state = RunState.PLANNING;
    /** The current phase of the run. */
    private volatile String phase = "PLANNING";
    /** The revision of the blueprint this run is bound to. */
    private volatile int schemaRevision = 1;
    /** True while the module may issue game actions for this run. */
    private volatile boolean controlHeld = true;
    /** True when the module holds no game input for this run. */
    private volatile boolean controlReleased;
    /** The tick on which the release of the control was requested; -1 means never. */
    private volatile long releaseRequestedTick = -1L;
    /** The tick on which the control was released; -1 means never. */
    private volatile long releasedTick = -1L;
    /** The structured reason of the last release. */
    private volatile String releaseReason = "";
    /** The number of the processed client ticks between the request and the release. */
    private volatile int releaseLatencyTicks = -1;
    /** The tick on which the run was created. */
    private final long createdTick;
    /** The last processed client tick of the run. */
    private volatile long updatedTick;

    /** The counters of the mutations; the physical and the command way are counted apart. */
    private volatile int physicalMutations;
    private volatile int commandMutations;
    private volatile int teleportMutations;

    /** The temporary blocks that still have to be removed. */
    private volatile int cleanupDebt;
    /** The obligations to restore cells. */
    private volatile int restorationDebt;

    /** The state of the expert of this run. */
    private volatile String expertState = "IDLE";
    /** The incident the expert is working on. */
    private volatile String expertIncidentId = "";

    /** The reason why the run does not act right now. */
    private volatile String waitingReason = "";
    /** The owner of the game input: builder, unionclef, foreign or none. */
    private volatile String inputOwner = "builder";

    /**
     * Creates a run.
     *
     * @param projectId   the identity of the project
     * @param runId       the identity of the run
     * @param requestId   the identity of the request
     * @param fingerprint the fingerprint of the accepted request
     * @param worldUid    the identity of the world
     * @param context     the accepted context
     * @param blueprint   the accepted blueprint; may be null
     * @param tick        the current client tick
     */
    public RunRecord(String projectId, String runId, String requestId, String fingerprint,
                     String worldUid, JsonObject context, JsonObject blueprint, long tick) {
        this(projectId, runId, requestId, fingerprint, worldUid, context, blueprint, "", null, tick);
    }

    public RunRecord(String projectId, String runId, String requestId, String fingerprint,
                     String worldUid, JsonObject context, JsonObject blueprint, String description,
                     JsonObject options, long tick) {
        this.projectId = projectId;
        this.runId = runId;
        this.requestId = requestId;
        this.fingerprint = fingerprint;
        this.worldUid = worldUid;
        this.context = context.deepCopy();
        this.blueprint = blueprint == null ? null : blueprint.deepCopy();
        this.description = description;
        this.options = options == null ? new JsonObject() : options.deepCopy();
        this.createdTick = tick;
        this.updatedTick = tick;
    }

    public String projectId() {
        return projectId;
    }

    public String runId() {
        return runId;
    }

    public String requestId() {
        return requestId;
    }

    public String fingerprint() {
        return fingerprint;
    }

    public String worldUid() {
        return worldUid;
    }

    public JsonObject context() {
        return context;
    }

    public JsonObject blueprint() {
        return blueprint;
    }

    public String description() {
        return description;
    }

    public JsonObject options() {
        return options;
    }

    public boolean continuousRecovery() {
        var value = options.get("continuousRecovery");
        return value != null && value.isJsonPrimitive() && value.getAsJsonPrimitive().isBoolean() && value.getAsBoolean();
    }

    void acceptDesignedBlueprint(JsonObject value) {
        blueprint = value.deepCopy();
    }

    JsonObject recovery() { return recovery; }

    void recovery(JsonObject value) { recovery = value; }

    JsonObject designFeedback() { return designFeedback; }

    boolean rememberDesignRejection(String fingerprint, JsonObject feedback) {
        if (!rejectedDesigns.add(fingerprint)) return false;
        if (designFeedback != null && designFeedback.get("detail").equals(feedback.get("detail"))) return false;
        designFeedback = feedback;
        return true;
    }

    void clearDesignRejections() {
        designFeedback = null;
        rejectedDesigns.clear();
    }

    boolean engineStarted() {
        return engineStarted;
    }

    void engineStarted(boolean value) {
        engineStarted = value;
    }

    public int generation() {
        return generation;
    }

    /** Raises the generation of the permit. A callback of the previous generation is stale. */
    public int nextGeneration() {
        generation++;
        return generation;
    }

    public RunState state() {
        return state;
    }

    public void state(RunState value) {
        this.state = value;
    }

    public String phase() {
        return phase;
    }

    public void phase(String value) {
        this.phase = value;
    }

    public int schemaRevision() {
        return schemaRevision;
    }

    public void schemaRevision(int value) {
        this.schemaRevision = value;
    }

    public boolean controlHeld() {
        return controlHeld;
    }

    public void controlHeld(boolean value) {
        this.controlHeld = value;
    }

    public boolean controlReleased() {
        return controlReleased;
    }

    public void controlReleased(boolean value) {
        this.controlReleased = value;
    }

    public long releaseRequestedTick() {
        return releaseRequestedTick;
    }

    public void releaseRequestedTick(long value) {
        this.releaseRequestedTick = value;
    }

    public long releasedTick() {
        return releasedTick;
    }

    public void releasedTick(long value) {
        this.releasedTick = value;
    }

    public String releaseReason() {
        return releaseReason;
    }

    public void releaseReason(String value) {
        this.releaseReason = value;
    }

    public int releaseLatencyTicks() {
        return releaseLatencyTicks;
    }

    public void releaseLatencyTicks(int value) {
        this.releaseLatencyTicks = value;
    }

    public long createdTick() {
        return createdTick;
    }

    public long updatedTick() {
        return updatedTick;
    }

    public void updatedTick(long value) {
        this.updatedTick = value;
    }

    public int physicalMutations() {
        return physicalMutations;
    }

    public void physicalMutations(int value) {
        this.physicalMutations = value;
    }

    public int commandMutations() {
        return commandMutations;
    }

    public void commandMutations(int value) {
        this.commandMutations = value;
    }

    public int teleportMutations() {
        return teleportMutations;
    }

    public void teleportMutations(int value) {
        this.teleportMutations = value;
    }

    public int cleanupDebt() {
        return cleanupDebt;
    }

    public void cleanupDebt(int value) {
        this.cleanupDebt = value;
    }

    public int restorationDebt() {
        return restorationDebt;
    }

    public void restorationDebt(int value) {
        this.restorationDebt = value;
    }

    public String expertState() {
        return expertState;
    }

    public void expertState(String value) {
        this.expertState = value;
    }

    public String expertIncidentId() {
        return expertIncidentId;
    }

    public void expertIncidentId(String value) {
        this.expertIncidentId = value;
    }

    public String waitingReason() {
        return waitingReason;
    }

    public void waitingReason(String value) {
        this.waitingReason = value;
    }

    public String inputOwner() {
        return inputOwner;
    }

    public void inputOwner(String value) {
        this.inputOwner = value;
    }
}
