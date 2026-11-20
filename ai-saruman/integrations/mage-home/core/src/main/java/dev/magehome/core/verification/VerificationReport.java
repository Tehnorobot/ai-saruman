package dev.magehome.core.verification;

import dev.magehome.core.model.Cell;
import dev.magehome.core.template.CellSpec;
import dev.magehome.core.template.HouseTemplate;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Result of comparing a whole house volume with the project.
 *
 * <p>{@code confirmedCells} counts the occupied cells that correspond to the project (180 in a
 * complete house). It is never the number of sent clicks.
 */
public final class VerificationReport {

    private final HouseTemplate template;
    private final Map<Cell, CellVerdict> verdicts;
    private final int confirmedCells;
    private final List<Cell> missing;
    private final List<Cell> obstructed;
    private final List<Cell> wrongProperty;
    private final List<Cell> unknown;
    private final List<Cell> singleHalves;
    private final boolean airClean;

    private VerificationReport(HouseTemplate template, Map<Cell, CellVerdict> verdicts, int confirmedCells,
                               List<Cell> missing, List<Cell> obstructed, List<Cell> wrongProperty,
                               List<Cell> unknown, List<Cell> singleHalves, boolean airClean) {
        this.template = template;
        this.verdicts = Collections.unmodifiableMap(verdicts);
        this.confirmedCells = confirmedCells;
        this.missing = Collections.unmodifiableList(missing);
        this.obstructed = Collections.unmodifiableList(obstructed);
        this.wrongProperty = Collections.unmodifiableList(wrongProperty);
        this.unknown = Collections.unmodifiableList(unknown);
        this.singleHalves = Collections.unmodifiableList(singleHalves);
        this.airClean = airClean;
    }

    public static VerificationReport of(HouseTemplate template, Map<Cell, ObservedBlock> observed) {
        Map<Cell, CellVerdict> verdicts = new LinkedHashMap<>();
        List<Cell> missing = new ArrayList<>();
        List<Cell> obstructed = new ArrayList<>();
        List<Cell> wrongProperty = new ArrayList<>();
        List<Cell> unknown = new ArrayList<>();
        int confirmed = 0;
        boolean airClean = true;

        for (Cell cell : template.volumeCells()) {
            CellSpec spec = template.spec(cell);
            ObservedBlock block = observed.get(cell);
            if (block == null) {
                block = ObservedBlock.unknown();
            }
            CellVerdict verdict = CellVerifier.verify(spec, block);
            verdicts.put(cell, verdict);
            if (spec == null) {
                if (verdict != CellVerdict.MATCH) {
                    airClean = false;
                }
                continue;
            }
            switch (verdict) {
                case MATCH -> confirmed++;
                case ABSENT -> missing.add(cell);
                case WRONG_BLOCK -> obstructed.add(cell);
                case WRONG_PROPERTY -> wrongProperty.add(cell);
                case UNKNOWN -> unknown.add(cell);
                default -> throw new IllegalStateException("unexpected verdict " + verdict);
            }
        }

        List<Cell> singleHalves = new ArrayList<>();
        for (CellSpec.Operation operation : template.operations()) {
            if (!operation.isMultiCell()) {
                continue;
            }
            int matches = 0;
            for (Cell cell : operation.cells()) {
                if (verdicts.get(cell) == CellVerdict.MATCH) {
                    matches++;
                }
            }
            if (matches > 0 && matches < operation.cells().size()) {
                // A single remaining half of a door or a bed must not be overwritten.
                for (Cell cell : operation.cells()) {
                    if (verdicts.get(cell) != CellVerdict.MATCH) {
                        singleHalves.add(cell);
                    }
                }
            }
        }

        return new VerificationReport(template, verdicts, confirmed, missing, obstructed, wrongProperty, unknown,
                singleHalves, airClean);
    }

    public HouseTemplate template() {
        return template;
    }

    public Map<Cell, CellVerdict> verdicts() {
        return verdicts;
    }

    public CellVerdict verdict(Cell cell) {
        return verdicts.get(cell);
    }

    /** Occupied cells that correspond to the project (out of 180). */
    public int confirmedCells() {
        return confirmedCells;
    }

    public List<Cell> missing() {
        return missing;
    }

    public List<Cell> obstructed() {
        return obstructed;
    }

    public List<Cell> wrongProperty() {
        return wrongProperty;
    }

    public List<Cell> unknown() {
        return unknown;
    }

    /** Cells of a door or bed where only one half is present. */
    public List<Cell> singleHalves() {
        return singleHalves;
    }

    public boolean airClean() {
        return airClean;
    }

    /** True when all 180 occupied cells and all 65 air cells correspond to the project. */
    public boolean complete() {
        return confirmedCells == HouseTemplate.OCCUPIED_CELLS && airClean;
    }
}
