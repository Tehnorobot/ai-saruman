from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[Optional[str]] = mapped_column(Text)
    rank: Mapped[Optional[float]] = mapped_column(Float)
    firstreg: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_interact: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_answered: Mapped[Optional[datetime]] = mapped_column(DateTime)
    diags_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_question_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_question: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)

    nicknames: Mapped[List["UserNickname"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    dialogs: Mapped[List["UserDialog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User(id={self.user_id}, name={self.name}, rank={self.rank})>"
