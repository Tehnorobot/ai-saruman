import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from saruman.admin import Admin
from saruman.runtime import CharacterRuntime
from saruman.game_connection import GameConnection,validate
from saruman.telegram import Store
from test_runtime import FakeGame


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'telegram.sqlite')
        self.runtime=CharacterRuntime(self.root,self.store,Mock(),game=FakeGame());self.addCleanup(self.runtime.close)
        self.interpret=Mock(return_value={"operation":"status","text":""})
        self.admin=Admin(self.store,self.runtime,self.interpret)

    def command(self,text='status',identifier='request',**extra):
        return self.admin.execute({"text":text,"request_id":identifier,**extra})

    def test_private_log_and_status_never_become_public_events(self):
        value=self.command('Приватно проверь состояние')
        self.assertEqual(value['status'],'accepted')
        self.assertEqual(self.store.snapshot()['events'],[])
        self.assertEqual(len(self.admin.history()),1)
        self.assertNotIn('messages',self.interpret.call_args.args[0])

    def test_button_bypasses_model_and_request_is_idempotent(self):
        value=self.command(operation='start')
        self.assertTrue(self.runtime.active)
        self.interpret.assert_not_called()
        self.assertEqual(self.command(operation='start')['status'],'accepted')
        self.assertEqual(len(self.admin.history()),1)

    def test_publish_requires_explicit_existing_id_and_never_claims_delivery(self):
        post=self.store.manual('Public text')
        self.interpret.return_value={"operation":"publish","text":post}
        rejected=self.command('Опубликуй что-нибудь')
        self.assertEqual(rejected['status'],'error')
        value=self.command('Опубликуй '+post,'publish')
        self.assertEqual(json.loads(value['result'])['status'],'queued')
        self.assertEqual(self.store.snapshot()['posts'][0]['status'],'queued')

    def test_only_explicit_public_topic_enters_post_events(self):
        self.interpret.return_value={"operation":"draft","text":"Размышление о деревьях"}
        self.command('Секретное поручение: подготовь пост о деревьях')
        events=self.store.snapshot()['events']
        self.assertEqual(events[0]['kind'],'public_topic')
        self.assertNotIn('Секретное',str(events))

    def test_publicly_unknown_operations_and_launcher_arguments_are_rejected(self):
        self.interpret.return_value={"operation":"shell","text":"whoami"}
        self.assertEqual(self.command()['status'],'error')
        self.assertEqual(self.store.snapshot()['events'],[])
        self.assertFalse(self.runtime.active)

    def test_game_command_is_requested_once_and_kept_private(self):
        self.command('посади дерево',operation='game_task',argument='посади дерево')
        self.assertEqual(self.runtime.game.directive['text'],'посади дерево')
        self.assertEqual(self.store.snapshot()['events'],[])


