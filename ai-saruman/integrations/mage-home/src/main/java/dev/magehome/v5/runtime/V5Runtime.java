package dev.magehome.v5.runtime;

import com.google.gson.Gson;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dev.magehome.v5.engine.BuildEngine;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.function.Consumer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The runtime of the public contract {@code mage_home.api/1}.
 *
 * <p>Every mutation of a run happens on the client thread. A request of the control server is
 * therefore queued and executed in the next client tick, which keeps the state of a run
 * consistent and lets the release of the control be measured in processed client ticks.
 */
public final class V5Runtime {

    /** The versioned name of the public contract. */
    public static final String API = "mage_home.api/1";
    private static final Logger LOG = LoggerFactory.getLogger("mage_home/v5");
    private final ConcurrentLinkedQueue<Designed> designs = new ConcurrentLinkedQueue<>();
    private Thread designTask;
    private RunRecord designRun;
    private record Designed(RunRecord run, int generation, DesignProcess.Result result) { }

    /** The single runtime of the client. */
    private static final V5Runtime INSTANCE = new V5Runtime();

    /** The registry of the runs. */
    private final RunRegistry registry = new RunRegistry();
    /** Revocations are never queued behind ordinary status or build admission traffic. */
    private final ArrayBlockingQueue<Pending> urgent = new ArrayBlockingQueue<>(32);
    private final ArrayBlockingQueue<Pending> pending = new ArrayBlockingQueue<>(32);
    /** The engine that carries out the runs. */
    private volatile BuildEngine engine;
    /** True when the module accepts new work. */
    private volatile boolean enabled = true;
    /** The number of the processed client ticks. */
    private volatile long tick;

    /** One operation that waits for the client thread. */
    private record Pending(String op, String requestId, JsonObject payload, long receivedTick,
                           Consumer<String> respond) {
    }

    private V5Runtime() {
    }

    /** The runtime of the client. */
    public static V5Runtime get() {
        return INSTANCE;
    }

    /**
     * Binds the engine that carries out the runs.
     *
     * @param value the engine; null detaches the runtime from the client
     */
    public void bind(BuildEngine value) {
        this.engine = value;
    }

    /** The engine of the runtime; null when the runtime is not bound. */
    public BuildEngine engine() {
        return engine;
    }

    /** The registry of the runs. */
    public RunRegistry registry() {
        return registry;
    }

    /** The number of the processed client ticks. */
    public long currentTick() {
        return tick;
    }

    /** True when the module accepts new work. */
    public boolean enabled() {
        return enabled;
    }

    // ---------------------------------------------------------------- control

    /**
     * Queues an operation of the control server.
     *
     * <p>The operation is executed in the next client tick and the answer is written back to the
     * caller. The tick at which the request arrived is remembered, so that the latency of the
     * release of the control is measured in processed client ticks and not in wall clock time.
     *
     * @param op        the name of the operation
     * @param requestId the identity of the request
     * @param payload   the payload of the request
     * @param respond   the consumer of the answer
     */
    public void submit(String op, String requestId, JsonObject payload, Consumer<String> respond) {
        var queue = switch (op == null ? "" : op) {
            case "pause", "resume", "stop", "moduleStop", "disable", "enable" -> urgent;
            default -> pending;
        };
        if (!queue.offer(new Pending(op, requestId, payload, tick, respond))) {
            respond.accept(failure(op, requestId, "control_queue_full", "Client control queue is full", true));
        }
    }

