import os
import shutil
import logging
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, scoped_session
from werkzeug.security import generate_password_hash
from config import Config

# تنظیم دیتابیس
engine = create_engine(Config.DATABASE_URL, connect_args={"check_same_thread": False})
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(100))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
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
    tx_type = Column(String(20), nullable=False) # deposit, purchase, renewal, admin_adj
    description = Column(String(255))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    wallet = relationship("Wallet", back_populates="transactions")

class Server(Base):
    __tablename__ = 'servers'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    hetzner_account_id = Column(Integer, ForeignKey('hetzner_accounts.id'))
    ip_address = Column(String(50))
    status = Column(String(20), default="running")
    name = Column(String(100))
    expires_at = Column(DateTime)
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
    base_price = Column(Float)
    final_price = Column(Float)

class Setting(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    key = Column(String(50), unique=True)
    value = Column(String(2000))

# توابع مدیریت دیتابیس
def init_db():
    Base.metadata.create_all(bind=engine)
    # ایجاد ادمین پیش‌فرض اگر وجود ندارد
    from database import Admin
    if not Admin.query.filter_by(username=Config.PANEL_ADMIN_USER).first():
        hashed_pass = generate_password_hash(Config.PANEL_ADMIN_PASS)
        db_session.add(Admin(username=Config.PANEL_ADMIN_USER, password_hash=hashed_pass))
        db_session.commit()

class Admin(Base):
    __tablename__ = 'admins'
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True)
    password_hash = Column(String(255))

def backup_database():
    if not os.path.exists(Config.BACKUP_DIR):
        os.makedirs(Config.BACKUP_DIR)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(Config.BACKUP_DIR, f"backup_{timestamp}.db")
    try:
        shutil.copy2(Config.DB_FILE, backup_path)
        return backup_path
    except Exception as e:
        return None

def restore_database(filename):
    backup_path = os.path.join(Config.BACKUP_DIR, filename)
    if os.path.exists(backup_path):
        shutil.copy2(backup_path, Config.DB_FILE)
        return True
    return False
