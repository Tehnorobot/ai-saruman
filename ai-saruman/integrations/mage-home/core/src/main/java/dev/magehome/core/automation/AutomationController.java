package dev.magehome.core.automation;

import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.placement.ArrivalPolicy;
import dev.magehome.core.placement.PlacementSession;
import dev.magehome.core.placement.PlacementPlan;
import dev.magehome.core.placement.PlacementPlanner;
import dev.magehome.core.placement.PlacementTarget;
import dev.magehome.core.placement.PlannedPlacement;
import dev.magehome.core.registry.HouseMaintenance;
import dev.magehome.core.registry.HouseRecord;
import dev.magehome.core.registry.HouseRegistry;
import dev.magehome.core.registry.HouseState;
import dev.magehome.core.registry.MalformedRegistryException;
import dev.magehome.core.registry.StorageException;
import dev.magehome.core.registry.VariantAssigner;
import dev.magehome.core.repair.RepairDecision;
import dev.magehome.core.repair.RepairPolicy;
import dev.magehome.core.sites.DelayCounter;
import dev.magehome.core.sites.SiteEvaluator;
import dev.magehome.core.sites.SiteRules;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.supply.GiveAvailability;
import dev.magehome.core.supply.GiveCommandPlanner;
import dev.magehome.core.supply.ItemNeed;
import dev.magehome.core.supply.RateLimiter;
import dev.magehome.core.supply.SupplyCalculator;
import dev.magehome.core.supply.SuspensionReason;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.template.HouseTemplate;
import dev.magehome.core.verification.CellVerdict;
import dev.magehome.core.verification.ObservedBlock;
import dev.magehome.core.verification.VerificationReport;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The state machine of the automation (ТЗ.md sections 3, 4, 5 and 6).
 *
 * <p>The controller owns the decisions; the client module implements {@link AutomationContext} and
 * performs the real interactions. Reading the world, the inventory and the registry is the only
 * way the controller learns about the game, therefore the logic can be checked without a game.
 */
public final class AutomationController {

    /** Time the supply step waits for a confirmation of the inventory (4 seconds of work). */
    public static final long SUPPLY_CONFIRM_TIMEOUT_MILLIS = 4000L;
    /** Time a placement waits for the confirmation of the world (3 seconds of work). */
    public static final long PLACEMENT_CONFIRM_TIMEOUT_MILLIS = 3000L;
    /**
     * Time after which the wait for the command tree of the server is reported (5 seconds of work).
     *
     * <p>The tree of a server arrives with the connection. When it does not arrive, the house
     * waits with the reason {@code give_unknown}, and the observation of the wait names the
     * tick. After this time the observation names the timeout as well, but the behaviour of the
     * automation does not change: the house keeps waiting with the reason {@code give_unknown}
     * until the tree is read. The house is not suspended with {@code materials_missing}: that
     * reason states a fact about a tree that was read, and here the tree was never read.
     */
    public static final long GIVE_TREE_WAIT_TIMEOUT_MILLIS = 5000L;
    /** Maximum number of sends per position in one attempt. */
    public static final int MAX_PLACEMENT_ATTEMPTS = 2;
    /**
     * Time the repair of a wrong block waits for the world (20 seconds of work).
     *
     * <p>The player has to walk to the block and to break it with an ordinary attack; the budget
     * is generous enough for that and short enough to end the wait with a concrete reason
     * instead of waiting for ever.
     *
     * <p>The budget is longer than the ten seconds in which the dependency of the player stops a
     * task that does not move: the player walks and climbs while the repair waits, so the wait
     * does not end with the stop of the dependency. The house is suspended here with the
     * coordinate and the reason of the repair (ТЗ.md section 4).
     */
    public static final long REPAIR_TIMEOUT_MILLIS = 60_000L;
    /** Geometry identity is separate from palette; missing legacy ids cannot be safely guessed. */
    public static final String CURRENT_TEMPLATE_ID = "mage_home:closed_v1";
    /**
     * The number of the tries of an operation whose cells of the approach were all tried.
     *
     * <p>The house goes on with the other operations in between, and the cell is suspended with
     * a concrete reason when it fails again and again (ТЗ.md section 4).
     */
    public static final int MAX_DEFERRALS = 3;
    /** Number of houses that are verified per call of {@link #verifyHomes}. */
    public static final int VERIFICATION_BATCH_SIZE = 4;

    /**
     * The neighbours of a cell that are read for the rule of the liquid.
     *
     * <p>The cell itself is read by the rule of the cell; these six neighbours decide whether a
     * repair could spill a liquid (ТЗ.md section 4).
     */
    private static final List<Vec3i> LIQUID_NEIGHBOURS = List.of(
            new Vec3i(1, 0, 0), new Vec3i(-1, 0, 0), new Vec3i(0, 0, 1), new Vec3i(0, 0, -1),
            new Vec3i(0, 1, 0), new Vec3i(0, -1, 0));

    private boolean enabled;
    private boolean suspended;
    private AutomationState state = AutomationState.DISABLED;
    private String reason;

    private HouseRecord currentHouse;
    private HouseRecord candidateRecord;
    private HouseTemplate template;

    private final DelayCounter delayCounter = new DelayCounter();
    private final RateLimiter rateLimiter = new RateLimiter();

    private SiteSearch search;
    private String lastWorldKey;
    /** The candidate whose volume still contains the allowed plants. */
    private Vec3i pendingSite;
    /** The plants that have to be removed before the volume is checked again. */
    private List<Cell> pendingPlants = new ArrayList<>();
    /** The record of the site is not written yet; no block is placed before it. */
    private boolean awaitingSiteWrite;
    /** The house that is verified next; the verification runs in batches. */
    private int verificationCursor;

    private long lastTickMillis;
    private long activeMillis;
    private boolean previousTickActive;
    private long pendingSinceActiveMillis = -1L;
    private CellSpec.Operation pendingOperation;
    /** The operations that wait for a later try after their cells of the approach were tried. */
    private final Set<Cell> deferredCells = new LinkedHashSet<>();
    /** The number of the tries of the operations that wait for a later try. */
    private final Map<Cell, Integer> deferralCounts = new LinkedHashMap<>();
    /** Limit repair cycles that repeatedly recreate obstructions without building progress. */
    private int repairsWithoutPlacement;
    private static final int MAX_REPAIRS_WITHOUT_PLACEMENT = 6;
    private int placementAttempts;
    /** The cell whose wrong block is removed at the moment. */
    private Cell repairCell;
    /** The cell of the roof that the add-on holds open for the descent of the player. */
    private Cell descentCell;
    /** The number of the repair operations that were started for the current cell. */
    private int repairAttempts;
    /** The moment the repair of the current cell began. */
    private long repairSinceActiveMillis = -1L;
    private List<String> giveQueue = new ArrayList<>();
    private int giveIndex;
    private long giveSinceActiveMillis = -1L;
    private String giveItemId;
    private int giveBaselineCount;
    /**
     * The number of items of the given type that the add-on had spent when the command was sent.
     *
     * <p>The confirmation of a delivery compares the count of the inventory with the count of the
     * moment of the request. The add-on spends the material of the project itself (the blocks and
     * the temporary supports of the climb), and that spending would hide the delivery. The count
     * of the spending is therefore added to the comparison (ТЗ.md section 4).
     */
    private int giveBaselineConsumed;
    /** The moment the wait for the command tree of the server began; -1 means it is not waited for. */
    private long giveUnknownSinceActiveMillis = -1L;
    private boolean waitForShift;
    /**
     * The number of the ticks of the automation.
     *
     * <p>The number is written into the observations of the log, so that a wait can be read
     * against the facts of the game: a tree that arrives late and a tree that never arrives
     * have to be distinguishable.
     */
    private long ticks;

    // --- public API -----------------------------------------------------------------------------

    public boolean enabled() {
        return enabled;
    }

    public AutomationState state() {
        return state;
    }

    public String reason() {
        return reason;
    }

