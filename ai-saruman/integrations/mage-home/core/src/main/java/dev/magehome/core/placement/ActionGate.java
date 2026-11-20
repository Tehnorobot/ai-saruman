package dev.magehome.core.placement;

/**
 * The gate of the background actions of the client (ТЗ.md sections 3 and 5).
 *
 * <p>While UnionClef is paused, while a task of another owner controls the chain, while the chain
 * of UnionClef does not exist yet or while the automation is suspended, the add-on sends no
 * interaction with the world, asks for no navigation, sends no {@code give} command and starts no
 * building. An operation that was requested before the gate was closed is not performed
 * afterwards.
 *
 * <p>The rule is kept here, free of game types, so that it can be checked without Minecraft. The
 * client uses exactly this method; the check reads the client source to make sure that the
 * tested rule is the one the client applies.
 */
public final class ActionGate {

    private ActionGate() {
    }

    /**
     * True when the client may act on the world.
     *
     * @param enabled           the automation is enabled
     * @param suspended         the automation is suspended and waits for an explicit resume
     * @param unionClefPaused   UnionClef is paused
     * @param foreignTaskActive a task of another owner controls the chain of UnionClef
     * @param chainAvailable    the chain of UnionClef exists (it is created in
     *                          {@code AltoClef.onInitializeLoad()})
     */
    public static boolean mayAct(boolean enabled, boolean suspended, boolean unionClefPaused,
                                 boolean foreignTaskActive, boolean chainAvailable) {
        return enabled && !suspended && !unionClefPaused && !foreignTaskActive && chainAvailable;
    }
}
