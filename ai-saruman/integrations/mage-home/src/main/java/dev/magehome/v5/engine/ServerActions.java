package dev.magehome.v5.engine;

import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.block.CarpetBlock;
import net.minecraft.block.DoorBlock;
import net.minecraft.block.FenceGateBlock;
import net.minecraft.block.ScaffoldingBlock;
import net.minecraft.block.StairsBlock;
import net.minecraft.block.TrapdoorBlock;
import net.minecraft.client.MinecraftClient;
import net.minecraft.component.DataComponentTypes;
import net.minecraft.entity.decoration.painting.PaintingEntity;
import net.minecraft.item.BlockItem;
import net.minecraft.item.Item;
import net.minecraft.item.ItemPlacementContext;
import net.minecraft.item.ItemStack;
import net.minecraft.item.ScaffoldingItem;
import net.minecraft.network.packet.c2s.play.PlayerActionC2SPacket;
import net.minecraft.network.packet.c2s.play.PlayerInteractBlockC2SPacket;
import net.minecraft.network.packet.c2s.play.PlayerMoveC2SPacket;
import net.minecraft.network.packet.c2s.play.UpdateSelectedSlotC2SPacket;
import net.minecraft.registry.Registries;
import net.minecraft.registry.tag.BlockTags;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.state.property.Properties;
import net.minecraft.block.enums.DoubleBlockHalf;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.RaycastContext;
import net.minecraft.world.chunk.ChunkStatus;

import java.util.*;
import java.util.function.BiPredicate;
import java.util.function.Predicate;

/** Loaded-chunk observations and ordinary, non-predictive client protocol actions. */
final class ServerActions {
    private final MinecraftClient client;
    ServerActions(MinecraftClient client) { this.client = client; }

    BlockState read(BlockPos p) {
        var w = client.world;
        if (w == null || !inWorld(p) || w.getChunkManager().getChunk(p.getX() >> 4, p.getZ() >> 4,
                ChunkStatus.FULL, false) == null) return null;
        return w.getBlockState(p);
    }
    boolean inWorld(BlockPos p) {
        return client.world != null && !client.world.isOutOfHeightLimit(p) && client.world.getWorldBorder().contains(p);
    }
    static boolean dry(BlockState state) { return state != null && state.getFluidState().isEmpty(); }
    static boolean scaffold(BlockState state) {
        return dry(state) && state.isOf(Blocks.SCAFFOLDING) && state.get(ScaffoldingBlock.DISTANCE) < ScaffoldingBlock.MAX_DISTANCE;
    }
    boolean scaffold(BlockPos p) { return scaffold(read(p)); }
    boolean stable(BlockPos p) {
        var s = read(p);
        return dry(s) && s.isSideSolidFullSquare(client.world, p, Direction.UP)
                && !(s.getBlock() instanceof net.minecraft.block.FallingBlock)
                && !Registries.BLOCK.getId(s.getBlock()).getPath().endsWith("_leaves");
    }
    boolean passable(BlockPos p, boolean allowDoor) {
        var s = read(p);
        if (!dry(s)) return false;
        if (allowDoor && s.getBlock() instanceof DoorBlock
                && !Registries.BLOCK.getId(s.getBlock()).getPath().equals("iron_door")) return true;
        return s.getCollisionShape(client.world, p).isEmpty();
    }

    boolean closedWoodenDoor(BlockPos p) {
        var state = read(p);
        return state != null && state.getBlock() instanceof DoorBlock && !state.get(Properties.OPEN)
                && !Registries.BLOCK.getId(state.getBlock()).getPath().equals("iron_door");
    }

    BlockHitResult doorHit(BlockPos p, Vec3d eye, double reach) {
        if (!closedWoodenDoor(p)) return null;
        var door = read(p);
        var hit = doorHalfHit(p, door, eye, reach);
        if (hit != null) return hit;
        var other = door.get(DoorBlock.HALF) == DoubleBlockHalf.LOWER ? p.up() : p.down();
        var otherState = read(other);
        return closedWoodenDoor(other) && otherState.isOf(door.getBlock())
                ? doorHalfHit(other, otherState, eye, reach) : null;
    }

