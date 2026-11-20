package dev.magehome.v5.engine;

import adris.altoclef.tasksystem.Task;
import com.google.gson.*;
import dev.magehome.client.UnionClefBridge;
import dev.magehome.core.automation.PlanHold;
import dev.magehome.core.automation.TaskChainOwnership;
import dev.magehome.v5.runtime.ClientFacts;
import dev.magehome.v5.runtime.RunRecord;
import dev.magehome.v5.runtime.RunState;
import dev.magehome.v5.runtime.V5Runtime;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.option.KeyBinding;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.item.Item;
import net.minecraft.item.Items;
import net.minecraft.network.packet.c2s.play.PlayerActionC2SPacket;
import net.minecraft.registry.Registries;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.function.Predicate;

import static dev.magehome.v5.engine.BuildPlan.*;

/** Production executor: every mutation is sent by one elected UnionClef root on the client thread. */
public final class ProductionBuildEngine implements BuildEngine {
    private static final Logger LOG = LoggerFactory.getLogger("mage_home/v5-build");
    private static final int ACK_TICKS = 45;
    private static final int GOAL_FAILURE_LIMIT = 10;
    private static final int MAX_RECONCILIATIONS = 5;
    private static final int JOURNAL_WAIT_TICKS = 200;
    private static final int JOURNAL_RETRY_TICKS = 200;
    private static final int PREPARATION_DELAY = Math.clamp(Integer.getInteger("mage_home.lab.preparationDelayTicks", 0), 0, 120);
    /** The bounded wait for a native chain of UnionClef that holds the scheduler. */
    private static final int PREEMPTION_WAIT_TICKS = Math.clamp(
            Integer.getInteger("mage_home.lab.preemptionWaitTicks", 600), 20, 6000);
    private final MinecraftClient client = MinecraftClient.getInstance();
    private final UnionClefBridge bridge = new UnionClefBridge();
    private final ServerActions world = new ServerActions(client);
    private final Map<String, Session> sessions = new LinkedHashMap<>();
    private Session active;

    private enum Method {
        PLACE("physical_place"), BREAK("physical_break"), WALK("walk"), GIVE("command_give"),
        SWAP("inventory_swap"), OBSERVE("observe");
        final String wire;
        Method(String wire) { this.wire = wire; }
    }

    private record Options(boolean supply, boolean place, boolean breakBlocks, boolean useItems, boolean scaffolding,
                           double reach, String profile, int deadline, int expertBudget) {
        static Options read(JsonObject context, JsonObject options) {
            var caps = object(context, "capabilities");
            var expert = object(options, "expert");
            var mode = text(options, "mode", "blueprint_only");
            if (!Set.of("blueprint_only", "text_to_blueprint").contains(mode)) throw new UnsupportedOperationException("Unsupported mode " + mode);
            if (!bool(options, "sitePreparation", true)) throw new UnsupportedOperationException("Site preparation is mandatory");
            boolean strict = bool(options, "strictPhysical", false);
            var profile = text(expert, "providerProfile", System.getProperty("mage_home.model.expertProfile", ""));
            int deadline = integer(expert, "deadlineMillis", 30_000);
            int budget = integer(expert, "incidentBudget", 3);
            if (deadline < 0 || deadline > 120_000 || (deadline > 0 && deadline < 100) || budget < 0 || budget > 10) throw invalid("invalid expert budget/deadline");
            double reach = 4.35;
            if (caps.has("maxReach")) {
                if (!caps.get("maxReach").isJsonPrimitive() || !caps.getAsJsonPrimitive("maxReach").isNumber()) throw invalid("maxReach must be numeric");
                reach = caps.get("maxReach").getAsDouble();
                if (!Double.isFinite(reach) || reach <= 0) throw invalid("maxReach must be positive");
                reach = Math.min(reach, 4.35);
            }
            return new Options(!strict && bool(options, "allowCommands", true) && bool(caps, "canUseCommands", false),
                    bool(caps, "canPlace", true), bool(caps, "canBreak", true), bool(caps, "canUseItems", true),
                    bool(caps, "supportsScaffolding", true), reach,
                    bool(expert, "enabled", true) ? profile : "", deadline, budget);
        }
    }

    private record Debt(Goal restore, Goal installed, boolean opening) { }
    private record ScaffoldColumn(BlockPos base, BlockPos top, BlockPos next, int missing) {
        BlockPos stance() { return top.up(); }
    }
    private record ScaffoldApproach(boolean direct, List<BlockPos> route) { }
    private record StancePhase(String goal, String context) { }
    private record StanceAttempt(String goal, String context, BlockPos destination) { }
    private record LocalSnapshot(List<BlockPos> positions, String revision, JsonArray known, JsonArray unknown) { }
    private record Incident(String id, String key, Goal target, JsonObject request, LocalSnapshot snapshot,
                            ExpertProcess process, long expires, int generation, long lease, ClientWorld world,
                            List<ScaffoldColumn> scaffoldAccess) { }
    private record PatchStep(String id, Goal goal, Goal before, Method force, List<BlockPos> route,
                             Set<String> dependencies, boolean needsCleanup, ScaffoldColumn scaffold) { }
    private static final class Patch {
        final String incident;
        final Goal expected;
        final Deque<PatchStep> steps;
        final List<String> confirmed = new ArrayList<>();
        final Set<String> executed = new LinkedHashSet<>();
        final List<ScaffoldColumn> scaffoldAlternatives;
        final Set<BlockPos> rejectedColumns = new HashSet<>();
        BlockPos scaffoldStance;
        boolean effectObservedByProduct;
        Patch(String incident, Goal expected, List<PatchStep> steps, List<ScaffoldColumn> scaffoldAlternatives) {
            this.incident = incident; this.expected = expected; this.steps = new ArrayDeque<>(steps);
            this.scaffoldAlternatives = scaffoldAlternatives;
        }
    }

    private static final class Operation {
        final Goal goal;
        final Method method;
        final PlanHold.Key key;
        final String worldContext;
        final int generation;
        final long started;
        final BlockPos from;
        final List<BlockPos> route;
        final List<BlockPos> actual = new ArrayList<>();
        final JsonArray supports;
        final JsonObject intent;
        final String reason;
        final Item item;
        final BlockPos destination;
        long sent = -1;
        long walkIndex;
        long waypointIndex = -1;
        long waypointProgressTick;
        double waypointDistance;
        int beforeCount;
        int giveCount;
        boolean breaking;
        double damage;
        BlockHitResult hit;
        Operation(Goal goal, Method method, PlanHold.Key key, String worldContext, int generation,
                  long started, BlockPos from, List<BlockPos> route, JsonArray supports, String reason,
                  Item item, BlockPos destination) {
            this.goal = goal; this.method = method; this.key = key; this.worldContext = worldContext;
            this.generation = generation;
            this.started = started; this.from = from; this.route = route; this.supports = supports;
            this.reason = reason; this.item = item; this.destination = destination;
            intent = new JsonObject();
            intent.addProperty("id", UUID.randomUUID().toString());
        }
    }
    private static final class Session {
        final RunRecord run;
        BuildPlan plan;
        final Options options;
        final BuildJournal journal;
        final CompletableFuture<String> loading;
        PlanHold hold = new PlanHold();
        final Map<String, Goal> goals = new HashMap<>();
        final Map<BlockPos, Debt> debt = new LinkedHashMap<>();
        final Set<BlockPos> placedEquipment = new HashSet<>();
        final Set<BlockPos> confirmedHouse = new HashSet<>();
        final Set<BlockPos> confirmedAir = new HashSet<>();
        final Set<String> coveredPreparation = new HashSet<>();
        final Set<String> requestedPreparation = new HashSet<>();
        final Set<String> spentSupply = new HashSet<>();
        long lastGive = -20;
        final Set<String> incidents = new HashSet<>();
        final Set<String> proposedRepairs = new HashSet<>();
        final Set<String> exteriorAttempts = new HashSet<>();
        BlockPos exteriorStaging;
        final Deque<Goal> openingCuts = new ArrayDeque<>();
        BlockPos openingExit;
        final Set<String> replans = new HashSet<>();
        final Set<String> acceptedDesigns = new HashSet<>();
        BlockPos waitingChunk;
        final Set<BlockPos> confirmedPaintings = new HashSet<>();
        BuildPlan.Painting pendingPainting;
        long paintingDeadline;
        long paintingInventoryCheck;
        final Set<BlockPos> requestedPaintings = new HashSet<>();
        final Set<PlanHold.Key> walkedContexts = new HashSet<>();
        final Set<StanceAttempt> triedStances = new HashSet<>();
        final Set<StanceAttempt> rejectedPlacementStances = new HashSet<>();
        final Map<BlockPos, Map<BlockPos, List<BlockState>>> blockedTransitions = new HashMap<>();
        final Map<StancePhase, Integer> walkedAlternatives = new HashMap<>();
        ClientWorld boundWorld;
        Root root;
        Operation op;
        Incident incident;
        Patch patch;
        List<Goal> preparation;
        List<BlockPos> surveySamples = List.of();
        String surveyRevision = "";
        int prepCursor, buildCursor, equipmentCursor, repairs, surveyPasses, expertUsed, confirmedCuts, confirmedFill;
        String preparationState = "NOT_STARTED";
        String access = "unverified";
        boolean loaded, surveySaved, resurvey = true, reconciliation;
        long lease, waitUntil, lastActionTick, missingSince = -1;
        String asyncRejection = "";
        String interrupted = "";
        long waitForOwner = -1;
        boolean readyCheckpointQueued;
        long journalPendingSince = -1;
        long nextJournalRetry;
        Session(RunRecord run, BuildPlan plan, Options options, Path directory, ClientWorld boundWorld) {
            this.run = run; this.plan = plan; this.options = options; this.boundWorld = boundWorld;
            plan.goals().forEach(g -> goals.put(g.id(), g));
            acceptedDesigns.add(designFingerprint(run.blueprint()));
            // Bind to world, project and immutable blueprint content. A new run can reconcile the
            // same project; a revised or moved plan cannot inherit old permits and goals.
            var binding = run.worldUid() + '|' + run.projectId() + '|' + coords(plan.anchor()) + '|'
                    + (run.continuousRecovery() ? "revision-history/1" : BuildJournal.digest(run.blueprint().toString()));
            journal = new BuildJournal(directory, binding);
            loading = journal.load();
        }
    }

    @Override public String name() { return "production-v5"; }

    @Override public void validateStart(JsonObject request) {
        var context = requiredObject(request, "context");
        Options.read(context, object(request, "options"));
        BuildPlan.parse(context, requiredObject(request, "blueprint"), client.world);
    }

    @Override public void onStart(RunRecord run) {
        var plan = BuildPlan.parse(run.context(), run.blueprint(), client.world);
        var s = new Session(run, plan, Options.read(run.context(), run.options()),
                client.runDirectory.toPath().resolve("config/mage_home/build-journals"), client.world);
        sessions.put(run.runId(), s);
        active = s;
        run.schemaRevision(s.plan.revision());
        owner(s, "none", true);
        run.waitingReason("journal_loading");
    }

    @Override public void onTick(RunRecord run, long tick) {
        var s = sessions.get(run.runId());
        if (s == null || active != s) return;
        try {
            if (client.world != s.boundWorld || client.player == null || !s.run.worldUid().equals(ClientFacts.worldUid())) {
                pause(s, "world_changed", tick);
                return;
            }
            if (!s.loaded) {
                if (!s.loading.isDone()) {
                    if (journalWaitExpired(s, tick)) pause(s, "journal_read_pending", tick);
                    return;
                }
                load(s);
                s.loaded = true;
                s.journalPendingSince = -1;
                s.waitUntil = tick + (s.reconciliation ? 12 : 0);
            }
            if (!journalCommitted(s, tick) && !s.run.controlHeld()) return;
            if (!V5Runtime.get().enabled()) { pause(s, "module_disabled", tick); return; }
            if (client.currentScreen != null || manualInput(s.root)) {
                pause(s, client.currentScreen == null ? "manual_input" : "screen_open", tick);
                return;
            }
            if (!bridge.chainAvailable() || bridge.paused()) {
                if (s.root != null) pause(s, "unionclef_paused", tick);
                else waitOwner(s, tick, "none", "unionclef_unavailable");
                return;
            }
            var runner = bridge.mod().getTaskRunner();
            var user = bridge.mod().getUserTaskChain();
            var current = bridge.currentTask();
            boolean foreign = current != null && current != s.root;
            boolean mob = runner.getCurrentTaskChain() != null
                    && runner.getCurrentTaskChain() == bridge.mod().getMobDefenseChain();
            if (s.root != null && s.root.stopped() && current != s.root) {
                // A library-owned stop/unstuck can remove our root itself; no stealing or rearm of
                // the old lease. Resurvey and acquire a fresh root only if the chain is free.
                releaseRoot(s);
                if (!foreign && !mob) s.resurvey = true;
            }
            boolean nativeOther = runner.getCurrentTaskChain() != null && runner.getCurrentTaskChain() != user
                    && ownsWork(runner.getCurrentTaskChain());
            if (foreign || mob) {
                if (s.root != null || s.incident != null || !s.interrupted.isEmpty()) {
                    pause(s, foreign ? "foreign_owner" : "mob_defense", tick);
                } else waitOwner(s, tick, foreign ? "foreign" : "unionclef", foreign ? "foreign_owner" : "mob_defense");
                return;
            }
            if (nativeOther) {
                // Do not label ordinary path shimmies/unstuck as user Stop. The scheduler will
                // return to this root when its higher priority chain releases it.
                owner(s, "unionclef", true);
                run.waitingReason("unionclef_" + runner.getCurrentTaskChain().getName());
                if (s.incident != null) { pause(s, "unionclef_preempted_expert", tick); return; }
                if (s.waitForOwner < 0) s.waitForOwner = tick;
                if (tick - s.waitForOwner > PREEMPTION_WAIT_TICKS) {
                    // A displacement is not a Stop. The run pauses with a concrete reason and the
                    // same project continues after an explicit resume once the owner is released.
                    pause(s, "unionclef_preemption_timeout", tick);
                }
                return;
            }
            s.waitForOwner = -1;
            if (!s.interrupted.isEmpty()) {
                LOG.info("MAGE_RECONCILE run={} generation={} tick={} reason={}", run.runId(), run.generation(), tick, s.interrupted);
                s.resurvey = true;
                s.interrupted = "";
            }
            if (s.incident != null) {
                if (s.root != null) releaseRoot(s);
                consumeExpert(s, tick);
                return;
            }
            if (s.root == null && current == null) {
                s.root = new Root(s, ++s.lease);
                bridge.runTask(s.root, TaskChainOwnership.Action.START_OWN_TASK);
            }
        } catch (RuntimeException e) {
            LOG.error("Mage Home execution error (run {})", run.runId(), e);
            terminal(s, s.loaded ? RunState.FAILED : RunState.BLOCKED, s.loaded ? "engine_exception" : "journal_read_failed");
        }
    }

    /**
     * True when a chain of UnionClef carries work of its own.
     *
     * <p>UnionClef keeps an auxiliary chain that is always active and has the lowest priority.
     * It is the fallback of the scheduler when no real owner holds it and it never carries a task
     * of its own. Such a chain is not a foreign owner: the builder may take its own chain back
     * after a pause, and it must not wait forever for a chain that never does any work.
     *
     * @param chain the chain that the scheduler selected
     * @return true when the chain holds tasks of its own
     */
    private static boolean ownsWork(adris.altoclef.tasksystem.TaskChain chain) {
        try {
            return chain != null && !chain.getTasks().isEmpty();
        } catch (Throwable t) {
            // An unreadable chain is treated as an owner, so the builder yields instead of fighting.
            return true;
        }
    }

    private void waitOwner(Session s, long tick, String owner, String reason) {
        owner(s, owner, true);
        s.run.waitingReason(reason);
        if (s.waitForOwner < 0) s.waitForOwner = tick;
        else if (tick - s.waitForOwner > 200) pause(s, reason, tick);
    }

