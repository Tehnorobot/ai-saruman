package dev.magehome.client;

import dev.magehome.v5.runtime.UnionClefFacts;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.TitleScreen;
import net.minecraft.client.gui.screen.multiplayer.ConnectScreen;
import net.minecraft.client.network.ServerAddress;
import net.minecraft.client.network.ServerInfo;

/** Private lab launch adapter. It does not submit builds, prepare blocks, or report a pass. */
final class SteveLabLauncher {
    private final String address;
    private boolean attempted;

    SteveLabLauncher(String address) {
        if (!address.matches("127\\.0\\.0\\.1:[0-9]{1,5}")) {
            throw new IllegalArgumentException("Steve lab server must be loopback");
        }
        this.address = address;
    }

    void tick(MinecraftClient client) {
        // Quick Play can bypass TitleScreen.init(), which the pinned UnionClef uses to initialize.
        if (attempted || !(client.currentScreen instanceof TitleScreen)) return;
        var facts = UnionClefFacts.read();
        if (!facts.taskChain() || !facts.slotHandler()) return;
        attempted = true;
        ConnectScreen.connect(client.currentScreen, client, ServerAddress.parse(address),
                new ServerInfo("Mage Home isolated lab", address, ServerInfo.ServerType.OTHER), false, null);
    }
}