    private BlockHitResult doorHalfHit(BlockPos pos, BlockState state, Vec3d eye, double reach) {
        var shape = state.getOutlineShape(client.world, pos, net.minecraft.block.ShapeContext.of(client.player));
        for (var box : shape.getBoundingBoxes()) {
            var target = box.getCenter().add(pos.getX(), pos.getY(), pos.getZ());
            var hit = ray(eye, target);
            if (hit.getType() == HitResult.Type.BLOCK && pos.equals(hit.getBlockPos())
                    && eye.squaredDistanceTo(hit.getPos()) <= reach * reach) return hit;
        }
        return null;
    }
    boolean stand(BlockPos p, boolean allowDoor) {
        var feet = read(p);
        boolean headClear = passable(p.up(), allowDoor) || scaffold(p.up());
        return headClear && (scaffold(feet) || (passable(p, allowDoor) || dry(feet) && feet.getBlock() instanceof CarpetBlock)
                && (stable(p.down()) || scaffold(p.down()) || stair(p.down())));
    }

    boolean stair(BlockPos p) {
        var state = read(p);
        return dry(state) && state.getBlock() instanceof StairsBlock;
    }

    private boolean stairTransition(BlockPos from, BlockPos to, Direction movement, int rise) {
        if (rise == 0) return true;
        var support = read((rise > 0 ? to : from).down());
        if (support == null || !(support.getBlock() instanceof StairsBlock)) return true;
        return support.get(StairsBlock.FACING) == (rise > 0 ? movement : movement.getOpposite());
    }
    boolean overlapsPlayer(BlockPos p) {
        return client.player != null && client.player.getBoundingBox().intersects(new Box(p));
    }
    BlockPos feet() { return client.player.getBlockPos(); }
    boolean centered(BlockPos p, double tolerance) {
        if (client.player == null || !centeredHorizontally(p, tolerance)) return false;
        double offset = p.getY() - client.player.getY();
        return Math.abs(offset) < .25 && feet().equals(p)
                || stair(p.down()) && Math.abs(offset - .5) < .15;
    }

    boolean centeredHorizontally(BlockPos p, double tolerance) {
        return Math.abs(client.player.getX() - p.getX() - .5) < tolerance
                && Math.abs(client.player.getZ() - p.getZ() - .5) < tolerance;
    }

    /** No pathfinder-terrain side effects: only known, dry walkable cells, no breaking or pillaring. */
    List<BlockPos> route(BlockPos start, Predicate<BlockPos> goal, int budget, boolean allowDoor) {
        return route(start, goal, budget, allowDoor, (from, to) -> true);
    }

    List<BlockPos> route(BlockPos start, Predicate<BlockPos> goal, int budget, boolean allowDoor,
                         BiPredicate<BlockPos, BlockPos> allowedTransition) {
        var frontier = new ArrayDeque<BlockPos>();
        var prev = new HashMap<BlockPos, BlockPos>();
        frontier.add(start);
        prev.put(start, start);
        int[] steps = client.player.getHealth() >= 10 ? new int[] {0, 1, -1, -2, -3, -4, -5}
                : new int[] {0, 1, -1, -2, -3};
        while (!frontier.isEmpty() && budget-- > 0) {
            var p = frontier.removeFirst();
            if (goal.test(p)) {
                var path = new ArrayList<BlockPos>();
                for (var current = p; !current.equals(start); current = prev.get(current)) path.add(current);
                Collections.reverse(path);
                return List.copyOf(path);
            }
            boolean onScaffold = scaffold(p) || scaffold(p.down());
            for (var dir : Direction.Type.HORIZONTAL) for (int delta : steps) {
                var next = p.offset(dir).add(0, delta, 0);
                if (onScaffold && delta < -1 && (delta < -3 || !stable(next.down()) || scaffold(next.down()))) continue;
                if (prev.containsKey(next) || !stand(next, allowDoor)) continue;
                if (!stairTransition(p, next, dir, delta) || !allowedTransition.test(p, next)) continue;
                if (delta > 0 && !passable(p.up(2), false)) continue;
                if (delta < 0 && !clearDescent(next, -delta)) continue;
                frontier.addLast(next);
                prev.put(next, p);
            }
            for (var next : List.of(p.up(), p.down())) {
                if (prev.containsKey(next) || !stand(next, allowDoor) || !scaffoldVertical(p, next)
                        || !allowedTransition.test(p, next)) continue;
                frontier.addLast(next);
                prev.put(next, p);
            }
        }
        return null;
    }

