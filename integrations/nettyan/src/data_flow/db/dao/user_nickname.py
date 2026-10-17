from sqlalchemy import select
from data_flow.db.dao.base import BaseDAO
from data_flow.db.models.user_nickname import UserNickname


class UserNicknameDAO(BaseDAO):
    model = UserNickname
    
    @classmethod
    def get_by_nick(cls, session, nick) -> UserNickname:
        return cls.filter_by(session, nick = nick, is_list=False)
    
    @classmethod
    def get_by_user_id(cls, session, user_id) -> UserNickname:
        return cls.filter_by(session, user_id = user_id, is_list=True)
    
    @classmethod
    def get_user_nick_by_data(cls, session, nick, data): 
        stmt = select(UserNickname.nick_id).where(UserNickname.nick == nick)

        if data:
            env = data.get("env")
            server = data.get("server")

            if env and server:
                stmt = stmt.where(
                    UserNickname.env == env,
                    UserNickname.server == server
                )
            elif env:
                stmt = stmt.where(UserNickname.env == env)

        result = session.execute(stmt)
        nick_id = result.scalar_one_or_none()
        return nick_id
    