    public HouseRecord currentHouse() {
        return currentHouse;
    }

    /** Enables the local automation. It never resumes a suspended or damaged house. */
    public void enable() {
        if (enabled) {
            return;
        }
        enabled = true;
        state = AutomationState.IDLE;
        reason = null;
        delayCounter.reset();
    }

    /** Disables the automation; the registry stays untouched. */
    public void disable() {
        enabled = false;
        suspended = false;
        state = AutomationState.DISABLED;
        reason = null;
        delayCounter.reset();
        activeMillis = 0L;
        previousTickActive = false;
        pendingOperation = null;
        pendingSinceActiveMillis = -1L;
        placementAttempts = 0;
        giveQueue = new ArrayList<>();
        giveIndex = 0;
        giveSinceActiveMillis = -1L;
        giveUnknownSinceActiveMillis = -1L;
        pendingSite = null;
        pendingPlants = new ArrayList<>();
        awaitingSiteWrite = false;
    }

    /** Repeats the search; only used when no site was found. */
    public void retrySearch() {
        search = null;
        waitForShift = false;
        pendingSite = null;
        pendingPlants = new ArrayList<>();
        giveQueue = new ArrayList<>();
        giveIndex = 0;
    }

    /**
     * Another owner controls the chain of UnionClef: the automation waits and does not take the
     * control (ТЗ.md section 6).
     */
    public void busy() {
        state = AutomationState.WAITING;
        reason = SuspensionReason.BUSY.wireName();
    }

    /**
     * Resumes a house. Returns the answer of the API: {@code rejected} with
     * {@code unknown_house} for an unknown or foreign id and {@code already_ready} for a house
     * that still corresponds to the project.
     */
    public String resume(String houseId, AutomationContext ctx) {
        if (!enabled) {
            // A resume needs an enabled automation.
            return ApiResponses.rejected("disabled");
        }
        if (ctx.foreignTaskActive()) {
            // Another owner controls the chain of UnionClef; the task of the other owner is not
            // touched and the automation waits (ТЗ.md section 6).
            state = AutomationState.WAITING;
            reason = SuspensionReason.BUSY.wireName();
            return ApiResponses.rejected("busy");
        }
        HouseRegistry registry;
        try {
            registry = ctx.loadRegistry();
        } catch (Exception e) {
            return ApiResponses.rejected("storage_error");
        }
        HouseRecord record = registry.byId(houseId);
        if (record == null) {
            return ApiResponses.rejected("unknown_house");
        }
        if (!record.worldKey().equals(ctx.worldKey()) || !record.dimension().equals(ctx.dimension())
                || !record.playerUuid().equals(ctx.playerUuid())) {
            return ApiResponses.rejected("unknown_house");
        }
        if (!CURRENT_TEMPLATE_ID.equals(record.templateId())) {
            // A missing/unknown id can refer to either legacy shape. Do not verify, repair or
            // silently reinterpret the player's existing house using another geometry.
            return ApiResponses.rejected("unknown_template");
        }
        if (record.state() == HouseState.READY && stillMatches(record, ctx)) {
            return ApiResponses.rejected("already_ready");
        }
        // The houses are verified before the work continues (ТЗ.md section 5).
        VerificationReport report = verifyHouse(record, ctx);
        currentHouse = record;
        candidateRecord = null;
        deferredCells.clear();
        deferralCounts.clear();
        repairsWithoutPlacement = 0;
        template = HouseTemplate.forVariant(record.variant());
        record.setState(HouseState.BUILDING);
        record.setStage("placement");
        record.setReason(null);
        if (report != null) {
            // The number of the confirmed cells comes from the observation of the world; the
            // record of the progress never replaces it (ТЗ.md section 5).
            record.setConfirmedCells(report.confirmedCells());
        }
        suspended = false;
        pendingOperation = null;
        pendingSinceActiveMillis = -1L;
        placementAttempts = 0;
        repairCell = null;
        repairAttempts = 0;
        repairSinceActiveMillis = -1L;
        giveQueue = new ArrayList<>();
        giveIndex = 0;
        giveSinceActiveMillis = -1L;
        giveUnknownSinceActiveMillis = -1L;
        state = AutomationState.BUILDING;
        reason = null;
        save(registry, ctx);
        ctx.observe("resume", "house=" + record.houseId() + ", variant=" + record.variant()
                + ", confirmed_cells=" + record.confirmedCells() + ", attempt=1");
        return ApiResponses.accepted();
    }

    /**
     * The own task was stopped from the outside (the ordinary Stop of UnionClef).
     *
     * <p>The house keeps its record and the confirmed installations; only the own actions are
     * released. An explicit resume is needed to continue the work (ТЗ.md section 5).
     */
    public void interrupt(AutomationContext ctx) {
        interrupt(ctx, SuspensionReason.INTERRUPTED.wireName());
    }

    /**
     * The work was interrupted by an event of the game: the ordinary Stop of UnionClef, the death
     * of the player, the disconnect or the change of the world (ТЗ.md section 5).
     *
     * <p>The house keeps its record and the confirmed installations, the reason of the
     * suspension is concrete and an explicit resume is needed to continue the work.
     *
     * @param reason the concrete reason of the suspension
     */
    public void interrupt(AutomationContext ctx, String reason) {
        if (!enabled || suspended || currentHouse == null) {
            // Nothing is built, so nothing has to be resumed.
            return;
        }
        state = AutomationState.SUSPENDED;
        suspended = true;
        this.reason = reasonWithPlace(ctx, reason);
        pendingOperation = null;
        pendingSinceActiveMillis = -1L;
        placementAttempts = 0;
        repairCell = null;
        repairAttempts = 0;
        repairSinceActiveMillis = -1L;
        giveQueue = new ArrayList<>();
        giveIndex = 0;
        giveSinceActiveMillis = -1L;
        giveUnknownSinceActiveMillis = -1L;
        giveItemId = null;
        currentHouse.setState(HouseState.SUSPENDED);
        currentHouse.setReason(this.reason);
        currentHouse.setStage("suspended");
        try {
            save(ctx.loadRegistry(), ctx);
        } catch (Exception e) {
            // The state could not be written; the automation stays suspended either way.
        }
        ctx.observe("interrupted", "house=" + currentHouse.houseId() + ", reason=" + reason);
    }

    /**
     * The reason of the suspension with the place where the work stopped.
     *
     * <p>The reason names the cell of the project and the block that stands there. A suspension
     * without a place cannot be read afterwards: the report of the run has to say where the work
     * stopped and what the world showed (ТЗ.md section 4).
     *
     * @param ctx    the context of the automation
     * @param reason the name of the reason of the interruption
     * @return the reason with the coordinate and the observed state of the cell
     */
    private String reasonWithPlace(AutomationContext ctx, String reason) {
        Vec3i target = currentTarget();
        if (target == null) {
            return reason;
        }
        String place = target.x() + "," + target.y() + "," + target.z();
        try {
            ObservedBlock block = ctx.world().read(target);
            if (block == null || !block.known()) {
                return reason + "@" + place + ":unknown";
            }
            return reason + "@" + place + ":" + block.blockId();
        } catch (RuntimeException failure) {
            // The world cannot be read: the coordinate is named anyway.
            return reason + "@" + place + ":unknown";
        }
    }

    /**
     * The number of the uninterrupted client ticks outside every service radius.
     *
     * <p>The counter is the one of the delay of 1200 ticks of ТЗ.md section 3; it is observed in
     * the log of the client, so that the delay can be followed in the game.
     */
    public int delayTicks() {
        return delayCounter.ticks();
    }

