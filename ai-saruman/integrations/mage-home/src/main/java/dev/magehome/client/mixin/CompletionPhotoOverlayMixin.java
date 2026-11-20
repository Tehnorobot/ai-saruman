package dev.magehome.client.mixin;

import dev.magehome.client.CompletionPhoto;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(targets = "adris.altoclef.ui.CommandStatusOverlay", remap = false)
abstract class CompletionPhotoOverlayMixin {
    @Inject(method = "render", at = @At("HEAD"), cancellable = true, remap = false)
    private void mageHomeHideTaskOverlay(CallbackInfo callback) {
        if (CompletionPhoto.isRendering()) callback.cancel();
    }
}