    private final class Root extends Task {
        final Session s;
        final int generation;
        final int revision;
        final long lease;
        final Set<KeyBinding> ownKeys = new HashSet<>();
        Integer originalSlot, ownedSlot;
        Root(Session s, long lease) { this.s = s; this.lease = lease; generation = s.run.generation(); revision = s.run.schemaRevision(); }
        private boolean permits() {
            return active == s && s.root == this && s.lease == lease && generation == s.run.generation()
                    && revision == s.run.schemaRevision() && s.run.state().working() && s.run.controlHeld()
                    && V5Runtime.get().enabled() && client.currentScreen == null && client.player != null
                    && client.world == s.boundWorld && s.run.worldUid().equals(ClientFacts.worldUid())
                    && client.getNetworkHandler() != null && bridge.currentTask() == this && !bridge.paused()
                    && (bridge.mod().getTaskRunner().getCurrentTaskChain() == bridge.mod().getUserTaskChain()
                        || !ownsWork(bridge.mod().getTaskRunner().getCurrentTaskChain()));
        }
        @Override protected void onStart() { }
        @Override protected Task onTick() {
            if (permits()) {
                owner(s, "builder", false);
                try { advance(s, this, V5Runtime.get().currentTick()); }
                catch (RuntimeException e) {
                    LOG.error("Mage Home action failure (run {})", s.run.runId(), e);
                    terminal(s, RunState.FAILED, "action_exception");
                }
            }
            return null;
        }
        @Override protected void onStop(Task interrupting) {
            long tick = V5Runtime.get().currentTick();
            if (s.root == this) {
                s.interrupted = interrupting == null ? "scheduler_displaced" : "foreign_owner";
                interruptOperation(s, this, s.interrupted, tick, true);
                owner(s, interrupting == null ? "unionclef" : "foreign", true);
            }
            clearInputs();
        }
        void clearInputs() {
            ownKeys.forEach(key -> key.setPressed(false));
            ownKeys.clear();
            if (ownedSlot != null && client.player != null && client.world == s.boundWorld
                    && client.player.getInventory().getSelectedSlot() == ownedSlot) {
                client.player.getInventory().setSelectedSlot(originalSlot);
            }
            ownedSlot = null;
            originalSlot = null;
        }
        void key(KeyBinding key, boolean hold) {
            if (hold && permits() && (ownKeys.contains(key) || !key.isPressed())) {
                ownKeys.add(key);
                key.setPressed(true);
            } else if (!hold && ownKeys.remove(key)) key.setPressed(false);
        }

        void releaseMovement() {
            key(client.options.forwardKey, false);
            key(client.options.jumpKey, false);
            key(client.options.sneakKey, false);
        }
        void select(int slot) {
            if (!permits() || client.player.getInventory().getSelectedSlot() == slot) return;
            if (originalSlot == null) originalSlot = client.player.getInventory().getSelectedSlot();
            ownedSlot = slot;
            world.select(slot);
        }
        @Override protected boolean isEqual(Task other) { return other == this; }
        // We explicitly detach. Allowing UserTaskChain's generic finish path to fire disables other chains.
        @Override public boolean isFinished() { return false; }
        @Override protected String toDebugString() { return "mage_home_v5:" + s.run.runId(); }
    }

    private boolean manualInput(Root root) {
        if (root == null || bridge.currentTask() != root) return false;
        var runner = bridge.mod().getTaskRunner();
        if (runner.getCurrentTaskChain() != bridge.mod().getUserTaskChain() && ownsWork(runner.getCurrentTaskChain())) return false;
        return List.of(client.options.forwardKey, client.options.backKey, client.options.leftKey, client.options.rightKey,
                client.options.jumpKey, client.options.sneakKey, client.options.attackKey, client.options.useKey)
                .stream().anyMatch(key -> !root.ownKeys.contains(key) && key.isPressed());
    }

    private void advance(Session s, Root root, long tick) {
        if (tick < s.waitUntil) return;
        if (!journalCommitted(s, tick)) {
            if (s.run.controlHeld()) s.run.waitingReason("journal_commit_pending");
            return;
        }
        if (s.op != null) { advanceOperation(s, root, tick); return; }
        if (s.patch != null) { advancePatch(s, root, tick); return; }
        if (advanceOpening(s, root, tick)) return;
        if (s.resurvey || s.run.state() == RunState.PLANNING) {
            s.resurvey = false;
            s.surveyPasses = 0;
            s.preparation = null;
            s.buildCursor = 0;
            phase(s, RunState.PREPARING);
            return;
        }
        switch (s.run.state()) {
            case PREPARING -> prepare(s, root, tick);
            case BUILDING -> build(s, root, tick);
            case VERIFYING -> verify(s, root, tick);
            case CLEANUP -> cleanup(s, root, tick);
            case EXITING -> exit(s, root, tick);
            default -> { }
        }
    }

    private static boolean journalWaitExpired(Session s, long tick) {
        if (s.journalPendingSince < 0) s.journalPendingSince = tick;
        return tick - s.journalPendingSince >= JOURNAL_WAIT_TICKS;
    }

    private boolean journalCommitted(Session s, long tick) {
        switch (s.journal.written.state()) {
            case SUCCESS -> { s.journalPendingSince = -1; return true; }
            case RUNNING -> {
                if (journalWaitExpired(s, tick)) pause(s, "journal_commit_pending", tick);
            }
            case FAILED, CANCELLED -> {
                s.nextJournalRetry = tick + JOURNAL_RETRY_TICKS;
                pause(s, "journal_write_failed", tick);
            }
        }
        return false;
    }

    private void prepare(Session s, Root root, long tick) {
        if (s.preparation != null && s.prepCursor < s.preparation.size()) {
            var goal = s.preparation.get(s.prepCursor);
            var observed = world.read(goal.pos());
            if (observed == null) { unknown(s, goal.pos(), tick); return; }
            if (goal.matches(observed)) {
                confirmPreparation(s, goal);
                s.prepCursor++;
            } else choose(s, root, goal, tick, null);
            return;
        }
        var survey = SiteSurvey.inspect(s.plan, world, pos -> ownedScaffold(s, pos));
        if (survey.unknown() != null) {
            s.preparationState = "UNKNOWN";
            unknown(s, survey.unknown(), tick);
            return;
        }
        s.missingSince = -1;
        if (survey.blocked() != null) {
            s.preparationState = "BLOCKED";
            if (s.run.continuousRecovery()) failGoal(s, simple(s.plan.anchor(), FOUNDATION, "site_preparation"), survey.blocked());
            else terminal(s, RunState.BLOCKED, survey.blocked());
            return;
        }
        s.surveyRevision = survey.revision();
        if (!s.surveySaved) {
            s.surveySaved = true;
            var sampled = new JsonObject();
            sampled.addProperty("id", UUID.randomUUID().toString());
            sampled.addProperty("type", "site_survey");
            sampled.addProperty("runId", s.run.runId());
            sampled.addProperty("worldUid", s.run.worldUid());
            sampled.addProperty("revision", survey.revision());
            sampled.addProperty("sampledCells", survey.sampled().size());
            var workBefore = new TreeMap<BlockPos, BlockState>(ORDER);
            survey.work().stream().limit(256).forEach(g -> workBefore.put(g.pos(), survey.sampled().get(g.pos())));
            sampled.add("observedWorkCells", SiteSurvey.snapshot(workBefore));
            s.journal.add(sampled);
            save(s);
        }
        if (!survey.work().isEmpty()) {
            if (s.surveyPasses++ >= MAX_RECONCILIATIONS) { terminal(s, RunState.BLOCKED, "site_did_not_stabilize"); return; }
            s.preparation = survey.work();
            s.prepCursor = 0;
            s.preparation.forEach(g -> s.requestedPreparation.add(g.id()));
            s.preparationState = "RUNNING";
            return;
        }
        for (var p : s.plan.approach()) {
            if (!world.stand(p, false)) {
                // If the standing surface is itself a future cell, its current absence is not an
                // excuse for claiming the access is verified. It must be part of PREPARING.
                s.preparationState = "BLOCKED";
                terminal(s, RunState.BLOCKED, "approach_not_standable:" + coords(p));
                return;
            }
        }
        s.preparationState = "DONE";
        s.access = "verified";
        if (s.exteriorStaging == null) s.exteriorStaging = s.plan.outside();
        LOG.info("MAGE_SITE_READY run={} generation={} tick={} planned={} confirmed={} revision={}", s.run.runId(),
                s.run.generation(), tick, s.requestedPreparation.size(), s.coveredPreparation.size(), s.surveyRevision);
        phase(s, RunState.BUILDING);
        save(s);
    }

    private void confirmPreparation(Session s, Goal goal) {
        if (!s.coveredPreparation.add(goal.id())) return;
        if (goal.isAir()) s.confirmedCuts++;
        else if (Set.of("foundation", "approach_support").contains(goal.kind())) s.confirmedFill++;
    }

    private void build(Session s, Root root, long tick) {
        // Requested working equipment is temporary. Preserve pre-existing blocks and never
        // appropriate or remove identical blocks that were already placed by another owner.
        while (s.equipmentCursor < s.plan.equipment().size()) {
            var equipment = s.plan.equipment().get(s.equipmentCursor);
            var observed = world.read(equipment.pos());
            if (observed == null) { unknown(s, equipment.pos(), tick); return; }
            if (equipment.matches(observed) || s.placedEquipment.contains(equipment.pos())) { s.equipmentCursor++; continue; }
            if (!observed.isAir()) { s.equipmentCursor++; continue; }
            s.debt.putIfAbsent(equipment.pos(), new Debt(simple(equipment.pos(), AIR, "cleanup"), equipment, false));
            choose(s, root, equipment, tick, null);
            return;
        }
        var goals = s.plan.goals();
        for (int budget = 0; budget < 128 && s.buildCursor < goals.size(); budget++) {
            var goal = goals.get(s.buildCursor);
            var observed = world.read(goal.pos());
            if (observed == null) { unknown(s, goal.pos(), tick); return; }
            if (goal.matches(observed)) { s.confirmedHouse.add(goal.pos()); s.buildCursor++; continue; }
            s.confirmedHouse.remove(goal.pos());
            if (activeOpening(s, goal.pos(), observed)) { s.buildCursor++; continue; }
            for (var id : goal.dependencies()) {
                var dependency = s.goals.get(id);
                var support = world.read(dependency.pos());
                if (support == null) { unknown(s, dependency.pos(), tick); return; }
                if (!dependency.matches(support)) {
                    s.confirmedHouse.remove(dependency.pos());
                    if (expert(s, goal, "support_removed", "dependency:" + id, tick)) return;
                    choose(s, root, dependency, tick, null);
                    return;
                }
            }
            if (!observed.isAir() && expert(s, goal, "world_plan_mismatch", "different_block_or_static_state", tick)) return;
            choose(s, root, goal, tick, null);
            return;
        }
        if (s.buildCursor == goals.size()) phase(s, RunState.VERIFYING);
    }

    private boolean dependenciesSatisfied(Session s, Goal goal) {
        for (var id : goal.dependencies()) {
            var prerequisite = s.goals.get(id);
            if (prerequisite == null || !prerequisite.matches(world.read(prerequisite.pos()))) return false;
        }
        return true;
    }

    private void verify(Session s, Root root, long tick) {
        Goal mismatch = null;
        s.confirmedHouse.clear();
        s.confirmedAir.clear();
        for (var g : s.plan.goals()) {
            var b = world.read(g.pos());
            if (b == null) { unknown(s, g.pos(), tick); return; }
            if (g.matches(b)) s.confirmedHouse.add(g.pos());
            else if (mismatch == null && !activeOpening(s, g.pos(), b)) mismatch = g;
        }
        for (var g : s.plan.air()) {
            var b = world.read(g.pos());
            if (b == null) { unknown(s, g.pos(), tick); return; }
            if (g.matches(b)) s.confirmedAir.add(g.pos());
            else if (mismatch == null && !s.debt.containsKey(g.pos())) mismatch = g;
        }
        if (mismatch != null) {
            if (++s.repairs > MAX_RECONCILIATIONS) { terminal(s, RunState.BLOCKED, "final_world_not_stable:" + coords(mismatch.pos())); return; }
            if (expert(s, mismatch, "world_plan_mismatch", "verification_mismatch", tick)) return;
            choose(s, root, mismatch, tick, null);
            return;
        }
        phase(s, RunState.CLEANUP);
    }

    private void cleanup(Session s, Root root, long tick) {
        var debt = new ArrayList<>(s.debt.entrySet());
        // Remove work equipment before closing the route used to reach it.
        debt.sort(Comparator.<Map.Entry<BlockPos, Debt>, Boolean>comparing(entry -> entry.getValue().opening())
                .thenComparing(Map.Entry.comparingByKey(ORDER.reversed())));
        for (var entry : debt) {
            var d = entry.getValue();
            var actual = world.read(entry.getKey());
            if (actual == null) { unknown(s, entry.getKey(), tick); return; }
            if (d.restore().matches(actual)) {
                s.debt.remove(entry.getKey()); s.placedEquipment.remove(entry.getKey());
                if (d.opening() && s.debt.values().stream().noneMatch(Debt::opening)) s.openingExit = null;
                recordObligation(s, d, true); save(s); continue;
            }
            if (!d.installed().matches(actual)) {
                terminal(s, RunState.BLOCKED, "cleanup_foreign_change:" + coords(entry.getKey()));
                return;
            }
            if (d.opening() && s.openingExit != null && !world.centered(s.openingExit, .4)) {
                if (!travel(s, root, s.openingExit, "leave_temporary_opening", tick))
                    failGoal(s, d.restore(), "opening_restore_stance_unavailable");
                return;
            }
            if (ownedScaffold(s, entry.getKey())) {
                var base = ownedScaffoldBase(s, entry.getKey());
                if (base != null) {
                    choose(s, root, s.debt.get(base).restore(), tick, null);
                    return;
                }
            }
            choose(s, root, d.restore(), tick, null);
            return;
        }
        for (var restore : s.plan.restoration()) {
            var actual = world.read(restore.pos());
            if (actual == null) { unknown(s, restore.pos(), tick); return; }
            if (!restore.matches(actual)) { choose(s, root, restore, tick, null); return; }
        }
        s.openingExit = null;
        phase(s, RunState.EXITING);
    }

    private static boolean activeOpening(Session s, BlockPos pos, BlockState actual) {
        var debt = s.debt.get(pos);
        return debt != null && debt.opening() && debt.installed().matches(actual);
    }

    private boolean ownedScaffold(Session s, BlockPos pos) {
        var debt = s.debt.get(pos);
        return debt != null && !debt.opening() && "minecraft:scaffolding".equals(debt.installed().block())
                && world.scaffold(pos);
    }

    private BlockPos ownedScaffoldBase(Session s, BlockPos piece) {
        return ownedScaffoldNetwork(s, piece).stream().filter(this::groundedScaffoldBase)
                .min(Comparator.comparing((BlockPos p) -> horizontalDistance(p, piece) != 0)
                        .thenComparingDouble(p -> p.getSquaredDistance(world.feet()))
                        .thenComparing(ORDER)).orElse(null);
    }

    private boolean groundedScaffoldBase(BlockPos pos) {
        var below = pos.down();
        return world.stable(below) && !world.read(below).isOf(net.minecraft.block.Blocks.SCAFFOLDING);
    }

    private Set<BlockPos> ownedScaffoldNetwork(Session s, BlockPos start) {
        var pending = new ArrayDeque<BlockPos>();
        var seen = new HashSet<BlockPos>();
        pending.add(start);
        seen.add(start);
        while (!pending.isEmpty()) {
            var p = pending.removeFirst();
            var debt = s.debt.get(p);
            if (debt == null || debt.opening() || !"minecraft:scaffolding".equals(debt.installed().block())
                    || seen.size() > 1024) return Set.of();
            for (var direction : Direction.values()) {
                var neighbor = world.read(p.offset(direction));
                if (neighbor == null) return Set.of();
                if (neighbor.isOf(net.minecraft.block.Blocks.SCAFFOLDING) && seen.add(p.offset(direction)))
                    pending.addLast(p.offset(direction));
            }
        }
        return seen;
    }

