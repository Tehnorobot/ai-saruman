package dev.magehome.core.registry;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonSyntaxException;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * The registry file {@code .minecraft/config/mage_home/houses.json}: {@code version = 1} and the
 * house records. A malformed file is never replaced by an empty registry (ТЗ.md section 5).
 */
public final class HouseRegistry {

    public static final int VERSION = 1;

    private final List<HouseRecord> houses = new ArrayList<>();

    public List<HouseRecord> houses() {
        return Collections.unmodifiableList(houses);
    }

    public void add(HouseRecord record) {
        houses.add(record);
    }

    public void remove(String houseId) {
        houses.removeIf(record -> record.houseId().equals(houseId));
    }

    public HouseRecord byId(String houseId) {
        for (HouseRecord record : houses) {
            if (record.houseId().equals(houseId)) {
                return record;
            }
        }
        return null;
    }

    /** All records of one world/dimension/player scope, including unfinished and damaged ones. */
    public List<HouseRecord> inScope(String worldKey, String dimension, String playerUuid) {
        List<HouseRecord> scoped = new ArrayList<>();
        for (HouseRecord record : houses) {
            if (record.worldKey().equals(worldKey) && record.dimension().equals(dimension)
                    && record.playerUuid().equals(playerUuid)) {
                scoped.add(record);
            }
        }
        return scoped;
    }

    public int countInScope(String worldKey, String dimension, String playerUuid) {
        return inScope(worldKey, dimension, playerUuid).size();
    }

    public HouseRegistry copy() {
        HouseRegistry copy = new HouseRegistry();
        for (HouseRecord record : houses) {
            copy.add(record.copy());
        }
        return copy;
    }

    public String toJson() {
        JsonObject root = new JsonObject();
        root.addProperty("version", VERSION);
        JsonArray array = new JsonArray();
        for (HouseRecord record : houses) {
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
            if (record.hasTemplateIdField()) {
                entry.addProperty("template_id", record.templateId());
            }
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
            array.add(entry);
        }
        root.add("houses", array);
        Gson gson = new GsonBuilder().setPrettyPrinting().serializeNulls().create();
        return gson.toJson(root);
    }

    public static HouseRegistry fromJson(String json) throws MalformedRegistryException {
        if (json == null || json.isBlank()) {
            throw new MalformedRegistryException("registry file is empty");
        }
        JsonElement parsed;
        try {
            parsed = JsonParser.parseString(json);
        } catch (JsonSyntaxException e) {
            throw new MalformedRegistryException("registry file is not valid JSON: " + e.getMessage(), e);
        }
        if (!parsed.isJsonObject()) {
            throw new MalformedRegistryException("registry root must be a JSON object");
        }
        JsonObject root = parsed.getAsJsonObject();
        if (!root.has("version") || !root.get("version").isJsonPrimitive()) {
            throw new MalformedRegistryException("registry file has no version");
        }
        int version = root.get("version").getAsInt();
        if (version != VERSION) {
            throw new MalformedRegistryException("unsupported registry version: " + version);
        }
        if (!root.has("houses") || !root.get("houses").isJsonArray()) {
            throw new MalformedRegistryException("registry file has no houses array");
        }
        HouseRegistry registry = new HouseRegistry();
        for (JsonElement element : root.getAsJsonArray("houses")) {
            if (!element.isJsonObject()) {
                throw new MalformedRegistryException("house entry must be a JSON object");
            }
            registry.add(readHouse(element.getAsJsonObject()));
        }
        return registry;
    }

    private static HouseRecord readHouse(JsonObject entry) throws MalformedRegistryException {
        try {
            String houseId = requiredString(entry, "house_id");
            String worldKey = requiredString(entry, "world_key");
            String dimension = requiredString(entry, "dimension");
            String playerUuid = requiredString(entry, "player_uuid");
            JsonObject originObject = entry.getAsJsonObject("origin");
            if (originObject == null) {
                throw new MalformedRegistryException("house entry has no origin");
            }
            dev.magehome.core.model.Vec3i origin = new dev.magehome.core.model.Vec3i(
                    originObject.get("x").getAsInt(),
                    originObject.get("y").getAsInt(),
                    originObject.get("z").getAsInt());
            int variant = entry.get("variant").getAsInt();
            HouseState state = HouseState.fromWire(requiredString(entry, "state"));
            String stage = requiredString(entry, "stage");
            int confirmedCells = entry.get("confirmed_cells").getAsInt();
            String lastVerifiedAt = optionalString(entry, "last_verified_at");
            String reason = optionalString(entry, "reason");
            HouseRecord record = new HouseRecord(houseId, worldKey, dimension, playerUuid, origin, variant, state, stage,
                    confirmedCells, lastVerifiedAt, reason);
            if (entry.has("template_id")) {
                JsonElement templateId = entry.get("template_id");
                if (!templateId.isJsonNull()
                        && (!templateId.isJsonPrimitive() || !templateId.getAsJsonPrimitive().isString())) {
                    throw new MalformedRegistryException("house entry template_id must be a string or null");
                }
                record.setTemplateId(templateId.isJsonNull() ? null : templateId.getAsString());
            }
            return record;
        } catch (MalformedRegistryException e) {
            throw e;
        } catch (RuntimeException e) {
            throw new MalformedRegistryException("house entry is invalid: " + e.getMessage(), e);
        }
    }

    private static String requiredString(JsonObject entry, String field) throws MalformedRegistryException {
        JsonElement value = entry.get(field);
        if (value == null || value.isJsonNull() || !value.isJsonPrimitive()) {
            throw new MalformedRegistryException("house entry has no " + field);
        }
        return value.getAsString();
    }

    private static String optionalString(JsonObject entry, String field) {
        JsonElement value = entry.get(field);
        if (value == null || value.isJsonNull() || !value.isJsonPrimitive()) {
            return null;
        }
        return value.getAsString();
    }
}
