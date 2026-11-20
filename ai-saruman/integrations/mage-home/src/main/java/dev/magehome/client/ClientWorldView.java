package dev.magehome.client;

import dev.magehome.core.model.Vec3i;
import dev.magehome.core.verification.ObservedBlock;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.registry.Registries;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.chunk.ChunkStatus;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Reads the blocks of the client world for the domain core. Unknown cells (chunks that are not
 * loaded) are never reported as air and no chunk is ever loaded by this class.
 */
public final class ClientWorldView implements dev.magehome.core.sites.WorldView {

    private final MinecraftClient client;

    public ClientWorldView(MinecraftClient client) {
        this.client = client;
    }

    @Override
    public ObservedBlock read(Vec3i position) {
        ClientWorld world = client.world;
        if (world == null) {
            return ObservedBlock.unknown();
        }
        BlockPos pos = new BlockPos(position.x(), position.y(), position.z());
        // ClientWorld.isChunkLoaded(int, int) always returns true in 1.21.11.
        // The nullable FULL lookup observes received data without loading a chunk
        // or substituting the manager's empty-chunk placeholder for real air.
        if (world.getChunkManager().getChunk(pos.getX() >> 4, pos.getZ() >> 4,
                ChunkStatus.FULL, false) == null) {
            return ObservedBlock.unknown();
        }
        BlockState state = world.getBlockState(pos);
        String id = Registries.BLOCK.getId(state.getBlock()).toString();
        Map<String, String> properties = new LinkedHashMap<>();
        state.getProperties().forEach(property -> properties.put(property.getName(),
                String.valueOf(state.get(property))));
        boolean solid = state.isSolidBlock(world, pos);
        boolean liquid = !state.getFluidState().isEmpty();
        boolean plant = id.equals("minecraft:short_grass") || id.equals("minecraft:tall_grass")
                || id.equals("minecraft:fern") || id.equals("minecraft:large_fern");
        // A block with a block entity holds something (a chest, a barrel, a furnace). The client
        // does not read the content of a container, so every container is treated as protected
        // (ТЗ.md section 4).
        boolean container = state.hasBlockEntity();
        return ObservedBlock.of(id, properties, solid, liquid, plant, container);
    }
}
