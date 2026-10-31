import os
import random
import time
import traceback
from datetime import datetime
from typing import Union

from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from data_flow.db.dao.mind_variable import MindVariableDAO
from data_flow.db.dao.user import UserDAO
from data_flow.db.dao.user_dialog import UserDialogDAO
from data_flow.db.dao.user_nickname import UserNicknameDAO
from data_flow.db.models.user import User
from data_flow.db.models.user_nickname import UserNickname
from data_flow.db.models.action_voice_line import ActionVoiceLine
from data_schema.chat_structures import NicknameData, UserData
from data_flow.db.dao import mind_variable
from utils.time_helper import eztime, tm


def clamp(n, smallest, largest):
    return max(smallest, min(n, largest))


_pg_user = os.environ.get("POSTGRES_USER", "saruman")
_pg_password = os.environ.get("POSTGRES_PASSWORD", "")
_pg_host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
_pg_port = os.environ.get("POSTGRES_PORT", "5432")
_pg_db = os.environ.get("POSTGRES_DB", "saruman")
DB_URI = f'postgresql+psycopg2://{_pg_user}:{_pg_password}@{_pg_host}:{_pg_port}/{_pg_db}'
_DB_URI_SAFE = f'postgresql+psycopg2://{_pg_user}:***@{_pg_host}:{_pg_port}/{_pg_db}'

print("[DB] Loading Main Database from " + _DB_URI_SAFE + "...")


