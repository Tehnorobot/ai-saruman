package dev.magehome.core.placement;

/**
 * The arrival of the player at the cell of the approach (ТЗ.md section 4).
 *
 * <p>The add-on builds with ordinary interactions of the player: the player has to stand in the
 * cell of the approach, the view is turned to the face of the support block and the interaction
 * is sent from there. The rule of the arrival is exact: the player stands <em>in</em> the cell of
 * the approach, not next to it.
 *
 * <p>The rule is the one of the dependency itself. The delivered UnionClef 0.95.61 ends its
 * navigation task {@code GetToBlockTask} only when the player stands in the wanted block: the
 * constant pool of the delivered jar names the goal {@code
 * adris.altoclef.util.goals.AltoGoal$Block}, but the exact comparison of that goal was not
 * verified in this project (the check T-A19-05 reads only the names), so the add-on applies its
 * own exact rule and does not rely on that reading. A tolerance of the add-on that is wider than
 * the tolerance of the dependency declares the arrival before the navigation is finished — and
 * the click leaves the cell that the add-on itself fills, so the world does not change and the
 * house waits for a confirmation that never comes.
 *
 * <p>The rule lives in the domain core, free of game types, so that it can be checked without
 * Minecraft. The client uses exactly this rule and does not keep a second copy of it.
 */
public final class ArrivalPolicy {

    /**
     * The number of ticks the player may need for one cell of the approach.
     *
     * <p>The number is a budget, not a rule of the game: when the player did not stand in the
     * cell of the approach and no interaction was sent for it yet, the next cell of the approach
     * is tried. When no cell is left, the house is suspended with a concrete reason and the
     * coordinate of the cell — it never waits silently.
     *
     * <p>The budget is 240 ticks and not the 60 ticks of the first revision of ТЗ.md: the house
     * is 7x7 and the cell of the approach can lie on the other side of it, so the walk of the
     * player takes longer than three seconds. The owner allowed the change on 2026-11-13
     * because the old budget stopped the walk and suspended the house.
     */
    public static final int ACCESS_WAIT_TICKS = 240;

    /**
     * The number of ticks the view of the player may point at another block.
     *
     * <p>The budget belongs to the same rule as the wait of the arrival: the player stands in the
     * cell of the approach, the view is turned to the support of the operation, and the click
     * waits for the direction of the view to reach the server. When the view does not name the
     * support within this budget, the cell of the approach is left for the next one — the house
     * never waits silently for ever, and the budget is not kept in the client as a second copy.
     *
     * <p>The budget is a budget of the wait, not a limit of the game: a cell that already
     * received an interaction is never left (see {@link #mayLeaveAccess}).
     */
    public static final int AIM_WAIT_TICKS = 60;

    private ArrivalPolicy() {
    }

    /**
     * True when the cell of the approach may be left because the view did not name the support.
     *
     * <p>The rule is the one of the core: the client asks it and keeps no second copy of the
     * budget. A cell that already received an interaction keeps its sends (ТЗ.md section 4).
     *
     * @param aimTicks          the number of ticks the view did not name the support
     * @param interactionsSent  the number of interactions that were sent for the current cell
     * @return true when the current cell may be left for the next one
     */
    public static boolean mayLeaveAccessOnAim(int aimTicks, int interactionsSent) {
        if (interactionsSent > 0) {
            // The cell already received an interaction: it keeps its sends.
            return false;
        }
        return aimTicks >= AIM_WAIT_TICKS;
    }

    /**
     * True when the player stands in the cell of the approach itself.
     *
     * @param playerX the x of the block of the player
     * @param playerY the y of the block of the player
     * @param playerZ the z of the block of the player
     * @param accessX the x of the cell of the approach
     * @param accessY the y of the cell of the approach
     * @param accessZ the z of the cell of the approach
     * @return true only when the three coordinates are equal
     */
    public static boolean arrived(int playerX, int playerY, int playerZ,
                                  int accessX, int accessY, int accessZ) {
        return playerX == accessX && playerY == accessY && playerZ == accessZ;
    }

