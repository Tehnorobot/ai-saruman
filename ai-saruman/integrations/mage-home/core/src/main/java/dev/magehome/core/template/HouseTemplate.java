package dev.magehome.core.template;

import dev.magehome.core.model.Cell;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The exact house project of ТЗ.md section 2.
 *
 * <p>The map is a pure function of the variant: 7x7x5 cells (245), 180 occupied and 65 air.
 * A door and a bed are placed with one item each, therefore 178 items are needed for a house
 * that is built from an empty inventory.
 */
public final class HouseTemplate {

    public static final int SIZE_X = 7;
    public static final int SIZE_Z = 7;
    public static final int SIZE_Y = 5;
    public static final int TOTAL_CELLS = SIZE_X * SIZE_Z * SIZE_Y;
    public static final int OCCUPIED_CELLS = 180;
    public static final int AIR_CELLS = 65;
    public static final int ITEM_COUNT = 178;

    /** Glass windows of the project (relative cells). */
    public static final List<Cell> GLASS_CELLS = List.of(
            new Cell(1, 2, 0),
            new Cell(5, 2, 0),
            new Cell(0, 2, 3),
            new Cell(6, 2, 3),
            new Cell(3, 2, 6));

    /** The two cells of the door (one spruce door item, facing=south). */
    public static final Cell DOOR_LOWER = new Cell(3, 1, 0);
    public static final Cell DOOR_UPPER = new Cell(3, 2, 0);

    /** Required hinge for both halves of the entrance door. */
    public static final String DOOR_HINGE = "left";

    /** The two cells of the bed (one bed item, facing=north). */
    public static final Cell BED_FOOT = new Cell(1, 1, 4);
    public static final Cell BED_HEAD = new Cell(1, 1, 3);

    private final Palette palette;
    private final Map<Cell, CellSpec> cells;
    private final List<CellSpec.Operation> operations;
    private final Map<String, Integer> itemNeeds;
    private final Set<String> materialIds;

    private HouseTemplate(Palette palette, Map<Cell, CellSpec> cells) {
        this.palette = palette;
        this.cells = Collections.unmodifiableMap(cells);
        this.operations = Collections.unmodifiableList(buildOperations(cells));
        Map<String, Integer> needs = new LinkedHashMap<>();
        for (CellSpec.Operation operation : this.operations) {
            needs.merge(operation.itemId(), 1, Integer::sum);
        }
        this.itemNeeds = Collections.unmodifiableMap(needs);
        // The materials of the project are read from the cells of the project itself and not
        // from a list written by hand: every block that the project places is a material, and
        // every other block is a foreign block (ТЗ.md section 4).
        Set<String> materials = new LinkedHashSet<>();
        for (CellSpec spec : cells.values()) {
            materials.add(spec.blockId());
        }
        this.materialIds = Collections.unmodifiableSet(materials);
    }

    /**
     * The registry ids of the blocks that the project places.
     *
     * <p>A block that is not in this set is a foreign block: a tree, a crop or any other change
     * of the world that the project did not make. The add-on never breaks such a block
     * (ТЗ.md section 4).
     *
     * @return the ids of the materials of the project
     */
    public Set<String> materialIds() {
        return materialIds;
    }

    /**
     * True when the block is a material of the project.
     *
     * @param blockId the registry id of the block
     * @return true for a block that the project places
     */
    public boolean isProjectMaterial(String blockId) {
        return blockId != null && materialIds.contains(blockId);
    }

    public static HouseTemplate forVariant(int variant) {
        return new HouseTemplate(Palette.of(variant), buildCells(Palette.of(variant)));
    }

    public Palette palette() {
        return palette;
    }

    public int variant() {
        return palette.variant();
    }

    /** All 180 occupied cells with their required block state. */
    public Map<Cell, CellSpec> cells() {
        return cells;
    }

    public CellSpec spec(Cell cell) {
        return cells.get(cell);
    }

    public List<Cell> occupiedCells() {
        return List.copyOf(cells.keySet());
    }

