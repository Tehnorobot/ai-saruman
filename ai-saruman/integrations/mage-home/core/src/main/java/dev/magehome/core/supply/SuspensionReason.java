package dev.magehome.core.supply;

/** Reasons why the automation is suspended (ТЗ.md sections 4 and 5). */
public enum SuspensionReason {
    INVENTORY_FULL("inventory_full"),
    MATERIALS_MISSING("materials_missing"),
    SUPPLY_UNCONFIRMED("supply_unconfirmed"),
    NO_PLACEMENT_SUPPORT("no_placement_support"),
    PLACEMENT_UNCONFIRMED("placement_unconfirmed"),
    OBSTRUCTED("obstructed"),
    STORAGE_ERROR("storage_error"),
    BUSY("busy"),
    RESTART("restart"),
    INTERRUPTED("interrupted"),
    PLAYER_DEAD("player_dead"),
    WORLD_CHANGED("world_changed");

    private final String wireName;

    SuspensionReason(String wireName) {
        this.wireName = wireName;
    }

    public String wireName() {
        return wireName;
    }
}
