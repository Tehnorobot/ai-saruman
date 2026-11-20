package dev.magehome.core.supply;

import java.util.ArrayList;
import java.util.List;

/**
 * Builds the give commands of the supply step: {@code give @s minecraft:<item> <deficit>} without a
 * leading slash, at most 64 items per command (ТЗ.md section 4).
 */
public final class GiveCommandPlanner {

    public static final int MAX_ITEMS_PER_COMMAND = 64;

    private GiveCommandPlanner() {
    }

    public static List<String> plan(List<ItemNeed> needs) {
        List<String> commands = new ArrayList<>();
        for (ItemNeed need : needs) {
            int deficit = need.deficit();
            while (deficit > 0) {
                int chunk = Math.min(MAX_ITEMS_PER_COMMAND, deficit);
                commands.add("give @s " + need.itemId() + " " + chunk);
                deficit -= chunk;
            }
        }
        return commands;
    }
}
