package dev.magehome.core.registry;

import dev.magehome.core.verification.VerificationReport;

/**
 * Maintenance of the registry entries (ТЗ.md section 5).
 */
public final class HouseMaintenance {

    private HouseMaintenance() {
    }

    /**
     * A house that was building when the game stopped becomes suspended with the reason restart;
     * it is continued only by an explicit resume.
     */
    public static void restartSuspendedBuilding(HouseRecord record) {
        if (record.state() == HouseState.BUILDING) {
            record.setState(HouseState.SUSPENDED);
            record.setReason("restart");
        }
    }

    /**
     * Applies the result of a verification to a record.
     *
     * @param record   the house record
     * @param report   the verification of the loaded cells
     * @param loaded   whether the area of the house is loaded
     * @param nowIso   observation time in UTC ISO 8601
     */
    public static void applyVerification(HouseRecord record, VerificationReport report, boolean loaded, String nowIso) {
        record.setVisibility(loaded ? Visibility.LOADED : Visibility.UNLOADED);
        if (!loaded) {
            // An unloaded area keeps the time of the last verification and does not become damaged.
            return;
        }
        record.setLastVerifiedAt(nowIso);
        if (record.state() == HouseState.READY && !report.complete()) {
            record.setState(HouseState.DAMAGED);
            record.setReason("mismatch");
        }
    }
}
