package dev.magehome.v5.runtime;

import com.google.gson.JsonObject;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/** Executes the standalone Python designer off the game thread. No shell or player/model credentials. */
final class DesignProcess {
    private static final int MAX_OUTPUT_BYTES = 2 * 1024 * 1024;
    // The profile permits up to 190 s of inference. Also allow process startup,
    // blueprint validation, and draining the response before killing the worker.
    private static final long TIMEOUT_MS = 200_000L;

    record Result(JsonObject blueprint, String error) {
        static Result failed(String code) { return new Result(null, code); }
    }

    private DesignProcess() { }

    static String profile(JsonObject options) {
        var configured = options.get("designProfile");
        if (configured != null && configured.isJsonPrimitive()
                && configured.getAsJsonPrimitive().isString() && !configured.getAsString().isBlank()) {
            return configured.getAsString();
        }
        return System.getProperty("mage_home.model.designProfile", "");
    }

    static Result run(JsonObject input, String selectedProfile) {
        Process process = null;
        try {
            String python = System.getProperty("mage_home.model.python", "python3");
            Path profile = Path.of(selectedProfile).toAbsolutePath().normalize();
            long deadline = unlimited(profile) ? 0 : System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(TIMEOUT_MS);
            ProcessBuilder builder = new ProcessBuilder(List.of(python, "-m", "mage_home", "design",
                    "--profile", profile.toString()));
            String module = System.getProperty("mage_home.model.modulePath", "");
            if (!module.isBlank()) builder.environment().put("PYTHONPATH", module);
            builder.environment().put("PYTHONIOENCODING", "utf-8");
            builder.redirectError(ProcessBuilder.Redirect.DISCARD);
            Process child = builder.start();
            process = child;
            byte[] bytes = (input + "\n").getBytes(StandardCharsets.UTF_8);
            var write = new CompletableFuture<Void>();
            var read = new CompletableFuture<byte[]>();
            Thread.ofVirtual().start(() -> {
                try (var toChild = child.getOutputStream()) {
                    toChild.write(bytes);
                } catch (Exception e) {
                    write.completeExceptionally(e);
                    return;
                }
                write.complete(null);
            });
            Thread.ofVirtual().start(() -> {
                try (var fromChild = child.getInputStream()) {
                    byte[] output = fromChild.readNBytes(MAX_OUTPUT_BYTES + 1);
                    if (output.length > MAX_OUTPUT_BYTES) child.destroyForcibly();
                    read.complete(output);
                } catch (Exception e) {
                    read.completeExceptionally(e);
                }
            });
            if (deadline == 0) child.waitFor();
            else if (!child.waitFor(remaining(deadline), TimeUnit.NANOSECONDS)) return Result.failed("design_timeout");
            byte[] output = await(read, deadline);
            if (output.length > MAX_OUTPUT_BYTES) return Result.failed("design_response_too_large");
            JsonObject answer = V5Runtime.parse(new String(output, StandardCharsets.UTF_8));
            if (answer != null && answer.has("ok") && answer.get("ok").isJsonPrimitive()
                    && answer.getAsJsonPrimitive("ok").isBoolean()) {
                if (answer.get("ok").getAsBoolean() && child.exitValue() == 0
                        && answer.has("blueprint") && answer.get("blueprint").isJsonObject()) {
                    await(write, deadline);
                    return new Result(answer.getAsJsonObject("blueprint"), null);
                }
                if (!answer.get("ok").getAsBoolean() && answer.has("error") && answer.get("error").isJsonObject()) {
                    var code = answer.getAsJsonObject("error").get("code");
                    if (code != null && code.isJsonPrimitive() && code.getAsString().matches("[a-zA-Z0-9_]{1,60}")) {
                        return Result.failed("design_" + code.getAsString());
                    }
                }
            }
            return Result.failed("design_invalid_response");
        } catch (TimeoutException e) {
            return Result.failed("design_timeout");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return Result.failed("design_cancelled");
        } catch (Exception e) {
            return Result.failed("design_subprocess_unavailable");
        } finally {
            if (process != null && process.isAlive()) process.destroyForcibly();
        }
    }

    static boolean unlimited(Path profile) {
        try (var file = Files.newInputStream(profile)) {
            byte[] bytes = file.readNBytes(MAX_OUTPUT_BYTES + 1);
            if (bytes.length > MAX_OUTPUT_BYTES) return false;
            JsonObject config = V5Runtime.parse(new String(bytes, StandardCharsets.UTF_8));
            if (config == null) return false;
            var provider = config.get("provider");
            var limit = config.get("deadlineMillis");
            return provider != null && provider.isJsonPrimitive() && provider.getAsJsonPrimitive().isString()
                    && provider.getAsString().equals("openai_compatible")
                    && limit != null && limit.isJsonPrimitive() && limit.getAsJsonPrimitive().isNumber()
                    && limit.getAsString().equals("0");
        } catch (IOException e) {
            return false; // Let the Python worker report an unreadable profile.
        }
    }

    private static <T> T await(CompletableFuture<T> future, long deadline)
            throws InterruptedException, ExecutionException, TimeoutException {
        return deadline == 0 ? future.get() : future.get(remaining(deadline), TimeUnit.NANOSECONDS);
    }

    private static long remaining(long deadline) throws TimeoutException {
        long value = deadline - System.nanoTime();
        if (value <= 0) throw new TimeoutException();
        return value;
    }
}
