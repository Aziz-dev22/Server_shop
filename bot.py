# -*- coding: utf-8 -*-
"""
Hetzner Shop Bot - ربات فروش و مدیریت سرور مجازی (VPS) روی Hetzner Cloud
همه چیز عمداً در یک فایل نگه داشته شده تا نصب و نگهداری برای فرد غیر برنامه‌نویس ساده باشد.
دیتابیس: SQLite (یک فایل، بدون نیاز به نصب دیتابیس جداگانه)
"""

import asyncio
import logging
import os
import secrets
import string
from datetime import datetime

import aiosqlite
import httpx
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
DB_PATH = os.getenv("DB_PATH", "shop.db")
CARD_NUMBER = os.getenv("CARD_NUMBER", "تنظیم نشده - از /admin وارد کنید")
CURRENCY = "تومان"
HETZNER_API = "https://api.hetzner.cloud/v1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("hetzner_shop_bot")

router = Router()


# ---------------------------------------------------------------------------
# دیتابیس
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    balance INTEGER DEFAULT 0,
    is_banned INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS provider (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_token TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_type TEXT,
    title TEXT,
    cpu TEXT,
    ram TEXT,
    disk TEXT,
    price INTEGER,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    hetzner_id INTEGER,
    name TEXT,
    ip TEXT,
    location TEXT,
    plan_title TEXT,
    os_image TEXT,
    root_password TEXT,
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    server_id INTEGER,
    plan_title TEXT,
    location TEXT,
    price INTEGER,
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS wallet_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    message TEXT,
    created_at TEXT
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def get_user(db, tid):
    cur = await db.execute("SELECT * FROM users WHERE telegram_id=?", (tid,))
    return await cur.fetchone()


async def ensure_user(db, tid, username, full_name):
    user = await get_user(db, tid)
    if not user:
        await db.execute(
            "INSERT INTO users (telegram_id, username, full_name, created_at) VALUES (?,?,?,?)",
            (tid, username, full_name, datetime.utcnow().isoformat()),
        )
        await db.commit()
        user = await get_user(db, tid)
    return user


async def get_provider_token(db):
    cur = await db.execute("SELECT api_token FROM provider ORDER BY id DESC LIMIT 1")
    row = await cur.fetchone()
    return row[0] if row else None


def is_admin(tid: int) -> bool:
    return tid in ADMIN_IDS


def gen_password(length=14):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def fmt_price(v: int) -> str:
    return f"{v:,} {CURRENCY}"


# ---------------------------------------------------------------------------
# کلاس اتصال به Hetzner Cloud API
# ---------------------------------------------------------------------------
class HetznerAPI:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    async def _get(self, path, params=None):
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{HETZNER_API}{path}", headers=self.headers, params=params)
            r.raise_for_status()
            return r.json()

    async def _post(self, path, json=None):
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{HETZNER_API}{path}", headers=self.headers, json=json)
            r.raise_for_status()
            return r.json()

    async def _delete(self, path):
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(f"{HETZNER_API}{path}", headers=self.headers)
            r.raise_for_status()
            return True

    async def locations(self):
        data = await self._get("/locations")
        return data.get("locations", [])

    async def server_types(self):
        data = await self._get("/server_types")
        return [t for t in data.get("server_types", []) if t.get("deprecated") is not True]

    async def images(self):
        data = await self._get("/images", params={"type": "system", "per_page": 50})
        return data.get("images", [])

    async def create_server(self, name, server_type, image, location):
        payload = {
            "name": name,
            "server_type": server_type,
            "image": image,
            "location": location,
        }
        data = await self._post("/servers", json=payload)
        return data["server"], data.get("root_password")

    async def delete_server(self, hetzner_id):
        return await self._delete(f"/servers/{hetzner_id}")

    async def power_action(self, hetzner_id, action):
        # action: poweron, poweroff, reboot
        return await self._post(f"/servers/{hetzner_id}/actions/{action}")

    async def rebuild(self, hetzner_id, image):
        data = await self._post(f"/servers/{hetzner_id}/actions/rebuild", json={"image": image})
        return data

    async def reset_password(self, hetzner_id):
        data = await self._post(f"/servers/{hetzner_id}/actions/reset_password")
        return data.get("root_password")

    async def server_status(self, hetzner_id):
        data = await self._get(f"/servers/{hetzner_id}")
        return data["server"]


# ---------------------------------------------------------------------------
# کیبوردها
# ---------------------------------------------------------------------------
def main_menu_kb(admin: bool):
    rows = [
        [KeyboardButton(text="🛒 خرید سرور"), KeyboardButton(text="🖥 سرورهای من")],
        [KeyboardButton(text="📦 سفارش‌های من"), KeyboardButton(text="💰 کیف پول")],
        [KeyboardButton(text="👤 پروفایل"), KeyboardButton(text="🆘 پشتیبانی")],
    ]
    if admin:
        rows.append([KeyboardButton(text="⚙️ پنل مدیریت")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def back_inline(cb="back_main"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data=cb)]])


