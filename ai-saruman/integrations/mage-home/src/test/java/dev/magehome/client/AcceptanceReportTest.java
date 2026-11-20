package dev.magehome.client;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The JUnit report of a scenario is read by the independent runner.
 *
 * <p>The report must never show a failure for a case that was not observed: a stand that cannot
 * show the situation of a scenario leaves skipped cases behind, and a skipped case is not a
 * failure of the product. These checks execute the writer of the report and read the XML it
 * writes.
 */
class AcceptanceReportTest {

    @TempDir
    Path directory;

    @Test
    void aCompletedCheckpointRemainsReadableBeforeTheNextActionFinishes() throws Exception {
        write(List.of(new AcceptanceReport.Case("first-block", AcceptanceReport.State.PASSED, "world observed"),
                new AcceptanceReport.Case("later-block", AcceptanceReport.State.SKIPPED, "action in progress")));
        var document = javax.xml.parsers.DocumentBuilderFactory.newInstance().newDocumentBuilder()
                .parse(directory.resolve("scenario.xml").toFile());
        var cases = document.getElementsByTagName("testcase");
        assertTrue(cases.getLength() == 2);
        assertTrue(((org.w3c.dom.Element) cases.item(0)).getElementsByTagName("skipped").getLength() == 0);
        assertTrue(((org.w3c.dom.Element) cases.item(1)).getElementsByTagName("skipped").getLength() == 1);
        assertFalse(Files.exists(directory.resolve("scenario.xml.tmp")));
    }

    @Test
    void aSkippedCaseIsWrittenAsSkippedAndNeverAsAFailure() throws IOException {
        Path report = write(List.of(
                new AcceptanceReport.Case("full-house-from-empty-inventory.stand-ready",
                        AcceptanceReport.State.SKIPPED, "the setup check of the stand did not hold"),
                new AcceptanceReport.Case("full-house-from-empty-inventory.house-ready",
                        AcceptanceReport.State.SKIPPED, "the scenario was not verified"),
                new AcceptanceReport.Case("full-house-from-empty-inventory",
                        AcceptanceReport.State.SKIPPED, "the task chain of UnionClef did not exist")));

        String xml = Files.readString(report, StandardCharsets.UTF_8);
        assertTrue(xml.contains("<skipped"), "the skipped case is written as skipped");
        assertFalse(xml.contains("<failure"), "a case that was not observed is never a failure");
        assertTrue(xml.contains("skipped=\"3\""), "the report counts the skipped cases");
        assertTrue(xml.contains("failures=\"0\""), "the report counts no failure");
        // The reason of a case that was not observed is part of the report: the runner reads
        // why the behaviour was not verified without looking into the log of the game.
        assertTrue(xml.contains("message=\"the setup check of the stand did not hold\""),
                "the reason of the skipped case is written into the report");
        assertTrue(xml.contains("message=\"the task chain of UnionClef did not exist\""),
                "the reason of the skipped aggregate is written into the report");
    }

    /**
     * A scenario whose preparation of the laboratory is missing is not verified and never failed.
     *
     * <p>Раунд 6: сценарии, лабораторная подготовка которых не сделана (площадка, вода,
     * засеянная запись реестра), должны дать пропуск с причиной на предусловии и на агрегате
     * сценария и ни одного отказа. Ветка «непроверено, а не провалено» живёт в игре, но
     * исполняемое доказательство формы отчёта проверяется здесь.
     */
    @Test
    void aScenarioWithAMissingPreparationIsNotVerifiedAndNeverFailed() throws IOException {
        Path report = write(List.of(
                new AcceptanceReport.Case("site-blocked-by-water.platform-prepared",
                        AcceptanceReport.State.SKIPPED,
                        "the setup check of the stand did not hold: the laboratory prepared no platform"),
                new AcceptanceReport.Case("site-blocked-by-water.no-site",
                        AcceptanceReport.State.SKIPPED, "the scenario was not verified"),
                new AcceptanceReport.Case("site-blocked-by-water",
                        AcceptanceReport.State.SKIPPED,
                        "the scenario was not verified: site-blocked-by-water.no-site")));

        String xml = Files.readString(report, StandardCharsets.UTF_8);
        assertFalse(xml.contains("<failure"), "a scenario whose preparation is missing is never a failure");
        assertTrue(xml.contains("skipped=\"3\""), "every case of the scenario is skipped");
        assertTrue(xml.contains("failures=\"0\""), "the report counts no failure");
        assertTrue(xml.contains("the laboratory prepared no platform"),
                "the reason of the unmet setup check is written into the report");
    }

    /**
     * A product check that was not observed leaves the scenario unverified and never failed.
     *
     * <p>Раунд 6: кейс оракула владения цепочкой задач может остаться непроверенным, когда
     * цепочку целиком держал чужой владелец. Такой кейс не должен ни читаться как пройденный,
     * ни превращать агрегат сценария в отказ.
     */
    @Test
    void anUnverifiedProductCheckLeavesTheScenarioUnverified() throws IOException {
        Path report = write(List.of(
                new AcceptanceReport.Case("stop-resume-single-house.own-root",
                        AcceptanceReport.State.SKIPPED,
                        "the chain of UnionClef belonged to another owner during the whole window"),
                new AcceptanceReport.Case("stop-resume-single-house.foreign-task-untouched",
                        AcceptanceReport.State.PASSED,
                        "the add-on never called the chain while it held the task of another owner"),
                new AcceptanceReport.Case("stop-resume-single-house",
                        AcceptanceReport.State.SKIPPED,
                        "the scenario was not verified: stop-resume-single-house.own-root")));

        String xml = Files.readString(report, StandardCharsets.UTF_8);
        assertFalse(xml.contains("<failure"), "an unverified case never makes the scenario fail");
        assertTrue(xml.contains("failures=\"0\""), "the report counts no failure");
        assertTrue(xml.contains("skipped=\"2\""), "the unverified cases are counted as skipped");
        assertTrue(xml.contains("message=\"the chain of UnionClef belonged to another owner during the whole "
                        + "window\""), "the reason of the unverified case is written into the report");
    }

    @Test
    void anObservedCaseIsWrittenWithItsResult() throws IOException {
        Path report = write(List.of(
                new AcceptanceReport.Case("stop-resume-single-house.own-root",
                        AcceptanceReport.State.PASSED, "the chain holds the own root task"),
                new AcceptanceReport.Case("stop-resume-single-house.single-record",
                        AcceptanceReport.State.FAILED, "the house was not built"),
                new AcceptanceReport.Case("stop-resume-single-house",
                        AcceptanceReport.State.FAILED, "at least one named check failed")));

        String xml = Files.readString(report, StandardCharsets.UTF_8);
        assertTrue(xml.contains("name=\"stop-resume-single-house.own-root\""),
                "an observed case is written with its name");
        assertTrue(xml.contains("<failure message=\"the house was not built\""),
                "a case that was observed and does not hold is a failure");
        assertTrue(xml.contains("failures=\"2\""), "the report counts the failures");
        assertTrue(xml.contains("skipped=\"0\""), "the report counts no skipped case");
    }

    /** Writes the report of a scenario and returns the path of the file. */
    private Path write(List<AcceptanceReport.Case> cases) {
        AcceptanceReport.write(directory, "scenario", cases);
        return directory.resolve("scenario.xml");
    }
}