    /**
     * True when the player stands in the given cell.
     *
     * @param playerX the x of the block of the player
     * @param playerY the y of the block of the player
     * @param playerZ the z of the block of the player
     * @param cellX the x of the cell
     * @param cellY the y of the cell
     * @param cellZ the z of the cell
     * @return true only when the three coordinates are equal
     */
    public static boolean occupies(int playerX, int playerY, int playerZ,
                                   int cellX, int cellY, int cellZ) {
        return playerX == cellX && playerY == cellY && playerZ == cellZ;
    }

    /**
     * True when the interaction of the cell may be sent.
     *
     * <p>Two conditions have to hold at the same time: the player stands in the cell of the
     * approach, and the player does not stand in the cell that is to be filled. A block cannot be
     * placed into the cell where the player stands — the server refuses the interaction and the
     * world does not change.
     *
     * @param playerX the x of the block of the player
     * @param playerY the y of the block of the player
     * @param playerZ the z of the block of the player
     * @param accessX the x of the cell of the approach
     * @param accessY the y of the cell of the approach
     * @param accessZ the z of the cell of the approach
     * @param targetX the x of the cell that is to be filled
     * @param targetY the y of the cell that is to be filled
     * @param targetZ the z of the cell that is to be filled
     * @return true only when the interaction may be sent
     */
    public static boolean mayClick(int playerX, int playerY, int playerZ,
                                   int accessX, int accessY, int accessZ,
                                   int targetX, int targetY, int targetZ) {
        return arrived(playerX, playerY, playerZ, accessX, accessY, accessZ)
                && !occupies(playerX, playerY, playerZ, targetX, targetY, targetZ);
    }

    /**
     * True when the player stands on the front point of the finished house.
     *
     * <p>The house becomes ready only when the player really left the house through the front
     * point A+(3,0,-1) (ТЗ.md section 4, R21). The position is compared exactly: a place near
     * the front point is not the front point.
     *
     * @param playerX the x of the block of the player
     * @param playerY the y of the block of the player
     * @param playerZ the z of the block of the player
     * @param frontX the x of the front point
     * @param frontY the y of the front point
     * @param frontZ the z of the front point
     * @return true only when the three coordinates are equal
     */
    public static boolean atFrontPoint(int playerX, int playerY, int playerZ,
                                       int frontX, int frontY, int frontZ) {
        return playerX == frontX && playerY == frontY && playerZ == frontZ;
    }

    /**
     * True when the next cell of the approach may be tried.
     *
     * <p>The next cell is only tried when no interaction was sent for the current one: a position
     * that already received an interaction keeps its two sends (ТЗ.md section 4), and it is not
     * abandoned before the world had the chance to answer.
     *
     * @param ticksWaiting the number of ticks the player needed for the current cell
     * @param interactionsSent the number of interactions that were sent for the current cell
     * @return true when the current cell may be left
     */
    public static boolean maySwitchAccess(int ticksWaiting, int interactionsSent) {
        return interactionsSent == 0 && ticksWaiting >= ACCESS_WAIT_TICKS;
    }

    /**
     * True when the current cell of the approach may be left for another one.
     *
     * <p>Two different situations lead to another cell of the approach:
     *
     * <ul>
     *   <li>the player did not stand in the cell of the approach within the budget of the wait and
     *       no interaction was sent for that cell yet;</li>
     *   <li>the player stands in the cell of the approach, but the cell belongs to the operation
     *       itself — the second cell of a bed or of a door, for example. A block cannot be placed
     *       into the cell where the player stands, so the interaction is never sent from there and
     *       waiting does not help: the cell is left at once.</li>
     * </ul>
     *
     * <p>A cell that already received an interaction is not left: it keeps its sends (ТЗ.md
     * section 4), and the world has the chance to answer them.
     *
     * @param arrived true when the player stands in the cell of the approach itself
     * @param clickAllowed true when the interaction may be sent from the cell of the approach
     * @param ticksWaiting the number of ticks the player needed for the current cell
     * @param interactionsSent the number of interactions that were sent for the current cell
     * @return true when the current cell may be left for the next one
     */
    public static boolean mayLeaveAccess(boolean arrived, boolean clickAllowed, int ticksWaiting,
                                          int interactionsSent) {
        if (interactionsSent > 0) {
            // The cell already received an interaction: it keeps its sends.
            return false;
        }
        if (!arrived) {
            return maySwitchAccess(ticksWaiting, interactionsSent);
        }
        // The player is in the cell of the approach, but the click is not allowed there.
        return !clickAllowed;
    }
}
