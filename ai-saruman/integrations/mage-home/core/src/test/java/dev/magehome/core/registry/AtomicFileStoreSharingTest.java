package dev.magehome.core.registry;

import com.sun.nio.file.ExtendedOpenOption;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledOnOs;
import org.junit.jupiter.api.condition.OS;
import org.junit.jupiter.api.io.TempDir;

import java.nio.channels.FileChannel;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@EnabledOnOs(OS.WINDOWS)
class AtomicFileStoreSharingTest {
    @TempDir Path directory;

    @Test void backgroundWriteSurvivesReaderLock() throws Exception {
        var store = new AtomicFileStore(directory.resolve("journal.json"));
        store.write("original");
        try (var writer = Executors.newSingleThreadExecutor()) {
            Future<?> commit;
            try (var reader = FileChannel.open(store.file(), StandardOpenOption.READ, ExtendedOpenOption.NOSHARE_DELETE)) {
                assertThrows(StorageException.class, () -> store.write("replacement"));
                var started = new CountDownLatch(1);
                commit = writer.submit(() -> {
                    started.countDown();
                    store.writeWithSharingRetry("replacement");
                    return null;
                });
                assertTrue(started.await(5, TimeUnit.SECONDS));
                assertThrows(TimeoutException.class, () -> commit.get(300, TimeUnit.MILLISECONDS));
                assertEquals("original", store.read());
            }
            commit.get(5, TimeUnit.SECONDS);
        }
        assertEquals("replacement", store.read());
    }
}
