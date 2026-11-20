package dev.magehome.client;

import net.minecraft.util.math.BlockPos;

/**
 * The navigation of the mod. The implementation asks UnionClef to walk to a position; UnionClef
 * stays the only owner of the movement of the player.
 */
public interface NavigationRequest {

    void requestNavigation(BlockPos target);

    void clearNavigation();

    /**
     * The add-on tells the dependency that the blocks of the house must not be broken.
     *
     * <p>The navigation of the dependency digs when it cannot find a way, and a dug block in the
     * volume of the house is a wrong block that the rule of the repair has to remove. The
     * dependency is told before the first operation of the house, and the rule stays until the
     * automation stops (ТЗ.md section 4, T-A12-03).
     *
     * @param origin the beginning of the volume of the house in the world
     * @param sizeX the length of the volume along x
     * @param sizeY the height of the volume
     * @param sizeZ the length of the volume along z
     */
    void avoidBreaking(dev.magehome.core.model.Vec3i origin, int sizeX, int sizeY, int sizeZ);

    /**
     * The add-on tells the dependency which blocks it removes itself.
     *
     * <p>The safe repair of a wrong cell and the removal of a temporary support of the add-on are
     * ordinary attacks of the player on blocks inside the volume of the house. The dependency must
     * not refuse them as a private area, otherwise the cell is never repaired
     * (ТЗ.md section 4).
     *
     * @param positions the positions that the add-on removes itself
     */
    void allowBreaking(java.util.Collection<BlockPos> positions);
}
