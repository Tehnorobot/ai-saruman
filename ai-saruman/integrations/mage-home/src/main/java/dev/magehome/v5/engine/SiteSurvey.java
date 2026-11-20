package dev.magehome.v5.engine;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.block.BlockState;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.BlockPos;

import java.util.*;
import java.util.function.Predicate;

/** Observation-driven preparation. A missing chunk never enters the cut/fill plan as air. */
final class SiteSurvey {
    record Result(List<BuildPlan.Goal> work, Map<BlockPos, BlockState> sampled, BlockPos unknown,
                  String blocked, String revision) { }

    static Result inspect(BuildPlan plan, ServerActions world, Predicate<BlockPos> temporaryScaffold) {
        var observations = new TreeMap<BlockPos, BlockState>(BuildPlan.ORDER);
        var clearance = new TreeSet<BlockPos>(BuildPlan.ORDER.reversed());
        plan.air().forEach(g -> clearance.add(g.pos()));
        var supports = new TreeMap<BlockPos, String>(BuildPlan.ORDER);
        for (var g : plan.goals()) {
            clearance.add(g.pos());
            if (g.pos().getY() == plan.anchor().getY()) supports.put(g.pos().down(), "foundation");
        }
        for (var p : plan.approach()) {
            if (plan.occupied().containsKey(p) || plan.occupied().containsKey(p.up())) {
                return new Result(List.of(), Map.of(), null, "approach_conflicts_with_blueprint:" + BuildPlan.coords(p), "");
            }
            clearance.add(p);
            clearance.add(p.up());
            if (!plan.occupied().containsKey(p.down())) supports.put(p.down(), "approach_support");
        }
        var cuts = new TreeMap<BlockPos, BuildPlan.Goal>(BuildPlan.ORDER.reversed());
        var fill = new TreeMap<BlockPos, BuildPlan.Goal>(BuildPlan.ORDER);
        for (var p : clearance) {
            var observed = world.read(p);
            if (observed == null) return new Result(List.of(), Map.of(), p, null, "");
            observations.put(p, observed);
            var finalGoal = plan.occupied().get(p);
            if (!observed.isAir() && (finalGoal == null || !finalGoal.matches(observed)) && !temporaryScaffold.test(p)) {
                cuts.put(p, BuildPlan.simple(p, BuildPlan.AIR, "clearance"));
            }
        }
        for (var entry : supports.entrySet()) {
            var top = entry.getKey();
            var column = new ArrayList<BlockPos>();
            for (int d = 0; ; d++) {
                var p = top.down(d);
                if (!world.inWorld(p) || d > 8) {
                    return new Result(List.of(), Map.of(), null, "no_stable_foundation:" + BuildPlan.coords(top), "");
                }
                var observed = world.read(p);
                if (observed == null) return new Result(List.of(), Map.of(), p, null, "");
                observations.put(p, observed);
                if (!cuts.containsKey(p) && world.stable(p)) break;
                if (clearance.contains(p) || plan.occupied().containsKey(p)) {
                    return new Result(List.of(), Map.of(), null, "foundation_conflicts_with_clearance:" + BuildPlan.coords(p), "");
                }
                column.add(p);
            }
            for (var p : column) {
                if (!observations.get(p).isAir()) cuts.put(p, BuildPlan.simple(p, BuildPlan.AIR, "clearance"));
                fill.put(p, BuildPlan.simple(p, BuildPlan.FOUNDATION, entry.getValue()));
            }
        }
        var goals = new ArrayList<BuildPlan.Goal>();
        goals.addAll(cuts.values());
        goals.addAll(fill.values());
        plan.goals().stream().filter(BuildPlan.Goal::prep).filter(g -> !g.matches(observations.get(g.pos()))).forEach(goals::add);
        // The verification scan uses the same conditions, not the operation cursor or send count.
        return new Result(List.copyOf(goals), Collections.unmodifiableMap(observations), null, null,
                BuildJournal.digest(observations.entrySet().toString()));
    }
    static JsonArray snapshot(Map<BlockPos, BlockState> sampled) {
        var cells = new JsonArray();
        sampled.forEach((p, s) -> {
            JsonObject node = BuildPlan.point(p);
            node.addProperty("block", Registries.BLOCK.getId(s.getBlock()).toString());
            node.add("state", BuildPlan.propertyJson(s));
            cells.add(node);
        });
        return cells;
    }
}
