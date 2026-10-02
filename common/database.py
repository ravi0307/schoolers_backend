"""
SQLAlchemy engine/session setup, plus the `get_db` FastAPI dependency.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from common.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
    pool_size=20,          # connections per service
    max_overflow=10,       # spike capacity
    pool_recycle=1800,     # recycle before Postgres kills idle connections
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def get_db():
    """Yield a DB session per-request, always closed afterwards."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
