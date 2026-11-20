package dev.magehome.core.checks;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * Executable inventory of the domain checks.
 *
 * <p>The task prints every check with its status and writes the report into {@code build/}. The
 * exit code is not zero when a check fails, so a broken check cannot be hidden.
 */
public final class CoreCheckHarness {

    public static void main(String[] args) {
        String reportDirectory = args.length > 0 ? args[0] : "build/reports/mage_home";
        String forcedFailureId = null;
        for (String argument : args) {
            if (argument.startsWith("--force-failure=")) {
                forcedFailureId = argument.substring("--force-failure=".length());
            }
        }
        Path directory = Path.of(reportDirectory);
        try {
            Files.createDirectories(directory);
        } catch (IOException e) {
            System.err.println("cannot create the report directory: " + e.getMessage());
            System.exit(2);
            return;
        }

        System.out.println("mage_home core checks");
        System.out.println("classpath: " + System.getProperty("lab.core.check.classpath", "<not set>"));

        List<CoreChecks.Result> results = CoreChecks.runAll(forcedFailureId);
        int failed = 0;
        JsonArray array = new JsonArray();
        for (CoreChecks.Result result : results) {
            String status = result.passed() ? "PASS" : "FAIL";
            if (!result.passed()) {
                failed++;
            }
            System.out.println(status + " " + result.id() + " " + result.title()
                    + (result.passed() ? "" : " -> " + result.message()));
            JsonObject entry = new JsonObject();
            entry.addProperty("id", result.id());
            entry.addProperty("title", result.title());
            entry.addProperty("status", status);
            entry.addProperty("message", result.message());
            array.add(entry);
        }
        System.out.println("checks: " + results.size() + ", failed: " + failed);

        JsonObject root = new JsonObject();
        root.addProperty("total", results.size());
        root.addProperty("failed", failed);
        root.add("checks", array);
        Gson gson = new GsonBuilder().setPrettyPrinting().create();
        try {
            Files.writeString(directory.resolve("core-checks.json"), gson.toJson(root), StandardCharsets.UTF_8);
            StringBuilder text = new StringBuilder();
            for (CoreChecks.Result result : results) {
                text.append(result.passed() ? "PASS " : "FAIL ").append(result.id()).append(' ')
                        .append(result.title());
                if (!result.passed()) {
                    text.append(" -> ").append(result.message());
                }
                text.append('\n');
            }
            Files.writeString(directory.resolve("core-checks.txt"), text.toString(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            System.err.println("cannot write the report: " + e.getMessage());
            System.exit(2);
            return;
        }

        if (failed > 0) {
            System.exit(1);
        }
    }
}
