package dev.magehome.core;

import dev.magehome.core.automation.AutomationController;
import dev.magehome.core.automation.AutomationState;
import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.placement.PlacementSession;
import dev.magehome.core.registry.HouseRecord;
import dev.magehome.core.registry.HouseState;
import dev.magehome.core.template.HouseTemplate;
import dev.magehome.core.testing.FakeContext;
import dev.magehome.core.testing.FakeWorld;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class DeferredQueueTest {
    private static final Vec3i ORIGIN = new Vec3i(100, 64, 200);
    private static final Cell GAP = new Cell(0, 0, 0);
    private static final String EXHAUSTED = "all approaches tried";

    /** A previously built house with exactly one unfinished operation, not a simulated success. */
    private static void addHouse(FakeContext ctx, String id, Vec3i origin) {
        ctx.world = FakeWorld.withSite(origin);
        ctx.world.applyHouse(HouseTemplate.forVariant(0), origin);
        ctx.world.air(origin.add(GAP.x(), GAP.y(), GAP.z()));
        HouseRecord record = new HouseRecord(id, ctx.worldKey, ctx.dimension, ctx.playerUuid,
                origin, 0, HouseState.SUSPENDED, "suspended", 0, null, null);
        record.setTemplateId(AutomationController.CURRENT_TEMPLATE_ID);
        ctx.registry.add(record);
        ctx.player = origin.add(3, 0, -1);
    }

    private static final class Harness {
        final FakeContext ctx = new FakeContext();
        final AutomationController controller = new AutomationController();

        Harness() {
            ctx.immediatePlacementSend = false;
            ctx.inventory.put("minecraft:stone_bricks", 64);
            addHouse(ctx, "first", ORIGIN);
            controller.enable();
            assertTrue(controller.resume("first", ctx).contains("accepted"));
        }

        void startAttempt(int expectedRequests) {
            controller.tick(ctx);
            assertEquals(AutomationState.BUILDING, controller.state());
            assertEquals(expectedRequests, ctx.placementOperations.size(), "a new executor request is required");
            assertEquals(GAP, ctx.placementOperations.get(expectedRequests - 1).cells().get(0));
            assertEquals(PlacementSession.Stage.APPROACHING, ctx.placementSession.progress(
                    ctx.placementOperations.get(expectedRequests - 1), controller.currentHouse().origin()).stage());
            assertEquals(0, ctx.placementSession.interactions());
            controller.tick(ctx);
            assertEquals(expectedRequests, ctx.placementOperations.size(), "polling must not count as another try");
        }

        void defer() {
            ctx.placementSession.update(PlacementSession.Stage.DEFERRED, EXHAUSTED);
            controller.tick(ctx);
        }
    }

    @Test
    void actualDeferredAttemptsAreBoundedAndResumeStartsFresh() {
        Harness h = new Harness();
        for (int attempt = 1; attempt <= AutomationController.MAX_DEFERRALS; attempt++) {
            h.startAttempt(attempt);
            h.defer();
            if (attempt < AutomationController.MAX_DEFERRALS) {
                assertEquals(AutomationState.BUILDING, h.controller.state());
            }
        }
        assertEquals(AutomationState.SUSPENDED, h.controller.state());
        assertEquals("no_placement_support", h.controller.reason());
        assertEquals(HouseState.SUSPENDED, h.ctx.registry.byId("first").state());
        assertTrue(h.ctx.registry.byId("first").reason().contains("100,64,200"));
        assertTrue(h.ctx.registry.byId("first").reason().contains(EXHAUSTED));
        assertTrue(h.ctx.confirmedPlacements.isEmpty());
        assertTrue(h.ctx.world.read(ORIGIN).isAir());
        h.controller.tick(h.ctx);
        assertEquals(AutomationController.MAX_DEFERRALS, h.ctx.placementOperations.size());

        assertTrue(h.controller.resume("first", h.ctx).contains("accepted"));
        h.startAttempt(AutomationController.MAX_DEFERRALS + 1);
        h.defer();
        assertEquals(AutomationState.BUILDING, h.controller.state(), "resume grants a new budget");
        h.startAttempt(AutomationController.MAX_DEFERRALS + 2);
    }

    @Test
    void interruptedAttemptDoesNotSpendTheResumeBudget() {
        Harness h = new Harness();
        h.startAttempt(1);
        h.defer();
        h.controller.interrupt(h.ctx);
        assertEquals(AutomationState.SUSPENDED, h.controller.state());
        assertTrue(h.controller.resume("first", h.ctx).contains("accepted"));
        for (int attempt = 1; attempt <= AutomationController.MAX_DEFERRALS; attempt++) {
            h.startAttempt(attempt + 1);
            h.defer();
            assertEquals(attempt == AutomationController.MAX_DEFERRALS
                    ? AutomationState.SUSPENDED : AutomationState.BUILDING, h.controller.state());
        }
    }

    @Test
    void newHouseDoesNotInheritAnotherHousesDeferredBudget() {
        Harness h = new Harness();
        h.startAttempt(1);
        h.defer();
        h.controller.interrupt(h.ctx);
        Vec3i nextOrigin = ORIGIN.add(200, 0, 0);
        addHouse(h.ctx, "second", nextOrigin);
        assertTrue(h.controller.resume("second", h.ctx).contains("accepted"));
        for (int attempt = 1; attempt <= AutomationController.MAX_DEFERRALS; attempt++) {
            h.startAttempt(attempt + 1);
            h.defer();
            assertEquals(attempt == AutomationController.MAX_DEFERRALS
                    ? AutomationState.SUSPENDED : AutomationState.BUILDING, h.controller.state());
        }
        assertEquals("second", h.controller.currentHouse().houseId());
        assertTrue(h.controller.currentHouse().reason().contains("300,64,200"));
        assertTrue(h.ctx.world.read(nextOrigin).isAir());
    }
}