    /**
     * Advances the runtime by one client tick.
     *
     * <p>The order is fixed: the queued requests are executed first, then the release of a
     * revoked control is carried out, and only a run that still holds the control may act.
     */
    public void onClientTick() {
        tick++;
        drain(urgent);
        drain(pending);
        for (Designed completed; (completed = designs.poll()) != null; ) applyDesign(completed);
        BuildEngine current = engine;
        if (current == null) {
            return;
        }
        if (tick % 20 == 0 && enabled) {
            for (RunRecord run : registry.allRuns()) {
                if (!run.continuousRecovery() || run.state() != RunState.PAUSED || !run.controlReleased()) continue;
                var reason = run.waitingReason();
                boolean reconnect = reason.equals("world_disconnected") || reason.equals("world_changed");
                if (!reconnect && !run.engineStarted()) continue;
                if (!run.worldUid().equals(ClientFacts.worldUid()) || registry.currentRun(run.worldUid()) != run
                        || registry.busy(run)) continue;
                if (!run.engineStarted() || current.canAutoResume(run)) resumeRun(run);
            }
        }
        for (RunRecord run : registry.workingRuns()) {
            run.updatedTick(tick);
            if (!run.worldUid().equals(ClientFacts.worldUid())) {
                run.state(RunState.PAUSED);
                run.phase("PAUSED");
                run.waitingReason("world_changed");
                revoke(run, "world_changed");
                continue;
            }
            if (run.controlHeld() && run.engineStarted() && designRun != run) {
                try {
                    current.onTick(run, tick);
                } catch (RuntimeException e) {
                    failRun(run, "engine_tick_failed", e);
                }
            }
            if (run.state().terminal() && !run.controlReleased()) {
                revoke(run, "terminal");
            }
        }
    }

    private void drain(ArrayBlockingQueue<Pending> queue) {
        for (int remaining = queue.size(); remaining > 0; remaining--) {
            Pending request = queue.poll();
            if (request == null) break;
            String answer = executeAt(request.op(), request.requestId(), request.payload(), request.receivedTick());
            request.respond().accept(answer);
        }
    }

    /** Close the action gate even if the engine signals an error while releasing. */
    private void revoke(RunRecord run, String reason) {
        revoke(run, reason, tick);
    }

    private void revoke(RunRecord run, String reason, long requestTick) {
        run.controlHeld(false);
        run.controlReleased(false);
        run.nextGeneration();
        run.releaseReason(reason);
        run.releaseRequestedTick(requestTick);
        run.updatedTick(tick);
        if (run == designRun && designTask != null) {
            designTask.interrupt();
            designTask = null;
            designRun = null;
        }
        if (run.engineStarted()) engine.releaseControl(run, reason);
        else run.inputOwner("none");
        run.controlReleased(true);
        run.releasedTick(tick);
        run.releaseLatencyTicks(Math.toIntExact(tick - requestTick));
    }

    // ------------------------------------------------------------- operations

    /**
     * Executes an operation of the contract.
     *
     * @param op        the name of the operation
     * @param requestId the identity of the request
     * @param payload   the payload of the request
     * @return the answer of the operation as one JSON object
     */
    public String execute(String op, String requestId, JsonObject payload) {
        if (!net.minecraft.client.MinecraftClient.getInstance().isOnThread()) {
            throw new IllegalStateException("Use the queued API off the client thread");
        }
        return executeAt(op, requestId, payload, tick);
    }

    private String executeAt(String op, String requestId, JsonObject payload, long requestTick) {
        JsonObject request = payload == null ? new JsonObject() : payload;
        try {
            return switch (op == null ? "" : op) {
                case "start" -> start(request, requestId);
                case "status" -> status(request);
                case "pause" -> pause(request, requestTick);
                case "resume" -> resume(request);
                case "stop" -> stop(request, requestTick);
                case "moduleStop", "disable", "enable" -> moduleControl(op, requestId, requestTick);
                case "capabilities" -> capabilities(request);
                default -> failure(op, requestId, "unsupported_operation",
                        "The operation " + op + " is not part of " + API, false);
            };
        } catch (Throwable t) {
            return failure(op, requestId, "internal_error",
                    t.getClass().getSimpleName() + ": " + String.valueOf(t.getMessage()), false);
        }
    }

