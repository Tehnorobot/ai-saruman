"""Real gateway/SQLite dialogue contracts with isolated network/model doubles."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

folder=Path(__file__).resolve().parent
if "_saruman_telegram" not in sys.modules:
    spec=importlib.util.spec_from_file_location("_saruman_telegram",folder/"__init__.py",submodule_search_locations=[str(folder)])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
from _saruman_telegram.automation import Store
from _saruman_telegram.conversations import Conversations
from _saruman_telegram.gateway import Publisher,Settings,TelegramError


def message(identifier=1,user=7,chat=None,thread=None,text="Привет"):
    value={"update_id":identifier,"message":{"message_id":identifier+100,"from":{"id":user,"first_name":"User","is_bot":False},
        "chat":{"id":user if chat is None else chat,"type":"private" if chat is None else "supergroup"},"text":text}}
    if thread is not None:value["message"]["message_thread_id"]=thread
    return value


class ConversationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=1000;self.store=Store(Path(self.temp.name)/"local.sqlite",clock=lambda:self.now)
        self.store.configure({"enabled":True,"reply_enabled":True});self.owner()
        self.telegram=Mock();self.sends=[]
        self.updates=[message()]
        def call(method,payload):
            if method=="getChat":return {"id":-100123,"type":"channel","linked_chat_id":-100456}
            if method=="getUpdates":return self.updates
            if method=="sendMessage":self.sends.append(payload);return {"message_id":500}
            raise AssertionError(method)
        self.telegram.call.side_effect=call
        self.publisher=Publisher(Settings("token","-100123","key",Path(self.temp.name)/"gateway.sqlite"),self.telegram)
        self.control=Mock()
        self.control.config.return_value=({},"http://127.0.0.1:8787","key")
        def transport(url,key,method,path,payload=None):
            if method=="GET":
                value=self.publisher.get(path.rsplit('/',1)[1]);return (200,value) if value else (404,{})
            if path=="/v1/replies":return self.publisher.reply(payload)
            if path.endswith('/retry'):return self.publisher.retry(path.split('/')[-2])
            raise AssertionError(path)
        self.control.transport.side_effect=transport
        self.generate=Mock(side_effect=lambda p:{"text":"Слушаю тебя.","memory":"Поздоровался."})
        self.worker=Conversations(self.store,self.generate,self.control)

    def owner(self,active=True,epoch=1):
        self.store.set_meta("character_runtime",{"active":active,"updated":self.now,"owner":"lab","epoch":epoch})

    def ingest(self):
        values=self.publisher.updates(0)["updates"]
        with self.store.db() as db:
            for u in values:db.execute("INSERT OR IGNORE INTO inbox VALUES (?,?,?)",(u["update_id"],json.dumps(u),self.now))

    def test_private_reply_reaches_only_the_verified_sender_and_deduplicates(self):
        self.ingest();self.worker.step();self.worker.step()
        self.assertEqual(len(self.sends),1)
        self.assertEqual(self.sends[0]["chat_id"],7)
        self.assertEqual(self.sends[0]["reply_parameters"]["message_id"],101)
        self.assertEqual(self.worker.snapshot()["messages"][0]["status"],"sent")
        self.assertEqual(self.generate.call_count,1)

    def test_unknown_update_arbitrary_chat_and_reserved_reply_id_rejected(self):
        for payload in ({"update_id":999,"text":"x"},{"update_id":1,"text":"x","chat_id":999}):
            with self.assertRaises(ValueError):self.publisher.reply(payload)
        with self.assertRaises(ValueError):self.publisher.publish({"publication_id":"reply-1","text":"x"})
        self.assertEqual(self.sends,[])

    def test_discussion_keeps_thread_and_private_memory_cannot_leak(self):
        self.ingest();self.worker.step()
        self.updates=[message(2,chat=-100456,thread=77)]
        self.ingest();self.worker.step()
        payload=self.generate.call_args.args[0]
        self.assertEqual(payload["person"]["memory"],"")
        self.assertEqual(payload["history"],[])
        self.assertEqual(self.sends[-1]["chat_id"],-100456)
        self.assertEqual(self.sends[-1]["message_thread_id"],77)
        self.assertEqual(len(self.worker.snapshot()["people"]),2)

    def test_unrelated_groups_and_bots_cannot_register_reply_targets(self):
        bot=message(3);bot["message"]["from"]["is_bot"]=True
        self.updates=[message(2,chat=-999),bot]
        self.ingest();self.worker.step()
        self.assertEqual(self.sends,[]);self.generate.assert_not_called()

    def test_same_user_new_username_retains_scoped_memory(self):
        self.ingest();self.worker.step()
        self.updates=[message(2)];self.updates[0]["message"]["from"]["first_name"]="Renamed"
        self.ingest();self.worker.step()
        payload=self.generate.call_args.args[0]
        self.assertEqual(payload["person"]["memory"],"Поздоровался.")
        self.assertEqual(payload["person"]["name"],"Renamed")
        self.assertEqual(len(payload["history"]),1)

    def test_stop_during_generation_preserves_reply_without_sending(self):
        def generate(payload):self.owner(False,2);return {"text":"Привет.","memory":"Новый знакомый."}
        self.generate.side_effect=generate;self.ingest();self.worker.step()
        self.assertEqual(self.sends,[])
        self.owner(True,3);self.worker.step()
        self.assertEqual(len(self.sends),1);self.assertEqual(self.generate.call_count,1)

    def test_lost_send_response_is_reconciled_without_a_duplicate(self):
        real=self.control.transport.side_effect;lost=[False]
        def transport(url,key,method,path,payload=None):
            result=real(url,key,method,path,payload)
            if method=="POST" and not lost[0]:lost[0]=True;raise OSError("reply lost")
            return result
        self.control.transport.side_effect=transport;self.ingest()
        with self.assertRaises(OSError):self.worker.step()
        self.worker.step()
        self.assertEqual(len(self.sends),1);self.assertEqual(self.generate.call_count,1)

    def test_gateway_restart_retains_incoming_before_offset_acknowledgement(self):
        self.publisher.updates(0)
        self.updates=[]
        self.publisher=Publisher(self.publisher.settings,self.telegram)
        result=self.publisher.updates(0)
        self.assertEqual(result["updates"][0]["update_id"],1)
        self.assertEqual(self.publisher.reply({"update_id":1,"text":"Reply"})[1]["status"],"sent")

if __name__=="__main__":unittest.main()
