import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database import db_session, User, Wallet, Server, Plan, HetznerAccount, WalletTransaction
from hetzner_client import HetznerClient
from datetime import datetime, timedelta, timezone

bot = telebot.TeleBot(Config.TELEGRAM_BOT_TOKEN)

def get_or_create_user(tg_user):
    user = User.query.get(tg_user.id)
    if not user:
        user = User(id=tg_user.id, username=tg_user.username)
        wallet = Wallet(user_id=tg_user.id, balance=0.0)
        db_session.add(user)
        db_session.add(wallet)
        db_session.commit()
    return user

def main_menu_keyboard(user_id):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🖥 خرید سرور", callback_data="menu_buy"),
        InlineKeyboardButton("💻 سرورهای من", callback_data="menu_servers"),
        InlineKeyboardButton("💰 کیف پول", callback_data="menu_wallet"),
        InlineKeyboardButton("👤 حساب کاربری", callback_data="menu_profile"),
        InlineKeyboardButton("🎫 پشتیبانی", callback_data="menu_support"),
        InlineKeyboardButton("📖 آموزش‌ها", callback_data="menu_tutorials"),
        InlineKeyboardButton("ℹ قوانین", callback_data="menu_rules")
    )
    # اضافه شدن پنل ادمین داخل ربات فقط برای ادمین اصلی
    if user_id == Config.ADMIN_TELEGRAM_ID:
        markup.add(InlineKeyboardButton("🛠 پنل مدیریت (Admin)", callback_data="menu_admin"))
    return markup