    /** The start operation: it accepts a request quickly and returns the identities. */
    private String start(JsonObject request, String envelopeRequestId) {
        String declaredRequestId = text(request, "requestId");
        final String requestId = (declaredRequestId == null || declaredRequestId.isBlank())
                ? envelopeRequestId : declaredRequestId;
        if (requestId == null || requestId.isBlank()) {
            return failure("start", requestId, "invalid_request", "The request identity is required", false);
        }
        if (!enabled) {
            return failure("start", requestId, "module_disabled", "The building module is disabled", false);
        }
        if (engine == null) {
            return failure("start", requestId, "not_initialized", "The client is not ready to accept work", true);
        }
        JsonObject context = object(request, "context");
        if (context == null) {
            return failure("start", requestId, "invalid_request", "The context is required", false);
        }
        String requestedWorld = RunRegistry.worldUidOf(context);
        if (requestedWorld == null) {
            return failure("start", requestId, "invalid_request",
                    "The context names no world", false);
        }
        String currentWorld = ClientFacts.worldUid();
        if (currentWorld == null) {
            return failure("start", requestId, "not_initialized",
                    "The client is not in a world yet", true);
        }
        if (!requestedWorld.equals(currentWorld)) {
            return failure("start", requestId, "stale_world",
                    "The context names the world " + requestedWorld + " but the client is in " + currentWorld, false);
        }
        String fingerprint = fingerprint(request);
        var options = object(request, "options");
        var resumeRunId = text(options, "restoreRunId");
        var resumeProjectId = text(options, "restoreProjectId");
        if ((resumeRunId != null || resumeProjectId != null)
                && (resumeRunId == null || !resumeRunId.matches("run-[0-9a-f-]{36}") || resumeProjectId == null
                || !resumeProjectId.matches("prj-[0-9a-f-]{36}|saruman-[0-9a-f]{64}") || registry.run(resumeRunId) != null
                && !requestId.equals(registry.run(resumeRunId).requestId()))) {
            return failure("start", requestId, "invalid_request", "Invalid saved run or project identity", false);
        }
        RunRecord existing = registry.byRequestId(requestId);
        if (existing == null) {
            if (registry.busy(null)) {
                return failure("start", requestId, "busy", "Another builder run still owns construction", true);
            }
            try {
                JsonObject blueprint = object(request, "blueprint");
                if (blueprint != null) {
                    blueprintRevision(blueprint);
                    engine.validateStart(request);
                } else if (text(request, "description") == null || text(request, "description").isBlank()) {
                    throw new IllegalArgumentException("Provide a blueprint or a description");
                } else if (DesignProcess.profile(object(request, "options")).isBlank()) {
                    throw new UnsupportedOperationException("No standalone designer profile is configured");
                }
            } catch (UnsupportedOperationException e) {
                return failure("start", requestId, "unsupported_operation", e.getMessage(), false);
            } catch (IllegalArgumentException e) {
                return failure("start", requestId, "invalid_request", e.getMessage(), false);
            }
        }
        RunRegistry.Admission admission = registry.admit(requestId, fingerprint, () -> {
            String runId = resumeRunId == null ? "run-" + UUID.randomUUID() : resumeRunId;
            JsonObject blueprint = object(request, "blueprint");
            String savedId = text(blueprint, "projectId");
            String projectId = resumeProjectId != null ? resumeProjectId : savedId == null ? "prj-" + UUID.randomUUID() : savedId;
            RunRecord created = new RunRecord(projectId, runId, requestId, fingerprint, requestedWorld, context,
                    blueprint, text(request, "description"), object(request, "options"), tick);
            if (blueprint != null) created.schemaRevision(blueprintRevision(blueprint));
            return created;
        });
        if (admission == null) {
            return failure("start", requestId, "duplicate_conflict",
                    "The request identity exists with another payload", false);
        }
        RunRecord run = admission.run();
        JsonObject result = new JsonObject();
        result.addProperty("projectId", run.projectId());
        result.addProperty("runId", run.runId());
        result.addProperty("accepted", true);
        result.addProperty("duplicate", admission.duplicate());
        result.addProperty("state", run.state().name());
        result.addProperty("generation", run.generation());
        result.addProperty("schemaRevision", run.schemaRevision());
        result.addProperty("controlRequested", run.controlHeld());
        result.addProperty("worldUid", run.worldUid());
        if (!admission.duplicate()) {
            if (run.blueprint() == null) launchDesign(run);
            else startEngine(run);
        }
        return success("start", requestId, result);
    }

