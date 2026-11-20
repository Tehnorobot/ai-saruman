package dev.magehome.core.placement;

import dev.magehome.core.model.Cell;

import java.util.List;
import java.util.Map;

/**
 * The target of one placement operation: the cell that is filled, the block that the project
 * requires there and the admissible ways to place it.
 *
 * <p>The required properties are the ones of ТЗ.md section 2 (facing, half, hinge, part, axis,
 * hanging). The client module compares them with the state that the real vanilla placement would
 * produce, so a wrong orientation never reaches the world.
 */
public record PlacementTarget(Cell cell, String blockId, Map<String, String> requiredProperties,
                              List<PlacementAttempt> attempts) {

    public PlacementTarget {
        requiredProperties = Map.copyOf(requiredProperties);
        attempts = List.copyOf(attempts);
    }

    public boolean hasAttempt() {
        return !attempts.isEmpty();
    }
}
