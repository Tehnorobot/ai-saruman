package dev.magehome.core.supply;

/**
 * What the client knows about the command {@code give} of the server.
 *
 * <p>The facts come from the bytecode of Minecraft 1.21.11 (Yarn):
 * {@code ClientPlayNetworkHandler.onCommandTree(CommandTreeS2CPacket)} replaces the field
 * {@code commandDispatcher} with a dispatcher that is built from the command tree of the server,
 * and the constructor of the handler fills the field with an empty dispatcher. The root of the
 * empty dispatcher has no child at all, while the root of the tree of a server always has
 * children. Therefore:
 *
 * <ul>
 *   <li>the root has no child: the tree of the server was not read yet - the availability of the
 *       command is unknown, not absent;</li>
 *   <li>the root has children and none of them is {@code give}: the server was read and it has
 *       no such command;</li>
 *   <li>the root has a child {@code give}: the server offers the command.</li>
 * </ul>
 *
 * <p>The distinction matters: a command that was not read yet must never be reported as a
 * missing command, because that would suspend the house with the wrong reason.
 */
public enum GiveAvailability {

    /** The command tree of the server was read and it holds the command {@code give}. */
    AVAILABLE("the command tree of the server holds give"),
    /** The command tree of the server was read and it holds no command {@code give}. */
    ABSENT("the command tree of the server holds no give"),
    /** The command tree of the server was not read yet. */
    UNKNOWN("the command tree of the server was not read yet");

    private final String description;

    GiveAvailability(String description) {
        this.description = description;
    }

    public String description() {
        return description;
    }

    /** True when the command can be used. */
    public boolean available() {
        return this == AVAILABLE;
    }

    /** True when the server was read and it has no such command. */
    public boolean absent() {
        return this == ABSENT;
    }

    /** True when nothing is known about the command yet. */
    public boolean unknown() {
        return this == UNKNOWN;
    }
}
