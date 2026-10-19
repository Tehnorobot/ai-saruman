from typing import Optional

from sqlalchemy import Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class MindVariable(Base, TimestampMixin):
    __tablename__ = "mind_variables"

    mind_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mind_name: Mapped[Optional[str]] = mapped_column(Text)
    mood: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    def __repr__(self) -> str:
        return f"<MindVariable(id={self.mind_id}, name={self.mind_name}, mood={self.mood})>"
