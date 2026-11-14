import java.lang.reflect.Field;
import java.util.*;
import org.saruman.bridge.ModGateway;
import adris.altoclef.AltoClef;
import dev.autodefense.client.AutoDefenseClient;
import lab.treegrow.TreeGrowClient;

/** Offline ABI fixtures only. No gameplay mechanics are mocked as acceptance. */
public final class GatewayContractTest {
    private static final Object CLIENT=new GatewayContractTest();
    private static Map<String,Object> invoke(String operation, Map<String,Object> args) throws Exception {
        return new ModGateway(CLIENT,operation,args).get();
    }
    private static void require(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }
    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        // Seed the verifier ONLY in this test process; production verifies actual JAR hashes.
        Field f=ModGateway.class.getDeclaredField("verified"); f.setAccessible(true);
        Set<Class<?>> verified=(Set<Class<?>>)f.get(null);
        verified.add(AltoClef.class); verified.add(AutoDefenseClient.class); verified.add(TreeGrowClient.class);
        Map<String,Object> snapshot=invoke("snapshot",Map.of());
        require(snapshot.get("status").equals("ready"),snapshot.toString());
        String world=(String)snapshot.get("world");
        require(invoke("tree_start",Map.of("world","wrong","request_id","a")).get("reason").equals("stale_world"),"world guard");
        invoke("defense",Map.of("enabled",true)); invoke("defense",Map.of("enabled",true));
        require(AutoDefenseClient.runtime().toggles==1,"idempotent enable");
        AutoDefenseClient.runtime().busy(true);
        require(invoke("tree_start",Map.of("world",world,"request_id","a")).get("status").equals("busy"),"defense owns body");
        AutoDefenseClient.runtime().busy(false);
        invoke("tree_start",Map.of("world",world,"request_id","a"));
        invoke("tree_start",Map.of("world",world,"request_id","a"));
        require(TreeGrowClient.starts==1,"no duplicate start");
        invoke("maintain",Map.of("world",world));
        require(TreeGrowClient.current().acquisitionRearmed(),"consumed sapling reacquisition");
        TreeGrowClient.current().interrupted(); // Terminal root is still retained by this fixture chain.
        Object original=TreeGrowClient.current();
        require(invoke("tree_resume",Map.of("world",world,"run_id","a","request_id","resume-a")).get("status").equals("resumed"),"resume");
        require(TreeGrowClient.current()==original && TreeGrowClient.current().boneUses()==7,"same task and budget");
        require(!TreeGrowClient.current().firstTickWouldReportNoSapling(),"stopped acquisition mistaken for search failure on resume");
        require(TreeGrowClient.current().acquisitionResets()==2,"acquisition lifecycle reset once per transition");
        require(TreeGrowClient.current().outcome().equals("running") && TreeGrowClient.current().interruption().isEmpty(),"new status fields reset on resume");
        Map<?,?> resumedTree=(Map<?,?>)invoke("snapshot",Map.of()).get("tree");
        require(resumedTree.get("sites").equals(2) && resumedTree.get("giveSends").equals(1),"new counters preserved");
        invoke("tree_resume",Map.of("world",world,"run_id","a","request_id","resume-a"));
        require(TreeGrowClient.starts==1,"resume receipt does not create new tree");
        require(TreeGrowClient.current().acquisitionResets()==2,"cached resume must not reset acquisition again");
        TreeGrowClient.current().interrupted();
        AltoClef.PriorityChain recovery=new AltoClef.PriorityChain("Unstuck Chain");
        AltoClef.getInstance().runner.select(recovery,55);
        Map<String,Object> recoverySnapshot=invoke("snapshot",Map.of());
        require(Boolean.TRUE.equals(recoverySnapshot.get("scheduler_busy")),"higher priority recovery must own controls");
        require(Boolean.TRUE.equals(((Map<?,?>)recoverySnapshot.get("tree")).get("resumable")),"scheduler cancellation is resumable");
        require(invoke("tree_resume",Map.of("world",world,"run_id","a","request_id","recovery-resume")).get("reason").equals("native_scheduler"),"do not interrupt recovery");
        require(TreeGrowClient.current().acquisitionResets()==2,"busy recovery must not reset child");
        AltoClef.getInstance().runner.select(recovery,Float.NaN);
        require(Boolean.TRUE.equals(invoke("snapshot",Map.of()).get("scheduler_busy")),"unknown priority is not permission to steal controls");
        AltoClef.getInstance().runner.select(new AltoClef.PriorityChain("Mob Defense"),0);
        require(Boolean.FALSE.equals(invoke("snapshot",Map.of()).get("scheduler_busy")),"idle lower-priority survival must not block forever");
        require(invoke("tree_resume",Map.of("world",world,"run_id","a","request_id","recovery-resume")).get("status").equals("resumed"),"resume after recovery releases priority");
        require(TreeGrowClient.current()==original && TreeGrowClient.current().boneUses()==7,"recovery retains exact task and budget");
        TreeGrowClient.current().ended("Отменено пользователем", "cancelled");
        require(invoke("tree_resume",Map.of("world",world,"run_id","a","request_id","manual-retry")).get("reason").equals("resume_unsupported"),"manual cancellation cannot resume");
        TreeGrowClient.current().interrupted(); TreeGrowClient.current().expired=true;
        require(invoke("tree_resume",Map.of("world",world,"run_id","a","request_id","expired-retry")).get("reason").equals("resume_unsupported"),"expired tree cannot resume");
        TreeGrowClient.current().expired=false;
        TreeGrowClient.current().ended("Неудача: саженец не найден в радиусе 64 блоков", "failed");
        require(invoke("tree_resume",Map.of("world",world,"run_id","a","request_id","failed-retry")).get("reason").equals("resume_unsupported"),"acquisition failure cannot resume");
        invoke("cancel",Map.of("world",world,"run_id","a"));
        invoke("command",Map.of("world",world,"request_id","cmd1","command","@get bed 1"));
        invoke("command",Map.of("world",world,"request_id","cmd1","command","@get bed 1"));
        require(AltoClef.getInstance().commands.sent==1,"command receipt");
        require(AltoClef.getInstance().commands.last.equals("@get bed 1"),"correct native prefix");
        Object foreign=new Object(); AltoClef.getInstance().chain.setTask(foreign);
        invoke("stop_command",Map.of("world",world,"run_id","cmd1"));
        require(AltoClef.getInstance().chain.task==foreign,"do not cancel foreign task");
        AltoClef.getInstance().chain.setTask(null);
        invoke("command",Map.of("world",world,"request_id","cmd2","command","@goto 1 2 3"));
        invoke("stop_command",Map.of("world",world,"run_id","cmd2"));
        require(AltoClef.getInstance().chain.task==null,"cancel own command task");
        AltoClef.getInstance().player.alive=false;
        require(invoke("command",Map.of("world",world,"request_id","dead","command","@goto 1 2 3")).get("reason").equals("player_not_alive"),"dead player guard");
        AltoClef.getInstance().player.alive=true;
        AltoClef.getInstance().world=new AltoClef.World();
        snapshot=invoke("snapshot",Map.of());
        require(!world.equals(snapshot.get("world")),"world generation");
        require(((Map<?,?>)snapshot.get("tree")).get("run_id").equals(""),"no stale tree");
        world=(String)snapshot.get("world");
        AltoClef.getInstance().defense.setTask(new AltoClef.CommandTask());
        require(invoke("snapshot",Map.of()).get("native_defense_task")!=null,"native residual task must be visible");
        require(invoke("halt",Map.of("world","old-world")).get("reason").equals("stale_world"),"stale Stop must not stop the new world");
        require(AltoClef.getInstance().runner.isActive(),"stale Stop disabled runner");
        require(invoke("halt",Map.of("world",world)).get("status").equals("halted"),"explicit global stop");
        require(!AltoClef.getInstance().runner.isActive() && AltoClef.getInstance().defense.task==null,"native defense survived Stop");
        require(invoke("halt",Map.of("world",world)).get("status").equals("halted"),"idempotent Stop");
        System.out.println("Native gateway contract: PASS (offline fixtures)");
    }
}