    /** Every cell of the volume, occupied or not. */
    public List<Cell> volumeCells() {
        List<Cell> all = new ArrayList<>(TOTAL_CELLS);
        for (int x = 0; x < SIZE_X; x++) {
            for (int z = 0; z < SIZE_Z; z++) {
                for (int y = 0; y < SIZE_Y; y++) {
                    all.add(new Cell(x, y, z));
                }
            }
        }
        return all;
    }

    public List<Cell> airCells() {
        List<Cell> air = new ArrayList<>();
        for (Cell cell : volumeCells()) {
            if (!cells.containsKey(cell)) {
                air.add(cell);
            }
        }
        return air;
    }

    /** Placement operations: one item each, a door and a bed cover two cells. */
    public List<CellSpec.Operation> operations() {
        return operations;
    }

    /** Item id to required count for a complete house (178 items in total). */
    public Map<String, Integer> itemNeeds() {
        return itemNeeds;
    }

    public int itemCount() {
        int total = 0;
        for (int count : itemNeeds.values()) {
            total += count;
        }
        return total;
    }

    /**
     * The order in which the operations are built.
     *
     * <p>Build the walls first, then the roof perimeter and successive inner rings. The
     * inner rings have a previously planned horizontal neighbour for support. Furniture
     * precedes the door so that the entrance remains open. This ordering alone does not
     * establish that the player can reach every placement in the game.
     *
     * @param cells the cells of the project
     * @return the operations in the order in which they are built
     */
    private static List<CellSpec.Operation> buildOperations(Map<Cell, CellSpec> cells) {
        List<CellSpec.Operation> operations = new ArrayList<>();
        List<Cell> ring = ringOrder();
        Set<Cell> doorCells = new LinkedHashSet<>();
        for (Map.Entry<Cell, CellSpec> entry : cells.entrySet()) {
            if (entry.getValue().placement() == CellSpec.Placement.DOOR) {
                doorCells.add(entry.getKey());
            }
        }

        // 1. The floor of the house: the player stands on the ground next to it.
        for (int x = 0; x < SIZE_X; x++) {
            for (int z = 0; z < SIZE_Z; z++) {
                addSingle(operations, cells, new Cell(x, 0, z));
            }
        }

        // 2. The first level of the walls: the player stands on the floor inside the house.
        for (Cell cell : ring) {
            if (doorCells.contains(cell)) {
                // The door is one item for two cells; it replaces the wall of this position and it
                // is built at the end, when the player stands on the floor inside the house.
                continue;
            }
            addSingle(operations, cells, new Cell(cell.x(), 1, cell.z()));
        }

        // 3. The second level of the walls: the player stands on the crest of the first level,
        // in the free cell of the second level of the next position of the ring.
        for (Cell cell : ring) {
            addSingle(operations, cells, new Cell(cell.x(), 2, cell.z()));
        }

        // 4. The third level of the walls: the player stands in the free cell of the second level
        // inside the house, on a temporary column of one block, and clicks the upper face of the
        // wall of the second level. The player never stands on the crest of the third level: from
        // there a fall is four blocks and the chain of the fall of the dependency takes the task
        // away (2026-11-13).
        for (Cell cell : ring) {
            addSingle(operations, cells, new Cell(cell.x(), 3, cell.z()));
        }

        // 5. The ring of the roof: the player stands in the free cell of the third level inside the
        // house, on a temporary column of two blocks, and clicks the upper face of the wall of the
        // third level. Every cell of the ring stands on its own wall, so the ring is not a chain:
        // the player may build it in any order, and the last cell of the ring needs no free cell
        // of a neighbour (ТЗ.md section 4).
        for (Cell cell : ring) {
            addSingle(operations, cells, new Cell(cell.x(), 4, cell.z()));
        }

        // 6. The interior of the roof, from outside in.
        for (int inset = 1; inset <= 3; inset++) {
            for (int x = inset; x < SIZE_X - inset; x++) {
                for (int z = inset; z < SIZE_Z - inset; z++) {
                    if (x == inset || x == SIZE_X - 1 - inset
                            || z == inset || z == SIZE_Z - 1 - inset) {
                        addSingle(operations, cells, new Cell(x, 4, z));
                    }
                }
            }
        }

        // 7. Furnish the interior before closing the doorway, so a player outside can enter.
        // The bed is one item for two cells.
        List<Cell> bedCells = new ArrayList<>();
        for (Map.Entry<Cell, CellSpec> entry : cells.entrySet()) {
            CellSpec spec = entry.getValue();
            if (spec.placement() == CellSpec.Placement.BED) {
                bedCells.add(entry.getKey());
            }
        }
        for (Map.Entry<Cell, CellSpec> entry : cells.entrySet()) {
            CellSpec spec = entry.getValue();
            Cell cell = entry.getKey();
            if (spec.placement() != CellSpec.Placement.SINGLE) {
                continue;
            }
            if (cell.y() == 0 || cell.y() == 4 || isWallLevel(cell)) {
                // The floor, the roof and the walls have their own place in the order.
                continue;
            }
            operations.add(new CellSpec.Operation(spec.itemId(), List.of(cell),
                    CellSpec.Placement.SINGLE));
        }
        if (!bedCells.isEmpty()) {
            operations.add(new CellSpec.Operation(cells.get(BED_FOOT).itemId(), bedCells,
                    CellSpec.Placement.BED));
        }
        // Place the two-cell entrance door only after the furniture.
        if (!doorCells.isEmpty()) {
            operations.add(new CellSpec.Operation("minecraft:spruce_door",
                    List.of(DOOR_LOWER, DOOR_UPPER), CellSpec.Placement.DOOR));
        }
        return operations;
    }

