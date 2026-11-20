package dev.magehome.core.acceptance;

import dev.magehome.core.model.Vec3i;
import dev.magehome.core.sites.SiteEvaluator;
import dev.magehome.core.sites.WorldView;
import dev.magehome.core.verification.ObservedBlock;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * The preparation of the world of the stand of a scenario.
 *
 * <p>The scenarios of the laboratory need a world that the laboratory prepared: a flat platform
 * with a full support that the add-on can build on, and for the scenario of the blocked site a
 * platform that is covered with water. The preparation of the world belongs to the laboratory
 * and not to the add-on: the add-on never changes the world of the stand.
 *
 * <p>This class only reads the world. It answers the question whether the preparation of the
 * laboratory is there, so that a scenario whose preparation is missing is reported as
 * <em>not verified</em> with a reason and never as a failure of the add-on. The rules of the
 * platform are the rules of the product itself (see {@link SiteEvaluator}), so that the fixture
 * and the product speak about the same place.
 */
public final class PlatformFixture {

    /** The horizontal distance around the player that is looked at. */
    public static final int RADIUS = 20;

    /** The distance between two cells of the coarse scan of the ground. */
    public static final int STEP = 4;

    /** The maximum number of the candidates that are looked at in one inspection. */
    public static final int MAX_CANDIDATES = 256;

    /** What the laboratory prepared around the player. */
    public enum Kind {
        /** No platform was found. */
        NONE,
        /** A platform that the add-on can build on was found. */
        BUILDABLE,
        /** A platform that is covered with water was found. */
        WATER_COVERED
    }

    /**
     * The result of the inspection of the world.
     *
     * @param kind       what was found
     * @param origin     the origin of the platform; null when nothing was found
     * @param candidates the number of the candidates that were looked at
     * @param detail     the description of the finding
     */
    public record Result(Kind kind, Vec3i origin, int candidates, String detail) {
    }

    private PlatformFixture() {
    }

    /**
     * Looks at the world around the player for the platform of the stand.
     *
     * <p>The scan looks at the level of the player and at the level below it. First a coarse
     * scan of the ground finds the cells that carry a support; every such cell belongs to the
     * floor of the candidates whose origin is around it, and those candidates are the ones the
     * product itself would try. The number of the candidates is bounded, so that the inspection
     * never blocks the client tick.
     *
     * @param view   the read only view of the world
     * @param player the place of the player
     */
    public static Result inspect(WorldView view, Vec3i player) {
        if (view == null || player == null) {
            return new Result(Kind.NONE, null, 0, "the world of the stand is not readable");
        }
        int candidates = 0;
        for (int level = 0; level >= -1; level--) {
            int floor = player.y() + level;
            int support = floor - 1;
            Set<Vec3i> origins = new LinkedHashSet<>();
            for (int dx = -RADIUS; dx <= RADIUS; dx += STEP) {
                for (int dz = -RADIUS; dz <= RADIUS; dz += STEP) {
                    Vec3i cell = new Vec3i(player.x() + dx, support, player.z() + dz);
                    if (!solidSupport(cell, view)) {
                        continue;
                    }
                    // Every origin whose floor holds this cell is a candidate of the product.
                    for (int ox = cell.x() - 6; ox <= cell.x(); ox++) {
                        for (int oz = cell.z() - 6; oz <= cell.z(); oz++) {
                            origins.add(new Vec3i(ox, floor, oz));
                        }
                    }
                }
            }
            // The candidates are looked at in the order in which the product looks for its
            // site: the distance of the centre of the candidate (the origin plus (3, 0, 3))
            // from the player, then the x and then the z. The order matters: the scan of the
            // ground alone starts at the far corner of the world, and a platform that the
            // product would use was then found only after hundreds of candidates, or not at
            // all when the budget of the scan was spent.
            List<Vec3i> ordered = new ArrayList<>(origins);
            ordered.sort(Comparator
                    .comparingDouble((Vec3i origin) -> {
                        double cx = origin.x() + 3.0 - player.x();
                        double cz = origin.z() + 3.0 - player.z();
                        return cx * cx + cz * cz;
                    })
                    .thenComparingInt(Vec3i::x)
                    .thenComparingInt(Vec3i::z));
            for (Vec3i origin : ordered) {
                candidates++;
                if (candidates > MAX_CANDIDATES) {
                    return new Result(Kind.NONE, null, candidates,
                            "the scan was limited to " + MAX_CANDIDATES + " candidates");
                }
                if (!supportHolds(origin, view)) {
                    continue;
                }
                if (coveredWithWater(origin, view)) {
                    return new Result(Kind.WATER_COVERED, origin, candidates,
                            "the platform at " + origin + " is covered with water");
                }
                SiteEvaluator.Result evaluation = SiteEvaluator.evaluate(origin, view);
                if (evaluation.suitable()) {
                    return new Result(Kind.BUILDABLE, origin, candidates,
                            "the platform at " + origin + " can be built on");
                }
            }
        }
        return new Result(Kind.NONE, null, candidates,
                "no platform with a full support was found in " + (2 * RADIUS + 1) + " blocks around the player "
                        + "at " + player + " (" + candidates + " candidates)");
    }

    /**
     * True when the floor of the platform has a full solid support.
     *
     * <p>The corners and the middle of the floor are looked at first, so that a world without a
     * platform is read with few blocks; the whole floor is read afterwards.
     */
    private static boolean supportHolds(Vec3i origin, WorldView view) {
        if (!solidSupport(origin.add(0, -1, 0), view)) {
            return false;
        }
        if (!solidSupport(origin.add(6, -1, 0), view)) {
            return false;
        }
        if (!solidSupport(origin.add(0, -1, 6), view)) {
            return false;
        }
        if (!solidSupport(origin.add(6, -1, 6), view)) {
            return false;
        }
        if (!solidSupport(origin.add(3, -1, 3), view)) {
            return false;
        }
        if (!solidSupport(origin.add(3, -1, -1), view)) {
            return false;
        }
        for (int x = 0; x < 7; x++) {
            for (int z = 0; z < 7; z++) {
                if (!solidSupport(origin.add(x, -1, z), view)) {
                    return false;
                }
            }
        }
        return true;
    }

    /** True when the block is a known solid block without liquid. */
    private static boolean solidSupport(Vec3i position, WorldView view) {
        ObservedBlock block = view.read(position);
        return block.known() && block.solidFull() && !block.liquid();
    }

    /**
     * True when the floor of the platform is covered with water.
     *
     * <p>Every cell of the floor has to hold a liquid; a platform that is only partly covered
     * is not the preparation of the scenario of the blocked site.
     */
    private static boolean coveredWithWater(Vec3i origin, WorldView view) {
        for (int x = 0; x < 7; x++) {
            for (int z = 0; z < 7; z++) {
                ObservedBlock block = view.read(origin.add(x, 0, z));
                if (!block.known() || !block.liquid()) {
                    return false;
                }
            }
        }
        return true;
    }
}
