package dev.magehome.core.verification;

import dev.magehome.core.template.CellSpec;

import java.util.Set;

/**
 * Compares an observed block with the project.
 *
 * <p>Dynamic properties (open door, open barrel, occupied bed, lit furnace) never change the
 * verdict; the block id and every required property do (ТЗ.md section 2 and 4).
 */
public final class CellVerifier {

    /** Properties that may change at runtime and are therefore ignored. */
    public static final Set<String> DYNAMIC_PROPERTIES = Set.of("open", "occupied", "lit");

    private CellVerifier() {
    }

    public static CellVerdict verify(CellSpec spec, ObservedBlock observed) {
        if (!observed.known()) {
            return CellVerdict.UNKNOWN;
        }
        if (spec == null) {
            // The cell has to stay empty.
            return observed.isAir() ? CellVerdict.MATCH : CellVerdict.WRONG_BLOCK;
        }
        if (observed.isAir()) {
            return CellVerdict.ABSENT;
        }
        if (!spec.blockId().equals(observed.blockId())) {
            return CellVerdict.WRONG_BLOCK;
        }
        for (String key : spec.requiredProperties().keySet()) {
            if (DYNAMIC_PROPERTIES.contains(key)) {
                continue;
            }
            String expected = spec.requiredProperties().get(key);
            String actual = observed.property(key);
            if (!expected.equals(actual)) {
                return CellVerdict.WRONG_PROPERTY;
            }
        }
        return CellVerdict.MATCH;
    }
}