    /**
     * True when the position belongs to a cell that the project of the current house occupies.
     *
     * <p>The client asks the core before it puts a temporary support somewhere: a support in a
     * cell of the project would stand in the way of a wall, of a piece of furniture or of the
     * roof, and the rule of the repair would fight the add-on for that cell (ТЗ.md section 4,
     * the temporary blocks of the 2026-11-13).
     *
     * @param position the position in the world
     * @return true when the project occupies the cell of the position
     */
    public boolean isProjectCell(Vec3i position) {
        if (currentHouse == null || currentHouse.origin() == null) {
            return false;
        }
        HouseTemplate template = HouseTemplate.forVariant(currentHouse.variant());
        Cell cell = new Cell(position.x() - currentHouse.origin().x(),
                position.y() - currentHouse.origin().y(),
                position.z() - currentHouse.origin().z());
        return template.spec(cell) != null;
    }

    /** The status snapshot of the current work. */
    public String statusJson() {
        String houseId = currentHouse != null ? currentHouse.houseId() : null;
        String stage = currentHouse != null ? currentHouse.stage() : null;
        int confirmed = currentHouse != null ? currentHouse.confirmedCells() : 0;
        String lastVerified = currentHouse != null ? currentHouse.lastVerifiedAt() : null;
        Vec3i target = currentTarget();
        return ApiResponses.status(enabled, state, reason, houseId, stage, confirmed,
                HouseTemplate.OCCUPIED_CELLS, target, lastVerified);
    }

    /** The registry snapshot including the runtime visibility. */
    public String homesJson(HouseRegistry registry) {
        return ApiResponses.homes(registry);
    }

    /**
     * Verifies the loaded houses in batches (ТЗ.md section 5).
     *
     * <p>The verification runs on the command {@code status} and before a resume; there is no
     * constant background sweep. A ready house that does not match the project becomes damaged,
     * an unloaded house keeps the time of its last verification and is marked as unloaded.
     *
     * <p>Every call verifies at most {@link #VERIFICATION_BATCH_SIZE} houses; the next call
     * continues with the following house, so a big registry is verified in parts.
     */
    public void verifyHomes(AutomationContext ctx) {
        HouseRegistry registry;
        try {
            registry = ctx.loadRegistry();
        } catch (Exception e) {
            // A damaged file is reported by the automation itself.
            return;
        }
        List<HouseRecord> houses = registry.houses();
        if (houses.isEmpty()) {
            return;
        }
        int size = houses.size();
        int index = Math.floorMod(verificationCursor, size);
        int checked = 0;
        while (checked < VERIFICATION_BATCH_SIZE && checked < size) {
            verifyHouse(houses.get(index), ctx);
            index = (index + 1) % size;
            checked++;
        }
        verificationCursor = index;
        try {
            ctx.saveRegistry(registry);
        } catch (Exception e) {
            // The next tick of the automation reports the write error.
        }
    }

    /**
     * Verifies one house against the project.
     *
     * <p>An area that is not loaded is never counted as free: the record keeps the time of its
     * last verification and is marked as unloaded.
     *
     * @return the report of the observation of the world or null when the area is not loaded
     */
    private VerificationReport verifyHouse(HouseRecord record, AutomationContext ctx) {
        if (!CURRENT_TEMPLATE_ID.equals(record.templateId())) {
            // Unknown legacy geometry is not evidence of damage, and must not be rewritten.
            return null;
        }
        HouseTemplate recordTemplate = HouseTemplate.forVariant(record.variant());
        Map<Cell, ObservedBlock> observed = new LinkedHashMap<>();
        boolean loaded = true;
        for (Cell cell : recordTemplate.volumeCells()) {
            Vec3i position = record.origin().add(cell.x(), cell.y(), cell.z());
            ObservedBlock block = ctx.world().read(position);
            observed.put(cell, block);
            if (!block.known()) {
                loaded = false;
            }
        }
        VerificationReport report = VerificationReport.of(recordTemplate, observed);
        HouseMaintenance.applyVerification(record, report, loaded, nowIso());
        return loaded ? report : null;
    }

    // --- tick -----------------------------------------------------------------------------------

    /** One client tick of the automation. */
    public void tick(AutomationContext ctx) {
        long now = ctx.nowMillis();
        long delta = lastTickMillis == 0L ? 0L : Math.max(0L, now - lastTickMillis);
        lastTickMillis = now;
        boolean wasActive = previousTickActive;
        previousTickActive = false;
        ticks++;

        if (!enabled) {
            state = AutomationState.DISABLED;
            reason = null;
            return;
        }

        if (suspended) {
            // A suspended automation waits for an explicit resume.
            state = AutomationState.SUSPENDED;
            return;
        }

        if (ctx.worldKey() != null && !ctx.worldKey().equals(lastWorldKey)) {
            // A new world resets the delay counter.
            lastWorldKey = ctx.worldKey();
            delayCounter.reset();
        }

        if (!ctx.playerAlive()) {
            delayCounter.reset();
            return;
        }

        if (!ctx.inWorld()) {
            // Without a living player in the world the counter does not run.
            return;
        }

        if (ctx.unionClefPaused() || ctx.foreignTaskActive() || !ctx.unionClefChainAvailable()) {
            // No background actions while UnionClef is paused, busy with a foreign task or while
            // its task chain does not exist yet (ТЗ.md section 5: the add-on works only inside its
            // own root task of the chain of UnionClef).
            state = AutomationState.WAITING;
            reason = SuspensionReason.BUSY.wireName();
            return;
        }

        if (wasActive) activeMillis += delta;
        previousTickActive = true;

        HouseRegistry registry;
        try {
            registry = ctx.loadRegistry();
        } catch (Exception e) {
            suspend(SuspensionReason.STORAGE_ERROR, ctx, null);
            return;
        }

        if (ctx.registryWriteFailed()) {
            // The registry could not be written; the building stops until the file works again
            // (ТЗ.md section 5).
            suspend(SuspensionReason.STORAGE_ERROR, ctx, registry);
            return;
        }

        if (currentHouse != null) {
            buildStep(ctx, registry);
            return;
        }

        List<HouseRecord> scope = registry.inScope(ctx.worldKey(), ctx.dimension(), ctx.playerUuid());
        // While a candidate of an existing record is searched, the place of that record is not a
        // separate house for the distance check.
        boolean candidateSearch = candidateRecord != null;
        Vec3i candidateOrigin = candidateSearch ? candidateRecord.origin() : null;
        List<Vec3i> scopeOrigins = new ArrayList<>();
        for (HouseRecord record : scope) {
            if (candidateSearch && record.origin().equals(candidateOrigin)) {
                continue;
            }
            scopeOrigins.add(record.origin());
        }

        if (!candidateSearch && SiteRules.insideAnyRadius(ctx.playerPosition(), scopeOrigins)) {
            // Inside the service radius of a saved house no new house is created.
            delayCounter.reset();
            state = AutomationState.IDLE;
            reason = null;
            return;
        }

        if (!candidateSearch && !scope.isEmpty()) {
            delayCounter.tick(true);
            if (!delayCounter.elapsed(SiteRules.SEARCH_DELAY_TICKS)) {
                state = AutomationState.WAITING;
                reason = "delay";
                ctx.observe("delay", "ticks=" + delayCounter.ticks() + "/" + SiteRules.SEARCH_DELAY_TICKS);
                return;
            }
        }

        searchStep(ctx, registry);
    }

    // --- search ----------------------------------------------------------------------------------

