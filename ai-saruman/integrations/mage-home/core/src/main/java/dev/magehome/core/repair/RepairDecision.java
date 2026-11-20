package dev.magehome.core.repair;

/**
 * The decision about one wrong cell of the volume of a registered house (ТЗ.md section 4).
 *
 * <p>The decision is a pure fact of the domain: it names what may happen with the cell and, when
 * the repair is not applicable, the concrete reason of the refusal. The coordinate of the cell is
 * added by the caller, because the decision itself does not know where the house stands.
 */
public record RepairDecision(Kind kind, String reason) {

    /** What may happen with the cell. */
    public enum Kind {
        /** The cell needs no repair: it is empty or it already corresponds to the project. */
        SKIP,
        /** The wrong block may be removed with an ordinary attack of the player. */
        REPAIR,
        /** The cell is protected: it must not be touched at all. */
        PROTECTED,
        /** The repair is not applicable: the house is suspended with the reason. */
        REFUSE
    }

    public RepairDecision {
        if (reason == null || reason.isBlank()) {
            throw new IllegalArgumentException("a decision about a cell needs a reason");
        }
    }

    public static RepairDecision skip(String reason) {
        return new RepairDecision(Kind.SKIP, reason);
    }

    public static RepairDecision repair(String reason) {
        return new RepairDecision(Kind.REPAIR, reason);
    }

    public static RepairDecision protectedCell(String reason) {
        return new RepairDecision(Kind.PROTECTED, reason);
    }

    public static RepairDecision refuse(String reason) {
        return new RepairDecision(Kind.REFUSE, reason);
    }

    /** True when the wrong block of the cell may be removed. */
    public boolean needsRepair() {
        return kind == Kind.REPAIR;
    }

    /**
     * True when the decision stops the building.
     *
     * <p>A protected cell and a refused repair suspend the house with the reason
     * {@code obstructed}, the coordinate of the cell and the concrete reason (ТЗ.md section 4).
     */
    public boolean blocksBuilding() {
        return kind == Kind.PROTECTED || kind == Kind.REFUSE;
    }
}
