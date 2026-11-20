package dev.magehome.core.testing;

import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.sites.WorldView;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.template.HouseTemplate;
import dev.magehome.core.verification.ObservedBlock;

import java.util.HashMap;
import java.util.Map;

/**
 * A world that is described by a map of positions. It replaces the game for the domain checks; it
 * is not a stand in for UnionClef and it never loads a chunk.
 */
public final class FakeWorld implements WorldView {

    private final Map<Vec3i, ObservedBlock> blocks = new HashMap<>();
    private int chunkLoadRequests;

    public FakeWorld put(Vec3i position, ObservedBlock block) {
        blocks.put(position, block);
        return this;
    }

    public FakeWorld air(Vec3i position) {
        return put(position, ObservedBlock.air());
    }

    public FakeWorld solid(Vec3i position, String blockId) {
        return put(position, ObservedBlock.of(blockId, Map.of(), true, false, false));
    }

    public FakeWorld plant(Vec3i position, String blockId) {
        return put(position, ObservedBlock.of(blockId, Map.of(), false, false, true));
    }

    public FakeWorld liquid(Vec3i position, String blockId) {
        return put(position, ObservedBlock.of(blockId, Map.of(), false, true, false));
    }

    public FakeWorld fill(Vec3i from, Vec3i to, ObservedBlock block) {
        for (int x = from.x(); x <= to.x(); x++) {
            for (int y = from.y(); y <= to.y(); y++) {
                for (int z = from.z(); z <= to.z(); z++) {
                    blocks.put(new Vec3i(x, y, z), block);
                }
            }
        }
        return this;
    }

    @Override
    public ObservedBlock read(Vec3i position) {
        return blocks.getOrDefault(position, ObservedBlock.unknown());
    }

    public int chunkLoadRequests() {
        return chunkLoadRequests;
    }

    /** A world with a legal building site at the given origin: solid ground, free volume. */
    public static FakeWorld withSite(Vec3i origin) {
        FakeWorld world = new FakeWorld();
        for (int x = 0; x < 7; x++) {
            for (int z = 0; z < 7; z++) {
                world.solid(origin.add(x, -1, z), "minecraft:stone");
            }
        }
        world.solid(origin.add(3, -1, -1), "minecraft:stone");
        for (int x = 0; x < 7; x++) {
            for (int z = 0; z < 7; z++) {
                for (int y = 0; y < 5; y++) {
                    world.air(origin.add(x, y, z));
                }
            }
        }
        world.air(origin.add(3, 0, -1));
        world.air(origin.add(3, 1, -1));
        return world;
    }

    /** Writes the blocks of a placement operation into the world, as the game would do. */
    public void applyOperation(HouseTemplate template, CellSpec.Operation operation, Vec3i origin) {
        for (Cell cell : operation.cells()) {
            CellSpec spec = template.spec(cell);
            blocks.put(origin.add(cell.x(), cell.y(), cell.z()),
                    ObservedBlock.of(spec.blockId(), spec.requiredProperties(), true, false, false));
        }
    }

    /** Writes the whole house into the world. */
    public void applyHouse(HouseTemplate template, Vec3i origin) {
        for (Cell cell : template.occupiedCells()) {
            CellSpec spec = template.spec(cell);
            blocks.put(origin.add(cell.x(), cell.y(), cell.z()),
                    ObservedBlock.of(spec.blockId(), spec.requiredProperties(), true, false, false));
        }
    }
}