    /** Called at the actual disconnect event, including reconnects with no disconnected tick. */
    public void onDisconnect() {
        for (RunRecord run : registry.workingRuns()) {
            run.state(RunState.PAUSED);
            run.phase("PAUSED");
            run.waitingReason("world_disconnected");
            revoke(run, "world_disconnected");
        }
    }

    private void launchDesign(RunRecord run) {
        JsonObject input = new JsonObject();
        input.addProperty("description", run.description());
        input.addProperty("projectId", run.projectId());
        input.addProperty("runId", run.runId());
        input.add("context", run.context().deepCopy());
        if (run.designFeedback() != null) input.add("feedback", run.designFeedback());
        if (run.recovery() != null) {
            var recovery = run.recovery().deepCopy();
            recovery.add("baseline", run.blueprint());
            input.add("recovery", recovery);
        }
        int generation = run.generation();
        run.inputOwner("none");
        run.controlReleased(true);
        run.waitingReason(run.recovery() != null ? "design_replanning" : run.designFeedback() != null ? "design_revising" : "design_pending");
        designRun = run;
        String profile = DesignProcess.profile(run.options());
        designTask = Thread.ofVirtual().name("mage-home-design").start(() ->
                designs.add(new Designed(run, generation, DesignProcess.run(input, profile))));
    }

    public boolean requestReplan(RunRecord run, JsonObject recovery) {
        if (!run.continuousRecovery() || run.description() == null || run.description().isBlank()
                || !run.engineStarted() || designRun != null || !run.controlHeld()) return false;
        run.recovery(recovery.deepCopy());
        run.nextGeneration();
        run.state(RunState.PLANNING);
        run.phase("REPLANNING");
        run.waitingReason("design_replanning");
        engine.releaseControl(run, "design_replanning");
        launchDesign(run);
        return true;
    }

    public void waitForRecovery(RunRecord run, String reason) {
        run.state(RunState.PAUSED);
        run.phase("PAUSED");
        run.waitingReason(reason);
        revoke(run, reason);
    }

    private void applyDesign(Designed completed) {
        RunRecord run = completed.run();
        if (designRun != run || run.state() != RunState.PLANNING || !run.controlHeld()
                || run.generation() != completed.generation()
                || !run.worldUid().equals(ClientFacts.worldUid())) {
            LOG.info("MAGE_DESIGN_STALE run={} generation={}", run.runId(), completed.generation());
            return;
        }
        designTask = null;
        designRun = null;
        DesignProcess.Result result = completed.result();
        if (result.error() != null) {
            if (run.continuousRecovery() && !result.error().equals("design_invalid_request")) {
                waitForRecovery(run, result.error());
            } else failRun(run, result.error(), null);
            return;
        }
        JsonObject blueprint = result.blueprint();
        try {
            if (!run.projectId().equals(text(blueprint, "projectId"))) {
                throw new IllegalArgumentException("Designer output has a different projectId");
            }
            int revision = blueprintRevision(blueprint);
            JsonObject validated = new JsonObject();
            validated.add("blueprint", blueprint);
            validated.add("context", run.context());
            validated.add("options", run.options());
            validated.addProperty("description", run.description());
            engine.validateStart(validated);
            if (run.recovery() != null) {
                engine.onReplan(run, blueprint, run.recovery());
            }
            run.acceptDesignedBlueprint(blueprint);
            run.schemaRevision(revision);
            run.recovery(null);
            run.clearDesignRejections();
            run.waitingReason("");
            LOG.info("MAGE_DESIGN_ACCEPTED run={} generation={} revision={}", run.runId(), run.generation(), revision);
        } catch (RuntimeException e) {
            if (run.continuousRecovery()) {
                var issue = new JsonObject();
                issue.addProperty("code", "minecraft_rejected_blueprint");
                var detail = e instanceof IllegalArgumentException && e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
                issue.addProperty("detail", detail.substring(0, Math.min(512, detail.length())));
                var rejected = new JsonObject();
                rejected.add("blueprint", blueprint);
                if (run.rememberDesignRejection(fingerprint(rejected), issue)) launchDesign(run);
                else waitForRecovery(run, "design_rejected_blueprint");
            } else failRun(run, "design_rejected_blueprint", e);
            return;
        }
        if (!run.engineStarted()) startEngine(run);
        else try {
            engine.onResume(run);
        } catch (RuntimeException e) {
            waitForRecovery(run, "recovery_waiting:engine_resume_failed");
        }
    }

