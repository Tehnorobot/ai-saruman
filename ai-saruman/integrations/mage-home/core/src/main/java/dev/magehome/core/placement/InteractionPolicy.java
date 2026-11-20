package dev.magehome.core.placement;

/**
 * The rule of the client that sends the interactions with the world (ТЗ.md section 4).
 *
 * <p>One operation is one interaction: the click on the face of the support block is sent once
 * for the attempt and it is not repeated on every client tick. Nothing is sent when the world has
 * already confirmed the operation.
 *
 * <p>The rule is kept here, free of game types, so that it can be checked without Minecraft. The
 * client uses exactly this method; the check reads the client source to make sure that the tested
 * rule is the one the client applies.
 */
public final class InteractionPolicy {

    private InteractionPolicy() {
    }

    /**
     * True when the interaction of the current attempt may be sent.
     *
     * @param interactionSent  the interaction of this attempt was already sent
     * @param confirmedByWorld the world has confirmed the operation already
     * @return true only for the first interaction of an attempt that the world did not confirm
     */
    public static boolean maySend(boolean interactionSent, boolean confirmedByWorld) {
        return !interactionSent && !confirmedByWorld;
    }
}
