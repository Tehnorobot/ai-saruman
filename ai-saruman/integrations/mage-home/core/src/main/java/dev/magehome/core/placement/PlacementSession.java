package dev.magehome.core.placement;

import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.template.CellSpec;

/** Per-operation handshake. Accepting work and sending an interaction are separate events. */
public final class PlacementSession {
    public enum Stage {
        IDLE, APPROACHING, AIMING, SYNCHRONIZING, SENT, CONFIRMED, NO_SUPPORT, EXHAUSTED,
        /**
         * The operation waits for a later try.
         *
         * <p>The cells of the approach of this operation were all tried and none of them gave a
         * placement. The operation is not a failure of the house: it is tried again after the
         * next operations, and the house is suspended only when the cell fails again and again
         * (ТЗ.md section 4).
         */
        DEFERRED
    }

    public record Snapshot(Stage stage, int interactions, String detail) {
        public boolean failed() {
            return stage == Stage.NO_SUPPORT || stage == Stage.EXHAUSTED;
        }
    }

    private CellSpec.Operation operation;
    private Vec3i origin;
    private Cell cell;
    private Stage stage = Stage.IDLE;
    private int interactions;
    private int preparationTicks;
    private String detail = "";

    /** False for a repeated request: no aim, navigation, send count or clock is reset. */
    public boolean begin(CellSpec.Operation operation, Vec3i origin, Cell cell) {
        if (matches(operation, origin) && cell.equals(this.cell) && stage != Stage.DEFERRED) return false;
        clear();
        this.operation = operation;
        this.origin = origin;
        this.cell = cell;
        update(Stage.APPROACHING, "operation accepted; no interaction sent");
        return true;
    }

    public Snapshot progress(CellSpec.Operation operation, Vec3i origin) {
        return matches(operation, origin) ? new Snapshot(stage, interactions, detail)
                : new Snapshot(Stage.IDLE, 0, "no matching operation");
    }

    private boolean matches(CellSpec.Operation operation, Vec3i origin) {
        return operation.equals(this.operation) && origin.equals(this.origin);
    }

    public void update(Stage stage, String detail) {
        this.stage = stage;
        this.detail = detail;
    }

    /** Called only alongside the actual ordinary interactBlock invocation. */
    public void sent() {
        if (!maySend()) throw new IllegalStateException("Placement interaction is not armed");
        interactions++;
        update(Stage.SENT, "ordinary interaction invoked; world confirmation pending");
    }

    /** Arms a retry once; preparation is not itself another send. */
    public boolean armRetry() {
        if (stage != Stage.SENT || interactions >= 2) return false;
        preparationTicks = 0;
        update(Stage.APPROACHING, "retry armed; no new interaction sent");
        return true;
    }

    public boolean maySend() {
        return operation != null && interactions < 2 && stage != Stage.SENT
                && stage != Stage.CONFIRMED && stage != Stage.NO_SUPPORT && stage != Stage.EXHAUSTED
                && stage != Stage.DEFERRED;
    }

    public int interactions() {
        return interactions;
    }

    /** Budget for all preparation branches, not just arrival or view mismatch. */
    public boolean preparationExpired(int budgetTicks) {
        if (stage == Stage.SENT || stage == Stage.CONFIRMED || stage == Stage.IDLE
                || stage == Stage.NO_SUPPORT || stage == Stage.EXHAUSTED) return false;
        return ++preparationTicks >= budgetTicks;
    }

    /**
     * The wait for the material of the project does not belong to the cell of the approach.
     *
     * <p>The item is given by the server and reaches the hand of the player after the command;
     * that wait can be longer than the budget of the approach. The budget of the approach counts
     * the ticks in which the player could have stood in the cell and clicked, and not the ticks in
     * which the material was on its way (ТЗ.md section 4).
     */
    public void holdPreparation() {
        preparationTicks = 0;
    }

    public void nextAccess() {
        preparationTicks = 0;
        update(Stage.APPROACHING, "trying another approach without an interaction");
    }

    public void clear() {
        operation = null;
        origin = null;
        cell = null;
        stage = Stage.IDLE;
        interactions = 0;
        preparationTicks = 0;
        detail = "";
    }
}
