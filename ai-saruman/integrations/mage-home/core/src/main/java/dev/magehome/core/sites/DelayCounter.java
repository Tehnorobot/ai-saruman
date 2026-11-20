package dev.magehome.core.sites;

/**
 * Counter of the uninterrupted client ticks outside every service radius. Entering a radius,
 * dying, disabling the automation and changing the world reset the counter (ТЗ.md section 3).
 */
public final class DelayCounter {

    private int ticks;

    /** Counts one tick when the work is active; a paused, menu or foreign controlled tick counts nothing. */
    public int tick(boolean active) {
        if (!active) {
            return ticks;
        }
        return ++ticks;
    }

    public void reset() {
        ticks = 0;
    }

    public int ticks() {
        return ticks;
    }

    public boolean elapsed(int threshold) {
        return ticks >= threshold;
    }
}
