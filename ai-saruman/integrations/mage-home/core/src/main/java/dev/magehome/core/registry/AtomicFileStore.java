package dev.magehome.core.registry;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AccessDeniedException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

/**
 * Atomic file write: the content goes to a temporary file in the same directory and is moved over
 * the target with an atomic rename (ТЗ.md section 5).
 */
public final class AtomicFileStore {
    private static final long[] SHARING_RETRY_MS = {25, 50, 100, 200, 400, 800, 1600};

    private final Path file;

    public AtomicFileStore(Path file) {
        this.file = file;
    }

    public Path file() {
        return file;
    }

    public boolean exists() {
        return Files.exists(file);
    }

    public String read() throws StorageException {
        try {
            return Files.readString(file, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new StorageException("cannot read " + file + ": " + e.getMessage(), e);
        }
    }

    public void write(String content) throws StorageException {
        write(content, false);
    }

    /** For background writers that can wait for a Windows reader to release the published file. */
    public void writeWithSharingRetry(String content) throws StorageException {
        write(content, true);
    }

    private void write(String content, boolean retrySharing) throws StorageException {
        Path directory = file.getParent();
        Path temporary = null;
        try {
            if (directory != null) {
                Files.createDirectories(directory);
            }
            temporary = Files.createTempFile(directory, file.getFileName().toString(), ".tmp");
            Files.writeString(temporary, content, StandardCharsets.UTF_8);
            replace(temporary, retrySharing);
            temporary = null;
        } catch (IOException e) {
            throw new StorageException("cannot write " + file + ": " + e.getMessage(), e);
        } finally {
            if (temporary != null) {
                try {
                    Files.deleteIfExists(temporary);
                } catch (IOException ignored) {
                    // The temporary file stays behind only if it cannot be removed.
                }
            }
        }
    }

    private void replace(Path temporary, boolean retrySharing) throws IOException {
        for (int attempt = 0; ; attempt++) {
            try {
                try {
                    Files.move(temporary, file, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
                } catch (AtomicMoveNotSupportedException e) {
                    Files.move(temporary, file, StandardCopyOption.REPLACE_EXISTING);
                }
                return;
            } catch (AccessDeniedException denied) {
                if (!retrySharing || attempt == SHARING_RETRY_MS.length) throw denied;
                try {
                    Thread.sleep(SHARING_RETRY_MS[attempt]);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    denied.addSuppressed(interrupted);
                    throw denied;
                }
            }
        }
    }
}
