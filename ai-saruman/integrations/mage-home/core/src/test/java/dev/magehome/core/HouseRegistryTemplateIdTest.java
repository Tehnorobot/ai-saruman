package dev.magehome.core;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.registry.HouseRecord;
import dev.magehome.core.registry.HouseRegistry;
import dev.magehome.core.registry.HouseState;
import dev.magehome.core.registry.MalformedRegistryException;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class HouseRegistryTemplateIdTest {
    private HouseRegistry registry() {
        HouseRegistry registry = new HouseRegistry();
        registry.add(new HouseRecord("h1", "world", "minecraft:overworld", "player",
                new Vec3i(1, 2, 3), 1, HouseState.SUSPENDED, "placement", 7, null, null));
        return registry;
    }

    private JsonObject entry(String json) {
        return JsonParser.parseString(json).getAsJsonObject().getAsJsonArray("houses")
                .get(0).getAsJsonObject();
    }

    @Test
    void absentIdRemainsAbsentAcrossLoadCopyAndSave() throws Exception {
        String json = registry().toJson();
        assertFalse(entry(json).has("template_id"));
        HouseRegistry loaded = HouseRegistry.fromJson(json);
        assertNull(loaded.byId("h1").templateId());
        assertFalse(entry(loaded.copy().toJson()).has("template_id"));
        assertEquals(1, JsonParser.parseString(loaded.toJson()).getAsJsonObject().get("version").getAsInt());
    }

    @Test
    void unknownStringIsPreservedVerbatimThroughCopyAndRoundTrip() throws Exception {
        HouseRegistry original = registry();
        original.byId("h1").setTemplateId("future:unrecognized/家-v99");
        HouseRegistry loaded = HouseRegistry.fromJson(original.toJson());
        assertEquals("future:unrecognized/家-v99", loaded.byId("h1").templateId());
        assertEquals("future:unrecognized/家-v99", entry(loaded.copy().toJson()).get("template_id").getAsString());
    }

    @Test
    void explicitlyAssignedKnownIdSurvivesRoundTripAndCopy() throws Exception {
        HouseRegistry original = registry();
        original.byId("h1").setTemplateId("mage_home:closed_v1");
        HouseRecord copy = original.byId("h1").copy();
        original.byId("h1").setTemplateId("other");
        assertEquals("mage_home:closed_v1", copy.templateId());
        HouseRegistry loaded = HouseRegistry.fromJson(registryWith(copy).toJson());
        assertEquals("mage_home:closed_v1", loaded.byId("h1").templateId());
        assertEquals(1, loaded.byId("h1").variant());
    }

    @Test
    void explicitNullStaysNullAndIsNotConvertedToAnId() throws Exception {
        HouseRegistry original = registry();
        original.byId("h1").setTemplateId(null);
        HouseRegistry loaded = HouseRegistry.fromJson(original.toJson());
        assertNull(loaded.byId("h1").templateId());
        assertTrue(entry(loaded.copy().toJson()).get("template_id").isJsonNull());
    }

    @Test
    void malformedIdIsRejectedRatherThanSilentlyDiscarded() {
        for (String invalid : new String[] {"42", "true", "{}", "[]"}) {
            String json = registry().toJson().replace("\"variant\": 1", "\"template_id\": " + invalid + ", \"variant\": 1");
            assertThrows(MalformedRegistryException.class, () -> HouseRegistry.fromJson(json), invalid);
        }
    }

    private HouseRegistry registryWith(HouseRecord record) {
        HouseRegistry registry = new HouseRegistry();
        registry.add(record);
        return registry;
    }
}
