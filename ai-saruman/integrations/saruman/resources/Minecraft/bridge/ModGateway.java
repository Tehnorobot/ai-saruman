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
    private static Object world, owned;
    private static Object commandTask;
    private static String commandRun="";
    private static String session = "", run = "";
    private static final LinkedHashMap<String,Map<String,Object>> receipts = new LinkedHashMap<>();
    private static final Set<Class<?>> verified = new HashSet<>();

    public ModGateway(Object client, String op, Map<String,Object> args) throws Exception {
        this.client = client; this.op = op; this.args = args;
        // Constructor runs on the Py4J thread: no artifact IO on the client tick.
        verify(type("adris.altoclef.AltoClef"),"76bbb677d2f8294e6d2de98b554f81818ae15605ef8a82ff7e1d3b9b82555d1b");
        verify(type("dev.autodefense.client.AutoDefenseClient"),"e19d47caa2ae553ab81b5b84ab4fc11179595809797cee7d122cad5f2b1db43c");
        verify(type("lab.treegrow.TreeGrowClient"),"edd7a699318d0b18f46abe67db25046502e20ca400f4751c0ac0679664e5bee9");
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
    private static synchronized void verify(Class<?> c, String hash) throws Exception {
        if (verified.contains(c)) return;
        Path jar=Paths.get(c.getProtectionDomain().getCodeSource().getLocation().toURI());
        // Fabric may execute a remapped cache JAR. Pin the installed container's
        // origin, not a cache whose bytes can differ after runtime remapping.
        try {
            Class<?> loader=Class.forName("net.fabricmc.loader.api.FabricLoader",true,c.getClassLoader());
            String id=c.getName().startsWith("adris.")?"altoclef":c.getName().startsWith("lab.")?"treegrow":"auto-defense";
            Optional<?> container=(Optional<?>)call(call(loader,"getInstance"),"getModContainer",id);
            if (container.isPresent()) {
                Collection<?> paths=(Collection<?>)call(call(container.get(),"getOrigin"),"getPaths");
                if (paths.size()==1) jar=(Path)paths.iterator().next();
            }
        } catch (ReflectiveOperationException ignored) { /* code-source fallback */ }
        String actual=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(jar)));
        if (!hash.equals(actual)) throw new IllegalStateException("Unsupported artifact: "+jar.getFileName());
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
        return r;
    }
    // Only scheduler cancellation is resumable. Manual stop/death/world/limits are not.
    private boolean resumableTree() throws Exception {
        return owned != null && Boolean.TRUE.equals(call(owned,"isFinished"))
            && !Boolean.TRUE.equals(call(owned,"success")) && !Boolean.TRUE.equals(call(owned,"expired"))
            && "cancelled".equals(call(owned,"outcome"))
            && String.valueOf(call(owned,"result")).startsWith("Отменено: задача UnionClef прервана")
            && !String.valueOf(call(owned,"interruption")).isBlank();
    }
    @Override public Map<String,Object> get() {
        try {
            Class<?> alto=type("adris.altoclef.AltoClef");
            verify(alto,"76bbb677d2f8294e6d2de98b554f81818ae15605ef8a82ff7e1d3b9b82555d1b");
            Object mod=call(alto,"getInstance");
            Object now=call(mod,"getWorld");
            if (now != world) { world=now; session=UUID.randomUUID().toString(); owned=null; run=""; commandTask=null; commandRun=""; receipts.clear(); }
            Class<?> dc=type("dev.autodefense.client.AutoDefenseClient");
            verify(dc,"e19d47caa2ae553ab81b5b84ab4fc11179595809797cee7d122cad5f2b1db43c");
            Class<?> tc=type("lab.treegrow.TreeGrowClient");
            verify(tc,"edd7a699318d0b18f46abe67db25046502e20ca400f4751c0ac0679664e5bee9");
            Object runtime=call(dc,"runtime");
            Object handover=field(runtime,"handover");
            String handoverState=String.valueOf(call(handover,"state"));
            boolean busy=!Set.of("IDLE","OFF").contains(handoverState);
            Object chain=call(mod,"getUserTaskChain");
            boolean nativeBusy=Boolean.TRUE.equals(call(chain,"isActive"));
            boolean enabled=Boolean.TRUE.equals(call(runtime,"enabled"));
            if ("snapshot".equals(op)) {
                Map<String,Object> r=result("ready",null);
                r.put("world",session); r.put("in_world",world!=null);
                r.put("defense_json",call(type("dev.autodefense.api.AutoDefenseApi"),"getStatusJson"));
                r.put("defense_busy",busy); r.put("defense_enabled",enabled);
                r.put("native_busy",nativeBusy); r.put("tree",tree());
                r.put("manual_stop",field(field(runtime,"bridge"),"cancelledSinceCapture"));
                return r;
            }
            if ("defense".equals(op)) {
                if (enabled != Boolean.TRUE.equals(args.get("enabled"))) call(runtime,"toggle",client);
                return result("applied",null);
            }
            if (!session.equals(args.get("world"))) return result("rejected","stale_world");
            // This coordinator owns replay. Native defense must never replay a stale @command.
            put(field(runtime,"bridge"),"lastLine",null);
            put(handover,"savedCommand",null);
            if ("maintain".equals(op)) {
                if (Boolean.TRUE.equals(args.get("ack_manual_stop"))) put(field(runtime,"bridge"),"cancelledSinceCapture",false);
                // Pinned TreeGrow 0.1.0 forgets to reacquire a consumed sapling after
                // abandoning a site. Re-enter its OWN acquisition branch, retaining
                // search position, deadline and GrowthBudget. No Python planting AI.
                if (!busy && owned != null && call(chain,"getCurrentTask") == owned
                    && !Boolean.TRUE.equals(call(owned,"isFinished")) && call(owned,"site") == null
                    && field(owned,"species") != null && Integer.valueOf(0).equals(call(owned,"count",call(owned,"sapling")))) {
                    put(owned,"species",null); put(owned,"acquiring",true);
                }
                return result("applied",null);
            }
            if (busy) return result("busy","native_defense");
            if ("cancel".equals(op)) {
                if (owned != null && run.equals(args.get("run_id"))) {
                    call(tc,"cancel","Cancelled by Saruman dispatcher");
                    if (call(chain,"getCurrentTask") == owned) call(chain,"setTask",new Object[]{null});
                }
                return result("cancelled",null);
            }
            if ("stop_command".equals(op)) {
                if (!commandRun.equals(args.get("run_id"))) return result("rejected","unknown_command_run");
                if (commandTask != null && call(chain,"getCurrentTask") == commandTask) {
                    call(mod,"cancelUserTask"); call(chain,"setTask",new Object[]{null});
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
