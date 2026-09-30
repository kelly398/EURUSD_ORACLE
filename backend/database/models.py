from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime
)

from sqlalchemy.orm import declarative_base


Base = declarative_base()


class Tick(Base):

    __tablename__ = "ticks"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    symbol = Column(
        String,
        nullable=False
    )

    bid = Column(
        Float,
        nullable=False
    )

    ask = Column(
        Float,
        nullable=False
    )

    spread = Column(
        Float,
        nullable=False
    )

    timestamp = Column(
    DateTime,
    nullable=False,
    index=True
)

    