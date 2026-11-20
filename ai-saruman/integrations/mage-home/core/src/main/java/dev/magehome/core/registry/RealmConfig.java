package dev.magehome.core.registry;

/**
 * Content of {@code .minecraft/config/mage_home/config.json}. Only {@code realm_id} is defined:
 * 1..64 characters of {@code A-Z a-z 0-9 _ -}; the default value is {@code default}.
 */
public final class RealmConfig {

    public static final String DEFAULT_REALM_ID = "default";
    public static final int MAX_REALM_ID_LENGTH = 64;

    private final String realmId;

    public RealmConfig(String realmId) {
        if (!isValidRealmId(realmId)) {
            throw new IllegalArgumentException("invalid realm_id: " + realmId);
        }
        this.realmId = realmId;
    }

    public static RealmConfig defaultConfig() {
        return new RealmConfig(DEFAULT_REALM_ID);
    }

    public String realmId() {
        return realmId;
    }

    public static boolean isValidRealmId(String value) {
        if (value == null || value.isEmpty() || value.length() > MAX_REALM_ID_LENGTH) {
            return false;
        }
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            boolean ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')
                    || c == '_' || c == '-';
            if (!ok) {
                return false;
            }
        }
        return true;
    }
}
