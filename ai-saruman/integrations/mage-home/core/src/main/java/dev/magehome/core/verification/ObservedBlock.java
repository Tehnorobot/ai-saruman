package dev.magehome.core.verification;

import java.util.Map;

/**
 * One observed block of the world.
 *
 * @param known         false when the cell is not loaded; unknown is never treated as air
 * @param blockId       registry id of the block, {@code minecraft:air} for air
 * @param properties    block state properties (facing, part, half, axis, open, lit, ...)
 * @param solidFull     true for a full solid block (valid support)
 * @param liquid        true for water or lava
 * @param removablePlant true for short grass, tall grass, fern and large fern
 * @param container     true for a block that holds something (chest, barrel, furnace, ...)
 */
public record ObservedBlock(boolean known, String blockId, Map<String, String> properties, boolean solidFull,
                            boolean liquid, boolean removablePlant, boolean container) {

    public static final String AIR = "minecraft:air";

    public ObservedBlock {
        properties = Map.copyOf(properties);
    }

    public static ObservedBlock unknown() {
        return new ObservedBlock(false, AIR, Map.of(), false, false, false, false);
    }

    public static ObservedBlock air() {
        return new ObservedBlock(true, AIR, Map.of(), false, false, false, false);
    }

    public static ObservedBlock of(String blockId, Map<String, String> properties) {
        return new ObservedBlock(true, blockId, properties, false, false, false, false);
    }

    public static ObservedBlock of(String blockId, Map<String, String> properties, boolean solidFull,
                                   boolean liquid, boolean removablePlant) {
        return new ObservedBlock(true, blockId, properties, solidFull, liquid, removablePlant, false);
    }

    public static ObservedBlock of(String blockId, Map<String, String> properties, boolean solidFull,
                                   boolean liquid, boolean removablePlant, boolean container) {
        return new ObservedBlock(true, blockId, properties, solidFull, liquid, removablePlant, container);
    }

    public boolean isAir() {
        return AIR.equals(blockId);
    }

    public String property(String key) {
        return properties.get(key);
    }
}
