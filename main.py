import threading
from config import Config, logger
from database import init_db
from scheduler import start_scheduler
from bot import bot
from web_panel import app

def run_web():
    logger.info(f"Starting Web Panel on port {Config.PANEL_PORT}...")
    app.run(host='0.0.0.0', port=Config.PANEL_PORT, use_reloader=False, debug=False)

if __name__ == "__main__":
    logger.info("Initializing Server Shop Production Environment...")
    init_db()
    
    start_scheduler()
    logger.info("Scheduler Started.")

    # اجرای پنل وب در ترد پس‌زمینه
    threading.Thread(target=run_web, daemon=True).start()
    
    # اجرای ربات در ترد اصلی (که باعث می‌شود پردازش ترمینال را زنده نگه دارد)
    logger.info("Starting Telegram Bot...")
    bot.infinity_polling()