    /** True when the cell belongs to the wall of the house. */
    private static boolean isWallLevel(Cell cell) {
        boolean perimeter = cell.x() == 0 || cell.x() == SIZE_X - 1
                || cell.z() == 0 || cell.z() == SIZE_Z - 1;
        return perimeter && cell.y() >= 1 && cell.y() <= 3;
    }

    /** Adds the operation of a cell when the project holds one for it. */
    private static void addSingle(List<CellSpec.Operation> operations, Map<Cell, CellSpec> cells, Cell cell) {
        CellSpec spec = cells.get(cell);
        if (spec == null || spec.placement() != CellSpec.Placement.SINGLE) {
            return;
        }
        operations.add(new CellSpec.Operation(spec.itemId(), List.of(cell), CellSpec.Placement.SINGLE));
    }

    /**
     * The perimeter of the house, in the order in which it is walked.
     *
     * <p>The order goes around the ring: the player walks along the wall and every cell of the
     * ring is built from the cell that is one step ahead.
     *
     * @return the cells of the perimeter, without duplicates
     */
    private static List<Cell> ringOrder() {
        List<Cell> ring = new ArrayList<>();
        for (int z = 0; z < SIZE_Z; z++) {
            ring.add(new Cell(0, 1, z));
        }
        for (int x = 1; x < SIZE_X; x++) {
            ring.add(new Cell(x, 1, SIZE_Z - 1));
        }
        for (int z = SIZE_Z - 2; z >= 0; z--) {
            ring.add(new Cell(SIZE_X - 1, 1, z));
        }
        for (int x = SIZE_X - 2; x >= 1; x--) {
            ring.add(new Cell(x, 1, 0));
        }
        return ring;
    }

