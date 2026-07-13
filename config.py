import os
import logging
from dotenv import load_dotenv

load_dotenv()

class Config:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", 0))
    
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///server_shop.db")
    BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
    
    SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-dev-key")
    PANEL_PORT = int(os.getenv("PANEL_PORT", 1085))
    PANEL_ADMIN_USER = os.getenv("PANEL_ADMIN_USER", "admin")
    PANEL_ADMIN_PASS = os.getenv("PANEL_ADMIN_PASS")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("server_shop.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ServerShop")