    private boolean cleanupFooting(Session s, BlockPos feet) {
        return world.stand(feet, false) && !world.scaffold(feet) && !world.scaffold(feet.down())
                && !s.debt.containsKey(feet.down());
    }

    private boolean removesEquipment(Session s, Goal goal, Method method) {
        var debt = s.debt.get(goal.pos());
        return s.run.state() == RunState.CLEANUP && method == Method.BREAK && debt != null && !debt.opening();
    }

    private void exit(Session s, Root root, long tick) {
        if (s.readyCheckpointQueued) {
            terminal(s, RunState.READY, "");
            LOG.info("MAGE_READY run={} generation={} tick={} cells={} air={} physical={} commands={} teleports={} exit={}",
                    s.run.runId(), s.run.generation(), tick, s.confirmedHouse.size(), s.confirmedAir.size(),
                    s.run.physicalMutations(), s.run.commandMutations(), s.run.teleportMutations(), coords(s.plan.exit()));
            return;
        }
        if (!finishPaintings(s, root, tick)) return;
        if (!world.stand(s.plan.exit(), false) || !world.stand(s.plan.outside(), false)) {
            if (!expert(s, simple(s.plan.exit(), AIR, "exit"), "access_lost", "exit_not_standable", tick))
                terminal(s, RunState.BLOCKED, "exit_not_standable");
            return;
        }
        var entrance = s.plan.entrance();
        if (route(s, s.plan.exit(), entrance::equals, 384) == null) {
            if (!expert(s, simple(entrance, AIR, "exit"), "access_lost", "entrance_not_reachable_from_exit", tick))
                terminal(s, RunState.BLOCKED, "entrance_not_reachable_from_exit");
            return;
        }
        if (!world.centered(s.plan.exit(), .45)) {
            if (!travel(s, root, s.plan.exit(), "exit", tick)) terminal(s, RunState.BLOCKED, "exit_unreachable");
            return;
        }
        // Do not rely on counts from a previous phase or compare against the work list.
        for (var g : s.plan.goals()) if (!g.matches(world.read(g.pos()))) { phase(s, RunState.VERIFYING); return; }
        for (var g : s.plan.air()) if (!g.matches(world.read(g.pos()))) { phase(s, RunState.VERIFYING); return; }
        for (var g : s.plan.restoration()) if (!g.matches(world.read(g.pos()))) { phase(s, RunState.CLEANUP); return; }
        if (!s.debt.isEmpty()) { phase(s, RunState.CLEANUP); return; }
        s.confirmedAir.addAll(s.plan.air().stream().map(Goal::pos).toList());
        s.access = "exit_confirmed";
        save(s, RunState.READY);
        s.readyCheckpointQueued = true;
    }

    private boolean finishPaintings(Session s, Root root, long tick) {
        for (var painting : s.plan.paintings()) {
            var pos = painting.pos();
            if (world.painting(painting)) {
                if (s.confirmedPaintings.add(pos)) {
                    if (painting.equals(s.pendingPainting)) s.run.physicalMutations(s.run.physicalMutations() + 1);
                    paintingEvent(s, painting, "confirmed", tick);
                    save(s);
                }
                if (painting.equals(s.pendingPainting)) s.pendingPainting = null;
                continue;
            }
            if (world.read(pos) == null) { unknown(s, pos, tick); return false; }
            var support = pos.offset(painting.facing().getOpposite());
            var backing = world.read(support);
            if (backing == null) { unknown(s, support, tick); return false; }
            if (!backing.isSideSolidFullSquare(client.world, support, painting.facing()) || !world.read(pos).isAir()) {
                phase(s, RunState.VERIFYING);
                return false;
            }
            if (s.pendingPainting != null) {
                if (tick >= s.paintingDeadline) {
                    paintingEvent(s, s.pendingPainting, "failed", tick);
                    s.pendingPainting = null;
                    failGoal(s, simple(pos, AIR, "painting"), "painting_unconfirmed");
                }
                return false;
            }
            if (!root.permits()) return false;
            var target = simple(pos, AIR, "painting");
            var choice = world.paintingHit(painting, client.player.getEyePos(), s.options.reach());
            if (choice == null) {
                var route = route(s, world.feet(), candidate -> candidate.getSquaredDistance(pos) <= 36
                        && world.paintingHit(painting, Vec3d.ofCenter(candidate).add(0, 1.12, 0), s.options.reach()) != null,
                        12_000);
                if (!walkToStance(s, target, tick, route, "painting_stance")) failGoal(s, target, "painting_unreachable");
                return false;
            }
            java.util.function.Predicate<net.minecraft.item.ItemStack> targetItem = stack -> world.paintingItem(stack, painting);
            int slot = world.hotbar(targetItem);
            if (slot < 0) {
                if (tick < s.paintingInventoryCheck) return false;
                if (world.swapToHotbar(targetItem)) {
                    s.paintingInventoryCheck = tick + 6;
                } else if (canGive(s) && world.capacity(Items.PAINTING) > 0 && s.requestedPaintings.add(pos)) {
                    world.givePainting(painting);
                    s.paintingInventoryCheck = tick + 60;
                    paintingEvent(s, painting, "supply_requested", tick);
                } else failGoal(s, target, "painting_item_unavailable");
                return false;
            }
            root.select(slot);
            world.place(choice);
            s.pendingPainting = painting;
            s.paintingDeadline = tick + 100;
            paintingEvent(s, painting, "requested", tick);
            s.run.waitingReason("confirming_painting:" + coords(pos));
            save(s);
            return false;
        }
        return true;
    }

    private void paintingEvent(Session s, BuildPlan.Painting painting, String outcome, long tick) {
        var event = new JsonObject();
        event.addProperty("id", UUID.randomUUID().toString());
        event.addProperty("type", "painting");
        event.addProperty("runId", s.run.runId());
        event.addProperty("projectId", s.run.projectId());
        event.addProperty("worldUid", s.run.worldUid());
        event.addProperty("schemaRevision", s.plan.revision());
        event.addProperty("outcome", outcome);
        event.addProperty("observedAtTick", tick);
        event.addProperty("variant", painting.variant());
        event.add("goal", point(painting.pos()));
        s.journal.add(event);
    }
    /** Try a direct interaction, an alternative stance or temporary equipment before consulting the expert. */
    private void choose(Session s, Root root, Goal goal, long tick, Method forced) {
        var observed = world.read(goal.pos());
        if (observed == null) { unknown(s, goal.pos(), tick); return; }
        s.missingSince = -1;
        if (goal.matches(observed)) return;
        boolean breaking = !observed.isAir() && (!observed.isReplaceable() || goal.isAir());
        Method physical = breaking ? Method.BREAK : Method.PLACE;
        if (forced == Method.BREAK || forced == Method.PLACE) physical = forced;
        var item = goal.vanilla().getBlock().asItem();
        var eye = client.player.getEyePos();
        var ctx = context(goal);
        var from = world.feet();
        var physicalContext = stanceContext(ctx, from);
        var key = new PlanHold.Key(goal.id(), physical.wire, physicalContext);
        boolean removingEquipment = removesEquipment(s, goal, physical);
        boolean needsCleanupFooting = removingEquipment && !cleanupFooting(s, from);
        boolean physicalAllowed = physical == Method.BREAK ? s.options.breakBlocks() :
                s.options.place() && s.options.useItems() && item != Items.AIR && !goal.isAir();
        var pair = s.plan.pairByCell().get(goal.pos());
        if (pair != null && !pair.getFirst().pos().equals(goal.pos()) && physical == Method.PLACE) physicalAllowed = false;
        if (physicalAllowed) {
            if (physical == Method.BREAK && s.hold.permits(key, 2, GOAL_FAILURE_LIMIT)
                    && (!removingEquipment || !needsCleanupFooting && !world.overlapsPlayer(goal.pos()))) {
                var hit = world.breakHit(goal.pos(), eye, s.options.reach());
                if (hit != null && observed.getHardness(client.world, goal.pos()) >= 0) {
                    var tool = world.miningTool(observed);
                    if (tool == null && observed.calcBlockBreakingDelta(client.player, client.world, goal.pos()) < .02f) {
                        var supply = ServerActions.supplyTool(observed);
                        if (supply != null && requestSupply(s, goal, ctx, tick, supply, 1)) return;
                    }
                    if (tool == null || world.hotbar(tool) >= 0) {
                        begin(s, goal, physical, physicalContext, tick, List.of(), tool, null, "direct_visible", 0);
                        return;
                    }
                    if (s.hold.permits(new PlanHold.Key(goal.id(), Method.SWAP.wire, ctx), 1, GOAL_FAILURE_LIMIT)) {
                        begin(s, goal, Method.SWAP, ctx, tick, List.of(), tool, null, "mining_tool_in_inventory", 0);
                        return;
                    }
                }
            } else if (physical == Method.PLACE && s.hold.permits(key, 2, GOAL_FAILURE_LIMIT)
                    && !world.overlapsPlayer(goal.pos()) && (pair == null || !world.overlapsPlayer(pair.getLast().pos()))
                    && !s.rejectedPlacementStances.contains(new StanceAttempt(goal.id(), ctx, from))) {
                var aim = world.placement(goal, item, s.options.reach());
                if (aim != null) {
                    if (world.hotbar(item) >= 0) {
                        begin(s, goal, physical, physicalContext, tick, List.of(), item, null, "direct_supported_face", 0);
                        return;
                    }
                    if (world.count(item) > 0 && s.hold.permits(new PlanHold.Key(goal.id(), Method.SWAP.wire, ctx), 1, GOAL_FAILURE_LIMIT)) {
                        begin(s, goal, Method.SWAP, ctx, tick, List.of(), item, null, "material_in_inventory", 0);
                        return;
                    }
                    int need = (int) s.plan.goals().stream()
                            .filter(g -> g.vanilla().getBlock().asItem() == item && !s.confirmedHouse.contains(g.pos())).count();
                    if (requestSupply(s, goal, ctx, tick, item, Math.max(1, need))) return;
                }
            }
            boolean materialForAlternative = physical == Method.BREAK || world.count(item) > 0 || canGive(s);
            if (materialForAlternative && s.walkedAlternatives.getOrDefault(new StancePhase(goal.id(), ctx), 0) < 4) {
                final Method wanted = physical;
                boolean closingOpening = s.openingExit != null && activeOpening(s, goal.pos(), observed);
                var path = localAccessRoute(s, from, goal.pos(), candidate -> {
                    if (candidate.equals(from) || candidate.equals(goal.pos()) || candidate.up().equals(goal.pos())
                            || closingOpening && !outside(s.plan, candidate)
                            || removingEquipment && !cleanupFooting(s, candidate)
                            || s.triedStances.contains(new StanceAttempt(goal.id(), ctx, candidate))
                            || s.rejectedPlacementStances.contains(new StanceAttempt(goal.id(), ctx, candidate))
                            || candidate.getSquaredDistance(goal.pos()) > 36
                            || !s.hold.permits(new PlanHold.Key(goal.id(), wanted.wire, stanceContext(ctx, candidate)),
                            2, GOAL_FAILURE_LIMIT)) return false;
                    Vec3d nextEye = new Vec3d(candidate.getX() + .5, candidate.getY() + 1.62, candidate.getZ() + .5);
                    return wanted == Method.BREAK ? world.breakHit(goal.pos(), nextEye, s.options.reach()) != null
                            : world.placement(goal, item, nextEye, s.options.reach()) != null;
                });
                String strategy = needsCleanupFooting ? "cleanup_leave_support" : "alternate_checked_stance";
                if (walkToStance(s, goal, tick, path, strategy)) return;
            }
        }
        if (forced != null) { failGoal(s, goal, "expert_physical_step_unavailable"); return; }
        if (!breaking && physicalAllowed && s.run.state() == RunState.BUILDING && "structure".equals(goal.kind())
                && tryScaffolding(s, goal, tick)) return;
        if (!breaking && physicalAllowed && s.run.state() == RunState.BUILDING
                && Set.of("cleanup", "structure").contains(goal.kind()) && trySupport(s, goal, tick)) return;
        if (physicalAllowed && !goal.isAir() && s.run.state() == RunState.BUILDING && !"cleanup".equals(goal.kind())
                && "structure".equals(goal.kind()) && tryPlatform(s, root, goal, tick)) return;
        if (world.overlapsPlayer(goal.pos()) && !goal.isAir()) {
            if (!travel(s, root, s.plan.exit(), "leave_target_cell", tick)) failGoal(s, goal, "target_player_collision");
            return;
        }
        String reason = !physicalAllowed ? "physical_method_unavailable"
                : physical == Method.PLACE && world.count(item) == 0 && !canGive(s) ? "material_unavailable"
                : "physical_variants_exhausted";
        failGoal(s, goal, reason);
    }

    private boolean tryScaffolding(Session s, Goal goal, long tick) {
        if (!canScaffold(s)) return false;
        for (var column : reachableScaffolds(s, goal, 2)) {
            if (useScaffold(s, column, tick, "scaffold_work_stance")) return true;
        }
        return false;
    }

    private boolean canScaffold(Session s) {
        return s.options.scaffolding() && s.options.place() && s.options.useItems() && s.options.breakBlocks()
                && (world.count(Items.SCAFFOLDING) > 0 || (canGive(s) && world.capacity(Items.SCAFFOLDING) > 0
                && !s.spentSupply.contains("minecraft:scaffolding")));
    }

    private List<ScaffoldColumn> scaffoldColumns(Session s, Goal goal, int heights) {
        var candidates = new ArrayList<ScaffoldColumn>();
        var target = goal.pos();
        for (int dx = -3; dx <= 3; dx++) for (int dz = -3; dz <= 3; dz++) {
            int distance = Math.abs(dx) + Math.abs(dz);
            if (distance < 1 || distance > 4) continue;
            for (int down = 1; down <= heights; down++) {
                var column = scaffoldColumn(s, target.add(dx, -down, dz));
                if (column != null) candidates.add(column);
            }
        }
        candidates.sort(Comparator.comparingInt(ScaffoldColumn::missing)
                .thenComparing(column -> !s.plan.scaffoldAccess().contains(column.top().withY(s.plan.min().getY())))
                .thenComparingInt(column -> horizontalDistance(column.top(), target))
                .thenComparingDouble(column -> column.base().getSquaredDistance(world.feet())));
        return candidates;
    }

    private ScaffoldColumn scaffoldColumn(Session s, BlockPos top) {
        for (var p : List.of(top.up(), top.up(2))) {
            if (s.plan.occupied().containsKey(p) || s.plan.circulationClearance().contains(p) || !world.passable(p, false)) return null;
        }
        BlockPos next = null;
        int missing = 0;
        for (var p = top; p.getY() >= s.plan.min().getY() - 18; p = p.down()) {
            if (world.stable(p) && !world.scaffold(p)) return p.equals(top) ? null : new ScaffoldColumn(p.up(), top, next, missing);
            if (s.plan.occupied().containsKey(p) || s.plan.circulationClearance().contains(p)
                    || s.plan.entrance().equals(p) || s.plan.entrance().equals(p.down())
                    || s.plan.approach().contains(p) || s.plan.approach().contains(p.down())) return null;
            var block = world.read(p);
            if (block == null) return null;
            if (ownedScaffold(s, p)) continue;
            if (!block.isAir()) return null;
            var debt = s.debt.get(p);
            if (debt != null && (debt.opening() || !"minecraft:scaffolding".equals(debt.installed().block()))) return null;
            next = p;
            missing++;
        }
        return null;
    }

    private List<ScaffoldColumn> reachableScaffolds(Session s, Goal goal, int heights) {
        var seen = new HashSet<BlockPos>();
        var observed = world.read(goal.pos());
        var item = goal.vanilla().getBlock().asItem();
        return scaffoldColumns(s, goal, heights).stream().filter(column -> {
            if (column.stance().equals(world.feet()) || seen.contains(column.base())
                    || column.missing() > world.count(Items.SCAFFOLDING) && !canGive(s)) return false;
            var eye = Vec3d.ofBottomCenter(column.stance()).add(0, 1.62, 0);
            boolean reaches = observed != null && !observed.isAir()
                    ? !goal.matches(observed) && world.breakHit(goal.pos(), eye, s.options.reach()) != null
                    : item != Items.AIR && world.placement(goal, item, eye, s.options.reach()) != null;
            return reaches && scaffoldReachable(s, column) && seen.add(column.base());
        }).limit(8).toList();
    }

