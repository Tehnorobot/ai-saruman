package dev.magehome.core.automation;

import dev.magehome.core.model.Vec3i;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Batched enumeration of the candidate sites (ТЗ.md section 3).
 *
 * <p>The candidates are derived from the player position that was recorded at the start of the
 * search: the centre of the candidate may not be farther than 32 blocks away and the floor level
 * may not differ by more than 4 blocks. The search runs in small batches so that the client tick
 * is never blocked, and an unreachable candidate is skipped instead of being retried forever.
 */
public final class SiteSearch {

    /** Number of candidates that are checked in one tick. */
    public static final int BATCH_SIZE = 8;

    private final List<Vec3i> offsets = new ArrayList<>();
    private final Set<Vec3i> rejected = new LinkedHashSet<>();
    private Vec3i base;
    private int cursor;

    public SiteSearch(Vec3i base) {
        this.base = base;
        buildOffsets();
    }

    private void buildOffsets() {
        offsets.clear();
        for (int dx = -32; dx <= 32; dx++) {
            for (int dz = -32; dz <= 32; dz++) {
                // The centre of the candidate is base + (3, 0, 3) + (dx, 0, dz).
                double cx = dx + 3;
                double cz = dz + 3;
                if (cx * cx + cz * cz > 32.0 * 32.0) {
                    continue;
                }
                offsets.add(new Vec3i(dx, 0, dz));
            }
        }
        offsets.sort(Comparator
                .comparingDouble((Vec3i o) -> {
                    double cx = o.x() + 3;
                    double cz = o.z() + 3;
                    return cx * cx + cz * cz;
                })
                .thenComparingInt(Vec3i::x)
                .thenComparingInt(Vec3i::z));
    }

    public Vec3i base() {
        return base;
    }

    /** Moves the search point; used after a search without a site (8 blocks). */
    public void shift(int blocks) {
        base = base.add(blocks, 0, 0);
        cursor = 0;
    }

    public void restart() {
        cursor = 0;
    }

    /**
     * The next batch of candidate origins. A candidate that was rejected before is skipped, so a
     * failed approach is not repeated endlessly.
     */
    public List<Vec3i> nextBatch() {
        List<Vec3i> batch = new ArrayList<>();
        int guard = 0;
        while (batch.size() < BATCH_SIZE && cursor < offsets.size() && guard < offsets.size()) {
            Vec3i offset = offsets.get(cursor);
            cursor++;
            guard++;
            Vec3i origin = base.add(offset);
            if (rejected.contains(origin)) {
                continue;
            }
            batch.add(origin);
        }
        return batch;
    }

    /** Rejects a candidate and starts the enumeration again. */
    public void reject(Vec3i origin) {
        rejected.add(origin);
        cursor = 0;
    }

    public boolean exhausted() {
        if (cursor >= offsets.size()) {
            return true;
        }
        for (int i = cursor; i < offsets.size(); i++) {
            if (!rejected.contains(base.add(offsets.get(i)))) {
                return false;
            }
        }
        return true;
    }

    public int candidates() {
        return offsets.size();
    }
}
