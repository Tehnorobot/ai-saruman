package dev.magehome.api;

import dev.magehome.client.MageHomeRuntime;
import dev.magehome.core.automation.ApiResponses;

/**
 * The public API of the mod.
 *
 * <p>The mutations do not block: they are accepted and executed on the client thread. The reading
 * methods return the last snapshot of the current work and never start a task.
 */
public final class MageHomeApi {

    private MageHomeApi() {
    }

    /** Enables or disables the local automation. */
    public static String setEnabled(boolean enabled) {
        MageHomeRuntime runtime = MageHomeRuntime.get();
        if (runtime == null) {
            return ApiResponses.rejected("not_initialized");
        }
        return runtime.setEnabled(enabled);
    }

    /** Resumes the house with the given id. */
    public static String resume(String houseId) {
        MageHomeRuntime runtime = MageHomeRuntime.get();
        if (runtime == null) {
            return ApiResponses.rejected("unknown_house");
        }
        return runtime.resume(houseId);
    }

    /** Repeats the search for a site. */
    public static String retrySearch() {
        MageHomeRuntime runtime = MageHomeRuntime.get();
        if (runtime == null) {
            return ApiResponses.rejected("not_initialized");
        }
        return runtime.retrySearch();
    }

    /** The status of the automation and of the current work. */
    public static String getStatusJson() {
        MageHomeRuntime runtime = MageHomeRuntime.get();
        if (runtime == null) {
            return ApiResponses.status(false, dev.magehome.core.automation.AutomationState.DISABLED, null, null, null,
                    0, 180, null, null);
        }
        return runtime.statusJson();
    }

    /** The registry of the houses with the visibility of every record. */
    public static String getHomesJson() {
        MageHomeRuntime runtime = MageHomeRuntime.get();
        if (runtime == null) {
            return ApiResponses.homes(new dev.magehome.core.registry.HouseRegistry());
        }
        return runtime.homesJson();
    }
}
