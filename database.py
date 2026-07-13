import os
import shutil
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Index
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, scoped_session
from werkzeug.security import generate_password_hash
from config import Config, logger

engine = create_engine(Config.DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in Config.DATABASE_URL else {})
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

Base = declarative_base()
Base.query = db_session.query_property()

class Admin(Base):
    __tablename__ = 'admins'
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(100), nullable=True)
    wallet = relationship("Wallet", uselist=False, back_populates="user")
    servers = relationship("Server", back_populates="user")

class Wallet(Base):
    __tablename__ = 'wallets'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True)
    balance = Column(Float, default=0.0)
    user = relationship("User", back_populates="wallet")
    transactions = relationship("WalletTransaction", back_populates="wallet")

class WalletTransaction(Base):
    __tablename__ = 'wallet_transactions'
    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey('wallets.id'))
    amount = Column(Float, nullable=False)
    tx_type = Column(String(20), nullable=False)
    description = Column(String(255))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    wallet = relationship("Wallet", back_populates="transactions")

class Setting(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    key = Column(String(50), unique=True)
    value = Column(String(1000))

class HetznerAccount(Base):
    __tablename__ = 'hetzner_accounts'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    api_token = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)

class Plan(Base):
    __tablename__ = 'plans'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    server_type = Column(String(50), nullable=False)
    base_price = Column(Float, nullable=False)
    final_price = Column(Float, nullable=False)
    is_available = Column(Boolean, default=True)

class Server(Base):
    __tablename__ = 'servers'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    ip_address = Column(String(50))
    status = Column(String(20), default="running")
    expires_at = Column(DateTime, nullable=False)
    user = relationship("User", back_populates="servers")

def init_db():
    Base.metadata.create_all(bind=engine)
    if not Admin.query.filter_by(username=Config.PANEL_ADMIN_USER).first():
        hashed_pass = generate_password_hash(Config.PANEL_ADMIN_PASS)
        db_session.add(Admin(username=Config.PANEL_ADMIN_USER, password_hash=hashed_pass))
    db_session.commit()

