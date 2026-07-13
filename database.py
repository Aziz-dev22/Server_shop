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
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    wallet = relationship("Wallet", uselist=False, back_populates="user")
    servers = relationship("Server", back_populates="user")

class Wallet(Base):
    __tablename__ = 'wallets'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True, index=True)
    balance = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    user = relationship("User", back_populates="wallet")
    transactions = relationship("WalletTransaction", back_populates="wallet")

class WalletTransaction(Base):
    __tablename__ = 'wallet_transactions'
    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey('wallets.id'), index=True)
    amount = Column(Float, nullable=False)
    tx_type = Column(String(20), nullable=False)
    description = Column(String(255))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    wallet = relationship("Wallet", back_populates="transactions")

class HetznerAccount(Base):
    __tablename__ = 'hetzner_accounts'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    api_token = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Plan(Base):
    __tablename__ = 'plans'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    server_type = Column(String(50), nullable=False)
    location = Column(String(20), default="nbg1")
    base_price = Column(Float, nullable=False)
    final_price = Column(Float, nullable=False)
    is_available = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Server(Base):
    __tablename__ = 'servers'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), index=True)
    plan_id = Column(Integer, ForeignKey('plans.id'))
    hetzner_account_id = Column(Integer, ForeignKey('hetzner_accounts.id'))
    name = Column(String(100))
    ip_address = Column(String(50))
    root_password = Column(String(100))
    status = Column(String(20), default="running")
    traffic_limit_gb = Column(Float, default=2000.0)
    traffic_used_gb = Column(Float, default=0.0)
    traffic_warning_sent = Column(Boolean, default=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    grace_notices = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    user = relationship("User", back_populates="servers")

Index('idx_server_user_expiry', Server.user_id, Server.expires_at)

def init_db():
    Base.metadata.create_all(bind=engine)
    if not Admin.query.filter_by(username=Config.PANEL_ADMIN_USER).first():
        hashed_pass = generate_password_hash(Config.PANEL_ADMIN_PASS)
        admin = Admin(username=Config.PANEL_ADMIN_USER, password_hash=hashed_pass)
        db_session.add(admin)
    db_session.commit()
    logger.info("Database initialized successfully.")

def backup_database():
    if not os.path.exists(Config.BACKUP_DIR):
        os.makedirs(Config.BACKUP_DIR)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(Config.BACKUP_DIR, f"backup_{timestamp}.db")
    try:
        shutil.copy2("server_shop.db", backup_path)
        logger.info(f"Database backup created at {backup_path}")
        return True
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return False

