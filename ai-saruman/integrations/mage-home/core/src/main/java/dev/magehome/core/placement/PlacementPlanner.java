package dev.magehome.core.placement;

import dev.magehome.core.model.Cell;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.template.HouseTemplate;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Predicate;

/**
 * The order of the placement and the support of every cell.
 *
 * <p>The planner walks through the project of the house and simulates the blocks that are already
 * standing. A cell can be filled when the block below it is solid (the block is clicked on its
 * top face) or when a horizontal neighbour is solid (the block is clicked on the face that points
 * to the cell). The inner cells of the roof have no block below them; they are placed from the
 * side of the ring that is already standing. No temporary block and no scaffolding is used
 * (ТЗ.md section 4).
 *
 * <p>The planner is a pure function: it never touches the game and can be checked without a
 * client.
 */
public final class PlacementPlanner {

    /** The level of the free cells of the floor inside the house. */
    private static final int FLOOR_LEVEL = 1;

    private PlacementPlanner() {
    }

    /**
     * Plans the remaining operations.
     *
     * @param template  the project of the house
     * @param placed    the cells that are already built
     * @param solid     tells whether the block of a relative cell is solid at the moment
     */
    public static PlacementPlan plan(HouseTemplate template, Set<Cell> placed, Predicate<Cell> solid) {
        Set<Cell> standing = new HashSet<>(placed);
        List<CellSpec.Operation> remaining = new ArrayList<>();
        for (CellSpec.Operation operation : template.operations()) {
            if (!standing.containsAll(operation.cells())) {
                remaining.add(operation);
            }
        }

        List<PlannedPlacement> placements = new ArrayList<>();
        while (!remaining.isEmpty()) {
            int index = -1;
            PlacementTarget target = null;
            for (int i = 0; i < remaining.size(); i++) {
                PlacementTarget candidate = targetFor(template, remaining.get(i), standing, solid);
                if (candidate != null) {
                    index = i;
                    target = candidate;
                    break;
                }
            }
            if (index < 0) {
                // No remaining operation has an admissible support.
                CellSpec.Operation blocked = remaining.get(0);
                return new PlacementPlan(placements, blocked.cells().get(0));
            }
            CellSpec.Operation operation = remaining.remove(index);
            placements.add(new PlannedPlacement(operation, target));
            standing.addAll(operation.cells());
        }
        return new PlacementPlan(placements, null);
    }

    /**
     * The support of one operation.
     *
     * <p>A door and a bed need a solid block below them, therefore only the block under the cell
     * is a support for them. Every other block may also be placed on the side of a neighbour.
     */
    /** Returns an operation's target using only supports present in the supplied world snapshot. */
    public static PlacementTarget targetFor(HouseTemplate template, CellSpec.Operation operation,
                                            Set<Cell> standing, Predicate<Cell> solid) {
        Cell anchor = operation.cells().get(0);
        CellSpec spec = template.spec(anchor);
        List<PlacementAttempt> attempts = new ArrayList<>();
        for (Support support : Support.values()) {
            if (operation.placement() != CellSpec.Placement.SINGLE && support != Support.BELOW) {
                // A door and a bed are only placed on a solid block below them.
                continue;
            }
            Cell supportCell = support.supportCell(anchor);
            if (!isSolid(supportCell, standing, solid)) {
                continue;
            }
            attempts.add(new PlacementAttempt(supportCell, support,
                    accessCandidates(template, anchor, standing, solid)));
        }
        if (attempts.isEmpty()) {
            return null;
        }
        return new PlacementTarget(anchor, spec.blockId(), spec.requiredProperties(), attempts);
    }

    private static boolean isSolid(Cell cell, Set<Cell> standing, Predicate<Cell> solid) {
        return standing.contains(cell) || solid.test(cell);
    }