    private void startEngine(RunRecord run) {
        run.engineStarted(true);
        run.controlReleased(false);
        try {
            engine.onStart(run);
        } catch (RuntimeException failure) {
            failRun(run, "engine_start_failed", failure);
        }
    }

    private void failRun(RunRecord run, String reason, RuntimeException error) {
        run.state(RunState.FAILED);
        run.phase("FAILED");
        run.waitingReason(reason);
        try {
            revoke(run, reason);
        } catch (RuntimeException releaseFailure) {
            LOG.error("Builder release failed for {}", run.runId(), releaseFailure);
        }
        if (error != null) LOG.warn("Builder failure in {}: {}", run.runId(), error.getClass().getSimpleName());
    }

    private static int blueprintRevision(JsonObject blueprint) {
        var revision = blueprint.get("revision");
        if (revision == null || !revision.isJsonPrimitive() || !revision.getAsJsonPrimitive().isNumber()
                || !revision.getAsString().matches("[1-9][0-9]{0,8}")) {
            throw new IllegalArgumentException("Blueprint revision must be a positive integer");
        }
        if (blueprint.has("projectId")) {
            String id = text(blueprint, "projectId");
            if (id == null || !id.matches("[A-Za-z0-9_.:-]{1,96}")) {
                throw new IllegalArgumentException("Malformed blueprint projectId");
            }
        }
        return revision.getAsInt();
    }

    /** The status operation: it returns the last snapshot and never starts work. */
    private String status(JsonObject request) {
        String runId = text(request, "runId");
        RunRecord run = runId == null || runId.isBlank()
                ? registry.currentRun(ClientFacts.worldUid()) : registry.run(runId);
        if (run == null) {
            return failure("status", runId, "unknown_run", "There is no run with this identity", false);
        }
        return success("status", runId, snapshot(run));
    }

    /** The pause operation: it revokes the permission and keeps the progress. */
    private String pause(JsonObject request, long requestTick) {
        String runId = text(request, "runId");
        RunRecord run = registry.run(runId);
        if (run == null) {
            return failure("pause", runId, "unknown_run", "There is no run with this identity", false);
        }
        if (run.state().terminal()) {
            return failure("pause", runId, "run_terminal",
                    "The run is " + run.state().name() + " and cannot be paused", false);
        }
        if (run.state() == RunState.PAUSED) {
            run.waitingReason("pause_requested");
            run.releaseReason("pause_requested");
            return success("pause", runId, controlResult(run, true));
        }
        run.state(RunState.PAUSED);
        run.phase("PAUSED");
        run.waitingReason("pause_requested");
        revoke(run, "pause_requested", requestTick);
        return success("pause", runId, controlResult(run, true));
    }

