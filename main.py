import threading
import time
from database import init_db
from bot import bot
from web_panel import app
from config import logger

def run_web_panel():
    """اجرای پنل وب روی پورت مشخص شده در تنظیمات"""
    try:
        logger.info(f"Starting Web Panel on port {app.config.get('PORT', 5000)}...")
        app.run(host='0.0.0.0', port=1085, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Web Panel failed to start: {e}")

def run_telegram_bot():
    """اجرای ربات تلگرام"""
    try:
        logger.info("Starting Telegram Bot...")
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.error(f"Bot failed to start: {e}")
        time.sleep(5)
        run_telegram_bot() # تلاش مجدد در صورت خطا

if __name__ == "__main__":
    # 1. مقداردهی اولیه دیتابیس
    init_db()
    logger.info("Database initialized.")

    # 2. اجرای پنل وب در یک ترد (Thread) جداگانه
    web_thread = threading.Thread(target=run_web_panel, daemon=True)
    web_thread.start()

    # 3. اجرای ربات تلگرام در ترد اصلی
    run_telegram_bot()
