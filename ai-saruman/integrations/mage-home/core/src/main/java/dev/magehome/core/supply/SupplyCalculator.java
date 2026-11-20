package dev.magehome.core.supply;

import dev.magehome.core.model.Cell;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.template.HouseTemplate;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Calculation of the missing items: the still unfinished cells of the project minus the items
 * that are already in the inventory. A door and a bed count as one item each (ТЗ.md section 4).
 */
public final class SupplyCalculator {

    private SupplyCalculator() {
    }

    public static List<ItemNeed> needs(HouseTemplate template, Set<Cell> unfinishedCells,
                                       Map<String, Integer> inventory) {
        Map<String, Integer> required = new LinkedHashMap<>();
        for (CellSpec.Operation operation : template.operations()) {
            boolean unfinished = false;
            for (Cell cell : operation.cells()) {
                if (unfinishedCells.contains(cell)) {
                    unfinished = true;
                    break;
                }
            }
            if (unfinished) {
                required.merge(operation.itemId(), 1, Integer::sum);
            }
        }
        List<ItemNeed> needs = new ArrayList<>();
        for (Map.Entry<String, Integer> entry : required.entrySet()) {
            int available = inventory.getOrDefault(entry.getKey(), 0);
            needs.add(new ItemNeed(entry.getKey(), entry.getValue(), available));
        }
        return needs;
    }
}
