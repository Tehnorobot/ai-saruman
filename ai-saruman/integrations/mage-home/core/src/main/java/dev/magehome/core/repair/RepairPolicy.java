package dev.magehome.core.repair;

import dev.magehome.core.model.Cell;
import dev.magehome.core.registry.HouseState;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.verification.CellVerdict;
import dev.magehome.core.verification.CellVerifier;
import dev.magehome.core.verification.ObservedBlock;

/**
 * The rule of the safe repair of one cell of the volume of a registered house (ТЗ.md section 4).
 *
 * <p>The rule is a pure function of the facts that the core already knows: the project of the
 * cell, the block that stands there, the partner cell of a multi-block item and the liquid next
 * to the cell. It never touches the game and can therefore be checked without a client.
 *
 * <p>The protected cells are the cells of the project that already correspond to it, the supports
 * under the house, every block outside the volume of the house and every container: the client
 * does not read the content of a container, so every container is treated as protected. A wrong
 * non-project block inside the registered house may be repaired only after the same protections
 * and liquid/partner checks pass (ТЗ.md A.3). A repair is refused when it would touch a protected
 * cell, an external multi-block half, or could spill a liquid.
 *
 * <p>The repair belongs to a house in the state building: a ready or a damaged house is never
 * repaired on its own (ТЗ.md section 5).
 */
public final class RepairPolicy {

    /** The cell is empty: there is nothing to remove. */
    public static final String REASON_EMPTY = "the cell is empty";
    /** The cell corresponds to the project: it is not touched. */
    public static final String REASON_MATCH = "the cell corresponds to the project";
    /** The cell is not loaded: nothing is known about it and nothing is touched. */
    public static final String REASON_UNKNOWN = "the cell is not loaded";
    /** A wrong block stands in the cell of the volume of the house. */
    public static final String REASON_WRONG_BLOCK = "a wrong block stands in the cell";
    /** A single half of a multi-block item stands in the cell; the pair is incomplete. */
    public static final String REASON_LONE_HALF = "a single half of a multi-block item stands in the cell";
    /** The cell is outside the volume of the house. */
    public static final String REASON_OUTSIDE = "the cell is outside the volume of the house";
    /** The block is a support under the house or under its front point. */
    public static final String REASON_SUPPORT = "the block is a support of the house";
    /** The block is a container; its content is not read by the client. */
    public static final String REASON_CONTAINER = "the block is a container";
    /** The repair would touch the outer half of a multi-block item. */
    public static final String REASON_EXTERNAL_HALF =
            "the repair would touch the outer half of a multi-block item";
    /** The repair would touch a protected cell. */
    public static final String REASON_PROTECTED_PARTNER = "the repair would touch a protected cell";
    /** The repair could spill a liquid. */
    public static final String REASON_LIQUID = "the repair could spill a liquid";
    /**
     * A foreign block stands in the cell.
     *
     * <p>A foreign block is a block that the project does not place: a tree, a crop or any other
     * change of the world that the player made. The add-on does not break it (ТЗ.md section 4),
     * and the house waits for the player with the reason {@code obstructed}.
     */
    public static final String REASON_FOREIGN_BLOCK = "a foreign block stands in the cell";
    /** The repair belongs to a house in the state building (ТЗ.md section 5). */
    public static final String REASON_STATE = "the house is not in the state building";

    private RepairPolicy() {
    }

    /**
     * The facts of the decision about one cell.
     *
     * @param spec           the project of the cell; null when the cell has to stay empty
     * @param observed       the block that stands in the cell
     * @param partnerInVolume the partner cell of a multi-block item is inside the volume of the
     *                        house; true when the block is not a part of a multi-block item
     * @param partnerMatches the partner cell of a multi-block item corresponds to the project;
     *                       false when the block is not a part of a multi-block item
     * @param liquidNearby   a liquid touches the cell
     * @param projectMaterial the block that stands in the cell is a material of the project; a
     *                       block that the project does not place is a foreign block
     */
    public record Facts(CellSpec spec, ObservedBlock observed, boolean partnerInVolume,
                        boolean partnerMatches, boolean liquidNearby, boolean projectMaterial) {
    }

