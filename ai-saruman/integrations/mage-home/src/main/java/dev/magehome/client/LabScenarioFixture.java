package dev.magehome.client;

import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.registry.HouseRecord;
import dev.magehome.core.registry.HouseRegistry;
import dev.magehome.core.registry.HouseState;
import dev.magehome.core.registry.RegistryFileStore;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.template.HouseTemplate;
import dev.magehome.core.verification.CellVerdict;
import dev.magehome.core.verification.CellVerifier;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.block.DoorBlock;
import net.minecraft.block.enums.DoorHinge;
import net.minecraft.block.enums.DoubleBlockHalf;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.world.LevelLoadingScreen;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.registry.Registries;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.state.property.Property;
import net.minecraft.util.ActionResult;
import net.minecraft.util.Hand;
import net.minecraft.util.Identifier;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;

/** Integrated-server fixture setup; unreachable without lab.acceptance.scenario. */
final class LabScenarioFixture {

    private static final Logger LOGGER = LoggerFactory.getLogger("mage_home/lab-stand");

    /**
     * The number of ticks that one gate of the preparation may wait.
     *
     * <p>The preparation of the stand is not allowed to wait for ever: every gate of it has a
     * name and a budget, and when the budget is spent the scenario ends with the named reason
     * instead of hanging until the timeout of the command.
     */
    private static final int GATE_WAIT_TICKS = 200;

    /**
     * The cell of the volume of the house that holds the wrong block of the scenario.
     *
     * <p>The cell is a corner cell of the floor: the player stands on the platform of the stand
     * next to the house and breaks the block with an ordinary attack, without entering the house.
     */
    static final Cell REPAIR_CELL = new Cell(0, 0, 0);

    /**
     * The cell of the volume of the house that holds the lone half of the door.
     *
     * <p>The half belongs to the project, but the pair of the door is incomplete: the half is not
     * overwritten by a new installation, it is eliminated by the safe repair and the pair is
     * placed again (ТЗ.md section 4).
     */
    static final Cell LONE_HALF_CELL = HouseTemplate.DOOR_LOWER;

    /**
     * The cell of the volume of the house that holds the foreign block (a tree).
     *
     * <p>A foreign block is a block that the project does not place: the add-on never breaks it
     * and the house waits for the player with the reason obstructed.
     */
    static final Cell FOREIGN_LOG_CELL = new Cell(4, 0, 3);

    /**
     * The cell of the volume of the house that holds the second foreign block.
     *
     * <p>The block is a full block of earth: it survives on the floor of the house and it is not
     * a material of the project, so the rule of the safe repair treats it like the tree.
     */
    static final Cell FOREIGN_EARTH_CELL = new Cell(5, 0, 5);

    /**
     * The cell of the volume of the house that holds the protected foreign block.
     *
     * <p>A container is never broken: the client does not read its content, so the rule of the
     * safe repair protects it and the house is suspended with the coordinate and the reason
     * (ТЗ.md section 4). The cell stands after the two repairable cells in the order of the
     * volume, so that the scenario observes the repair first and the suspension afterwards.
     */
    static final Cell FOREIGN_CONTAINER_CELL = new Cell(6, 0, 6);

    /** The number of the confirmed cells of the house of the scenario of the repair. */
    static final int REPAIR_CONFIRMED_CELLS = 48;

    private CompletableFuture<Void> prepared;
    private HouseRegistry seeded;
    private boolean saved;
    private HouseRecord persistenceSeed;
    private Vec3i initialPlayer;
    /** The facts of the preparation of the scenario of the repair; they are read on the server. */
    private volatile String repairFact = "none";
    /** The empty post-probe inventory was observed before any repair materials were issued. */
    private boolean initialInventoryObserved;
    private CompletableFuture<java.util.Map<String, Integer>> initialInventorySnapshot;
    /** One server-thread issuance; never resubmit it while waiting for synchronization. */
    private CompletableFuture<Void> repairMaterialsIssue;
    /** A server-thread snapshot of the material counts, refreshed while waiting. */
    private CompletableFuture<java.util.Map<String, Integer>> repairInventorySnapshot;
    /** The state of the probe of the environment: 0 = not started, 4 = done. */
    private int probeState;
    /** The cell of the probe of the environment. */
    private BlockPos probeCell;
    /** The number of ticks the probe waited for the world. */
    private int probeTicks;
    /** The result of the call of the interaction of the probe. */
    private String probeResult = "none";
    /** The fact of the probe of the environment. */
    private String probeFact = "none";
    /** The number of the interactions that the probe of the environment sent. */
    private int probeAttempts;
    /** The name of the gate of the preparation that is running. */
    private String gate = "none";
    /** The key of the world of the stand; it names the world in the facts of the gates. */
    private String worldKey = "none";
    /** The fact of the gate of the preparation that is running. */
    private String gateFact = "none";
    /** The number of ticks the current gate waited. */
    private final java.util.Map<String, Integer> gateTicks = new java.util.HashMap<>();
    /** The facts of the gates of the preparation, in the order of their passing. */
    private final List<String> gateFacts = new ArrayList<>();
    private boolean siteGateObserved;

