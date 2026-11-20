package dev.magehome.v5.api;

import com.google.gson.JsonObject;
import dev.magehome.v5.runtime.V5Runtime;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/**
 * The public program interface of the building module.
 *
 * <p>The contract is {@code mage_home.api/1}; its operations, their payloads and their error
 * codes are frozen in {@code spec/api-contract-v5.json}. Every answer is one JSON object.
 *
 * <p>The operations do not block: a request is accepted and the work happens on the client thread.
 * The reading operations return the last snapshot and never start a task.
 */
public final class MageHomePublicApi {

    private MageHomePublicApi() {
    }

    /**
     * Accepts a building request and returns the identities of the project and of the run.
     *
     * @param request the payload of the request; it carries the request identity, the description
     *                or the blueprint, the context and the options
     * @return one JSON object with the result or with the error
     */
    public static String start(JsonObject request) {
        return execute("start", request);
    }

    /**
     * Returns the last snapshot of a run.
     *
     * @param runId the identity of the run; the empty value means the current run
     * @return one JSON object with the result or with the error
     */
    public static String status(String runId) {
        JsonObject payload = new JsonObject();
        payload.addProperty("runId", runId == null ? "" : runId);
        return execute("status", payload);
    }

    /**
     * Stops the new game actions of a run and revokes the permission of the module.
     *
     * @param runId the identity of the run
     * @return one JSON object with the result or with the error
     */
    public static String pause(String runId) {
        JsonObject payload = new JsonObject();
        payload.addProperty("runId", runId == null ? "" : runId);
        return execute("pause", payload);
    }

    /**
     * Continues a run after the world and the control availability were checked again.
     *
     * @param runId the identity of the run
     * @return one JSON object with the result or with the error
     */
    public static String resume(String runId) {
        JsonObject payload = new JsonObject();
        payload.addProperty("runId", runId == null ? "" : runId);
        return execute("resume", payload);
    }

    /**
     * Makes a run terminal and revokes the new actions. The cleanup debt is kept.
     *
     * @param runId the identity of the run
     * @param reason the structured reason of the stop; may be null
     * @return one JSON object with the result or with the error
     */
    public static String stop(String runId, String reason) {
        JsonObject payload = new JsonObject();
        payload.addProperty("runId", runId == null ? "" : runId);
        if (reason != null) {
            payload.addProperty("reason", reason);
        }
        return execute("stop", payload);
    }

    /**
     * Describes what the module can do in the client right now.
     *
     * @return one JSON object with the result or with the error
     */
    public static String capabilities() {
        return execute("capabilities", new JsonObject());
    }

    /**
     * Executes an operation of the contract on the client thread.
     *
     * <p>The call is queued and answered in the next client tick, so that the state of a run stays
     * consistent and the release of the control is measured in processed client ticks.
     *
     * @param op      the name of the operation
     * @param payload the payload of the request
     * @return the answer as one JSON object
     */
    public static String execute(String op, JsonObject payload) {
        V5Runtime runtime = V5Runtime.get();
        String requestId = payload != null && payload.has("requestId")
                && payload.get("requestId").isJsonPrimitive()
                ? payload.get("requestId").getAsString() : null;
        JsonObject copy = payload == null ? new JsonObject() : payload.deepCopy();
        if (onClientThread()) {
            // A caller on the client thread would wait for a tick that cannot arrive while it
            // waits, so the operation is executed right away.
            return runtime.execute(op, requestId, copy);
        }
        var answer = new CompletableFuture<String>();
        runtime.submit(op, requestId, copy, answer::complete);
        try {
            return answer.get(5, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (java.util.concurrent.TimeoutException | java.util.concurrent.ExecutionException e) {
            // A timeout is not a cancellation. Retry a start with the same logical requestId.
        }
        JsonObject error = new JsonObject();
        error.addProperty("code", "control_timeout");
        error.addProperty("message", "The client thread did not answer; outcome unknown");
        error.addProperty("retryable", true);
        JsonObject envelope = new JsonObject();
        envelope.addProperty("api", V5Runtime.API);
        envelope.addProperty("op", op);
        envelope.addProperty("requestId", requestId);
        envelope.addProperty("ok", false);
        envelope.add("error", error);
        return envelope.toString();
    }

    /** True when the caller runs on the client thread. */
    private static boolean onClientThread() {
        try {
            net.minecraft.client.MinecraftClient client = net.minecraft.client.MinecraftClient.getInstance();
            return client != null && client.isOnThread();
        } catch (Throwable t) {
            return false;
        }
    }
}
