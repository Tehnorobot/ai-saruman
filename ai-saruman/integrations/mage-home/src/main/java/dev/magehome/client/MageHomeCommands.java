package dev.magehome.client;

import dev.magehome.core.automation.ApiResponses;
import com.mojang.brigadier.arguments.StringArgumentType;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandManager;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.command.v2.FabricClientCommandSource;
import net.minecraft.text.Text;

/**
 * The client commands of the mod: {@code /magehome enable|disable|status|list|retry|resume <id>}.
 */
public final class MageHomeCommands {

    private MageHomeCommands() {
    }

    public static void register(MageHomeRuntime runtime) {
        ClientCommandRegistrationCallback.EVENT.register((dispatcher, registryAccess) ->
                dispatcher.register(build(runtime)));
    }

    private static com.mojang.brigadier.builder.LiteralArgumentBuilder<FabricClientCommandSource> build(
            MageHomeRuntime runtime) {
        return ClientCommandManager.literal("magehome")
                .then(ClientCommandManager.literal("enable").executes(context -> {
                    runtime.onClientThread(() -> runtime.controller().enable());
                    context.getSource().sendFeedback(Text.literal("mage_home: the automation is enabled"));
                    return 1;
                }))
                .then(ClientCommandManager.literal("disable").executes(context -> {
                    runtime.onClientThread(() -> runtime.controller().disable());
                    context.getSource().sendFeedback(Text.literal("mage_home: the automation is disabled"));
                    return 1;
                }))
                .then(ClientCommandManager.literal("status").executes(context -> {
                    // The command status verifies the loaded houses in batches (ТЗ.md section 5).
                    runtime.verifyHomes();
                    context.getSource().sendFeedback(Text.literal(runtime.statusJson()));
                    return 1;
                }))
                .then(ClientCommandManager.literal("list").executes(context -> {
                    context.getSource().sendFeedback(Text.literal(runtime.homesJson()));
                    return 1;
                }))
                .then(ClientCommandManager.literal("retry").executes(context -> {
                    runtime.onClientThread(() -> runtime.controller().retrySearch());
                    context.getSource().sendFeedback(Text.literal("mage_home: the search is repeated"));
                    return 1;
                }))
                .then(ClientCommandManager.literal("resume")
                        .then(ClientCommandManager.argument("house_id", StringArgumentType.string())
                                .executes(context -> {
                                    String houseId = StringArgumentType.getString(context, "house_id");
                                    String answer = runtime.resume(houseId);
                                    context.getSource().sendFeedback(Text.literal(answer));
                                    return 1;
                                })))
                .executes(context -> {
                    context.getSource().sendFeedback(Text.literal(
                            "mage_home: enable | disable | status | list | retry | resume <house_id>"));
                    return 1;
                });
    }
}