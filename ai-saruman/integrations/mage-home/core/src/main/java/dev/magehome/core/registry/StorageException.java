package dev.magehome.core.registry;

/** Raised when the registry cannot be written; building stops with the reason storage_error. */
public class StorageException extends Exception {

    public StorageException(String message) {
        super(message);
    }

    public StorageException(String message, Throwable cause) {
        super(message, cause);
    }
}
