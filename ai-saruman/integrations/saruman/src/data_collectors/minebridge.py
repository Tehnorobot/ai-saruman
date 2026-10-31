# TODO refactor this
import copy
import logging
import multiprocessing
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime

from py4j.java_gateway import JavaGateway

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    from data_schema.structure_templates import create_ctx_swarm

from character.character_manager import CharacterManager
from data_flow.ctx_handler import CtxHandler
from data_schema.ctx_structures import CtxSwarmType, FilterRequest
from data_schema.structure_templates import REPO_DATA_PATH
from utils.minecraft_helper import parse_nearby_players, primitivize_nearby_players
from utils.time_helper import eztime, tm
from saruman.minecraft import connect as connect_minecraft, normalize_chat_message
from saruman.minecraft import _java_to_python
from saruman.navigation import NavigationGuard
from saruman.mod_goals import using_mods, submit_command
from saruman.mod_adapter import NativeMods
from saruman.mod_runtime import AutomationRuntime
from saruman.mod_supply import ModSupply
from saruman.collection import read_user_task
from saruman.world_rules import NativeWorldRules
from saruman.peaceful import guard_command, guard_crop_inputs, install_native_guard
from saruman.respawn import RespawnController
from saruman.survival import WaterSafety
from saruman.combat_reflex import CombatReflex
from saruman.combat_policy import record_native_damage

# Disable unused py4j logging (but may need for debug)
logger = logging.getLogger("py4j")
logger.setLevel(logging.FATAL)

character = CharacterManager.get_main_character()
SELF_NAME = character.get_names().Minecraft


def chat_queue_handler(
    mc: JavaGateway, ctx_swarm: CtxSwarmType, main_thread_actived={"actived": True}, navigation=None
):

    while ctx_swarm["env"]["actived"] and main_thread_actived["actived"]:
        if ctx_swarm["game"]["ingame"]:
            chat_queue_handle(mc, ctx_swarm, navigation)
        time.sleep(0.1)


