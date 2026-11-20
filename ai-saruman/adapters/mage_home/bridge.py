"""One call to Mage Home through the existing UnionClef gateway."""

import json
import os
from pathlib import Path
import sys

from py4j.java_gateway import GatewayParameters, JavaGateway, JavaPackage, get_field
from py4j.protocol import Py4JError


def building_resources(gateway):
    client = gateway.jvm.net.minecraft.class_310.method_1551()
    player = get_field(client, 'field_1724')
    if player is None or client.method_1562() is None:
        return None
    inventory = player.method_31548()
    stock = {}
    # These are the same main-inventory slots the builder can count and swap.
    for index in range(36):
        stack = inventory.method_5438(index)
        count = stack.method_7947()
        if count > 0:
            block = str(stack.method_7909().toString())
            stock[block] = stock.get(block, 0) + count
    commands = client.method_1562().method_2886().getRoot()
    give = commands.getChild("give") is not None
    return {"inventory": stock, "canSupplyBlocks": give, "canHangPaintings": give}


def enable_design_cache(request):
    if request["op"] != "start":
        return
    design_profile = request.get("properties", {}).get("designProfile")
    if not design_profile:
        return
    from saruman.managed import atomic_json

    path = Path(design_profile)
    profile = json.loads(path.read_text(encoding="utf-8"))
    if "designCacheDirectory" not in profile:
        profile["designCacheDirectory"] = "design-cache"
        atomic_json(path, profile)


def call(request):
    gateway = JavaGateway(gateway_parameters=GatewayParameters(
        address="127.0.0.1", port=int(os.environ.get("MINECRAFT_GATEWAY_PORT", "25333")),
        read_timeout=6,
    ))
    try:
        if request["op"] == "find_site":
            from saruman.mod_adapter import NativeMods

            result = NativeMods(gateway).call("find_site", **request["payload"])
            if result.get("status") != "found":
                reasons = {
                    "world_not_ready": "Войдите в мир Minecraft перед поиском участка.",
                    "unsupported_survey_size": "Автопоиск проверяет ширину и глубину от 3 до 32 блоков. Для другого размера укажите X, Y, Z.",
                    "no_suitable_site": "Среди загруженных участков не нашлось свободной площадки, где можно срезать лишний грунт и устроить опору. Подойдите к другой части мира или укажите X, Y, Z.",
                }
                code = result.get("reason")
                return {"ok": False, "error": {"code": "site_not_found", "message": reasons.get(
                    code, "Не удалось обследовать участок: " + str(code or "игровой клиент недоступен"))}}
            return {"ok": True, "api": "mage_home.api/1", "op": "find_site", "result": result}
        api = gateway.jvm.dev.magehome.v5.api.MageHomePublicApi
        if isinstance(api, JavaPackage):
            raise RuntimeError("Minecraft запущен без Mage Home v5. Проверьте JAR в папке mods.")
        if request["op"] in {"start", "resume"}:
            capabilities = json.loads(api.execute("capabilities", gateway.jvm.com.google.gson.JsonObject()))
            if capabilities.get("result", {}).get("blockMutationPolicy") != "physical_only":
                return {"ok": False, "error": {"code": "mage_home_client_update_required",
                        "message": "Обновите клиентский JAR Mage Home и перезапустите Minecraft для строительства вручную персонажем."}}
        enable_design_cache(request)
        for key, value in request.get("properties", {}).items():
            gateway.jvm.java.lang.System.setProperty("mage_home.model." + key, value)
        payload = gateway.jvm.com.google.gson.JsonParser.parseString(
            json.dumps(request.get("payload", {}), ensure_ascii=False)
        ).getAsJsonObject()
        reply = json.loads(api.execute(request["op"], payload))
        if request["op"] == "capabilities" and reply.get("ok"):
            loader = gateway.jvm.net.fabricmc.loader.api.FabricLoader.getInstance()
            reply["result"]["gameDir"] = str(loader.getGameDir().toAbsolutePath().normalize().toString())
            if reply["result"].get("world", {}).get("connected"):
                resources = building_resources(gateway)
                if resources is not None:
                    if reply["result"].get("interiorProtocol") != 2:
                        resources["canHangPaintings"] = False
                    reply["result"]["resources"] = resources
        return reply
    finally:
        gateway.close()


if __name__ == "__main__":
    try:
        result = call(json.load(sys.stdin))
    except (Py4JError, OSError):
        result = {"ok": False, "error": {"code": "bridge_unavailable",
                  "message": "Соединение с Minecraft прервано. Проверьте клиент с UnionClef и Mage Home."}}
    except (ValueError, RuntimeError) as error:
        result = {"ok": False, "error": {"code": "bridge_setup", "message": str(error)}}
    print(json.dumps(result, ensure_ascii=False))
