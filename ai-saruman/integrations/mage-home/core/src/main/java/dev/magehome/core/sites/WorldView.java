package dev.magehome.core.sites;

import dev.magehome.core.model.Vec3i;
import dev.magehome.core.verification.ObservedBlock;

/**
 * Read only view of the world. The implementation lives in the client module; the domain core
 * never touches a Minecraft type and never loads a chunk.
 */
public interface WorldView {

    /** The block at the position; {@link ObservedBlock#known()} is false when it is not loaded. */
    ObservedBlock read(Vec3i position);
}