    boolean scaffoldVertical(BlockPos from, BlockPos to) {
        if (from.getX() != to.getX() || from.getZ() != to.getZ() || from.getY() == to.getY()) return false;
        return scaffold(from.getY() < to.getY() ? from : to);
    }

    private boolean clearDescent(BlockPos landing, int depth) {
        for (int offset = 2; offset <= depth + 1; offset++) {
            if (!passable(landing.up(offset), false)) return false;
        }
        return true;
    }

    BlockHitResult breakHit(BlockPos p, Vec3d eye, double reach) {
        if (scaffold(p)) return scaffoldHits(p, eye, reach).stream().findFirst().orElse(null);
        var end = Vec3d.ofCenter(p);
        if (eye.squaredDistanceTo(end) > (reach + .87) * (reach + .87)) return null;
        var hit = ray(eye, end);
        return hit.getType() == HitResult.Type.BLOCK && p.equals(hit.getBlockPos())
                && eye.squaredDistanceTo(hit.getPos()) <= reach * reach ? hit : null;
    }
    List<BlockHitResult> placeHits(BlockPos p, Vec3d eye, double reach) {
        return placeHits(p, eye, reach, false);
    }

    private List<BlockHitResult> placeHits(BlockPos p, Vec3d eye, double reach, boolean edgeSamples) {
        var result = new ArrayList<BlockHitResult>();
        var at = read(p);
        if (at == null || !at.isReplaceable()) return result;
        for (Direction side : Direction.values()) {
            BlockPos support = p.offset(side.getOpposite());
            var state = read(support);
            if (!dry(state) || state.getCollisionShape(client.world, support).isEmpty()
                    || state.hasBlockEntity() || state.getBlock() instanceof DoorBlock
                    || state.getBlock() instanceof TrapdoorBlock || state.getBlock() instanceof FenceGateBlock) continue;
            Vec3d faceCenter = Vec3d.ofCenter(support).add(side.getOffsetX() * .499, side.getOffsetY() * .499, side.getOffsetZ() * .499);
            addFaceHit(result, support, side, eye, faceCenter, reach);
            if (edgeSamples) {
                for (Direction along : Direction.values()) {
                    if (along.getAxis() == side.getAxis()) continue;
                    var point = faceCenter.add(along.getOffsetX() * .29, along.getOffsetY() * .29, along.getOffsetZ() * .29);
                    addFaceHit(result, support, side, eye, point, reach);
                }
            }
        }
        return result;
    }

    private void addFaceHit(List<BlockHitResult> result, BlockPos support, Direction side, Vec3d eye, Vec3d point, double reach) {
        if (eye.squaredDistanceTo(point) > reach * reach) return;
        var hit = ray(eye, point);
        if (hit.getType() == HitResult.Type.BLOCK && support.equals(hit.getBlockPos()) && side == hit.getSide()) result.add(hit);
    }
    private BlockHitResult ray(Vec3d eye, Vec3d end) {
        return client.world.raycast(new RaycastContext(eye, end, RaycastContext.ShapeType.OUTLINE,
                RaycastContext.FluidHandling.NONE, client.player));
    }

    record Choice(BlockHitResult hit, float yaw, float pitch) { }

    private List<BlockHitResult> scaffoldHits(BlockPos p, Vec3d eye, double reach) {
        var result = new ArrayList<BlockHitResult>();
        for (var side : Direction.values()) {
            var center = Vec3d.ofCenter(p).add(side.getOffsetX() * .499, side.getOffsetY() * .499, side.getOffsetZ() * .499);
            addFaceHit(result, p, side, eye, center, reach);
            // The frame is targetable before the inventory selects the scaffold item.
            for (var edge : Direction.Type.HORIZONTAL) {
                if (edge.getAxis() == side.getAxis()) continue;
                addFaceHit(result, p, side, eye, center.add(edge.getOffsetX() * .43, 0, edge.getOffsetZ() * .43), reach);
            }
        }
        return result;
    }

