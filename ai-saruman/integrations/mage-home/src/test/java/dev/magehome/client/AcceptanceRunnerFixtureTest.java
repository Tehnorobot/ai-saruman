package dev.magehome.client;

import org.junit.jupiter.api.Test;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;

class AcceptanceRunnerFixtureTest {
    @Test
    void everyMaterialMustArriveWithTheRequiredCount() {
        var needs = Map.of("minecraft:stone_bricks", 49, "minecraft:glass", 5);
        assertFalse(LabScenarioFixture.hasMaterials(needs, Map.of("minecraft:stone_bricks", 49)));
        assertFalse(LabScenarioFixture.hasMaterials(needs, Map.of("minecraft:stone_bricks", 48, "minecraft:glass", 5)));
        assertTrue(LabScenarioFixture.hasMaterials(needs, Map.of("minecraft:stone_bricks", 49, "minecraft:glass", 5)));
    }

    @Test
    void stopRequiresSingleBuildingOwnActiveRootAndActualFirstBlock() {
        assertFalse(AcceptanceRunner.stopPrerequisites(false, true, true));
        assertFalse(AcceptanceRunner.stopPrerequisites(true, false, true));
        assertFalse(AcceptanceRunner.stopPrerequisites(true, true, false));
        assertTrue(AcceptanceRunner.stopPrerequisites(true, true, true));
    }
}
