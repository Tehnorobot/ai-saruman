package adris.altoclef;

public final class AltoClef {
    private static final AltoClef INSTANCE=new AltoClef();
    public static AltoClef getInstance() { return INSTANCE; }
    public Object world=new World();
    public final Chain chain=new Chain();
    public final Runner runner=new Runner();
    public final Chain defense=new Chain();
    public final Commands commands=new Commands();
    public final Player player=new Player();
    public Object getWorld() { return world; }
    public Player getPlayer() { return player; }
    public Chain getUserTaskChain() { return chain; }
    public Runner getTaskRunner() { return runner; }
    public Chain getMobDefenseChain() { return defense; }
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
        public String getName() { return "UserTaskChain"; }
        public float getPriority() { return 50; }
    }
    public static final class PriorityChain {
        public final String name;
        public boolean active=true;
        public PriorityChain(String name) { this.name=name; }
        public String getName() { return name; }
        public boolean isActive() { return active; }
    }
    public static final class Runner {
        public Object current;
        private Object cachedCurrentTaskChain;
        public boolean active=true;
        private final java.util.Map<String,Float> lastPriority=new java.util.HashMap<>();
        public Object getCurrentTaskChain() { return current==null?INSTANCE.chain:current; }
        public boolean isActive() { return active; }
        public void disable() { active=false;INSTANCE.chain.setTask(null);INSTANCE.defense.setTask(null);current=null; }
        public void select(PriorityChain chain,float priority) { current=chain;lastPriority.put(chain.getName(),priority); }
    }
    public static final class Commands {
        public int sent;
        public String last;
        public void execute(String line) {
            if (!line.startsWith("@")) return;
            sent++; last=line; AltoClef.getInstance().chain.setTask(new CommandTask());
        }
        public void executeWithPrefix(String line) { execute(line.startsWith("@")?line:"@"+line); }
    }
    public static final class CommandTask {
        public boolean isFinished() { return false; }
        public boolean stopped() { return false; }
    }
    public static final class Player {
        public boolean alive=true;
        public boolean method_5805() { return alive; }
    }
    public static final class World {
        public java.util.List<Player> method_18456() { return java.util.List.of(INSTANCE.player); }
    }
}
