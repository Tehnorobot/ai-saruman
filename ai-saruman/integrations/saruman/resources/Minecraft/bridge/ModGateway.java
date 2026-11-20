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
    private final Class<?> treeClient;
    private final boolean addonApi;
    private static Object world, owned;
    private static Object commandTask;
    private static String commandRun="";
    private static String session = "", run = "";
    private static boolean stopAcknowledged;
    private static Object suppressedSettings;
    private static boolean previousMobDefense;
    private static final LinkedHashMap<String,Map<String,Object>> receipts = new LinkedHashMap<>();
    private static final Set<Class<?>> verified = new HashSet<>();

    public ModGateway(Object client, String op, Map<String,Object> args) throws Exception {
        this.client = client; this.op = op; this.args = args;
        // Constructor runs on the Py4J thread: no artifact IO on the client tick.
        verify(type("adris.altoclef.AltoClef"),"76bbb677d2f8294e6d2de98b554f81818ae15605ef8a82ff7e1d3b9b82555d1b");
        Class<?> dc;
        boolean modern;
        try { dc=type("dev.autodefense.client.AutoDefenseClient"); modern=false; }
        catch (ClassNotFoundException absent) { dc=optional("dev.autodefense.AutoDefenseClient"); modern=true; }
        addonApi=modern && dc != null;
        if (addonApi) dc=pinned(dc,"5e773d1e64f651943b5faf709aea3ef1458503a2a25910acd476ab6a457e25cb",
                              "6943228ddd690a24b5aad2e98bccb36e798c15b96c0a57a419979e42eeabe4ce",
                              "15af6beddd33e4388812a4502f629fdf08d73dd711017c3950612658bb094385",
                              "bdc144486e720b9a31a58544883593c7f0412e9a8da8262c6bf34f618d38f736",
                              "3ab5e109545c52a6a5592b414e16299211f387c138c811a563c0336ccd8dd16a");
        else dc=pinned(dc,"e19d47caa2ae553ab81b5b84ab4fc11179595809797cee7d122cad5f2b1db43c");
        defenseClient=dc;
        treeClient=pinned(optional("lab.treegrow.TreeGrowClient"),"edd7a699318d0b18f46abe67db25046502e20ca400f4751c0ac0679664e5bee9",
                                                "ed50500c43293f614c632b65f9c31c3a116a35c7759f16d547c392c3ae5708bf",
                                                "f41e3b8710d15d391b07e35c0707d23fe7d9b634fdf47cf7f5cd246fda52c074");
    }
    private Class<?> type(String name) throws Exception {
        return Class.forName(name, true, client.getClass().getClassLoader());
    }
    private Class<?> optional(String name) throws Exception {
        try { return type(name); }
        catch (ClassNotFoundException | LinkageError missing) { return null; }
    }
    private static Class<?> pinned(Class<?> type,String... hashes) {
        if (type == null) return null;
        try { verify(type, hashes); return type; }
        catch (Exception unsupported) { return null; }
    }
    private void setNativeSuppressed(Object mod, boolean suppress) throws Exception {
        if (!suppress && suppressedSettings==null) return;
        Object settings=call(mod,"getModSettings");
        if (suppressedSettings!=null && (!suppress || suppressedSettings!=settings)) {
            put(suppressedSettings,"mobDefense",previousMobDefense);
            suppressedSettings=null;
        }
        if (suppress && suppressedSettings==null) {
            previousMobDefense=Boolean.TRUE.equals(field(settings,"mobDefense"));
            put(settings,"mobDefense",false);
            suppressedSettings=settings;
            if (addonApi) {
                try { call(defenseClient,"resetNativeDefense"); }
                catch (NoSuchMethodException historicalArtifact) { /* No reset hook. */ }
            }
        }
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
    private static final Set<String> GROUND = Set.of("minecraft:grass_block", "minecraft:dirt",
            "minecraft:coarse_dirt", "minecraft:podzol", "minecraft:stone", "minecraft:sand",
            "minecraft:red_sand", "minecraft:gravel", "minecraft:snow_block", "minecraft:terracotta",
            "minecraft:dirt_path", "minecraft:rooted_dirt", "minecraft:mud", "minecraft:moss_block",
            "minecraft:sandstone", "minecraft:red_sandstone", "minecraft:clay", "minecraft:andesite",
            "minecraft:diorite", "minecraft:granite", "minecraft:deepslate", "minecraft:tuff",
            "minecraft:calcite", "minecraft:snow", "minecraft:ice", "minecraft:powder_snow",
            "minecraft:netherrack", "minecraft:end_stone");
    private static final Set<String> FALLING_GROUND = Set.of("minecraft:sand","minecraft:red_sand",
            "minecraft:gravel","minecraft:suspicious_sand","minecraft:suspicious_gravel");
    private static final int SURVEY_RADIUS=112, FOUNDATION_DEPTH=8, SURVEY_FINALISTS=128;
    private static final int[] SURVEY_HEIGHTS={0,-1,1,-2,2,-3,3,-4,4,-6,6,-8,8,-12,12,-16,16,-24,24,-32,32};

    private record Site(int x, int y, int z, int distance, int score) {}
    private record Cell(int x, int y, int z) {}
    private record Plot(int minX,int minZ,int maxX,int maxZ) {
        boolean overlaps(int x,int z,int width,int depth) {
            return minX<=x+width && maxX>=x-1 && minZ<=z+depth && maxZ>=z-1;
        }
    }
    private record Terrain(String id, boolean solid, boolean liquid, boolean plant, boolean protectedBlock) {
        boolean stable() {
            return solid && !liquid && !id.endsWith("_leaves") && !id.endsWith("_concrete_powder")
                    && !FALLING_GROUND.contains(id);
        }
        boolean air() {
            return id.equals("minecraft:air") || id.equals("minecraft:cave_air") || id.equals("minecraft:void_air");
        }
        boolean natural() {
            return GROUND.contains(id) || canopy() || id.endsWith("_terracotta") || id.endsWith("_ore")
                    || id.endsWith("_sapling") || id.equals("minecraft:vine") || id.equals("minecraft:lily_pad");
        }
        boolean canopy() {
            return id.endsWith("_leaves") || id.endsWith("_log") || id.endsWith("_stem");
        }
    }
    private record Work(int cut, int fill, int water) {
        static final Work NONE=new Work(0,0,0);
        Work add(Work other) {
            return new Work(cut+other.cut,fill+other.fill,water+other.water);
        }
        int cost() { return cut*2+fill*3+water*9; }
    }

    private Map<String,Object> findSite(Object mod, Object currentWorld) throws Exception {
        Object player = call(mod,"getPlayer");
        if (currentWorld==null || player==null) return result("rejected","world_not_ready");
        int width=((Number)args.get("width")).intValue(), depth=((Number)args.get("depth")).intValue();
        if (width<3 || depth<3 || width>32 || depth>32) return result("rejected","unsupported_survey_size");
        int clearance=Math.clamp(((Number)args.getOrDefault("height",8)).intValue(), 7, 32);
        List<Plot> occupied=new ArrayList<>();
        String reserved=(String)args.getOrDefault("occupied", "");
        if (!reserved.isEmpty()) for (String row : reserved.split(";")) {
            int[] bounds=Arrays.stream(row.split(",")).mapToInt(Integer::parseInt).toArray();
            if (bounds.length!=4) return result("rejected","invalid_occupied_plot");
            occupied.add(new Plot(bounds[0],bounds[1],bounds[2],bounds[3]));
        }
        int px=(int)Math.floor(((Number)call(player,"method_23317")).doubleValue());
        int py=(int)Math.floor(((Number)call(player,"method_23318")).doubleValue());
        int pz=(int)Math.floor(((Number)call(player,"method_23321")).doubleValue());
        Class<?> vector=type("dev.magehome.core.model.Vec3i"), viewType=type("dev.magehome.client.ClientWorldView");
        Object view=viewType.getConstructor(client.getClass()).newInstance(client);
        SiteColumns columns=new SiteColumns(view, vector, clearance);
        var shortlist=new ArrayList<Site>();
        int step=Math.max(8,Math.min(width,depth)/2);
        int radius=SURVEY_RADIUS/step*step;
        for (int dx=-radius; dx<=radius; dx+=step)
            for (int dz=-radius; dz<=radius; dz+=step) {
                int distance=(int)Math.ceil(Math.hypot(dx,dz));
                if (distance>SURVEY_RADIUS || Math.abs(dx)<=width/2+2 && Math.abs(dz)<=depth/2+2) continue;
                if (distance>72 && (dx%(step*2)!=0 || dz%(step*2)!=0)) continue;
                int x=px+dx-width/2, z=pz+dz-depth/2;
                if (occupied.stream().anyMatch(plot -> plot.overlaps(x,z,width,depth))) continue;
                Terrain center=columns.read(px+dx,py,pz+dz);
                if (center==SiteColumns.UNKNOWN) continue;
                var heights=new ArrayList<Site>();
                for (int dy : SURVEY_HEIGHTS) {
                    int y=py+dy;
                    if (y<-63 || y+clearance>319) continue;
                    Work estimated=columns.measure(x,y,z,width,depth,false);
                    if (estimated!=null)
                        heights.add(new Site(x,y,z,distance,estimated.cost()*4+distance+Math.abs(dy)*2));
                }
                heights.sort(Comparator.comparingInt(Site::score));
                shortlist.addAll(heights.subList(0,Math.min(heights.size(),4)));
            }
        shortlist.sort(Comparator.comparingInt(Site::score));
        Site best=null;
        Work bestWork=null;
        int bestScore=Integer.MAX_VALUE;
        int inspected=0;
        for (Site site : shortlist) {
            if (inspected>=SURVEY_FINALISTS && best!=null || inspected>=SURVEY_FINALISTS*4) break;
            inspected++;
            Work work=columns.measure(site.x(),site.y(),site.z(),width,depth,true);
            if (work==null) continue;
            int score=work.cost()*36/(width*depth) + site.distance() + Math.abs(site.y()-py)*2;
            if (score<bestScore) {
                best=site;
                bestWork=work;
                bestScore=score;
            }
        }
        if (best==null) return result("rejected","no_suitable_site");
        Map<String,Object> reply=result("found",null);
        reply.put("anchor",Map.of("x",best.x(),"y",best.y(),"z",best.z()));
        reply.put("survey",Map.of("distance",best.distance(),"cut",bestWork.cut(),"fill",bestWork.fill(),
                                  "water",bestWork.water(),"clearance",clearance,"radius",SURVEY_RADIUS));
        return reply;
    }
    private static final class SiteColumns {
        private final Object view;
        private final Constructor<?> point;
        private final Method read, known, id, solid, liquid, plant, container;
        private final int clearance;
        private final Map<Cell,Terrain> observations=new HashMap<>();
        private static final Terrain UNKNOWN=new Terrain(null,false,false,false,false);

        SiteColumns(Object view, Class<?> vector, int clearance) throws Exception {
            this.view=view;
            this.clearance=clearance;
            point=vector.getConstructor(int.class,int.class,int.class);
            read=view.getClass().getMethod("read",vector);
            Class<?> block=read.getReturnType();
            known=block.getMethod("known");
            id=block.getMethod("blockId");
            solid=block.getMethod("solidFull");
            liquid=block.getMethod("liquid");
            plant=block.getMethod("removablePlant");
            container=block.getMethod("container");
        }

        private Terrain read(int x, int y, int z) throws Exception {
            Cell position=new Cell(x,y,z);
            Terrain observed=observations.get(position);
            if (observed!=null) return observed;
            Object cell=read.invoke(view,point.newInstance(x,y,z));
            observed=Boolean.TRUE.equals(known.invoke(cell))
                    ? new Terrain((String)id.invoke(cell),(Boolean)solid.invoke(cell),(Boolean)liquid.invoke(cell),
                                  (Boolean)plant.invoke(cell),(Boolean)container.invoke(cell))
                    : UNKNOWN;
            observations.put(position,observed);
            return observed;
        }

        private Work column(int x, int y, int z) throws Exception {
            Terrain floor=read(x,y,z);
            if (!clearable(floor)) return null;
            Terrain aboveWork=read(x,y+clearance,z);
            if (!clearable(aboveWork) || !(aboveWork.air() || aboveWork.plant() || aboveWork.canopy())) return null;
            int cut=floor.air()?0:1, fill=0, water=0;
            for (int depth=0; depth<=FOUNDATION_DEPTH; depth++) {
                Terrain cell=read(x,y-1-depth,z);
                if (cell==UNKNOWN || cell.protectedBlock() || cell.id().equals("minecraft:lava")) return null;
                if (cell.stable()) {
                    if (!cell.natural()) return null;
                    for (int above=1; above<=clearance; above++) {
                        Terrain head=read(x,y+above,z);
                        if (!clearable(head)) return null;
                        if (!head.air()) cut++;
                    }
                    return new Work(cut,fill,water);
                }
                if (!cell.air() && !cell.liquid() && !cell.plant() && !cell.natural()) return null;
                fill++;
                if (cell.liquid()) water++;
                else if (!cell.air()) cut++;
            }
            return null;
        }

        private boolean clearable(Terrain cell) {
            return cell!=UNKNOWN && !cell.protectedBlock() && !cell.liquid() &&
                    (cell.air() || cell.plant() || cell.natural());
        }

        Work measure(int x, int y, int z, int width, int depth, boolean complete) throws Exception {
            int[] axesX={0,width/2,width-1}, axesZ={0,depth/2,depth-1};
            Work work=Work.NONE;
            int stepX=complete?width:axesX.length, stepZ=complete?depth:axesZ.length;
            for (int i=0; i<stepX; i++) for (int j=0; j<stepZ; j++) {
                Work value=column(x+(complete?i:axesX[i]),y,z+(complete?j:axesZ[j]));
                if (value==null) return null;
                work=work.add(value);
            }
            if (complete) {
                Work approach=null;
                for (Cell access : List.of(new Cell(x-1,y,z+depth/2),new Cell(x+width,y,z+depth/2),
                                           new Cell(x+width/2,y,z-1),new Cell(x+width/2,y,z+depth))) {
                    Work value=column(access.x(),access.y(),access.z());
                    if (value!=null && (approach==null || value.cost()<approach.cost())) approach=value;
                }
                if (approach==null) return null;
                work=work.add(approach);
            }
            return work;
        }
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
            Class<?> tc=treeClient;
            if ("find_site".equals(op)) return findSite(mod, now);
            if (op.startsWith("tree_") && tc==null) return result("rejected","treegrow_unavailable");
            Object runtime=dc==null?null:call(dc,addonApi?"combat":"runtime");
            Object handover=runtime==null || addonApi?null:field(runtime,"handover");
            boolean busy=runtime!=null && (addonApi
                ? Boolean.TRUE.equals(call(runtime,"ownsControls")) || Set.of("COMBAT","RETREAT","RESCUE","EAT").contains(String.valueOf(call(runtime,"action")))
                : !Set.of("IDLE","OFF").contains(String.valueOf(call(handover,"state"))));
            Object chain=call(mod,"getUserTaskChain");
            boolean nativeBusy=Boolean.TRUE.equals(call(chain,"isActive"));
            Map<String,Object> scheduler=scheduler(mod,chain);
            boolean schedulerBusy=Boolean.TRUE.equals(scheduler.get("busy"));
            boolean enabled=runtime!=null && Boolean.TRUE.equals(call(runtime,addonApi?"isEnabled":"enabled"));
            boolean manualStop=runtime!=null && (addonApi?Boolean.TRUE.equals(call(runtime,"manualStop")):Boolean.TRUE.equals(field(field(runtime,"bridge"),"cancelledSinceCapture")));
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
                if (runtime!=null) {
                    Class<?> api=type("dev.autodefense.api.AutoDefenseApi");
                    r.put("defense_json",addonApi?call(call(api,"get"),"state"):call(api,"getStatusJson"));
                } else r.put("defense_json","{}");
                r.put("defense_api",runtime==null?"unavailable":addonApi?"addon-0.1":"legacy-1.0");
                r.put("extensions",Map.of("unionclef",true,"treegrow",tc!=null,"auto_defense",dc!=null));
                r.put("defense_busy",busy); r.put("defense_enabled",enabled);
                if (addonApi) {
                    try {
                        r.put("defense_mode",String.valueOf(call(runtime,"mode")));
                        r.put("defense_modes",true);
                    } catch (NoSuchMethodException historicalArtifact) { r.put("defense_modes",false); }
                }
                r.put("native_busy",nativeBusy); r.put("tree",tree());
                r.put("scheduler_busy",schedulerBusy);r.put("scheduler_owner",scheduler.get("owner"));
                r.put("scheduler",scheduler);
                Object nativeDefense=call(mod,"getMobDefenseChain");
                Object nativeDefenseTask=call(nativeDefense,"getCurrentTask");
                r.put("native_defense_task",nativeDefenseTask==null?null:nativeDefenseTask.toString());
                r.put("runner_active",call(call(mod,"getTaskRunner"),"isActive"));
                r.put("native_suppressed",suppressedSettings!=null);
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
                if (args.containsKey("suppress_native"))
                    setNativeSuppressed(mod,Boolean.TRUE.equals(args.get("suppress_native")));
                if (runtime==null) return result("applied",null);
                boolean desired=Boolean.TRUE.equals(args.get("enabled"));
                boolean changed=false;
                if (addonApi && args.get("mode") instanceof String modeName) {
                    Object mode=call(runtime,"mode");
                    if (!String.valueOf(mode).equals(modeName)) {
                        Object selected=call(mode.getClass(),"valueOf",modeName);
                        call(runtime,"setMode",client,selected);
                        changed=true;
                    }
                }
                // setMode latches manualStop. An explicit Lab Start must also
                // clear the latch in the same tick even when defense stays off.
                boolean clearStop=addonApi && (changed || manualStop && Boolean.TRUE.equals(args.get("resume")));
                if (clearStop) call(runtime,"enable",client);
                if ((clearStop || enabled) != desired) {
                    if (addonApi && dc!=null) {
                        call(runtime,desired?"enable":"disable",client);
                    } else call(runtime,"toggle",client);
                }
                if (addonApi && (clearStop || enabled != desired)) call(dc,"publishSnapshot",client);
                return result("applied",null);
            }
            if (!session.equals(args.get("world"))) return result("rejected","stale_world");
            if ("halt".equals(op)) {
                setNativeSuppressed(mod,false);
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
                    if (addonApi && dc!=null) call(dc,"publishSnapshot",client);
                } finally {
                    put(runner,"cachedCurrentTaskChain",null);
                }
                return result("halted",null);
            }
            // This coordinator owns replay. Native defense must never replay a stale @command.
            if (runtime!=null && !addonApi) {
                put(field(runtime,"bridge"),"lastLine",null);
                put(handover,"savedCommand",null);
            }
            if ("maintain".equals(op)) {
                if (runtime!=null && Boolean.TRUE.equals(args.get("ack_manual_stop"))) {
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
