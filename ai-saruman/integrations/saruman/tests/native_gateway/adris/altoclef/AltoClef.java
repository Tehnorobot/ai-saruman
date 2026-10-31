package adris.altoclef;

public final class AltoClef {
    private static final AltoClef INSTANCE=new AltoClef();
    public static AltoClef getInstance() { return INSTANCE; }
    public Object world=new Object();
    public final Chain chain=new Chain();
    public final Commands commands=new Commands();
    public final Player player=new Player();
    public Object getWorld() { return world; }
    public Player getPlayer() { return player; }
    public Chain getUserTaskChain() { return chain; }
    public boolean isPaused() { return false; }
    public void runUserTask(Object task) { chain.task=task; chain.active=true; }
    public void cancelUserTask() { chain.active=false; chain.task=null; }
    public Commands getCommandExecutor() { return commands; }
    public static final class Chain {
        public Object task;
        public boolean active;
        public boolean isActive() { return active; }
        public Object getCurrentTask() { return task; }
        public void setTask(Object value) { task=value; active=value!=null; }
    }
    public static final class Commands {
        public int sent;
        public String last;
        public void execute(String line) {
            if (!line.startsWith("@")) return;
            sent++; last=line; AltoClef.getInstance().chain.setTask(new Object());
        }
        public void executeWithPrefix(String line) { execute(line.startsWith("@")?line:"@"+line); }
    }
    public static final class Player {
        public boolean alive=true;
        public boolean method_5805() { return alive; }
    }
}
