package dev.magehome.client;

import adris.altoclef.tasksystem.Task;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.acceptance.PlatformFixture;
import dev.magehome.core.acceptance.StandRules;
import dev.magehome.core.automation.TaskChainOwnership;
import dev.magehome.core.automation.TaskOwnership;
import dev.magehome.core.model.Cell;
import dev.magehome.core.registry.HouseRecord;
import dev.magehome.core.registry.HouseState;
import dev.magehome.core.sites.SiteRules;
import dev.magehome.core.supply.GiveAvailability;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.template.HouseTemplate;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.item.ItemStack;
import net.minecraft.block.Blocks;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.BlockPos;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The acceptance scenarios of the mod.
 *
 * <p>A scenario is started with the system property {@code lab.acceptance.scenario}; the Gradle
 * task {@code runClient} passes the value of {@code -PlabAcceptanceScenario=<id>} to the game.
 * The scenario prints the marker {@code LAB_ACCEPTANCE_BEGIN:<id>} after the world was loaded and
 * {@code LAB_ACCEPTANCE_END:<id>} when it is finished; the independent runner looks for these
 * markers in the log of the client.
 *
 * <p>Every scenario owns named checks. A check is one test of the scenario manifest: it reads a
 * real observable of the running mod (the registry, the status of the automation, the position of
 * the player, the blocks and the items around the player) and prints its own result:
 * {@code LAB_ACCEPTANCE_TEST:<name>:PASS} or {@code LAB_ACCEPTANCE_TEST:<name>:FAIL:<detail>}.
 * The name of the check is the id of the test in the scenario manifest, so the runner can bind
 * every checkpoint to exactly one named test.
 */
public final class AcceptanceRunner {

    private static final Logger LOGGER = LoggerFactory.getLogger("mage_home/acceptance");

    /** The height of the observed column of blocks around the player. */
    private static final int OBSERVED_HEIGHT = 8;

    /** The time the scenario waits for the world of the stand before it gives up. */
    private static final long STAND_TIMEOUT_MILLIS = 120_000L;

    /** The ticks between the end of the scenario and the stop of the client. */
    private static final int STOP_DELAY_TICKS = 20;

    /** The ticks between two refreshes of the report of the scenario. */
    private static final int REPORT_INTERVAL_TICKS = 20;

    /** The ticks between two publications of the facts of the attempt into the journal. */
    private static final int JOURNAL_FACTS_INTERVAL_TICKS = 100;

    /** The time the scenario waits for the own root task in the chain of UnionClef. */
    private static final long OWN_ROOT_TIMEOUT_MILLIS = 60_000L;

    /**
     * The time the scenario waits for the task chain of UnionClef.
     *
     * <p>The chain is created while the mods are loaded; in the laboratory it can appear a few
     * ticks after the world was loaded. The scenario waits for it inside this window. When the
     * window is over, the situation of the scenario cannot be shown: the scenario is then not
     * verified (skipped) and never failed.
     */
    private static final long CHAIN_WAIT_TIMEOUT_MILLIS = 60_000L;

    /**
     * The length of the observation window of the diagnostic scenario of the stand.
     *
     * <p>The window is longer than the time between the start of the client and the crash of the
     * run of the round 6 (about nine seconds), so that a crash of the same kind falls inside it,
     * and it is short enough to keep the run of the laboratory cheap.
     */
    private static final long DIAGNOSTIC_WINDOW_MILLIS = 30_000L;

    /** The detail of a named case that was not observed yet. */
    private static final String PENDING_DETAIL = "the scenario is running";

    /**
     * The hint about the task chain of UnionClef that never appears.
     *
     * <p>The facts come from the bytecode of the delivered UnionClef 0.95.61 and from the
     * bytecode of Minecraft 1.21.11 (Yarn): the mod of UnionClef creates its task chain in
     * {@code AltoClef.onInitializeLoad()}, that method is called from the subscriber of
     * {@code TitleScreenEntryEvent}, and the event is published by the mixin of UnionClef in
     * {@code TitleScreen.init()}. The world of the stand is opened by the laboratory of the
     * platform: for the dependency {@code altoclef} {@code 1.21.11-0.95.61} its adapter removes
     * the arguments of the Quick Play of Minecraft and lets Minecraft show its normal title
     * screen, so that the dependency initializes itself there (see {@link LabStand}).
     *
     * <p>The hint names what the stand observed and what it did about it: the preparation of the
     * stand does not ask UnionClef for its initialization and does not publish the events of the
     * dependency (see {@link LabStand}); the initialization belongs to the lifecycle of the
     * dependency, which the laboratory of the platform lets run through the title screen. The
     * reason is not guessed: the chain is missing, and that is what the log says.
     */
    private static final String CHAIN_HINT = "the task chain of UnionClef does not exist: the preparation of the "
            + "stand waits for the initialization of the dependency and does not publish its events, and the "
            + "chain of the mod is still not there";

    /** The reach of the player; the interaction has to stay inside it. */
    private static final double REACH_LIMIT = 4.6;

    /**
     * The longest distance that the player may cover in one tick while he walks.
     *
     * <p>The value is a safety margin for the speed of the vanilla player (0.28 blocks per tick
     * while he sprints); the measured way and the measured steps of the run are written into the
     * detail of the check, so that the number can be read against the facts of the game.
     */
    private static final double LONGEST_STEP = 3.0;

    /** One step of a scenario. */
    private interface Step {
        String name();

        /** Returns true when the step is finished. */
        boolean run(MageHomeRuntime runtime);
    }

    /** One named check of a scenario; the name is the id of the test in the manifest. */
    private interface Check {
        String name();

        /** Reads the observable of the running mod. */
        CheckResult run(MageHomeRuntime runtime);
    }

    /** The state of one named check. */
    private enum CheckState {
        /** The behaviour was observed and it holds. */
        PASSED,
        /** The behaviour was observed and it does not hold. */
        FAILED,
        /**
         * The behaviour was not observed.
         *
         * <p>A case in this state is written as a skipped case: the stand could not show the
         * behaviour, and that is not a failure of the add-on.
         */
        SKIPPED
    }

    /** The result of one named check. */
    private record CheckResult(CheckState state, String detail) {

        static CheckResult ok(String detail) {
            return new CheckResult(CheckState.PASSED, detail);
        }

        static CheckResult failed(String detail) {
            return new CheckResult(CheckState.FAILED, detail);
        }

        /** The behaviour was not observed; the case stays unverified and is never a failure. */
        static CheckResult unverified(String detail) {
            return new CheckResult(CheckState.SKIPPED, detail);
        }

        boolean passed() {
            return state == CheckState.PASSED;
        }
    }

    /** The steps and the named checks of one scenario. */
    private static final class Scenario {
        private final List<Step> steps = new ArrayList<>();
        private final List<Check> checks = new ArrayList<>();
        /** The setup checks: they decide whether the stand can show the product at all. */
        private final List<Check> preconditions = new ArrayList<>();
    }

    private String scenarioId;
    private Scenario scenario = new Scenario();
    private int stepIndex;
    private String stopHouseId;
    private boolean stopIssued;
    private boolean interruptedAfterStop;
    private boolean resumeIssued;
    private long startedAt;
    private boolean begun;
    private boolean finished;
    /** The setup checks of the scenario were already read. */
    private boolean preconditionsChecked;
    /** The reasons of the setup checks that did not hold. */
    private List<String> preconditionFailures = List.of();
    /** The log already said that the stand waits for the world. */
    private boolean standAnnounced;
    private Map<BlockPos, BlockState> blocksBefore = new LinkedHashMap<>();
    private Map<String, Integer> itemsBefore = new LinkedHashMap<>();
    private final Set<String> observedStates = new LinkedHashSet<>();
    /** The place of the player at the beginning of the scenario. */
    private BlockPos playerStart;
    /** The results of the named cases of the scenario; they are written as a JUnit report. */
    private final Map<String, AcceptanceReport.Case> outcomes = new LinkedHashMap<>();
    /** The preparation of the stand of the scenario; it belongs to the laboratory alone. */
    private final LabStand labStand = new LabStand();
    /** The relations of the task chain of UnionClef in the order of the ticks of the observation. */
    private final List<TaskOwnership.Relation> observedRelations = new ArrayList<>();
    /**
     * The states of the task chain of UnionClef in the order of the ticks of the observation.
     *
     * <p>A state carries the relation of the chain and the state of the task that owns it, so
     * that the life of the task of another owner can be followed inside the window.
     */
    private final List<TaskChainOwnership.Sample> observedSamples = new ArrayList<>();
    /** The number of the calls of the add-on that were made before the window began. */
    private int callsBeforeWindow;
    /** The task of another owner that was seen in the window; null when none was seen. */
    private Task foreignTask;
    /** The moment the scenario was installed; the waiting for the world is bounded. */
    private long installedAt;
    /** The owner of the task chain of UnionClef that was observed after the resume. */
    private String observedRelation;
    /** The task that owned the chain of UnionClef when it was observed. */
    private String observedTask;
    /** The number of the ticks of the client since the scenario began. */
    private long clientTicks;
    /** The number of the ticks of the client since the preparation of the stand began. */
    private long standTicks;
    /**
     * The number of the records of the registry at the beginning of the diagnostic window.
     *
     * <p>The diagnostic scenario of the stand must not change the registry. The records of an
     * earlier run of the laboratory are not its own, therefore the number is read once at the
     * beginning of the window; -1 means that the window did not begin yet.
     */
    private int diagnosticHousesAtStart = -1;
    /** True while the relation of the task chain is observed after the resume. */
    private boolean chainObservation;
    /** The tick on which the chain of UnionClef held the own root task; -1 means never. */
    private long ownRootTick = -1L;
    /** The task that owned the chain on the tick of the own root task. */
    private String ownRootTask;
    /** True when the chain of UnionClef belonged to another owner during the observation. */
    private boolean foreignRelationSeen;
    /** The tick on which the chain of UnionClef belonged to another owner; -1 means never. */
    private long foreignRelationTick = -1L;
    /** The task that owned the chain of UnionClef at the tick of the foreign relation. */
    private String foreignRelationTask;
    /**
     * True when an empty chain was seen after the last foreign owner.
     *
     * <p>The chain of UnionClef changes inside its own task tick, so a foreign task that ends and
     * the own task that starts in the same tick are seen as one jump from one owner to the other
     * without an empty chain in between. The flag tells such a jump from a real change of the
     * owner.
     */
    private boolean emptyChainSinceForeign;
    /** The moment the scenario started to wait for the chain of UnionClef; -1 means never. */
    private long chainWaitStartedAt = -1L;
    /** The log already said that the scenario waits for the chain of UnionClef. */
    private boolean chainWaitAnnounced;
    /** The moment the step that waits for the own root task was entered; -1 means never. */
    private long ownRootWaitStartedAt = -1L;
    /** The length of the way that the player walked during the scenario. */
    private double walkedDistance;
    /** The longest distance that the player covered in one tick. */
    private double longestStep;
    /** The place of the player in the last tick. */
    private BlockPos lastPlace;
    /** The ticks that have to pass before the client stops; -1 means that the client stays. */
    private int stopCountdown = -1;
    private final LabScenarioFixture fixture = new LabScenarioFixture();
    private AcceptanceJournal journal;
    private boolean fixtureStarted;
    private boolean fixtureReady;
    private int activeStep = -1;
    private final Set<String> progress = new LinkedHashSet<>();
    /** The facts of the last attempt of the placement that were written into the journal. */
    private String lastJournaledFacts = "";
    /** The tick of the last publication of the facts of the attempt into the journal. */
    private long lastJournaledFactsTick;

    /** Installs the scenario of the system property; nothing happens without the property. */
    public void install(String scenarioId) {
        if (scenarioId == null || scenarioId.isBlank()) {
            return;
        }
        this.scenarioId = scenarioId.trim();
        this.scenario = scenarioFor(this.scenarioId);
        this.journal = AcceptanceJournal.configured(this.scenarioId);
        journal.event("scenario", "started", null, null,
                "client JVM phase=" + System.getProperty("lab.acceptance.fixture.phase", "single"));
        this.installedAt = System.currentTimeMillis();
        LOGGER.info("the acceptance scenario {} is installed with {} steps and {} checks",
                this.scenarioId, this.scenario.steps.size(), this.scenario.checks.size());
        // The report of the scenario is written from the very beginning: a run that dies leaves
        // the named cases of the scenario behind, so that an independent runner finds them
        // instead of an empty report. A case that was not observed is skipped, never passed.
        publishPendingCases(PENDING_DETAIL);
    }

    /**
     * Writes the report of the scenario with every named case that is not decided yet.
     *
     * <p>The report is refreshed while the scenario runs. A case that was not observed stays
     * skipped: the report of an unfinished run never claims that something passed.
     */
    private void publishPendingCases(String detail) {
        if (fixture.seedPhase()) {
            pending(scenarioId + ".seed.fixture", detail);
            pending(scenarioId + ".seed", detail);
            writeReport();
            return;
        }
        for (Check check : scenario.preconditions) {
            pending(check.name(), detail);
        }
        for (Check check : scenario.checks) {
            pending(check.name(), detail);
        }
        pending(scenarioId, detail);
        writeReport();
    }

    /**
     * Records a named case that is not decided yet.
     *
     * <p>A case that was already observed keeps its result: the report of the scenario never
     * replaces a result by a placeholder.
     */
    private void pending(String name, String detail) {
        AcceptanceReport.Case known = outcomes.get(name);
        if (known == null) {
            outcomes.put(name, new AcceptanceReport.Case(name, AcceptanceReport.State.SKIPPED, detail));
            return;
        }
        if (known.state() == AcceptanceReport.State.SKIPPED && PENDING_DETAIL.equals(known.detail())) {
            // Only a case that still waits for its observation takes a new detail. A case that
            // already carries the reason why it was not observed keeps that reason.
            outcomes.put(name, new AcceptanceReport.Case(name, AcceptanceReport.State.SKIPPED, detail));
        }
    }

    public String scenarioId() {
        return scenarioId;
    }

    /**
     * Prepares the stand of the scenario; it is called in every client tick.
     *
     * <p>The platform owns UnionClef's normal title-screen initialization. This harness only
     * observes that lifecycle and never initializes the dependency a second time.
     */
    public void prepareStand() {
        if (scenarioId == null) {
            return;
        }
        standTicks++;
        labStand.prepare(scenarioId, standTicks);
    }

    /**
     * The stand has no world yet.
     *
     * <p>The markers of the scenario are printed only after the world was loaded. The log says
     * that the scenario waits, so that an independent runner can tell a missing world from a
     * failed check.
     */
    public void standWaiting() {
        if (scenarioId == null || finished) {
            return;
        }
        if (!standAnnounced) {
            standAnnounced = true;
            LOGGER.info("LAB_ACCEPTANCE_STAND:{}: the world of the scenario is not loaded yet, the scenario waits",
                    scenarioId);
        }
        if (System.currentTimeMillis() - installedAt < STAND_TIMEOUT_MILLIS) {
            return;
        }
        // The stand did not open a world: nothing of the scenario can be observed. Every named
        // case is reported as skipped, so that the scenario is not read as a product failure.
        finished = true;
        String reason = "the world of the stand was not loaded within " + STAND_TIMEOUT_MILLIS + " ms";
        publishPendingCases(reason);
        journal.event("setup", "failed", scenarioId + ".world", "SKIPPED", reason);
        journal.event("scenario", "failed", scenarioId, "SKIPPED", reason);
        LOGGER.info("LAB_ACCEPTANCE_TEST:{}:SKIPPED:{}", scenarioId, reason);
        // The scenario never began: the markers of a demonstration are not printed.
        MinecraftClient.getInstance().stop();
    }

