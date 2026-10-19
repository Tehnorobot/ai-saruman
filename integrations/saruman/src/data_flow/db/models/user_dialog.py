from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class UserDialog(Base, TimestampMixin):
    __tablename__ = "users_dialogs"

    diag_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    diag_nick: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    command: Mapped[Optional[str]] = mapped_column(Text)
    emotion: Mapped[Optional[str]] = mapped_column(Text)
    env: Mapped[Optional[str]] = mapped_column(Text)
    server: Mapped[Optional[str]] = mapped_column(Text)
    other_info: Mapped[Optional[str]] = mapped_column(Text)
    bind_to_diag_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users_dialogs.diag_id", ondelete="CASCADE")
    )
    bind_to_nick_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users_nicknames.nick_id", ondelete="SET NULL")
    )
    filter_allowed: Mapped[Optional[int]] = mapped_column(Integer)
    filter_topics: Mapped[Optional[str]] = mapped_column(Text)
    filter_results: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="dialogs")
    bind_dialog: Mapped[Optional["UserDialog"]] = relationship(remote_side="UserDialog.diag_id")
    bind_nickname: Mapped[Optional["UserNickname"]] = relationship(back_populates="dialogs")

    def __repr__(self) -> str:
        return f"<UserDialog(id={self.diag_id}, role={self.role}, date={self.date})>"
