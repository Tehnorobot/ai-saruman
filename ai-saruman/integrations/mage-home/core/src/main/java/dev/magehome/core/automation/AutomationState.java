package dev.magehome.core.automation;

/** State of the automation itself; it is separate from the state of a house (ТЗ.md section 6). */
public enum AutomationState {
    DISABLED("disabled"),
    IDLE("idle"),
    SEARCHING("searching"),
    BUILDING("building"),
    WAITING("waiting"),
    SUSPENDED("suspended");

    private final String wireName;

    AutomationState(String wireName) {
        this.wireName = wireName;
    }

    public String wireName() {
        return wireName;
    }
}
