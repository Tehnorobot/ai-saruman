package dev.magehome.core;

import dev.magehome.core.automation.AutomationController;
import dev.magehome.core.automation.AutomationState;
import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.placement.PlacementSession;
import dev.magehome.core.registry.HouseRecord;
import dev.magehome.core.registry.HouseState;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.template.HouseTemplate;
import dev.magehome.core.testing.FakeContext;
import dev.magehome.core.testing.FakeWorld;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class PlacementTimingTest {
    private static final Vec3i ORIGIN = new Vec3i(100, 64, 200);

    private static final class Harness {
        final FakeContext context = new FakeContext();
        final AutomationController controller = new AutomationController();

        Harness() {
            context.world = FakeWorld.withSite(ORIGIN);
            context.player = ORIGIN.add(3, 0, -1);
            context.inventory.put("minecraft:stone_bricks", 64);
            context.immediatePlacementSend = false;
            HouseRecord record = new HouseRecord("timing", context.worldKey, context.dimension,
                    context.playerUuid, ORIGIN, 0, HouseState.BUILDING, "placement", 0, null, null);
            record.setTemplateId(AutomationController.CURRENT_TEMPLATE_ID);
            context.registry.add(record);
            controller.enable();
            controller.resume("timing", context);
            controller.tick(context);
            assertEquals(1, context.placementOperations.size());
        }

        void tick(long millis) {
            context.advance(millis);
            controller.tick(context);
        }

        void send() {
            context.placementSession.sent();
            tick(0);
        }
    }

    @Test
    void slowApproachAndAimDoNotConsumeTheConfirmationWindow() {
        Harness h = new Harness();
        for (int i = 0; i < 240; i++) h.tick(50);
        assertEquals(AutomationState.BUILDING, h.controller.state());
        assertEquals(1, h.context.placementRequests.size());
        assertEquals(0, h.context.placementRetryRequests);
        assertEquals(0, h.context.placementSession.interactions());
        h.send();
        h.tick(2999);
        assertEquals(0, h.context.placementRetryRequests);
        h.tick(1);
        assertEquals(1, h.context.placementRetryRequests);
    }

    @Test
    void retryPreparationDoesNotCountAsASecondSendOrRestartEveryTick() {
        Harness h = new Harness();
        h.send();
        h.tick(3000);
        for (int i = 0; i < 240; i++) h.tick(50);
        assertEquals(AutomationState.BUILDING, h.controller.state());
        assertEquals(1, h.context.placementRetryRequests);
        assertEquals(1, h.context.placementSession.interactions());
        h.send();
        h.tick(2999);
        assertEquals(AutomationState.BUILDING, h.controller.state());
        h.tick(1);
        assertEquals("placement_unconfirmed", h.controller.reason());
        assertEquals(2, h.context.placementSession.interactions());
        assertEquals(1, h.context.placementRetryRequests);
    }

    @Test
    void worldConfirmationAtTheTimeoutBoundarySuppressesTheRetry() {
        Harness h = new Harness();
        h.send();
        h.context.world.applyOperation(HouseTemplate.forVariant(0), h.context.placementOperations.get(0), ORIGIN);
        h.tick(3000);
        assertEquals(0, h.context.placementRetryRequests);
        assertEquals(1, h.context.confirmedPlacements.size());
        assertNotEquals(AutomationState.SUSPENDED, h.controller.state());
    }

    @Test
    void temporaryGatesFreezeTimeIncludingTheIntervalBeforeReopening() {
        for (int gate = 0; gate < 3; gate++) {
            Harness h = new Harness();
            h.send();
            h.tick(1000);
            if (gate == 0) h.context.paused = true;
            if (gate == 1) h.context.foreign = true;
            if (gate == 2) h.context.chainAvailable = false;
            h.tick(50);
            h.context.advance(100000);
            h.context.paused = false;
            h.context.foreign = false;
            h.context.chainAvailable = true;
            h.tick(0);
            assertEquals(0, h.context.placementRetryRequests, "gate " + gate);
            h.tick(1999);
            assertEquals(0, h.context.placementRetryRequests);
            h.tick(1);
            assertEquals(1, h.context.placementRetryRequests);
        }
    }

    @Test
    void exhaustedApproachesReportTheirCauseWithoutPretendingAnInteractionWasSent() {
        Harness h = new Harness();
        h.context.placementSession.update(PlacementSession.Stage.EXHAUSTED, "all approaches blocked; precondition=REACH");
        h.tick(50);
        assertEquals("no_placement_support", h.controller.reason());
        assertEquals(0, h.context.placementSession.interactions());
        assertEquals(0, h.context.placementRetryRequests);
        assertTrue(h.controller.currentHouse().reason().contains("all approaches blocked"));
        assertTrue(h.controller.currentHouse().reason().contains("100,64,200"));
    }

    private static CellSpec.Operation operation() {
        return new CellSpec.Operation("minecraft:stone_bricks", List.of(new Cell(0, 0, 0)), CellSpec.Placement.SINGLE);
    }

    @Test
    void repeatedRequestsAndPollsPreserveAimSendAndPreparationProgress() {
        PlacementSession session = new PlacementSession();
        CellSpec.Operation op = operation();
        assertTrue(session.begin(op, ORIGIN, op.cells().get(0)));
        session.update(PlacementSession.Stage.AIMING, "view is moving");
        for (int i = 0; i < 9; i++) {
            assertFalse(session.begin(op, ORIGIN, op.cells().get(0)));
            assertEquals(PlacementSession.Stage.AIMING, session.progress(op, ORIGIN).stage());
            assertFalse(session.preparationExpired(10));
        }
        assertTrue(session.preparationExpired(10), "polling did not reset preparation");
        session.sent();
        for (int i = 0; i < 20; i++) {
            assertFalse(session.begin(op, ORIGIN, op.cells().get(0)));
            assertEquals(1, session.progress(op, ORIGIN).interactions());
            assertFalse(session.maySend());
        }
        assertTrue(session.armRetry());
        assertFalse(session.armRetry());
        session.sent();
        assertFalse(session.armRetry(), "two real sends remain the maximum");
        assertFalse(session.maySend());
    }

    @Test
    void doorwayRemainsOpenUntilAllFurnitureOperationsAreScheduled() {
        var operations = HouseTemplate.forVariant(0).operations();
        assertEquals(CellSpec.Placement.DOOR, operations.get(operations.size() - 1).placement());
        assertTrue(operations.stream().limit(operations.size() - 1L)
                .anyMatch(op -> op.placement() == CellSpec.Placement.BED));
    }

    @Test
    void simulatedFutureSupportsAreNotUsableInTheCurrentWorld() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Cell roofCell = new Cell(0, 4, 0);
        CellSpec.Operation roof = template.operations().stream()
                .filter(op -> op.cells().contains(roofCell)).findFirst().orElseThrow();
        var noStandingBlocks = java.util.Set.<Cell>of();
        assertNull(dev.magehome.core.placement.PlacementPlanner.targetFor(template, roof,
                noStandingBlocks, cell -> false));
        var wallBelow = java.util.Set.of(new Cell(0, 3, 0));
        assertNotNull(dev.magehome.core.placement.PlacementPlanner.targetFor(template, roof,
                wallBelow, cell -> false));
    }

    @Test
    void deferredOperationIsRearmedOnlyByANewRequest() {
        PlacementSession session = new PlacementSession();
        CellSpec.Operation op = operation();
        assertTrue(session.begin(op, ORIGIN, op.cells().get(0)));
        session.update(PlacementSession.Stage.DEFERRED, "all approaches tried");
        assertEquals(PlacementSession.Stage.DEFERRED, session.progress(op, ORIGIN).stage());
        assertTrue(session.begin(op, ORIGIN, op.cells().get(0)));
        assertEquals(PlacementSession.Stage.APPROACHING, session.progress(op, ORIGIN).stage());
        assertFalse(session.begin(op, ORIGIN, op.cells().get(0)));
        assertEquals(0, session.interactions());
    }

    @Test
    void everyPreparationStageCanExhaustItsAccessWithoutChargingASend() {
        for (PlacementSession.Stage stage : List.of(PlacementSession.Stage.APPROACHING,
                PlacementSession.Stage.AIMING, PlacementSession.Stage.SYNCHRONIZING)) {
            PlacementSession session = new PlacementSession();
            session.begin(operation(), ORIGIN, new Cell(0, 0, 0));
            session.update(stage, "stalled");
            assertFalse(session.preparationExpired(2));
            assertTrue(session.preparationExpired(2));
            session.nextAccess();
            assertFalse(session.preparationExpired(2));
            assertEquals(0, session.interactions());
        }
    }

    @Test
    void lateConfirmationCancellationAndAnotherOriginCannotInheritArmedWork() {
        PlacementSession session = new PlacementSession();
        CellSpec.Operation op = operation();
        session.begin(op, ORIGIN, op.cells().get(0));
        session.sent();
        session.armRetry();
        session.update(PlacementSession.Stage.CONFIRMED, "world matched before retry dispatch");
        assertFalse(session.maySend());
        assertFalse(session.armRetry());
        assertEquals(PlacementSession.Stage.IDLE, session.progress(op, ORIGIN.add(10, 0, 0)).stage());
        session.clear();
        assertFalse(session.maySend());
        assertTrue(session.begin(op, ORIGIN, op.cells().get(0)));
        assertEquals(0, session.interactions());
        assertTrue(session.begin(op, ORIGIN.add(10, 0, 0), op.cells().get(0)));
        assertEquals(0, session.interactions());
    }
}
