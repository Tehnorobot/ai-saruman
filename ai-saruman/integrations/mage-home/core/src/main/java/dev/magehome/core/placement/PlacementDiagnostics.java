package dev.magehome.core.placement;

/**
 * The diagnostics of one attempt of a placement (ТЗ.md section 4).
 *
 * <p>They answer the questions that the world itself does not answer: which block and which face
 * the view of the player points at, what the game of the client has under the crosshair, which
 * slot of the screen handler holds the item of the project, where the client thinks the player is
 * and where the server thinks the player is, which precondition of the ordinary placement did
 * not hold and what the world shows in the cell of the project and around it.
 *
 * <p>The record lives in the domain core, free of game types, so that the shape of the
 * diagnostics can be checked without Minecraft.
 *
 * @param aimSource       the source of the hit result of the click
 * @param aimBlock        the block under the crosshair of the client
 * @param aimFace         the face under the crosshair of the client
 * @param aimVector       the point of the crosshair of the client
 * @param aimVerdict      the verdict of the comparison of the view with the support
 * @param selectedSlot    the slot of the screen handler that holds the item of the project
 * @param slotItem        the item in the main hand of the player
 * @param clientImage     the place and the view of the player on the client
 * @param serverImage     the place and the view of the player on the server
 * @param preconditionStep the first precondition of the placement that did not hold
 * @param aimTicks        the number of ticks between the turn of the view and the click
 * @param neighbours      the states of the cell of the project and of the cells around it
 */
public record PlacementDiagnostics(String aimSource, String aimBlock, String aimFace, String aimVector,
                                    String aimVerdict, String selectedSlot, String slotItem,
                                    String clientImage, String serverImage, String preconditionStep,
                                    int aimTicks, String neighbours) {

    /**
     * The diagnostics as one line of text.
     *
     * <p>The line is stable, so that a log of a run can be read against the facts of another
     * run without a search of the words of the sentence.
     *
     * @return the diagnostics of the attempt as one line
     */
    public String describe() {
        return " aim_source=" + text(aimSource)
                + " aim_block=" + text(aimBlock)
                + " aim_face=" + text(aimFace)
                + " aim_vector=" + text(aimVector)
                + " aim_verdict=" + text(aimVerdict)
                + " slot=" + text(selectedSlot)
                + " slot_item=" + text(slotItem)
                + " client=" + text(clientImage)
                + " server=" + text(serverImage)
                + " precondition=" + text(preconditionStep)
                + " aim_ticks=" + aimTicks
                + " neighbours=" + text(neighbours);
    }

    private static String text(String value) {
        return value == null || value.isBlank() ? "none" : value;
    }
}
