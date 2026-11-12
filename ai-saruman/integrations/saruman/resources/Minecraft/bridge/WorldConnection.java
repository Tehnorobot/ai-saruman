package org.saruman.bridge;
import java.util.*;
import java.util.function.Supplier;
import java.nio.file.*;
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
            if(!mode.equals("world")||target==null||!target.matches("[^/\\\\:]{1,100}")
                    ||target.indexOf(0)>=0||target.equals(".")||target.equals(".."))
                return Map.of("status","rejected","code","invalid_world_name",
                        "reason","Укажите точное имя папки мира из saves текущего профиля Minecraft.");
            // Pinned Minecraft 1.21.11 IntegratedServerLoader: open an EXISTING save.
            java.io.File run=(java.io.File)client.getClass().getField("field_1697").get(client);
            Path saves=run.toPath().resolve("saves");
            if(!Files.isDirectory(saves))return missingWorld();
            saves=saves.toRealPath();
            Path candidate=saves.resolve(target);
            if(!Files.isDirectory(candidate))return missingWorld();
            Path save=candidate.toRealPath();
            if(!save.startsWith(saves)||!Files.isRegularFile(save.resolve("level.dat")))return missingWorld();
            Object loader=client.getClass().getMethod("method_41735").invoke(client);
            submitted.add(id);
            loader.getClass().getMethod("method_57784",String.class,Runnable.class).invoke(loader,target,(Runnable)()->{});
            return Map.of("status","connecting");
        }catch(NoSuchFileException error){return missingWorld();}
        catch(InvalidPathException error){return Map.of("status","rejected","code","invalid_world_name",
                "reason","Укажите точное имя папки мира из saves текущего профиля Minecraft.");}
        catch(Exception error){
            submitted.remove(id);
            return Map.of("status","error","reason","Не удалось открыть мир: "+error.getClass().getSimpleName()
                    +". Проверьте окно Minecraft.");
        }
    }
    private Map<String,Object> missingWorld() {
        return Map.of("status","rejected","code","world_not_found",
                "reason","Мир «"+target+"» не найден в saves текущего профиля Minecraft. "
                        +"Укажите точное имя папки мира в Настройки → Подключение к Minecraft.");
    }
}