    private void searchStep(AutomationContext ctx, HouseRegistry registry) {
        if (!"minecraft:overworld".equals(ctx.dimension())) {
            // In 0.1.0 houses are built in the overworld only.
            state = AutomationState.IDLE;
            reason = null;
            return;
        }
        if (waitForShift) {
            // The next search starts 8 blocks away from the last search point.
            waitForShift = false;
            if (search == null) {
                search = new SiteSearch(ctx.playerPosition());
            } else {
                search.shift(SiteRules.RETRY_SHIFT);
            }
        }
        if (search == null) {
            search = new SiteSearch(ctx.playerPosition());
        }
        state = AutomationState.SEARCHING;
        reason = null;

        if (pendingSite != null) {
            // The candidate is cleared first: the allowed plants are removed and the volume is
            // checked again (ТЗ.md section 3).
            SiteState clearing = clearPlants(ctx);
            if (clearing == SiteState.WORKING) {
                return;
            }
            Vec3i candidate = pendingSite;
            pendingSite = null;
            pendingPlants = new ArrayList<>();
            if (clearing == SiteState.REJECTED) {
                if (search != null) {
                    search.reject(candidate);
                }
                return;
            }
            SiteEvaluator.Result recheck = SiteEvaluator.evaluate(candidate, ctx.world());
            if (!recheck.suitable() || !recheck.removablePlants().isEmpty()) {
                // The volume is still blocked; the candidate is skipped.
                if (search != null) {
                    search.reject(candidate);
                }
                return;
            }
            startHouse(candidate, registry, ctx);
            return;
        }

        List<Vec3i> batch = search.nextBatch();
        for (Vec3i candidate : batch) {
            if (!SiteRules.centreWithinSearchLimit(candidate, search.base())) {
                continue;
            }
            if (!SiteRules.heightWithinLimit(candidate.y(), search.base().y())) {
                continue;
            }
            List<Vec3i> allOrigins = new ArrayList<>();
            for (HouseRecord record : registry.houses()) {
                if (candidateRecord != null && record.origin().equals(candidateRecord.origin())) {
                    // The previous place of the same record is not a separate house.
                    continue;
                }
                allOrigins.add(record.origin());
            }
            if (!SiteRules.farFromAllHouses(candidate, allOrigins)) {
                continue;
            }
            SiteEvaluator.Result result = SiteEvaluator.evaluate(candidate, ctx.world());
            if (!result.suitable()) {
                continue;
            }
            if (!result.removablePlants().isEmpty()) {
                // Only the four allowed plants are removed; every other block stays untouched.
                pendingSite = candidate;
                pendingPlants = new ArrayList<>(result.removablePlants());
                state = AutomationState.SEARCHING;
                reason = "clearing";
                return;
            }
            startHouse(candidate, registry, ctx);
            return;
        }
        if (search.exhausted()) {
            state = AutomationState.WAITING;
            reason = "no_site";
            waitForShift = true;
        }
    }

    /** The state of the clearing of a candidate. */
    private enum SiteState {
        /** The volume is free; the house can start. */
        DONE,
        /** The plants are still being removed. */
        WORKING,
        /** The candidate cannot be used. */
        REJECTED
    }

    /**
     * Removes the allowed plants of the chosen candidate.
     *
     * <p>One plant per tick is removed; the world is read again before the next one. A cell that
     * holds another block is never touched, the candidate is skipped then.
     */
    private SiteState clearPlants(AutomationContext ctx) {
        state = AutomationState.SEARCHING;
        reason = "clearing";
        while (!pendingPlants.isEmpty()) {
            Cell plant = pendingPlants.get(0);
            Vec3i position = pendingSite.add(plant.x(), plant.y(), plant.z());
            ObservedBlock block = ctx.world().read(position);
            if (block.isAir()) {
                pendingPlants.remove(0);
                continue;
            }
            if (!block.removablePlant()) {
                // Something else stands there; that block is never broken.
                return SiteState.REJECTED;
            }
            if (!ctx.requestPlantRemoval(position)) {
                return SiteState.REJECTED;
            }
            return SiteState.WORKING;
        }
        return SiteState.DONE;
    }

    private void startHouse(Vec3i origin, HouseRegistry registry, AutomationContext ctx) {
        if (candidateRecord == null) {
            int variant = VariantAssigner.nextVariant(registry, ctx.worldKey(), ctx.dimension(), ctx.playerUuid());
            HouseRecord record = new HouseRecord(nextHouseId(registry), ctx.worldKey(), ctx.dimension(), ctx.playerUuid(),
                    origin, variant, HouseState.BUILDING, "site", 0, null, null);
            record.setTemplateId(CURRENT_TEMPLATE_ID);
            registry.add(record);
            candidateRecord = record;
            // The record of the site is written before the first block is placed.
            if (!save(registry, ctx)) {
                registry.remove(record.houseId());
                candidateRecord = null;
                return;
            }
            // The first block is only placed when the write of the record is finished.
            awaitingSiteWrite = true;
        } else {
            // The candidate changed before the first block: only the origin changes, the record and
            // the palette stay.
            candidateRecord.setOrigin(origin);
        }
        HouseRecord record = candidateRecord;
        candidateRecord = null;
        record.setState(HouseState.BUILDING);
        record.setStage("placement");
        record.setReason(null);
        if (!save(registry, ctx)) {
            return;
        }
        currentHouse = record;
        deferredCells.clear();
        deferralCounts.clear();
        repairsWithoutPlacement = 0;
        template = HouseTemplate.forVariant(record.variant());
        pendingOperation = null;
        pendingSinceActiveMillis = -1L;
        placementAttempts = 0;
        repairCell = null;
        repairAttempts = 0;
        repairSinceActiveMillis = -1L;
        giveQueue = new ArrayList<>();
        giveIndex = 0;
        giveSinceActiveMillis = -1L;
        state = AutomationState.BUILDING;
        reason = null;
    }

    private static String nextHouseId(HouseRegistry registry) {
        int index = 1;
        while (registry.byId("h" + index) != null) {
            index++;
        }
        return "h" + index;
    }

    // --- building --------------------------------------------------------------------------------

