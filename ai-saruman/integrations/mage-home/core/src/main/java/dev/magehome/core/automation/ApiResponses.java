package dev.magehome.core.automation;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import dev.magehome.core.model.Vec3i;
import dev.magehome.core.registry.HouseRecord;
import dev.magehome.core.registry.HouseRegistry;
import dev.magehome.core.registry.Visibility;
import dev.magehome.core.template.HouseTemplate;

/**
 * The JSON answers of the public API (ТЗ.md section 6). Reading the API never starts a task and
 * never waits for the client thread.
 */
public final class ApiResponses {

    public static final int API_VERSION = 1;

    private ApiResponses() {
    }

    public static String accepted() {
        JsonObject root = new JsonObject();
        root.addProperty("api_version", API_VERSION);
        root.addProperty("result", "accepted");
        return root.toString();
    }

    public static String rejected(String reason) {
        JsonObject root = new JsonObject();
        root.addProperty("api_version", API_VERSION);
        root.addProperty("result", "rejected");
        root.addProperty("reason", reason);
        return root.toString();
    }

    public static String status(boolean enabled, AutomationState state, String reason, String houseId, String stage,
                                int confirmedCells, int totalCells, Vec3i currentTarget, String lastVerifiedAt) {
        JsonObject root = new JsonObject();
        root.addProperty("api_version", API_VERSION);
        root.addProperty("enabled", enabled);
        root.addProperty("state", state.wireName());
        if (reason == null) {
            root.add("reason", com.google.gson.JsonNull.INSTANCE);
        } else {
            root.addProperty("reason", reason);
        }
        if (houseId == null) {
            root.add("house_id", com.google.gson.JsonNull.INSTANCE);
        } else {
            root.addProperty("house_id", houseId);
        }
        if (stage == null) {
            root.add("stage", com.google.gson.JsonNull.INSTANCE);
        } else {
            root.addProperty("stage", stage);
        }
        root.addProperty("confirmed_cells", confirmedCells);
        root.addProperty("total_cells", totalCells);
        if (currentTarget == null) {
            root.add("current_target", com.google.gson.JsonNull.INSTANCE);
        } else {
            JsonArray target = new JsonArray();
            target.add(currentTarget.x());
            target.add(currentTarget.y());
            target.add(currentTarget.z());
            root.add("current_target", target);
        }
        if (lastVerifiedAt == null) {
            root.add("last_verified_at", com.google.gson.JsonNull.INSTANCE);
        } else {
            root.addProperty("last_verified_at", lastVerifiedAt);
        }
        return root.toString();
    }

    /** The registry snapshot with the runtime visibility of every record. */
    public static String homes(HouseRegistry registry) {
        JsonObject root = new JsonObject();
        root.addProperty("api_version", API_VERSION);
        JsonArray array = new JsonArray();
        for (HouseRecord record : registry.houses()) {
            JsonObject entry = new JsonObject();
            entry.addProperty("house_id", record.houseId());
            entry.addProperty("world_key", record.worldKey());
            entry.addProperty("dimension", record.dimension());
            entry.addProperty("player_uuid", record.playerUuid());
            JsonObject origin = new JsonObject();
            origin.addProperty("x", record.origin().x());
            origin.addProperty("y", record.origin().y());
            origin.addProperty("z", record.origin().z());
            entry.add("origin", origin);
            entry.addProperty("variant", record.variant());
            entry.addProperty("state", record.state().wireName());
            entry.addProperty("stage", record.stage());
            entry.addProperty("confirmed_cells", record.confirmedCells());
            if (record.lastVerifiedAt() == null) {
                entry.add("last_verified_at", com.google.gson.JsonNull.INSTANCE);
            } else {
                entry.addProperty("last_verified_at", record.lastVerifiedAt());
            }
            if (record.reason() == null) {
                entry.add("reason", com.google.gson.JsonNull.INSTANCE);
            } else {
                entry.addProperty("reason", record.reason());
            }
            entry.addProperty("visibility", record.visibility().wireName());
            array.add(entry);
        }
        root.add("houses", array);
        return root.toString();
    }

    /** Total number of occupied cells of the project. */
    public static int totalCells() {
        return HouseTemplate.OCCUPIED_CELLS;
    }

    /** Visibility helper used by the client when a chunk is unloaded. */
    public static Visibility visibilityOf(boolean loaded) {
        return loaded ? Visibility.LOADED : Visibility.UNLOADED;
    }
}
