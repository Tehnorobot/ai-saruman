package dev.magehome.core.supply;

/**
 * Frequency limit of the supply step: at most one give command per second. The limit survives a
 * suspension and a new attempt (ТЗ.md section 4).
 */
public final class RateLimiter {

    private static final long INTERVAL_MILLIS = 1000L;

    private long lastSendMillis = -1L;

    /** Returns true when a command may be sent now. */
    public boolean tryAcquire(long nowMillis) {
        if (lastSendMillis >= 0 && nowMillis - lastSendMillis < INTERVAL_MILLIS) {
            return false;
        }
        lastSendMillis = nowMillis;
        return true;
    }

    public long lastSendMillis() {
        return lastSendMillis;
    }

    public void reset() {
        lastSendMillis = -1L;
    }
}