    private void buildStep(AutomationContext ctx, HouseRegistry registry) {
        HouseTemplate currentTemplate = template;
        if (currentTemplate == null) {
            currentTemplate = HouseTemplate.forVariant(currentHouse.variant());
            template = currentTemplate;
        }

        if (!RepairPolicy.repairApplies(currentHouse.state())) {
            // Only a house in the state building is built and repaired automatically (ТЗ.md
            // section 5). A ready or a damaged house waits for an explicit resume, which puts
            // the record of the house back into the state building.
            state = AutomationState.IDLE;
            reason = null;
            return;
        }

        if (awaitingSiteWrite) {
            // The record of the site has to be completely written before the first block is
            // placed (ТЗ.md section 5).
            if (ctx.registryWriteFailed()) {
                awaitingSiteWrite = false;
                suspend(SuspensionReason.STORAGE_ERROR, ctx, registry);
                return;
            }
            if (!ctx.registryWriteFinished()) {
                state = AutomationState.BUILDING;
                reason = null;
                return;
            }
            awaitingSiteWrite = false;
        }

        // The chosen place is checked again before the building starts.
        if (currentHouse.confirmedCells() == 0 && pendingOperation == null) {
            SiteEvaluator.Result siteCheck = SiteEvaluator.checkExisting(currentHouse.origin(), ctx.world());
            if (!siteCheck.suitable()) {
                // The record stays, only the origin may change; no second record is created.
                currentHouse.setStage("site");
                candidateRecord = currentHouse;
                currentHouse = null;
                if (search != null) {
                    search.reject(candidateRecord.origin());
                }
                save(registry, ctx);
                state = AutomationState.SEARCHING;
                reason = null;
                return;
            }
        }

        VerificationReport report = readReport(ctx, currentTemplate);
        currentHouse.setConfirmedCells(report.confirmedCells());

        // The descent of the player from the roof of the house. The roof is closed and the player
        // stands on it; the only way into the house is the doorway below. The add-on holds one
        // cell of the roof open, the player walks down through it, and the cell is built from
        // inside afterwards (ТЗ.md section 2 and the exit through the doorway).
        if (!descentStep(ctx, currentTemplate, report)) {
            return;
        }

        if (pendingOperation != null && operationMatches(report, pendingOperation)) {
            // The world has the operation: the client stops the interaction and the automation
            // does not ask for this position a second time (ТЗ.md section 4).
            ctx.confirmPlacement(pendingOperation);
            // The count of the confirmed cells is read again: the world of the client may have
            // received the block after the report of the beginning of the tick, and the record
            // of the house must not lag behind the world.
            currentHouse.setConfirmedCells(readReport(ctx, currentTemplate).confirmedCells());
            deferredCells.clear();
            deferralCounts.clear();
            repairsWithoutPlacement = 0;
            pendingOperation = null;
            pendingSinceActiveMillis = -1L;
            placementAttempts = 0;
        }

        String cleanupFailure = ctx.supportCleanupFailure();
        if (cleanupFailure != null) {
            suspend(SuspensionReason.OBSTRUCTED, ctx, registry, cleanupFailure);
            return;
        }
        // Temporary supports may occupy cells that the completed template requires to be air.
        // Do not mark the house ready, or fall through to an empty plan, before removing them.
        if (report.confirmedCells() == currentTemplate.cells().size() && !ctx.requestSupportCleanup()) {
            return;
        }

        if (report.complete() && atFrontPoint(ctx)) {
            currentHouse.setState(HouseState.READY);
            currentHouse.setStage("ready");
            currentHouse.setReason(null);
            currentHouse.setLastVerifiedAt(nowIso());
            save(registry, ctx);
            currentHouse = null;
            pendingOperation = null;
            pendingSinceActiveMillis = -1L;
            placementAttempts = 0;
            state = AutomationState.IDLE;
            reason = null;
            return;
        }

        if (report.complete()) {
            // The house is complete, but the player still has to leave it through the front
            // point A+(3,0,-1); only then the record becomes ready (C50).
            ctx.requestFrontPoint(SiteRules.frontPoint(currentHouse.origin()));
            return;
        }

        // A wrong block inside the volume of the registered house is removed with an ordinary
        // attack of the player, the cell is read again and the building continues (ТЗ.md section 4).
        // A protected cell and a repair that is not applicable suspend the house with the reason
        // obstructed, the coordinate of the cell and the concrete reason.
        RepairOutcome repair = repairStep(ctx, registry, currentTemplate, report);
        if (repair != RepairOutcome.PROCEED) {
            return;
        }

        // The planner decides the order of the operations and the support of every cell.
        Set<Cell> standingCells = placedCells(report);
        if (descentCell != null) {
            // The cell of the roof that the add-on holds open is not planned: the player has to
            // come down through it first, and the cell is built from inside afterwards.
            standingCells.add(descentCell);
        }
        PlacementPlan plan = PlacementPlanner.plan(currentTemplate, standingCells,
                cell -> solidAt(ctx, currentHouse.origin(), cell));
        if (!plan.complete()) {
            // There are unfinished cells but no admissible operation for them.
            Cell blocked = plan.unsupportedCell();
            Vec3i position = currentHouse.origin().add(blocked.x(), blocked.y(), blocked.z());
            suspend(SuspensionReason.NO_PLACEMENT_SUPPORT, ctx, registry,
                    position.x() + "," + position.y() + "," + position.z());
            return;
        }
        // The full plan simulates future placements. A later operation must be replanned against
        // the actual world before it can be selected, otherwise it may rely on a deferred block.
        Set<Cell> standingNow = new LinkedHashSet<>(standingCells);
        PlannedPlacement planned = null;
        for (int pass = 0; pass < 2 && planned == null; pass++) {
            for (PlannedPlacement candidate : plan.placements()) {
                if (candidate.operation().placement() == CellSpec.Placement.DOOR
                        && plan.placements().stream().anyMatch(p -> p.operation().placement() != CellSpec.Placement.DOOR)) {
                    // A deferred furniture operation must not allow the doorway to close early.
                    continue;
                }
                if (deferredCells.contains(candidate.operation().cells().get(0))) continue;
                PlacementTarget actual = PlacementPlanner.targetFor(currentTemplate, candidate.operation(),
                        standingNow, cell -> solidAt(ctx, currentHouse.origin(), cell));
                if (actual != null) {
                    planned = new PlannedPlacement(candidate.operation(), actual);
                    break;
                }
            }
            if (planned == null && !deferredCells.isEmpty()) {
                // A new round retries cells, but keeps their counts so repeated failure is bounded.
                deferredCells.clear();
            } else {
                break;
            }
        }
        if (planned == null) {
            suspend(SuspensionReason.NO_PLACEMENT_SUPPORT, ctx, registry);
            return;
        }
        CellSpec.Operation target = planned.operation();

        SupplyOutcome supply = supplyStep(ctx, registry, currentTemplate, report, target);
        if (supply != SupplyOutcome.READY) {
            return;
        }

        placementStep(ctx, registry, currentTemplate, report, target, planned.target());
    }

    /**
     * The descent of the player from the roof of the house.
     *
     * <p>The roof of the house is closed, and the player who built it stands on it: the only way
     * into the house is the doorway, which stands three levels below, and the player would have to
     * fall from the roof. The add-on therefore keeps the last cell of the roof open: the player
     * walks down through it, and the cell is built from inside afterwards — the block of the roof
     * is placed on the side face of the block next to it, and that face is within the ordinary
     * range from the floor of the house (ТЗ.md section 2, the owner of 2026-11-13).
     *
     * <p>The house waits while the player is on the way down: the furniture and the door stand
     * inside the house, and the player has to be inside for them.
     *
     * @param ctx      the context of the automation
     * @param template the project of the house
     * @param report   the comparison of the volume with the project
     * @return false when the house has to wait for the player to come down
     */
    private boolean descentStep(AutomationContext ctx, HouseTemplate template,
                                VerificationReport report) {
        if (descentCell != null) {
            if (ctx.descentHeld(descentCell)) {
                // The player is on the way down: the house waits.
                return false;
            }
            // The player is inside: the cell of the roof is built from inside.
            descentCell = null;
        }
        Cell missing = lastMissingRoofCell(template, report);
        if (missing == null || !playerIsOnRoof(ctx)) {
            return true;
        }
        descentCell = missing;
        ctx.requestDescent(missing);
        ctx.observe("descent_requested", "cell=" + missing.x() + "," + missing.y() + "," + missing.z());
        return false;
    }

    /**
     * The last cell of the roof that is not built yet, or null when the roof is not one cell short.
     *
     * <p>Only the last cell is held open: while more of the roof is missing, the player builds the
     * roof from the top, as usual.
     *
     * @param template the project of the house
     * @param report   the comparison of the volume with the project
     * @return the cell of the roof or null
     */
    private Cell lastMissingRoofCell(HouseTemplate template, VerificationReport report) {
        Cell missing = null;
        int count = 0;
        int roofLevel = HouseTemplate.SIZE_Y - 1;
        for (Cell cell : template.volumeCells()) {
            if (cell.y() != roofLevel || template.spec(cell) == null) {
                continue;
            }
            if (report.verdict(cell) == CellVerdict.MATCH) {
                continue;
            }
            missing = cell;
            count++;
        }
        return count == 1 ? missing : null;
    }

    /** True when the player stands on the roof of the house. */
    private boolean playerIsOnRoof(AutomationContext ctx) {
        if (currentHouse == null) {
            return false;
        }
        return ctx.playerPosition().y() >= currentHouse.origin().y() + HouseTemplate.SIZE_Y;
    }

    /**
     * The client reports that the player has come down through the held cell.
     */
    public void descentFinished() {
        // The next tick of the automation reads the world and builds the cell from inside.
    }

    /** Outcome of the repair step. */
    private enum RepairOutcome {
        /** No repair is needed: the building continues. */
        PROCEED,
        /** The repair of a cell runs: the automation waits for the world. */
        WAITING,
        /** The automation was suspended. */
        SUSPENDED
    }

