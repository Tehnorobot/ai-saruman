package dev.magehome.client;

import dev.magehome.core.registry.HouseRegistry;
import dev.magehome.core.registry.MalformedRegistryException;
import dev.magehome.core.registry.RegistryFileStore;
import dev.magehome.core.registry.StorageException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.file.Path;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * The registry file of the mod.
 *
 * <p>The file is read once when the world is entered and written by a single background thread,
 * so the client tick is never blocked by the file system. The writes are atomic (a temporary
 * file plus a rename) and happen one after another.
 */
public final class RegistryService {

    private static final Logger LOGGER = LoggerFactory.getLogger("mage_home/registry");

    private final Path directory;
    private final RegistryFileStore store;
    private final ExecutorService writer = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "mage_home-registry-writer");
        thread.setDaemon(true);
        return thread;
    });

    private HouseRegistry registry = new HouseRegistry();
    private boolean broken;
    private final java.util.concurrent.atomic.AtomicInteger pendingWrites =
            new java.util.concurrent.atomic.AtomicInteger();
    private volatile boolean writeFailed;

    public RegistryService(Path gameDirectory) {
        this.directory = gameDirectory.resolve("config").resolve("mage_home");
        this.store = new RegistryFileStore(directory.resolve("houses.json"));
    }

    public Path directory() {
        return directory;
    }

    /** Reads the registry; a damaged file is reported and never replaced by an empty registry. */
    public void loadFromDisk() {
        try {
            registry = store.load();
            broken = false;
        } catch (MalformedRegistryException e) {
            broken = true;
            LOGGER.error("the registry file is damaged and will not be replaced: {}", e.getMessage());
        } catch (StorageException e) {
            broken = true;
            LOGGER.error("the registry file cannot be read: {}", e.getMessage());
        }
    }

    public boolean broken() {
        return broken;
    }

    public HouseRegistry registry() {
        return registry;
    }

    /** Writes the registry in the background; the client tick is not blocked. */
    public void save(HouseRegistry registry) {
        this.registry = registry;
        HouseRegistry snapshot = registry.copy();
        pendingWrites.incrementAndGet();
        try {
            writer.execute(() -> {
                try {
                    store.save(snapshot);
                    writeFailed = false;
                } catch (StorageException e) {
                    writeFailed = true;
                    LOGGER.error("the registry cannot be written: {}", e.getMessage());
                } finally {
                    pendingWrites.decrementAndGet();
                }
            });
        } catch (RejectedExecutionException e) {
            pendingWrites.decrementAndGet();
            writeFailed = true;
            LOGGER.error("the registry write was rejected", e);
        }
    }

    /** Flush preceding snapshots. This is not writer shutdown; the next world can write again. */
    public void awaitPendingWrites() {
        try {
            writer.submit(() -> { }).get(10, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (ExecutionException | TimeoutException | RejectedExecutionException e) {
            writeFailed = true;
            LOGGER.error("the registry writer did not flush", e);
        }
    }

    /** True when the last write of the registry failed. */
    public boolean writeFailed() {
        return writeFailed;
    }

    /** True when a write is still running; the record of the site has to be written completely. */
    public boolean writePending() {
        return pendingWrites.get() > 0;
    }
}
