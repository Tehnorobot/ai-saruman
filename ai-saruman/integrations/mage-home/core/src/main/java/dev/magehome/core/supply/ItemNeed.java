package dev.magehome.core.supply;

/**
 * One item type that the house still needs: {@code required} items are missing according to the
 * project, {@code available} items are in the inventory.
 */
public record ItemNeed(String itemId, int required, int available) {

    public int deficit() {
        return Math.max(0, required - available);
    }
}
