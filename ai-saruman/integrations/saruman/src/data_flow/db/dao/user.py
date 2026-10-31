from sqlalchemy import func, select, update
from data_flow.db.dao.base import BaseDAO
from data_flow.db.models.user import User


class UserDAO(BaseDAO):
    """Базовый DAO с классовыми методами для CRUD операций."""
    model = User

    @classmethod
    def check_user_id_exists(cls, session, user_id: int) -> bool:
        stmt = select(func.count()).select_from(cls.model).where(cls.model.user_id == user_id)
        result = session.execute(stmt)
        count = result.scalar()
        return bool(count)

    @classmethod
    def increment_diags_count(cls, session, user_id):
        stmt = (
            update(User)
            .where(User.user_id == user_id)
            .values(diags_count=User.diags_count + 1)
        )
        session.execute(stmt)
        session.commit()

    @classmethod
    def get_user_dialog_count(cls, session, user_id):
        stmt = select(User.diags_count).where(User.user_id == user_id)
        cached_count = session.execute(stmt).scalar_one_or_none()
        return cached_count

    @classmethod
    def add_to_rank(cls, session, user_id: int, amount: float):
        stmt = (
            update(User)
            .where(User.user_id == user_id)
            .values(rank=func.greatest(0.0, func.least(10.0, User.rank + amount)))
            .returning(User.rank)
        )
        result = session.execute(stmt)
        session.commit()
        return result.scalar_one_or_none()
