import os
import logging
from dotenv import load_dotenv

# بارگذاری متغیرهای محیطی
load_dotenv()

class Config:
    # تنظیمات تلگرام و دیتابیس
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8632418060:AAHDAzKnflvm6zrdCa-ltH8kfovBcDdPx2I")
    ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", 158912388))
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///server_shop.db")
    SECRET_KEY = os.getenv("SECRET_KEY", "a_very_secure_secret_key_123")
    
    # تنظیمات پنل وب
    PANEL_PORT = int(os.getenv("PANEL_PORT", 1085))
    PANEL_ADMIN_USER = os.getenv("PANEL_ADMIN_USER", "aziz")
    PANEL_ADMIN_PASS = os.getenv("PANEL_ADMIN_PASS", "aziz")
    
    # تنظیمات برند و سیستم
    BRAND_NAME = os.getenv("BRAND_NAME", "Server Shop")
    IP_CHANGE_PRICE = float(os.getenv("IP_CHANGE_PRICE", 2.0))
    BACKUP_DIR = "backups"
    
    # مسیر فایل دیتابیس برای بکاپ گیری
    DB_FILE = "server_shop.db"

# تنظیمات لاگینگ برای دیباگ کردن خطاها
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger("ServerShop")

# اطمینان از وجود پوشه بکاپ
if not os.path.exists(Config.BACKUP_DIR):
    os.makedirs(Config.BACKUP_DIR)