    /**
     * The safe repair of the wrong cells of the volume of the house (ТЗ.md section 4).
     *
     * <p>A wrong, non empty block inside the volume of a registered house is removed with an
     * ordinary attack of the player; the cell is read again and the building continues. The
     * decision about the cell is taken by the domain rule ({@link RepairPolicy}), so that the
     * client and the checks of the core agree about every cell.
     *
     * <p>A protected cell (a cell of the project that already corresponds to it, a support under
     * the house, a block outside the volume and every container) is never touched, and a repair
     * that would touch a protected cell, the outer half of a multi-block item or that could spill
     * a liquid suspends the house with the reason {@code obstructed}, the coordinate of the cell
     * and the concrete reason.
     *
     * <p>A single half of a door or a bed is never overwritten by a new installation: the half is
     * eliminated with the same safe rules, the cell is read again and the pair is placed again.
     */
    private RepairOutcome repairStep(AutomationContext ctx, HouseRegistry registry,
                                     HouseTemplate currentTemplate, VerificationReport report) {
        if (!RepairPolicy.repairApplies(currentHouse.state())) {
            // The repair belongs to a house in the state building (ТЗ.md section 5): a ready or
            // a damaged house is never repaired on its own, it waits for an explicit resume.
            return RepairOutcome.PROCEED;
        }
        if (repairCell != null) {
            CellVerdict verdict = report.verdict(repairCell);
            CellSpec spec = currentTemplate.spec(repairCell);
            boolean single = spec == null || spec.placement() == CellSpec.Placement.SINGLE;
            if (verdict == CellVerdict.ABSENT || (verdict == CellVerdict.MATCH && single)) {
                // The world has the cell as the project wants it: the repair is done. An empty
                // cell of the room of the house is air by the project, and the air is the end of
                // the repair of a foreign block. A cell that corresponds to the project is not the
                // end of the repair: a single half of a door or of a bed corresponds to the
                // project and must still be removed, so the repair waits for the empty cell and
                // not for the match (ТЗ.md section 4). A resolved obstruction is progress, so the
                // budget of the repeated obstructions counts the unresolved ones only.
                repairCell = null;
                repairAttempts = 0;
                repairsWithoutPlacement = 0;
                repairSinceActiveMillis = -1L;
            }
        }
        for (Cell cell : currentTemplate.volumeCells()) {
            CellVerdict verdict = report.verdict(cell);
            if (verdict != CellVerdict.WRONG_BLOCK && verdict != CellVerdict.WRONG_PROPERTY) {
                continue;
            }
            if (ctx.isTemporarySupport(currentHouse.origin().add(cell.x(), cell.y(), cell.z()))) {
                // The block is a temporary support of the add-on: it is the step of the climb and
                // it stands until the climb is over (ТЗ.md section 4, the 2026-11-13).
                continue;
            }
            RepairDecision decision = classifyCell(ctx, currentTemplate, report, cell);
            if (decision.blocksBuilding()) {
                // The cell cannot be repaired (a protected or dangerous cell). The house waits
                // for the player with the coordinate and the concrete reason (ТЗ.md section 4).
                suspendCell(ctx, registry, cell, decision.reason());
                return RepairOutcome.SUSPENDED;
            }
            if (decision.needsRepair()) {
                return startRepair(ctx, registry, cell, decision);
            }
        }
        for (CellSpec.Operation operation : currentTemplate.operations()) {
            if (!operation.isMultiCell()) {
                continue;
            }
            boolean complete = true;
            for (Cell cell : operation.cells()) {
                if (report.verdict(cell) != CellVerdict.MATCH) {
                    complete = false;
                    break;
                }
            }
            if (complete) {
                continue;
            }
            for (Cell cell : operation.cells()) {
                if (report.verdict(cell) != CellVerdict.MATCH) {
                    // The wrong part of the pair is handled by the loop above.
                    continue;
                }
                RepairDecision decision = classifyCell(ctx, currentTemplate, report, cell);
                if (decision.blocksBuilding()) {
                    suspendCell(ctx, registry, cell, decision.reason());
                    return RepairOutcome.SUSPENDED;
                }
                if (decision.needsRepair()) {
                    return startRepair(ctx, registry, cell, decision);
                }
            }
        }
        return RepairOutcome.PROCEED;
    }

    /**
     * The decision of the domain rule about one cell of the volume of the house.
     *
     * @param ctx     the context of the automation
     * @param template the project of the house
     * @param report  the comparison of the volume with the project
     * @param cell    the cell of the house, relative to the origin A
     * @return the decision of the rule
     */
    private RepairDecision classifyCell(AutomationContext ctx, HouseTemplate template,
                                         VerificationReport report, Cell cell) {
        Vec3i origin = currentHouse.origin();
        ObservedBlock observed = ctx.world().read(origin.add(cell.x(), cell.y(), cell.z()));
        boolean partnerInVolume = true;
        boolean partnerMatches = false;
        Cell partner = RepairPolicy.partnerCell(cell, observed);
        if (partner != null) {
            partnerInVolume = partner.inVolume();
            if (partnerInVolume) {
                partnerMatches = report.verdict(partner) == CellVerdict.MATCH;
            }
        }
        return RepairPolicy.classify(cell, new RepairPolicy.Facts(template.spec(cell), observed,
                partnerInVolume, partnerMatches, liquidTouches(ctx, origin, cell),
                template.isProjectMaterial(observed == null ? null : observed.blockId())));
    }

    /** True when a liquid touches the cell; the repair of such a cell is refused. */
    private boolean liquidTouches(AutomationContext ctx, Vec3i origin, Cell cell) {
        Vec3i position = origin.add(cell.x(), cell.y(), cell.z());
        for (Vec3i offset : LIQUID_NEIGHBOURS) {
            ObservedBlock block = ctx.world().read(position.add(offset));
            if (block.known() && block.liquid()) {
                return true;
            }
        }
        return false;
    }

    /**
     * Asks the client to remove the wrong block of the cell.
     *
     * <p>The client breaks the block with an ordinary attack of the player and reads the cell
     * again; the automation waits for the world and continues when the cell is empty. When the
     * block is still there after the budget of the repair, the house is suspended with the
     * reason {@code obstructed}, the coordinate of the cell and the concrete reason.
     *
     * @param ctx      the context of the automation
     * @param registry the registry of the houses
     * @param cell     the cell of the house, relative to the origin A
     * @param decision the decision of the domain rule
     * @return the outcome of the step
     */
    private RepairOutcome startRepair(AutomationContext ctx, HouseRegistry registry, Cell cell,
                                       RepairDecision decision) {
        Vec3i position = currentHouse.origin().add(cell.x(), cell.y(), cell.z());
        if (repairCell == null || !repairCell.equals(cell)) {
            repairCell = cell;
            repairAttempts = 0;
            repairSinceActiveMillis = activeMillis;
        }
        if (repairAttempts == 0) {
            if (repairsWithoutPlacement >= MAX_REPAIRS_WITHOUT_PLACEMENT) {
                suspend(SuspensionReason.OBSTRUCTED, ctx, registry,
                        position.x() + "," + position.y() + "," + position.z()
                                + ":repeated obstructions without confirmed building progress");
                return RepairOutcome.SUSPENDED;
            }
            if (!ctx.requestRepair(position)) {
                suspend(SuspensionReason.OBSTRUCTED, ctx, registry,
                        position.x() + "," + position.y() + "," + position.z()
                                + ":the client cannot remove the block");
                return RepairOutcome.SUSPENDED;
            }
            repairAttempts++;
            repairsWithoutPlacement++;
            ctx.observe("repair_requested", "cell=" + cell.x() + "," + cell.y() + "," + cell.z()
                    + ", position=" + position.x() + "," + position.y() + "," + position.z()
                    + ", reason=" + decision.reason());
            return RepairOutcome.WAITING;
        }
        if (activeMillis - repairSinceActiveMillis >= REPAIR_TIMEOUT_MILLIS) {
            suspend(SuspensionReason.OBSTRUCTED, ctx, registry,
                    position.x() + "," + position.y() + "," + position.z()
                            + ":the wrong block was not removed");
            return RepairOutcome.SUSPENDED;
        }
        return RepairOutcome.WAITING;
    }

    /**
     * Suspends the house because the repair of the cell is not applicable.
     *
     * @param ctx      the context of the automation
     * @param registry the registry of the houses
     * @param cell     the cell of the house, relative to the origin A
     * @param reason   the concrete reason of the refusal
     */
    private void suspendCell(AutomationContext ctx, HouseRegistry registry, Cell cell, String reason) {
        Vec3i position = currentHouse.origin().add(cell.x(), cell.y(), cell.z());
        suspend(SuspensionReason.OBSTRUCTED, ctx, registry,
                position.x() + "," + position.y() + "," + position.z() + ":" + reason);
    }

