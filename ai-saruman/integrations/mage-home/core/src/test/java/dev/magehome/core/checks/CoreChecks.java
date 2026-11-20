package dev.magehome.core.checks;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import dev.magehome.core.automation.ApiResponses;
import dev.magehome.core.automation.AutomationController;
import dev.magehome.core.automation.AutomationState;
import dev.magehome.core.automation.BehaviourBalance;
import dev.magehome.core.automation.ResumeOwnership;
import dev.magehome.core.automation.SiteSearch;
import dev.magehome.core.automation.TaskOwnership;
import dev.magehome.core.automation.TaskChainOwnership;
import dev.magehome.core.automation.TaskProtocol;
import dev.magehome.core.acceptance.PlatformFixture;
import dev.magehome.core.acceptance.StandRules;
import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.placement.ActionGate;
import dev.magehome.core.placement.AimMatch;
import dev.magehome.core.placement.AimPolicy;
import dev.magehome.core.placement.ArrivalPolicy;
import dev.magehome.core.placement.InteractionPolicy;
import dev.magehome.core.placement.ItemSelection;
import dev.magehome.core.placement.PlacementAttempt;
import dev.magehome.core.placement.PlacementDiagnostics;
import dev.magehome.core.placement.PlacementFacts;
import dev.magehome.core.placement.PlacementPlan;
import dev.magehome.core.placement.PlacementPlanner;
import dev.magehome.core.placement.PlacementPreconditions;
import dev.magehome.core.placement.PlacementTarget;
import dev.magehome.core.placement.PlannedPlacement;
import dev.magehome.core.placement.ReachPolicy;
import dev.magehome.core.placement.Support;
import dev.magehome.core.registry.AtomicFileStore;
import dev.magehome.core.registry.HouseMaintenance;
import dev.magehome.core.registry.HouseRecord;
import dev.magehome.core.registry.HouseRegistry;
import dev.magehome.core.registry.HouseState;
import dev.magehome.core.registry.MalformedRegistryException;
import dev.magehome.core.registry.RealmConfig;
import dev.magehome.core.registry.RegistryFileStore;
import dev.magehome.core.registry.VariantAssigner;
import dev.magehome.core.registry.Visibility;
import dev.magehome.core.registry.WorldKey;
import dev.magehome.core.repair.RepairDecision;
import dev.magehome.core.repair.RepairPolicy;
import dev.magehome.core.sites.DelayCounter;
import dev.magehome.core.sites.SiteEvaluator;
import dev.magehome.core.sites.SiteRules;
import dev.magehome.core.supply.GiveAvailability;
import dev.magehome.core.supply.GiveCommandPlanner;
import dev.magehome.core.supply.ItemNeed;
import dev.magehome.core.supply.RateLimiter;
import dev.magehome.core.supply.SupplyCalculator;
import dev.magehome.core.supply.SuspensionReason;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.template.HouseTemplate;
import dev.magehome.core.template.Palette;
import dev.magehome.core.testing.FakeContext;
import dev.magehome.core.testing.FakeWorld;
import dev.magehome.core.verification.CellVerdict;
import dev.magehome.core.verification.CellVerifier;
import dev.magehome.core.verification.ObservedBlock;
import dev.magehome.core.verification.VerificationReport;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/**
 * The single inventory of executable checks for the domain core.
 *
 * <p>Every check has an id of the plan (T-A2-xx ... T-A5-xx), a title and a body. The same
 * inventory is executed by the JUnit test and by the {@code coreCheck} task, so the report and the
 * test run can never disagree.
 */
public final class CoreChecks {

    /** One named check. */
    public record Check(String id, String title, Body body) {
    }

    /** Body of a check; a failed assertion is reported as a failure. */
    public interface Body {
        void run() throws Exception;
    }

    /** Result of one check. */
    public record Result(String id, String title, boolean passed, String message) {
    }

    private CoreChecks() {
    }

    public static List<Check> all() {
        List<Check> checks = new ArrayList<>();
        checks.add(new Check("T-A1-01", "The mod metadata is a client mod and depends on UnionClef 0.95.61",
                CoreChecks::modMetadata));
        checks.add(new Check("T-A1-03", "The sources use the Yarn names of Minecraft",
                CoreChecks::yarnNamesOnly));
        checks.add(new Check("T-A1-04", "The check task cannot hide a failed check",
                CoreChecks::harnessReportsFailures));
        checks.add(new Check("T-A1-05", "The check run does not change the files of the project",
                CoreChecks::projectUnchanged));
        checks.add(new Check("T-A1-06", "The delivered UnionClef jar has the hash of the specification",
                CoreChecks::unionclefFileHash));
        checks.add(new Check("T-A1-07", "The build uses the delivered file and not a network artifact",
                CoreChecks::unionclefFileDependency));
        checks.add(new Check("T-A1-08", "Product mutations use client protocol, never direct world writes or a lab fixture",
                CoreChecks::noDirectWorldWrites));
        checks.add(new Check("T-A1-09", "The versions of the toolchain are pinned in the project",
                CoreChecks::pinnedToolchain));
        checks.add(new Check("T-A1-10", "The build does not depend on the host of the Gradle distribution",
                CoreChecks::offlineGradleDistribution));
        checks.add(new Check("T-A2-01", "Geometry: front point, support level, bed cells, inclusive ranges, no rotation",
                CoreChecks::geometry));
        checks.add(new Check("T-A2-02", "The three palettes match the project table", CoreChecks::palettes));
        checks.add(new Check("T-A2-03", "The variant cycles 0,1,2,0 per world/dimension/player scope",
                CoreChecks::variants));
        checks.add(new Check("T-A2-04", "The floor covers 49 cells and the full roof 49", CoreChecks::floorAndRoof));
        checks.add(new Check("T-A2-05", "Walls are on the perimeter only, corners are posts with axis=y",
                CoreChecks::wallsAndPosts));
        checks.add(new Check("T-A2-06", "Five windows and one spruce door with facing=south and hinge=left",
                CoreChecks::glassAndDoor));
        checks.add(new Check("T-A2-07", "Furniture with the required properties", CoreChecks::furniture));
        checks.add(new Check("T-A2-08", "All other cells stay empty and no cell is used twice",
                CoreChecks::airCells));
        checks.add(new Check("T-A2-09", "180 occupied cells, 65 air cells, 245 cells in total",
                CoreChecks::cellCounts));
        checks.add(new Check("T-A2-10", "178 items are needed, a door and a bed count as one item",
                CoreChecks::itemCounts));
        checks.add(new Check("T-A2-11", "The own task and the tasks below it are not a foreign task",
                CoreChecks::taskOwnership));

        checks.add(new Check("T-A3-01", "The registry file has version 1 and exactly the project fields",
                CoreChecks::registryFields));
        checks.add(new Check("T-A3-02", "world_key rules and the realm_id of the config", CoreChecks::worldKeys));
        checks.add(new Check("T-A3-03", "The dimension is stored as a registry id", CoreChecks::dimension));
        checks.add(new Check("T-A3-04", "The registry is written atomically through a temporary file",
                CoreChecks::atomicWrite));
        checks.add(new Check("T-A3-05", "A write error stops the building with storage_error",
                CoreChecks::storageError));
        checks.add(new Check("T-A3-06", "A damaged registry file is not replaced by an empty registry",
                CoreChecks::damagedRegistry));
        checks.add(new Check("T-A3-07", "A building that was interrupted becomes suspended/restart",
                CoreChecks::restartState));
        checks.add(new Check("T-A3-08", "An unloaded area does not turn into damaged", CoreChecks::unloadedArea));
        checks.add(new Check("T-A3-09", "A ready house with a mismatch becomes damaged and is not repaired",
                CoreChecks::readyBecomesDamaged));
        checks.add(new Check("T-A3-10", "confirmed_cells counts the confirmed cells, not the sent clicks",
                CoreChecks::confirmedCells));
        checks.add(new Check("T-A3-11", "Dynamic properties do not change the verdict, required ones do",
                CoreChecks::dynamicProperties));
        checks.add(new Check("T-A3-12", "The automation is disabled after the start and disable keeps the registry",
                CoreChecks::defaultOff));
        checks.add(new Check("T-A3-13", "An external interruption suspends the house and waits for an explicit resume",
                CoreChecks::externalInterrupt));
        checks.add(new Check("T-A3-14", "The status command verifies the loaded houses in batches",
                CoreChecks::verificationOnStatus));

        checks.add(new Check("T-A4-01", "The service radius is 100 blocks, the height does not count",
                CoreChecks::radius));
        checks.add(new Check("T-A4-02", "Inside the radius of any record no new house is created",
                CoreChecks::noHouseInsideRadius));
        checks.add(new Check("T-A4-03", "With records the search starts after 1200 uninterrupted ticks",
                CoreChecks::searchDelay));
        checks.add(new Check("T-A4-04", "The delay counter is reset by radius, death, disable and world change",
                CoreChecks::delayReset));
        checks.add(new Check("T-A4-05", "The candidate centre is at most 32 blocks away",
                CoreChecks::centreLimit));
        checks.add(new Check("T-A4-06", "The floor level differs by at most 4 blocks, the base stays fixed",
                CoreChecks::heightLimit));
        checks.add(new Check("T-A4-07", "Every floor cell and the front point need a solid support",
                CoreChecks::support));
        checks.add(new Check("T-A4-08", "The volume has to be free, only four plants may be removed",
                CoreChecks::volume));
        checks.add(new Check("T-A4-09", "Unknown is not air, no chunk is loaded, the front point stays dry",
                CoreChecks::unknownAndFront));
        checks.add(new Check("T-A4-10", "The search runs in batches and skips unreachable candidates",
                CoreChecks::batchedSearch));
        checks.add(new Check("T-A4-11", "The place is checked again, no site means waiting/no_site",
                CoreChecks::noSiteWaiting));
        checks.add(new Check("T-A4-12", "The record is written before the first block and keeps house_id and variant",
                CoreChecks::recordBeforeBuild));
        checks.add(new Check("T-A4-13", "The allowed plants are removed and the volume is checked again",
                CoreChecks::plantRemoval));
        checks.add(new Check("T-A4-14", "The record of the site is written before the first block is placed",
                CoreChecks::siteRecordBeforeFirstBlock));
        checks.add(new Check("T-A4-15", "The house becomes ready only when the player stands at the front point",
                CoreChecks::readyAtFrontPoint));
        checks.add(new Check("T-A4-16", "At most two sends per position and no send after the confirmation",
                CoreChecks::interactionPerOperation));
        checks.add(new Check("T-A4-17", "While UnionClef is paused or a foreign task runs no background action starts",
                CoreChecks::pausedOrForeignTask));
        // The check T-A4-18 of the earlier revisions ("a wrong block and a single half of a door
        // block the house and are not broken") asserted the text of the old section 4 of the
        // specification. The current text of the section 4 allows the removal of a wrong block
        // inside the volume of a registered house, therefore the id was migrated deliberately to
        // the group T-A21: the old result says nothing about the new text.
        checks.add(new Check("T-A4-19", "The client sends the interaction of an attempt once and stops after the confirmation",
                CoreChecks::oneInteractionPerAttempt));

        checks.add(new Check("T-A5-01", "The deficit counts the unfinished cells minus the inventory",
                CoreChecks::deficit));
        checks.add(new Check("T-A5-02", "The command is give @s minecraft:<item> <deficit> with at most 64 items",
                CoreChecks::giveCommand));
        checks.add(new Check("T-A5-03", "At most one give command per second", CoreChecks::giveFrequency));
        checks.add(new Check("T-A5-04", "The automation never drops items, digs or crafts",
                CoreChecks::noOtherActions));
        checks.add(new Check("T-A5-05", "A full inventory and a missing give command suspend the automation",
                CoreChecks::supplySuspensions));
        checks.add(new Check("T-A5-06", "A sent command is not a confirmation", CoreChecks::noFakeConfirmation));
        checks.add(new Check("T-A5-07", "Without a confirmation after four seconds the automation suspends",
                CoreChecks::supplyTimeout));
        checks.add(new Check("T-A5-08", "A resume asks only for the rest and a failure does not forbid a new attempt",
                CoreChecks::resumeSupply));
        checks.add(new Check("T-A5-09", "The spending of the add-on does not hide the delivery of the server",
                CoreChecks::spendingDoesNotHideDelivery));

        checks.add(new Check("T-A6-01", "The floor comes first and every cell has a support when it is placed",
                CoreChecks::placementOrder));
        checks.add(new Check("T-A6-02", "The inner cells of the roof are placed on the side of the standing ring",
                CoreChecks::roofOrder));
        checks.add(new Check("T-A6-03", "Every cell of the house is planned exactly once and stays in the volume",
                CoreChecks::completePlan));
        checks.add(new Check("T-A6-04", "The planner continues a house that is already partly built",
                CoreChecks::resumePlan));
        checks.add(new Check("T-A6-05", "A door and a bed are one operation on two cells",
                CoreChecks::multiCellOperations));
        checks.add(new Check("T-A6-06", "The player can stand next to every placement",
                CoreChecks::accessCells));

        // The stand of the acceptance scenarios (ТЗ.md section 8). Every precondition of a
        // scenario is a setup check: the rule of the core is exercised and the harness of the
        // game is read, so that the checked rule is the one the harness really applies.
        checks.add(new Check("T-A7-01", "full-house-from-empty-inventory.stand-ready: the world, the player and UnionClef have to be ready",
                () -> standPrecondition("full-house-from-empty-inventory", "stand-ready",
                        "StandRules.readiness(", CoreChecks::readinessRule)));
        checks.add(new Check("T-A7-02", "full-house-from-empty-inventory.empty-inventory: the inventory of the player has to be empty",
                () -> standPrecondition("full-house-from-empty-inventory", "empty-inventory",
                        "StandRules.inventoryEmpty(", CoreChecks::emptyInventoryRule)));
        checks.add(new Check("T-A7-03", "give-refused.stand-ready: the world, the player and UnionClef have to be ready",
                () -> standPrecondition("give-refused", "stand-ready",
                        "StandRules.readiness(", CoreChecks::readinessRule)));
        checks.add(new Check("T-A7-04", "give-refused.give-not-available: the command tree of the server has to hold no give",
                () -> standPrecondition("give-refused", "give-not-available",
                        "StandRules.giveAvailable(", CoreChecks::giveRule)));
        checks.add(new Check("T-A7-05", "site-blocked-by-water.stand-ready: the world, the player and UnionClef have to be ready",
                () -> standPrecondition("site-blocked-by-water", "stand-ready",
                        "StandRules.readiness(", CoreChecks::readinessRule)));
        checks.add(new Check("T-A7-06", "stop-resume-single-house.stand-ready: the world, the player and UnionClef have to be ready",
                () -> standPrecondition("stop-resume-single-house", "stand-ready",
                        "StandRules.readiness(", CoreChecks::readinessRule)));
        checks.add(new Check("T-A7-07", "persistence-after-exit.stand-ready: the world, the player and UnionClef have to be ready",
                () -> standPrecondition("persistence-after-exit", "stand-ready",
                        "StandRules.readiness(", CoreChecks::readinessRule)));
        checks.add(new Check("T-A7-08", "persistence-after-exit.building-record: the registry has to hold a record in the state building",
                () -> standPrecondition("persistence-after-exit", "building-record",
                        "fixture.hasPersistenceSeed(", CoreChecks::buildingRecordRule)));
        checks.add(new Check("T-A7-09", "no-new-house-inside-radius.stand-ready: the world, the player and UnionClef have to be ready",
                () -> standPrecondition("no-new-house-inside-radius", "stand-ready",
                        "StandRules.readiness(", CoreChecks::readinessRule)));
        checks.add(new Check("T-A7-10", "no-new-house-inside-radius.seeded-record: the registry has to hold exactly one record with the variant 0",
                () -> standPrecondition("no-new-house-inside-radius", "seeded-record",
                        "StandRules.exactlyOneSeededRecord(", CoreChecks::seededRecordRule)));
        checks.add(new Check("T-A7-11", "next-palette-after-radius.stand-ready: the world, the player and UnionClef have to be ready",
                () -> standPrecondition("next-palette-after-radius", "stand-ready",
                        "StandRules.readiness(", CoreChecks::readinessRule)));
        checks.add(new Check("T-A7-12", "next-palette-after-radius.seeded-record: the registry has to hold exactly one record with the variant 0",
                () -> standPrecondition("next-palette-after-radius", "seeded-record",
                        "StandRules.exactlyOneSeededRecord(", CoreChecks::seededRecordRule)));

        // The life cycle of the work (ТЗ.md section 5).
        checks.add(new Check("T-A8-01", "The resume checks the owner of the task chain before it acts",
                CoreChecks::resumeOwnership));
        checks.add(new Check("T-A8-02", "A death, a disconnect or a change of the world suspends the house with a concrete reason",
                CoreChecks::interruptionReasons));
        checks.add(new Check("T-A8-03", "After a restart the blocks are verified again and the record does not replace the observation",
                CoreChecks::verifiedProgress));

        // The gate of the background actions (ТЗ.md sections 3 and 5).
        checks.add(new Check("T-A9-01", "While the gate is closed no interaction, no navigation and no give is sent",
                CoreChecks::actionGate));

        // The commands and the public API (ТЗ.md section 6).
        checks.add(new Check("T-A10-01", "The client commands enable, disable, status, list, retry and resume are available",
                CoreChecks::commandsOfTheClient));
        checks.add(new Check("T-A10-02", "The public API offers the five methods of ТЗ.md section 6",
                CoreChecks::publicApi));
        checks.add(new Check("T-A10-03", "The answers of the API have the shape of ТЗ.md section 6",
                CoreChecks::apiAnswers));
        checks.add(new Check("T-A10-04", "The mutations of the API do not block and the readings start nothing",
                CoreChecks::apiThreading));
        checks.add(new Check("T-A10-05", "The state of the automation is separate from the state of the house",
                CoreChecks::statusSeparatesTheHouse));
        checks.add(new Check("T-A10-06", "The resume answers with disabled, unknown_house and already_ready",
                CoreChecks::resumeAnswers));
        checks.add(new Check("T-A10-07", "Only one house is built at a time and a busy chain is not displaced",
                CoreChecks::oneBuildingAndBusy));
        checks.add(new Check("T-A10-08", "Unknown and missing template IDs are rejected without changing registry or world",
                CoreChecks::unknownTemplateRejected));

        // The remaining topics of the specification.
        checks.add(new Check("T-A11-01", "In 0.1.0 houses are built in the overworld only",
                CoreChecks::overworldOnly));
        checks.add(new Check("T-A11-02", "A missing progress saves a concrete reason and the time of a pause is no work",
                CoreChecks::concreteReasonAndPausedTime));
        checks.add(new Check("T-A11-03", "An explicit resume starts a new attempt on the same house",
                CoreChecks::resumeStartsANewAttempt));
        checks.add(new Check("T-A11-04", "The inventory of the checks covers the topics of the automatic tests",
                CoreChecks::testInventoryCoversTheTopics));

        // The facts of the delivered UnionClef (ТЗ.md section 7) and the topics that had no
        // named check yet.
        checks.add(new Check("T-A12-01", "The client uses the API of the delivered UnionClef and nothing else",
                CoreChecks::unionclefApi));
        checks.add(new Check("T-A12-02", "The special blocks are placed with the real vanilla placement API",
                CoreChecks::vanillaPlacementApi));
        checks.add(new Check("T-A12-03", "The navigation breaks no block, the add-on places no block outside the project and the behaviour is balanced",
                CoreChecks::navigationWithoutDamage));
        checks.add(new Check("T-A12-04", "A failed candidate is skipped and the search is shifted by eight blocks",
                CoreChecks::failedCandidateIsSkipped));
        checks.add(new Check("T-A12-05", "Already correct cells are skipped and a finished stage is not installed again",
                CoreChecks::finishedStageIsNotInstalledAgain));
        checks.add(new Check("T-A12-06", "Without an admissible operation the house is suspended with a coordinate",
                CoreChecks::noPlacementSupport));
        checks.add(new Check("T-A12-07", "The complete project can be planned and contains no permanent scaffold blocks",
                CoreChecks::noScaffolding));
        checks.add(new Check("T-A12-08", "A missing world makes the cases of the game skipped and never passed",
                CoreChecks::missingWorldIsSkipped));
        checks.add(new Check("T-A12-09", "The delivery holds the sources, the instruction and the artifact of the build",
                CoreChecks::deliveryOfTheProject));
        checks.add(new Check("T-A12-10", "While the chain of UnionClef does not exist the add-on performs no action",
                CoreChecks::missingChainKeepsTheGateClosed));

        // The status of the wait for the command tree and the ownership of the task chain.
        checks.add(new Check("T-A13-01", "The status shows that the automation waits for the command tree of the server",
                CoreChecks::unknownCommandTreeStatus));
        checks.add(new Check("T-A13-02", "The observation of the unread command tree names the tick",
                CoreChecks::unknownCommandTreeObservation));
        checks.add(new Check("T-A13-03", "A chain that another owner holds is no failure of the add-on",
                CoreChecks::foreignOwnerOfTheChain));
        checks.add(new Check("T-A13-04", "The add-on takes the chain only when it is empty",
                CoreChecks::emptyChainOfUnionClef));
        checks.add(new Check("T-A13-05", "The stand of a scenario knows the slot handler of UnionClef",
                CoreChecks::slotHandlerOfTheStand));

        // The oracle of the ownership of the task chain of UnionClef.
        checks.add(new Check("T-A14-01", "The change of the owner of the chain has to be explained by a call of the add-on",
                CoreChecks::changeOfTheOwnerNeedsACall));
        checks.add(new Check("T-A14-02", "The own root task of the add-on is the only root of its actions",
                CoreChecks::ownRootIsTheOnlyRoot));
        checks.add(new Check("T-A14-03", "The task of another owner is neither taken away nor stopped by the add-on",
                CoreChecks::foreignTaskIsNotStopped));
        checks.add(new Check("T-A14-04", "The add-on does not stop the chain of UnionClef while it holds the task of another owner",
                CoreChecks::stopOfAForeignChain));
        checks.add(new Check("T-A14-05", "The rule and the harness of the game judge the ownership of the chain by one criterion",
                CoreChecks::oneCriterionForTheOwnership));
        checks.add(new Check("T-A14-06", "The task of another owner that had already ended is not harmed by the add-on",
                CoreChecks::endedForeignTaskIsNotHarmed));

        // The preparation of the world of the stand.
        checks.add(new Check("T-A15-01", "The laboratory prepared the world of the stand",
                CoreChecks::preparationOfTheWorld));

        // The mixed sequences of the observation of the chain.
        checks.add(new Check("T-A16-01", "A mixed sequence of the states of the chain has one decided verdict",
                CoreChecks::mixedWindowsOfTheObservation));

        // The protocol of the root task and the two gaps of the oracle of the chain.
        checks.add(new Check("T-A17-01", "The root task of the add-on never returns itself from its tick",
                CoreChecks::rootTaskNeverReturnsItself));
        checks.add(new Check("T-A17-02", "The own root task and an empty chain at the end of the window",
                CoreChecks::ownRootAndEmptyChainAtTheEnd));
        checks.add(new Check("T-A17-03", "A foreign task that was never observed is not a proven norm",
                CoreChecks::foreignTaskWithoutObservation));
        checks.add(new Check("T-A17-04", "The preparation of the stand does not publish the events of the dependency",
                CoreChecks::standDoesNotPublishTheEventsOfTheDependency));

        // The observable attempt of a placement, the range of the interaction, the item in the
        // hand, the balance of the behaviour and the end of the scenario with a reason.
        checks.add(new Check("T-A18-01", "The interaction is sent only inside the ordinary range of the player",
                CoreChecks::interactionInsideTheOrdinaryRange));
        checks.add(new Check("T-A18-02", "The item of the project is in the main hand and the slot of the screen handler is read",
                CoreChecks::itemInTheMainHand));
        checks.add(new Check("T-A18-03", "Every attempt publishes its observable facts",
                CoreChecks::attemptPublishesItsFacts));
        checks.add(new Check("T-A18-04", "The behaviour of UnionClef is balanced on the start, the stop and the error",
                CoreChecks::behaviourIsBalanced));
        checks.add(new Check("T-A18-05", "The add-on opens no foreign container and drops no item",
                CoreChecks::noForeignContainerAndNoDrop));
        checks.add(new Check("T-A18-06", "The give command goes through the ordinary network API without a slash",
                CoreChecks::giveCommandThroughTheNetworkApi));
        checks.add(new Check("T-A18-07", "The own task of the add-on implements isFinished",
                CoreChecks::ownTaskIsFinished));
        checks.add(new Check("T-A18-08", "The scenario ends with the reason of the suspension instead of waiting",
                CoreChecks::scenarioEndsWithTheReason));
        checks.add(new Check("T-A19-01", "The arrival at the cell of the approach is exact and the client uses the rule of the core",
                CoreChecks::exactArrival));
        checks.add(new Check("T-A19-02", "The interaction is never sent from the cell that is to be filled",
                CoreChecks::noClickFromTheFilledCell));
        checks.add(new Check("T-A19-03", "A cell of the approach that is not reached is left and the house is suspended with a reason",
                CoreChecks::boundedAccessFallback));
        checks.add(new Check("T-A19-04", "The house becomes ready only when the player stands on the front point itself",
                CoreChecks::exitAtTheFrontPoint));
        checks.add(new Check("T-A19-05", "The navigation of the dependency ends in the cell itself",
                CoreChecks::navigationEndsInTheCell));
        checks.add(new Check("T-A19-06", "A cell of the approach that belongs to the operation itself is left for the next one",
                CoreChecks::forbiddenAccessCellIsLeft));
        checks.add(new Check("T-A20-01", "The click waits for the turn of the view and only when the view points at the support",
                CoreChecks::clickFollowsTheAim));
        checks.add(new Check("T-A20-02", "The click uses the hit result that the game itself computes under the crosshair",
                CoreChecks::hitResultOfTheGame));
        checks.add(new Check("T-A20-03", "The preconditions of the ordinary placement are asked before the click",
                CoreChecks::preconditionsOfThePlacement));
        checks.add(new Check("T-A20-04", "The facts of the attempt name the view, the slot, the images of the player and the precondition",
                CoreChecks::factsNameTheDiagnostics));
        checks.add(new Check("T-A20-05", "The stand probes the ordinary placement of the environment and removes the block of the probe",
                CoreChecks::standProbesTheEnvironment));

        // The safe repair of a wrong cell of the volume of a registered house (ТЗ.md section 4).
        checks.add(new Check("T-A21-01", "A wrong block inside the volume of the house is removed, the cell is read again and the building continues",
                CoreChecks::wrongBlockIsRepaired));
        checks.add(new Check("T-A21-02", "The protected cells are never removed: the support under the house, the block outside the volume and the container",
                CoreChecks::protectedCellsAreNotRepaired));
        checks.add(new Check("T-A21-03", "A repair that would touch the outer half of a multi-block item or that could spill a liquid is refused with the coordinate and the reason",
                CoreChecks::inapplicableRepairIsRefused));
        checks.add(new Check("T-A21-04", "The repair is asked for one cell at a time and only for the chosen cell",
                CoreChecks::repairIsAskedForOneCell));
        checks.add(new Check("T-A21-05", "The house waits for the removal of the wrong block and suspends with a concrete reason when the block stays",
                CoreChecks::repairWaitsForTheWorld));
        checks.add(new Check("T-A21-06", "A single half of a door is not overwritten: it is eliminated and the pair is placed again",
                CoreChecks::singleHalfIsNotOverwritten));
        checks.add(new Check("T-A21-07", "The client breaks a block only in the chosen repair operation and in the removal of its own temporary support",
                CoreChecks::clientBreaksOnlyInTheRepair));
        checks.add(new Check("T-A21-08", "The search of a site is not weakened by the repair: the volume of a new site has to be free",
                CoreChecks::siteSearchIsNotWeakened));
        checks.add(new Check("T-A21-09", "A safe foreign block in a registered house can be repaired without touching other cells",
                CoreChecks::foreignBlockIsRepaired));
        checks.add(new Check("T-A21-10", "The repair belongs to a house in the state building: a ready or a damaged house is not repaired on its own",
                CoreChecks::repairBelongsToABuildingHouse));
        checks.add(new Check("T-A21-11", "The repair breaks the wrong block with the ordinary destruction of the game and names its facts",
                CoreChecks::repairUsesTheOrdinaryDestruction));
        return checks;
    }

    public static List<Result> runAll() {
        return runAll(null);
    }

    /**
     * Runs the inventory of the checks and reports the check with the given id as failed.
     *
     * <p>The forced failure exists for the check T-A1-04: it proves that the check task prints the
     * failed check and leaves a non zero exit code, so a broken check cannot stay hidden. A normal
     * run never uses it.
     */
    public static List<Result> runAll(String forcedFailureId) {
        List<Result> results = new ArrayList<>();
        for (Check check : all()) {
            if (check.id().equals(forcedFailureId)) {
                results.add(new Result(check.id(), check.title(), false, "forced failure of the harness check"));
                continue;
            }
            try {
                check.body().run();
                results.add(new Result(check.id(), check.title(), true, "ok"));
            } catch (Throwable t) {
                results.add(new Result(check.id(), check.title(), false, t.getClass().getSimpleName() + ": "
                        + t.getMessage()));
            }
        }
        return results;
    }

    // --- project layout ---------------------------------------------------------------------------

    /**
     * The prefixes of the official and of the intermediary names of Minecraft. The sources of the
     * project use the names of Yarn, so an import with one of these prefixes is a foreign name.
     * {@code com.mojang.brigadier} is not listed: Brigadier is a separate library of Fabric and
     * not a name of Minecraft.
     */
    private static final List<String> FOREIGN_MINECRAFT_NAMES = List.of(
            "net.minecraft.class_",
            "net.minecraft.world.level.",
            "net.minecraft.world.item.",
            "net.minecraft.world.entity.",
            "net.minecraft.client.gui.screens.",
            "com.mojang.blaze3d.",
            "com.mojang.math.",
            "com.mojang.authlib.",
            "com.mojang.datafixerupper.");

    /** The directories that hold generated files or the state of the pipeline. */
    private static final List<String> IGNORED_DIRECTORIES = List.of(
            "build", ".gradle", ".git", ".idea", "libs", "run", ".lab-pipeline", ".lab-memory");

    /** The file types of the project that the snapshot of the sources covers. */
    private static final List<String> TEXT_SUFFIXES = List.of(
            ".java", ".gradle", ".properties", ".json", ".md", ".txt", ".toml", ".cfg");

    /** The state of the files of the project when the inventory was loaded. */
    private static final Map<String, String> PROJECT_SNAPSHOT = snapshotAtStart();