def admin_menu_kb():
    rows = [
        [InlineKeyboardButton(text="👥 کاربران", callback_data="adm_users")],
        [InlineKeyboardButton(text="🖥 سرورها", callback_data="adm_servers")],
        [InlineKeyboardButton(text="📦 سفارش‌ها", callback_data="adm_orders")],
        [InlineKeyboardButton(text="📊 آمار", callback_data="adm_stats")],
        [InlineKeyboardButton(text="🧩 مدیریت پلن‌ها", callback_data="adm_plans")],
        [InlineKeyboardButton(text="🔑 اتصال Hetzner", callback_data="adm_token")],
        [InlineKeyboardButton(text="💳 درخواست‌های شارژ کیف پول", callback_data="adm_wallet_reqs")],
        [InlineKeyboardButton(text="📢 پیام همگانی", callback_data="adm_broadcast")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# وضعیت‌های مکالمه (FSM)
# ---------------------------------------------------------------------------
class BuyFlow(StatesGroup):
    location = State()
    plan = State()
    image = State()
    confirm = State()


class ChargeWallet(StatesGroup):
    amount = State()
    receipt = State()


class AdminToken(StatesGroup):
    waiting = State()


class AdminAddPlan(StatesGroup):
    choosing_type = State()
    title = State()
    price = State()


class AdminBroadcast(StatesGroup):
    waiting = State()


class SupportTicket(StatesGroup):
    waiting = State()


# ---------------------------------------------------------------------------
# شروع / منوی اصلی
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        await ensure_user(db, message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer(
        "🌐 به فروشگاه سرور مجازی خوش آمدید!\n\nاز منوی زیر یکی از گزینه‌ها را انتخاب کنید.",
        reply_markup=main_menu_kb(is_admin(message.from_user.id)),
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("⚙️ پنل مدیریت", reply_markup=admin_menu_kb())


@router.message(F.text == "⚙️ پنل مدیریت")
async def open_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("⚙️ پنل مدیریت", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await call.message.answer("منوی اصلی:", reply_markup=main_menu_kb(is_admin(call.from_user.id)))


# ---------------------------------------------------------------------------
# پروفایل
# ---------------------------------------------------------------------------
@router.message(F.text == "👤 پروفایل")
async def profile(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        user = await ensure_user(db, message.from_user.id, message.from_user.username, message.from_user.full_name)
        cur = await db.execute("SELECT COUNT(*) FROM servers WHERE user_id=?", (message.from_user.id,))
        server_count = (await cur.fetchone())[0]
    await message.answer(
        f"👤 پروفایل شما\n\n"
        f"شناسه تلگرام: {message.from_user.id}\n"
        f"نام: {user[2]}\n"
        f"موجودی کیف پول: {fmt_price(user[3])}\n"
        f"تعداد سرورها: {server_count}"
    )


# ---------------------------------------------------------------------------
# کیف پول
# ---------------------------------------------------------------------------
@router.message(F.text == "💰 کیف پول")
async def wallet_menu(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        user = await ensure_user(db, message.from_user.id, message.from_user.username, message.from_user.full_name)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="➕ افزایش موجودی", callback_data="wallet_charge")]]
    )
    await message.answer(f"💰 موجودی فعلی شما: {fmt_price(user[3])}", reply_markup=kb)


@router.callback_query(F.data == "wallet_charge")
async def wallet_charge_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(ChargeWallet.amount)
    await call.message.answer(
        f"مبلغ مورد نظر برای شارژ را به {CURRENCY} وارد کنید (فقط عدد):"
    )
    await call.answer()


@router.message(ChargeWallet.amount)
async def wallet_charge_amount(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("لطفاً فقط عدد وارد کنید.")
        return
    await state.update_data(amount=int(message.text))
    await state.set_state(ChargeWallet.receipt)
    await message.answer(
        f"مبلغ {fmt_price(int(message.text))} را به شماره کارت زیر واریز کنید و سپس عکس رسید را ارسال نمایید:\n\n"
        f"💳 {CARD_NUMBER}"
    )


@router.message(ChargeWallet.receipt, F.photo)
async def wallet_charge_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_requests (user_id, amount, created_at) VALUES (?,?,?)",
            (message.from_user.id, amount, datetime.utcnow().isoformat()),
        )
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        req_id = (await cur.fetchone())[0]
    await state.clear()
    await message.answer("✅ درخواست شارژ ثبت شد و پس از تایید ادمین به کیف پول شما اضافه می‌شود.")
    for admin_id in ADMIN_IDS:
        try:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ تایید", callback_data=f"wr_ok_{req_id}"),
                        InlineKeyboardButton(text="❌ رد", callback_data=f"wr_no_{req_id}"),
                    ]
                ]
            )
            await message.bot.send_photo(
                admin_id,
                message.photo[-1].file_id,
                caption=f"درخواست شارژ کیف پول\nکاربر: {message.from_user.id}\nمبلغ: {fmt_price(amount)}",
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning(f"cannot notify admin {admin_id}: {e}")


@router.callback_query(F.data.startswith("wr_ok_") | F.data.startswith("wr_no_"))
async def wallet_request_decision(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    approve = call.data.startswith("wr_ok_")
    req_id = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, amount, status FROM wallet_requests WHERE id=?", (req_id,))
        row = await cur.fetchone()
        if not row or row[2] != "pending":
            await call.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
            return
        user_id, amount, _ = row
        new_status = "approved" if approve else "rejected"
        await db.execute("UPDATE wallet_requests SET status=? WHERE id=?", (new_status, req_id))
        if approve:
            await db.execute("UPDATE users SET balance = balance + ? WHERE telegram_id=?", (amount, user_id))
        await db.commit()
    await call.message.edit_caption(caption=call.message.caption + f"\n\nوضعیت: {'✅ تایید شد' if approve else '❌ رد شد'}")
    try:
        if approve:
            await call.bot.send_message(user_id, f"✅ کیف پول شما به مبلغ {fmt_price(amount)} شارژ شد.")
        else:
            await call.bot.send_message(user_id, "❌ درخواست شارژ کیف پول شما رد شد.")
    except Exception:
        pass
    await call.answer()


# ---------------------------------------------------------------------------
# پشتیبانی
# ---------------------------------------------------------------------------
@router.message(F.text == "🆘 پشتیبانی")
async def support_start(message: Message, state: FSMContext):
    await state.set_state(SupportTicket.waiting)
    await message.answer("پیام خود را برای تیم پشتیبانی بنویسید:")


@router.message(SupportTicket.waiting)
async def support_receive(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tickets (user_id, message, created_at) VALUES (?,?,?)",
            (message.from_user.id, message.text, datetime.utcnow().isoformat()),
        )
        await db.commit()
    await state.clear()
    await message.answer("✅ پیام شما ثبت شد. به زودی پاسخ داده می‌شود.")
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                f"🆘 تیکت پشتیبانی جدید\nکاربر: {message.from_user.id} (@{message.from_user.username})\n\n{message.text}",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# خرید سرور
# ---------------------------------------------------------------------------
@router.message(F.text == "🛒 خرید سرور")
async def buy_start(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_provider_token(db)
    if not token:
        await message.answer("در حال حاضر امکان خرید فعال نیست (اتصال Hetzner توسط ادمین تنظیم نشده).")
        return
    api = HetznerAPI(token)
    try:
        locations = await api.locations()
    except Exception as e:
        await message.answer(f"خطا در دریافت لیست دیتاسنترها: {e}")
        return
    if not locations:
        await message.answer("در حال حاضر دیتاسنتری در دسترس نیست.")
        return
    kb_rows = [
        [InlineKeyboardButton(text=f"{loc['city']} ({loc['country']})", callback_data=f"loc_{loc['name']}")]
        for loc in locations
    ]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")])
    await state.set_state(BuyFlow.location)
    await message.answer("📍 یک دیتاسنتر انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(BuyFlow.location, F.data.startswith("loc_"))
async def buy_location(call: CallbackQuery, state: FSMContext):
    location = call.data.replace("loc_", "")
    await state.update_data(location=location)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, title, cpu, ram, disk, price FROM plans WHERE active=1")
        plans = await cur.fetchall()
    if not plans:
        await call.message.answer("در حال حاضر پلنی برای فروش تعریف نشده است.")
        await call.answer()
        return
    kb_rows = [
        [
            InlineKeyboardButton(
                text=f"{p[1]} | {p[2]} CPU / {p[3]} RAM / {p[4]} - {fmt_price(p[5])}",
                callback_data=f"plan_{p[0]}",
            )
        ]
        for p in plans
    ]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")])
    await state.set_state(BuyFlow.plan)
    await call.message.edit_text("📦 یک پلن انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(BuyFlow.plan, F.data.startswith("plan_"))
async def buy_plan(call: CallbackQuery, state: FSMContext):
    plan_id = int(call.data.replace("plan_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, server_type, title, price FROM plans WHERE id=?", (plan_id,))
        plan = await cur.fetchone()
        token = await get_provider_token(db)
    if not plan or not token:
        await call.answer("خطا در دریافت اطلاعات پلن", show_alert=True)
        return
    await state.update_data(plan_id=plan[0], server_type=plan[1], plan_title=plan[2], price=plan[3])
    api = HetznerAPI(token)
    try:
        images = await api.images()
    except Exception as e:
        await call.message.answer(f"خطا در دریافت سیستم‌عامل‌ها: {e}")
        await call.answer()
        return
    kb_rows = [
        [InlineKeyboardButton(text=f"{img.get('name')}", callback_data=f"img_{img['id']}")]
        for img in images[:20]
    ]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")])
    await state.set_state(BuyFlow.image)
    await call.message.edit_text("💿 سیستم‌عامل را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(BuyFlow.image, F.data.startswith("img_"))
async def buy_image(call: CallbackQuery, state: FSMContext):
    image_id = call.data.replace("img_", "")
    await state.update_data(image=image_id)
    data = await state.get_data()
    text = (
        "🧾 خلاصه سفارش:\n\n"
        f"دیتاسنتر: {data['location']}\n"
        f"پلن: {data['plan_title']}\n"
        f"قیمت: {fmt_price(data['price'])}\n\n"
        "آیا تایید می‌کنید؟"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ تایید و خرید", callback_data="confirm_buy")],
            [InlineKeyboardButton(text="❌ انصراف", callback_data="back_main")],
        ]
    )
    await state.set_state(BuyFlow.confirm)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(BuyFlow.confirm, F.data == "confirm_buy")
async def buy_confirm(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = call.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        user = await get_user(db, user_id)
        if user[3] < data["price"]:
            await call.message.edit_text("❌ موجودی کیف پول شما کافی نیست. ابتدا کیف پول را شارژ کنید.")
            await state.clear()
            await call.answer()
            return
        token = await get_provider_token(db)

    await call.message.edit_text("⏳ در حال ساخت سرور شما... لطفاً چند لحظه صبر کنید.")
    api = HetznerAPI(token)
    server_name = f"srv-{user_id}-{secrets.token_hex(2)}"
    try:
        server, root_password = await api.create_server(
            name=server_name,
            server_type=data["server_type"],
            image=data["image"],
            location=data["location"],
        )
    except Exception as e:
        await call.message.answer(f"❌ خطا در ساخت سرور: {e}")
        await state.clear()
        await call.answer()
        return

    if not root_password:
        root_password = "از طریق Reset Password در بخش سرورهای من دریافت کنید"

    ip = server.get("public_net", {}).get("ipv4", {}).get("ip", "در حال تخصیص")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE telegram_id=?", (data["price"], user_id))
        await db.execute(
            "INSERT INTO servers (user_id, hetzner_id, name, ip, location, plan_title, os_image, root_password, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                user_id,
                server["id"],
                server_name,
                ip,
                data["location"],
                data["plan_title"],
                data["image"],
                root_password,
                "running",
                datetime.utcnow().isoformat(),
            ),
        )
        cur = await db.execute("SELECT last_insert_rowid()")
        server_db_id = (await cur.fetchone())[0]
        await db.execute(
            "INSERT INTO orders (user_id, server_id, plan_title, location, price, status, created_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, server_db_id, data["plan_title"], data["location"], data["price"], "completed", datetime.utcnow().isoformat()),
        )
        await db.commit()

    await state.clear()
    await call.message.answer(
        "✅ سرور شما با موفقیت ساخته شد!\n\n"
        f"نام: {server_name}\n"
        f"آی‌پی: {ip}\n"
        f"یوزر: root\n"
        f"پسورد: {root_password}\n\n"
        "برای مدیریت سرور به بخش «سرورهای من» بروید."
    )
    await call.answer()


# ---------------------------------------------------------------------------
# سرورهای من
# ---------------------------------------------------------------------------
@router.message(F.text == "🖥 سرورهای من")
async def my_servers(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, name, ip, location, plan_title, status FROM servers WHERE user_id=? ORDER BY id DESC",
            (message.from_user.id,),
        )
        servers = await cur.fetchall()
    if not servers:
        await message.answer("شما هنوز سروری خریداری نکرده‌اید.")
        return
    for s in servers:
        text = (
            f"🖥 {s[1]}\n"
            f"آی‌پی: {s[2]}\n"
            f"دیتاسنتر: {s[3]}\n"
            f"پلن: {s[4]}\n"
            f"وضعیت: {s[5]}"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="▶️ روشن", callback_data=f"sv_on_{s[0]}"),
                    InlineKeyboardButton(text="⏹ خاموش", callback_data=f"sv_off_{s[0]}"),
                    InlineKeyboardButton(text="🔄 ریبوت", callback_data=f"sv_reboot_{s[0]}"),
                ],
                [
                    InlineKeyboardButton(text="🔑 تغییر پسورد", callback_data=f"sv_pass_{s[0]}"),
                    InlineKeyboardButton(text="🗑 حذف", callback_data=f"sv_del_{s[0]}"),
                ],
            ]
        )
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("sv_"))
async def server_action(call: CallbackQuery):
    _, action, server_db_id = call.data.split("_")
    server_db_id = int(server_db_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, hetzner_id, name FROM servers WHERE id=?", (server_db_id,)
        )
        row = await cur.fetchone()
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return
        token = await get_provider_token(db)
    api = HetznerAPI(token)
    hetzner_id = row[1]
    try:
        if action == "on":
            await api.power_action(hetzner_id, "poweron")
            await call.answer("✅ دستور روشن کردن ارسال شد")
        elif action == "off":
            await api.power_action(hetzner_id, "poweroff")
            await call.answer("✅ دستور خاموش کردن ارسال شد")
        elif action == "reboot":
            await api.power_action(hetzner_id, "reboot")
            await call.answer("✅ دستور ریبوت ارسال شد")
        elif action == "pass":
            new_pass = await api.reset_password(hetzner_id)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE servers SET root_password=? WHERE id=?", (new_pass, server_db_id))
                await db.commit()
            await call.message.answer(f"🔑 پسورد جدید سرور {row[2]}:\n`{new_pass}`", parse_mode=ParseMode.MARKDOWN)
            await call.answer()
        elif action == "del":
            await api.delete_server(hetzner_id)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM servers WHERE id=?", (server_db_id,))
                await db.commit()
            await call.message.edit_text(f"🗑 سرور {row[2]} حذف شد.")
            await call.answer()
    except Exception as e:
        await call.message.answer(f"❌ خطا: {e}")
        await call.answer()


# ---------------------------------------------------------------------------
# سفارش‌های من
# ---------------------------------------------------------------------------
@router.message(F.text == "📦 سفارش‌های من")
async def my_orders(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT plan_title, location, price, status, created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (message.from_user.id,),
        )
        orders = await cur.fetchall()
    if not orders:
        await message.answer("سفارشی ثبت نشده است.")
        return
    lines = ["📦 سفارش‌های اخیر شما:\n"]
    for o in orders:
        lines.append(f"• {o[0]} | {o[1]} | {fmt_price(o[2])} | {o[3]}")
    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# پنل ادمین: کاربران / سرورها / سفارش‌ها / آمار
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "adm_stats")
async def adm_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        users_c = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        servers_c = (await (await db.execute("SELECT COUNT(*) FROM servers")).fetchone())[0]
        orders_c = (await (await db.execute("SELECT COUNT(*) FROM orders")).fetchone())[0]
        revenue = (await (await db.execute("SELECT COALESCE(SUM(price),0) FROM orders")).fetchone())[0]
    await call.message.edit_text(
        f"📊 آمار کلی\n\nکاربران: {users_c}\nسرورها: {servers_c}\nسفارش‌ها: {orders_c}\nدرآمد کل: {fmt_price(revenue)}",
        reply_markup=back_inline("adm_back"),
    )
    await call.answer()


@router.callback_query(F.data == "adm_back")
async def adm_back(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    await call.message.edit_text("⚙️ پنل مدیریت", reply_markup=admin_menu_kb())
    await call.answer()


@router.callback_query(F.data == "adm_users")
async def adm_users(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT telegram_id, full_name, balance FROM users ORDER BY telegram_id DESC LIMIT 30")
        users = await cur.fetchall()
    lines = ["👥 آخرین کاربران:\n"]
    for u in users:
        lines.append(f"• {u[1]} | {u[0]} | موجودی: {fmt_price(u[2])}")
    await call.message.edit_text("\n".join(lines), reply_markup=back_inline("adm_back"))
    await call.answer()


@router.callback_query(F.data == "adm_servers")
async def adm_servers(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name, ip, user_id, status FROM servers ORDER BY id DESC LIMIT 30")
        servers = await cur.fetchall()
    lines = ["🖥 آخرین سرورها:\n"]
    for s in servers:
        lines.append(f"• {s[0]} | {s[1]} | کاربر: {s[2]} | {s[3]}")
    await call.message.edit_text("\n".join(lines) if servers else "سروری ثبت نشده است.", reply_markup=back_inline("adm_back"))
    await call.answer()


@router.callback_query(F.data == "adm_orders")
async def adm_orders(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT plan_title, user_id, price, status FROM orders ORDER BY id DESC LIMIT 30")
        orders = await cur.fetchall()
    lines = ["📦 آخرین سفارش‌ها:\n"]
    for o in orders:
        lines.append(f"• {o[0]} | کاربر: {o[1]} | {fmt_price(o[2])} | {o[3]}")
    await call.message.edit_text("\n".join(lines) if orders else "سفارشی ثبت نشده است.", reply_markup=back_inline("adm_back"))
    await call.answer()


@router.callback_query(F.data == "adm_wallet_reqs")
async def adm_wallet_reqs(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, user_id, amount FROM wallet_requests WHERE status='pending' ORDER BY id DESC LIMIT 20"
        )
        reqs = await cur.fetchall()
    if not reqs:
        await call.message.edit_text("درخواست شارژ در انتظاری وجود ندارد.", reply_markup=back_inline("adm_back"))
        await call.answer()
        return
    lines = ["💳 درخواست‌های شارژ در انتظار:\n"]
    for r in reqs:
        lines.append(f"• #{r[0]} | کاربر: {r[1]} | مبلغ: {fmt_price(r[2])}")
    await call.message.edit_text("\n".join(lines), reply_markup=back_inline("adm_back"))
    await call.answer()


# ---------------------------------------------------------------------------
# پنل ادمین: اتصال Hetzner
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "adm_token")
async def adm_token_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminToken.waiting)
    await call.message.answer(
        "🔑 توکن API هتزنر را ارسال کنید.\n"
        "(از پنل Hetzner Cloud > Security > API Tokens با دسترسی Read & Write بسازید)"
    )
    await call.answer()


@router.message(AdminToken.waiting)
async def adm_token_save(message: Message, state: FSMContext):
    token = message.text.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO provider (api_token, created_at) VALUES (?,?)", (token, datetime.utcnow().isoformat()))
        await db.commit()
    await state.clear()
    api = HetznerAPI(token)
    try:
        await api.locations()
        await message.answer("✅ اتصال به Hetzner با موفقیت برقرار شد.")
    except Exception as e:
        await message.answer(f"⚠️ توکن ذخیره شد اما تست اتصال ناموفق بود: {e}")


# ---------------------------------------------------------------------------
# پنل ادمین: مدیریت پلن‌ها
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "adm_plans")
async def adm_plans(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, title, price, active FROM plans ORDER BY id")
        plans = await cur.fetchall()
    lines = ["🧩 پلن‌های فعلی:\n"]
    for p in plans:
        lines.append(f"• #{p[0]} {p[1]} - {fmt_price(p[2])} {'✅' if p[3] else '❌'}")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزودن پلن جدید", callback_data="adm_plan_add")],
            [InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")],
        ]
    )
    await call.message.edit_text("\n".join(lines) if plans else "پلنی تعریف نشده.", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "adm_plan_add")
async def adm_plan_add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_provider_token(db)
    if not token:
        await call.message.answer("ابتدا باید اتصال Hetzner را تنظیم کنید.")
        await call.answer()
        return
    api = HetznerAPI(token)
    try:
        types = await api.server_types()
    except Exception as e:
        await call.message.answer(f"خطا در دریافت لیست پلن‌های هتزنر: {e}")
        await call.answer()
        return
    kb_rows = [
        [
            InlineKeyboardButton(
                text=f"{t['name']} | {t['cores']} CPU / {t['memory']}GB RAM / {t['disk']}GB",
                callback_data=f"adpt_{t['name']}_{t['cores']}_{t['memory']}_{t['disk']}",
            )
        ]
        for t in types[:25]
    ]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")])
    await state.set_state(AdminAddPlan.choosing_type)
    await call.message.edit_text("یک نوع سرور Hetzner انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(AdminAddPlan.choosing_type, F.data.startswith("adpt_"))
async def adm_plan_choose_type(call: CallbackQuery, state: FSMContext):
    _, name, cores, memory, disk = call.data.split("_")
    await state.update_data(server_type=name, cpu=f"{cores} core", ram=f"{memory} GB", disk=f"{disk} GB")
    await state.set_state(AdminAddPlan.title)
    await call.message.edit_text("یک عنوان نمایشی برای این پلن بنویسید (مثلاً: پلن اقتصادی):")
    await call.answer()


@router.message(AdminAddPlan.title)
async def adm_plan_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(AdminAddPlan.price)
    await message.answer(f"قیمت این پلن را به {CURRENCY} وارد کنید (فقط عدد):")


@router.message(AdminAddPlan.price)
async def adm_plan_price(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("لطفاً فقط عدد وارد کنید.")
        return
    data = await state.get_data()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO plans (server_type, title, cpu, ram, disk, price, active) VALUES (?,?,?,?,?,?,1)",
            (data["server_type"], data["title"], data["cpu"], data["ram"], data["disk"], int(message.text)),
        )
        await db.commit()
    await state.clear()
    await message.answer("✅ پلن جدید اضافه شد.", reply_markup=main_menu_kb(True))


# ---------------------------------------------------------------------------
# پنل ادمین: پیام همگانی
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminBroadcast.waiting)
    await call.message.answer("متن پیام همگانی را ارسال کنید:")
    await call.answer()


@router.message(AdminBroadcast.waiting)
async def adm_broadcast_send(message: Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT telegram_id FROM users")
        users = await cur.fetchall()
    sent, failed = 0, 0
    status_msg = await message.answer("⏳ در حال ارسال...")
    for (uid,) in users:
        try:
            await message.bot.send_message(uid, message.text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"✅ ارسال شد به {sent} کاربر ({failed} ناموفق).")


# ---------------------------------------------------------------------------
# اجرا
# ---------------------------------------------------------------------------
async def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN تنظیم نشده است. فایل .env را بررسی کنید.")
    await init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("Bot started.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

