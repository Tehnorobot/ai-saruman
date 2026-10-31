from sqlalchemy import func, select, union_all
from data_flow.db.dao.base import BaseDAO
from data_flow.db.models.user_dialog import UserDialog


class UserDialogDAO(BaseDAO):
    model = UserDialog

    @classmethod
    def get_last_any_diag_time(cls, session):
        stmt = (
            select(UserDialog.date, UserDialog.diag_id)
            .where(
                UserDialog.bind_to_diag_id.is_not(None),
                UserDialog.role == "assistant",
            )
            .order_by(UserDialog.diag_id.desc())
            .limit(1)
        )
        result = session.execute(stmt).mappings().first()
        return dict(result) if result else None

    @classmethod
    def get_user_dialogs(cls, session, user_id: int | None, count: int):
        bind_subq = (
            select(UserDialog.bind_to_diag_id)
            .where(
                UserDialog.role == "assistant",
                UserDialog.bind_to_diag_id.is_not(None),
                *([UserDialog.user_id == user_id] if user_id is not None else [])
            )
        )

        q1 = (
            select(
                UserDialog.diag_id,
                UserDialog.diag_nick,
                UserDialog.content,
                UserDialog.role,
                UserDialog.date,
                UserDialog.command,
                UserDialog.emotion,
                UserDialog.env,
            )
            .where(UserDialog.diag_id.in_(bind_subq))
        )

        q2 = (
            select(
                UserDialog.diag_id,
                UserDialog.diag_nick,
                UserDialog.content,
                UserDialog.role,
                UserDialog.date,
                UserDialog.command,
                UserDialog.emotion,
                UserDialog.env,
            )
            .where(
                UserDialog.role == "assistant",
                UserDialog.bind_to_diag_id.is_not(None),
                *([UserDialog.user_id == user_id] if user_id is not None else [])
            )
        )

        union_q = union_all(q1, q2).subquery()

        inner_q = (
            select(
                union_q.c.diag_id,
                union_q.c.diag_nick,
                union_q.c.content,
                union_q.c.role,
                union_q.c.date,
                union_q.c.command,
                union_q.c.emotion,
                union_q.c.env,
            )
            .order_by(union_q.c.diag_id.desc())
            .limit(count * 2)
            .subquery()
        )

        final_q = (
            select(
                inner_q.c.diag_id,
                inner_q.c.diag_nick,
                inner_q.c.content,
                inner_q.c.role,
                inner_q.c.date,
                inner_q.c.command,
                inner_q.c.emotion,
                inner_q.c.env,
            )
            .order_by(inner_q.c.diag_id.asc())
        )

        return session.execute(final_q).all()

    @classmethod
    def get_user_diag_count(cls, session, user_id: int):
        stmt = (
            select(func.count(UserDialog.diag_id))
            .where(UserDialog.user_id == user_id)
            .where(UserDialog.role == "assistant")
        )
        return session.execute(stmt).scalar_one()

    @classmethod
    def get_last_assistant_dialog_with_bind(cls, session):
        stmt = (
            select(UserDialog.date, UserDialog.diag_id)
            .where(UserDialog.bind_to_diag_id.is_not(None))
            .where(UserDialog.role == "assistant")
            .order_by(UserDialog.diag_id.desc())
            .limit(1)
        )
        return session.execute(stmt).first()