    /** Outcome of the supply step. */
    private enum SupplyOutcome {
        /** The needed item is in the inventory: the placement may start. */
        READY,
        /** The delivery is still running: the automation waits for the confirmation. */
        WAIT,
        /** The automation was suspended. */
        SUSPENDED
    }

    /**
     * The supply step of one item type.
     *
     * <p>The next needed type is requested with {@code give @s minecraft:<item> <deficit>}; the
     * deficit counts the unfinished cells of the project minus the items in the inventory. The
     * delivery is confirmed by the inventory, never by the sent command. Without a confirmation
     * after four seconds of active work the automation is suspended and the give command is not
     * repeated automatically.
     */
    private SupplyOutcome supplyStep(AutomationContext ctx, HouseRegistry registry, HouseTemplate currentTemplate,
                                     VerificationReport report, CellSpec.Operation target) {
        int available = ctx.inventoryCount(target.itemId());
        if (giveItemId != null) {
            // The confirmation follows the item that was asked for, not the item that the work
            // needs now: the target of the work may change while the delivery is on its way.
            //
            // The delivery is confirmed by the inventory. The material that the add-on spent in
            // the meantime (the blocks of the project and the temporary supports of the climb)
            // left the inventory as well, so the count of the inventory alone does not show the
            // delivery. The spending of the add-on is counted back: the delivered number is the
            // growth of the inventory plus the spending, and it is zero without a delivery
            // (ТЗ.md section 4).
            int deliveredNow = ctx.inventoryCount(giveItemId);
            int consumedNow = ctx.consumedItemCount(giveItemId);
            int delivered = deliveredNow - giveBaselineCount + (consumedNow - giveBaselineConsumed);
            if (delivered > 0) {
                ctx.observe("give_confirmed", "item=" + giveItemId + ", inventory_before=" + giveBaselineCount
                        + ", inventory_now=" + deliveredNow
                        + ", consumed_before=" + giveBaselineConsumed
                        + ", consumed_now=" + consumedNow
                        + ", delivered=" + delivered);
                clearGiveState();
            }
        }

        Map<String, Integer> inventory = inventorySnapshot(ctx, currentTemplate);
        Set<Cell> unfinished = unfinishedCells(report);
        List<ItemNeed> needs = SupplyCalculator.needs(currentTemplate, unfinished, inventory);
        int required = 0;
        for (ItemNeed need : needs) {
            if (need.itemId().equals(target.itemId())) {
                required = need.required();
            }
        }
        int deficit = Math.max(0, required - available);

        if (deficit == 0) {
            clearGiveState();
            return available > 0 ? SupplyOutcome.READY : SupplyOutcome.WAIT;
        }

        if (!ctx.inventoryHasSpace()) {
            suspend(SuspensionReason.INVENTORY_FULL, ctx, registry);
            return SupplyOutcome.SUSPENDED;
        }
        GiveAvailability availability = ctx.giveCommandState();
        if (availability == GiveAvailability.ABSENT) {
            // The command tree of the server was read and it holds no give: the materials are
            // missing for good.
            suspend(SuspensionReason.MATERIALS_MISSING, ctx, registry);
            return SupplyOutcome.SUSPENDED;
        }
        if (availability == GiveAvailability.UNKNOWN) {
            // The command tree of the server was not read yet. That is not a missing command:
            // the automation waits until the tree is known.
            //
            // The decision about the boundary: the wait is bounded. As long as the tree is not
            // read, the house waits with the reason give_unknown; the house is not suspended
            // with materials_missing, because that reason states a fact about the tree that was
            // never read. The command may still appear, and the automation continues as soon as
            // the tree arrives.
            //
            // The status shows the actual state (ТЗ.md section 6): the automation waits for the
            // tree of the server, it does not stay in the state building with an empty reason.
            if (giveUnknownSinceActiveMillis < 0L) {
                giveUnknownSinceActiveMillis = activeMillis;
            }
            long waited = activeMillis - giveUnknownSinceActiveMillis;
            state = AutomationState.WAITING;
            reason = "give_unknown";
            if (waited >= GIVE_TREE_WAIT_TIMEOUT_MILLIS) {
                ctx.observe("give_unknown_timeout", "tick=" + ticks + ", the command tree of the server was not "
                        + "read within " + GIVE_TREE_WAIT_TIMEOUT_MILLIS
                        + " ms of active work, the automation waits for it");
                return SupplyOutcome.WAIT;
            }
            ctx.observe("give_unknown", "tick=" + ticks + ", the command tree of the server was not read yet, "
                    + "the automation has waited " + waited + " ms of active work");
            return SupplyOutcome.WAIT;
        }
        // The tree of the server is known again: the boundary of the wait starts from scratch and
        // the status shows the work of the house again.
        giveUnknownSinceActiveMillis = -1L;
        state = AutomationState.BUILDING;
        reason = null;

        if (giveQueue.isEmpty()) {
            giveQueue = GiveCommandPlanner.plan(List.of(new ItemNeed(target.itemId(), required, available)));
            giveIndex = 0;
        }

        if (giveIndex < giveQueue.size() && rateLimiter.tryAcquire(ctx.nowMillis())) {
            ctx.observe("give_sent", "item=" + target.itemId() + ", deficit=" + deficit
                    + ", command=" + giveQueue.get(giveIndex) + ", inventory=" + available);
            ctx.sendCommand(giveQueue.get(giveIndex));
            giveIndex++;
            if (giveSinceActiveMillis < 0L) {
                giveSinceActiveMillis = activeMillis;
                giveItemId = target.itemId();
                giveBaselineCount = available;
                giveBaselineConsumed = ctx.consumedItemCount(target.itemId());
            }
        }

        if (giveSinceActiveMillis >= 0L && activeMillis - giveSinceActiveMillis >= SUPPLY_CONFIRM_TIMEOUT_MILLIS) {
            // The inventory did not confirm the delivery; the give command is not repeated.
            // The reason names the cell of the work and the observed state of the inventory, so
            // that the suspension can be read (ТЗ.md section 4).
            Cell cell = target.cells().get(0);
            Vec3i position = currentHouse.origin().add(cell.x(), cell.y(), cell.z());
            suspend(SuspensionReason.SUPPLY_UNCONFIRMED, ctx, registry,
                    position.x() + "," + position.y() + "," + position.z()
                            + ":the item " + target.itemId() + " was not confirmed; the inventory holds "
                            + ctx.inventoryCount(target.itemId()) + " items of it");
            return SupplyOutcome.SUSPENDED;
        }
        return available > 0 ? SupplyOutcome.READY : SupplyOutcome.WAIT;
    }

    private void clearGiveState() {
        giveItemId = null;
        giveSinceActiveMillis = -1L;
        giveQueue = new ArrayList<>();
        giveIndex = 0;
    }

