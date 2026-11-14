package org.saruman.bridge;

import java.lang.reflect.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.function.Supplier;

/** Pinned compatibility boundary, invoked ONLY on Minecraft's client executor. */
public final class ModGateway implements Supplier<Map<String,Object>> {
    private final Object client;
    private final String op;
    private final Map<String,Object> args;
    private final Class<?> defenseClient;
    private final boolean addonApi;
    private static Object world, owned;
    private static Object commandTask;
    private static String commandRun="";
    private static String session = "", run = "";
    private static boolean stopAcknowledged;
    private static final LinkedHashMap<String,Map<String,Object>> receipts = new LinkedHashMap<>();
    private static final Set<Class<?>> verified = new HashSet<>();

    public ModGateway(Object client, String op, Map<String,Object> args) throws Exception {
        this.client = client; this.op = op; this.args = args;
        // Constructor runs on the Py4J thread: no artifact IO on the client tick.
        verify(type("adris.altoclef.AltoClef"),"76bbb677d2f8294e6d2de98b554f81818ae15605ef8a82ff7e1d3b9b82555d1b");
        Class<?> dc;
        boolean modern;
        try { dc=type("dev.autodefense.client.AutoDefenseClient"); modern=false; }
        catch (ClassNotFoundException absent) { dc=type("dev.autodefense.AutoDefenseClient"); modern=true; }
        defenseClient=dc; addonApi=modern;
        if (modern) verify(dc,"5e773d1e64f651943b5faf709aea3ef1458503a2a25910acd476ab6a457e25cb",
                              "6943228ddd690a24b5aad2e98bccb36e798c15b96c0a57a419979e42eeabe4ce",
                              "15af6beddd33e4388812a4502f629fdf08d73dd711017c3950612658bb094385",
                              "bdc144486e720b9a31a58544883593c7f0412e9a8da8262c6bf34f618d38f736",
                              "3ab5e109545c52a6a5592b414e16299211f387c138c811a563c0336ccd8dd16a");
        else verify(dc,"e19d47caa2ae553ab81b5b84ab4fc11179595809797cee7d122cad5f2b1db43c");
        verify(type("lab.treegrow.TreeGrowClient"),"edd7a699318d0b18f46abe67db25046502e20ca400f4751c0ac0679664e5bee9",
                                                "ed50500c43293f614c632b65f9c31c3a116a35c7759f16d547c392c3ae5708bf",
                                                "f41e3b8710d15d391b07e35c0707d23fe7d9b634fdf47cf7f5cd246fda52c074");
    }
    private Class<?> type(String name) throws Exception {
        return Class.forName(name, true, client.getClass().getClassLoader());
    }
    private static Object call(Object object, String name, Object... args) throws Exception {
        Class<?> type = object instanceof Class<?> c ? c : object.getClass();
        List<Method> methods=new ArrayList<>(Arrays.asList(type.getMethods()));
        methods.addAll(Arrays.asList(type.getDeclaredMethods()));
        for (Method m : methods) {
            if (!m.getName().equals(name) || m.getParameterCount() != args.length) continue;
            boolean match = true;
            for (int i=0; i<args.length; i++) {
                Class<?> p = m.getParameterTypes()[i];
                if (args[i] != null && !p.isInstance(args[i]) && !(p == boolean.class && args[i] instanceof Boolean)) match=false;
            }
            if (match) { m.setAccessible(true); return m.invoke(object instanceof Class<?> ? null : object, args); }
        }
        throw new NoSuchMethodException(type.getName()+"#"+name);
    }
    private static Object field(Object o, String name) throws Exception {
        Field f=o.getClass().getDeclaredField(name); f.setAccessible(true); return f.get(o);
    }
    private static void put(Object o, String name, Object value) throws Exception {
        Field f=o.getClass().getDeclaredField(name); f.setAccessible(true); f.set(o,value);
    }
    private static synchronized void verify(Class<?> c, String... hashes) throws Exception {
        if (verified.contains(c)) return;
        Path jar=Paths.get(c.getProtectionDomain().getCodeSource().getLocation().toURI());
        // Fabric may execute a remapped cache JAR. Pin the installed container's
        // origin, not a cache whose bytes can differ after runtime remapping.
        try {
            Class<?> loader=Class.forName("net.fabricmc.loader.api.FabricLoader",true,c.getClassLoader());
            String id=c.getName().startsWith("adris.")?"altoclef":c.getName().startsWith("lab.")?"treegrow":c.getName().startsWith("dev.autodefense.client.")?"auto-defense":"auto_defense";
            Optional<?> container=(Optional<?>)call(call(loader,"getInstance"),"getModContainer",id);
            if (container.isPresent()) {
                Collection<?> paths=(Collection<?>)call(call(container.get(),"getOrigin"),"getPaths");
                if (paths.size()==1) jar=(Path)paths.iterator().next();
            }
        } catch (ReflectiveOperationException ignored) { /* code-source fallback */ }
        String actual=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(jar)));
        if (!Arrays.asList(hashes).contains(actual)) throw new IllegalStateException("Unsupported artifact: "+jar.getFileName());
        verified.add(c);
    }
    private static Map<String,Object> result(String status, String reason) {
        Map<String,Object> r=new LinkedHashMap<>(); r.put("status",status); r.put("reason",reason); return r;
    }
    private Map<String,Object> tree() throws Exception {
        Map<String,Object> r=new LinkedHashMap<>(); r.put("run_id",run);
        if (owned == null) return r;
        for (String name : List.of("isFinished","success","result","planted","boneUses","plantSends","expired","outcome","reason","interruption","sites","uses","giveSends","remainingNanos")) r.put(name,call(owned,name));
        r.put("stage",owned.toString());
        Object p=call(owned,"site"); r.put("site",p==null?null:p.toString());
        r.put("resumable",resumableTree());
        return r;
    }
    private Map<String,Object> scheduler(Object mod,Object userChain) throws Exception {
        Object runner=call(mod,"getTaskRunner");
        Object selected=call(runner,"getCurrentTaskChain");
        String name=selected==null?"":String.valueOf(call(selected,"getName"));
        // Use the runner's last selection, not getPriority() on foreign chains:
        // priority evaluation itself can run stuck detection or survival logic.
        Map<?,?> priorities=(Map<?,?>)field(runner,"lastPriority");
        Object cached=priorities.get(name);
        double threshold=((Number)call(userChain,"getPriority")).doubleValue();
        Double priority=cached instanceof Number n?n.doubleValue():null;
        boolean foreignActive=selected!=null && selected!=userChain
            && Boolean.TRUE.equals(call(runner,"isActive")) && Boolean.TRUE.equals(call(selected,"isActive"));
        boolean blocked=foreignActive && (priority==null || Double.isNaN(priority) || priority>threshold);
        Map<String,Object> value=new LinkedHashMap<>();
        value.put("busy",blocked);value.put("owner",name);
        value.put("priority",priority==null?null:Double.isFinite(priority)?priority:priority.toString());
        value.put("user_priority",Double.isFinite(threshold)?threshold:Double.toString(threshold));
        return value;
    }
    // Only scheduler cancellation is resumable. Manual stop/death/world/limits are not.
    private boolean resumableTree() throws Exception {
        return owned != null && Boolean.TRUE.equals(call(owned,"isFinished"))
            && !Boolean.TRUE.equals(call(owned,"success")) && !Boolean.TRUE.equals(call(owned,"expired"))
            && "cancelled".equals(call(owned,"outcome"))
            && String.valueOf(call(owned,"result")).startsWith("Отменено: задача UnionClef прервана")
            && !String.valueOf(call(owned,"interruption")).isBlank();
    }
    private static void rearmAcquisition() throws Exception {
        Object acquisition=field(owned,"acquisition");
        // GrowTreeTask reads child.stopped() BEFORE returning that child for its
        // next tick. Task.stop() sets stopped=true even for scheduler preemption;
        // leaving it set would report an exhausted search before it can resume.
        // Public Task.reset() restores only first/active/stopped. Keep the SAME
        // acquisition, search, origin, started/acquiredAt and GrowthBudget.
        if (Boolean.TRUE.equals(call(acquisition,"stopped"))) call(acquisition,"reset");
    }
    @Override public Map<String,Object> get() {
        try {
            Class<?> alto=type("adris.altoclef.AltoClef");
            verify(alto,"76bbb677d2f8294e6d2de98b554f81818ae15605ef8a82ff7e1d3b9b82555d1b");
            Object mod=call(alto,"getInstance");
            Object now=call(mod,"getWorld");
            if (now != world) { world=now; session=UUID.randomUUID().toString(); owned=null; run=""; commandTask=null; commandRun=""; receipts.clear(); stopAcknowledged=false; }
            Class<?> dc=defenseClient;
            Class<?> tc=type("lab.treegrow.TreeGrowClient");
            verify(tc,"edd7a699318d0b18f46abe67db25046502e20ca400f4751c0ac0679664e5bee9",
                      "ed50500c43293f614c632b65f9c31c3a116a35c7759f16d547c392c3ae5708bf",
                      "f41e3b8710d15d391b07e35c0707d23fe7d9b634fdf47cf7f5cd246fda52c074");
            Object runtime=call(dc,addonApi?"combat":"runtime");
            Object handover=addonApi?null:field(runtime,"handover");
            boolean busy=addonApi
                ? Boolean.TRUE.equals(call(runtime,"ownsControls")) || Set.of("COMBAT","RETREAT","RESCUE","EAT").contains(String.valueOf(call(runtime,"action")))
                : !Set.of("IDLE","OFF").contains(String.valueOf(call(handover,"state")));
            Object chain=call(mod,"getUserTaskChain");
            boolean nativeBusy=Boolean.TRUE.equals(call(chain,"isActive"));
            Map<String,Object> scheduler=scheduler(mod,chain);
            boolean schedulerBusy=Boolean.TRUE.equals(scheduler.get("busy"));
            boolean enabled=Boolean.TRUE.equals(call(runtime,addonApi?"isEnabled":"enabled"));
            boolean manualStop=addonApi?Boolean.TRUE.equals(call(runtime,"manualStop")):Boolean.TRUE.equals(field(field(runtime,"bridge"),"cancelledSinceCapture"));
            if (!manualStop) stopAcknowledged=false;
            if ("snapshot".equals(op)) {
                Map<String,Object> r=result("ready",null);
                r.put("world",session); r.put("in_world",world!=null);
                Object player=call(mod,"getPlayer");
                r.put("player_alive",player!=null && Boolean.TRUE.equals(call(player,"method_5805")));
                List<String> players=new ArrayList<>();
                if (world!=null) for (Object other:(List<?>)call(world,"method_18456")) {
                    if (other!=player && Boolean.TRUE.equals(call(other,"method_5805")))
                        players.add(String.valueOf(call(call(other,"method_5477"),"getString")));
                }
                r.put("players",players);
                Class<?> api=type("dev.autodefense.api.AutoDefenseApi");
                r.put("defense_json",addonApi?call(call(api,"get"),"state"):call(api,"getStatusJson"));
                r.put("defense_api",addonApi?"addon-0.1":"legacy-1.0");
                r.put("defense_busy",busy); r.put("defense_enabled",enabled);
                r.put("native_busy",nativeBusy); r.put("tree",tree());
                r.put("scheduler_busy",schedulerBusy);r.put("scheduler_owner",scheduler.get("owner"));
                r.put("scheduler",scheduler);
                Object nativeDefense=call(mod,"getMobDefenseChain");
                Object nativeDefenseTask=call(nativeDefense,"getCurrentTask");
                r.put("native_defense_task",nativeDefenseTask==null?null:nativeDefenseTask.toString());
                r.put("runner_active",call(call(mod,"getTaskRunner"),"isActive"));
                r.put("manual_stop",manualStop && !stopAcknowledged);
                r.put("manual_stop_latched",addonApi && manualStop);
                r.put("stop_kind",manualStop ? addonApi && "RESET".equals(String.valueOf(call(runtime,"action"))) ? "reset" : "manual" : "none");
                Map<String,Object> command=new LinkedHashMap<>();
                command.put("run_id",commandRun);
                command.put("current",commandTask!=null && call(chain,"getCurrentTask")==commandTask);
                command.put("finished",commandTask!=null && Boolean.TRUE.equals(call(commandTask,"isFinished")));
                command.put("stopped",commandTask!=null && Boolean.TRUE.equals(call(commandTask,"stopped")));
                r.put("command",command);
                return r;
            }
            if ("defense".equals(op)) {
                if (args.containsKey("world") && !session.equals(args.get("world"))) return result("rejected","stale_world");
                if (enabled != Boolean.TRUE.equals(args.get("enabled"))) {
                    if (addonApi) {
                        call(runtime,Boolean.TRUE.equals(args.get("enabled"))?"enable":"disable",client);
                        call(dc,"publishSnapshot",client);
                    } else call(runtime,"toggle",client);
                }
                return result("applied",null);
            }
            if (!session.equals(args.get("world"))) return result("rejected","stale_world");
            if ("halt".equals(op)) {
                // Explicit operator Stop owns the whole automation session, unlike
                // preemption of one owned goal. Turning off just the addon leaves
                // UnionClef's independent Mob Defense running.
                if (enabled) {
                    if (addonApi) call(runtime,"disable",client);
                    else call(runtime,"toggle",client);
                }
                Object runner=call(mod,"getTaskRunner");
                try {
                    // Detach native defense first: a stale task's teardown can
                    // throw after world unload, before runner.disable flips active.
                    if (addonApi) {
                        try { call(dc,"resetNativeDefense"); }
                        catch (NoSuchMethodException historicalArtifact) { /* Older pinned JARs lack the lifecycle hook. */ }
                    }
                    call(runner,"disable");
                    if (addonApi) call(dc,"publishSnapshot",client);
                } finally {
                    put(runner,"cachedCurrentTaskChain",null);
                }
                return result("halted",null);
            }
            // This coordinator owns replay. Native defense must never replay a stale @command.
            if (!addonApi) {
                put(field(runtime,"bridge"),"lastLine",null);
                put(handover,"savedCommand",null);
            }
            if ("maintain".equals(op)) {
                if (Boolean.TRUE.equals(args.get("ack_manual_stop"))) {
                    if (addonApi) stopAcknowledged=true;
                    else put(field(runtime,"bridge"),"cancelledSinceCapture",false);
                }
                // Pinned TreeGrow 0.1.0 forgets to reacquire a consumed sapling after
                // abandoning a site. Re-enter its OWN acquisition branch, retaining
                // search position, deadline and GrowthBudget. No Python planting AI.
                if (!busy && !schedulerBusy && owned != null && call(chain,"getCurrentTask") == owned
                    && !Boolean.TRUE.equals(call(owned,"isFinished")) && call(owned,"site") == null
                    && field(owned,"species") != null && Integer.valueOf(0).equals(call(owned,"count",call(owned,"sapling")))) {
                    rearmAcquisition();
                    put(owned,"species",null); put(owned,"acquiring",true);
                }
                // UnionClef may retain a terminal root. Release only the exact
                // object we own, keeping its receipt/result for reconciliation.
                if (!busy && owned!=null && call(chain,"getCurrentTask")==owned
                    && Boolean.TRUE.equals(call(owned,"isFinished")))
                    call(chain,"setTask",new Object[]{null});
                if (!busy && commandTask!=null && call(chain,"getCurrentTask")==commandTask
                    && (Boolean.TRUE.equals(call(commandTask,"isFinished")) || Boolean.TRUE.equals(call(commandTask,"stopped"))))
                    call(chain,"setTask",new Object[]{null});
                return result("applied",null);
            }
            if (busy) return result("busy","native_defense");
            if (schedulerBusy) return result("busy","native_scheduler");
            if ("cancel".equals(op)) {
                if (owned != null && run.equals(args.get("run_id"))) {
                    // TreeGrowClient.cancel also disables the global runner.
                    // End only this run, then detach only its own root.
                    if (!Boolean.TRUE.equals(call(owned,"isFinished"))
                        && (call(chain,"getCurrentTask")==owned || !nativeBusy))
                        call(owned,"finish","Отменено: приоритет нового поручения",false);
                    if (call(chain,"getCurrentTask") == owned) call(chain,"setTask",new Object[]{null});
                    call(tc,"publish");
                }
                return result("cancelled",null);
            }
            if ("stop_command".equals(op)) {
                if (!commandRun.equals(args.get("run_id"))) return result("rejected","unknown_command_run");
                if (commandTask != null && call(chain,"getCurrentTask") == commandTask) {
                    // cancelUserTask also disables the entire TaskRunner. That
                    // would look like a human Stop to Auto Defense and latch all
                    // later work. setTask stops only our exact root and children.
                    call(chain,"setTask",new Object[]{null});
                }
                return result("cancelled",null);
            }
            Object player=call(mod,"getPlayer");
            if (player == null || !Boolean.TRUE.equals(call(player,"method_5805"))) return result("rejected","player_not_alive");
            String request=String.valueOf(args.get("request_id"));
            if (receipts.containsKey(request)) return receipts.get(request);
            if ("tree_resume".equals(op) && (!run.equals(args.get("run_id")) || !resumableTree()))
                return result("rejected","resume_unsupported");
            if ("tree_resume".equals(op) && nativeBusy && owned != null && call(chain,"getCurrentTask") == owned
                && run.equals(args.get("run_id")) && resumableTree()) {
                // This chain can retain a terminal root after cancel(). Clear only
                // our exact interrupted object; never somebody else's task.
                call(chain,"setTask",new Object[]{null}); nativeBusy=false;
            }
            if (nativeBusy || Boolean.TRUE.equals(call(mod,"isPaused"))) return result("busy","unionclef");
            Map<String,Object> receipt;
            if ("tree_start".equals(op)) {
                if (!Integer.valueOf(1).equals(call(tc,"start"))) return result("busy","tree_start_rejected");
                owned=call(tc,"current"); run=request;
                receipt=result("started",null); receipt.put("run_id",run);
            } else if ("tree_resume".equals(op)) {
                if (!run.equals(args.get("run_id")) || !resumableTree()) return result("rejected","resume_unsupported");
                if (field(owned,"species")==null) rearmAcquisition();
                // Same task, original site/search/budget/start time; never a new tree or allowance.
                put(owned,"terminal",false); put(owned,"success",false); put(owned,"result","");
                put(owned,"outcome","running"); put(owned,"interruption","");
                call(mod,"runUserTask",owned); call(tc,"publish"); receipt=result("resumed",null);
            } else if ("command".equals(op)) {
                String command=String.valueOf(args.get("command"));
                if (!command.startsWith("@") || command.contains("\n") || command.contains("\r") || command.contains(";")) return result("rejected","invalid_command");
                // execute(String) silently ignores a missing configured prefix in
                // UnionClef 0.95.61. Its native helper adds the actual prefix.
                call(call(mod,"getCommandExecutor"),"executeWithPrefix",command.substring(1));
                commandTask=call(chain,"getCurrentTask"); commandRun=request;
                receipt=result("sent",null);
            } else return result("rejected","unknown_operation");
            receipts.put(request,receipt);
            while(receipts.size()>256) receipts.remove(receipts.keySet().iterator().next());
            return receipt;
        } catch (Throwable e) {
            Throwable cause=e instanceof InvocationTargetException i ? i.getCause() : e;
            return result("error",cause.getClass().getSimpleName()+": "+cause.getMessage());
        }
    }
}
