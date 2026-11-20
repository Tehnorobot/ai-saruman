package dev.magehome.core.registry;

/**
 * Assignment of the house variant: 0 for the first record of a world/dimension/player scope,
 * then 1, 2, 0... A candidate change before the first block and every resume keep the record
 * and its palette (ТЗ.md section 2).
 */
public final class VariantAssigner {

    private VariantAssigner() {
    }

    public static int nextVariant(HouseRegistry registry, String worldKey, String dimension, String playerUuid) {
        int count = registry.countInScope(worldKey, dimension, playerUuid);
        return count % 3;
    }
}
