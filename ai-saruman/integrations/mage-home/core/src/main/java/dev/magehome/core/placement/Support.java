package dev.magehome.core.placement;

import dev.magehome.core.model.Cell;

/**
 * Where the support of a placement is.
 *
 * <p>The name is the direction from the cell that is filled to the block that is clicked:
 * {@code BELOW} is the block under the cell, the other values are the horizontal neighbours.
 * The clicked face of the support is the face that points to the cell, so the block is placed
 * with an ordinary interaction on an available face (ТЗ.md section 4).
 */
public enum Support {

    BELOW,
    NORTH,
    SOUTH,
    WEST,
    EAST;

    /** The cell of the support block for a given target cell. */
    public Cell supportCell(Cell cell) {
        return switch (this) {
            case BELOW -> new Cell(cell.x(), cell.y() - 1, cell.z());
            case NORTH -> new Cell(cell.x(), cell.y(), cell.z() - 1);
            case SOUTH -> new Cell(cell.x(), cell.y(), cell.z() + 1);
            case WEST -> new Cell(cell.x() - 1, cell.y(), cell.z());
            case EAST -> new Cell(cell.x() + 1, cell.y(), cell.z());
        };
    }
}
