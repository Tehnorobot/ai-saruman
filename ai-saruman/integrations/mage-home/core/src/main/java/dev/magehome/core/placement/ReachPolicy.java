package dev.magehome.core.placement;

/**
 * The ordinary range of the interaction of the player (ТЗ.md section 4).
 *
 * <p>The rule lives in the domain core, free of game types, so that it can be checked without
 * Minecraft. The client uses exactly this rule before it sends the interaction.
 *
 * <p>The number comes from the bytecode of Minecraft 1.21.11 (Yarn): the server lets a player
 * interact with a block when the eyes of the player are not further away from the box of the
 * block than the attribute {@code block_interaction_range} (4.5 in the survival mode) plus a
 * small margin — {@code PlayerEntity.canInteractWithBlockAt(BlockPos, double)}. The distance
 * from the eyes to the <em>clicked face</em> is never smaller than the distance to the box of
 * the block, so the rule of the add-on is the stricter one: an interaction whose distance is
 * bigger than 4.5 is not sent at all. A click that the server would reject leaves no trace in
 * the log of the game, so the add-on keeps the distance of its own eyes and does not send it.
 */
public final class ReachPolicy {

    /** The ordinary range of the interaction of the player. */
    public static final double ORDINARY_REACH = 4.5;

    private ReachPolicy() {
    }

    /**
     * The distance between the eyes of the player and the point of the click.
     *
     * @param eyeX the x of the eyes of the player
     * @param eyeY the y of the eyes of the player
     * @param eyeZ the z of the eyes of the player
     * @param hitX the x of the point of the click
     * @param hitY the y of the point of the click
     * @param hitZ the z of the point of the click
     * @return the distance in blocks
     */
    public static double distance(double eyeX, double eyeY, double eyeZ,
                                  double hitX, double hitY, double hitZ) {
        double dx = hitX - eyeX;
        double dy = hitY - eyeY;
        double dz = hitZ - eyeZ;
        return Math.sqrt(dx * dx + dy * dy + dz * dz);
    }

    /**
     * True when the interaction may be sent.
     *
     * @param distance the distance between the eyes of the player and the point of the click
     * @return true only inside the ordinary range
     */
    public static boolean mayInteract(double distance) {
        return distance <= ORDINARY_REACH;
    }

    /**
     * True when the interaction may be sent.
     *
     * @param eyeX the x of the eyes of the player
     * @param eyeY the y of the eyes of the player
     * @param eyeZ the z of the eyes of the player
     * @param hitX the x of the point of the click
     * @param hitY the y of the point of the click
     * @param hitZ the z of the point of the click
     * @return true only inside the ordinary range
     */
    public static boolean mayInteract(double eyeX, double eyeY, double eyeZ,
                                       double hitX, double hitY, double hitZ) {
        return mayInteract(distance(eyeX, eyeY, eyeZ, hitX, hitY, hitZ));
    }
}