    /**
     * Names the gate of the preparation that is running and counts its wait.
     *
     * <p>Every gate of the preparation has a name and a budget. The name and the fact of the
     * gate are written into the log of the client and into the journal of the scenario, so that a
     * run that stops inside the preparation says which gate it stopped in. When the budget of the
     * gate is spent, the preparation fails with the name of the gate: the scenario ends as "not
     * verified because of the preparation" and never as a failure of the add-on.
     *
     * @param name the name of the gate
     * @param fact what the gate observed
     * @return true while the gate may still wait
     */
    private boolean gate(String name, String fact) {
        this.gate = name;
        int ticks = gateTicks.merge(name, 1, Integer::sum);
        if (!fact.equals(this.gateFact)) {
            this.gateFact = fact;
            gateFacts.add(name + " " + fact);
            LOGGER.info("LAB_OBSERVE fixture_gate:{} {}", name, fact);
        }
        return ticks <= GATE_WAIT_TICKS;
    }

    /**
     * The failure of the preparation of the stand with the name of the gate.
     *
     * @param name the name of the gate
     * @param fact what the gate observed
     * @return the exception that names the gate
     */
    private IllegalStateException gateFailure(String name, String fact) {
        return new IllegalStateException("the preparation of the stand did not pass the gate "
                + name + ": " + fact);
    }

    /**
     * The facts of the gates of the preparation.
     *
     * @return the facts of the gates as one line of text
     */
    String gateFacts() {
        return String.join(" | ", gateFacts);
    }