class StreamerDatabase:
    def __init__(self, run_migrations: bool = True):
        t_start = time.time()

        # Create engine first (shared for migrations and queries)
        self.engine = create_engine(DB_URI)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        print(f"[DB ⏱️ {time.time()-t_start:.2f}s] Engine created")

        # Run migrations only if needed (check current version first)
        if run_migrations:
            self._run_migrations()

        # Warmup — verify connection works
        try:
            mood = self.get_mood()
            print(f"[DB ⏱️ {time.time()-t_start:.2f}s] WARMUP MOOD = {mood}")
            if mood is None:
                raise Exception("MOOD IS NONE")
        except Exception as err:
            print(f"[DB INIT ERR] Connection failed: {_DB_URI_SAFE} ({type(err).__name__})")
            print("[DB] Attempting re-init...")
            print(f"[DB WARMUP TRY 2] MOOD = {self.get_mood()}")

        print(f"[DB ⏱️ {time.time()-t_start:.2f}s] Database ready")
        self.action_voice_records = self.load_action_voice_lines()

    def load_action_voice_lines(self):
        """Load once at startup. A missing catalogue must not block the game loop."""
        try:
            with self.session_factory() as session:
                return [
                    {"id": row.id, "kind": row.kind, "event": row.event,
                     "text": row.text, "enabled": row.enabled}
                    for row in session.scalars(select(ActionVoiceLine).order_by(ActionVoiceLine.id))
                ]
        except Exception as error:
            print(f"[DB] Action speech catalogue unavailable ({type(error).__name__}); using local phrases")
            return None

    def _run_migrations(self):
        """Run Alembic migrations only when database is behind."""
        t = time.time()
        try:
            from alembic.migration import MigrationContext
            from alembic.script import ScriptDirectory
            from data_schema.structure_templates import REPO_ROOT_PATH

            alembic_cfg = Config(os.path.join(REPO_ROOT_PATH, "alembic.ini"))
            alembic_cfg.set_main_option("sqlalchemy.url", DB_URI)

            # Quick check: is DB already at head?
            script = ScriptDirectory.from_config(alembic_cfg)
            head_rev = script.get_current_head()

            with self.engine.connect() as conn:
                context = MigrationContext.configure(conn)
                current_rev = context.get_current_revision()

            if current_rev == head_rev:
                print(f"[DB MIGRATE ⏱️ {time.time()-t:.2f}s] Already at head ({current_rev}), skipping")
                return

            print(f"[DB MIGRATE] {current_rev} -> {head_rev}, applying...")
            from alembic import command
            command.upgrade(alembic_cfg, "head")

            print(f"[DB MIGRATE ⏱️ {time.time()-t:.2f}s] Migrations applied")

        except Exception as e:
            print(f"[DB MIGRATE ⏱️ {time.time()-t:.2f}s] Migration error: {e}")

    def get_user_id(self, nick: str) -> int:
        with self.session_factory() as session:     
            user_nickname = UserNicknameDAO.get_by_nick(session, nick)
            if user_nickname:
                return user_nickname.user_id
        return None

    def set_mood(self, new_mood: float, mind_id: int = 1) -> bool:
        with self.session_factory() as session:     
            try:
                MindVariableDAO.update(session, mind_id, 
                    mood=new_mood
                )
            except Exception as ex:
                print(ex)
                return False
        return True

    def get_mood(self, mind_id: int = 1) -> float:
        with self.session_factory() as session:
            mind_variable = MindVariableDAO.get_by_id(session, mind_id)
            if mind_variable:
                return mind_variable.mood
            return None

    def get_user_rank(self, user_id: int):
        with self.session_factory() as session:
            user = UserDAO.get_by_id(session, user_id)
            if user:
                return user.rank
            return None

    def get_user_last_interact_time(self, user_id: int, last_answered=False):
        with self.session_factory() as session:
            user = UserDAO.get_by_id(session, user_id)
            if user:
                if last_answered:
                    return user.last_answered
                else:
                    return user.last_interact

    def get_user_last_question_date(self, user_id: int):
        with self.session_factory() as session:
            user = UserDAO.get_by_id(session, user_id)
            if user:
                return user.last_question_date
            return None

    def set_user_last_question(self, user_id: int, question: str) -> bool:
        with self.session_factory() as session:
            data = {
                "last_question_date": datetime.now(),
                "last_question": question
            }
            try:    
                UserDAO.update(session, user_id, **data)  
            except Exception as ex:
                print(ex)
                return False
            return True

    def set_user_last_interact_time(
        self, user_id: int, new_value, last_answered=False
    ):
        with self.session_factory() as session:
            data = {"last_interact": new_value}
            if last_answered:
                data["last_answered"] = new_value
            try:
                UserDAO.update(session, user_id, **data)
            except Exception:
                return False
            return True
            
    def set_user_rank(self, user_id: int, new_rank: float):
        with self.session_factory() as session:
            data = {
                "rank": new_rank,
            }
            try:
                UserDAO.update(session, user_id, **data)
            except Exception:
                return False
            return True

    def add_to_user_rank(self, user_id: int, amount: float):
        with self.session_factory() as session:
            new_rank = UserDAO.add_to_rank(session, user_id, amount)
            return new_rank if new_rank is not None else -1

    def check_user_id_exists(self, user_id: int):
        with self.session_factory() as session:
            return UserDAO.check_user_id_exists(session, user_id)

    def get_or_create_user(
        self, data: dict, return_nick_id_too: bool = False
    ) -> Union[int, dict, None]:
        nick = data["user"]
        if nick.strip():
            user_id = self.get_user_id(nick)
            if user_id is None:
                user_id = self.add_new_user(data)
            if not return_nick_id_too:
                return user_id
            else:
                return {
                    "user_id": user_id,
                    "nick_id": self.get_user_nick_id(nick, data),
                }
        return None

    def get_user_nick_id(
        self, nick: str, data: dict = None    ) -> int:
        with self.session_factory() as session:
            return UserNicknameDAO.get_user_nick_by_data(session, nick, data)

    def set_analyze_for_nick(self, nick_id: int, nick_analyze: str) -> bool:
        with self.session_factory() as session:
            data = {
                "nick_analyze": nick_analyze,
            }
            try:
                UserNicknameDAO.update(session, nick_id, **data)
            except Exception:
                return False
            return True

    def get_nick_analyze(self, nick_id: int, return_bool: bool):
        with self.session_factory() as session:
            user_nickname = UserNicknameDAO.get_by_id(session, nick_id)
            if return_bool:
                return user_nickname is not None and user_nickname.nick_analyze is not None
            if user_nickname:
                return user_nickname.nick_analyze
            return None

    def add_nick_to_user(self, user_id: int, data: dict) -> int:
        with self.session_factory() as session:
        
            nick = data["user"]
            env = data["env"]
            server = data.get("server", None)
            date = datetime.now()
            
            data = {
                'user_id': user_id, 
                'nick': nick,
                'env': env,
                'server': server,
                'other_info': None,
                'added_date': date
            }
            
            unserted = UserNicknameDAO.create(session, **data)
            
            if unserted is not None:
                print(
                    "[DB ADD NEW NICK TO USER ID("
                    + str(user_id)
                    + ") INSERTED "
                    + str(nick)
                    + ". Nick in table ID = ("
                    + str(unserted.nick_id)
                    + ")"
                )
                return unserted.nick_id

    def add_new_user(self, data: dict = None) -> Union[int, None]:
        print("CALLED ADD NEW USER", data)
        if data is None:
            print("[DB EXCEPTION] **** НАДО ПЕРЕДАТЬ ДАННЫЕ В БАЗУ а DATA пуст!")
            return None
        rank = random.choice([3.0, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 4.0])
        try:
            with self.session_factory() as session:
                date = datetime.now()
                name = data["user"]
                env = data.get("env", "minecraft")
                data["env"] = env

                # Создаём User и основной ник атомарно в одной транзакции
                user = User(
                    name=name,
                    rank=rank,
                    firstreg=date,
                    last_interact=date,
                    diags_count=0,
                )
                session.add(user)
                session.flush()  # получаем user_id без коммита

                nick = UserNickname(
                    user_id=user.user_id,
                    nick=name,
                    env=env,
                    server=data.get("server"),
                    other_info=None,
                    added_date=date,
                )
                session.add(nick)
                session.commit()

                user_id = user.user_id
                print(f"[DB ADD NEW USER] INSERTED {name} AT ID {user_id}")
        except IntegrityError:
            # Другой поток уже создал пользователя — возвращаем существующего
            print(f"[DB ADD NEW USER] Race condition for '{data['user']}', fetching existing")
            return self.get_user_id(data["user"])

        # Доп. ники для специфичных env (в отдельных транзакциях — некритично)
        if data.get("env", "") == "discord":
            discord_user_id = data.get("discord_id")
            if discord_user_id:
                discord_data = dict(data)
                discord_data["env"] = "discord_id"
                discord_data["user"] = discord_user_id
                self.add_nick_to_user(user_id=user_id, data=discord_data)
                print(f"[DB ADD DISCORD NICK] Добавлен для ника {name} discord_id")
        elif data.get("env", "") == "youtube":
            youtube_user_id = data.get("youtube_user_channel_id")
            if youtube_user_id:
                youtube_data = dict(data)
                youtube_data["env"] = "youtube_user_channel_id"
                youtube_data["user"] = youtube_user_id
                self.add_nick_to_user(user_id=user_id, data=youtube_data)
                print("[DB ADD YT] Добавлен ID в YT")
        elif data.get("env", "") == "trovo":
            trovo_user_id = data.get("trovo_user_channel_id")
            if trovo_user_id:
                trovo_data = dict(data)
                trovo_data["env"] = "trovo_user_channel_id"
                trovo_data["user"] = str(trovo_user_id)
                self.add_nick_to_user(user_id=user_id, data=trovo_data)
                print("[DB ADD TROVO] Добавлен ID TROVO")

        return user_id

    def add_diags(
        self, user_id: int, diag_to_add: list[dict], data: dict = None
    ) -> list[int]:
        # log = {"user":user, "role": role, "content": content, "date":eztime(), "emotion":emotion, "command":command}
        diag_ids = []
        date = datetime.now()
        if data is None:
            env = None
            server = None
            other_info = None
            bind_to_nick_id = None
            diag_nick = None
        else:

            def get_other_info_from_env(d):
                result = ""
                sentence_type = d.get("sentence_type", "")
                if sentence_type != "":
                    result += "sentence_type=" + sentence_type

                return result if result else None

            diag_nick = data.get("user", None)
            env = data.get("env", None)
            server = data.get("server", None)

            other_info = get_other_info_from_env(data)  # TODO
            bind_to_nick_id = None  # data.get("user_id", None)
        if bind_to_nick_id == None and diag_nick:
            if diag_nick.strip() != "":
                bind_to_nick_id = self.get_user_nick_id(
                    nick=diag_nick, data=data
                )
        last_user_diag_id = None
        last_interact_time = None  # get last interact
        for i, record in enumerate(diag_to_add):

            filter_allowed = record.get("filter_allowed", None)
            if filter_allowed is not None:
                filter_allowed = int(filter_allowed)
            filter_topics = record.get("filter_topics", None)
            if not filter_topics:
                filter_topics = None
            filter_results = record.get("filter_results", None)

            diag_nick = record.get("user", diag_nick)
            role = record["role"]
            bind_to_diag_id = None
            date_rec = record.get("date", None)
            if role == "assistant" and last_user_diag_id is not None:
                if date_rec is not None:
                    last_interact_time = date_rec
                bind_to_diag_id = last_user_diag_id
            if date_rec is None:
                date_rec = date
            with self.session_factory() as session:
                record_data = {
                    "user_id": user_id,
                    "diag_nick": diag_nick,
                    "content": record["content"],
                    "role": role,
                    "date": date_rec,
                    "command": record.get("command", None),
                    "emotion": record.get("emotion", None),
                    "env": env,
                    "server": server,
                    "other_info": other_info,
                    "bind_to_diag_id": bind_to_diag_id,
                    "bind_to_nick_id": bind_to_nick_id,
                    "filter_allowed": filter_allowed,
                    "filter_topics": filter_topics,
                    "filter_results": filter_results,
                }

                user_dialog = UserDialogDAO.create(session, **record_data)
                inserted_diag_id = user_dialog.diag_id
                if inserted_diag_id is not None:
                    diag_ids.append(inserted_diag_id)
                    if role == "user":
                        last_user_diag_id = inserted_diag_id

                    # INCREMENT DIAGS COUNT VALUE
                    if bind_to_diag_id is not None:
                        UserDAO.increment_diags_count(session, user_id)
        if len(diag_ids) > 0:
            if last_interact_time:
                with self.session_factory() as session:
                    UserDAO.update(session, user_id, last_answered=last_interact_time)
        return diag_ids

    def get_user_diags(self, user_id: Union[int, None], count: int = 1):
        # SELECT * FROM l LIMIT 100
        # cursor.execute(sql,(user_id,user_id,count*2,))
        with self.session_factory() as session:
            result_diags = UserDialogDAO.get_user_dialogs(session, user_id, count)
            diags = []
            if result_diags is not None:
                for d in result_diags:
                    # 0 diag_id,1 diag_nick,2 content,3 role,4 date,5 command,6 emotion,7 env
                    # {"user":d[1],"role":d[3],"content":d[2],"date":d[4],"command":d[5],"emotion":d[6],"env":d[7]}
                    diag_dict = {}
                    field_ids = {
                        "user": 1,
                        "role": 3,
                        "content": 2,
                        "date": 4,
                        "command": 5,
                        "emotion": 6,
                        "env": 7,
                    }
                    for field, value in field_ids.items():
                        if d[value]:
                            diag_dict[field] = d[value]
                    diags.append(diag_dict)
        return diags

    def get_user_diag_count(self, user_id: int, real=True):
        with self.session_factory() as session:
            if real:
                if self.check_user_id_exists(user_id):
                    return UserDialogDAO.get_user_diag_count(session, user_id)
                else:
                    return None
            else:
                return UserDAO.get_user_dialog_count(session, user_id)

    def get_last_any_diag_time(self):
        with self.session_factory() as session:
            return UserDialogDAO.get_last_any_diag_time(session)

    def get_relevant_diag(
        self,
        user_id: Union[int, None] = None,
        count: int = 1,
        exact_user_timeout: float = 160.0,
        any_user_timeout: float = 250.0,
    ) -> list[dict]:
        now = datetime.now()
        with self.session_factory() as session:
            if user_id is not None:
                user = UserDAO.get_by_id(session, user_id)
                last_answered = user.last_answered
                if last_answered:
                    try:
                        if (now - tm(last_answered)).total_seconds() > exact_user_timeout:
                            user_id = None
                    except BaseException as err:
                        print(
                            f"[DB Get Time EXACT relevant user_id={str(user_id)} diag ERR] err =",
                            err,
                        )
                        user_id = None
                else:
                    user_id = None
            diags = []
            if user_id is None:
                last_answered = self.get_last_any_diag_time() ## TODO: last answered is dict
                if last_answered:
                    try:
                        if (now - tm(last_answered)).total_seconds() <= any_user_timeout: ## TODO: but strftime to tuple ??? In the original also
                            print(
                                "[DB get ANY RELEVANT DIAG] time succed, searching for ANY diag.."
                            )
                            diags = self.get_user_diags(user_id=None, count=count)
                    except BaseException as err:
                        print("[DB Get Time ANY relevant diag ERR] err =", err)
            else:
                print(
                    f"[DB get EXACT user_id={str(user_id)} RELEVANT] time succed, searching for EXACT diag.."
                )
                diags = self.get_user_diags(user_id=user_id, count=count)
        return diags

    def get_user_summary(self, user_id: int) -> str:
        """Get the summary text for a user

        Args:
            user_id (int): The user ID to get summary for

        Returns:
            str: The user's summary text, or None if not set
        """
        with self.session_factory() as session:
            user = UserDAO.get_by_id(session, user_id)
            if user:
                return user.summary
            return None

    def set_user_summary(self, user_id: int, summary: str) -> bool:
        """Set the summary text for a user

        Args:
            user_id (int): The user ID to set summary for
            summary (str): The summary text to set

        Returns:
            bool: True if successful, False otherwise
        """
        with self.session_factory() as session:
            data = {
                'summary': summary
            }
            try:
                UserDAO.update(session, user_id, **data)
            except Exception:
                return False
            return True

    def get_user_data(self, user_name: str, raise_errors: bool = False) -> UserData:
        """Get comprehensive user data including nicknames and metadata

        Args:
            user_name (str): The username to lookup

        Returns:
            UserData: User data structured according to UserData type
        """
        try:
            user_id = self.get_user_id(user_name)

            if user_id is None:
                return None

            # Get basic user information
            with self.session_factory() as session:
                user_row = UserDAO.get_by_id(session, user_id)
                if not user_row:
                    return None

                # Get all nicknames for this user
                nickname_rows = UserNicknameDAO.get_by_user_id(session, user_id)
                # Format user's nicknames
                nicknames = {}
                for row in nickname_rows:
                    nick, env, server, nick_analyze, added_date = row.nick, row.env, row.server, row.nick_analyze, row.added_date
                    nickname_data: NicknameData = {
                        "nick": nick,
                        "env": env,
                    }

                    # Add optional fields if they exist
                    if server:
                        nickname_data["server"] = server
                    if nick_analyze:
                        nickname_data["nick_analyze"] = nick_analyze
                    if added_date:
                        nickname_data["added_date"] = added_date

                    # Use nickname as the key
                    nicknames[nick] = nickname_data

            # Get the most recent activity timestamp
            last_activity = user_row.last_interact
            if user_row.last_answered and user_row.last_answered > last_activity:
                last_activity = user_row.last_answered

            # Format the complete UserData object
            user_data: UserData = {
                "name": user_name,
                "user_id": user_id,
                "found_count": 1,
                "last_activity_timestamp": (
                    int(
                        last_activity.timestamp()
                    )
                    if last_activity
                    else 0
                ),
                "last_activity_string": last_activity if last_activity else "",
                "binded_events": [],
                "last_db_interaction": user_row.last_interact,  # last_interact
                "summary": user_row.summary if user_row.summary else "",
                "rank": float(user_row.rank) if user_row.rank is not None else 0.0,
                "nicknames": nicknames,
            }
        except Exception as e:
            print(f"[DATABASE] Error getting user data for {user_name}: {e}")
            if raise_errors:
                raise e
            return {}
        return user_data

    def connection_close(self):
        ...


