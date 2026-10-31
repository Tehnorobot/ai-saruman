"""Loads a reflection-only, pinned Java shim; world operations run on the client tick."""

import hashlib
import json
import os
import shutil
import subprocess

from saruman.mod_goals import ROOT


class NativeMods:
    def __init__(self, gateway):
        from saruman.minecraft import _java_to_python

        self.gateway, self.convert = gateway, _java_to_python
        source = ROOT / "resources/Minecraft/bridge/ModGateway.java"
        target = (
            ROOT
            / "data/native-bridge"
            / hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        )
        compiled = target / "org/saruman/bridge/ModGateway.class"
        if not compiled.exists():
            javac = os.getenv("SARUMAN_JAVAC") or shutil.which("javac")
            if not javac:
                raise RuntimeError(
                    "Native mod adapter requires JDK 21+ javac; set SARUMAN_JAVAC"
                )
            target.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    javac,
                    "--release",
                    "21",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(target),
                    str(source),
                ],
                check=True,
                capture_output=True,
                timeout=45,
            )
        jvm = gateway.jvm
        urls = gateway.new_array(jvm.java.net.URL, 1)
        urls[0] = jvm.java.io.File(str(target)).toURI().toURL()
        parent = gateway.entry_point.getClass().getClassLoader()
        # Keep the receipt/run ledger alive across Python bridge reconnects in the
        # same Minecraft JVM. A changed shim hash deliberately creates a new scope.
        registry = jvm.java.lang.System.getProperties()
        key = "saruman.native.adapter:" + target.name
        self.loader = registry.get(key)
        if self.loader is None:
            candidate = jvm.java.net.URLClassLoader.newInstance(urls, parent)
            previous = registry.putIfAbsent(key, candidate)
            self.loader = candidate if previous is None else previous
        self.cls = self.loader.loadClass("org.saruman.bridge.ModGateway")
        params = gateway.new_array(jvm.java.lang.Class, 3)
        params[0] = jvm.java.lang.Class.forName("java.lang.Object")
        params[1] = jvm.java.lang.Class.forName("java.lang.String")
        params[2] = jvm.java.lang.Class.forName("java.util.Map")
        self.constructor = self.cls.getConstructor(params)
        self.client = jvm.net.minecraft.class_310.method_1551()

    def call(self, operation, **args):
        gateway, jvm = self.gateway, self.gateway.jvm
        values = jvm.java.util.HashMap()
        for key, value in args.items():
            values.put(key, value)
        objects = gateway.new_array(jvm.java.lang.Object, 3)
        objects[0], objects[1], objects[2] = self.client, operation, values
        supplier = self.constructor.newInstance(objects)
        future = jvm.java.util.concurrent.CompletableFuture.supplyAsync(
            supplier, self.client
        )
        result = self.convert(future.get(3, jvm.java.util.concurrent.TimeUnit.SECONDS))
        if operation == "snapshot" and result.get("status") == "ready":
            result["defense"] = json.loads(result.pop("defense_json"))
        return result
