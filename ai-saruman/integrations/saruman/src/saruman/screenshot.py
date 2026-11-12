"""Capture only Minecraft's framebuffer into the project's fixed media staging folder."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from py4j.java_gateway import JavaGateway,GatewayParameters


def capture(identifier,gateway=None):
    if not re.fullmatch('[a-f0-9]{64}',identifier):raise ValueError('Invalid capture ID')
    root=Path(__file__).resolve().parents[2]
    target=root.parents[1]/'.saruman/runtime/captures'/(identifier+'.png')
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.resolve().parent!=target.parent.resolve():raise ValueError('Capture path escapes storage')
    if target.is_file():return {'status':'completed'}
    source=root/'resources/Minecraft/bridge/GameScreenshot.java'
    output=root/'data/screenshot-bridge'/hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    if not (output/'org/saruman/bridge/GameScreenshot.class').is_file():
        output.mkdir(parents=True,exist_ok=True)
        subprocess.run([shutil.which('javac') or 'javac','--release','21','-encoding','UTF-8','-d',str(output),str(source)],
                       check=True,capture_output=True,timeout=45)
    own=gateway is None
    g=gateway or JavaGateway(gateway_parameters=GatewayParameters(port=25333,read_timeout=35,auto_convert=True))
    try:
        props=g.jvm.java.lang.System.getProperties()
        loader_key='saruman.screenshot.adapter:'+output.name
        loader=props.get(loader_key)
        if loader is None:
            urls=g.new_array(g.jvm.java.net.URL,1);urls[0]=g.jvm.java.io.File(str(output)).toURI().toURL()
            loader=g.jvm.java.net.URLClassLoader.newInstance(urls,g.entry_point.getClass().getClassLoader())
            props.put(loader_key,loader)
        key='saruman.screenshot:'+identifier
        job=props.get(key)
        if job is None:
            cls=loader.loadClass('org.saruman.bridge.GameScreenshot')
            types=g.new_array(g.jvm.java.lang.Class,2)
            types[0]=g.jvm.java.lang.Class.forName('java.lang.Object');types[1]=g.jvm.java.lang.Class.forName('java.lang.String')
            args=g.new_array(g.jvm.java.lang.Object,2)
            args[0]=g.jvm.net.minecraft.class_310.method_1551();args[1]=str(target)
            job=cls.getConstructor(types).newInstance(args)
            previous=props.putIfAbsent(key,job)
            if previous is not None:job=previous
            else:args[0].execute(job)
        return dict(job.getResult().get(30,g.jvm.java.util.concurrent.TimeUnit.SECONDS).items())
    finally:
        if own:g.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('identifier');args=parser.parse_args()
    try:result=capture(args.identifier)
    except Exception as error:result={'status':'error','reason':type(error).__name__}
    print(json.dumps(result))


if __name__=='__main__':main()