    private boolean scaffoldReachable(Session s, ScaffoldColumn column) {
        if (column.missing() == 0) return scaffoldRoute(s, column) != null;
        if (equipmentApproach(s, column.next(), Items.SCAFFOLDING) == null) return false;
        // Until the bottom section exists, check that a route actually reaches its entry level.
        // The grounded column provides the later vertical route in both directions.
        if (ownedScaffold(s, column.base())) return true;
        return localAccessRoute(s, world.feet(), column.base(), p -> p.getY() == column.base().getY()
                && horizontalDistance(p, column.base()) == 1 && !world.scaffold(p) && !world.scaffold(p.down())) != null;
    }

    private List<BlockPos> scaffoldRoute(Session s, ScaffoldColumn column) {
        return localAccessRoute(s, world.feet(), column.stance(), column.stance()::equals);
    }

    private boolean useScaffold(Session s, ScaffoldColumn column, long tick, String strategy) {
        if (column.missing() > 0)
            return scaffoldReachable(s, column) && installEquipment(s, column.next(), Items.SCAFFOLDING, column.missing(), tick);
        return walkToStance(s, simple(column.stance(), AIR, strategy), tick, scaffoldRoute(s, column), strategy);
    }

    private boolean trySupport(Session s, Goal goal, long tick) {
        if (!world.placeHits(goal.pos(), client.player.getEyePos(), s.options.reach()).isEmpty()) return false;
        var pos = goal.pos().down();
        for (int depth = 0; depth < 5; depth++, pos = pos.down()) {
            var block = world.read(pos);
            if (block == null || !block.isAir() || s.plan.occupied().containsKey(pos) || s.debt.containsKey(pos)
                    || s.plan.approach().contains(pos) || s.plan.approach().contains(pos.up()) || world.overlapsPlayer(pos)) return false;
            if (world.stable(pos.down())) return installSupport(s, pos, tick);
        }
        return false;
    }

    private boolean tryPlatform(Session s, Root root, Goal goal, long tick) {
        var feet = world.feet();
        var target = goal.pos();
        if (s.placedEquipment.size() >= s.plan.equipment().size() + s.plan.goals().size()
                || feet.getY() >= target.getY()) return false;
        boolean jumpClear = world.passable(feet.up(2), false);
        if (horizontalDistance(feet, target) > 4 || !jumpClear) {
            var approach = route(s, feet, candidate -> horizontalDistance(candidate, target) <= 3
                    && candidate.getY() <= target.getY() && world.passable(candidate.up(2), false), 4_000);
            if (walkToStance(s, goal, tick, approach, "temporary_platform_approach")) return true;
        }
        // Some targets need more than one step of elevation before a click is possible.
        var ascent = route(s, feet, candidate -> candidate.getY() > feet.getY()
                && candidate.getY() <= target.getY() && s.placedEquipment.contains(candidate.down())
                && manhattan(candidate, goal.pos()) <= manhattan(feet, goal.pos()), 1_200);
        if (walkToStance(s, goal, tick, ascent, "temporary_platform_ascent")) return true;
        if (!jumpClear) return false;
        for (var dir : net.minecraft.util.math.Direction.Type.HORIZONTAL) {
            var p = feet.offset(dir);
            var at = world.read(p);
            if (at == null || !at.isAir() || s.plan.occupied().containsKey(p) || s.debt.containsKey(p)
                    || s.plan.approach().contains(p) || s.plan.approach().contains(p.down())
                    || !world.passable(p.up(), false) || !world.passable(p.up(2), false)
                    || manhattan(p, goal.pos()) > manhattan(feet, goal.pos()) + 1) continue;
            if (world.stable(p.down()) ? installSupport(s, p, tick)
                    : trySupport(s, simple(p, "minecraft:cobblestone", "cleanup"), tick)) return true;
        }
        return false;
    }

    private boolean recoverAccess(Session s, Goal goal, long tick) {
        var staging = s.exteriorStaging;
        if (staging == null || !world.stand(staging, true)) staging = s.plan.exit();
        if (!world.stand(staging, true)) return false;
        var exterior = staging;
        var from = world.feet();
        var route = localAccessRoute(s, from, exterior, exterior::equals);
        String attempt = goal.id() + ':' + context(goal);
        if (route != null) {
            if (!s.exteriorAttempts.contains(attempt) && walkToStance(s, goal, tick, route, "return_to_exterior_staging")) {
                s.exteriorAttempts.add(attempt);
                return true;
            }
            return false;
        }
        if (s.openingExit != null || !s.options.breakBlocks() || !s.options.place() || !s.options.useItems()) return false;

        // Search from reachable interior stances. Only consider a one-block-thick wall with a
        // safe landing outside and a route from that landing to the saved staging point.
        var found = new ArrayList<Goal>();
        BlockPos[] exit = {null};
        var approach = route(s, from, p -> {
            if (p.getSquaredDistance(from) > 36) return false;
            for (var direction : Direction.Type.HORIZONTAL) {
                var wall = p.offset(direction);
                var landing = wall.offset(direction);
                if (!outside(s.plan, landing) || outside(s.plan, p) || !world.stand(landing, false)) continue;
                var cuts = restorableCuts(s, goal, wall);
                if (cuts.isEmpty()) continue;
                var eye = Vec3d.ofBottomCenter(p).add(0, 1.62, 0);
                if (world.breakHit(cuts.getFirst().pos(), eye, s.options.reach()) == null
                        || route(s, landing, exterior::equals, 6_000) == null) continue;
                found.addAll(cuts);
                exit[0] = landing;
                return true;
            }
            return false;
        }, 1_200);
        if (approach == null || found.isEmpty()) return false;
        if (!approach.isEmpty()) return walkToStance(s, goal, tick, approach, "temporary_opening_approach");
        s.openingExit = exit[0];
        for (var restore : found) {
            var cut = simple(restore.pos(), AIR, "temporary_opening");
            var obligation = new Debt(restore, cut, true);
            s.debt.put(cut.pos(), obligation);
            recordObligation(s, obligation, false);
            s.openingCuts.addLast(cut);
        }
        LOG.info("MAGE_OPENING run={} target={} exit={} cuts={}", s.run.runId(), coords(goal.pos()), coords(s.openingExit),
                found.stream().map(cut -> coords(cut.pos())).toList());
        save(s);
        return true;
    }

    private List<Goal> restorableCuts(Session s, Goal target, BlockPos wall) {
        if (wall.getY() <= s.plan.anchor().getY() || !world.stable(wall.down())) return List.of();
        var cuts = new ArrayList<Goal>(2);
        for (var cell : List.of(wall.up(), wall)) {
            var state = world.read(cell);
            if (state == null || cell.equals(target.pos()) || s.debt.containsKey(cell)
                    || s.plan.circulationClearance().contains(cell) || s.plan.pairByCell().containsKey(cell)) return List.of();
            if (state.isAir()) continue;
            if (!s.plan.occupied().containsKey(cell) || !world.stable(cell) || state.hasBlockEntity()
                    || state.getHardness(client.world, cell) < 0) return List.of();
            var item = state.getBlock().asItem();
            if (item == Items.AIR || world.count(item) == 0 && (!canGive(s) || world.capacity(item) == 0)) return List.of();
            for (var direction : Direction.values()) {
                var neighbor = cell.offset(direction);
                var block = world.read(neighbor);
                if (block == null || (!block.isAir() && (!world.stable(neighbor) || block.hasBlockEntity()))) return List.of();
            }
            if (s.plan.paintings().stream().anyMatch(p -> cell.equals(p.pos().offset(p.facing().getOpposite())))) return List.of();
            cuts.add(fromState(cell, state, "cleanup"));
        }
        return cuts;
    }

    private static boolean outside(BuildPlan plan, BlockPos p) {
        return p.getX() < plan.min().getX() || p.getX() > plan.max().getX()
                || p.getZ() < plan.min().getZ() || p.getZ() > plan.max().getZ();
    }

    private boolean advanceOpening(Session s, Root root, long tick) {
        if (s.openingCuts.isEmpty()) return false;
        var cut = s.openingCuts.getFirst();
        var actual = world.read(cut.pos());
        if (cut.matches(actual)) {
            s.openingCuts.removeFirst();
            save(s);
        } else if (actual == null) {
            unknown(s, cut.pos(), tick);
        } else if (s.debt.get(cut.pos()).restore().matches(actual)) {
            choose(s, root, cut, tick, Method.BREAK);
        } else {
            V5Runtime.get().waitForRecovery(s.run, "recovery_waiting:opening_block_changed:" + coords(cut.pos()));
        }
        return true;
    }

    private static int horizontalDistance(BlockPos first, BlockPos second) {
        return Math.abs(first.getX() - second.getX()) + Math.abs(first.getZ() - second.getZ());
    }

    private boolean installSupport(Session s, BlockPos pos, long tick) {
        return installEquipment(s, pos, Items.COBBLESTONE, 8, tick);
    }

    private ScaffoldApproach equipmentApproach(Session s, BlockPos pos, Item item) {
        var goal = simple(pos, Registries.ITEM.getId(item).toString(), "cleanup");
        if (!world.overlapsPlayer(pos) && world.placement(goal, item, s.options.reach()) != null)
            return new ScaffoldApproach(true, List.of());
        var from = world.feet();
        String local = context(goal);
        var route = localAccessRoute(s, from, pos,
                candidate -> !candidate.equals(from) && !candidate.equals(pos) && !candidate.up().equals(pos)
                && (item == Items.SCAFFOLDING ? horizontalDistance(candidate, pos) <= 5 : candidate.getSquaredDistance(pos) <= 36)
                && !s.triedStances.contains(new StanceAttempt(goal.id(), local, candidate))
                && world.placement(goal, item, Vec3d.ofBottomCenter(candidate).add(0, 1.62, 0), s.options.reach()) != null);
        return route == null || route.isEmpty() ? null : new ScaffoldApproach(false, route);
    }

    private boolean installEquipment(Session s, BlockPos pos, Item item, int amount, long tick) {
        var equipment = simple(pos, Registries.ITEM.getId(item).toString(), "cleanup");
        var key = new PlanHold.Key(equipment.id(), Method.PLACE.wire, context(equipment));
        if (!s.hold.permits(key, 1, GOAL_FAILURE_LIMIT)) return false;
        var approach = equipmentApproach(s, pos, item);
        if (approach == null) return false;
        if (!approach.direct()) return walkToStance(s, equipment, tick, approach.route(),
                item == Items.SCAFFOLDING ? "scaffold_base_approach" : "alternate_checked_stance");
        if (world.hotbar(item) < 0) {
            if (world.count(item) == 0) return requestSupply(s, equipment, key.context(), tick, item, amount);
            if (!s.hold.permits(new PlanHold.Key(equipment.id(), Method.SWAP.wire, key.context()), 1, GOAL_FAILURE_LIMIT)) return false;
            begin(s, equipment, Method.SWAP, key.context(), tick, List.of(), item, null, "support_material_in_inventory", 0);
            return true;
        }
        var obligation = new Debt(simple(pos, AIR, "cleanup"), equipment, false);
        s.debt.put(pos, obligation);
        recordObligation(s, obligation, false);
        String reason = item == Items.SCAFFOLDING ? "scaffold_installation" : "temporary_work_platform";
        begin(s, equipment, Method.PLACE, key.context(), tick, List.of(), item, null, reason, 0);
        return true;
    }

    private boolean canGive(Session s) {
        return s.options.supply() && world.canGive();
    }

    private boolean requestSupply(Session s, Goal goal, String context, long tick, Item item, int need) {
        if (!canGive(s) || world.count(item) > 0) return false;
        int amount = Math.min(48, Math.min(need, world.capacity(item)));
        if (amount == 0) return false;
        String id = Registries.ITEM.getId(item).toString();
        if (s.spentSupply.contains(id)) return false;
        if (tick - s.lastGive < 20) {
            s.waitUntil = s.lastGive + 20;
            return true;
        }
        s.spentSupply.add(id); // Persist the request before sending any item delivery.
        begin(s, goal, Method.GIVE, context, tick, List.of(), item, null, "missing_item", amount);
        return true;
    }

    private boolean travel(Session s, Root root, BlockPos destination, String reason, long tick) {
        var spatial = simple(destination, AIR, reason);
        if (!world.stand(destination, false)) return false;
        var ctx = context(spatial);
        var route = route(s, world.feet(), destination::equals, 12_000);
        if (world.feet().equals(destination)) route = List.of(destination);
        if (route != null && !route.isEmpty() && s.hold.permits(new PlanHold.Key(spatial.id(), Method.WALK.wire, ctx), 1, GOAL_FAILURE_LIMIT)) {
            begin(s, spatial, Method.WALK, ctx, tick, route, null, destination, reason, 0);
            return true;
        }
        return false;
    }

    private boolean walkToStance(Session s, Goal goal, long tick, List<BlockPos> route, String reason) {
        if (route == null || route.isEmpty()) return false;
        var context = routeContext(context(goal), route);
        var key = new PlanHold.Key(goal.id(), Method.WALK.wire, context);
        if (s.walkedContexts.contains(key) || !s.hold.permits(key, 1, GOAL_FAILURE_LIMIT)) return false;
        begin(s, goal, Method.WALK, context, tick, route, null, route.getLast(), reason, 0);
        return true;
    }

    private List<BlockPos> localAccessRoute(Session s, BlockPos start, BlockPos target,
                                            Predicate<BlockPos> destination) {
        var path = route(s, start, destination, 12_000);
        // An equipment approach is local to the build. A distant detour can unload both the
        // target and the starting supports before the actor reaches the proposed work position.
        int detour = 24;
        return path == null || path.size() > manhattan(start, target) + detour ? null : path;
    }

    private List<BlockPos> route(Session s, BlockPos start, Predicate<BlockPos> destination, int budget) {
        return world.route(start, destination, budget, true, (from, to) -> {
            var edges = s.blockedTransitions.get(from);
            var previous = edges == null ? null : edges.get(to);
            return previous == null || !previous.equals(transitionStates(from, to));
        });
    }

    private List<BlockState> transitionStates(BlockPos from, BlockPos to) {
        return Arrays.asList(world.read(from.down()), world.read(from), world.read(from.up()), world.read(from.up(2)),
                world.read(to.down()), world.read(to), world.read(to.up()));
    }

    private String routeContext(String goal, List<BlockPos> path) {
        var conditions = new StringBuilder(goal);
        for (var p : path) conditions.append(p).append(':').append(world.read(p.down())).append(':')
                .append(world.read(p)).append(':').append(world.read(p.up()));
        return BuildJournal.digest(conditions.toString());
    }

    private static String stanceContext(String context, BlockPos stance) {
        return context + '|' + coords(stance);
    }

    private String context(Goal goal) {
        var hash = new StringBuilder();
        var p = goal.pos();
        hash.append(world.read(p)).append('|');
        for (var side : net.minecraft.util.math.Direction.values()) hash.append(world.read(p.offset(side))).append('|');
        // The strategy context omits player yaw, selected slot, tick and transient movement.
        return BuildJournal.digest(hash.toString());
    }

