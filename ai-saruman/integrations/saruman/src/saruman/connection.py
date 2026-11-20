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
        request_key="saruman.connect:"+identifier
        root=Path(__file__).resolve().parents[2]
        source=root/"resources/Minecraft/bridge/WorldConnection.java"
        output=root/"data/world-bridge"/hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        if not (output/"org/saruman/bridge/WorldConnection.class").exists():
            output.mkdir(parents=True,exist_ok=True)
            subprocess.run([shutil.which("javac") or "javac","--release","21","-encoding","UTF-8","-d",str(output),str(source)],
                           check=True,capture_output=True,timeout=45)
        urls=g.new_array(g.jvm.java.net.URL,1);urls[0]=g.jvm.java.io.File(str(output)).toURI().toURL()
        registry=g.jvm.java.lang.System.getProperties();loader_key="saruman.world.adapter:"+output.name
        loader=registry.get(loader_key)
        if loader is None:
            loader=g.jvm.java.net.URLClassLoader.newInstance(urls,g.entry_point.getClass().getClassLoader())
            registry.put(loader_key,loader)
        cls=loader.loadClass("org.saruman.bridge.WorldConnection")
        types=g.new_array(g.jvm.java.lang.Class,4)
        types[0]=g.jvm.java.lang.Class.forName("java.lang.Object")
        for i in (1,2,3):types[i]=g.jvm.java.lang.Class.forName("java.lang.String")
        values=g.new_array(g.jvm.java.lang.Object,4)
        values[0]=g.jvm.net.minecraft.class_310.method_1551();values[1]=mode;values[2]=target;values[3]=identifier
        future=g.jvm.java.util.concurrent.CompletableFuture.supplyAsync(cls.getConstructor(types).newInstance(values),values[0])
        result=dict(future.get(5,g.jvm.java.util.concurrent.TimeUnit.SECONDS).items())
        if mode=="server" and result.get("status")=="menu":
            # UnionClef schedules the join on the client executor. Retry the observation, not the join.
            if props.putIfAbsent(request_key,"submitted") is None:g.entry_point.ConnectToServer(target)
            return {"status":"connecting"}
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
