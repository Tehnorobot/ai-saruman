package dev.magehome.core;

import dev.magehome.core.checks.CoreChecks;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.TestFactory;

import java.util.List;
import java.util.stream.Collectors;

/**
 * The named checks of the domain core as JUnit cases. Every case has the id of the check as its
 * name, so the report of the test run and the report of the {@code coreCheck} task agree.
 */
class CoreChecksTest {

    @TestFactory
    List<DynamicTest> domainChecks() {
        return CoreChecks.all().stream()
                .map(check -> DynamicTest.dynamicTest(check.id() + " " + check.title(), () -> check.body().run()))
                .collect(Collectors.toList());
    }
}
