package dev.codexlab.fixture;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.MappingResolver;
import net.fabricmc.loader.api.ModContainer;

import java.io.IOException;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Test-process preparation for dependencies whose normal initialization needs
 * the title screen. This helper never creates a product assertion or a PASS.
 * Minecraft itself shows its title screen, then opens the saved world through
 * its real integrated-server loader. No dependency event or private guard is
 * changed by this helper.
 */
public final class ClientDependencyBootstrap implements ClientModInitializer {
    private static final String ADAPTER = "altoclef-title-1.21.11-0.95.61";
    private static final String DEPENDENCY_VERSION = "1.21.11-0.95.61";
    private static final AtomicBoolean REGISTERED = new AtomicBoolean();

    private final Map<String, Object> facts = new LinkedHashMap<>();
    private String adapter;
    private String world;
    private Path output;
    private MappingResolver mappings;
    private Class<?> clientType;
    private Class<?> titleType;
    private Field screen;
    private Field clientWorld;
    private Field player;
    private Method modInstance;
    private Method taskChain;
    private Method slots;
    private Method serverLoader;
    private Method openWorld;
    private boolean requested;
    private boolean complete;
    private String previousStatus;

    @Override
    public void onInitializeClient() {
        adapter = System.getProperty("lab.client.setup.adapter", "");
        if (adapter.isEmpty() || !REGISTERED.compareAndSet(false, true)) return;
        try {
            world = System.getProperty("lab.client.setup.world", "");
            if (!world.matches("[a-z0-9][a-z0-9_-]{0,63}")) {
                throw new IllegalArgumentException("Unsafe prepared-world name");
            }
            Path game = FabricLoader.getInstance().getGameDir().toAbsolutePath().normalize();
            Path selectedOutput = Path.of(System.getProperty("lab.client.setup.output", ""))
                    .toAbsolutePath().normalize();
            if (!selectedOutput.getParent().equals(game)) {
                throw new IllegalArgumentException("Preparation receipt must stay in the private game directory");
            }
            output = selectedOutput;
            if (!ADAPTER.equals(adapter)) {
                throw new IllegalArgumentException("Unsupported dependency adapter: " + adapter);
            }
            Optional<ModContainer> installed = FabricLoader.getInstance().getModContainer("altoclef");
            if (installed.isEmpty() || !DEPENDENCY_VERSION.equals(
                    installed.get().getMetadata().getVersion().getFriendlyString())) {
                throw new IllegalStateException("The selected UnionClef version is not actually loaded");
            }
            Optional<ModContainer> minecraft = FabricLoader.getInstance().getModContainer("minecraft");
            if (minecraft.isEmpty() || !"1.21.11".equals(
                    minecraft.get().getMetadata().getVersion().getFriendlyString())) {
                throw new IllegalStateException("This dependency adapter requires Minecraft 1.21.11");
            }
            Path level = game.resolve("saves").resolve(world).resolve("level.dat");
            if (!Files.isRegularFile(level, LinkOption.NOFOLLOW_LINKS) || Files.size(level) == 0) {
                throw new IllegalStateException("The prepared saved world is missing");
            }
            mappings = FabricLoader.getInstance().getMappingResolver();
            clientType = mappedClass("net.minecraft.class_310");
            titleType = mappedClass("net.minecraft.class_442");
            screen = mappedField(clientType, "net.minecraft.class_310", "field_1755",
                    "Lnet/minecraft/class_437;");
            clientWorld = mappedField(clientType, "net.minecraft.class_310", "field_1687",
                    "Lnet/minecraft/class_638;");
            player = mappedField(clientType, "net.minecraft.class_310", "field_1724",
                    "Lnet/minecraft/class_746;");
            serverLoader = clientType.getMethod(mappings.mapMethodName("intermediary",
                    "net.minecraft.class_310", "method_41735", "()Lnet/minecraft/class_7196;"));
            Class<?> loader = mappedClass("net.minecraft.class_7196");
            openWorld = loader.getMethod(mappings.mapMethodName("intermediary",
                    "net.minecraft.class_7196", "method_57784", "(Ljava/lang/String;Ljava/lang/Runnable;)V"),
                    String.class, Runnable.class);
            Class<?> dependency = Class.forName("adris.altoclef.AltoClef");
            modInstance = dependency.getMethod("getInstance");
            taskChain = dependency.getMethod("getUserTaskChain");
            slots = dependency.getMethod("getSlotHandler");
            facts.put("dependency_id", "altoclef");
            facts.put("dependency_version", DEPENDENCY_VERSION);
            facts.put("minecraft_version", "1.21.11");
            facts.put("initialization", "Minecraft title-screen lifecycle");
            write("WAITING", "Waiting for Minecraft to initialize its normal title screen");
            Class<?> events = Class.forName("net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents");
            Class<?> callback = Class.forName(
                    "net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents$EndTick");
            Object event = events.getField("END_CLIENT_TICK").get(null);
            Object listener = Proxy.newProxyInstance(callback.getClassLoader(), new Class<?>[]{callback},
                    (proxy, method, args) -> {
                        if (method.getDeclaringClass() == Object.class) {
                            return switch (method.getName()) {
                                case "toString" -> "LabClientDependencyBootstrap";
                                case "hashCode" -> System.identityHashCode(proxy);
                                case "equals" -> proxy == args[0];
                                default -> null;
                            };
                        }
                        if (args != null && args.length == 1) tick(args[0]);
                        return null;
                    });
            Class.forName("net.fabricmc.fabric.api.event.Event").getMethod("register", Object.class)
                    .invoke(event, listener);
        } catch (Throwable failure) {
            fail(failure);
        }
    }