    /** One tick of the scenario. */
    public void tick(MageHomeRuntime runtime) {
        if (scenarioId == null) {
            return;
        }
        if (stopCountdown >= 0) {
            // The result of the scenario stays visible for a second before the client stops.
            stopCountdown--;
            if (stopCountdown == 0) {
                LOGGER.info("LAB_ACCEPTANCE_TEST:{}: the scenario is finished, the client stops", scenarioId);
                MinecraftClient.getInstance().stop();
            }
            return;
        }
        if (finished) {
            return;
        }
        observePlayerPath();
        clientTicks++;
        if (!fixtureReady) {
            String name = scenarioId + (fixture.seedPhase() ? ".seed.fixture" : ".fixture");
            if (!fixtureStarted) {
                fixtureStarted = true;
                pending(name, "fixture setup in progress");
                writeReport();
                journal.event("setup", "started", name, null, "prepare real integrated-server world and registry");
            }
            try {
                if (!fixture.prepare(scenarioId, runtime)) return;
                fixtureReady = true;
                // The probe of the environment is the preparation of the stand: it places one
                // block with an ordinary interaction of the player on the prepared platform and
                // it removes it again. Its fact says whether the environment accepts an ordinary
                // placement at all; it is not an assertion about the add-on.
                String probe = fixture.probeFact();
                LOGGER.info("LAB_OBSERVE stand_probe:{}", probe);
                journal.event("checkpoint", "completed", scenarioId + ".stand-probe", "OBSERVED", probe);
                outcomes.put(name, new AcceptanceReport.Case(name, AcceptanceReport.State.PASSED,
                        "integrated-server fixture prepared and observed; the probe of the "
                                + "environment: " + probe));
                writeReport();
                journal.event("setup", "completed", name, "OBSERVED",
                        "fixture prepared and observed; the gates of the preparation: "
                                + fixture.gateFacts());
            } catch (RuntimeException failure) {
                LOGGER.error("fixture preparation failed for {}", scenarioId, failure);
                outcomes.put(name, new AcceptanceReport.Case(name, AcceptanceReport.State.SKIPPED,
                        "fixture preparation failed: " + failure));
                journal.event("setup", "failed", name, "ERROR", failure.toString()
                        + "; the gates of the preparation: " + fixture.gateFacts());
                finishUnverified(runtime, "fixture preparation failed: " + failure);
                return;
            }
            if (fixture.seedPhase()) {
                finished = true;
                outcomes.put(scenarioId + ".seed", new AcceptanceReport.Case(scenarioId + ".seed",
                        AcceptanceReport.State.PASSED, "partial BUILDING registry and real floor saved; "
                        + "clean exit requested before reload JVM"));
                writeReport();
                journal.event("scenario", "completed", null, "OBSERVED",
                        "seed JVM saved partial BUILDING registry and real floor; clean exit requested");
                MinecraftClient.getInstance().stop();
                return;
            }
        }
        if (!begun) {
            begun = true;
            startedAt = System.currentTimeMillis();
            LOGGER.info("LAB_ACCEPTANCE_BEGIN:{}", scenarioId);
            observe(runtime);
        }
        if (chainObservation) {
            // The relation of the task chain is read in every tick after the resume: UnionClef
            // may install the own task a few ticks later, and a single snapshot would see an
            // empty chain and would fail a correct product.
            sampleTaskChain(runtime);
        }
        if (!preconditionsChecked) {
            StandRules.Readiness readiness = standReadiness(runtime);
            if (readiness == StandRules.Readiness.UNIONCLEF_CHAIN_MISSING) {
                // The chain of UnionClef is created while the mods are loaded and can appear a
                // few ticks after the world was loaded. The scenario waits for it inside a
                // bounded window; the wait is a product behaviour and is written into the log
                // with the number of the tick, so that a run that never sees the chain stays
                // readable.
                if (chainWaitStartedAt < 0L) {
                    chainWaitStartedAt = System.currentTimeMillis();
                }
                if (!chainWaitAnnounced || clientTicks % REPORT_INTERVAL_TICKS == 0) {
                    chainWaitAnnounced = true;
                    LOGGER.info("LAB_ACCEPTANCE_STAND:{}: tick={} the task chain of UnionClef does not exist "
                            + "yet, the scenario waits for it ({})", scenarioId, clientTicks, CHAIN_HINT);
                }
                refreshReport();
                if (System.currentTimeMillis() - chainWaitStartedAt <= CHAIN_WAIT_TIMEOUT_MILLIS) {
                    return;
                }
                // The window is over: the stand cannot show the situation of the scenario. The
                // scenario is not verified then; it is skipped with the reason and never failed.
                finishUnverified(runtime, "the task chain of UnionClef did not exist within "
                        + CHAIN_WAIT_TIMEOUT_MILLIS + " ms, the scenario was not verified (" + CHAIN_HINT + ")");
                return;
            }
            preconditionsChecked = true;
            if (!runPreconditions(runtime)) {
                // The stand does not show the situation of the scenario: the product checks are
                // not read at all, because a broken fixture proves nothing about the product.
                finishUnverified(runtime, "the setup check of the scenario failed: "
                        + String.join("; ", preconditionFailures));
                return;
            }
        }
        observeStates(runtime);
        observeProgress(runtime);
        while (stepIndex < scenario.steps.size()) {
            Step step = scenario.steps.get(stepIndex);
            String stepCase = scenarioId + ".step-" + stepIndex + "." + step.name();
            if (activeStep != stepIndex) {
                activeStep = stepIndex;
                pending(stepCase, "action in progress");
                writeReport();
                journal.event("action", "started", stepCase, null, step.name());
            }
            if (!step.run(runtime)) {
                refreshReport();
                return;
            }
            outcomes.put(stepCase, new AcceptanceReport.Case(stepCase, AcceptanceReport.State.SKIPPED,
                    "harness step finished; its named product checks determine the verdict"));
            writeReport();
            journal.event("action", "completed", stepCase, "OBSERVED", step.name());
            stepIndex++;
            // The report is refreshed after every step, so that the cases of the scenario are
            // visible even when the run ends in the middle of the scenario.
            writeReport();
        }
        finished = true;
        boolean anyFailed = false;
        boolean anyUnverified = false;
        List<String> unverified = new ArrayList<>();
        for (Check check : scenario.checks) {
            CheckResult result;
            journal.event("checkpoint", "started", check.name(), null, "read named observable");
            try {
                result = check.run(runtime);
            } catch (RuntimeException e) {
                result = CheckResult.failed("the check failed: " + e);
            }
            switch (result.state()) {
                case PASSED -> {
                    outcomes.put(check.name(),
                            new AcceptanceReport.Case(check.name(), AcceptanceReport.State.PASSED, result.detail()));
                    LOGGER.info("LAB_ACCEPTANCE_TEST:{}:PASS:{}", check.name(), result.detail());
                }
                case FAILED -> {
                    anyFailed = true;
                    outcomes.put(check.name(),
                            new AcceptanceReport.Case(check.name(), AcceptanceReport.State.FAILED, result.detail()));
                    LOGGER.info("LAB_ACCEPTANCE_TEST:{}:FAIL:{}", check.name(), result.detail());
                }
                case SKIPPED -> {
                    // The behaviour was not observed. The case stays unverified: it is no failure
                    // of the add-on, and the aggregate of the scenario is unverified as well.
                    anyUnverified = true;
                    unverified.add(check.name());
                    outcomes.put(check.name(),
                            new AcceptanceReport.Case(check.name(), AcceptanceReport.State.SKIPPED, result.detail()));
                    LOGGER.info("LAB_ACCEPTANCE_TEST:{}:SKIPPED:{}", check.name(), result.detail());
                }
                default -> {
                    // every state of the enumeration is named above
                }
            }
            writeReport();
            journal.event("checkpoint", result.state() == CheckState.FAILED ? "failed" : "completed",
                    check.name(), result.state() == CheckState.PASSED ? "PASS"
                            : result.state() == CheckState.FAILED ? "FAIL" : "SKIPPED", result.detail());
        }
        boolean allPassed = !anyFailed && !anyUnverified;
        if (allPassed) {
            outcomes.put(scenarioId, new AcceptanceReport.Case(scenarioId,
                    AcceptanceReport.State.PASSED, "every named check of the scenario was observed"));
        } else if (anyFailed) {
            outcomes.put(scenarioId, new AcceptanceReport.Case(scenarioId,
                    AcceptanceReport.State.FAILED, "at least one named check failed"));
        } else {
            outcomes.put(scenarioId, new AcceptanceReport.Case(scenarioId,
                    AcceptanceReport.State.SKIPPED, "the scenario was not verified: " + String.join(", ", unverified)));
        }
        writeReport();
        journal.event("scenario", anyFailed ? "failed" : "completed", scenarioId,
                anyFailed ? "FAIL" : anyUnverified ? "SKIPPED" : "PASS", "named checks completed");
        LOGGER.info("LAB_ACCEPTANCE_TEST:{}:{}", scenarioId,
                anyFailed ? "FAIL" : (anyUnverified ? "SKIPPED" : "PASS"));
        LOGGER.info("LAB_ACCEPTANCE_END:{}", scenarioId);
        stopCountdown = STOP_DELAY_TICKS;
        LOGGER.info("LAB_ACCEPTANCE_STATUS:{}", runtime.statusJson());
        LOGGER.info("LAB_ACCEPTANCE_REPORT:{}", runtime.statusJson());
        LOGGER.info("LAB_ACCEPTANCE_HOMES:{}", runtime.homesJson());
        LOGGER.info("LAB_ACCEPTANCE_INTERACTIONS:last={},total={},door={}",
                runtime.placement().lastOperationInteractions(), runtime.placement().totalInteractions(),
                runtime.placement().doorInteractions());
    }

    // --- the scenarios ---------------------------------------------------------------------------