@bot.message_handler(commands=['start'])
def start_cmd(message):
    get_or_create_user(message.from_user)
    bot.send_message(
        message.chat.id, 
        "به **فروشگاه سرور ابری** خوش آمدید! 🚀\nزیرساخت ابری خود را به صورت آنی و مستقیم از طریق تلگرام مدیریت کنید.", 
        reply_markup=main_menu_keyboard(message.from_user.id),
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def handle_menus(call):
    user = get_or_create_user(call.from_user)
    action = call.data.split("_")[1]
    
    if action == "admin":
        if user.id != Config.ADMIN_TELEGRAM_ID:
            bot.answer_callback_query(call.id, "شما دسترسی ندارید.", show_alert=True)
            return
            
        total_users = User.query.count()
        total_servers = Server.query.count()
        text = f"🛠 **پنل مدیریت ربات**\n\n👥 تعداد کل کاربران: `{total_users}`\n🖥 سرورهای فعال: `{total_servers}`\n\nبرای ارسال پیام همگانی (Broadcast) روی دکمه زیر کلیک کنید:"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📢 ارسال پیام همگانی", callback_data="admin_broadcast"))
        markup.add(InlineKeyboardButton("⬅ بازگشت", callback_data="menu_main"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif action == "profile":
        text = f"👤 **پروفایل کاربری شما**\n\nآیدی تلگرام: `{user.id}`\nیوزرنیم: @{user.username}\nتاریخ عضویت: {user.created_at.strftime('%Y-%m-%d')}"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=main_menu_keyboard(user.id), parse_mode="Markdown")
        
    elif action == "wallet":
        text = f"💰 **وضعیت کیف پول**\n\nموجودی فعلی: `${user.wallet.balance:.2f}`"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("➕ شارژ حساب (USDT)", callback_data="wallet_deposit"))
        markup.add(InlineKeyboardButton("⬅ بازگشت", callback_data="menu_main"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif action == "buy":
        plans = Plan.query.filter_by(is_available=True).all()
        if not plans:
            bot.answer_callback_query(call.id, "در حال حاضر پلنی برای فروش موجود نیست.", show_alert=True)
            return
        markup = InlineKeyboardMarkup(row_width=1)
        for plan in plans:
            markup.add(InlineKeyboardButton(f"{plan.name} ({plan.server_type}) - ${plan.final_price}/ماهانه", callback_data=f"buy_plan_{plan.id}"))
        markup.add(InlineKeyboardButton("⬅ بازگشت", callback_data="menu_main"))
        bot.edit_message_text("🖥 **لطفاً یک پلن ابری انتخاب کنید:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif action == "servers":
        servers = Server.query.filter_by(user_id=user.id).all()
        if not servers:
            bot.answer_callback_query(call.id, "شما هنوز هیچ سروری خریداری نکرده‌اید.", show_alert=True)
            return
        markup = InlineKeyboardMarkup(row_width=1)
        for srv in servers:
            status_emoji = "🟢" if srv.status == "running" else "🔴" if srv.status == "off" else "❌"
            markup.add(InlineKeyboardButton(f"{status_emoji} {srv.ip_address} | {srv.name}", callback_data=f"manage_srv_{srv.id}"))
        markup.add(InlineKeyboardButton("⬅ بازگشت", callback_data="menu_main"))
        bot.edit_message_text("💻 **لیست سرورهای ابری شما:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        
    elif action == "main":
        bot.edit_message_text("منوی اصلی", call.message.chat.id, call.message.message_id, reply_markup=main_menu_keyboard(user.id))

# --- بخش پیام همگانی ادمین ---
@bot.callback_query_handler(func=lambda call: call.data == "admin_broadcast")
def admin_broadcast_step1(call):
    if call.from_user.id != Config.ADMIN_TELEGRAM_ID: return
    msg = bot.send_message(call.message.chat.id, "لطفا پیامی که می‌خواهید برای همه کاربران ارسال شود را بفرستید:\n(برای لغو /cancel را بفرستید)")
    bot.register_next_step_handler(msg, admin_broadcast_step2)

def admin_broadcast_step2(message):
    if message.text == "/cancel":
        bot.send_message(message.chat.id, "ارسال پیام لغو شد.", reply_markup=main_menu_keyboard(message.from_user.id))
        return
    
    users = User.query.all()
    success = 0
    bot.send_message(message.chat.id, "در حال ارسال پیام...")
    for u in users:
        try:
            bot.copy_message(chat_id=u.id, from_chat_id=message.chat.id, message_id=message.message_id)
            success += 1
        except Exception:
            pass
    bot.send_message(message.chat.id, f"✅ پیام با موفقیت به {success} کاربر ارسال شد.", reply_markup=main_menu_keyboard(message.from_user.id))
# -----------------------------

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_plan_"))
def handle_purchase(call):
    user = get_or_create_user(call.from_user)
    plan_id = int(call.data.split("_")[2])
    plan = Plan.query.get(plan_id)
    
    if user.wallet.balance < plan.final_price:
        bot.answer_callback_query(call.id, "موجودی کافی نیست! لطفاً کیف پول خود را شارژ کنید.", show_alert=True)
        return
        
    active_account = HetznerAccount.query.filter_by(is_active=True).first()
    if not active_account:
        bot.answer_callback_query(call.id, "سیستم در حال بروزرسانی است (اکانت API فعال نیست).", show_alert=True)
        return

    bot.answer_callback_query(call.id, "در حال ساخت سرور... این فرایند ممکن است چند ثانیه طول بکشد.")
    
    client = HetznerClient(api_token=active_account.api_token)
    server_name = f"srv-{user.id}-{int(datetime.now().timestamp())}"
    res = client.create_server(server_name, plan.server_type, location=plan.location)
    
    if res:
        user.wallet.balance -= plan.final_price
        tx = WalletTransaction(wallet_id=user.wallet.id, amount=-plan.final_price, tx_type="purchase", description=f"خرید سرور {plan.name}")
        
        new_server = Server(
            id=res["id"], user_id=user.id, plan_id=plan.id, hetzner_account_id=active_account.id,
            name=server_name, ip_address=res["ip"], root_password=res["root_password"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=30)
        )
        db_session.add(tx)
        db_session.add(new_server)
        db_session.commit()
        
        msg = f"✅ **سرور با موفقیت ساخته شد!**\n\nآی‌پی: `{res['ip']}`\nیوزرنیم: `root`\nپسورد: `{res['root_password']}`\n\nمیتوانید از بخش 'سرورهای من' آن را مدیریت کنید."
        bot.send_message(call.message.chat.id, msg, parse_mode="Markdown")
    else:
        bot.send_message(call.message.chat.id, "❌ خطایی در ارتباط با سرور هتزنر رخ داد. وجه کسر نشد. لطفا به پشتیبانی پیام دهید.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("manage_srv_"))
def handle_management(call):
    server_id = int(call.data.split("_")[2])
    srv = Server.query.get(server_id)
    if not srv or srv.user_id != call.from_user.id: return

    markup = InlineKeyboardMarkup(row_width=2)
    
    if srv.status == "expired":
        markup.add(InlineKeyboardButton("💳 تمدید سرور", callback_data=f"action_renew_{srv.id}"))
    else:
        markup.add(
            InlineKeyboardButton("🟢 روشن کردن", callback_data=f"action_poweron_{srv.id}"),
            InlineKeyboardButton("🔴 خاموش کردن", callback_data=f"action_poweroff_{srv.id}"),
            InlineKeyboardButton("🔄 ریستارت", callback_data=f"action_reboot_{srv.id}"),
            InlineKeyboardButton("🔑 تغییر رمز", callback_data=f"action_resetpass_{srv.id}"),
            InlineKeyboardButton("💳 تمدید زمان", callback_data=f"action_renew_{srv.id}")
        )
        
    markup.add(InlineKeyboardButton("⬅ بازگشت به لیست", callback_data="menu_servers"))
    
    status_fa = "روشن 🟢" if srv.status == "running" else "خاموش 🔴" if srv.status == "off" else "منقضی شده ❌"
    
    details = f"💻 **مدیریت سرور**\n\nآی‌پی: `{srv.ip_address}`\nوضعیت: {status_fa}\nترافیک مصرفی: {srv.traffic_used_gb:.2f} GB / {srv.traffic_limit_gb} GB\nتاریخ انقضا: {srv.expires_at.strftime('%Y-%m-%d %H:%M UTC')}"
    bot.edit_message_text(details, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("action_"))
def exec_action(call):
    parts = call.data.split("_")
    action = parts[1]
    srv_id = int(parts[2])
    
    srv = Server.query.get(srv_id)
    if not srv or srv.user_id != call.from_user.id: return

    if action == "renew":
        user = srv.user
        plan = Plan.query.get(srv.plan_id)
        if user.wallet.balance < plan.final_price:
            bot.answer_callback_query(call.id, f"موجودی کافی نیست. برای تمدید به ${plan.final_price} نیاز دارید.", show_alert=True)
            return
            
        user.wallet.balance -= plan.final_price
        tx = WalletTransaction(wallet_id=user.wallet.id, amount=-plan.final_price, tx_type="renewal", description=f"تمدید سرور {srv.ip_address}")
        
        srv.expires_at = srv.expires_at + timedelta(days=30) if srv.status != "expired" else datetime.now(timezone.utc) + timedelta(days=30)
        
        if srv.status == "expired":
            account = HetznerAccount.query.get(srv.hetzner_account_id)
            if account:
                client = HetznerClient(api_token=account.api_token)
                client.action_server(srv.id, "poweron")
        
        srv.status = "running"
        srv.grace_notices = 0
        
        db_session.add(tx)
        db_session.commit()
        bot.answer_callback_query(call.id, "✅ سرور با موفقیت برای 30 روز تمدید شد!", show_alert=True)
        handle_management(call)
        return

    account = HetznerAccount.query.get(srv.hetzner_account_id)
    if not account:
        bot.answer_callback_query(call.id, "اکانت API یافت نشد.", show_alert=True)
        return
        
    client = HetznerClient(api_token=account.api_token)
    api_action = "poweron" if action == "poweron" else "poweroff" if action == "poweroff" else "reboot" if action == "reboot" else "reset_password"
    
    res = client.action_server(srv.id, api_action)
    if res:
        if action in ["poweron", "poweroff"]:
            srv.status = "running" if action == "poweron" else "off"
            db_session.commit()
        
        action_name = "روشن شدن" if action == "poweron" else "خاموش شدن" if action == "poweroff" else "ریستارت" if action == "reboot" else "تغییر رمز"
        bot.answer_callback_query(call.id, f"دستور {action_name} با موفقیت به سرور ارسال شد.")
        handle_management(call)
    else:
        bot.answer_callback_query(call.id, "اجرای دستور با خطا مواجه شد.", show_alert=True)
