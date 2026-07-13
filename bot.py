import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import db_session, User, Wallet, Server, Plan, HetznerAccount, WalletTransaction, Setting
from hetzner_client import HetznerClient
from config import Config
from datetime import datetime, timezone

bot = telebot.TeleBot(Config.TELEGRAM_BOT_TOKEN)

# --- توابع پایه و جوین اجباری ---
def is_member(user_id):
    channel = Setting.query.filter_by(key='channel_id').first()
    if not channel or not channel.value: return True
    try:
        return bot.get_chat_member(channel.value, user_id).status in ['member', 'creator', 'administrator']
    except: return False

def get_or_create_user(tg_user):
    user = User.query.get(tg_user.id)
    if not user:
        user = User(id=tg_user.id, username=tg_user.username)
        db_session.add(user)
        db_session.add(Wallet(user_id=user.id, balance=0.0))
        db_session.commit()
    return user

# --- پنل ادمین تلگرامی ---
@bot.message_handler(commands=['start'])
def start(message):
    if not is_member(message.chat.id):
        bot.send_message(message.chat.id, "❌ ابتدا در کانال ما عضو شوید.")
        return
    get_or_create_user(message.from_user)
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🖥 خرید سرور", callback_data="buy"), InlineKeyboardButton("💻 سرورهای من", callback_data="my_servers"))
    if message.chat.id == Config.ADMIN_TELEGRAM_ID:
        markup.add(InlineKeyboardButton("🛠 پنل مدیریت", callback_data="admin_panel"))
    bot.send_message(message.chat.id, f"به {Config.BRAND_NAME} خوش آمدید.", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_panel")
def admin_panel(call):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("➕ شارژ کیف پول کاربر", callback_data="admin_add_balance"))
    markup.add(InlineKeyboardButton("📢 پیام همگانی", callback_data="admin_broadcast"))
    bot.edit_message_text("🛠 پنل ادمین:", call.message.chat.id, call.message.message_id, reply_markup=markup)

# --- منطق تراکنش دستی ---
@bot.callback_query_handler(func=lambda call: call.data == "admin_add_balance")
def add_bal_init(call):
    msg = bot.send_message(call.chat.id, "آیدی کاربر و مبلغ را با فاصله وارد کنید:")
    bot.register_next_step_handler(msg, process_add_balance)

def process_add_balance(message):
    try:
        u_id, amt = message.text.split()
        user = User.query.get(int(u_id))
        user.wallet.balance += float(amt)
        db_session.add(WalletTransaction(wallet_id=user.wallet.id, amount=float(amt), description="تراکنش دستی ادمین"))
        db_session.commit()
        bot.send_message(message.chat.id, "✅ انجام شد.")
    except: bot.send_message(message.chat.id, "خطا در فرمت!")

# --- منطق ساخت سرور و مدیریت ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_plan_"))
def handle_buy(call):
    plan_id = call.data.split("_")[2]
    plan = Plan.query.get(plan_id)
    user = User.query.get(call.from_user.id)
    
    if user.wallet.balance < plan.final_price:
        bot.answer_callback_query(call.id, "موجودی کافی نیست!")
        return

    account = HetznerAccount.query.filter_by(is_active=True).first()
    client = HetznerClient(account.api_token)
    res = client.create_server(f"Server-{user.id}", plan.server_type)
    
    if res:
        user.wallet.balance -= plan.final_price
        db_session.add(Server(id=res["id"], user_id=user.id, ip_address=res["ip"], name=plan.name, hetzner_account_id=account.id))
        db_session.commit()
        bot.send_message(call.message.chat.id, f"✅ سرور ساخته شد: {res['ip']}")

# --- شروع ربات ---
if __name__ == "__main__":
    bot.infinity_polling()

