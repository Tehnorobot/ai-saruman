package dev.magehome.core.registry;

/**
 * Visibility of a house area. It is kept apart from the house state: an unloaded area never
 * turns into "damaged" (ТЗ.md section 5).
 */
public enum Visibility {
    LOADED("loaded"),
    UNLOADED("unloaded");

    private final String wireName;

    Visibility(String wireName) {
        this.wireName = wireName;
    }

    public String wireName() {
        return wireName;
    }
}
