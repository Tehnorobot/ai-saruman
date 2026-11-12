"""Readiness and explicit join only. Does not accept EULA, create worlds or log in."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from py4j.java_gateway import JavaGateway,GatewayParameters


def connect(mode,target,identifier,gateway=None):
    own=gateway is None
    g=gateway or JavaGateway(gateway_parameters=GatewayParameters(port=25333,read_timeout=5,auto_convert=True))
    try:
        props=g.jvm.java.lang.System.getProperties()
        key="saruman.connect:"+identifier
        if g.entry_point.inGame():
            return {"status":"in_world"} if mode=="status" or props.get(key) is not None else {
                "status":"blocked","reason":"Уже открыт мир. Переключение не выполнено; для другого мира сначала выйдите в меню"}
        if mode=="server":
            # Verified UnionClef API schedules its operation on the Minecraft executor.
            if props.putIfAbsent(key,"submitted") is None:g.entry_point.ConnectToServer(target)
            return {"status":"connecting"}
        if mode=="status":return {"status":"menu"}
        root=Path(__file__).resolve().parents[2]
        source=root/"resources/Minecraft/bridge/WorldConnection.java"
        output=root/"data/world-bridge"/hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        if not (output/"org/saruman/bridge/WorldConnection.class").exists():
            output.mkdir(parents=True,exist_ok=True)
            subprocess.run([shutil.which("javac") or "javac","--release","21","-encoding","UTF-8","-d",str(output),str(source)],
                           check=True,capture_output=True,timeout=45)
        urls=g.new_array(g.jvm.java.net.URL,1);urls[0]=g.jvm.java.io.File(str(output)).toURI().toURL()
        registry=g.jvm.java.lang.System.getProperties();key="saruman.world.adapter:"+output.name
        loader=registry.get(key)
        if loader is None:
            loader=g.jvm.java.net.URLClassLoader.newInstance(urls,g.entry_point.getClass().getClassLoader())
            registry.put(key,loader)
        cls=loader.loadClass("org.saruman.bridge.WorldConnection")
        types=g.new_array(g.jvm.java.lang.Class,4)
        types[0]=g.jvm.java.lang.Class.forName("java.lang.Object")
        for i in (1,2,3):types[i]=g.jvm.java.lang.Class.forName("java.lang.String")
        values=g.new_array(g.jvm.java.lang.Object,4)
        values[0]=g.jvm.net.minecraft.class_310.method_1551();values[1]=mode;values[2]=target;values[3]=identifier
        # Persist intent before delivery: loading may outlive the RPC timeout while
        # the same client operation still completes. A later poll must recognize it.
        props.putIfAbsent(key,"submitted")
        future=g.jvm.java.util.concurrent.CompletableFuture.supplyAsync(cls.getConstructor(types).newInstance(values),values[0])
        result=dict(future.get(5,g.jvm.java.util.concurrent.TimeUnit.SECONDS).items())
        if result.get("status")=="connecting":props.put(key,"submitted")
        return result
    finally:
        if own:g.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument("mode",choices=["status","server","world"])
    parser.add_argument("target",nargs="?",default="");parser.add_argument("identifier",nargs="?",default="status")
    args=parser.parse_args()
    try:value=connect(args.mode,args.target,args.identifier)
    except Exception as error:value={"status":"unavailable","reason":type(error).__name__}
    print(json.dumps(value,ensure_ascii=False))

if __name__=="__main__":main()
