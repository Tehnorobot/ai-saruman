package dev.magehome.client;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * The JUnit report of an acceptance scenario.
 *
 * <p>The independent runner reads fresh JUnit XML of the scenario and binds every named case of
 * the scenario manifest to the observation of the running game. The report is written from the
 * results of the scenario itself: a check that was observed and holds becomes a passed case, a
 * check that was observed and does not hold becomes a failure, and a check that could not be
 * executed at all becomes a skipped case. Nothing is reported as passed that was not observed.
 *
 * <p>The report is written into the directory of the system property
 * {@code lab.acceptance.results.dir}; without that property no report is written at all, so that
 * a run of the game outside of the acceptance run never leaves a report behind.
 */
final class AcceptanceReport {

    /** The state of one named case of the scenario. */
    enum State {
        PASSED,
        FAILED,
        SKIPPED
    }

    /** One named case of the scenario. */
    record Case(String name, State state, String detail) {
    }

    /** The class name of the cases of the report; it is the name of the scenario harness. */
    private static final String CLASSNAME = "dev.magehome.client.AcceptanceRunner";

    private AcceptanceReport() {
    }

    /**
     * Writes the report of the scenario.
     *
     * @param directory the directory of the report
     * @param scenarioId the id of the scenario; it is also the name of the report file
     * @param cases the named cases of the scenario
     */
    static void write(Path directory, String scenarioId, List<Case> cases) {
        if (directory == null || scenarioId == null || cases.isEmpty()) {
            return;
        }
        int failures = 0;
        int skipped = 0;
        for (Case item : cases) {
            failures += item.state() == State.FAILED ? 1 : 0;
            skipped += item.state() == State.SKIPPED ? 1 : 0;
        }
        StringBuilder xml = new StringBuilder();
        xml.append("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
        xml.append("<testsuite name=\"").append(escape(CLASSNAME)).append('"');
        xml.append(" tests=\"").append(cases.size()).append('"');
        xml.append(" failures=\"").append(failures).append('"');
        xml.append(" errors=\"0\"");
        xml.append(" skipped=\"").append(skipped).append('"');
        xml.append(" time=\"0.0\">\n");
        for (Case item : cases) {
            xml.append("  <testcase classname=\"").append(escape(CLASSNAME)).append('"');
            xml.append(" name=\"").append(escape(item.name())).append('"');
            xml.append(" time=\"0.0\"");
            switch (item.state()) {
                case PASSED -> xml.append("/>\n");
                case FAILED -> {
                    xml.append(">\n    <failure message=\"").append(escape(item.detail())).append("\">");
                    xml.append(escape(item.detail())).append("</failure>\n  </testcase>\n");
                }
                case SKIPPED -> {
                    // The reason of a case that was not observed is written into the report, so
                    // that the runner reads why the behaviour was not verified and does not have
                    // to look for it in the log of the game.
                    xml.append(">\n    <skipped message=\"").append(escape(item.detail())).append("\"/>\n");
                    xml.append("  </testcase>\n");
                }
            }
        }
        xml.append("</testsuite>\n");
        try {
            Files.createDirectories(directory);
            Path report = directory.resolve(scenarioId + ".xml");
            Path temporary = directory.resolve(scenarioId + ".xml.tmp");
            byte[] bytes = xml.toString().getBytes(StandardCharsets.UTF_8);
            try (var channel = java.nio.channels.FileChannel.open(temporary,
                    java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.WRITE,
                    java.nio.file.StandardOpenOption.TRUNCATE_EXISTING)) {
                java.nio.ByteBuffer buffer = java.nio.ByteBuffer.wrap(bytes);
                while (buffer.hasRemaining()) channel.write(buffer);
                channel.force(true);
            }
            try {
                Files.move(temporary, report, java.nio.file.StandardCopyOption.ATOMIC_MOVE,
                        java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            } catch (java.nio.file.AtomicMoveNotSupportedException unsupported) {
                Files.move(temporary, report, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (IOException e) {
            throw new UncheckedIOException("the JUnit report of the scenario cannot be written", e);
        }
    }

    /** Escapes the text of an attribute or of an element. */
    private static String escape(String text) {
        if (text == null) {
            return "";
        }
        return text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;")
                .replace("'", "&apos;");
    }
}
