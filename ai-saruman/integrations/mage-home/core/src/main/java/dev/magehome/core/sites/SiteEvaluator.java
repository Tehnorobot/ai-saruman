package dev.magehome.core.sites;

import dev.magehome.core.model.Cell;
import dev.magehome.core.model.Vec3i;
import dev.magehome.core.verification.ObservedBlock;

import java.util.ArrayList;
import java.util.List;

/**
 * Checks whether a candidate origin is a legal building site (ТЗ.md section 3).
 *
 * <ul>
 *   <li>below every cell of the floor and below the front point there is a full solid support
 *       without liquid;</li>
 *   <li>the volume 7x7x5 is free; only short grass, tall grass, fern and large fern are allowed
 *       and have to be removed before the volume is checked again;</li>
 *   <li>every checked cell is loaded; unknown is never air and no chunk is loaded;</li>
 *   <li>the front point has dry access and free space for the feet and the head of the player.</li>
 * </ul>
 */
public final class SiteEvaluator {

    /** Why a candidate was rejected. */
    public enum Problem {
        NONE,
        CELL_NOT_LOADED,
        SUPPORT_MISSING,
        SUPPORT_LIQUID,
        VOLUME_BLOCKED,
        FRONT_BLOCKED
    }

    /** Result of one evaluation. */
    public record Result(boolean suitable, Problem problem, Vec3i cell, List<Cell> removablePlants) {
        public Result {
            removablePlants = List.copyOf(removablePlants);
        }

        public static Result ok(List<Cell> removablePlants) {
            return new Result(true, Problem.NONE, null, removablePlants);
        }

        public static Result rejected(Problem problem, Vec3i cell) {
            return new Result(false, problem, cell, List.of());
        }
    }

    /**
     * Checks a site that is already in use: the support under the floor and the front point have
     * to be intact and the front point has to stay free. The volume is not checked because it
     * already contains the blocks of the house.
     */
    public static Result checkExisting(Vec3i origin, WorldView view) {
        for (int x = 0; x < 7; x++) {
            for (int z = 0; z < 7; z++) {
                Vec3i support = origin.add(x, -1, z);
                ObservedBlock block = view.read(support);
                if (!block.known()) {
                    return Result.rejected(Problem.CELL_NOT_LOADED, support);
                }
                if (block.liquid()) {
                    return Result.rejected(Problem.SUPPORT_LIQUID, support);
                }
                if (!block.solidFull()) {
                    return Result.rejected(Problem.SUPPORT_MISSING, support);
                }
            }
        }
        Vec3i frontSupport = origin.add(3, -1, -1);
        ObservedBlock frontSupportBlock = view.read(frontSupport);
        if (!frontSupportBlock.known()) {
            return Result.rejected(Problem.CELL_NOT_LOADED, frontSupport);
        }
        if (frontSupportBlock.liquid() || !frontSupportBlock.solidFull()) {
            return Result.rejected(Problem.SUPPORT_MISSING, frontSupport);
        }
        Vec3i front = origin.add(3, 0, -1);
        for (int offset = 0; offset <= 1; offset++) {
            Vec3i position = front.add(0, offset, 0);
            ObservedBlock block = view.read(position);
            if (!block.known()) {
                return Result.rejected(Problem.CELL_NOT_LOADED, position);
            }
            if (block.liquid()) {
                return Result.rejected(Problem.FRONT_BLOCKED, position);
            }
            if (!block.isAir() && !block.removablePlant()) {
                return Result.rejected(Problem.FRONT_BLOCKED, position);
            }
        }
        return Result.ok(List.of());
    }

    private SiteEvaluator() {
    }

    public static Result evaluate(Vec3i origin, WorldView view) {
        List<Cell> plants = new ArrayList<>();

        // support below the floor cells and below the front point
        for (int x = 0; x < 7; x++) {
            for (int z = 0; z < 7; z++) {
                Vec3i support = origin.add(x, -1, z);
                ObservedBlock block = view.read(support);
                if (!block.known()) {
                    return Result.rejected(Problem.CELL_NOT_LOADED, support);
                }
                if (block.liquid()) {
                    return Result.rejected(Problem.SUPPORT_LIQUID, support);
                }
                if (!block.solidFull()) {
                    return Result.rejected(Problem.SUPPORT_MISSING, support);
                }
            }
        }
        Vec3i frontSupport = origin.add(3, -1, -1);
        ObservedBlock frontSupportBlock = view.read(frontSupport);
        if (!frontSupportBlock.known()) {
            return Result.rejected(Problem.CELL_NOT_LOADED, frontSupport);
        }
        if (frontSupportBlock.liquid()) {
            return Result.rejected(Problem.SUPPORT_LIQUID, frontSupport);
        }
        if (!frontSupportBlock.solidFull()) {
            return Result.rejected(Problem.SUPPORT_MISSING, frontSupport);
        }

        // the volume has to be free
        for (int x = 0; x < 7; x++) {
            for (int z = 0; z < 7; z++) {
                for (int y = 0; y < 5; y++) {
                    Vec3i position = origin.add(x, y, z);
                    ObservedBlock block = view.read(position);
                    if (!block.known()) {
                        return Result.rejected(Problem.CELL_NOT_LOADED, position);
                    }
                    if (block.isAir()) {
                        continue;
                    }
                    if (block.removablePlant()) {
                        plants.add(new Cell(x, y, z));
                        continue;
                    }
                    return Result.rejected(Problem.VOLUME_BLOCKED, position);
                }
            }
        }

        // free space for the feet and the head of the player at the front point
        Vec3i front = origin.add(3, 0, -1);
        for (int offset = 0; offset <= 1; offset++) {
            Vec3i position = front.add(0, offset, 0);
            ObservedBlock block = view.read(position);
            if (!block.known()) {
                return Result.rejected(Problem.CELL_NOT_LOADED, position);
            }
            if (block.liquid()) {
                return Result.rejected(Problem.FRONT_BLOCKED, position);
            }
            if (!block.isAir() && !block.removablePlant()) {
                return Result.rejected(Problem.FRONT_BLOCKED, position);
            }
        }
        ObservedBlock frontBlock = view.read(front);
        if (!frontBlock.known()) {
            return Result.rejected(Problem.CELL_NOT_LOADED, front);
        }
        if (frontBlock.liquid()) {
            return Result.rejected(Problem.FRONT_BLOCKED, front);
        }

        return Result.ok(plants);
    }
}
