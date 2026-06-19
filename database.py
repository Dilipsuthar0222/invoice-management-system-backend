# database.py
from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# The empty space between : and @ means NO password
SQLALCHEMY_DATABASE_URL = "mysql+pymysql://root:@localhost/invoice_db"

engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)

# Boost MySQL max_allowed_packet size dynamically to prevent connection drops on large base64 image insertions
try:
    with engine.connect() as connection:
        connection.execute(text("SET GLOBAL max_allowed_packet = 67108864"))
        connection.execute(text("SET SESSION max_allowed_packet = 67108864"))
        connection.commit()
except Exception as e:
    print("Warning: Failed to set larger MySQL max_allowed_packet dynamically:", e)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()