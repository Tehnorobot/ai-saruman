package dev.magehome.core.acceptance;

import dev.magehome.core.registry.HouseRecord;
import dev.magehome.core.registry.HouseState;
import dev.magehome.core.supply.GiveAvailability;

import java.util.List;
import java.util.Map;

/**
 * The rules of the readiness of the stand of an acceptance scenario (ТЗ.md section 8).
 *
 * <p>The rules live in the domain core, so that the client harness and the executable checks
 * apply exactly the same rule and not two copies of it. A precondition of a scenario is a setup
 * check of the stand: it never proves anything about the product itself.
 */
public final class StandRules {

    /** The result of the readiness rule of the stand. */
    public enum Readiness {
        /** The stand shows the situation of the scenario. */
        READY("the world is loaded, the player is alive and UnionClef is loaded"),
        /** The world is not loaded. */
        NO_WORLD("the world is not loaded"),
        /** The player is not alive. */
        NO_PLAYER("the player is not alive"),
        /** The player of the world is not known. */
        NO_PLAYER_ID("the player of the world is not known"),
        /** The mod of UnionClef is not loaded. */
        UNIONCLEF_MISSING("UnionClef 0.95.61 is not loaded"),
        /**
         * The chain of tasks of UnionClef does not exist yet.
         *
         * <p>The chain is created in {@code AltoClef.onInitializeLoad()} of the delivered
         * UnionClef; before that the mod cannot hold a task, so the stand does not show the
         * situation of a scenario that needs the task chain of the add-on.
         */
        UNIONCLEF_CHAIN_MISSING("the task chain of UnionClef does not exist yet"),
        /**
         * The slot handler of UnionClef does not exist yet.
         *
         * <p>The slot handler is created in the same method as the task chain
         * ({@code AltoClef.onInitializeLoad()}). The mixin of UnionClef on the inventory of the
         * server calls it, so a missing slot handler is a fact of the environment that the stand
         * has to name: the add-on cannot move the player without it.
         */
        UNIONCLEF_SLOT_HANDLER_MISSING("the slot handler of UnionClef does not exist yet");

        private final String description;

        Readiness(String description) {
            this.description = description;
        }

        public String description() {
            return description;
        }

        public boolean ready() {
            return this == READY;
        }
    }

    private StandRules() {
    }

    /**
     * The readiness of the stand.
     *
     * <p>Every fact is read on its own: the world, the player, the uuid of the player, the mod
     * of UnionClef, the task chain of UnionClef and the slot handler of UnionClef. The rule does
     * not guess a cause: it names the first fact that does not hold.
     *
     * @param worldLoaded          the world of the client is loaded
     * @param playerAlive          the player is alive
     * @param playerKnown          the uuid of the player of the world is known
     * @param unionClefLoaded      the mod of UnionClef is loaded
     * @param chainAvailable       the chain of tasks of UnionClef exists
     * @param slotHandlerAvailable the slot handler of UnionClef exists
     */
    public static Readiness readiness(boolean worldLoaded, boolean playerAlive, boolean playerKnown,
                                      boolean unionClefLoaded, boolean chainAvailable,
                                      boolean slotHandlerAvailable) {
        if (!worldLoaded) {
            return Readiness.NO_WORLD;
        }
        if (!playerAlive) {
            return Readiness.NO_PLAYER;
        }
        if (!playerKnown) {
            return Readiness.NO_PLAYER_ID;
        }
        if (!unionClefLoaded) {
            return Readiness.UNIONCLEF_MISSING;
        }
        if (!chainAvailable) {
            return Readiness.UNIONCLEF_CHAIN_MISSING;
        }
        if (!slotHandlerAvailable) {
            return Readiness.UNIONCLEF_SLOT_HANDLER_MISSING;
        }
        return Readiness.READY;
    }

    /**
     * The inventory of the player is empty.
     *
     * @param itemCounts the number of items of every type in the inventory
     */
    public static boolean inventoryEmpty(Map<String, Integer> itemCounts) {
        if (itemCounts == null) {
            return false;
        }
        for (Integer count : itemCounts.values()) {
            if (count != null && count != 0) {
                return false;
            }
        }
        return true;
    }

    /**
     * The command tree of the server contains the command {@code give}.
     *
     * <p>A command tree that was not read yet is not a missing command: the availability of the
     * command is unknown then, and the stand of the scenario does not show the situation.
     *
     * @param availability what the client knows about the command
     */
    public static boolean giveAvailable(GiveAvailability availability) {
        return availability != null && availability.available();
    }

    /**
     * The registry holds exactly one record and it has the expected variant.
     *
     * @param records         the records of the registry
     * @param expectedVariant the variant of the seeded record
     */
    public static boolean exactlyOneSeededRecord(List<HouseRecord> records, int expectedVariant) {
        if (records == null || records.size() != 1) {
            return false;
        }
        return records.get(0).variant() == expectedVariant;
    }

    /**
     * The registry holds a record in the given state.
     *
     * @param records the records of the registry
     * @param state   the expected state of the record
     */
    public static boolean hasRecordInState(List<HouseRecord> records, HouseState state) {
        if (records == null) {
            return false;
        }
        for (HouseRecord record : records) {
            if (record.state() == state) {
                return true;
            }
        }
        return false;
    }
}
