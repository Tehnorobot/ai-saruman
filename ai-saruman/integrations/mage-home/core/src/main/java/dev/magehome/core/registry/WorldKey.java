package dev.magehome.core.registry;

/**
 * World key of a house record (ТЗ.md section 5).
 *
 * <ul>
 *   <li>server: {@code server|host:port|realm_id}</li>
 *   <li>single player: {@code singleplayer|<absolute path of the save folder>}</li>
 * </ul>
 */
public final class WorldKey {

    private WorldKey() {
    }

    public static String forServer(String host, int port, String realmId) {
        if (host == null || host.isBlank()) {
            throw new IllegalArgumentException("host must not be blank");
        }
        if (!RealmConfig.isValidRealmId(realmId)) {
            throw new IllegalArgumentException("invalid realm_id: " + realmId);
        }
        return "server|" + host + ":" + port + "|" + realmId;
    }

    public static String forSingleplayer(String absoluteSavePath) {
        if (absoluteSavePath == null || absoluteSavePath.isBlank()) {
            throw new IllegalArgumentException("save path must not be blank");
        }
        if (!isAbsoluteSavePath(absoluteSavePath)) {
            throw new IllegalArgumentException("save path must be absolute: " + absoluteSavePath);
        }
        return "singleplayer|" + absoluteSavePath;
    }

    // Recognize both supported host syntaxes even when tests run on Linux.
    // This validates an identity string; it neither opens a path nor grants access.
    // Keep the supplied spelling so existing registry keys remain stable.
    private static boolean isAbsoluteSavePath(String path) {
        if (path.indexOf('\0') >= 0) return false;
        if (path.startsWith("/")) return true;
        if (path.length() >= 3 && isDriveLetter(path.charAt(0)) && path.charAt(1) == ':'
                && (path.charAt(2) == '\\' || path.charAt(2) == '/')) return true;
        if (path.startsWith("\\\\")) {
            String unc = path.substring(2).replace('\\', '/');
            int separator = unc.indexOf('/');
            return separator > 0 && separator + 1 < unc.length()
                    && unc.charAt(separator + 1) != '/';
        }
        return false;
    }

    private static boolean isDriveLetter(char value) {
        return (value >= 'A' && value <= 'Z') || (value >= 'a' && value <= 'z');
    }

    public static boolean isServerKey(String worldKey) {
        return worldKey != null && worldKey.startsWith("server|");
    }

    public static boolean isSingleplayerKey(String worldKey) {
        return worldKey != null && worldKey.startsWith("singleplayer|");
    }
}
