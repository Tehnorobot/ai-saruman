package org.saruman.bridge;
import java.nio.file.*;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;

/** Minecraft 1.21.11 framebuffer only. No desktop capture or synthetic key presses. */
public final class GameScreenshot implements Runnable {
    private final Object client;
    private final Path output;
    private final String focus;
    public final CompletableFuture<Map<String,Object>> result=new CompletableFuture<>();
    public GameScreenshot(Object client,String output,String focus){
        this.client=client;this.output=Path.of(output);this.focus=focus;
    }
    public CompletableFuture<Map<String,Object>> getResult(){return result;}
    public void run(){
        try{
            Object world=client.getClass().getField("field_1687").get(client);
            if(world==null)throw new IllegalStateException("Minecraft world is not open");
            // Do not publish menus, inventory, chat or a stale scene while paused.
            if(client.getClass().getField("field_1755").get(client)!=null)
                throw new IllegalStateException("Close the Minecraft screen before taking a photo");
            ClassLoader loader=client.getClass().getClassLoader();
            Consumer<Object> callback=image->{
                Path temporary=output.resolveSibling(output.getFileName()+".tmp.png");
                try{
                    if(client.getClass().getField("field_1687").get(client)!=world)
                        throw new IllegalStateException("Minecraft world changed during capture");
                    Files.createDirectories(output.getParent());
                    image.getClass().getMethod("method_4325",java.io.File.class).invoke(image,temporary.toFile());
                    Files.move(temporary,output,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
                    result.complete(Map.of("status","completed"));
                }catch(Exception error){result.completeExceptionally(error);}
                finally{
                    try{image.getClass().getMethod("close").invoke(image);}catch(Exception ignored){}
                    try{Files.deleteIfExists(temporary);}catch(Exception ignored){}
                }
            };
            if (!focus.isEmpty()) {
                Class<?> camera;
                try {
                    camera = Class.forName("dev.magehome.client.CompletionPhoto", true, loader);
                } catch (ClassNotFoundException missing) {
                    result.complete(Map.of("status", "error", "reason",
                            "Обновите клиентский JAR Mage Home для снимка готового дома."));
                    return;
                }
                Consumer<String> failed = reason -> result.complete(Map.of("status", "error", "reason", reason));
                camera.getMethod("request", String.class, Consumer.class, Consumer.class)
                        .invoke(null, focus, callback, failed);
            } else {
                Object framebuffer=client.getClass().getMethod("method_1522").invoke(client);
                Class<?> recorder=Class.forName("net.minecraft.class_318",true,loader);
                Class<?> fb=Class.forName("net.minecraft.class_276",true,loader);
                recorder.getMethod("method_1663",fb,Consumer.class).invoke(null,framebuffer,callback);
            }
        }catch(Exception error){result.completeExceptionally(error);}
    }
}
