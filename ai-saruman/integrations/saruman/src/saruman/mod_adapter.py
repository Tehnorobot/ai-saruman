"""Loads a reflection-only, pinned Java shim; world operations run on the client tick."""

import hashlib
import json
import os
import shutil
import subprocess

from saruman.mod_goals import ROOT

ARTIFACTS = {
    "altoclef": {"76bbb677d2f8294e6d2de98b554f81818ae15605ef8a82ff7e1d3b9b82555d1b"},
    "treegrow": {"edd7a699318d0b18f46abe67db25046502e20ca400f4751c0ac0679664e5bee9"},
    "auto-defense": {"e19d47caa2ae553ab81b5b84ab4fc11179595809797cee7d122cad5f2b1db43c"},
    "auto_defense": {"5e773d1e64f651943b5faf709aea3ef1458503a2a25910acd476ab6a457e25cb"},
}


def probe_native_mods(gateway):
    """Read actual API/contracts and origin JARs. No compilation or game commands."""
    from pathlib import Path

    loader = gateway.jvm.net.fabricmc.loader.api.FabricLoader.getInstance()
    names = {}
    defense_id = "auto_defense" if loader.getModContainer("auto_defense").isPresent() else "auto-defense"
    for mod_id in ("altoclef", defense_id, "treegrow"):
        optional = loader.getModContainer(mod_id)
        if not optional.isPresent():
            raise RuntimeError(f"Не загружен мод {mod_id}")
        paths = optional.get().getOrigin().getPaths()
        if paths.size() != 1:
            raise RuntimeError(f"Не удалось определить JAR {mod_id}")
        path = Path(str(paths.iterator().next().toString()))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in ARTIFACTS[mod_id]:
            raise RuntimeError(f"Непроверенная сборка {mod_id}: {path.name}, SHA-256 {digest[:16]}; требуется проверка совместимости адаптера")
        names[mod_id] = path.name
    api = gateway.jvm.dev.autodefense.api.AutoDefenseApi
    state = json.loads(api.get().state() if defense_id == "auto_defense" else api.getStatusJson())
    if not isinstance(state, dict) or (defense_id == "auto_defense" and state.get("ok") is not True):
        raise RuntimeError("Auto Defense API не подтвердил чтение состояния")
    gateway.jvm.lab.treegrow.TreeGrowClient.current()  # None means idle, not missing.
    return names


def normalize_snapshot(result):
    """Preserve native facts and expose the fields consumed by existing UI/supply."""
    if result.get("status") != "ready":
        return result
    defense = json.loads(result.pop("defense_json"))
    if not isinstance(defense, dict):
        raise ValueError("Invalid Auto Defense snapshot")
    if result.get("defense_api") == "addon-0.1":
        if defense.get("ok") is not True:
            raise RuntimeError("Auto Defense API failed to read state")
        defense["missingItems"] = defense.get("needs", [])
        defense["owner"] = "auto-defense" if result["defense_busy"] else "unionclef" if result["native_busy"] else "none"
        # This addon owns its own supply policy. Do not run a second supplier.
        result["native_supply"] = True
    result["defense"] = defense
    return result


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
            result = normalize_snapshot(result)
        return result
