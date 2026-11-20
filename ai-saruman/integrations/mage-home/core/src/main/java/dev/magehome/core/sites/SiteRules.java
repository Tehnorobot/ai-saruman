package dev.magehome.core.sites;

import dev.magehome.core.model.Vec3i;

import java.util.Collection;

/**
 * The geometric rules of ТЗ.md section 3: service radius, search window and the minimum distance
 * to the already saved houses.
 */
public final class SiteRules {

    /** Horizontal service radius around the centre of a house. */
    public static final double SERVICE_RADIUS = 100.0;
    /** Maximum horizontal distance between the candidate centre and the player at search start. */
    public static final double MAX_CENTER_OFFSET = 32.0;
    /** Maximum height difference between the floor of the candidate and the player at search start. */
    public static final int MAX_HEIGHT_DELTA = 4;
    /** Uninterrupted client ticks that have to pass before a new house may be searched. */
    public static final int SEARCH_DELAY_TICKS = 1200;
    /** Distance by which the search is shifted when no site was found. */
    public static final int RETRY_SHIFT = 8;

    private SiteRules() {
    }

    /** Centre of a house: C = (ax + 3, ay, az + 3). */
    public static Vec3i centre(Vec3i origin) {
        return origin.add(3, 0, 3);
    }

    /** The point in front of the house where the player stands: A + (3, 0, -1). */
    public static Vec3i frontPoint(Vec3i origin) {
        return origin.add(3, 0, -1);
    }

    /** The support level under the floor: y = ay - 1. */
    public static int supportLevel(Vec3i origin) {
        return origin.y() - 1;
    }

    public static boolean insideServiceRadius(Vec3i playerPosition, Vec3i houseOrigin) {
        return centre(houseOrigin).horizontalDistanceTo(playerPosition) <= SERVICE_RADIUS;
    }

    public static boolean insideAnyRadius(Vec3i playerPosition, Collection<Vec3i> houseOrigins) {
        for (Vec3i origin : houseOrigins) {
            if (insideServiceRadius(playerPosition, origin)) {
                return true;
            }
        }
        return false;
    }

    /** The centre of a new site has to be farther than the service radius from every saved house. */
    public static boolean farFromAllHouses(Vec3i candidateOrigin, Collection<Vec3i> houseOrigins) {
        Vec3i candidateCentre = centre(candidateOrigin);
        for (Vec3i origin : houseOrigins) {
            if (candidateCentre.horizontalDistanceTo(centre(origin)) <= SERVICE_RADIUS) {
                return false;
            }
        }
        return true;
    }

    public static boolean centreWithinSearchLimit(Vec3i candidateOrigin, Vec3i searchBase) {
        return centre(candidateOrigin).horizontalDistanceTo(searchBase) <= MAX_CENTER_OFFSET;
    }

    public static boolean heightWithinLimit(int floorLevel, int baseLevel) {
        return Math.abs(floorLevel - baseLevel) <= MAX_HEIGHT_DELTA;
    }
}
