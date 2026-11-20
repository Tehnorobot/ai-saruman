package dev.magehome.core.placement;

/**
 * The comparison of the view of the player with the support of the cell.
 *
 * <p>The click is sent with the hit result that the game itself computes under the crosshair.
 * That hit result is only used when it names the support of the cell and the face that the
 * operation needs; every other answer is a reason to turn the view again and to wait, not to
 * send an interaction.
 *
 * <p>The comparison lives in the domain core, free of game types, so that it can be checked
 * without Minecraft. The client observes the hit result of the game and asks this rule.
 */
public final class AimMatch {

    /** The verdict of the comparison of the view with the support. */
    public enum Verdict {
        /** The view points at the expected block and at the expected face. */
        MATCH,
        /** The game has no block under the crosshair. */
        NO_TARGET,
        /** The view points at another block. */
        WRONG_BLOCK,
        /** The view points at the expected block, but at another face of it. */
        WRONG_FACE
    }

    private AimMatch() {
    }

    /**
     * The verdict of the comparison.
     *
     * @param hasTarget  the game has a block under the crosshair
     * @param sameBlock  the block under the crosshair is the expected support
     * @param sameFace   the face under the crosshair is the expected face
     * @return the verdict of the comparison
     */
    public static Verdict of(boolean hasTarget, boolean sameBlock, boolean sameFace) {
        if (!hasTarget) {
            return Verdict.NO_TARGET;
        }
        if (!sameBlock) {
            return Verdict.WRONG_BLOCK;
        }
        if (!sameFace) {
            return Verdict.WRONG_FACE;
        }
        return Verdict.MATCH;
    }

    /**
     * True when the view may be used for the click.
     *
     * @param verdict the verdict of the comparison
     * @return true only for the verdict that names the expected support and face
     */
    public static boolean matches(Verdict verdict) {
        return verdict == Verdict.MATCH;
    }
}