    private Scenario scenarioFor(String id) {
        Scenario scenario = new Scenario();
        switch (id) {
            case "stand-diagnostic" -> {
                // The diagnostic scenario of the stand observes the environment of the run without
                // any action of the add-on: the automation stays disabled, the preparation of the
                // stand publishes no event of the dependency, and no clause of the behaviour of the
                // add-on is established here. It answers the question of the crash of the client of the
                // round 6: does the client survive the start of UnionClef with the add-on loaded,
                // and does the dependency hold a real task chain and a real slot handler there.
                scenario.steps.add(new Step() {
                    @Override
                    public String name() {
                        return "observe_the_dependency_without_a_product_action";
                    }

                    @Override
                    public boolean run(MageHomeRuntime runtime) {
                        if (diagnosticHousesAtStart < 0) {
                            // The number of the records of the registry is read once at the
                            // beginning of the window: the scenario must not change it, and the
                            // records of an earlier run of the laboratory are not its own.
                            diagnosticHousesAtStart = runtime.registryService().registry().houses().size();
                        }
                        return System.currentTimeMillis() - startedAt() >= DIAGNOSTIC_WINDOW_MILLIS;
                    }
                });
                scenario.steps.add(report());
                scenario.checks.add(clientSurvivedCheck("stand-diagnostic.client-survived"));
                scenario.checks.add(dependencyFactCheck("stand-diagnostic.chain-present", false));
                scenario.checks.add(dependencyFactCheck("stand-diagnostic.slot-handler-present", true));
                scenario.checks.add(noProductActionCheck("stand-diagnostic.no-product-action"));
            }
            case "union-task-regression" -> {
                scenario.preconditions.add(standReady("union-task-regression.stand-ready"));
                scenario.preconditions.add(platformPrepared("union-task-regression.platform-prepared"));
                scenario.preconditions.add(standProbePrepared("union-task-regression.stand-probe"));
                scenario.preconditions.add(emptyInventory("union-task-regression.empty-inventory"));
                scenario.steps.add(enable());
                scenario.steps.add(new Step() {
                    public String name() { return "wait_for_first_real_block"; }
                    public boolean run(MageHomeRuntime runtime) {
                        if (progress.contains("first-block-confirmed")) {
                            return true;
                        }
                        for (HouseRecord record : runtime.registryService().registry().houses()) {
                            if ("suspended".equals(record.state().wireName()) && record.reason() != null
                                    && !record.reason().isBlank()) {
                                // The attempt ended with a concrete reason: the scenario does not
                                // wait for the end of the world, it reads the reason.
                                LOGGER.warn("the scenario ends with the suspension of the house: {}", record.reason());
                                return true;
                            }
                        }
                        return System.currentTimeMillis() - startedAt() > 300_000L;
                    }
                });
                scenario.steps.add(report());
                scenario.checks.add(progressCheck("union-task-regression.root-without-self-child",
                        "union-root-without-child"));
                scenario.checks.add(progressCheck("union-task-regression.materials-received", "materials-received"));
                scenario.checks.add(giveFactsCheck("union-task-regression.give-facts"));
                scenario.checks.add(progressCheck("union-task-regression.first-block-confirmed", "first-block-confirmed"));
            }
            case "full-house-from-empty-inventory" -> {
                scenario.preconditions.add(standReady("full-house-from-empty-inventory.stand-ready"));
                scenario.preconditions.add(platformPrepared("full-house-from-empty-inventory.platform-prepared"));
                scenario.preconditions.add(standProbePrepared("full-house-from-empty-inventory.stand-probe"));
                scenario.preconditions.add(emptyInventory("full-house-from-empty-inventory.empty-inventory"));
                scenario.steps.add(enable());
                // The window of the wait is the patience of the scenario, not a check of the
                // result: the house of 180 cells needs more than fifteen minutes on the stand of
                // the test lab (148 cells in 900 seconds on 2026-11-14), and the run that was cut
                // off was still building (ТЗ.md section I.4). The house of 172 cells was still
                // building the furniture when the window of 1500 seconds was over.
                scenario.steps.add(waitForHouseStateOrSuspension("ready", 2_700_000L));
                scenario.steps.add(report());
                scenario.checks.add(houseStateCheck("full-house-from-empty-inventory.house-ready", "ready"));
                scenario.checks.add(confirmedCellsCheck("full-house-from-empty-inventory.confirmed-cells", HouseTemplate.OCCUPIED_CELLS));
                scenario.checks.add(frontPointCheck("full-house-from-empty-inventory.exit"));
                scenario.checks.add(physicalPlacementCheck("full-house-from-empty-inventory.physical-placement"));
                scenario.checks.add(attemptFactsCheck("full-house-from-empty-inventory.attempt-facts"));
                scenario.checks.add(aimBeforeClickCheck("full-house-from-empty-inventory.aim-verdict"));
                scenario.checks.add(gameHitResultCheck("full-house-from-empty-inventory.game-hit-result"));
                scenario.checks.add(placementPreconditionsCheck("full-house-from-empty-inventory.placement-preconditions"));
                scenario.checks.add(attemptDiagnosticsCheck("full-house-from-empty-inventory.attempt-diagnostics"));
                scenario.checks.add(suspensionDiagnosisCheck("full-house-from-empty-inventory.suspension-diagnosis"));
                scenario.checks.add(noNavigationWhileSuspendedCheck("full-house-from-empty-inventory.no-navigation-while-suspended"));
            }
            case "give-refused" -> {
                scenario.preconditions.add(standReady("give-refused.stand-ready"));
                scenario.preconditions.add(platformPrepared("give-refused.platform-prepared"));
                scenario.preconditions.add(standProbePrepared("give-refused.stand-probe"));
                scenario.preconditions.add(giveRemovedByTheStand("give-refused.give-removed-by-the-stand"));
                scenario.preconditions.add(giveNotAvailable("give-refused.give-not-available"));
                scenario.steps.add(enable());
                scenario.steps.add(waitForReason("materials_missing", 180_000L));
                scenario.steps.add(report());
                scenario.checks.add(reasonCheck("give-refused.materials-missing", "materials_missing"));
                scenario.checks.add(noMiningOrCraftingCheck("give-refused.no-mining-or-crafting"));
            }
            case "site-blocked-by-water" -> {
                scenario.preconditions.add(standReady("site-blocked-by-water.stand-ready"));
                scenario.preconditions.add(waterPlatformPrepared("site-blocked-by-water.water-platform-prepared"));
                scenario.preconditions.add(standProbePrepared("site-blocked-by-water.stand-probe"));
                scenario.steps.add(enable());
                scenario.steps.add(waitForReason("no_site", 300_000L));
                scenario.steps.add(report());
                scenario.checks.add(reasonCheck("site-blocked-by-water.no-site", "no_site"));
                scenario.checks.add(blocksUnchangedCheck("site-blocked-by-water.no-break"));
            }
            case "stop-resume-single-house" -> {
                scenario.preconditions.add(standReady("stop-resume-single-house.stand-ready"));
                scenario.preconditions.add(platformPrepared("stop-resume-single-house.platform-prepared"));
                scenario.preconditions.add(standProbePrepared("stop-resume-single-house.stand-probe"));
                scenario.steps.add(enable());
                scenario.steps.add(waitForStopPrerequisites(300_000L));
                scenario.steps.add(stopUnionClef());
                scenario.steps.add(waitForStopInterruption(60_000L));
                scenario.steps.add(resumeHouse());
                // The resume is handed to the client thread and is applied in the next tick. The
                // chain of the tasks is observed only after the house is building again, so that
                // the observation is not the one of the moment before the resume.
                scenario.steps.add(waitForResumedHouse(60_000L));
                scenario.steps.add(observeTaskChain());
                scenario.steps.add(waitForHouseStateOrSuspension("ready", 900_000L));
                scenario.steps.add(report());
                scenario.checks.add(stateObservedCheck("stop-resume-single-house.interrupted",
                        "suspended", "interrupted"));
                scenario.checks.add(housesCountCheck("stop-resume-single-house.single-record", 1));
                scenario.checks.add(houseStateCheck("stop-resume-single-house.same-house-ready", "ready"));
                scenario.checks.add(ownRootCheck("stop-resume-single-house.own-root"));
                scenario.checks.add(foreignTaskUntouchedCheck("stop-resume-single-house.foreign-task-untouched"));
                scenario.checks.add(suspensionDiagnosisCheck("stop-resume-single-house.suspension-diagnosis"));
                scenario.checks.add(noNavigationWhileSuspendedCheck("stop-resume-single-house.no-navigation-while-suspended"));
            }
            case "persistence-after-exit" -> {
                scenario.preconditions.add(standReady("persistence-after-exit.stand-ready"));
                scenario.preconditions.add(buildingRecord("persistence-after-exit.building-record"));
                // The world is entered again after the client was closed; the registry is read and
                // the house that was building becomes suspended with the reason restart.
                scenario.steps.add(waitForHouses(1, 60_000L));
                scenario.steps.add(report());
                scenario.checks.add(registryFileCheck("persistence-after-exit.registry-file"));
                scenario.checks.add(houseStateCheck("persistence-after-exit.suspended-restart", "suspended"));
                scenario.checks.add(houseReasonCheck("persistence-after-exit.restart-reason", "restart"));
            }
            case "no-new-house-inside-radius" -> {
                scenario.preconditions.add(standReady("no-new-house-inside-radius.stand-ready"));
                scenario.preconditions.add(platformPrepared("no-new-house-inside-radius.platform-prepared"));
                scenario.preconditions.add(standProbePrepared("no-new-house-inside-radius.stand-probe"));
                scenario.preconditions.add(seededRecord("no-new-house-inside-radius.seeded-record", 0));
                scenario.steps.add(enable());
                // The delay of 1200 ticks is 60 seconds; the scenario waits longer than that.
                scenario.steps.add(waitForMillis(90_000L));
                scenario.steps.add(report());
                scenario.checks.add(housesCountCheck("no-new-house-inside-radius.no-new-house", 1));
            }
            case "next-palette-after-radius" -> {
                scenario.preconditions.add(standReady("next-palette-after-radius.stand-ready"));
                scenario.preconditions.add(platformPrepared("next-palette-after-radius.platform-prepared"));
                scenario.preconditions.add(standProbePrepared("next-palette-after-radius.stand-probe"));
                scenario.preconditions.add(seededRecord("next-palette-after-radius.seeded-record", 0));
                scenario.steps.add(enable());
                scenario.steps.add(waitForHouses(2, 600_000L));
                scenario.steps.add(report());
                scenario.checks.add(housesCountCheck("next-palette-after-radius.new-record", 2));
                scenario.checks.add(variantCheck("next-palette-after-radius.next-palette", 1));
            }
            case "safe-repair-of-wrong-block" -> {
                scenario.preconditions.add(standReady("safe-repair-of-wrong-block.stand-ready"));
                scenario.preconditions.add(platformPrepared("safe-repair-of-wrong-block.platform-prepared"));
                scenario.preconditions.add(standProbePrepared("safe-repair-of-wrong-block.stand-probe"));
                scenario.preconditions.add(repairHousePrepared("safe-repair-of-wrong-block.repair-house-prepared"));
                scenario.steps.add(enable());
                scenario.steps.add(resumeHouse());
                scenario.steps.add(waitForRepair(180_000L));
                scenario.steps.add(report());
                scenario.checks.add(wrongBlockRemovedCheck("safe-repair-of-wrong-block.wrong-block-removed"));
                scenario.checks.add(requiredBlockPlacedCheck("safe-repair-of-wrong-block.required-block-placed"));
                scenario.checks.add(buildingContinuedCheck("safe-repair-of-wrong-block.building-continued"));
                scenario.checks.add(protectedCellsUntouchedCheck("safe-repair-of-wrong-block.protected-cells-untouched"));
                scenario.checks.add(loneHalfNotOverwrittenCheck("safe-repair-of-wrong-block.lone-half-not-overwritten"));
            }
            case "safe-repair-of-foreign-block" -> {
                scenario.preconditions.add(standReady("safe-repair-of-foreign-block.stand-ready"));
                scenario.preconditions.add(platformPrepared("safe-repair-of-foreign-block.platform-prepared"));
                scenario.preconditions.add(standProbePrepared("safe-repair-of-foreign-block.stand-probe"));
                scenario.preconditions.add(foreignHousePrepared("safe-repair-of-foreign-block.foreign-house-prepared"));
                scenario.steps.add(enable());
                scenario.steps.add(resumeHouse());
                scenario.steps.add(waitForSuspension(180_000L));
                scenario.steps.add(report());
                scenario.checks.add(foreignBlockRepairedCheck("safe-repair-of-foreign-block.foreign-block-repaired"));
                scenario.checks.add(protectedForeignBlockUntouchedCheck("safe-repair-of-foreign-block.protected-foreign-block-untouched"));
                scenario.checks.add(houseSuspendedCheck("safe-repair-of-foreign-block.house-suspended-obstructed"));
            }
            default -> {
                scenario.steps.add(report());
            }
        }
        return scenario;
    }