    /** The resume operation: it continues the same run after a new check. */
    private String resume(JsonObject request) {
        String runId = text(request, "runId");
        RunRecord run = registry.run(runId);
        if (run == null) {
            return failure("resume", runId, "unknown_run", "There is no run with this identity", false);
        }
        if (run.state().terminal()) {
            return failure("resume", runId, "run_terminal",
                    "The run is " + run.state().name() + "; a stopped project needs a new explicit start", false);
        }
        if (run.state() != RunState.PAUSED || !run.controlReleased()) {
            return failure("resume", runId, "not_paused", "Only a released paused run can resume", false);
        }
        String currentWorld = ClientFacts.worldUid();
        if (currentWorld == null || !run.worldUid().equals(currentWorld)) {
            return failure("resume", runId, "world_mismatch",
                    "The world of the run is not the world of the client", false);
        }
        if (registry.busy(run)) {
            return failure("resume", runId, "busy", "Another builder run still owns construction", true);
        }
        if (!enabled) return failure("resume", runId, "module_disabled", "The module is disabled", true);
        resumeRun(run);
        JsonObject result = new JsonObject();
        result.addProperty("accepted", true);
        result.addProperty("state", run.state().name());
        result.addProperty("generation", run.generation());
        return success("resume", runId, result);
    }

    private void resumeRun(RunRecord run) {
        registry.select(run);
        run.nextGeneration();
        run.state(RunState.PLANNING);
        run.phase("PLANNING");
        run.releaseRequestedTick(-1L);
        run.releasedTick(-1L);
        run.releaseLatencyTicks(-1);
        run.controlHeld(true);
        run.controlReleased(false);
        run.waitingReason("");
        if (run.recovery() != null) {
            run.phase("REPLANNING");
            launchDesign(run);
        } else if (run.engineStarted()) {
            try {
                engine.onResume(run);
            } catch (RuntimeException e) {
                failRun(run, "engine_resume_failed", e);
            }
        } else {
            launchDesign(run);
        }
    }

    /** The stop operation: it ends the run and keeps the cleanup debt. */
    private String stop(JsonObject request, long requestTick) {
        String runId = text(request, "runId");
        RunRecord run = registry.run(runId);
        if (run == null) {
            return failure("stop", runId, "unknown_run", "There is no run with this identity", false);
        }
        if (run.state().terminal() && run.state() != RunState.STOPPED) {
            return failure("stop", runId, "run_terminal", "The run already ended", false);
        }
        String reason = text(request, "reason");
        stopRun(run, reason == null || reason.isBlank() ? "stop_requested" : reason, requestTick);
        JsonObject result = controlResult(run, true);
        result.addProperty("cancellationReason", run.releaseReason());
        return success("stop", runId, result);
    }

    private void stopRun(RunRecord run, String reason, long requestTick) {
        if (run.state() == RunState.STOPPED) return;
        run.state(RunState.STOPPED);
        run.phase("STOPPED");
        revoke(run, reason, requestTick);
        if (run.engineStarted()) engine.onStop(run, reason);
    }

    private String moduleControl(String op, String requestId, long requestTick) {
        if (op.equals("enable")) enabled = true;
        if (op.equals("disable")) enabled = false;
        RunRecord run = registry.workingRuns().stream().findFirst()
                .orElse(registry.currentRun(ClientFacts.worldUid()));
        if (!op.equals("enable") && run != null && !run.state().terminal()) {
            stopRun(run, op.equals("disable") ? "module_disabled" : "module_stop_requested", requestTick);
        }
        JsonObject result = new JsonObject();
        result.addProperty("accepted", true);
        result.addProperty("moduleEnabled", enabled);
        result.addProperty("runId", run == null ? null : run.runId());
        result.addProperty("controlReleased", run == null || run.controlReleased());
        result.addProperty("state", run == null ? "IDLE" : run.state().name());
        return success(op, requestId, result);
    }

