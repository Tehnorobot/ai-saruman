package dev.magehome.core.placement;

import dev.magehome.core.template.CellSpec;

/** One operation of the project with the target that the planner found for it. */
public record PlannedPlacement(CellSpec.Operation operation, PlacementTarget target) {
}