def chat_queue_handle(mc, ctx_swarm, navigation=None):
    ctx_chat_queue = ctx_swarm["chat_queue"]
    ctx_game = ctx_swarm["game"]
    delta = (
        datetime.now()
        - tm(ctx_game.get("mc_last_chat_interact", "2024-01-01 00:00:00"))
    ).total_seconds()
    # print("DELTA KGSDJGKL " + str(round(delta, 2)), "LEN CHAT Q", len(ctx_chat_queue))
    if (len(ctx_chat_queue) > 0) and (
        datetime.now()
        - tm(ctx_game.get("mc_last_chat_interact", "2024-01-01 00:00:00"))
    ).total_seconds() >= 0.5:
        try:
            # Peek at first message to decide if we should send now
            try:
                peek_msg = ctx_chat_queue[0]
            except (IndexError, Exception):
                return  # queue became empty between len() check and access
            if peek_msg is None:
                return
            peek_msg = str(peek_msg).strip()

            is_command = False
            special_symbol = "@"
            try:
                if peek_msg[0] in ["$", "@", "/"]:
                    special_symbol = peek_msg[0]
                    is_command = True
            except Exception:
                is_command = False

            if (
                is_command
                or (
                    datetime.now()
                    - tm(
                        ctx_game.get(
                            "mc_last_chat_interact",
                            "2024-01-01 00:00:00",
                        )
                    )
                ).total_seconds()
                >= 6
            ):
                try:
                    # Atomic pop — this is the real message we'll send
                    chat_msg = ctx_chat_queue.pop(0)
                    if chat_msg is None:
                        return  # queue was emptied by another thread
                    chat_msg = str(chat_msg).strip()

                    # Re-check command status on the actually popped message
                    is_command = False
                    special_symbol = "@"
                    if chat_msg and chat_msg[0] in ["$", "@", "/"]:
                        special_symbol = chat_msg[0]
                        is_command = True

                    print(
                        "[MC DEBUG] chat_queue msg, queue",
                        chat_msg,
                        ctx_chat_queue,
                    )
                    if chat_msg:
                        if is_command:
                            if using_mods(ctx_game):
                                print("[MOD DISPATCH]", submit_command(ctx_game, chat_msg.replace("$", "@", 1)))
                                return
                            if "alive" in ctx_game and ctx_game.get("alive") is not True:
                                print("[MC POLICY] Discarded command while player is dead or unavailable")
                                return
                            if ctx_game.get("survival", {}).get("active") or ctx_game.get("self_defense", {}).get("active"):
                                print("[MC POLICY] Discarded queued command during self preservation")
                                return
                            try:
                                guard_command(chat_msg)
                                parts = chat_msg.lstrip("@$/!").lower().split()
                                if len(parts) > 1 and parts[0] == "get" and parts[1].removeprefix("minecraft:") == "bread":
                                    inventory = _java_to_python(mc.getInventoryFull())
                                    if not isinstance(inventory, dict) or not inventory.get("ok"):
                                        raise ValueError("Cannot verify harvested wheat for bread crafting")
                                    guard_crop_inputs(chat_msg, inventory)
                            except ValueError as error:
                                print(f"[MC POLICY] Command rejected: {error}")
                                return

                            CMD_SIGN_TO_TO_OPERATOR = {"$": "@"}
                            if special_symbol == "@":
                                cmd = (
                                    CMD_SIGN_TO_TO_OPERATOR.get(
                                        special_symbol,
                                        special_symbol,
                                    )
                                    + chat_msg[1:]
                                )
                                print("[MC] RUN EXECUTOR: '" + cmd + "'")
                                if navigation is None:
                                    mc.RunInnerCommand(cmd)
                                else:
                                    navigation.send(mc, cmd, ctx_game)
                                ctx_game["mc_last_chat_interact"] = eztime()
                            else:
                                cmd = (
                                    CMD_SIGN_TO_TO_OPERATOR.get(
                                        special_symbol,
                                        special_symbol,
                                    )
                                    + chat_msg[1:]
                                )
                                print("[MC] RUN CMD: '" + cmd + "'")
                                mc.ChatMessage(chat_msg)
                                ctx_game["mc_last_chat_interact"] = eztime()
                        else:
                            # TODO NEW UNTESTED
                            # FILTER THE PRINTING MESSAGE AND CHECK
                            # IF IT IS ACCEPTABLE
                            filter_enabled = ctx_swarm.get("filter", {}).get(
                                "chat_filter_enabled", True
                            )
                            is_nonmoderatable_server = any(
                                [
                                    srv in ctx_game.get("server", "")
                                    for srv in ["musteryworld"]
                                ]
                            )
                            if is_nonmoderatable_server:
                                filter_enabled = False
                            acceptable = False
                            if filter_enabled:
                                print(
                                    "putting to filter queue",
                                    ctx_game.get("server", ""),
                                )
                                try:
                                    ctx_swarm["filter_input_queue"].put(
                                        FilterRequest(
                                            text=chat_msg,
                                            filter_type="basic",
                                        ),
                                        timeout=5,
                                    )
                                    acceptable = ctx_swarm["filter_output_queue"].get(
                                        timeout=5
                                    )["acceptable"]
                                except queue.Empty:
                                    print(
                                        "[MC] CHAT MSG "
                                        + chat_msg
                                        + " NOT SENT: FILTER TIMEOUT"
                                    )
                                except Exception as e:
                                    print(
                                        "[MC] CHAT MSG "
                                        + str(chat_msg)
                                        + " NOT SENT: FILTER ERROR",
                                        e,
                                    )
                                    traceback.print_exc()
                            else:
                                acceptable = True
                            print(
                                "[DEBUG SRV CHAT MSG] ",
                                ctx_game.get("server", ""),
                                "<---",
                            )
                            if acceptable:
                                need_global_chat = any(
                                    [
                                        srv in ctx_game.get("server", "")
                                        for srv in ["musteryworld"]
                                    ]
                                )
                                # ctx_game.get("server", "") in [
                                #    "mc.musteryworld.net"
                                # ]  # for bedwars / other team modes
                                if need_global_chat:
                                    chat_msg = "!" + chat_msg
                                # TODO find another workaround for global chat
                                # potentially DANGEROUS if SYMBOL LIMIT exceeds
                                # not precize, need precize options for another modes
                                mc.ChatMessage(chat_msg)
                                # print("FJSKG SDJG JFK GHJKFD JHFDJKH")
                                ctx_game["mc_last_chat_interact"] = eztime()
                    else:
                        print("[MC] CHAT MSG EMPTY:" + chat_msg)
                except Exception as err:
                    print(
                        "[mineBridge] error sending msg in MC chat",
                        err,
                    )
                    traceback.print_exc()
                    # message already popped — don't re-raise, just log and continue
                # finally:
                # try:
                #    ctx_chat_queue.pop(0)

            # ctx_chat_queue.pop(0)
        except Exception as err:
            print("VERY BAD CHAT PRINT ERR", err)
            traceback.print_exc()
        except KeyboardInterrupt:
            print("[ Mine Bridge ] MAIN KEYBOARD INTERRUPT")
            raise KeyboardInterrupt