if __name__ == "__main__":
    db = StreamerDatabase()
    
    # get or create user
    user = {
        'user': '123112122', # TODO: check get. The second time, if user != youtube channel id, the error output
        'env': 'youtube',
        'youtube_user_channel_id': 123112122
    }
    
    payload = db.get_or_create_user(user)
    print(payload)
    
    now_str = datetime.now().isoformat()

    db.set_mood(11.11111)
    
    rank = db.get_user_rank(1)
    print(rank)
    
    payload = db.set_user_last_question(1, "что?")
    print(payload)
    
    payload = db.get_user_last_interact_time(1)
    print(payload)
    
    payload = db.get_user_last_question_date(1)
    print(payload)
    
    payload = db.get_user_nick_id("1231123")
    print(payload)
    
    payload = db.set_analyze_for_nick(5, "apchi")
    print(payload)
    
    payload = db.get_nick_analyze(5, True)
    print(payload)
    
    payload = db.get_nick_analyze(5, False)
    print(payload)
    
    payload = db.add_diags(1, [{'user': 'antonio', 'role': 'user', 'content': 'apchi'}, 
                               {'user': 'antonio', 'role': 'assistant', 'content': 'apchi'}])
    
    payload = db.get_user_diags(1, 1)
    print(payload)
    
    payload = db.get_user_diag_count(1)    
    print(payload)
    
    payload = db.get_last_any_diag_time()
    print(payload)
    
    payload = db.get_relevant_diag(1) # TODO: looks like the original, but there are errors
    print(payload)
    
    payload = db.set_user_summary(1, "апсчи")
    print(payload)

    payload = db.get_user_summary(1)
    print(payload)

    payload = db.set_user_rank(1, 1.0)
    print(payload)
    
    payload = db.get_user_rank(1)
    print(payload)
    
    payload = db.get_user_data("1231123")
    print(payload)
    
    payload = db.check_user_id_exists(1)
    print(payload)
    
    payload = db.check_user_id_exists(3000)
    print(payload)

    payload = db.add_to_user_rank(1, 1.0)
    print(payload)
