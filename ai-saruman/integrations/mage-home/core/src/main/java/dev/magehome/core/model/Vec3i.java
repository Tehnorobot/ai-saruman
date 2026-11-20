package dev.magehome.core.model;

/** Immutable world position. Deliberately free of any Minecraft type. */
public record Vec3i(int x, int y, int z) {

    public Vec3i add(int dx, int dy, int dz) {
        return new Vec3i(x + dx, y + dy, z + dz);
    }

    public Vec3i add(Vec3i delta) {
        return new Vec3i(x + delta.x, y + delta.y, z + delta.z);
    }

    /** Horizontal (X/Z) distance; height is never part of the service radius. */
    public double horizontalDistanceTo(Vec3i other) {
        double dx = (double) x - other.x;
        double dz = (double) z - other.z;
        return Math.sqrt(dx * dx + dz * dz);
    }

    public static Vec3i fromArray(int[] values) {
        if (values == null || values.length != 3) {
            throw new IllegalArgumentException("expected three coordinates");
        }
        return new Vec3i(values[0], values[1], values[2]);
    }

    public int[] toArray() {
        return new int[]{x, y, z};
    }
}