def mine_bridge_handler(ctx_swarm: CtxSwarmType, debug: bool = False):
    internal_chat_msgs = []
    internal_command_queue = []
    internal_audio_queue = []
    internal_events = []
    native_damage = []
    own_chat_list = []
    ctx_chat_queue = ctx_swarm.get("chat_queue", [])
    java_gateway = None
    ctx_env = ctx_swarm["env"]
    ctx_game = ctx_swarm["game"]
    debug = debug
    handler = CtxHandler(ctx_swarm)
    while ctx_env["actived"]:
        main_thread_actived = {"actived": True}
        water_safety = None
        combat_reflex = None
        mod_runtime = None
        try:
            # ctx.lastmsg = "hz"
            print("запуск python callback")

            class PythonCallback(object):
                def isStarted(self):
                    return True

                def onVoiceFeed(self, playerName, voiceBytes):
                    internal_audio_queue.append(
                        (
                            playerName,
                            voiceBytes,
                        )
                    )
                    # print("[MC] GOT AUDIOFEED BYTES FOR "+ str(playerName) + " bytes " + str(voiceBytes))

                def onCaptchaSolveRequest(self, image_bytes):
                    print("GOT CAPTCHA REQUEST!")
                    # requestCaptchaResolve(image_bytes)

                def onUpdateServerInfo(self, infoMas=None):
                    for cho in infoMas:
                        ctx_game[cho] = copy.deepcopy(infoMas[cho])
                        print("GameInfoUpdated", infoMas[cho])

                def onVerifedChat(self, msgMas=None):
                    if msgMas is None:
                        pass
                        # msgMas = {}
                    else:
                        # print("RECIEVED BIG MSG:",msgMas.get("user",""),'>',msgMas.get("msg",""),' CLAN=',msgMas.get("clan","")," NONEXIST=",msgMas.get("gdfjkgdf",""))
                        msgDict = {
                            "user": msgMas.get("user", ""),
                            "msg": msgMas.get("msg", ""),
                        }
                        # pre, rank, user, msg, clan, team, server, serverMode, chat_type, precision
                        fields = [
                            "pre",
                            "rank",
                            "clan",
                            "team",
                            "server",
                            "serverMode",
                            "chat_type",
                            "precision",
                        ]
                        for field in fields:
                            if msgMas.get(field, "") is not None:
                                if msgMas.get(field, "").strip() != "":
                                    msgDict[field] = msgMas.get(field, "")
                        internal_chat_msgs.append(normalize_chat_message(msgDict))
                    return msgMas

                def onChatMessage(self, msg=""):
                    # print(string)
                    # ctx.lastmsg = msg
                    # print('recieved msg >>',msg)
                    return msg

                def onDeath(self, killer="unknown"):
                    if killer is None or not killer.strip() or killer in ("unknown", "неизвестный"):
                        internal_events.append({
                            "type": "death", "user": "", "happiness_score": -3,
                            "isEvent": True, "description": "You died; killer unknown. Check respawn status.",
                            "filter_results": {"acceptable": True},
                        })
                        return
                    if (
                        killer is not None
                        and killer.strip() != ""
                        and killer != "unknown"
                        and killer != "неизвестный"
                    ):
                        event_data = {
                            "type": "death",
                            "user": killer,
                            "happiness_score": -3,
                            "filter_results": {"acceptable": True},
                        }
                        event_data["isEvent"] = True
                        event_data["description"] = " killed you!!! =("
                        internal_events.append(event_data)

                def onKill(self, killed="unknown"):
                    if (
                        killed is not None
                        and killed.strip() != ""
                        and killed != "unknown"
                    ):
                        event_data = {
                            "type": "kill",
                            "user": killed,
                            "happiness_score": 1,
                            "filter_results": {"acceptable": True},
                        }
                        event_data["isEvent"] = True
                        event_data["description"] = " was killed by you =)"
                        internal_events.append(event_data)

                def agentCommandRequest(self, command):
                    if command is not None:
                        internal_command_queue.append(command)
                        return "command sent"
                    return "command is None"

                def onAutoclefEvent(self, event_type, description):
                    try:
                        if (
                            description is not None
                            and description.strip() != ""
                            and description != "unknown"
                            and event_type is not None
                            and event_type.strip() != ""
                            and event_type != "unknown"
                        ):
                            event_data = {
                                "type": event_type,
                                "user": character.get_names().Default,
                                "happiness_score": 0,
                                "filter_results": {"acceptable": True},
                            }
                            event_data["isEvent"] = True
                            event_data["description"] = " " + description  # oh...
                            internal_events.append(event_data)
                    except:
                        pass

                def onDamage(self, amount=0):
                    pass
                    # print('MINECRAFT DAMAGE =',str(amount))

                def onDamageConfirmed(
                    self, damaged="unknown", attacker="unknown", amount=0
                ):
                    try:
                        native_damage.append({"victim": str(damaged), "attacker": str(attacker), "amount": float(amount), "at": time.time()})
                        del native_damage[:-64]
                        description = "damaged by " + attacker + " for " + str(amount)
                        # print("MINECRAFT DAMAGE =", damaged, attacker, amount)
                        if (
                            description is not None
                            and description.strip() != ""
                            and description != "unknown"
                        ):
                            event_data = {
                                "type": "mc_damaged",
                                "user": damaged,
                                "happiness_score": 1,
                                "filter_results": {"acceptable": True},
                            }
                            event_data["isEvent"] = True
                            event_data["description"] = " " + description  # oh...
                            internal_events.append(event_data)
                    except:
                        pass

                class Java:
                    implements = ["adris.altoclef.PythonCallback"]

            cb = PythonCallback()
            java_gateway = connect_minecraft(cb)

            mc = java_gateway.entry_point

            def ingame(loop=True):
                if loop:
                    needLog = False
                    fail = True
                    while fail and ctx_env["actived"]:
                        try:
                            ez = mc.inGame()
                            fail = False
                            if not ctx_game["mc_started"]:
                                ctx_game["mc_started"] = True
                            if needLog:
                                print("BRIDGE CONNECTION SUCCESSFUL")
                            ctx_game["ingame"] = ez
                            return ez
                        except KeyboardInterrupt:
                            print("[ Mine Bridge ] MAIN KEYBOARD INTERRUPT")
                            raise KeyboardInterrupt
                        except BaseException as err:
                            # print('INGAME ERROR', err)
                            if needLog:
                                print("BRIDGE CONNECTION FAILED", err)
                            ctx_game["mc_started"] = False
                            ctx_game["ingame"] = False
                            time.sleep(5)
                            # return False
                        finally:
                            needLog = False  # False
                else:
                    try:
                        ez = mc.inGame()
                        if not ctx_game["mc_started"]:
                            ctx_game["mc_started"] = True
                        # print('CONNECTION SUCCESSFUL')
                        return ez
                    except KeyboardInterrupt:
                        print("[ Mine Bridge ] MAIN KEYBOARD INTERRUPT")
                        raise KeyboardInterrupt
                    except BaseException as err:
                        print(
                            f"ERROR BRIDGE когда чекал запущенный майн. Его видимо нет в списке процессов или ещё че похуже {err}"
                        )
                        ctx_game["mc_started"] = False
                        return False

            print("[minebridge] waiting 1 sec, then ingame check")
            time.sleep(1)
            print("[minebridge] ingame =", ingame())
            try:
                ctx_game["available_commands"] = list(
                    java_gateway.jvm.adris.altoclef.AltoClef.getCommandExecutor().allCommandNames()
                )
            except Exception:
                ctx_game["available_commands"] = []  # Older mods use the verified core commands.
            ctx_swarm["vtube"]["game_yawspeed"] = 0.0
            ctx_swarm["vtube"]["game_pitchspeed"] = 0.0
            AngSpeedTableP = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
            AngSpeedTableY = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
            print("[minebridge] getting initial info dict")

            initial_info_dict = mc.getServerInfoDict()
            for key in initial_info_dict:
                ctx_game[key] = initial_info_dict[key]
                print(
                    "[minebrige] [infoDict] [initial]",
                    key,
                    "->",
                    initial_info_dict[key],
                )

            def updater():
                next_snapshot = 0.0
                while ctx_env["actived"]:
                    time.sleep(max(0.05, float(os.getenv("MINECRAFT_POLL_INTERVAL", "0.1"))))
                    enabled = bool(ctx_env.get("agent_enabled", False))
                    living = respawn.tick(mc, ctx_game, navigation, enabled=(
                        enabled and os.getenv("SARUMAN_AUTO_RESPAWN", "true").lower() in ("true", "1", "yes")))
                    if using_mods(ctx_game):
                        if mod_runtime is not None:
                            mod_runtime.tick(ctx_game, enabled=enabled, tools=ctx_swarm.get("tool_bank"))
                        safe = True
                    else:
                        safe = water_safety.tick(java_gateway, ctx_game, navigation, enabled=enabled)
                    for damage in native_damage[:]:
                        if time.time() - damage["at"] <= 20:
                            try:
                                record_native_damage(java_gateway, ctx_game, damage)
                            except Exception as error:
                                print(f"[COMBAT] Cannot verify attacker: {type(error).__name__}")
                    native_damage[:] = []
                    if not using_mods(ctx_game):
                        combat_reflex.tick(java_gateway, ctx_game, navigation, enabled=enabled)
                    if not living or not safe:
                        continue
                    # print('DEBUG ACTIVED')
                    if len(internal_audio_queue) > 0:
                        for user, audio in internal_audio_queue:
                            # print(
                            #     "[minebridge] Agent command, processing audio",
                            #     audio,
                            # )
                            import base64
                            audio_b64 = base64.b64encode(audio if isinstance(audio, bytes) else bytes(audio)).decode("ascii")
                            ctx_swarm["audio_feed_queue"].put(
                                {"user": user, "audio": audio_b64, "audio_encoding": "base64", "env": "minecraft"}
                            )
                        internal_audio_queue[:] = []
                    if len(internal_command_queue) > 0:
                        for command in internal_command_queue:
                            print(
                                "[minebridge] Agent command, processing command",
                                command,
                            )
                            ctx_swarm["internal_command_queue"].put(
                                {"command": command, "env": "minecraft"}
                            )
                        internal_command_queue[:] = []
                    if len(internal_events) > 0:
                        for event in internal_events:
                            event["date"] = eztime()
                            event["processing_timestamp"] = time.time_ns()
                            event["env"] = "minecraft"
                            # TODO lock
                            # TODO check duplicates

                            # TODO UNTESTED!!!! WARN #1
                            if handler.add_message(event):
                                pass
                                # print(
                                #     f"[{datetime.now().strftime('%H:%M:%S')}] [MC EVENT]",
                                #     str(event),
                                # )
                            else:
                                pass
                                # print(
                                #     f"[{datetime.now().strftime('%H:%M:%S')}] [MC EV DUPE] ",
                                #     str(event.get("type", "none_type")),
                                #     str(event.get("description", "")),
                                # )
                            # ctx_chat.append(event)
                        internal_events[:] = []
                    if len(internal_chat_msgs) > 0:
                        for msg in internal_chat_msgs:
                            # print('Processing msgsmas', msg)
                            msg["date"] = eztime()
                            msg["processing_timestamp"] = time.time_ns()
                            msg["env"] = "minecraft"
                            # pre, rank, user, msg, clan, team, server, serverMode, chat_type, precision
                            username = msg.get("user", "")
                            if not username:
                                username = ""
                            if character.is_character_alias(username):
                                print(
                                    "Встречено собственное сообщение (тоже вносится)",
                                    username,
                                    "вносим в базу",
                                    msg["msg"],
                                )
                                own_chat_list.append(msg)
                                msg["self"] = True
                                ### ctx_chatOwn = ctx_chatOwn + [msg]
                                # ctx_game["mc_last_chat_interact"] = eztime()
                            if using_mods(ctx_game) and username and not msg.get("self"):
                                ctx_game["mod_player_pending_until"] = time.time()+30
                            if True:
                                # else:

                                message_l = msg["msg"].lower()
                                player = username

                                if character.is_user_godmode(player) and not using_mods(ctx_game):
                                    if ingame(loop=False):
                                        try:
                                            if message_l.find("за мной") != -1:
                                                mc.RunInnerCommand(
                                                    f"""@follow {player}"""
                                                )
                                            elif message_l.find("вперед") != -1:
                                                mc.RunInnerCommand("@test killall")
                                            elif message_l.find("мочи") != -1:
                                                mc.RunInnerCommand(
                                                    f"""@punk {msg["msg"].split(' ')[1]}"""
                                                )
                                            elif message_l.find("стоп") != -1:
                                                mc.RunInnerCommand("@stop")
                                        except KeyboardInterrupt:
                                            print(
                                                "[ Mine Bridge ] MAIN KEYBOARD INTERRUPT"
                                            )
                                            raise KeyboardInterrupt
                                        except BaseException as err:
                                            print(
                                                "ERROR WHILE EXEC RAZRAB COMMAND", err
                                            )
                                # TODO UNTESTED!!!! WARN #2
                                if handler.add_message(msg):
                                    print(
                                        f"[{datetime.now().strftime('%H:%M:%S')}] [MC CHAT]",
                                        username,
                                        ">",
                                        msg["msg"],
                                    )
                                else:
                                    print(
                                        f"[{datetime.now().strftime('%H:%M:%S')}] [MC CHAT DUPE] ",
                                        str(msg.get("type", "none_type")),
                                        str(msg.get("description", "")),
                                    )
                                # ctx_chat.append(msg)
                        internal_chat_msgs[:] = []
                    if ingame():
                        # try:
                        #     captchaSolved = captchaQueueOutput.get(block=False)
                        #     if captchaSolved is not None:
                        #         print(
                        #             "[BRIDGE CAPTCHA] GET CAPTCHA OUTPUT ! Entering in chat >>",
                        #             captchaSolved,
                        #             "<<",
                        #         )
                        #         e.CaptchaSolvedSend(
                        #             captchaSolved["result"],
                        #             float(captchaSolved["predict"]),
                        #         )
                        # except queue.Empty:
                        #     pass
                        if time.monotonic() >= next_snapshot:
                            try:

                                state = mc.getState()
                                # print("Old get state", state)
                                # ctx_swarm["voice"]["is_speaking"]
                                # TODO untested
                                if ctx_swarm["voice"].get("speak_entry"):
                                    emotion = ctx_swarm["voice"]["speak_entry"].get(
                                        "emotion", "neutral"
                                    )
                                    mc.setEmotionalState(emotion)
                                g_block = mc.getGroundBlock()
                                g_item = mc.getHeldItem()
                                g_tasks = mc.getTaskChainString()
                                g_self = respawn.self_state
                                g_position = g_self.get("pos", "")
                                g_busy = bool(mc.hasActiveTask())
                                equipment = ctx_game.get("autonomy", {}).get("equipment_job")
                                user_task = read_user_task(java_gateway) if navigation.collection.goal is not None or equipment else None
                                ctx_game["native_user_task"] = user_task
                                ctx_game["native_user_task_seen_at"] = time.time()
                                if not using_mods(ctx_game):
                                    navigation.tick(mc, ctx_game, g_position, g_busy, str(g_tasks), g_self, user_task)
                                g_players = list(mc.getPlayersInfo(10))
                                # TEST save bytes to png
                                # screen_buffer = mc.getScreenshot()
                                # this works only after 14 april 2025 autoclef release
                                # with open('test_screenshot.png', 'wb') as f:
                                #     f.write(screen_buffer)
                                #     print("screen saved to " + str(f.name))
                                # time.sleep(50)
                                # if screen_buffer is not None:
                                #     screen_buffer
                                g_players_pythonic = primitivize_nearby_players(
                                    g_players
                                )
                                ctx_game.update(
                                    {
                                        "task_chain": str(g_tasks),
                                        "ground_block": str(g_block),
                                        "held_item": str(g_item),
                                        "nearby_players": list(
                                            parse_nearby_players(g_players_pythonic)
                                        ),
                                        # ready string version
                                        "near_threats": list(
                                            mc.nearestPlayersInfo(3)
                                        ).copy(),
                                        "last_talking_player": str(
                                            mc.getLastTalkingPlayer()
                                        ),
                                        "hasActiveTask": g_busy,
                                        "position": g_position,
                                    }
                                )
                                # print("Last talking player:", str(mc.getLastTalkingPlayer()))
                                # print(json.dumps([v["distance"] for v in parse_nearby_players(g_players)], indent=2, ensure_ascii=False))
                                # print(json.dumps(parse_nearby_players_dict(g_players), indent=2, ensure_ascii=False))
                                # print(json.dumps([v["distance"] for k, v in parse_nearby_players_dict(g_players).items()], indent=2, ensure_ascii=False))
                            except KeyboardInterrupt:
                                print("[ Mine Bridge ] MAIN KEYBOARD INTERRUPT")
                                raise KeyboardInterrupt
                            except Exception as e:
                                print("EXCEPTION WHEN GETTING MC INFO", e)
                                traceback.print_exc()
                            next_snapshot = time.monotonic() + 0.5
                        if len(own_chat_list) > 80:
                            own_chat_list.pop(0)
                        goalRotation = mc.getGoalRotation()
                        if goalRotation is None:
                            AngSpeedTableP.append(mc.getPitch())
                            AngSpeedTableY.append(mc.getYaw())
                            if len(AngSpeedTableP) > 10:
                                AngSpeedTableP.pop(0)
                            if len(AngSpeedTableY) > 10:
                                AngSpeedTableY.pop(0)

                            ctx_swarm["vtube"]["game_yawspeed"] = (
                                -AngSpeedTableY[5] + AngSpeedTableY[0]
                            )
                            ctx_swarm["vtube"]["game_pitchspeed"] = (
                                -AngSpeedTableP[5] + AngSpeedTableP[0]
                            )
                        else:
                            # print('ыы',e.ChatMessage("ПриветМир"))
                            ctx_swarm["vtube"]["game_yawspeed"] = -goalRotation.getYaw()
                            ctx_swarm["vtube"][
                                "game_pitchspeed"
                            ] = goalRotation.getPitch()

                    else:
                        # print('НЕ В ИГРЕ!!!')
                        time.sleep(2)

            debug_additional_data = {"bow_spotted": 0}
            server_info_kwargs = {"mode": "skywars"}  # {"mode": "survival"}
            player_collecting = "kilbro"

            def debug_listener():
                from data_collectors.experiments.predictorcompetition.collect_mc_data import (
                    create_collector,
                )

                collector = create_collector(os.path.join(REPO_DATA_PATH, "predictors"))
                print("Collecting started.")
                try:
                    ent = mc.getEntity(player_collecting)
                    collector.init_entity(ent)
                except KeyboardInterrupt:
                    print("[ Mine Bridge ] MAIN KEYBOARD INTERRUPT")
                    raise KeyboardInterrupt
                except Exception as e:
                    print("[debug bgridge err],", e)
                    traceback.print_exc()
                while ctx_env["actived"]:
                    time.sleep(0.01)
                    if ingame():
                        # time.sleep(5)

                        # print("debug run")
                        try:
                            ent = mc.getEntity(player_collecting)
                            collector.on_tick(ent, **debug_additional_data)
                            # print("DEBUG GETTING ENTITY", ent)
                            # print(ent.getPos())
                            # print(ent.getHealth())
                        except KeyboardInterrupt:
                            print("[ Mine Bridge ] MAIN KEYBOARD INTERRUPT")
                            raise KeyboardInterrupt
                        except Exception as e:
                            print("[debug bgridge err],", e)
                            traceback.print_exc()
                        # print(e.getPlayersInfo())
                        # print("@test killall")
                        # e.ExecuteCommand("@test killall")
                        # time.sleep(3)
                        # print("стоп")
                        # e.ExecuteCommand("@stop")
                        # time.sleep(1)
                print("Finishing run...")

            print("UPDATER STARTING...")
            navigation = NavigationGuard(rules_factory=lambda: NativeWorldRules(java_gateway))
            respawn = RespawnController(hardcore=lambda: bool(
                java_gateway.jvm.adris.altoclef.AltoClef.getInstance().getWorld().method_8401().method_152()
            ))
            water_safety = WaterSafety()
            combat_reflex = CombatReflex()
            ctx_game["bridge_runtime"] = "water-safety-v2"
            ctx_game["combat_runtime"] = "reflex-v2"
            ctx_game["combat_threats"] = {}
            ctx_game["self_defense"] = {}
            ctx_game["automation_mode"] = os.getenv("SARUMAN_AUTOMATION_MODE", "mods").lower()
            if using_mods(ctx_game):
                ctx_game["bridge_runtime"] = "native-mods-v1"
                ctx_game["combat_runtime"] = "native-mods-v1"
                ctx_game["survival"] = {}
                ctx_game["combat_policy"] = {"owner": "Auto Defense", "player_attacks": False}
                try:
                    ctx_game["combat_policy"] = {**install_native_guard(java_gateway), "owner": "Auto Defense", "player_attacks": False}
                    def native_goal_event(goal):
                        internal_events.append({"type": "system", "user": "", "msg":
                            "Player goal result (inspect before continuing): " + str(goal),
                            "mod_goal_id": goal["id"], "mod_context_id": goal.get("context_id"),
                            "filter_results": {"acceptable": True}})
                    def native_speech(text, emotion):
                        if len(ctx_swarm["tts_queue"]) < 3:
                            ctx_swarm["tts_queue"].append({"text": text, "emotion": emotion})
                    mod_runtime = AutomationRuntime(NativeMods(java_gateway), notify=native_goal_event, server_send=mc.ChatMessage, announce=native_speech)
                    mod_runtime.supply = ModSupply(java_gateway, mod_runtime.store)
                except Exception as error:
                    ctx_game["mod_runtime"] = {"status": "unavailable", "error": str(error)}
                    print(f"[MOD DISPATCH] Adapter unavailable: {error}")
            else:
                ctx_game["combat_policy"] = install_native_guard(java_gateway)
            ctx_game["navigation"] = {}
            ctx_game["collection"] = {}
            updaterThread = threading.Thread(target=updater)
            updaterThread.start()
            chat_queue_thread = threading.Thread(
                target=chat_queue_handler,
                kwargs={
                    "mc": mc,
                    "ctx_swarm": ctx_swarm,
                    "main_thread_actived": main_thread_actived,
                    "navigation": navigation,
                },
            )
            chat_queue_thread.start()
            if debug:
                for i in range(10):
                    print("DEBUG LISTENER STARTING...")
                    ctx_env["actived"] = True
                    listner_thread = threading.Thread(target=debug_listener)
                    listner_thread.start()
                    time.sleep(10)
                    ctx_env["actived"] = False
                    listner_thread.join()
                    print(f"Phase {str(i)} ended. Running next")
            updaterThread.join()
        except Exception as err:
            print("Mine Bridge Произошла большая ошибка >", err)
            print("ТЕКСТ ОШИБКИ", traceback.format_exc())
            time.sleep(5)
        except KeyboardInterrupt:
            print("[ Mine Bridge ] MAIN KEYBOARD INTERRUPT")
            raise KeyboardInterrupt
        finally:
            print(
                "[ Mine Bridge ] Достигнут конец процесса Mine Bridge, перезапускаем его..."
            )
            try:
                main_thread_actived["actived"] = False
                if java_gateway is not None:
                    if mod_runtime is not None:
                        mod_runtime.close()
                    if water_safety is not None:
                        water_safety.close(java_gateway)
                    if combat_reflex is not None:
                        combat_reflex.close(java_gateway)
                    java_gateway.shutdown_callback_server()
                    java_gateway.close()  # The Java gateway belongs to the running mod.
                    time.sleep(1)
                    java_gateway = None
            except KeyboardInterrupt:
                print("[ Mine Bridge ] MAIN KEYBOARD INTERRUPT>>")
                raise KeyboardInterrupt
            except BaseException as err:
                print(f"MineBridge: не удалось завершить gateway {err}")
                time.sleep(1)


if __name__ == "__main__":
    logger.setLevel(logging.DEBUG)
    manager = multiprocessing.Manager()
    ctx_swarm = create_ctx_swarm(manager=manager)

    ctx_swarm["chat_queue"].extend(
        ["/randomcommand123", "привет", "пвалопрова дловалор"]
    )
    print(ctx_swarm)
    mine_bridge_handler(ctx_swarm)
    # mine_bridge_handler(ctx_swarm, True)  # for collecting data now
