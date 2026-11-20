package dev.magehome.client;

import dev.magehome.v5.engine.ProductionBuildEngine;
import dev.magehome.v5.runtime.V5Runtime;
import dev.magehome.v5.transport.ControlServer;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.loader.api.FabricLoader;

import java.io.IOException;
import java.nio.file.Path;

/**
 * The client entry point of the addon. The mod works on the client only; on a server and for
 * other players it is not needed.
 *
 * <p>The public contract {@code mage_home.api/1} can expose a local control server
 * and publish its port in {@code <gameDir>/config/mage_home/control-port.json}.
 */
public final class MageHomeClient implements ClientModInitializer {

    /** The control server of the public contract; null when it is not running. */
    private static ControlServer controlServer;

    @Override
    public void onInitializeClient() {
        MageHomeRuntime runtime = MageHomeRuntime.create();
        MageHomeCommands.register(runtime);
        ClientTickEvents.START_CLIENT_TICK.register(client -> {
            var v5 = V5Runtime.get();
            if (v5.engine() == null) {
                v5.bind(new ProductionBuildEngine());
            }
            v5.onClientTick();
        });
        ClientTickEvents.END_CLIENT_TICK.register(client -> runtime.onClientTick());
        ClientPlayConnectionEvents.JOIN.register((handler, sender, client) -> runtime.onWorldJoined());
        ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> {
            V5Runtime.get().onDisconnect();
            runtime.onWorldLeft();
        });
        if (Boolean.getBoolean("mage_home.control.enable")) {
            startControlServer();
        }
        ClientLifecycleEvents.CLIENT_STOPPING.register(client -> {
            if (controlServer != null) controlServer.close();
        });
    }

    /**
     * Starts the control server of the public contract.
     *
     * <p>The server binds the loopback interface only.
     */
    private static void startControlServer() {
        try {
            Path gameDirectory = FabricLoader.getInstance().getGameDir();
            Path receipt = gameDirectory.resolve("config").resolve("mage_home")
                    .resolve(ControlServer.RECEIPT_NAME);
            controlServer = new ControlServer(receipt);
            System.out.println("MAGE_HOME_CONTROL_READY: " + controlServer.host() + ":"
                    + controlServer.port());
        } catch (IOException | RuntimeException e) {
            System.err.println("MAGE_HOME_CONTROL_ERROR: " + e);
        }
    }

    /** The control server of the public contract; null when it is not running. */
    public static ControlServer controlServer() {
        return controlServer;
    }
}