    private void begin(Session s, Goal goal, Method method, String context, long tick, List<BlockPos> route,
                       Item item, BlockPos destination, String reason, int give) {
        var key = new PlanHold.Key(goal.id(), method.wire, context);
        if (!s.hold.hold(key)) throw new IllegalStateException("two actions in a single hold");
        var supports = new JsonArray();
        var positions = new LinkedHashSet<BlockPos>();
        if (method == Method.PLACE || method == Method.BREAK) {
            for (var dir : net.minecraft.util.math.Direction.values()) positions.add(goal.pos().offset(dir));
        }
        for (var point : route) positions.add(point.down());
        for (var p : positions) {
            var state = world.read(p);
            if (state == null || state.isAir()) continue;
            var support = point(p);
            support.addProperty("block", Registries.BLOCK.getId(state.getBlock()).toString());
            support.add("state", propertyJson(state));
            supports.add(support);
        }
        s.op = new Operation(goal, method, key, context(goal), s.run.generation(), tick, world.feet(),
                List.copyOf(route), supports, reason, item, destination);
        s.op.giveCount = give;
        if (item != null) s.op.beforeCount = world.count(item);
        s.op.intent.addProperty("goalId", goal.id());
        s.op.intent.add("goal", goalJson(goal));
        s.op.intent.addProperty("method", method.wire);
        s.op.intent.addProperty("context", context);
        if (method == Method.WALK && Set.of("alternate_checked_stance", "scaffold_base_approach", "cleanup_leave_support").contains(reason)) {
            s.op.intent.addProperty("stanceContext", context(goal));
            s.op.intent.add("stanceDestination", point(destination));
        }
        if (method == Method.PLACE && "direct_supported_face".equals(reason)) {
            s.op.intent.addProperty("stanceContext", context(goal));
            s.op.intent.add("stanceDestination", point(s.op.from));
        }
        s.op.intent.addProperty("generation", s.run.generation());
        s.op.intent.addProperty("startedAtTick", tick);
        if (item != null) s.op.intent.addProperty("item", Registries.ITEM.getId(item).toString());
        s.run.waitingReason("intent_commit_pending");
        save(s);
    }

    private void advanceOperation(Session s, Root root, long tick) {
        var op = s.op;
        var observed = world.read(op.goal.pos());
        var expected = op.method == Method.BREAK ? simple(op.goal.pos(), AIR, "break_effect") : op.goal;
        if (op.sent >= 0) {
            switch (op.method) {
                case PLACE -> {
                    if (op.goal.matches(observed)) { finish(s, "confirmed", "observed_target_state", observed, tick); return; }
                    if (observed != null && observed.isOf(op.goal.vanilla().getBlock()) && tick >= op.sent + 6) {
                        finish(s, "conflict", "server_placed_different_state", observed, tick);
                        return;
                    }
                }
                case BREAK -> {
                    if (expected.matches(observed)) { op.breaking = false; finish(s, "confirmed", "observed_air", observed, tick); return; }
                }
                case WALK -> {
                    appendPosition(op);
                    if (world.centered(op.destination, .40)) {
                        root.releaseMovement();
                        finish(s, "confirmed", "observed_working_position", observed, tick);
                        return;
                    }
                }
                case GIVE -> {
                    int delta = world.count(op.item) - op.beforeCount;
                    if (delta > 0 && (delta >= op.giveCount || tick >= op.sent + 6)) {
                        op.intent.addProperty("delivered", delta);
                        var itemId = Registries.ITEM.getId(op.item).toString();
                        if (delta >= op.giveCount) s.spentSupply.remove(itemId);
                        finish(s, "confirmed", delta >= op.giveCount ? "delivery_observed" : "partial_delivery_observed", observed, tick);
                        return;
                    }
                }
                case SWAP -> {
                    if (world.hotbar(op.item) >= 0 && tick >= op.sent + 3) {
                        finish(s, "confirmed", "hotbar_item_observed", observed, tick);
                        return;
                    }
                }
                default -> { }
            }
            int budget = op.method == Method.BREAK ? 72 : op.method == Method.WALK ? Math.min(2_000, 20 + op.route.size() * 12) : ACK_TICKS;
            if (tick - op.sent >= budget) {
                if (op.breaking) abortBreak(s, op);
                root.releaseMovement();
                finish(s, "timeout", "server_effect_unconfirmed", observed, tick);
                return;
            }
        }
        if (!root.permits() || op.generation != s.run.generation()) return;
        if (op.sent < 0 && observed == null && op.method != Method.WALK) {
            finish(s, "conflict", "goal_no_longer_observed", null, tick);
            return;
        }
        if (op.sent < 0 && (op.method == Method.PLACE || op.method == Method.BREAK)
                && expected.matches(observed)) {
            finish(s, "confirmed", "already_observed_before_send", observed, tick);
            return;
        }
        var opening = s.debt.get(op.goal.pos());
        if (op.method == Method.BREAK && opening != null && opening.opening() && !opening.restore().matches(observed)) {
            if (op.breaking) abortBreak(s, op);
            finish(s, "conflict", "opening_block_changed", observed, tick);
            return;
        }
        if (op.sent < 0) {
            if (tick - s.lastActionTick < 1) return;
            send(s, root, op, observed, tick);
        } else if (op.method == Method.WALK) drive(s, root, op, tick);
        else if (op.method == Method.BREAK && op.breaking) {
            var hit = world.breakHit(op.goal.pos(), client.player.getEyePos(), s.options.reach());
            if (observed == null || hit == null) {
                abortBreak(s, op);
                finish(s, "failed", "break_visibility_lost", observed, tick);
            } else {
                op.damage += observed.calcBlockBreakingDelta(client.player, client.world, op.goal.pos());
                if (op.damage >= 1 && tick - op.sent >= 2) {
                    world.breakPacket(PlayerActionC2SPacket.Action.STOP_DESTROY_BLOCK, op.hit);
                    op.breaking = false;
                }
            }
        }
    }

    private void send(Session s, Root root, Operation op, BlockState observed, long tick) {
        if (!root.permits()) return;
        var goal = op.goal;
        if (Set.of(Method.PLACE, Method.BREAK).contains(op.method)
                && (!op.worldContext.equals(context(goal)) || !dependenciesSatisfied(s, goal))) {
            finish(s, "conflict", "action_world_or_dependency_changed", observed, tick);
            return;
        }
        switch (op.method) {
            case PLACE -> {
                int slot = world.hotbar(op.item);
                var choice = slot >= 0 && !world.overlapsPlayer(goal.pos()) ? world.placement(goal, op.item, s.options.reach()) : null;
                if (choice == null) { finish(s, "conflict", "placement_precondition_changed", observed, tick); return; }
                root.select(slot);
                world.place(choice);
            }
            case BREAK -> {
                if (removesEquipment(s, goal, op.method) && (!cleanupFooting(s, world.feet())
                        || world.overlapsPlayer(goal.pos())
                        || world.scaffold(goal.pos()) && groundedScaffoldBase(goal.pos())
                        && ownedScaffoldNetwork(s, goal.pos()).isEmpty())) {
                    finish(s, "conflict", "cleanup_footing_or_ownership_changed", observed, tick);
                    return;
                }
                var hit = world.breakHit(goal.pos(), client.player.getEyePos(), s.options.reach());
                if (hit == null) { finish(s, "conflict", "break_precondition_changed", observed, tick); return; }
                if (op.item != null) {
                    int slot = world.hotbar(op.item);
                    if (slot < 0) { finish(s, "conflict", "mining_tool_unavailable", observed, tick); return; }
                    root.select(slot);
                }
                var rotation = ServerActions.angles(client.player.getEyePos(), hit.getPos());
                world.look(rotation[0], rotation[1]);
                op.hit = hit;
                op.breaking = true;
                op.damage = observed.calcBlockBreakingDelta(client.player, client.world, goal.pos());
                world.breakPacket(PlayerActionC2SPacket.Action.START_DESTROY_BLOCK, hit);
            }
            case GIVE -> {
                if (!canGive(s) || world.count(op.item) != op.beforeCount || world.capacity(op.item) < op.giveCount) {
                    finish(s, "conflict", "supply_precondition_changed", observed, tick); return;
                }
                world.give(op.item, op.giveCount);
                s.lastGive = tick;
            }
            case SWAP -> {
                if (!world.swapToHotbar(op.item)) { finish(s, "failed", "no_accessible_inventory_slot", observed, tick); return; }
            }
            case WALK -> { }
            case OBSERVE -> { finish(s, "confirmed", "loaded_snapshot", observed, tick); return; }
        }
        op.sent = tick;
        s.lastActionTick = tick;
        LOG.info("MAGE_SEND run={} generation={} tick={} phase={} id={} method={} at={} expected={} reason={}", s.run.runId(),
                s.run.generation(), tick, s.run.phase(), op.intent.get("id").getAsString(), op.method.wire, coords(goal.pos()),
                goal.commandState(), op.reason);
        s.run.waitingReason(op.method.wire + "_observation_pending");
        if (op.method == Method.WALK) drive(s, root, op, tick);
    }

    private void drive(Session s, Root root, Operation op, long tick) {
        if (!root.permits()) return;
        while (op.walkIndex < op.route.size() - 1 && world.centered(op.route.get((int) op.walkIndex), .38)) op.walkIndex++;
        BlockPos step = op.route.get((int) op.walkIndex);
        if (!world.stand(step, true) || !supportsCurrent(op.supports)) {
            root.releaseMovement();
            finish(s, "failed", "route_support_or_clearance_changed", world.read(op.goal.pos()), tick);
            return;
        }
        var from = op.walkIndex == 0 ? op.from : op.route.get((int) op.walkIndex - 1);
        var playerPosition = new Vec3d(client.player.getX(), client.player.getY(), client.player.getZ());
        double distance = playerPosition.squaredDistanceTo(Vec3d.ofBottomCenter(step));
        if (op.waypointIndex != op.walkIndex || distance + .04 < op.waypointDistance) {
            op.waypointIndex = op.walkIndex;
            op.waypointDistance = distance;
            op.waypointProgressTick = tick;
        } else if (tick - op.waypointProgressTick >= 42) {
            s.blockedTransitions.computeIfAbsent(from, p -> new HashMap<>()).put(step, transitionStates(from, step));
            op.intent.add("blockedTransition", points(List.of(from, step)));
            root.releaseMovement();
            finish(s, "failed", "route_step_no_progress", world.read(op.goal.pos()), tick);
            return;
        }
        if (world.scaffoldVertical(from, step) && world.centeredHorizontally(step, .28)) {
            root.key(client.options.forwardKey, false);
            root.key(client.options.jumpKey, step.getY() > from.getY());
            root.key(client.options.sneakKey, step.getY() < from.getY());
            return;
        }
        if (step.getY() < from.getY() - 1 && world.centeredHorizontally(step, .28)) {
            root.releaseMovement();
            return;
        }
        root.key(client.options.sneakKey, false);
        if (world.closedWoodenDoor(step)) {
            var hit = world.doorHit(step, client.player.getEyePos(), s.options.reach());
            if (hit != null) {
                root.releaseMovement();
                if (tick - s.lastActionTick > 6) {
                    var rotation = ServerActions.angles(client.player.getEyePos(), hit.getPos());
                    world.place(new ServerActions.Choice(hit, rotation[0], rotation[1]));
                    s.lastActionTick = tick;
                }
                return;
            }
        }
        var point = new Vec3d(step.getX() + .5, client.player.getEyePos().y, step.getZ() + .5);
        float yaw = ServerActions.angles(client.player.getEyePos(), point)[0];
        client.player.setYaw(yaw);
        client.player.setPitch(0);
        root.key(client.options.forwardKey, true);
        root.key(client.options.jumpKey, !world.scaffoldVertical(from, step) && !world.stair(step.down())
                && step.getY() > world.feet().getY());
    }

    private boolean supportsCurrent(JsonArray supports) {
        for (var entry : supports) {
            var obj = asObject(entry);
            var state = world.read(position(obj));
            if (state == null || !Registries.BLOCK.getId(state.getBlock()).toString().equals(text(obj, "block", ""))
                    || !propertyJson(state).equals(object(obj, "state"))) return false;
        }
        return true;
    }

    private void appendPosition(Operation op) {
        var p = world.feet();
        if (op.actual.size() < 32 && (op.actual.isEmpty() ? !op.from.equals(p) : !op.actual.getLast().equals(p))) op.actual.add(p);
    }

    private void abortBreak(Session s, Operation op) {
        if (!op.breaking) return;
        op.breaking = false;
        if (op.hit != null && client.world == s.boundWorld && client.getNetworkHandler() != null) {
            // Cancel only the exact vanilla destroy session the builder initiated. This is release,
            // not a new plan action; do not change any other owner's inputs or mining target.
            world.breakPacket(PlayerActionC2SPacket.Action.ABORT_DESTROY_BLOCK, op.hit);
        }
    }

    private void finish(Session s, String outcome, String reason, BlockState observed, long tick) {
        var op = s.op;
        if (op == null) return;
        s.op = null;
        boolean confirmed = "confirmed".equals(outcome);
        boolean failed = Set.of("failed", "timeout", "conflict", "blocked").contains(outcome);
        s.hold.outcome(op.key, failed);
        boolean checkedStance = op.method == Method.WALK && (confirmed || failed);
        boolean rejectedPlacement = op.method == Method.PLACE && failed && op.sent >= 0;
        if ((checkedStance || rejectedPlacement) && op.intent.has("stanceContext")) {
            var stances = rejectedPlacement ? s.rejectedPlacementStances : s.triedStances;
            stances.add(new StanceAttempt(op.goal.id(), text(op.intent, "stanceContext", ""),
                    position(requiredObject(op.intent, "stanceDestination"))));
        }
        if (confirmed && op.method == Method.WALK && ("alternate_checked_stance".equals(op.reason)
                || "scaffold_base_approach".equals(op.reason) || "cleanup_leave_support".equals(op.reason)
                || "temporary_opening_approach".equals(op.reason) || "return_to_exterior_staging".equals(op.reason)
                || "painting_stance".equals(op.reason) || op.reason.startsWith("temporary_platform_"))) {
            if (s.walkedContexts.add(op.key) && Set.of("alternate_checked_stance", "cleanup_leave_support").contains(op.reason)) {
                var phase = new StancePhase(op.goal.id(), text(op.intent, "stanceContext", ""));
                s.walkedAlternatives.merge(phase, 1, Integer::sum);
            }
        }
        if (confirmed && op.sent >= 0) {
            switch (op.method) {
                case PLACE, BREAK -> s.run.physicalMutations(s.run.physicalMutations() + 1);
                default -> { }
            }
        }
        if (confirmed && op.goal.matches(observed) && (op.method == Method.PLACE || op.method == Method.BREAK)) {
            if (s.run.state() == RunState.PREPARING && s.preparation != null && s.prepCursor < s.preparation.size()
                    && op.goal.id().equals(s.preparation.get(s.prepCursor).id())) {
                confirmPreparation(s, op.goal);
                s.waitUntil = tick + PREPARATION_DELAY;
            }
            var equip = s.debt.get(op.goal.pos());
            if (equip != null && !equip.opening() && equip.installed().matches(observed)) s.placedEquipment.add(op.goal.pos());
            if (op.method == Method.BREAK && equip != null && "minecraft:scaffolding".equals(equip.installed().block())) {
                // Grounded scaffolds propagate a vanilla neighbour update up the column.
                s.waitUntil = Math.max(s.waitUntil, tick + 8);
            }
        }
        var attempt = op.intent.deepCopy();
        attempt.addProperty("type", "attempt");
        attempt.addProperty("runId", s.run.runId());
        attempt.addProperty("projectId", s.run.projectId());
        attempt.addProperty("worldUid", s.run.worldUid());
        attempt.addProperty("schemaRevision", s.run.schemaRevision());
        attempt.addProperty("outcome", outcome);
        attempt.addProperty("reason", reason);
        attempt.addProperty("strategy", op.reason);
        attempt.addProperty("expectedEffect", op.method == Method.BREAK ? AIR : op.goal.commandState());
        attempt.addProperty("observedEffect", observed == null ? "unknown" : observed.toString());
        attempt.add("expectedState", op.method == Method.BREAK ? new JsonObject() : V5Runtime.gson().toJsonTree(op.goal.state()));
        attempt.add("observedState", propertyJson(observed));
        attempt.add("startPosition", point(op.from));
        attempt.addProperty("attemptNumber", s.hold.failures(op.key) + (failed ? 0 : 1));
        attempt.addProperty("endedAtTick", tick);
        attempt.addProperty("sentAtTick", op.sent);
        attempt.add("plannedRoute", points(op.route));
        attempt.add("actualRoute", points(op.actual));
        attempt.addProperty("routeStatus", confirmed ? "working" : failed ? "blocked" : "interrupted");
        attempt.add("supports", op.supports);
        var conditions = new JsonArray(); conditions.add(op.reason); conditions.add(op.key.context());
        attempt.add("accessConditions", conditions);
        var cleanupIds = new JsonArray();
        if (s.debt.containsKey(op.goal.pos())) cleanupIds.add("debt@" + coords(op.goal.pos()));
        attempt.add("cleanupIds", cleanupIds);
        attempt.addProperty("expertIncidentId", s.patch == null ? null : s.patch.incident);
        attempt.addProperty("expertProposalApplied", s.patch != null && confirmed);
        s.journal.add(attempt);
        if (op.method == Method.WALK && !op.actual.isEmpty()) {
            var route = new JsonObject();
            for (String field : List.of("id", "runId", "projectId", "worldUid", "goal", "supports")) route.add(field, attempt.get(field));
            route.addProperty("id", attempt.get("id").getAsString() + "-route");
            route.addProperty("type", "route"); route.add("cells", attempt.get("actualRoute"));
            route.addProperty("status", confirmed ? "working" : failed ? "blocked" : "interrupted");
            route.addProperty("observedAtTick", tick); route.addProperty("revision", op.key.context()); route.addProperty("note", reason);
            s.journal.add(route);
        }
        s.run.waitingReason(reason);
        LOG.info("MAGE_ATTEMPT run={} generation={} tick={} method={} at={} outcome={} reason={}", s.run.runId(),
                op.generation, tick, op.method.wire, coords(op.goal.pos()), outcome, reason);
        if (s.patch != null && confirmed && op.sent >= 0 && !s.patch.steps.isEmpty()) {
            var step = s.patch.steps.getFirst();
            if (step.scaffold() == null && op.goal.pos().equals(step.goal().pos()) && op.method == step.force()) {
                s.patch.executed.add(step.id());
                if (op.goal.pos().equals(s.patch.expected.pos()) && s.patch.expected.matches(observed)
                        && Set.of(Method.BREAK, Method.PLACE).contains(op.method)) {
                    s.patch.effectObservedByProduct = true;
                }
            }
        }
        if (s.patch != null && failed) {
            if (s.patch.scaffoldStance != null && !s.patch.steps.isEmpty() && s.patch.steps.getFirst().scaffold() != null) {
                skipScaffold(s, s.patch, s.patch.scaffoldStance, reason, tick);
            } else rejectPatch(s, reason, tick);
        }
        save(s);
    }

