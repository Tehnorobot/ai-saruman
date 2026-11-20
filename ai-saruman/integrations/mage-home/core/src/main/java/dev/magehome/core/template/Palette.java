package dev.magehome.core.template;

/**
 * The three colour palettes of ТЗ.md section 2. All ids carry the {@code minecraft:} namespace.
 * Variant 0 is used for the first house record of a world/dimension/player scope, then 1, 2, 0...
 */
public record Palette(int variant, String floor, String post, String wall, String roof, String bed) {

    public static Palette of(int variant) {
        return switch (variant) {
            case 0 -> new Palette(0,
                    "minecraft:stone_bricks",
                    "minecraft:stripped_dark_oak_log",
                    "minecraft:purple_terracotta",
                    "minecraft:dark_oak_planks",
                    "minecraft:purple_bed");
            case 1 -> new Palette(1,
                    "minecraft:polished_deepslate",
                    "minecraft:stripped_spruce_log",
                    "minecraft:blue_terracotta",
                    "minecraft:spruce_planks",
                    "minecraft:blue_bed");
            case 2 -> new Palette(2,
                    "minecraft:mossy_stone_bricks",
                    "minecraft:stripped_oak_log",
                    "minecraft:cyan_terracotta",
                    "minecraft:oak_planks",
                    "minecraft:cyan_bed");
            default -> throw new IllegalArgumentException("unknown house variant: " + variant);
        };
    }
}
