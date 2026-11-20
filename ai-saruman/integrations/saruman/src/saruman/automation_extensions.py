"""Shared catalog and local transport for the Lab and the native game worker."""
import json
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
PRIORITY = {"planner": 10, "normal": 20, "defense": 30, "player": 40, "operator": 50}


def native_catalog():
    return json.loads((ROOT / "resources/Minecraft/extensions.json").read_text(encoding="utf-8"))


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def managed_directory():
    directory = os.getenv("SARUMAN_LAB_CONTROL_DIR")
    return Path(directory) if directory else None


def enabled_for(kind):
    directory = managed_directory()
    return directory is None or read_json(directory / "extensions.json").get("native", {}).get(kind, True)


def inspect(request):
    from py4j.java_gateway import GatewayParameters, JavaGateway
    from py4j.protocol import Py4JError
    from saruman.mod_adapter import probe_native_mods

    gateway = JavaGateway(gateway_parameters=GatewayParameters(
        address="127.0.0.1", port=int(os.environ.get("MINECRAFT_GATEWAY_PORT", "25333")), read_timeout=2))
    try:
        loader = gateway.jvm.net.fabricmc.loader.api.FabricLoader.getInstance()
        mods, jars = {}, {}
        for key in request["mods"]:
            container = loader.getModContainer(key)
            mods[key] = container.isPresent()
            if mods[key]:
                paths = container.get().getOrigin().getPaths()
                if paths.size() == 1:
                    jars[key] = str(paths.iterator().next().getFileName().toString())
        class_loader = gateway.entry_point.getClass().getClassLoader()
        apis = {}
        for name in request["apis"]:
            try:
                class_loader.loadClass(name)
            except Py4JError:
                apis[name] = False
            else:
                apis[name] = True
        names, failures = probe_native_mods(gateway, partial=True)
        return {"connected": True, "world": bool(gateway.entry_point.inGame()),
                "installed": mods, "jars": jars, "compatible": list(names), "failures": failures, "apis": apis}
    finally:
        gateway.close()


if __name__ == "__main__":
    import sys

    try:
        print(json.dumps(inspect(json.load(sys.stdin)), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"connected": False, "error": str(error).strip()[:240]}))
