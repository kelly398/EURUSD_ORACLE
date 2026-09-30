from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.config.credentials import DATABASE_URL


engine = create_engine(
    DATABASE_URL,
    connect_args={
        "options": "-c timezone=UTC"
    },
    pool_pre_ping=True
)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)
