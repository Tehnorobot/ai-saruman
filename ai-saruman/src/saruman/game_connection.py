"""Operator-configured launcher and join target. No command text from public agents."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid


def validate(value):
    if not isinstance(value,dict) or set(value)!={"mode","target","launcher","arguments"}:raise ValueError("Некорректные настройки входа")
    if value["mode"] not in {"server","world"}:raise ValueError("Выберите сервер или существующий мир")
    target=value["target"]
    if not isinstance(target,str) or not target or len(target)>100:raise ValueError("Укажите адрес сервера или имя папки мира")
    if value["mode"]=="server" and not re.fullmatch(r"[A-Za-z0-9_.-]+(?::[0-9]{1,5})?",target):raise ValueError("Ожидается host или host:port")
    if value["mode"]=="server" and ':' in target and not 1<=int(target.rsplit(':',1)[1])<=65535:raise ValueError("Неверный порт")
    if value["mode"]=="world" and (target in {".",".."} or any(c in target for c in '/\\:\0')):raise ValueError("Укажите имя папки существующего сохранения")
    launcher=value["launcher"]
    if not isinstance(launcher,str) or (launcher and (not Path(launcher).is_absolute() or not Path(launcher).is_file() or Path(launcher).suffix.lower()!=".exe")):
        raise ValueError("Выберите существующий .exe лаунчера или оставьте поле пустым")
    if not isinstance(value["arguments"],list) or any(not isinstance(x,str) or '\0' in x for x in value["arguments"]):raise ValueError("Аргументы — JSON-массив строк")
    return value


class GameConnection:
    def __init__(self,project,store,clock=time.time):
        self.project,self.store,self.clock=project,store,clock
        self.value={"status":"stopped","reason":"Подключение к миру не запрошено"}
        self.probe=None;self.launcher=None;self.identifier=None;self.last_probe=0;self.deadline=0
        self.selected=None

    def configure(self,value):
        self.store.set_meta("game_connection",validate(value));return value

    def request(self):
        value=validate(self.store.meta("game_connection",{}))
        self.selected={**value,"arguments":list(value["arguments"])}
        if self.probe and self.probe.poll() is None:raise RuntimeError("Проверка подключения ещё выполняется")
        self.identifier=uuid.uuid4().hex;self.deadline=self.clock()+180
        self.value={"status":"starting","reason":"Ожидание Minecraft и подключение к выбранному миру"}
        # Launching is an explicit authenticated operator action, using a saved executable
        # and argument array. No shell, credentials or launch arguments are invented by LLM.
        if value["launcher"] and not self._port_open():
            if not self.launcher or self.launcher.poll() is not None:
                self.launcher=subprocess.Popen([value["launcher"],*value["arguments"]],cwd=str(Path(value["launcher"]).parent),shell=False)
        return self.value

    @staticmethod
    def _port_open():
        import socket
        try:
            with socket.create_connection(("127.0.0.1",25333),timeout=.2):return True
        except OSError:return False

    def cancel(self):
        self.identifier=None
        if self.probe and self.probe.poll() is None:
            self.probe.terminate()
            try:self.probe.communicate(timeout=2)
            except subprocess.TimeoutExpired:self.probe.kill();self.probe.communicate()
        self.probe=None
        self.value={"status":"stopped","reason":"Подключение отменено; открытый мир не закрывается"}

    def step(self):
        if not self.identifier:return
        if self.probe and self.probe.poll() is None:return
        if self.probe:
            out,_=self.probe.communicate();self.probe=None
            try:result=json.loads(out)
            except (ValueError,TypeError):result={"status":"unavailable"}
            if result.get("status")=="in_world":
                self.value={"status":"running","reason":"Minecraft подтвердил открытый мир"};self.identifier=None;return
            if result.get("status") in {"error","rejected","blocked"}:
                self.value={"status":"error","reason":result.get("reason","Ошибка входа")};self.identifier=None;return
        if self.clock()>self.deadline:
            self.value={"status":"waiting_world","reason":"Вход не подтверждён. Проверьте окно Minecraft/лаунчер; новый вход — по вашей команде"};self.identifier=None;return
        if self.clock()-self.last_probe<2:return
        if not self._port_open():
            self.value={"status":"waiting_world","reason":"Ожидание клиента Minecraft с UnionClef; при необходимости запустите игровой профиль в лаунчере"}
            self.last_probe=self.clock();return
        game=self.project/"integrations/saruman"
        python=game/".venv/Scripts/python.exe"
        if not python.is_file():raise RuntimeError("Игровая .venv не подготовлена")
        value=self.selected
        self.last_probe=self.clock()
        self.probe=subprocess.Popen([str(python),"-m","saruman.connection",value["mode"],value["target"],self.identifier],
            cwd=game,env={**os.environ,"PYTHONPATH":str(game/"src"),"PYTHONIOENCODING":"utf-8"},
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