    private void tick(Object client) {
        if (complete) return;
        try {
            if (requested) {
                if (clientWorld.get(client) != null && player.get(client) != null) {
                    facts.put("world_loaded", true);
                    facts.put("player_present", true);
                    write("WORLD_LOADED", "Dependency initialized and player joined the prepared world; product behavior is unverified");
                    complete = true;
                }
                return;
            }
            if (!titleType.isInstance(screen.get(client))) return;
            facts.put("title_screen_observed", true);
            Object dependency = modInstance.invoke(null);
            boolean chainReady = dependency != null && taskChain.invoke(dependency) != null;
            boolean slotsReady = dependency != null && slots.invoke(dependency) != null;
            facts.put("task_chain_present", chainReady);
            facts.put("slot_handler_present", slotsReady);
            if (!chainReady || !slotsReady) {
                write("WAITING", "The title screen is present; waiting for the loaded dependency to finish initialization");
                return;
            }
            write("READY", "The loaded dependency completed its initialization before world opening");
            requested = true;
            write("WORLD_OPENING", "Opening the prepared world through the Minecraft integrated-server loader");
            Object loader = serverLoader.invoke(client);
            openWorld.invoke(loader, world, (Runnable) () -> {
                if (!complete) {
                    fail(new IllegalStateException("Minecraft returned from the prepared-world loader before player readiness"));
                }
            });
        } catch (Throwable failure) {
            fail(failure);
        }
    }

    private Class<?> mappedClass(String intermediary) throws ClassNotFoundException {
        return Class.forName(mappings.mapClassName("intermediary", intermediary));
    }

    private Field mappedField(Class<?> owner, String intermediary, String name, String descriptor)
            throws NoSuchFieldException {
        return owner.getField(mappings.mapFieldName("intermediary", intermediary, name, descriptor));
    }

    private void fail(Throwable failure) {
        complete = true;
        Throwable cause = failure instanceof InvocationTargetException && failure.getCause() != null
                ? failure.getCause() : failure;
        System.err.println("LAB_CLIENT_DEPENDENCY_ERROR: " + cause);
        cause.printStackTrace(System.err);
        facts.put("error_type", cause.getClass().getName());
        facts.put("error", String.valueOf(cause.getMessage()));
        try {
            write("UNAVAILABLE", "Dependency preparation failed: " + cause.getClass().getName() + ": "
                    + String.valueOf(cause.getMessage()) + "; no product assertion was executed by this helper");
        } catch (IOException receiptFailure) {
            System.err.println("LAB_CLIENT_DEPENDENCY_RECEIPT_ERROR: " + receiptFailure);
        }
    }

    private void write(String status, String detail) throws IOException {
        if ((status + "\n" + detail).equals(previousStatus)) return;
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("schema_version", 1);
        value.put("status", status);
        value.put("adapter", adapter);
        value.put("world", world);
        value.put("detail", detail);
        value.put("observed_at", Instant.now().toString());
        value.put("product_assertions", false);
        value.put("facts", facts);
        String json = json(value);
        System.out.println("LAB_CLIENT_DEPENDENCY_PREPARATION:" + json);
        if (output != null && output.getParent() != null) {
            Path temporary = output.resolveSibling(".lab-client-dependency-" + UUID.randomUUID() + ".tmp");
            try {
                Files.writeString(temporary, json + "\n", StandardCharsets.UTF_8,
                        java.nio.file.StandardOpenOption.CREATE_NEW,
                        java.nio.file.StandardOpenOption.WRITE);
                Files.move(temporary, output, StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING);
            } finally {
                Files.deleteIfExists(temporary);
            }
        }
        previousStatus = status + "\n" + detail;
    }

    private static String json(Object value) {
        if (value == null) return "null";
        if (value instanceof Boolean || value instanceof Number) return value.toString();
        if (value instanceof Map<?, ?> map) {
            StringBuilder result = new StringBuilder("{");
            for (Map.Entry<?, ?> item : map.entrySet()) {
                if (result.length() > 1) result.append(',');
                result.append(json(String.valueOf(item.getKey()))).append(':').append(json(item.getValue()));
            }
            return result.append('}').toString();
        }
        String text = value.toString();
        StringBuilder result = new StringBuilder("\"");
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
                case '\n' -> result.append("\\n");
                case '\r' -> result.append("\\r");
                case '\t' -> result.append("\\t");
                default -> {
                    if (c < 0x20) result.append(String.format("\\u%04x", (int) c));
                    else result.append(c);
                }
            }
        }
        return result.append('"').toString();
    }
}
