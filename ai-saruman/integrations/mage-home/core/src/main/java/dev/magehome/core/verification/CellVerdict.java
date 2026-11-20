package dev.magehome.core.verification;

/** Result of comparing one cell with the project (ТЗ.md section 4). */
public enum CellVerdict {
    /** The cell is exactly as the project requires. */
    MATCH,
    /** The cell is empty but has to be occupied. */
    ABSENT,
    /** A wrong, non empty block occupies the cell; it must not be broken. */
    WRONG_BLOCK,
    /** The block is right but a required property differs. */
    WRONG_PROPERTY,
    /** The cell is not loaded; unknown is never treated as air. */
    UNKNOWN
}
