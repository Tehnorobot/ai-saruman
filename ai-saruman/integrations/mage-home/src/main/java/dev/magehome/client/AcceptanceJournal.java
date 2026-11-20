package dev.magehome.client;

import com.google.gson.Gson;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.LinkedHashMap;
import java.util.Map;

/** Test harness evidence. Every event is appended and forced before the risky action starts. */
final class AcceptanceJournal {
    private final Path file;
    private final String scenarioId;
    private long sequence;

    AcceptanceJournal(Path file, String scenarioId) {
        this.file = file;
        this.scenarioId = scenarioId;
        if (file != null && Files.exists(file)) {
            try (var lines = Files.lines(file)) {
                // A persistence scenario runs in two JVMs and continues its own journal.
                sequence = lines.filter(line -> !line.isBlank()).count();
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
    }

    static AcceptanceJournal configured(String scenarioId) {
        String configured = System.getProperty("lab.acceptance.journal", "");
        if (configured.isBlank()) {
            configured = System.getenv().getOrDefault("LAB_GAMEPLAY_EXECUTION_JOURNAL", "");
        }
        return new AcceptanceJournal(configured.isBlank() ? null : Path.of(configured), scenarioId);
    }

    synchronized void event(String stage, String type, String checkpoint, String status, String detail) {
        if (file == null) return;
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("schema_version", 1);
        data.put("seq", ++sequence);
        data.put("scenario_id", scenarioId);
        data.put("stage", stage);
        data.put("type", type);
        String executionId = System.getenv("LAB_GAMEPLAY_EXECUTION_ID");
        if (executionId != null && !executionId.isBlank()) data.put("execution_id", executionId);
        if (checkpoint != null) data.put("checkpoint_id", checkpoint);
        if (status != null) data.put("status", status);
        if (detail != null) data.put("detail", detail.substring(0, Math.min(detail.length(), 4000)));
        byte[] bytes = (new Gson().toJson(data) + "\n").getBytes(StandardCharsets.UTF_8);
        try {
            if (file.toAbsolutePath().getParent() != null) Files.createDirectories(file.toAbsolutePath().getParent());
            try (FileChannel channel = FileChannel.open(file, StandardOpenOption.CREATE,
                    StandardOpenOption.WRITE, StandardOpenOption.APPEND)) {
                ByteBuffer buffer = ByteBuffer.wrap(bytes);
                while (buffer.hasRemaining()) channel.write(buffer);
                channel.force(true);
            }
        } catch (IOException e) {
            throw new UncheckedIOException("acceptance journal cannot be persisted", e);
        }
    }
}
