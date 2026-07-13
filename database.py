import os, shutil
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from werkzeug.security import generate_password_hash
from config import Config

engine = create_engine(Config.DATABASE_URL, connect_args={"check_same_thread": False})
db_session = scoped_session(sessionmaker(bind=engine))
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(100))
    wallet = relationship("Wallet", uselist=False, back_populates="user")
    servers = relationship("Server", back_populates="user")

class Wallet(Base):
    __tablename__ = 'wallets'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    balance = Column(Float, default=0.0)
    user = relationship("User", back_populates="wallet")
    transactions = relationship("WalletTransaction", back_populates="wallet")

class WalletTransaction(Base):
    __tablename__ = 'wallet_transactions'
    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey('wallets.id'))
    amount = Column(Float, nullable=False)
    description = Column(String(255))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    wallet = relationship("Wallet", back_populates="transactions")

class Server(Base):
    __tablename__ = 'servers'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    hetzner_account_id = Column(Integer, ForeignKey('hetzner_accounts.id'))
    ip_address = Column(String(50))
    status = Column(String(20))
    name = Column(String(100))
    user = relationship("User", back_populates="servers")

class HetznerAccount(Base):
    __tablename__ = 'hetzner_accounts'
    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    api_token = Column(String(255))
    is_active = Column(Boolean, default=True)

class Plan(Base):
    __tablename__ = 'plans'
    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    server_type = Column(String(50))
    final_price = Column(Float)

class Setting(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    key = Column(String(50), unique=True)
    value = Column(String(1000))

def init_db():
    Base.metadata.create_all(bind=engine)
