package dev.magehome.v5.engine;

import com.google.gson.*;
import dev.magehome.core.registry.AtomicFileStore;
import dev.magehome.core.registry.StorageException;
import net.minecraft.util.math.BlockPos;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import java.util.concurrent.*;

/** One writer per client, not per world connection; only immutable UTF-8 snapshots cross threads. */
final class BuildJournal {
    private static final Logger LOG = LoggerFactory.getLogger("mage_home/v5-journal");
    private static final ExecutorService IO = Executors.newSingleThreadExecutor(task -> {
        var thread = new Thread(task, "mage-home-v5-journal");
        thread.setDaemon(true);
        return thread;
    });
    private static final int MAX_RECORDS = 1024;
    private static final int MAX_BYTES = 12 * 1024 * 1024;
    private final AtomicFileStore store;
    private final Path events;
    private final String binding;
    private final Deque<JsonObject> records = new ArrayDeque<>();
    private final List<JsonObject> pendingEvents = new ArrayList<>();
    // Only the writer thread accesses this queue. A failed archive write keeps its original file name.
    private final Deque<ArchiveBatch> uncommitted = new ArrayDeque<>();
    CompletableFuture<Void> written = CompletableFuture.completedFuture(null);

    private record ArchiveBatch(Path file, String contents) {}

    BuildJournal(Path directory, String binding) {
        this.binding = binding;
        store = new AtomicFileStore(directory.resolve(digest(binding) + ".json"));
        events = directory.resolve(digest(binding) + ".events");
    }
    CompletableFuture<String> load() {
        return CompletableFuture.supplyAsync(() -> {
            try {
                // Not Files.exists(): an inaccessible file is not a missing file.
                if (Files.size(store.file()) > MAX_BYTES) throw new IllegalStateException("journal over size limit");
                return Files.readString(store.file(), StandardCharsets.UTF_8);
            } catch (NoSuchFileException missing) {
                return null;
            } catch (java.io.IOException e) {
                throw new UncheckedIOException(e);
            }
        }, IO);
    }
    JsonObject decode(String snapshot) {
        if (snapshot == null) return new JsonObject();
        var data = JsonParser.parseString(snapshot).getAsJsonObject();
        if (!"mage_home.attempts/1".equals(BuildPlan.text(data, "schema", ""))
                || !binding.equals(BuildPlan.text(data, "binding", ""))) throw new IllegalStateException("journal binding mismatch");
        for (var row : BuildPlan.array(data, "records")) remember(row.getAsJsonObject());
        return data;
    }
    void add(JsonObject row) {
        var copy = row.deepCopy();
        pendingEvents.add(copy);
        remember(copy);
    }

    private void remember(JsonObject row) {
        if (records.size() == MAX_RECORDS) {
            // Retain distinctive failures, preemptions and expert evidence longer than routine success.
            var it = records.iterator();
            boolean deleted = false;
            while (it.hasNext()) {
                var entry = it.next();
                if ("confirmed".equals(BuildPlan.text(entry, "outcome", ""))
                        && !BuildPlan.bool(entry, "expertProposalApplied", false)) {
                    it.remove();
                    deleted = true;
                    break;
                }
            }
            if (!deleted) records.removeFirst();
        }
        records.addLast(row);
    }
    List<JsonObject> rows() { return List.copyOf(records); }
    JsonArray relevant(BlockPos target, String worldUid, int revision) {
        var result = new JsonArray();
        var eligible = records.reversed().stream().filter(row -> row.has("goal")
                && worldUid.equals(BuildPlan.text(row, "worldUid", ""))
                && revision == BuildPlan.integer(row, "schemaRevision", -1))
                .filter(row -> BuildPlan.manhattan(target, BuildPlan.position(row.getAsJsonObject("goal"))) <= 8).toList();
        // Give each outcome class a chance instead of handing the expert only recent failed retries.
        for (String status : List.of("confirmed", "timeout", "failed", "interrupted", "cancelled", "conflict")) {
            eligible.stream().filter(row -> status.equals(BuildPlan.text(row, "outcome", "")))
                    .limit(3).forEach(row -> result.add(row.deepCopy()));
        }
        return result;
    }

    /** Called off the client thread. Find an older match for each outcome without flooding the model. */
    JsonArray olderEvidence(BlockPos target, String worldUid, int revision, JsonArray recent) {
        var excluded = new HashSet<JsonObject>();
        recent.forEach(row -> excluded.add(row.getAsJsonObject()));
        var selected = new LinkedHashMap<String, JsonObject>();
        try (var files = Files.list(events)) {
            for (var file : files.filter(p -> p.getFileName().toString().endsWith(".json")).sorted(Comparator.reverseOrder()).toList()) {
                if (selected.size() == 6) break;
                var batch = JsonParser.parseString(Files.readString(file, StandardCharsets.UTF_8)).getAsJsonArray();
                for (var item : batch.asList().reversed()) {
                    var row = item.getAsJsonObject();
                    var outcome = BuildPlan.text(row, "outcome", "");
                    if (Set.of("confirmed", "failed", "timeout", "interrupted", "cancelled", "conflict").contains(outcome)
                            && !excluded.contains(row) && row.has("goal") && row.has("worldUid")
                            && worldUid.equals(BuildPlan.text(row, "worldUid", ""))
                            && BuildPlan.integer(row, "schemaRevision", -1) <= revision
                            && BuildPlan.manhattan(target, BuildPlan.position(row.getAsJsonObject("goal"))) <= 8) {
                        selected.putIfAbsent(outcome, row);
                    }
                }
            }
        } catch (NoSuchFileException ignored) {
            // A new project may have no archived actions yet.
        } catch (java.io.IOException | RuntimeException error) {
            // The live checkpoint and recent evidence are still available.
        }
        var result = new JsonArray();
        selected.values().forEach(result::add);
        return result;
    }
    void save(JsonObject state) {
        var archive = new JsonArray();
        records.forEach(archive::add);
        state.add("records", archive);
        state.addProperty("schema", "mage_home.attempts/1");
        state.addProperty("binding", binding);
        String snapshot = state.toString();
        var eventsToCommit = new JsonArray();
        pendingEvents.forEach(eventsToCommit::add);
        pendingEvents.clear();
        var batch = eventsToCommit.isEmpty() ? null : new ArchiveBatch(
                events.resolve("%019d-%s.json".formatted(System.currentTimeMillis(), UUID.randomUUID())), eventsToCommit.toString());
        String runId = BuildPlan.text(state, "runId", "");
        try {
            written = written.handleAsync((ignored, previousError) -> {
                if (batch != null) uncommitted.addLast(batch);
                try {
                    while (!uncommitted.isEmpty()) {
                        var next = uncommitted.getFirst();
                        new AtomicFileStore(next.file()).writeWithSharingRetry(next.contents());
                        uncommitted.removeFirst();
                    }
                    store.writeWithSharingRetry(snapshot);
                } catch (StorageException e) {
                    LOG.error("Cannot persist Mage Home journal for run {}", runId, e);
                    throw new CompletionException(e);
                }
                return null;
            }, IO);
        } catch (RejectedExecutionException e) {
            LOG.error("Cannot schedule Mage Home journal write for run {}", runId, e);
            written = CompletableFuture.failedFuture(e);
        }
    }
    static String digest(String content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }
}
