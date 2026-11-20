package dev.magehome.client;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dev.magehome.v5.runtime.ClientFacts;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.OtherClientPlayerEntity;
import net.minecraft.client.option.Perspective;
import net.minecraft.client.texture.NativeImage;
import net.minecraft.client.util.ScreenshotRecorder;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.RaycastContext;
import net.minecraft.world.chunk.ChunkStatus;

import java.util.function.Consumer;

/** A client-only photograph. The camera entity is never spawned or sent to the server. */
public final class CompletionPhoto {
    private CompletionPhoto() {}

    private record Subject(String worldUid, BlockPos min, BlockPos max, int frontX, int frontZ) {
        static Subject read(String input) {
            JsonObject data = JsonParser.parseString(input).getAsJsonObject();
            BlockPos min = point(data.getAsJsonArray("min"));
            BlockPos max = point(data.getAsJsonArray("max"));
            for (int span : new int[]{max.getX()-min.getX(), max.getY()-min.getY(), max.getZ()-min.getZ()}) {
                if (span < 0 || span > 95) throw new IllegalArgumentException("Invalid house bounds");
            }
            String facing = data.get("facing").getAsString();
            return new Subject(data.get("world_uid").getAsString(), min, max,
                    facing.equals("east") ? 1 : facing.equals("west") ? -1 : 0,
                    facing.equals("south") ? 1 : facing.equals("north") ? -1 : 0);
        }

        private static BlockPos point(JsonArray array) {
            return new BlockPos(array.get(0).getAsInt(), array.get(1).getAsInt(), array.get(2).getAsInt());
        }

        Vec3d center() {
            return Vec3d.ofCenter(min).lerp(Vec3d.ofCenter(max), 0.5);
        }

        boolean contains(BlockPos pos) {
            return pos.getX() >= min.getX() && pos.getX() <= max.getX()
                    && pos.getY() >= min.getY() && pos.getY() <= max.getY()
                    && pos.getZ() >= min.getZ() && pos.getZ() <= max.getZ();
        }
    }

    private record Shot(ClientWorld world, OtherClientPlayerEntity camera, Consumer<NativeImage> output,
                        Consumer<String> failure, long expires) {}
    private record View(Entity camera, Perspective perspective, boolean hudHidden) {}
    private static Shot pending;
    private static View saved;

    /** Called by the local screenshot bridge on the client thread. */
    public static void request(String input, Consumer<NativeImage> output, Consumer<String> failure) {
        MinecraftClient client = MinecraftClient.getInstance();
        Subject subject = Subject.read(input);
        if (client.world == null || client.player == null || !subject.worldUid().equals(ClientFacts.worldUid())) {
            failure.accept("Откройте мир и измерение готового дома.");
            return;
        }
        if (pending != null && System.nanoTime() < pending.expires()) {
            failure.accept("Minecraft уже делает снимок готового объекта.");
            return;
        }
        Vec3d eye = viewpoint(client, subject);
        if (eye == null) {
            failure.accept("Дом не помещается в свободный кадр из загруженной области Minecraft.");
            return;
        }
        OtherClientPlayerEntity camera = new OtherClientPlayerEntity(client.world, client.player.getGameProfile());
        Vec3d look = subject.center().subtract(eye);
        float yaw = (float) Math.toDegrees(Math.atan2(-look.x, look.z));
        float pitch = (float) -Math.toDegrees(Math.atan2(look.y, Math.hypot(look.x, look.z)));
        camera.refreshPositionAndAngles(eye.x, eye.y - camera.getStandingEyeHeight(), eye.z, yaw, pitch);
        camera.resetPosition();
        camera.lastYaw = yaw;
        camera.lastPitch = pitch;
        pending = new Shot(client.world, camera, output, failure, System.nanoTime() + 5_000_000_000L);
    }

    private static Vec3d viewpoint(MinecraftClient client, Subject subject) {
        Vec3d center = subject.center();
        if (center.squaredDistanceTo(client.player.getEntityPos()) > 128 * 128) return null;
        Vec3d front = new Vec3d(subject.frontX, 0, subject.frontZ);
        if (front.lengthSquared() != 1) return null;
        Vec3d side = new Vec3d(-front.z, 0, front.x);
        double width = subject.max.getX() - subject.min.getX() + 1;
        double height = subject.max.getY() - subject.min.getY() + 1;
        double depth = subject.max.getZ() - subject.min.getZ() + 1;
        double size = Math.max(Math.max(width, depth), height);
        for (double turn : new double[]{0.40, -0.40, 0, 1.3, -1.3, 2.7, -2.7, Math.PI}) {
            Vec3d horizontal = front.multiply(Math.cos(turn)).add(side.multiply(Math.sin(turn)));
            for (double rise : new double[]{0.35, 0.65, 0.12}) {
                for (double setback : new double[]{1.12, 1.38, 1.75, 2.15, 2.65}) {
                    Vec3d eye = center.add(horizontal.multiply(Math.max(7, size * setback)))
                            .add(0, Math.max(1.5, size * rise), 0);
                    if (fits(client, subject, eye) && visible(client, subject, eye)) return eye;
                }
            }
        }
        return null;
    }