    private void placementStep(AutomationContext ctx, HouseRegistry registry, HouseTemplate currentTemplate,
                               VerificationReport report, CellSpec.Operation target, PlacementTarget placementTarget) {
        boolean confirmed = operationMatches(report, target);
        if (confirmed) {
            pendingOperation = null;
            pendingSinceActiveMillis = -1L;
            placementAttempts = 0;
            // The world has the operation; the client stops the interaction.
            ctx.confirmPlacement(target);
            return;
        }

        if (pendingOperation == null || !pendingOperation.equals(target)) {
            pendingOperation = target;
            placementAttempts = 0;
            pendingSinceActiveMillis = -1L;
            if (!ctx.requestPlacement(target, currentHouse.origin(), placementTarget)) {
                placementUnavailable(ctx, registry, placementTarget, "no admissible placement");
                return;
            }
        }

        PlacementSession.Snapshot progress = ctx.placementProgress(target, currentHouse.origin());
        if (progress.stage() == PlacementSession.Stage.DEFERRED) {
            // The cells of the approach of this operation were all tried: the operation waits
            // for a later try, and the house goes on with the next one. After a few tries the
            // house is suspended with the coordinate and the reason (ТЗ.md section 4).
            Cell cell = target.cells().get(0);
            int tries = deferralCounts.merge(cell, 1, Integer::sum);
            if (tries >= MAX_DEFERRALS) {
                Vec3i position = currentHouse.origin().add(cell.x(), cell.y(), cell.z());
                deferredCells.clear();
                deferralCounts.clear();
                suspend(SuspensionReason.NO_PLACEMENT_SUPPORT, ctx, registry,
                        position.x() + "," + position.y() + "," + position.z() + ":" + progress.detail());
                return;
            }
            deferredCells.add(cell);
            pendingOperation = null;
            pendingSinceActiveMillis = -1L;
            return;
        }
        if (progress.failed()) {
            placementUnavailable(ctx, registry, placementTarget, progress.detail());
            return;
        }
        if (progress.interactions() > placementAttempts) {
            // Only an actual interaction starts the world-confirmation window. Navigation,
            // view/slot synchronization and retry preparation do not consume that window.
            placementAttempts = progress.interactions();
            pendingSinceActiveMillis = activeMillis;
        }
        if (pendingSinceActiveMillis < 0L
                || activeMillis - pendingSinceActiveMillis < PLACEMENT_CONFIRM_TIMEOUT_MILLIS) return;

        if (placementAttempts >= MAX_PLACEMENT_ATTEMPTS) {
            Cell cell = target.cells().get(0);
            Vec3i position = currentHouse.origin().add(cell.x(), cell.y(), cell.z());
            ObservedBlock observed = ctx.world().read(position);
            suspend(SuspensionReason.PLACEMENT_UNCONFIRMED, ctx, registry,
                    position.x() + "," + position.y() + "," + position.z() + ":" + observed.blockId());
            return;
        }
        // The world was read above, before this retry decision. Polls never restart the executor.
        if (!ctx.retryPlacement(target, currentHouse.origin())) {
            placementUnavailable(ctx, registry, placementTarget, "retry could not be armed");
            return;
        }
        pendingSinceActiveMillis = -1L;

    }

    private void placementUnavailable(AutomationContext ctx, HouseRegistry registry,
                                      PlacementTarget target, String detail) {
        Cell cell = target.cell();
        Vec3i position = currentHouse.origin().add(cell.x(), cell.y(), cell.z());
        suspend(SuspensionReason.NO_PLACEMENT_SUPPORT, ctx, registry,
                position.x() + "," + position.y() + "," + position.z() + ":" + detail);
    }


    // --- helpers ---------------------------------------------------------------------------------

    private VerificationReport readReport(AutomationContext ctx, HouseTemplate currentTemplate) {
        Map<Cell, ObservedBlock> observed = new LinkedHashMap<>();
        for (Cell cell : currentTemplate.volumeCells()) {
            Vec3i position = currentHouse.origin().add(cell.x(), cell.y(), cell.z());
            observed.put(cell, ctx.world().read(position));
        }
        return VerificationReport.of(currentTemplate, observed);
    }

    private boolean stillMatches(HouseRecord record, AutomationContext ctx) {
        HouseTemplate recordTemplate = HouseTemplate.forVariant(record.variant());
        Map<Cell, ObservedBlock> observed = new LinkedHashMap<>();
        for (Cell cell : recordTemplate.volumeCells()) {
            Vec3i position = record.origin().add(cell.x(), cell.y(), cell.z());
            observed.put(cell, ctx.world().read(position));
        }
        return VerificationReport.of(recordTemplate, observed).complete();
    }

    private boolean atFrontPoint(AutomationContext ctx) {
        Vec3i front = SiteRules.frontPoint(currentHouse.origin());
        Vec3i player = ctx.playerPosition();
        // The rule of the arrival lives in the core of the placement: the player has to stand on
        // the front point itself, a place near it is not the front point (ТЗ.md section 4).
        return ArrivalPolicy.atFrontPoint(player.x(), player.y(), player.z(),
                front.x(), front.y(), front.z());
    }

    private Vec3i currentTarget() {
        if (currentHouse == null) {
            return null;
        }
        if (pendingOperation == null) {
            return currentHouse.origin();
        }
        Cell cell = pendingOperation.cells().get(0);
        return currentHouse.origin().add(cell.x(), cell.y(), cell.z());
    }

    private Set<Cell> unfinishedCells(VerificationReport report) {
        Set<Cell> unfinished = new HashSet<>();
        for (Cell cell : report.template().occupiedCells()) {
            if (report.verdict(cell) != CellVerdict.MATCH) {
                unfinished.add(cell);
            }
        }
        return unfinished;
    }

    /** The cells that already correspond to the project. */
    private Set<Cell> placedCells(VerificationReport report) {
        Set<Cell> placed = new HashSet<>();
        for (Cell cell : report.template().occupiedCells()) {
            if (report.verdict(cell) == CellVerdict.MATCH) {
                placed.add(cell);
            }
        }
        return placed;
    }

    /**
     * True when every cell of the operation corresponds to the project in the last reading of the
     * world. The world confirms the operation in that case.
     */
    private static boolean operationMatches(VerificationReport report, CellSpec.Operation operation) {
        for (Cell cell : operation.cells()) {
            if (report.verdict(cell) != CellVerdict.MATCH) {
                return false;
            }
        }
        return true;
    }

    /** True when the block of the relative cell is solid in the world. */
    private boolean solidAt(AutomationContext ctx, Vec3i origin, Cell cell) {
        return ctx.world().read(origin.add(cell.x(), cell.y(), cell.z())).solidFull();
    }

    private Map<String, Integer> inventorySnapshot(AutomationContext ctx, HouseTemplate currentTemplate) {
        Map<String, Integer> inventory = new HashMap<>();
        for (String itemId : currentTemplate.itemNeeds().keySet()) {
            inventory.put(itemId, ctx.inventoryCount(itemId));
        }
        return inventory;
    }



    private void suspend(SuspensionReason suspensionReason, AutomationContext ctx, HouseRegistry registry) {
        suspend(suspensionReason, ctx, registry, null);
    }

    /**
     * Suspends the automation.
     *
     * <p>The wire name of the reason is the state of the automation; the record of the house also
     * carries the detail (a coordinate or an observed state) when there is one.
     */
    private void suspend(SuspensionReason suspensionReason, AutomationContext ctx, HouseRegistry registry,
                         String detail) {
        state = AutomationState.SUSPENDED;
        suspended = true;
        reason = suspensionReason.wireName();
        pendingOperation = null;
        pendingSinceActiveMillis = -1L;
        placementAttempts = 0;
        repairCell = null;
        repairAttempts = 0;
        repairSinceActiveMillis = -1L;
        giveQueue = new ArrayList<>();
        giveIndex = 0;
        giveSinceActiveMillis = -1L;
        giveItemId = null;
        if (currentHouse != null) {
            currentHouse.setState(HouseState.SUSPENDED);
            currentHouse.setReason(detail == null ? suspensionReason.wireName()
                    : suspensionReason.wireName() + "@" + detail);
            currentHouse.setStage("suspended");
            if (registry != null) {
                save(registry, ctx);
            }
        }
    }

    private boolean save(HouseRegistry registry, AutomationContext ctx) {
        try {
            ctx.saveRegistry(registry);
        } catch (Exception e) {
            state = AutomationState.SUSPENDED;
            suspended = true;
            reason = SuspensionReason.STORAGE_ERROR.wireName();
            return false;
        }
        if (ctx.registryWriteFailed()) {
            // The file could not be written; the building does not continue without a saved record.
            state = AutomationState.SUSPENDED;
            suspended = true;
            reason = SuspensionReason.STORAGE_ERROR.wireName();
            return false;
        }
        return true;
    }

    public static String nowIso() {
        return Instant.now().truncatedTo(ChronoUnit.SECONDS).toString();
    }
}
