package dev.autodefense.client;

public final class AutoDefenseClient {
    private static final Runtime RUNTIME=new Runtime();
    public static Runtime runtime() { return RUNTIME; }
    public static final class Runtime {
        private final Bridge bridge=new Bridge();
        private final Handover handover=new Handover();
        private boolean enabled;
        public int toggles;
        public boolean enabled() { return enabled; }
        public void toggle(Object client) { enabled=!enabled; toggles++; }
        public void busy(boolean value) { handover.state=value?"ACTIVE":"IDLE"; }
    }
    public static final class Bridge {
        private String lastLine="@stale";
        private boolean cancelledSinceCapture;
    }
    public static final class Handover {
        private String savedCommand="stale";
        public String state="IDLE";
        public String state() { return state; }
    }
}
