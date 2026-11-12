package org.saruman.bridge;
import java.util.*;
import java.util.function.Supplier;
/** Explicit local-admin request; execution is scheduled on Minecraft's own executor. */
public final class WorldConnection implements Supplier<Map<String,Object>> {
    private final Object client;
    private final String mode,target,id;
    private static final Set<String> submitted=new HashSet<>();
    public WorldConnection(Object client,String mode,String target,String id) {
        this.client=client;this.mode=mode;this.target=target;this.id=id;
    }
    @Override public Map<String,Object> get() {
        try {
            Object world=client.getClass().getField("field_1687").get(client);
            if(world!=null)return Map.of("status","in_world");
            if(mode.equals("status"))return Map.of("status","menu");
            if(submitted.contains(id))return Map.of("status","connecting");
            if(!mode.equals("world")||!target.matches("[^/\\\\:]{1,100}")||target.equals(".")||target.equals(".."))
                return Map.of("status","rejected","reason","Invalid existing-world name");
            // Pinned Minecraft 1.21.11 IntegratedServerLoader: open an EXISTING save.
            Object loader=client.getClass().getMethod("method_41735").invoke(client);
            java.io.File run=(java.io.File)client.getClass().getField("field_1697").get(client);
            java.nio.file.Path saves=run.toPath().resolve("saves").toRealPath();
            java.nio.file.Path save=saves.resolve(target).toRealPath();
            if(!save.startsWith(saves)||!java.nio.file.Files.isRegularFile(save.resolve("level.dat")))
                return Map.of("status","rejected","reason","Existing save not found");
            submitted.add(id);
            loader.getClass().getMethod("method_57784",String.class,Runnable.class).invoke(loader,target,(Runnable)()->{});
            return Map.of("status","connecting");
        }catch(Exception error){return Map.of("status","error","reason",error.getClass().getSimpleName());}
    }
}
