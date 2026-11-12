"""Private durable action sequences. Only observed completion advances the next step."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import threading

OPERATIONS=('connect','game_task','screenshot','draft','publish')
STEP_SCHEMA={'type':'object','additionalProperties':False,'required':['operation','text'],
             'properties':{'operation':{'type':'string','enum':list(OPERATIONS)},'text':{'type':'string'}}}


class Workflows:
    def __init__(self,project,store,runtime,draft,capture=None):
        self.project,self.store,self.runtime,self.draft=Path(project),store,runtime,draft
        self.capture=capture or self.capture_game
        self.lock=threading.Lock();self.closed=threading.Event();self.thread=None
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY,body TEXT NOT NULL,created REAL NOT NULL)')

    def create(self,identifier,steps,original):
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
            if op=='connect':
                if text and text not in original:raise ValueError('Имя мира должно быть указано в команде')
                item['configuration']=self.runtime.connection.configuration(world=text or None)
            if op=='screenshot':photo_pending=True
            if op=='draft':has_draft=True
            if op=='publish':
                if not has_draft:raise ValueError('Перед публикацией нужен этап подготовки поста')
                has_draft=False;photo_pending=False
            prepared.append(item)
        if photo_pending and any(s['operation']=='publish' for s in prepared):
            raise ValueError('Скриншот должен предшествовать своему посту')
        value={'id':identifier,'status':'queued','steps':prepared,'detail':'Цепочка сохранена'}
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

    def context(self):
        with self.store.db() as db:rows=db.execute('SELECT body FROM workflows ORDER BY created').fetchall()
        values=[json.loads(row[0]) for row in rows]
        return [{'id':v['id'],'status':v['status'],'steps':[
            {'operation':s['operation'],'text':s['text'],'status':s['status'],'observed_status':s['result'].get('status')}
            for s in v['steps']]} for v in values if v['status'] in {'queued','running'}]

    def resume(self,identifier):
        value=self.get(identifier)
        if not value or value['status'] not in {'queued','running','waiting_start'}:
            raise ValueError('Эта цепочка не ожидает продолжения. Повторное выполнение требует нового поручения.')
        self.start()
        return {'workflow_id':identifier,'detail':'Продолжается сохранённая цепочка; выполненные этапы не повторяются.'}

    def save(self,value):
        with self.store.db() as db:db.execute('UPDATE workflows SET body=? WHERE id=?',(json.dumps(value,ensure_ascii=False),value['id']))

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
            value=next((v for r in rows if (v:=json.loads(r[0]))['status'] in {'queued','running'}),None)
            if not value:return
            index=next((i for i,s in enumerate(value['steps']) if s['status']!='completed'),None)
            if index is None:
                value.update(status='completed',detail='Все этапы подтверждены');self.save(value);return
            step=value['steps'][index];value['status']='running'
            step['status']='running';value['detail']='Выполняется этап '+str(index+1)+'/'+str(len(value['steps']))
            self.save(value)  # Reserve the exact ID before any external action.
            try:
                complete,result=self.run(value,index)
                step['result']=result
                if complete:step['status']='completed'
                elif result.get('status') in {'failed','error','unknown','unresolved','cancelled','uncertain','blocked','needs_world_name','rejected','unavailable','conversation','refused','clarify'}:
                    step['status']='failed';value.update(status='failed',detail=result.get('reason') or result.get('error') or 'Этап не подтверждён; последующие действия не запускались')
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
            with self.runtime.lock:
                if not self.runtime.active:return False,{'status':'waiting_start'}
                connection=self.runtime.connection
                if not step.get('submitted') or connection.last_request_id!=identifier or connection.value.get('status')=='stopped':
                    connection.request(identifier=identifier,configuration=step['configuration'])
                    step['submitted']=True
                result=dict(connection.value)
            return result.get('status')=='running',result
        if op=='game_task':
            result=self.runtime.game.read('results/'+identifier+'.json')
            if result.get('command_id')!=identifier:result={}
            if not result:
                with self.runtime.lock:
                    if not self.runtime.active:return False,{'status':'waiting_start'}
                    self.runtime.game.directive={'id':identifier,'text':step['text']};self.runtime.game.heartbeat()
                return False,{'status':'waiting_game','detail':'Ожидание результата игрового агента'}
            if result.get('status')=='completed' and not result.get('facts'):
                return False,{'status':'unresolved','reason':'Игровой агент не приложил данные результата'}
            if result.get('request_state') in {'finished','error','superseded','cancelled'} and result.get('status')!='completed':
                return False,{**result,'status':'unresolved','reason':'Игровое поручение завершено без подтверждения успеха'}
            return result.get('status')=='completed',result
        if op=='screenshot':return True,self.capture(identifier)
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
            media=next((s['result'] for s in reversed(previous) if s['operation']=='screenshot'),None)
            output=self.draft({'topic':step['text'],'confirmed_game_results':facts,'screenshot_attached':bool(media),
                               'caption_limit':1024 if media else 3500})
            if not self.runtime.active:return False,{'status':'waiting_start'}
            self.store.workflow_post(post_id,output.get('text'),media)
            step['post_id']=post_id
            return True,{'post_id':post_id}
        draft=next(s for s in reversed(previous) if s['operation']=='draft')
        post_id=draft['result']['post_id']
        with self.store.db() as db:row=db.execute('SELECT * FROM posts WHERE id=?',(post_id,)).fetchone()
        if row['status']=='draft':
            with self.runtime.lock:
                if not self.runtime.active:return False,{'status':'waiting_start'}
                self.store.action(post_id,'queue')
        result=self.store.publication_result(row)
        if row['status']=='draft':result['status']='queued'
        if not self.store.settings()['enabled'] and row['status'] in {'draft','queued'}:
            result['detail']='Пост сохранён; включите Telegram для доставки'
        if row['error']=='gateway_upgrade_required':result['detail']='Обновите Telegram-шлюз до API v4 для отправки фото'
        return row['status']=='sent',result

    def capture_game(self,identifier):
        game=self.project/'integrations/saruman'
        argv=[str(game/'.venv/Scripts/python.exe'),'-m','saruman.screenshot',identifier]
        result=subprocess.run(argv,cwd=game,env={**os.environ,'PYTHONPATH':str(game/'src'),'PYTHONIOENCODING':'utf-8'},
                              stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=90,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:value=json.loads(result.stdout)
        except ValueError:raise RuntimeError('Не удалось прочитать результат снимка') from None
        if value.get('status')!='completed':raise RuntimeError('Снимок Minecraft не получен: '+value.get('reason','ошибка'))
        return self.store.save_photo((self.project/'.saruman/runtime/captures'/(identifier+'.png')).read_bytes())
