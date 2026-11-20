package dev.magehome.client.mixin;

import dev.magehome.client.CompletionPhoto;
import net.minecraft.client.render.GameRenderer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(GameRenderer.class)
abstract class CompletionPhotoMixin {
    @Inject(method = "render", at = @At("HEAD"))
    private void mageHomePreparePhoto(CallbackInfo callback) {
        CompletionPhoto.beforeRender();
    }

    @Inject(method = "render", at = @At("RETURN"))
    private void mageHomeCapturePhoto(CallbackInfo callback) {
        CompletionPhoto.afterRender();
    }
}
