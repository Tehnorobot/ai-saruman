package dev.magehome.v5.engine;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.util.math.BlockPos;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class BuildJournalArchiveTest {
    @TempDir Path directory;

    @Test void allEventsSurviveEvictionFromTheCheckpoint() throws Exception {
        var journal = new BuildJournal(directory, "house-recovery");
        for (int i = 0; i < 1040; i++) {
            var event = new JsonObject();
            event.addProperty("id", i);
            event.addProperty("outcome", "confirmed");
            journal.add(event);
        }
        journal.save(new JsonObject());
        journal.written.get(10, TimeUnit.SECONDS);
        assertEquals(1024, journal.rows().size());
        var events = directory.resolve(BuildJournal.digest("house-recovery") + ".events");
        try (var files = Files.list(events)) {
            var archive = JsonParser.parseString(Files.readString(files.findFirst().orElseThrow())).getAsJsonArray();
            assertEquals(1040, archive.size());
            assertEquals(0, archive.get(0).getAsJsonObject().get("id").getAsInt());
        }
        var reopened = new BuildJournal(directory, "house-recovery");
        reopened.decode(reopened.load().get(10, TimeUnit.SECONDS));
        assertEquals(1024, reopened.rows().size());
        assertEquals(0, reopened.olderEvidence(BlockPos.ORIGIN, "different-world", 1, new com.google.gson.JsonArray()).size());
    }

    @Test void aFailedArchiveWriteKeepsEventsForTheNextCheckpoint() throws Exception {
        String binding = "failed-archive";
        var journal = new BuildJournal(directory, binding);
        var events = directory.resolve(BuildJournal.digest(binding) + ".events");
        journal.save(new JsonObject());
        journal.written.get(10, TimeUnit.SECONDS);
        Files.writeString(events, "temporarily not a directory");

        for (int id = 1; id <= 2; id++) {
            var event = new JsonObject();
            event.addProperty("id", id);
            journal.add(event);
            journal.save(new JsonObject());
            var failed = journal.written;
            assertThrows(ExecutionException.class, () -> failed.get(10, TimeUnit.SECONDS));
        }
        assertEquals(0, JsonParser.parseString(journal.load().get(10, TimeUnit.SECONDS))
                .getAsJsonObject().getAsJsonArray("records").size());

        Files.delete(events);
        journal.save(new JsonObject());
        journal.written.get(10, TimeUnit.SECONDS);
        assertEquals(2, JsonParser.parseString(journal.load().get(10, TimeUnit.SECONDS))
                .getAsJsonObject().getAsJsonArray("records").size());
        try (var files = Files.list(events)) {
            var paths = files.toList();
            assertEquals(2, paths.size());
            var ids = new java.util.TreeSet<Integer>();
            for (var file : paths) {
                var batch = JsonParser.parseString(Files.readString(file)).getAsJsonArray();
                assertEquals(1, batch.size());
                ids.add(batch.get(0).getAsJsonObject().get("id").getAsInt());
            }
            assertEquals(java.util.Set.of(1, 2), ids);
        }
    }
}