    private static void modMetadata() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        Path metadata = root.resolve("src/main/resources/fabric.mod.json");
        check(Files.isRegularFile(metadata), "the metadata of the mod exists at " + metadata);
        JsonObject mod = JsonParser.parseString(Files.readString(metadata, StandardCharsets.UTF_8))
                .getAsJsonObject();
        eq("mage_home", mod.get("id").getAsString(), "the id of the mod");
        eq(1, mod.get("schemaVersion").getAsInt(), "the schema version of the metadata");
        eq("client", mod.get("environment").getAsString(), "the mod runs on the client");
        JsonArray entrypoints = mod.getAsJsonObject("entrypoints").getAsJsonArray("client");
        eq(1, entrypoints.size(), "the number of client entrypoints");
        eq("dev.magehome.client.MageHomeClient", entrypoints.get(0).getAsString(), "the client entrypoint");
        JsonObject depends = mod.getAsJsonObject("depends");
        eq("1.21.11-0.95.61", depends.get("altoclef").getAsString(), "the dependency on UnionClef");
        eq("1.21.11", depends.get("minecraft").getAsString(), "the dependency on Minecraft");
    }

    private static void yarnNamesOnly() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        List<Path> sources = new ArrayList<>();
        for (Path directory : List.of(root.resolve("src"), root.resolve("core/src"))) {
            if (!Files.isDirectory(directory)) {
                continue;
            }
            try (var stream = Files.walk(directory)) {
                stream.filter(path -> path.toString().endsWith(".java")).forEach(sources::add);
            }
        }
        check(!sources.isEmpty(), "the project has Java sources");
        for (Path source : sources) {
            for (String line : Files.readAllLines(source, StandardCharsets.UTF_8)) {
                String trimmed = line.trim();
                if (!trimmed.startsWith("import ")) {
                    continue;
                }
                for (String prefix : FOREIGN_MINECRAFT_NAMES) {
                    check(!trimmed.contains(prefix),
                            "the file " + source + " uses the foreign name " + trimmed);
                }
            }
        }
    }

    private static void harnessReportsFailures() throws Exception {
        if (System.getProperty("lab.core.check.nested") != null) {
            // A nested run of the check task only proves the exit code of the task; it must not
            // start another process, otherwise the check would call itself for ever.
            return;
        }
        String classpath = System.getProperty("lab.core.check.classpath");
        if (classpath == null || classpath.isBlank()) {
            classpath = System.getProperty("java.class.path", "");
        }
        check(!classpath.isBlank(), "the classpath of the check task is known");
        Path report = Files.createTempDirectory("mage_home_harness");
        String java = Path.of(System.getProperty("java.home"), "bin", "java").toString();
        Process process = new ProcessBuilder(java, "-cp", classpath,
                "-Dlab.core.check.nested=true",
                "dev.magehome.core.checks.CoreCheckHarness", report.toString(),
                "--force-failure=T-A2-01")
                .redirectErrorStream(true)
                .start();
        String output;
        try (var stream = process.getInputStream()) {
            output = new String(stream.readAllBytes(), StandardCharsets.UTF_8);
        }
        check(process.waitFor(120, TimeUnit.SECONDS), "the check task finished");
        check(process.exitValue() != 0, "the check task leaves a non zero exit code when a check fails");
        check(output.contains("FAIL T-A2-01"), "the check task prints the failed check");
        JsonObject written = JsonParser.parseString(
                Files.readString(report.resolve("core-checks.json"), StandardCharsets.UTF_8)).getAsJsonObject();
        check(written.get("failed").getAsInt() >= 1, "the report of the check task counts the failed check");
        eq(all().size(), written.get("total").getAsInt(), "the report counts every check of the inventory");
    }

    private static void projectUnchanged() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        Map<String, String> now = projectSnapshot(root);
        check(PROJECT_SNAPSHOT.keySet().equals(now.keySet()),
                "the run added or removed files of the project outside of the build directories");
        for (Map.Entry<String, String> entry : PROJECT_SNAPSHOT.entrySet()) {
            eq(entry.getValue(), now.get(entry.getKey()), "the file " + entry.getKey() + " did not change");
        }
    }

    private static Map<String, String> snapshotAtStart() {
        try {
            Path root = projectRoot();
            return root == null ? Map.of() : projectSnapshot(root);
        } catch (Exception e) {
            return Map.of();
        }
    }

    private static Map<String, String> projectSnapshot(Path root) throws Exception {
        Map<String, String> snapshot = new LinkedHashMap<>();
        try (var stream = Files.walk(root)) {
            List<Path> files = stream
                    .filter(Files::isRegularFile)
                    .filter(path -> !insideIgnoredDirectory(root, path))
                    .filter(path -> TEXT_SUFFIXES.contains(suffixOf(path)))
                    .sorted()
                    .toList();
            for (Path file : files) {
                snapshot.put(root.relativize(file).toString(), hash(file));
            }
        }
        return snapshot;
    }

    private static boolean insideIgnoredDirectory(Path root, Path file) {
        for (Path part : root.relativize(file)) {
            if (IGNORED_DIRECTORIES.contains(part.toString())) {
                return true;
            }
        }
        return false;
    }

    private static String suffixOf(Path file) {
        String name = file.getFileName().toString();
        int dot = name.lastIndexOf('.');
        return dot < 0 ? "" : name.substring(dot);
    }

    private static String hash(Path file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        return HexFormat.of().formatHex(digest.digest(Files.readAllBytes(file)));
    }

    private static Path projectRoot() {
        String configured = System.getProperty("lab.project.dir");
        if (configured != null && !configured.isBlank()) {
            Path path = Path.of(configured).toAbsolutePath();
            return Files.isDirectory(path) ? path : null;
        }
        Path current = Path.of("").toAbsolutePath();
        while (current != null) {
            if (Files.isRegularFile(current.resolve("settings.gradle"))
                    && Files.isRegularFile(current.resolve("src/main/resources/fabric.mod.json"))) {
                return current;
            }
            current = current.getParent();
        }
        return null;
    }

    // --- template --------------------------------------------------------------------------------

    private static void geometry() {
        Vec3i origin = new Vec3i(100, 64, 200);
        eq(new Vec3i(103, 64, 199), SiteRules.frontPoint(origin), "front point");
        eq(63, SiteRules.supportLevel(origin), "support level below the floor");
        HouseTemplate template = HouseTemplate.forVariant(0);
        eq(new Vec3i(101, 65, 204), world(origin, new Cell(1, 1, 4)), "bed foot");
        eq(new Vec3i(101, 65, 203), world(origin, new Cell(1, 1, 3)), "bed head");
        eq(245, template.volumeCells().size(), "volume cells");
        for (Cell cell : template.volumeCells()) {
            check(cell.inVolume(), "cell out of the volume: " + cell);
        }
        check(template.volumeCells().contains(new Cell(6, 4, 6)), "the bounds are inclusive");
        for (java.lang.reflect.Field field : HouseTemplate.class.getDeclaredFields()) {
            check(!field.getName().toLowerCase().contains("rotat"), "the template has no rotation parameter");
        }
        for (java.lang.reflect.Field field : CellSpec.class.getDeclaredFields()) {
            check(!field.getName().toLowerCase().contains("rotat"), "a cell has no rotation parameter");
        }
    }

    private static void palettes() {
        Palette zero = Palette.of(0);
        eq("minecraft:stone_bricks", zero.floor(), "floor of variant 0");
        eq("minecraft:stripped_dark_oak_log", zero.post(), "post of variant 0");
        eq("minecraft:purple_terracotta", zero.wall(), "wall of variant 0");
        eq("minecraft:dark_oak_planks", zero.roof(), "roof of variant 0");
        eq("minecraft:purple_bed", zero.bed(), "bed of variant 0");
        Palette one = Palette.of(1);
        eq("minecraft:polished_deepslate", one.floor(), "floor of variant 1");
        eq("minecraft:stripped_spruce_log", one.post(), "post of variant 1");
        eq("minecraft:blue_terracotta", one.wall(), "wall of variant 1");
        eq("minecraft:spruce_planks", one.roof(), "roof of variant 1");
        eq("minecraft:blue_bed", one.bed(), "bed of variant 1");
        Palette two = Palette.of(2);
        eq("minecraft:mossy_stone_bricks", two.floor(), "floor of variant 2");
        eq("minecraft:stripped_oak_log", two.post(), "post of variant 2");
        eq("minecraft:cyan_terracotta", two.wall(), "wall of variant 2");
        eq("minecraft:oak_planks", two.roof(), "roof of variant 2");
        eq("minecraft:cyan_bed", two.bed(), "bed of variant 2");
        for (Palette palette : List.of(zero, one, two)) {
            for (String id : List.of(palette.floor(), palette.post(), palette.wall(), palette.roof(), palette.bed())) {
                check(id.startsWith("minecraft:"), "id without namespace: " + id);
            }
        }
    }

    private static void variants() {
        HouseRegistry registry = new HouseRegistry();
        eq(0, VariantAssigner.nextVariant(registry, "world", "minecraft:overworld", "uuid"), "first record");
        registry.add(record("h1", "world", "minecraft:overworld", "uuid"));
        eq(1, VariantAssigner.nextVariant(registry, "world", "minecraft:overworld", "uuid"), "second record");
        registry.add(record("h2", "world", "minecraft:overworld", "uuid"));
        eq(2, VariantAssigner.nextVariant(registry, "world", "minecraft:overworld", "uuid"), "third record");
        registry.add(record("h3", "world", "minecraft:overworld", "uuid"));
        eq(0, VariantAssigner.nextVariant(registry, "world", "minecraft:overworld", "uuid"), "fourth record");
        registry.add(record("h4", "other", "minecraft:overworld", "uuid"));
        eq(0, VariantAssigner.nextVariant(registry, "world", "minecraft:overworld", "uuid"),
                "a record of another world does not count");
    }

    private static void floorAndRoof() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        int floor = 0;
        int roof = 0;
        for (Cell cell : template.volumeCells()) {
            CellSpec spec = template.spec(cell);
            if (cell.y() == 0) {
                floor++;
                eq("minecraft:stone_bricks", spec.blockId(), "floor block");
            }
            if (cell.y() == 4) {
                roof++;
                eq("minecraft:dark_oak_planks", spec.blockId(), "roof block");
            }
        }
        eq(49, floor, "floor cells");
        eq(49, roof, "roof cells");
    }

    private static void wallsAndPosts() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        int posts = 0;
        int walls = 0;
        for (Cell cell : template.volumeCells()) {
            if (cell.y() < 1 || cell.y() > 3) {
                continue;
            }
            boolean perimeter = cell.x() == 0 || cell.x() == 6 || cell.z() == 0 || cell.z() == 6;
            if (!perimeter) {
                CellSpec interior = template.spec(cell);
                if (interior != null) {
                    check(!interior.blockId().equals("minecraft:purple_terracotta")
                                    && !interior.blockId().equals("minecraft:stripped_dark_oak_log")
                                    && !interior.blockId().equals("minecraft:glass")
                                    && !interior.blockId().equals("minecraft:spruce_door"),
                            "a wall inside the volume: " + cell);
                }
                continue;
            }
            CellSpec spec = template.spec(cell);
            boolean corner = (cell.x() == 0 || cell.x() == 6) && (cell.z() == 0 || cell.z() == 6);
            if (corner) {
                posts++;
                eq("minecraft:stripped_dark_oak_log", spec.blockId(), "post block");
                eq("y", spec.requiredProperties().get("axis"), "post axis");
            } else if (template.spec(cell).blockId().equals("minecraft:purple_terracotta")) {
                walls++;
            } else {
                check(template.spec(cell).blockId().equals("minecraft:glass")
                                || template.spec(cell).blockId().equals("minecraft:spruce_door"),
                        "unexpected block on the perimeter: " + cell + " " + template.spec(cell).blockId());
            }
        }
        eq(12, posts, "posts");
        eq(53, walls, "wall cells");
    }

    private static void glassAndDoor() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<Cell> windows = Set.of(new Cell(1, 2, 0), new Cell(5, 2, 0), new Cell(0, 2, 3),
                new Cell(6, 2, 3), new Cell(3, 2, 6));
        eq(windows, Set.copyOf(HouseTemplate.GLASS_CELLS), "the five specified window positions");
        eq(new Cell(3, 1, 0), HouseTemplate.DOOR_LOWER, "lower door position");
        eq(new Cell(3, 2, 0), HouseTemplate.DOOR_UPPER, "upper door position");
        for (Cell cell : windows) {
            CellSpec spec = template.spec(cell);
            check(spec != null, "no window at " + cell);
            eq("minecraft:glass", spec.blockId(), "window block");
        }
        CellSpec lower = template.spec(HouseTemplate.DOOR_LOWER);
        CellSpec upper = template.spec(HouseTemplate.DOOR_UPPER);
        eq("minecraft:spruce_door", lower.blockId(), "door block");
        eq("minecraft:spruce_door", upper.blockId(), "door block");
        eq("lower", lower.requiredProperties().get("half"), "lower half");
        eq("upper", upper.requiredProperties().get("half"), "upper half");
        eq("south", lower.requiredProperties().get("facing"), "door facing");
        eq("left", lower.requiredProperties().get("hinge"), "door hinge");
        eq("south", upper.requiredProperties().get("facing"), "door facing");
        eq("left", upper.requiredProperties().get("hinge"), "door hinge");
        int doorItems = 0;
        for (Map.Entry<String, Integer> entry : template.itemNeeds().entrySet()) {
            if (entry.getKey().equals("minecraft:spruce_door")) {
                doorItems = entry.getValue();
            }
        }
        eq(1, doorItems, "one door item");
    }

    private static void furniture() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        CellSpec foot = template.spec(new Cell(1, 1, 4));
        eq("minecraft:purple_bed", foot.blockId(), "bed foot block");
        eq("foot", foot.requiredProperties().get("part"), "bed foot part");
        eq("north", foot.requiredProperties().get("facing"), "bed facing");
        CellSpec head = template.spec(new Cell(1, 1, 3));
        eq("minecraft:purple_bed", head.blockId(), "bed head block");
        eq("head", head.requiredProperties().get("part"), "bed head part");
        eq("north", head.requiredProperties().get("facing"), "bed facing");
        for (Cell cell : List.of(new Cell(5, 1, 4), new Cell(5, 1, 5))) {
            CellSpec barrel = template.spec(cell);
            eq("minecraft:barrel", barrel.blockId(), "barrel block");
            eq("up", barrel.requiredProperties().get("facing"), "barrel facing");
        }
        eq("minecraft:crafting_table", template.spec(new Cell(1, 1, 1)).blockId(), "crafting table");
        CellSpec furnace = template.spec(new Cell(2, 1, 1));
        eq("minecraft:furnace", furnace.blockId(), "furnace block");
        eq("south", furnace.requiredProperties().get("facing"), "furnace facing");
        for (Cell cell : List.of(new Cell(5, 1, 1), new Cell(5, 2, 1))) {
            eq("minecraft:bookshelf", template.spec(cell).blockId(), "bookshelf");
        }
        for (Cell cell : List.of(new Cell(2, 1, 5), new Cell(4, 1, 5))) {
            CellSpec lantern = template.spec(cell);
            eq("minecraft:lantern", lantern.blockId(), "lantern block");
            eq("false", lantern.requiredProperties().get("hanging"), "lantern hanging");
            eq("false", lantern.requiredProperties().get("waterlogged"), "lantern waterlogged");
        }
    }

    private static void airCells() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<Cell> occupied = new HashSet<>();
        for (Cell cell : template.occupiedCells()) {
            check(occupied.add(cell), "cell used twice: " + cell);
        }
        eq(180, occupied.size(), "occupied cells");
        for (Cell cell : template.airCells()) {
            check(!occupied.contains(cell), "an occupied cell is counted as air: " + cell);
        }
        eq(65, template.airCells().size(), "air cells");
    }

    private static void cellCounts() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        eq(245, HouseTemplate.TOTAL_CELLS, "total cells");
        eq(180, template.occupiedCells().size(), "occupied cells");
        eq(65, template.airCells().size(), "air cells");
        eq(180, HouseTemplate.OCCUPIED_CELLS, "occupied cells constant");
        eq(65, HouseTemplate.AIR_CELLS, "air cells constant");
    }

    private static void itemCounts() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Map<String, Integer> expected = new LinkedHashMap<>();
        expected.put("minecraft:stone_bricks", 49);
        expected.put("minecraft:stripped_dark_oak_log", 12);
        expected.put("minecraft:purple_terracotta", 53);
        expected.put("minecraft:dark_oak_planks", 49);
        expected.put("minecraft:glass", 5);
        expected.put("minecraft:spruce_door", 1);
        expected.put("minecraft:purple_bed", 1);
        expected.put("minecraft:barrel", 2);
        expected.put("minecraft:crafting_table", 1);
        expected.put("minecraft:furnace", 1);
        expected.put("minecraft:bookshelf", 2);
        expected.put("minecraft:lantern", 2);
        eq(expected, new LinkedHashMap<>(template.itemNeeds()), "item needs");
        eq(178, template.itemCount(), "items for a complete house");
        eq(178, HouseTemplate.ITEM_COUNT, "items constant");
    }

    // --- registry --------------------------------------------------------------------------------

    private static void registryFields() throws Exception {
        HouseRegistry registry = new HouseRegistry();
        registry.add(record("h1", "server|localhost:25565|default", "minecraft:overworld", "uuid"));
        String json = registry.toJson();
        JsonObject root = JsonParser.parseString(json).getAsJsonObject();
        eq(1, root.get("version").getAsInt(), "registry version");
        JsonObject entry = root.getAsJsonArray("houses").get(0).getAsJsonObject();
        eq(Set.of("house_id", "world_key", "dimension", "player_uuid", "origin", "variant", "state", "stage",
                "confirmed_cells", "last_verified_at", "reason", "template_id"), entry.keySet(), "registry fields");
        eq(AutomationController.CURRENT_TEMPLATE_ID, entry.get("template_id").getAsString(), "template id");
        eq("h1", entry.get("house_id").getAsString(), "house id");
        eq(0, entry.get("variant").getAsInt(), "variant");
        eq("building", entry.get("state").getAsString(), "state");
        eq(0, entry.get("confirmed_cells").getAsInt(), "confirmed cells");
        check(entry.get("last_verified_at").isJsonNull(), "last_verified_at is null");
        check(entry.get("reason").isJsonNull(), "reason is null");

        HouseRegistry parsed = HouseRegistry.fromJson(json);
        eq(1, parsed.houses().size(), "parsed records");
        HouseRecord back = parsed.byId("h1");
        eq("server|localhost:25565|default", back.worldKey(), "world key");
        eq("minecraft:overworld", back.dimension(), "dimension");
        eq(new Vec3i(100, 64, 200), back.origin(), "origin");
        eq(HouseState.BUILDING, back.state(), "state");
    }

    private static void worldKeys() {
        for (String path : List.of("C:\\Users\\aimihail\\AppData\\Roaming\\.minecraft\\saves\\test",
                "d:/Games/Minecraft/saves/My World", "\\\\server\\share\\saves\\world",
                "/home/user/saves/world1", "C:\\Игры\\Миры\\Дом", "C:\\", "/")) {
            eq("singleplayer|" + path, WorldKey.forSingleplayer(path), "absolute save path kept verbatim");
        }
        for (String path : List.of("saves/world", "C:world", "\\world", "\\\\server", "\\\\server\\", "C:\\bad\0world")) {
            boolean rejected = false;
            try {
                WorldKey.forSingleplayer(path);
            } catch (IllegalArgumentException expected) {
                rejected = true;
            }
            check(rejected, "relative or malformed save path rejected: " + path);
        }
        eq("server|localhost:25565|default", WorldKey.forServer("localhost", 25565, "default"), "server key");
        eq("server|host:1234|realm_1", WorldKey.forServer("host", 1234, "realm_1"), "server key with realm");
        eq("singleplayer|/home/user/saves/world1", WorldKey.forSingleplayer("/home/user/saves/world1"),
                "single player key");
        check(WorldKey.isServerKey(WorldKey.forServer("localhost", 25565, "default")), "server key detected");
        check(WorldKey.isSingleplayerKey(WorldKey.forSingleplayer("/tmp/world")), "single player key detected");
        check(RealmConfig.isValidRealmId("default"), "default realm id");
        check(RealmConfig.isValidRealmId("a"), "one character");
        check(RealmConfig.isValidRealmId("A-b_9"), "allowed characters");
        check(!RealmConfig.isValidRealmId(""), "empty realm id");
        check(!RealmConfig.isValidRealmId("x".repeat(65)), "65 characters");
        check(!RealmConfig.isValidRealmId("with space"), "invalid character");
        check(!RealmConfig.isValidRealmId("with.dot"), "invalid character");
        eq("default", RealmConfig.defaultConfig().realmId(), "default realm id value");
    }

    private static void dimension() {
        HouseRegistry registry = new HouseRegistry();
        registry.add(record("h1", "world", "minecraft:the_nether", "uuid"));
        eq("minecraft:the_nether", registry.houses().get(0).dimension(), "dimension is kept as registry id");
    }

    private static void atomicWrite() throws Exception {
        Path directory = Files.createTempDirectory("mage-home-check");
        Path file = directory.resolve("houses.json");
        AtomicFileStore store = new AtomicFileStore(file);
        store.write("{\"version\":1,\"houses\":[]}");
        eq("{\"version\":1,\"houses\":[]}", store.read(), "written content");
        try (var stream = Files.list(directory)) {
            List<Path> files = stream.toList();
            eq(1, files.size(), "no temporary file is left behind: " + files);
        }
        store.write("{\"version\":1,\"houses\":[{\"house_id\":\"h1\"}]}");
        eq("{\"version\":1,\"houses\":[{\"house_id\":\"h1\"}]}", store.read(), "second write");
    }

    private static void storageError() {
        // The write of the registry fails: the automation stops with storage_error.
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        context.writeFailed = true;
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        eq(AutomationState.SUSPENDED, controller.state(), "state after a write error");
        eq("storage_error", controller.reason(), "reason after a write error");
        context.advance(50);
        controller.tick(context);
        eq(AutomationState.SUSPENDED, controller.state(), "the automation stays suspended");
        eq("storage_error", controller.reason(), "the reason stays storage_error");
        check(context.placementRequests.isEmpty(), "no block is placed without a saved record");

        // A client that reports the error of the write stops in the same way.
        FakeContext failing = new FakeContext();
        failing.world = FakeWorld.withSite(firstCandidate(failing.player));
        failing.failSave = true;
        AutomationController second = new AutomationController();
        second.enable();
        second.tick(failing);
        eq(AutomationState.SUSPENDED, second.state(), "state after an error of the client");
        eq("storage_error", second.reason(), "reason after an error of the client");
    }

    private static void damagedRegistry() throws Exception {
        Path directory = Files.createTempDirectory("mage-home-check");
        Path file = directory.resolve("houses.json");
        Files.writeString(file, "{ this is not json", StandardCharsets.UTF_8);
        RegistryFileStore store = new RegistryFileStore(file);
        boolean failed = false;
        try {
            store.load();
        } catch (MalformedRegistryException e) {
            failed = true;
        }
        check(failed, "a damaged registry file is reported");
        eq("{ this is not json", Files.readString(file, StandardCharsets.UTF_8),
                "the damaged file is not replaced by an empty registry");
    }

    private static void restartState() {
        HouseRecord record = record("h1", "world", "minecraft:overworld", "uuid");
        record.setState(HouseState.BUILDING);
        HouseMaintenance.restartSuspendedBuilding(record);
        eq(HouseState.SUSPENDED, record.state(), "state after the restart");
        eq("restart", record.reason(), "reason after the restart");
        eq(4, HouseState.values().length, "the number of house states");
        for (HouseState state : HouseState.values()) {
            check(!state.wireName().equals("restart"), "restart is not a house state");
        }
    }

    private static void unloadedArea() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        HouseRecord record = record("h1", "world", "minecraft:overworld", "uuid");
        record.setState(HouseState.READY);
        record.setLastVerifiedAt("2024-01-01T00:00:00Z");
        VerificationReport report = VerificationReport.of(template, new HashMap<>());
        HouseMaintenance.applyVerification(record, report, false, "2024-02-02T00:00:00Z");
        eq(HouseState.READY, record.state(), "an unloaded area stays ready");
        eq(Visibility.UNLOADED, record.visibility(), "visibility of an unloaded area");
        eq("2024-01-01T00:00:00Z", record.lastVerifiedAt(), "the time of the last verification is kept");
    }

    private static void readyBecomesDamaged() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        FakeWorld world = FakeWorld.withSite(new Vec3i(100, 64, 200));
        world.applyHouse(template, new Vec3i(100, 64, 200));
        HouseRecord record = record("h1", "world", "minecraft:overworld", "uuid");
        record.setState(HouseState.READY);
        Map<Cell, ObservedBlock> observed = new HashMap<>();
        for (Cell cell : template.volumeCells()) {
            observed.put(cell, world.read(new Vec3i(100, 64, 200).add(cell.x(), cell.y(), cell.z())));
        }
        VerificationReport report = VerificationReport.of(template, observed);
        HouseMaintenance.applyVerification(record, report, true, "2024-01-01T00:00:00Z");
        eq(HouseState.READY, record.state(), "a complete house stays ready");
        // A single wrong cell makes the ready house damaged.
        world.put(new Vec3i(100, 65, 200), ObservedBlock.of("minecraft:dirt", Map.of(), true, false, false));
        observed.clear();
        for (Cell cell : template.volumeCells()) {
            observed.put(cell, world.read(new Vec3i(100, 64, 200).add(cell.x(), cell.y(), cell.z())));
        }
        VerificationReport second = VerificationReport.of(template, observed);
        HouseMaintenance.applyVerification(record, second, true, "2024-01-02T00:00:00Z");
        eq(HouseState.DAMAGED, record.state(), "a ready house with a mismatch becomes damaged");
        // The damage is not repaired automatically.
        HouseMaintenance.applyVerification(record, second, true, "2024-01-03T00:00:00Z");
        eq(HouseState.DAMAGED, record.state(), "the damage is not repaired automatically");
    }

    private static void confirmedCells() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Vec3i origin = new Vec3i(100, 64, 200);
        FakeWorld world = FakeWorld.withSite(origin);
        // Only the floor is built.
        for (int x = 0; x < 7; x++) {
            for (int z = 0; z < 7; z++) {
                world.put(origin.add(x, 0, z),
                        ObservedBlock.of("minecraft:stone_bricks", Map.of(), true, false, false));
            }
        }
        Map<Cell, ObservedBlock> observed = new HashMap<>();
        for (Cell cell : template.volumeCells()) {
            observed.put(cell, world.read(origin.add(cell.x(), cell.y(), cell.z())));
        }
        VerificationReport report = VerificationReport.of(template, observed);
        eq(49, report.confirmedCells(), "confirmed cells of a house with a floor");
        check(!report.complete(), "the house is not complete");

        // A placement request alone does not change the number of confirmed cells.
        FakeContext context = new FakeContext();
        context.world = world;
        context.player = origin.add(3, 0, 3);
        // The floor stands, so the next operation is a corner post of the perimeter; the roof has
        // no support yet and is therefore not the next operation.
        context.inventory.put("minecraft:stripped_dark_oak_log", 12);
        context.inventory.put("minecraft:purple_terracotta", 53);
        context.inventory.put("minecraft:dark_oak_planks", 49);
        context.registry.add(record("h1", context.worldKey, context.dimension, context.playerUuid, origin));
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.resume("h1", context);
        context.advance(50);
        controller.tick(context);
        check(!context.placementRequests.isEmpty(), "a placement was requested");
        int confirmedBefore = context.registry.houses().get(0).confirmedCells();
        eq(49, confirmedBefore, "the confirmed cells are the cells of the world");
        controller.tick(context);
        eq(confirmedBefore, context.registry.houses().get(0).confirmedCells(),
                "the number of confirmed cells does not follow the clicks");
    }

    private static void dynamicProperties() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        CellSpec door = template.spec(HouseTemplate.DOOR_LOWER);
        Map<String, String> open = new HashMap<>(door.requiredProperties());
        open.put("open", "true");
        eq(CellVerdict.MATCH, CellVerifier.verify(door, ObservedBlock.of("minecraft:spruce_door", open)),
                "an open door still matches");
        Map<String, String> wrongFacing = new HashMap<>(door.requiredProperties());
        wrongFacing.put("facing", "north");
        eq(CellVerdict.WRONG_PROPERTY, CellVerifier.verify(door, ObservedBlock.of("minecraft:spruce_door", wrongFacing)),
                "a wrong facing is a mismatch");
        eq(CellVerdict.WRONG_BLOCK, CellVerifier.verify(door, ObservedBlock.of("minecraft:oak_door", Map.of())),
                "a wrong block is a mismatch");
        eq(CellVerdict.ABSENT, CellVerifier.verify(door, ObservedBlock.air()), "an empty cell is absent");
        eq(CellVerdict.UNKNOWN, CellVerifier.verify(door, ObservedBlock.unknown()), "an unknown cell is unknown");
        CellSpec barrel = template.spec(new Cell(5, 1, 4));
        Map<String, String> openBarrel = new HashMap<>(barrel.requiredProperties());
        openBarrel.put("open", "true");
        eq(CellVerdict.MATCH, CellVerifier.verify(barrel, ObservedBlock.of("minecraft:barrel", openBarrel)),
                "an open barrel still matches");
        CellSpec bed = template.spec(HouseTemplate.BED_FOOT);
        Map<String, String> occupied = new HashMap<>(bed.requiredProperties());
        occupied.put("occupied", "true");
        eq(CellVerdict.MATCH, CellVerifier.verify(bed, ObservedBlock.of("minecraft:purple_bed", occupied)),
                "an occupied bed still matches");
        CellSpec furnace = template.spec(new Cell(2, 1, 1));
        Map<String, String> lit = new HashMap<>(furnace.requiredProperties());
        lit.put("lit", "true");
        eq(CellVerdict.MATCH, CellVerifier.verify(furnace, ObservedBlock.of("minecraft:furnace", lit)),
                "a lit furnace still matches");
    }

    private static void defaultOff() throws Exception {
        AutomationController controller = new AutomationController();
        eq(AutomationState.DISABLED, controller.state(), "the automation starts disabled");
        FakeContext context = new FakeContext();
        controller.tick(context);
        eq(AutomationState.DISABLED, controller.state(), "a tick does not enable the automation");
        check(context.registry.houses().isEmpty(), "a disabled automation creates no record");

        controller.enable();
        eq(AutomationState.IDLE, controller.state(), "the state after enable");
        controller.disable();
        eq(AutomationState.DISABLED, controller.state(), "the state after disable");

        Path directory = Files.createTempDirectory("mage-home-check");
        RegistryFileStore store = new RegistryFileStore(directory.resolve("houses.json"));
        HouseRegistry registry = new HouseRegistry();
        registry.add(record("h1", "world", "minecraft:overworld", "uuid"));
        store.save(registry);
        eq(1, store.load().houses().size(), "the registry is still readable after disable");
    }

    // --- sites -----------------------------------------------------------------------------------

    private static void radius() {
        Vec3i origin = new Vec3i(0, 64, 0);
        Vec3i centre = SiteRules.centre(origin);
        eq(new Vec3i(3, 64, 3), centre, "the centre of the house");
        check(SiteRules.insideServiceRadius(new Vec3i(103, 64, 3), origin), "exactly 100 blocks is inside");
        check(!SiteRules.insideServiceRadius(new Vec3i(104, 64, 3), origin), "101 blocks is outside");
        check(!SiteRules.insideServiceRadius(new Vec3i(103, 64, 4), origin), "100.005 blocks is outside");
        check(SiteRules.insideServiceRadius(new Vec3i(103, 500, 3), origin), "the height does not count");
    }

    private static void noHouseInsideRadius() {
        FakeContext context = new FakeContext();
        Vec3i houseOrigin = new Vec3i(100, 64, 100);
        context.world = FakeWorld.withSite(new Vec3i(0, 64, 0));
        context.registry.add(record("h1", context.worldKey, context.dimension, context.playerUuid, houseOrigin));
        context.player = houseOrigin.add(3, 0, 3);
        AutomationController controller = new AutomationController();
        controller.enable();
        for (int i = 0; i < 10; i++) {
            controller.tick(context);
            context.advance(50);
        }
        eq(1, context.registry.houses().size(), "no second record inside the radius");
        eq(AutomationState.IDLE, controller.state(), "the automation waits inside the radius");
    }

    private static void searchDelay() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        context.registry.add(record("h1", context.worldKey, context.dimension, context.playerUuid,
                new Vec3i(1000, 64, 1000)));
        AutomationController controller = new AutomationController();
        controller.enable();
        for (int i = 0; i < 1199; i++) {
            controller.tick(context);
            context.advance(50);
        }
        eq(AutomationState.WAITING, controller.state(), "the search waits for 1200 ticks");
        eq(1, context.registry.houses().size(), "no new record during the delay");
        controller.tick(context);
        context.advance(50);
        check(controller.state() != AutomationState.WAITING || !"delay".equals(controller.reason()),
                "the search starts after 1200 ticks");
    }

    private static void delayReset() {
        DelayCounter counter = new DelayCounter();
        counter.tick(true);
        counter.tick(true);
        eq(2, counter.ticks(), "the counter counts active ticks");
        counter.tick(false);
        eq(2, counter.ticks(), "a tick without work is not counted");
        counter.reset();
        eq(0, counter.ticks(), "the counter is reset");

        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        context.registry.add(record("h1", context.worldKey, context.dimension, context.playerUuid,
                new Vec3i(1000, 64, 1000)));
        AutomationController controller = new AutomationController();
        controller.enable();
        for (int i = 0; i < 100; i++) {
            controller.tick(context);
            context.advance(50);
        }
        context.alive = false;
        controller.tick(context);
        context.alive = true;
        context.player = new Vec3i(1000, 64, 1003);
        controller.tick(context);
        eq(AutomationState.IDLE, controller.state(), "the counter is reset when a radius is entered");
    }

    private static void centreLimit() {
        Vec3i base = new Vec3i(0, 64, 0);
        SiteSearch search = new SiteSearch(base);
        List<Vec3i> batch = search.nextBatch();
        check(!batch.isEmpty(), "the search produces candidates");
        for (Vec3i candidate : batch) {
            check(SiteRules.centreWithinSearchLimit(candidate, base), "the candidate centre is too far: " + candidate);
        }
        check(!SiteRules.centreWithinSearchLimit(base.add(100, 0, 0), base), "a far centre is rejected");
    }

    private static void heightLimit() {
        check(SiteRules.heightWithinLimit(64, 60), "four blocks are allowed");
        check(SiteRules.heightWithinLimit(64, 68), "four blocks are allowed");
        check(!SiteRules.heightWithinLimit(64, 69), "five blocks are rejected");
        check(!SiteRules.heightWithinLimit(64, 59), "five blocks are rejected");
        SiteSearch search = new SiteSearch(new Vec3i(0, 64, 0));
        Vec3i base = search.base();
        for (Vec3i candidate : search.nextBatch()) {
            eq(base.y(), candidate.y(), "the base height stays fixed during the search");
        }
    }

    private static void support() {
        Vec3i origin = new Vec3i(0, 64, 0);
        FakeWorld world = FakeWorld.withSite(origin);
        check(SiteEvaluator.evaluate(origin, world).suitable(), "a site with a solid ground");
        FakeWorld missing = FakeWorld.withSite(origin);
        missing.put(origin.add(2, -1, 2), ObservedBlock.air());
        SiteEvaluator.Result result = SiteEvaluator.evaluate(origin, missing);
        check(!result.suitable(), "a missing support is rejected");
        eq(SiteEvaluator.Problem.SUPPORT_MISSING, result.problem(), "the reason of the rejection");
        FakeWorld wet = FakeWorld.withSite(origin);
        wet.liquid(origin.add(3, -1, 3), "minecraft:water");
        check(!SiteEvaluator.evaluate(origin, wet).suitable(), "a liquid support is rejected");
        FakeWorld noFront = FakeWorld.withSite(origin);
        noFront.put(origin.add(3, -1, -1), ObservedBlock.air());
        check(!SiteEvaluator.evaluate(origin, noFront).suitable(), "the front point needs a support");
    }

    private static void volume() {
        Vec3i origin = new Vec3i(0, 64, 0);
        FakeWorld world = FakeWorld.withSite(origin);
        world.plant(origin.add(2, 0, 2), "minecraft:short_grass");
        world.plant(origin.add(2, 0, 3), "minecraft:tall_grass");
        world.plant(origin.add(2, 0, 4), "minecraft:fern");
        world.plant(origin.add(2, 0, 5), "minecraft:large_fern");
        SiteEvaluator.Result result = SiteEvaluator.evaluate(origin, world);
        check(result.suitable(), "the four plants are allowed");
        eq(4, result.removablePlants().size(), "the removable plants are reported");
        FakeWorld tree = FakeWorld.withSite(origin);
        tree.put(origin.add(2, 1, 2), ObservedBlock.of("minecraft:oak_log", Map.of("axis", "y"), true, false, false));
        check(!SiteEvaluator.evaluate(origin, tree).suitable(), "a tree is not removed");
        FakeWorld crop = FakeWorld.withSite(origin);
        crop.put(origin.add(2, 0, 2), ObservedBlock.of("minecraft:wheat", Map.of(), false, false, false));
        check(!SiteEvaluator.evaluate(origin, crop).suitable(), "a crop is not removed");
        FakeWorld roofBlocked = FakeWorld.withSite(origin);
        roofBlocked.solid(origin.add(3, 4, 3), "minecraft:stone");
        check(!SiteEvaluator.evaluate(origin, roofBlocked).suitable(), "the fifth layer must be clear");
    }

    private static void unknownAndFront() {
        Vec3i origin = new Vec3i(0, 64, 0);
        FakeWorld world = FakeWorld.withSite(origin);
        SiteEvaluator.Result result = SiteEvaluator.evaluate(origin, world);
        check(result.suitable(), "the site is usable");
        eq(0, world.chunkLoadRequests(), "no chunk is loaded");
        FakeWorld hidden = FakeWorld.withSite(origin);
        hidden.put(origin.add(2, 2, 2), ObservedBlock.unknown());
        SiteEvaluator.Result unknown = SiteEvaluator.evaluate(origin, hidden);
        check(!unknown.suitable(), "an unknown cell is not air");
        eq(SiteEvaluator.Problem.CELL_NOT_LOADED, unknown.problem(), "the reason of the rejection");
        eq(0, hidden.chunkLoadRequests(), "observing an unknown cell does not request chunk loading");
        hidden.put(origin.add(2, 2, 2), ObservedBlock.air());
        check(SiteEvaluator.evaluate(origin, hidden).suitable(),
                "the same site becomes usable when its previously unknown cell is observed as air");
        eq(0, hidden.chunkLoadRequests(), "receiving world data does not require a forced chunk load");
        FakeWorld blocked = FakeWorld.withSite(origin);
        blocked.put(origin.add(3, 1, -1), ObservedBlock.of("minecraft:stone", Map.of(), true, false, false));
        check(!SiteEvaluator.evaluate(origin, blocked).suitable(), "the front point needs free space");
        FakeWorld wet = FakeWorld.withSite(origin);
        wet.liquid(origin.add(3, 0, -1), "minecraft:water");
        check(!SiteEvaluator.evaluate(origin, wet).suitable(), "the front point has to stay dry");
    }

    private static void batchedSearch() {
        SiteSearch search = new SiteSearch(new Vec3i(0, 64, 0));
        List<Vec3i> batch = search.nextBatch();
        check(batch.size() <= SiteSearch.BATCH_SIZE, "a batch is small enough for one tick");
        check(!search.exhausted(), "the search has more candidates");
        FakeContext context = new FakeContext();
        // No site at all: the search skips the candidates instead of retrying one of them.
        context.world = new FakeWorld();
        AutomationController controller = new AutomationController();
        controller.enable();
        for (int i = 0; i < 800; i++) {
            controller.tick(context);
            context.advance(50);
            if ("no_site".equals(controller.reason())) {
                break;
            }
        }
        eq(AutomationState.WAITING, controller.state(), "without a site the automation waits");
        eq("no_site", controller.reason(), "the reason is no_site");
        check(context.registry.houses().isEmpty(), "an inspected candidate creates no record");
    }

    private static void noSiteWaiting() {
        FakeContext context = new FakeContext();
        context.world = new FakeWorld();
        AutomationController controller = new AutomationController();
        controller.enable();
        for (int i = 0; i < 800; i++) {
            controller.tick(context);
            context.advance(50);
            if ("no_site".equals(controller.reason())) {
                break;
            }
        }
        eq("no_site", controller.reason(), "no site");
        check(context.sentCommands.isEmpty(), "no give command while waiting");
        check(context.placementRequests.isEmpty(), "no placement while waiting");
    }

    private static void recordBeforeBuild() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        eq(1, context.registry.houses().size(), "the record is written");
        HouseRecord record = context.registry.houses().get(0);
        eq(HouseState.BUILDING, record.state(), "the record is building");
        eq(0, record.confirmedCells(), "no confirmed cell before the first block");
        check(context.saves >= 1, "the record was written to the registry");
        check(context.placementRequests.isEmpty(), "no block was placed before the record was written");
        eq(0, record.variant(), "the first record uses the variant 0");
        Vec3i firstOrigin = record.origin();

        // The place becomes unusable before the first block: the record stays and only the origin
        // may change.
        List<Vec3i> candidates = new SiteSearch(context.player).nextBatch();
        Vec3i second = candidates.get(1);
        FakeWorld world = FakeWorld.withSite(second);
        world.put(firstOrigin.add(6, -1, 6), ObservedBlock.air());
        context.world = world;
        context.advance(50);
        controller.tick(context);
        eq(1, context.registry.houses().size(), "the candidate change does not create a second record");
        eq(0, context.registry.houses().get(0).variant(), "the palette stays");
        eq("h1", context.registry.houses().get(0).houseId(), "the record keeps its id");
        context.advance(50);
        controller.tick(context);
        eq(1, context.registry.houses().size(), "still one record");
        eq(second, context.registry.houses().get(0).origin(), "the origin changed to the new candidate");
        eq(0, context.registry.houses().get(0).variant(), "the palette is still the same");
    }

    // --- supply ----------------------------------------------------------------------------------

    private static void deficit() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<Cell> unfinished = new HashSet<>(template.occupiedCells());
        Map<String, Integer> inventory = new HashMap<>();
        inventory.put("minecraft:stone_bricks", 10);
        List<ItemNeed> needs = SupplyCalculator.needs(template, unfinished, inventory);
        int floorNeed = 0;
        for (ItemNeed need : needs) {
            if (need.itemId().equals("minecraft:stone_bricks")) {
                floorNeed = need.deficit();
            }
        }
        eq(39, floorNeed, "the deficit of the floor");
        int doorNeed = 0;
        for (ItemNeed need : needs) {
            if (need.itemId().equals("minecraft:spruce_door")) {
                doorNeed = need.deficit();
            }
        }
        eq(1, doorNeed, "a door counts as one item");
    }

    private static void giveCommand() {
        List<String> commands = GiveCommandPlanner.plan(List.of(new ItemNeed("minecraft:stone_bricks", 49, 0)));
        eq(List.of("give @s minecraft:stone_bricks 49"), commands, "the command of the supply step");
        check(!commands.get(0).startsWith("/"), "the command has no leading slash");
        List<String> big = GiveCommandPlanner.plan(List.of(new ItemNeed("minecraft:stone_bricks", 178, 0)));
        eq(3, big.size(), "178 items need three commands");
        eq("give @s minecraft:stone_bricks 64", big.get(0), "the first chunk");
        eq("give @s minecraft:stone_bricks 64", big.get(1), "the second chunk");
        eq("give @s minecraft:stone_bricks 50", big.get(2), "the last chunk");
        for (String command : big) {
            String[] parts = command.split(" ");
            check(parts.length == 4, "the command has four parts: " + command);
            check(Integer.parseInt(parts[3]) <= 64, "a command carries at most 64 items: " + command);
        }
    }

    private static void giveFrequency() {
        RateLimiter limiter = new RateLimiter();
        check(limiter.tryAcquire(1000L), "the first command is sent");
        check(!limiter.tryAcquire(1500L), "a second command in the same second is not sent");
        check(limiter.tryAcquire(2000L), "a command after a second is sent");

        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        context.advance(50);
        controller.tick(context);
        context.advance(50);
        controller.tick(context);
        eq(1, context.sentCommands.size(), "only one command per second is sent");
    }

    private static void noOtherActions() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        controller.enable();
        for (int i = 0; i < 10; i++) {
            controller.tick(context);
            context.advance(50);
        }
        for (String command : context.sentCommands) {
            check(command.matches("give @s minecraft:[a-z_]+ [0-9]+"),
                    "only give commands are sent: " + command);
        }
        for (String command : context.sentCommands) {
            String verb = command.split(" ")[0];
            check(!verb.equals("drop") && !verb.equals("mine") && !verb.equals("craft"),
                    "no item is dropped, mined or crafted: " + command);
        }
    }

    private static void supplySuspensions() {
        FakeContext full = new FakeContext();
        full.world = FakeWorld.withSite(firstCandidate(full.player));
        full.inventorySpace = false;
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(full);
        full.advance(50);
        controller.tick(full);
        eq(AutomationState.SUSPENDED, controller.state(), "a full inventory suspends the automation");
        eq("inventory_full", controller.reason(), "the reason is inventory_full");

        FakeContext missing = new FakeContext();
        missing.world = FakeWorld.withSite(firstCandidate(missing.player));
        missing.giveAvailable = false;
        AutomationController second = new AutomationController();
        second.enable();
        second.tick(missing);
        missing.advance(50);
        second.tick(missing);
        eq(AutomationState.SUSPENDED, second.state(), "a missing give command suspends the automation");
        eq("materials_missing", second.reason(), "the reason is materials_missing");

        // A command tree that was not read yet is no missing command: the automation waits and
        // does not suspend the house with the wrong reason.
        FakeContext unknown = new FakeContext();
        unknown.world = FakeWorld.withSite(firstCandidate(unknown.player));
        unknown.giveState = GiveAvailability.UNKNOWN;
        AutomationController third = new AutomationController();
        third.enable();
        third.tick(unknown);
        unknown.advance(50);
        third.tick(unknown);
        check(!"materials_missing".equals(third.reason()),
                "an unread command tree is no missing material, the reason is " + third.reason());
        check(unknown.sentCommands.isEmpty(),
                "no material is asked while the command tree of the server was not read yet");
    }

    private static void noFakeConfirmation() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        context.advance(50);
        controller.tick(context);
        eq(1, context.sentCommands.size(), "the command was sent");
        check(controller.state() != AutomationState.SUSPENDED, "the automation is not suspended yet");
        eq(0, context.registry.houses().get(0).confirmedCells(),
                "a sent command is not a confirmation of a placed block");
        check(context.placementRequests.isEmpty(), "nothing is placed without the material");
    }

    private static void supplyTimeout() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        for (int i = 0; i < 100; i++) {
            context.advance(50);
            controller.tick(context);
            if (controller.state() == AutomationState.SUSPENDED) {
                break;
            }
        }
        eq(AutomationState.SUSPENDED, controller.state(), "without a confirmation the automation suspends");
        eq("supply_unconfirmed", controller.reason(), "the reason is supply_unconfirmed");
        eq(1, context.sentCommands.size(), "the give command is not repeated automatically");
    }

    private static void resumeSupply() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        context.advance(50);
        controller.tick(context);
        eq(1, context.sentCommands.size(), "the first command was sent");
        // A manual delivery lets the building continue.
        context.inventory.put("minecraft:stone_bricks", 49);
        controller.resume(context.registry.houses().get(0).houseId(), context);
        controller.tick(context);
        check(!context.placementRequests.isEmpty(), "the placement starts after the manual delivery");

        // A resume asks only for the rest of the deficit.
        FakeContext second = new FakeContext();
        second.world = FakeWorld.withSite(firstCandidate(second.player));
        second.inventory.put("minecraft:stone_bricks", 10);
        AutomationController other = new AutomationController();
        other.enable();
        other.tick(second);
        second.advance(50);
        other.tick(second);
        eq(1, second.sentCommands.size(), "the rest of the deficit is requested");
        eq("give @s minecraft:stone_bricks 39", second.sentCommands.get(0), "only the rest is requested");
        // The frequency limit survives the suspension and the new attempt.
        second.advance(100);
        other.tick(second);
        eq(1, second.sentCommands.size(), "the frequency limit is kept");
    }

    /**
     * The spending of the add-on does not hide the delivery of the server.
     *
     * <p>The add-on places the blocks of the project and the temporary supports of the climb from
     * the inventory of the player. The count of the inventory therefore does not grow while the
     * delivery arrives: the material that the server gave is spent again in the same time. The
     * delivery is nevertheless confirmed, because the spending of the add-on is counted back
     * (ТЗ.md section 4).
     */
    private static void spendingDoesNotHideDelivery() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        context.advance(50);
        controller.tick(context);
        eq(1, context.sentCommands.size(), "the command was sent");
        // The server gave the material and the add-on spent it in the same time: the count of the
        // inventory is the same as before, and the add-on placed one item itself.
        context.inventory.put("minecraft:stone_bricks", 48);
        context.consume("minecraft:stone_bricks", 1);
        context.advance(50);
        controller.tick(context);
        context.advance(50);
        controller.tick(context);
        check(controller.state() != AutomationState.SUSPENDED,
                "the spending of the add-on does not suspend the automation: " + controller.reason());
        boolean confirmed = false;
        for (String observation : context.observations) {
            if (observation.startsWith("give_confirmed|")) {
                confirmed = true;
            }
        }
        check(confirmed, "the delivery is confirmed in the observations: " + context.observations);
    }

    private static void externalInterrupt() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        context.advance(50);
        controller.tick(context);
        eq(1, context.registry.houses().size(), "the house was recorded before the first block");

        // The ordinary Stop of UnionClef interrupts the work.
        controller.interrupt(context);
        eq(AutomationState.SUSPENDED, controller.state(), "the automation is suspended after the interruption");
        // The reason names the place where the work stopped, so that the suspension can be read.
        check(controller.reason() != null && controller.reason().startsWith("interrupted@"),
                "the reason of the interruption is interrupted with a place: " + controller.reason());
        check(controller.reason() != null && controller.reason().contains("@")
                && controller.reason().contains(":"),
                "the reason of the interruption names the coordinate and the observed state: "
                        + controller.reason());
        eq(HouseState.SUSPENDED, context.registry.houses().get(0).state(), "the house is suspended");
        eq(1, context.registry.houses().size(), "the interruption does not create a second house");

        // Only an explicit resume continues the work on the same house.
        String houseId = context.registry.houses().get(0).houseId();
        String answer = controller.resume(houseId, context);
        check(answer.contains("\"result\":\"accepted\""), "the resume is accepted: " + answer);
        eq(AutomationState.BUILDING, controller.state(), "the work continues after the resume");
        eq(1, context.registry.houses().size(), "there is still exactly one house");
        eq(houseId, context.registry.houses().get(0).houseId(), "it is the same house");
    }

    // --- the placement order ----------------------------------------------------------------------
    /** The ground under the footprint of the house; the site check guarantees it. */
    private static Set<Cell> groundCells() {
        Set<Cell> ground = new HashSet<>();
        for (int x = 0; x < 7; x++) {
            for (int z = 0; z < 7; z++) {
                ground.add(new Cell(x, -1, z));
            }
        }
        ground.add(new Cell(3, -1, -1));
        return ground;
    }

    private static void placementOrder() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<Cell> ground = groundCells();
        PlacementPlan plan = PlacementPlanner.plan(template, Set.of(), ground::contains);
        check(plan.complete(), "the house can be placed completely: " + plan.unsupportedCell());

        int cells = 0;
        for (PlannedPlacement planned : plan.placements()) {
            cells += planned.operation().cells().size();
        }
        eq(180, cells, "every occupied cell is planned");

        // The floor is placed before anything else, because everything else stands on it.
        for (int i = 0; i < 49; i++) {
            eq(0, plan.placements().get(i).operation().cells().get(0).y(), "the floor comes first");
        }

        // Every placement has a support that is standing at that moment.
        Set<Cell> built = new HashSet<>(ground);
        for (PlannedPlacement planned : plan.placements()) {
            Cell cell = planned.target().cell();
            boolean supported = planned.target().attempts().stream()
                    .anyMatch(attempt -> built.contains(attempt.supportCell()));
            check(supported, "the cell " + cell + " has a support that is standing");
            built.addAll(planned.operation().cells());
        }
    }

    private static void roofOrder() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<Cell> ground = groundCells();
        PlacementPlan plan = PlacementPlanner.plan(template, Set.of(), ground::contains);
        check(plan.complete(), "the house can be placed completely");

        Set<Cell> built = new HashSet<>(ground);
        int inner = 0;
        for (PlannedPlacement planned : plan.placements()) {
            Cell cell = planned.target().cell();
            if (cell.y() == 4) {
                inner++;
                boolean onTheSide = planned.target().attempts().stream()
                        .anyMatch(attempt -> attempt.support() != Support.BELOW
                                && built.contains(attempt.supportCell()));
                boolean onTheWall = planned.target().attempts().stream()
                        .anyMatch(attempt -> attempt.support() == Support.BELOW
                                && built.contains(attempt.supportCell()));
                check(onTheSide || onTheWall,
                        "the cell of the roof " + cell + " is placed on the side of the ring or on the wall below");
            }
            built.addAll(planned.operation().cells());
        }
        eq(49, inner, "the full roof is planned at y=4");
    }

    private static void completePlan() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<Cell> ground = groundCells();
        PlacementPlan plan = PlacementPlanner.plan(template, Set.of(), ground::contains);
        check(plan.complete(), "the house can be placed completely");

        Set<Cell> covered = new HashSet<>();
        for (PlannedPlacement planned : plan.placements()) {
            for (Cell cell : planned.operation().cells()) {
                check(cell.inVolume(), "the cell " + cell + " is inside the volume");
                check(covered.add(cell), "the cell " + cell + " is planned only once");
            }
        }
        eq(180, covered.size(), "all occupied cells are planned");
        eq(180, template.occupiedCells().size(), "the project has 180 occupied cells");
    }

    private static void resumePlan() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<Cell> ground = groundCells();
        PlacementPlan first = PlacementPlanner.plan(template, Set.of(), ground::contains);
        check(first.complete(), "the house can be placed completely");

        Set<Cell> built = new HashSet<>(ground);
        int cut = 60;
        for (int i = 0; i < cut; i++) {
            built.addAll(first.placements().get(i).operation().cells());
        }
        int builtCells = 0;
        for (Cell cell : built) {
            if (cell.y() >= 0) {
                builtCells++;
            }
        }
        PlacementPlan second = PlacementPlanner.plan(template, built, ground::contains);
        check(second.complete(), "the rest of the house can be planned");
        int remaining = 0;
        for (PlannedPlacement planned : second.placements()) {
            for (Cell cell : planned.operation().cells()) {
                check(!built.contains(cell), "the cell " + cell + " is not placed twice");
                remaining++;
            }
        }
        eq(180 - builtCells, remaining, "only the missing cells are planned");
    }

    private static void multiCellOperations() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<Cell> ground = groundCells();
        PlacementPlan plan = PlacementPlanner.plan(template, Set.of(), ground::contains);
        int doors = 0;
        int beds = 0;
        for (PlannedPlacement planned : plan.placements()) {
            CellSpec.Operation operation = planned.operation();
            if (operation.placement() == CellSpec.Placement.DOOR) {
                doors++;
                eq(2, operation.cells().size(), "the door fills two cells");
            }
            if (operation.placement() == CellSpec.Placement.BED) {
                beds++;
                eq(2, operation.cells().size(), "the bed fills two cells");
            }
            if (operation.isMultiCell()) {
                boolean onTheFloor = planned.target().attempts().stream()
                        .allMatch(attempt -> attempt.support() == Support.BELOW);
                check(onTheFloor, "a door and a bed stand on a solid block below them");
            }
        }
        eq(1, doors, "the project has one door");
        eq(1, beds, "the project has one bed");
    }

    private static void accessCells() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<Cell> ground = groundCells();
        PlacementPlan plan = PlacementPlanner.plan(template, Set.of(), ground::contains);
        Set<Cell> occupied = template.cells().keySet();
        Set<Cell> standing = new HashSet<>(ground);
        for (PlannedPlacement planned : plan.placements()) {
            PlacementTarget target = planned.target();
            check(!target.attempts().isEmpty(), "the cell " + target.cell() + " has a way to place it");
            boolean freePlace = false;
            for (PlacementAttempt attempt : target.attempts()) {
                check(!attempt.accessCandidates().isEmpty(), "the player has a place for " + target.cell());
                for (Cell candidate : attempt.accessCandidates()) {
                    check(!standing.contains(candidate), "the player does not stand inside " + candidate);
                    if (!occupied.contains(candidate)) {
                        freePlace = true;
                    }
                }
            }
            check(freePlace || target.cell().y() == 0,
                    "the player has a place next to " + target.cell() + " that stays free");
            standing.addAll(planned.operation().cells());
        }
    }

    // --- helpers ---------------------------------------------------------------------------------

    // --- the new checks of the round 2 ---------------------------------------------------------------

    /** The hash of the delivered UnionClef jar (ТЗ.md section 1). */
    private static final String UNIONCLEF_SHA256 =
            "76bbb677d2f8294e6d2de98b554f81818ae15605ef8a82ff7e1d3b9b82555d1b";

    /** The nested runtime jars of UnionClef that are delivered with the project. */
    private static final List<String> UNIONCLEF_NESTED_JARS = List.of(
            "mixinextras-fabric-0.3.5.jar", "nether-pathfinder-1.5.jar", "tungsten-BETA-1.0.0.jar");

    /** v5 authorizes ordinary vanilla commands, never an out-of-protocol mutation of a local/server world. */
    private static final java.util.regex.Pattern DIRECT_WORLD_WRITE = java.util.regex.Pattern.compile(
            "\\.\\s*(setBlock|setBlockState|removeBlock|breakBlock|setBlockEntity|addBlockEntity|removeBlockEntity|setBlockEntityNbt)\\s*\\(");

    private static void unionclefFileHash() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        Path jar = root.resolve("libs/unionclef-1.21.11-0.95.61.jar");
        check(Files.isRegularFile(jar), "the delivered UnionClef jar is in the project: " + jar);
        eq(UNIONCLEF_SHA256, hash(jar), "the hash of the delivered UnionClef jar");
    }

    private static void unionclefFileDependency() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        String build = Files.readString(root.resolve("build.gradle"), StandardCharsets.UTF_8);
        check(build.contains("files(project.unionclef_jar)"), "the build uses the delivered file of UnionClef");
        for (String nested : UNIONCLEF_NESTED_JARS) {
            check(build.contains("libs/nested/" + nested), "the build uses the nested runtime jar " + nested);
            check(Files.isRegularFile(root.resolve("libs/nested/" + nested)),
                    "the nested runtime jar " + nested + " is in the project");
        }
        check(!java.util.regex.Pattern.compile("(?im)^\\s*(?:modImplementation|modRuntimeOnly|implementation|runtimeOnly)\\s*\\(?\\s*['\"][^'\"\\n]*(?:altoclef|unionclef)[^'\"\\n]*:")
                        .matcher(build).find(), "the build has no Maven coordinate of UnionClef");
        check(Files.isDirectory(root.resolve("libs/maven-repo")),
                "the project has its own repository for the libraries of the Mojang set");
    }

    private static void noDirectWorldWrites() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        List<Path> sources = new ArrayList<>();
        for (Path directory : List.of(root.resolve("src/main/java"), root.resolve("core/src/main/java"))) {
            if (!Files.isDirectory(directory)) {
                continue;
            }
            try (var stream = Files.walk(directory)) {
                stream.filter(path -> path.toString().endsWith(".java")).forEach(sources::add);
            }
        }
        check(!sources.isEmpty(), "the add-on has sources");
        Path labFixture = root.resolve("src/main/java/dev/magehome/client/LabScenarioFixture.java");
        for (Path source : sources) {
            if (!source.equals(labFixture)
                    && !source.equals(root.resolve("src/main/java/dev/magehome/client/AcceptanceRunner.java"))) {
                check(!Files.readString(source, StandardCharsets.UTF_8).contains("LabScenarioFixture"),
                        "the lab fixture has no caller in the product: " + source);
            }
            if (source.equals(labFixture)) {
                // Authorized acceptance setup writes a real server world before product actions.
                // Keep this one explicit boundary narrow; every product source is scanned below.
                String fixture = Files.readString(source, StandardCharsets.UTF_8);
                check(fixture.contains("final class LabScenarioFixture"), "only the named lab fixture is exempt");
                check(fixture.contains("!scenarioId.equals(System.getProperty(\"lab.acceptance.scenario\"))"),
                        "fixture requires an explicit matching scenario before scheduling any world mutation");
                check(fixture.indexOf("fixture requires an explicit matching lab.acceptance.scenario")
                                < fixture.indexOf("CompletableFuture.runAsync"),
                        "fixture scenario guard precedes the server mutation schedule");
                continue;
            }
            for (String line : Files.readAllLines(source, StandardCharsets.UTF_8)) {
                String trimmed = line.trim();
                if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) {
                    continue;
                }
                check(!DIRECT_WORLD_WRITE.matcher(trimmed).find(),
                        "the file " + source + " bypasses vanilla client protocol: " + trimmed);
            }
        }
    }

    private static void pinnedToolchain() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        String properties = Files.readString(root.resolve("gradle.properties"), StandardCharsets.UTF_8);
        check(properties.contains("minecraft_version=1.21.11"), "the version of Minecraft is pinned");
        check(properties.contains("yarn_mappings=1.21.11+build.3"), "the Yarn mappings are pinned");
        check(properties.contains("loader_version=0.19.2"), "the version of the loader is pinned");
        check(properties.contains("fabric_version=0.141.3+1.21.11"), "the version of the Fabric API is pinned");
        check(properties.contains("loom_coordinate=net.fabricmc.fabric-loom-remap:1.15.5"),
                "the coordinate of Loom is pinned");
        String build = Files.readString(root.resolve("build.gradle"), StandardCharsets.UTF_8);
        check(build.contains("net.fabricmc.fabric-loom-remap' version '1.15.5"), "the plugin of Loom is pinned");
        check(build.contains("JavaLanguageVersion.of(21)"), "the client uses Java 21");
        check(build.contains("options.release = 21"), "the client is compiled with release 21");
        String coreBuild = Files.readString(root.resolve("core/build.gradle"), StandardCharsets.UTF_8);
        check(coreBuild.contains("JavaLanguageVersion.of(21)"), "the core uses Java 21");
        check(coreBuild.contains("options.release = 21"), "the core is compiled with release 21");
        Path wrapper = root.resolve("gradle/wrapper/gradle-wrapper.properties");
        check(Files.isRegularFile(wrapper), "the wrapper of Gradle is in the project");
        String wrapperText = Files.readString(wrapper, StandardCharsets.UTF_8);
        check(wrapperText.contains("gradle-9.2.1-bin.zip"), "Gradle 9.2.1 is pinned in the wrapper");
        check(Files.isRegularFile(root.resolve("gradlew")), "the script of the wrapper is in the project");
    }

    /**
     * The build does not depend on the host of the Gradle distribution.
     *
     * <p>The independent build failed before the first compilation when the download of the
     * distribution timed out, so that not a single product check could run. The script of the
     * wrapper therefore uses a distribution of the pinned version that is already installed, and
     * the pinned version is never replaced: a distribution of another version is not used either.
     * The check is distinguishing: with the discovery of the script the distribution of the
     * pinned version is found, and the distribution of another version is not.
     */
    private static void offlineGradleDistribution() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        Path properties = root.resolve("gradle/wrapper/gradle-wrapper.properties");
        String wrapperText = Files.readString(properties, StandardCharsets.UTF_8);
        check(wrapperText.contains("gradle-9.2.1-bin.zip"), "the distribution of the pinned version is named");
        Matcher timeout = Pattern.compile("networkTimeout=([0-9]+)").matcher(wrapperText);
        check(timeout.find(), "the timeout of the download of Gradle is named");
        check(Long.parseLong(timeout.group(1)) >= 60_000L,
                "the timeout of the download of Gradle is large enough for a slow proxy");
        check(wrapperText.contains("validateDistributionUrl=false"),
                "the address of the distribution is not asked before the download");
        Path script = root.resolve("gradlew");
        String text = Files.readString(script, StandardCharsets.UTF_8);
        check(text.contains("GRADLE_PINNED_VERSION=9.2.1"), "the script knows the pinned version");
        Matcher named = Pattern.compile("gradle-([0-9]+(?:\\.[0-9]+)*)").matcher(text);
        boolean onlyPinned = true;
        while (named.find()) {
            onlyPinned = onlyPinned && "9.2.1".equals(named.group(1));
        }
        check(onlyPinned, "the script names no other version of Gradle");
        int start = text.indexOf("is_pinned_gradle_home() {");
        int discovery = text.indexOf("find_pinned_gradle() {");
        int end = text.indexOf("PINNED_GRADLE=$( find_pinned_gradle )");
        check(start >= 0 && discovery > start && end > discovery,
                "the script holds the discovery of the distribution");
        check(text.contains("command -v gradle"), "the script looks for a distribution on the PATH");
        // The predicate and the discovery of the script are checked apart, so that the meaning of
        // the predicate does not depend on the file system of the machine.
        String predicate = text.substring(start, discovery);
        check(predicate.contains("[ -x \"$1/bin/gradle\" ]"),
                "the script wants a distribution with an executable program");
        check(predicate.contains("\"$1\"/lib/*-\"$GRADLE_PINNED_VERSION\".jar"),
                "the script wants the library of the pinned version");
        Path directory = fixtureDirectory();
        boolean executable = allowsExecution(directory) && !degradedBranchIsForced();
        System.out.println("T-A1-10 runs the " + (executable ? "real" : "degraded")
                + " branch, the fixture of the check is " + directory);
        // The functions of the script are the subject of the test. The predicate of the script is
        // used as it is; only when the machine does not allow the execution of a file, the one
        // bit of the predicate that the machine cannot show is derived from the predicate of the
        // script itself, so that the test cannot drift away from the product.
        String functions = executable
                ? text.substring(start, end)
                : relaxTheExecutableBit(predicate) + "\n" + text.substring(discovery, end);
        try {
            Path onPath = fakeDistribution(directory.resolve("on-path/gradle-9.2.1"), "9.2.1");
            Path usual = fakeDistribution(directory.resolve("usual-9.2.1"), "9.2.1");
            Path other = fakeDistribution(directory.resolve("other-8.5"), "8.5");
            // The script resolves the file of the PATH with readlink, so the expected answer is
            // the path that the shell of the machine resolves.
            String expectedOnPath = resolveByShell(onPath.resolve("bin/gradle"));
            String found = discover(functions, "", onPath.resolve("bin"), !executable);
            if (!expectedOnPath.equals(found)) {
                throw new AssertionError("the script uses the distribution of the pinned version that is on the PATH"
                        + " (expected " + expectedOnPath + ", got " + found + ")"
                        + environmentDiagnostics(onPath, directory, executable));
            }
            eq(usual.resolve("bin/gradle").toString(),
                    discover(functions, directory.resolve("usual").toString(), null, !executable),
                    "the script uses the distribution of the pinned version of the usual places");
            String otherFound = discover(functions, directory.resolve("other").toString(), null, !executable);
            check(!otherFound.contains(other.toString()),
                    "the script does not use a distribution of another version");

            // The test distinguishes: it is not enough that the script finds a distribution, the
            // script has to find it because of its own rules. A broken lookup of the PATH and a
            // broken rule of the version have to make the discovery of the script fail.
            String brokenLookup = functions.replace("command -v gradle", "command -v gradleX");
            check(!brokenLookup.equals(functions),
                    "the lookup of the PATH of the script could be broken for the test");
            check(discover(brokenLookup, "", onPath.resolve("bin"), !executable).isEmpty(),
                    "the script finds no distribution on the PATH when its own lookup is broken");
            String brokenVersion = functions.replace("\"$GRADLE_PINNED_VERSION\"", "\"0.0.0\"");
            check(!brokenVersion.equals(functions),
                    "the rule of the version of the script could be broken for the test");
            check(discover(brokenVersion, "", onPath.resolve("bin"), !executable).isEmpty(),
                    "the script finds no distribution when its own rule of the version is broken");
        } finally {
            deleteTree(directory);
        }
    }

    /**
     * Whether the check is forced into the degraded branch.
     *
     * <p>A machine that forbids the execution of a file cannot show the executable bit of the
     * predicate of the script; in that case the check derives the predicate of the script without
     * that one bit. The branch can be forced with the system property
     * {@code lab.core.check.degraded.gradle}, so that the degraded branch runs on every machine and
     * not only on the machines that forbid the execution of a file.
     */
    private static boolean degradedBranchIsForced() {
        return System.getProperty("lab.core.check.degraded.gradle") != null;
    }

    /**
     * The predicate of the script without the bit of the execution.
     *
     * <p>The derivation is done from the predicate of the script itself and not from a copy that
     * the check keeps: only the test of the executable bit is replaced, because a file system that
     * forbids the execution cannot show that bit. Every other rule of the predicate stays the one
     * of the script, so that the test cannot become weaker than the product.
     */
    private static String relaxTheExecutableBit(String predicate) {
        String executableTest = "[ -x \"$1/bin/gradle\" ]";
        String fileTest = "[ -f \"$1/bin/gradle\" ]";
        check(predicate.contains(executableTest),
                "the predicate of the script tests the executable bit of the program");
        String relaxed = predicate.replace(executableTest, fileTest);
        check(relaxed.contains(fileTest) && !relaxed.contains(executableTest),
                "the predicate of the test differs from the predicate of the script only in the executable bit");
        check(relaxed.contains("\"$1\"/lib/*-\"$GRADLE_PINNED_VERSION\".jar"),
                "the rule of the version of the script stays in the predicate of the test");
        return relaxed;
    }

    /**
     * The directory of the fake distributions of the check.
     *
     * <p>The directories of the machine are tried one after another: the build directory of the
     * project, the temporary directory of the machine and the home directory of the user. The
     * first directory that the machine allows to write in is used, and a directory in which a
     * file can be executed is preferred, because the predicate of the script wants a program
     * that can be executed. The snapshot of the files of the project ignores the build
     * directories, so the check does not change the sources.
     */
    private static Path fixtureDirectory() throws IOException {
        Path writable = null;
        for (Path candidate : fixtureCandidates()) {
            Path directory;
            try {
                directory = candidate.resolve("mage-home-gradle-fixture");
                Files.createDirectories(directory);
            } catch (IOException e) {
                // The directory of the machine cannot be used.
                continue;
            }
            if (writable == null) {
                writable = directory;
            }
            if (allowsExecution(directory)) {
                return directory;
            }
        }
        if (writable != null) {
            return writable;
        }
        return Files.createTempDirectory("gradle-distribution");
    }

    /**
     * The directories of the machine in which the fixture of the check can be placed.
     *
     * <p>The build directory of the project comes first: it is ignored by the snapshot of the
     * sources and it is not shared with another program. The temporary directory of the machine
     * and the home directory of the user follow, because a machine does not always allow the
     * execution of a file in its temporary directory.
     */
    private static List<Path> fixtureCandidates() {
        List<Path> candidates = new ArrayList<>();
        Path root = projectRoot();
        if (root != null) {
            candidates.add(root.resolve("build").resolve("tmp"));
        }
        String temporary = System.getProperty("java.io.tmpdir");
        if (temporary != null && !temporary.isBlank()) {
            candidates.add(Path.of(temporary));
        }
        String home = System.getProperty("user.home");
        if (home != null && !home.isBlank()) {
            candidates.add(Path.of(home));
        }
        return candidates;
    }

    /**
     * Whether a file can be executed in the given directory.
     *
     * <p>The question is asked with the same test that the predicate of the script uses: the
     * shell of the machine has to answer that the file is executable. A file system that forbids
     * the execution of a file (a mount with the option {@code noexec}) is therefore recognised
     * before the fixture of the check is used.
     */
    private static boolean allowsExecution(Path directory) {
        try {
            Path probe = directory.resolve("probe.sh");
            Files.writeString(probe, "#!/bin/sh\nexit 0\n", StandardCharsets.UTF_8);
            try {
                Files.setPosixFilePermissions(probe, PosixFilePermissions.fromString("rwxr-xr-x"));
            } catch (UnsupportedOperationException e) {
                // A file system without POSIX permissions keeps the bit of the owner.
                if (!probe.toFile().setExecutable(true)) {
                    return false;
                }
            }
            Process process = new ProcessBuilder("sh", "-c", "test -x \"$1\"", "sh", probe.toString()).start();
            process.waitFor();
            return process.exitValue() == 0;
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * The path that the shell of the machine resolves.
     *
     * <p>The script of the wrapper resolves the file of the PATH with readlink and keeps the
     * path itself when readlink is not available, so the expectation of the check uses the same
     * rule.
     */
    private static String resolveByShell(Path file) {
        try {
            Process process = new ProcessBuilder("sh", "-c",
                    "readlink -f \"$1\" 2>/dev/null || printf '%s\\n' \"$1\"", "sh", file.toString())
                    .redirectErrorStream(true).start();
            String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            process.waitFor();
            return output.trim();
        } catch (Exception e) {
            return file.toString();
        }
    }

    /** Removes the directory of the fixture of the check and its files. */
    private static void deleteTree(Path directory) {
        try (var stream = Files.walk(directory)) {
            stream.sorted(Comparator.reverseOrder()).forEach(path -> {
                try {
                    Files.deleteIfExists(path);
                } catch (IOException e) {
                    // The file stays; the check does not fail because of the cleanup.
                }
            });
        } catch (IOException e) {
            // The directory stays; the check does not fail because of the cleanup.
        }
    }

    /**
     * Creates a directory that looks like an installed Gradle distribution of the given version.
     *
     * @param home the directory of the distribution
     * @param version the version of the distribution
     * @return the directory of the distribution
     */
    private static Path fakeDistribution(Path home, String version) throws IOException {
        Files.createDirectories(home.resolve("bin"));
        Path binary = home.resolve("bin/gradle");
        Files.writeString(binary, "#!/bin/sh\nexit 0\n", StandardCharsets.UTF_8);
        try {
            // The mode is set for every user, so that the shell of the machine finds the file
            // whatever user it runs as.
            Files.setPosixFilePermissions(binary, PosixFilePermissions.fromString("rwxr-xr-x"));
        } catch (IOException | UnsupportedOperationException e) {
            // A file system without POSIX permissions keeps the bit of the owner.
            if (!binary.toFile().setExecutable(true)) {
                throw new IOException("the distribution of the check cannot be made executable");
            }
        }
        Files.createDirectories(home.resolve("lib"));
        Files.write(home.resolve("lib/gradle-x-" + version + ".jar"), new byte[] {0});
        return home;
    }

    /**
     * Runs the discovery of the wrapper script with a controlled environment.
     *
     * <p>The environment knows only the given distribution, so that the result of the discovery
     * says which distribution the script would use.
     *
     * @param functions the part of the script that holds the discovery
     * @param usualPlaces the usual places of a machine, empty when there is none
     * @param path the directory that is on the PATH of the run, null when there is none
     * @return the distribution that the script would use
     */
    private static String discover(String functions, String usualPlaces, Path path) throws Exception {
        return discover(functions, usualPlaces, path, false);
    }

    /**
     * Runs the discovery of the wrapper script with a controlled environment.
     *
     * <p>The environment knows only the given distribution, so that the result of the discovery
     * says which distribution the script would use. With {@code controlled} the lookup of the
     * PATH is done by the check itself: the lookup of a machine is not a property of the
     * product, and the machine of the independent runner does not always see an executable file
     * in the temporary directory. The script of the project is the same in both runs, and the
     * controlled lookup is used only when the machine cannot show the executable bit.
     *
     * @param functions the part of the script that holds the discovery
     * @param usualPlaces the usual places of a machine, empty when there is none
     * @param path the directory that is on the PATH of the run, null when there is none
     * @param controlled true when the lookup of the PATH is done by the check
     * @return the distribution that the script would use
     */
    private static String discover(String functions, String usualPlaces, Path path, boolean controlled)
            throws Exception {
        Path script = Files.createTempFile("gradle-discovery", ".sh");
        Files.writeString(script, (controlled ? CONTROLLED_LOOKUP : "") + functions, StandardCharsets.UTF_8);
        Path empty = Files.createTempDirectory("gradle-empty-home");
        String command = "GRADLE_PINNED_VERSION=9.2.1; export GRADLE_PINNED_VERSION; "
                + "GRADLE_DISTRIBUTION_HOMES=\"" + usualPlaces + "\"; export GRADLE_DISTRIBUTION_HOMES; "
                + "GRADLE_HOME=; export GRADLE_HOME; "
                + "HOME=\"" + empty + "\"; export HOME; "
                + "GRADLE_USER_HOME=\"" + empty + "\"; export GRADLE_USER_HOME; "
                + "PATH=\"" + (path == null ? "/usr/bin:/bin" : path + ":/usr/bin:/bin") + "\"; export PATH; "
                + ". \"" + script + "\"; find_pinned_gradle";
        Process process = new ProcessBuilder("sh", "-c", command).redirectErrorStream(true).start();
        String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        process.waitFor();
        return output.trim();
    }

    /**
     * The lookup of the PATH that the check uses instead of the lookup of the machine.
     *
     * <p>The lookup of the machine is used whenever the machine allows the execution of a file.
     * A machine that does not allow it cannot show the executable bit, and the lookup of the PATH
     * of the shell would find nothing at all; the check uses this lookup only in that case, and
     * the discovery of the script stays the subject of the test: the controls of the test break
     * the lookup and the rule of the version of the script itself and demand that the discovery
     * fails then.
     */
    private static final String CONTROLLED_LOOKUP = """
            command() {
                if [ "$1" = "-v" ] ; then
                    shift
                    for name in "$@" ; do
                        case "$name" in
                            */*) if [ -f "$name" ] ; then printf '%s\\n' "$name" ; return 0 ; fi ;;
                            *) saved=$IFS ; IFS=: ;
                               for dir in $PATH ; do
                                   if [ -f "$dir/$name" ] ; then
                                       IFS=$saved ; printf '%s\\n' "$dir/$name" ; return 0 ;
                                   fi
                               done
                               IFS=$saved ;;
                        esac
                        return 1
                    done
                fi
                builtin command "$@"
            }
            """;

    /**
     * The facts of the machine that run the check.
     *
     * <p>They tell the next run whether the file of the check is executable, what the lookup of
     * the PATH of the machine answers and how the script resolves the file.
     */
    private static String environmentDiagnostics(Path onPath, Path directory, boolean executable) {
        Path binary = onPath.resolve("bin/gradle");
        StringBuilder text = new StringBuilder();
        text.append("; the file of the check is ").append(binary);
        text.append(", the mode is ").append(mode(binary));
        text.append(", the file is executable: ").append(Files.isExecutable(binary));
        text.append("; the directory of the check is ").append(directory);
        text.append(", the directory allows the execution of a file: ").append(executable);
        text.append("; the lookup of the machine answers '").append(lookupOfTheMachine(onPath.resolve("bin")))
                .append("'");
        text.append("; the script resolves the file to '").append(resolveOfTheMachine(binary)).append("'");
        text.append("; /usr/bin/gradle exists: ").append(Files.exists(Path.of("/usr/bin/gradle")));
        text.append("; the temporary directory of the machine is ")
                .append(System.getProperty("java.io.tmpdir"));
        text.append("; the home directory of the user is ").append(System.getProperty("user.home"));
        return text.toString();
    }

    /** The mode of a file, as the machine shows it. */
    private static String mode(Path file) {
        try {
            return PosixFilePermissions.toString(Files.getPosixFilePermissions(file));
        } catch (IOException | UnsupportedOperationException e) {
            return "unknown";
        }
    }

    /** The answer of the lookup of the PATH of the machine. */
    private static String lookupOfTheMachine(Path path) {
        try {
            Process process = new ProcessBuilder("sh", "-c",
                    "PATH=\"" + path + ":/usr/bin:/bin\"; export PATH; command -v gradle")
                    .redirectErrorStream(true).start();
            String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            process.waitFor();
            return output.trim();
        } catch (Exception e) {
            return "the lookup failed: " + e;
        }
    }

    /** The answer of the machine to the resolution of a path. */
    private static String resolveOfTheMachine(Path file) {
        try {
            Process process = new ProcessBuilder("sh", "-c", "readlink -f \"" + file + "\"")
                    .redirectErrorStream(true).start();
            String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            process.waitFor();
            return output.trim();
        } catch (Exception e) {
            return "the resolution failed: " + e;
        }
    }

    /**
     * The ownership of the UnionClef task chain.
     *
     * <p>The facts come from the bytecode of the delivered UnionClef: the walk of
     * {@code thisOrChildSatisfies} goes down to the children of the task and
     * {@code getCurrentTask()} returns the root task of the chain. The check is distinguishing:
     * with the opposite direction of the walk the case {@code OWN_DESCENDANT} would be a foreign
     * task and the automation would stop with {@code reason=busy} during its own navigation.
     */
    private static void taskOwnership() {
        eq(TaskOwnership.Relation.OWN_ROOT, TaskOwnership.relation(true, true),
                "the own root task is the current task");
        eq(TaskOwnership.Relation.OWN_DESCENDANT, TaskOwnership.relation(false, true),
                "the own root task is below the current task");
        eq(TaskOwnership.Relation.FOREIGN, TaskOwnership.relation(false, false),
                "the task of another owner is a foreign task");
        eq(TaskOwnership.Relation.FOREIGN, TaskOwnership.relation(true, false),
                "a current task that does not hold the own root task is a foreign task");
        check(!TaskOwnership.foreign(TaskOwnership.relation(true, true)), "the own task is not foreign");
        check(!TaskOwnership.foreign(TaskOwnership.relation(false, true)),
                "the own navigation is not a foreign task");
        check(!TaskOwnership.foreign(TaskOwnership.noTask()), "an empty chain is not foreign");
        check(TaskOwnership.foreign(TaskOwnership.relation(false, false)), "a foreign task is foreign");
    }

    /**
     * The verification of the loaded houses runs on the command status and before a resume.
     *
     * <p>There is no constant background sweep: the tick never verifies a house.
     */
    private static void verificationOnStatus() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Vec3i origin = new Vec3i(100, 64, 200);
        FakeWorld world = FakeWorld.withSite(origin);
        world.applyHouse(template, origin);
        FakeContext context = new FakeContext();
        context.world = world;
        HouseRecord record = record("h1", context.worldKey, context.dimension, context.playerUuid, origin);
        record.setState(HouseState.READY);
        context.registry.add(record);
        AutomationController controller = new AutomationController();
        controller.enable();

        controller.verifyHomes(context);
        eq(HouseState.READY, record.state(), "a complete house stays ready");
        eq(Visibility.LOADED, record.visibility(), "the visibility of a loaded house");
        check(record.lastVerifiedAt() != null, "the time of the verification is written");

        // One wrong cell makes the ready house damaged.
        world.put(origin.add(0, 1, 0), ObservedBlock.of("minecraft:dirt", Map.of(), true, false, false));
        controller.verifyHomes(context);
        eq(HouseState.DAMAGED, record.state(), "a ready house with a mismatch becomes damaged");

        // An unloaded house keeps the time of its last verification.
        FakeWorld hidden = new FakeWorld();
        context.world = hidden;
        record.setLastVerifiedAt("2024-01-01T00:00:00Z");
        controller.verifyHomes(context);
        eq(Visibility.UNLOADED, record.visibility(), "the visibility of an unloaded house");
        eq("2024-01-01T00:00:00Z", record.lastVerifiedAt(), "the time of the last verification is kept");
        eq(HouseState.DAMAGED, record.state(), "an unloaded house is not damaged by the verification");

        // The tick never verifies a house in the background.
        FakeContext quiet = new FakeContext();
        quiet.world = world;
        HouseRecord quietRecord = record("h2", quiet.worldKey, quiet.dimension, quiet.playerUuid, origin);
        quietRecord.setState(HouseState.READY);
        quiet.registry.add(quietRecord);
        AutomationController quietController = new AutomationController();
        quietController.enable();
        for (int i = 0; i < 100; i++) {
            quietController.tick(quiet);
            quiet.advance(50);
        }
        check(quietRecord.lastVerifiedAt() == null, "the tick does not verify the houses in the background");
    }

    /** The allowed plants are removed and the volume is checked again. */
    private static void plantRemoval() {
        FakeContext context = new FakeContext();
        Vec3i origin = firstCandidate(context.player);
        context.world = FakeWorld.withSite(origin);
        context.world.plant(origin.add(2, 0, 2), "minecraft:short_grass");
        context.world.plant(origin.add(3, 0, 3), "minecraft:tall_grass");
        AutomationController controller = new AutomationController();
        controller.enable();
        for (int i = 0; i < 8; i++) {
            controller.tick(context);
            context.advance(50);
        }
        eq(2, context.plantRemovals.size(), "the two plants are removed");
        check(context.plantRemovals.contains(origin.add(2, 0, 2)), "the first plant is removed");
        check(context.plantRemovals.contains(origin.add(3, 0, 3)), "the second plant is removed");
        eq(1, context.registry.houses().size(), "the house starts after the volume is free");
        eq(HouseState.BUILDING, context.registry.houses().get(0).state(), "the house is building");

        // A tree is never removed and the candidate is skipped.
        FakeContext blocked = new FakeContext();
        Vec3i other = firstCandidate(blocked.player);
        blocked.world = FakeWorld.withSite(other);
        blocked.world.put(other.add(2, 1, 2), ObservedBlock.of("minecraft:oak_log", Map.of("axis", "y"), true, false, false));
        AutomationController second = new AutomationController();
        second.enable();
        for (int i = 0; i < 8; i++) {
            second.tick(blocked);
            blocked.advance(50);
        }
        check(blocked.plantRemovals.isEmpty(), "a tree is never removed");
        check(blocked.registry.houses().isEmpty(), "a blocked candidate creates no record");
    }

    /** The record of the site is written completely before the first block is placed. */
    private static void siteRecordBeforeFirstBlock() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        context.writeFinished = false;
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        eq(1, context.registry.houses().size(), "the record of the site is written");
        check(context.placementRequests.isEmpty(), "no block is placed while the write is running");
        check(context.sentCommands.isEmpty(), "no material is asked for while the write is running");

        // The write is finished: the building starts.
        context.writeFinished = true;
        controller.tick(context);
        check(!context.sentCommands.isEmpty(), "the building continues when the write is finished");
        check(context.placementRequests.isEmpty(), "the block is placed after the material arrived");
    }

    /** The house becomes ready only when the player stands at the front point. */
    private static void readyAtFrontPoint() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Vec3i origin = new Vec3i(100, 64, 200);
        FakeWorld world = FakeWorld.withSite(origin);
        world.applyHouse(template, origin);
        FakeContext context = new FakeContext();
        context.world = world;
        context.player = origin.add(3, 0, 3);
        HouseRecord record = record("h1", context.worldKey, context.dimension, context.playerUuid, origin);
        record.setState(HouseState.BUILDING);
        context.registry.add(record);
        AutomationController controller = new AutomationController();
        controller.enable();
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");

        controller.tick(context);
        eq(HouseState.BUILDING, record.state(), "the house is not ready while the player is inside");
        check(!context.frontPointRequests.isEmpty(), "the automation asks for the front point");
        eq(origin.add(3, 0, -1), context.frontPointRequests.get(0), "the front point of the house");

        // The player stands at the front point: the house is ready.
        context.player = origin.add(3, 0, -1);
        controller.tick(context);
        eq(HouseState.READY, record.state(), "the house is ready at the front point");
    }

    /**
     * The number of sends per position and the end of the interaction.
     *
     * <p>The world confirms an operation: the automation does not ask for it again. Without the
     * confirmation the automation asks a second time and then stops the house.
     */
    private static void interactionPerOperation() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        FakeContext context = new FakeContext();
        Vec3i origin = firstCandidate(context.player);
        context.world = FakeWorld.withSite(origin);
        context.inventory.put("minecraft:stone_bricks", 64);
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        controller.tick(context);
        eq(1, context.placementOperations.size(), "the first operation is requested once");

        // The world has the block: the interaction stops.
        context.world.applyOperation(template, context.placementOperations.get(0), origin);
        controller.tick(context);
        check(!context.confirmedPlacements.isEmpty(), "the world confirms the operation");
        String first = context.placementRequests.get(0);
        long sameOperation = context.placementRequests.stream().filter(request -> request.equals(first)).count();
        eq(1L, sameOperation, "the confirmed operation is not requested again");

        // Without the confirmation the automation asks a second time and then suspends.
        FakeContext silent = new FakeContext();
        silent.world = FakeWorld.withSite(origin);
        silent.inventory.put("minecraft:stone_bricks", 64);
        AutomationController waiting = new AutomationController();
        waiting.enable();
        for (int i = 0; i < 400; i++) {
            waiting.tick(silent);
            silent.advance(50);
            if ("placement_unconfirmed".equals(waiting.reason())) {
                break;
            }
        }
        eq("placement_unconfirmed", waiting.reason(), "the house is suspended when the world does not confirm");
        HouseRecord suspended = waiting.currentHouse();
        check(suspended != null, "the record of the house is kept");
        String houseReason = suspended.reason();
        check(houseReason != null && houseReason.startsWith("placement_unconfirmed@"),
                "the reason of the house names the cause and the coordinate: " + houseReason);
        check(houseReason.contains(origin.x() + "," + origin.y() + ","),
                "the reason of the house contains the coordinate of the cell: " + houseReason);
        check(houseReason.contains(":"), "the reason of the house contains the observed state: " + houseReason);
        String request = silent.placementRequests.isEmpty() ? "" : silent.placementRequests.get(0);
        long sends = silent.placementRequests.stream().filter(entry -> entry.equals(request)).count();
        eq((long) AutomationController.MAX_PLACEMENT_ATTEMPTS, sends,
                "the number of sends per position in one attempt");
    }

    /**
     * The client sends the interaction of an attempt once and stops after the confirmation.
     *
     * <p>The rule itself lives in the domain core ({@link InteractionPolicy}) and is checked here
     * without a game. The source of the client is read as well, so that the checked rule is the
     * one the client really applies and not a copy of it.
     */
    private static void oneInteractionPerAttempt() throws Exception {
        check(InteractionPolicy.maySend(false, false), "the first interaction of an attempt is sent");
        check(!InteractionPolicy.maySend(true, false), "the interaction is not repeated on every tick");
        check(!InteractionPolicy.maySend(false, true), "nothing is sent after the world confirmed the block");
        check(!InteractionPolicy.maySend(true, true), "nothing is sent after the confirmation either");

        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        String client = Files.readString(
                root.resolve("src/main/java/dev/magehome/client/PlacementExecutor.java"),
                StandardCharsets.UTF_8);
        check(client.contains("InteractionPolicy.maySend(clickSent, confirmed)"),
                "the client applies the checked rule");
        check(client.contains("this.confirmed = true;"),
                "the client stops the interaction when the world confirmed the operation");
        check(client.contains("this.confirmed = false;"),
                "a new operation starts without a confirmation of the world");
    }

    /**
     * UnionClef is paused or a foreign task owns the chain: the add-on starts no background
     * action at all (ТЗ.md section 3).
     */
    private static void pausedOrForeignTask() {
        FakeContext paused = new FakeContext();
        paused.world = FakeWorld.withSite(firstCandidate(paused.player));
        paused.paused = true;
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(paused);
        eq(AutomationState.WAITING, controller.state(), "the state while UnionClef is paused");
        eq("busy", controller.reason(), "the reason while UnionClef is paused");
        check(paused.sentCommands.isEmpty(), "no material is asked while UnionClef is paused");
        check(paused.placementRequests.isEmpty(), "no block is placed while UnionClef is paused");
        check(paused.registry.houses().isEmpty(), "no house is created while UnionClef is paused");

        FakeContext foreign = new FakeContext();
        foreign.world = FakeWorld.withSite(firstCandidate(foreign.player));
        foreign.foreign = true;
        AutomationController second = new AutomationController();
        second.enable();
        second.tick(foreign);
        eq(AutomationState.WAITING, second.state(), "the state while a foreign task runs");
        eq("busy", second.reason(), "the reason while a foreign task runs");
        check(foreign.sentCommands.isEmpty(), "no material is asked while a foreign task runs");
        check(foreign.placementRequests.isEmpty(), "no block is placed while a foreign task runs");
        check(foreign.registry.houses().isEmpty(), "no house is created while a foreign task runs");
    }

    /**
     * A wrong block inside the volume of a registered house is removed with an ordinary attack of
     * the player, the cell is read again and the building continues (ТЗ.md section 4).
     *
     * <p>This check replaces the check T-A4-18 of the earlier revisions, which asserted that a
     * wrong block is never broken. The current text of the section 4 of the specification allows
     * the removal of a wrong block inside the volume of a registered house; the id was migrated
     * deliberately, so that the result of the old text is not read as a result of the new one.
     */
    private static void wrongBlockIsRepaired() {
        FakeContext context = new FakeContext();
        Vec3i origin = new Vec3i(100, 64, 200);
        HouseTemplate template = HouseTemplate.forVariant(0);
        // The wrong block is a material of the project in the wrong cell: such a block is removed
        // by the safe repair. A block that the project does not place is a foreign block and is
        // never removed (the check T-A21-09).
        String wrongBlock = template.palette().wall();
        context.world = FakeWorld.withSite(origin);
        context.world.put(origin.add(2, 0, 2), ObservedBlock.of(wrongBlock, Map.of(), true, false, false));
        context.inventory.put("minecraft:stone_bricks", 1);
        context.registry.add(buildingRecord("h1", context, origin, 48));
        // The block of the cell is read before the automation acts on it.
        ObservedBlock wrong = context.world.read(origin.add(2, 0, 2));
        AutomationController controller = new AutomationController();
        controller.enable();
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");
        controller.tick(context);

        // The rule of the domain says that the cell may be repaired.
        Cell cell = new Cell(2, 0, 2);
        RepairDecision decision = RepairPolicy.classify(cell, new RepairPolicy.Facts(
                template.spec(cell), wrong, true, false, false, template.isProjectMaterial(wrong.blockId())));
        eq(RepairDecision.Kind.REPAIR, decision.kind(), "a wrong block inside the volume may be repaired");

        // The client is asked for the removal of exactly this cell and nothing is placed yet.
        eq(1, context.repairRequests.size(), "one repair is asked for");
        eq(origin.add(2, 0, 2), context.repairRequests.get(0), "the repair names the wrong cell");
        check(context.placementRequests.isEmpty(), "no block is placed while the wrong block is there");
        eq(AutomationState.BUILDING, controller.state(), "the house is not suspended by the wrong block");

        // The world has the cell empty: the cell is read again and the block of the project is
        // placed.
        controller.tick(context);
        check(context.placementRequests.size() == 1,
                "the block of the project is placed after the repair: " + context.placementRequests);
        check(context.placementRequests.get(0).startsWith("minecraft:stone_bricks"),
                "the floor block of the project is placed: " + context.placementRequests.get(0));
        eq(AutomationState.BUILDING, controller.state(), "the house continues after the repair");
        check(!wrongBlock.equals(context.world.read(origin.add(2, 0, 2)).blockId()),
                "the wrong block is not in the world any more");
        check(context.observations.stream().anyMatch(o -> o.startsWith("repair_requested|")),
                "the repair is written into the log of the client");
    }

    /** The protected cells are never removed (ТЗ.md section 4). */
    private static void protectedCellsAreNotRepaired() {
        ObservedBlock stone = ObservedBlock.of("minecraft:stone", Map.of(), true, false, false);
        ObservedBlock dirt = ObservedBlock.of("minecraft:dirt", Map.of(), true, false, false);
        ObservedBlock chest = ObservedBlock.of("minecraft:chest", Map.of(), true, false, false, true);

        // The support under the house and every block outside the volume of the house.
        eq(RepairDecision.Kind.PROTECTED, RepairPolicy.classify(new Cell(2, -1, 2),
                new RepairPolicy.Facts(null, stone, true, false, false, false)).kind(),
                "the support under the house is protected");
        eq(RepairDecision.Kind.PROTECTED, RepairPolicy.classify(new Cell(7, 0, 2),
                new RepairPolicy.Facts(null, dirt, true, false, false, false)).kind(),
                "the block outside the volume of the house is protected");

        // A container is protected: the client does not read the content of a container.
        eq(RepairDecision.Kind.PROTECTED, RepairPolicy.classify(new Cell(2, 0, 2),
                new RepairPolicy.Facts(HouseTemplate.forVariant(0).spec(new Cell(2, 0, 2)), chest,
                        true, false, false, false)).kind(), "a container is protected");

        // The house with a container in its volume is suspended with the coordinate and the reason,
        // and the container is not touched.
        FakeContext context = new FakeContext();
        Vec3i origin = new Vec3i(100, 64, 200);
        context.world = FakeWorld.withSite(origin);
        context.world.put(origin.add(2, 0, 2), chest);
        context.registry.add(buildingRecord("h1", context, origin, 48));
        AutomationController controller = new AutomationController();
        controller.enable();
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");
        controller.tick(context);
        eq(AutomationState.SUSPENDED, controller.state(), "the house is suspended by the container");
        eq("obstructed", controller.reason(), "the reason of the suspension");
        check(context.repairRequests.isEmpty(), "no repair is asked for a container");
        check("minecraft:chest".equals(context.world.read(origin.add(2, 0, 2)).blockId()),
                "the container is still in the world");
        String homes = controller.homesJson(context.registry);
        check(homes.contains("obstructed@102,64,202"), "the record names the coordinate: " + homes);
        check(homes.contains("the block is a container"), "the record names the reason: " + homes);
    }

    /** A repair that is not applicable is refused with the coordinate and the reason. */
    private static void inapplicableRepairIsRefused() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        // The lower half of a door in the top layer of the volume: the partner of the half would
        // stand above the house.
        Cell roof = new Cell(2, 4, 2);
        ObservedBlock half = ObservedBlock.of("minecraft:spruce_door",
                Map.of("half", "lower", "facing", "south", "hinge", "left"), true, false, false);
        RepairDecision outer = RepairPolicy.classify(roof,
                new RepairPolicy.Facts(template.spec(roof), half, false, false, false,
                        template.isProjectMaterial(half.blockId())));
        eq(RepairDecision.Kind.REFUSE, outer.kind(), "the outer half of a multi-block item is refused");
        eq(RepairPolicy.REASON_EXTERNAL_HALF, outer.reason(), "the reason of the refusal");
        eq(new Cell(2, 5, 2), RepairPolicy.partnerCell(roof, half),
                "the partner of the lower half of a door stands above it");

        // A cell next to a liquid: the repair could spill the liquid.
        Cell wet = new Cell(2, 0, 2);
        ObservedBlock wrong = ObservedBlock.of(template.palette().wall(), Map.of(), true, false, false);
        RepairDecision spill = RepairPolicy.classify(wet, new RepairPolicy.Facts(template.spec(wet),
                wrong, true, false, true, template.isProjectMaterial(wrong.blockId())));
        eq(RepairDecision.Kind.REFUSE, spill.kind(), "a cell next to a liquid is refused");
        eq(RepairPolicy.REASON_LIQUID, spill.reason(), "the reason of the refusal");

        // The house with a wrong block next to water is suspended and the block stays.
        FakeContext context = new FakeContext();
        Vec3i origin = new Vec3i(100, 64, 200);
        context.world = FakeWorld.withSite(origin);
        context.world.put(origin.add(2, 0, 2), wrong);
        context.world.liquid(origin.add(3, 0, 2), "minecraft:water");
        context.registry.add(buildingRecord("h1", context, origin, 48));
        AutomationController controller = new AutomationController();
        controller.enable();
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");
        controller.tick(context);
        eq(AutomationState.SUSPENDED, controller.state(), "the house is suspended by the liquid");
        eq("obstructed", controller.reason(), "the reason of the suspension");
        check(context.repairRequests.isEmpty(), "no repair is asked for a cell next to a liquid");
        check(wrong.blockId().equals(context.world.read(origin.add(2, 0, 2)).blockId()),
                "the wrong block is still in the world");
        String homes = controller.homesJson(context.registry);
        check(homes.contains("obstructed@102,64,202"), "the record names the coordinate: " + homes);
        check(homes.contains("could spill a liquid"), "the record names the reason: " + homes);
    }

    /** The repair is asked for one cell at a time and only for the chosen cell. */
    private static void repairIsAskedForOneCell() {
        FakeContext context = new FakeContext();
        Vec3i origin = new Vec3i(100, 64, 200);
        context.world = FakeWorld.withSite(origin);
        context.world.put(origin.add(2, 0, 2),
                ObservedBlock.of(HouseTemplate.forVariant(0).palette().wall(), Map.of(), true, false, false));
        context.registry.add(buildingRecord("h1", context, origin, 48));
        AutomationController controller = new AutomationController();
        controller.enable();
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");
        controller.tick(context);
        eq(1, context.repairRequests.size(), "one repair is asked for");
        eq(origin.add(2, 0, 2), context.repairRequests.get(0), "the repair names the chosen cell");
        // The cells around the wrong cell are not touched.
        check("minecraft:air".equals(context.world.read(origin.add(1, 0, 2)).blockId()),
                "the neighbour of the wrong cell is not touched");
        check("minecraft:stone".equals(context.world.read(origin.add(2, -1, 2)).blockId()),
                "the support under the wrong cell is not touched");
        check("minecraft:air".equals(context.world.read(origin.add(2, 1, 2)).blockId()),
                "the cell above the wrong cell is not touched");
    }

    /** The house waits for the removal and suspends with a concrete reason when the block stays. */
    private static void repairWaitsForTheWorld() {
        FakeContext context = new FakeContext();
        Vec3i origin = new Vec3i(100, 64, 200);
        HouseTemplate template = HouseTemplate.forVariant(0);
        String wrongBlock = template.palette().wall();
        context.world = FakeWorld.withSite(origin);
        context.world.put(origin.add(2, 0, 2), ObservedBlock.of(wrongBlock, Map.of(), true, false, false));
        context.repairRemovesBlock = false;
        context.registry.add(buildingRecord("h1", context, origin, 48));
        AutomationController controller = new AutomationController();
        controller.enable();
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");
        controller.tick(context);
        eq(AutomationState.BUILDING, controller.state(), "the house waits for the removal of the block");
        eq(1, context.repairRequests.size(), "the repair is asked for once");
        // The house waits as long as the budget of the repair allows: the player may have to walk
        // around the house to the wrong cell, and the budget is a minute now
        // (AutomationController.REPAIR_TIMEOUT_MILLIS).
        for (int i = 0; i < 2000 && controller.state() != AutomationState.SUSPENDED; i++) {
            context.advance(50L);
            controller.tick(context);
        }
        eq(AutomationState.SUSPENDED, controller.state(), "the house is suspended when the block stays");
        eq("obstructed", controller.reason(), "the reason of the suspension");
        String homes = controller.homesJson(context.registry);
        check(homes.contains("obstructed@102,64,202"), "the record names the coordinate: " + homes);
        check(homes.contains("the wrong block was not removed"), "the record names the reason: " + homes);
        check(wrongBlock.equals(context.world.read(origin.add(2, 0, 2)).blockId()),
                "the wrong block is still in the world");
    }

    /** A single half of a door is eliminated and the pair is placed again. */
    private static void singleHalfIsNotOverwritten() {
        FakeContext context = new FakeContext();
        Vec3i origin = new Vec3i(100, 64, 200);
        context.world = FakeWorld.withSite(origin);
        // The house stands in the world and the upper half of the door is missing.
        context.world.applyHouse(HouseTemplate.forVariant(0), origin);
        context.world.air(origin.add(3, 2, 0));
        context.inventory.put("minecraft:spruce_door", 1);
        context.registry.add(buildingRecord("h1", context, origin, 179));
        AutomationController controller = new AutomationController();
        controller.enable();
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");
        controller.tick(context);
        // The half is not overwritten: it is eliminated with the rules of the safe repair.
        eq(1, context.repairRequests.size(), "one repair is asked for");
        eq(origin.add(3, 1, 0), context.repairRequests.get(0), "the repair names the half of the door");
        check(context.placementRequests.isEmpty(), "no block is placed over the half of the door");
        controller.tick(context);
        check(context.placementRequests.size() == 1
                        && context.placementRequests.get(0).startsWith("minecraft:spruce_door"),
                "the pair of the door is placed again: " + context.placementRequests);
        check(context.placementOperations.size() == 1
                        && context.placementOperations.get(0).cells().size() == 2,
                "the door is placed with one item for two cells");
        eq(AutomationState.BUILDING, controller.state(), "the house continues after the repair");
    }

    /**
     * The client breaks a block only in the chosen repair operation.
     *
     * <p>The source of the client is read: the ordinary attack is sent in the repair of a wrong
     * cell and in the removal of the four allowed plants, and in no other place (R28, C90).
     */
    /**
     * The client breaks a block only in the chosen repair operation.
     *
     * <p>The scan covers every source of the client: the repair of a wrong cell and the removal
     * of the allowed plants are the only places that break a block, and they do it with the
     * ordinary interaction of the player. The laboratory of the platform (the fixture) changes the
     * world of the server, but it belongs to the preparation and not to the product, therefore it
     * is named separately.
     */
    private static void clientBreaksOnlyInTheRepair() throws Exception {
        String executor = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        String repair = between(executor, "private void repairTick()", "private BlockPos repairAccess()");
        String removal = between(executor, "private void removalTick()", "private void exitTick()");
        check(repair.contains("interactionManager.updateBlockBreakingProgress("),
                "the repair breaks the block with the ordinary destruction of the game");
        check(repair.contains("swingHand("), "the repair swings the hand of the player");
        check(repair.contains("hit.getBlockPos().equals(position)"),
                "the repair breaks only the block that the game itself names under the crosshair");
        check(removal.contains("interactionManager.attackBlock("),
                "the removal of the plants breaks them with an ordinary attack of the player");
        eq(1, occurrences(executor, ".attackBlock("),
                "the client attacks a block with the api of the game only in the removal of the plants");
        eq(2, occurrences(executor, ".updateBlockBreakingProgress("),
                "the ordinary destruction of the game is used in the repair and in the removal of "
                        + "the temporary support of the climb");
        String support = between(executor, "private boolean removeScaffoldTick()",
                "private BlockPos scaffoldAccess(");
        check(support.contains("interactionManager.updateBlockBreakingProgress("),
                "the third break belongs to the temporary support that the add-on built itself");
        check(support.contains("hit.getBlockPos().equals(position)"),
                "the support is broken only when the game itself names it under the crosshair");
        // Every other source of the client breaks no block and changes no block of the world.
        Path client = projectRoot().resolve("src/main/java/dev/magehome/client");
        List<Path> sources = new ArrayList<>();
        try (var files = Files.list(client)) {
            files.filter(path -> path.getFileName().toString().endsWith(".java")).forEach(sources::add);
        }
        check(!sources.isEmpty(), "the sources of the client were found");
        for (Path source : sources) {
            String name = source.getFileName().toString();
            if ("PlacementExecutor.java".equals(name) || "LabScenarioFixture.java".equals(name)) {
                // The executor holds the chosen repair operation; the fixture belongs to the
                // laboratory of the platform and prepares the world of the server.
                continue;
            }
            String text = Files.readString(source, StandardCharsets.UTF_8);
            check(!text.contains(".attackBlock("), name + " does not break a block");
            check(!text.contains(".updateBlockBreakingProgress("), name + " does not break a block");
            check(!text.contains("breakBlock("), name + " does not break a block with the api of the game");
            check(!text.contains("setBlockState("), name + " does not change the world directly");
        }
    }

    /** A safe wrong block is repairable irrespective of whether it is a project material. */
    private static void foreignBlockIsRepaired() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Cell cell = new Cell(2, 0, 2);
        ObservedBlock tree = ObservedBlock.of("minecraft:oak_log", Map.of("axis", "y"), true, false, false);
        ObservedBlock crop = ObservedBlock.of("minecraft:wheat", Map.of("age", "0"), false, false, false);
        for (ObservedBlock block : List.of(tree, crop)) {
            check(!template.isProjectMaterial(block.blockId()), "the block is not a project material");
            RepairDecision decision = RepairPolicy.classify(cell, new RepairPolicy.Facts(
                    template.spec(cell), block, true, false, false, false));
            eq(RepairDecision.Kind.REPAIR, decision.kind(), "a safe wrong block can be repaired");
        }
        FakeContext context = new FakeContext();
        Vec3i origin = new Vec3i(100, 64, 200);
        context.world = FakeWorld.withSite(origin);
        context.world.put(origin.add(2, 0, 2), tree);
        context.registry.add(buildingRecord("h1", context, origin, 48));
        AutomationController controller = new AutomationController();
        controller.enable();
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");
        controller.tick(context);
        eq(List.of(origin.add(2, 0, 2)), context.repairRequests, "only the wrong block is selected");
        eq("minecraft:stone", context.world.read(origin.add(2, -1, 2)).blockId(), "support remains intact");
    }
    /**
     * The repair belongs to a house in the state building (ТЗ.md section 5).
     *
     * <p>A ready or a damaged house is never repaired on its own: the automation does not touch
     * it, and it waits for an explicit resume, which puts the record of the house back into the
     * state building. Only then the repair of a wrong cell belongs to the house.
     */
    private static void repairBelongsToABuildingHouse() {
        check(RepairPolicy.repairApplies(HouseState.BUILDING), "the repair belongs to a house in the state building");
        check(!RepairPolicy.repairApplies(HouseState.READY), "a ready house is not repaired on its own");
        check(!RepairPolicy.repairApplies(HouseState.DAMAGED), "a damaged house is not repaired on its own");
        check(!RepairPolicy.repairApplies(HouseState.SUSPENDED), "a suspended house is not repaired on its own");

        // A damaged house with a wrong block in its volume: the automation does not repair it.
        FakeContext context = new FakeContext();
        Vec3i origin = new Vec3i(100, 64, 200);
        HouseTemplate template = HouseTemplate.forVariant(0);
        context.world = FakeWorld.withSite(origin);
        context.world.put(origin.add(2, 0, 2),
                ObservedBlock.of(template.palette().wall(), Map.of(), true, false, false));
        // The player stands in the service radius of the house: the automation waits and does not
        // touch the record of the house.
        context.player = origin;
        HouseRecord record = buildingRecord("h1", context, origin, 48);
        record.setState(HouseState.DAMAGED);
        record.setReason("the house does not correspond to the project");
        context.registry.add(record);
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        check(context.repairRequests.isEmpty(),
                "no repair is asked for a house that is not in the state building: "
                        + context.repairRequests);
        check(context.placementRequests.isEmpty(), "no block is placed in a house that is not built");
        eq(HouseState.DAMAGED, record.state(), "the record of the damaged house keeps its state");
        eq("the house does not correspond to the project", record.reason(),
                "the record of the damaged house keeps its reason");

        // The explicit resume puts the record into the state building: the repair belongs to the
        // house from now on.
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");
        eq(HouseState.BUILDING, record.state(), "the record of the house is in the state building");
        controller.tick(context);
        eq(1, context.repairRequests.size(),
                "the repair of the wrong cell is asked for after the explicit resume: "
                        + context.repairRequests);
        eq(origin.add(2, 0, 2), context.repairRequests.get(0), "the repair names the wrong cell");
    }

    /**
     * The repair uses the ordinary destruction of the game and names its facts.
     *
     * <p>The client starts the destruction of the block with the api of the game, keeps the crosshair
     * on the chosen cell, records the attack and the progress of the destruction, and finishes the
     * repair only when the world shows the empty cell.
     */
    private static void repairUsesTheOrdinaryDestruction() throws Exception {
        String executor = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        String repair = between(executor, "private void repairTick()", "private BlockPos repairAccess()");
        check(repair.contains("interactionManager.updateBlockBreakingProgress(position, hit.getSide())"),
                "the repair uses the ordinary destruction of the game for the chosen cell");
        check(repair.contains("hit.getBlockPos().equals(position)"),
                "the repair breaks only the cell that the game names under the crosshair");
        check(repair.contains("getBlockBreakingProgress()"), "the repair reads the progress of the destruction");
        check(repair.contains("repair_attack:"), "the repair records the attack of the player");
        check(repair.contains("repair_progress:"), "the repair records the progress of the destruction");
        check(repair.contains("repair_finished:"), "the repair records the end of the repair");
        check(repair.contains("repairEvents.add(\"repair_finished:\""),
                "the repair finishes only when the world shows the empty cell");
        check(repair.contains("cancelBlockBreaking()"),
                "the repair gives the destruction of the block up when it ends");
    }

    /** The search of a site is not weakened by the repair (ТЗ.md section 3 and 4). */
    private static void siteSearchIsNotWeakened() {
        Vec3i origin = new Vec3i(0, 64, 0);
        FakeWorld world = FakeWorld.withSite(origin);
        world.put(origin.add(2, 0, 2), ObservedBlock.of("minecraft:dirt", Map.of(), true, false, false));
        SiteEvaluator.Result result = SiteEvaluator.evaluate(origin, world);
        check(!result.suitable(), "a candidate with a wrong block inside is not a site");
        eq(SiteEvaluator.Problem.VOLUME_BLOCKED, result.problem(), "the reason of the rejection");
        FakeWorld tree = FakeWorld.withSite(origin);
        tree.put(origin.add(2, 0, 2), ObservedBlock.of("minecraft:oak_log", Map.of("axis", "y"), true, false, false));
        check(!SiteEvaluator.evaluate(origin, tree).suitable(), "a tree is not removed by the search");
    }

    /** The number of the occurrences of a text in a text. */
    private static int occurrences(String text, String part) {
        int count = 0;
        int index = text.indexOf(part);
        while (index >= 0) {
            count++;
            index = text.indexOf(part, index + part.length());
        }
        return count;
    }

    private static Vec3i world(Vec3i origin, Cell cell) {
        return origin.add(cell.x(), cell.y(), cell.z());
    }

    private static Vec3i firstCandidate(Vec3i base) {
        return new SiteSearch(base).nextBatch().get(0);
    }

    private static void unknownTemplateRejected() {
        for (String templateId : new String[] {"unknown-future-template", null}) {
            FakeContext context = new FakeContext();
            Vec3i origin = new Vec3i(100, 64, 200);
            context.world = FakeWorld.withSite(origin);
            context.world.solid(origin.add(2, 0, 2), "minecraft:dirt");
            HouseRecord house = new HouseRecord("h1", context.worldKey, context.dimension, context.playerUuid,
                    origin, 0, HouseState.SUSPENDED, "placement", 17, null, "interrupted");
            if (templateId != null) house.setTemplateId(templateId);
            context.registry.add(house);
            String before = context.registry.toJson();
            Map<Vec3i, ObservedBlock> blocks = new HashMap<>();
            for (int x = -1; x <= 7; x++) {
                for (int y = -1; y <= 5; y++) {
                    for (int z = -1; z <= 7; z++) {
                        Vec3i position = origin.add(x, y, z);
                        blocks.put(position, context.world.read(position));
                    }
                }
            }
            AutomationController controller = new AutomationController();
            controller.enable();
            eq(ApiResponses.rejected("unknown_template"), controller.resume("h1", context), "template rejection");
            eq(before, context.registry.toJson(), "registry unchanged");
            eq(0, context.saves, "no registry writes");
            check(controller.currentHouse() == null, "no house selected");
            check(context.placementRequests.isEmpty() && context.repairRequests.isEmpty()
                    && context.plantRemovals.isEmpty() && context.frontPointRequests.isEmpty()
                    && context.sentCommands.isEmpty(), "no world or navigation actions");
            for (Map.Entry<Vec3i, ObservedBlock> entry : blocks.entrySet()) {
                eq(entry.getValue(), context.world.read(entry.getKey()), "world unchanged at " + entry.getKey());
            }
        }
    }

    private static HouseRecord record(String houseId, String worldKey, String dimension, String uuid) {
        return record(houseId, worldKey, dimension, uuid, new Vec3i(100, 64, 200));
    }

    /**
     * The record of a house that is being built and that already stands in the world.
     *
     * <p>The record has confirmed cells, therefore the site is not searched again: the repair of
     * a wrong cell belongs to a registered house (ТЗ.md section 4).
     */
    private static HouseRecord buildingRecord(String houseId, FakeContext context, Vec3i origin,
                                               int confirmedCells) {
        HouseRecord record = record(houseId, context.worldKey, context.dimension, context.playerUuid, origin);
        record.setState(HouseState.BUILDING);
        record.setConfirmedCells(confirmedCells);
        return record;
    }

    private static HouseRecord record(String houseId, String worldKey, String dimension, String uuid, Vec3i origin) {
        HouseRecord record = new HouseRecord(houseId, worldKey, dimension, uuid, origin, 0, HouseState.BUILDING, "placement", 0,
                null, null);
        record.setTemplateId(AutomationController.CURRENT_TEMPLATE_ID);
        return record;
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void eq(Object expected, Object actual, String message) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(message + " (expected " + expected + ", got " + actual + ")");
        }
    }

    // --- the helpers of the new checks ----------------------------------------------------------------

    /** Reads a file of the client from the root of the project. */
    private static String clientSource(String relativePath) throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        Path file = root.resolve(relativePath);
        check(Files.isRegularFile(file), "the file " + relativePath + " exists");
        return Files.readString(file, StandardCharsets.UTF_8);
    }

    /** The part of a text between two markers. */
    private static String between(String text, String from, String to) {
        int start = text.indexOf(from);
        check(start >= 0, "the marker " + from + " was found");
        int end = text.indexOf(to, start + from.length());
        check(end > start, "the end of the block " + from + " was found");
        return text.substring(start, end);
    }

    // --- the stand of the acceptance scenarios ------------------------------------------------------

    /**
     * A precondition of a scenario: the rule of the core is exercised and the harness of the game
     * is read, so that the checked rule is the one the harness really applies and not a copy.
     */
    private static void standPrecondition(String scenario, String precondition, String ruleCall, Runnable rule) {
        rule.run();
        try {
            String harness = clientSource("src/main/java/dev/magehome/client/AcceptanceRunner.java");
            check(harness.contains("\"" + scenario + "." + precondition + "\""),
                    "the harness registers the precondition " + scenario + "." + precondition);
            check(harness.contains(ruleCall), "the harness applies the rule of the core: " + ruleCall);
        } catch (Exception e) {
            throw new AssertionError("the harness of the game could not be read: " + e.getMessage());
        }
    }

    private static void readinessRule() {
        eq(StandRules.Readiness.READY, StandRules.readiness(true, true, true, true, true, true),
                "the stand is ready when the world, the player and UnionClef are there");
        eq(StandRules.Readiness.NO_WORLD, StandRules.readiness(false, true, true, true, true, true),
                "the stand is not ready without a world");
        eq(StandRules.Readiness.NO_PLAYER, StandRules.readiness(true, false, true, true, true, true),
                "the stand is not ready with a dead player");
        eq(StandRules.Readiness.NO_PLAYER_ID, StandRules.readiness(true, true, false, true, true, true),
                "the stand is not ready without the uuid of the player");
        eq(StandRules.Readiness.UNIONCLEF_MISSING, StandRules.readiness(true, true, true, false, true, true),
                "the stand is not ready without UnionClef");
        eq(StandRules.Readiness.UNIONCLEF_CHAIN_MISSING, StandRules.readiness(true, true, true, true, false, true),
                "the stand is not ready while the chain of UnionClef does not exist yet");
        eq(StandRules.Readiness.UNIONCLEF_SLOT_HANDLER_MISSING,
                StandRules.readiness(true, true, true, true, true, false),
                "the stand is not ready while the slot handler of UnionClef does not exist yet");
    }

    /**
     * The status of the automation shows that it waits for the command tree of the server.
     *
     * <p>ТЗ.md section 6 asks the status to reflect the actual state of the work. While the tree
     * of the server was not read, the automation waits; it does not stay in the state building
     * with an empty reason.
     */
    private static void unknownCommandTreeStatus() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        context.giveState = GiveAvailability.UNKNOWN;
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        context.advance(50);
        controller.tick(context);
        eq(AutomationState.WAITING, controller.state(),
                "the status shows that the automation waits while the tree of the server was not read");
        eq("give_unknown", controller.reason(), "the reason of the wait is give_unknown");
        check(context.sentCommands.isEmpty(), "no material is asked while the tree of the server was not read");

        // The tree arrives: the status shows the work of the house again.
        context.giveState = GiveAvailability.AVAILABLE;
        controller.tick(context);
        eq(AutomationState.BUILDING, controller.state(),
                "the status shows the building when the tree of the server is known");
        check(!"give_unknown".equals(controller.reason()),
                "the reason of the wait is gone when the tree of the server is known");
    }

    /**
     * The observation of the unread command tree names the tick.
     *
     * <p>Without the number of the tick a tree that arrives late cannot be told apart from a
     * tree that never arrives.
     */
    private static void unknownCommandTreeObservation() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        context.giveState = GiveAvailability.UNKNOWN;
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        context.advance(50);
        controller.tick(context);
        boolean found = false;
        for (String observation : context.observations) {
            if (!observation.startsWith("give_unknown|")) {
                continue;
            }
            found = true;
            check(observation.contains("tick="),
                    "the observation of the unread tree names the tick: " + observation);
        }
        check(found, "the automation wrote the observation of the unread tree: " + context.observations);
    }

    /**
     * A chain that another owner holds is no failure of the add-on.
     *
     * <p>ТЗ.md section 5: the add-on has to wait while another owner holds the chain and it may
     * not take that chain away. The add-on takes the chain only when it is empty.
     *
     * <p>Раунд 6: правило судит поведение аддона (запись его вызовов на цепочке) вместе с
     * состояниями цепочки. Прежняя проверка считала любое отношение FOREIGN провалом и любое
     * появление своего отношения нарушением: цепочка UnionClef меняется внутри своего тика
     * задач, поэтому чужая задача, которая закончилась, и своя, которая началась в том же тике,
     * видны как переход без пустой цепочки между ними. Такой переход у корректного продукта не
     * является нарушением, если запись вызовов объясняет его.
     */
    private static void foreignOwnerOfTheChain() {
        List<TaskChainOwnership.Sample> foreign = List.of(
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE),
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE));
        TaskChainOwnership.Verdict waiting = TaskChainOwnership.judge(foreign, List.of());
        eq(TaskChainOwnership.Verdict.WAITING_FOR_FOREIGN, waiting,
                "a chain that another owner holds is no failure of the add-on");
        check(waiting.accepted(), "the add-on behaves correctly while another owner holds the chain");
        check(!waiting.verified(), "the own root task of the add-on was not observed in that window");

        // The change of the owner is explained by the call of the add-on: the chain was empty
        // when the add-on started its own task.
        List<TaskChainOwnership.Sample> released = List.of(
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.FINISHED),
                sample(TaskOwnership.Relation.NONE, TaskChainOwnership.OwnerState.UNKNOWN),
                sample(TaskOwnership.Relation.OWN_ROOT, TaskChainOwnership.OwnerState.ALIVE));
        List<TaskChainOwnership.AddOnAction> start = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.START_OWN_TASK,
                        TaskChainOwnership.Held.CHAIN_EMPTY));
        eq(TaskChainOwnership.Verdict.OWN_ROOT_HELD, TaskChainOwnership.judge(released, start),
                "the add-on takes the chain when the other owner released it");
        check(TaskChainOwnership.judge(released, start).verified(),
                "the own root task of the add-on was the root of the chain");

        // The same jump without a call of the add-on is not explained by the observation.
        eq(TaskChainOwnership.Verdict.OBSERVATION_INCOMPLETE,
                TaskChainOwnership.judge(released, List.of()),
                "a change of the owner without a call of the add-on explains nothing");

        // A call while the chain holds the task of another owner takes the chain away.
        List<TaskChainOwnership.AddOnAction> take = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.START_OWN_TASK,
                        TaskChainOwnership.Held.FOREIGN));
        eq(TaskChainOwnership.Verdict.FOREIGN_DISPLACED, TaskChainOwnership.judge(released, take),
                "the add-on took the chain from the task of another owner");
        check(!TaskChainOwnership.judge(released, take).accepted(),
                "taking the chain of another owner is a failure of the add-on");
    }

    /** The add-on starts its own task when the chain of UnionClef is empty. */
    private static void emptyChainOfUnionClef() {
        List<TaskChainOwnership.Sample> empty = List.of(
                sample(TaskOwnership.Relation.NONE, TaskChainOwnership.OwnerState.UNKNOWN),
                sample(TaskOwnership.Relation.NONE, TaskChainOwnership.OwnerState.UNKNOWN));
        eq(TaskChainOwnership.Verdict.EMPTY_CHAIN_IGNORED, TaskChainOwnership.judge(empty, List.of()),
                "the add-on has to start its own task in an empty chain");
        check(!TaskChainOwnership.judge(empty, List.of()).accepted(),
                "an empty chain that the add-on ignores is a failure of the add-on");

        // An empty chain that is followed by a foreign owner is not lost: the duty of the
        // add-on stays, even when another owner holds the chain later in the window.
        List<TaskChainOwnership.Sample> mixed = List.of(
                sample(TaskOwnership.Relation.NONE, TaskChainOwnership.OwnerState.UNKNOWN),
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE));
        eq(TaskChainOwnership.Verdict.EMPTY_CHAIN_IGNORED, TaskChainOwnership.judge(mixed, List.of()),
                "an empty chain that the add-on ignores is a failure of the add-on, also when a foreign owner "
                        + "comes later in the window");

        List<TaskChainOwnership.Sample> unavailable = List.of(
                sample(TaskOwnership.Relation.CHAIN_UNAVAILABLE, TaskChainOwnership.OwnerState.UNKNOWN));
        eq(TaskChainOwnership.Verdict.CHAIN_UNAVAILABLE, TaskChainOwnership.judge(unavailable, List.of()),
                "a chain that does not exist yet is a fact of the environment");
        check(!TaskChainOwnership.judge(unavailable, List.of()).verified(),
                "the add-on cannot own a task while the chain of UnionClef does not exist");
        check(TaskChainOwnership.judge(unavailable, List.of()).accepted(),
                "a chain that does not exist yet is no failure of the add-on");
    }

    /**
     * The change of the owner of the chain has to be explained by a call of the add-on.
     *
     * <p>Раунд 6: без записи вызовов аддона переход от чужого отношения к своему нельзя ни
     * считать нарушением (корректный продукт так выглядит, когда чужая задача заканчивается и
     * своя начинается в одном тике), ни считать доказанным поведением.
     */
    private static void changeOfTheOwnerNeedsACall() {
        List<TaskChainOwnership.Sample> jump = List.of(
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE),
                sample(TaskOwnership.Relation.OWN_ROOT, TaskChainOwnership.OwnerState.ALIVE));
        TaskChainOwnership.Verdict unexplained = TaskChainOwnership.judge(jump, List.of());
        eq(TaskChainOwnership.Verdict.OBSERVATION_INCOMPLETE, unexplained,
                "a change of the owner without a call of the add-on proves nothing");
        check(!unexplained.violation(), "an unexplained change of the owner is no failure of the add-on");
        check(!unexplained.verified(), "an unexplained change of the owner proves nothing either");

        List<TaskChainOwnership.AddOnAction> start = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.START_OWN_TASK,
                        TaskChainOwnership.Held.CHAIN_EMPTY));
        eq(TaskChainOwnership.Verdict.OWN_ROOT_HELD, TaskChainOwnership.judge(jump, start),
                "the change of the owner is lawful when the chain was empty when the add-on started its task");
    }

    /**
     * The own root task of the add-on is the only root of its actions.
     *
     * <p>ТЗ.md section 5: all the actions of the add-on are one root task of UnionClef. A chain
     * that holds another task above the own root task does not satisfy that, and the harness of
     * the game and the rule judge the same thing.
     */
    private static void ownRootIsTheOnlyRoot() {
        List<TaskChainOwnership.Sample> below = List.of(
                sample(TaskOwnership.Relation.OWN_DESCENDANT, TaskChainOwnership.OwnerState.ALIVE));
        eq(TaskChainOwnership.Verdict.OWN_ROOT_NOT_HELD, TaskChainOwnership.judge(below, List.of()),
                "the own root task of the add-on was never the root of the chain");
        check(!TaskChainOwnership.judge(below, List.of()).accepted(),
                "a chain that holds a task above the own root task is a failure of the add-on");
        check(!TaskChainOwnership.judge(below, List.of()).verified(),
                "the own root task of the add-on was not observed as the root of the chain");
    }

    /**
     * The task of another owner is neither taken away nor stopped by the add-on.
     *
     * <p>Раунд 6: состояние задачи чужого владельца внутри окна наблюдения различается по
     * {@code Task.stopped()} (задачу остановили) и {@code Task.isFinished()} (задача закончилась
     * сама). Запись вызовов аддона решает, кто остановил задачу.
     */
    private static void foreignTaskIsNotStopped() {
        List<TaskChainOwnership.Sample> alive = List.of(
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE),
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE));
        List<TaskChainOwnership.AddOnAction> lawful = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.START_OWN_TASK,
                        TaskChainOwnership.Held.CHAIN_EMPTY));
        eq(TaskChainOwnership.ForeignFate.ALIVE, TaskChainOwnership.foreignFate(alive, lawful),
                "the task of the other owner was alive and the add-on did not touch it");
        check(!TaskChainOwnership.foreignFate(alive, lawful).harmedByTheAddOn(),
                "the add-on did not harm the task of the other owner");

        List<TaskChainOwnership.Sample> ended = List.of(
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE),
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.FINISHED));
        eq(TaskChainOwnership.ForeignFate.ENDED_BY_ITSELF, TaskChainOwnership.foreignFate(ended, lawful),
                "the task of the other owner ended by itself");

        List<TaskChainOwnership.Sample> stopped = List.of(
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE),
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.STOPPED));
        eq(TaskChainOwnership.ForeignFate.STOPPED_BY_ANOTHER_ACTOR,
                TaskChainOwnership.foreignFate(stopped, lawful),
                "the task of the other owner was stopped by somebody else");

        List<TaskChainOwnership.AddOnAction> take = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.START_OWN_TASK,
                        TaskChainOwnership.Held.FOREIGN));
        eq(TaskChainOwnership.ForeignFate.STOPPED_BY_THE_ADD_ON, TaskChainOwnership.foreignFate(alive, take),
                "the add-on took the chain away from the task of another owner and stopped it");
        check(TaskChainOwnership.foreignFate(alive, take).harmedByTheAddOn(),
                "the add-on harmed the task of the other owner");
    }

    /**
     * The add-on does not stop the chain of UnionClef while it holds the task of another owner.
     *
     * <p>{@code TaskChain.stop()} stops the root task that the chain holds.
     */
    private static void stopOfAForeignChain() {
        List<TaskChainOwnership.Sample> foreign = List.of(
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE));
        List<TaskChainOwnership.AddOnAction> stop = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.STOP_CHAIN,
                        TaskChainOwnership.Held.FOREIGN));
        eq(TaskChainOwnership.Verdict.FOREIGN_TASK_STOPPED, TaskChainOwnership.judge(foreign, stop),
                "the add-on stopped the chain that held the task of another owner");
        check(!TaskChainOwnership.judge(foreign, stop).accepted(),
                "stopping the chain of another owner is a failure of the add-on");

        List<TaskChainOwnership.AddOnAction> own = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.STOP_CHAIN,
                        TaskChainOwnership.Held.OWN_ROOT));
        check(TaskChainOwnership.judge(foreign, own).accepted(),
                "stopping the chain that holds the own root task of the add-on is no failure");
    }

    /**
     * One criterion for the ownership of the chain.
     *
     * <p>Раунд 6: правило и харнесс игры должны судить одно и то же. Раньше правило считало
     * достаточным отношение OWN_DESCENDANT, а харнесс требовал наблюдения OWN_ROOT: два
     * оракула расходились. Теперь вердикт «своя корневая задача держит цепочку» возможен только
     * при наблюдении OWN_ROOT.
     */
    private static void oneCriterionForTheOwnership() {
        List<List<TaskChainOwnership.Sample>> windows = List.of(
                List.of(sample(TaskOwnership.Relation.NONE, TaskChainOwnership.OwnerState.UNKNOWN)),
                List.of(sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE)),
                List.of(sample(TaskOwnership.Relation.OWN_DESCENDANT, TaskChainOwnership.OwnerState.ALIVE)),
                List.of(sample(TaskOwnership.Relation.OWN_ROOT, TaskChainOwnership.OwnerState.ALIVE)),
                List.of(sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE),
                        sample(TaskOwnership.Relation.OWN_ROOT, TaskChainOwnership.OwnerState.ALIVE)));
        for (List<TaskChainOwnership.Sample> window : windows) {
            TaskChainOwnership.Verdict verdict = TaskChainOwnership.judge(window, List.of());
            boolean ownRootSeen = window.stream()
                    .anyMatch(sample -> sample.relation() == TaskOwnership.Relation.OWN_ROOT);
            if (verdict == TaskChainOwnership.Verdict.OWN_ROOT_HELD) {
                check(ownRootSeen, "the own root task was the root of the chain: " + window);
            } else {
                check(!verdict.verified(),
                        "the own root task was not the root of the chain: " + window + " -> " + verdict);
            }
        }
    }

    /**
     * The task of another owner that had already ended is not harmed by the add-on.
     *
     * <p>Раунд 6, ревью кода: цепочка UnionClef меняется внутри её собственного тика задач, поэтому
     * чужая задача может закончиться и своя начаться в одном тике, а выборка (один раз в клиентский
     * тик) не увидит промежуточной пустой цепочки. Если к моменту вызова аддона задача чужого
     * владельца уже закончилась сама ({@code Task.isFinished()}) или её остановили
     * ({@code Task.stopped()}), то аддон не забрал цепочку у живого владельца и никто не
     * пострадал: такой вызов не является нарушением. Вызов при живой чужой задаче — нарушение.
     */
    private static void endedForeignTaskIsNotHarmed() {
        List<TaskChainOwnership.Sample> jump = List.of(
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE),
                sample(TaskOwnership.Relation.OWN_ROOT, TaskChainOwnership.OwnerState.ALIVE));

        List<TaskChainOwnership.AddOnAction> ended = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.START_OWN_TASK,
                        TaskChainOwnership.Held.FOREIGN, TaskChainOwnership.OwnerState.FINISHED));
        eq(TaskChainOwnership.Verdict.OWN_ROOT_HELD, TaskChainOwnership.judge(jump, ended),
                "the foreign task had already ended: the add-on took an idle chain and holds its own task");
        eq(TaskChainOwnership.ForeignFate.ENDED_BY_ITSELF, TaskChainOwnership.foreignFate(jump, ended),
                "the task of the other owner ended by itself and the add-on did not harm it");

        List<TaskChainOwnership.AddOnAction> stopped = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.START_OWN_TASK,
                        TaskChainOwnership.Held.FOREIGN, TaskChainOwnership.OwnerState.STOPPED));
        eq(TaskChainOwnership.Verdict.OWN_ROOT_HELD, TaskChainOwnership.judge(jump, stopped),
                "the foreign task had been stopped by somebody else: the add-on took an idle chain");
        eq(TaskChainOwnership.ForeignFate.STOPPED_BY_ANOTHER_ACTOR, TaskChainOwnership.foreignFate(jump, stopped),
                "the task of the other owner was stopped by somebody else, not by the add-on");

        List<TaskChainOwnership.AddOnAction> alive = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.START_OWN_TASK,
                        TaskChainOwnership.Held.FOREIGN, TaskChainOwnership.OwnerState.ALIVE));
        eq(TaskChainOwnership.Verdict.FOREIGN_DISPLACED, TaskChainOwnership.judge(jump, alive),
                "the add-on took the chain away from the living task of another owner");
        eq(TaskChainOwnership.ForeignFate.STOPPED_BY_THE_ADD_ON, TaskChainOwnership.foreignFate(jump, alive),
                "the add-on stopped the living task of the other owner");

        List<TaskChainOwnership.AddOnAction> unknown = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.START_OWN_TASK,
                        TaskChainOwnership.Held.FOREIGN, TaskChainOwnership.OwnerState.UNKNOWN));
        eq(TaskChainOwnership.Verdict.FOREIGN_DISPLACED, TaskChainOwnership.judge(jump, unknown),
                "an unknown state of the foreign task is not assumed away: the call is a violation");
    }

    /** One observed state of the chain of UnionClef. */
    private static TaskChainOwnership.Sample sample(TaskOwnership.Relation relation,
                                                    TaskChainOwnership.OwnerState state) {
        return new TaskChainOwnership.Sample(relation, state);
    }

    /**
     * A mixed sequence of the states of the chain has one decided verdict.
     *
     * <p>Раунд 6, ревью кода: оракул должен решать смешанные последовательности наблюдения
     * однозначно, иначе харнесс игры и правило разойдутся. Проверяются три последовательности,
     * названные ревью:
     *
     * <ul>
     *   <li>{@code [NONE, FOREIGN]}: пустая цепочка — обязательное действие аддона, и чужая связь,
     *       встреченная позже, это действие не отменяет (вердикт {@code EMPTY_CHAIN_IGNORED});</li>
     *   <li>{@code [NONE, OWN_ROOT]}: аддон запустил свою задачу, когда цепочка была пуста, —
     *       законное поведение (вердикт {@code OWN_ROOT_HELD});</li>
     *   <li>{@code [CHAIN_UNAVAILABLE, OWN_ROOT]}: цепочка появилась и держит собственную корневую
     *       задачу аддона; с объясняющим вызовом это {@code OWN_ROOT_HELD}, а без записи вызовов
     *       наблюдение ничего не доказывает ({@code OBSERVATION_INCOMPLETE}).</li>
     * </ul>
     */
    private static void mixedWindowsOfTheObservation() {
        List<TaskChainOwnership.AddOnAction> lawful = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.START_OWN_TASK,
                        TaskChainOwnership.Held.CHAIN_EMPTY));

        List<TaskChainOwnership.Sample> emptyThenForeign = List.of(
                sample(TaskOwnership.Relation.NONE, TaskChainOwnership.OwnerState.UNKNOWN),
                sample(TaskOwnership.Relation.FOREIGN, TaskChainOwnership.OwnerState.ALIVE));
        eq(TaskChainOwnership.Verdict.EMPTY_CHAIN_IGNORED,
                TaskChainOwnership.judge(emptyThenForeign, List.of()),
                "an empty chain that the add-on ignores is a failure of the add-on, also when a foreign "
                        + "owner comes later in the window");

        List<TaskChainOwnership.Sample> emptyThenOwn = List.of(
                sample(TaskOwnership.Relation.NONE, TaskChainOwnership.OwnerState.UNKNOWN),
                sample(TaskOwnership.Relation.OWN_ROOT, TaskChainOwnership.OwnerState.ALIVE));
        eq(TaskChainOwnership.Verdict.OWN_ROOT_HELD,
                TaskChainOwnership.judge(emptyThenOwn, lawful),
                "the add-on starts its own task in an empty chain and holds it");
        eq(TaskChainOwnership.Verdict.OBSERVATION_INCOMPLETE,
                TaskChainOwnership.judge(emptyThenOwn, List.of()),
                "the own root task without a recorded call of the add-on explains nothing");

        List<TaskChainOwnership.Sample> unavailableThenOwn = List.of(
                sample(TaskOwnership.Relation.CHAIN_UNAVAILABLE, TaskChainOwnership.OwnerState.UNKNOWN),
                sample(TaskOwnership.Relation.OWN_ROOT, TaskChainOwnership.OwnerState.ALIVE));
        eq(TaskChainOwnership.Verdict.OWN_ROOT_HELD,
                TaskChainOwnership.judge(unavailableThenOwn, lawful),
                "the chain of UnionClef appeared and holds the own root task of the add-on");
        eq(TaskChainOwnership.Verdict.OBSERVATION_INCOMPLETE,
                TaskChainOwnership.judge(unavailableThenOwn, List.of()),
                "the chain of UnionClef appeared without a recorded call of the add-on: the observation "
                        + "explains nothing");
        check(!TaskChainOwnership.judge(unavailableThenOwn, List.of()).violation(),
                "an unexplained change of the owner is no failure of the add-on");
    }

    /**
     * The root task of the add-on never returns itself from its tick.
     *
     * <p>Раунд 7: байткод доставленного UnionClef 0.95.61 даёт точное условие падения клиента.
     * {@code Task.tick(TaskChain)} берёт задачу, которую вернул {@code onTick()}, как своего
     * ребёнка (у задачи без ребёнка {@code canBeInterrupted(null, task)} истинно) и тикает этого
     * ребёнка сразу после. Задача, которая возвращает саму себя, становится своим же ребёнком,
     * и тик цепочки рекурсирует до конца стека — это и есть {@code StackOverflowError} с
     * повторяющимися кадрами {@code Task.tick}, что наблюдалось в прогоне раунда 6.
     * Корневая задача аддона возвращала саму себя всякий раз, когда навигация не требовалась.
     *
     * <p>Проверяются правило ядра ({@code TaskProtocol}), запрет возврата корневой задачи в
     * исходниках клиента и то, что клиентский модуль пользуется именно этим правилом.
     */
    private static void rootTaskNeverReturnsItself() throws Exception {
        eq(TaskProtocol.Decision.NO_CHILD, TaskProtocol.decide(false, false, false),
                "a disabled automation holds no child task");
        eq(TaskProtocol.Decision.NO_CHILD, TaskProtocol.decide(true, true, true),
                "a suspended automation holds no child task");
        eq(TaskProtocol.Decision.NO_CHILD, TaskProtocol.decide(true, false, false),
                "the root task holds no child task when no navigation is wanted");
        eq(TaskProtocol.Decision.NAVIGATION_CHILD, TaskProtocol.decide(true, false, true),
                "the navigation task is the child of the root task");

        Object root = new Object();
        check(TaskProtocol.isOwnRoot(root, root), "the root task is recognized as itself");
        check(!TaskProtocol.isOwnRoot(new Object(), root), "another task is not the root task");
        check(!TaskProtocol.isOwnRoot(null, root), "no task at all is not the root task");

        Path project = projectRoot();
        check(project != null, "the root of the project was found");
        String runtime = Files.readString(
                project.resolve("src/main/java/dev/magehome/client/MageHomeRuntime.java"),
                StandardCharsets.UTF_8);
        int start = runtime.indexOf("public Task onTaskTick()");
        check(start >= 0, "the tick of the root task was found");
        int end = runtime.indexOf("private Task navigationChild()", start);
        check(end > start, "the end of the tick of the root task was found");
        String body = runtime.substring(start, end);
        check(!body.contains("return task"), "the root task never returns itself from its tick: " + body);
        check(body.contains("TaskProtocol"), "the tick of the root task uses the rule of the core");
    }

    /**
     * Собственная корневая задача аддона и пустая цепочка в конце окна.
     *
     * <p>Раунд 7, ревью кода: окно {@code [OWN_ROOT, NONE]} — аддон держал свою корневую задачу,
     * а цепочка в конце окна пуста — не разбиралось оракулом. Решение: пустая цепочка в конце
     * окна означает, что собственная корневая задача в конце окна не удерживалась. Нарушением это
     * не является: прерывание задачи аддона опустошает цепочку, и после прерывания аддон ждёт
     * явного resume (ТЗ.md раздел 5). Кейс остаётся непроверенным, а не зелёным и не проваленным.
     */
    private static void ownRootAndEmptyChainAtTheEnd() {
        List<TaskChainOwnership.AddOnAction> lawful = List.of(
                new TaskChainOwnership.AddOnAction(TaskChainOwnership.Action.START_OWN_TASK,
                        TaskChainOwnership.Held.CHAIN_EMPTY));

        List<TaskChainOwnership.Sample> ownThenEmpty = List.of(
                sample(TaskOwnership.Relation.OWN_ROOT, TaskChainOwnership.OwnerState.ALIVE),
                sample(TaskOwnership.Relation.NONE, TaskChainOwnership.OwnerState.UNKNOWN));
        eq(TaskChainOwnership.Verdict.OWN_ROOT_EMPTY_AT_THE_END,
                TaskChainOwnership.judge(ownThenEmpty, lawful),
                "the own root task was held and the chain was empty at the end of the window");
        check(!TaskChainOwnership.judge(ownThenEmpty, lawful).violation(),
                "an empty chain at the end of the window is no violation of the add-on");
        check(!TaskChainOwnership.judge(ownThenEmpty, lawful).verified(),
                "the own root task was not held at the end of the window: the case is unverified");

        List<TaskChainOwnership.Sample> ownOnly = List.of(
                sample(TaskOwnership.Relation.OWN_ROOT, TaskChainOwnership.OwnerState.ALIVE));
        eq(TaskChainOwnership.Verdict.OWN_ROOT_HELD, TaskChainOwnership.judge(ownOnly, lawful),
                "the own root task at the end of the window is the proved ownership of the chain");
    }

    /**
     * Чужая задача, которой никто не видел, не доказывает норму.
     *
     * <p>Раунд 7, ревью кода: кейс о неприкосновенной чужой задаче был зелёным, даже когда
     * чужая задача не наблюдалась ни разу. Лаборатория не может создать чужую задачу, поэтому
     * в игре такой кейс не может доказать норму ТЗ.md раздела 5: он остаётся непроверенным.
     */
    private static void foreignTaskWithoutObservation() {
        TaskChainOwnership.ForeignTaskVerdict notObserved =
                TaskChainOwnership.foreignTaskUntouched(TaskChainOwnership.ForeignFate.NOT_OBSERVED);
        eq(TaskChainOwnership.ForeignTaskVerdict.NOT_OBSERVED, notObserved,
                "a foreign task that was never observed proves nothing");
        check(!notObserved.verified(),
                "the norm of the untouched task of another owner is not verified without an observation");
        check(!notObserved.violation(),
                "a foreign task that was never observed is no violation of the add-on");

        eq(TaskChainOwnership.ForeignTaskVerdict.UNTOUCHED,
                TaskChainOwnership.foreignTaskUntouched(TaskChainOwnership.ForeignFate.ALIVE),
                "an observed living task of another owner was not touched");
        check(TaskChainOwnership.foreignTaskUntouched(TaskChainOwnership.ForeignFate.ALIVE).verified(),
                "the untouched task of another owner is verified when it was observed");
        eq(TaskChainOwnership.ForeignTaskVerdict.UNTOUCHED,
                TaskChainOwnership.foreignTaskUntouched(TaskChainOwnership.ForeignFate.ENDED_BY_ITSELF),
                "a task of another owner that ended by itself was not touched");
        eq(TaskChainOwnership.ForeignTaskVerdict.UNTOUCHED,
                TaskChainOwnership.foreignTaskUntouched(TaskChainOwnership.ForeignFate.STOPPED_BY_ANOTHER_ACTOR),
                "a task of another owner that was stopped by somebody else was not touched by the add-on");
        eq(TaskChainOwnership.ForeignTaskVerdict.HARMED,
                TaskChainOwnership.foreignTaskUntouched(TaskChainOwnership.ForeignFate.STOPPED_BY_THE_ADD_ON),
                "the add-on stopped the task of another owner");
        check(TaskChainOwnership.foreignTaskUntouched(TaskChainOwnership.ForeignFate.STOPPED_BY_THE_ADD_ON).violation(),
                "the harmed task of another owner is a violation of the add-on");
    }

    /**
     * Подготовка стенда не публикует события зависимости.
     *
     * <p>Раунд 7: лаборатория публиковала внутреннее событие титульного экрана UnionClef из кода
     * аддона и писала её собственный guard. Жизненный цикл зависимости — титульный экран
     * Minecraft, и собственная подготовка платформы (адаптер образа) открывает его для
     * зависимости {@code altoclef} {@code 1.21.11-0.95.61} и наблюдает настоящую цепочку задач
     * и слот-хендлер до открытия сохранённого мира. Подготовка стенда не должна ни
     * публиковать события зависимости, ни трогать её guard.
     */
    private static void standDoesNotPublishTheEventsOfTheDependency() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        List<Path> sources = new ArrayList<>();
        try (var stream = Files.walk(root.resolve("src/main/java"))) {
            stream.filter(path -> path.toString().endsWith(".java")).forEach(sources::add);
        }
        check(!sources.isEmpty(), "the add-on has sources");
        for (Path source : sources) {
            for (String line : Files.readAllLines(source, StandardCharsets.UTF_8)) {
                String trimmed = line.trim();
                if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) {
                    continue;
                }
                check(!trimmed.contains("TitleScreenEntryEvent"),
                        "the sources do not publish the events of the dependency: " + trimmed);
                check(!trimmed.contains("EventBus.publish"),
                        "the sources do not publish on the bus of the dependency: " + trimmed);
                check(!trimmed.contains("\"_initialized\""),
                        "the sources do not write the guard of the mixin of the dependency: " + trimmed);
            }
        }
    }

    // --- the observable attempt of a placement (раунд 8) --------------------------------------------

    /**
     * The interaction is sent only inside the ordinary range of the player.
     *
     * <p>The rule lives in the domain core and is executed here: the distance from the eyes of
     * the player to the point of the click decides whether the interaction may be sent. The
     * client is read to make sure that it uses exactly this rule and not a distance from the
     * hypothetical cell of the approach (ТЗ.md section 4).
     */
    private static void interactionInsideTheOrdinaryRange() throws Exception {
        check(ReachPolicy.ORDINARY_REACH == 4.5, "the ordinary range is 4.5 blocks");
        check(ReachPolicy.mayInteract(0.0, 0.0, 0.0, 0.0, 0.0, 4.5), "4.5 blocks are inside the range");
        check(!ReachPolicy.mayInteract(0.0, 0.0, 0.0, 0.0, 0.0, 4.51), "4.51 blocks are outside the range");
        check(Math.abs(ReachPolicy.distance(0.0, 0.0, 0.0, 3.0, 4.0, 0.0) - 5.0) < 1e-9,
                "the distance is the distance of the eyes to the point of the click");
        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("ReachPolicy.mayInteract"), "the client uses the rule of the range of the core");
        check(client.contains("client.player.getEyePos()"), "the client measures from the eyes of the player");
        check(!client.contains("slot <= 17 ? slot : slot - 9"),
                "the client does not compute the index of a slot of the screen handler by hand");
    }

    /**
     * The item of the project is in the main hand and the slot of the screen handler is read.
     *
     * <p>The rule of the core is executed here: the slot that holds the wanted item is found by
     * reading the slots, a slot outside the inventory of the player is not used, and the
     * interaction is only allowed when the main hand holds the wanted item.
     */
    private static void itemInTheMainHand() throws Exception {
        List<ItemSelection.SlotView> slots = List.of(
                new ItemSelection.SlotView(9, "minecraft:stone_bricks", true),
                new ItemSelection.SlotView(20, "minecraft:glass", true),
                new ItemSelection.SlotView(45, "minecraft:stone_bricks", false));
        eq(20, ItemSelection.slotWithItem(slots, "minecraft:glass"),
                "the slot of the wanted item is found by reading the slots");
        eq(9, ItemSelection.slotWithItem(slots, "minecraft:stone_bricks"),
                "the slot of the item in the inventory is found");
        eq(-1, ItemSelection.slotWithItem(slots, "minecraft:bed"),
                "an item that is not in the inventory has no slot");
        check(ItemSelection.mayClick("minecraft:stone_bricks", "minecraft:stone_bricks"),
                "the interaction is allowed with the wanted item in the hand");
        check(!ItemSelection.mayClick("minecraft:stone_bricks", "minecraft:glass"),
                "the interaction is not allowed with another item in the hand");
        check(!ItemSelection.mayClick("minecraft:stone_bricks", "minecraft:air"),
                "the interaction is not allowed with an empty hand");
        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("ItemSelection.slotWithItem"), "the client reads the slot of the screen handler");
        check(client.contains("ItemSelection.mayClick"), "the client checks the item in the main hand");
        check(!client.contains("slot <= 17 ? slot : slot - 9"),
                "the client does not compute the index of a slot of the screen handler by hand");
    }

    /**
     * Every attempt publishes its observable facts.
     *
     * <p>The facts of an attempt are the answer to the question "what happened with this cell".
     * The check executes the record of the core and reads the client, so that the facts really
     * reach the log of the game and the journal of the scenario.
     */
    private static void attemptPublishesItsFacts() throws Exception {
        PlacementDiagnostics diagnostics = new PlacementDiagnostics("crosshair", "[2, -61, -12]", "UP",
                "2.50,-59.00,-12.00", "MATCH", "36", "minecraft:stone_bricks",
                "2.50,-60.00,-12.00 yaw=180.00 pitch=-12.50",
                "2.50,-60.00,-12.00 yaw=180.00 pitch=-12.50 held=minecraft:stone_bricks",
                "none", 1, "U=minecraft:air D=minecraft:stone");
        PlacementFacts facts = new PlacementFacts("1,0,0", "2,-60,-12", "[2, -61, -12]", "UP", "[3, -60, -12]",
                "[2, -60, -12]", "minecraft:stone_bricks", "minecraft:stone_bricks", true, 1.9,
                "minecraft:air (the interaction of the attempt was sent)", true, "Success", 180.0, -12.5,
                "2.50,-59.00,-12.00", diagnostics);
        String line = facts.describe();
        for (String key : List.of("cell=", "world=", "support=", "face=", "access=", "player=", "wanted=",
                "held=", "interaction_sent=", "reach=", "observed=", "arrived=", "action_result=", "yaw=",
                "pitch=", "click=", "aim_verdict=", "server=", "precondition=")) {
            check(line.contains(key), "the facts of the attempt name " + key + ": " + line);
        }
        check(line.contains("interaction_sent=true"), "the facts say that the interaction was sent: " + line);
        check(line.contains("reach=1.90"), "the facts name the distance from the eyes: " + line);
        check(line.contains("arrived=true"), "the facts say that the player stood in the cell of the approach: "
                + line);
        check(line.contains("action_result=Success"),
                "the facts name the result of the call of the interaction: " + line);
        check(line.contains("click=2.50,-59.00,-12.00"), "the facts name the point of the click: " + line);
        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("LAB_OBSERVE placement"), "the client writes the facts of the attempt into the log");
        check(client.contains("PlacementFacts"), "the client fills the record of the facts of the core");
        check(client.contains("lastActionResult"), "the client keeps the result of the call of the interaction");
        String harness = clientSource("src/main/java/dev/magehome/client/AcceptanceRunner.java");
        check(harness.contains("placement-facts"), "the harness writes the facts into the journal of the scenario");
        check(harness.contains("journal.event(\"checkpoint\", \"completed\", scenarioId + \".placement-facts\""),
                "the journal event of the facts uses a stage that the journal of the run accepts");
        check(!harness.contains("journal.event(\"observation\""),
                "the journal of the run has no event with a stage that it does not accept");
    }

    /**
     * The behaviour of UnionClef is balanced on the start, the stop and the error.
     *
     * <p>The ledger of the core is executed here on the three ways out of the own root task:
     * the ordinary start and stop, the start that is repeated without a stop in between and the
     * error of the tick. Every push has to be answered by exactly one pop.
     */
    private static void behaviourIsBalanced() throws Exception {
        BehaviourBalance balance = new BehaviourBalance();
        check(balance.onStart(), "the first start pushes the behaviour");
        check(!balance.onStart(), "a start without a stop in between does not push the behaviour again");
        check(balance.onStop(), "the stop pops the behaviour");
        check(balance.balanced(), "the behaviour is balanced after the stop");
        check(!balance.onStop(), "a stop without a push does not pop the behaviour");
        check(balance.balanced(), "the behaviour is balanced after the second stop");

        BehaviourBalance failed = new BehaviourBalance();
        failed.onStart();
        check(failed.onError(), "the error pops the behaviour");
        check(failed.balanced(), "the behaviour is balanced after the error");
        check(failed.pushes() == 1 && failed.pops() == 1, "the error answered the push of the start");

        BehaviourBalance repeated = new BehaviourBalance();
        repeated.onStart();
        repeated.onError();
        repeated.onStart();
        repeated.onStop();
        check(repeated.balanced(), "the behaviour is balanced after an error and a new start");

        String runtime = clientSource("src/main/java/dev/magehome/client/MageHomeRuntime.java");
        check(runtime.contains("behaviour.onStart()"), "the runtime pushes through the ledger of the core");
        check(runtime.contains("behaviour.onStop()"), "the runtime pops through the ledger of the core");
        check(runtime.contains("behaviour.onError()"), "the runtime releases the behaviour on the error");
        check(!runtime.contains("behaviourPushed"), "the runtime has no own flag of the behaviour any more");
    }

    /**
     * The add-on opens no foreign container and drops no item.
     *
     * <p>The sources of the client are scanned for the calls that would throw away a good item
     * or open a container of another owner (ТЗ.md section 4). The only click of the client on a
     * slot of the inventory is the exchange of the item of the project into the hotbar.
     */
    private static void noForeignContainerAndNoDrop() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        List<Path> sources = new ArrayList<>();
        try (var stream = Files.walk(root.resolve("src/main/java"))) {
            stream.filter(path -> path.toString().endsWith(".java")).forEach(sources::add);
        }
        check(!sources.isEmpty(), "the add-on has sources");
        for (Path source : sources) {
            for (String line : Files.readAllLines(source, StandardCharsets.UTF_8)) {
                String trimmed = line.trim();
                if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) {
                    continue;
                }
                for (String forbidden : List.of("SlotActionType.THROW", "dropItem(", "dropSelectedItem",
                        "openHandledScreen", "openScreen(", "PlayerDropItemC2SPacket")) {
                    check(!trimmed.contains(forbidden),
                            "the file " + source + " uses the forbidden call " + forbidden + ": " + trimmed);
                }
            }
        }
        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("SlotActionType.SWAP"), "the only click on a slot of the inventory is the exchange "
                + "of the item of the project into the hotbar");
        check(client.contains("attackBlock"), "the only attack of the client is the removal of an allowed plant");
    }

    /**
     * The give command goes through the ordinary network API without a slash.
     *
     * <p>The facts come from the bytecode of Minecraft 1.21.11 (Yarn) and from the run of the
     * round 7: the command of the server is read from the tree of the commands
     * ({@code ClientPlayNetworkHandler.getCommandDispatcher()}) and the command of the delivery
     * is sent without a leading slash ({@code sendChatCommand}).
     */
    private static void giveCommandThroughTheNetworkApi() throws Exception {
        String context = clientSource("src/main/java/dev/magehome/client/MageHomeContext.java");
        check(context.contains("sendChatCommand(command)"), "the command of the delivery is sent without a slash");
        check(!context.contains("sendChatCommand(\"/"), "the command of the delivery has no leading slash");
        check(context.contains("getCommandDispatcher()"), "the tree of the commands of the server is read");
        check(context.contains("getChild(\"give\")"), "the command give is read from the tree of the commands");
        String core = Files.readString(projectRoot().resolve(
                "core/src/main/java/dev/magehome/core/supply/GiveCommandPlanner.java"), StandardCharsets.UTF_8);
        check(!core.contains("\"/give"), "the command of the core has no leading slash");
    }

    /**
     * The own task of the add-on implements isFinished.
     *
     * <p>ТЗ.md section 7 names the methods of the own task. The check reads the sources of the
     * client and makes sure that every one of them is there.
     */
    private static void ownTaskIsFinished() throws Exception {
        String task = clientSource("src/main/java/dev/magehome/client/MageHomeTask.java");
        check(task.contains("protected void onStart()"), "the own task implements onStart");
        check(task.contains("protected Task onTick()"), "the own task implements onTick");
        check(task.contains("protected void onStop(Task task)"), "the own task implements onStop");
        check(task.contains("protected boolean isEqual(Task task)"), "the own task implements isEqual");
        check(task.contains("public boolean isFinished()"), "the own task implements isFinished");
        String runtime = clientSource("src/main/java/dev/magehome/client/MageHomeRuntime.java");
        check(runtime.contains("automationEnabled()"), "the runtime tells the task whether the automation runs");
    }

    /**
     * The scenario ends with the reason of the suspension instead of waiting for the end of the
     * world.
     *
     * <p>The harness of the game is read: a house that is suspended with a concrete reason ends
     * the scenario, and the reason, the coordinate and the observed state of the cell are read
     * (ТЗ.md section 4).
     */
    private static void scenarioEndsWithTheReason() throws Exception {
        String harness = clientSource("src/main/java/dev/magehome/client/AcceptanceRunner.java");
        check(harness.contains("waitForHouseStateOrSuspension"), "the harness ends the scenario with a suspension");
        check(harness.contains("full-house-from-empty-inventory.suspension-diagnosis"),
                "the scenario reads the reason of the suspension");
        check(harness.contains("full-house-from-empty-inventory.no-navigation-while-suspended"),
                "the scenario counts the requests of the navigation of the suspended automation");
        check(harness.contains("record.reason()"), "the harness reads the reason of the house");
        String runtime = clientSource("src/main/java/dev/magehome/client/MageHomeRuntime.java");
        check(runtime.contains("navigationWhileSuspended"), "the runtime counts the navigation of the suspension");
    }

    /**
     * The arrival at the cell of the approach is exact.
     *
     * <p>The rule of the core is executed here: the player stands in the cell of the approach
     * itself, a neighbour cell is not the arrival. The client uses exactly this rule and keeps
     * no second copy of it: the tolerance of the old arrival (1.6 blocks) is gone, because it
     * declared the arrival while the player stood in the cell that the add-on itself fills, and
     * the server refuses such an interaction (ТЗ.md section 4).
     */
    private static void exactArrival() throws Exception {
        check(ArrivalPolicy.arrived(3, -60, 7, 3, -60, 7),
                "the player stands in the cell of the approach: the arrival holds");
        check(!ArrivalPolicy.arrived(4, -60, 7, 3, -60, 7),
                "the player stands next to the cell of the approach: the arrival does not hold");
        check(!ArrivalPolicy.arrived(3, -59, 7, 3, -60, 7),
                "the player stands one level above the cell of the approach: the arrival does not hold");
        check(!ArrivalPolicy.arrived(3, -60, 8, 3, -60, 7),
                "the player stands in the neighbouring cell of the approach: the arrival does not hold");

        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("ArrivalPolicy.arrived"), "the client uses the rule of the arrival of the core");
        check(!client.contains("atAccess"), "the client has no own rule of the arrival any more");
        check(!client.contains("<= 1.6"), "the client has no tolerance of the arrival any more");
    }

    /**
     * The interaction is never sent from the cell that is to be filled.
     *
     * <p>A block cannot be placed into the cell where the player stands: the server refuses the
     * interaction, the world does not change and the house waits for a confirmation that never
     * comes. The rule of the core forbids the click in that case, and the client uses it.
     */
    private static void noClickFromTheFilledCell() throws Exception {
        check(ArrivalPolicy.mayClick(3, -60, 7, 3, -60, 7, 3, -60, 8),
                "the player stands in the cell of the approach and not in the cell that is filled");
        check(!ArrivalPolicy.mayClick(3, -60, 8, 3, -60, 8, 3, -60, 8),
                "the player stands in the cell that is to be filled: the click is not sent");
        check(!ArrivalPolicy.mayClick(4, -60, 7, 3, -60, 7, 3, -60, 8),
                "the player did not stand in the cell of the approach: the click is not sent");
        check(ArrivalPolicy.occupies(3, -60, 8, 3, -60, 8),
                "the player occupies the cell that is to be filled");
        check(!ArrivalPolicy.occupies(3, -60, 7, 3, -60, 8),
                "the player does not occupy the cell that is to be filled");

        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("ArrivalPolicy.occupies"), "the client uses the rule of the occupied cell");
    }

    /**
     * A cell of the approach that is not reached is left, and the house is suspended with a reason.
     *
     * <p>The wait for one cell of the approach is bounded, and the next cell is only tried when
     * nothing was sent for the current one: a position that already received an interaction keeps
     * its two sends (ТЗ.md section 4). When no cell of the approach is left, the client reports
     * that there is no admissible way to place the operation, and the controller suspends the
     * house with the reason no_placement_support and the coordinate of the cell.
     */
    private static void boundedAccessFallback() throws Exception {
        check(!ArrivalPolicy.maySwitchAccess(ArrivalPolicy.ACCESS_WAIT_TICKS - 1, 0),
                "the cell of the approach is not left before the budget is spent");
        check(ArrivalPolicy.maySwitchAccess(ArrivalPolicy.ACCESS_WAIT_TICKS, 0),
                "the cell of the approach is left when the budget is spent and nothing was sent");
        check(!ArrivalPolicy.maySwitchAccess(ArrivalPolicy.ACCESS_WAIT_TICKS, 1),
                "a cell that already received an interaction is not left");
        check(ArrivalPolicy.ACCESS_WAIT_TICKS > 0, "the budget of the wait is a positive number of ticks");

        check(!ArrivalPolicy.mayLeaveAccessOnAim(ArrivalPolicy.AIM_WAIT_TICKS - 1, 0),
                "the cell of the approach is not left before the budget of the view is spent");
        check(ArrivalPolicy.mayLeaveAccessOnAim(ArrivalPolicy.AIM_WAIT_TICKS, 0),
                "the cell of the approach is left when the budget of the view is spent and nothing was sent");
        check(!ArrivalPolicy.mayLeaveAccessOnAim(ArrivalPolicy.AIM_WAIT_TICKS, 1),
                "a cell that already received an interaction is not left because of the view");
        check(ArrivalPolicy.AIM_WAIT_TICKS <= ArrivalPolicy.ACCESS_WAIT_TICKS,
                "the budget of the view of the core is not longer than the budget of the wait of the core");

        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("ArrivalPolicy.mayLeaveAccess"),
                "the client uses the rule of the bounded wait of the core");
        check(client.contains("ArrivalPolicy.mayLeaveAccessOnAim"),
                "the client asks the core for the rule of the bounded wait of the view");
        check(!client.contains("AIM_RETRIES"),
                "the client keeps no second copy of the budget of the wait of the view");
        check(client.contains("yawAtSupport") && !client.contains("yawFor"),
                "the direction of the view is the geometry of the cell of the approach and not a "
                        + "search over four directions");
        check(client.contains("stateMatchesProject"),
                "the client asks the game which state it would place with that direction of the view");
        check(client.contains("the operation is admissible, but no cell of the approach gave a"),
                "the client does not answer that there is no admissible operation while the operation "
                        + "is admissible and only the confirmation is missing");
        check(client.contains("solve(solutionSlot + 1)"), "the client tries the next cell of the approach");
        check(client.contains("accessExhausted"), "the client remembers that no cell of the approach was reachable");
        String controller = clientSource("core/src/main/java/dev/magehome/core/automation/AutomationController.java");
        check(controller.contains("NO_PLACEMENT_SUPPORT"),
                "the controller suspends the house when there is no admissible way to place the operation");
        check(controller.contains("if (!ctx.requestPlacement("),
                "the controller reads the answer of the client about the admissible way to place");
    }

    /**
     * The house becomes ready only when the player stands on the front point itself.
     *
     * <p>The rule of the core is exact: a place near the front point is not the front point, and
     * the controller uses exactly this rule (ТЗ.md section 4, R21).
     */
    private static void exitAtTheFrontPoint() throws Exception {
        check(ArrivalPolicy.atFrontPoint(3, -60, -1, 3, -60, -1),
                "the player stands on the front point: the exit counts");
        check(!ArrivalPolicy.atFrontPoint(4, -60, -1, 3, -60, -1),
                "the player stands next to the front point: the exit does not count");
        check(!ArrivalPolicy.atFrontPoint(3, -60, 0, 3, -60, -1),
                "the player stands one cell away from the front point: the exit does not count");

        String controller = clientSource("core/src/main/java/dev/magehome/core/automation/AutomationController.java");
        check(controller.contains("ArrivalPolicy.atFrontPoint"),
                "the controller uses the rule of the front point of the core");
    }

    /**
     * The navigation of the dependency ends in the cell itself.
     *
     * <p>The rule of the arrival of the add-on is the rule of the dependency: the goal
     * {@code adris.altoclef.util.goals.AltoGoal$Block} of the delivered UnionClef 0.95.61 ends
     * the navigation only when the three coordinates of the place of the player are equal to the
     * three coordinates of the goal. The fact is read from the delivered jar itself.
     */
    private static void navigationEndsInTheCell() throws Exception {
        byte[] goal = unionclefClass("adris/altoclef/util/goals/AltoGoal$Block.class");
        check(classFileHoldsName(goal, "reached"), "the goal of the navigation declares the method reached");
        check(classFileHoldsName(goal, "(Lnet/minecraft/class_2338;)Z"),
                "the goal of the navigation compares the position of the player with the goal");
        byte[] task = unionclefClass("adris/altoclef/tasks/movement/GetToBlockTask.class");
        check(classFileHoldsName(task, "newAltoGoal"), "the task of the navigation uses the goal of the block");
    }

    /**
     * A cell of the approach that belongs to the operation itself is left for the next one.
     *
     * <p>The player can stand in the cell of the approach, and that cell can be a cell of the
     * operation itself — the second cell of a bed or of a door, for example. A block cannot be
     * placed into the cell where the player stands, so the interaction is never sent from there;
     * waiting does not help, because the player is already there. The cell is left at once and the
     * next cell of the approach is tried. A cell that already received an interaction is not left:
     * it keeps its two sends (ТЗ.md section 4).
     */
    private static void forbiddenAccessCellIsLeft() throws Exception {
        check(ArrivalPolicy.mayLeaveAccess(true, false, 0, 0),
                "the player stands in the cell of the operation itself: the cell is left at once");
        check(!ArrivalPolicy.mayLeaveAccess(true, true, 0, 0),
                "the player stands in the cell of the approach and the click is allowed: the cell is kept");
        check(!ArrivalPolicy.mayLeaveAccess(true, false, 0, 1),
                "a cell that already received an interaction is not left");
        check(!ArrivalPolicy.mayLeaveAccess(false, true, ArrivalPolicy.ACCESS_WAIT_TICKS - 1, 0),
                "the cell of the approach is not left before the budget of the wait is spent");
        check(ArrivalPolicy.mayLeaveAccess(false, true, ArrivalPolicy.ACCESS_WAIT_TICKS, 0),
                "the cell of the approach is left when the budget is spent and nothing was sent");

        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("ArrivalPolicy.mayLeaveAccess"),
                "the client uses the rule of the leaving of the cell of the approach");
        check(client.contains("leaveAccess()"), "the client tries the next cell of the approach");
        check(client.contains("accessExhausted"),
                "the client remembers that no cell of the approach was reachable");
    }

    /**
     * The laboratory prepared the world of the stand.
     *
     * <p>Раунд 6: сценарий, в котором лаборатория не подготовила площадку, должен быть
     * непроверенным с причиной, а не проваленным: без подготовки причина автоматики была бы
     * другой, и её нельзя приписывать аддону. Проверка читает мир и отвечает, что подготовка
     * есть: ровная площадка с полной опорой и площадка, перекрытая водой.
     */
    /**
     * The click waits for the turn of the view and only when the view points at the support.
     *
     * <p>Раунд 10: порядок ТЗ требует синхронизировать наведение перед установкой. Направление
     * взгляда достигает сервера с пакетом движения следующего тика, поэтому клик не отправляется
     * в том же тике, в котором повернут взгляд, и не отправляется, пока взгляд указывает не на
     * опору операции. Правило живёт в ядре (dev.magehome.core.placement.AimPolicy) и исполняется
     * без Minecraft, а клиент применяет именно это правило и не держит второй копии.
     */
    private static void clickFollowsTheAim() throws Exception {
        check(!AimPolicy.mayClick(false, 0, true), "без поворота взгляда клик не отправляется");
        check(!AimPolicy.mayClick(true, 0, true),
                "в том же тике, в котором повернут взгляд, клик не отправляется");
        check(AimPolicy.mayClick(true, AimPolicy.MIN_TICKS_BEFORE_CLICK, true),
                "через тик после поворота взгляда клик разрешён");
        check(!AimPolicy.mayClick(true, 5, false),
                "пока взгляд указывает не на опору, клик не отправляется");
        check(!AimPolicy.mayClick(false, 5, true), "без поворота взгляда клик не отправляется");

        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("AimPolicy.mayClick"),
                "клиент применяет правило ядра для поворота взгляда и клика");
        String aim = between(client, "private void aimAt(Vec3d point)", "\n    }\n");
        check(aim.contains("client.player.setYaw") && aim.contains("client.player.setPitch"),
                "клиент поворачивает и азимут, и угол взгляда в одном шаге");
        check(aim.contains("client.player.getEyePos()"),
                "направление взгляда считается по настоящим глазам игрока, а не по клетке подхода");
        check(client.contains("aimTicks++"),
                "клиент считает тики между поворотом взгляда и кликом");
    }

    /**
     * The click uses the hit result that the game itself computes under the crosshair.
     *
     * <p>Раунд 10: клик отправляется с тем hit result, который сама игра вычисляет под
     * прицелом, и только когда он называет опору операции и нужную грань. Сравнение живёт в
     * ядре (dev.magehome.core.placement.AimMatch) и различает отсутствие цели, чужой блок и
     * чужую грань.
     */
    private static void hitResultOfTheGame() throws Exception {
        eq(AimMatch.Verdict.NO_TARGET, AimMatch.of(false, false, false),
                "под прицелом нет блока");
        eq(AimMatch.Verdict.WRONG_BLOCK, AimMatch.of(true, false, true),
                "под прицелом другой блок");
        eq(AimMatch.Verdict.WRONG_FACE, AimMatch.of(true, true, false),
                "под прицелом другая грань того же блока");
        eq(AimMatch.Verdict.MATCH, AimMatch.of(true, true, true),
                "под прицелом нужная опора и нужная грань");
        check(AimMatch.matches(AimMatch.Verdict.MATCH),
                "прицел, который называет опору, можно использовать для клика");
        check(!AimMatch.matches(AimMatch.Verdict.WRONG_BLOCK),
                "прицел, который называет чужой блок, использовать нельзя");
        check(!AimMatch.matches(AimMatch.Verdict.WRONG_FACE),
                "прицел, который называет чужую грань, использовать нельзя");
        check(!AimMatch.matches(AimMatch.Verdict.NO_TARGET),
                "прицел без блока использовать нельзя");

        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("client.crosshairTarget"),
                "клиент берёт hit result, который вычисляет сама игра");
        check(client.contains("AimMatch.of"), "клиент сравнивает взгляд с опорой через правило ядра");
        check(client.contains("interactBlock(client.player, Hand.MAIN_HAND, crosshair)"),
                "клик отправляется с hit result игры, а не с собственным");
    }

    /**
     * The preconditions of the ordinary placement are asked before the click.
     *
     * <p>Раунд 10: игра не отвечает, почему она отказала во взаимодействии. Аддон задаёт те же
     * вопросы в том же порядке (предмет в руке, возможности предмета, возможность заполнить
     * ячейку, состояние игры, место состояния и обычная дальность) и публикует первое
     * предусловие, которое не выполнилось. Порядок и правило живут в ядре
     * (dev.magehome.core.placement.PlacementPreconditions).
     */
    private static void preconditionsOfThePlacement() throws Exception {
        Map<PlacementPreconditions.Step, Boolean> observed = PlacementPreconditions.allTrue();
        eq(null, PlacementPreconditions.firstFailure(observed),
                "ни одно предусловие не нарушено");
        observed.put(PlacementPreconditions.Step.CAN_PLACE, Boolean.FALSE);
        eq("CAN_PLACE", PlacementPreconditions.firstFailure(observed),
                "названо предусловие, которое не выполнилось");
        observed.put(PlacementPreconditions.Step.STACK_IN_HAND, Boolean.FALSE);
        eq("STACK_IN_HAND", PlacementPreconditions.firstFailure(observed),
                "названо первое предусловие в порядке игры, которое не выполнилось");
        observed = PlacementPreconditions.allTrue();
        observed.put(PlacementPreconditions.Step.TARGET_CELL, Boolean.FALSE);
        eq("TARGET_CELL", PlacementPreconditions.firstFailure(observed),
                "названо предусловие ячейки проекта, которое не выполнилось");

        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("PlacementPreconditions.firstFailure"),
                "клиент спрашивает правило ядра о первом невыполненном предусловии");
        check(client.contains("stack.isItemEnabled"),
                "клиент спрашивает, знает ли игра возможности предмета");
        check(client.contains("context.canPlace()"), "клиент спрашивает, можно ли заполнить ячейку");
        check(client.contains("getPlacementState(context)"),
                "клиент спрашивает, какое состояние даст игра");
        check(client.contains("state.canPlaceAt"),
                "клиент спрашивает, может ли состояние стоять в ячейке проекта");
        check(client.contains("PlacementPreconditions.Step.TARGET_CELL"),
                "клиент спрашивает, встанет ли блок в ячейку проекта");
    }

    /**
     * The facts of the attempt name the view, the slot, the images of the player and the
     * precondition.
     *
     * <p>Раунд 10: прогон, в котором блок не поставился, должен читаться, а не угадываться.
     * Факты называют блок и грань под прицелом, вердикт сравнения с опорой, слот экрана с
     * предметом проекта, место и взгляд игрока на клиенте и на сервере, первое невыполненное
     * предусловие обычной установки и состояния ячейки проекта и соседних ячеек.
     */
    private static void factsNameTheDiagnostics() throws Exception {
        PlacementDiagnostics diagnostics = new PlacementDiagnostics("crosshair", "[2, -61, -12]", "UP",
                "2.50,-59.00,-12.00", "MATCH", "36", "minecraft:stone_bricks",
                "2.50,-60.00,-12.00 yaw=180.00 pitch=-12.50",
                "2.50,-60.00,-12.00 yaw=180.00 pitch=-12.50 held=minecraft:stone_bricks",
                "none", 1, "U=minecraft:air D=minecraft:stone");
        String line = diagnostics.describe();
        for (String key : List.of("aim_source=", "aim_block=", "aim_face=", "aim_vector=", "aim_verdict=",
                "slot=", "slot_item=", "client=", "server=", "precondition=", "aim_ticks=", "neighbours=")) {
            check(line.contains(key), "диагностика называет " + key + ": " + line);
        }
        check(line.contains("aim_verdict=MATCH"), "диагностика называет вердикт сравнения: " + line);
        check(line.contains("precondition=none"),
                "диагностика называет предусловие обычной установки: " + line);

        String client = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(client.contains("PlacementDiagnostics"), "клиент заполняет запись диагностики ядра");
        check(client.contains("publishedVerdict") && client.contains("\"SUPPORT\""),
                "клиент публикует вердикт прицела как решение о клике: SUPPORT значит, что прицел "
                        + "игры называет опору и игра поставит состояние проекта");
        check(client.contains("lastActionResult, aimVerdict") && client.contains("preconditionStep"),
                "факты отправленного взаимодействия несут вердикт прицела и первое предусловие");
        check(client.contains("serverImage()"),
                "клиент публикует место и взгляд игрока на сервере игры");
        check(client.contains("neighbours()"),
                "клиент публикует состояния ячейки проекта и соседних ячеек");
        check(client.contains("slot_sync="),
                "клиент публикует признак того, что слот предмета дошел до сервера");
        check(client.contains("isPaused()") && client.contains("isWindowFocused()"),
                "клиент публикует состояние паузы и фокуса окна вместе с образом игрока");
    }

    /**
     * The stand probes the ordinary placement of the environment and removes the block of the
     * probe.
     *
     * <p>Раунд 10: подготовка стенда (не задача аддона) ставит один блок обычным
     * взаимодействием игрока на подготовленной площадке рядом с игроком, вне footprint дома, и
     * убирает блок и предмет обратно. Факт пробы говорит, принимает ли окружение обычную
     * установку вообще, и не является утверждением о аддоне.
     */
    private static void standProbesTheEnvironment() throws Exception {
        String fixture = clientSource("src/main/java/dev/magehome/client/LabScenarioFixture.java");
        check(fixture.contains("interactBlock(client.player, Hand.MAIN_HAND, hit)"),
                "проба среды ставит блок обычным взаимодействием игрока");
        check(fixture.contains("finishProbe"), "проба среды убирает блок пробы обратно");
        check(fixture.contains("serverPlayer.getInventory().clear()"),
                "проба среды убирает предмет пробы из инвентаря игрока");
        String runner = clientSource("src/main/java/dev/magehome/client/AcceptanceRunner.java");
        check(runner.contains("LAB_OBSERVE stand_probe"),
                "факт пробы среды пишется в лог клиента");
        check(runner.contains("stand-probe"),
                "факт пробы среды пишется в журнал прогона");
        check(fixture.contains("player_server=") && fixture.contains("player_client="),
                "факт пробы среды называет образ игрока на сервере и на клиенте");
        check(fixture.contains("paused=") && fixture.contains("focused="),
                "факт пробы среды называет состояние паузы и фокуса окна");
        check(fixture.contains("fixture_gate:"), "факты гейтов подготовки пишутся в лог клиента");
        check(fixture.contains("GATE_WAIT_TICKS"),
                "каждый гейт подготовки стенда имеет ограниченное ожидание");
        check(fixture.contains("gateFailure"),
                "подготовка стенда, которая не прошла гейт, называет этот гейт");
        check(fixture.contains("the environment refused the ordinary placement"),
                "отказ окружения останавливает подготовку стенда, а не проверку продукта");
        check(fixture.contains("setDifficulty"),
                "стенд защищает игрока сценария от мобов мира");
    }

    private static void preparationOfTheWorld() {
        Vec3i origin = new Vec3i(0, 0, 0);
        PlatformFixture.Result buildable = PlatformFixture.inspect(FakeWorld.withSite(origin),
                new Vec3i(3, 0, 3));
        eq(PlatformFixture.Kind.BUILDABLE, buildable.kind(),
                "the laboratory prepared a platform that the add-on can build on");

        FakeWorld wet = new FakeWorld();
        for (int x = 0; x < 7; x++) {
            for (int z = 0; z < 7; z++) {
                wet.solid(origin.add(x, -1, z), "minecraft:stone");
                wet.liquid(origin.add(x, 0, z), "minecraft:water");
            }
        }
        wet.solid(origin.add(3, -1, -1), "minecraft:stone");
        PlatformFixture.Result water = PlatformFixture.inspect(wet, new Vec3i(3, 0, 3));
        eq(PlatformFixture.Kind.WATER_COVERED, water.kind(),
                "the laboratory prepared a platform that is covered with water");

        PlatformFixture.Result empty = PlatformFixture.inspect(new FakeWorld(), new Vec3i(3, 0, 3));
        eq(PlatformFixture.Kind.NONE, empty.kind(),
                "a world without a prepared platform holds none");

        // Two platforms inside the reach of the scan: the one that the product would use is near
        // the player, the other one is further away. The inspection has to name the near one,
        // because the product looks for its site in the order of the distance of the centre of
        // the candidate from the player.
        FakeWorld two = new FakeWorld();
        for (Vec3i site : List.of(new Vec3i(0, 0, 0), new Vec3i(10, 0, 10))) {
            for (int x = 0; x < 7; x++) {
                for (int z = 0; z < 7; z++) {
                    two.solid(site.add(x, -1, z), "minecraft:stone");
                }
            }
            two.solid(site.add(3, -1, -1), "minecraft:stone");
            for (int x = 0; x < 7; x++) {
                for (int z = 0; z < 7; z++) {
                    for (int y = 0; y < 5; y++) {
                        two.air(site.add(x, y, z));
                    }
                }
            }
            two.air(site.add(3, 0, -1));
            two.air(site.add(3, 1, -1));
        }
        PlatformFixture.Result near = PlatformFixture.inspect(two, new Vec3i(3, 0, 3));
        eq(PlatformFixture.Kind.BUILDABLE, near.kind(), "the platform near the player is found");
        eq(new Vec3i(0, 0, 0), near.origin(),
                "the platform near the player is the one that the product would use");
    }

    /**
     * The stand of a scenario knows the slot handler of UnionClef.
     *
     * <p>The slot handler is created in the same method of UnionClef as the task chain, and the
     * mixin of UnionClef on the inventory of the server calls it. The harness of the game reads
     * it and names it in the detail of the check of the stand.
     */
    private static void slotHandlerOfTheStand() {
        readinessRule();
        try {
            String harness = clientSource("src/main/java/dev/magehome/client/AcceptanceRunner.java");
            check(harness.contains("bridge.slotHandlerAvailable()"),
                    "the harness reads the slot handler of UnionClef");
            String bridge = clientSource("src/main/java/dev/magehome/client/UnionClefBridge.java");
            check(bridge.contains("getSlotHandler()"),
                    "the bridge of UnionClef reads the slot handler of the delivered mod");
        } catch (Exception e) {
            throw new AssertionError("the harness of the game could not be read: " + e.getMessage());
        }
    }

    private static void emptyInventoryRule() {
        check(StandRules.inventoryEmpty(Map.of()), "an empty inventory is recognised");
        check(!StandRules.inventoryEmpty(Map.of("minecraft:stone_bricks", 1)),
                "an inventory with one item is not empty");
        check(!StandRules.inventoryEmpty(null), "an unknown inventory is not empty");
    }

    private static void giveRule() {
        check(!StandRules.giveAvailable(GiveAvailability.ABSENT),
                "without the command give no material is asked");
        check(StandRules.giveAvailable(GiveAvailability.AVAILABLE),
                "with the command give the material is asked");
        check(!StandRules.giveAvailable(GiveAvailability.UNKNOWN),
                "a command tree that was not read yet asks for no material");
    }

    private static void seededRecordRule() {
        HouseRecord seeded = seededRecord("h1", 0, HouseState.BUILDING);
        check(StandRules.exactlyOneSeededRecord(List.of(seeded), 0), "the seeded record is recognised");
        check(!StandRules.exactlyOneSeededRecord(List.of(seeded), 1), "the variant of the record is checked");
        check(!StandRules.exactlyOneSeededRecord(List.of(), 0), "an empty registry holds no seeded record");
        check(!StandRules.exactlyOneSeededRecord(List.of(seeded, seededRecord("h2", 1, HouseState.BUILDING)), 0),
                "two records are not one seeded record");
    }

    private static void buildingRecordRule() {
        check(StandRules.hasRecordInState(List.of(seededRecord("h1", 0, HouseState.BUILDING)), HouseState.BUILDING),
                "the record in the state building is recognised");
        check(!StandRules.hasRecordInState(List.of(seededRecord("h1", 0, HouseState.READY)), HouseState.BUILDING),
                "a ready record is not a building record");
        check(!StandRules.hasRecordInState(List.of(), HouseState.BUILDING), "an empty registry holds no record");
    }

    private static HouseRecord seededRecord(String houseId, int variant, HouseState state) {
        HouseRecord record = new HouseRecord(houseId, "server|localhost:25565|default", "minecraft:overworld", "player-uuid",
                new Vec3i(100, 64, 200), variant, state, "placement", 0, null, null);
        record.setTemplateId(AutomationController.CURRENT_TEMPLATE_ID);
        return record;
    }

    // --- the life cycle of the work -----------------------------------------------------------------

    /** The resume checks the owner of the task chain before it acts (ТЗ.md section 5). */
    private static void resumeOwnership() {
        eq(ResumeOwnership.Action.START_OWN_TASK,
                ResumeOwnership.decide(TaskOwnership.Relation.NONE, false),
                "an empty chain starts the own task");
        eq(ResumeOwnership.Action.REARM_OWN_ROOT,
                ResumeOwnership.decide(TaskOwnership.Relation.OWN_ROOT, true),
                "the own stopped root task is re-armed");
        eq(ResumeOwnership.Action.OWN_TASK_RUNNING,
                ResumeOwnership.decide(TaskOwnership.Relation.OWN_ROOT, false),
                "the own running task is left alone");
        check(ResumeOwnership.decide(TaskOwnership.Relation.FOREIGN, false).keepsForeignTask(),
                "the task of another owner is kept");
        check(ResumeOwnership.decide(TaskOwnership.Relation.OWN_DESCENDANT, true).keepsForeignTask(),
                "a task of another owner above the own task is kept");
        eq(ResumeOwnership.Action.WAIT_FOR_CHAIN,
                ResumeOwnership.decide(TaskOwnership.Relation.CHAIN_UNAVAILABLE, false),
                "a chain that does not exist yet starts no task of the add-on");
        check(!ResumeOwnership.decide(TaskOwnership.Relation.CHAIN_UNAVAILABLE, false).keepsForeignTask(),
                "a chain that does not exist yet is no task of another owner");
        check(TaskOwnership.chainUnavailable(TaskOwnership.unavailableChain()),
                "a chain that does not exist yet is its own relation");
        check(!TaskOwnership.foreign(TaskOwnership.unavailableChain()),
                "a chain that does not exist yet belongs to nobody");
        try {
            String runtime = clientSource("src/main/java/dev/magehome/client/MageHomeRuntime.java");
            check(runtime.contains("ResumeOwnership.decide("), "the client applies the rule of the core");
            check(runtime.contains("bridge.relationToOwnRoot(task)"), "the client reads the owner of the chain");
            check(runtime.contains("ResumeOwnership.Action.WAIT_FOR_CHAIN"),
                    "the client starts no task of its own while the chain of UnionClef does not exist");
            String bridge = clientSource("src/main/java/dev/magehome/client/UnionClefBridge.java");
            check(bridge.contains("getCurrentTask()"), "the bridge reads the current task of the chain");
            check(bridge.contains("thisOrChildSatisfies"),
                    "the bridge asks whether the own task is below the current one");
            check(bridge.contains("current.stopped()"), "the bridge reads whether the task is stopped");
            check(bridge.contains("chainAvailable()"),
                    "the bridge tells whether the chain of UnionClef exists at all");
            check(!bridge.contains("mod.getUserTaskChain().getCurrentTask()"),
                    "the bridge never reads the current task of a chain that does not exist");
        } catch (Exception e) {
            throw new AssertionError("the client could not be read: " + e.getMessage());
        }
    }

    /** A death, a disconnect or a change of the world suspends the house (ТЗ.md section 5). */
    private static void interruptionReasons() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        eq(1, context.registry.houses().size(), "the record of the house was written");
        controller.interrupt(context, SuspensionReason.PLAYER_DEAD.wireName());
        eq(AutomationState.SUSPENDED, controller.state(), "the state after the interruption");
        check(controller.reason() != null && controller.reason().startsWith("player_dead@"),
                "the reason of the interruption names the reason and the place: " + controller.reason());
        HouseRecord record = context.registry.houses().get(0);
        eq(HouseState.SUSPENDED, record.state(), "the record is suspended");
        check(record.reason() != null && record.reason().startsWith("player_dead@"),
                "the record keeps the concrete reason with the place: " + record.reason());
        // The work continues only after an explicit resume.
        context.advance(5000);
        controller.tick(context);
        eq(AutomationState.SUSPENDED, controller.state(), "a suspended automation does not continue by itself");
        try {
            String runtime = clientSource("src/main/java/dev/magehome/client/MageHomeRuntime.java");
            check(runtime.contains("SuspensionReason.PLAYER_DEAD.wireName()"),
                    "the death of the player suspends the house");
            check(runtime.contains("SuspensionReason.WORLD_CHANGED.wireName()"),
                    "the change of the world suspends the house");
        } catch (Exception e) {
            throw new AssertionError("the client could not be read: " + e.getMessage());
        }
    }

    /** After a restart the blocks are verified again (ТЗ.md section 5). */
    private static void verifiedProgress() {
        FakeContext context = new FakeContext();
        Vec3i origin = firstCandidate(context.player);
        context.world = FakeWorld.withSite(origin);
        // The floor of the house stands already; the world is the only source of the progress.
        for (int x = 0; x < 7; x++) {
            for (int z = 0; z < 7; z++) {
                context.world.put(origin.add(x, 0, z),
                        ObservedBlock.of("minecraft:stone_bricks", Map.of(), true, false, false));
            }
        }
        context.registry.add(record("h1", context.worldKey, context.dimension, context.playerUuid, origin));
        AutomationController controller = new AutomationController();
        controller.enable();
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");
        HouseRecord resumed = context.registry.houses().get(0);
        eq(49, resumed.confirmedCells(), "the confirmed cells come from the observation of the world");
        eq(HouseState.BUILDING, resumed.state(), "the house is built again");
        eq("h1", resumed.houseId(), "the same house is continued");
    }

    /** The gate of the background actions (ТЗ.md sections 3 and 5). */
    private static void actionGate() {
        check(ActionGate.mayAct(true, false, false, false, true),
                "the gate is open while the automation works");
        check(!ActionGate.mayAct(false, false, false, false, true),
                "the gate is closed while the automation is off");
        check(!ActionGate.mayAct(true, true, false, false, true),
                "the gate is closed while the automation waits");
        check(!ActionGate.mayAct(true, false, true, false, true),
                "the gate is closed while UnionClef is paused");
        check(!ActionGate.mayAct(true, false, false, true, true),
                "the gate is closed while a foreign task runs");
        check(!ActionGate.mayAct(true, false, false, false, false),
                "the gate is closed while the chain of UnionClef does not exist yet");
        check(ActionGate.mayAct(true, false, false, false, true),
                "the gate is open while the chain of UnionClef exists");
        try {
            String runtime = clientSource("src/main/java/dev/magehome/client/MageHomeRuntime.java");
            check(runtime.contains("ActionGate.mayAct("), "the client applies the rule of the core");
            String placement = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
            check(placement.contains("actionAllowed.getAsBoolean()"),
                    "the placement asks the gate before it walks or clicks");
            String context = clientSource("src/main/java/dev/magehome/client/MageHomeContext.java");
            check(context.contains("actionAllowed.getAsBoolean()"),
                    "the give command asks the gate before it is sent");
        } catch (Exception e) {
            throw new AssertionError("the client could not be read: " + e.getMessage());
        }
        gateIsExecuted();
    }

    /**
     * The rule of the gate is executed, not only read from a table.
     *
     * <p>The same situation is run twice: while the gate is open the automation asks for the
     * materials and for a placement, and while the gate is closed it asks for nothing and sends
     * nothing. The first run shows that the situation really would produce actions, so that the
     * second run is a real observation and not an empty one.
     */
    private static void gateIsExecuted() {
        Vec3i origin = new Vec3i(100, 64, 200);

        // The gate is open: the automation asks for the materials.
        FakeContext openSupply = buildingContext(origin);
        AutomationController supplyController = new AutomationController();
        supplyController.enable();
        check(supplyController.resume("h1", openSupply).contains("\"result\":\"accepted\""),
                "the resume of the house is accepted");
        openSupply.advance(100);
        supplyController.tick(openSupply);
        check(!openSupply.sentCommands.isEmpty(),
                "the automation asks for the materials while the gate is open");

        // The gate is open: the automation asks for a placement.
        FakeContext openPlacement = buildingContext(origin);
        openPlacement.inventory.put("minecraft:stone_bricks", 49);
        AutomationController placementController = new AutomationController();
        placementController.enable();
        placementController.resume("h1", openPlacement);
        openPlacement.advance(100);
        placementController.tick(openPlacement);
        check(!openPlacement.placementRequests.isEmpty(),
                "the automation asks for a placement while the gate is open");

        // The gate is open and the house is complete: the automation asks for the walk to the
        // front point, because the record becomes ready only when the player stands there (C50).
        FakeContext openFront = completedHouseContext(origin);
        AutomationController frontController = new AutomationController();
        frontController.enable();
        frontController.resume("h1", openFront);
        openFront.advance(100);
        frontController.tick(openFront);
        check(!openFront.frontPointRequests.isEmpty(),
                "the automation asks for the walk to the front point while the gate is open");

        // The gate is closed: the same situation, but nothing is asked at all.
        for (boolean paused : new boolean[] {true, false}) {
            FakeContext closed = completedHouseContext(origin);
            AutomationController controller = new AutomationController();
            controller.enable();
            controller.resume("h1", closed);
            closed.paused = paused;
            closed.foreign = !paused;
            for (int i = 0; i < 5; i++) {
                closed.advance(100);
                controller.tick(closed);
            }
            String why = paused ? "while UnionClef is paused" : "while a foreign task runs";
            check(closed.sentCommands.isEmpty(), "no give is sent " + why);
            check(closed.placementRequests.isEmpty(), "no placement is requested " + why);
            check(closed.frontPointRequests.isEmpty(), "no walk to the front point is requested " + why);
        }
    }

    /**
     * The rule of the gate is executed while the chain of UnionClef does not exist yet.
     *
     * <p>The chain is created while the mods are loaded; before that the add-on may neither start
     * a task of its own nor act on the world (ТЗ.md section 5). The rule is not only read from
     * the table: the same situations are run with and without the chain. While the chain does not
     * exist the automation asks for nothing and sends nothing, and as soon as the chain exists
     * the work begins. The runs without the chain would produce actions, so they are real
     * observations and not empty ones.
     */
    private static void missingChainKeepsTheGateClosed() {
        Vec3i origin = new Vec3i(140, 64, 280);

        // The finished house: the automation would ask for the walk to the front point.
        FakeContext finished = completedHouseContext(origin);
        AutomationController finishedController = new AutomationController();
        finishedController.enable();
        finishedController.resume("h1", finished);
        finished.chainAvailable = false;
        for (int i = 0; i < 5; i++) {
            finished.advance(100);
            finishedController.tick(finished);
        }
        check(finished.sentCommands.isEmpty(),
                "no give is sent while the chain of UnionClef does not exist");
        check(finished.placementRequests.isEmpty(),
                "no placement is requested while the chain of UnionClef does not exist");
        check(finished.frontPointRequests.isEmpty(),
                "no walk to the front point is requested while the chain of UnionClef does not exist");
        eq(AutomationState.WAITING, finishedController.state(),
                "the automation waits while the chain of UnionClef does not exist");
        eq("busy", finishedController.reason(),
                "the reason of the wait is written into the status");

        // The chain exists now: the same situation produces the actions.
        finished.chainAvailable = true;
        finished.advance(100);
        finishedController.tick(finished);
        check(!finished.frontPointRequests.isEmpty(),
                "the automation asks for the walk to the front point when the chain of UnionClef exists");

        // The unfinished house: the automation would ask for the materials and for a placement.
        FakeContext building = buildingContext(origin);
        AutomationController buildingController = new AutomationController();
        buildingController.enable();
        buildingController.resume("h1", building);
        building.chainAvailable = false;
        for (int i = 0; i < 5; i++) {
            building.advance(100);
            buildingController.tick(building);
        }
        check(building.sentCommands.isEmpty(),
                "no give is sent while the chain of UnionClef does not exist");
        check(building.placementRequests.isEmpty(),
                "no placement is requested while the chain of UnionClef does not exist");
        building.chainAvailable = true;
        building.inventory.put("minecraft:stone_bricks", 49);
        building.advance(100);
        buildingController.tick(building);
        check(!building.placementRequests.isEmpty(),
                "the automation asks for a placement when the chain of UnionClef exists");
    }

    /**
     * A context in which the house is already complete and the player is not at the front point.
     *
     * <p>The house of the template is written into the world, so that the automation really asks
     * for the walk to the front point. Without a complete house the request could not happen at
     * all, and the closed gate would not be able to distinguish anything.
     */
    private static FakeContext completedHouseContext(Vec3i origin) {
        FakeContext context = buildingContext(origin);
        context.world.applyHouse(HouseTemplate.forVariant(0), origin);
        return context;
    }

    /**
     * A context in which the automation would build a house at the given origin.
     *
     * <p>The world has a free area, the player stands in it and the registry holds the record of
     * the house in the state building.
     */
    private static FakeContext buildingContext(Vec3i origin) {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(origin);
        context.player = origin.add(3, 0, 3);
        context.registry.add(record("h1", context.worldKey, context.dimension, context.playerUuid, origin));
        return context;
    }

    // --- the commands and the public API -------------------------------------------------------------

    /** The commands of the client (ТЗ.md section 6). */
    private static void commandsOfTheClient() {
        try {
            String commands = clientSource("src/main/java/dev/magehome/client/MageHomeCommands.java");
            for (String literal : List.of("enable", "disable", "status", "list", "retry", "resume")) {
                check(commands.contains("ClientCommandManager.literal(\"" + literal + "\")"),
                        "the command " + literal + " is registered");
            }
            check(commands.contains("ClientCommandManager.argument(\"house_id\", StringArgumentType.string())"),
                    "the command resume takes the id of the house");
            check(commands.contains("runtime.controller().enable()"), "the command enable enables the automation");
            check(commands.contains("runtime.controller().disable()"), "the command disable disables the automation");
            check(commands.contains("runtime.verifyHomes()"), "the command status verifies the loaded houses");
            check(commands.contains("runtime.controller().retrySearch()"), "the command retry repeats the search");
            check(commands.contains("runtime.resume(houseId)"), "the command resume resumes the house");
        } catch (Exception e) {
            throw new AssertionError("the client could not be read: " + e.getMessage());
        }
    }

    /** The public API (ТЗ.md section 6). */
    private static void publicApi() {
        try {
            String api = clientSource("src/main/java/dev/magehome/api/MageHomeApi.java");
            check(api.contains("public final class MageHomeApi"), "the class of the public API exists");
            for (String signature : List.of("public static String setEnabled(boolean",
                    "public static String resume(String",
                    "public static String retrySearch()",
                    "public static String getStatusJson()",
                    "public static String getHomesJson()")) {
                check(api.contains(signature), "the public API has the method " + signature);
            }
        } catch (Exception e) {
            throw new AssertionError("the API could not be read: " + e.getMessage());
        }
    }

    /** The shape of the answers (ТЗ.md section 6). */
    private static void apiAnswers() {
        eq("{\"api_version\":1,\"result\":\"accepted\"}", ApiResponses.accepted(),
                "the answer of an accepted mutation");
        eq("{\"api_version\":1,\"result\":\"rejected\",\"reason\":\"unknown_house\"}",
                ApiResponses.rejected("unknown_house"), "the answer of a rejected mutation");
        String status = ApiResponses.status(true, AutomationState.BUILDING, null, "h1", "placement", 49, 180,
                new Vec3i(100, 65, 200), null);
        JsonObject json = JsonParser.parseString(status).getAsJsonObject();
        for (String key : List.of("api_version", "enabled", "state", "reason", "house_id", "stage",
                "confirmed_cells", "total_cells", "current_target", "last_verified_at")) {
            check(json.has(key), "the status has the field " + key);
        }
        eq(180, json.get("total_cells").getAsInt(), "the total number of the cells");
        eq(180, ApiResponses.totalCells(), "the total number of the cells of the project");
        List<String> states = new ArrayList<>();
        for (AutomationState state : AutomationState.values()) {
            states.add(state.wireName());
        }
        eq(6, states.size(), "the number of the states of the automation");
        for (String allowed : List.of("disabled", "idle", "searching", "building", "waiting", "suspended")) {
            check(states.contains(allowed), "the state " + allowed + " is allowed");
        }
    }

    /** The mutations do not block and the readings start nothing (ТЗ.md section 6). */
    private static void apiThreading() {
        try {
            String runtime = clientSource("src/main/java/dev/magehome/client/MageHomeRuntime.java");
            String status = between(runtime, "public String statusJson() {", "\n    }");
            check(!status.contains("pendingActions"), "the reading of the status starts no task");
            check(!status.contains("verifyHomes"), "the reading of the status verifies no house");
            String homes = between(runtime, "public String homesJson() {", "\n    }");
            check(!homes.contains("pendingActions"), "the reading of the homes starts no task");
            check(!homes.contains("verifyHomes"), "the reading of the homes verifies no house");
            String enable = between(runtime, "public String setEnabled(boolean enabled) {", "\n    }");
            check(enable.contains("pendingActions.add("), "the mutation is handed to the client thread");
            String resume = between(runtime, "public String resume(String houseId) {", "\n    }");
            check(resume.contains("pendingActions.add("), "the resume is handed to the client thread");
            String retry = between(runtime, "public String retrySearch() {", "\n    }");
            check(retry.contains("pendingActions.add("), "the retry is handed to the client thread");
            check(runtime.contains("private final Queue<Runnable> pendingActions = new ConcurrentLinkedQueue<>();"),
                    "the actions of another thread wait in a queue");
        } catch (Exception e) {
            throw new AssertionError("the client could not be read: " + e.getMessage());
        }
    }

    /** The state of the automation is separate from the state of the house (ТЗ.md section 6). */
    private static void statusSeparatesTheHouse() {
        FakeContext context = new FakeContext();
        Vec3i origin = firstCandidate(context.player);
        context.world = FakeWorld.withSite(origin);
        HouseTemplate template = HouseTemplate.forVariant(0);
        context.world.applyHouse(template, origin);
        context.registry.add(record("h1", context.worldKey, context.dimension, context.playerUuid, origin));
        AutomationController controller = new AutomationController();
        controller.enable();
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");
        context.player = origin.add(3, 0, -1);
        context.advance(50);
        controller.tick(context);
        JsonObject status = JsonParser.parseString(controller.statusJson()).getAsJsonObject();
        eq("idle", status.get("state").getAsString(), "the automation is idle after the house is ready");
        HouseRecord ready = context.registry.houses().get(0);
        eq(HouseState.READY, ready.state(), "the record of the house is ready");
        eq("ready", ready.state().wireName(), "the state of the house is ready");
        JsonObject homes = JsonParser.parseString(controller.homesJson(context.registry)).getAsJsonObject();
        eq("ready", homes.getAsJsonArray("houses").get(0).getAsJsonObject().get("state").getAsString(),
                "the registry holds the state of the house");
    }

    /** The answers of the resume (ТЗ.md section 6). */
    private static void resumeAnswers() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        eq(ApiResponses.rejected("disabled"), controller.resume("h1", context),
                "the resume needs an enabled automation");
        controller.enable();
        eq(ApiResponses.rejected("unknown_house"), controller.resume("unknown", context),
                "an unknown id is rejected");
        HouseRecord foreign = record("h2", "server|other:25565|default", context.dimension, context.playerUuid,
                firstCandidate(context.player));
        context.registry.add(foreign);
        eq(ApiResponses.rejected("unknown_house"), controller.resume("h2", context),
                "a house of another world is rejected");
        check(context.frontPointRequests.isEmpty(), "the player is not moved to another world");
        Vec3i origin = firstCandidate(context.player);
        HouseTemplate template = HouseTemplate.forVariant(0);
        context.world.applyHouse(template, origin);
        HouseRecord ready = record("h3", context.worldKey, context.dimension, context.playerUuid, origin);
        ready.setState(HouseState.READY);
        context.registry.add(ready);
        eq(ApiResponses.rejected("already_ready"), controller.resume("h3", context),
                "a house that still matches the project is already ready");
        eq(2, context.registry.houses().size(), "the rejected resumes do not create a record");
    }

    /** Only one house is built at a time and a busy chain is not displaced (ТЗ.md section 6). */
    private static void oneBuildingAndBusy() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        eq(1, context.registry.houses().size(), "the record of the house was written");
        controller.enable();
        controller.tick(context);
        eq(1, context.registry.houses().size(), "a repeated enable does not create a second record");
        FakeContext busy = new FakeContext();
        busy.world = FakeWorld.withSite(firstCandidate(busy.player));
        busy.foreign = true;
        AutomationController second = new AutomationController();
        second.enable();
        second.tick(busy);
        eq(AutomationState.WAITING, second.state(), "the state while another owner controls the chain");
        eq("busy", second.reason(), "the reason while another owner controls the chain");
        check(busy.sentCommands.isEmpty(), "no material is asked while another owner controls the chain");
        eq(ApiResponses.rejected("busy"), second.resume("h1", busy),
                "the resume is rejected while the chain belongs to another owner");
        eq(AutomationState.WAITING, second.state(), "the state stays waiting");
        eq("busy", second.reason(), "the reason stays busy");
    }

    // --- the remaining topics of the specification --------------------------------------------------

    /** In 0.1.0 houses are built in the overworld only (ТЗ.md section 1). */
    private static void overworldOnly() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        context.dimension = "minecraft:the_nether";
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        check(context.registry.houses().isEmpty(), "no record is written outside the overworld");
        eq(AutomationState.IDLE, controller.state(), "the automation does nothing outside the overworld");
    }

    /** A missing progress saves a concrete reason and the time of a pause is no work (ТЗ.md section 4). */
    private static void concreteReasonAndPausedTime() {
        FakeContext context = new FakeContext();
        context.world = FakeWorld.withSite(firstCandidate(context.player));
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        eq(1, context.registry.houses().size(), "the record of the house was written");
        context.paused = true;
        for (int i = 0; i < 200; i++) {
            context.advance(1000);
            controller.tick(context);
        }
        eq(AutomationState.WAITING, controller.state(), "the automation waits while UnionClef is paused");
        eq("busy", controller.reason(), "the reason of the wait is concrete");
        check(context.sentCommands.isEmpty(), "no material is asked while UnionClef is paused");
        context.paused = false;
        context.advance(50);
        controller.tick(context);
        check(controller.state() != AutomationState.SUSPENDED,
                "the time of the pause is not the time of the work");
        try {
            String source = clientSource("core/src/main/java/dev/magehome/core/automation/AutomationController.java");
            check(!source.contains("LIFETIME") && !source.contains("MAX_BUILD"),
                    "the building has no overall time limit");
        } catch (Exception e) {
            throw new AssertionError("the controller could not be read: " + e.getMessage());
        }
    }

    /** An explicit resume starts a new attempt on the same house (ТЗ.md section 4). */
    private static void resumeStartsANewAttempt() {
        FakeContext context = new FakeContext();
        Vec3i origin = firstCandidate(context.player);
        context.world = FakeWorld.withSite(origin);
        context.inventory.put("minecraft:stone_bricks", 49);
        AutomationController controller = new AutomationController();
        controller.enable();
        controller.tick(context);
        eq(1, context.registry.houses().size(), "the record of the house was written");
        HouseRecord record = context.registry.houses().get(0);
        controller.interrupt(context, SuspensionReason.INTERRUPTED.wireName());
        eq(HouseState.SUSPENDED, record.state(), "the house is suspended after the interruption");
        eq(ApiResponses.accepted(), controller.resume(record.houseId(), context), "the resume is accepted");
        eq(1, context.registry.houses().size(), "the resume does not create a second record");
        HouseRecord resumed = context.registry.houses().get(0);
        eq(record.houseId(), resumed.houseId(), "the same house is continued");
        eq(HouseState.BUILDING, resumed.state(), "the new attempt builds the same house again");
        eq(null, resumed.reason(), "the reason of the suspension is cleared");
        eq(AutomationState.BUILDING, controller.state(), "the automation builds again");
        int requestsBefore = context.placementRequests.size();
        context.advance(50);
        controller.tick(context);
        check(context.placementRequests.size() > requestsBefore,
                "the new attempt asks for a placement again, the limit of the old attempt is over");
    }

    /** The inventory of the checks covers the topics of the automatic tests (ТЗ.md section 8). */
    private static void testInventoryCoversTheTopics() {
        List<String> ids = new ArrayList<>();
        for (Check check : all()) {
            ids.add(check.id());
        }
        eq(ids.size(), new HashSet<>(ids).size(), "every check has its own id");
        // The topics of ТЗ.md section 8 have a named check each.
        for (String topic : List.of("T-A2-08", "T-A2-09", "T-A2-10", "T-A4-01", "T-A4-02", "T-A4-03",
                "T-A4-05", "T-A4-07", "T-A4-09", "T-A4-13", "T-A5-03", "T-A5-08", "T-A6-04", "T-A3-07",
                "T-A11-03")) {
            check(ids.contains(topic), "the inventory holds the check " + topic);
        }
    }

    // --- the facts of the delivered UnionClef (ТЗ.md section 7) -------------------------------------

    /** The bytes of a class of the delivered UnionClef jar. */
    private static byte[] unionclefClass(String entry) throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        Path jar = root.resolve("libs/unionclef-1.21.11-0.95.61.jar");
        check(Files.isRegularFile(jar), "the delivered UnionClef jar exists");
        try (ZipFile zip = new ZipFile(jar.toFile(), StandardCharsets.UTF_8)) {
            ZipEntry zipEntry = zip.getEntry(entry);
            check(zipEntry != null, "the class " + entry + " is in the delivered jar");
            try (InputStream in = zip.getInputStream(zipEntry)) {
                return in.readAllBytes();
            }
        }
    }

    /** True when the constant pool of the class file holds the given name. */
    private static boolean classFileHoldsName(byte[] classBytes, String name) {
        byte[] needle = name.getBytes(StandardCharsets.UTF_8);
        outer:
        for (int i = 0; i + needle.length <= classBytes.length; i++) {
            for (int j = 0; j < needle.length; j++) {
                if (classBytes[i + j] != needle[j]) {
                    continue outer;
                }
            }
            return true;
        }
        return false;
    }

    /**
     * The client uses exactly the API of the delivered UnionClef (ТЗ.md section 7).
     *
     * <p>The facts of the API are read from the delivered jar itself: the class of the task and
     * the class of the mod have to hold the members that the specification names. The client has
     * to call exactly these members and its own task has to implement the four abstract methods
     * of the task of UnionClef.
     */
    private static void unionclefApi() throws Exception {
        byte[] task = unionclefClass("adris/altoclef/tasksystem/Task.class");
        for (String name : List.of("onStart", "onTick", "onStop", "isEqual", "isFinished", "isActive")) {
            check(classFileHoldsName(task, name), "the task of UnionClef declares " + name);
        }
        byte[] altoClef = unionclefClass("adris/altoclef/AltoClef.class");
        for (String name : List.of("getInstance", "runUserTask", "getUserTaskChain", "isPaused", "getItemStorage",
                "getBehaviour")) {
            check(classFileHoldsName(altoClef, name), "the mod of UnionClef declares " + name);
        }
        unionclefClass("adris/altoclef/tasks/movement/GetToBlockTask.class");

        String bridge = clientSource("src/main/java/dev/magehome/client/UnionClefBridge.java");
        check(bridge.contains("AltoClef.getInstance()"), "the bridge reads the instance of UnionClef");
        check(bridge.contains("mod.runUserTask(task)"), "the bridge starts the own task with runUserTask");
        check(bridge.contains("getUserTaskChain()"), "the bridge reads the chain of the tasks of UnionClef");
        check(bridge.contains("getCurrentTask()"), "the bridge reads the current task of the chain");
        check(!bridge.contains("mod.getUserTaskChain().getCurrentTask()"),
                "the bridge never reads the current task of a chain that does not exist yet");
        check(bridge.contains("mod.isPaused()"), "the bridge reads the pause of UnionClef");
        check(bridge.contains("mod.getItemStorage().getItemCountInventoryOnly(item)"),
                "the bridge counts the items of the inventory");
        check(bridge.contains("mod.getBehaviour()"), "the bridge reads the behaviour of UnionClef");

        String runtime = clientSource("src/main/java/dev/magehome/client/MageHomeRuntime.java");
        check(runtime.contains("new GetToBlockTask(navigationTarget)"),
                "the navigation uses GetToBlockTask with the position of the target");

        String ownTask = clientSource("src/main/java/dev/magehome/client/MageHomeTask.java");
        check(ownTask.contains("protected void onStart()"), "the own task implements onStart()");
        check(ownTask.contains("protected Task onTick()"), "the own task implements onTick():Task");
        check(ownTask.contains("protected void onStop(Task task)"), "the own task implements onStop(Task)");
        check(ownTask.contains("protected boolean isEqual(Task task)"), "the own task implements isEqual(Task)");
    }

    /**
     * The special blocks are placed with the real vanilla placement API (ТЗ.md section 7).
     *
     * <p>PlaceBlockTask is not used at all, so a ready BlockState can never be handed to it. The
     * client asks the vanilla API which state the block would get, checks the cell and the face
     * and compares the state with the state of the project.
     */
    private static void vanillaPlacementApi() throws Exception {
        String placement = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        check(!placement.contains("PlaceBlockTask"),
                "the client does not use PlaceBlockTask, so it never passes a ready BlockState");
        check(placement.contains("new ItemPlacementContext("), "the client asks the vanilla placement API");
        check(placement.contains("context.getBlockPos().equals(wanted)"), "the client checks the cell of the placement");
        check(placement.contains("context.canPlace()"), "the client checks that the face of the support is available");
        check(placement.contains("block.getPlacementState(context)"), "the client reads the state of the placement");
        check(placement.contains("matches(state, target.requiredProperties())"),
                "the client compares the state with the state of the project");

        // The special blocks of the project are the ones that need the orientation of the vanilla
        // API: the door, the bed, the barrel, the furnace, the lantern and the bookshelf.
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<String> blocks = new HashSet<>();
        for (CellSpec spec : template.cells().values()) {
            blocks.add(spec.blockId());
        }
        for (String special : List.of("minecraft:spruce_door", "minecraft:barrel", "minecraft:furnace",
                "minecraft:lantern", "minecraft:bookshelf")) {
            check(blocks.contains(special), "the project holds the special block " + special);
        }
        int withProperties = 0;
        for (CellSpec spec : template.cells().values()) {
            if (!spec.requiredProperties().isEmpty()) {
                withProperties++;
            }
        }
        check(withProperties > 0, "the project asks for the properties of the special blocks");
    }

    /**
     * The navigation breaks no block, the add-on places no block outside the project and the
     * behaviour of UnionClef is balanced on every exit (ТЗ.md section 7).
     *
     * <p>The owner allowed temporary blocks and scaffolds on 2026-11-13 (ТЗ.md section 4). They
     * are an aid of the approach and not a part of the project: the add-on itself still places no
     * block outside the project and breaks a block only to remove one of the four allowed plants
     * and in the chosen repair.
     */
    private static void navigationWithoutDamage() throws Exception {
        String placement = clientSource("src/main/java/dev/magehome/client/PlacementExecutor.java");
        // The client attacks in three places only: the removal of the four allowed plants, the
        // chosen repair of a wrong cell of the volume of a registered house (ТЗ.md section 4)
        // and the removal of the temporary support of the climb that the add-on built itself
        // (the temporary blocks of the 2026-11-13).
        // The repair breaks the block with the ordinary destruction of the game
        // (updateBlockBreakingProgress), the removal of the plants with the ordinary attack
        // (attackBlock); no other place of the client breaks a block.
        eq(3, countOccurrences(placement, "attackBlock(")
                        + countOccurrences(placement, "updateBlockBreakingProgress("),
                "the client breaks a block only to remove one of the four allowed plants, in the "
                        + "chosen repair and in the removal of its own temporary support");
        check(placement.contains("ALLOWED_PLANTS.contains(id)"), "the attack is limited to the allowed plants");
        String repair = between(placement, "private void repairTick()", "private BlockPos repairAccess()");
        check(repair.contains("interactionManager.updateBlockBreakingProgress("),
                "the second break belongs to the chosen repair of a wrong cell");
        String support = between(placement, "private boolean removeScaffoldTick()",
                "private BlockPos scaffoldAccess(");
        check(support.contains("interactionManager.updateBlockBreakingProgress("),
                "the third break belongs to the removal of the temporary support of the climb");
        for (String plant : List.of("minecraft:short_grass", "minecraft:tall_grass", "minecraft:fern",
                "minecraft:large_fern")) {
            check(placement.contains(plant), "the plant " + plant + " may be removed");
        }

        // The add-on names no block outside the project: it cannot place a temporary block or a
        // scaffold by itself, and it cannot dig a block either. The temporary blocks of the
        // navigation are allowed since the 2026-11-13, but they are no part of the project.
        Set<String> allowed = Set.of("minecraft:short_grass", "minecraft:tall_grass", "minecraft:fern",
                "minecraft:large_fern", "minecraft:air", "minecraft:overworld");
        for (Path file : clientFiles()) {
            String text = Files.readString(file, StandardCharsets.UTF_8);
            for (String id : blockIds(text)) {
                check(allowed.contains(id),
                        "the source " + file.getFileName() + " names no block outside the project: " + id);
            }
        }

        // The behaviour is pushed once for a start and popped once for a stop. The rule lives in
        // the ledger of the core (dev.magehome.core.automation.BehaviourBalance), so the client
        // asks the ledger and the ledger decides; the check of the ledger itself is T-A18-04.
        String runtime = clientSource("src/main/java/dev/magehome/client/MageHomeRuntime.java");
        check(runtime.contains("if (behaviour.onStart())"), "the behaviour is pushed only once for a start");
        check(runtime.contains("if (behaviour.onStop())"), "the behaviour is popped only when it was pushed");
        String bridge = clientSource("src/main/java/dev/magehome/client/UnionClefBridge.java");
        check(bridge.contains("mod.getBehaviour().push()"), "the bridge pushes the behaviour of UnionClef");
        check(bridge.contains("mod.getBehaviour().pop()"), "the bridge pops the behaviour of UnionClef");
    }

    // --- the search and the plan (ТЗ.md sections 3 and 4) -----------------------------------------

    /**
     * A failed candidate is skipped and the search is shifted by eight blocks (ТЗ.md section 3).
     */
    private static void failedCandidateIsSkipped() {
        SiteSearch search = new SiteSearch(new Vec3i(0, 64, 0));
        List<Vec3i> first = search.nextBatch();
        check(!first.isEmpty(), "the search offers candidates");
        Vec3i rejected = first.get(0);
        search.reject(rejected);
        List<Vec3i> second = search.nextBatch();
        check(!second.contains(rejected), "a rejected candidate is not offered again");

        eq(8, SiteRules.RETRY_SHIFT, "the shift of the specification is eight blocks");
        Vec3i base = search.base();
        search.shift(SiteRules.RETRY_SHIFT);
        eq(base.add(SiteRules.RETRY_SHIFT, 0, 0), search.base(), "the search is shifted by eight blocks");

        // The search never swims, digs or builds a bridge: the automation waits and sends
        // nothing.
        FakeContext context = new FakeContext();
        context.world = new FakeWorld();
        AutomationController controller = new AutomationController();
        controller.enable();
        for (int i = 0; i < 800; i++) {
            controller.tick(context);
            context.advance(50);
            if ("no_site".equals(controller.reason())) {
                break;
            }
        }
        eq(AutomationState.WAITING, controller.state(), "without a site the automation waits");
        eq("no_site", controller.reason(), "the reason is no_site");
        check(context.sentCommands.isEmpty(), "no give command while waiting");
        check(context.placementRequests.isEmpty(), "no placement while waiting");
        check(context.frontPointRequests.isEmpty(), "no movement while waiting");
        check(context.registry.houses().isEmpty(), "no record is written for an unusable candidate");
    }

    /**
     * Already correct cells are skipped and a finished stage is not installed again
     * (ТЗ.md section 4).
     */
    private static void finishedStageIsNotInstalledAgain() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<Cell> ground = groundCells();
        PlacementPlan plan = PlacementPlanner.plan(template, Set.of(), ground::contains);
        check(plan.complete(), "the house can be placed completely");

        // The floor is standing completely: the second plan does not touch it again.
        Set<Cell> built = new HashSet<>(ground);
        for (int i = 0; i < 49; i++) {
            built.addAll(plan.placements().get(i).operation().cells());
        }
        PlacementPlan second = PlacementPlanner.plan(template, built, ground::contains);
        check(second.complete(), "the rest of the house can be planned");
        int floorAgain = 0;
        for (PlannedPlacement planned : second.placements()) {
            for (Cell cell : planned.operation().cells()) {
                check(!built.contains(cell), "the cell " + cell + " is not placed a second time");
                if (cell.y() == 0) {
                    floorAgain++;
                }
            }
        }
        eq(0, floorAgain, "the finished floor is not installed again");
        eq(180 - 49, countCells(second), "only the missing cells are planned");
    }

    /**
     * Without an admissible operation the house is suspended with the reason and the coordinate
     * (ТЗ.md section 4).
     */
    private static void noPlacementSupport() {
        FakeContext context = new FakeContext();
        Vec3i origin = new Vec3i(100, 64, 200);
        context.world = FakeWorld.withSite(origin);
        context.inventory.put("minecraft:stone_bricks", 49);
        // The client finds no admissible way to place the operation with the blocks that are
        // there: the house has to wait with a concrete reason and the coordinate of the cell.
        context.placementPossible = false;
        HouseRecord record = record("h1", context.worldKey, context.dimension, context.playerUuid, origin);
        record.setState(HouseState.BUILDING);
        context.registry.add(record);
        AutomationController controller = new AutomationController();
        controller.enable();
        eq(ApiResponses.accepted(), controller.resume("h1", context), "the resume is accepted");
        controller.tick(context);
        eq(AutomationState.SUSPENDED, controller.state(), "the house is suspended without an admissible operation");
        eq("no_placement_support", controller.reason(), "the reason is no_placement_support");
        check(record.reason() != null && record.reason().startsWith("no_placement_support@"),
                "the record of the house carries the reason: " + record.reason());
        String detail = record.reason() == null ? "" : record.reason().substring("no_placement_support@".length());
        check(detail.split(",").length == 3, "the reason carries the coordinate of the cell: " + detail);
        check(detail.startsWith(origin.x() + ","), "the coordinate is the coordinate of the cell: " + detail);
        check(context.placementRequests.isEmpty(), "no block is placed without an admissible operation");
        check(record.state() == HouseState.SUSPENDED, "the record of the house is suspended");
    }

    /**
     * The complete project can be planned; a scaffold is not a permanent project block.
     */
    private static void noScaffolding() {
        HouseTemplate template = HouseTemplate.forVariant(0);
        Set<Cell> ground = groundCells();
        PlacementPlan plan = PlacementPlanner.plan(template, Set.of(), ground::contains);
        check(plan.complete(),
                "the complete abstract plan has structural supports: " + plan.unsupportedCell());
        eq(180, countCells(plan), "every occupied cell of the house is filled");

        // The blocks of the project are the blocks of the palette and the special blocks; there is
        // no block of a scaffold in the template.
        Set<String> blocks = new HashSet<>();
        for (CellSpec spec : template.cells().values()) {
            blocks.add(spec.blockId());
        }
        Set<String> known = new HashSet<>(Set.of("minecraft:glass", "minecraft:spruce_door", "minecraft:barrel",
                "minecraft:crafting_table", "minecraft:furnace", "minecraft:bookshelf", "minecraft:lantern"));
        for (int variant = 0; variant < 3; variant++) {
            Palette palette = HouseTemplate.forVariant(variant).palette();
            known.add(palette.floor());
            known.add(palette.post());
            known.add(palette.wall());
            known.add(palette.roof());
            known.add(palette.bed());
        }
        for (String block : blocks) {
            check(known.contains(block), "the block " + block + " belongs to the project");
        }
    }

    // --- the delivery (ТЗ.md sections 6 and 8) ------------------------------------------------------

    /**
     * A missing world makes the cases of the game skipped and never passed (ТЗ.md section 8).
     */
    private static void missingWorldIsSkipped() throws Exception {
        String harness = clientSource("src/main/java/dev/magehome/client/AcceptanceRunner.java");
        check(harness.contains("AcceptanceReport.State.SKIPPED"), "the harness marks a case as skipped");
        check(harness.contains("LAB_ACCEPTANCE_TEST:{}:SKIPPED:{}"), "the harness writes SKIPPED for a missing world");
        check(harness.contains("the world of the stand was not loaded"), "the harness names the missing world");
        check(!harness.contains("LAB_ACCEPTANCE_TEST:{}:PASS:the world of the stand was not loaded"),
                "the harness never passes a case without a world");

        // A stand that cannot show the situation of the scenario is not a failure of the product:
        // the scenario waits for the chain of UnionClef in a bounded window and is then skipped
        // with the reason, and the aggregate of the scenario is never failed for that reason.
        String flat = harness.replaceAll("\\s+", " ");
        check(harness.contains("CHAIN_WAIT_TIMEOUT_MILLIS"),
                "the scenario waits for the chain of UnionClef in a bounded window");
        check(flat.contains("new AcceptanceReport.Case(scenarioId, AcceptanceReport.State.SKIPPED, reason)"),
                "the aggregate of a scenario that could not be shown is skipped with the reason");
        check(!flat.contains("AcceptanceReport.State.FAILED, reason)"),
                "the reason of a stand that cannot show the scenario never fails the aggregate");
        check(harness.contains("finishUnverified"), "the harness has one path for an unverifiable scenario");

        String report = clientSource("src/main/java/dev/magehome/client/AcceptanceReport.java");
        check(report.contains("SKIPPED"), "the report of the scenario knows the state skipped");
        // Раунд 6: пропущенный кейс пишется как пропуск вместе с причиной, чтобы исполнитель
        // читал в отчёте, почему поведение не было проверено.
        check(report.contains("<skipped message="), "the report of the scenario writes a skipped case with its reason");
    }

    /**
     * The delivery holds the sources, the instruction and the artifact of the build
     * (ТЗ.md section 8).
     */
    private static void deliveryOfTheProject() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        check(Files.isRegularFile(root.resolve("README.md")), "the instruction is in the project");
        String readme = Files.readString(root.resolve("README.md"), StandardCharsets.UTF_8);
        var buildProperties = new java.util.Properties();
        try (var reader = Files.newBufferedReader(root.resolve("gradle.properties"), StandardCharsets.UTF_8)) {
            buildProperties.load(reader);
        }
        String artifact = "mage_home-" + buildProperties.getProperty("mod_version") + ".jar";
        check(readme.contains("build/libs/" + artifact), "the instruction names the artifact of the build");
        check(readme.contains("./gradlew build"), "the instruction names the build of the artifact");
        check(Files.isDirectory(root.resolve("src/main/java")), "the sources of the mod are in the project");
        check(Files.isDirectory(root.resolve("core/src/main/java")), "the sources of the core are in the project");
        check(Files.isRegularFile(root.resolve("src/main/resources/fabric.mod.json")),
                "the metadata of the mod is in the project");

        // The artifact of the build. The check is unconditional: the JAR of the client is built
        // before the checks run, so a missing artifact is a failure of the delivery and not a
        // silent success of the check.
        Path libs = root.resolve("build/libs");
        check(Files.isDirectory(libs), "the build produced the directory of the artifact: " + libs);
        Path jar = libs.resolve(artifact);
        check(Files.isRegularFile(jar), "the build produced the client jar in " + libs);
        try (ZipFile zip = new ZipFile(jar.toFile(), StandardCharsets.UTF_8)) {
            check(zip.getEntry("fabric.mod.json") != null, "the artifact of the build is the mod");
            check(zip.getEntry("dev/magehome/core/automation/AutomationController.class") != null,
                    "the artifact of the build holds the domain core");
        }
    }

    // --- the helpers of the new checks ----------------------------------------------------------------

    /** The number of cells that the plan fills. */
    private static int countCells(PlacementPlan plan) {
        int cells = 0;
        for (PlannedPlacement planned : plan.placements()) {
            cells += planned.operation().cells().size();
        }
        return cells;
    }

    /** The number of times the text holds the fragment. */
    private static int countOccurrences(String text, String fragment) {
        int count = 0;
        int index = text.indexOf(fragment);
        while (index >= 0) {
            count++;
            index = text.indexOf(fragment, index + fragment.length());
        }
        return count;
    }

    /** The java files of the client mod. */
    private static List<Path> clientFiles() throws Exception {
        Path root = projectRoot();
        check(root != null, "the root of the project was found");
        Path directory = root.resolve("src/main/java/dev/magehome/client");
        check(Files.isDirectory(directory), "the sources of the client are in the project");
        try (var files = Files.list(directory)) {
            return files.filter(file -> file.getFileName().toString().endsWith(".java")).sorted().toList();
        }
    }

    /** The ids of the blocks that a text names. */
    private static List<String> blockIds(String text) {
        List<String> ids = new ArrayList<>();
        int index = text.indexOf("minecraft:");
        while (index >= 0) {
            int end = index + "minecraft:".length();
            while (end < text.length() && (Character.isLetterOrDigit(text.charAt(end)) || text.charAt(end) == '_')) {
                end++;
            }
            ids.add(text.substring(index, end));
            index = text.indexOf("minecraft:", end);
        }
        return ids;
    }
}
