package dev.magehome.core.template;

import dev.magehome.core.model.Cell;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * One occupied cell of the template: the block id, the properties the project requires and the
 * item that has to be used for the placement.
 */
public record CellSpec(String blockId, Map<String, String> requiredProperties, String itemId,
                       Placement placement, Cell cell) {

    /** Placement kind: a door or a bed is placed with a single item but covers two cells. */
    public enum Placement {
        SINGLE,
        DOOR,
        BED
    }

    public CellSpec {
        requiredProperties = Map.copyOf(requiredProperties);
    }

    public boolean isMultiCell() {
        return placement != Placement.SINGLE;
    }

    /**
     * One placement operation: the cells that a single item fills. A door and a bed are one
     * operation each (ТЗ.md section 2).
     */
    public record Operation(String itemId, List<Cell> cells, Placement placement) {
        public Operation {
            cells = List.copyOf(cells);
        }

        public boolean isMultiCell() {
            return placement != Placement.SINGLE;
        }
    }

    static Map<String, String> props(String... pairs) {
        Map<String, String> map = new LinkedHashMap<>();
        for (int i = 0; i + 1 < pairs.length; i += 2) {
            map.put(pairs[i], pairs[i + 1]);
        }
        return map;
    }
}
