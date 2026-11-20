package dev.magehome.core.placement;

/**
 * The observable facts of one attempt of a placement (ТЗ.md section 4).
 *
 * <p>The facts are the answer to the question "what happened with this cell": the cell of the
 * project, the block that was clicked, the place where the player stood, the item that was in
 * his hand, whether the interaction was sent, how far the point of the click was from his eyes
 * and what the world showed in the cell afterwards.
 *
 * <p>The facts of the arrival are part of the record: whether the player stood in the cell of the
 * approach ({@code arrived}), what the call of the interaction returned ({@code action_result}),
 * the direction of the view ({@code yaw} and {@code pitch}) and the point of the click
 * ({@code click}). Without them a run that does not confirm a cell cannot be read: the world does
 * not tell whether the client refused the interaction or the server did.
 *
 * <p>The record lives in the domain core, free of game types, so that the shape of the facts can
 * be checked without Minecraft. The client fills the record and writes it into the log of the
 * game and into the journal of the scenario.
 *
 * @param cellRelative   the cell of the project, relative to the origin of the house
 * @param cellWorld      the cell of the project in the world
 * @param support        the block that was clicked
 * @param face           the face of the block that was clicked
 * @param access         the cell where the player has to stand for the click
 * @param player         the place where the player really was
 * @param wantedItem     the item that the project wants for the cell
 * @param heldItem       the item that was in the main hand
 * @param interactionSent true when the interaction of this attempt was sent
 * @param reach          the distance between the eyes of the player and the point of the click
 * @param observed       what the world showed in the cell after the window of the attempt
 * @param arrived        true when the player stood in the cell of the approach itself
 * @param actionResult   what the call of the interaction returned
 * @param yaw            the direction of the view of the player at the click
 * @param pitch          the angle of the view of the player at the click
 * @param clickVector    the point of the click in the world
 * @param diagnostics    the diagnostics of the attempt: the view of the client, the hit result of
 *                        the game, the slot of the player, the place of the player on the client and
 *                        on the server, the first precondition of the placement that did not hold
 *                        and the states of the cell of the project and of the cells around it
 */
public record PlacementFacts(String cellRelative, String cellWorld, String support, String face, String access,
                              String player, String wantedItem, String heldItem, boolean interactionSent,
                              double reach, String observed, boolean arrived, String actionResult,
                              double yaw, double pitch, String clickVector, PlacementDiagnostics diagnostics) {

    /**
     * The facts as one line of text.
     *
     * <p>The line is stable, so that a log of a run can be read against the facts of another
     * run without a search of the words of the sentence.
     *
     * @return the facts of the attempt as one line
     */
    public String describe() {
        return "cell=" + text(cellRelative)
                + " world=" + text(cellWorld)
                + " support=" + text(support)
                + " face=" + text(face)
                + " access=" + text(access)
                + " player=" + text(player)
                + " wanted=" + text(wantedItem)
                + " held=" + text(heldItem)
                + " interaction_sent=" + interactionSent
                + " reach=" + round(reach)
                + " arrived=" + arrived
                + " action_result=" + text(actionResult)
                + " yaw=" + round(yaw)
                + " pitch=" + round(pitch)
                + " click=" + text(clickVector)
                + " observed=" + text(observed)
                + (diagnostics == null ? "" : diagnostics.describe());
    }

    private static String text(String value) {
        return value == null || value.isBlank() ? "none" : value;
    }

    private static String round(double value) {
        return String.format(java.util.Locale.ROOT, "%.2f", value);
    }
}