    private void failGoal(Session s, Goal goal, String reason) {
        if (s.patch != null) { rejectPatch(s, reason, V5Runtime.get().currentTick()); return; }
        if ("temporary_opening".equals(goal.kind())) {
            V5Runtime.get().waitForRecovery(s.run, "recovery_waiting:opening_cut_unavailable:" + coords(goal.pos()));
            return;
        }
        long tick = V5Runtime.get().currentTick();
        if ("physical_variants_exhausted".equals(reason) && s.run.state() == RunState.BUILDING
                && recoverAccess(s, goal, tick)) return;
        if (expert(s, goal, "access_lost", reason, tick)) return;
        if (s.run.continuousRecovery()) {
            if (replan(s, goal, reason)) return;
            V5Runtime.get().waitForRecovery(s.run, "recovery_waiting:" + reason + ":" + coords(goal.pos()));
            return;
        }
        if (s.run.state() == RunState.PREPARING) s.preparationState = "BLOCKED";
        terminal(s, RunState.BLOCKED, reason + ":" + goal.kind() + ":" + coords(goal.pos()));
    }

    private static boolean expertAvailable(Session s) {
        return s.options.expertBudget() > 0 && (s.run.continuousRecovery() || s.expertUsed < s.options.expertBudget());
    }

    private static boolean replanAvailable(Session s, Goal goal) {
        return s.run.continuousRecovery() && !"cleanup".equals(goal.kind()) && s.debt.values().stream().noneMatch(Debt::opening)
                && s.run.description() != null && !s.run.description().isBlank();
    }

    private boolean replan(Session s, Goal goal, String reason) {
        if (!replanAvailable(s, goal) || !s.replans.add(coords(goal.pos()) + ':' + context(goal))) return false;
        var recovery = new JsonObject();
        recovery.add("center", point(goal.pos().subtract(s.plan.anchor())));
        recovery.addProperty("radius", 8);
        recovery.addProperty("reason", reason);
        recovery.add("target", wireGoal(goal));
        recovery.addProperty("coordinateSystem", "center is relative to the blueprint origin; target and attempts are world coordinates");
        recovery.add("attempts", s.journal.relevant(goal.pos(), s.run.worldUid(), s.plan.revision()));
        var nearby = new JsonArray();
        s.confirmedHouse.stream().filter(p -> manhattan(p, goal.pos()) <= 8).sorted(ORDER)
                .map(s.plan.occupied()::get).filter(Objects::nonNull).forEach(g -> nearby.add(wireGoal(g)));
        recovery.add("confirmedNearby", nearby);
        return V5Runtime.get().requestReplan(s.run, recovery);
    }

    private boolean expert(Session s, Goal target, String kind, String why, long tick) {
        if (s.patch != null || s.incident != null || s.options.profile() == null || s.options.profile().isBlank()
                || !expertAvailable(s)) return false;
        String key = kind + '@' + coords(target.pos()) + ':' + context(target)
                + ':' + s.plan.revision();
        if (!s.incidents.add(key)) return false;
        requestExpert(s, target, kind, why, key, tick, List.of());
        return true;
    }

    private void requestExpert(Session s, Goal target, String kind, String why, String key, long tick, List<BlockPos> requested) {
        var positions = new TreeSet<BlockPos>(ORDER);
        var center = target.pos();
        for (int y = -2; y <= 2; y++) for (int x = -4; x <= 4; x++) for (int z = -4; z <= 4; z++) {
            var p = center.add(x, y, z);
            if (world.inWorld(p)) positions.add(p);
        }
        positions.addAll(requested);
        var past = s.journal.relevant(center, s.run.worldUid(), s.plan.revision());
        for (var element : past) {
            var row = asObject(element);
            for (String name : List.of("actualRoute", "plannedRoute", "supports")) {
                for (var p : array(row, name)) {
                    var at = position(asObject(p));
                    if (manhattan(at, center) <= 16) {
                        for (var around : List.of(at, at.up(), at.down())) if (world.inWorld(around)) positions.add(around);
                    }
                }
            }
        }
        var scaffoldAccess = canScaffold(s) ? reachableScaffolds(s, target, 3) : List.<ScaffoldColumn>of();
        for (var column : scaffoldAccess) {
            for (var p = column.base().down(); p.getY() <= column.stance().getY() + 1; p = p.up()) positions.add(p);
        }
        var snapshot = snapshot(List.copyOf(positions));
        releaseRoot(s);
        var request = new JsonObject();
        String id = "incident-" + UUID.randomUUID();
        request.addProperty("requestId", "expert-" + UUID.randomUUID());
        request.addProperty("runId", s.run.runId());
        request.addProperty("generation", s.run.generation());
        request.addProperty("schemaRevision", s.run.schemaRevision());
        request.addProperty("permitGeneration", s.lease);
        request.addProperty("worldSnapshotRevision", snapshot.revision());
        var incident = new JsonObject();
        incident.addProperty("id", id);
        incident.addProperty("kind", kind);
        var goal = goalJson(target);
        goal.add("cell", point(target.pos()));
        incident.add("goal", goal);
        var symptoms = new JsonArray();
        symptoms.add(why);
        incident.add("symptoms", symptoms);
        incident.addProperty("attemptsBudgetUsed", s.expertUsed++);
        if (!s.run.continuousRecovery()) incident.addProperty("attemptsBudgetTotal", s.options.expertBudget());
        request.add("incident", incident);
        var snapshotJson = new JsonObject();
        snapshotJson.add("anchor", point(s.plan.anchor()));
        snapshotJson.add("cells", snapshot.known());
        snapshotJson.add("known", snapshot.unknown());
        snapshotJson.add("playerPosition", point(world.feet()));
        snapshotJson.addProperty("revision", snapshot.revision());
        request.add("worldSnapshot", snapshotJson);
        var owner = new JsonObject();
        owner.addProperty("inputOwner", "none");
        owner.addProperty("controlHeld", false);
        var actions = new JsonArray();
        actions.add("observe"); actions.add("move");
        if (s.options.breakBlocks()) actions.add("break_block");
        if (s.options.place() && s.options.useItems()) actions.add("place_block");
        if (!scaffoldAccess.isEmpty()) actions.add("scaffold_access");
        owner.add("availableActions", actions);
        owner.addProperty("commandReserveAllowed", false);
        request.add("ownership", owner);
        var ledger = new JsonObject(); ledger.add("records", past); request.add("attemptLedger", ledger);
        var planInfo = new JsonObject();
        var steps = new JsonArray();
        var nearby = new LinkedHashSet<Goal>();
        nearby.add(target);
        s.plan.goals().stream().filter(g -> manhattan(center, g.pos()) <= 4).limit(48).forEach(nearby::add);
        var visibleIds = nearby.stream().map(Goal::id).collect(java.util.stream.Collectors.toSet());
        for (var g : nearby) {
            var row = new JsonObject(); row.addProperty("id", g.id()); row.addProperty("kind", g.kind());
            row.add("cells", points(List.of(g.pos())));
            var deps = new JsonArray(); g.dependencies().stream().filter(visibleIds::contains).forEach(deps::add);
            row.add("dependsOn", deps); row.add("cleanupIds", new JsonArray()); steps.add(row);
        }
        planInfo.add("steps", steps); planInfo.addProperty("currentStepId", target.id()); request.add("plan", planInfo);
        var options = new JsonObject(); options.addProperty("allowCommandReserve", false); options.addProperty("deadlineMillis", s.options.deadline());
        options.addProperty("continuousRecovery", s.run.continuousRecovery());
        options.addProperty("allowReplan", replanAvailable(s, target));
        request.add("options", options);
        var inventory = new JsonObject();
        var item = target.vanilla().getBlock().asItem();
        inventory.addProperty("targetItem", Registries.ITEM.getId(item).toString()); inventory.addProperty("count", item == Items.AIR ? 0 : world.count(item));
        inventory.addProperty("canSupply", canGive(s));
        inventory.add("temporarySupports", points(new ArrayList<>(s.debt.keySet())));
        var scaffoldOptions = new JsonArray();
        for (var column : scaffoldAccess) {
            var row = new JsonObject();
            row.add("base", point(column.base()));
            row.add("stance", point(column.stance()));
            row.addProperty("blocksNeeded", column.missing());
            scaffoldOptions.add(row);
        }
        inventory.add("scaffoldAccess", scaffoldOptions);
        request.add("resources", inventory);
        var process = new ExpertProcess(s.options.profile(), request, s.options.deadline(),
                () -> s.journal.olderEvidence(center, s.run.worldUid(), request.get("schemaRevision").getAsInt(), past));
        s.incident = new Incident(id, key, target, request, snapshot, process,
                s.options.deadline() == 0 ? 0 : System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(s.options.deadline()),
                s.run.generation(), s.lease, s.boundWorld, scaffoldAccess);
        s.run.expertIncidentId(id);
        setExpert(s, "PENDING", kind, tick);
        s.run.waitingReason("expert_pending:" + kind);
        save(s);
    }

    private LocalSnapshot snapshot(List<BlockPos> positions) {
        var known = new TreeMap<BlockPos, BlockState>(ORDER);
        var unknown = new JsonArray();
        for (var p : positions) {
            var state = world.read(p);
            if (state == null) unknown.add(point(p)); else known.put(p, state);
        }
        var cells = SiteSurvey.snapshot(known);
        return new LocalSnapshot(positions, BuildJournal.digest(cells.toString() + unknown + world.feet()), cells, unknown);
    }

    private void consumeExpert(Session s, long tick) {
        var incident = s.incident;
        boolean done = incident.process().answer.isDone();
        boolean expired = incident.expires() != 0 && System.nanoTime() >= incident.expires();
        if (!done && !expired) return;
        s.incident = null;
        if (expired) {
            incident.process().cancel();
            expertDecision(s, incident.id(), "blocked", false, "expert_deadline", List.of(), tick);
            setExpert(s, "TIMEOUT", "expert_deadline", tick);
            return;
        }
        try {
            if (s.run.generation() != incident.generation() || s.lease != incident.lease() || s.boundWorld != incident.world()
                    || active != s || !s.run.state().working() || !s.run.controlHeld()
                    || !snapshot(incident.snapshot().positions()).revision().equals(incident.snapshot().revision())) {
                throw invalid("stale_world_or_permit");
            }
            var envelope = incident.process().answer.getNow(null);
            if (envelope == null || !bool(envelope, "ok", false)) {
                var code = text(object(envelope, "error"), "code", "expert_unavailable");
                if (!code.matches("[a-z0-9_]{1,64}")) code = "expert_unavailable";
                expertDecision(s, incident.id(), "blocked", false, code, List.of(), tick);
                setExpert(s, code.contains("timeout") ? "TIMEOUT" : "REJECTED", code, tick);
                return;
            }
            var proposal = requiredObject(envelope, "proposal");
            if (!ExpertProcess.sameToken(incident.request(), proposal)) throw invalid("stale_proposal_token");
            switch (text(proposal, "outcome", "")) {
                case "repair" -> {
                    var repair = requiredObject(proposal, "repair");
                    if (s.run.continuousRecovery() && !s.proposedRepairs.add(context(incident.target()) + ':' + BuildJournal.digest(repair.toString()))) {
                        throw invalid("repair_already_tried");
                    }
                    s.patch = validatePatch(s, incident, repair);
                    expertDecision(s, incident.id(), "repair", true, "accepted_pending_observation", List.of(), tick);
                    setExpert(s, "PENDING", "patch_accepted", tick);
                }
                case "request_observation" -> {
                    var more = array(requiredObject(proposal, "observationRequest"), "cells");
                    if (more.isEmpty() || more.size() > 32) throw invalid("invalid_observation_request");
                    var requested = new ArrayList<BlockPos>();
                    for (var p : more) {
                        var at = position(asObject(p));
                        if (manhattan(at, incident.target().pos()) > 16 || !world.inWorld(at)) throw invalid("observation_out_of_scope");
                        requested.add(at);
                    }
                    expertDecision(s, incident.id(), "request_observation", true, "local_observation_requested", List.of(), tick);
                    if (expertAvailable(s)
                            && requested.stream().anyMatch(p -> !incident.snapshot().positions().contains(p) && world.read(p) != null)) {
                        var info = requiredObject(incident.request(), "incident");
                        requestExpert(s, incident.target(), text(info, "kind", "access_lost"), "additional_observation", incident.key(), tick, requested);
                    } else setExpert(s, "BLOCKED", "observation_no_new_information", tick);
                }
                case "blocked" -> {
                    expertDecision(s, incident.id(), "blocked", true, "awaiting_physical_recovery", List.of(), tick);
                    setExpert(s, "BLOCKED", "model_found_no_physical_repair", tick);
                }
                case "request_replan" -> {
                    var reason = text(proposal, "reason", "local_geometry");
                    var handedOff = replan(s, incident.target(), reason);
                    expertDecision(s, incident.id(), "request_replan", handedOff,
                            handedOff ? reason : "replan_already_tried_or_cleanup_pending", List.of(), tick);
                    setExpert(s, handedOff ? "PENDING" : "REJECTED", "design_handoff", tick);
                }
                default -> throw invalid("unknown_expert_outcome");
            }
        } catch (RuntimeException rejected) {
            String code = rejected instanceof IllegalArgumentException && rejected.getMessage() != null
                    && rejected.getMessage().matches("[a-z_]+") ? rejected.getMessage() : "invalid_or_unavailable_proposal";
            expertDecision(s, incident.id(), "repair", false, code, List.of(), tick);
            setExpert(s, "REJECTED", code, tick);
        } finally {
            save(s);
        }
    }

