package dev.magehome.core.automation;

import java.util.HashMap;
import java.util.Map;

/** A commitment to a single bounded strategy. Time, cancellations and yaw don't change context. */
public final class PlanHold {
    public record Key(String goal, String method, String context) { }
    private final Map<Key, Integer> failures = new HashMap<>();
    private final Map<String, Integer> totals = new HashMap<>();
    private Key held;

    public boolean permits(Key key, int methodLimit, int goalLimit) {
        return failures.getOrDefault(key, 0) < methodLimit && totals.getOrDefault(key.goal(), 0) < goalLimit;
    }
    public boolean hold(Key key) {
        if (held != null && !held.equals(key)) return false;
        held = key;
        return true;
    }
    public void outcome(Key key, boolean strategyFailed) {
        if (strategyFailed) {
            failures.merge(key, 1, Integer::sum);
            totals.merge(key.goal(), 1, Integer::sum);
        }
        if (key.equals(held)) held = null;
    }
    public int failures(Key key) { return failures.getOrDefault(key, 0); }
}