    boolean prepare(String scenarioId, MageHomeRuntime runtime) {
        if (scenarioId == null || scenarioId.isBlank()
                || !scenarioId.equals(System.getProperty("lab.acceptance.scenario"))) {
            throw new IllegalStateException("fixture requires an explicit matching lab.acceptance.scenario");
        }
        boolean repair = "safe-repair-of-wrong-block".equals(scenarioId);
        boolean foreign = "safe-repair-of-foreign-block".equals(scenarioId);
        boolean safeRepair = repair || foreign;
        // A joined client can still show the terrain-loading screen. Wait for
        // normal gameplay before changing the fixture or starting any action.
        // This is an observable client state, independent of capture settings.
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.currentScreen instanceof LevelLoadingScreen) {
            if (!gate("world", "the world of the stand is still loading")) {
                throw gateFailure("world", "the world of the stand is still loading");
            }
            return false;
        }
        if ("persistence-after-exit".equals(scenarioId) && !seedPhase()) {
            if (!"reload".equals(System.getProperty("lab.acceptance.fixture.phase"))) {
                throw new IllegalStateException("persistence requires runPersistenceCheck (two client JVMs)");
            }
            return inspectPersistence(runtime);
        }
        var server = client.getServer();
        if (server == null || client.player == null || client.world == null) {
            if (!gate("world", "the client, the server, the player or the world is missing")) {
                throw gateFailure("world", "the client, the server, the player or the world is missing");
            }
            return false;
        }
        if (prepared == null) {
            Vec3i player = runtime.context().playerPosition();
            initialPlayer = player;
            String worldKey = runtime.context().worldKey();
            this.worldKey = worldKey;
            String dimension = runtime.context().dimension();
            String playerUuid = runtime.context().playerUuid();
            var playerId = client.player.getUuid();
            var worldId = client.world.getRegistryKey();
            boolean water = "site-blocked-by-water".equals(scenarioId);
            seeded = new HouseRegistry();
            Vec3i origin = switch (scenarioId) {
                case "next-palette-after-radius" -> player.add(-115, 0, -3);
                case "safe-repair-of-wrong-block", "safe-repair-of-foreign-block" -> player.add(-8, 0, -6);
                default -> player.add(-16, 0, -3);
            };
            boolean partial = "persistence-after-exit".equals(scenarioId);
            boolean readyHouse = "no-new-house-inside-radius".equals(scenarioId)
                    || "next-palette-after-radius".equals(scenarioId);
            if (partial || readyHouse) {
                HouseRecord record = new HouseRecord("lab-seed-house", worldKey, dimension, playerUuid,
                        origin, 0, partial ? HouseState.BUILDING : HouseState.READY,
                        partial ? "placement" : "ready", partial ? 49 : HouseTemplate.OCCUPIED_CELLS,
                        Instant.now().toString(), null);
                record.setTemplateId(dev.magehome.core.automation.AutomationController.CURRENT_TEMPLATE_ID);
                seeded.add(record);
                persistenceSeed = partial ? record : null;
            }
            if (safeRepair) {
                // The house of the scenario of the repair is registered before the run: its floor
                // stands in the world and one cell of the volume holds the block of the scenario.
                // The record has confirmed cells, therefore the site is not searched again.
                HouseRecord record = new HouseRecord("lab-repair-house", worldKey, dimension, playerUuid,
                        origin, 0, HouseState.BUILDING, "placement", REPAIR_CONFIRMED_CELLS,
                        Instant.now().toString(), null);
                record.setTemplateId(dev.magehome.core.automation.AutomationController.CURRENT_TEMPLATE_ID);
                seeded.add(record);
            }
            prepared = CompletableFuture.runAsync(() -> {
                ServerWorld world = server.getWorld(worldId);
                if (world == null) throw new IllegalStateException("integrated server world missing");
                // The stand protects the player from the mobs of the world: in the round 10 the
                // player of the scenario was slain by a slime, and a dead player shows nothing of
                // the add-on. The difficulty belongs to the preparation of the laboratory and
                // not to the add-on.
                server.setDifficulty(net.minecraft.world.Difficulty.PEACEFUL, false);
                // Cover every site centre in the product's 32-block search window, plus its footprint.
                platform(world, player, 40, water);
                if (partial || readyHouse) {
                    platform(world, origin.add(3, 0, 3), 8, false);
                    seedHouse(world, origin, partial);
                    inspectServerHouse(world, origin, partial);
                }
                if (safeRepair) {
                    seedRepairHouse(world, origin, foreign);
                    repairFact = inspectRepairHouse(world, origin);
                    LOGGER.info("LAB_OBSERVE fixture_repair: {}", repairFact);
                    if (repair && !repairFact.contains("wrong_block=" + repairWrongBlock())) {
                        throw new IllegalStateException("the wrong block of the scenario is missing: "
                                + repairFact);
                    }
                    if (foreign && !repairFact.contains("foreign_log=" + foreignLogBlock())) {
                        throw new IllegalStateException("the foreign block of the scenario is missing: "
                                + repairFact);
                    }
                    if (foreign && !repairFact.contains("foreign_earth=" + foreignEarthBlock())) {
                        throw new IllegalStateException("the second foreign block of the scenario is missing: "
                                + repairFact);
                    }
                    if (foreign && !repairFact.contains("foreign_container=" + foreignContainerBlock())) {
                        throw new IllegalStateException("the protected foreign block of the scenario is "
                                + "missing: " + repairFact);
                    }
                }
                var serverPlayer = server.getPlayerManager().getPlayer(playerId);
                if (serverPlayer == null) throw new IllegalStateException("server player missing");
                serverPlayer.getInventory().clear();
                serverPlayer.currentScreenHandler.sendContentUpdates();
                serverPlayer.changeGameMode(net.minecraft.world.GameMode.SURVIVAL);
                if (water) {
                    // A single dry standing block protects the player; it cannot support a 7x7 site.
                    world.setBlockState(pos(player), Blocks.STONE.getDefaultState(), 2);
                    serverPlayer.requestTeleport(player.x() + 0.5, player.y() + 1, player.z() + 0.5);
                }
            }, server::execute);
            return false;
        }
        if (!prepared.isDone()) return false;
        prepared.join(); // Surface setup failures to the independent execution journal.
        if (!saved) {
            saved = true;
            runtime.registryService().save(seeded);
            return false;
        }
        if (runtime.registryService().writePending()) {
            if (!gate("registry", "the registry of the stand is being written")) {
                throw gateFailure("registry", "the registry of the stand is being written");
            }
            return false;
        }
        if (runtime.registryService().writeFailed()) throw new IllegalStateException("fixture registry save failed");
        var platform = dev.magehome.core.acceptance.PlatformFixture.inspect(runtime.context().world(),
                runtime.context().playerPosition());
        boolean water = "site-blocked-by-water".equals(scenarioId);
        var wanted = water ? dev.magehome.core.acceptance.PlatformFixture.Kind.WATER_COVERED
                : dev.magehome.core.acceptance.PlatformFixture.Kind.BUILDABLE;
        // The fact of the platform names what the laboratory prepared and what the product would
        // use: the platform is looked for in the same order in which the product looks for its
        // site, so that the preparation and the product speak about the same place.
        String platformFact = "platform kind=" + platform.kind() + " origin=" + platform.origin()
                + " candidates=" + platform.candidates() + " detail=" + platform.detail();
        if (platform.kind() != wanted) {
            if (!gate("site", platformFact)) {
                throw gateFailure("site", platformFact);
            }
            return false;
        }
        if (!siteGateObserved) {
            gate("site", platformFact);
            siteGateObserved = true;
        }
        if (water && runtime.context().playerPosition().y() != initialPlayer.y() + 1) {
            if (!gate("site", "the player of the scenario was not put on the dry block")) {
                throw gateFailure("site", "the player of the scenario was not put on the dry block");
            }
            return false;
        }
        if (persistenceSeed != null && !inspectSeedWorld(runtime, persistenceSeed, true)) return false;
        if (persistenceSeed != null) {
            try {
                new RegistryFileStore(seedReceipt(runtime)).save(seeded);
            } catch (Exception e) {
                throw new IllegalStateException("cannot persist seed receipt", e);
            }
        }
        if (!probe(scenarioId, water)) return false;
        // The inventory of the client has to be empty before the product acts: the scenario of
        // the building from an empty inventory observes it. The gate stands behind the probe of
        // the environment, because the probe gives the player one block and removes it again
        // with the ordinary interaction: the preparation must not be blocked by its own item,
        // and the emptiness is observed after the probe has cleaned the inventory up.
        if (!initialInventoryObserved) {
            if (!inventoryEmpty(client)) {
                String diagnostics = inventoryDiagnostics(client);
                if (!gate("inventory", diagnostics)) {
                    throw gateFailure("inventory", diagnostics);
                }
                return false;
            }
            if (initialInventorySnapshot == null) {
                var playerId = client.player.getUuid();
                initialInventorySnapshot = CompletableFuture.supplyAsync(() -> {
                    var player = server.getPlayerManager().getPlayer(playerId);
                    if (player == null) throw new IllegalStateException("server player missing during empty inventory verification");
                    return inventoryCounts(player.getInventory().getMainStacks());
                }, server::execute);
            }
            if (!initialInventorySnapshot.isDone()) {
                if (!gate("inventory", "server empty inventory snapshot pending"))
                    throw gateFailure("inventory", "server empty inventory snapshot pending");
                return false;
            }
            var serverInventory = initialInventorySnapshot.join();
            initialInventorySnapshot = null;
            if (!serverInventory.isEmpty()) {
                if (!gate("inventory", "server inventory not empty: " + serverInventory))
                    throw gateFailure("inventory", "server inventory not empty: " + serverInventory);
                return false;
            }
            initialInventoryObserved = true;
            gate("inventory", "post-probe client and server inventories empty before repair materials");
        }
        if (safeRepair && !repairMaterials()) return false;
        return true;
    }

    /**
     * True when the inventory of the client is empty.
     *
     * <p>Packet arrival, rather than elapsed ticks, decides when the emptiness is observed: the
     * inventory of the client is empty only when the update of the server has reached it.
     *
     * @param client the client of the scenario
     * @return true when no slot of the client holds an item
     */
    private static boolean inventoryEmpty(MinecraftClient client) {
        if (client.player == null) {
            return false;
        }
        for (var stack : client.player.getInventory().getMainStacks()) {
            if (!stack.isEmpty()) {
                return false;
            }
        }
        return true;
    }

    /**
     * The facts of a non empty inventory at the moment of the gate.
     *
     * <p>The names distinguish the possible causes of a non empty inventory: a leftover of the
     * initialization of the dependency, a leftover of an earlier scenario in the game directory,
     * a difference between the client and the server, and the own item of the probe of the
     * environment. The ids of the items, the state of the probe, the content of the inventory of
     * the server and the identity of the world and of the game directory are named, so that the
     * run that stops in this gate can be read instead of guessed at.
     *
     * @param client the client of the scenario
     * @return the facts of the inventory as one line of text
     */
    private String inventoryDiagnostics(MinecraftClient client) {
        StringBuilder clientStacks = new StringBuilder();
        if (client.player != null) {
            var main = client.player.getInventory().getMainStacks();
            for (int slot = 0; slot < main.size(); slot++) {
                var stack = main.get(slot);
                if (stack.isEmpty()) {
                    continue;
                }
                if (clientStacks.length() > 0) {
                    clientStacks.append(", ");
                }
                clientStacks.append("slot=").append(slot)
                        .append(" item=").append(Registries.ITEM.getId(stack.getItem()))
                        .append(" count=").append(stack.getCount());
            }
        }
        String serverStacks = "unknown";
        var server = client.getServer();
        if (server != null && client.player != null) {
            var serverPlayer = server.getPlayerManager().getPlayer(client.player.getUuid());
            if (serverPlayer != null) {
                StringBuilder text = new StringBuilder();
                var main = serverPlayer.getInventory().getMainStacks();
                for (int slot = 0; slot < main.size(); slot++) {
                    var stack = main.get(slot);
                    if (stack.isEmpty()) {
                        continue;
                    }
                    if (text.length() > 0) {
                        text.append(", ");
                    }
                    text.append("slot=").append(slot)
                            .append(" item=").append(Registries.ITEM.getId(stack.getItem()))
                            .append(" count=").append(stack.getCount());
                }
                serverStacks = text.length() == 0 ? "empty" : text.toString();
            }
        }
        return "the inventory of the client is not empty yet: client=[" + clientStacks
                + "], server=[" + serverStacks
                + "], probe_state=" + probeState
                + ", world=" + (client.world == null ? "none" : client.world.getRegistryKey())
                + ", world_key=" + worldKey
                + ", game_dir=" + client.runDirectory;
    }

    /**
     * Puts the materials of the project into the inventory of the player.
     *
     * <p>The scenario of the repair observes the safe repair of a wrong cell and nothing else:
     * the delivery of the materials is not its subject, so the laboratory puts the materials of
     * the project into the inventory of the player. The probe of the environment empties the
     * inventory, therefore the materials come after the probe.
     *
     * @return true when the materials are in the inventory of the client
     */
    private boolean repairMaterials() {
        MinecraftClient client = MinecraftClient.getInstance();
        var server = client.getServer();
        if (server == null || client.player == null || client.world == null) {
            if (!gate("repair-materials", "the client, the server, the player or the world is missing")) {
                throw gateFailure("repair-materials", "the client, the server, the player or the world is missing");
            }
            return false;
        }
        var playerId = client.player.getUuid();
        var needs = HouseTemplate.forVariant(0).itemNeeds();
        if (repairMaterialsIssue == null) {
            repairMaterialsIssue = CompletableFuture.runAsync(() -> {
                var player = server.getPlayerManager().getPlayer(playerId);
                if (player == null) throw new IllegalStateException("server player missing during material issuance");
                int slot = 0;
                for (var need : needs.entrySet()) {
                    var item = Registries.ITEM.get(Identifier.of(need.getKey()));
                    int count = need.getValue();
                    while (count > 0) {
                        if (slot >= player.getInventory().getMainStacks().size()) {
                            throw new IllegalStateException("repair materials exceed inventory capacity");
                        }
                        int stack = Math.min(count, item.getMaxCount());
                        player.getInventory().setStack(slot++, new ItemStack(item, stack));
                        count -= stack;
                    }
                }
                player.getInventory().setSelectedSlot(0);
                player.currentScreenHandler.sendContentUpdates();
            }, server::execute);
        }
        if (!repairMaterialsIssue.isDone()) return waitForMaterials("server issuance pending");
        repairMaterialsIssue.join();
        if (repairInventorySnapshot == null) {
            repairInventorySnapshot = CompletableFuture.supplyAsync(() -> {
                var player = server.getPlayerManager().getPlayer(playerId);
                if (player == null) throw new IllegalStateException("server player missing during material verification");
                return inventoryCounts(player.getInventory().getMainStacks());
            }, server::execute);
        }
        if (!repairInventorySnapshot.isDone()) return waitForMaterials("server inventory snapshot pending");
        var serverCounts = repairInventorySnapshot.join();
        repairInventorySnapshot = null;
        var clientCounts = inventoryCounts(client.player.getInventory().getMainStacks());
        if (!hasMaterials(needs, serverCounts) || !hasMaterials(needs, clientCounts)) {
            return waitForMaterials("required=" + needs + " server=" + serverCounts + " client=" + clientCounts);
        }
        LOGGER.info("LAB_OBSERVE fixture_repair_materials: required={} server={} client={}", needs, serverCounts, clientCounts);
        return true;
    }

    private boolean waitForMaterials(String fact) {
        if (!gate("repair-materials", fact)) throw gateFailure("repair-materials", fact);
        return false;
    }

    private static java.util.Map<String, Integer> inventoryCounts(Iterable<ItemStack> stacks) {
        var counts = new java.util.HashMap<String, Integer>();
        for (var stack : stacks) {
            if (!stack.isEmpty()) counts.merge(Registries.ITEM.getId(stack.getItem()).toString(), stack.getCount(), Integer::sum);
        }
        return counts;
    }

    static boolean hasMaterials(java.util.Map<String, Integer> needs, java.util.Map<String, Integer> counts) {
        return needs.entrySet().stream().allMatch(e -> counts.getOrDefault(e.getKey(), 0) >= e.getValue());
    }

    /**
     * The facts of the preparation of the scenario of the repair.
     *
     * <p>The facts are read from the world of the server, not from the client: the preparation is
     * confirmed independently of the add-on before the product acts.
     *
     * @return the facts of the preparation as one line of text
     */
    String repairFact() {
        return repairFact;
    }

    /**
     * Builds the floor of the house of the scenario of the repair.
     *
     * <p>The scenario of the wrong block prepares three cells of the volume, and every one of
     * them is a separate observation:
     *
     * <ul>
     *   <li>the cell of the wrong block holds a material of the project in the wrong cell: such a
     *       block is removed by the safe repair and the cell is read again;</li>
     *   <li>the cell of the lone half holds the lower half of the door of the project without its
     *       upper half: the half is not overwritten by a new installation;</li>
     *   <li>the cells of the foreign blocks hold a tree and a crop: the add-on never breaks a
     *       block that the project does not place.</li>
     * </ul>
     *
     * <p>The scenario of the foreign block prepares only the foreign cells: its subject is the
     * block that the add-on does not touch.
     *
     * @param world   the world of the server
     * @param origin  the origin of the house of the scenario
     * @param foreign true when the scenario observes the foreign blocks
     */
    private static void seedRepairHouse(ServerWorld world, Vec3i origin, boolean foreign) {
        HouseTemplate template = HouseTemplate.forVariant(0);
        for (Cell cell : template.volumeCells()) {
            if (cell.y() != 0) {
                continue;
            }
            CellSpec spec = template.spec(cell);
            if (spec == null) {
                continue;
            }
            BlockState state = Registries.BLOCK.get(Identifier.of(spec.blockId())).getDefaultState();
            if (!foreign && cell.equals(REPAIR_CELL)) {
                // The wrong block of the scenario is a material of the project in the wrong cell:
                // the safe repair removes it (ТЗ.md section 4).
                state = Registries.BLOCK.get(Identifier.of(template.palette().wall())).getDefaultState();
            }
            if (foreign && cell.equals(FOREIGN_LOG_CELL)) {
                // A foreign block: a tree that stands in the volume of the house.
                state = Blocks.OAK_LOG.getDefaultState();
            }
            if (foreign && cell.equals(FOREIGN_CONTAINER_CELL)) {
                // A protected foreign block: a container whose content the client never reads.
                state = Blocks.BARREL.getDefaultState();
            }
            if (foreign && cell.equals(FOREIGN_EARTH_CELL)) {
                // A second foreign block: a block of earth that is not a material of the project.
                state = Blocks.COARSE_DIRT.getDefaultState();
            }
            world.setBlockState(pos(origin.add(cell.x(), cell.y(), cell.z())), state, 2);
        }
        if (!foreign) {
            // The lone half of the door of the project: the pair of the door is incomplete.
            world.setBlockState(pos(origin.add(LONE_HALF_CELL.x(), LONE_HALF_CELL.y(), LONE_HALF_CELL.z())),
                    Blocks.SPRUCE_DOOR.getDefaultState()
                            .with(DoorBlock.HALF, DoubleBlockHalf.LOWER)
                            .with(DoorBlock.FACING, Direction.SOUTH)
                            .with(DoorBlock.HINGE, DoorHinge.LEFT),
                    2);
        }
    }

    /** Reads the facts of the preparation of the scenario of the repair from the server world. */
    private static String inspectRepairHouse(ServerWorld world, Vec3i origin) {
        BlockState wrong = world.getBlockState(pos(origin.add(REPAIR_CELL.x(), REPAIR_CELL.y(), REPAIR_CELL.z())));
        BlockState neighbour = world.getBlockState(pos(origin.add(1, 0, 0)));
        BlockState support = world.getBlockState(pos(origin.add(0, -1, 0)));
        BlockState loneHalf = world.getBlockState(
                pos(origin.add(LONE_HALF_CELL.x(), LONE_HALF_CELL.y(), LONE_HALF_CELL.z())));
        BlockState foreignLog = world.getBlockState(
                pos(origin.add(FOREIGN_LOG_CELL.x(), FOREIGN_LOG_CELL.y(), FOREIGN_LOG_CELL.z())));
        BlockState foreignEarth = world.getBlockState(
                pos(origin.add(FOREIGN_EARTH_CELL.x(), FOREIGN_EARTH_CELL.y(), FOREIGN_EARTH_CELL.z())));
        BlockState foreignContainer = world.getBlockState(
                pos(origin.add(FOREIGN_CONTAINER_CELL.x(), FOREIGN_CONTAINER_CELL.y(),
                        FOREIGN_CONTAINER_CELL.z())));
        return "wrong_block=" + Registries.BLOCK.getId(wrong.getBlock()).toString()
                + " at " + origin.add(REPAIR_CELL.x(), REPAIR_CELL.y(), REPAIR_CELL.z())
                + ", floor_cell=" + Registries.BLOCK.getId(neighbour.getBlock()).toString()
                + " at " + origin.add(1, 0, 0)
                + ", support=" + Registries.BLOCK.getId(support.getBlock()).toString()
                + " at " + origin.add(0, -1, 0)
                + ", lone_half=" + Registries.BLOCK.getId(loneHalf.getBlock()).toString()
                + " at " + origin.add(LONE_HALF_CELL.x(), LONE_HALF_CELL.y(), LONE_HALF_CELL.z())
                + ", foreign_log=" + Registries.BLOCK.getId(foreignLog.getBlock()).toString()
                + " at " + origin.add(FOREIGN_LOG_CELL.x(), FOREIGN_LOG_CELL.y(), FOREIGN_LOG_CELL.z())
                + ", foreign_earth=" + Registries.BLOCK.getId(foreignEarth.getBlock()).toString()
                + " at " + origin.add(FOREIGN_EARTH_CELL.x(), FOREIGN_EARTH_CELL.y(), FOREIGN_EARTH_CELL.z())
                + ", foreign_container=" + Registries.BLOCK.getId(foreignContainer.getBlock()).toString()
                + " at " + origin.add(FOREIGN_CONTAINER_CELL.x(), FOREIGN_CONTAINER_CELL.y(),
                        FOREIGN_CONTAINER_CELL.z());
    }

    /** The wrong block of the scenario of the repair: a material of the project in the wrong cell. */
    static String repairWrongBlock() {
        return HouseTemplate.forVariant(0).palette().wall();
    }

    /** The foreign block of the scenario of the foreign block: a tree. */
    static String foreignLogBlock() {
        return Registries.BLOCK.getId(Blocks.OAK_LOG).toString();
    }

    /** The second foreign block of the scenario of the foreign block. */
    static String foreignEarthBlock() {
        return Registries.BLOCK.getId(Blocks.COARSE_DIRT).toString();
    }

    /** The protected foreign block of the scenario of the foreign block: a container. */
    static String foreignContainerBlock() {
        return Registries.BLOCK.getId(Blocks.BARREL).toString();
    }

    /**
     * Probes the ordinary placement of the environment of the stand.
     *
     * <p>The probe is the preparation of the stand and not a task of the add-on: it puts one
     * block into the hand of the player, places it with an ordinary interaction of the player on
     * the prepared platform next to the player, outside the footprint of the house, and it
     * removes the block and the item again. The fact of the probe tells whether the environment
     * accepts an ordinary placement at all; it says nothing about the add-on and it never builds
     * a part of the house.
     *
     * @param scenarioId the scenario that is prepared
     * @param water      true when the platform of the scenario is covered with water
     * @return true when the probe is done
     */
    private boolean probe(String scenarioId, boolean water) {
        if (water) {
            // The platform of this scenario is covered with water: an ordinary placement is not
            // possible there and the probe would say nothing about the environment.
            probeFact = "skipped: the platform of the scenario is covered with water";
            return true;
        }
        if (probeState == 4) {
            return true;
        }
        MinecraftClient client = MinecraftClient.getInstance();
        var server = client.getServer();
        if (server == null || client.player == null || client.world == null) return false;
        if (client.interactionManager == null) return false;
        var serverPlayer = server.getPlayerManager().getPlayer(client.player.getUuid());
        if (serverPlayer == null) return false;
        var world = server.getWorld(client.world.getRegistryKey());
        if (world == null) return false;
        switch (probeState) {
            case 0 -> {
                // The stand gives one block for the probe and puts it into the hotbar of the player.
                serverPlayer.getInventory().setStack(0, new ItemStack(Items.STONE_BRICKS));
                serverPlayer.getInventory().setSelectedSlot(0);
                serverPlayer.currentScreenHandler.sendContentUpdates();
                probeCell = client.player.getBlockPos().east();
                probeState = 1;
                return false;
            }
            case 1 -> {
                // The client waits for the item and turns the view to the platform below the cell
                // of the probe. The wait is bounded: a stand that does not get the item fails
                // with a named reason instead of waiting for ever.
                if (client.player.getInventory().getStack(0).isEmpty()) {
                    if (!gate("probe", "the item of the probe did not reach the client")) {
                        throw gateFailure("probe", "the item of the probe did not reach the client");
                    }
                    return false;
                }
                client.player.getInventory().setSelectedSlot(0);
                Vec3d eye = client.player.getEyePos();
                Vec3d hit = new Vec3d(probeCell.getX() + 0.5, probeCell.getY(), probeCell.getZ() + 0.5);
                double dx = hit.x - eye.x;
                double dy = hit.y - eye.y;
                double dz = hit.z - eye.z;
                client.player.setYaw((float) Math.toDegrees(Math.atan2(-dx, dz)));
                client.player.setPitch((float) -Math.toDegrees(Math.atan2(dy, Math.sqrt(dx * dx + dz * dz))));
                probeState = 2;
                return false;
            }
            case 2 -> {
                // The view needs a tick and the crosshair of the client has to name the platform.
                BlockHitResult hit = client.crosshairTarget instanceof BlockHitResult block ? block : null;
                if (hit == null || !hit.getBlockPos().equals(probeCell.down())) {
                    if (++probeTicks > 40) {
                        probeFact = "the crosshair of the client does not name the platform of the probe";
                        finishProbe(world, serverPlayer);
                        return true;
                    }
                    return false;
                }
                ActionResult result = client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hit);
                probeResult = result == null ? "none" : result.getClass().getSimpleName();
                probeAttempts++;
                probeTicks = 0;
                probeState = 3;
                return false;
            }
            default -> {
                // The world answers the probe: the server holds the block or it does not.
                String serverBlock = Registries.BLOCK.getId(world.getBlockState(probeCell).getBlock()).toString();
                String clientBlock = Registries.BLOCK.getId(
                        client.world.getBlockState(probeCell).getBlock()).toString();
                if (serverBlock.equals("minecraft:air") && ++probeTicks < 40) return false;
                if (serverBlock.equals("minecraft:air") && probeAttempts < 3) {
                    // The world did not answer the interaction of the probe: the interaction is
                    // sent once more, because a lost packet of the stand is not a refusal of the
                    // environment.
                    probeTicks = 0;
                    probeState = 2;
                    return false;
                }
                probeFact = "cell=" + probeCell.toShortString() + " action_result=" + probeResult
                        + " server=" + serverBlock + " client=" + clientBlock
                        + " player_client=" + playerImage(client.player)
                        + " player_server=" + playerImage(serverPlayer)
                        + " paused=" + client.isPaused() + " focused=" + client.isWindowFocused();
                finishProbe(world, serverPlayer);
                if (!serverBlock.equals(Registries.BLOCK.getId(Blocks.STONE_BRICKS).toString())) {
                    // The environment refused an ordinary placement. The behaviour of the add-on
                    // that depends on it is not verified, and it is never a failure of the add-on.
                    if (client.isPaused() || !client.isWindowFocused()) {
                        throw gateFailure("probe", "the window of the client is not focused, so the "
                                + "ordinary placement cannot be sent: " + probeFact);
                    }
                    throw gateFailure("probe", "the environment refused the ordinary placement: "
                            + probeFact);
                }
                return true;
            }
        }
    }

    /** Removes the block and the item of the probe of the environment. */
    private void finishProbe(ServerWorld world, ServerPlayerEntity serverPlayer) {
        world.setBlockState(probeCell, Blocks.AIR.getDefaultState(), 2);
        serverPlayer.getInventory().clear();
        serverPlayer.getInventory().setSelectedSlot(0);
        serverPlayer.currentScreenHandler.sendContentUpdates();
        probeState = 4;
    }

    /**
     * The place and the view of a player as one line of text.
     *
     * <p>The image of the client and the image of the server are compared in the fact of the
     * probe: when they differ, the movement packets of the player did not reach the server and
     * the failure of the environment belongs to the stand and not to the add-on.
     *
     * @param player the player whose place and view are named
     * @return the place and the view of the player as one line
     */
    private static String playerImage(net.minecraft.entity.player.PlayerEntity player) {
        return String.format(java.util.Locale.ROOT, "%.2f,%.2f,%.2f yaw=%.2f pitch=%.2f",
                player.getX(), player.getY(), player.getZ(), player.getYaw(), player.getPitch());
    }

    /**
     * The fact of the probe of the environment.
     *
     * @return the fact of the probe as one line of text
     */
    String probeFact() {
        return probeFact;
    }

    boolean seedPhase() {
        return "seed".equals(System.getProperty("lab.acceptance.fixture.phase"));
    }

    boolean hasPersistenceSeed() { return persistenceSeed != null; }

    private boolean inspectPersistence(MageHomeRuntime runtime) {
        try {
            if (persistenceSeed == null) {
                if (!Files.isRegularFile(seedReceipt(runtime))) throw new IllegalStateException("seed JVM receipt missing");
                HouseRegistry receipt = new RegistryFileStore(seedReceipt(runtime)).load();
                if (receipt.houses().size() != 1) throw new IllegalStateException("seed receipt must contain one house");
                persistenceSeed = receipt.houses().getFirst();
                if (persistenceSeed.state() != HouseState.BUILDING || persistenceSeed.confirmedCells() != 49) {
                    throw new IllegalStateException("seed JVM did not persist a partial BUILDING house");
                }
            }
            return inspectSeedWorld(runtime, persistenceSeed, true);
        } catch (Exception e) {
            throw new IllegalStateException("persistence reload fixture is invalid: " + e.getMessage(), e);
        }
    }

    private Path seedReceipt(MageHomeRuntime runtime) {
        return runtime.registryService().directory().resolve("lab-persistence-seed.json");
    }

    private boolean inspectSeedWorld(MageHomeRuntime runtime, HouseRecord record, boolean partial) {
        if (!record.worldKey().equals(runtime.context().worldKey())
                || !record.dimension().equals(runtime.context().dimension())
                || !record.playerUuid().equals(runtime.context().playerUuid())) {
            throw new IllegalStateException("seeded house has the wrong scope: expected="
                    + record.worldKey() + "/" + record.dimension() + "/" + record.playerUuid()
                    + "; actual=" + runtime.context().worldKey() + "/" + runtime.context().dimension()
                    + "/" + runtime.context().playerUuid());
        }
        HouseTemplate template = HouseTemplate.forVariant(record.variant());
        for (Cell cell : template.volumeCells()) {
            CellSpec spec = template.spec(cell);
            if (partial && cell.y() != 0) spec = null;
            var observed = runtime.context().world().read(record.origin().add(cell.x(), cell.y(), cell.z()));
            if (!observed.known()) return false;
            if (CellVerifier.verify(spec, observed)
                    != CellVerdict.MATCH) {
                throw new IllegalStateException("seeded world disagrees with receipt at " + cell
                        + "; expected=" + spec + "; observed=" + observed);
            }
        }
        return true;
    }

    private static void platform(ServerWorld world, Vec3i centre, int radius, boolean water) {
        for (int x = -radius; x <= radius; x++) {
            for (int z = -radius; z <= radius; z++) {
                world.setBlockState(pos(centre.add(x, -1, z)), Blocks.STONE.getDefaultState(), 2);
                for (int y = 0; y <= 6; y++) {
                    world.setBlockState(pos(centre.add(x, y, z)),
                            water && y == 0 ? Blocks.WATER.getDefaultState() : Blocks.AIR.getDefaultState(), 2);
                }
            }
        }
    }

    private static void seedHouse(ServerWorld world, Vec3i origin, boolean partial) {
        for (var entry : HouseTemplate.forVariant(0).cells().entrySet()) {
            Cell cell = entry.getKey();
            if (partial && cell.y() != 0) continue;
            CellSpec spec = entry.getValue();
            BlockState state = Registries.BLOCK.get(Identifier.of(spec.blockId())).getDefaultState();
            for (var property : spec.requiredProperties().entrySet()) {
                Property<?> found = state.getBlock().getStateManager().getProperty(property.getKey());
                if (found == null) throw new IllegalStateException("missing block property " + property);
                state = property(state, found, property.getValue());
            }
            world.setBlockState(pos(origin.add(cell.x(), cell.y(), cell.z())), state, 2);
        }
    }

    private static void inspectServerHouse(ServerWorld world, Vec3i origin, boolean partial) {
        HouseTemplate template = HouseTemplate.forVariant(0);
        for (Cell cell : template.volumeCells()) {
            CellSpec spec = template.spec(cell);
            if (partial && cell.y() != 0) spec = null;
            BlockState actual = world.getBlockState(pos(origin.add(cell.x(), cell.y(), cell.z())));
            java.util.Map<String, String> properties = new java.util.LinkedHashMap<>();
            actual.getProperties().forEach(property -> properties.put(property.getName(),
                    String.valueOf(actual.get(property))));
            var observed = dev.magehome.core.verification.ObservedBlock.of(
                    Registries.BLOCK.getId(actual.getBlock()).toString(), properties);
            if (CellVerifier.verify(spec, observed) != CellVerdict.MATCH) {
                throw new IllegalStateException("authoritative seeded house differs at " + cell);
            }
        }
    }

    private static <T extends Comparable<T>> BlockState property(BlockState state, Property<T> property, String value) {
        return state.with(property, property.parse(value).orElseThrow());
    }

    private static BlockPos pos(Vec3i value) { return new BlockPos(value.x(), value.y(), value.z()); }
}
