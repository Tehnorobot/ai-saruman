package dev.magehome.v5.engine;

import com.google.gson.JsonObject;
import dev.magehome.client.RegistryService;
import dev.magehome.core.automation.PlanHold;
import dev.magehome.core.registry.HouseRegistry;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;

class EngineInvariantsTest {
    @TempDir Path root;

    @Test void holdAppliesAcrossPollingAndCancellationButAllowsRealChange() {
        var memory = new PlanHold();
        var same = new PlanHold.Key("window", "walk", "obstruction-a");
        var changed = new PlanHold.Key("window", "walk", "obstruction-cleared");
        assertTrue(memory.hold(same));
        assertFalse(memory.hold(changed));
        memory.outcome(same, false); // preemption is not a blocked route
        assertTrue(memory.permits(same, 1, 3));
        assertTrue(memory.hold(same));
        memory.outcome(same, true);
        assertFalse(memory.permits(same, 1, 3));
        assertTrue(memory.permits(changed, 1, 3));
    }

    @Test void delayedExpertResponseCannotInheritAnyNewToken() {
        var request = new JsonObject();
        request.addProperty("requestId", "request"); request.addProperty("runId", "run");
        request.addProperty("generation", 2); request.addProperty("schemaRevision", 3);
        request.addProperty("permitGeneration", 9); request.addProperty("worldSnapshotRevision", "observed-world");
        assertTrue(ExpertProcess.sameToken(request, request.deepCopy()));
        for (String key : request.keySet()) {
            var late = request.deepCopy(); late.remove(key);
            assertFalse(ExpertProcess.sameToken(request, late), key);
        }
        var stale = request.deepCopy(); stale.addProperty("worldSnapshotRevision", "support-removed");
        assertFalse(ExpertProcess.sameToken(request, stale));
        stale = request.deepCopy(); stale.addProperty("generation", 3);
        assertFalse(ExpertProcess.sameToken(request, stale));
    }

    @Test void rejoinReadsImmutableJournalAndCanWriteNewSnapshot() throws Exception {
        var first = new BuildJournal(root, "server|dimension|project|rev");
        assertNull(first.load().get(3, TimeUnit.SECONDS));
        var snapshot = new JsonObject(); snapshot.addProperty("progress", 4);
        first.save(snapshot);
        snapshot.addProperty("progress", 99);
        first.written.get(3, TimeUnit.SECONDS);
        var afterRejoin = new BuildJournal(root, "server|dimension|project|rev");
        assertEquals(4, afterRejoin.decode(afterRejoin.load().get(3, TimeUnit.SECONDS)).get("progress").getAsInt());
        var next = new JsonObject(); next.addProperty("progress", 5);
        afterRejoin.save(next);
        afterRejoin.written.get(3, TimeUnit.SECONDS);
        assertEquals(5, first.decode(first.load().get(3, TimeUnit.SECONDS)).get("progress").getAsInt());
        assertNull(new BuildJournal(root, "other-world|dimension|project|rev").load().get(3, TimeUnit.SECONDS));
    }

    @Test void legacyRegistryFlushDoesNotCloseWriterForNextWorld() {
        var registry = new RegistryService(root);
        registry.save(new HouseRegistry());
        registry.awaitPendingWrites();
        assertFalse(registry.writePending());
        registry.loadFromDisk();
        registry.save(new HouseRegistry());
        registry.awaitPendingWrites();
        assertFalse(registry.writePending());
        assertFalse(registry.writeFailed());
    }
}
