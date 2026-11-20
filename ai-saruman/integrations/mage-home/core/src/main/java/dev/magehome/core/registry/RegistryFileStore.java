package dev.magehome.core.registry;

import java.nio.file.Path;

/**
 * The registry file {@code .minecraft/config/mage_home/houses.json}.
 *
 * <p>A malformed file is reported and never replaced by an empty registry; the automation is
 * suspended until the file is restored (ТЗ.md section 5).
 */
public final class RegistryFileStore {

    private final AtomicFileStore store;

    public RegistryFileStore(Path file) {
        this.store = new AtomicFileStore(file);
    }

    public Path file() {
        return store.file();
    }

    public boolean exists() {
        return store.exists();
    }

    /** Reads the registry; a missing file is an empty registry, a broken file is an error. */
    public HouseRegistry load() throws StorageException, MalformedRegistryException {
        if (!store.exists()) {
            return new HouseRegistry();
        }
        String content = store.read();
        return HouseRegistry.fromJson(content);
    }

    public void save(HouseRegistry registry) throws StorageException {
        store.write(registry.toJson());
    }
}
