package dev.magehome.core.registry;

/** Lifecycle of a house record (ТЗ.md section 5). */
public enum HouseState {
    BUILDING("building"),
    READY("ready"),
    SUSPENDED("suspended"),
    DAMAGED("damaged");

    private final String wireName;

    HouseState(String wireName) {
        this.wireName = wireName;
    }

    public String wireName() {
        return wireName;
    }

    public static HouseState fromWire(String value) {
        for (HouseState state : values()) {
            if (state.wireName.equals(value)) {
                return state;
            }
        }
        throw new IllegalArgumentException("unknown house state: " + value);
    }
}
