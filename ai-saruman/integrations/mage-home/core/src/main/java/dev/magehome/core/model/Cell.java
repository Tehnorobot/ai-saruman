package dev.magehome.core.model;

/**
 * Cell of the house volume, relative to the origin A of the house.
 * x and z run 0..6, y runs 0..4, both bounds included (ТЗ.md section 2).
 */
public record Cell(int x, int y, int z) {

    public boolean inVolume() {
        return x >= 0 && x <= 6 && z >= 0 && z <= 6 && y >= 0 && y <= 4;
    }
}
