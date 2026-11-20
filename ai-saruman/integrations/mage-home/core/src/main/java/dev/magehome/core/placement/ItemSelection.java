package dev.magehome.core.placement;

import java.util.List;

/**
 * The rule of the item in the hand of the player (ТЗ.md section 4).
 *
 * <p>The rule lives in the domain core, free of game types, so that it can be checked without
 * Minecraft. The client uses exactly this rule: the item of the project has to be in the main
 * hand before the interaction is sent, and the slot of the screen handler that holds the item is
 * found by reading the slots, never by computing an index by hand.
 *
 * <p>The facts come from the bytecode of Minecraft 1.21.11 (Yarn): the handler of the player
 * holds the slots of the inventory at the indices 9..35 and the slots of the hotbar at the
 * indices 36..44 ({@code PlayerScreenHandler.INVENTORY_START}, {@code HOTBAR_START}), and the
 * click of the client is applied to the slots of the handler and sent to the server
 * ({@code ClientPlayerInteractionManager.clickSlot}).
 */
public final class ItemSelection {

    /**
     * One slot of the screen handler of the player.
     *
     * @param index         the index of the slot in the screen handler
     * @param itemId        the identifier of the item in the slot; empty when the slot is empty
     * @param mainInventory true when the slot belongs to the inventory of the player and not to
     *                      the armour, the off hand or the table of the craft
     */
    public record SlotView(int index, String itemId, boolean mainInventory) {
    }

    private ItemSelection() {
    }

    /**
     * The slot of the screen handler that holds the wanted item.
     *
     * <p>Only the slots of the inventory of the player are used: a wanted item in the off hand
     * or in the armour is not a stack of the inventory and cannot be swapped into the hotbar.
     *
     * @param slots   the slots of the screen handler of the player
     * @param itemId  the identifier of the wanted item
     * @return the index of the slot or -1 when the item is not in the inventory
     */
    public static int slotWithItem(List<SlotView> slots, String itemId) {
        if (slots == null || itemId == null || itemId.isBlank()) {
            return -1;
        }
        for (SlotView slot : slots) {
            if (slot.mainInventory() && itemId.equals(slot.itemId())) {
                return slot.index();
            }
        }
        return -1;
    }

    /**
     * True when the interaction may be sent.
     *
     * <p>The item of the project has to be in the main hand: a click with another item in the
     * hand would place a wrong block or nothing at all, and the world would not change.
     *
     * @param wantedItemId the identifier of the item of the project
     * @param heldItemId   the identifier of the item in the main hand
     * @return true only when the main hand holds the wanted item
     */
    public static boolean mayClick(String wantedItemId, String heldItemId) {
        return wantedItemId != null && wantedItemId.equals(heldItemId);
    }
}
