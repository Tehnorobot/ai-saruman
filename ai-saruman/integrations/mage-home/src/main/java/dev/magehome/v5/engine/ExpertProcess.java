package dev.magehome.v5.engine;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import java.util.function.Supplier;

/** Isolated subprocess. No callback can perform a game action; the client tick consumes data. */
final class ExpertProcess {
    private static final int LIMIT = 256 * 1024;
    private static final ExecutorService IO = Executors.newVirtualThreadPerTaskExecutor();
    final CompletableFuture<JsonObject> answer;
    private volatile Process process;
    private volatile boolean cancelled;

    ExpertProcess(String profile, JsonObject request, int deadlineMillis) {
        this(profile, request, deadlineMillis, null);
    }

    ExpertProcess(String profile, JsonObject request, int deadlineMillis, Supplier<com.google.gson.JsonArray> olderEvidence) {
        var input = request.deepCopy();
        answer = CompletableFuture.supplyAsync(() -> {
            if (olderEvidence != null) input.getAsJsonObject("attemptLedger").getAsJsonArray("records").addAll(olderEvidence.get());
            if (cancelled) throw new CancellationException();
            return invoke(profile, input.toString(), deadlineMillis);
        }, IO);
    }
    private JsonObject invoke(String profile, String request, int deadlineMillis) {
        Process child = null;
        try {
            var builder = new ProcessBuilder(System.getProperty("mage_home.model.python", "python"),
                    "-m", "mage_home", "expert", "--profile", profile);
            // Never copy model stderr (potential secrets) to the game log.
            builder.redirectError(ProcessBuilder.Redirect.DISCARD);
            var modulePath = System.getProperty("mage_home.model.modulePath", "");
            if (!modulePath.isBlank()) builder.environment().merge("PYTHONPATH", modulePath,
                    (a, b) -> b + java.io.File.pathSeparator + a);
            builder.environment().put("PYTHONUTF8", "1");
            child = builder.start();
            process = child;
            if (cancelled) throw new CancellationException();
            final Process live = child;
            var response = CompletableFuture.supplyAsync(() -> {
                try (var in = live.getInputStream()) { return in.readNBytes(LIMIT + 1); }
                catch (IOException e) { throw new CompletionException(e); }
            }, IO);
            try (var stdin = child.getOutputStream()) { stdin.write(request.getBytes(StandardCharsets.UTF_8)); }
            if (deadlineMillis == 0) child.waitFor();
            else if (!child.waitFor(deadlineMillis, TimeUnit.MILLISECONDS)) throw new TimeoutException("expert_deadline");
            byte[] output = response.get(1, TimeUnit.SECONDS);
            if (output.length > LIMIT) throw new IllegalArgumentException("expert_response_too_large");
            return JsonParser.parseString(new String(output, StandardCharsets.UTF_8)).getAsJsonObject();
        } catch (IOException | InterruptedException | ExecutionException | TimeoutException e) {
            if (e instanceof InterruptedException) Thread.currentThread().interrupt();
            throw new CompletionException(e);
        } finally {
            if (child != null && child.isAlive()) child.destroyForcibly();
        }
    }
    void cancel() {
        cancelled = true;
        var child = process;
        if (child != null) IO.execute(child::destroyForcibly);
        answer.cancel(false);
    }

    static boolean sameToken(JsonObject request, JsonObject proposal) {
        for (var field : new String[] {"requestId", "runId", "generation", "schemaRevision", "permitGeneration", "worldSnapshotRevision"}) {
            if (!request.has(field) || !request.get(field).equals(proposal.get(field))) return false;
        }
        return true;
    }
}
