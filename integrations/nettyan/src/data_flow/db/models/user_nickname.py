from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class UserNickname(Base, TimestampMixin):
    __tablename__ = "users_nicknames"
    __table_args__ = (UniqueConstraint("nick", "env"),)

    nick_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    nick: Mapped[str] = mapped_column(Text, nullable=False)
    env: Mapped[Optional[str]] = mapped_column(Text)
    server: Mapped[Optional[str]] = mapped_column(Text)
    other_info: Mapped[Optional[str]] = mapped_column(Text)
    added_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    nick_analyze: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="nicknames")
    dialogs: Mapped[List["UserDialog"]] = relationship(back_populates="bind_nickname")

    def __repr__(self) -> str:
        return f"<UserNickname(id={self.nick_id}, nick={self.nick}, env={self.env})>"
