import threading
from config import Config, logger
from database import init_db
from scheduler import start_scheduler
from bot import bot
from web_panel import app

def run_bot():
    logger.info("Starting Telegram Bot...")
    bot.infinity_polling()

def run_web():
    logger.info(f"Starting Web Panel on port {Config.PANEL_PORT}...")
    app.run(host='0.0.0.0', port=Config.PANEL_PORT, use_reloader=False)

if __name__ == "__main__":
    logger.info("Initializing Server Shop Production Environment...")
    init_db()
    
    start_scheduler()
    logger.info("Scheduler Started.")

    threading.Thread(target=run_bot, daemon=True).start()
    run_web()

