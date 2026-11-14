"""Execute the real reflection shim against isolated Java doubles, never Minecraft."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

ALTO = r'''
package adris.altoclef;
public class AltoClef {
 public static final AltoClef INSTANCE=new AltoClef();
 public final World world=new World(); public final Player player=new Player();public final Chain chain=new Chain(); public final Chain defense=new Chain(); public final Runner runner=new Runner(); public final Executor executor=new Executor();public boolean runnerEnabled=true;
 public static AltoClef getInstance(){return INSTANCE;} public Object getWorld(){return world;}
 public Chain getUserTaskChain(){return chain;} public Runner getTaskRunner(){return runner;} public Executor getCommandExecutor(){return executor;}
 public Chain getMobDefenseChain(){return defense;}
 public Player getPlayer(){return player;} public boolean isPaused(){return false;}
 public void runUserTask(Object task){chain.setTask(task);} public void cancelUserTask(){runnerEnabled=false;chain.setTask(null);}
 public static class Chain { public Object task; public boolean isActive(){return task!=null;} public Object getCurrentTask(){return task;} public void setTask(Object t){task=t;} public String getName(){return "UserTaskChain";} public float getPriority(){return 50;} }
 public static class Runner {private final java.util.Map<String,Float> lastPriority=java.util.Map.of();private Object cachedCurrentTaskChain;public Chain getCurrentTaskChain(){return INSTANCE.chain;}public boolean isActive(){return INSTANCE.runnerEnabled;}public void disable(){if(INSTANCE.defense.task instanceof Task t && t.failStop)throw new IllegalStateException("stale native teardown");INSTANCE.runnerEnabled=false;INSTANCE.chain.setTask(null);INSTANCE.defense.setTask(null);} }
 public static class World {public java.util.List<Player> method_18456(){return java.util.List.of(INSTANCE.player,new Player("Alex",true),new Player("Steve",false));}}
 public static class Task {public boolean done,stopped,failStop;public boolean isFinished(){return done;}public boolean stopped(){return stopped;}}
 public static class Executor {public int calls; public void executeWithPrefix(String command){calls++; INSTANCE.chain.setTask(new Task());}}
 public static class Player {public final String name;public final boolean alive;public Player(){this("Saruman",true);}public Player(String n,boolean a){name=n;alive=a;}public boolean method_5805(){return alive;}public Name method_5477(){return new Name(name);}}
 public record Name(String value) {public String getString(){return value;}}
}'''
TREE = r'''
package lab.treegrow;
import adris.altoclef.AltoClef;
public class TreeGrowClient {
 public static Grow current; public static int cancels;
 public static Grow current(){return current;} public static int start(){current=new Grow();AltoClef.INSTANCE.chain.setTask(current);return 1;}
 public static void publish(){} public static void cancel(String reason){cancels++;}
 public static class Grow {
  public Object species; public boolean terminal=false,success=false;public String result="",outcome="running",interruption="";
  public boolean isFinished(){return terminal;}public boolean success(){return success;}public String result(){return result;}
  public boolean planted(){return false;}public int boneUses(){return 0;}public int plantSends(){return 0;}public boolean expired(){return false;}
  public String outcome(){return outcome;}public String reason(){return "";}public String interruption(){return interruption;}
  public int sites(){return 0;}public int uses(){return 0;}public int giveSends(){return 0;}public long remainingNanos(){return 100;}
  public Object site(){return null;}public Object sapling(){return null;}public int count(Object ignored){return 1;}
  public void finish(String reason,boolean grown){terminal=true;success=grown;result=reason;cancels++;}
 }
}'''
MODERN = r'''
package dev.autodefense;
public class AutoDefenseClient {
 public static final Combat RUNTIME=new Combat();public static int publications,nativeResets;
 public static void resetNativeDefense(){nativeResets++;adris.altoclef.AltoClef.INSTANCE.defense.setTask(null);}
 public static Combat combat(){return RUNTIME;}public static void publishSnapshot(Object client){publications++;}
 public static class Combat {
  public boolean enabled=false,busy=false,manual=false,reset=false;public int changes;
  public boolean isEnabled(){return enabled;}public boolean ownsControls(){return busy;}public String action(){return reset?"RESET":busy?"COMBAT":"IDLE";}
  public boolean manualStop(){return manual;}public void enable(Object client){enabled=true;manual=false;changes++;}
  public void disable(Object client){enabled=false;changes++;}
 }
}'''
MODERN_API = r'''
package dev.autodefense.api;
public class AutoDefenseApi {public static final AutoDefenseApi INSTANCE=new AutoDefenseApi();
 public static AutoDefenseApi get(){return INSTANCE;}public String state(){return "{\"ok\":true,\"needs\":[]}";}}
'''
LEGACY = r'''
package dev.autodefense.client;
public class AutoDefenseClient {
 public static final Runtime RUNTIME=new Runtime();public static Runtime runtime(){return RUNTIME;}
 public static class Runtime {public final Handover handover=new Handover();public final Bridge bridge=new Bridge();
  public boolean enabled=false;public int changes;public boolean enabled(){return enabled;}
  public void toggle(Object client){enabled=!enabled;changes++;}}
 public static class Handover {public String state="IDLE",savedCommand="@old";public String state(){return state;}}
 public static class Bridge {public String lastLine="@old";public boolean cancelledSinceCapture=false;}
}'''
LEGACY_API = r'''
package dev.autodefense.api;
public class AutoDefenseApi {public static String getStatusJson(){return "{\"owner\":\"NONE\",\"missingItems\":[]}";}}
'''
HARNESS = r'''
import java.util.*;
import java.lang.reflect.*;
import org.saruman.bridge.ModGateway;
import adris.altoclef.AltoClef;
import lab.treegrow.TreeGrowClient;
public class Harness {
 static final Object client=new Harness();static String world;static boolean modern;
 static void check(boolean pass,String message){if(!pass)throw new AssertionError(message);}
 static Map<String,Object> apply(String op,Map<String,Object> args)throws Exception{return new ModGateway(client,op,args).get();}
 static Map<String,Object> snapshot()throws Exception{return apply("snapshot",Map.of());}
 public static void main(String[] args)throws Exception {
  modern=Boolean.parseBoolean(args[0]);
  Class<?> dc=Class.forName(modern?"dev.autodefense.AutoDefenseClient":"dev.autodefense.client.AutoDefenseClient");
  Object runtime=dc.getMethod(modern?"combat":"runtime").invoke(null);
  // Production hashes must reject our test doubles. Only this isolated harness
  // seeds the private cache, so the real operation paths can be exercised next.
  boolean rejected=false;try{new ModGateway(client,"snapshot",Map.of());}catch(Exception expected){rejected=true;}
  check(rejected,"unknown artifacts accepted");
  Field cache=ModGateway.class.getDeclaredField("verified");cache.setAccessible(true);
  Set<Class<?>> verified=(Set<Class<?>>)cache.get(null);verified.addAll(List.of(AltoClef.class,dc,TreeGrowClient.class));
  Map<String,Object> first=snapshot();check("ready".equals(first.get("status")),first.toString());
  check((modern?"addon-0.1":"legacy-1.0").equals(first.get("defense_api")),"wrong API");
  check(Boolean.FALSE.equals(first.get("defense_enabled")),"snapshot changed enabled state");
  check(Boolean.TRUE.equals(first.get("player_alive")),"alive state missing");
  check(List.of("Alex").equals(first.get("players")),"follow candidates include self or dead players");
  world=(String)first.get("world");
  apply("defense",Map.of("enabled",true));apply("defense",Map.of("enabled",true));apply("defense",Map.of("enabled",false));
  check(runtime.getClass().getField("changes").getInt(runtime)==2,"non-idempotent enable/disable");
  Object handover=null,bridge=null;
  if(modern)runtime.getClass().getField("busy").setBoolean(runtime,true);
  else {handover=runtime.getClass().getField("handover").get(runtime);handover.getClass().getField("state").set(handover,"ACTIVE");}
  check(Boolean.TRUE.equals(snapshot().get("defense_busy")),"lost body ownership");
  check("busy".equals(apply("command",Map.of("world",world,"request_id","busy","command","@follow Alex")).get("status")),"command ran during defense");
  check(AltoClef.INSTANCE.executor.calls==0,"busy command executed");
  if(modern){runtime.getClass().getField("busy").setBoolean(runtime,false);runtime.getClass().getField("manual").setBoolean(runtime,true);}
  else {handover.getClass().getField("state").set(handover,"IDLE");bridge=runtime.getClass().getField("bridge").get(runtime);bridge.getClass().getField("cancelledSinceCapture").setBoolean(bridge,true);}
  check(Boolean.TRUE.equals(snapshot().get("manual_stop")),"manual stop not observed");
  apply("maintain",Map.of("world",world,"ack_manual_stop",true));
  Map<String,Object> ack=snapshot();check(Boolean.FALSE.equals(ack.get("manual_stop")),"stop acknowledgement repeated");
  if(modern){check(Boolean.TRUE.equals(ack.get("manual_stop_latched")),"ack erased native stop latch");runtime.getClass().getField("manual").setBoolean(runtime,false);}
  else {check(bridge.getClass().getField("lastLine").get(bridge)==null,"legacy replay retained");check(handover.getClass().getField("savedCommand").get(handover)==null,"handover replay retained");}
  check("rejected".equals(apply("command",Map.of("world","old-world","request_id","stale","command","@follow Alex")).get("status")),"stale world accepted");
  Map<String,Object> command=Map.of("world",world,"request_id","once","command","@follow Alex");
  check("sent".equals(apply("command",command).get("status")),"command not sent");
  check("sent".equals(apply("command",command).get("status")),"receipt not recovered");
  check(AltoClef.INSTANCE.executor.calls==1,"command replayed twice");
  apply("stop_command",Map.of("world",world,"run_id","once"));
  check(AltoClef.INSTANCE.runnerEnabled,"personal activity cancellation disabled the whole runner");
  check(AltoClef.INSTANCE.chain.task==null,"owned follow kept running after player interruption");
  apply("command",Map.of("world",world,"request_id","finished-command","command","@follow Alex"));
  AltoClef.Task finished=(AltoClef.Task)AltoClef.INSTANCE.chain.task;finished.done=true;
  apply("maintain",Map.of("world",world));
  check(AltoClef.INSTANCE.chain.task==null,"completed owned command blocked queue");
  check(Boolean.TRUE.equals(((Map<?,?>)snapshot().get("command")).get("finished")),"terminal command evidence lost");
  apply("command",Map.of("world",world,"request_id","failed-command","command","@follow Alex"));
  ((AltoClef.Task)AltoClef.INSTANCE.chain.task).stopped=true;
  apply("maintain",Map.of("world",world));
  check(AltoClef.INSTANCE.chain.task==null,"stopped command blocked later work");
  Map<?,?> failure=(Map<?,?>)snapshot().get("command");
  check(Boolean.TRUE.equals(failure.get("stopped")) && Boolean.FALSE.equals(failure.get("finished")),"stop confused with success");
  Object foreign=new Object();AltoClef.INSTANCE.chain.setTask(foreign);
  apply("maintain",Map.of("world",world));check(AltoClef.INSTANCE.chain.task==foreign,"foreign task cleared");
  AltoClef.INSTANCE.chain.setTask(null);
  check("started".equals(apply("tree_start",Map.of("world",world,"request_id","tree-one")).get("status")),"tree did not start");
  apply("cancel",Map.of("world",world,"run_id","foreign"));check(TreeGrowClient.cancels==0,"foreign task cancelled");
  apply("cancel",Map.of("world",world,"run_id","tree-one"));check(TreeGrowClient.cancels==1,"owned tree not cancelled");
  check(AltoClef.INSTANCE.runnerEnabled,"tree preemption disabled the global runner");
  check(AltoClef.INSTANCE.chain.task==null,"owned task retained");
  apply("tree_start",Map.of("world",world,"request_id","terminal-tree"));
  TreeGrowClient.current.terminal=true;TreeGrowClient.current.success=false;TreeGrowClient.current.result="no sapling";
  apply("maintain",Map.of("world",world));
  check(AltoClef.INSTANCE.chain.task==null,"failed owned tree blocked queue");
  check("no sapling".equals(((Map<?,?>)snapshot().get("tree")).get("result")),"failure evidence lost");
  if(modern){
   runtime.getClass().getField("manual").setBoolean(runtime,true);
   runtime.getClass().getField("reset").setBoolean(runtime,true);
   check("reset".equals(snapshot().get("stop_kind")),"death confused with manual stop");
   runtime.getClass().getField("reset").setBoolean(runtime,false);
   check("manual".equals(snapshot().get("stop_kind")),"explicit stop lost");
  }
  AltoClef.INSTANCE.defense.setTask(new AltoClef.Task());
  if(modern)((AltoClef.Task)AltoClef.INSTANCE.defense.task).failStop=true;
  check(snapshot().get("native_defense_task")!=null,"native residual task hidden");
  check("stale_world".equals(apply("halt",Map.of("world","stale")).get("reason")),"old Stop accepted in new world");
  check(AltoClef.INSTANCE.runnerEnabled,"old Stop disabled runner");
  int changesBefore=runtime.getClass().getField("changes").getInt(runtime);
  check("stale_world".equals(apply("defense",Map.of("world","stale","enabled",true)).get("reason")),"stale defense enable changed new world");
  check(runtime.getClass().getField("changes").getInt(runtime)==changesBefore,"stale defense mutation");
  check("halted".equals(apply("halt",Map.of("world",world)).get("status")),"Stop rejected");
  check(!AltoClef.INSTANCE.runnerEnabled && AltoClef.INSTANCE.defense.task==null,"native task survived Stop");
  if(modern)check(dc.getField("nativeResets").getInt(null)==1,"native lifecycle reset omitted");
  check("halted".equals(apply("halt",Map.of("world",world)).get("status")),"Stop is not idempotent");
  System.out.println(modern?"MODERN PASS":"LEGACY PASS");
 }
}'''


class JavaContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK required')
    def test_resume_and_sapling_reacquisition_keep_task_and_budget(self):
        with tempfile.TemporaryDirectory(prefix='saruman-tree-resume-') as directory:
            folder=Path(directory)
            sources=[str(ROOT/'resources/Minecraft/bridge/ModGateway.java')]
            sources.extend(str(p) for p in (ROOT/'tests/native_gateway').rglob('*.java'))
            built=subprocess.run([shutil.which('javac'),'--release','21','-encoding','UTF-8','-d',str(folder),*sources],
                capture_output=True,text=True,encoding='utf-8',timeout=40)
            self.assertEqual(built.returncode,0,built.stderr)
            result=subprocess.run([shutil.which('java'),'-cp',str(folder),'GatewayContractTest'],
                capture_output=True,text=True,encoding='utf-8',timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS',result.stdout)

    @unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "JDK required")
    def test_real_shim_against_both_isolated_contracts(self):
        for modern in (True, False):
            with self.subTest(modern=modern), tempfile.TemporaryDirectory(prefix="saruman-java-contract-") as directory:
                folder=Path(directory)
                files={"adris/altoclef/AltoClef.java":ALTO,"lab/treegrow/TreeGrowClient.java":TREE,
                       "dev/autodefense/api/AutoDefenseApi.java":MODERN_API if modern else LEGACY_API,
                       ("dev/autodefense/AutoDefenseClient.java" if modern else "dev/autodefense/client/AutoDefenseClient.java"):MODERN if modern else LEGACY,
                       "Harness.java":HARNESS}
                sources=[str(ROOT/"resources/Minecraft/bridge/ModGateway.java")]
                for name,body in files.items():
                    path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body,encoding="utf-8");sources.append(str(path))
                built=subprocess.run([shutil.which("javac"),"--release","21","-encoding","UTF-8","-d",str(folder),*sources],capture_output=True,text=True,timeout=40)
                self.assertEqual(built.returncode,0,built.stderr)
                result=subprocess.run([shutil.which("java"),"-cp",str(folder),"Harness",str(modern).lower()],capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn("MODERN PASS" if modern else "LEGACY PASS",result.stdout)