    private Patch validatePatch(Session s, Incident incident, JsonObject repair) {
        if (!array(repair, "removedStepIds").isEmpty()) throw invalid("cannot_remove_blueprint_steps");
        var steps = array(repair, "steps");
        if (steps.isEmpty() || steps.size() > 32) throw invalid("invalid_patch_size");
        var target = incident.target();
        var ids = new HashSet<String>();
        var result = new ArrayList<PatchStep>();
        var planned = new HashMap<BlockPos, Goal>();
        var openings = new HashMap<BlockPos, Goal>();
        var cleanupIds = new HashSet<>(strings(repair, "cleanupIds"));
        var usedCleanup = new HashSet<String>();
        var planRoute = readPoints(repair, "route");
        boolean moved = false;
        String scaffoldStepId = null;
        var prerequisites = new HashMap<String, Set<String>>();
        for (var element : steps) {
            var step = asObject(element);
            String id = text(step, "id", "");
            if (id.isBlank() || id.length() > 128 || s.goals.containsKey(id) || !ids.add(id)) throw invalid("invalid_patch_id");
            var deps = new LinkedHashSet<>(strings(step, "dependsOn"));
            if (deps.contains(id) || deps.contains(target.id()) || deps.stream().anyMatch(dep -> !ids.contains(dep)
                    && !s.goals.containsKey(dep))) throw invalid("invalid_patch_dependency");
            var inherited = new HashSet<>(deps);
            deps.forEach(dep -> inherited.addAll(prerequisites.getOrDefault(dep, Set.of())));
            prerequisites.put(id, inherited);
            var cells = array(step, "cells");
            if (cells.isEmpty() || cells.size() > 64) throw invalid("invalid_patch_cells");
            var at = position(asObject(cells.get(0)));
            for (var point : cells) {
                var p = position(asObject(point));
                if (manhattan(target.pos(), p) > 16 || world.read(p) == null || !incident.snapshot().positions().contains(p)) throw invalid("unknown_patch_cell");
            }
            String kind = text(step, "kind", "");
            ScaffoldColumn scaffold = null;
            Method method = switch (kind) {
                case "break_block" -> Method.BREAK;
                case "place_block" -> Method.PLACE;
                case "move", "scaffold_access" -> Method.WALK;
                case "observe" -> Method.OBSERVE;
                default -> throw invalid("unsupported_patch_action");
            };
            List<BlockPos> route = new ArrayList<>();
            if (method == Method.WALK || method == Method.OBSERVE) {
                cells.forEach(p -> route.add(position(asObject(p))));
            } else if (cells.size() != 1) throw invalid("invalid_patch_cells");
            var before = planned.getOrDefault(at, fromState(at, world.read(at), "before"));
            Goal goal;
            boolean opening = false;
            var stepCleanup = new HashSet<>(strings(step, "cleanupIds"));
            if (!cleanupIds.containsAll(stepCleanup)) throw invalid("unknown_cleanup_id");
            usedCleanup.addAll(stepCleanup);
            if ("scaffold_access".equals(kind)) {
                if (scaffoldStepId != null || !planRoute.isEmpty() || cells.size() != 1 || !stepCleanup.isEmpty()
                        || asObject(cells.get(0)).has("block") || asObject(cells.get(0)).has("state")
                        || !canScaffold(s) || planned.containsKey(target.pos())) throw invalid("invalid_scaffold_access");
                scaffold = incident.scaffoldAccess().stream().filter(c -> c.stance().equals(at)).findFirst()
                        .orElseThrow(() -> invalid("unknown_scaffold_access"));
                scaffoldStepId = id;
                goal = simple(at, AIR, "expert_scaffold_access");
                result.add(new PatchStep(id, goal, before, method, List.of(), Set.copyOf(deps), false, scaffold));
                continue;
            }
            if (scaffoldStepId != null && target.pos().equals(at) && (method == Method.BREAK || method == Method.PLACE)
                    && !inherited.contains(scaffoldStepId)) throw invalid("scaffold_access_dependency_missing");
            switch (method) {
                case BREAK -> {
                    if (!s.options.breakBlocks() || before.isAir()) throw invalid("break_precondition_unmet");
                    if (asObject(cells.get(0)).has("block")) {
                        var evidence = readGoal(id, at, asObject(cells.get(0)), "expert", List.of());
                        if (!evidence.matches(before.vanilla())) throw invalid("break_precondition_unmet");
                    }
                    if (!target.pos().equals(at)) {
                        if (stepCleanup.isEmpty() || before.vanilla().hasBlockEntity() || s.debt.containsKey(at)
                                || openings.putIfAbsent(at, before) != null) throw invalid("opening_has_no_safe_restore");
                        opening = true;
                    } else if (target.matches(before.vanilla())) throw invalid("cannot_clear_already_satisfied_goal");
                    goal = simple(at, AIR, "expert");
                    planned.put(at, goal);
                }
                case PLACE -> {
                    if (!s.options.place() || !s.options.useItems() || !before.isAir()) throw invalid("placement_precondition_unmet");
                    goal = readGoal(id, at, asObject(cells.get(0)), "expert", List.of());
                    if (target.pos().equals(at)) {
                        if (!sameRequirements(goal, target)) throw invalid("patch_changes_blueprint");
                    } else {
                        var prior = openings.remove(at);
                        if (prior == null || stepCleanup.isEmpty() || !sameRequirements(goal, prior)) throw invalid("opening_not_exactly_restored");
                    }
                    planned.put(at, goal);
                }
                case WALK -> {
                    if (moved || !planRoute.equals(route) || !flatRoute(route)) throw invalid("invalid_patch_route");
                    moved = true;
                    goal = simple(route.getLast(), AIR, "expert_move");
                }
                default -> goal = simple(at, AIR, "expert_observation");
            }
            result.add(new PatchStep(id, goal, before, method, List.copyOf(route), Set.copyOf(deps), opening, null));
        }
        if (!openings.isEmpty() || !cleanupIds.equals(usedCleanup) || (!planRoute.isEmpty() && !moved)) throw invalid("unfulfilled_patch_obligation");
        var expects = requiredObject(repair, "expects");
        var at = position(requiredObject(expects, "at"));
        var expected = readGoal("expert-proof", at, expects, "expert", List.of());
        var overlay = planned.get(at);
        if (!target.pos().equals(at) || overlay == null || !sameRequirements(overlay, expected)
                || expected.matches(world.read(at)) || (!sameRequirements(target, expected) && !expected.isAir())) throw invalid("patch_lacks_new_goal_effect");
        return new Patch(incident.id(), expected, result, incident.scaffoldAccess());
    }

    private boolean flatRoute(List<BlockPos> route) {
        if (route.isEmpty() || new HashSet<>(route).size() != route.size()) return false;
        BlockPos previous = null;
        for (var p : route) {
            if (!world.stand(p, false) || (previous != null && (previous.getY() != p.getY() || manhattan(previous, p) != 1))) return false;
            previous = p;
        }
        return true;
    }

    private void advancePatch(Session s, Root root, long tick) {
        var patch = s.patch;
        if (patch.steps.isEmpty()) {
            if (patch.effectObservedByProduct && patch.expected.matches(world.read(patch.expected.pos()))) {
                expertDecision(s, patch.incident, "repair", true, "observed_applied_effect", List.copyOf(patch.executed), tick);
                setExpert(s, "APPLIED", "observed_applied_effect", tick);
                s.patch = null;
                save(s);
            } else rejectPatch(s, "expert_effect_not_observed", tick);
            return;
        }
        var step = patch.steps.getFirst();
        for (String dep : step.dependencies()) {
            var goal = s.goals.get(dep);
            if (!patch.confirmed.contains(dep) && (goal == null || !goal.matches(world.read(goal.pos())))) {
                rejectPatch(s, "expert_dependency_changed", tick);
                return;
            }
        }
        if (step.scaffold() != null) {
            var columns = new LinkedHashSet<ScaffoldColumn>();
            if (patch.scaffoldStance != null) patch.scaffoldAlternatives.stream()
                    .filter(option -> option.stance().equals(patch.scaffoldStance)).findFirst().ifPresent(columns::add);
            columns.add(step.scaffold());
            columns.addAll(patch.scaffoldAlternatives);
            for (var option : columns) {
                if (patch.rejectedColumns.contains(option.stance())) continue;
                var column = scaffoldColumn(s, option.top());
                if (column != null && column.base().equals(option.base())) {
                    patch.scaffoldStance = column.stance();
                    if (column.missing() == 0 && world.centered(column.stance(), .4)) {
                        patch.confirmed.add(step.id());
                        patch.executed.add(step.id());
                        patch.steps.removeFirst();
                        return;
                    }
                    if ((column.missing() == 0 || canScaffold(s))
                            && useScaffold(s, column, tick, "expert_scaffold_access:" + step.id())) return;
                }
                skipScaffold(s, patch, option.stance(), "scaffold_access_unavailable", tick);
            }
            rejectPatch(s, "expert_scaffold_alternatives_exhausted", tick);
            return;
        }
        if (step.force() == Method.OBSERVE || (step.force() == Method.WALK && world.centered(step.goal().pos(), .4))
                || (step.force() != Method.WALK && step.goal().matches(world.read(step.goal().pos())))) {
            if (step.force() == Method.OBSERVE && step.route().stream().anyMatch(p -> world.read(p) == null)) {
                rejectPatch(s, "expert_observation_unknown", tick);
                return;
            }
            patch.confirmed.add(step.id());
            patch.steps.removeFirst();
            return;
        }
        if (step.force() == Method.WALK) {
            var route = step.route();
            if (!flatRoute(route) || manhattan(world.feet(), route.getFirst()) > 1 || world.feet().getY() != route.getFirst().getY()) {
                rejectPatch(s, "expert_route_precondition_changed", tick);
                return;
            }
            begin(s, step.goal(), Method.WALK, context(step.goal()), tick, route, null, route.getLast(), "expert_route:" + step.id(), 0);
            return;
        }
        if (!step.before().matches(world.read(step.goal().pos()))) { rejectPatch(s, "expert_step_precondition_changed", tick); return; }
        if (step.needsCleanup()) {
            var debt = new Debt(step.before(), simple(step.goal().pos(), AIR, "temporary_opening"), true);
            if (s.debt.putIfAbsent(step.goal().pos(), debt) == null) recordObligation(s, debt, false);
        }
        choose(s, root, step.goal(), tick, step.force());
    }

    private void rejectPatch(Session s, String reason, long tick) {
        var patch = s.patch;
        s.patch = null;
        if (patch != null) expertDecision(s, patch.incident, "repair", false, reason, patch.confirmed, tick);
        setExpert(s, "REJECTED", reason, tick);
        save(s);
    }

    private void skipScaffold(Session s, Patch patch, BlockPos stance, String reason, long tick) {
        patch.rejectedColumns.add(stance);
        patch.scaffoldStance = null;
        expertDecision(s, patch.incident, "scaffold_alternative", false, reason + ':' + coords(stance), List.of(), tick);
        LOG.info("MAGE_SCAFFOLD_REJECTED run={} stance={} reason={}", s.run.runId(), coords(stance), reason);
    }

    private void setExpert(Session s, String state, String reason, long tick) {
        s.run.expertState(state);
        LOG.info("MAGE_EXPERT run={} generation={} tick={} incident={} state={} reason={}", s.run.runId(),
                s.run.generation(), tick, s.run.expertIncidentId(), state, reason);
    }

    private void expertDecision(Session s, String id, String outcome, boolean accepted, String reason, List<String> applied, long tick) {
        var decision = new JsonObject();
        decision.addProperty("id", UUID.randomUUID().toString()); decision.addProperty("type", "expert_decision");
        decision.addProperty("runId", s.run.runId()); decision.addProperty("projectId", s.run.projectId());
        decision.addProperty("incidentId", id); decision.addProperty("outcome", outcome); decision.addProperty("accepted", accepted);
        decision.addProperty("rejectionReason", accepted ? null : reason);
        decision.add("appliedSteps", V5Runtime.gson().toJsonTree(applied));
        decision.addProperty("source", "local_attempt_ledger");
        decision.addProperty("observedAtTick", tick); decision.addProperty("detail", reason);
        s.journal.add(decision);
    }

    private void cancelExpert(Session s, String reason, long tick) {
        if (s.incident != null) {
            var incident = s.incident;
            s.incident = null;
            incident.process().cancel();
            expertDecision(s, incident.id(), "repair", false, "stale_generation:" + reason, List.of(), tick);
            setExpert(s, "REJECTED", "stale_generation:" + reason, tick);
        }
        if (s.patch != null) rejectPatch(s, "stale_generation:" + reason, tick);
    }

    private void unknown(Session s, BlockPos p, long tick) {
        if (s.missingSince < 0) s.missingSince = tick;
        s.run.waitingReason("observation_unavailable:" + coords(p));
        if (tick - s.missingSince >= 120) {
            if (s.run.continuousRecovery()) {
                s.waitingChunk = p;
                V5Runtime.get().waitForRecovery(s.run, "unloaded_chunk:" + coords(p));
            } else terminal(s, RunState.BLOCKED, "unloaded_chunk:" + coords(p));
        }
    }

    private String reportedOwner() {
        var mod = bridge.mod();
        if (mod != null && mod.getTaskRunner() != null) {
            var chain = mod.getTaskRunner().getCurrentTaskChain();
            if (chain != null && chain != mod.getUserTaskChain()) return "unionclef";
        }
        return bridge.currentTask() == null ? "none" : "foreign";
    }

    private void owner(Session s, String owner, boolean released) {
        s.run.inputOwner(owner);
        s.run.controlReleased(released);
    }

    private void pause(Session s, String reason, long tick) {
        s.run.controlHeld(false);
        s.run.nextGeneration();
        s.run.releaseRequestedTick(tick);
        s.run.releaseReason(reason);
        phase(s, RunState.PAUSED);
        s.run.waitingReason(reason);
        releaseControl(s.run, reason);
        s.run.releasedTick(tick);
        s.run.releaseLatencyTicks(0);
    }

    private static void phase(Session s, RunState phase) {
        s.run.state(phase);
        s.run.phase(phase.name());
        s.run.waitingReason("");
    }

    private void terminal(Session s, RunState state, String reason) {
        if (state == RunState.BLOCKED && s.run.continuousRecovery() && !reason.startsWith("journal_")) {
            V5Runtime.get().waitForRecovery(s.run, "recovery_waiting:" + reason);
            return;
        }
        phase(s, state);
        // Runtime also owns the public permit, but detach now: it may have finished its tick.
        s.run.controlHeld(false);
        long tick = V5Runtime.get().currentTick();
        var root = s.root;
        if (root != null && s.op != null && s.op.breaking && bridge.currentTask() == root) abortBreak(s, s.op);
        releaseRoot(s);
        if (s.op != null) interruptOperation(s, root, reason, tick, false);
        cancelExpert(s, reason, tick);
        s.run.waitingReason(reason);
        LOG.info("MAGE_TERMINAL run={} tick={} state={} reason={}", s.run.runId(), tick, state, reason);
        if (state != RunState.READY) save(s);
    }

    private void releaseRoot(Session s) {
        var previous = s.root;
        if (previous == null) return;
        s.root = null;
        s.lease++;
        previous.clearInputs();
        world.stopOwned(previous, bridge);
        if (bridge.currentTask() == previous) throw new IllegalStateException("own UnionClef root not released");
        owner(s, reportedOwner(), true);
    }

    private void interruptOperation(Session s, Root root, String reason, long tick, boolean displaced) {
        var op = s.op;
        if (op == null) return;
        if (op.breaking && (root == null || bridge.currentTask() == root)) abortBreak(s, op);
        if (op.sent >= 0) s.waitUntil = tick + 12; // Earlier packets may still take effect; re-observe, never replay give.
        if (op.sent >= 0 && op.method == Method.WALK && client.player != null && client.world == s.boundWorld) appendPosition(op);
        finish(s, displaced ? "interrupted" : "cancelled", reason,
                client.world == s.boundWorld ? world.read(op.goal.pos()) : null, tick);
    }