    /**
     * The decision about the cell.
     *
     * @param cell   the cell of the house, relative to the origin A
     * @param facts  the facts of the cell
     * @return the decision; a decision that blocks the building names the concrete reason
     */
    public static RepairDecision classify(Cell cell, Facts facts) {
        if (!cell.inVolume()) {
            // The supports under the house (y = ay - 1) and every block outside the volume of the
            // house are protected: they are never broken.
            return RepairDecision.protectedCell(cell.y() < 0 ? REASON_SUPPORT : REASON_OUTSIDE);
        }
        ObservedBlock observed = facts.observed();
        if (observed == null || !observed.known()) {
            // Unknown is never treated as air and never touched.
            return RepairDecision.skip(REASON_UNKNOWN);
        }
        if (observed.isAir()) {
            return RepairDecision.skip(REASON_EMPTY);
        }
        boolean matches = facts.spec() != null
                && CellVerifier.verify(facts.spec(), observed) == CellVerdict.MATCH;
        if (matches) {
            if (!isMultiBlockPart(observed)) {
                // A cell of the project that already corresponds to it is not touched.
                return RepairDecision.skip(REASON_MATCH);
            }
            if (!facts.partnerInVolume()) {
                return RepairDecision.refuse(REASON_EXTERNAL_HALF);
            }
            if (facts.partnerMatches()) {
                // The pair of the multi-block item is complete.
                return RepairDecision.skip(REASON_MATCH);
            }
            // Even an otherwise-correct lone half may not be removed if doing so could release
            // adjacent liquid; it is subject to the same safety checks as any other repair.
            if (facts.liquidNearby()) return RepairDecision.refuse(REASON_LIQUID);
            return RepairDecision.repair(REASON_LONE_HALF);
        }
        if (observed.container()) {
            // The client does not read the content of a container, so every container is
            // protected.
            return RepairDecision.protectedCell(REASON_CONTAINER);
        }
        if (facts.liquidNearby()) {
            return RepairDecision.refuse(REASON_LIQUID);
        }
        if (isMultiBlockPart(observed)) {
            if (!facts.partnerInVolume()) {
                // The outer half of the multi-block item stands outside the volume of the house.
                return RepairDecision.refuse(REASON_EXTERNAL_HALF);
            }
            if (facts.partnerMatches()) {
                // The repair would remove the correct half of the pair.
                return RepairDecision.refuse(REASON_PROTECTED_PARTNER);
            }
        }
        // Project membership alone is not a protection: A.3 permits an otherwise-safe wrong
        // block inside a registered building volume to be repaired, including a foreign material.
        return RepairDecision.repair(REASON_WRONG_BLOCK);
    }

    /**
     * The repair belongs to a house in the state building (ТЗ.md section 5).
     *
     * <p>A ready or a damaged house is never repaired on its own: it waits for an explicit
     * resume, which puts the record of the house back into the state building.
     *
     * @param state the state of the record of the house
     * @return true when the repair of the house may run
     */
    public static boolean repairApplies(HouseState state) {
        return state == HouseState.BUILDING;
    }

    /**
     * True when the block is a part of a multi-block item.
     *
     * <p>A door has the property {@code half} and a bed the property {@code part}; a trapdoor
     * has a property {@code half} as well, but it is a single block and therefore not a part.
     *
     * @param observed the block that stands in the cell
     * @return true for a half of a door and for a part of a bed
     */
    public static boolean isMultiBlockPart(ObservedBlock observed) {
        if (observed == null) {
            return false;
        }
        String id = observed.blockId();
        if (id.endsWith("_door")) {
            return observed.property("half") != null;
        }
        return id.endsWith("_bed") && observed.property("part") != null;
    }

    /**
     * The cell of the partner of a multi-block item.
     *
     * <p>The lower half of a door has its partner above, the upper half below; the head of a bed
     * stands in the direction of the facing of the bed and the foot in the opposite direction.
     *
     * @param cell     the cell of the part
     * @param observed the block that stands in the cell
     * @return the cell of the partner or null when the block is not a part of a multi-block item
     */
    public static Cell partnerCell(Cell cell, ObservedBlock observed) {
        if (!isMultiBlockPart(observed)) {
            return null;
        }
        String id = observed.blockId();
        if (id.endsWith("_door")) {
            String half = observed.property("half");
            if ("lower".equals(half)) {
                return new Cell(cell.x(), cell.y() + 1, cell.z());
            }
            if ("upper".equals(half)) {
                return new Cell(cell.x(), cell.y() - 1, cell.z());
            }
            return null;
        }
        String part = observed.property("part");
        String facing = observed.property("facing");
        if (facing == null) {
            return null;
        }
        int dx = 0;
        int dz = 0;
        switch (facing) {
            case "north" -> dz = -1;
            case "south" -> dz = 1;
            case "east" -> dx = 1;
            case "west" -> dx = -1;
            default -> {
                return null;
            }
        }
        if ("foot".equals(part)) {
            return new Cell(cell.x() + dx, cell.y(), cell.z() + dz);
        }
        if ("head".equals(part)) {
            return new Cell(cell.x() - dx, cell.y(), cell.z() - dz);
        }
        return null;
    }
}
