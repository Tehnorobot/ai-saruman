package dev.magehome.v5.engine;

import com.google.gson.*;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.registry.Registries;
import net.minecraft.state.property.Property;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;

import java.util.*;

/** Validates wire data once, then keeps an immutable absolute-coordinate work plan. */
record BuildPlan(BlockPos anchor, int revision, List<Goal> goals, Map<BlockPos, Goal> occupied,
                 List<Goal> air, List<Goal> equipment, List<Goal> restoration,
                 List<BlockPos> approach, BlockPos entrance, BlockPos outside, BlockPos exit,
                 BlockPos min, BlockPos max, Map<BlockPos, List<Goal>> pairByCell, List<Painting> paintings,
                 Set<BlockPos> scaffoldAccess, Set<BlockPos> circulationClearance) {
    static final String AIR = "minecraft:air";
    static final String FOUNDATION = "minecraft:stone_bricks";
    static final int MAX_CELLS = 8192;
    static final Set<String> DYNAMIC = Set.of("open", "powered", "occupied", "lit");
    static final Set<String> KINDS = Set.of("structure", "furniture", "decoration", "site_preparation", "access");
    static final Set<String> PAINTINGS = Set.of("minecraft:alban", "minecraft:aztec", "minecraft:aztec2", "minecraft:bomb",
            "minecraft:kebab", "minecraft:plant", "minecraft:wasteland");
    static final Comparator<BlockPos> ORDER = Comparator.comparingInt(BlockPos::getY)
            .thenComparingInt(BlockPos::getZ).thenComparingInt(BlockPos::getX);

    record Goal(String id, BlockPos pos, String block, Map<String, String> state, BlockState vanilla,
                String kind, List<String> dependencies) {
        String commandState() {
            return block + (state.isEmpty() ? "" : "[" + String.join(",", state.entrySet().stream()
                    .map(p -> p.getKey() + "=" + p.getValue()).toList()) + "]");
        }
        boolean isAir() { return vanilla.isAir(); }
        boolean matches(BlockState observed) {
            if (observed == null) return false;
            if (AIR.equals(block)) return observed.isAir();
            if (!observed.isOf(vanilla.getBlock())) return false;
            return state.entrySet().stream().allMatch(p -> DYNAMIC.contains(p.getKey())
                    || p.getValue().equals(properties(observed).get(p.getKey())));
        }
        boolean prep() { return "site_preparation".equals(kind); }
    }

    record Painting(BlockPos pos, Direction facing, String variant) { }

    static BuildPlan parse(JsonObject context, JsonObject blueprint, ClientWorld world) {
        if (!"mage_home.blueprint/1".equals(text(blueprint, "schema", null))) {
            throw new UnsupportedOperationException("Expected mage_home.blueprint/1");
        }
        int revision = integer(blueprint, "revision", 1);
        if (revision < 1) throw invalid("revision must be positive");
        var requirements = object(blueprint, "requirements");
        if (!array(requirements, "conflicts").isEmpty()) throw invalid("unresolved requirements conflict");
        BlockPos anchor = position(requiredObject(context, "anchor"));
        BlockPos origin = blueprint.has("origin") ? offset(anchor, position(requiredObject(blueprint, "origin"))) : anchor;
        BlockPos size = position(requiredObject(blueprint, "size"));
        if (size.getX() <= 0 || size.getY() <= 0 || size.getZ() <= 0
                || size.getX() > 96 || size.getY() > 96 || size.getZ() > 96
                || (long) size.getX() * size.getY() * size.getZ() > 65536) throw invalid("unsupported blueprint size");
        validPosition(world, origin);
        BlockPos end = offset(origin, size.add(-1, -1, -1));
        validPosition(world, end);
        var occupied = new LinkedHashMap<BlockPos, Goal>();
        var byId = new LinkedHashMap<String, Goal>();
        int autoId = 0;
        for (var entry : array(blueprint, "cells")) {
            var item = asObject(entry);
            var pos = offset(origin, position(item));
            validPosition(world, pos);
            String kind = text(item, "kind", "structure");
            if (!KINDS.contains(kind)) throw invalid("unsupported cell kind: " + kind);
            if (!inBox(pos, origin, end) && (!Set.of("site_preparation", "access").contains(kind)
                    || !inBox(pos, origin.add(-10, -10, -10), end.add(10, 10, 10)))) {
                throw invalid("cell outside declared project: " + pos);
            }
            var goal = readGoal(text(item, "id", "cell-" + autoId++), pos, item, kind,
                    strings(item, "dependsOn"));
            if (goal.isAir()) throw invalid("occupied cell names air: " + pos);
            if (byId.putIfAbsent(goal.id, goal) != null || occupied.putIfAbsent(pos, goal) != null) {
                throw invalid("duplicate cell id or position: " + goal.id);
            }
        }
        if (occupied.isEmpty() || occupied.size() > MAX_CELLS) throw invalid("unsupported target count");
        List<Goal> air = new ArrayList<>();
        Set<BlockPos> airPositions = new HashSet<>();
        for (var entry : array(blueprint, "requiredAir")) {
            var pos = offset(origin, position(asObject(entry)));
            validPosition(world, pos);
            if (!inBox(pos, origin.add(-10, -10, -10), end.add(10, 10, 10))
                    || occupied.containsKey(pos) || !airPositions.add(pos)) throw invalid("invalid or duplicate required air: " + pos);
            air.add(simple(pos, AIR, "required_air"));
        }
        if (air.size() + occupied.size() > MAX_CELLS) throw invalid("too many verification cells");
        air.sort(Comparator.comparing(Goal::pos, ORDER.reversed()));
        var reserved = new HashSet<BlockPos>();
        for (var entry : array(blueprint, "circulationClearance")) {
            var p = offset(origin, position(asObject(entry)));
            if (!airPositions.contains(p)) throw invalid("circulation clearance is not required air: " + coords(p));
            reserved.add(p);
        }
        var paintings = new ArrayList<Painting>();
        var picturePositions = new HashSet<BlockPos>();
        if (array(blueprint, "paintings").size() > 64) throw invalid("too many paintings");
        for (var entry : array(blueprint, "paintings")) {
            var picture = asObject(entry);
            var at = offset(origin, position(picture));
            var direction = switch (text(picture, "facing", "")) {
                case "north" -> Direction.NORTH;
                case "south" -> Direction.SOUTH;
                case "east" -> Direction.EAST;
                case "west" -> Direction.WEST;
                default -> null;
            };
            var variant = text(picture, "variant", "");
            if (!inBox(at, origin, end) || !airPositions.contains(at) || !picturePositions.add(at) || reserved.contains(at)
                    || direction == null || !PAINTINGS.contains(variant)) {
                throw invalid("invalid or duplicate painting at " + coords(at));
            }
            var back = at.offset(direction.getOpposite());
            var goal = occupied.get(back);
            if (goal == null || !goal.vanilla().isSideSolidFullSquare(world, back, direction)) {
                throw invalid("painting needs a solid wall at " + coords(back));
            }
            paintings.add(new Painting(at, direction, variant));
        }

        Map<BlockPos, List<Goal>> pairByCell = new HashMap<>();
        Map<String, Set<String>> extraDeps = new HashMap<>();
        for (var entry : array(blueprint, "multiBlockPairs")) {
            JsonObject pair = asObject(entry);
            List<Goal> group = new ArrayList<>();
            Set<BlockPos> seen = new HashSet<>();
            for (var cell : array(pair, "cells")) {
                var pos = offset(origin, position(asObject(cell)));
                var goal = occupied.get(pos);
                if (goal == null || !seen.add(pos) || pairByCell.containsKey(pos)) throw invalid("invalid multiblock coordinate " + pos);
                group.add(goal);
            }
            if (group.size() != 2 || !group.get(0).block.equals(group.get(1).block)
                    || manhattan(group.get(0).pos, group.get(1).pos) != 1) throw invalid("invalid multiblock pair");
            validatePair(group, text(pair, "kind", ""));
            if ("upper".equals(group.getFirst().state.get("half")) || "head".equals(group.getFirst().state.get("part"))) {
                Collections.reverse(group);
            }
            var pairGoals = List.copyOf(group);
            for (var g : pairGoals) pairByCell.put(g.pos, pairGoals);
            extraDeps.computeIfAbsent(group.getLast().id, ignored -> new LinkedHashSet<>()).add(group.getFirst().id);
        }
        for (Goal goal : occupied.values()) {
            if ((goal.block.endsWith("_bed") || goal.block.endsWith("_door")) && !pairByCell.containsKey(goal.pos)) {
                throw invalid("missing bed/door multiblock pair: " + goal.pos);
            }
        }
        List<Goal> ordered = order(byId, extraDeps);
        Map<BlockPos, Goal> resolved = new LinkedHashMap<>();
        ordered.forEach(goal -> resolved.put(goal.pos, goal));

        List<Goal> equipment = extras(blueprint, "cleanup", origin, end, world);
        List<Goal> restoration = extras(blueprint, "restoration", origin, end, world);
        Set<BlockPos> extraPositions = new HashSet<>();
        for (Goal g : equipment) {
            if (g.isAir() || occupied.containsKey(g.pos) || !extraPositions.add(g.pos)) {
                throw invalid("temporary equipment overlaps a target or lacks a block: " + g.pos);
            }
        }
        extraPositions.clear();
        for (Goal g : restoration) {
            var finalGoal = occupied.get(g.pos);
            if (!extraPositions.add(g.pos) || (finalGoal != null && !sameRequirements(finalGoal, g))
                    || (airPositions.contains(g.pos) && !g.isAir())) throw invalid("restoration contradicts final state: " + g.pos);
        }
        var entranceJson = requiredObject(blueprint, "entrance");
        var entrance = offset(origin, position(entranceJson));
        validPosition(world, entrance);
        var outside = offset(origin, position(requiredObject(entranceJson, "outsidePoint")));
        var exit = blueprint.has("exitPoint") ? offset(origin, position(requiredObject(blueprint, "exitPoint"))) : outside;
        List<BlockPos> approach = new ArrayList<>();
        approach.add(outside);
        if (!outside.equals(exit)) approach.add(exit);
        for (var entry : array(entranceJson, "approachCells")) {
            var p = offset(origin, position(asObject(entry)));
            if (!approach.contains(p)) approach.add(p);
        }
        if (approach.size() > 128) throw invalid("too many approach cells");
        for (var p : approach) {
            validPosition(world, p.down());
            validPosition(world, p.up());
            if (!inBox(p, origin.add(-10, -10, -10), end.add(10, 10, 10))) throw invalid("approach too far: " + p);
        }
        var reservedColumns = new HashSet<BlockPos>();
        for (var p : occupied.keySet()) reservedColumns.add(p.withY(origin.getY()));
        for (var p : airPositions) reservedColumns.add(p.withY(origin.getY()));
        for (var p : approach) reservedColumns.add(p.withY(origin.getY()));
        reservedColumns.add(entrance.withY(origin.getY()));
        var scaffoldAccess = new HashSet<BlockPos>();
        var hints = array(blueprint, "scaffoldAccess");
        if (hints.size() > 8) throw invalid("too many scaffold access hints");
        for (var entry : hints) {
            var p = offset(origin, position(asObject(entry)));
            validPosition(world, p);
            if (p.getY() != origin.getY() || !inBox(p, origin.add(-4, 0, -4), end.add(4, 0, 4))
                    || reservedColumns.contains(p) || !scaffoldAccess.add(p)) {
                throw invalid("invalid or occupied scaffold access column: " + coords(p));
            }
        }
        return new BuildPlan(origin, revision, List.copyOf(ordered), Map.copyOf(resolved), List.copyOf(air),
                List.copyOf(equipment), List.copyOf(restoration), List.copyOf(approach), entrance, outside, exit,
                origin, end, Map.copyOf(pairByCell), List.copyOf(paintings), Set.copyOf(scaffoldAccess), Set.copyOf(reserved));
    }

    private static List<Goal> extras(JsonObject blueprint, String key, BlockPos origin, BlockPos end, ClientWorld world) {
        List<Goal> result = new ArrayList<>();
        for (var entry : array(blueprint, key)) {
            var item = asObject(entry);
            var pos = offset(origin, position(item));
            validPosition(world, pos);
            if (!inBox(pos, origin.add(-10, -10, -10), end.add(10, 10, 10))) throw invalid(key + " outside allowed work envelope");
            result.add(readGoal(key + "@" + coords(pos), pos, item, key, List.of()));
        }
        if (result.size() > 256) throw invalid("too many " + key + " cells");
        return result;
    }

    private static void validatePair(List<Goal> group, String kind) {
        Goal a = group.get(0), b = group.get(1);
        if ("door".equals(kind) || a.block.endsWith("_door")) {
            Goal lower = "lower".equals(a.state.get("half")) ? a : b;
            Goal upper = lower == a ? b : a;
            if (!"lower".equals(lower.state.get("half")) || !"upper".equals(upper.state.get("half"))
                    || !lower.pos.up().equals(upper.pos) || !sameWhenPresent(a, b, "facing")
                    || !sameWhenPresent(a, b, "hinge")) throw invalid("invalid door pair");
        } else if ("bed".equals(kind) || a.block.endsWith("_bed")) {
            Goal foot = "foot".equals(a.state.get("part")) ? a : b;
            Goal head = foot == a ? b : a;
            if (!"foot".equals(foot.state.get("part")) || !"head".equals(head.state.get("part"))
                    || !sameWhenPresent(a, b, "facing")) throw invalid("invalid bed pair");
            var facing = Direction.Type.HORIZONTAL.stream().filter(d -> d.asString().equals(foot.state.getOrDefault("facing", "north")))
                    .findFirst().orElse(null);
            if (facing == null || !foot.pos.offset(facing).equals(head.pos)) throw invalid("invalid bed facing");
        }
    }

    private static boolean sameWhenPresent(Goal a, Goal b, String key) {
        return !a.state.containsKey(key) || !b.state.containsKey(key) || a.state.get(key).equals(b.state.get(key));
    }

    private static List<Goal> order(Map<String, Goal> byId, Map<String, Set<String>> extra) {
        Map<String, Integer> incoming = new HashMap<>();
        Map<String, List<String>> successors = new HashMap<>();
        Map<String, Goal> resolved = new HashMap<>();
        var ready = new PriorityQueue<Goal>(Comparator.comparing((Goal g) -> !g.prep())
                // Install doors once both jambs are in place and rooms have been furnished.
                .thenComparing(g -> g.block.endsWith("_door"))
                .thenComparingInt(g -> g.pos.getY())
                .thenComparing(g -> Set.of("furniture", "decoration").contains(g.kind))
                // Neighbouring walls can hide the top face needed for a vertical post.
                .thenComparingInt(g -> "y".equals(g.state.get("axis")) ? 0 : 1)
                .thenComparing(Goal::pos, ORDER).thenComparing(Goal::id));
        for (var g : byId.values()) {
            var dependencies = new LinkedHashSet<>(g.dependencies);
            dependencies.addAll(extra.getOrDefault(g.id, Set.of()));
            for (var id : dependencies) {
                var support = byId.get(id);
                if (support == null || (g.prep() && !support.prep())) throw invalid("invalid dependency " + id + " for " + g.id);
                successors.computeIfAbsent(id, ignored -> new ArrayList<>()).add(g.id);
            }
            var goal = new Goal(g.id, g.pos, g.block, g.state, g.vanilla, g.kind, List.copyOf(dependencies));
            resolved.put(g.id, goal);
            incoming.put(g.id, dependencies.size());
            if (dependencies.isEmpty()) ready.add(goal);
        }
        var result = new ArrayList<Goal>();
        while (!ready.isEmpty()) {
            var g = ready.remove();
            result.add(g);
            for (var next : successors.getOrDefault(g.id, List.of())) {
                if (incoming.merge(next, -1, Integer::sum) == 0) ready.add(resolved.get(next));
            }
        }
        if (result.size() != byId.size()) throw invalid("cyclic blueprint dependencies");
        return result;
    }

    static Goal simple(BlockPos pos, String block, String kind) {
        JsonObject value = new JsonObject();
        value.addProperty("block", block);
        return readGoal(kind + "@" + coords(pos), pos, value, kind, List.of());
    }

    static Goal fromState(BlockPos pos, BlockState state, String kind) {
        var value = new JsonObject();
        value.addProperty("block", Registries.BLOCK.getId(state.getBlock()).toString());
        value.add("state", propertyJson(state));
        return readGoal(kind + "@" + coords(pos), pos, value, kind, List.of());
    }

    static Goal readGoal(String id, BlockPos pos, JsonObject value, String kind, List<String> dependencies) {
        if (id.isBlank() || id.length() > 200) throw invalid("invalid goal id");
        String raw = text(value, "block", null);
        Identifier identifier = raw == null ? null : Identifier.tryParse(raw);
        if (identifier == null || !"minecraft".equals(identifier.getNamespace()) || !identifier.toString().equals(raw)
                || !Registries.BLOCK.containsId(identifier)) throw invalid("unknown or non-vanilla block: " + raw);
        BlockState state = Registries.BLOCK.get(identifier).getDefaultState();
        Map<String, String> properties = new TreeMap<>();
        for (var entry : object(value, "state").entrySet()) {
            var property = state.getBlock().getStateManager().getProperty(entry.getKey());
            if (property == null || !entry.getValue().isJsonPrimitive()) throw invalid("unknown property " + entry.getKey() + " of " + raw);
            String chosen = entry.getValue().getAsString();
            state = setProperty(state, property, chosen);
            properties.put(property.getName(), chosen);
        }
        return new Goal(id, pos, raw, Collections.unmodifiableMap(properties), state, kind, List.copyOf(dependencies));
    }

    private static <T extends Comparable<T>> BlockState setProperty(BlockState state, Property<T> p, String chosen) {
        return state.with(p, p.parse(chosen).orElseThrow(() -> invalid("invalid state " + p.getName() + "=" + chosen)));
    }

    static Map<String, String> properties(BlockState state) {
        var result = new TreeMap<String, String>();
        if (state != null) for (var property : state.getProperties()) result.put(property.getName(), nameOf(state, property));
        return result;
    }

    private static <T extends Comparable<T>> String nameOf(BlockState state, Property<T> property) {
        return property.name(state.get(property));
    }

    static JsonObject propertyJson(BlockState state) {
        var result = new JsonObject();
        properties(state).forEach(result::addProperty);
        return result;
    }

    static boolean sameRequirements(Goal a, Goal b) {
        return a.vanilla.isOf(b.vanilla.getBlock()) && a.state.equals(b.state);
    }

    static void validPosition(ClientWorld world, BlockPos p) {
        if (world == null || world.isOutOfHeightLimit(p) || !world.getWorldBorder().contains(p)
                || p.getX() < -29999983 || p.getX() > 29999983 || p.getZ() < -29999983 || p.getZ() > 29999983) {
            throw invalid("out of world/border: " + p);
        }
    }

    static boolean inBox(BlockPos pos, BlockPos min, BlockPos max) {
        return pos.getX() >= min.getX() && pos.getX() <= max.getX() && pos.getY() >= min.getY()
                && pos.getY() <= max.getY() && pos.getZ() >= min.getZ() && pos.getZ() <= max.getZ();
    }

    static BlockPos offset(BlockPos a, BlockPos b) {
        try {
            return new BlockPos(Math.addExact(a.getX(), b.getX()), Math.addExact(a.getY(), b.getY()), Math.addExact(a.getZ(), b.getZ()));
        } catch (ArithmeticException e) { throw invalid("coordinate overflow"); }
    }

    static BlockPos position(JsonObject o) {
        return new BlockPos(integer(o, "x", null), integer(o, "y", null), integer(o, "z", null));
    }

    static JsonObject point(BlockPos p) {
        var value = new JsonObject();
        value.addProperty("x", p.getX()); value.addProperty("y", p.getY()); value.addProperty("z", p.getZ());
        return value;
    }

    static String coords(BlockPos p) { return p.getX() + " " + p.getY() + " " + p.getZ(); }
    static int manhattan(BlockPos a, BlockPos b) { return Math.abs(a.getX() - b.getX()) + Math.abs(a.getY() - b.getY()) + Math.abs(a.getZ() - b.getZ()); }
    static IllegalArgumentException invalid(String message) { return new IllegalArgumentException(message); }
    static JsonObject requiredObject(JsonObject o, String key) { return asObject(o == null ? null : o.get(key)); }
    static JsonObject object(JsonObject o, String key) { return o == null || !o.has(key) || o.get(key).isJsonNull() ? new JsonObject() : requiredObject(o, key); }
    static JsonObject asObject(JsonElement element) {
        if (element == null || !element.isJsonObject()) throw invalid("expected object");
        return element.getAsJsonObject();
    }
    static JsonArray array(JsonObject o, String key) {
        if (o == null || !o.has(key)) return new JsonArray();
        if (!o.get(key).isJsonArray()) throw invalid("expected array " + key);
        return o.getAsJsonArray(key);
    }
    static String text(JsonObject o, String key, String fallback) {
        var x = o == null ? null : o.get(key);
        if (x == null || x.isJsonNull()) return fallback;
        if (!x.isJsonPrimitive() || !x.getAsJsonPrimitive().isString()) throw invalid("expected string " + key);
        return x.getAsString();
    }
    static List<String> strings(JsonObject o, String key) {
        var list = new ArrayList<String>();
        for (var x : array(o, key)) {
            if (!x.isJsonPrimitive() || !x.getAsJsonPrimitive().isString()) throw invalid("expected string in " + key);
            list.add(x.getAsString());
        }
        return list;
    }
    static int integer(JsonObject o, String key, Integer fallback) {
        var x = o == null ? null : o.get(key);
        if (x == null && fallback != null) return fallback;
        if (x == null || !x.isJsonPrimitive() || !x.getAsJsonPrimitive().isNumber() || !x.getAsString().matches("-?[0-9]+")) {
            throw invalid("expected integer " + key);
        }
        return Integer.parseInt(x.getAsString());
    }
    static boolean bool(JsonObject o, String key, boolean fallback) {
        if (o == null || !o.has(key)) return fallback;
        var x = o.get(key);
        if (!x.isJsonPrimitive() || !x.getAsJsonPrimitive().isBoolean()) throw invalid("expected boolean " + key);
        return x.getAsBoolean();
    }
}
