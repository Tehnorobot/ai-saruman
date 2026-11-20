package dev.magehome.core.registry;

import dev.magehome.core.model.Vec3i;

/**
 * One record of {@code .minecraft/config/mage_home/houses.json} (ТЗ.md section 5).
 * The persisted fields are exactly the fields of the file; {@code visibility} is runtime only.
 */
public final class HouseRecord {

    private String houseId;
    private String worldKey;
    private String dimension;
    private String playerUuid;
    private Vec3i origin;
    private int variant;
    // Missing and explicit JSON null are both unknown, but must round-trip distinctly.
    private String templateId;
    private boolean templateIdPresent;
    private HouseState state = HouseState.BUILDING;
    private String stage = "placement";
    private int confirmedCells;
    private String lastVerifiedAt;
    private String reason;
    private Visibility visibility = Visibility.LOADED;

    public HouseRecord() {
    }

    public HouseRecord(String houseId, String worldKey, String dimension, String playerUuid, Vec3i origin,
                        int variant, HouseState state, String stage, int confirmedCells, String lastVerifiedAt,
                        String reason) {
        this.houseId = houseId;
        this.worldKey = worldKey;
        this.dimension = dimension;
        this.playerUuid = playerUuid;
        this.origin = origin;
        this.variant = variant;
        this.state = state;
        this.stage = stage;
        this.confirmedCells = confirmedCells;
        this.lastVerifiedAt = lastVerifiedAt;
        this.reason = reason;
    }

    public String houseId() {
        return houseId;
    }

    public void setHouseId(String houseId) {
        this.houseId = houseId;
    }

    public String worldKey() {
        return worldKey;
    }

    public void setWorldKey(String worldKey) {
        this.worldKey = worldKey;
    }

    public String dimension() {
        return dimension;
    }

    public void setDimension(String dimension) {
        this.dimension = dimension;
    }

    public String playerUuid() {
        return playerUuid;
    }

    public void setPlayerUuid(String playerUuid) {
        this.playerUuid = playerUuid;
    }

    public Vec3i origin() {
        return origin;
    }

    public void setOrigin(Vec3i origin) {
        this.origin = origin;
    }

    public int variant() {
        return variant;
    }

    public void setVariant(int variant) {
        this.variant = variant;
    }

    /** The persisted template identifier, or null if it is unknown. Never infer it from variant. */
    public String templateId() {
        return templateId;
    }

    /** Explicitly sets the identifier; null represents an explicitly unknown identifier. */
    public void setTemplateId(String templateId) {
        this.templateId = templateId;
        this.templateIdPresent = true;
    }

    boolean hasTemplateIdField() {
        return templateIdPresent;
    }

    public HouseState state() {
        return state;
    }

    public void setState(HouseState state) {
        this.state = state;
    }

    public String stage() {
        return stage;
    }

    public void setStage(String stage) {
        this.stage = stage;
    }

    public int confirmedCells() {
        return confirmedCells;
    }

    public void setConfirmedCells(int confirmedCells) {
        this.confirmedCells = confirmedCells;
    }

    public String lastVerifiedAt() {
        return lastVerifiedAt;
    }

    public void setLastVerifiedAt(String lastVerifiedAt) {
        this.lastVerifiedAt = lastVerifiedAt;
    }

    public String reason() {
        return reason;
    }

    public void setReason(String reason) {
        this.reason = reason;
    }

    public Visibility visibility() {
        return visibility;
    }

    public void setVisibility(Visibility visibility) {
        this.visibility = visibility;
    }

    public HouseRecord copy() {
        HouseRecord copy = new HouseRecord(houseId, worldKey, dimension, playerUuid, origin, variant, state, stage,
                confirmedCells, lastVerifiedAt, reason);
        if (templateIdPresent) {
            copy.setTemplateId(templateId);
        }
        copy.setVisibility(visibility);
        return copy;
    }

    @Override
    public String toString() {
        return "HouseRecord[" + houseId + " variant=" + variant + " state=" + state + " origin=" + origin + "]";
    }
}
