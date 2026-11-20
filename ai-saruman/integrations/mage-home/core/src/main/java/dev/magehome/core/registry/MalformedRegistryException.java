package dev.magehome.core.registry;

/** Raised when the registry file cannot be parsed; the file must not be replaced by an empty one. */
public class MalformedRegistryException extends Exception {

    public MalformedRegistryException(String message) {
        super(message);
    }

    public MalformedRegistryException(String message, Throwable cause) {
        super(message, cause);
    }
}
