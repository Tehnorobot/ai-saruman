package lab.treegrow;

import adris.altoclef.AltoClef;

public final class TreeGrowClient {
    private static GrowTreeTask owned;
    public static int starts;
    public static void publish() {}
    public static int start() { starts++; owned=new GrowTreeTask(); AltoClef.getInstance().runUserTask(owned); return 1; }
    public static GrowTreeTask current() { return owned; }
    public static void cancel(String reason) { owned.terminal=true; AltoClef.getInstance().cancelUserTask(); }
    public static final class GrowTreeTask {
        private boolean terminal, success, acquiring;
        private String result="", outcome="running", interruption="";
        public boolean expired;
        private Object species=new Object();
        private final Acquisition acquisition=new Acquisition();
        public boolean isFinished() { return terminal; }
        public boolean success() { return success; }
        public boolean planted() { return true; }
        public String result() { return result; }
        public int boneUses() { return 7; }
        public int plantSends() { return 1; }
        public boolean expired() { return expired; }
        public Object site() { return null; }
        private Object sapling() { return "sapling"; }
        private int count(Object item) { return 0; }
        public boolean acquisitionRearmed() { return species==null && acquiring && !acquisition.stopped(); }
        public int acquisitionResets() { return acquisition.resets; }
        public boolean firstTickWouldReportNoSapling() { return species==null && acquisition.stopped(); }
        public String outcome() { return outcome; }
        public String reason() { return terminal ? result : ""; }
        public String interruption() { return interruption; }
        public int sites() { return 2; }
        public int uses() { return 7; }
        public int giveSends() { return 1; }
        public long remainingNanos() { return 100; }
        public void ended(String reason, String state) { terminal=true; result=reason; outcome=state; }
        public void interrupted() { ended("Отменено: задача UnionClef прервана (scheduler)","cancelled"); interruption="scheduler"; acquisition.stopped=true; }
    }
    /** Task.stop leaves first=true and stopped=true; reset clears only its lifecycle. */
    public static final class Acquisition {
        private boolean stopped=true;
        private int resets;
        public boolean stopped() { return stopped; }
        public void reset() { stopped=false; resets++; }
    }
}