    /** The capabilities operation: it describes what the module can do right now. */
    private String capabilities(JsonObject request) {
        JsonObject result = new JsonObject();
        result.addProperty("api", API);
        result.addProperty("recoveryProtocol", 1);
        result.addProperty("multiProject", true);
        result.addProperty("interiorProtocol", 2);
        result.addProperty("blockMutationPolicy", "physical_only");
        result.addProperty("moduleEnabled", enabled);
        result.addProperty("engine", engine == null ? null : engine.name());
        JsonObject unionClef = new JsonObject();
        UnionClefFacts.Facts facts = UnionClefFacts.read();
        unionClef.addProperty("loaded", facts.loaded());
        unionClef.addProperty("taskChain", facts.taskChain());
        unionClef.addProperty("slotHandler", facts.slotHandler());
        unionClef.addProperty("version", facts.version());
        unionClef.addProperty("currentChain", facts.currentChain());
        unionClef.addProperty("userChainHeld", facts.userChainHeld());
        unionClef.addProperty("currentTask", facts.currentTask());
        result.add("unionClef", unionClef);
        JsonObject world = new JsonObject();
        String server = ClientFacts.serverAddress();
        String dimension = ClientFacts.dimension();
        world.addProperty("server", server);
        world.addProperty("dimension", dimension);
        String worldUid = ClientFacts.worldUid();
        world.addProperty("worldUid", worldUid);
        world.addProperty("name", worldUid != null && worldUid.startsWith("local:") ? ClientFacts.localSaveName() : null);
        world.addProperty("connected", worldUid != null);
        result.add("world", world);
        result.addProperty("player", ClientFacts.playerName());
        JsonObject ui = new JsonObject();
        ui.addProperty("screenOpen", ClientFacts.currentScreenName() != null);
        ui.addProperty("screen", ClientFacts.currentScreenName());
        ui.addProperty("playerPresent", ClientFacts.playerPresent());
        result.add("ui", ui);
        List<String> allowed = new ArrayList<>(List.of("start", "status", "pause", "resume", "stop",
                "capabilities", "moduleStop", "disable", "enable"));
        JsonArrayHolder allowedArray = new JsonArrayHolder(allowed);
        result.add("allowedOperations", allowedArray.array());
        JsonArrayHolder states = new JsonArrayHolder(names());
        result.add("states", states.array());
        return success("capabilities", text(request, "requestId"), result);
    }

    /** The names of the states of the contract. */
    public static List<String> names() {
        List<String> result = new ArrayList<>();
        for (RunState state : RunState.values()) {
            result.add(state.name());
        }
        return result;
    }

    // ----------------------------------------------------------------- status

    /** The snapshot of a run for the status operation. */
    private JsonObject snapshot(RunRecord run) {
        JsonObject result = new JsonObject();
        result.addProperty("runId", run.runId());
        result.addProperty("projectId", run.projectId());
        result.addProperty("worldUid", run.worldUid());
        result.addProperty("inRunWorld", run.worldUid().equals(ClientFacts.worldUid()));
        result.addProperty("state", run.state().name());
        result.addProperty("phase", run.phase());
        result.addProperty("generation", run.generation());
        result.addProperty("schemaRevision", run.schemaRevision());
        BuildEngine current = run.engineStarted() ? engine : null;
        result.add("sitePreparation", current == null
                ? new JsonObject() : current.sitePreparation(run));
        result.add("progress", current == null ? new JsonObject() : current.progress(run));
        result.addProperty("waitingReason", run.waitingReason());
        result.addProperty("inputOwner", run.inputOwner());
        result.addProperty("controlReleased", run.controlReleased());
        result.addProperty("releasedTick", run.releasedTick());
        result.addProperty("releaseRequestedTick", run.releaseRequestedTick());
        result.addProperty("releaseLatencyTicks", run.releaseLatencyTicks());
        result.addProperty("releaseReason", run.releaseReason());
        result.addProperty("clientTick", tick);
        JsonObject debt = new JsonObject();
        debt.addProperty("temporaryBlocks", run.cleanupDebt());
        debt.addProperty("obligations", run.restorationDebt());
        result.add("cleanupDebt", debt);
        JsonObject mutations = new JsonObject();
        mutations.addProperty("physical", run.physicalMutations());
        mutations.addProperty("commands", run.commandMutations());
        mutations.addProperty("teleports", run.teleportMutations());
        result.add("mutations", mutations);
        result.add("expert", current == null ? new JsonObject() : current.expert(run));
        result.addProperty("updatedAtTick", run.updatedTick());
        return result;
    }