    /**
     * The places where the player can stand for the click.
     *
     * <p>The candidates are the horizontal neighbours of the cell on the level of the cell, one
     * level below, and one level above. A cell that is already standing is not a candidate: the
     * player cannot stand inside a block. The cells that the project never occupies come first,
     * because the player can stay there; the cells that are built later come last.
     *
     * <p>The cells of the roof are reached from the free cells of the floor inside the house,
     * because there is nothing to stand on at the level of the roof before the walls are complete.
     */
    private static List<Cell> accessCandidates(HouseTemplate template, Cell target, Set<Cell> standing,
                                               Predicate<Cell> solid) {
        Set<Cell> occupied = template.cells().keySet();
        List<Cell> safe = new ArrayList<>();
        List<Cell> inside = new ArrayList<>();
        List<Cell> high = new ArrayList<>();
        List<Cell> later = new ArrayList<>();
        for (Cell neighbour : horizontalNeighbours(target)) {
            Cell level = new Cell(neighbour.x(), target.y(), neighbour.z());
            Cell below = new Cell(neighbour.x(), target.y() - 1, neighbour.z());
            Cell above = new Cell(neighbour.x(), target.y() + 1, neighbour.z());
            addCandidate(safe, later, level, occupied, standing, solid);
            addCandidate(safe, later, below, occupied, standing, solid);
            addCandidate(high, later, above, occupied, standing, solid);
            if (target.y() >= 3) {
                // The ring of the roof is two levels above the wall that carries it, and the player
                // climbs the wall step by step, and the cell of the wall is the step in between:
                // the navigation cannot climb two blocks at once and would build a temporary
                // block for it (ТЗ.md section 4).
                addCandidate(safe, later, new Cell(neighbour.x(), target.y() - 2, neighbour.z()),                        occupied, standing, solid);
            }
        }
        // The diagonal cells are the way out when the body of the player stands in the cell of the
        // approach: the player walks into the cell and stops at its edge, and the box of the
        // player then reaches into the cell of the project, and the game refuses the placement
        // (ТЗ.md section 4). From a diagonal cell the box of the player does not reach.
        for (Cell diagonal : diagonalNeighbours(target)) {
            addCandidate(safe, later, new Cell(diagonal.x(), target.y(), diagonal.z()),
                    occupied, standing, solid);
            addCandidate(safe, later, new Cell(diagonal.x(), target.y() - 1, diagonal.z()),
                    occupied, standing, solid);
        }
        if (target.y() >= 3) {
            // The roof is placed from the free cells of the floor inside the house.
            for (int x = 1; x < HouseTemplate.SIZE_X; x++) {
                for (int z = 1; z < HouseTemplate.SIZE_Z; z++) {
                    Cell cell = new Cell(x, FLOOR_LEVEL, z);
                    if (!occupied.contains(cell) && !standing.contains(cell)) {
                        inside.add(cell);
                    }
                }
            }
            inside.sort(Comparator.comparingDouble(cell -> horizontalDistance(cell, target)));
        }
        List<Cell> candidates = new ArrayList<>(safe);
        candidates.addAll(inside);
        candidates.addAll(high);
        candidates.addAll(later);
        return candidates;
    }

    /**
     * Adds a cell to the list of the places that the project never occupies or to the list of the
     * places that are built later. A cell that is already standing is no place for the player.
     *
     * <p>Cells that the project will occupy later are added to {@code later}: the player can walk
     * there if those cells are already built, but the navigation may need to wait or take a detour.
     * Cells that the project never occupies are added to {@code free}: the player can always walk
     * there without waiting.
     */
    private static void addCandidate(List<Cell> free, List<Cell> later, Cell cell, Set<Cell> occupied,
                                     Set<Cell> standing, Predicate<Cell> solid) {
        if (standing.contains(cell) || solid.test(cell)) {
            // The block is already there; the player cannot stand in it.
            return;
        }
        if (occupied.contains(cell)) {
            later.add(cell);
        } else {
            free.add(cell);
        }
    }

    private static double horizontalDistance(Cell cell, Cell target) {        double dx = cell.x() - target.x();
        double dz = cell.z() - target.z();
        return Math.sqrt(dx * dx + dz * dz);
    }

    private static List<Cell> horizontalNeighbours(Cell cell) {
        return List.of(
                new Cell(cell.x(), cell.y(), cell.z() - 1),
                new Cell(cell.x(), cell.y(), cell.z() + 1),
                new Cell(cell.x() - 1, cell.y(), cell.z()),
                new Cell(cell.x() + 1, cell.y(), cell.z()));
    }

    /** The cells that touch the cell of the project only at a corner. */
    private static List<Cell> diagonalNeighbours(Cell cell) {
        return List.of(
                new Cell(cell.x() - 1, cell.y(), cell.z() - 1),
                new Cell(cell.x() - 1, cell.y(), cell.z() + 1),
                new Cell(cell.x() + 1, cell.y(), cell.z() - 1),
                new Cell(cell.x() + 1, cell.y(), cell.z() + 1));
    }
}
