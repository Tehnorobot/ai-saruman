package dev.magehome.core.placement;

/**
 * The rule of the aim and of the click of one operation (ТЗ.md section 4).
 *
 * <p>The order of the operation is fixed: read the world, skip the block that is already right,
 * walk into the ordinary range of the interaction, <em>synchronise the slot and the aim</em>,
 * place the block and wait for the confirmation of the world. The direction of the view of the
 * player reaches the server with the movement packet of the next tick, therefore the interaction
 * is never sent in the same tick in which the view was turned: the block is only placed when the
 * server knows where the player looks at.
 *
 * <p>The rule lives in the domain core, free of game types, so that it can be checked without
 * Minecraft. The client uses exactly this method and keeps no second copy of it.
 */
public final class AimPolicy {

    /** The number of ticks between the turn of the view and the click. */
    public static final int MIN_TICKS_BEFORE_CLICK = 1;

    private AimPolicy() {
    }

    /**
     * True when the interaction of the current attempt may be sent.
     *
     * @param aimSent      the view was already turned to the support of the cell
     * @param ticksSinceAim the number of ticks since the view was turned
     * @param aimMatches   the view points at the expected support and at the expected face
     * @return true only when the view is known to the server and points at the right block
     */
    public static boolean mayClick(boolean aimSent, int ticksSinceAim, boolean aimMatches) {
        return mayClick(aimSent, ticksSinceAim, aimMatches, aimMatches);
    }

    /**
     * True when the interaction of the current attempt may be sent.
     *
     * <p>The click needs two observations of the game: the crosshair has to name the support of
     * the operation, and the state that the game would place with that hit result has to be the
     * state of the project (and to stand in the cell of the project). The orientation of a door,
     * a bed, a furnace or a lantern depends on the direction of the view, therefore the direction
     * of the view and the required state cannot be checked apart from each other: the rule asks
     * the game and waits for the answer.
     *
     * @param aimSent       the view was already turned to the support of the cell
     * @param ticksSinceAim the number of ticks since the view was turned
     * @param supportNamed  the crosshair of the game names the support of the operation
     * @param stateMatches  the game would place the state of the project with that hit result
     * @return true only when the view is known to the server and the click is the one of the project
     */
    public static boolean mayClick(boolean aimSent, int ticksSinceAim, boolean supportNamed,
                                   boolean stateMatches) {
        return aimSent && ticksSinceAim >= MIN_TICKS_BEFORE_CLICK && supportNamed && stateMatches;
    }
}