    @Override public void releaseControl(RunRecord run, String reason) {
        var s = sessions.get(run.runId());
        if (s == null) return;
        s.readyCheckpointQueued = false;
        long tick = V5Runtime.get().currentTick();
        var root = s.root;
        // Hard revoke the local lease first. Actual release never depends on task.isFinished or persistence.
        s.lease++;
        if (root != null && s.op != null && s.op.breaking && bridge.currentTask() == root) abortBreak(s, s.op);
        releaseRoot(s);
        owner(s, reportedOwner(), true);
        LOG.info("MAGE_RELEASE run={} generation={} tick={} reason={} rootReleased=true", run.runId(), run.generation(), tick, reason);
        if (run.state() == RunState.PAUSED && "RUNNING".equals(s.preparationState)) s.preparationState = "PAUSED";
        interruptOperation(s, root, reason, tick, false);
        cancelExpert(s, reason, tick);
        save(s);
    }

    @Override public void onStop(RunRecord run, String reason) {
        var s = sessions.get(run.runId());
        if (s != null) save(s);
    }

    @Override public void onResume(RunRecord run) {
        var s = sessions.get(run.runId());
        if (s == null) return;
        active = s;
        s.boundWorld = client.world;
        s.resurvey = true;
        s.surveySaved = false;
        s.missingSince = -1;
        s.waitForOwner = -1;
        s.waitingChunk = null;
        s.interrupted = "";
        if (run.releaseReason().startsWith("recovery_waiting:")) {
            s.exteriorAttempts.clear();
            s.incidents.removeIf(incident -> incident.startsWith("access_lost@"));
            s.proposedRepairs.clear();
            s.triedStances.clear();
            s.rejectedPlacementStances.clear();
        }
        s.journalPendingSince = -1;
        s.waitUntil = V5Runtime.get().currentTick() + 12;
        save(s);
    }

    @Override public boolean canAutoResume(RunRecord run) {
        var s = sessions.get(run.runId());
        if (s == null) return false;
        var reason = run.waitingReason();
        boolean journalWait = Set.of("journal_read_pending", "journal_write_failed", "journal_commit_pending").contains(reason);
        if (journalWait && !journalAvailableForResume(s, reason)) return false;
        if (client.player == null || client.world == null || client.currentScreen != null
                || !bridge.chainAvailable() || bridge.paused() || bridge.currentTask() != null) return false;
        var runner = bridge.mod().getTaskRunner();
        if (runner.getCurrentTaskChain() == bridge.mod().getMobDefenseChain() || ownsWork(runner.getCurrentTaskChain())) return false;
        if (journalWait) return true;
        if (reason.startsWith("unloaded_chunk:")) return s.waitingChunk != null && world.read(s.waitingChunk) != null;
        return Set.of("world_disconnected", "world_changed", "mob_defense", "foreign_owner", "screen_open",
                "unionclef_preempted_expert", "unionclef_preemption_timeout", "unionclef_unavailable", "unionclef_paused").contains(reason);
    }

    private boolean journalAvailableForResume(Session s, String reason) {
        if (reason.equals("journal_read_pending")) return s.loading.isDone();
        var state = s.journal.written.state();
        if (state == Future.State.SUCCESS) return true;
        long tick = V5Runtime.get().currentTick();
        if (state != Future.State.RUNNING && tick >= s.nextJournalRetry) {
            s.nextJournalRetry = tick + JOURNAL_RETRY_TICKS;
            save(s);
        }
        return false;
    }

    @Override public void onReplan(RunRecord run, JsonObject blueprint, JsonObject recovery) {
        var s = sessions.get(run.runId());
        if (s == null || s.debt.values().stream().anyMatch(Debt::opening)) throw invalid("replan_cleanup_pending");
        var revised = BuildPlan.parse(run.context(), blueprint, client.world);
        var previous = s.plan;
        var center = previous.anchor().add(position(requiredObject(recovery, "center")));
        int radius = integer(recovery, "radius", 0);
        if (!previous.anchor().equals(revised.anchor()) || revised.revision() != previous.revision() + 1
                || radius < 1 || radius > 12 || !previous.equipment().equals(revised.equipment())
                || !previous.restoration().equals(revised.restoration())
                || !previous.paintings().equals(revised.paintings())) throw invalid("invalid_replan_scope");
        var oldAir = new HashSet<>(previous.air().stream().map(Goal::pos).toList());
        var newAir = new HashSet<>(revised.air().stream().map(Goal::pos).toList());
        var changed = new HashSet<BlockPos>();
        var positions = new HashSet<>(previous.occupied().keySet());
        positions.addAll(revised.occupied().keySet());
        positions.addAll(oldAir);
        positions.addAll(newAir);
        for (var pos : positions) {
            var a = previous.occupied().get(pos);
            var b = revised.occupied().get(pos);
            boolean same = a == b || a != null && b != null && a.block().equals(b.block()) && a.state().equals(b.state());
            if (same && oldAir.contains(pos) == newAir.contains(pos)) continue;
            if (manhattan(pos, center) > radius || a != null && b == null && !newAir.contains(pos)
                    || s.debt.keySet().stream().anyMatch(equipment -> manhattan(pos, equipment) <= 1)) {
                throw invalid("invalid_replan_scope");
            }
            changed.add(pos);
        }
        if (changed.isEmpty() || !s.acceptedDesigns.add(designFingerprint(blueprint))) throw invalid("replan_no_change_or_cycle");
        s.plan = revised;
        s.goals.clear();
        revised.goals().forEach(goal -> s.goals.put(goal.id(), goal));
        s.confirmedHouse.removeAll(changed);
        s.confirmedAir.removeAll(changed);
        s.hold = new PlanHold();
        s.walkedAlternatives.clear();
        s.walkedContexts.clear();
        s.triedStances.clear();
        s.rejectedPlacementStances.clear();
        s.repairs = 0;
        var event = new JsonObject();
        event.addProperty("id", UUID.randomUUID().toString());
        event.addProperty("type", "design_revision");
        event.addProperty("runId", run.runId());
        event.addProperty("projectId", run.projectId());
        event.addProperty("worldUid", run.worldUid());
        event.addProperty("schemaRevision", revised.revision());
        event.addProperty("outcome", "confirmed");
        event.add("changed", points(changed.stream().sorted(ORDER).toList()));
        s.journal.add(event);
    }

    private static String designFingerprint(JsonObject blueprint) {
        return BuildJournal.digest(blueprint.get("cells").toString() + '|' + blueprint.get("requiredAir"));
    }

    private void recordObligation(Session s, Debt debt, boolean settled) {
        var goal = debt.restore();
        var record = point(goal.pos());
        record.addProperty("id", "debt@" + coords(goal.pos()));
        record.addProperty("type", debt.opening() ? "restoration_obligation" : "cleanup_obligation");
        record.addProperty("runId", s.run.runId()); record.addProperty("projectId", s.run.projectId());
        record.addProperty("block", debt.opening() ? goal.block() : debt.installed().block());
        record.add("state", V5Runtime.gson().toJsonTree(debt.opening() ? goal.state() : debt.installed().state()));
        record.addProperty("purpose", debt.opening() ? "temporary_opening" : "platform");
        if (debt.opening()) record.addProperty("reason", "restore_pre_mutation_block_state");
        record.addProperty("state_of_obligation", settled ? debt.opening() ? "restored" : "removed" : "open");
        record.addProperty(debt.opening() ? "confirmedRestoration" : "confirmedRemoval", settled);
        s.journal.add(record);
    }

    private void save(Session s) {
        save(s, s.run.state());
    }

    private void save(Session s, RunState checkpointState) {
        s.run.cleanupDebt((int) s.debt.values().stream().filter(d -> !d.opening()).count());
        s.run.restorationDebt((int) s.debt.values().stream().filter(Debt::opening).count());
        if (!s.loaded) return;
        var state = new JsonObject();
        state.addProperty("runId", s.run.runId()); state.addProperty("projectId", s.run.projectId());
        state.addProperty("worldUid", s.run.worldUid()); state.addProperty("schemaRevision", s.run.schemaRevision());
        state.addProperty("generation", s.run.generation()); state.addProperty("lastState", checkpointState.name());
        state.add("replans", V5Runtime.gson().toJsonTree(s.replans));
        state.add("acceptedDesigns", V5Runtime.gson().toJsonTree(s.acceptedDesigns));
        state.addProperty("reason", checkpointState == RunState.READY ? "" : s.run.waitingReason());
        state.add("anchor", point(s.plan.anchor()));
        if (s.exteriorStaging != null) state.add("exteriorStaging", point(s.exteriorStaging));
        if (s.openingExit != null) {
            state.add("openingExit", point(s.openingExit));
            state.add("openingCuts", points(s.openingCuts.stream().map(Goal::pos).toList()));
        }
        state.add("blueprint", s.run.blueprint());
        state.add("progress", progress(s.run));
        state.add("sitePreparation", sitePreparation(s.run));
        state.add("spentSupply", V5Runtime.gson().toJsonTree(s.spentSupply));
        state.add("coveredPreparation", V5Runtime.gson().toJsonTree(s.coveredPreparation));
        state.add("requestedPreparation", V5Runtime.gson().toJsonTree(s.requestedPreparation));
        state.addProperty("confirmedCuts", s.confirmedCuts); state.addProperty("confirmedFill", s.confirmedFill);
        state.addProperty("physical", s.run.physicalMutations()); state.addProperty("commands", s.run.commandMutations());
        state.addProperty("teleports", s.run.teleportMutations());
        var debts = new JsonArray();
        s.debt.forEach((p, debt) -> {
            var o = point(p); o.add("restore", wireGoal(debt.restore())); o.add("installed", wireGoal(debt.installed()));
            o.addProperty("opening", debt.opening()); debts.add(o);
        });
        state.add("debt", debts);
        if (s.op != null) state.add("inFlight", s.op.intent.deepCopy());
        s.journal.save(state);
    }

    private void load(Session s) {
        var snapshot = s.journal.decode(s.loading.getNow(null));
        if (snapshot.isEmpty()) return;
        if (snapshot.has("exteriorStaging") && s.plan.outside().equals(position(requiredObject(snapshot, "exteriorStaging"))))
            s.exteriorStaging = s.plan.outside();
        s.replans.addAll(strings(snapshot, "replans"));
        s.acceptedDesigns.addAll(strings(snapshot, "acceptedDesigns"));
        s.reconciliation = true;
        s.spentSupply.addAll(strings(snapshot, "spentSupply"));
        s.coveredPreparation.addAll(strings(snapshot, "coveredPreparation"));
        s.requestedPreparation.addAll(strings(snapshot, "requestedPreparation"));
        s.confirmedCuts = integer(snapshot, "confirmedCuts", 0); s.confirmedFill = integer(snapshot, "confirmedFill", 0);
        for (var entry : array(snapshot, "debt")) {
            var obj = asObject(entry);
            var p = position(obj);
            validPosition(client.world, p);
            if (!inBox(p, s.plan.min().add(-18, -18, -18), s.plan.max().add(18, 18, 18))) throw invalid("invalid persisted cleanup scope");
            s.debt.put(p, new Debt(readGoal("restore@" + coords(p), p, requiredObject(obj, "restore"), "cleanup", List.of()),
                    readGoal("installed@" + coords(p), p, requiredObject(obj, "installed"), "cleanup", List.of()), bool(obj, "opening", false)));
        }
        if (snapshot.has("openingExit") && s.debt.values().stream().anyMatch(Debt::opening)) {
            var exit = position(requiredObject(snapshot, "openingExit"));
            if (!outside(s.plan, exit) || s.debt.keySet().stream().noneMatch(p -> horizontalDistance(p, exit) == 1))
                throw invalid("invalid persisted opening exit");
            s.openingExit = exit;
            for (var cell : readPoints(snapshot, "openingCuts")) {
                var debt = s.debt.get(cell);
                if (debt == null || !debt.opening()) throw invalid("invalid persisted opening cut");
                s.openingCuts.addLast(simple(cell, AIR, "temporary_opening"));
            }
        }
        for (var row : s.journal.rows()) {
            if (!"attempt".equals(text(row, "type", ""))) continue;
            String outcome = text(row, "outcome", "");
            boolean failed = Set.of("failed", "timeout", "conflict", "blocked").contains(outcome);
            String goal = text(row, "goalId", "");
            if (failed) s.hold.outcome(new PlanHold.Key(goal, text(row, "method", ""), text(row, "context", "")), true);
            String method = text(row, "method", "");
            boolean checkedStance = Method.WALK.wire.equals(method) && (failed || "confirmed".equals(outcome));
            boolean rejectedPlacement = Method.PLACE.wire.equals(method) && failed && integer(row, "sentAtTick", -1) >= 0;
            if ((checkedStance || rejectedPlacement) && row.has("stanceContext") && row.has("stanceDestination")) {
                var stances = rejectedPlacement ? s.rejectedPlacementStances : s.triedStances;
                stances.add(new StanceAttempt(goal, text(row, "stanceContext", ""), position(requiredObject(row, "stanceDestination"))));
            }
        }
        var inFlight = object(snapshot, "inFlight");
        if (!inFlight.isEmpty()) {
            var row = inFlight.deepCopy(); row.addProperty("type", "reconciled_intent");
            row.addProperty("previousRunId", text(snapshot, "runId", "")); row.addProperty("runId", s.run.runId());
            row.addProperty("outcome", "cancelled"); row.addProperty("reason", "prior_effect_unattributed_until_world_reconciliation");
            s.journal.add(row);
        }
        // Historical totals remain in the prior snapshot/attempt records, not attributed to the new run.
        s.run.cleanupDebt((int) s.debt.values().stream().filter(d -> !d.opening()).count());
        s.run.restorationDebt((int) s.debt.values().stream().filter(Debt::opening).count());
    }

    private static JsonObject wireGoal(Goal goal) {
        var row = point(goal.pos()); row.addProperty("block", goal.block()); row.add("state", V5Runtime.gson().toJsonTree(goal.state())); return row;
    }
    private static JsonObject goalJson(Goal goal) {
        var row = point(goal.pos()); row.addProperty("expectedBlock", goal.block());
        row.add("expectedState", V5Runtime.gson().toJsonTree(goal.state())); return row;
    }
    private static JsonArray points(List<BlockPos> list) {
        var result = new JsonArray(); list.forEach(p -> result.add(point(p))); return result;
    }
    private static List<BlockPos> readPoints(JsonObject row, String key) {
        var result = new ArrayList<BlockPos>();
        for (var value : array(row, key)) result.add(position(asObject(value)));
        return result;
    }

    @Override public JsonObject sitePreparation(RunRecord run) {
        var s = sessions.get(run.runId());
        var response = new JsonObject();
        response.addProperty("state", s == null ? "NOT_STARTED" : s.preparationState);
        response.addProperty("plannedMutations", s == null ? 0 : s.requestedPreparation.size());
        response.addProperty("confirmedMutations", s == null ? 0 : s.coveredPreparation.size());
        response.addProperty("remainingMutations", s == null ? 0 : s.requestedPreparation.size() - s.coveredPreparation.size());
        response.addProperty("clearance", s == null ? 0 : s.confirmedCuts);
        response.addProperty("foundation", s == null ? 0 : s.confirmedFill);
        response.addProperty("access", s == null ? "unverified" : s.access);
        response.addProperty("snapshotRevision", s == null ? "" : s.surveyRevision);
        return response;
    }

    @Override public JsonObject progress(RunRecord run) {
        var s = sessions.get(run.runId());
        var response = new JsonObject();
        response.addProperty("targetCells", s == null ? 0 : s.plan.goals().size());
        response.addProperty("confirmedCells", s == null ? 0 : s.confirmedHouse.size());
        response.addProperty("requiredAirCells", s == null ? 0 : s.plan.air().size());
        response.addProperty("confirmedAirCells", s == null ? 0 : s.confirmedAir.size());
        return response;
    }

    @Override public JsonObject expert(RunRecord run) {
        var response = new JsonObject();
        response.addProperty("state", run.expertState());
        response.addProperty("incidentId", run.expertIncidentId().isBlank() ? null : run.expertIncidentId());
        return response;
    }
}