    private static boolean fits(MinecraftClient client, Subject subject, Vec3d eye) {
        Vec3d forward = subject.center().subtract(eye).normalize();
        Vec3d right = forward.crossProduct(new Vec3d(0, 1, 0)).normalize();
        Vec3d up = right.crossProduct(forward);
        var framebuffer = client.getFramebuffer();
        double aspect = (double) framebuffer.textureWidth / Math.max(1, framebuffer.textureHeight);
        double margin = Math.tan(Math.toRadians(client.options.getFov().getValue() / 2.0)) * 0.74;
        for (int x : new int[]{subject.min.getX(), subject.max.getX()+1}) {
            for (int y : new int[]{subject.min.getY(), subject.max.getY()+1}) {
                for (int z : new int[]{subject.min.getZ(), subject.max.getZ()+1}) {
                    Vec3d corner = new Vec3d(x, y, z).subtract(eye);
                    double depth = corner.dotProduct(forward);
                    if (depth <= 0 || Math.abs(corner.dotProduct(right)) > depth * margin * aspect
                            || Math.abs(corner.dotProduct(up)) > depth * margin) return false;
                }
            }
        }
        return true;
    }

    private static boolean visible(MinecraftClient client, Subject subject, Vec3d eye) {
        ClientWorld world = client.world;
        BlockPos eyeCell = BlockPos.ofFloored(eye);
        if (eye.squaredDistanceTo(client.player.getEntityPos()) > 128 * 128
                || !loaded(world, eyeCell) || !world.getBlockState(eyeCell).isAir()) return false;
        for (int x = subject.min.getX() >> 4; x <= subject.max.getX() >> 4; x++) {
            for (int z = subject.min.getZ() >> 4; z <= subject.max.getZ() >> 4; z++) {
                if (!loaded(world, new BlockPos(x << 4, eyeCell.getY(), z << 4))) return false;
            }
        }
        Vec3d center = subject.center();
        Vec3d side = new Vec3d(eye.z - center.z, 0, center.x - eye.x).normalize();
        double breadth = Math.min(subject.max.getX()-subject.min.getX(), subject.max.getZ()-subject.min.getZ());
        double height = subject.max.getY() - subject.min.getY();
        int hits = 0;
        for (double across : new double[]{-0.26, 0, 0.26}) {
            for (double elevation : new double[]{-0.28, 0.18}) {
                Vec3d target = center.add(side.multiply(breadth * across)).add(0, height * elevation, 0);
                for (double t = 0.125; t < 1; t += 0.125) {
                    if (!loaded(world, BlockPos.ofFloored(eye.lerp(target, t)))) return false;
                }
                var hit = world.raycast(new RaycastContext(eye, target, RaycastContext.ShapeType.OUTLINE,
                        RaycastContext.FluidHandling.ANY, client.player));
                if (hit.getType() != HitResult.Type.MISS && subject.contains(hit.getBlockPos())) hits++;
            }
        }
        return hits >= 4;
    }

    private static boolean loaded(ClientWorld world, BlockPos point) {
        return world.getChunkManager().getChunk(point.getX() >> 4, point.getZ() >> 4, ChunkStatus.FULL, false) != null;
    }

    public static void beforeRender() {
        if (pending == null) return;
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.world != pending.world() || client.currentScreen != null || System.nanoTime() >= pending.expires()) {
            pending.failure().accept("Откройте вид игры в мире готового дома и повторите снимок.");
            pending = null;
            return;
        }
        saved = new View(client.getCameraEntity(), client.options.getPerspective(), client.options.hudHidden);
        client.options.setPerspective(Perspective.FIRST_PERSON);
        client.options.hudHidden = true;
        client.setCameraEntity(pending.camera());
    }

    public static boolean isRendering() {
        return saved != null;
    }

    public static void afterRender() {
        if (saved == null) return;
        MinecraftClient client = MinecraftClient.getInstance();
        Shot shot = pending;
        pending = null;
        try {
            if (client.world != shot.world() || client.currentScreen != null
                    || client.gameRenderer.getCamera().getFocusedEntity() != shot.camera()) {
                shot.failure().accept("Minecraft не отрисовал выбранный вид готового дома.");
                return;
            }
            ScreenshotRecorder.takeScreenshot(client.getFramebuffer(), shot.output());
        } finally {
            client.setCameraEntity(saved.camera());
            client.options.setPerspective(saved.perspective());
            client.options.hudHidden = saved.hudHidden();
            saved = null;
        }
    }
}
