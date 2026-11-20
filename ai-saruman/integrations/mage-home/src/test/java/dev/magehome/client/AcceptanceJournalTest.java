package dev.magehome.client;

import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import static org.junit.jupiter.api.Assertions.*;

class AcceptanceJournalTest {
    @TempDir Path directory;

    /** The stages, the types and the statuses that the journal of a gameplay execution accepts. */
    private static final Set<String> STAGES = Set.of("setup", "action", "checkpoint", "scenario");
    private static final Set<String> TYPES = Set.of("started", "completed", "failed", "crashed");
    private static final Set<String> STATUSES = Set.of("PASS", "FAIL", "ERROR", "SKIPPED", "NOT_EXECUTED", "OBSERVED");

    @Test void aCompletedCheckpointSurvivesAnUnfinishedNextAction() throws Exception {
        Path path = directory.resolve("scenario.jsonl");
        AcceptanceJournal journal = new AcceptanceJournal(path, "real-house");
        journal.event("checkpoint", "completed", "first-block", "OBSERVED", "world confirmed one block");
        journal.event("action", "started", "second-block", null, "ordinary placement");
        // No close/finalizer/aggregate write: the client could die at this point.
        var lines = Files.readAllLines(path);
        assertEquals(2, lines.size());
        var completed = JsonParser.parseString(lines.getFirst()).getAsJsonObject();
        assertEquals("completed", completed.get("type").getAsString());
        assertEquals("first-block", completed.get("checkpoint_id").getAsString());
        assertEquals("started", JsonParser.parseString(lines.getLast()).getAsJsonObject().get("type").getAsString());
    }

    @Test void aSecondJvmWriterContinuesTheSeedJournal() throws Exception {
        Path path = directory.resolve("persistence.jsonl");
        new AcceptanceJournal(path, "persistence-after-exit")
                .event("setup", "completed", "seed", "OBSERVED", "saved by seed JVM");
        new AcceptanceJournal(path, "persistence-after-exit")
                .event("scenario", "started", "reload", null, "fresh client JVM");
        var lines = Files.readAllLines(path);
        assertEquals(1, JsonParser.parseString(lines.getFirst()).getAsJsonObject().get("seq").getAsLong());
        assertEquals(2, JsonParser.parseString(lines.getLast()).getAsJsonObject().get("seq").getAsLong());
    }

    /**
     * Every event of the harness uses a stage that the journal of the gameplay execution accepts.
     *
     * <p>The journal of the run rejects an event with an unknown stage, and the rejected file is
     * not readable: the observations of the run are lost together with the evidence of the
     * actions. The stages of the harness are read from its own source, so that a new event with a
     * stage that the run does not accept fails the test before the next run of the game.
     */
    @Test void everyEventOfTheHarnessUsesAStageThatTheExecutionJournalAccepts() throws Exception {
        Path source = Path.of("src/main/java/dev/magehome/client/AcceptanceRunner.java");
        assertTrue(Files.isRegularFile(source), "the source of the harness was found");
        Matcher matcher = Pattern.compile("journal\\.event\\(\"([^\"]+)\"").matcher(Files.readString(source));
        int found = 0;
        while (matcher.find()) {
            found++;
            String stage = matcher.group(1);
            assertTrue(STAGES.contains(stage), "the stage " + stage + " is not accepted by the journal of the run");
        }
        assertTrue(found > 0, "the harness writes events into the journal of the run");
    }

    /**
     * The written events are accepted by the journal of the gameplay execution.
     *
     * <p>The events of the facts of the attempt are written as a checkpoint: they are an
     * observation of the product action, and the journal of the run accepts the stages setup,
     * action, checkpoint and scenario only.
     */
    @Test void theWrittenEventsAreAcceptedByTheJournalOfTheRun() throws Exception {
        Path path = directory.resolve("events.jsonl");
        AcceptanceJournal journal = new AcceptanceJournal(path, "full-house-from-empty-inventory");
        journal.event("scenario", "started", null, null, "the scenario begins");
        journal.event("checkpoint", "completed", "full-house-from-empty-inventory.placement-facts", "OBSERVED",
                "cell=0,0,1 world=0,-60,8 access=-1,-60,8 player=-1,-60,8 interaction_sent=true arrived=true");
        journal.event("checkpoint", "failed", "full-house-from-empty-inventory.house-ready", "FAIL",
                "the house is not ready");
        journal.event("scenario", "completed", "full-house-from-empty-inventory", "SKIPPED", "named checks completed");
        List<String> lines = Files.readAllLines(path);
        assertEquals(4, lines.size());
        for (int index = 0; index < lines.size(); index++) {
            var event = JsonParser.parseString(lines.get(index)).getAsJsonObject();
            assertEquals(1, event.get("schema_version").getAsInt());
            assertEquals(index + 1, event.get("seq").getAsInt());
            assertEquals("full-house-from-empty-inventory", event.get("scenario_id").getAsString());
            assertTrue(STAGES.contains(event.get("stage").getAsString()),
                    "the stage " + event.get("stage").getAsString() + " is accepted by the journal of the run");
            assertTrue(TYPES.contains(event.get("type").getAsString()),
                    "the type " + event.get("type").getAsString() + " is accepted by the journal of the run");
            if (event.has("status")) {
                assertTrue(STATUSES.contains(event.get("status").getAsString()),
                        "the status " + event.get("status").getAsString() + " is accepted by the journal of the run");
            }
        }
    }
}
