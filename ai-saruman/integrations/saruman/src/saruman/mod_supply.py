"""One external equipment supplier; never blocks combat or fabricates confirmation."""

import time

from saruman.equipment_supply import command_capabilities

STOCK = {
    "minecraft:iron_sword": 1,
    "minecraft:shield": 1,
    "minecraft:bow": 1,
    "minecraft:arrow": 64,
    "minecraft:cooked_beef": 8,
    "minecraft:potion": 2,
}


class ModSupply:
    def __init__(self, gateway, store, clock=time.time):
        self.gateway, self.store, self.clock = gateway, store, clock
        self.due = 0

    def tick(self, game, snapshot):
        if snapshot.get("native_supply"):
            game["mod_supply"] = {"status": "owned_by_addon", "needs": snapshot["defense"].get("missingItems", [])}
            return
        now = self.clock()
        if now < self.due or snapshot["defense_busy"] or snapshot["native_busy"]:
            return
        self.due = now + 2
        capabilities = command_capabilities(self.gateway)
        if not capabilities["give"]:
            game["mod_supply"] = {
                "status": "commands_unavailable",
                "needs": snapshot["defense"].get("missingItems", []),
            }
            return
        key = f"supply:{snapshot['world']}"
        requests = self.store.meta(key, {})
        missing = {
            m["item"]: m["missing"] for m in snapshot["defense"].get("missingItems", [])
        }
        for item, request in requests.items():
            if item not in missing:
                request["status"] = "confirmed_by_native_inventory"
            elif now - request["at"] >= 4:
                request["status"] = "unconfirmed"
        for item, count in missing.items():
            if item not in STOCK or count <= 0:
                continue
            previous = requests.get(item, {})
            if now - previous.get("at", 0) < 60:
                continue
            quantity = min(STOCK[item], int(count))
            request = {"at": now, "count": quantity, "status": "delivery_unknown"}
            requests[item] = request
            self.store.set_meta(key, requests)  # reserve BEFORE transport
            stack = (
                'minecraft:potion[minecraft:potion_contents={potion:"minecraft:healing"}]'
                if item == "minecraft:potion"
                else item
            )
            self.gateway.entry_point.ChatMessage(
                f"/{capabilities['give']} @s {stack} {quantity}"
            )
            request["status"] = "awaiting_inventory"
            break
        self.store.set_meta(key, requests)
        game["mod_supply"] = {"status": "ready", "requests": requests, "needs": missing}
