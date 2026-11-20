package dev.magehome.core.placement;

import java.util.EnumMap;
import java.util.Map;

/**
 * The preconditions of the ordinary placement of the game and their order.
 *
 * <p>The client of the game does not answer why it refused an interaction: the call of the
 * placement returns a plain refusal and the world stays as it was. The add-on asks the same
 * questions in the same order before it sends the interaction and publishes the first
 * precondition that did not hold, so that a run in which no block was placed can be read
 * instead of being guessed.
 *
 * <p>The order lives in the domain core: the client only observes the answers of the game and
 * lets this rule name the first precondition that did not hold.
 */
public final class PlacementPreconditions {

    /** The preconditions of the placement, in the order of the game. */
    public enum Step {
        /** The item of the project is in the main hand of the player. */
        STACK_IN_HAND,
        /** The game knows the features that the item of the project needs. */
        ITEM_ENABLED,
        /** The cell of the project may be filled. */
        CAN_PLACE,
        /** The game produces the state that the project wants. */
        PLACEMENT_STATE,
        /**
         * The block would stand in the cell of the project.
         *
         * <p>The game places the block next to the face that was clicked: the cell of the project
         * is the cell on the other side of that face. The step answers whether the interaction
         * of the crosshair would fill the cell of the project at all — a click on another face of
         * the support fills another cell, and the project would wait for a confirmation that
         * never comes.
         */
        TARGET_CELL,
        /** The state of the project may stand in the cell of the project. */
        STATE_AT,
        /**
         * The placed block would not collide with the player or other entities.
         *
         * <p>The vanilla placement checks {@code World.canPlace(state, pos, context)} with the
         * shape context of the player. This detects collisions with the player's bounding box
         * and refuses placement when the player stands too close to the target cell. Without
         * this check, the interaction is sent but the server silently refuses it, and the
         * house waits for a confirmation that never comes.
         */
        NO_COLLISION,
        /** The point of the click is inside the ordinary range of the player. */
        REACH
    }

    private PlacementPreconditions() {
    }

    /**
     * The first precondition that did not hold.
     *
     * @param observed the answers of the game, one for every step
     * @return the name of the first step that did not hold or null when every step held
     */
    public static String firstFailure(Map<Step, Boolean> observed) {
        for (Step step : Step.values()) {
            Boolean passed = observed.get(step);
            if (passed == null || !passed) {
                return step.name();
            }
        }
        return null;
    }

    /**
     * The answers of a placement that holds every precondition.
     *
     * @return an empty answer for every step
     */
    public static Map<Step, Boolean> allTrue() {
        Map<Step, Boolean> observed = new EnumMap<>(Step.class);
        for (Step step : Step.values()) {
            observed.put(step, Boolean.TRUE);
        }
        return observed;
    }
}
