package dev.magehome.core.placement;

import dev.magehome.core.model.Cell;

import java.util.List;

/**
 * The result of the planner: the operations in the order in which they can be performed and the
 * cell that has no admissible support when the plan is not complete.
 *
 * <p>An incomplete plan means that the remaining cells cannot be reached with the blocks of the
 * project; the automation suspends with {@code no_placement_support} and the coordinate of the
 * cell (ТЗ.md section 4).
 */
public record PlacementPlan(List<PlannedPlacement> placements, Cell unsupportedCell) {

    public PlacementPlan {
        placements = List.copyOf(placements);
    }

    public boolean complete() {
        return unsupportedCell == null;
    }

    /** The operation that has to be performed next. */
    public PlannedPlacement first() {
        return placements.isEmpty() ? null : placements.get(0);
    }
}