    /** The result of a control operation. */
    private JsonObject controlResult(RunRecord run, boolean accepted) {
        JsonObject result = new JsonObject();
        result.addProperty("accepted", accepted);
        result.addProperty("controlReleased", run.controlReleased());
        result.addProperty("state", run.state().name());
        result.addProperty("releaseLatencyTicks", run.releaseLatencyTicks());
        return result;
    }

    // ---------------------------------------------------------------- helpers

    /** The fingerprint of an accepted request. */
    private static String fingerprint(JsonObject request) {
        StringBuilder canonical = new StringBuilder();
        canonical.append(text(request, "description")).append(' ');
        JsonObject blueprint = object(request, "blueprint");
        canonical.append(blueprint == null ? "" : blueprint.toString()).append(' ');
        JsonObject context = object(request, "context");
        canonical.append(context == null ? "" : context.toString()).append(' ');
        JsonObject options = object(request, "options");
        canonical.append(options == null ? "" : options.toString());
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(canonical.toString().getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (byte value : hash) {
                hex.append(String.format(Locale.ROOT, "%02x", value));
            }
            return hex.toString();
        } catch (NoSuchAlgorithmException e) {
            return Integer.toHexString(canonical.toString().hashCode());
        }
    }

    /** The text of a field; null when the field is absent or not a string. */
    private static String text(JsonObject object, String field) {
        if (object == null || !object.has(field) || object.get(field).isJsonNull()) {
            return null;
        }
        JsonElement value = object.get(field);
        return value.isJsonPrimitive() ? value.getAsString() : null;
    }

    /** The object of a field; null when the field is absent or not an object. */
    private static JsonObject object(JsonObject object, String field) {
        if (object == null || !object.has(field) || !object.get(field).isJsonObject()) {
            return null;
        }
        return object.getAsJsonObject(field);
    }

    /** The answer of a successful operation. */
    private static String success(String op, String requestId, JsonObject result) {
        JsonObject envelope = new JsonObject();
        envelope.addProperty("api", API);
        envelope.addProperty("op", op);
        envelope.addProperty("requestId", requestId);
        envelope.addProperty("ok", true);
        envelope.add("result", result);
        return envelope.toString();
    }

    /** The answer of a rejected operation. */
    private static String failure(String op, String requestId, String code, String message, boolean retryable) {
        JsonObject error = new JsonObject();
        error.addProperty("code", code);
        error.addProperty("message", message);
        error.addProperty("retryable", retryable);
        JsonObject envelope = new JsonObject();
        envelope.addProperty("api", API);
        envelope.addProperty("op", op);
        envelope.addProperty("requestId", requestId);
        envelope.addProperty("ok", false);
        envelope.add("error", error);
        return envelope.toString();
    }

    /** Parses one JSON object; null when the text is not a JSON object. */
    public static JsonObject parse(String text) {
        try {
            JsonElement element = JsonParser.parseString(text);
            return element.isJsonObject() ? element.getAsJsonObject() : null;
        } catch (Throwable t) {
            return null;
        }
    }

    /** The serializer of the runtime. */
    public static Gson gson() {
        return new Gson();
    }

    /** A tiny holder that turns a list of strings into a JSON array. */
    private static final class JsonArrayHolder {
        private final List<String> values;

        JsonArrayHolder(List<String> values) {
            this.values = values;
        }

        com.google.gson.JsonArray array() {
            com.google.gson.JsonArray result = new com.google.gson.JsonArray();
            values.forEach(result::add);
            return result;
        }
    }
}
