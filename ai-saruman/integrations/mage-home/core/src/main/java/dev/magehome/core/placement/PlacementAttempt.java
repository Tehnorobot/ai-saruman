package dev.magehome.core.placement;

import dev.magehome.core.model.Cell;

import java.util.List;

/**
 * One admissible way to fill a cell: the block that is clicked and the places where the player
 * can stand for the click.
 *
 * <p>The access candidates are ordered by preference; the client module uses the first one that
 * is free in the world and that reaches the clicked face.
 */
public record PlacementAttempt(Cell supportCell, Support support, List<Cell> accessCandidates) {

    public PlacementAttempt {
        accessCandidates = List.copyOf(accessCandidates);
    }
}