    /**
     * Waits until the wrong cell of the house holds the block of the project and the lone half of
     * the door is gone.
     *
     * <p>The wait is bounded: a run that does not see the repair ends with the reason of the
     * suspension instead of waiting for ever, and the named checks of the scenario read the state
     * that was observed.
     *
     * @param timeoutMillis the budget of the wait
     */
    private Step waitForRepair(long timeoutMillis) {
        return new Step() {
            private long startedAt = -1L;

            @Override
            public String name() {
                return "wait_for_repair";
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                if (startedAt < 0L) {
                    startedAt = System.currentTimeMillis();
                }
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    LOGGER.warn("LAB_ACCEPTANCE_OBSERVE repair: the registry has no house");
                    return true;
                }
                var observed = runtime.context().world().read(repairPosition(record));
                String wrong = LabScenarioFixture.repairWrongBlock();
                if (!observed.isAir() && !wrong.equals(observed.blockId())) {
                    // The wrong block is gone and the block of the project stands in the cell. The
                    // step waits for the record of the house to name the same state: the count of
                    // the confirmed cells is written by the automation in its own tick, and the
                    // checks of the scenario read the record, not the world alone.
                    if (record.confirmedCells() >= 49 && loneHalfHandled(runtime, record)) {
                        LOGGER.info("LAB_ACCEPTANCE_OBSERVE repair: the cell holds {} and the record names "
                                + "{} confirmed cells", observed.blockId(), record.confirmedCells());
                        return true;
                    }
                    LOGGER.info("LAB_ACCEPTANCE_OBSERVE repair: the cell holds {} and the record names {} "
                                    + "confirmed cells", observed.blockId(), record.confirmedCells());
                    if (System.currentTimeMillis() - startedAt > timeoutMillis) {
                        LOGGER.warn("LAB_ACCEPTANCE_OBSERVE repair: the repair was not observed within {} ms",
                                timeoutMillis);
                        return true;
                    }
                    return false;
                }
                if (!loneHalfHandled(runtime, record)) {
                    LOGGER.info("LAB_ACCEPTANCE_OBSERVE repair: the lone half of the door still stands");
                    return false;
                }
                String reason = runtime.controller().reason();
                if ("obstructed".equals(reason)) {
                    LOGGER.warn("LAB_ACCEPTANCE_OBSERVE repair: the house is suspended with the reason {}",
                            reason);
                    return true;
                }
                if (System.currentTimeMillis() - startedAt > timeoutMillis) {
                    LOGGER.warn("LAB_ACCEPTANCE_OBSERVE repair: the repair was not observed within {} ms",
                            timeoutMillis);
                    return true;
                }
                return false;
            }
        };
    }

    /**
     * Waits until the house is suspended with the reason obstructed.
     *
     * <p>The wait is bounded: a run that does not see the suspension ends with the timeout of the
     * step, and the named checks of the scenario read the state that was observed.
     *
     * @param timeoutMillis the budget of the wait
     */
    private Step waitForSuspension(long timeoutMillis) {
        return new Step() {
            private long startedAt = -1L;

            @Override
            public String name() {
                return "wait_for_suspension";
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                if (startedAt < 0L) {
                    startedAt = System.currentTimeMillis();
                }
                String reason = runtime.controller().reason();
                if ("obstructed".equals(reason)) {
                    LOGGER.info("LAB_ACCEPTANCE_OBSERVE foreign: the house is suspended with the reason {}",
                            reason);
                    return true;
                }
                if (System.currentTimeMillis() - startedAt > timeoutMillis) {
                    LOGGER.warn("LAB_ACCEPTANCE_OBSERVE foreign: the suspension was not observed within {} ms",
                            timeoutMillis);
                    return true;
                }
                return false;
            }
        };
    }

    /**
     * True when the lone half of the door of the house is gone.
     *
     * <p>The half is gone when the cell is empty or when the cell holds the lower half of a
     * complete pair of the door: the pair was placed again after the half was eliminated by the
     * safe repair. A half that still stands alone is not handled yet.
     *
     * @param runtime the runtime of the scenario
     * @param record  the record of the house of the scenario
     * @return true when the lone half is gone
     */
    private boolean loneHalfHandled(MageHomeRuntime runtime, HouseRecord record) {
        Cell cell = LabScenarioFixture.LONE_HALF_CELL;
        var lower = runtime.context().world().read(record.origin().add(cell.x(), cell.y(), cell.z()));
        if (lower.isAir()) {
            return true;
        }
        Cell upper = new Cell(cell.x(), cell.y() + 1, cell.z());
        var upperBlock = runtime.context().world().read(record.origin().add(upper.x(), upper.y(), upper.z()));
        return "upper".equals(upperBlock.property("half"));
    }

    /** The position of the wrong cell of the house of the scenario. */
    private Vec3i repairPosition(HouseRecord record) {
        return record.origin().add(0, 0, 0);
    }

    /** The block that the project wants in the wrong cell of the house of the scenario. */
    private String repairWantedBlock() {
        return HouseTemplate.forVariant(0).spec(new Cell(0, 0, 0)).blockId();
    }

    // --- the steps -------------------------------------------------------------------------------

    private Step enable() {
        return new Step() {
            @Override
            public String name() {
                return "enable";
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                runtime.onClientThread(() -> runtime.controller().enable());
                return true;
            }
        };
    }

    private Step report() {
        return new Step() {
            @Override
            public String name() {
                return "report";
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                LOGGER.info("LAB_ACCEPTANCE_REPORT:{}", runtime.statusJson());
                LOGGER.info("LAB_ACCEPTANCE_HOMES:{}", runtime.homesJson());
                LOGGER.info("LAB_ACCEPTANCE_INTERACTIONS:last={},total={},door={}",
                        runtime.placement().lastOperationInteractions(), runtime.placement().totalInteractions(),
                        runtime.placement().doorInteractions());
                return true;
            }
        };
    }

    static boolean stopPrerequisites(boolean singleBuilding, boolean activeOwnRoot, boolean actualFirstBlock) {
        return singleBuilding && activeOwnRoot && actualFirstBlock;
    }

    /** A Stop is meaningful only after an active own root and a real confirmed placement. */
    private Step waitForStopPrerequisites(long timeoutMillis) {
        return new Step() {
            private long start = -1;
            public String name() { return "wait_for_stop_prerequisites"; }
            public boolean run(MageHomeRuntime runtime) {
                if (start < 0) start = System.currentTimeMillis();
                var houses = runtime.registryService().registry().houses();
                Task root = runtime.ownRootTask();
                if (stopPrerequisites(houses.size() == 1 && "building".equals(houses.get(0).state().wireName()),
                        root != null && runtime.currentTaskObject() == root && root.isActive(),
                        progress.contains("first-block-confirmed"))) {
                    stopHouseId = houses.get(0).houseId();
                    LOGGER.info("LAB_ACCEPTANCE_STOP: prerequisites confirmed for {}", stopHouseId);
                    return true;
                }
                if (System.currentTimeMillis() - start > timeoutMillis) {
                    finishUnverified(runtime, "Stop prerequisites not confirmed: own active root, single building house and real first block; houses="
                            + runtime.homesJson() + "; progress=" + progress);
                }
                return false;
            }
        };
    }

    private Step waitForStopInterruption(long timeoutMillis) {
        return new Step() {
            private long start = -1;
            public String name() { return "wait_for_stop_interruption"; }
            public boolean run(MageHomeRuntime runtime) {
                if (start < 0) start = System.currentTimeMillis();
                var houses = runtime.registryService().registry().houses();
                if (stopIssued && houses.size() == 1 && stopHouseId.equals(houses.get(0).houseId())
                        && "suspended".equals(houses.get(0).state().wireName())
                        && "interrupted".equals(houses.get(0).reason())) {
                    interruptedAfterStop = true;
                    return true;
                }
                if (System.currentTimeMillis() - start > timeoutMillis) {
                    finishUnverified(runtime, "suspended/interrupted after Stop not observed: " + runtime.homesJson());
                }
                return false;
            }
        };
    }

    private Step waitForResumedHouse(long timeoutMillis) {
        return new Step() {
            private long start = -1;
            public String name() { return "wait_for_resumed_house"; }
            public boolean run(MageHomeRuntime runtime) {
                if (start < 0) start = System.currentTimeMillis();
                var houses = runtime.registryService().registry().houses();
                if (resumeIssued && houses.size() == 1 && stopHouseId.equals(houses.get(0).houseId())
                        && "building".equals(houses.get(0).state().wireName())) return true;
                if (System.currentTimeMillis() - start > timeoutMillis) {
                    finishUnverified(runtime, "resume of the interrupted house was not observed: " + runtime.homesJson());
                }
                return false;
            }
        };
    }

    /** The ordinary Stop of UnionClef. */
    private Step stopUnionClef() {
        return new Step() {
            @Override
            public String name() {
                return "stop_unionclef";
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                Task current = runtime.currentTaskObject();
                if (stopHouseId == null || current == null || current != runtime.ownRootTask() || !current.isActive()) {
                    finishUnverified(runtime, "Stop not performed: confirmed own active root is no longer held");
                    return false;
                }
                new UnionClefBridge().stopTaskChain();
                stopIssued = true;
                LOGGER.info("LAB_ACCEPTANCE_STOP:unionclef");
                return true;
            }
        };
    }

    /** The explicit resume of the house that is being built. */
    private Step resumeHouse() {
        return new Step() {
            @Override
            public String name() {
                return "resume_house";
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                List<HouseRecord> houses = runtime.registryService().registry().houses();
                if (houses.isEmpty()) {
                    LOGGER.warn("LAB_ACCEPTANCE_RESUME: no house in the registry");
                    return true;
                }
                String houseId = houses.get(0).houseId();
                if ("stop-resume-single-house".equals(scenarioId)
                        && (!interruptedAfterStop || houses.size() != 1 || !houseId.equals(stopHouseId)
                        || !"suspended".equals(houses.get(0).state().wireName())
                        || !"interrupted".equals(houses.get(0).reason()))) {
                    finishUnverified(runtime, "resume not performed: interrupted house no longer confirmed");
                    return false;
                }
                LOGGER.info("LAB_ACCEPTANCE_RESUME:{}", houseId);
                runtime.resume(houseId);
                resumeIssued = true;
                // From now on the relation of the task chain is read in every tick, until the
                // chain holds the own root task of the add-on. The window of the observation
                // begins here: the states of the chain and the calls of the add-on that were
                // made before are not part of it.
                chainObservation = true;
                observedRelations.clear();
                observedSamples.clear();
                callsBeforeWindow = runtime.chainActions().size();
                foreignTask = null;
                return true;
            }
        };
    }

    /**
     * Waits until the chain of UnionClef holds the own root task of the add-on.
     *
     * <p>The relation is read in every tick since the resume (see
     * {@link #sampleTaskChain(MageHomeRuntime)}); the step waits for the observation and gives
     * up after a bounded time, so that a run that never sees the own root task ends with a
     * readable result instead of hanging.
     */
    private Step observeTaskChain() {
        return new Step() {
            @Override
            public String name() {
                return "observe_task_chain";
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                if (ownRootTick >= 0L) {
                    LOGGER.info("LAB_ACCEPTANCE_OBSERVE task_chain tick={} current={} relation=OWN_ROOT",
                            ownRootTick, ownRootTask);
                    return true;
                }
                if (ownRootWaitStartedAt < 0L) {
                    // The budget of this step starts when the step is entered. The steps before
                    // it have their own budgets, and a slow run must not spend the budget of
                    // this step before the step began.
                    ownRootWaitStartedAt = System.currentTimeMillis();
                    LOGGER.info("LAB_ACCEPTANCE_OBSERVE task_chain the observation of the own root task "
                            + "begins, the last relation was {}", observedRelation);
                }
                if (System.currentTimeMillis() - ownRootWaitStartedAt > OWN_ROOT_TIMEOUT_MILLIS) {
                    LOGGER.warn("LAB_ACCEPTANCE_OBSERVE task_chain the chain of UnionClef did not hold the own "
                            + "root task within {} ms after the resume, the last relation was {}",
                            OWN_ROOT_TIMEOUT_MILLIS, observedRelation);
                    return true;
                }
                return false;
            }
        };
    }

    /**
     * The ownership of the task chain of UnionClef, observed with the real UnionClef.
     *
     * <p>The check judges the conduct of the add-on and not the presence of another owner: the
     * add-on has to hold its own root task, it must not take the chain away from the task of
     * another owner and it must not stop that task. A chain that another owner held lawfully is
     * no failure of the add-on: the add-on has to wait then (ТЗ.md section 5). The relation was
     * read in every tick since the resume, so the observation is not a single snapshot.
     *
     * <p>The case is green only when the own root task of the add-on was really the root of the
     * chain of UnionClef. When another owner held the chain during the whole window, the own
     * root task was not observed: the case stays unverified then and is never a failure of the
     * add-on.
     */
    private Check ownRootCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                List<TaskChainOwnership.Sample> samples = List.copyOf(observedSamples);
                List<TaskChainOwnership.AddOnAction> calls = windowCalls(runtime);
                TaskChainOwnership.Verdict verdict = TaskChainOwnership.judge(samples, calls);
                String facts = "the states of the chain were " + samples
                        + ", the calls of the add-on were " + calls;
                switch (verdict) {
                    case OWN_ROOT_HELD -> {
                        return CheckResult.ok(verdict.description() + ": the own root task was the root of the "
                                + "chain of UnionClef at tick " + ownRootTick + " (" + ownRootTask + "), " + facts);
                    }
                    case WAITING_FOR_FOREIGN -> {
                        return CheckResult.unverified(verdict.description() + ": the own root task of the add-on was "
                                + "not observed, the chain belonged to another owner during the whole window"
                                + (foreignRelationTask == null ? ""
                                : " (" + foreignRelationTask + " at tick " + foreignRelationTick + ")")
                                + ", " + facts);
                    }
                    case OBSERVATION_INCOMPLETE -> {
                        return CheckResult.unverified(verdict.description() + ": the own root task of the add-on was "
                                + "not observed, " + facts);
                    }
                    case CHAIN_UNAVAILABLE -> {
                        return CheckResult.unverified(verdict.description() + ": " + facts);
                    }
                    case OWN_ROOT_EMPTY_AT_THE_END -> {
                        // The add-on held its own root task, and the chain was empty at the end of
                        // the window: the own root task was not held then. The window proves
                        // nothing about the ownership of the chain, and it is no violation of the
                        // add-on either (an interruption empties the chain and the add-on waits
                        // for an explicit resume then).
                        return CheckResult.unverified(verdict.description() + ": the own root task was not held at "
                                + "the end of the window, " + facts);
                    }
                    default -> {
                        return CheckResult.failed(verdict.description() + ": " + facts);
                    }
                }
            }
        };
    }

    /**
     * The task of another owner is neither taken away nor stopped by the add-on.
     *
     * <p>The record of the calls of the add-on shows on which task the chain of UnionClef was
     * when the add-on acted on it. A call while the chain holds the task of another owner would
     * have stopped that task ({@code SingleTaskChain.setTask} stops the old root task), and that
     * is forbidden (ТЗ.md section 5). The state of the task of the other owner inside the
     * window is read as well, so that the report shows whether it was alive, whether it ended by
     * itself and whether somebody else stopped it.
     */
    private Check foreignTaskUntouchedCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                List<TaskChainOwnership.Sample> samples = List.copyOf(observedSamples);
                List<TaskChainOwnership.AddOnAction> calls = windowCalls(runtime);
                TaskChainOwnership.ForeignFate fate = TaskChainOwnership.foreignFate(samples, calls);
                TaskChainOwnership.ForeignTaskVerdict verdict = TaskChainOwnership.foreignTaskUntouched(fate);
                if (verdict.violation()) {
                    return CheckResult.failed("the add-on took the chain away from the task of another owner and "
                            + "stopped it: the calls of the add-on were " + calls);
                }
                if (calls.isEmpty()) {
                    return CheckResult.unverified("the add-on made no call on the chain of UnionClef inside the "
                            + "window, there was nothing to assert about the task of another owner");
                }
                if (!verdict.verified()) {
                    // No task of another owner was observed at all: the add-on harmed nobody, but
                    // the norm of ТЗ.md section 5 was not observed either. The case stays
                    // unverified and is never read as a proven norm.
                    return CheckResult.unverified(verdict.description() + ": " + fate.description()
                            + ", the calls of the add-on were " + calls
                            + ", the states of the chain were " + samples);
                }
                return CheckResult.ok("the add-on called the chain of UnionClef " + calls.size()
                        + " time(s) and never while it held the task of another owner; " + fate.description()
                        + ", the calls were " + calls + ", the states of the chain were " + samples);
            }
        };
    }

    /**
     * The calls that the add-on made on the chain of UnionClef inside the window.
     *
     * <p>Only the calls that were made after the window began belong to the observation; the
     * calls of the earlier ticks of the scenario are not part of it.
     */
    private List<TaskChainOwnership.AddOnAction> windowCalls(MageHomeRuntime runtime) {
        List<UnionClefBridge.ChainAction> all = runtime.chainActions();
        List<TaskChainOwnership.AddOnAction> calls = new ArrayList<>();
        for (int index = Math.min(callsBeforeWindow, all.size()); index < all.size(); index++) {
            UnionClefBridge.ChainAction call = all.get(index);
            calls.add(new TaskChainOwnership.AddOnAction(call.action(), heldBy(call, runtime),
                    call.heldState()));
        }
        return calls;
    }

    /**
     * What the chain of UnionClef held when the add-on made a call.
     *
     * <p>The chain held the own root task of the add-on, nothing at all, or the task of another
     * owner. The identity of the task decides it, not a flag of the add-on.
     */
    private TaskChainOwnership.Held heldBy(UnionClefBridge.ChainAction call, MageHomeRuntime runtime) {
        Task held = call.heldTask();
        if (held == null) {
            return TaskChainOwnership.Held.CHAIN_EMPTY;
        }
        if (held == runtime.ownRootTask()) {
            return TaskChainOwnership.Held.OWN_ROOT;
        }
        return TaskChainOwnership.Held.FOREIGN;
    }

    /** Waits until the state of the house in the registry is the expected one. */
    private Step waitForHouseState(String state, long timeoutMillis) {
        return new Step() {
            @Override
            public String name() {
                return "wait_for_house_" + state;
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                for (HouseRecord record : runtime.registryService().registry().houses()) {
                    if (state.equals(record.state().wireName())) {
                        return true;
                    }
                }
                if (System.currentTimeMillis() - startedAt() > timeoutMillis) {
                    LOGGER.warn("the acceptance step {} timed out, the houses are {}", name(),
                            runtime.homesJson());
                    return true;
                }
                return false;
            }
        };
    }

    /**
     * Waits until the state of the house is the expected one or until the house is suspended.
     *
     * <p>A house that is suspended with a concrete reason is the end of the attempt: the
     * scenario does not wait for the state "ready" until the end of the world, but it finishes
     * with the reason of the suspension, the coordinate and the observed state of the cell
     * (ТЗ.md section 4). The assertion of the ready house fails with this reason then; it is not
     * skipped, because the attempt really did not finish the house.
     */
    private Step waitForHouseStateOrSuspension(String state, long timeoutMillis) {
        return new Step() {
            @Override
            public String name() {
                return "wait_for_house_" + state + "_or_suspension";
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                for (HouseRecord record : runtime.registryService().registry().houses()) {
                    if (state.equals(record.state().wireName())) {
                        return true;
                    }
                    if ("suspended".equals(record.state().wireName()) && record.reason() != null
                            && !record.reason().isBlank()) {
                        LOGGER.warn("the acceptance step {} ends with the suspension of the house: {}",
                                name(), record.reason());
                        return true;
                    }
                }
                if (System.currentTimeMillis() - startedAt() > timeoutMillis) {
                    LOGGER.warn("the acceptance step {} timed out, the houses are {}", name(),
                            runtime.homesJson());
                    return true;
                }
                return false;
            }
        };
    }

    /** Waits until the registry contains the given number of houses. */
    private Step waitForHouses(int count, long timeoutMillis) {
        return new Step() {
            @Override
            public String name() {
                return "wait_for_" + count + "_houses";
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                if (runtime.registryService().registry().houses().size() >= count) {
                    return true;
                }
                if (System.currentTimeMillis() - startedAt() > timeoutMillis) {
                    LOGGER.warn("the acceptance step {} timed out, the houses are {}", name(),
                            runtime.homesJson());
                    return true;
                }
                return false;
            }
        };
    }

    /** Waits for the given time; the scenario uses it to let the delay of the search expire. */
    private Step waitForMillis(long millis) {
        return new Step() {
            @Override
            public String name() {
                return "wait_" + millis + "_ms";
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                return System.currentTimeMillis() - startedAt() > millis;
            }
        };
    }

    private Step waitForReason(String reason, long timeoutMillis) {
        return new Step() {
            @Override
            public String name() {
                return "wait_for_" + reason;
            }

            @Override
            public boolean run(MageHomeRuntime runtime) {
                String status = runtime.statusJson();
                // The reason of a suspension names the place where the work stopped, so the step
                // waits for the name of the reason and not for the whole text of it.
                if (status.contains("\"reason\":\"" + reason)) {
                    return true;
                }
                if (System.currentTimeMillis() - startedAt() > timeoutMillis) {
                    LOGGER.warn("the acceptance step {} timed out, the status is {}", name(), status);
                    return true;
                }
                return false;
            }
        };
    }

    // --- the named checks ------------------------------------------------------------------------

    /**
     * Reads the setup checks of the scenario.
     *
     * <p>A setup check is a fact about the stand, not about the product: a loaded world, an empty
     * inventory, a server without the command {@code give} or the seeded record of the registry.
     * When one of them does not hold, the dependent product checks stay unread, because a broken
     * fixture proves nothing about the behaviour of the add-on.
     *
     * @return true when every setup check holds
     */
    private boolean runPreconditions(MageHomeRuntime runtime) {
        boolean ready = true;
        List<String> failures = new ArrayList<>();
        for (Check check : scenario.preconditions) {
            CheckResult result;
            journal.event("setup", "started", check.name(), null, "read fixture precondition");
            try {
                result = check.run(runtime);
            } catch (RuntimeException e) {
                result = CheckResult.failed("the setup check failed: " + e);
            }
            if (result.passed()) {
                outcomes.put(check.name(),
                        new AcceptanceReport.Case(check.name(), AcceptanceReport.State.PASSED, result.detail()));
                LOGGER.info("LAB_ACCEPTANCE_TEST:{}:PASS:{}", check.name(), result.detail());
            } else {
                ready = false;
                failures.add(check.name() + ": " + result.detail());
                // The setup check of the stand did not hold. That is a fact about the fixture
                // and not a failure of the product, so the case stays skipped with the reason.
                outcomes.put(check.name(), new AcceptanceReport.Case(check.name(),
                        AcceptanceReport.State.SKIPPED,
                        "the setup check of the stand did not hold: " + result.detail()));
                LOGGER.info("LAB_ACCEPTANCE_TEST:{}:SKIPPED:the setup check of the stand did not hold: {}",
                        check.name(), result.detail());
            }
            writeReport();
            journal.event("setup", result.passed() ? "completed" : "failed", check.name(),
                    result.passed() ? "OBSERVED" : "SKIPPED", result.detail());
        }
        preconditionFailures = failures;
        return ready;
    }

    /**
     * Ends the scenario without a verdict about the product.
     *
     * <p>The stand could not show the situation of the scenario: every named case that was not
     * observed stays skipped with the reason, and the aggregate of the scenario is skipped as
     * well. A fixture that cannot show the product is never read as a failure of the product.
     *
     * @param runtime the runtime of the mod; its state is written into the log
     * @param reason  the reason why the scenario was not verified
     */
    private void finishUnverified(MageHomeRuntime runtime, String reason) {
        finished = true;
        publishPendingCases(reason);
        outcomes.put(scenarioId, new AcceptanceReport.Case(scenarioId,
                AcceptanceReport.State.SKIPPED, reason));
        writeReport();
        journal.event("scenario", "failed", scenarioId, "SKIPPED", reason);
        LOGGER.info("LAB_ACCEPTANCE_TEST:{}:SKIPPED:{}", scenarioId, reason);
        LOGGER.info("LAB_ACCEPTANCE_END:{}", scenarioId);
        stopCountdown = STOP_DELAY_TICKS;
        LOGGER.info("LAB_ACCEPTANCE_STATUS:{}", runtime.statusJson());
        LOGGER.info("LAB_ACCEPTANCE_REPORT:{}", runtime.statusJson());
        LOGGER.info("LAB_ACCEPTANCE_HOMES:{}", runtime.homesJson());
    }

    /**
     * The readiness of the stand of the scenario.
     *
     * <p>The rule lives in the domain core, so that the harness and the executable checks apply
     * exactly the same rule. The chain of UnionClef is a fact of the environment: it can appear
     * a few ticks after the world was loaded, therefore the caller waits for it.
     */
    private StandRules.Readiness standReadiness(MageHomeRuntime runtime) {
        MinecraftClient client = MinecraftClient.getInstance();
        UnionClefBridge bridge = new UnionClefBridge();
        return StandRules.readiness(
                client.world != null,
                runtime.context().playerAlive(),
                !runtime.context().playerUuid().isEmpty(),
                bridge.available(),
                bridge.chainAvailable(),
                bridge.slotHandlerAvailable());
    }

    /**
     * Writes the JUnit report of the scenario.
     *
     * <p>The report holds one case for every named case of the scenario, with the result that was
     * really observed. The directory of the report is given by the system property
     * {@code lab.acceptance.results.dir}; without it no report is written, so that a run of the
     * game outside of the acceptance run leaves nothing behind.
     */
    private void writeReport() {
        String directory = System.getProperty("lab.acceptance.results.dir", "");
        if (directory.isBlank()) {
            LOGGER.warn("LAB_ACCEPTANCE_JUNIT:{}: no result directory is configured, "
                    + "the JUnit report of the scenario is not written", scenarioId);
            return;
        }
        try {
            AcceptanceReport.write(Path.of(directory), scenarioId + (fixture.seedPhase() ? "-seed" : ""),
                    new ArrayList<>(outcomes.values()));
            LOGGER.info("LAB_ACCEPTANCE_JUNIT:{}: the JUnit report of the scenario is written into {}",
                    scenarioId, directory);
        } catch (RuntimeException e) {
            LOGGER.error("LAB_ACCEPTANCE_JUNIT:{}: the JUnit report of the scenario is not written: {}",
                    scenarioId, e.toString());
        }
    }

    /**
     * Refreshes the report of the scenario while it runs.
     *
     * <p>The named cases of the scenario are written again from time to time, so that a run that
     * ends abnormally still leaves a report with the cases of the scenario behind. A case that
     * was not observed stays skipped.
     */
    private void refreshReport() {
        // Results are forced at transitions. An unchanged wait never rewrites the report each tick.
    }

    /** Durable observations of work already completed, even if a later placement crashes. */
    private void observeProgress(MageHomeRuntime runtime) {
        observePlacementFacts(runtime);
        if (!items(runtime).isEmpty()) progress(runtime, "materials-received", "nonempty inventory observed");
        HouseRecord house = lastHouse(runtime);
        if (house != null && house.confirmedCells() > 0 && !progress.contains("first-block-confirmed")) {
            HouseTemplate template = HouseTemplate.forVariant(house.variant());
            for (var interaction : runtime.placement().interactions()) {
                Cell cell = interaction.cell();
                if (cell == null || template.spec(cell) == null) continue;
                Vec3i actual = house.origin().add(cell.x(), cell.y(), cell.z());
                if (dev.magehome.core.verification.CellVerifier.verify(template.spec(cell),
                        runtime.context().world().read(actual)) == dev.magehome.core.verification.CellVerdict.MATCH) {
                    progress(runtime, "first-block-confirmed", "ordinary placement interaction and actual template "
                            + "block observed at " + actual + "; confirmed_cells=" + house.confirmedCells());
                    break;
                }
            }
        }
        if (runtime.ownRootTask() != null && runtime.currentTaskObject() == runtime.ownRootTask() && runtime.ownRootTask().isActive()
                && runtime.onTaskTick() == null) {
            progress(runtime, "union-root-without-child", "real UnionClef chain holds MageHomeTask; "
                    + "onTaskTick returned null while no navigation child was requested");
        }
    }

    /**
     * Writes the facts of the last attempt of the placement into the journal of the scenario.
     *
     * <p>The facts are the observation of the attempt: the cell of the project, the block that
     * was clicked, the place where the player stood, the item in his hand, whether the
     * interaction was sent, the distance from his eyes to the point of the click and what the
     * world showed in the cell. Without them a run that does not confirm a cell cannot be read
     * (ТЗ.md section 4).
     */
    private void observePlacementFacts(MageHomeRuntime runtime) {
        String facts = runtime.placement().lastFacts();
        if (facts == null || facts.isBlank() || facts.equals(lastJournaledFacts)) {
            return;
        }
        if (clientTicks - lastJournaledFactsTick < JOURNAL_FACTS_INTERVAL_TICKS) {
            // The journal keeps the facts of the attempt, but not every tick of the walk: five
            // seconds are enough to see the way of the player and the distance to the click.
            return;
        }
        lastJournaledFacts = facts;
        lastJournaledFactsTick = clientTicks;
        // The stage of the event is one of the stages that the journal of the gameplay execution
        // accepts (setup, action, checkpoint, scenario). The facts of the attempt are an
        // observation of the product action, so they are written as a checkpoint.
        journal.event("checkpoint", "completed", scenarioId + ".placement-facts", "OBSERVED", facts);
    }

    private void progress(MageHomeRuntime runtime, String checkpoint, String detail) {
        if (!progress.add(checkpoint)) return;        String name = scenarioId + ".progress." + checkpoint;
        outcomes.put(name, new AcceptanceReport.Case(name, AcceptanceReport.State.PASSED, detail));
        writeReport();
        journal.event("checkpoint", "completed", name, "OBSERVED", detail);
        LOGGER.info("LAB_ACCEPTANCE_PROGRESS:{}:{}:{}", scenarioId, checkpoint, detail);
    }

    private Check progressCheck(String name, String checkpoint) {
        return new Check() {
            public String name() { return name; }
            public CheckResult run(MageHomeRuntime runtime) {
                return progress.contains(checkpoint) ? CheckResult.ok("observed " + checkpoint)
                        : CheckResult.failed("not observed: " + checkpoint + "; " + runtime.statusJson());
            }
        };
    }

    /**
     * Reads the relation of the task chain of UnionClef in every tick after the resume.
     *
     * <p>The relation and the task that owns the chain are written into the log with the number
     * of the tick, and the tick on which the chain holds the own root task of the add-on is
     * remembered. The observation is therefore not a single snapshot.
     */
    private void sampleTaskChain(MageHomeRuntime runtime) {
        TaskOwnership.Relation current = runtime.taskChainRelation();
        String relation = String.valueOf(current);
        Task owner = runtime.currentTaskObject();
        String task = owner == null ? null : owner.getClass().getName();
        String name = task == null ? "none" : task;
        String previousRelation = observedRelation;
        // The relation of every tick is kept: the judgement about the ownership of the chain
        // needs the whole sequence and not a single snapshot.
        observedRelations.add(current);
        observedSamples.add(new TaskChainOwnership.Sample(current, ownerState(owner)));
        if (!relation.equals(observedRelation) || !name.equals(observedTask)) {
            LOGGER.info("LAB_ACCEPTANCE_OBSERVE task_chain tick={} current={} relation={}",
                    clientTicks, name, relation);
        }
        if (clientTicks % REPORT_INTERVAL_TICKS == 0) {
            // The whole sequence of the relations is written into the log, so that a run can be
            // read without a debugger and the change of the owner can be followed tick by tick.
            LOGGER.info("LAB_ACCEPTANCE_OBSERVE task_chain tick={} the whole sequence of the states of the chain is {}",
                    clientTicks, observedSamples);
        }
        if ("OWN_ROOT".equals(relation) && "FOREIGN".equals(previousRelation) && !emptyChainSinceForeign) {
            // The owner of the chain changed from another owner to the own root task without an
            // empty chain in between. The chain of UnionClef changes inside its own task tick, so
            // this is not a failure of the add-on: the record of the calls says whether the
            // add-on started its own task then, and whether the task of the other owner had
            // already ended (ТЗ.md section 5).
            LOGGER.info("LAB_ACCEPTANCE_OBSERVE task_chain tick={} the owner of the chain changed from "
                    + "FOREIGN to OWN_ROOT without an empty chain in between; the calls of the add-on were {}",
                    clientTicks, runtime.chainActions());
        }
        observedRelation = relation;
        observedTask = name;
        if ("NONE".equals(relation)) {
            emptyChainSinceForeign = true;
        }
        if ("FOREIGN".equals(relation)) {
            emptyChainSinceForeign = false;
            foreignRelationSeen = true;
            if (foreignRelationTick < 0L) {
                // The chain of UnionClef belongs to the task of another owner. The add-on must
                // not take such a chain away from its owner (ТЗ.md section 5), so the tick and
                // the name of the task are remembered for the check of the scenario.
                foreignRelationTick = clientTicks;
                foreignRelationTask = name;
                LOGGER.warn("LAB_ACCEPTANCE_OBSERVE task_chain tick={} current={} relation=FOREIGN: "
                        + "the chain of UnionClef belongs to another owner", clientTicks, name);
            }
            if (foreignTask == null && owner != null) {
                // The task of the other owner is kept, so that its life can be followed inside
                // the window: the identity of the task is the fact, not a name of a class.
                foreignTask = owner;
            }
        }
        if ("OWN_ROOT".equals(relation) && ownRootTick < 0L) {
            ownRootTick = clientTicks;
            ownRootTask = name;
            LOGGER.info("LAB_ACCEPTANCE_OBSERVE task_chain tick={} the chain of UnionClef holds the own root task",
                    clientTicks);
        }
    }

    /**
     * The state of the task that owns the chain of UnionClef.
     *
     * <p>The state comes from the real UnionClef: {@code Task.stopped()} is true only after
     * {@code Task.stop(..)} was called, and {@code Task.isFinished()} is the decision of the task
     * itself that it is done.
     */
    private TaskChainOwnership.OwnerState ownerState(Task owner) {
        if (owner == null) {
            return TaskChainOwnership.OwnerState.UNKNOWN;
        }
        try {
            if (owner.stopped()) {
                return TaskChainOwnership.OwnerState.STOPPED;
            }
            if (owner.isFinished()) {
                return TaskChainOwnership.OwnerState.FINISHED;
            }
        } catch (Throwable t) {
            return TaskChainOwnership.OwnerState.UNKNOWN;
        }
        return TaskChainOwnership.OwnerState.ALIVE;
    }

    /**
     * The way that the player walked during the scenario.
     *
     * <p>The place of the player is read in every tick of the client. The sum of the steps is the
     * length of the way, the longest step shows whether the player was moved at once instead of
     * walking. The add-on never moves the player directly, so a long step would mean that the
     * house was not built by the player.
     */
    private void observePlayerPath() {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.player == null) {
            return;
        }
        BlockPos now = client.player.getBlockPos();
        if (lastPlace != null) {
            double step = Math.sqrt(lastPlace.getSquaredDistance(now));
            walkedDistance += step;
            longestStep = Math.max(longestStep, step);
        }
        lastPlace = now;
    }

    /**
     * The blocks of the house are the result of ordinary interactions of the player.
     *
     * <p>The add-on never writes into the world directly and never teleports the player: every
     * block of the house is the result of an ordinary interaction of the player, who walks to the
     * cell before every placement.
     *
     * <p>The check reports the measured distribution of the run (the number of interactions, the
     * longest reach, the length of the way and the longest step of a tick) in its detail, so that
     * the thresholds can be read against the facts of the game and not against a guess.
     */
    private Check physicalPlacementCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                List<PlacementExecutor.Interaction> facts = runtime.placement().interactions();
                if (facts.isEmpty()) {
                    return CheckResult.failed("no interaction with the world was sent");
                }
                MinecraftClient client = MinecraftClient.getInstance();
                if (client.player == null) {
                    return CheckResult.failed("the player is not in the world");
                }
                if (playerStart != null && playerStart.equals(client.player.getBlockPos())) {
                    return CheckResult.failed("the player did not walk to the house");
                }
                HouseTemplate template = templateOfTheHouse(runtime);
                if (template == null) {
                    return CheckResult.failed("the registry holds no house");
                }
                double longestReach = 0.0;
                for (PlacementExecutor.Interaction fact : facts) {
                    // The block came from an ordinary interaction of the player: he stood in the
                    // world and the clicked block was within his reach.
                    longestReach = Math.max(longestReach, fact.reach());
                    if (fact.reach() > REACH_LIMIT) {
                        return CheckResult.failed("the player interacted from too far away: " + fact
                                + ", the measured distribution is " + distribution(facts, longestReach));
                    }
                    // The item of the project was in the hand of the player.
                    if (!fact.itemId().equals(fact.heldItemId())) {
                        return CheckResult.failed("the player held another item than the project wants: " + fact
                                + ", the measured distribution is " + distribution(facts, longestReach));
                    }
                    // The item was the one that the project prescribes for the cell.
                    if (fact.cell() == null) {
                        return CheckResult.failed("the interaction does not belong to a cell: " + fact
                                + ", the measured distribution is " + distribution(facts, longestReach));
                    }
                    CellSpec spec = template.spec(fact.cell());
                    if (spec == null || !spec.itemId().equals(fact.itemId())) {
                        return CheckResult.failed("the item does not belong to the cell of the house: " + fact
                                + ", the measured distribution is " + distribution(facts, longestReach));
                    }
                }
                // The player walked to the cells: the way covers the distance to the first cell of
                // the house and no step was a jump.
                double needed = distanceToTheFirstCell(facts);
                if (walkedDistance + 0.5 < needed) {
                    return CheckResult.failed("the player did not walk to the house: the way is "
                            + round(walkedDistance) + " blocks, the first cell is " + round(needed)
                            + " blocks away, the measured distribution is "
                            + distribution(facts, longestReach));
                }
                if (longestStep > LONGEST_STEP) {
                    return CheckResult.failed("the player was moved at once instead of walking: " + longestStep
                            + " blocks in one tick, the measured distribution is "
                            + distribution(facts, longestReach));
                }
                return CheckResult.ok(facts.size() + " interactions were sent by the player, "
                        + distribution(facts, longestReach));
            }
        };
    }

    /**
     * The attempts of the placement obey the rule of the arrival.
     *
     * <p>The check reads the facts of the interactions that the player sent: the player stood in
     * the cell of the approach itself, the player did not stand in the cell that is filled, the
     * point of the click was inside the ordinary range of the player and the item in the hand was
     * the item of the project. A click that leaves the cell of the approach or that leaves the
     * cell of the project is refused by the world, so the rule is a condition of the product, not
     * a detail of the walk (ТЗ.md section 4).
     */
    private Check attemptFactsCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                List<PlacementExecutor.Interaction> facts = runtime.placement().interactions();
                if (facts.isEmpty()) {
                    return CheckResult.unverified("no attempt of the placement was observed in this run");
                }
                for (PlacementExecutor.Interaction fact : facts) {
                    if (fact.access() == null) {
                        return CheckResult.failed("the attempt does not name the cell of the approach: " + fact);
                    }
                    if (!fact.player().equals(fact.access())) {
                        return CheckResult.failed("the player did not stand in the cell of the approach: "
                                + fact + ", the cell of the approach is " + fact.access().toShortString());
                    }
                    if (fact.target() != null && fact.player().equals(fact.target())) {
                        return CheckResult.failed("the interaction was sent from the cell that is to be "
                                + "filled: " + fact);
                    }
                    if (fact.reach() > REACH_LIMIT) {
                        return CheckResult.failed("the player interacted from too far away: " + fact);
                    }
                    if (!fact.itemId().equals(fact.heldItemId())) {
                        return CheckResult.failed("the player held another item than the project wants: " + fact);
                    }
                }
                return CheckResult.ok(facts.size() + " attempts were sent from the cell of the approach, "
                        + "none of them from the cell that is to be filled");
            }
        };
    }

    /**
     * The aim of the player is observed before the click.
     *
     * <p>The check reads the interactions that the player really sent: every one of them was sent
     * when the crosshair of the game named the support of the operation and the game would place
     * the state of the project with that hit result (aim_verdict=SUPPORT). A run in which the
     * view never named the support is therefore read and not guessed.
     */
    private Check aimBeforeClickCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                List<PlacementExecutor.Interaction> facts = runtime.placement().interactions();
                if (facts.isEmpty()) {
                    return CheckResult.unverified("no attempt of the placement was observed in this run");
                }
                for (PlacementExecutor.Interaction fact : facts) {
                    if (!"SUPPORT".equals(fact.aimVerdict())) {
                        return CheckResult.failed("the interaction was not sent with the support of the "
                                + "operation under the crosshair of the game: " + fact);
                    }
                }
                return CheckResult.ok(facts.size() + " interactions were sent with the support of the "
                        + "operation under the crosshair of the game");
            }
        };
    }

    /**
     * The click is sent with the hit result that the game computes.
     *
     * <p>The check reads the interactions that the player really sent: every one of them names
     * the block that was clicked and the cell that the interaction fills, and the cell lies next
     * to the clicked block — the block of the project was placed against the block under the
     * crosshair of the game and not into another cell.
     */
    private Check gameHitResultCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                List<PlacementExecutor.Interaction> facts = runtime.placement().interactions();
                if (facts.isEmpty()) {
                    return CheckResult.unverified("no attempt of the placement was observed in this run");
                }
                for (PlacementExecutor.Interaction fact : facts) {
                    if (fact.support() == null || fact.target() == null) {
                        return CheckResult.failed("the interaction does not name the clicked block and the "
                                + "cell that it fills: " + fact);
                    }
                    if (!nextTo(fact.support(), fact.target())) {
                        return CheckResult.failed("the cell that the interaction fills is not the cell next "
                                + "to the clicked block: " + fact);
                    }
                }
                return CheckResult.ok(facts.size() + " interactions filled the cell next to the block that "
                        + "was clicked");
            }
        };
    }

    /**
     * True when the cell lies next to the other cell.
     */
    private static boolean nextTo(BlockPos support, BlockPos target) {
        return Math.abs(support.getX() - target.getX()) + Math.abs(support.getY() - target.getY())
                + Math.abs(support.getZ() - target.getZ()) == 1;
    }

    /**
     * The preconditions of the ordinary placement held before every click.
     *
     * <p>The check reads the interactions that the player really sent: every one of them names
     * the first precondition of the ordinary placement that did not hold, and that name is
     * "none" — the item was in the main hand, the game knew the features of the item, the cell
     * could be filled, the state of the project stood in the cell of the project and the point of
     * the click was inside the ordinary range of the player.
     */
    private Check placementPreconditionsCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                List<PlacementExecutor.Interaction> facts = runtime.placement().interactions();
                if (facts.isEmpty()) {
                    return CheckResult.unverified("no attempt of the placement was observed in this run");
                }
                for (PlacementExecutor.Interaction fact : facts) {
                    if (!"none".equals(fact.precondition())) {
                        return CheckResult.failed("the interaction was sent although a precondition of the "
                                + "ordinary placement did not hold: " + fact);
                    }
                }
                return CheckResult.ok(facts.size() + " interactions were sent after every precondition of "
                        + "the ordinary placement held");
            }
        };
    }

    /**
     * The facts of the attempt name the diagnostics of the click.
     *
     * <p>The check reads the facts that the client published for its last attempt: they name the
     * block and the face under the crosshair, the point of the crosshair, the verdict of the aim,
     * the slot of the screen handler with the item of the project, the place and the view of the
     * player on the client and on the server, the first precondition that did not hold and the
     * states of the cell of the project and of the cells around it.
     */
    private Check attemptDiagnosticsCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                String facts = runtime.placement().lastFacts();
                if (facts == null || facts.isBlank() || "none".equals(facts)) {
                    return CheckResult.unverified("no facts of an attempt were published in this run");
                }
                for (String key : List.of("aim_block=", "aim_face=", "aim_vector=", "aim_verdict=",
                        "slot=", "slot_item=", "client=", "server=", "precondition=", "neighbours=")) {
                    if (!facts.contains(key)) {
                        return CheckResult.failed("the facts of the attempt do not name " + key + ": " + facts);
                    }
                }
                return CheckResult.ok("the facts of the attempt name the view, the slot, the images of the "
                        + "player and the cells around the cell of the project");
            }
        };
    }

    /**
     * The delivery of the materials is confirmed by the facts of the delivery.
     *
     * <p>The check reads the observations of the automation: the command of the delivery left the
     * client without a leading slash (the ordinary network API of the chat), it asked for the
     * items of the deficit in commands of at most 64 items, and the inventory of the player grew
     * by the items that were asked for. The marker "the inventory is not empty" proves nothing
     * about the delivery, so it is not used here (ТЗ.md section 4).
     *
     * <p>The sum of the asked items is not compared with the whole deficit: a run that ends while
     * the delivery is still running asked for less than the deficit, and that is not a failure.
     */
    private Check giveFactsCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                List<String> observations = runtime.context().observations();
                Map<String, Integer> asked = new LinkedHashMap<>();
                Map<String, Integer> deficit = new LinkedHashMap<>();
                Map<String, Integer> lastAsked = new LinkedHashMap<>();
                for (String observation : observations) {
                    if (observation.startsWith("give_sent ")) {
                        String item = valueOf(observation, "item=");
                        String command = valueOf(observation, "command=");
                        Integer count = countOf(command);
                        Integer missing = numberOf(observation, "deficit=");
                        if (item == null || command == null || count == null || missing == null) {
                            return CheckResult.failed("the observation of the delivery is not readable: "
                                    + observation);
                        }
                        if (command.startsWith("/")) {
                            return CheckResult.failed("the command of the delivery has a leading slash: "
                                    + observation);
                        }
                        if (!command.startsWith("give @s " + item + " ")) {
                            return CheckResult.failed("the command of the delivery is not the command of the "
                                    + "project: " + observation);
                        }
                        if (count <= 0 || count > 64) {
                            return CheckResult.failed("the command of the delivery asks for no items or for "
                                    + "too many: " + observation);
                        }
                        if (count > missing) {
                            return CheckResult.failed("the command of the delivery asks for more than the "
                                    + "deficit: " + observation);
                        }
                        asked.merge(item, count, Integer::sum);
                        deficit.putIfAbsent(item, missing);
                        lastAsked.put(item, count);
                    } else if (observation.startsWith("give_confirmed ")) {
                        String item = valueOf(observation, "item=");
                        Integer before = numberOf(observation, "inventory_before=");
                        Integer now = numberOf(observation, "inventory_now=");
                        Integer consumedBefore = numberOf(observation, "consumed_before=");
                        Integer consumedNow = numberOf(observation, "consumed_now=");
                        Integer delivered = numberOf(observation, "delivered=");
                        if (item == null || before == null || now == null
                                || consumedBefore == null || consumedNow == null || delivered == null) {
                            return CheckResult.failed("the confirmation of the delivery is not readable: "
                                    + observation);
                        }
                        int requested = lastAsked.getOrDefault(item, 0);
                        if (requested == 0) {
                            return CheckResult.failed("the delivery was confirmed without a command that "
                                    + "was sent: " + observation);
                        }
                        // The delivery is confirmed by the inventory. The material that the add-on
                        // spent in the same time (the blocks of the project and the temporary
                        // supports of the climb) leaves the inventory as well, so the count of the
                        // inventory alone does not show the delivery; the spending of the add-on is
                        // counted back here, and the delivery has to be visible in the sum.
                        long growth = (long) now - before + (long) consumedNow - consumedBefore;
                        if (growth < requested) {
                            return CheckResult.failed("the inventory did not grow by the items that were "
                                    + "asked for: " + observation + ", the last command asked for " + requested
                                    + ", the inventory grew by " + growth
                                    + " (the add-on spent " + (consumedNow - consumedBefore) + " items itself)");
                        }
                        if (delivered != growth) {
                            return CheckResult.failed("the confirmed number of the delivered items does not "
                                    + "match the inventory: " + observation);
                        }
                        int sum = asked.getOrDefault(item, 0);
                        int missing = deficit.getOrDefault(item, 0);
                        if (sum > missing) {
                            return CheckResult.failed("the delivery asked for more items than the deficit: "
                                    + observation + ", the asked number of items is " + sum
                                    + ", the deficit is " + missing);
                        }
                        return CheckResult.ok("the command of the delivery left the client without a slash, "
                                + "it asked for " + sum + " items of " + item + " (deficit " + missing
                                + "), and the inventory grew by " + growth + " items");
                    }
                }
                return CheckResult.unverified("the delivery of the materials was not confirmed in this run");
            }
        };
    }

    /** The value of a field of an observation of the automation. */
    private static String valueOf(String observation, String key) {
        int start = observation.indexOf(key);
        if (start < 0) {
            return null;
        }
        start += key.length();
        int end = observation.indexOf(", ", start);
        return end < 0 ? observation.substring(start) : observation.substring(start, end);
    }

    /** The number of a field of an observation of the automation. */
    private static Integer numberOf(String observation, String key) {
        String value = valueOf(observation, key);
        if (value == null) {
            return null;
        }
        try {
            return Integer.valueOf(value.trim());
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /** The number of items that a command of the delivery asks for. */
    private static Integer countOf(String command) {
        if (command == null) {
            return null;
        }
        String[] parts = command.trim().split(" ");
        if (parts.length == 0) {
            return null;
        }
        try {
            return Integer.valueOf(parts[parts.length - 1]);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /**
     * The measured distribution of the run.
     *
     * <p>The numbers are the facts of the game: the number of interactions, the longest reach,
     * the length of the way that the player walked and the longest step of a tick.
     */
    private String distribution(List<PlacementExecutor.Interaction> facts, double longestReach) {
        return "the measured distribution is " + facts.size() + " interactions, the longest reach is "
                + round(longestReach) + " blocks, the way is " + round(walkedDistance)
                + " blocks, the longest step of a tick is " + round(longestStep) + " blocks";
    }

    /** The distance between the place of the player and the first cell he interacted with. */
    private double distanceToTheFirstCell(List<PlacementExecutor.Interaction> facts) {
        if (playerStart == null || facts.isEmpty()) {
            return 0.0;
        }
        BlockPos first = facts.get(0).player();
        if (first == null) {
            return 0.0;
        }
        return Math.sqrt(first.getSquaredDistance(playerStart));
    }

    /** A number with two digits after the point. */
    private static double round(double value) {
        return Math.round(value * 100.0) / 100.0;
    }

    /** The project of the house that the scenario builds. */
    private HouseTemplate templateOfTheHouse(MageHomeRuntime runtime) {
        List<HouseRecord> houses = runtime.registryService().registry().houses();
        if (houses.isEmpty()) {
            return null;
        }
        return HouseTemplate.forVariant(houses.get(0).variant());
    }

    /**
     * The stand of the scenario: a loaded world, a living player and the mod of UnionClef.
     *
     * <p>The detail of the check names every fact of the stand on its own, so that a run that
     * could not be verified can be read instead of guessed at: the world, the player, the uuid of
     * the player, the mod of UnionClef, the task chain of UnionClef and the slot handler of
     * UnionClef.
     */
    private Check standReady(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                MinecraftClient client = MinecraftClient.getInstance();
                UnionClefBridge bridge = new UnionClefBridge();
                StandRules.Readiness readiness = standReadiness(runtime);
                String facts = "world=" + (client.world != null)
                        + ", player=" + (runtime.context().playerAlive() ? "alive" : "not alive")
                        + ", uuid=" + (runtime.context().playerUuid().isEmpty() ? "unknown" : "known")
                        + ", unionclef=" + (bridge.available() ? "loaded" : "not loaded")
                        + ", chain=" + (bridge.chainAvailable() ? "there" : "not there")
                        + ", slot_handler=" + (bridge.slotHandlerAvailable() ? "there" : "not there")
                        + ", the task chain of UnionClef appeared at tick "
                        + labStand.dependencyReadyTick()
                        + ", the player was at " + labStand.playerBeforeDependencyReady()
                        + " before it and at " + labStand.playerAfterDependencyReady() + " after it"
                        + ", the chain of UnionClef held the task " + labStand.chainTaskAfterDependencyReady()
                        + " right after the initialization (read: " + labStand.chainKnownAfterDependencyReady() + ")";
                if (!readiness.ready()) {
                    return CheckResult.failed(readiness.description() + " (" + facts + ")");
                }
                if (labStand.chainReplaced()) {
                    // The mod of UnionClef was initialized a second time: the stand is not the
                    // environment of the specification then, and nothing of the product can be
                    // read in that run.
                    return CheckResult.failed("the mod of UnionClef was initialized a second time: the chain of "
                            + "UnionClef was replaced after it had been created (" + facts + ")");
                }
                if (labStand.playerMovedByTheInitialization()) {
                    // The initialization of UnionClef moved the player: the preparation of the
                    // stand disturbed the world and the scenario would not show the product.
                    return CheckResult.failed("the initialization of UnionClef moved the player: from "
                            + labStand.playerBeforeDependencyReady() + " to " + labStand.playerAfterDependencyReady()
                            + " (" + facts + ")");
                }
                return CheckResult.ok(readiness.description() + " (" + facts + ")");
            }
        };
    }

    /**
     * The client was alive at the end of the observation window of the diagnostic scenario.
     *
     * <p>The case exists only when the client reached the end of the window: a crash of the
     * client leaves the report of the run as it was before the crash, and the runner sees the
     * missing case together with the crash report of the game. The case therefore says nothing
     * about the product: it names the fact that the client of the run stayed alive.
     */
    private Check clientSurvivedCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                MinecraftClient client = MinecraftClient.getInstance();
                return CheckResult.ok("the client was alive after the observation window of "
                        + DIAGNOSTIC_WINDOW_MILLIS + " ms: the world is loaded="
                        + (client.world != null) + ", the player is alive="
                        + runtime.context().playerAlive() + ", the ticks of the client="
                        + clientTicks);
            }
        };
    }

    /**
     * One fact of the dependency of the stand.
     *
     * <p>The lifecycle of the dependency belongs to the platform: its own preparation lets
     * Minecraft show the title screen, lets the own mixin of UnionClef initialize the dependency
     * and observes the real task chain and the real slot handler before the world is opened. The
     * fact that is read here therefore belongs to the environment and not to the product: when it
     * does not hold, the situation of the scenario was not shown, the case stays unverified with
     * the reason, and it is never a failure of the add-on (ТЗ.md section 8).
     *
     * @param name         the name of the case
     * @param slotHandler  true for the slot handler, false for the task chain
     */
    private Check dependencyFactCheck(String name, boolean slotHandler) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                UnionClefBridge bridge = new UnionClefBridge();
                String what = slotHandler ? "the slot handler of UnionClef" : "the task chain of UnionClef";
                boolean holds = slotHandler ? bridge.slotHandlerAvailable() : bridge.chainAvailable();
                if (holds) {
                    return CheckResult.ok(what + " exists: the mod of UnionClef is "
                            + (bridge.available() ? "loaded" : "not loaded")
                            + ", the chain was created at tick " + labStand.dependencyReadyTick()
                            + ", the chain holds the task " + labStand.chainTaskAfterDependencyReady());
                }
                return CheckResult.unverified(what + " does not exist: the stand of the laboratory "
                        + "cannot show the situation of the scenario, and the initialization of the "
                        + "dependency belongs to the platform (" + CHAIN_HINT + ")");
            }
        };
    }

    /**
     * The add-on did not act while its automation was disabled.
     *
     * <p>This is the one assertion of the diagnostic scenario that belongs to the product: the
     * automation is disabled by default (ТЗ.md section 4), and the add-on may not start a task,
     * may not write a record of a house and may not touch the world while it is disabled. The
     * number of the records of the registry is compared with the number at the beginning of the
     * window: the records of an earlier run of the laboratory are not the own doing of the
     * diagnostic scenario.
     */
    private Check noProductActionCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                boolean enabled = runtime.controller().enabled();
                int houses = runtime.registryService().registry().houses().size();
                boolean ownRootOwnsTheChain = runtime.currentTaskObject() == runtime.ownRootTask();
                int interactions = runtime.placement().totalInteractions();
                boolean housesChanged = diagnosticHousesAtStart >= 0 && houses != diagnosticHousesAtStart;
                if (enabled || housesChanged || ownRootOwnsTheChain || interactions > 0) {
                    return CheckResult.failed("the add-on acted although its automation is disabled: enabled="
                            + enabled + ", the records of the registry went from " + diagnosticHousesAtStart
                            + " to " + houses + ", the own root task owns the chain of UnionClef="
                            + ownRootOwnsTheChain + ", the interactions with the world=" + interactions);
                }
                return CheckResult.ok("the automation of the add-on stayed disabled and the add-on did nothing: "
                        + "enabled=false, the records of the registry stayed at " + houses
                        + ", the own root task does not own the chain of UnionClef, the interactions with the world=0");
            }
        };
    }

    /** The inventory of the player is empty at the beginning of the scenario. */
    private Check emptyInventory(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                Map<String, Integer> items = items(runtime);
                if (!StandRules.inventoryEmpty(items)) {
                    return CheckResult.failed("the inventory is not empty: " + items);
                }
                return CheckResult.ok("the inventory of the player is empty");
            }
        };
    }

    /** The command tree of the server does not contain the command {@code give}. */
    private Check giveNotAvailable(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                GiveAvailability availability = runtime.context().giveCommandState();
                if (availability.unknown()) {
                    // A tree that was not read yet is not a tree without give: the stand does not
                    // show the situation of the scenario.
                    return CheckResult.failed(availability.description());
                }
                if (StandRules.giveAvailable(availability)) {
                    return CheckResult.failed("the command tree of the server contains give");
                }
                return CheckResult.ok("the command tree of the server has no give");
            }
        };
    }

    /**
     * The preparation of the stand removed the command give from the tree of the server.
     *
     * <p>The single player server always offers the command give. The scenario of the refused
     * delivery needs a tree that was read and that holds no give, so the preparation of the
     * stand removes that node and writes it into the log. The setup check names the fact, so
     * that the observation of the product cannot be read as a fact about the server.
     */
    private Check giveRemovedByTheStand(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                GiveAvailability availability = runtime.context().giveCommandState();
                if (availability.unknown()) {
                    // A tree that was not read yet is not a tree without give: the stand does not
                    // show the situation of the scenario.
                    return CheckResult.failed(availability.description());
                }
                if (!labStand.giveRemoved()) {
                    return CheckResult.failed("the preparation of the stand did not remove the command give "
                            + "from the tree of the server");
                }
                return CheckResult.ok("the preparation of the stand removed the command give from the tree of "
                        + "the server at tick " + labStand.giveRemovedTick()
                        + ", the tree of the server holds no give");
            }
        };
    }

    /**
     * The laboratory prepared a platform that the add-on can build on.
     *
     * <p>The preparation of the world belongs to the laboratory and not to the add-on: the
     * add-on never changes the world of the stand. When the platform is missing, the scenario
     * is not verified: the automation would look for a site that the laboratory did not
     * prepare, and the reason of the automation would not be the one of the scenario.
     */
    private Check platformPrepared(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                PlatformFixture.Result result = PlatformFixture.inspect(runtime.context().world(),
                        runtime.context().playerPosition());
                if (result.kind() != PlatformFixture.Kind.BUILDABLE) {
                    return CheckResult.failed("the laboratory prepared no platform that the add-on can build on: "
                            + result.detail());
                }
                return CheckResult.ok("the laboratory prepared a platform that the add-on can build on: "
                        + result.detail());
            }
        };
    }

    /**
     * The laboratory prepared the house of the scenario of the repair.
     *
     * <p>The fact is read from the world of the server: the wrong block stands in the cell of the
     * volume of the registered house, the rest of the floor carries the block of the project, the
     * lone half of the door stands in its cell and the support under the house is there. The
     * preparation is confirmed before the add-on acts; a broken preparation proves nothing
     * about the product.
     */
    private Check repairHousePrepared(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                String fact = fixture.repairFact();
                if (fact == null || fact.isBlank() || "none".equals(fact)) {
                    return CheckResult.failed("the preparation of the house of the repair was not observed");
                }
                String wrong = LabScenarioFixture.repairWrongBlock();
                if (!fact.contains("wrong_block=" + wrong)) {
                    return CheckResult.failed("the wrong block of the house is not prepared: " + fact);
                }
                if (!fact.contains("lone_half=" + doorBlockId())) {
                    return CheckResult.failed("the lone half of the door is not prepared: " + fact);
                }
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                var observed = runtime.context().world().read(repairPosition(record));
                if (!wrong.equals(observed.blockId())) {
                    return CheckResult.failed("the wrong block is not in the world of the client: " + observed);
                }
                Cell lone = LabScenarioFixture.LONE_HALF_CELL;
                var half = runtime.context().world().read(record.origin().add(lone.x(), lone.y(), lone.z()));
                if (!doorBlockId().equals(half.blockId())) {
                    return CheckResult.failed("the lone half of the door is not in the world of the client: "
                            + half);
                }
                return CheckResult.ok("the house of the repair is prepared: " + fact);
            }
        };
    }

    /**
     * The laboratory prepared the house of the scenario of the foreign block.
     *
     * <p>The fact is read from the world of the server: a tree and a crop stand in the volume of
     * the registered house, the rest of the floor carries the block of the project. The
     * preparation is confirmed before the add-on acts; a broken preparation proves nothing about
     * the product.
     */
    private Check foreignHousePrepared(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                String fact = fixture.repairFact();
                if (fact == null || fact.isBlank() || "none".equals(fact)) {
                    return CheckResult.failed("the preparation of the house of the foreign block "
                            + "was not observed");
                }
                if (!fact.contains("foreign_log=" + LabScenarioFixture.foreignLogBlock())) {
                    return CheckResult.failed("the foreign block of the house is not prepared: " + fact);
                }
                if (!fact.contains("foreign_earth=" + LabScenarioFixture.foreignEarthBlock())) {
                    return CheckResult.failed("the second foreign block of the house is not prepared: " + fact);
                }
                if (!fact.contains("foreign_container=" + LabScenarioFixture.foreignContainerBlock())) {
                    return CheckResult.failed("the protected foreign block of the house is not prepared: " + fact);
                }
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                var log = runtime.context().world().read(foreignPosition(record, LabScenarioFixture.FOREIGN_LOG_CELL));
                if (!LabScenarioFixture.foreignLogBlock().equals(log.blockId())) {
                    return CheckResult.failed("the foreign block is not in the world of the client: " + log);
                }
                var earth = runtime.context().world().read(foreignPosition(record, LabScenarioFixture.FOREIGN_EARTH_CELL));
                if (!LabScenarioFixture.foreignEarthBlock().equals(earth.blockId())) {
                    return CheckResult.failed("the second foreign block is not in the world of the client: "
                            + earth);
                }
                var container = runtime.context().world().read(
                        foreignPosition(record, LabScenarioFixture.FOREIGN_CONTAINER_CELL));
                if (!LabScenarioFixture.foreignContainerBlock().equals(container.blockId())) {
                    return CheckResult.failed("the protected foreign block is not in the world of the client: "
                            + container);
                }
                return CheckResult.ok("the house of the foreign block is prepared: " + fact);
            }
        };
    }

    /** The block of the project that the lone half of the door belongs to. */
    private String doorBlockId() {
        return HouseTemplate.forVariant(0).spec(HouseTemplate.DOOR_LOWER).blockId();
    }

    /** The position of a cell of the volume of the house of the scenario. */
    private Vec3i foreignPosition(HouseRecord record, Cell cell) {
        return record.origin().add(cell.x(), cell.y(), cell.z());
    }

    /**
     * True when one of the events of the repair names the given position.
     *
     * <p>The events of the repair name the position of the cell as the game writes it
     * ({@code BlockPos.toShortString}); the comparison ignores the spaces, so that the fact of the
     * event is read and not its formatting.
     *
     * @param events   the events of the repair
     * @param prefix   the prefix of the event
     * @param position the position of the cell
     * @return true when an event names the position
     */
    private static boolean repairNames(List<String> events, String prefix, Vec3i position) {
        String wanted = (position.x() + "," + position.y() + "," + position.z());
        for (String event : events) {
            if (!event.startsWith(prefix)) {
                continue;
            }
            String coordinates = event.substring(prefix.length()).replace(" ", "");
            if (coordinates.equals(wanted)) {
                return true;
            }
        }
        return false;
    }

    /** The wrong block of the cell is gone: it was broken with an ordinary attack of the player. */
    private Check wrongBlockRemovedCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                var observed = runtime.context().world().read(repairPosition(record));
                String wrong = LabScenarioFixture.repairWrongBlock();
                if (wrong.equals(observed.blockId())) {
                    return CheckResult.failed("the wrong block is still in the cell: " + observed);
                }
                List<String> events = runtime.placement().repairEvents();
                boolean attacked = events.stream().anyMatch(event -> event.startsWith("repair_attack:"));
                boolean finished = events.stream().anyMatch(event -> event.startsWith("repair_finished:"));
                if (!attacked || !finished) {
                    return CheckResult.failed("the client did not break the wrong block with an ordinary "
                            + "attack of the player: " + events);
                }
                return CheckResult.ok("the wrong block is gone, the client broke it with an ordinary attack: "
                        + events);
            }
        };
    }

    /**
     * The lone half of the door was not covered by a new installation.
     *
     * <p>The half is eliminated by the safe repair: the client broke it with an ordinary attack
     * and the cell was read again. Afterwards the cell is empty or the pair of the door stands
     * again, but never a half that stands alone over a new installation.
     */
    private Check loneHalfNotOverwrittenCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                Cell cell = LabScenarioFixture.LONE_HALF_CELL;
                Vec3i position = record.origin().add(cell.x(), cell.y(), cell.z());
                List<String> events = runtime.placement().repairEvents();
                boolean repaired = repairNames(events, "repair_requested:", position);
                if (!repaired) {
                    return CheckResult.failed("the lone half of the door was not eliminated by the safe "
                            + "repair: " + events);
                }
                var lower = runtime.context().world().read(position);
                if (lower.isAir()) {
                    return CheckResult.ok("the lone half of the door was eliminated by the safe repair "
                            + "and the cell is empty: " + events);
                }
                Cell upper = new Cell(cell.x(), cell.y() + 1, cell.z());
                var upperBlock = runtime.context().world().read(record.origin().add(upper.x(), upper.y(), upper.z()));
                if ("upper".equals(upperBlock.property("half"))) {
                    return CheckResult.ok("the lone half of the door was eliminated by the safe repair and "
                            + "the pair of the door stands again: " + events);
                }
                return CheckResult.failed("the lone half of the door still stands alone: " + lower);
            }
        };
    }

    /**
     * The foreign blocks of the volume of the house were not touched.
     *
     * <p>The tree and the crop stand in the volume of the registered house: the add-on never
     * breaks a block that the project does not place, and it does not even ask for the removal
     * of such a block.
     */
    /**
     * The admissible foreign blocks inside the volume are removed by the safe repair.
     *
     * <p>The owner allowed a limited repair inside a registered house (ТЗ.md A.3): a wrong block
     * that stands in a cell of the volume is removed with an ordinary attack of the player, the
     * cell is read again and the building continues. The block of the project that the cell
     * wants is placed there afterwards.
     */
    private Check foreignBlockRepairedCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                List<String> events = runtime.placement().repairEvents();
                List<String> unchanged = new ArrayList<>();
                List<String> repaired = new ArrayList<>();
                for (Cell cell : List.of(LabScenarioFixture.FOREIGN_LOG_CELL,
                        LabScenarioFixture.FOREIGN_EARTH_CELL)) {
                    Vec3i position = record.origin().add(cell.x(), cell.y(), cell.z());
                    var observed = runtime.context().world().read(position);
                    String foreign = LabScenarioFixture.FOREIGN_LOG_CELL.equals(cell)
                            ? LabScenarioFixture.foreignLogBlock() : LabScenarioFixture.foreignEarthBlock();
                    if (foreign.equals(observed.blockId())) {
                        unchanged.add(cell + "=" + observed.blockId());
                        continue;
                    }
                    if (!repairNames(events, "repair_requested:", position)
                            && !repairNames(events, "repair_finished:", position)) {
                        unchanged.add(cell + "=" + observed.blockId() + " (no repair was observed)");
                        continue;
                    }
                    repaired.add(cell + "=" + observed.blockId());
                }
                if (!unchanged.isEmpty()) {
                    return CheckResult.failed("the admissible foreign blocks were not repaired: " + unchanged
                            + "; the events of the repair: " + events);
                }
                return CheckResult.ok("the admissible foreign blocks of the house were repaired: " + repaired
                        + "; the events of the repair: " + events);
            }
        };
    }

    /**
     * The protected foreign block inside the volume is not touched.
     *
     * <p>A container holds something and the client never reads its content, so the rule of the
     * safe repair protects it (ТЗ.md section 4). The house waits for the player instead of
     * destroying the content of a container.
     */
    private Check protectedForeignBlockUntouchedCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                Cell cell = LabScenarioFixture.FOREIGN_CONTAINER_CELL;
                Vec3i position = record.origin().add(cell.x(), cell.y(), cell.z());
                var observed = runtime.context().world().read(position);
                if (!LabScenarioFixture.foreignContainerBlock().equals(observed.blockId())) {
                    return CheckResult.failed("the protected foreign block was changed: " + observed);
                }
                List<String> events = runtime.placement().repairEvents();
                if (repairNames(events, "repair_requested:", position)
                        || repairNames(events, "repair_finished:", position)) {
                    return CheckResult.failed("the add-on asked for the removal of the protected foreign "
                            + "block: " + events);
                }
                return CheckResult.ok("the protected foreign block of the house stands untouched: " + observed);
            }
        };
    }

    /**
     * The house is suspended with the reason obstructed, the coordinate and the concrete reason.
     *
     * <p>The record of the house names the coordinate of the protected cell and the reason: the
     * work waits for the player instead of breaking a block that the rule protects.
     */
    private Check houseSuspendedCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                if (!"obstructed".equals(runtime.controller().reason())) {
                    return CheckResult.failed("the house is not suspended with the reason obstructed: "
                            + runtime.controller().reason());
                }
                String reason = record.reason();
                if (reason == null || !reason.startsWith("obstructed@")) {
                    return CheckResult.failed("the record of the house does not name the coordinate and "
                            + "the reason: " + reason);
                }
                Cell cell = LabScenarioFixture.FOREIGN_CONTAINER_CELL;
                Vec3i position = record.origin().add(cell.x(), cell.y(), cell.z());
                String wanted = position.x() + "," + position.y() + "," + position.z();
                if (!reason.contains(wanted)) {
                    return CheckResult.failed("the record of the house names another coordinate than the "
                            + "protected cell: " + reason + "; the protected cell stands at " + wanted);
                }
                if (!reason.contains("a container")) {
                    return CheckResult.failed("the record of the house does not name the concrete reason: "
                            + reason);
                }
                return CheckResult.ok("the house is suspended with the reason obstructed: " + reason);
            }
        };
    }

    /** The repaired cell holds the block that the project wants there. */
    private Check requiredBlockPlacedCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                var observed = runtime.context().world().read(repairPosition(record));
                String wanted = repairWantedBlock();
                if (!wanted.equals(observed.blockId())) {
                    return CheckResult.failed("the cell holds " + observed.blockId() + ", not " + wanted);
                }
                return CheckResult.ok("the cell holds the block of the project: " + wanted);
            }
        };
    }

    /** The building continues after the repair instead of being suspended. */
    private Check buildingContinuedCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                String reason = runtime.controller().reason();
                if ("obstructed".equals(reason)) {
                    return CheckResult.failed("the house was suspended with the reason obstructed: "
                            + record.reason());
                }
                int cells = record.confirmedCells();
                if (cells < 49) {
                    // The cells of the floor that do not correspond to the project are named, so
                    // that the failure of the check says which cell of the world is wrong.
                    HouseTemplate template = HouseTemplate.forVariant(0);
                    List<String> wrong = new ArrayList<>();
                    for (Cell cell : template.volumeCells()) {
                        if (cell.y() != 0 || template.spec(cell) == null) {
                            continue;
                        }
                        var observed = runtime.context().world().read(
                                record.origin().add(cell.x(), cell.y(), cell.z()));
                        if (dev.magehome.core.verification.CellVerifier.verify(template.spec(cell), observed)
                                != dev.magehome.core.verification.CellVerdict.MATCH) {
                            wrong.add(cell + "=" + observed.blockId());
                        }
                    }
                    return CheckResult.failed("the house has only " + cells + " confirmed cells, not the 49 "
                            + "cells of the floor; the cells of the floor that do not correspond to the "
                            + "project: " + wrong);
                }
                return CheckResult.ok("the house continues after the repair: the reason is "
                        + (reason == null ? "none" : reason) + ", the house has " + cells + " confirmed cells");
            }
        };
    }

    /** The protected cells of the house were not touched by the repair. */
    private Check protectedCellsUntouchedCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                HouseTemplate template = HouseTemplate.forVariant(0);
                List<String> changed = new ArrayList<>();
                for (Cell cell : List.of(new Cell(1, 0, 0), new Cell(0, 0, 1), new Cell(1, 0, 1))) {
                    var observed = runtime.context().world().read(record.origin().add(cell.x(), cell.y(), cell.z()));
                    String wanted = template.spec(cell).blockId();
                    if (!wanted.equals(observed.blockId())) {
                        changed.add(cell + "=" + observed.blockId() + " (wanted " + wanted + ")");
                    }
                }
                // The support under the house is not touched either.
                var support = runtime.context().world().read(record.origin().add(0, -1, 0));
                if (support.isAir() || !support.solidFull()) {
                    changed.add("support=" + support.blockId());
                }
                if (!changed.isEmpty()) {
                    return CheckResult.failed("the protected cells were changed: " + changed);
                }
                return CheckResult.ok("the protected cells of the house are untouched");
            }
        };
    }

    /**
     * The environment accepts an ordinary placement.
     *
     * <p>The probe of the stand puts one block with an ordinary interaction of the player on the
     * prepared platform and removes it again. Its fact says whether the environment accepts an
     * ordinary placement at all. The check is a check of the preparation and not of the add-on:
     * when the environment refuses, the scenario is not verified (skipped with the reason) and it
     * is never a failure of the add-on.
     */
    private Check standProbePrepared(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                String fact = fixture.probeFact();
                if (fact == null || fact.isBlank() || "none".equals(fact)) {
                    return CheckResult.failed("the probe of the environment was not observed");
                }
                if (fact.startsWith("skipped:")) {
                    return CheckResult.ok("the probe of the environment was skipped for this "
                            + "scenario: " + fact);
                }
                if (!fact.contains("server=" + Registries.BLOCK.getId(Blocks.STONE_BRICKS).toString())) {
                    return CheckResult.failed("the environment refused the ordinary placement: " + fact);
                }
                return CheckResult.ok("the environment accepts an ordinary placement: " + fact);
            }
        };
    }

    /**
     * The laboratory prepared a platform that is covered with water.
     *
     * <p>The scenario of the blocked site needs a platform with a full support that is covered
     * with water: without it the reason of the automation would not be the water but the
     * missing platform.
     */
    private Check waterPlatformPrepared(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                PlatformFixture.Result result = PlatformFixture.inspect(runtime.context().world(),
                        runtime.context().playerPosition());
                if (result.kind() != PlatformFixture.Kind.WATER_COVERED) {
                    return CheckResult.failed("the laboratory prepared no platform that is covered with water: "
                            + result.detail());
                }
                return CheckResult.ok("the laboratory prepared a platform that is covered with water: "
                        + result.detail());
            }
        };
    }

    /** The registry holds exactly one seeded record with the expected variant. */
    private Check seededRecord(String name, int expectedVariant) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                List<HouseRecord> houses = runtime.registryService().registry().houses();
                if (!StandRules.exactlyOneSeededRecord(houses, expectedVariant)) {
                    return CheckResult.failed("the registry does not hold exactly one record with the variant "
                            + expectedVariant + ": " + houses);
                }
                HouseRecord record = houses.get(0);
                return CheckResult.ok("the seeded record " + record.houseId() + " has the variant "
                        + expectedVariant);
            }
        };
    }

    /** The registry holds a record of a house that was building when the client was closed. */
    private Check buildingRecord(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                List<HouseRecord> houses = runtime.registryService().registry().houses();
                if (!fixture.hasPersistenceSeed()) {
                    return CheckResult.failed("the previous JVM left no observed partial BUILDING fixture: " + houses);
                }
                return CheckResult.ok("the previous client JVM persisted BUILDING with 49 actual floor cells; "
                        + "this JVM reads the saved world without modifying it");
            }
        };
    }

    /** The state of the house in the registry is the expected one. */
    private Check houseStateCheck(String name, String expectedState) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                String state = record.state().wireName();
                if (!expectedState.equals(state)) {
                    return CheckResult.failed("the state of the house is " + state + ", not " + expectedState
                            + "; the reason is " + record.reason());
                }
                return CheckResult.ok("the state of the house is " + state);
            }
        };
    }

    /**
     * The run of the scenario ended with a concrete result.
     *
     * <p>Either the house is ready, or it is suspended with a reason that names the coordinate
     * and the observed state of the cell (ТЗ.md section 4). A run that ends without one of the
     * two results is not a result at all and fails the check.
     */
    private Check suspensionDiagnosisCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                String state = record.state().wireName();
                String facts = runtime.placement().lastFacts();
                if ("ready".equals(state)) {
                    return CheckResult.ok("the house is ready; no suspension happened; the facts of the last "
                            + "attempt: " + facts);
                }
                if (!"suspended".equals(state)) {
                    return CheckResult.failed("the run ended with the state " + state + " and the reason "
                            + record.reason() + "; the facts of the last attempt: " + facts);
                }
                String reason = record.reason();
                if (reason == null || reason.isBlank()) {
                    return CheckResult.failed("the house is suspended without a reason");
                }
                if (!reason.contains("@")) {
                    return CheckResult.failed("the reason of the house names no coordinate and no observed "
                            + "state: " + reason + "; the facts of the last attempt: " + facts);
                }
                return CheckResult.ok("the house is suspended with the reason " + reason
                        + "; the facts of the last attempt: " + facts);
            }
        };
    }

    /**
     * The suspended automation asked for nothing.
     *
     * <p>A suspended house waits for an explicit resume; the add-on may not lead the player
     * any more (ТЗ.md sections 3 and 5). The counter of the requests of the navigation shows it.
     */
    private Check noNavigationWhileSuspendedCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                int requests = runtime.navigationWhileSuspended();
                if (requests != 0) {
                    return CheckResult.failed("the suspended automation asked for the navigation "
                            + requests + " times");
                }
                return CheckResult.ok("the suspended automation asked for the navigation 0 times");
            }
        };
    }

    /** The reason of the house in the registry is the expected one. */
    private Check houseReasonCheck(String name, String expectedReason) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                String reason = record.reason();
                if (!expectedReason.equals(reason)) {
                    return CheckResult.failed("the reason of the house is " + reason + ", not "
                            + expectedReason);
                }
                return CheckResult.ok("the reason of the house is " + reason);
            }
        };
    }

    /** The number of confirmed cells of the house is the expected one. */
    private Check confirmedCellsCheck(String name, int expectedCells) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                int cells = record.confirmedCells();
                if (cells != expectedCells) {
                    return CheckResult.failed("the house has " + cells + " confirmed cells, not "
                            + expectedCells);
                }
                return CheckResult.ok("the house has " + cells + " confirmed cells");
            }
        };
    }

    /** The player stands on the front point of the house. */
    private Check frontPointCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                Vec3i front = SiteRules.frontPoint(record.origin());
                Vec3i player = runtime.context().playerPosition();
                if (!front.equals(player)) {
                    return CheckResult.failed("the player is at " + player + ", not on the front point "
                            + front);
                }
                return CheckResult.ok("the player is on the front point " + front);
            }
        };
    }

    /** The reason of the automation is the expected one. */
    private Check reasonCheck(String name, String expectedReason) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                String reason = runtime.controller().reason();
                if (!expectedReason.equals(reason)) {
                    return CheckResult.failed("the reason of the automation is " + reason + ", not "
                            + expectedReason);
                }
                return CheckResult.ok("the reason of the automation is " + reason);
            }
        };
    }

    /** The registry contains the expected number of houses. */
    private Check housesCountCheck(String name, int expectedCount) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                int count = runtime.registryService().registry().houses().size();
                if (count != expectedCount) {
                    return CheckResult.failed("the registry has " + count + " houses, not " + expectedCount);
                }
                return CheckResult.ok("the registry has " + count + " houses");
            }
        };
    }

    /** The variant of the last house of the registry is the expected one. */
    private Check variantCheck(String name, int expectedVariant) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                HouseRecord record = lastHouse(runtime);
                if (record == null) {
                    return CheckResult.failed("the registry has no house");
                }
                int variant = record.variant();
                if (variant != expectedVariant) {
                    return CheckResult.failed("the variant of the house is " + variant + ", not "
                            + expectedVariant);
                }
                return CheckResult.ok("the variant of the house is " + variant);
            }
        };
    }

    /** The registry file exists on the disk and contains the records. */
    private Check registryFileCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                Path file = runtime.registryService().directory().resolve("houses.json");
                if (!Files.isRegularFile(file)) {
                    return CheckResult.failed("the registry file " + file + " does not exist");
                }
                String content;
                try {
                    content = Files.readString(file);
                } catch (Exception e) {
                    return CheckResult.failed("the registry file cannot be read: " + e);
                }
                try {
                    dev.magehome.core.registry.HouseRegistry parsed =
                            dev.magehome.core.registry.HouseRegistry.fromJson(content);
                    if (parsed.houses().isEmpty()) return CheckResult.failed("the registry file holds no record");
                } catch (Exception invalid) {
                    return CheckResult.failed("the registry file is invalid: " + invalid);
                }
                return CheckResult.ok("the registry file has " + content.length() + " bytes and a record");
            }
        };
    }

    /**
     * Nothing was mined and nothing was crafted: the items of the player are the same as at the
     * beginning of the scenario and no block of the observed area was changed.
     */
    private Check noMiningOrCraftingCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                Map<String, Integer> now = items(runtime);
                List<String> changed = new ArrayList<>();
                for (Map.Entry<String, Integer> entry : itemsBefore.entrySet()) {
                    int count = now.getOrDefault(entry.getKey(), 0);
                    if (count != entry.getValue()) {
                        changed.add(entry.getKey() + ": " + entry.getValue() + " -> " + count);
                    }
                }
                for (Map.Entry<String, Integer> entry : now.entrySet()) {
                    if (!itemsBefore.containsKey(entry.getKey())) {
                        changed.add(entry.getKey() + ": 0 -> " + entry.getValue());
                    }
                }
                if (!changed.isEmpty()) {
                    return CheckResult.failed("the items of the player changed: " + changed);
                }
                CheckResult blocks = blocksUnchanged(runtime);
                if (!blocks.passed()) {
                    return blocks;
                }
                return CheckResult.ok("the items and the blocks of the player are unchanged");
            }
        };
    }

    /** No block of the observed area was changed. */
    private Check blocksUnchangedCheck(String name) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                return blocksUnchanged(runtime);
            }
        };
    }

    // --- the observables -------------------------------------------------------------------------

    /** The state of the house was the expected one at some moment of the scenario. */
    private Check stateObservedCheck(String name, String expectedState, String expectedReason) {
        return new Check() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public CheckResult run(MageHomeRuntime runtime) {
                String wanted = expectedState + "/" + expectedReason;
                // The reason of a suspension names the place where the work stopped, so the
                // observed reason is compared by its name.
                boolean seen = observedStates.stream()
                        .anyMatch(state -> state.startsWith(expectedState + "/")
                                && state.substring(expectedState.length() + 1).startsWith(expectedReason));
                if (!seen) {
                    return CheckResult.failed("the house was never " + wanted + "; the observed states are "
                            + observedStates);
                }
                return CheckResult.ok("the house was " + wanted);
            }
        };
    }

    // --- the observables -------------------------------------------------------------------------

    /** Remembers the states of the houses that the scenario has seen. */
    private void observeStates(MageHomeRuntime runtime) {
        for (HouseRecord record : runtime.registryService().registry().houses()) {
            observedStates.add(record.state().wireName() + "/" + record.reason());
        }
    }

    /** The last house of the registry; the new houses are appended. */
    private HouseRecord lastHouse(MageHomeRuntime runtime) {
        List<HouseRecord> houses = runtime.registryService().registry().houses();
        if (houses.isEmpty()) {
            return null;
        }
        return houses.get(houses.size() - 1);
    }

    /** Compares the observed blocks with the ones of the beginning of the scenario. */
    private CheckResult blocksUnchanged(MageHomeRuntime runtime) {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.world == null) {
            return CheckResult.failed("the world is not loaded");
        }
        List<String> changed = new ArrayList<>();
        for (Map.Entry<BlockPos, BlockState> entry : blocksBefore.entrySet()) {
            BlockState now = client.world.getBlockState(entry.getKey());
            if (now != entry.getValue()) {
                changed.add(entry.getKey().toShortString());
                if (changed.size() >= 5) {
                    break;
                }
            }
        }
        if (!changed.isEmpty()) {
            return CheckResult.failed("the blocks changed at " + changed);
        }
        return CheckResult.ok("the " + blocksBefore.size() + " observed blocks are unchanged");
    }

    /** The items of the player as a map of the item id and the number of items. */
    private Map<String, Integer> items(MageHomeRuntime runtime) {
        Map<String, Integer> items = new HashMap<>();
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.player == null) {
            return items;
        }
        for (ItemStack stack : client.player.getInventory().getMainStacks()) {
            if (stack.isEmpty()) {
                continue;
            }
            String id = Registries.ITEM.getId(stack.getItem()).toString();
            items.merge(id, stack.getCount(), Integer::sum);
        }
        return items;
    }

    /** Reads the blocks and the items that the scenario observes while it runs. */
    private void observe(MageHomeRuntime runtime) {
        blocksBefore = new LinkedHashMap<>();
        itemsBefore = items(runtime);
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.world == null || client.player == null) {
            return;
        }
        playerStart = client.player.getBlockPos();
        BlockPos player = client.player.getBlockPos();
        int chunkX = player.getX() >> 4;
        int chunkZ = player.getZ() >> 4;
        int minX = chunkX << 4;
        int minZ = chunkZ << 4;
        for (int x = minX; x < minX + 16; x++) {
            for (int z = minZ; z < minZ + 16; z++) {
                for (int y = player.getY() - OBSERVED_HEIGHT; y <= player.getY() + OBSERVED_HEIGHT; y++) {
                    BlockPos pos = new BlockPos(x, y, z);
                    blocksBefore.put(pos, client.world.getBlockState(pos));
                }
            }
        }
        LOGGER.info("the scenario observes {} blocks and {} items", blocksBefore.size(), itemsBefore.size());
    }

    /** The time the scenario was started; the steps use it for their timeout. */
    private long startedAt() {
        return startedAt;
    }
}
