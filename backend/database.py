# backend/database.py
import os

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# База всегда лежит рядом с этим файлом (backend/kkci.db), откуда бы ни запускали сервер или скрипты
HERE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(HERE, "kkci.db")
SQLALCHEMY_DATABASE_URL = "sqlite:///" + DB_FILE.replace("\\", "/")

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