    Choice placement(BuildPlan.Goal goal, Item item, double reach) {
        return placement(goal, item, client.player.getEyePos(), reach);
    }

    Choice placement(BuildPlan.Goal goal, Item item, Vec3d eye, double reach) {
        if (!(item instanceof BlockItem blockItem)) return null;
        var hits = placeHits(goal.pos(), eye, reach, !goal.state().isEmpty());
        if (item instanceof ScaffoldingItem) {
            // Vanilla extends upwards from the side of any accessible piece of the column.
            for (var p = goal.pos().down(); scaffold(p); p = p.down()) {
                if (Math.abs(p.getY() + .5 - eye.y) <= reach + .5) hits.addAll(scaffoldHits(p, eye, reach));
            }
        }
        for (var hit : hits) {
            var angles = angles(eye, hit.getPos());
            float oldYaw = client.player.getYaw(), oldPitch = client.player.getPitch();
            try {
                client.player.setYaw(angles[0]);
                client.player.setPitch(angles[1]);
                var context = new ItemPlacementContext(client.player, Hand.MAIN_HAND, new ItemStack(item), hit);
                if (!context.canPlace()) continue;
                context = blockItem.getPlacementContext(context);
                if (context == null || !goal.pos().equals(context.getBlockPos())) continue;
                var state = goal.vanilla().getBlock().getPlacementState(context);
                if (goal.matches(state) && state.canPlaceAt(client.world, goal.pos())) return new Choice(hit, angles[0], angles[1]);
            } finally {
                client.player.setYaw(oldYaw);
                client.player.setPitch(oldPitch);
            }
        }
        return null;
    }

    static float[] angles(Vec3d eye, Vec3d target) {
        Vec3d d = target.subtract(eye);
        return new float[] {(float) Math.toDegrees(Math.atan2(d.z, d.x)) - 90F,
                (float) -Math.toDegrees(Math.atan2(d.y, Math.hypot(d.x, d.z)))};
    }

    void look(float yaw, float pitch) {
        client.player.setYaw(yaw);
        client.player.setPitch(pitch);
        client.getNetworkHandler().sendPacket(new PlayerMoveC2SPacket.LookAndOnGround(yaw, pitch,
                client.player.isOnGround(), client.player.horizontalCollision));
    }
    void place(Choice choice) {
        look(choice.yaw(), choice.pitch());
        // Non-predicted by design: sequence 0 (no client prediction to acknowledge), and no
        // interactBlock/doItemUse which would apply optimistic client-world placement.
        client.getNetworkHandler().sendPacket(new PlayerInteractBlockC2SPacket(Hand.MAIN_HAND, choice.hit(), 0));
    }
    void breakPacket(PlayerActionC2SPacket.Action action, BlockHitResult hit) {
        client.getNetworkHandler().sendPacket(new PlayerActionC2SPacket(action, hit.getBlockPos(), hit.getSide()));
    }

    int count(Item item) {
        var inv = client.player.getInventory();
        int count = 0;
        for (int slot = 0; slot < 36; slot++) if (inv.getStack(slot).isOf(item)) count += inv.getStack(slot).getCount();
        return count;
    }

    Item miningTool(BlockState block) {
        var inventory = client.player.getInventory();
        Item best = null;
        float speed = 1;
        for (int slot = 0; slot < 36; slot++) {
            var stack = inventory.getStack(slot);
            float current = stack.isEmpty() ? 0 : stack.getMiningSpeedMultiplier(block);
            if (current > speed && (!block.isToolRequired() || stack.isSuitableFor(block))) {
                best = stack.getItem();
                speed = current;
            }
        }
        return best;
    }

