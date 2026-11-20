package dev.magehome.v5.transport;

import com.google.gson.JsonObject;
import dev.magehome.v5.runtime.V5Runtime;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;

/** Opt-in local JSON-lines transport. Socket I/O never runs on the game thread. */
public final class ControlServer implements AutoCloseable {
    public static final String RECEIPT_NAME = "control-port.json";
    private static final int MAX_CHARS = 524_288;
    private final ServerSocket listener = new ServerSocket();
    private final Semaphore connections = new Semaphore(8);
    private final String token = UUID.randomUUID().toString();
    private final Path receipt;

    public ControlServer(Path receipt) throws IOException {
        this.receipt = receipt;
        try {
            listener.bind(new InetSocketAddress("127.0.0.1", 0), 16);
            publish();
            Thread.ofVirtual().name("mage-home-control-accept").start(this::acceptLoop);
        } catch (IOException | RuntimeException e) {
            listener.close();
            throw e;
        }
    }

    public int port() { return listener.getLocalPort(); }
    public String host() { return listener.getInetAddress().getHostAddress(); }

    private void publish() throws IOException {
        Path directory = receipt.getParent();
        Files.createDirectories(directory);
        Path temporary = Files.getFileStore(directory).supportsFileAttributeView("posix")
                ? Files.createTempFile(directory, ".control-", ".tmp",
                    PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))
                : Files.createTempFile(directory, ".control-", ".tmp");
        try {
            JsonObject value = new JsonObject();
            value.addProperty("api", V5Runtime.API);
            value.addProperty("host", host());
            value.addProperty("port", port());
            value.addProperty("token", token);
            value.addProperty("transport", "tcp-json-lines");
            Files.writeString(temporary, value + "\n", StandardCharsets.UTF_8);
            Files.move(temporary, receipt, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } finally {
            Files.deleteIfExists(temporary);
        }
    }

    private void acceptLoop() {
        while (!listener.isClosed()) {
            try {
                Socket socket = listener.accept();
                if (!connections.tryAcquire()) {
                    socket.close();
                    continue;
                }
                Thread.ofVirtual().name("mage-home-control-request").start(() -> serve(socket));
            } catch (IOException e) {
                if (!listener.isClosed()) System.err.println("MAGE_HOME_CONTROL_ACCEPT_ERROR: " + e);
                return;
            }
        }
    }

    private void serve(Socket socket) {
        try (socket) {
            socket.setSoTimeout(5000);
            var reader = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
            var line = new StringBuilder();
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
            for (int c; (c = reader.read()) != '\n'; ) {
                if (c == -1 || line.length() >= MAX_CHARS || System.nanoTime() > deadline) return;
                line.append((char) c);
            }
            var request = V5Runtime.parse(line.toString());
            if (!MessageDigest.isEqual(token.getBytes(StandardCharsets.UTF_8),
                    text(request, "token").getBytes(StandardCharsets.UTF_8))) return;
            String op = text(request, "op");
            String id = text(request, "requestId");
            if (op.isBlank() || id.isBlank() || !V5Runtime.API.equals(text(request, "api"))
                    || !request.has("payload") || !request.get("payload").isJsonObject()) return;
            var answer = new CompletableFuture<String>();
            V5Runtime.get().submit(op, id, request.getAsJsonObject("payload"), answer::complete);
            var response = V5Runtime.parse(answer.get(5, TimeUnit.SECONDS));
            response.addProperty("requestId", id);
            socket.getOutputStream().write((response + "\n").getBytes(StandardCharsets.UTF_8));
        } catch (Exception ignored) {
            // Caller may disconnect; it can query the same logical requestId again.
        } finally {
            connections.release();
        }
    }

    private static String text(JsonObject obj, String field) {
        var value = obj == null ? null : obj.get(field);
        return value != null && value.isJsonPrimitive() && value.getAsJsonPrimitive().isString()
                ? value.getAsString() : "";
    }

    @Override
    public void close() {
        try {
            listener.close();
            Files.deleteIfExists(receipt);
        } catch (IOException ignored) {
            // A receipt from an exited client is not a live endpoint.
        }
    }
}
