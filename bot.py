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

def main_menu_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🖥 Buy Server", callback_data="menu_buy"),
        InlineKeyboardButton("💻 My Servers", callback_data="menu_servers"),
        InlineKeyboardButton("💰 Wallet", callback_data="menu_wallet"),
        InlineKeyboardButton("👤 Profile", callback_data="menu_profile"),
        InlineKeyboardButton("🎫 Support", callback_data="menu_support"),
        InlineKeyboardButton("📖 Tutorials", callback_data="menu_tutorials"),
        InlineKeyboardButton("ℹ Rules", callback_data="menu_rules")
    )
    return markup

@bot.message_handler(commands=['start'])
def start_cmd(message):
    get_or_create_user(message.from_user)
    bot.send_message(
        message.chat.id, 
        "Welcome to **Server Shop**! Manage your cloud infrastructure natively on Telegram.", 
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def handle_menus(call):
    user = get_or_create_user(call.from_user)
    action = call.data.split("_")[1]
    
    if action == "profile":
        text = f"👤 **User Profile**\n\nTelegram ID: `{user.id}`\nUsername: @{user.username}\nJoined: {user.created_at.strftime('%Y-%m-%d')}"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        
    elif action == "wallet":
        text = f"💰 **Wallet Overview**\n\nBalance: `${user.wallet.balance:.2f}`"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("➕ Add Funds (USDT)", callback_data="wallet_deposit"))
        markup.add(InlineKeyboardButton("⬅ Back", callback_data="menu_main"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif action == "buy":
        plans = Plan.query.filter_by(is_available=True).all()
        if not plans:
            bot.answer_callback_query(call.id, "No plans available right now.", show_alert=True)
            return
        markup = InlineKeyboardMarkup(row_width=1)
        for plan in plans:
            markup.add(InlineKeyboardButton(f"{plan.name} ({plan.server_type}) - ${plan.final_price}/mo", callback_data=f"buy_plan_{plan.id}"))
        markup.add(InlineKeyboardButton("⬅ Back", callback_data="menu_main"))
        bot.edit_message_text("🖥 **Select a Cloud Plan:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif action == "servers":
        servers = Server.query.filter_by(user_id=user.id).all()
        if not servers:
            bot.answer_callback_query(call.id, "You don't own any servers yet.", show_alert=True)
            return
        markup = InlineKeyboardMarkup(row_width=1)
        for srv in servers:
            status_emoji = "🟢" if srv.status == "running" else "🔴" if srv.status == "off" else "❌"
            markup.add(InlineKeyboardButton(f"{status_emoji} {srv.ip_address} | {srv.name}", callback_data=f"manage_srv_{srv.id}"))
        markup.add(InlineKeyboardButton("⬅ Back", callback_data="menu_main"))
        bot.edit_message_text("💻 **Your Cloud Instances:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        
    elif action == "main":
        bot.edit_message_text("Main Menu", call.message.chat.id, call.message.message_id, reply_markup=main_menu_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_plan_"))
def handle_purchase(call):
    user = get_or_create_user(call.from_user)
    plan_id = int(call.data.split("_")[2])
    plan = Plan.query.get(plan_id)
    
    if user.wallet.balance < plan.final_price:
        bot.answer_callback_query(call.id, "Insufficient credits. Top up your wallet.", show_alert=True)
        return
        
    active_account = HetznerAccount.query.filter_by(is_active=True).first()
    if not active_account:
        bot.answer_callback_query(call.id, "Maintenance: No API Accounts available.", show_alert=True)
        return

    bot.answer_callback_query(call.id, "Provisioning instance via API... please wait.")
    
    client = HetznerClient(api_token=active_account.api_token)
    server_name = f"srv-{user.id}-{int(datetime.now().timestamp())}"
    res = client.create_server(server_name, plan.server_type, location=plan.location)
    
    if res:
        user.wallet.balance -= plan.final_price
        tx = WalletTransaction(wallet_id=user.wallet.id, amount=-plan.final_price, tx_type="purchase", description=f"Bought {plan.name}")
        
        new_server = Server(
            id=res["id"], user_id=user.id, plan_id=plan.id, hetzner_account_id=active_account.id,
            name=server_name, ip_address=res["ip"], root_password=res["root_password"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=30)
        )
        db_session.add(tx)
        db_session.add(new_server)
        db_session.commit()
        
        msg = f"✅ **Server Provisioned!**\n\nIP: `{res['ip']}`\nUsername: `root`\nPassword: `{res['root_password']}`"
        bot.send_message(call.message.chat.id, msg, parse_mode="Markdown")
    else:
        bot.send_message(call.message.chat.id, "❌ Hetzner API Error. Please contact support.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("manage_srv_"))
def handle_management(call):
    server_id = int(call.data.split("_")[2])
    srv = Server.query.get(server_id)
    if not srv or srv.user_id != call.from_user.id: return

    markup = InlineKeyboardMarkup(row_width=2)
    
    if srv.status == "expired":
        markup.add(InlineKeyboardButton("💳 Renew Server (تمدید)", callback_data=f"action_renew_{srv.id}"))
    else:
        markup.add(
            InlineKeyboardButton("🟢 Power On", callback_data=f"action_poweron_{srv.id}"),
            InlineKeyboardButton("🔴 Power Off", callback_data=f"action_poweroff_{srv.id}"),
            InlineKeyboardButton("🔄 Reboot", callback_data=f"action_reboot_{srv.id}"),
            InlineKeyboardButton("🔑 Reset Pass", callback_data=f"action_resetpass_{srv.id}"),
            InlineKeyboardButton("💳 Extend Time", callback_data=f"action_renew_{srv.id}")
        )
        
    markup.add(InlineKeyboardButton("⬅ Back to Assets", callback_data="menu_servers"))
    
    details = f"💻 **Manage Instance**\n\nIP: `{srv.ip_address}`\nStatus: `{srv.status.upper()}`\nTraffic: {srv.traffic_used_gb:.2f} GB / {srv.traffic_limit_gb} GB\nExpires: {srv.expires_at.strftime('%Y-%m-%d %H:%M UTC')}"
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
            bot.answer_callback_query(call.id, f"Insufficient funds. You need ${plan.final_price}.", show_alert=True)
            return
            
        user.wallet.balance -= plan.final_price
        tx = WalletTransaction(wallet_id=user.wallet.id, amount=-plan.final_price, tx_type="renewal", description=f"Renewed {srv.ip_address}")
        
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
        bot.answer_callback_query(call.id, "✅ Server renewed successfully!", show_alert=True)
        handle_management(call)
        return

    account = HetznerAccount.query.get(srv.hetzner_account_id)
    if not account:
        bot.answer_callback_query(call.id, "API Account not found.", show_alert=True)
        return
        
    client = HetznerClient(api_token=account.api_token)
    api_action = "poweron" if action == "poweron" else "poweroff" if action == "poweroff" else "reboot" if action == "reboot" else "reset_password"
    
    res = client.action_server(srv.id, api_action)
    if res:
        if action in ["poweron", "poweroff"]:
            srv.status = "running" if action == "poweron" else "off"
            db_session.commit()
        bot.answer_callback_query(call.id, f"Command sent: {action.upper()}")
        handle_management(call)
    else:
        bot.answer_callback_query(call.id, "API call execution failed.", show_alert=True)