    static Item supplyTool(BlockState block) {
        if (block.isIn(BlockTags.PICKAXE_MINEABLE)) return net.minecraft.item.Items.DIAMOND_PICKAXE;
        if (block.isIn(BlockTags.SHOVEL_MINEABLE)) return net.minecraft.item.Items.DIAMOND_SHOVEL;
        if (block.isIn(BlockTags.AXE_MINEABLE)) return net.minecraft.item.Items.DIAMOND_AXE;
        if (block.isIn(BlockTags.HOE_MINEABLE)) return net.minecraft.item.Items.DIAMOND_HOE;
        return null;
    }
    int capacity(Item item) {
        var inv = client.player.getInventory();
        var normal = new ItemStack(item);
        int capacity = 0;
        for (int slot = 0; slot < 36; slot++) {
            var stack = inv.getStack(slot);
            if (stack.isEmpty()) capacity += normal.getMaxCount();
            else if (ItemStack.areItemsAndComponentsEqual(stack, normal)) capacity += stack.getMaxCount() - stack.getCount();
        }
        return capacity;
    }
    int hotbar(Item item) {
        return hotbar(stack -> stack.isOf(item));
    }
    int hotbar(Predicate<ItemStack> accepts) {
        var inv = client.player.getInventory();
        if (accepts.test(inv.getSelectedStack())) return inv.getSelectedSlot();
        for (int slot = 0; slot < 9; slot++) if (accepts.test(inv.getStack(slot))) return slot;
        return -1;
    }
    boolean swapToHotbar(Item item) {
        return swapToHotbar(stack -> stack.isOf(item));
    }
    boolean swapToHotbar(Predicate<ItemStack> accepts) {
        var player = client.player;
        if (client.currentScreen != null || player.currentScreenHandler != player.playerScreenHandler) return false;
        var handler = player.playerScreenHandler;
        int target = player.getInventory().getSelectedSlot();
        for (var slot : handler.slots) {
            if (slot.inventory == player.getInventory() && slot.getIndex() >= 9 && slot.getIndex() < 36
                    && accepts.test(slot.getStack())) {
                client.interactionManager.clickSlot(handler.syncId, slot.id, target, SlotActionType.SWAP, player);
                return true;
            }
        }
        return false;
    }
    void select(int slot) {
        client.player.getInventory().setSelectedSlot(slot);
        client.getNetworkHandler().sendPacket(new UpdateSelectedSlotC2SPacket(slot));
    }
    boolean canGive() {
        return client.getNetworkHandler() != null
                && client.getNetworkHandler().getCommandDispatcher().getRoot().getChild("give") != null;
    }

    void give(Item item, int count) {
        client.getNetworkHandler().sendChatCommand("give @s " + Registries.ITEM.getId(item) + " " + count);
    }

    void givePainting(BuildPlan.Painting painting) {
        // The validated resource identifier selects the variant of an inventory item.
        client.getNetworkHandler().sendChatCommand("give @s minecraft:painting[minecraft:painting/variant=\""
                + painting.variant() + "\"] 1");
    }

    boolean paintingItem(ItemStack stack, BuildPlan.Painting painting) {
        if (!stack.isOf(net.minecraft.item.Items.PAINTING)) return false;
        var variant = stack.get(DataComponentTypes.PAINTING_VARIANT);
        return variant != null && variant.getKey().map(key -> key.getValue().toString())
                .orElse("").equals(painting.variant());
    }

    Choice paintingHit(BuildPlan.Painting painting, Vec3d eye, double reach) {
        for (var hit : placeHits(painting.pos(), eye, reach)) {
            if (hit.getSide() != painting.facing()) continue;
            var rotation = angles(eye, hit.getPos());
            return new Choice(hit, rotation[0], rotation[1]);
        }
        return null;
    }

    boolean painting(BuildPlan.Painting target) {
        if (client.world == null) return false;
        return !client.world.getEntitiesByClass(PaintingEntity.class, new Box(target.pos()).expand(1), entity ->
                target.pos().equals(entity.getAttachedBlockPos())
                        && target.facing() == entity.getHorizontalFacing() && entity.canStayAttached()
                        && entity.getVariant().getKey().map(key -> key.getValue().toString()).orElse("").equals(target.variant())).isEmpty();
    }

    boolean stopOwned(adris.altoclef.tasksystem.Task expected, dev.magehome.client.UnionClefBridge bridge) {
        var chain = bridge.mod() == null ? null : bridge.mod().getUserTaskChain();
        if (chain == null || chain.getCurrentTask() != expected || expected == null) return false;
        // UserTaskChain.cancel()/normal onTaskFinish() disable the entire TaskRunner. setTask(null)
        // on this exact root detaches only that root and leaves the other native chains intact.
        chain.setTask(null);
        return chain.getCurrentTask() != expected;
    }
}
