"""Private durable action sequences. Only observed completion advances the next step."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import re
import subprocess
import threading

from .game_connection import matches_target
from .runtime import stale_native_snapshot

OPERATIONS=('connect','game_task','adapter_task','screenshot','draft','publish')
ACTIVE_STATES = {'queued', 'running', 'waiting'}
RESUMABLE_STATES = ACTIVE_STATES | {'paused'}
STEP_SCHEMA={'type':'object','additionalProperties':False,'required':['operation','text'],
             'properties':{'operation':{'type':'string','enum':list(OPERATIONS)},'text':{'type':'string'}}}


class Workflows:
    def __init__(self,project,store,runtime,draft,capture=None):
        self.project,self.store,self.runtime,self.draft=Path(project),store,runtime,draft
        self.capture=capture or self.capture_game
        self.admin = None
        self.lock=threading.Lock();self.closed=threading.Event();self.thread=None
        self.cancellation_pending = False
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY,body TEXT NOT NULL,created REAL NOT NULL)')

    def create(self,identifier,steps,original,*,context_id=None,world_session=None):
        if not isinstance(steps,list) or not steps:raise ValueError('Нужен список действий')
        prepared=[];has_draft=False;photo_pending=False
        for i,step in enumerate(steps):
            if (not isinstance(step,dict) or set(step)!={'operation','text'} or step['operation'] not in OPERATIONS
                    or not isinstance(step['text'],str) or len(step['text'])>12000):raise ValueError('Некорректный этап')
            op,text=step['operation'],step['text'].strip()
            if op=='game_task' and not text:raise ValueError('Укажите игровое действие')
            if op in {'screenshot','publish'} and text:raise ValueError('Для снимка и публикации не нужны путь или адрес')
            item={**step,'text':text,'id':hashlib.sha256((identifier+':'+str(i)).encode()).hexdigest(),
                  'status':'pending','result':{}}
            if op in {'game_task','adapter_task'} and self.admin and self.admin.adapters:
                adapter = self.admin.adapters.chat_target(text)
                if adapter:
                    literal = re.search(re.escape(text), original, re.I)
                    if not literal:
                        raise ValueError('Поручение адаптеру должно дословно входить в команду')
                    if not self.admin.adapters.state(adapter)[0]:
                        raise ValueError('Включите ' + self.admin.adapters.entry(adapter).title + ' в Lab')
                    item.update(operation='adapter_task', adapter=adapter, text=literal.group())
            if op == 'adapter_task' and 'adapter' not in item:
                raise ValueError('Для этого этапа не найден подходящий адаптер')
            if op=='connect':
                if text and text not in original:raise ValueError('Имя мира должно быть указано в команде')
                if text and re.search(re.escape(text)+r'\s*\(\d+\)', original):
                    raise ValueError('Укажите полное имя папки мира вместе с номером')
                item['configuration']=self.runtime.connection.configuration(world=text or None)
            if op=='screenshot':photo_pending=True
            if op=='draft':has_draft=True
            if op=='publish':
                if not has_draft:raise ValueError('Перед публикацией нужен этап подготовки поста')
                has_draft=False;photo_pending=False
            prepared.append(item)
        if photo_pending and any(s['operation']=='publish' for s in prepared):
            raise ValueError('Скриншот должен предшествовать своему посту')
        adapter_steps = [i for i, step in enumerate(prepared) if step['operation']=='adapter_task']
        if adapter_steps and any(step['operation']=='publish' for step in prepared):
            publish_words = (r'\b(?:о?публикуй(?:те)?|о?публиковать|выложи(?:те)?|выложить|'
                             r'запости(?:те)?|запостить|отправь(?:те)?|отправить|publish|post)\b')
            no_publication = r'\bбез публикации\b|\bне\s+(?:(?:надо|нужно|стоит)\s+)?'+publish_words
            if not re.search(publish_words, original, re.I) or re.search(no_publication, original, re.I):
                raise ValueError('Для публикации требуется явная команда администратора')
            if any(step['operation'] in {'draft','publish'} for step in prepared[:adapter_steps[-1]]):
                raise ValueError('Отчёт о стройке следует после подтверждения работы адаптера')
            if any(step['operation']=='screenshot' for step in prepared) and not any(
                step['operation']=='screenshot' for step in prepared[adapter_steps[-1]+1:]
            ):
                raise ValueError('Снимок готового здания следует после подтверждения работы адаптера')
        value={'id':identifier,'status':'queued','steps':prepared,'detail':'Цепочка сохранена'}
        if context_id:value['context_id']=context_id
        if world_session:value['world_session']=world_session
        with self.runtime.lock:
            self.flush_cancellation()
            with self.store.db() as db:
                db.execute('INSERT OR IGNORE INTO workflows VALUES (?,?,?)',(identifier,json.dumps(value,ensure_ascii=False),self.store.clock()))
        self.start()
        return self.get(identifier)

    def get(self,identifier):
        with self.store.db() as db:row=db.execute('SELECT body FROM workflows WHERE id=?',(identifier,)).fetchone()
        if not row:return None
        value=json.loads(row[0])
        if not self.runtime.active and value['status'] in {'queued','running'}:
            value={**value,'status':'waiting_start','detail':'Цепочка сохранена; нажмите Start для продолжения'}
        return value

    def object_metadata(self, adapter):
        objects = {}
        with self.store.db() as db:
            rows = db.execute('SELECT body,created FROM workflows ORDER BY created').fetchall()
        for row in rows:
            run, world = None, None
            for step in json.loads(row[0])['steps']:
                operation, result = step['operation'], step['result']
                if operation == 'connect':
                    world, run = result.get('target'), None
                elif operation in {'game_task', 'adapter_task'}:
                    run = result.get('run_id') if step.get('adapter') == adapter else None
                    if run:
                        objects.setdefault(run, {'created_at': row[1], 'world_name': world, 'prompt': step['text']})
                elif operation == 'screenshot' and run and step['status'] == 'completed' and result.get('sha256'):
                    objects[run]['photo'] = result['sha256']
                if step['status'] != 'completed':
                    break
        return objects

    def context(self):
        with self.store.db() as db:rows=db.execute('SELECT body FROM workflows ORDER BY created').fetchall()
        values=[json.loads(row[0]) for row in rows]
        return [{'id':v['id'],'status':v['status'],'steps':[
            {'operation':s['operation'],'text':s['text'],'status':s['status'],'observed_status':s['result'].get('status')}
            for s in v['steps']]} for v in values if v['status'] in RESUMABLE_STATES]

    def resume(self,identifier):
        value=self.get(identifier)
        if not value or value['status'] not in RESUMABLE_STATES | {'waiting_start'}:
            raise ValueError('Эта цепочка не ожидает продолжения. Повторное выполнение требует нового поручения.')
        detail = 'Продолжается сохранённая цепочка; выполненные этапы не повторяются.'
        if value['status'] == 'paused':
            index = next(i for i, step in enumerate(value['steps']) if step['status'] != 'completed')
            step = value['steps'][index]
            connect = next(s for s in reversed(value['steps'][:index]) if s['operation'] == 'connect')
            attempt = connect.get('attempt', 0) + 1
            connect.update(attempt=attempt, connection_request=f"{connect['id']}-{attempt}",
                           status='pending', result={}, submitted=False)
            step.update(status='pending', result={})
            value.pop('world_session', None)
            detail = 'Продолжение подтверждено. Запрашивается вход в мир.'
            value.update(status='queued', detail=detail)
            if not self.save(value):
                raise ValueError('Поручение уже отменено.')
        self.start()
        return {'workflow_id':identifier,'detail':detail}

    def save(self,value):
        # An in-flight step may finish after Stop. It cannot restore a cancelled workflow.
        with self.store.db() as db:
            changed = db.execute("UPDATE workflows SET body=? WHERE id=? AND json_extract(body, '$.status') <> 'cancelled'",
                                 (json.dumps(value, ensure_ascii=False), value['id'])).rowcount
        if changed:
            self.record_checkpoint(value)
        return bool(changed)

    def cancel_pending(self):
        self.cancellation_pending = True
        self.flush_cancellation()

    def flush_cancellation(self):
        if not self.cancellation_pending:
            return
        cancelled = []
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT body FROM workflows WHERE json_extract(body, '$.status') IN ('queued','running','waiting','paused')").fetchall()
            for row in rows:
                value = json.loads(row[0])
                value.update(status='cancelled', detail='Поручение отменено оператором.')
                for step in value['steps']:
                    if step['status'] != 'completed':
                        step.update(status='cancelled', result={**step['result'], 'status':'cancelled', 'detail':value['detail']})
                db.execute('UPDATE workflows SET body=? WHERE id=?', (json.dumps(value, ensure_ascii=False), value['id']))
                cancelled.append(value)
        self.cancellation_pending = False
        for value in cancelled:
            self.record_checkpoint(value)

    def record_checkpoint(self, value):
        observer=getattr(self.runtime,'observer',None)
        if observer:
            observer.journal.record('workflow.checkpoint', status=value['status'], trace=value.get('context_id') or value['id'],
                details=value, key='workflow:'+value['id']+':'+hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest())

    def can_dispatch(self, identifier):
        current = self.get(identifier)
        return self.runtime.active and current is not None and current['status'] in ACTIVE_STATES

    def start(self):
        if self.thread:return
        def loop():
            while not self.closed.is_set():
                try:self.step()
                except (OSError,ValueError,RuntimeError,sqlite3.Error):pass  # Retry a temporary checkpoint failure with the same step ID.
                self.closed.wait(1)
        self.thread=threading.Thread(target=loop,name='private-workflows',daemon=True);self.thread.start()

    def close(self):
        self.closed.set()
        if self.thread:self.thread.join(3)

    def step(self):
        if not self.runtime.active or self.closed.is_set() or not self.lock.acquire(False):return
        try:
            with self.store.db() as db:rows=db.execute('SELECT body FROM workflows ORDER BY created,id').fetchall()
            values=[json.loads(row[0]) for row in rows]
            value=next((v for v in values if v['status'] in {'queued','running'}),None)
            if value is None:
                waiting=[v for v in values if v['status']=='waiting']
                if waiting:
                    value=min(waiting,key=lambda v:v.get('last_observation',0))
            if not value:return
            index=next((i for i,s in enumerate(value['steps']) if s['status']!='completed'),None)
            if index is None:
                value.update(status='completed',detail='Все этапы подтверждены');self.save(value);return
            step=value['steps'][index]
            if step['status']=='pending':
                value['status']=step['status']='running'
                value['detail']='Выполняется этап '+str(index+1)+'/'+str(len(value['steps']))
                if not self.save(value):  # Reserve the exact ID before any external action.
                    return
            try:
                complete,result=self.run(value,index)
                value['status']=step['status']='running'
                step['result']=result
                if complete:step['status']='completed'
                elif result.get('status') == 'cancelled':
                    step['status']='cancelled';value.update(status='cancelled',detail=result.get('detail') or 'Поручение отменено')
                elif result.get('status') == 'world_closed':
                    step['status'] = value['status'] = 'paused'
                    value['detail'] = result['detail']
                elif result.get('status') in {'failed','error','unknown','unresolved','uncertain','blocked','needs_world_name','rejected','unavailable','conversation','refused','clarify','needs_input'}:
                    step['status']='failed';value.update(status='failed',detail=result.get('reason') or result.get('error') or result.get('detail') or 'Этап не подтверждён; последующие действия не запускались')
                elif result.get('status') in {'waiting_world','waiting_start','paused','waiting_adapter','waiting_services'}:
                    step['status']=value['status']='waiting'
                    value['last_observation']=self.store.clock()
                    value['detail']=result.get('detail') or result.get('reason') or 'Ожидание продолжения'
                elif result.get('detail'):
                    value.update(detail=result['detail'])
                self.save(value)
            except sqlite3.Error:
                # The reserved checkpoint and idempotency keys are already durable.
                # Observe them on the next iteration; do not repeat a confirmed action.
                raise
            except Exception as error:
                step.update(status='failed',result={'error':str(error)})
                value.update(status='failed',detail='Этап '+str(index+1)+': '+str(error));self.save(value)
        finally:self.lock.release()

    def run(self,value,index):
        step=value['steps'][index];op=step['operation'];identifier=step['id']
        previous=value['steps'][:index]
        if op=='connect':
            identifier = step.get('connection_request', identifier)
            with self.runtime.lock:
                if not self.runtime.active:return False,{'status':'waiting_start'}
                if not self.can_dispatch(value['id']):return False,{'status':'cancelled'}
                connection=self.runtime.connection
                if not step.get('submitted') or connection.last_request_id!=identifier or connection.value.get('status')=='stopped':
                    if connection.identifier and connection.identifier != identifier:
                        return False, {'status':'waiting_world','detail':'Ожидается завершение предыдущего входа в Minecraft'}
                    connection.request(identifier=identifier,configuration=step['configuration'])
                    step['submitted']=True
                result=dict(connection.value)
            if result.get('status') == 'running' and result.get('request_id') == identifier and matches_target(result, step['configuration']):
                return True, result
            if result.get('status') in {'error','needs_world_name'}:
                return False, result
            return False, {**result, 'status':'waiting_world',
                           'detail':'Ожидается подтверждение входа в «'+step['configuration']['target']+'»'}
        if op=='game_task':
            result=self.runtime.game.read('results/'+identifier+'.json')
            if result.get('command_id')!=identifier:result={}
            if not result:
                with self.runtime.lock:
                    if not self.runtime.active:return False,{'status':'waiting_start'}
                    if not self.can_dispatch(value['id']):return False,{'status':'cancelled'}
                    directive = self.runtime.game.directive
                    if not isinstance(directive, dict) or directive.get('id') != identifier:
                        self.runtime.game.directive={'id':identifier,'text':step['text'],'priority_until':self.store.clock()+120}
                    self.runtime.game.heartbeat()
                return False,{'status':'waiting_game','detail':'Ожидание результата игрового агента'}
            if result.get('status')=='completed' and not result.get('facts'):
                return False,{'status':'unresolved','reason':'Игровой агент не приложил данные результата'}
            if result.get('status')!='completed':
                facts=result.get('facts',{})
                failed=[g.get('reason') for g in facts.get('goals',[]) if g.get('state') in {'failed','cancelled','uncertain'} and g.get('reason')]
                reason=result.get('reason') or '; '.join(failed) or facts.get('reason') or facts.get('detail')
                if reason:result={**result,'reason':reason}
                if result.get('request_state') in {'finished','error','superseded','cancelled'}:
                    return False,{**result,'status':'unresolved','reason':reason or 'Игровое поручение завершено без подтверждения успеха'}
            return result.get('status')=='completed',result
        if op == 'adapter_task':
            return self.run_adapter(value, step, previous)
        if op=='screenshot':
            if not self.can_dispatch(value['id']):return False,{'status':'waiting_start'}
            if value.get('world_session'):
                state=self.runtime.game.read('status.json')
                if not state.get('world_ready'):return False,{'status':'waiting_world'}
                if state.get('world_session')!=value['world_session']:
                    return False,{'status':'failed','reason':'Мир изменился до снимка'}
            source = next((item for item in reversed(previous)
                           if item['operation'] in {'connect', 'game_task', 'adapter_task'}), {})
            if source.get('adapter') == 'mage_home' and source.get('operation') == 'adapter_task':
                result = source['result']
                adapters = self.admin.adapters
                with adapters.entry('mage_home').lock:
                    focus = adapters.instance('mage_home').photo_subject(result.get('run_id'), result.get('world_uid'))
                photo = self.capture(identifier, focus=focus)
            else:
                photo = self.capture(identifier)
            if value.get('world_session') and self.runtime.game.read('status.json').get('world_session')!=value['world_session']:
                return False,{'status':'failed','reason':'Мир изменился во время снимка'}
            return True,photo
        if op=='draft':
            post_id='chain-'+identifier
            with self.store.db() as db:row=db.execute('SELECT id FROM posts WHERE id=?',(post_id,)).fetchone()
            if row:return True,{'post_id':row['id']}
            # Never pass the private command or full goal history into the public writer.
            facts=[]
            for done in previous:
                if done['operation']=='game_task':
                    goals=done['result'].get('facts',{}).get('goals',[])
                    facts.extend({'kind':g.get('kind'),'completed':g.get('completed'),'state':g.get('state')} for g in goals)
                elif done['operation']=='adapter_task':
                    facts.append({'kind':done['adapter'],'state':'completed','completed':1})
            media=next((s['result'] for s in reversed(previous) if s['operation']=='screenshot'),None)
            output=self.draft({'topic':step['text'],'confirmed_game_results':facts,'screenshot_attached':bool(media),
                               'caption_limit':1024 if media else 3500,'_lab_trace':value.get('context_id') or value['id']})
            with self.runtime.lock:
                if not self.runtime.active:return False,{'status':'waiting_start'}
                if not self.can_dispatch(value['id']):return False,{'status':'cancelled'}
                self.store.workflow_post(post_id,output.get('text'),media)
            step['post_id']=post_id
            return True,{'post_id':post_id}
        draft=next(s for s in reversed(previous) if s['operation']=='draft')
        post_id=draft['result']['post_id']
        with self.store.db() as db:row=db.execute('SELECT * FROM posts WHERE id=?',(post_id,)).fetchone()
        if row['status']=='draft':
            with self.runtime.lock:
                if not self.runtime.active:return False,{'status':'waiting_start'}
                if not self.can_dispatch(value['id']):return False,{'status':'cancelled'}
                self.store.action(post_id,'queue')
        result=self.store.publication_result(row)
        if row['status']=='draft':result['status']='queued'
        if not self.store.settings()['enabled'] and row['status'] in {'draft','queued'}:
            result['detail']='Пост сохранён; включите Telegram для доставки'
        if row['error']=='gateway_upgrade_required':result['detail']='Обновите Telegram-шлюз до API v4 для отправки фото'
        return row['status']=='sent',result

    def run_adapter(self, value, step, previous):
        receipt = self.admin.adapter_receipt(step['id'])
        if receipt and receipt['status'] in {'cancelled','failed','error'}:
            result = json.loads(receipt['result'])
            return False, {**result, 'status':receipt['status']}
        state = self.runtime.game.read('status.json')
        world = state.get('world_session')
        if not self.runtime.active or not self.runtime.game.requested:
            return False, {'status':'waiting_start'}
        game_status = self.runtime.game.value
        if game_status.get('status') == 'restarting':
            return False, {'status':'waiting_adapter','detail':game_status.get('reason') or 'Восстанавливается игровой обработчик'}
        if not state.get('world_ready') or stale_native_snapshot(state, self.store.clock()) or not world:
            waiting_services = game_status.get('status') == 'waiting_services' or (
                game_status.get('status') == 'retrying' and state.get('phase') == 'waiting_services'
            )
            service_reason = (game_status.get('reason') or state.get('reason')) if waiting_services else None
            connect = next((s for s in reversed(previous) if s['operation']=='connect'), None)
            if connect and receipt is None:
                try:
                    caps = self.admin.adapters.instance(step['adapter']).call('capabilities')
                except (OSError, RuntimeError, ValueError):
                    caps = {}
                if caps.get('ui', {}).get('screen') == 'class_442' and not caps.get('world', {}).get('connected'):
                    return False, {'status':'world_closed', 'detail':f'Мир «{connect["configuration"]["target"]}» закрыт. Для повторного входа попросите продолжить это поручение.'}
                actual = caps.get('world', {})
                mode = 'world' if actual.get('name') else 'server'
                identity = {'mode':mode,'target':actual.get('name') or actual.get('server')}
                if actual.get('connected') and matches_target(identity, connect['configuration']):
                    reason = service_reason or state.get('native_error') or 'Игровой обработчик ещё не подтвердил готовность'
                    return False, {'status':'waiting_services' if waiting_services else 'waiting_adapter',
                                   'detail':f'Мир «{identity["target"]}» открыт. {reason}'}
            if waiting_services:
                return False, {'status':'waiting_services','detail':service_reason or 'Ожидаются службы игрового обработчика'}
            detail = ('Ожидается мир «'+connect['configuration']['target']+'» и подтверждение готовности персонажа'
                      if connect else 'Ожидается открытый мир Minecraft и готовность персонажа')
            return False, {'status':'waiting_world','detail':detail}
        expected = value.get('world_session')
        if expected and world != expected:
            world_uid = value.get('world_uid')
            if not world_uid:
                return False, {'status':'failed','reason':'Мир изменился до подтверждения работы адаптера'}
            actual = self.admin.adapters.instance(step['adapter']).call('capabilities').get('world', {})
            if not actual.get('connected') or actual.get('worldUid') != world_uid:
                return False, {'status':'waiting_world','detail':'Ожидается мир сохранённой стройки'}
            value['world_session'] = world
            self.save(value)
        if not expected:
            connect = next((s for s in reversed(previous) if s['operation']=='connect'), None)
            if connect:
                instance = self.admin.adapters.instance(step['adapter'])
                actual = instance.call('capabilities').get('world', {})
                target = connect['configuration']
                if not actual.get('connected'):
                    return False, {'status':'waiting_world','detail':'Minecraft ещё не подтвердил доступ адаптера к миру «'+target['target']+'»'}
                mode = 'server' if actual.get('server') else 'world'
                identity = {'mode':mode,'target':actual.get('name' if mode=='world' else 'server')}
                if not matches_target(identity, target):
                    return False, {'status':'failed','reason':'Клиент открыл другой мир'}
            value['world_session'] = world
            self.save(value)
        if not self.can_dispatch(value['id']):
            return False, {'status':'waiting_start'}
        row = self.admin.execute_adapter(step['adapter'], step['text'], step['id'], world_session=world)
        if row['status'] == 'interpreting':
            return False, {'status':'uncertain','reason':'Адаптер не подтвердил приём ранее отправленного этапа'}
        result = json.loads(row['result'])
        if row['status'] not in {'running','accepted','paused','completed'}:
            return False, {**result,'status':'failed'}
        if not result.get('run_id'):
            return False, {'status':'uncertain','reason':'Адаптер не вернул идентификатор игрового запуска'}
        if result.get('world_uid'):
            if value.get('world_uid') and value['world_uid'] != result['world_uid']:
                return False, {'status':'failed','reason':'Стройка принадлежит другому миру'}
            value['world_uid'] = result['world_uid']
        return row['status']=='completed', result

    def capture_game(self,identifier,*,focus=None):
        game=self.project/'integrations/saruman'
        scripts = 'Scripts/python.exe' if os.name == 'nt' else 'bin/python'
        argv=[str(game/'.venv'/scripts),'-m','saruman.screenshot',identifier]
        if focus:
            # Reuse the exact same framed shot in the listing and in the report.
            key = f"mage-home-frame/1:{focus['project_id']}:{focus['run_id']}"
            identifier = hashlib.sha256(key.encode()).hexdigest()
            argv[-1] = identifier
            argv.extend(('--focus', json.dumps(focus, ensure_ascii=False)))
        result=subprocess.run(argv,cwd=game,env={**os.environ,'PYTHONPATH':str(game/'src'),'PYTHONIOENCODING':'utf-8'},
                              stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=90,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:value=json.loads(result.stdout)
        except ValueError:raise RuntimeError('Не удалось прочитать результат снимка') from None
        if value.get('status')!='completed':raise RuntimeError('Снимок Minecraft не получен: '+value.get('reason','ошибка'))
        return self.store.save_photo((self.project/'.saruman/runtime/captures'/(identifier+'.png')).read_bytes())