class ConnectionTests(unittest.TestCase):
    def test_target_validation_rejects_paths_and_shell_launcher(self):
        base={"mode":"server","target":"localhost:25565","launcher":"","arguments":[]}
        self.assertEqual(validate(base),base)
        for changes in ({"target":"server;whoami"},{"target":"localhost:99999"},{"mode":"world","target":"../world"},
                        {"mode":"world","target":".."},{"arguments":"--profile"},{"launcher":"powershell.exe"}):
            with self.assertRaises(ValueError):validate({**base,**changes})

    def test_confirmation_is_required_before_reporting_in_world(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'tg.sqlite');now=[1000]
            game=GameConnection(root,store,clock=lambda:now[0])
            game.configure({"mode":"server","target":"localhost:25565","launcher":"","arguments":[]})
            python=root/'integrations/saruman/.venv/Scripts/python.exe';python.parent.mkdir(parents=True);python.touch()
            game.request();self.assertEqual(game.value['status'],'starting')
            probe=Mock();probe.poll.return_value=None
            with patch('saruman.game_connection.subprocess.Popen',return_value=probe) as run,patch.object(game,'_port_open',return_value=True):
                game.step()
                self.assertIn('saruman.connection',run.call_args.args[0])
                self.assertEqual(game.value['status'],'starting')
                probe.poll.return_value=0;probe.communicate.return_value=(b'{"status":"in_world"}',None)
                game.step();self.assertEqual(game.value['status'],'running')
                self.assertEqual(run.call_count,1)

    def test_settings_edit_does_not_retarget_an_active_join(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'tg.sqlite')
            game=GameConnection(root,store)
            game.configure({"mode":"server","target":"first.example","launcher":"","arguments":[]})
            game.request()
            game.configure({"mode":"server","target":"second.example","launcher":"","arguments":[]})
            self.assertEqual(game.selected['target'],'first.example')
            self.assertEqual(store.meta('game_connection')['target'],'second.example')

    def test_cancel_stops_only_its_probe_and_never_closes_the_world(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game=GameConnection(root,Store(root/'tg.sqlite'))
            probe=Mock();probe.poll.return_value=None;game.probe=probe;game.identifier='request'
            game.cancel();probe.terminate.assert_called_once();probe.communicate.assert_called_once()
            self.assertIsNone(game.identifier)

    def test_missing_world_asks_for_exact_folder_and_does_not_retry_or_guess(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game=GameConnection(root,Store(root/'tg.sqlite'))
            game.configure({"mode":"world","target":"Display name","launcher":"","arguments":[]})
            game.request()
            probe=Mock();probe.poll.return_value=0
            probe.communicate.return_value=(json.dumps({"status":"rejected","code":"world_not_found",
                "reason":"Укажите точное имя папки мира в saves."},ensure_ascii=False).encode(),None)
            game.probe=probe
            with patch('saruman.game_connection.subprocess.Popen') as spawn:
                game.step();game.step()
            self.assertEqual(game.value['status'],'needs_world_name')
            self.assertEqual(game.value['code'],'world_not_found')
            self.assertIn('точное имя',game.value['reason'])
            self.assertIsNone(game.identifier)
            self.assertEqual(game.selected['target'],'Display name')
            spawn.assert_not_called()

    def test_world_join_keeps_request_marker_separate_from_cached_classloader(self):
        from saruman.roles import load
        import sys
        from types import SimpleNamespace
        # Lab has no Py4J dependency; the real RPC module is loaded with an injected gateway.
        with patch.dict(sys.modules,{'py4j':SimpleNamespace(),
              'py4j.java_gateway':SimpleNamespace(JavaGateway=Mock(),GatewayParameters=Mock())}):
            module=load('connection')

        class Properties(dict):
            def putIfAbsent(self,key,value):
                old=self.get(key)
                if old is None:self[key]=value
                return old
            def put(self,key,value):
                old=self.get(key);self[key]=value;return old
            def remove(self,key):return self.pop(key,None)

        for status in ('connecting','rejected'):
            properties=Properties();gateway=Mock();loader=Mock()
            gateway.new_array.side_effect=lambda _,length:[None]*length
            gateway.jvm.java.lang.System.getProperties.return_value=properties
            gateway.jvm.java.net.URLClassLoader.newInstance.return_value=loader
            gateway.jvm.java.util.concurrent.CompletableFuture.supplyAsync.return_value.get.return_value={
                'status':status,'code':'world_not_found'}
            gateway.entry_point.inGame.side_effect=[False,True]
            with patch('pathlib.Path.exists',return_value=True):
                result=module.connect('world','Existing','request',gateway=gateway)
            cached=[value for key,value in properties.items() if key.startswith('saruman.world.adapter:')]
            self.assertEqual(cached,[loader])
            self.assertEqual(result['status'],status)
            if status=='connecting':
                self.assertEqual(properties.get('saruman.connect:request'),'submitted')
                self.assertEqual(module.connect('world','Existing','request',gateway=gateway)['status'],'in_world')
            else:
                self.assertNotIn('saruman.connect:request',properties)

    def test_world_loader_compiles_and_only_opens_an_existing_save(self):
        import shutil,subprocess
        if not shutil.which('javac') or not shutil.which('java'):self.skipTest('JDK unavailable')
        source=Path(__file__).resolve().parents[1]/'integrations/saruman/resources/Minecraft/bridge/WorldConnection.java'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'saves/Existing').mkdir(parents=True);(root/'saves/Existing/level.dat').touch()
            (root/'saves/Мир Сарумана').mkdir();(root/'saves/Мир Сарумана/level.dat').touch()
            (root/'saves/Incomplete').mkdir()
            harness=root/'Probe.java'
            harness.write_text('''import java.io.File; import org.saruman.bridge.WorldConnection;
public class Probe {
 public static class Loader {public String opened="";public void method_57784(String n,Runnable r){opened=n;}}
 public static class Client {public Object field_1687;public File field_1697;public Loader loader=new Loader();
  public Object method_41735(){return loader;}}
 static void check(boolean x){if(!x)throw new AssertionError();}
 public static void main(String[] args){Client c=new Client();c.field_1697=new File(args[0]);
  check(new WorldConnection(c,"status","","s").get().get("status").equals("menu"));
  var missing=new WorldConnection(c,"world","Missing","m").get();
  check(missing.get("status").equals("rejected"));
  check(missing.get("code").equals("world_not_found"));
  check(missing.get("reason").toString().contains("точное имя папки"));
  check(c.loader.opened.equals(""));
  check(new WorldConnection(c,"world","Incomplete","invalid-save").get().get("code").equals("world_not_found"));
  check(new WorldConnection(c,"world","Display name","no-guess").get().get("code").equals("world_not_found"));
  check(c.loader.opened.equals(""));
  check(new WorldConnection(c,"world","../escape","bad").get().get("status").equals("rejected"));
  check(new WorldConnection(c,"world","Existing","one").get().get("status").equals("connecting"));
  check(c.loader.opened.equals("Existing"));c.loader.opened="";
  new WorldConnection(c,"world","Existing","one").get();check(c.loader.opened.equals(""));
  check(new WorldConnection(c,"world","Мир Сарумана","russian").get().get("status").equals("connecting"));
  check(c.loader.opened.equals("Мир Сарумана"));c.loader.opened="";
  Client empty=new Client();empty.field_1697=new File(args[0],"another-profile");
  check(new WorldConnection(empty,"world","Existing","missing-saves").get().get("code").equals("world_not_found"));
  check(empty.loader.opened.equals(""));
  c.field_1687=new Object();check(new WorldConnection(c,"world","Existing","two").get().get("status").equals("in_world"));
  check(c.loader.opened.equals(""));System.out.println("OK");
 }}''',encoding='utf-8')
            compiled=subprocess.run(['javac','--release','21','-encoding','UTF-8','-d',str(root),str(source),str(harness)],capture_output=True)
            self.assertEqual(compiled.returncode,0,compiled.stderr.decode('utf-8',errors='replace'))
            run=subprocess.run(['java','-cp',str(root),'Probe',str(root)],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertIn('OK',run.stdout)

if __name__=='__main__':unittest.main()