    private static Map<Cell, CellSpec> buildCells(Palette palette) {
        Map<Cell, CellSpec> cells = new LinkedHashMap<>();

        // 1. floor: all 49 cells of the bottom layer.
        for (int x = 0; x < SIZE_X; x++) {
            for (int z = 0; z < SIZE_Z; z++) {
                cells.put(new Cell(x, 0, z), new CellSpec(palette.floor(), Map.of(), palette.floor(),
                        CellSpec.Placement.SINGLE, new Cell(x, 0, z)));
            }
        }

        // 2. roof: all 49 cells of the top layer, plain full blocks.
        for (int x = 0; x < SIZE_X; x++) {
            for (int z = 0; z < SIZE_Z; z++) {
                Cell cell = new Cell(x, 4, z);
                cells.put(cell, new CellSpec(palette.roof(), Map.of(), palette.roof(),
                        CellSpec.Placement.SINGLE, cell));
            }
        }

        // 3. perimeter walls for y = 1..3, corners are posts with axis=y.
        for (int y = 1; y <= 3; y++) {
            for (int x = 0; x < SIZE_X; x++) {
                for (int z = 0; z < SIZE_Z; z++) {
                    boolean perimeter = x == 0 || x == 6 || z == 0 || z == 6;
                    if (!perimeter) {
                        continue;
                    }
                    boolean corner = (x == 0 || x == 6) && (z == 0 || z == 6);
                    Cell cell = new Cell(x, y, z);
                    if (corner) {
                        cells.put(cell, new CellSpec(palette.post(), CellSpec.props("axis", "y"), palette.post(),
                                CellSpec.Placement.SINGLE, cell));
                    } else {
                        cells.put(cell, new CellSpec(palette.wall(), Map.of(), palette.wall(),
                                CellSpec.Placement.SINGLE, cell));
                    }
                }
            }
        }

        // 4. five windows.
        for (Cell cell : GLASS_CELLS) {
            cells.put(cell, new CellSpec("minecraft:glass", Map.of(), "minecraft:glass",
                    CellSpec.Placement.SINGLE, cell));
        }

        // 5. the door: one spruce door item, facing=south, hinge=left.
        cells.put(DOOR_LOWER, new CellSpec("minecraft:spruce_door",
                CellSpec.props("half", "lower", "facing", "south", "hinge", DOOR_HINGE),
                "minecraft:spruce_door", CellSpec.Placement.DOOR, DOOR_LOWER));
        cells.put(DOOR_UPPER, new CellSpec("minecraft:spruce_door",
                CellSpec.props("half", "upper", "facing", "south", "hinge", DOOR_HINGE),
                "minecraft:spruce_door", CellSpec.Placement.DOOR, DOOR_UPPER));

        // 6. furniture.
        put(cells, new Cell(1, 1, 4), palette.bed(), CellSpec.props("part", "foot", "facing", "north"),
                palette.bed(), CellSpec.Placement.BED);
        put(cells, new Cell(1, 1, 3), palette.bed(), CellSpec.props("part", "head", "facing", "north"),
                palette.bed(), CellSpec.Placement.BED);
        put(cells, new Cell(5, 1, 4), "minecraft:barrel", CellSpec.props("facing", "up"), "minecraft:barrel",
                CellSpec.Placement.SINGLE);
        put(cells, new Cell(5, 1, 5), "minecraft:barrel", CellSpec.props("facing", "up"), "minecraft:barrel",
                CellSpec.Placement.SINGLE);
        put(cells, new Cell(1, 1, 1), "minecraft:crafting_table", Map.of(), "minecraft:crafting_table",
                CellSpec.Placement.SINGLE);
        put(cells, new Cell(2, 1, 1), "minecraft:furnace", CellSpec.props("facing", "south"), "minecraft:furnace",
                CellSpec.Placement.SINGLE);
        put(cells, new Cell(5, 1, 1), "minecraft:bookshelf", Map.of(), "minecraft:bookshelf",
                CellSpec.Placement.SINGLE);
        put(cells, new Cell(5, 2, 1), "minecraft:bookshelf", Map.of(), "minecraft:bookshelf",
                CellSpec.Placement.SINGLE);
        put(cells, new Cell(2, 1, 5), "minecraft:lantern", CellSpec.props("hanging", "false", "waterlogged", "false"),
                "minecraft:lantern", CellSpec.Placement.SINGLE);
        put(cells, new Cell(4, 1, 5), "minecraft:lantern", CellSpec.props("hanging", "false", "waterlogged", "false"),
                "minecraft:lantern", CellSpec.Placement.SINGLE);

        return cells;
    }

    private static void put(Map<Cell, CellSpec> cells, Cell cell, String blockId,
                            Map<String, String> properties, String itemId, CellSpec.Placement placement) {
        cells.put(cell, new CellSpec(blockId, properties, itemId, placement, cell));
    }
}
