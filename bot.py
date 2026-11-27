# -*- coding: utf-8 -*-
"""
Hetzner Shop Bot v2 - ربات فروش و مدیریت سرور مجازی (VPS) روی Hetzner Cloud
همه چیز عمداً در یک فایل نگه داشته شده تا نصب و نگهداری برای فرد غیر برنامه‌نویس ساده باشد.
دیتابیس: SQLite (یک فایل، بدون نیاز به نصب دیتابیس جداگانه)

امکانات نسخه ۲:
- قیمت‌ها به دلار و به‌صورت خودکار از خود اکانت Hetzner خوانده می‌شوند (ادمین فقط درصد سود مشخص می‌کند)
- درگاه پرداخت خودکار ارز دیجیتال (OxaPay) + روش دستی (کارت/ولت که ادمین تنظیم می‌کند)
- مدیریت چند API Key هتزنر (افزودن/فعال‌سازی/حذف)
- دسترسی کامل ادمین به همه‌ی سرورها + امکان ساخت سرور رایگان توسط ادمین
"""

import asyncio
import json
import time
import html
import hashlib
import logging
import math
import os
import secrets
import zipfile
import tempfile
from urllib.parse import urlencode
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiosqlite
import httpx
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
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
    FSInputFile,
)
from aiogram import BaseMiddleware
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
DB_PATH = os.getenv("DB_PATH", "shop.db")
HETZNER_API = "https://api.hetzner.cloud/v1"
OXAPAY_API = "https://api.oxapay.com/v1"
NOWPAYMENTS_API = "https://api.nowpayments.io/v1"
NOWPAYMENTS_PAY_CURRENCY = "usdttrc20"  # ارز پرداختی ثابت: تتر روی شبکه ترون (کارمزد کم و پرکاربرد)

IP_CHANGE_FREE_COUNT = 2       # تعداد دفعات رایگان تغییر IP برای هر سرور
IP_CHANGE_PRICE = 0.5          # هزینه هر تغییر IP بعد از دفعات رایگان (دلار)
RENEWAL_PERIOD_DAYS = 30       # طول هر دوره تمدید سرویس
TRAFFIC_INCLUDED_BYTES = 20 * 1_000_000_000_000  # حجم بسته: ۲۰ ترابایت
IRAN_TZ = ZoneInfo("Asia/Tehran")

def iran_time(value):
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IRAN_TZ)
    except Exception:
        return None

TRAFFIC_SOFT_LIMIT_BYTES = int(19.5 * 1_000_000_000_000)  # هشدار اولیه در ۱۹.۵ ترابایت
EXPIRY_REMINDER_HOURS_BEFORE = 48   # چند ساعت قبل از پایان مهلت، یادآوری تمدید ارسال شود
EXPIRY_GRACE_HOURS = 2               # چند ساعت بعد از پایان مهلت، قبل از حذف کامل سرور صبر شود
MAINTENANCE_INTERVAL_SECONDS = 15 * 60  # بررسی مصرف و مهلت هر ۱۵ دقیقه
DEFAULT_EUR_USD_RATE = 1.08  # فقط در صورتی که دریافت نرخ زنده ناموفق باشد

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("hetzner_shop_bot")

router = Router()


@router.message(StateFilter("*"), F.text.in_({"❌ لغو عملیات", "لغو", "/cancel"}))
async def universal_fsm_cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        await message.answer("عملیات فعالی برای لغو وجود ندارد.", reply_markup=main_menu_kb(is_admin(message.from_user.id)))
        return
    await state.clear()
    await message.answer("❌ عملیات لغو شد. به منوی اصلی برگشتید.", reply_markup=main_menu_kb(is_admin(message.from_user.id)))


class AdminSupportSettings(StatesGroup):
    waiting_id = State()
    waiting_message = State()


# ---------------------------------------------------------------------------
# دیتابیس
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    balance REAL DEFAULT 0,
    is_banned INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS provider (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT,
    api_token TEXT,
    active INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_type TEXT,
    title TEXT,
    cpu TEXT,
    ram TEXT,
    disk TEXT,
    markup_percent REAL DEFAULT 20,
    active INTEGER DEFAULT 1,
    location TEXT
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
    price REAL,
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS wallet_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount REAL,
    method TEXT DEFAULT 'manual',
    track_id TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    message TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS pending_ip_cleanup (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER,
    provider_type TEXT DEFAULT 'hetzner',
    ip_id INTEGER,
    ip_address TEXT,
    cleanup_at TEXT
);
CREATE TABLE IF NOT EXISTS product_categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, active INTEGER DEFAULT 1, created_at TEXT);
CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER, name TEXT NOT NULL, description TEXT DEFAULT '', image_url TEXT DEFAULT '', price_usd REAL NOT NULL DEFAULT 0, price_toman INTEGER NOT NULL DEFAULT 0, stock INTEGER NOT NULL DEFAULT 0, active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT, delivery_mode TEXT DEFAULT 'manual');
CREATE TABLE IF NOT EXISTS product_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, product_name TEXT NOT NULL, quantity INTEGER NOT NULL, customer_name TEXT NOT NULL, mobile TEXT NOT NULL, email TEXT DEFAULT '', amount_usd REAL NOT NULL DEFAULT 0, amount_toman INTEGER NOT NULL DEFAULT 0, status TEXT DEFAULT 'pending', authority TEXT UNIQUE, ref_id TEXT, created_at TEXT, paid_at TEXT, delivery_note TEXT DEFAULT '', delivery_token_hash TEXT);
CREATE TABLE IF NOT EXISTS product_inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, payload TEXT NOT NULL, status TEXT DEFAULT 'available', order_id INTEGER, created_at TEXT, delivered_at TEXT);
CREATE TABLE IF NOT EXISTS customer_login_codes (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, code_hash TEXT NOT NULL, expires_at TEXT NOT NULL, attempts INTEGER DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS customer_web_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE, telegram_id INTEGER NOT NULL, redirect_path TEXT DEFAULT '/shop', expires_at TEXT NOT NULL, used_at TEXT);
CREATE TABLE IF NOT EXISTS wallet_transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount_usd REAL NOT NULL, kind TEXT NOT NULL, reference TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admin_server_renewals (id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL, owner_id INTEGER NOT NULL, admin_id INTEGER NOT NULL, days INTEGER NOT NULL, amount_usd REAL NOT NULL, balance_after REAL NOT NULL, created_at TEXT NOT NULL, reference TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS admin_wallet_actions (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_actor TEXT NOT NULL, target_user_id INTEGER NOT NULL, action TEXT NOT NULL, amount_usd REAL NOT NULL, reference TEXT NOT NULL UNIQUE, details TEXT DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discount_coupons (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 code TEXT NOT NULL UNIQUE COLLATE NOCASE,
 applies_to TEXT NOT NULL,
 discount_type TEXT NOT NULL,
 discount_value REAL NOT NULL,
 min_order_usd REAL NOT NULL DEFAULT 0,
 max_uses INTEGER,
 max_uses_per_user INTEGER,
 starts_at TEXT,
 expires_at TEXT,
 active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coupon_redemptions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 coupon_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL,
 order_type TEXT NOT NULL,
 order_id INTEGER NOT NULL,
 discount_usd REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'reserved',
 created_at TEXT NOT NULL,
 UNIQUE(order_type,order_id)
);
CREATE TABLE IF NOT EXISTS web_server_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, provider_type TEXT NOT NULL, location TEXT NOT NULL, plan_id INTEGER NOT NULL, plan_title TEXT NOT NULL, server_type TEXT NOT NULL, image TEXT NOT NULL, price_usd REAL NOT NULL, wallet_used_usd REAL DEFAULT 0, wallet_refunded INTEGER DEFAULT 0, gateway_method TEXT DEFAULT 'wallet', gateway_track_id TEXT, authority TEXT UNIQUE, gateway_due_usd REAL DEFAULT 0, gateway_due_toman INTEGER DEFAULT 0, status TEXT DEFAULT 'pending_payment', server_db_id INTEGER, created_at TEXT NOT NULL, paid_at TEXT);
CREATE TABLE IF NOT EXISTS dynamic_buttons (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 menu_key TEXT NOT NULL,
 label TEXT NOT NULL,
 action TEXT NOT NULL,
 payload TEXT DEFAULT '',
 sort_order INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dynamic_buttons_menu ON dynamic_buttons(menu_key,active,sort_order,id);

""" 

async def reserve_discount_coupon(db, code, applies_to, user_id, subtotal_usd, order_type, order_id):
    """Validate a coupon and reserve its usage atomically inside the caller's transaction."""
    code = str(code or "").strip().upper()
    if not code:
        return 0, None, ""
    now = datetime.utcnow().isoformat()
    row = await (await db.execute(
        "SELECT id,code,applies_to,discount_type,discount_value,min_order_usd,max_uses,max_uses_per_user,starts_at,expires_at,active "
        "FROM discount_coupons WHERE code=? COLLATE NOCASE", (code,)
    )).fetchone()
    if not row or not row[10]:
        return 0, None, "کد تخفیف معتبر یا فعال نیست."
    cid, saved_code, scope, kind, value, minimum, max_uses, per_user, starts, expires, active = row
    allowed_scopes = {part.strip() for part in str(scope or "").split(",") if part.strip()}
    if applies_to not in allowed_scopes:
        return 0, None, "این کد تخفیف برای این نوع خرید قابل استفاده نیست."
    if starts and now < starts:
        return 0, None, "زمان شروع اعتبار این کد هنوز نرسیده است."
    if expires and now > expires:
        return 0, None, "اعتبار این کد تخفیف به پایان رسیده است."
    subtotal = max(0.0, float(subtotal_usd or 0))
    if subtotal + 1e-8 < float(minimum or 0):
        return 0, None, "حداقل مبلغ سفارش برای این کد $" + format(float(minimum), ".2f") + " است."
    used = await (await db.execute(
        "SELECT COUNT(*) FROM coupon_redemptions WHERE coupon_id=? AND status IN ('reserved','used')", (cid,)
    )).fetchone()
    if max_uses is not None and int(used[0]) >= int(max_uses):
        return 0, None, "ظرفیت استفاده از این کد تخفیف تکمیل شده است."
    personal = await (await db.execute(
        "SELECT COUNT(*) FROM coupon_redemptions WHERE coupon_id=? AND user_id=? AND status IN ('reserved','used')",
        (cid, user_id)
    )).fetchone()
    if per_user is not None and int(personal[0]) >= int(per_user):
        return 0, None, "سقف استفاده شما از این کد تخفیف تکمیل شده است."
    discount = subtotal * float(value) / 100.0 if kind == "percent" else float(value)
    discount = min(subtotal, max(0.0, discount))
    if discount <= 0:
        return 0, None, "مقدار تخفیف این کد معتبر نیست."
    await db.execute(
        "INSERT INTO coupon_redemptions(coupon_id,user_id,order_type,order_id,discount_usd,status,created_at) VALUES(?,?,?,?,?,'reserved',?)",
        (cid, user_id, order_type, order_id, round(discount, 6), now)
    )
    return round(discount, 6), cid, ""

async def finalize_coupon_redemption(db, order_type, order_id, success=True):
    status = "used" if success else "released"
    await db.execute(
        "UPDATE coupon_redemptions SET status=? WHERE order_type=? AND order_id=? AND status='reserved'",
        (status, order_type, order_id)
    )


async def _safe_add_column(db, table, coldef):
    try:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
    except Exception:
        pass


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        seed = await (await db.execute("SELECT value FROM settings WHERE key='account_rules_button_seeded'")).fetchone()
        if not seed or seed[0] != "1":
            await db.execute(
                "INSERT INTO dynamic_buttons(menu_key,label,action,payload,sort_order,active,created_at) VALUES(?,?,?,?,?,1,?)",
                ("account_shop", "📜 قوانین خرید اکانت", "text",
                 "قوانین خرید اکانت هنوز توسط مدیریت تکمیل نشده است. لطفاً پیش از خرید، با پشتیبانی هماهنگ کنید.",
                 0, datetime.utcnow().isoformat())
            )
            await db.execute(
                "INSERT INTO settings(key,value) VALUES('account_rules_button_seeded','1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_coupon ON coupon_redemptions(coupon_id,status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_user ON coupon_redemptions(coupon_id,user_id,status)")
        # کد کمپین CLOUD7 واحد است و برای خرید و تمدید سرور معتبر می‌ماند.
        await db.execute("""INSERT INTO discount_coupons(code,applies_to,discount_type,discount_value,min_order_usd,active,created_at)
            VALUES('CLOUD7','server_purchase,server_renewal','percent',7,0,1,?)
            ON CONFLICT(code) DO UPDATE SET applies_to='server_purchase,server_renewal',discount_type='percent',discount_value=7""",
            (datetime.utcnow().isoformat(),))
        # سازگاری با نصب‌های قبلی (اضافه کردن ستون‌های جدید در صورت نبود)
        await _safe_add_column(db, "provider", "label TEXT")
        await _safe_add_column(db, "provider", "active INTEGER DEFAULT 0")
        await _safe_add_column(db, "plans", "markup_percent REAL DEFAULT 20")
        await _safe_add_column(db, "products", "delivery_mode TEXT DEFAULT 'manual'")
        await _safe_add_column(db, "product_inventory", "image_url TEXT DEFAULT ''")
        await _safe_add_column(db, "products", "price_usd REAL NOT NULL DEFAULT 0")
        await _safe_add_column(db, "product_orders", "amount_usd REAL NOT NULL DEFAULT 0")
        await _safe_add_column(db, "product_orders", "delivery_token_hash TEXT")
        await _safe_add_column(db, "users", "email TEXT")
        await _safe_add_column(db, "users", "email_verified INTEGER DEFAULT 0")
        await _safe_add_column(db, "product_orders", "user_id INTEGER")
        await _safe_add_column(db, "product_orders", "wallet_used_usd REAL DEFAULT 0")
        await _safe_add_column(db, "product_orders", "wallet_refunded INTEGER DEFAULT 0")
        await _safe_add_column(db, "product_orders", "gateway_method TEXT DEFAULT 'zarinpal'")
        await _safe_add_column(db, "product_orders", "gateway_track_id TEXT")
        await _safe_add_column(db, "product_orders", "gateway_due_toman INTEGER DEFAULT 0")
        await _safe_add_column(db, "product_orders", "currency_rate REAL DEFAULT 100000")
        await _safe_add_column(db, "web_server_orders", "gateway_due_toman INTEGER DEFAULT 0")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users(email) WHERE email IS NOT NULL AND email != ''")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_product_orders_status_gateway ON product_orders(status,gateway_method)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_web_server_orders_status_gateway ON web_server_orders(status,gateway_method)")
        await db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('wallet_usd_to_toman','100000')")
        await db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('wallet_rate_mode','auto')")
        # Legacy prices were stored in Toman; convert them once using the configured rate.
        _rate_row = await (await db.execute("SELECT value FROM settings WHERE key='wallet_usd_to_toman'")).fetchone()
        _rate = float((_rate_row[0] if _rate_row else 100000) or 100000)
        if _rate > 0:
            await db.execute("UPDATE products SET price_usd=ROUND(price_toman / ?, 6) WHERE price_usd=0 AND price_toman>0", (_rate,))
            await db.execute("UPDATE product_orders SET amount_usd=ROUND(amount_toman / ?, 6) WHERE amount_usd=0 AND amount_toman>0", (_rate,))
        await _safe_add_column(db, "wallet_requests", "method TEXT DEFAULT 'manual'")
        await _safe_add_column(db, "wallet_requests", "track_id TEXT")
        await _safe_add_column(db, "tickets", "status TEXT DEFAULT 'open'")
        await _safe_add_column(db, "tickets", "reply TEXT")
        await _safe_add_column(db, "tickets", "replied_at TEXT")
        await _safe_add_column(db, "plans", "location TEXT")
        await _safe_add_column(db, "servers", "provider_id INTEGER")
        await _safe_add_column(db, "servers", "server_type TEXT")
        await _safe_add_column(db, "servers", "markup_percent REAL")
        await _safe_add_column(db, "servers", "expires_at TEXT")
        await _safe_add_column(db, "servers", "ip_change_count INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "traffic_powered_off INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "expired_powered_off_at TEXT")
        await _safe_add_column(db, "servers", "monthly_price REAL")
        await _safe_add_column(db, "servers", "deletion_pending_reason TEXT")
        await _safe_add_column(db, "servers", "deletion_requested_at TEXT")
        await _safe_add_column(db, "servers", "deletion_notified INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "previous_ip_id INTEGER")
        await _safe_add_column(db, "servers", "previous_ip_address TEXT")
        await _safe_add_column(db, "servers", "previous_ip_cleanup_at TEXT")
        await _safe_add_column(db, "servers", "traffic_in_bytes INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "traffic_out_bytes INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "traffic_included_bytes INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "traffic_synced_at TEXT")
        await _safe_add_column(db, "servers", "traffic_limit_reviewed_at TEXT")
        await _safe_add_column(db, "servers", "expiry_reminder_sent INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "traffic_warning_sent INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "admin_reactivated INTEGER DEFAULT 0")
        await db.execute("UPDATE servers SET traffic_included_bytes=? WHERE hetzner_id IS NOT NULL", (TRAFFIC_INCLUDED_BYTES,))
        await _safe_add_column(db, "servers", "is_admin_owned INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "is_protected INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "source TEXT DEFAULT 'bot'")
        # Imported Hetzner servers remain ownerless until an administrator assigns them.
        if ADMIN_IDS:
            marks = ",".join("?" for _ in ADMIN_IDS)
            await db.execute(f"UPDATE servers SET user_id=NULL, is_admin_owned=0, is_protected=0 WHERE source='external' AND user_id IN ({marks})", ADMIN_IDS)
        await db.execute("UPDATE servers SET source='external' WHERE source IS NULL AND user_id IS NULL")
        if ADMIN_IDS:
            marks = ",".join("?" for _ in ADMIN_IDS)
            await db.execute(f"UPDATE servers SET is_admin_owned=1, is_protected=1 WHERE user_id IN ({marks}) AND COALESCE(source,'bot') != 'external'", ADMIN_IDS)
        await db.execute("UPDATE servers SET is_protected=1 WHERE user_id IS NULL AND COALESCE(source,'bot') != 'external'")
        await db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('timezone','Asia/Tehran')")
        await db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('zarinpal_merchant_id','')")
        await db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('zarinpal_sandbox','0')")
        await _safe_add_column(db, "provider", "type TEXT DEFAULT 'hetzner'")
        await _safe_add_column(db, "plans", "provider_type TEXT DEFAULT 'hetzner'")
        await _safe_add_column(db, "servers", "provider_type TEXT DEFAULT 'hetzner'")
        # سرورها/پلن‌ها/API‌های قبلی همیشه هتزنری بودند -> برای سازگاری صریحاً مقداردهی شود
        await db.execute("UPDATE provider SET type='hetzner' WHERE type IS NULL")
        await db.execute("UPDATE plans SET provider_type='hetzner' WHERE provider_type IS NULL")
        await db.execute("UPDATE servers SET provider_type='hetzner' WHERE provider_type IS NULL")
        # Collapse duplicate local rows for the same provider resource, preserving order references.
        dup_keys = await (await db.execute(
            "SELECT provider_type, provider_id, hetzner_id FROM servers WHERE hetzner_id IS NOT NULL "
            "GROUP BY provider_type, provider_id, hetzner_id HAVING COUNT(*)>1"
        )).fetchall()
        for _ptype, _pid, _remote_id in dup_keys:
            dup_rows = await (await db.execute(
                "SELECT id FROM servers WHERE provider_type IS ? AND provider_id IS ? AND hetzner_id=? "
                "ORDER BY CASE WHEN user_id IS NOT NULL THEN 0 ELSE 1 END, id",
                (_ptype, _pid, _remote_id),
            )).fetchall()
            if dup_rows:
                keep_id = dup_rows[0][0]
                for (duplicate_id,) in dup_rows[1:]:
                    await db.execute("UPDATE orders SET server_id=? WHERE server_id=?", (keep_id, duplicate_id))
                    await db.execute("UPDATE web_server_orders SET server_db_id=? WHERE server_db_id=?", (keep_id, duplicate_id))
                    await db.execute("DELETE FROM servers WHERE id=?", (duplicate_id,))
        await _safe_add_column(db, "users", "is_banned INTEGER DEFAULT 0")
        # Persist configurable IP-change price/quota while preserving current defaults.
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ip_change_free_count', ?)", (str(IP_CHANGE_FREE_COUNT),))
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ip_change_price', ?)", (str(IP_CHANGE_PRICE),))
        await db.commit()
        # اگر از نسخه قبلی آپدیت شده و هیچ توکنی فعال نیست، آخرین توکن را فعال کن
        cur = await db.execute("SELECT COUNT(*) FROM provider WHERE active=1")
        active_count = (await cur.fetchone())[0]
        if active_count == 0:
            cur = await db.execute("SELECT id FROM provider ORDER BY id DESC LIMIT 1")
            row = await cur.fetchone()
            if row:
                await db.execute("UPDATE provider SET active=1 WHERE id=?", (row[0],))
                await db.commit()


def format_bytes(value):
    try:
        value = max(0, int(value or 0))
    except Exception:
        value = 0
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    amount = float(value)
    for unit in units:
        if amount < 1000 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1000
    return f"{amount:.2f} PB"


def format_elapsed(started_at):
    if not started_at:
        return "نامشخص"
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if start.tzinfo:
            start = start.astimezone().replace(tzinfo=None)
        delta = max(timedelta(0), datetime.utcnow() - start)
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        return f"{days} روز، {hours} ساعت، {minutes} دقیقه"
    except Exception:
        return "نامشخص"


async def sync_server_usage(db, server_db_id, api):
    cur = await db.execute("SELECT hetzner_id FROM servers WHERE id=?", (server_db_id,))
    row = await cur.fetchone()
    if not row:
        return None
    live = await api.get_server(row[0])
    incoming = int(live.get("ingoing_traffic") or 0)
    outgoing = int(live.get("outgoing_traffic") or 0)
    included = TRAFFIC_INCLUDED_BYTES
    await db.execute(
        "UPDATE servers SET traffic_in_bytes=?, traffic_out_bytes=?, traffic_included_bytes=?, traffic_synced_at=? WHERE id=?",
        (incoming, outgoing, included, datetime.utcnow().isoformat(), server_db_id),
    )
    await db.commit()
    return live


async def request_server_deletion(bot, server_db_id, reason):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name, hetzner_id, provider_type, provider_id, user_id, deletion_pending_reason FROM servers WHERE id=?", (server_db_id,))
        row = await cur.fetchone()
        if not row:
            return False
        name, remote_id, ptype, pid, owner_id, pending = row
        cur = await db.execute("SELECT COALESCE(is_protected,0) FROM servers WHERE id=?", (server_db_id,))
        protected = (await cur.fetchone() or (0,))[0]
        if protected:
            logger.info("Skipping automatic deletion request for protected server %s", server_db_id)
            return False
        if pending:
            return False
        token = await get_token_for_server(db, server_db_id, ptype or "hetzner")
        await db.execute(
            "UPDATE servers SET deletion_pending_reason=?, deletion_requested_at=?, deletion_notified=1, status='off' WHERE id=?",
            (reason, datetime.utcnow().isoformat(), server_db_id),
        )
        await db.commit()
    if token:
        try:
            await make_provider_api(ptype or "hetzner", token).power_action(remote_id, "poweroff")
        except Exception as exc:
            logger.warning("Power-off before deletion approval failed for server %s: %s", server_db_id, exc)
    buttons = [
        [InlineKeyboardButton(text="🗑 تأیید حذف سرور", callback_data=f"delapprove_{server_db_id}")],
        [InlineKeyboardButton(text="⏳ تمدید ۳۰ روزه", callback_data=f"sv_renew_{server_db_id}")],
    ]
    if "درخواست حذف از منوی" in reason:
        buttons.append([InlineKeyboardButton(text="❌ لغو درخواست حذف", callback_data=f"delcancel_{server_db_id}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    if owner_id:
        try:
            await bot.send_message(
                owner_id,
                f"⚠️ سرور «{name}» خاموش شده و درخواست بررسی برای ادمین ارسال شد.\nعلت: {reason}\nمی‌توانید تمدید یا درخواست حذف را انتخاب کنید.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏳ تمدید", callback_data=f"sv_renew_{server_db_id}")],[InlineKeyboardButton(text="🗑 درخواست حذف", callback_data=f"sv_del_{server_db_id}")]]),
            )
        except Exception:
            pass
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"⚠️ درخواست بررسی حذف سرور\\n\\nسرور: {name} (DB #{server_db_id}, Provider ID {remote_id})\\nمالک: {owner_id}\\nعلت: {reason}\\n\\nسرور خاموش شده و تا تأیید ادمین حذف نخواهد شد.",
                reply_markup=keyboard,
            )
        except Exception:
            pass
    return True


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


async def get_active_provider_for(db, provider_type: str):
    """Return the preferred active provider for sales, without disabling other accounts."""
    preferred = await get_setting(db, f"priority_provider_{provider_type}")
    if preferred and str(preferred).isdigit():
        cur = await db.execute("SELECT id, api_token FROM provider WHERE id=? AND active=1 AND type=?", (int(preferred), provider_type))
        row = await cur.fetchone()
        if row:
            return row[0], row[1]
    cur = await db.execute("SELECT id, api_token FROM provider WHERE active=1 AND type=? ORDER BY id DESC LIMIT 1", (provider_type,))
    row = await cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


async def get_active_providers(db, provider_type: str):
    cur = await db.execute("SELECT id, api_token FROM provider WHERE type=? AND active=1 ORDER BY id", (provider_type,))
    rows = await cur.fetchall()
    preferred = await get_setting(db, f"priority_provider_{provider_type}")
    if preferred and str(preferred).isdigit():
        rows.sort(key=lambda row: (0 if str(row[0]) == str(preferred) else 1, row[0]))
    return rows


async def get_active_token(db):
    return (await get_active_provider_for(db, "hetzner"))[1]


async def get_active_provider(db):
    return await get_active_provider_for(db, "hetzner")


async def get_active_token_for(db, provider_type: str):
    return (await get_active_provider_for(db, provider_type))[1]


async def get_token_for_server(db, server_db_id: int, provider_type: str):
    """Resolve the owning account regardless of whether it is enabled for new sales."""
    cur = await db.execute("SELECT provider_id, hetzner_id FROM servers WHERE id=?", (server_db_id,))
    row = await cur.fetchone()
    if row and row[0] is not None:
        cur = await db.execute("SELECT api_token FROM provider WHERE id=? AND type=?", (row[0], provider_type))
        provider = await cur.fetchone()
        if provider:
            return provider[0]
    # Backfill legacy rows that predate provider_id by finding which account owns this remote server.
    if row and row[1] is not None:
        cur = await db.execute("SELECT id, api_token FROM provider WHERE type=? ORDER BY id", (provider_type,))
        providers = await cur.fetchall()
        for pid, candidate_token in providers:
            try:
                remote_servers = await make_provider_api(provider_type, candidate_token).all_servers()
                if any(int(remote.get("id", -1)) == int(row[1]) for remote in remote_servers):
                    await db.execute("UPDATE servers SET provider_id=? WHERE id=?", (pid, server_db_id))
                    await db.commit()
                    return candidate_token
            except Exception:
                continue
    return await get_active_token_for(db, provider_type)


async def get_enabled_provider_types(db):
    """لیست انواع سرویس‌دهنده‌هایی که هم‌اکنون یک API فعال دارند (هتزنر، دیجیتال‌اوشن، ولتر، جی‌کور)."""
    cur = await db.execute("SELECT DISTINCT type FROM provider WHERE active=1")
    return [r[0] for r in await cur.fetchall()]


PROVIDER_LABELS = {
    "hetzner": "🇩🇪 Hetzner",
    "digitalocean": "🌊 DigitalOcean",
    "vultr": "🚀 Vultr",
    "gcore": "🌐 Gcore",
}


def make_provider_api(provider_type: str, token: str):
    if provider_type == "digitalocean":
        return DigitalOceanAPI(token)
    if provider_type == "vultr":
        return VultrAPI(token)
    if provider_type == "gcore":
        return GcoreAPI(token)
    return HetznerAPI(token)


async def get_disabled_locations(db):
    raw = await get_setting(db, "disabled_locations", "")
    return set(x for x in raw.split(",") if x)


async def toggle_location(db, name: str):
    disabled = await get_disabled_locations(db)
    if name in disabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    await set_setting(db, "disabled_locations", ",".join(sorted(disabled)))


async def get_setting(db, key, default=None):
    cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row[0] if row else default


async def set_setting(db, key, value):
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    await db.commit()


async def get_usdt_toman_rate(db, require_fresh=False):
    """نرخ مرجع USDT به تومان از NerkhAra؛ مقدار ذخیره‌شده برحسب تومان است."""
    mode = (await get_setting(db, "wallet_rate_mode", "auto") or "auto").lower()
    cached_raw = await get_setting(db, "wallet_usd_to_toman", "100000")
    try:
        cached = float(cached_raw)
    except (TypeError, ValueError):
        cached = 0.0
    cached_at_raw = await get_setting(db, "wallet_rate_fetched_at", "")
    cached_at = None
    try:
        cached_at = datetime.fromisoformat(str(cached_at_raw).replace("Z", "+00:00"))
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        cached_age = (datetime.now(timezone.utc) - cached_at.astimezone(timezone.utc)).total_seconds()
    except Exception:
        cached_age = float("inf")

    if mode == "manual":
        if cached > 0:
            return cached
        raise RuntimeError("نرخ دستی تبدیل تنظیم نشده است.")

    if cached > 0 and cached_age < 10 * 60:
        return cached

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get("https://nerkhara.com/api/v1/latest/usdt.json")
            response.raise_for_status()
            payload = response.json()
        item = payload.get("item") or {}
        price = float(item.get("price") or 0)
        unit = str(item.get("unit") or "").upper()
        if unit != "IRT" or price <= 0 or item.get("stale") is True:
            raise ValueError("داده نرخ‌آرا نامعتبر یا قدیمی است.")
        updated_raw = item.get("updated_utc")
        updated = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
        if age < -300 or age > 30 * 60:
            raise ValueError("زمان به‌روزرسانی نرخ تتر معتبر نیست.")

        was_in_transaction = db.in_transaction
        rate_values = {
            "wallet_usd_to_toman": str(int(round(price))),
            "wallet_rate_fetched_at": datetime.now(timezone.utc).isoformat(),
            "wallet_rate_source_updated_at": updated.astimezone(timezone.utc).isoformat(),
            "wallet_rate_source": "NerkhAra",
            "wallet_rate_sources_count": str(len(item.get("sources") or [])),
        }
        for key, value in rate_values.items():
            await db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        if not was_in_transaction:
            await db.commit()
        return float(price)
    except Exception as exc:
        logger.warning("USDT/Toman live rate fetch failed: %s", exc)
        if cached > 0 and cached_age <= 20 * 60:
            return cached
        if require_fresh:
            raise RuntimeError("دریافت نرخ معتبر و به‌روز تتر ناموفق بود؛ پرداخت ریالی موقتاً متوقف شد.") from exc
        if cached > 0:
            return cached
        raise RuntimeError("نرخ تتر در دسترس نیست.") from exc


async def get_ip_change_config(db):
    """Return persisted IP-change quota and price, falling back to legacy defaults."""
    try:
        free_count = max(0, int(float(await get_setting(db, "ip_change_free_count", str(IP_CHANGE_FREE_COUNT)))))
    except (TypeError, ValueError):
        free_count = IP_CHANGE_FREE_COUNT
    try:
        price = max(0.0, float(await get_setting(db, "ip_change_price", str(IP_CHANGE_PRICE))))
    except (TypeError, ValueError):
        price = IP_CHANGE_PRICE
    return free_count, price


def is_admin(tid: int) -> bool:
    return tid in ADMIN_IDS


def fmt_price(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


# ---------------------------------------------------------------------------
# نرخ تبدیل یورو به دلار (برای قیمت‌هایی که هتزنر به یورو برمی‌گرداند)
# ---------------------------------------------------------------------------
async def get_eur_usd_rate(db) -> float:
    cached = await get_setting(db, "eur_usd_rate")
    cached_time = await get_setting(db, "eur_usd_rate_time")
    now = datetime.utcnow()
    if cached and cached_time:
        try:
            last = datetime.fromisoformat(cached_time)
            if (now - last).total_seconds() < 6 * 3600:
                return float(cached)
        except Exception:
            pass
    rate = None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://api.frankfurter.app/latest", params={"from": "EUR", "to": "USD"})
            r.raise_for_status()
            rate = float(r.json()["rates"]["USD"])
    except Exception as e:
        logger.warning(f"eur/usd rate fetch failed: {e}")
        rate = float(cached) if cached else DEFAULT_EUR_USD_RATE
    await set_setting(db, "eur_usd_rate", str(rate))
    await set_setting(db, "eur_usd_rate_time", now.isoformat())
    return rate


def to_usd(amount, currency: str, rate: float) -> float:
    amount = float(amount)
    if currency == "USD":
        return amount
    if currency == "EUR":
        return amount * rate
    return amount  # ارز ناشناخته -> بدون تبدیل


# ---------------------------------------------------------------------------
# خطاهای Hetzner: تفکیک علت‌ها + نگه‌داشتن کامل پاسخ JSON خطا (برای لاگ و عیب‌یابی 422)
# ---------------------------------------------------------------------------
HETZNER_AUTH_CODES = {"unauthorized", "forbidden", "token_readonly"}
HETZNER_LIMIT_CODES = {"resource_limit_exceeded", "server_limit_exceeded", "limit_exceeded"}
HETZNER_UNAVAILABLE_CODES = {"resource_unavailable", "placement_error", "no_space_left_in_location"}
HETZNER_INVALID_CODES = {"invalid_input", "json_error", "not_found", "protected"}
HETZNER_TRANSIENT_CODES = {"locked", "conflict", "timeout", "unavailable", "maintenance", "service_error"}


def classify_hetzner_error(status_code, code):
    """علت خطا را به یکی از این دسته‌ها تبدیل می‌کند:
    auth | limit | unavailable | invalid | duplicate | rate_limit | server_error | transient | unknown
    """
    code = (code or "").lower()
    if code in HETZNER_LIMIT_CODES:
        return "limit"
    if code in HETZNER_AUTH_CODES or status_code == 401:
        return "auth"
    if code in HETZNER_UNAVAILABLE_CODES:
        return "unavailable"
    if code == "uniqueness_error":
        return "duplicate"
    if code == "rate_limit_exceeded" or status_code == 429:
        return "rate_limit"
    if status_code is not None and status_code >= 500:
        return "server_error"
    if code in HETZNER_INVALID_CODES or status_code in (400, 404, 422):
        return "invalid"
    if code in HETZNER_TRANSIENT_CODES:
        return "transient"
    if status_code == 403:
        return "auth"
    return "unknown"


class HetznerError(Exception):
    """پایه‌ی همه‌ی خطاهای Hetzner. kind علت را مشخص می‌کند (classify_hetzner_error + network_*)."""

    kind = "unknown"
    status_code = None
    code = None
    api_message = None
    details = None
    body = None
    retry_after = None

    def _fill(self, *, kind, status_code=None, code=None, api_message=None, details=None, body=None, retry_after=None):
        self.kind = kind
        self.status_code = status_code
        self.code = code
        self.api_message = api_message
        self.details = details
        self.body = body
        self.retry_after = retry_after

    def describe(self):
        parts = []
        if self.status_code is not None:
            parts.append(f"HTTP {self.status_code}")
        if self.code:
            parts.append(f"code={self.code}")
        if self.api_message:
            parts.append(f"message={self.api_message}")
        if self.details:
            try:
                parts.append("details=" + json.dumps(self.details, ensure_ascii=False))
            except (TypeError, ValueError):
                parts.append(f"details={self.details}")
        return " | ".join(parts) if parts else str(self)


class HetznerAPIError(HetznerError, httpx.HTTPStatusError):
    """پاسخ خطای HTTP از Hetzner (زیرکلاس httpx.HTTPStatusError تا کدهای قبلی که آن را می‌گیرند سالم بمانند)."""

    def __init__(self, message, *, request, response, kind, code=None, api_message=None, details=None, body=None, retry_after=None):
        httpx.HTTPStatusError.__init__(self, message, request=request, response=response)
        self._fill(kind=kind, status_code=response.status_code, code=code, api_message=api_message,
                   details=details, body=body, retry_after=retry_after)


class HetznerNetworkError(HetznerError):
    """خطای ارتباطی. network_unsent = درخواست به Hetzner نرسیده؛ network_unknown = ممکن است رسیده باشد."""

    def __init__(self, message, *, kind="network_unknown"):
        Exception.__init__(self, message)
        self._fill(kind=kind)


class HetznerPreflightError(HetznerError):
    """خطای پیش از ارسال درخواست (پارامتر نامعتبر/ناسازگار) - هیچ درخواست ساختی ارسال نشده است."""

    def __init__(self, message, *, code="preflight", kind="invalid"):
        Exception.__init__(self, message)
        self._fill(kind=kind, code=code, api_message=message)


def hetzner_image_usable(img):
    """فقط ایمیج‌های available. (ایمیج‌های deprecated به‌صورت پیش‌فرض توسط خود API حذف می‌شوند.)"""
    if not isinstance(img, dict):
        return False
    return img.get("status") in (None, "available")


# ---------------------------------------------------------------------------
# کلاس اتصال به Hetzner Cloud API
# ---------------------------------------------------------------------------
class HetznerAPI:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method, path, *, params=None, json=None):
        url = f"{HETZNER_API}{path}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.request(method, url, headers=self.headers, params=params, json=json)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # اتصال برقرار نشد؛ درخواست قطعاً به Hetzner نرسیده است.
            raise HetznerNetworkError(f"اتصال به Hetzner برقرار نشد ({type(exc).__name__})", kind="network_unsent") from exc
        except httpx.TransportError as exc:
            # timeout روی خواندن پاسخ، قطع شدن اتصال و...: ممکن است درخواست پردازش شده باشد.
            raise HetznerNetworkError(f"ارتباط با Hetzner حین درخواست قطع شد ({type(exc).__name__})", kind="network_unknown") from exc
        if r.status_code >= 400:
            raise self._error_from_response(method, path, r)
        return r

    @staticmethod
    def _error_from_response(method, path, r):
        raw = (r.text or "")[:4000]
        try:
            payload = r.json()
        except Exception:
            payload = None
        err = payload.get("error") if isinstance(payload, dict) else None
        err = err if isinstance(err, dict) else {}
        code, api_message, details = err.get("code"), err.get("message"), err.get("details")
        kind = classify_hetzner_error(r.status_code, code)
        message = f"Hetzner {method} {path} -> HTTP {r.status_code}"
        if code:
            message += f" [{code}]"
        if api_message:
            message += f": {api_message}"
        try:
            request = r.request
        except RuntimeError:
            request = None
        retry_after = None
        try:
            retry_after = float(r.headers.get("retry-after")) if r.headers.get("retry-after") else None
        except (TypeError, ValueError):
            retry_after = None
        return HetznerAPIError(message, request=request, response=r, kind=kind, code=code,
                               api_message=api_message, details=details, body=raw, retry_after=retry_after)

    async def _get(self, path, params=None):
        r = await self._request("GET", path, params=params)
        return r.json()

    async def _post(self, path, json=None):
        r = await self._request("POST", path, json=json)
        return r.json()

    async def _delete(self, path):
        await self._request("DELETE", path)
        return True

    async def _get_paged(self, path, key, params=None):
        """همه‌ی صفحه‌های یک لیست Hetzner (پیش‌فرض API فقط ۲۵ مورد اول را برمی‌گرداند)."""
        items, page = [], 1
        while page <= 50:
            query = dict(params or {})
            query.update({"page": page, "per_page": 50})
            data = await self._get(path, params=query)
            items.extend(data.get(key, []))
            next_page = (data.get("meta") or {}).get("pagination", {}).get("next_page")
            if not next_page:
                break
            page = next_page
        return items

    async def locations(self):
        data = await self._get("/locations")
        return data.get("locations", [])

    async def server_types(self):
        types = await self._get_paged("/server_types", "server_types")
        return [t for t in types if t.get("deprecated") is not True]

    async def server_type_architecture(self, server_type_name):
        """معماری (x86 / arm) یک پلن؛ در صورت پیدا نشدن None."""
        data = await self._get("/server_types", params={"name": server_type_name})
        items = data.get("server_types") or []
        return items[0].get("architecture") if items else None

    async def images(self, architecture=None):
        """Fetch system images (optionally only one CPU architecture), without duplicates."""
        params = {"type": "system", "status": "available"}
        if architecture:
            params["architecture"] = architecture
        images = await self._get_paged("/images", "images", params)
        unique = {str(img.get("id")): img for img in images if img.get("id") is not None}
        return sorted(unique.values(), key=lambda img: (img.get("name") or "").lower())

    async def resolve_image(self, image, server_type_name):
        """ایمیج انتخاب‌شده را با معماری پلن هماهنگ می‌کند و مقداری برمی‌گرداند که برای ساخت سرور امن است.

        ایمیج‌های هتزنر برای x86 و arm نام یکسان ولی ID متفاوت دارند؛ ارسال ID ایمیج ناسازگار با پلن (مثلاً
        ubuntu-24.04 نسخه‌ی arm برای پلن x86) باعث خطای 422 می‌شود.
        """
        ref = str(image).strip() if image is not None else ""
        if not ref:
            raise HetznerPreflightError("سیستم‌عامل انتخاب نشده است.", code="image_missing")
        if not ref.isdigit():
            return ref  # نام ایمیج؛ خود Hetzner نسخه‌ی هم‌معماری را انتخاب می‌کند
        arch = await self.server_type_architecture(server_type_name)
        if not arch:
            return ref  # معماری نامشخص؛ اعتبارسنجی به عهده‌ی خود Hetzner
        try:
            img = (await self._get(f"/images/{ref}")).get("image") or {}
        except HetznerAPIError as exc:
            if exc.status_code == 404:
                raise HetznerPreflightError(f"سیستم‌عامل انتخاب‌شده (ID {ref}) دیگر در Hetzner وجود ندارد.", code="image_not_found") from exc
            raise
        if hetzner_image_usable(img) and img.get("architecture") in (None, arch):
            return ref
        name = img.get("name")
        if name:
            alt = await self._get("/images", params={"type": "system", "name": name, "architecture": arch, "status": "available"})
            for cand in alt.get("images") or []:
                if hetzner_image_usable(cand) and cand.get("id"):
                    logger.info("Hetzner image %s (%s) is not compatible with %s plan; using %s instead", ref, name, arch, cand["id"])
                    return str(cand["id"])
        raise HetznerPreflightError(
            f"سیستم‌عامل انتخاب‌شده با معماری پلن ({arch}) سازگار نیست.", code="image_architecture_mismatch"
        )

    async def pricing(self):
        data = await self._get("/pricing")
        return data.get("pricing", {})

    async def limits(self):
        """Read project limits when this API deployment exposes the endpoint.

        Hetzner does not expose billing/invoice data through the Cloud API, and some
        API versions do not provide a limits endpoint; in that case return an empty list
        so server/CPU usage can still be shown from the live server inventory.
        """
        try:
            data = await self._get("/limits")
            return data.get("limits", [])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise

    async def datacenters(self):
        data = await self._get("/datacenters")
        return data.get("datacenters", [])

    async def all_servers(self):
        """همه سرورهای موجود در این اکانت هتزنر را برمی‌گرداند (حتی آن‌هایی که با ربات ساخته نشده‌اند)."""
        servers, page = [], 1
        while True:
            data = await self._get("/servers", params={"page": page, "per_page": 50})
            batch = data.get("servers", [])
            servers.extend(batch)
            meta = data.get("meta", {}).get("pagination", {})
            if not meta.get("next_page"):
                break
            page = meta["next_page"]
        return servers

    async def create_server(self, name, server_type, image, location, labels=None):
        """POST /v1/servers. هر خطا با کامل‌ترین جزئیات (کد، پیام، details و بدنه‌ی خام JSON) لاگ می‌شود."""
        missing = [k for k, v in (("name", name), ("server_type", server_type), ("image", image), ("location", location))
                   if v is None or str(v).strip() == ""]
        if missing:
            raise HetznerPreflightError("پارامتر ساخت سرور ناقص است: " + ", ".join(missing), code="missing_parameter")
        payload = {"name": str(name), "server_type": str(server_type), "image": str(image), "location": str(location)}
        if labels:
            payload["labels"] = {str(k): str(v) for k, v in labels.items()}
        try:
            data = await self._post("/servers", json=payload)
        except HetznerError as exc:
            logger.error(
                "Hetzner POST /servers FAILED | kind=%s | %s | payload=%s | raw_response=%s",
                exc.kind, exc.describe(), json.dumps(payload, ensure_ascii=False), exc.body,
            )
            raise
        server = data.get("server") if isinstance(data, dict) else None
        if not isinstance(server, dict) or not server.get("id"):
            logger.error("Hetzner POST /servers returned an unexpected body: %s | payload=%s", str(data)[:2000], json.dumps(payload, ensure_ascii=False))
            # سرور ممکن است ساخته شده باشد؛ فراخوان‌کننده باید با نام/لیبل بررسی کند.
            raise HetznerNetworkError("پاسخ Hetzner به درخواست ساخت سرور نامعتبر بود.", kind="network_unknown")
        logger.info("Hetzner server created | id=%s | name=%s | type=%s | location=%s", server.get("id"), name, server_type, location)
        return server, data.get("root_password")

    async def find_server_by_name(self, name):
        """سرور با این نام دقیق (اگر وجود داشته باشد). برای جلوگیری از ساخت تکراری بعد از خطای ارتباطی."""
        data = await self._get("/servers", params={"name": name})
        servers = data.get("servers") or []
        return servers[0] if servers else None

    async def delete_server(self, hetzner_id):
        return await self._delete(f"/servers/{hetzner_id}")

    async def power_action(self, hetzner_id, action):
        return await self._post(f"/servers/{hetzner_id}/actions/{action}")

    async def reset_password(self, hetzner_id):
        data = await self._post(f"/servers/{hetzner_id}/actions/reset_password")
        return data.get("root_password")

    async def rebuild_server(self, hetzner_id, image):
        data = await self._post(f"/servers/{hetzner_id}/actions/rebuild", json={"image": image})
        return data.get("root_password")

    async def get_server(self, hetzner_id):
        data = await self._get(f"/servers/{hetzner_id}")
        return data["server"]

    async def _wait_action(self, result, timeout=120):
        """Wait until a Hetzner async action is finished and surface API errors."""
        action = (result or {}).get("action")
        if not action or not action.get("id"):
            return
        action_id = action["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = await self._get(f"/actions/{action_id}")
            action = current.get("action", {})
            status = action.get("status")
            if status == "success":
                return
            if status == "error":
                err = action.get("error") or {}
                raise RuntimeError(f"Hetzner action failed: {err.get('code', 'unknown')} — {err.get('message', 'no details')}")
            # Hetzner recommends avoiding overly frequent action polling.
            await asyncio.sleep(3)
        raise TimeoutError(f"Hetzner action {action_id} did not finish within {timeout}s")

    async def unassign_primary_ip(self, primary_ip_id):
        result = await self._post(f"/primary_ips/{primary_ip_id}/actions/unassign")
        await self._wait_action(result)
        return result

    async def delete_primary_ip(self, primary_ip_id):
        return await self._delete(f"/primary_ips/{primary_ip_id}")

    async def create_primary_ip(self, name, ip_type, location, assignee_id=None):
        # Hetzner removed the deprecated `datacenter` request field in July 2026.
        # Primary IPs must now be created with the location slug (e.g. nbg1).
        payload = {"type": ip_type, "name": name, "location": location}
        if assignee_id is not None:
            payload["assignee_type"] = "server"
            payload["assignee_id"] = assignee_id
        data = await self._post("/primary_ips", json=payload)
        return data["primary_ip"]

    async def assign_primary_ip(self, primary_ip_id, server_id):
        result = await self._post(f"/primary_ips/{primary_ip_id}/actions/assign", json={"assignee_type": "server", "assignee_id": server_id})
        await self._wait_action(result)
        return result


async def change_server_ip(api: "HetznerAPI", hetzner_id: int):
    """Replace the primary IPv4 while retaining the previous unassigned IP for delayed cleanup."""
    server = await api.get_server(hetzner_id)
    if server.get("status") != "off":
        raise RuntimeError("برای تغییر Primary IP، ابتدا سرور را خاموش کنید و وضعیت آن باید در هتزنر «off» باشد. پس از خاموش شدن دوباره تلاش کنید.")
    public_net = server.get("public_net") or {}
    old = public_net.get("ipv4") or {}
    # The Hetzner Cloud API removed the `datacenter` response field on 1 Oct 2026.
    # Current server responses expose the location directly.
    location = server.get("location") or {}
    location_name = location.get("name")
    if not location_name:
        # Compatibility with older API responses that still include datacenter.location.
        location_name = ((server.get("datacenter") or {}).get("location") or {}).get("name")
    if not old.get("id") or not location_name:
        raise RuntimeError("این سرور IPv4 اصلی قابل تعویض ندارد یا API هتزنر اطلاعات مکان سرور را برنگرداند.")

    new_ip = await api.create_primary_ip(f"replace-{hetzner_id}-{secrets.token_hex(3)}", "ipv4", location_name)
    new_id, new_address = new_ip.get("id"), new_ip.get("ip")
    if not new_id or not new_address:
        raise RuntimeError("هتزنر IP جدید معتبری برنگرداند.")

    # Unassign old first, then assign new. Each action is awaited to completion.
    await api.unassign_primary_ip(old["id"])
    try:
        await api.assign_primary_ip(new_id, hetzner_id)
    except Exception as exc:
        # Keep the old IP resource and attempt to restore it if assigning the new IP fails.
        try:
            await api.assign_primary_ip(old["id"], hetzner_id)
        except Exception as restore_exc:
            raise RuntimeError(f"اتصال IP جدید ناموفق بود ({exc}) و اتصال مجدد IP قبلی هم ناموفق شد ({restore_exc}). وضعیت IPها را در کنسول هتزنر بررسی کنید.") from exc
        try:
            await api.delete_primary_ip(new_id)
        except Exception:
            pass
        raise RuntimeError(f"اتصال IP جدید ناموفق بود؛ IP قبلی دوباره متصل شد. علت API: {exc}") from exc

    # Keep the previous IP unassigned for 30 minutes; maintenance will delete it later.
    return new_address, old["id"], old.get("ip")


# =============================================================================
# سرویس‌دهنده‌های دیگر (DigitalOcean, Vultr, Gcore)
# هرکدام یک کلاس با متدهای هم‌نام HetznerAPI دارند: locations, server_types,
# images, create_server, power_action, delete_server, get_server
# =============================================================================
class DigitalOceanAPI:
    BASE = "https://api.digitalocean.com/v2"

    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def _req(self, method, path, **kwargs):
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(method, f"{self.BASE}{path}", headers=self.headers, **kwargs)
            if r.status_code >= 400:
                try:
                    detail = r.json().get("message", r.text[:200])
                except Exception:
                    detail = r.text[:200]
                raise RuntimeError(f"DigitalOcean ({r.status_code}): {detail}")
            return r.json() if r.text else {}

    async def locations(self):
        data = await self._req("GET", "/regions")
        return [
            {"name": r["slug"], "city": r["name"], "country": ""}
            for r in data.get("regions", []) if r.get("available")
        ]

    async def server_types(self):
        data = await self._req("GET", "/sizes")
        return [
            {
                "id": s["slug"], "name": s["slug"],
                "cores": s.get("vcpus"), "memory": round((s.get("memory") or 0) / 1024, 1),
                "disk": s.get("disk"), "price_monthly": s.get("price_monthly"),
            }
            for s in data.get("sizes", []) if s.get("available")
        ]

    async def images(self):
        data = await self._req("GET", "/images", params={"type": "distribution", "per_page": 80})
        return [{"id": i["slug"], "name": i["name"]} for i in data.get("images", []) if i.get("slug")]

    async def datacenters(self):
        return []  # DigitalOcean موجودی جداگانه اعلام نمی‌کند؛ فیلتر موجودی روی این پروایدر اعمال نمی‌شود

    async def create_server(self, name, server_type, image, location):
        password = secrets.token_urlsafe(9)
        user_data = (
            "#cloud-config\n"
            f"chpasswd:\n  list: |\n    root:{password}\n  expire: false\n"
            "ssh_pwauth: true\n"
            "runcmd:\n"
            "  - sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config\n"
            "  - sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config\n"
            "  - systemctl restart ssh || systemctl restart sshd\n"
        )
        data = await self._req(
            "POST", "/droplets",
            json={"name": name, "region": location, "size": server_type, "image": image, "user_data": user_data},
        )
        droplet = data["droplet"]
        droplet_id = droplet["id"]
        # منتظر می‌مانیم تا IP تخصیص داده شود (حداکثر ~۶۰ ثانیه)
        for _ in range(20):
            await asyncio.sleep(3)
            info = (await self._req("GET", f"/droplets/{droplet_id}")).get("droplet", {})
            ipv4 = next((n["ip_address"] for n in info.get("networks", {}).get("v4", []) if n["type"] == "public"), None)
            if ipv4:
                droplet = info
                break
        return droplet, password

    async def get_server(self, server_id):
        data = await self._req("GET", f"/droplets/{server_id}")
        return data["droplet"]

    async def power_action(self, server_id, action):
        action_map = {"poweron": "power_on", "poweroff": "power_off", "reboot": "reboot"}
        return await self._req("POST", f"/droplets/{server_id}/actions", json={"type": action_map.get(action, action)})

    async def delete_server(self, server_id):
        return await self._req("DELETE", f"/droplets/{server_id}")

    async def reset_password(self, server_id):
        raise RuntimeError("تغییر پسورد پس از ساخت روی DigitalOcean پشتیبانی نمی‌شود؛ فقط از طریق Rebuild ممکن است.")

    async def rebuild_server(self, server_id, image):
        password = secrets.token_urlsafe(9)
        await self._req("POST", f"/droplets/{server_id}/actions", json={"type": "rebuild", "image": image})
        # DigitalOcean رمز جدید را ایمیل می‌کند مگر با cloud-init دوباره ست شود؛ اینجا فقط عملیات را انجام می‌دهیم
        return None


class VultrAPI:
    BASE = "https://api.vultr.com/v2"

    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def _req(self, method, path, **kwargs):
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(method, f"{self.BASE}{path}", headers=self.headers, **kwargs)
            if r.status_code >= 400:
                try:
                    detail = r.json().get("error", r.text[:200])
                except Exception:
                    detail = r.text[:200]
                raise RuntimeError(f"Vultr ({r.status_code}): {detail}")
            return r.json() if r.text else {}

    async def locations(self):
        data = await self._req("GET", "/regions")
        return [{"name": r["id"], "city": r.get("city", r["id"]), "country": r.get("country", "")} for r in data.get("regions", [])]

    async def server_types(self):
        data = await self._req("GET", "/plans")
        return [
            {
                "id": p["id"], "name": p["id"], "cores": p.get("vcpu_count"),
                "memory": round((p.get("ram") or 0) / 1024, 1), "disk": p.get("disk"),
                "price_monthly": p.get("monthly_cost"),
            }
            for p in data.get("plans", [])
        ]

    async def images(self):
        data = await self._req("GET", "/os")
        return [{"id": o["id"], "name": o["name"]} for o in data.get("os", [])]

    async def datacenters(self):
        return []  # فیلتر موجودی روی این پروایدر اعمال نمی‌شود

    async def create_server(self, name, server_type, image, location):
        data = await self._req(
            "POST", "/instances",
            json={"region": location, "plan": server_type, "os_id": int(image), "label": name, "hostname": name},
        )
        instance = data["instance"]
        instance_id = instance["id"]
        password = None
        for _ in range(30):
            await asyncio.sleep(4)
            info = (await self._req("GET", f"/instances/{instance_id}")).get("instance", {})
            if info.get("server_status") == "ok" and info.get("main_ip") not in (None, "0.0.0.0"):
                instance = info
                password = info.get("default_password")
                break
        return instance, password

    async def get_server(self, server_id):
        data = await self._req("GET", f"/instances/{server_id}")
        return data["instance"]

    async def power_action(self, server_id, action):
        action_map = {"poweron": "start", "poweroff": "halt", "reboot": "reboot"}
        return await self._req("POST", f"/instances/{server_id}/{action_map.get(action, action)}")

    async def delete_server(self, server_id):
        return await self._req("DELETE", f"/instances/{server_id}")

    async def reset_password(self, server_id):
        data = await self._req("POST", f"/instances/{server_id}/reinstall")
        return data.get("instance", {}).get("default_password")

    async def rebuild_server(self, server_id, image):
        data = await self._req("POST", f"/instances/{server_id}/reinstall", json={"os_id": int(image)})
        return data.get("instance", {}).get("default_password")


class GcoreAPI:
    """
    ⚠️ این اتصال به‌صورت آزمایشی پیاده‌سازی شده. مستندات عمومی Gcore Cloud به project_id/region_id
    نیاز دارد که در تنظیمات این پروایدر باید وارد شود. در صورت خطا هنگام تست، پیام دقیق خطا را
    برای اصلاح مسیر/پارامترها بررسی کنید.
    """
    BASE = "https://api.gcore.com/cloud"

    def __init__(self, token: str):
        # توکن باید به‌فرم "PROJECT_ID:REGION_ID:APIKEY" ذخیره شده باشد
        parts = token.split(":", 2)
        if len(parts) == 3:
            self.project_id, self.region_id, self.api_key = parts
        else:
            self.project_id, self.region_id, self.api_key = "", "", token
        self.headers = {"Authorization": f"APIKey {self.api_key}", "Content-Type": "application/json"}

    async def _req(self, method, path, **kwargs):
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(method, f"{self.BASE}{path}", headers=self.headers, **kwargs)
            if r.status_code >= 400:
                raise RuntimeError(f"Gcore ({r.status_code}): {r.text[:300]}")
            return r.json() if r.text else {}

    async def locations(self):
        data = await self._req("GET", "/v1/regions")
        results = data.get("results", data if isinstance(data, list) else [])
        return [{"name": str(r.get("id")), "city": r.get("display_name", str(r.get("id"))), "country": ""} for r in results]

    async def server_types(self):
        data = await self._req("GET", f"/v1/flavors/{self.project_id}/{self.region_id}")
        results = data.get("results", data if isinstance(data, list) else [])
        return [
            {
                "id": f.get("flavor_id") or f.get("name"), "name": f.get("flavor_name") or f.get("name"),
                "cores": f.get("vcpus"), "memory": round((f.get("ram") or 0) / 1024, 1), "disk": f.get("disk"),
                "price_monthly": None,
            }
            for f in results
        ]

    async def images(self):
        data = await self._req("GET", f"/v1/images/{self.project_id}/{self.region_id}")
        results = data.get("results", data if isinstance(data, list) else [])
        return [{"id": i.get("id"), "name": i.get("name")} for i in results]

    async def datacenters(self):
        return []

    async def create_server(self, name, server_type, image, location):
        password = secrets.token_urlsafe(9)
        payload = {
            "names": [name], "flavor": server_type, "password": password, "username": "root",
            "volumes": [{"source": "image", "image_id": image, "size": 20, "boot_index": 0}],
            "interfaces": [{"type": "external"}],
        }
        data = await self._req("POST", f"/v2/instances/{self.project_id}/{self.region_id}", json=payload)
        task_id = (data.get("tasks") or [None])[0]
        instance = {"id": task_id, "name": name}
        return instance, password

    async def get_server(self, server_id):
        data = await self._req("GET", f"/v2/instances/{self.project_id}/{self.region_id}/{server_id}")
        return data

    async def power_action(self, server_id, action):
        action_map = {"poweron": "start", "poweroff": "stop", "reboot": "reboot"}
        return await self._req(
            "POST", f"/v2/instances/{self.project_id}/{self.region_id}/{server_id}/{action_map.get(action, action)}"
        )

    async def delete_server(self, server_id):
        return await self._req("DELETE", f"/v2/instances/{self.project_id}/{self.region_id}/{server_id}")

    async def reset_password(self, server_id):
        raise RuntimeError("تغییر پسورد پس از ساخت روی Gcore پشتیبانی نمی‌شود.")

    async def rebuild_server(self, server_id, image):
        raise RuntimeError("نصب مجدد روی Gcore هنوز پیاده‌سازی نشده.")


async def available_types_for_location(api: "HetznerAPI", location_name: str):
    """Return server-type names whose current Hetzner availability flag is true."""
    try:
        types = await api.server_types()
    except Exception:
        return None
    return compute_available_types(types, location_name)


def compute_available_types(types, location_name: str):
    """Server-type names available in a location, computed from ONE account's server_types response."""
    available = set()
    known = False
    for server_type in types:
        name = server_type.get("name")
        if not name:
            continue
        # Current Hetzner API uses server_types[].locations[].available.
        location_entries = server_type.get("locations")
        if isinstance(location_entries, list):
            known = True
            for item in location_entries:
                if isinstance(item, dict) and item.get("name") == location_name and item.get("available") is True:
                    available.add(name)
                    break
            continue
        # Compatibility fallback for responses exposing available_locations.
        locations = server_type.get("available_locations")
        if isinstance(locations, list):
            known = True
            normalized = {item.get("name") if isinstance(item, dict) else item for item in locations}
            if location_name in normalized:
                available.add(name)
            continue
        # Legacy fallback: per-location prices show support, but not live stock.
        # Do not call it available unless API explicitly says available.
        prices = server_type.get("prices") or []
        if any(price.get("location") == location_name for price in prices):
            known = True
            if server_type.get("available") is True:
                available.add(name)
    return available if known else None


async def build_stock_map(api: "HetznerAPI"):
    """Build location -> currently available server types from current Hetzner API fields."""
    try:
        types = await api.server_types()
    except Exception:
        return None
    stock_map = {}
    known = False
    for server_type in types:
        name = server_type.get("name")
        if not name:
            continue
        location_entries = server_type.get("locations")
        if isinstance(location_entries, list):
            known = True
            for item in location_entries:
                if isinstance(item, dict) and item.get("name") and item.get("available") is True:
                    stock_map.setdefault(item["name"], set()).add(name)
            continue
        locations = server_type.get("available_locations")
        if isinstance(locations, list):
            known = True
            for item in locations:
                loc = item.get("name") if isinstance(item, dict) else item
                if loc:
                    stock_map.setdefault(loc, set()).add(name)
            continue
        # Prices alone are not proof of live inventory. Leave the map unknown if
        # the API response does not include any supported availability field.
    return stock_map if known else None


# ---------------------------------------------------------------------------
# ساخت سرور Hetzner با چند API (failover امن، بدون ترکیب اطلاعات APIهای مختلف)
# ---------------------------------------------------------------------------
HETZNER_KIND_PRIORITY = [
    "unavailable", "limit", "auth", "invalid", "rate_limit", "server_error",
    "network_unsent", "network_unknown", "transient", "duplicate", "stock_unknown", "unknown",
]
HETZNER_AMBIGUOUS_KINDS = {"network_unknown", "server_error"}  # ممکن است سرور واقعاً ساخته شده باشد
HETZNER_ADMIN_ALERT_KINDS = {"limit", "auth", "invalid", "server_error", "network_unknown", "network_unsent", "transient", "unknown"}

HETZNER_USER_MESSAGES = {
    "unavailable": "❌ موجودی این پلن همین حالا در Hetzner تمام شد یا در این دیتاسنتر قابل ساخت نیست. مبلغی از کیف پول شما کسر نشد؛ لطفاً دیتاسنتر/پلن دیگری انتخاب کنید یا کمی بعد دوباره تلاش کنید.",
    "stock_unknown": "❌ موجودی این پلن در APIهای فعال قابل تأیید نیست. مبلغی از کیف پول شما کسر نشد؛ کمی بعد دوباره تلاش کنید.",
    "limit": "❌ سقف/محدودیت اکانت Hetzner برای ساخت سرور جدید پر شده است. موضوع به ادمین گزارش شد و مبلغی از کیف پول شما کسر نشد.",
    "auth": "❌ احراز هویت API هتزنر ناموفق بود (توکن نامعتبر یا بدون دسترسی نوشتن). موضوع به ادمین گزارش شد و مبلغی کسر نشد.",
    "invalid": "❌ Hetzner پارامترهای ساخت سرور را نپذیرفت (مثلاً سیستم‌عامل یا پلن). موضوع به ادمین گزارش شد؛ می‌توانید سیستم‌عامل دیگری را امتحان کنید. مبلغی کسر نشد.",
    "rate_limit": "❌ Hetzner موقتاً تعداد درخواست‌ها را محدود کرده است. چند دقیقه بعد دوباره تلاش کنید. مبلغی کسر نشد.",
    "network_unsent": "❌ ارتباط با Hetzner برقرار نشد. کمی بعد دوباره تلاش کنید. مبلغی کسر نشد.",
    "transient": "❌ Hetzner موقتاً پاسخگو نیست. کمی بعد دوباره تلاش کنید. مبلغی کسر نشد.",
    "duplicate": "❌ تداخل نام سرور رخ داد. دوباره تلاش کنید. مبلغی کسر نشد.",
    "unknown": "❌ ساخت سرور ناموفق بود. موضوع به ادمین گزارش شد و مبلغی کسر نشد.",
}
HETZNER_USER_MESSAGES["server_error"] = HETZNER_USER_MESSAGES["transient"]
HETZNER_USER_MESSAGES["network_unknown"] = HETZNER_USER_MESSAGES["transient"]
HETZNER_UNCERTAIN_USER_MESSAGE = (
    "⚠️ پاسخ Hetzner هنگام ساخت سرور نامشخص بود. برای جلوگیری از ساخت سرور تکراری، تلاش مجدد انجام نشد "
    "و مبلغی از کیف پول شما کسر نشد. موضوع به ادمین گزارش شد؛ چند دقیقه بعد بخش «سرورهای من» را بررسی کنید."
)


class HetznerCreateFailed(Exception):
    """ساخت سرور در هیچ‌یک از APIها موفق نبود. attempts: لیست تلاش‌ها با علت هرکدام."""

    def __init__(self, attempts, uncertain=False, server_name=None, order_ref=None):
        self.attempts = attempts
        self.uncertain = uncertain
        self.server_name = server_name
        self.order_ref = order_ref
        super().__init__("; ".join(f"API#{a.get('provider_id')}:{a.get('kind')}:{a.get('text')}" for a in attempts) or "no provider")

    @property
    def primary_kind(self):
        kinds = {a.get("kind") for a in self.attempts}
        for kind in HETZNER_KIND_PRIORITY:
            if kind in kinds:
                return kind
        return "unknown"


def hetzner_failure_user_text(exc: "HetznerCreateFailed") -> str:
    text = HETZNER_UNCERTAIN_USER_MESSAGE if exc.uncertain else HETZNER_USER_MESSAGES.get(exc.primary_kind, HETZNER_USER_MESSAGES["unknown"])
    if exc.order_ref and exc.primary_kind != "unavailable" and exc.primary_kind != "stock_unknown":
        text += f"\nکد پیگیری: {exc.order_ref}"
    return text


async def _find_our_server(api: "HetznerAPI", server_name: str, order_ref: str, delays=(0, 3, 6)):
    """بعد از خطای مبهم بررسی می‌کند سرور همین سفارش (هم‌نام و هم‌لیبل) واقعاً ساخته شده یا نه."""
    for delay in delays:
        if delay:
            await asyncio.sleep(delay)
        try:
            server = await api.find_server_by_name(server_name)
        except Exception as exc:
            logger.warning("Could not verify server %s after ambiguous create error: %s", server_name, exc)
            continue
        if server and (server.get("labels") or {}).get("shop-order") == order_ref:
            return server
    return None


async def create_hetzner_server_with_failover(providers, *, server_name, server_type, image, location, order_ref):
    """سرور را روی اولین API که واقعاً توان ساخت دارد می‌سازد.

    - موجودی، Server Type، Location، Image و Token همیشه از همان API (همان اکانت) بررسی و استفاده می‌شوند.
    - اگر ساخت در یک API قطعاً شکست خورد (پلن ناموجود، پارامتر نامعتبر، محدودیت اکانت، احراز هویت...)، API بعدی امتحان می‌شود.
    - اگر نتیجه مبهم باشد (timeout/قطع اتصال/خطای 5xx) ابتدا با نام و لیبل سفارش بررسی می‌شود که سرور ساخته شده یا نه؛
      اگر ساخته شده بود همان پذیرفته می‌شود و اگر تکلیف روشن نشد، برای جلوگیری از سرور تکراری هیچ API دیگری امتحان نمی‌شود.
    """
    attempts = []
    labels = {"shop-bot": "1", "shop-order": order_ref}

    def note(pid, step, exc=None, kind=None, text=None):
        entry = {
            "provider_id": pid, "step": step,
            "kind": kind or getattr(exc, "kind", "unknown"),
            "text": text or (exc.describe() if isinstance(exc, HetznerError) else f"{type(exc).__name__}: {exc}"),
            "code": getattr(exc, "code", None), "status": getattr(exc, "status_code", None),
        }
        attempts.append(entry)
        logger.warning("Hetzner create attempt failed | API#%s | step=%s | kind=%s | %s", pid, step, entry["kind"], entry["text"])

    def success(pid, token, server, root_password, image_ref, adopted=False):
        return {"provider_id": pid, "token": token, "server": server, "root_password": root_password,
                "image": image_ref, "adopted": adopted}

    for pid, ptoken in providers:
        api = HetznerAPI(ptoken)

        # ۱) موجودی دقیقاً با توکن همین API
        try:
            types = await api.server_types()
        except Exception as exc:
            note(pid, "stock", exc)
            continue
        stock = compute_available_types(types, location)
        if stock is None:
            note(pid, "stock", kind="stock_unknown", text="API اطلاعات موجودی برنگرداند")
            continue
        if server_type not in stock:
            note(pid, "stock", kind="unavailable", text=f"{server_type} در {location} موجود نیست")
            continue

        # ۲) هماهنگ‌سازی ایمیج با معماری پلن (روی همین API)
        try:
            image_ref = await api.resolve_image(image, server_type)
        except Exception as exc:
            note(pid, "image", exc)
            continue

        # ۳) ساخت
        for attempt in (1, 2):
            try:
                server, root_password = await api.create_server(server_name, server_type, image_ref, location, labels=labels)
                return success(pid, ptoken, server, root_password, image_ref)
            except Exception as exc:
                kind = getattr(exc, "kind", "network_unknown")
                if kind == "rate_limit" and attempt == 1:
                    await asyncio.sleep(min(getattr(exc, "retry_after", None) or 3, 10))
                    continue
                if kind in HETZNER_AMBIGUOUS_KINDS:
                    found = await _find_our_server(api, server_name, order_ref)
                    if found:
                        logger.warning("Create reported %s but server %s exists on API#%s; adopting it", kind, found.get("id"), pid)
                        return success(pid, ptoken, found, None, image_ref, adopted=True)
                    note(pid, "create", exc)
                    raise HetznerCreateFailed(attempts, uncertain=True, server_name=server_name, order_ref=order_ref) from exc
                if kind == "duplicate":
                    found = await _find_our_server(api, server_name, order_ref, delays=(0,))
                    if found:
                        return success(pid, ptoken, found, None, image_ref, adopted=True)
                note(pid, "create", exc)
                break
    raise HetznerCreateFailed(attempts, uncertain=False, server_name=server_name, order_ref=order_ref)


async def notify_admins(bot, text: str):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text[:3900])
        except Exception as exc:
            logger.warning("Could not notify admin %s: %s", admin_id, exc)


async def notify_admins_sale(sale_type, order_id, user_id, title, amount_usd, payment_method, details="", bot=None):
    """Send one compact sale/renewal report to every configured administrator."""
    if not ADMIN_IDS or (bot is None and not BOT_TOKEN):
        return
    owned_bot = bot is None
    client = bot
    try:
        if owned_bot:
            client = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        username = ""
        full_name = ""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                user = await (await db.execute(
                    "SELECT username,full_name FROM users WHERE telegram_id=?", (user_id,)
                )).fetchone()
            if user:
                username, full_name = user
        except Exception:
            logger.exception("Could not load customer details for sale report %s", order_id)
        customer = html.escape(str(full_name or ""))
        if username:
            customer += f" (@{html.escape(str(username))})"
        if not customer:
            customer = "نام ثبت نشده"
        try:
            amount = max(0.0, float(amount_usd or 0))
        except (TypeError, ValueError):
            amount = 0.0
        stamp = iran_time(datetime.utcnow())
        stamp_text = stamp.strftime("%Y-%m-%d %H:%M:%S") if stamp else datetime.utcnow().isoformat()
        report = (
            "🟢 <b>گزارش فروش / تمدید موفق</b>\n"
            f"نوع: {html.escape(str(sale_type))}\n"
            f"شماره سفارش: #{html.escape(str(order_id))}\n"
            f"مشتری: {customer}\n"
            f"شناسه تلگرام: <code>{int(user_id or 0)}</code>\n"
            f"محصول / پلن: {html.escape(str(title or '-'))}\n"
            f"مبلغ ثبت‌شده: <b>\${amount:.2f}</b>\n"
            f"روش پرداخت: {html.escape(str(payment_method or '-'))}\n"
            + (f"جزئیات: {html.escape(str(details))}\n" if details else "")
            + f"زمان ایران: {stamp_text}"
        )
        await notify_admins(client, report)
    except Exception:
        logger.exception("Sale report notification failed: order=%s type=%s", order_id, sale_type)
    finally:
        if owned_bot and client is not None:
            try:
                await client.session.close()
            except Exception:
                pass


async def report_hetzner_create_failure(call, user_id, data, exc: "HetznerCreateFailed"):
    """پیام مناسب به کاربر + گزارش کامل به ادمین (برای خطاهایی که نیاز به اقدام ادمین دارند)."""
    logger.error("Hetzner server creation failed for user %s | ref=%s | uncertain=%s | attempts=%s",
                 user_id, exc.order_ref, exc.uncertain, exc.attempts)
    try:
        await call.message.answer(hetzner_failure_user_text(exc))
    except Exception as send_exc:
        logger.warning("Could not send failure message to user %s: %s", user_id, send_exc)
    if not exc.uncertain and exc.primary_kind not in HETZNER_ADMIN_ALERT_KINDS:
        return
    lines = [
        "🚨 ساخت سرور برای کاربر ناموفق بود" + (" (نتیجه نامشخص؛ احتمال وجود سرور)" if exc.uncertain else ""),
        f"کاربر: {user_id}",
        f"پلن: {html.escape(str(data.get('plan_title')))} ({html.escape(str(data.get('server_type')))})",
        f"دیتاسنتر: {html.escape(str(data.get('location')))}",
        f"کد پیگیری/لیبل shop-order: {html.escape(str(exc.order_ref))}",
        f"نام سرور: {html.escape(str(exc.server_name))}",
        "علت‌ها به تفکیک API:",
    ]
    for a in exc.attempts:
        lines.append(f"• API #{a.get('provider_id')} [{a.get('step')}/{a.get('kind')}] {html.escape(str(a.get('text')))[:500]}")
    if exc.uncertain:
        lines.append("برای جلوگیری از سرور تکراری تلاش مجدد انجام نشد؛ لطفاً در پنل Hetzner بررسی کنید که سروری با این نام ساخته شده یا نه.")
    await notify_admins(call.bot, "\n".join(lines))


def plan_has_stock(stock_map, plan_location, server_type, enabled_locations):
    """آیا این پلن (با توجه به لوکیشن تعیین‌شده‌اش) هم‌اکنون در حداقل یک لوکیشن فعال موجودی دارد؟"""
    if stock_map is None:
        return True  # نامشخص -> پنهان نشود
    candidate_locs = [plan_location] if plan_location else list(enabled_locations)
    for loc in candidate_locs:
        if server_type in stock_map.get(loc, set()):
            return True
    return False


def find_type_price(pricing: dict, type_name: str, location: str):
    for st in pricing.get("server_types", []):
        if st.get("name") == type_name:
            for p in st.get("prices", []):
                if p.get("location") == location:
                    return p
    return None


async def compute_price_usd(db, pricing: dict, type_name: str, location: str, markup_percent: float):
    p = find_type_price(pricing, type_name, location)
    if not p:
        return None
    rate = await get_eur_usd_rate(db)
    currency = pricing.get("currency", "EUR")
    base_usd = to_usd(p["price_monthly"]["gross"], currency, rate)
    return round(base_usd * (1 + (markup_percent or 0) / 100.0), 2)


async def resolve_external_hetzner_renewal_price(db, server_db_id: int, hetzner_id: int,
                                                provider_type: str = "hetzner"):
    """Resolve and persist a renewal price for an imported Hetzner server.

    Imported servers may not match a plan's exact location string (or may have been
    imported before a plan was configured). Prefer an active plan for the exact
    location, then a global plan, then the most recently configured plan for the
    same server type. Pricing itself is read from Hetzner's live pricing endpoint.
    """
    if provider_type != "hetzner" or not hetzner_id:
        return None
    token = await get_token_for_server(db, server_db_id, provider_type)
    if not token:
        return None
    try:
        api = HetznerAPI(token)
        live = await api.get_server(int(hetzner_id))
        st = live.get("server_type") or {}
        type_name = st.get("name") if isinstance(st, dict) else str(st or "")
        live_loc = live.get("location") or {}
        location = live_loc.get("name") if isinstance(live_loc, dict) else str(live_loc or "")
        if not location:
            dc = live.get("datacenter") or {}
            location = ((dc.get("location") or {}).get("name") or dc.get("name") or "")
        if not type_name:
            return None
        pricing = await api.pricing()
    except Exception as exc:
        logger.warning("Could not resolve live renewal price for external Hetzner server %s: %s", hetzner_id, exc)
        return None

    plans = await (await db.execute(
        "SELECT id, title, markup_percent, location, active FROM plans "
        "WHERE server_type=? AND (provider_type='hetzner' OR provider_type IS NULL OR provider_type='') "
        "ORDER BY CASE WHEN active=1 THEN 0 ELSE 1 END, "
        "CASE WHEN location=? THEN 0 WHEN location IS NULL OR location='' THEN 1 ELSE 2 END, id DESC",
        (type_name, location),
    )).fetchall()
    if not plans:
        return None

    # Exact-location price is preferred. Hetzner may omit a location price in some
    # account/API responses, so fall back to another published price for this type.
    price_row = find_type_price(pricing, type_name, location)
    if not price_row:
        type_rows = [x for x in pricing.get("server_types", []) if x.get("name") == type_name]
        for type_row in type_rows:
            candidates = type_row.get("prices") or []
            if candidates:
                price_row = candidates[0]
                break
    if not price_row:
        return None
    try:
        gross = (price_row.get("price_monthly") or {}).get("gross")
        if gross is None:
            return None
        rate = await get_eur_usd_rate(db)
        base_usd = to_usd(gross, pricing.get("currency", "EUR"), rate)
        selected = plans[0]
        renewal_price = round(base_usd * (1 + (float(selected[2] or 0) / 100.0)), 2)
        if renewal_price <= 0:
            return None
        await db.execute(
            "UPDATE servers SET monthly_price=?, plan_title=CASE "
            "WHEN plan_title IS NULL OR plan_title='' OR plan_title IN ('ساخته‌شده خارج از ربات','پلن شناسایی‌نشده') "
            "THEN ? ELSE plan_title END, server_type=?, location=CASE WHEN ?<>'' THEN ? ELSE location END "
            "WHERE id=?",
            (renewal_price, selected[1] or type_name, type_name, location, location, server_db_id),
        )
        await db.commit()
        return renewal_price
    except Exception as exc:
        logger.warning("Could not calculate/persist renewal price for Hetzner server %s: %s", hetzner_id, exc)
        return None


# ---------------------------------------------------------------------------
# درگاه پرداخت ارز دیجیتال (OxaPay)
# ---------------------------------------------------------------------------
async def oxapay_create_invoice(api_key: str, amount_usd: float, order_id: str, description: str = "شارژ کیف پول"):
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{OXAPAY_API}/payment/invoice",
            headers={"merchant_api_key": api_key, "Content-Type": "application/json"},
            json={
                "amount": amount_usd,
                "currency": "USD",
                "lifetime": 60,
                "order_id": order_id,
                "description": str(description)[:120],
            },
        )
        r.raise_for_status()
        data = r.json()["data"]
        return data["track_id"], data["payment_url"]


async def oxapay_check_status(api_key: str, track_id: str) -> str:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{OXAPAY_API}/payment/{track_id}",
            headers={"merchant_api_key": api_key, "Content-Type": "application/json"},
        )
        r.raise_for_status()
        return str(r.json().get("data", {}).get("status", ""))


async def refund_product_wallet(order_id: int):
    """Refund wallet contribution exactly once when the gateway payment fails."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute("SELECT user_id,wallet_used_usd,wallet_refunded,status FROM product_orders WHERE id=?", (order_id,))).fetchone()
        if row and row[3] not in ("delivered", "paid", "paid_stock_issue"):
            if row[0] and float(row[1] or 0) > 0 and not row[2]:
                await db.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?", (float(row[1]), row[0]))
                await db.execute("INSERT OR IGNORE INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)", (row[0], float(row[1]), "refund", f"product-order-{order_id}-refund", datetime.utcnow().isoformat()))
            await db.execute("UPDATE product_orders SET wallet_refunded=1,status='payment_failed' WHERE id=?", (order_id,))
            await finalize_coupon_redemption(db, "product", order_id, False)
        await db.commit()


async def complete_product_order(order_id: int, reference: str = ""):
    """Finalize a product order atomically; inventory is reserved and delivered once."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute(
            "SELECT product_id,quantity,status,user_id,product_name,amount_usd,gateway_method "
            "FROM product_orders WHERE id=?", (order_id,)
        )).fetchone()
        if not row or row[2] != "pending_payment":
            await db.commit()
            return None
        pid, qty, _, user_id, product_name, amount_usd, gateway_method = row
        await finalize_coupon_redemption(db, "product", order_id, True)
        product = await (await db.execute("SELECT delivery_mode FROM products WHERE id=?", (pid,))).fetchone()
        if not product:
            await db.execute("UPDATE product_orders SET status='paid_stock_issue',ref_id=?,paid_at=? WHERE id=?", (reference, datetime.utcnow().isoformat(), order_id))
            await db.commit()
            await notify_admins_sale("خرید اکانت / محصول", order_id, user_id, product_name, amount_usd, gateway_method,
                                     "پرداخت موفق است اما محصول در دیتابیس پیدا نشد.")
            return None
        token = None
        if product[0] == "automatic":
            items = await (await db.execute("SELECT id FROM product_inventory WHERE product_id=? AND status='available' ORDER BY id LIMIT ?", (pid, qty))).fetchall()
            if len(items) != qty:
                await db.execute("UPDATE product_orders SET status='paid_stock_issue',ref_id=?,paid_at=? WHERE id=?", (reference, datetime.utcnow().isoformat(), order_id))
                await db.commit()
                await notify_admins_sale("خرید اکانت / محصول", order_id, user_id, product_name, amount_usd, gateway_method,
                                         f"پرداخت موفق است اما موجودی کافی نیست؛ تعداد درخواستی: {qty}.")
                return None
            now = datetime.utcnow().isoformat()
            for item in items:
                cur = await db.execute("UPDATE product_inventory SET status='sold',order_id=?,delivered_at=? WHERE id=? AND status='available'", (order_id, now, item[0]))
                if cur.rowcount != 1:
                    await db.rollback()
                    return None
            await db.execute("UPDATE products SET stock=(SELECT COUNT(*) FROM product_inventory WHERE product_id=? AND status='available') WHERE id=?", (pid, pid))
            await db.execute("UPDATE products SET active=0 WHERE id=? AND stock<=0", (pid,))
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            await db.execute("UPDATE product_orders SET status='delivered',ref_id=?,paid_at=?,delivery_token_hash=? WHERE id=?", (reference, now, token_hash, order_id))
        else:
            cur = await db.execute("UPDATE products SET stock=stock-? WHERE id=? AND stock>=?", (qty, pid, qty))
            if cur.rowcount != 1:
                await db.execute("UPDATE product_orders SET status='paid_stock_issue',ref_id=?,paid_at=? WHERE id=?", (reference, datetime.utcnow().isoformat(), order_id))
            else:
                await db.execute("UPDATE products SET active=0 WHERE id=? AND stock<=0", (pid,))
                await db.execute("UPDATE product_orders SET status='paid',ref_id=?,paid_at=? WHERE id=?", (reference, datetime.utcnow().isoformat(), order_id))
        status_row = await (await db.execute("SELECT status FROM product_orders WHERE id=?", (order_id,))).fetchone()
        await db.commit()
    await notify_admins_sale(
        "خرید اکانت / محصول", order_id, user_id, product_name, amount_usd, gateway_method,
        f"تعداد: {qty} | وضعیت سفارش: {(status_row[0] if status_row else 'ثبت‌شده')}"
    )
    return token


async def oxapay_poller(bot: Bot):
    """هر ۴۵ ثانیه وضعیت فاکتورهای ارزی در انتظار (OxaPay و NOWPayments) را چک می‌کند (بدون نیاز به وب‌هوک/دامنه)."""
    while True:
        await asyncio.sleep(45)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                oxapay_key = await get_setting(db, "oxapay_api_key")
                nowpayments_key = await get_setting(db, "nowpayments_api_key")
                cur = await db.execute(
                    "SELECT id, user_id, amount, track_id, method FROM wallet_requests "
                    "WHERE status='pending' AND method IN ('oxapay','nowpayments') AND track_id IS NOT NULL"
                )
                rows = await cur.fetchall()
                product_rows = await (await db.execute("SELECT id,user_id,gateway_track_id,gateway_method FROM product_orders WHERE status='pending_payment' AND gateway_method IN ('oxapay','nowpayments') AND gateway_track_id IS NOT NULL")).fetchall()
                server_rows = await (await db.execute("SELECT id,user_id,gateway_track_id,gateway_method FROM web_server_orders WHERE status='pending_payment' AND gateway_method IN ('oxapay','nowpayments') AND gateway_track_id IS NOT NULL")).fetchall()
            for req_id, user_id, amount, track_id, method in rows:
                try:
                    if method == "oxapay":
                        if not oxapay_key:
                            continue
                        status = await oxapay_check_status(oxapay_key, track_id)
                        is_paid = "paid" in status.lower() or "complet" in status.lower() or "confirm" in status.lower()
                    else:
                        if not nowpayments_key:
                            continue
                        status = await nowpayments_check_status(nowpayments_key, track_id)
                        is_paid = status.lower() == "finished"
                except Exception:
                    continue
                if is_paid:
                    credited = False
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("BEGIN IMMEDIATE")
                        changed = await db.execute(
                            "UPDATE wallet_requests SET status='approved' WHERE id=? AND status='pending'",
                            (req_id,),
                        )
                        if changed.rowcount == 1:
                            balance_change = await db.execute(
                                "UPDATE users SET balance=balance+? WHERE telegram_id=?",
                                (amount, user_id),
                            )
                            if balance_change.rowcount != 1:
                                await db.rollback()
                                raise RuntimeError(f"wallet user {user_id} missing for request {req_id}")
                            await db.execute(
                                "INSERT INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) "
                                "VALUES(?,?,?,?,?)",
                                (user_id, float(amount), "wallet_deposit", f"wallet-request-{req_id}-credit", datetime.utcnow().isoformat()),
                            )
                            credited = True
                        await db.commit()
                    if credited:
                        try:
                            await bot.send_message(
                                user_id, f"✅ پرداخت ارزی شما تایید شد و مبلغ {fmt_price(amount)} به کیف پول اضافه شد."
                            )
                        except Exception:
                            pass
            for order_id, user_id, track_id, method in product_rows:
                try:
                    if method == "oxapay":
                        if not oxapay_key: continue
                        status = await oxapay_check_status(oxapay_key, track_id)
                        paid = "paid" in status.lower() or "complet" in status.lower() or "confirm" in status.lower()
                        failed = any(x in status.lower() for x in ("expired", "failed", "cancel"))
                    else:
                        if not nowpayments_key: continue
                        status = await nowpayments_check_status(nowpayments_key, track_id)
                        paid = status.lower() == "finished"
                        failed = status.lower() in ("failed", "expired", "refunded")
                    if paid:
                        token = await complete_product_order(order_id, str(track_id))
                        if token and user_id:
                            base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
                            try: await bot.send_message(user_id, f"✅ پرداخت و تحویل سفارش #{order_id} انجام شد.\nلینک امن دریافت: {base}/shop/delivery/{token}")
                            except Exception: pass
                    elif failed:
                        await refund_product_wallet(order_id)
                except Exception as exc:
                    logger.warning("product payment poll failed for order %s: %s", order_id, exc)
            for order_id, user_id, track_id, method in server_rows:
                try:
                    if method == "oxapay":
                        if not oxapay_key: continue
                        status = await oxapay_check_status(oxapay_key, track_id)
                        paid = "paid" in status.lower() or "complet" in status.lower() or "confirm" in status.lower()
                        failed = any(x in status.lower() for x in ("expired", "failed", "cancel"))
                    else:
                        if not nowpayments_key: continue
                        status = await nowpayments_check_status(nowpayments_key, track_id)
                        paid = status.lower() == "finished"; failed = status.lower() in ("failed", "expired", "refunded")
                    if paid:
                        server_id = await complete_web_server_order(order_id, str(track_id))
                        if server_id:
                            try: await bot.send_message(user_id, f"✅ سرور سفارش #{order_id} ساخته شد. شناسه سرور: {server_id}\nبرای مشاهده، از بخش سرورهای من اقدام کنید.")
                            except Exception: pass
                    elif failed: await refund_web_server_wallet(order_id)
                except Exception as exc:
                    logger.warning("web server payment poll failed for order %s: %s", order_id, exc)
        except Exception as e:
            logger.warning(f"crypto poller error: {e}")


# ---------------------------------------------------------------------------
# درگاه پرداخت ارز دیجیتال (NOWPayments)
# ---------------------------------------------------------------------------
async def nowpayments_create_payment(api_key: str, amount_usd: float, order_id: str, pay_currency: str = None, description: str = "شارژ کیف پول"):
    pay_currency = (pay_currency or NOWPAYMENTS_PAY_CURRENCY).strip().lower()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{NOWPAYMENTS_API}/payment",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={
                "price_amount": amount_usd,
                "price_currency": "usd",
                "pay_currency": pay_currency,
                "order_id": order_id,
                "order_description": str(description)[:120],
            },
        )
        if r.status_code >= 400:
            # پیام دقیق خطا از سمت NOWPayments را برمی‌گردانیم تا قابل‌عیب‌یابی باشد
            try:
                detail = r.json()
                reason = detail.get("message") or detail.get("code") or str(detail)
            except Exception:
                reason = r.text[:300]
            raise RuntimeError(f"NOWPayments ({r.status_code}): {reason}")
        data = r.json()
        return data["payment_id"], data["pay_address"], data["pay_amount"], data["pay_currency"]


async def nowpayments_check_status(api_key: str, payment_id: str) -> str:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{NOWPAYMENTS_API}/payment/{payment_id}",
            headers={"x-api-key": api_key},
        )
        r.raise_for_status()
        return str(r.json().get("payment_status", ""))


async def nowpayments_available_currencies(api_key: str):
    """لیست ارزهایی که برای این حساب NOWPayments فعال هستند (برای انتخاب ارز پرداخت درست)."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{NOWPAYMENTS_API}/merchant/coins",
            headers={"x-api-key": api_key},
        )
        r.raise_for_status()
        return r.json().get("selectedCurrencies", [])



# ---------------------------------------------------------------------------
# کیبوردها
# ---------------------------------------------------------------------------
DYNAMIC_BUTTON_CACHE = {}

async def refresh_dynamic_buttons_cache():
    """Reload admin-defined buttons from the shared SQLite database."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute(
                "SELECT id,menu_key,label,action,payload,sort_order FROM dynamic_buttons WHERE active=1 ORDER BY sort_order,id"
            )).fetchall()
        cache = {}
        for button_id, menu_key, label, action, payload, sort_order in rows:
            cache.setdefault(str(menu_key), []).append({
                "id": int(button_id), "label": str(label), "action": str(action),
                "payload": str(payload or ""), "sort_order": int(sort_order or 0)
            })
        DYNAMIC_BUTTON_CACHE.clear()
        DYNAMIC_BUTTON_CACHE.update(cache)
    except Exception:
        logger.exception("Could not refresh dynamic buttons cache")


def dynamic_inline_rows(menu_key: str):
    rows = []
    for item in DYNAMIC_BUTTON_CACHE.get(menu_key, []):
        if item["action"] == "url":
            rows.append([InlineKeyboardButton(text=item["label"], url=item["payload"])])
        else:
            rows.append([InlineKeyboardButton(text=item["label"], callback_data=f"dynbtn_{item['id']}")])
    return rows


async def run_dynamic_button_action(action: str, payload: str, user_id: int, message: Message, state: FSMContext = None):
    """Only allow the fixed, reviewed actions exposed in the admin panel."""
    if action == "text":
        await message.answer(payload or "متنی برای این دکمه تنظیم نشده است.")
        return
    if action == "url":
        safe_url = html.escape(payload, quote=True)
        await message.answer(f'<a href="{safe_url}">برای بازکردن لینک اینجا بزنید</a>')
        return
    if action == "support":
        if state is not None:
            async with aiosqlite.connect(DB_PATH) as db:
                enabled = await get_setting(db, "tickets_enabled", "1")
                support_message = await get_setting(db, "support_message", "")
            if enabled == "1":
                await state.set_state(SupportTicket.waiting)
                await message.answer("پیام خود را برای تیم پشتیبانی بنویسید:" + (f"\n\n{support_message}" if support_message else ""))
            else:
                await message.answer(support_message or "سیستم تیکت در حال حاضر غیرفعال است.")
        return
    if action == "buy_server":
        if state is None:
            await message.answer("برای شروع خرید سرور، از منوی اصلی گزینه خرید سرور را انتخاب کنید.")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            enabled = await get_enabled_provider_types(db)
        if not enabled:
            await message.answer("در حال حاضر امکان خرید فعال نیست؛ اتصال سرویس‌دهنده توسط مدیریت تنظیم نشده است.")
        elif len(enabled) == 1:
            await state.update_data(provider_type=enabled[0])
            await _buy_show_locations(message, state, enabled[0])
        else:
            kb_rows = [[InlineKeyboardButton(text=PROVIDER_LABELS.get(p, p), callback_data=f"buyprov_{p}")] for p in enabled]
            kb_rows += dynamic_inline_rows("buy_server")
            kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")])
            await state.set_state(BuyFlow.provider)
            await message.answer("🌍 کدام سرویس‌دهنده را می‌خواهید؟", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
        return
    if action == "shop":
        async with aiosqlite.connect(DB_PATH) as db:
            cats = await (await db.execute("SELECT id,name FROM product_categories WHERE active=1 ORDER BY name")).fetchall()
            products = await (await db.execute("SELECT id,name,price_usd,stock FROM products WHERE active=1 AND stock>0 ORDER BY id DESC LIMIT 30")).fetchall()
        buttons = [[InlineKeyboardButton(text=f"📂 {name}", callback_data=f"tgcat_{cid}")] for cid,name in cats]
        buttons += [[InlineKeyboardButton(text=f"🛍 {name[:24]} · ${float(price):.2f}", callback_data=f"tgprod_{pid}")] for pid,name,price,stock in products]
        buttons += dynamic_inline_rows("account_shop")
        buttons.append([InlineKeyboardButton(text="📦 سفارش‌های دیجیتال من", callback_data="tg_my_product_orders")])
        await message.answer("🛍 فروشگاه اکانت", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return
    if action == "stock":
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT name,stock FROM products WHERE active=1 ORDER BY name LIMIT 50")).fetchall()
        lines = ["📦 موجودی محصولات:"]
        lines.extend(f"• {html.escape(str(name))}: {int(stock or 0)} عدد" for name,stock in rows)
        await message.answer("\n".join(lines) if rows else "محصول فعالی ثبت نشده است.")
        return
    if action == "wallet":
        async with aiosqlite.connect(DB_PATH) as db:
            user = await (await db.execute("SELECT balance FROM users WHERE telegram_id=?", (user_id,))).fetchone()
        bal = float(user[0] or 0) if user else 0.0
        buttons = dynamic_inline_rows("wallet")
        buttons.extend([[InlineKeyboardButton(text="➕ افزایش موجودی", callback_data="wallet_charge")],
                        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")]])
        await message.answer(f"💰 موجودی فعلی شما: {fmt_price(bal)}", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return
    if action == "profile":
        async with aiosqlite.connect(DB_PATH) as db:
            user = await (await db.execute("SELECT username,full_name,balance FROM users WHERE telegram_id=?", (user_id,))).fetchone()
            count = await (await db.execute("SELECT COUNT(*) FROM servers WHERE user_id=?", (user_id,))).fetchone()
        if user:
            buttons = dynamic_inline_rows("profile") + [[InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")]]
            await message.answer(f"👤 پروفایل شما\nشناسه تلگرام: {user_id}\nنام: {user[1] or user[0] or '-'}\nموجودی: {fmt_price(user[2])}\nتعداد سرورها: {count[0]}", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return
    if action == "servers":
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute(
                "SELECT id,name,ip FROM servers WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,)
            )).fetchall()
        buttons = [[InlineKeyboardButton(text=f"🖥 {str(name or 'سرور')} · {ip or '-'} · #{sid}", callback_data=f"svshow_{sid}")] for sid,name,ip in rows]
        buttons += dynamic_inline_rows("servers")
        buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")])
        await message.answer("🖥 سرورهای شما:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return
    await message.answer("عملیات این دکمه پشتیبانی نمی‌شود.")


async def dynamic_buttons_poller():
    while True:
        await refresh_dynamic_buttons_cache()
        await asyncio.sleep(20)


def main_menu_kb(admin: bool):
    rows = [
        [KeyboardButton(text="🛒 خرید سرور"), KeyboardButton(text="🖥 سرورهای من")],
        [KeyboardButton(text="🛍 فروشگاه اکانت"), KeyboardButton(text="📦 سفارش‌های من")],
        [KeyboardButton(text="💰 کیف پول"), KeyboardButton(text="👤 پروفایل")],
        [KeyboardButton(text="🆘 پشتیبانی")],
    ]
    for item in DYNAMIC_BUTTON_CACHE.get("main", []):
        rows.append([KeyboardButton(text=item["label"])])
    if admin:
        rows.append([KeyboardButton(text="⚙️ پنل مدیریت")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def back_inline(cb="adm_back"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data=cb)]])


def admin_menu_kb():
    rows = [
        [InlineKeyboardButton(text="👥 کاربران", callback_data="adm_users")],
        [InlineKeyboardButton(text="🖥 همه سرورها (مدیریت کامل)", callback_data="adm_servers")],
        [InlineKeyboardButton(text="📦 سفارش‌ها", callback_data="adm_orders")],
        [InlineKeyboardButton(text="📊 آمار", callback_data="adm_stats")],
        [InlineKeyboardButton(text="🧩 مدیریت پلن‌ها", callback_data="adm_plans")],
        [InlineKeyboardButton(text="📍 مدیریت لوکیشن‌ها", callback_data="adm_locations")],
        [InlineKeyboardButton(text="🔑 مدیریت API های Hetzner", callback_data="adm_token")],
        [InlineKeyboardButton(text="🛠 ساخت سرور رایگان (ادمین)", callback_data="adm_direct")],
        [InlineKeyboardButton(text="💠 درگاه ارز دیجیتال (OxaPay)", callback_data="adm_oxapay")],
        [InlineKeyboardButton(text="💠 درگاه ارز دیجیتال (NOWPayments)", callback_data="adm_nowpayments")],
        [InlineKeyboardButton(text="🏦 آدرس واریز دستی", callback_data="adm_manual_addr")],
        [InlineKeyboardButton(text="💳 درخواست‌های شارژ در انتظار", callback_data="adm_wallet_reqs")],
        [InlineKeyboardButton(text="📢 پیام همگانی", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="🎫 تیکت‌ها و تنظیمات پشتیبانی", callback_data="adm_tickets")],
        [InlineKeyboardButton(text="🗄 بکاپ خودکار و عضویت اجباری", callback_data="adm_backup_join")],
        [InlineKeyboardButton(text="🌐 قیمت و دفعات رایگان تغییر IP", callback_data="adm_ip_pricing")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# وضعیت‌های مکالمه (FSM)
# ---------------------------------------------------------------------------
class BuyFlow(StatesGroup):
    provider = State()
    location = State()
    plan = State()
    image = State()
    confirm = State()


class ChargeWallet(StatesGroup):
    amount = State()
    receipt = State()


class AdminToken(StatesGroup):
    label = State()
    token = State()


class AdminAddPlan(StatesGroup):
    choosing_type = State()
    title = State()
    markup = State()
    location = State()


class AdminUserBalance(StatesGroup):
    amount = State()


class AdminServerOwner(StatesGroup):
    waiting_for_user_id = State()


class AdminOxapay(StatesGroup):
    waiting = State()


class AdminNowpayments(StatesGroup):
    waiting = State()


class AdminNowpaymentsCurrency(StatesGroup):
    waiting = State()


class AdminManualAddress(StatesGroup):
    waiting = State()


class AdminBroadcast(StatesGroup):
    waiting = State()


class RenewCoupon(StatesGroup):
    waiting = State()


class SupportTicket(StatesGroup):
    waiting = State()


class AdminIPFreeCount(StatesGroup):
    waiting = State()

class AdminIPPrice(StatesGroup):
    waiting = State()

class AdminBackupChannel(StatesGroup):
    waiting = State()

class AdminBackupInterval(StatesGroup):
    waiting = State()

class AdminJoinChannel(StatesGroup):
    waiting = State()

class AdminJoinLink(StatesGroup):
    waiting = State()

class AdminDirect(StatesGroup):
    location = State()
    type = State()
    image = State()
    confirm = State()


# ---------------------------------------------------------------------------
# تنظیمات قیمت و سهمیه رایگان تغییر IP
# ---------------------------------------------------------------------------
def ip_pricing_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 تغییر تعداد دفعات رایگان", callback_data="adm_ip_free_count")],
        [InlineKeyboardButton(text="💰 تغییر قیمت هر بار", callback_data="adm_ip_price")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")],
    ])

@router.callback_query(F.data == "adm_ip_pricing")
async def admin_ip_pricing(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("دسترسی ندارید", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        free_count, price = await get_ip_change_config(db)
    await call.message.edit_text(
        "🌐 <b>تنظیم قیمت و سهمیه تغییر IP</b>\n\n"
        f"🎁 دفعات رایگان برای هر سرور: <b>{free_count}</b> بار\n"
        f"💰 قیمت هر تغییر پس از سهمیه: <b>{fmt_price(price)}</b>\n\n"
        "تغییرات در دیتابیس ذخیره می‌شوند و پس از راه‌اندازی مجدد هم باقی می‌مانند.",
        reply_markup=ip_pricing_kb(), parse_mode=ParseMode.HTML)
    await call.answer()

@router.callback_query(F.data == "adm_ip_free_count")
async def admin_ip_free_count_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی ندارید", show_alert=True)
    await state.set_state(AdminIPFreeCount.waiting)
    await call.message.answer("🎁 تعداد دفعات رایگان را به‌صورت عدد صحیح بفرست (مثلاً 0، 2 یا 5). عدد صفر یعنی بدون دفعات رایگان.")
    await call.answer()

@router.message(AdminIPFreeCount.waiting)
async def admin_ip_free_count_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        value = int(message.text.strip())
        if not 0 <= value <= 100000: raise ValueError()
    except (AttributeError, ValueError):
        return await message.answer("❌ مقدار نامعتبر است. یک عدد صحیح بین 0 تا 100000 بفرست.")
    async with aiosqlite.connect(DB_PATH) as db:
        await set_setting(db, "ip_change_free_count", str(value))
    await state.clear()
    await message.answer(f"✅ سهمیه رایگان تغییر IP روی {value} بار تنظیم شد.", reply_markup=admin_menu_kb())

@router.callback_query(F.data == "adm_ip_price")
async def admin_ip_price_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی ندارید", show_alert=True)
    await state.set_state(AdminIPPrice.waiting)
    await call.message.answer("💰 قیمت هر بار تغییر IP را به دلار بفرست (مثلاً 0.50 یا 1). برای رایگان بودن پس از سهمیه، 0 وارد کن.")
    await call.answer()

@router.message(AdminIPPrice.waiting)
async def admin_ip_price_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        value = float(message.text.strip().replace(",", ".").replace("$", ""))
        if not 0 <= value <= 1000000: raise ValueError()
    except (AttributeError, ValueError):
        return await message.answer("❌ قیمت نامعتبر است. یک عدد صفر یا بیشتر وارد کن؛ مثلاً 0.50.")
    async with aiosqlite.connect(DB_PATH) as db:
        await set_setting(db, "ip_change_price", f"{value:.6f}".rstrip("0").rstrip("."))
    await state.clear()
    await message.answer(f"✅ قیمت هر تغییر IP روی {fmt_price(value)} تنظیم شد.", reply_markup=admin_menu_kb())

# ---------------------------------------------------------------------------
# بکاپ خودکار پروژه و عضویت اجباری کانال
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent
BACKUP_EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", "backups"}

class MembershipMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if isinstance(event, CallbackQuery) and event.data == "check_mandatory_membership":
            return await handler(event, data)
        if not user or is_admin(user.id):
            return await handler(event, data)
        bot = data.get("bot")
        async with aiosqlite.connect(DB_PATH) as db:
            channel = await get_setting(db, "mandatory_channel", "")
            invite = await get_setting(db, "mandatory_channel_link", "")
            enabled = await get_setting(db, "mandatory_enabled", "1" if channel else "0")
        if not channel or enabled != "1":
            return await handler(event, data)
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user.id)
            if member.status in ("creator", "administrator", "member") or (member.status == "restricted" and getattr(member, "is_member", False)):
                return await handler(event, data)
        except Exception as exc:
            logger.warning("membership check failed for %s in %s: %s", user.id, channel, exc)
            # Fail closed: configured channel must be accessible and the bot should be admin/member.
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 عضویت در کانال", url=invite)] if invite else [InlineKeyboardButton(text="📢 باز کردن کانال", url=f"https://t.me/{str(channel).lstrip('@')}")],
            [InlineKeyboardButton(text="✅ بررسی عضویت", callback_data="check_mandatory_membership")],
        ])
        if isinstance(event, Message):
            await event.answer("برای استفاده از ربات ابتدا در کانال زیر عضو شو و سپس «بررسی عضویت» را بزن.", reply_markup=kb)
        elif isinstance(event, CallbackQuery):
            await event.answer("ابتدا باید عضو کانال شوی.", show_alert=True)
        return None


def backup_join_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 تنظیم کانال بکاپ", callback_data="set_backup_channel")],
        [InlineKeyboardButton(text="⏱ تنظیم فاصله بکاپ", callback_data="set_backup_interval")],
        [InlineKeyboardButton(text="▶️ فعال‌کردن بکاپ", callback_data="enable_auto_backup"), InlineKeyboardButton(text="⏸ توقف بکاپ", callback_data="disable_auto_backup")],
        [InlineKeyboardButton(text="📢 تنظیم کانال عضویت اجباری", callback_data="set_join_channel")],
        [InlineKeyboardButton(text="🔗 تنظیم لینک عضویت", callback_data="set_join_link")],
        [InlineKeyboardButton(text="🧪 ارسال بکاپ آزمایشی", callback_data="send_backup_now")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")],
    ])

@router.callback_query(F.data == "adm_backup_join")
async def admin_backup_join(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("دسترسی ندارید", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        backup_channel = await get_setting(db, "backup_channel", "تنظیم نشده")
        interval = await get_setting(db, "backup_interval_minutes", "60")
        enabled = await get_setting(db, "backup_enabled", "0")
        join_channel = await get_setting(db, "mandatory_channel", "غیرفعال")
        join_link = await get_setting(db, "mandatory_channel_link", "تنظیم نشده")
        join_enabled = await get_setting(db, "mandatory_enabled", "1" if join_channel and join_channel != "غیرفعال" else "0")
    status = "فعال" if enabled == "1" else "متوقف"
    await call.message.edit_text(
        "🗄 <b>بکاپ خودکار و عضویت اجباری</b>\n\n"
        f"کانال بکاپ: <code>{html.escape(backup_channel)}</code>\n"
        f"فاصله بکاپ: {html.escape(str(interval))} دقیقه\nوضعیت بکاپ: {status}\n\n"
        f"کانال عضویت اجباری: <code>{html.escape(join_channel)}</code>\n"
        f"وضعیت عضویت اجباری: {'فعال' if join_enabled == '1' else 'غیرفعال'}\n"
        f"لینک عضویت: {html.escape(join_link)}\n\n"
        "برای ارسال بکاپ، ربات باید در کانال مقصد اجازه ارسال فایل داشته باشد. برای بررسی عضویت نیز ربات باید به کانال دسترسی داشته باشد.",
        reply_markup=backup_join_kb())
    await call.answer()

@router.callback_query(F.data == "set_backup_channel")
async def set_backup_channel_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی ندارید", show_alert=True)
    await state.set_state(AdminBackupChannel.waiting)
    await call.message.answer("شناسه کانال بکاپ را بفرست (مثلاً <code>-1001234567890</code> یا <code>@channelname</code>). ربات باید اجازه ارسال فایل داشته باشد.")
    await call.answer()

@router.message(AdminBackupChannel.waiting)
async def set_backup_channel_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    value=(message.text or "").strip()
    if not value or len(value)>100:
        return await message.answer("شناسه کانال معتبر نیست؛ دوباره بفرست.")
    async with aiosqlite.connect(DB_PATH) as db: await set_setting(db,"backup_channel",value)
    await state.clear(); await message.answer("✅ کانال بکاپ ذخیره شد. از /admin و بخش بکاپ و عضویت ادامه بده.")

@router.callback_query(F.data == "set_backup_interval")
async def set_backup_interval_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی ندارید", show_alert=True)
    await state.set_state(AdminBackupInterval.waiting)
    await call.message.answer("فاصله بکاپ را بفرست؛ مثال: <code>30m</code> برای ۳۰ دقیقه یا <code>2h</code> برای ۲ ساعت. حداقل ۵ دقیقه.")
    await call.answer()

@router.message(AdminBackupInterval.waiting)
async def set_backup_interval_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    raw=(message.text or "").strip().lower().replace(" ", "")
    try:
        if raw.endswith("m"): minutes=int(raw[:-1])
        elif raw.endswith("h"): minutes=int(raw[:-1])*60
        else: minutes=int(raw)
        if minutes < 5 or minutes > 43200: raise ValueError
    except ValueError:
        return await message.answer("فرمت نامعتبر است. مثل 15m، 2h یا 60 بفرست. حداقل ۵ دقیقه و حداکثر ۳۰ روز.")
    async with aiosqlite.connect(DB_PATH) as db: await set_setting(db,"backup_interval_minutes",str(minutes))
    await state.clear(); await message.answer(f"✅ فاصله بکاپ روی {minutes} دقیقه تنظیم شد.")

@router.callback_query(F.data.in_({"enable_auto_backup", "disable_auto_backup"}))
async def toggle_auto_backup(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی ندارید", show_alert=True)
    enabled = "1" if call.data == "enable_auto_backup" else "0"
    async with aiosqlite.connect(DB_PATH) as db:
        channel=await get_setting(db,"backup_channel","")
        await set_setting(db,"backup_enabled",enabled)
    if enabled == "1" and not channel:
        async with aiosqlite.connect(DB_PATH) as db: await set_setting(db,"backup_enabled","0")
        return await call.answer("اول کانال بکاپ را تنظیم کن.", show_alert=True)
    await call.answer("بکاپ خودکار فعال شد." if enabled=="1" else "بکاپ خودکار متوقف شد.", show_alert=True)

@router.callback_query(F.data == "send_backup_now")
async def send_backup_now(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("دسترسی ندارید", show_alert=True)
    await call.answer("در حال ساخت و ارسال بکاپ...", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        channel = await get_setting(db, "backup_channel", "")
    if not channel:
        return await call.message.answer("❌ ابتدا کانال بکاپ را تنظیم کن.")
    try:
        await send_project_backup(call.bot, channel)
        async with aiosqlite.connect(DB_PATH) as db:
            await set_setting(db, "backup_last_epoch", str(time.time()))
            await set_setting(db, "backup_last_result", "ارسال آزمایشی موفق در " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        await call.message.answer("✅ بکاپ آزمایشی با موفقیت به کانال ارسال شد.")
    except Exception as exc:
        logger.exception("manual backup send failed")
        async with aiosqlite.connect(DB_PATH) as db:
            await set_setting(db, "backup_last_result", "خطا: " + str(exc)[:300])
        await call.message.answer("❌ ارسال بکاپ ناموفق بود: " + html.escape(str(exc)[:500]))

@router.callback_query(F.data == "set_join_channel")
async def set_join_channel_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی ندارید", show_alert=True)
    await state.set_state(AdminJoinChannel.waiting)
    await call.message.answer("شناسه کانال عضویت اجباری را بفرست (مثلاً <code>@channelname</code> یا شناسه عددی <code>-100...</code>). برای کانال خصوصی، شناسه عددی را وارد کن.")
    await call.answer()

@router.message(AdminJoinChannel.waiting)
async def set_join_channel_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    value=(message.text or "").strip()
    if not value or len(value)>100: return await message.answer("شناسه کانال معتبر نیست؛ دوباره بفرست.")
    async with aiosqlite.connect(DB_PATH) as db: await set_setting(db,"mandatory_channel",value)
    await state.clear(); await message.answer("✅ کانال عضویت اجباری ذخیره شد. مطمئن شو ربات به کانال دسترسی دارد.")

@router.callback_query(F.data == "set_join_link")
async def set_join_link_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی ندارید", show_alert=True)
    await state.set_state(AdminJoinLink.waiting)
    await call.message.answer("لینک عضویت کانال را بفرست (مثلاً <code>https://t.me/channelname</code> یا لینک دعوت خصوصی). برای حذف لینک، کلمه <code>none</code> را بفرست.")
    await call.answer()

@router.message(AdminJoinLink.waiting)
async def set_join_link_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    value=(message.text or "").strip()
    if value.lower()=="none": value=""
    elif not value.startswith("https://t.me/"): return await message.answer("لینک باید با https://t.me/ شروع شود یا none بفرستی.")
    async with aiosqlite.connect(DB_PATH) as db: await set_setting(db,"mandatory_channel_link",value)
    await state.clear(); await message.answer("✅ لینک عضویت ذخیره شد.")

@router.callback_query(F.data == "check_mandatory_membership")
async def check_mandatory_membership(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        channel=await get_setting(db,"mandatory_channel","")
        enabled=await get_setting(db,"mandatory_enabled", "1" if channel else "0")
    if not channel or enabled != "1":
        return await call.answer("عضویت اجباری فعال نیست.", show_alert=True)
    try:
        member=await call.bot.get_chat_member(chat_id=channel,user_id=call.from_user.id)
        ok=member.status in ("creator","administrator","member") or (member.status=="restricted" and getattr(member,"is_member",False))
    except Exception:
        ok=False
    if ok:
        await call.answer("عضویت تأیید شد؛ حالا /start را بزن.", show_alert=True)
        try: await call.message.delete()
        except Exception: pass
    else:
        await call.answer("هنوز عضویتت تأیید نشده است.", show_alert=True)

async def create_project_backup() -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="hetzner_backup_"))
    archive = temp_dir / f"hetzner-shop-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in PROJECT_DIR.rglob("*"):
            if not path.is_file(): continue
            rel=path.relative_to(PROJECT_DIR)
            if any(part in BACKUP_EXCLUDE_DIRS for part in rel.parts): continue
            if path.name == ".env" or path.suffix in {".pyc", ".zip"}: continue
            # Avoid archiving the backup itself or temporary artifacts.
            if path.resolve() == archive.resolve(): continue
            zf.write(path, rel.as_posix())
    return archive

async def send_project_backup(bot: Bot, channel: str):
    """Send a new project backup, then best-effort delete the previous managed backup.

    The previous backup is never deleted until Telegram confirms the new document was sent.
    We only delete the exact message ID saved by this function, never other channel messages.
    """
    archive = await create_project_backup()
    try:
        sent = await bot.send_document(
            chat_id=channel,
            document=FSInputFile(archive),
            caption=(
                f"🗄 بکاپ پروژه\nزمان: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                "فایل تنظیمات محرمانه .env عمداً داخل بکاپ نیست."
            ),
        )
        # A successful Telegram response is required before touching the previous backup.
        async with aiosqlite.connect(DB_PATH) as db:
            old_channel = await get_setting(db, "backup_last_message_channel", "")
            old_message_id = await get_setting(db, "backup_last_message_id", "")
            # Persist the new message first, so the newest known-good backup is always tracked.
            await set_setting(db, "backup_last_message_channel", str(channel))
            await set_setting(db, "backup_last_message_id", str(sent.message_id))
        if old_channel and old_message_id:
            try:
                await bot.delete_message(chat_id=old_channel, message_id=int(old_message_id))
            except Exception as exc:
                # Do not report the successful new backup as failed just because the old
                # message cannot be deleted (missing admin rights, old message, etc.).
                logger.warning("New backup sent, but previous backup message could not be deleted: %s", exc)
    finally:
        try:
            archive.unlink()
        except OSError:
            pass
        try:
            archive.parent.rmdir()
        except OSError:
            pass

async def automatic_backup_poller(bot: Bot):
    # Settings are read repeatedly so changes in admin panel take effect without restart.
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                enabled=await get_setting(db,"backup_enabled","0")
                channel=await get_setting(db,"backup_channel","")
                interval=int(await get_setting(db,"backup_interval_minutes","60"))
                last=float(await get_setting(db,"backup_last_epoch","0"))
            now=time.time()
            if enabled=="1" and channel and now-last >= interval*60:
                await send_project_backup(bot,channel)
                async with aiosqlite.connect(DB_PATH) as db: await set_setting(db,"backup_last_epoch",str(time.time()))
        except Exception as exc:
            logger.exception("automatic backup failed: %s",exc)
        await asyncio.sleep(30)

# ---------------------------------------------------------------------------
# شروع / منوی اصلی
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        await ensure_user(db, message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer(
        "🌐 به فروشگاه سرور مجازی خوش آمدید!\n\nاز منوی زیر یکی از گزینه‌ها را انتخاب کنید.\nهمه قیمت‌ها به دلار آمریکا ($) نمایش داده می‌شوند.",
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


@router.callback_query(F.data == "adm_back")
async def adm_back(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.clear()
    await call.message.edit_text("⚙️ پنل مدیریت", reply_markup=admin_menu_kb())
    await call.answer()


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
        f"تعداد سرورها: {server_count}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=dynamic_inline_rows("profile") + [[InlineKeyboardButton(text="🔙 بازگشت به منوی اصلی", callback_data="back_main")]]
        ),
    )


# ---------------------------------------------------------------------------
# کیف پول
# ---------------------------------------------------------------------------
@router.message(F.text == "💰 کیف پول")
async def wallet_menu(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        user = await ensure_user(db, message.from_user.id, message.from_user.username, message.from_user.full_name)
    kb = InlineKeyboardMarkup(
        inline_keyboard=dynamic_inline_rows("wallet") + [
            [InlineKeyboardButton(text="➕ افزایش موجودی", callback_data="wallet_charge")],
            [InlineKeyboardButton(text="🔙 بازگشت به منوی اصلی", callback_data="back_main")],
        ]
    )
    await message.answer(f"💰 موجودی فعلی شما: {fmt_price(user[3])}", reply_markup=kb)


@router.callback_query(F.data == "wallet_charge")
async def wallet_charge_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(ChargeWallet.amount)
    await call.message.answer("مبلغ مورد نظر برای شارژ را به دلار وارد کنید (مثلاً: 10 یا 10.5):")
    await call.answer()


@router.message(ChargeWallet.amount)
async def wallet_charge_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("لطفاً یک عدد معتبر (مثلاً 10 یا 15.5) وارد کنید.")
        return
    await state.update_data(amount=amount)
    async with aiosqlite.connect(DB_PATH) as db:
        oxapay_key = await get_setting(db, "oxapay_api_key")
        nowpayments_key = await get_setting(db, "nowpayments_api_key")
    kb_rows = []
    if oxapay_key:
        kb_rows.append([InlineKeyboardButton(text="💠 پرداخت خودکار ارزی (OxaPay)", callback_data="chg_oxapay")])
    if nowpayments_key:
        kb_rows.append([InlineKeyboardButton(text="💠 پرداخت خودکار ارزی (NOWPayments)", callback_data="chg_nowpayments")])
    if kb_rows:
        kb_rows.append([InlineKeyboardButton(text="🏦 واریز دستی (کارت/ولت)", callback_data="chg_manual")])
        await message.answer("روش شارژ را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    else:
        await charge_manual_prompt(message, state)


@router.callback_query(F.data == "chg_oxapay")
async def wallet_charge_oxapay(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount")
    async with aiosqlite.connect(DB_PATH) as db:
        oxapay_key = await get_setting(db, "oxapay_api_key")
    if not amount or not oxapay_key:
        await call.answer("خطا در دریافت اطلاعات، دوباره تلاش کنید.", show_alert=True)
        return
    order_id = f"w{call.from_user.id}{int(datetime.utcnow().timestamp())}"
    try:
        track_id, pay_url = await oxapay_create_invoice(oxapay_key, amount, order_id)
    except Exception as e:
        await call.message.answer(f"❌ خطا در ساخت فاکتور پرداخت: {e}")
        await call.answer()
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_requests (user_id, amount, method, track_id, created_at) VALUES (?,?,?,?,?)",
            (call.from_user.id, amount, "oxapay", track_id, datetime.utcnow().isoformat()),
        )
        await db.commit()
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 پرداخت", url=pay_url)]])
    await call.message.edit_text(
        f"برای شارژ {fmt_price(amount)} روی دکمه پرداخت بزنید و ارز دیجیتال مورد نظر را انتخاب کنید.\n\n"
        "بعد از پرداخت، ظرف چند دقیقه به‌صورت خودکار به کیف پول شما اضافه می‌شود.",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data == "chg_nowpayments")
async def wallet_charge_nowpayments(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount")
    async with aiosqlite.connect(DB_PATH) as db:
        nowpayments_key = await get_setting(db, "nowpayments_api_key")
        pay_currency = await get_setting(db, "nowpayments_pay_currency", NOWPAYMENTS_PAY_CURRENCY)
    if not amount or not nowpayments_key:
        await call.answer("خطا در دریافت اطلاعات، دوباره تلاش کنید.", show_alert=True)
        return
    order_id = f"w{call.from_user.id}{int(datetime.utcnow().timestamp())}"
    try:
        payment_id, pay_address, pay_amount, pay_currency = await nowpayments_create_payment(
            nowpayments_key, amount, order_id, pay_currency
        )
    except Exception as e:
        await call.message.answer(
            f"❌ خطا در ساخت فاکتور پرداخت: {e}\n\n"
            "این خطا معمولاً یعنی ارز انتخاب‌شده در حساب NOWPayments شما فعال نیست. "
            "ادمین می‌تواند از بخش «💠 NOWPayments» در پنل ادمین، ارز پرداخت را تغییر دهد."
        )
        await call.answer()
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_requests (user_id, amount, method, track_id, created_at) VALUES (?,?,?,?,?)",
            (call.from_user.id, amount, "nowpayments", str(payment_id), datetime.utcnow().isoformat()),
        )
        await db.commit()
    await state.clear()
    await call.message.edit_text(
        f"برای شارژ {fmt_price(amount)} دقیقاً مبلغ زیر را به آدرس زیر واریز کنید:\n\n"
        f"💰 مبلغ: <code>{html.escape(str(pay_amount))} {html.escape(str(pay_currency).upper())}</code>\n"
        f"📥 آدرس: <code>{html.escape(str(pay_address))}</code>\n\n"
        "⚠️ فقط دقیقاً همین مبلغ و همین ارز را ارسال کنید.\n"
        "بعد از واریز و تایید شبکه، ظرف چند دقیقه به‌صورت خودکار به کیف پول شما اضافه می‌شود.",
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@router.callback_query(F.data == "chg_manual")
async def wallet_charge_manual_cb(call: CallbackQuery, state: FSMContext):
    await charge_manual_prompt(call.message, state)
    await call.answer()


async def charge_manual_prompt(message: Message, state: FSMContext):
    await state.set_state(ChargeWallet.receipt)
    async with aiosqlite.connect(DB_PATH) as db:
        address = await get_setting(db, "manual_address", "توسط ادمین تنظیم نشده - لطفاً از پشتیبانی بپرسید")
    await message.answer(
        f"مبلغ مورد نظر را به آدرس/کارت زیر واریز کنید و سپس عکس رسید را ارسال نمایید:\n\n💳 {address}"
    )


@router.message(ChargeWallet.receipt, F.photo)
async def wallet_charge_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount", 0)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_requests (user_id, amount, method, created_at) VALUES (?,?,?,?)",
            (message.from_user.id, amount, "manual", datetime.utcnow().isoformat()),
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
                caption=f"درخواست شارژ کیف پول (دستی)\nکاربر: {message.from_user.id}\nمبلغ: {fmt_price(amount)}",
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
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT user_id, amount FROM wallet_requests WHERE id=? AND status='pending'", (req_id,))
        row = await cur.fetchone()
        if not row:
            await db.rollback()
            await call.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
            return
        user_id, amount = row
        new_status = "approved" if approve else "rejected"
        changed = await db.execute(
            "UPDATE wallet_requests SET status=? WHERE id=? AND status='pending'",
            (new_status, req_id),
        )
        if changed.rowcount != 1:
            await db.rollback()
            await call.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
            return
        if approve:
            balance_change = await db.execute(
                "UPDATE users SET balance=balance+? WHERE telegram_id=?",
                (amount, user_id),
            )
            if balance_change.rowcount != 1:
                await db.rollback()
                await call.answer("حساب کاربر پیدا نشد؛ شارژ انجام نشد.", show_alert=True)
                return
            await db.execute(
                "INSERT INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) "
                "VALUES(?,?,?,?,?)",
                (user_id, float(amount), "wallet_deposit_manual", f"wallet-request-{req_id}-credit", datetime.utcnow().isoformat()),
            )
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
    async with aiosqlite.connect(DB_PATH) as db:
        enabled = await get_setting(db, "tickets_enabled", "1")
        support_id = await get_setting(db, "support_id", "")
        support_message = await get_setting(db, "support_message", "برای ارتباط با پشتیبانی از اطلاعات زیر استفاده کنید.")
    support_rows = dynamic_inline_rows("support")
    support_markup = InlineKeyboardMarkup(inline_keyboard=support_rows) if support_rows else None
    if enabled != "1":
        lines = ["🆘 پشتیبانی", support_message]
        if support_id:
            lines.append(f"شناسه پشتیبانی: {support_id}")
        await message.answer("\n\n".join(lines), reply_markup=support_markup)
        return
    await state.set_state(SupportTicket.waiting)
    lines = ["پیام خود را برای تیم پشتیبانی بنویسید:"]
    if support_message:
        lines.append(support_message)
    if support_id:
        lines.append(f"ارتباط مستقیم: {support_id}")
    await message.answer("\n\n".join(lines), reply_markup=support_markup)


@router.message(SupportTicket.waiting)
async def support_receive(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("لطفاً متن پیام را ارسال کنید.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        if await get_setting(db, "tickets_enabled", "1") != "1":
            await state.clear()
            await message.answer("سیستم تیکت در حال حاضر غیرفعال است.")
            return
        cur = await db.execute(
            "INSERT INTO tickets (user_id, message, created_at, status) VALUES (?,?,?,'open')",
            (message.from_user.id, message.text, datetime.utcnow().isoformat()),
        )
        ticket_id = cur.lastrowid
        await db.commit()
    await state.clear()
    await message.answer(f"✅ تیکت شماره #{ticket_id} ثبت شد. پاسخ از همین طریق برای شما ارسال می‌شود.")
    for admin_id in ADMIN_IDS:
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"✍️ پاسخ به تیکت #{ticket_id}", callback_data=f"ticket_reply_{ticket_id}")]])
            await message.bot.send_message(
                admin_id,
                f"🆘 تیکت جدید #{ticket_id}\nکاربر: {message.from_user.id} (@{message.from_user.username or 'ندارد'})\n\n{message.text}",
                reply_markup=kb,
            )
        except Exception as exc:
            logger.warning("cannot notify ticket to admin %s: %s", admin_id, exc)


@router.callback_query(F.data == "adm_tickets")
async def admin_tickets_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("دسترسی ندارید", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        enabled = await get_setting(db, "tickets_enabled", "1")
        support_id = await get_setting(db, "support_id", "")
        support_message = await get_setting(db, "support_message", "")
        cur = await db.execute("SELECT id, user_id, message, created_at, COALESCE(status,'open') FROM tickets ORDER BY id DESC LIMIT 15")
        rows = await cur.fetchall()
    kb_rows = [[InlineKeyboardButton(text=("⏸ غیرفعال‌کردن تیکت" if enabled == "1" else "▶️ فعال‌کردن تیکت"), callback_data="ticket_toggle")],
               [InlineKeyboardButton(text="🆔 تنظیم آیدی پشتیبانی", callback_data="support_set_id"), InlineKeyboardButton(text="💬 تنظیم پیام پشتیبانی", callback_data="support_set_message")]]
    lines = [f"🎫 مدیریت تیکت‌ها | وضعیت: {'فعال' if enabled == '1' else 'غیرفعال'}", f"آیدی پشتیبانی: {support_id or 'تنظیم نشده'}", f"پیام: {support_message or 'تنظیم نشده'}", "", "آخرین تیکت‌ها:"]
    for tid, uid, msg, created, status in rows:
        lines.append(f"#{tid} | کاربر {uid} | {'باز' if status == 'open' else 'پاسخ‌داده‌شده'}\n{(msg or '')[:180]}")
        if status == 'open':
            kb_rows.append([InlineKeyboardButton(text=f"✍️ پاسخ #{tid}", callback_data=f"ticket_reply_{tid}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")])
    await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(F.data == "ticket_toggle")
async def admin_ticket_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("دسترسی ندارید", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        old = await get_setting(db, "tickets_enabled", "1")
        await set_setting(db, "tickets_enabled", "0" if old == "1" else "1")
    await admin_tickets_menu(call)


@router.callback_query(F.data == "support_set_id")
async def support_set_id_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی ندارید", show_alert=True)
    await state.set_state(AdminSupportSettings.waiting_id)
    await call.message.answer("آیدی پشتیبانی را ارسال کنید (مثلاً @support یا لینک https://t.me/support). برای پاک کردن، - بفرستید.")
    await call.answer()


@router.callback_query(F.data == "support_set_message")
async def support_set_message_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی ندارید", show_alert=True)
    await state.set_state(AdminSupportSettings.waiting_message)
    await call.message.answer("متن دلخواهی که هنگام زدن دکمه پشتیبانی به کاربر نمایش داده شود را ارسال کنید.")
    await call.answer()


@router.message(AdminSupportSettings.waiting_id)
async def support_set_id_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    value = "" if (message.text or "").strip() == "-" else (message.text or "").strip()
    async with aiosqlite.connect(DB_PATH) as db: await set_setting(db, "support_id", value)
    await state.clear(); await message.answer("✅ آیدی پشتیبانی ذخیره شد.")


@router.message(AdminSupportSettings.waiting_message)
async def support_set_message_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    async with aiosqlite.connect(DB_PATH) as db: await set_setting(db, "support_message", message.text or "")
    await state.clear(); await message.answer("✅ متن پشتیبانی ذخیره شد.")


class AdminTicketReply(StatesGroup):
    waiting = State()


@router.callback_query(F.data.startswith("ticket_reply_"))
async def admin_ticket_reply_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی ندارید", show_alert=True)
    ticket_id = int(call.data.rsplit("_", 1)[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, message FROM tickets WHERE id=?", (ticket_id,))
        ticket = await cur.fetchone()
    if not ticket: return await call.answer("تیکت پیدا نشد", show_alert=True)
    await state.update_data(ticket_id=ticket_id, ticket_user_id=ticket[0])
    await state.set_state(AdminTicketReply.waiting)
    await call.message.answer(f"پاسخ خود را برای تیکت #{ticket_id} بنویسید.\nپیام کاربر: {(ticket[1] or '')[:500]}")
    await call.answer()


@router.message(AdminTicketReply.waiting)
async def admin_ticket_reply_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data(); ticket_id = data.get("ticket_id"); user_id = data.get("ticket_user_id")
    if not message.text:
        await message.answer("لطفاً پاسخ را به صورت متن ارسال کنید."); return
    try:
        await message.bot.send_message(user_id, f"📩 پاسخ پشتیبانی | تیکت #{ticket_id}\n\n{message.text}")
    except Exception as exc:
        await message.answer(f"❌ ارسال پاسخ به کاربر ناموفق بود: {exc}"); return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tickets SET status='answered', reply=?, replied_at=? WHERE id=?", (message.text, datetime.utcnow().isoformat(), ticket_id))
        await db.commit()
    await state.clear()
    await message.answer(f"✅ پاسخ تیکت #{ticket_id} برای کاربر ارسال شد.")


# ---------------------------------------------------------------------------
# خرید سرور (قیمت‌ها خودکار و زنده از Hetzner + درصد سود ادمین)
# ---------------------------------------------------------------------------
@router.message(F.text == "🛒 خرید سرور")
async def buy_start(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        enabled = await get_enabled_provider_types(db)
    if not enabled:
        await message.answer("در حال حاضر امکان خرید فعال نیست (هیچ اتصال سرویس‌دهنده‌ای توسط ادمین تنظیم نشده).")
        return
    if len(enabled) == 1:
        await state.update_data(provider_type=enabled[0])
        await _buy_show_locations(message, state, enabled[0])
        return
    kb_rows = [
        [InlineKeyboardButton(text=PROVIDER_LABELS.get(p, p), callback_data=f"buyprov_{p}")] for p in enabled
    ]
    kb_rows += dynamic_inline_rows("buy_server")
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")])
    await state.set_state(BuyFlow.provider)
    await message.answer("🌍 کدام سرویس‌دهنده را می‌خواهید؟", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(BuyFlow.provider, F.data.startswith("buyprov_"))
async def buy_provider_chosen(call: CallbackQuery, state: FSMContext):
    ptype = call.data.replace("buyprov_", "")
    await state.update_data(provider_type=ptype)
    await _buy_show_locations(call.message, state, ptype, edit=False)
    await call.answer()


async def _buy_show_locations(message: Message, state: FSMContext, provider_type: str, edit: bool = False):
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token_for(db, provider_type)
    if not token:
        text = "اتصال این سرویس‌دهنده در حال حاضر فعال نیست."
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return
    api = make_provider_api(provider_type, token)
    try:
        locations = await api.locations()
    except Exception as e:
        text = f"خطا در دریافت لیست دیتاسنترها: {e}"
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return
    if not locations:
        text = "در حال حاضر دیتاسنتری در دسترس نیست."
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return
    if provider_type == "hetzner":
        async with aiosqlite.connect(DB_PATH) as db:
            disabled = await get_disabled_locations(db)
        locations = [loc for loc in locations if loc["name"] not in disabled]
    if not locations:
        text = "در حال حاضر هیچ دیتاسنتری برای فروش فعال نیست."
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return
    kb_rows = [
        [InlineKeyboardButton(text=f"{loc['city']} ({loc['country']})" if loc["country"] else loc["city"], callback_data=f"loc_{loc['name']}")]
        for loc in locations
    ]
    kb_rows += dynamic_inline_rows("buy_server")
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")])
    await state.set_state(BuyFlow.location)
    text = "📍 یک دیتاسنتر انتخاب کنید:"
    if edit:
        try:
            await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
        except Exception as e:
            # Avoid creating duplicate menus; unchanged message is already correct.
            if "message is not modified" not in str(e).lower():
                logger.warning("Could not edit location menu: %s", e)
    else:
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


def compute_price_generic(types_list, server_type_id, markup_percent):
    for t in types_list:
        if str(t.get("id")) == str(server_type_id):
            base = t.get("price_monthly")
            if base is None:
                return None
            return round(float(base) * (1 + markup_percent / 100), 2)
    return None


async def images_for_plan(provider_type: str, api, server_type: str):
    """لیست سیستم‌عامل‌ها؛ برای Hetzner فقط ایمیج‌های هم‌معماری (x86/arm) با پلن انتخاب‌شده."""
    if provider_type != "hetzner":
        return await api.images()
    arch = None
    try:
        arch = await api.server_type_architecture(server_type)
    except Exception as exc:
        logger.warning("Could not read architecture of %s: %s", server_type, exc)
    return await api.images(architecture=arch)


def deduplicate_images(images):
    """Remove duplicate OS choices by display name, keeping the newest/first API entry."""
    result = []
    seen = set()
    for img in images or []:
        name = str(img.get("name") or img.get("description") or img.get("id") or "").strip()
        key = " ".join(name.casefold().split())
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(img)
    return result


def extract_server_ip(provider_type: str, server: dict) -> str:
    if provider_type == "hetzner":
        return ((server.get("public_net") or {}).get("ipv4") or {}).get("ip") or "در حال تخصیص"
    if provider_type == "digitalocean":
        for n in server.get("networks", {}).get("v4", []):
            if n.get("type") == "public":
                return n.get("ip_address", "در حال تخصیص")
        return "در حال تخصیص"
    if provider_type == "vultr":
        return server.get("main_ip") or "در حال تخصیص"
    if provider_type == "gcore":
        return server.get("ip") or "در حال تخصیص"
    return "-"


@router.callback_query(BuyFlow.location, F.data.startswith("loc_"))
async def buy_location(call: CallbackQuery, state: FSMContext):
    location = call.data.replace("loc_", "", 1)
    await _render_location_plans(call, state, location)


async def _render_location_plans(call: CallbackQuery, state: FSMContext, location: str):
    data = await state.get_data()
    provider_type = data.get("provider_type", "hetzner")
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token_for(db, provider_type)
        cur = await db.execute(
            "SELECT id, title, cpu, ram, disk, server_type, markup_percent FROM plans "
            "WHERE active=1 AND provider_type=? AND (location IS NULL OR location='' OR location=?)",
            (provider_type, location),
        )
        plans = await cur.fetchall()
        if not plans:
            await call.message.answer("در حال حاضر پلنی برای این دیتاسنتر تعریف نشده است.")
            await call.answer()
            return
        api = make_provider_api(provider_type, token) if token else None
        pricing = None
        types_list = None
        stock = None
        kb_rows = []
        if provider_type == "hetzner":
            try:
                providers = await get_active_providers(db, provider_type)
                if not providers:
                    raise RuntimeError("هیچ API فعال هتزنری وجود ندارد")
                pricing_api = make_provider_api(provider_type, providers[0][1])
                pricing = await pricing_api.pricing()
                stock_by_provider = []
                for pid, ptoken in providers:
                    try:
                        stock_by_provider.append((pid, await available_types_for_location(HetznerAPI(ptoken), location)))
                    except Exception:
                        stock_by_provider.append((pid, None))
                available_union = set().union(*(s for _, s in stock_by_provider if s is not None)) if any(s is not None for _, s in stock_by_provider) else None
            except Exception as e:
                await call.answer("استعلام قیمت/موجودی ناموفق بود؛ دوباره تلاش کنید.", show_alert=True)
                logger.warning("Hetzner plan/stock lookup failed for %s: %s", location, e)
                return
            for p in plans:
                plan_id, title, cpu, ram, disk, server_type, markup = p
                price = await compute_price_usd(db, pricing, server_type, location, markup)
                if price is None:
                    continue
                in_stock = (available_union is not None and server_type in available_union)
                if available_union is None:
                    label = "⚠️ موجودی نامشخص"
                    callback = f"planout_{plan_id}"
                elif in_stock:
                    label = "✅ موجود (یکی از APIها)"
                    callback = f"plan_{plan_id}"
                else:
                    label = "❌ ناموجود در همه APIها"
                    callback = f"planout_{plan_id}"
                kb_rows.append([InlineKeyboardButton(
                    text=f"{title} | {cpu} / {ram} / {disk} - {fmt_price(price)}/ماه | {label}",
                    callback_data=callback,
                )])
        else:
            try:
                types_list = await api.server_types()
            except Exception as e:
                await call.answer("خطا در دریافت قیمت‌ها؛ دوباره تلاش کنید.", show_alert=True)
                return
            for p in plans:
                plan_id, title, cpu, ram, disk, server_type, markup = p
                price = compute_price_generic(types_list, server_type, markup)
                if price is None:
                    continue
                kb_rows.append([InlineKeyboardButton(
                    text=f"{title} | {cpu} / {ram} / {disk} - {fmt_price(price)}/ماه",
                    callback_data=f"plan_{plan_id}",
                )])
    await state.update_data(location=location, pricing=pricing, types_list=types_list)
    kb_rows.append([InlineKeyboardButton(text="🔄 همگام‌سازی موجودی", callback_data=f"stock_sync_{location}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت به لوکیشن‌ها", callback_data="back_buy_locations")])
    await state.set_state(BuyFlow.plan)
    text = "📦 پلن موردنظر را انتخاب کنید (قیمت ماهانه زنده است):"
    if call.message.text:
        try:
            await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
        except Exception as e:
            # Telegram reports an error when the refreshed text/keyboard is identical.
            # Do not send another message: the existing menu is already up to date.
            if "message is not modified" not in str(e).lower():
                logger.warning("Could not refresh plan menu for %s: %s", location, e)
                await call.answer("به‌روزرسانی پیام انجام نشد؛ دوباره تلاش کنید.", show_alert=True)
                return
    else:
        await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer("موجودی به‌روزرسانی شد" if call.data.startswith("stock_sync_") else None)


@router.callback_query(F.data.startswith("stock_sync_"))
async def buy_stock_sync(call: CallbackQuery, state: FSMContext):
    location = call.data[len("stock_sync_"):]
    await _render_location_plans(call, state, location)


@router.callback_query(F.data.startswith("planout_"))
async def buy_unavailable_plan(call: CallbackQuery):
    await call.answer("❌ این پلن در حال حاضر موجود نیست یا موجودی آن قابل تأیید نیست. از دکمه همگام‌سازی موجودی استفاده کنید.", show_alert=True)


@router.callback_query(F.data == "back_buy_locations")
async def back_buy_locations(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await _buy_show_locations(call.message, state, data.get("provider_type", "hetzner"), edit=True)
    await call.answer()


@router.callback_query(BuyFlow.plan, F.data.startswith("plan_"))
async def buy_plan(call: CallbackQuery, state: FSMContext):
    plan_id = int(call.data.replace("plan_", ""))
    data = await state.get_data()
    provider_type = data.get("provider_type", "hetzner")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, server_type, title, markup_percent FROM plans WHERE id=?", (plan_id,))
        plan = await cur.fetchone()
        token = await get_active_token_for(db, provider_type)
    if not plan or not token:
        await call.answer("خطا در دریافت اطلاعات پلن", show_alert=True)
        return
    if provider_type == "hetzner":
        data_now = await state.get_data()
        location_now = data_now.get("location")
        async with aiosqlite.connect(DB_PATH) as db:
            providers = await get_active_providers(db, provider_type)
        stocks = []
        for pid, ptoken in providers:
            try:
                stocks.append(await available_types_for_location(HetznerAPI(ptoken), location_now))
            except Exception:
                stocks.append(None)
        available = set().union(*(s for s in stocks if s is not None)) if any(s is not None for s in stocks) else None
        if available is None or plan[1] not in available:
            await call.answer("❌ موجودی این پلن در هیچ API فعال تأیید نشد. لطفاً موجودی را همگام‌سازی کنید.", show_alert=True)
            return
    await state.update_data(plan_id=plan[0], server_type=plan[1], plan_title=plan[2], markup=plan[3])
    api = make_provider_api(provider_type, token)
    try:
        images = deduplicate_images(await images_for_plan(provider_type, api, plan[1]))
    except Exception as e:
        await call.message.answer(f"خطا در دریافت سیستم‌عامل‌ها: {e}")
        await call.answer()
        return
    kb_rows = [[InlineKeyboardButton(text=f"{img.get('name')}", callback_data=f"img_{img['id']}")] for img in images[:100]]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت به پلن‌ها", callback_data="back_buy_plan")])
    await state.set_state(BuyFlow.image)
    await call.message.edit_text("💿 سیستم‌عامل را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(F.data == "back_buy_plan")
async def back_buy_plan(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    location = data.get("location")
    if not location:
        await state.clear()
        await call.message.edit_text("اطلاعات خرید پیدا نشد. از منوی اصلی دوباره شروع کنید.")
        await call.answer()
        return
    await _render_location_plans(call, state, location)


@router.callback_query(F.data == "back_buy_image")
async def back_buy_image(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    provider_type = data.get("provider_type", "hetzner")
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token_for(db, provider_type)
    if not token:
        await state.clear()
        await call.message.edit_text("اتصال سرویس‌دهنده در دسترس نیست. از منوی اصلی دوباره شروع کنید.")
        await call.answer()
        return
    try:
        images = deduplicate_images(await images_for_plan(provider_type, make_provider_api(provider_type, token), data.get("server_type")))
    except Exception as exc:
        await call.answer("دریافت سیستم‌عامل‌ها ناموفق بود؛ دوباره تلاش کنید.", show_alert=True)
        logger.warning("Back-to-image menu failed: %s", exc)
        return
    rows = [[InlineKeyboardButton(text=img.get("name", "سیستم‌عامل"), callback_data=f"img_{img['id']}")] for img in images[:100]]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به پلن‌ها", callback_data="back_buy_plan")])
    await state.set_state(BuyFlow.image)
    await call.message.edit_text("💿 سیستم‌عامل را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(BuyFlow.image, F.data.startswith("img_"))
async def buy_image(call: CallbackQuery, state: FSMContext):
    image_id = call.data.replace("img_", "")
    await state.update_data(image=image_id)
    data = await state.get_data()
    provider_type = data.get("provider_type", "hetzner")
    async with aiosqlite.connect(DB_PATH) as db:
        if provider_type == "hetzner":
            price = await compute_price_usd(db, data["pricing"], data["server_type"], data["location"], data["markup"])
        else:
            price = compute_price_generic(data["types_list"], data["server_type"], data["markup"])
    if price is None:
        await call.message.answer("متاسفانه قیمت این پلن در حال حاضر در دسترس نیست.")
        await call.answer()
        return
    await state.update_data(price=price)
    text = (
        "🧾 خلاصه سفارش:\n\n"
        f"سرویس‌دهنده: {PROVIDER_LABELS.get(provider_type, provider_type)}\n"
        f"دیتاسنتر: {data['location']}\n"
        f"پلن: {data['plan_title']}\n"
        f"قیمت ماهانه: {fmt_price(price)}\n\n"
        "آیا تایید می‌کنید؟"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ خرید با کیف پول", callback_data="confirm_buy")],
            [InlineKeyboardButton(text="💳 پرداخت مستقیم یا ترکیبی با درگاه", callback_data="webpay_server")],
            [InlineKeyboardButton(text="🔙 بازگشت به سیستم‌عامل‌ها", callback_data="back_buy_image")],
        ]
    )
    await state.set_state(BuyFlow.confirm)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(BuyFlow.confirm, F.data == "webpay_server")
async def webpay_server_checkout(call: CallbackQuery, state: FSMContext):
    data=await state.get_data();base=os.getenv("PUBLIC_BASE_URL","").rstrip("/")
    if not base or not data.get("plan_id") or not data.get("image"):
        await call.answer("آدرس عمومی سایت یا اطلاعات سفارش تنظیم نشده است.",show_alert=True);return
    params=urlencode({"provider":data.get("provider_type","hetzner"),"location":data.get("location",""),"plan_id":data.get("plan_id"),"image":data.get("image")})
    raw=secrets.token_urlsafe(24)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO customer_web_tokens(token_hash,telegram_id,redirect_path,expires_at) VALUES(?,?,?,?)",(hashlib.sha256(raw.encode()).hexdigest(),call.from_user.id,"/servers/buy?"+params,(datetime.utcnow()+timedelta(minutes=15)).isoformat()))
        await db.commit()
    url=f"{base}/auth/telegram/link/{raw}"
    await call.message.answer("روش پرداخت را در سایت انتخاب کنید؛ می‌توانید کل مبلغ را از درگاه بدهید یا موجودی کیف پول را کسر کرده و فقط مابه‌التفاوت را پرداخت کنید.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ادامه پرداخت امن",url=url)]]))
    await state.clear();await call.answer()


_BUY_IN_PROGRESS = set()  # جلوگیری از دو بار کلیک / دو سفارش هم‌زمان یک کاربر (ساخت سرور تکراری)


@router.callback_query(BuyFlow.confirm, F.data == "confirm_buy")
async def buy_confirm(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    if user_id in _BUY_IN_PROGRESS:
        await call.answer("⏳ سفارش قبلی شما هنوز در حال پردازش است؛ لطفاً صبر کنید.", show_alert=True)
        return
    _BUY_IN_PROGRESS.add(user_id)
    try:
        await _buy_confirm_impl(call, state)
    finally:
        _BUY_IN_PROGRESS.discard(user_id)


async def _save_purchase(user_id, server, server_name, ip, root_password, os_image, data, price, expires_at, provider_type, provider_id, charge_wallet=True, order_amount=None, payment_method=None, report_amount=None, bot=None):
    """ثبت سرور و سفارش؛ در خرید وب سهم کیف پول پیش‌تر به‌صورت اتمیک رزرو شده است."""
    async with aiosqlite.connect(DB_PATH) as db:
        if charge_wallet:
            cur = await db.execute("UPDATE users SET balance = balance - ? WHERE telegram_id=? AND balance>=?", (price, user_id, price))
            if cur.rowcount != 1:
                await db.rollback()
                raise ValueError("موجودی کیف پول کافی نیست")
        await db.execute(
            "INSERT INTO servers (user_id, hetzner_id, name, ip, location, plan_title, os_image, root_password, status, created_at, expires_at, monthly_price, provider_type, provider_id, is_admin_owned, is_protected) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id, server["id"], server_name, ip, data["location"], data["plan_title"],
                os_image, root_password, "running", datetime.utcnow().isoformat(), expires_at, price, provider_type, provider_id, int(user_id in ADMIN_IDS), int(user_id in ADMIN_IDS),
            ),
        )
        cur = await db.execute("SELECT last_insert_rowid()")
        server_db_id = (await cur.fetchone())[0]
        order_cur = await db.execute(
            "INSERT INTO orders (user_id, server_id, plan_title, location, price, status, created_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, server_db_id, data["plan_title"], data["location"], price if order_amount is None else order_amount, "completed", datetime.utcnow().isoformat()),
        )
        order_id = order_cur.lastrowid
        await db.commit()
    sale_amount = (price if order_amount is None else order_amount) if report_amount is None else report_amount
    method = payment_method or ("کیف پول" if charge_wallet else "ثبت‌شده از وب / پنل")
    if user_id in ADMIN_IDS and float(sale_amount or 0) == 0:
        method = "رایگان ویژه ادمین"
    await notify_admins_sale(
        "خرید سرور", order_id, user_id, data.get("plan_title") or server_name, sale_amount, method,
        f"سرور: #{server_db_id} | IP: {ip} | دیتاسنتر: {data.get('location') or '-'}",
        bot=bot,
    )
    return server_db_id


async def _buy_confirm_impl(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    provider_type = data.get("provider_type", "hetzner")
    user_id = call.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        # محاسبه قیمت نهایی دوباره برای دقت بیشتر لحظه خرید
        if provider_type == "hetzner":
            price = await compute_price_usd(db, data["pricing"], data["server_type"], data["location"], data["markup"])
        else:
            price = compute_price_generic(data["types_list"], data["server_type"], data["markup"])
        if price is None:
            price = data.get("price")
        user = await get_user(db, user_id)
        admin_free = user_id in ADMIN_IDS
        if not user or (not admin_free and user[3] < price):
            base=os.getenv("PUBLIC_BASE_URL","").rstrip("/")
            if base and data.get("plan_id") and data.get("image"):
                params=urlencode({"provider":provider_type,"location":data.get("location",""),"plan_id":data.get("plan_id"),"image":data.get("image")})
                raw=secrets.token_urlsafe(24); digest=hashlib.sha256(raw.encode()).hexdigest()
                await db.execute("INSERT INTO customer_web_tokens(token_hash,telegram_id,redirect_path,expires_at) VALUES(?,?,?,?)",(digest,user_id,"/servers/buy?"+params,(datetime.utcnow()+timedelta(minutes=15)).isoformat()))
                await db.commit()
                url=f"{base}/auth/telegram/link/{raw}"
                kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 ادامه خرید با کیف پول + درگاه",url=url)]])
                await call.message.edit_text(f"موجودی کیف پول شما برای پرداخت کامل کافی نیست. می‌توانید موجودی فعلی را کسر کرده و فقط مابه‌التفاوت را از زرین‌پال یا درگاه ارزی پرداخت کنید.\nقیمت سرور: {fmt_price(price)}",reply_markup=kb)
            else:
                await call.message.edit_text("❌ موجودی کیف پول کافی نیست. برای پرداخت ترکیبی، آدرس عمومی سایت را در تنظیمات PUBLIC_BASE_URL وارد کنید.")
            await state.clear()
            await call.answer()
            return
        providers = await get_active_providers(db, provider_type)
        provider_id, token = (providers[0] if providers else (None, None))

    if not token:
        await call.message.edit_text("❌ هیچ API فعال برای ساخت سرور پیدا نشد.")
        await state.clear()
        await call.answer()
        return

    await call.message.edit_text("⏳ در حال ساخت سرور شما... لطفاً چند لحظه صبر کنید.")
    server_name = f"srv-{user_id}-{secrets.token_hex(3)}"
    order_ref = secrets.token_hex(8)
    os_image = data["image"]

    if provider_type == "hetzner":
        try:
            created = await create_hetzner_server_with_failover(
                providers, server_name=server_name, server_type=data["server_type"],
                image=data["image"], location=data["location"], order_ref=order_ref,
            )
        except HetznerCreateFailed as failure:
            await report_hetzner_create_failure(call, user_id, data, failure)
            await state.clear()
            await call.answer()
            return
        server, root_password = created["server"], created["root_password"]
        provider_id, token = created["provider_id"], created["token"]
        os_image = created["image"]
        server_name = server.get("name") or server_name
    else:
        api = make_provider_api(provider_type, token)
        try:
            server, root_password = await api.create_server(
                name=server_name, server_type=data["server_type"], image=data["image"], location=data["location"]
            )
        except Exception as first_error:
            await call.message.answer(f"❌ خطا در ساخت سرور: {first_error}")
            await state.clear()
            await call.answer()
            return

    if not root_password:
        root_password = "از طریق دکمه «تغییر پسورد» در بخش سرورهای من دریافت کنید"

    ip = extract_server_ip(provider_type, server)
    expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()

    server_db_id = None
    for attempt in range(1, 4):
        try:
            server_db_id = await _save_purchase(
                user_id, server, server_name, ip, root_password, os_image, data, price, expires_at, provider_type, provider_id,
                charge_wallet=not admin_free, order_amount=0.0 if admin_free else price,
                payment_method="رایگان ویژه ادمین" if admin_free else "کیف پول", bot=call.bot,
            )
            break
        except Exception:
            logger.exception("Saving purchased server failed (attempt %s/3) | remote id=%s | name=%s", attempt, server.get("id"), server_name)
            await asyncio.sleep(1)

    await state.clear()
    if server_db_id is None:
        # سرور واقعاً ساخته شده ولی ثبت دیتابیس ناموفق بود: مشتری بی‌خبر نماند و ادمین اطلاع داشته باشد.
        logger.critical("Server %s (remote id %s, provider %s) was CREATED but could not be saved to the DB", server_name, server.get("id"), provider_id)
        await notify_admins(
            call.bot,
            "🚨 سرور ساخته شد ولی ثبت در دیتابیس ناموفق بود و مبلغ از کیف پول کسر نشد!\n"
            f"کاربر: {user_id}\nنام: {html.escape(str(server_name))}\nID هتزنر: {server.get('id')}\nAPI #{provider_id}\n"
            f"آی‌پی: {html.escape(str(ip))}\nمبلغ: {fmt_price(price)}\nلطفاً دستی ثبت کنید یا از «همگام‌سازی سرورها» استفاده کنید.",
        )
        await call.message.answer(
            "✅ سرور ساخته شد، اما ثبت نهایی سفارش با خطا مواجه شد و ادمین مطلع شد.\n\n"
            f"نام: {server_name}\n"
            f"آی‌پی: {ip}\n"
            f"یوزر: root\n"
            f"پسورد: {root_password}\n\n"
            "لطفاً این اطلاعات را نگه دارید؛ ادمین سرور را به حساب شما متصل می‌کند."
        )
        await call.answer()
        return

    await call.message.answer(
        "✅ سرور شما با موفقیت ساخته شد!\n\n"
        f"نام: {server_name}\n"
        f"آی‌پی: {ip}\n"
        f"یوزر: root\n"
        f"پسورد: {root_password}\n"
        f"مبلغ کسر شده: {fmt_price(0 if admin_free else price)}"
        + ("\n🎁 خرید رایگان ویژه مدیر" if admin_free else "")
        + "\n\nبرای مدیریت سرور به بخش «سرورهای من» بروید."
    )
    await call.answer()


# ---------------------------------------------------------------------------
# سرورهای من (کاربر) - و پایه مشترک برای پنل ادمین
# ---------------------------------------------------------------------------
def server_actions_kb(server_id: int, back_callback: str = "my_servers_list", restricted: bool = False, admin_owned: bool = False):
    delete_button = (InlineKeyboardButton(text="🗑 حذف سرور خودم", callback_data=f"svadmin_del_{server_id}") if admin_owned else InlineKeyboardButton(text="🗑 درخواست حذف", callback_data=f"sv_del_{server_id}"))
    if restricted and back_callback != "adm_servers":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏳ تمدید", callback_data=f"sv_renew_{server_id}"), delete_button],
            [InlineKeyboardButton(text="🔙 بازگشت به فهرست", callback_data=back_callback)],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ روشن", callback_data=f"sv_on_{server_id}"), InlineKeyboardButton(text="⏹ خاموش", callback_data=f"sv_off_{server_id}"), InlineKeyboardButton(text="🔄 ریبوت", callback_data=f"sv_reboot_{server_id}")],
        [InlineKeyboardButton(text="🔑 تغییر پسورد", callback_data=f"sv_pass_{server_id}"), InlineKeyboardButton(text="🛠 نصب مجدد", callback_data=f"sv_rebuild_{server_id}")],
        [InlineKeyboardButton(text="🌐 تغییر IP", callback_data=f"sv_ipchange_{server_id}"), InlineKeyboardButton(text="⏳ تمدید", callback_data=f"sv_renew_{server_id}")],
        *([[InlineKeyboardButton(text="🛠 تمدید مدیریتی (۱/۳/۷/۳۰ روز)", callback_data=f"admsvrenew_{server_id}")]] if back_callback == "adm_servers" else []),
        [InlineKeyboardButton(text="📊 نمایش زمان و حجم", callback_data=f"svstats_{server_id}")],
        [delete_button],
        *([[InlineKeyboardButton(text="👤 تعیین / انتقال مالکیت", callback_data=f"admsvowner_{server_id}")]] if back_callback == "adm_servers" else []),
        [InlineKeyboardButton(text="🔙 بازگشت به فهرست", callback_data=back_callback)],
    ])


@router.message(Command("shop"))
@router.message(F.text == "🛍 فروشگاه اکانت")
async def telegram_shop(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await ensure_user(db,message.from_user.id,message.from_user.username,message.from_user.full_name)
        cats = await (await db.execute("SELECT id,name FROM product_categories WHERE active=1 ORDER BY name")).fetchall()
        rows = await (await db.execute("SELECT id,name,price_usd,stock FROM products WHERE active=1 AND stock>0 ORDER BY id DESC LIMIT 30")).fetchall()
    buttons = [[InlineKeyboardButton(text=f"📂 {name}", callback_data=f"tgcat_{cid}")] for cid,name in cats]
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    for pid,name,price,stock in rows:
        buttons.append([InlineKeyboardButton(text=f"🛍 {name[:24]} · ${float(price):.2f}", callback_data=f"tgprod_{pid}")])
        if base:
            raw = secrets.token_urlsafe(24)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO customer_web_tokens(token_hash,telegram_id,redirect_path,expires_at) VALUES(?,?,?,?)", (hashlib.sha256(raw.encode()).hexdigest(),message.from_user.id,f"/shop/{pid}",(datetime.utcnow()+timedelta(minutes=15)).isoformat()))
                await db.commit()
            buttons.append([InlineKeyboardButton(text="💳 پرداخت ترکیبی / درگاه", url=f"{base}/auth/telegram/link/{raw}")])
    buttons += dynamic_inline_rows("account_shop")
    buttons.append([InlineKeyboardButton(text="📦 سفارش‌های دیجیتال من", callback_data="tg_my_product_orders")])
    await message.answer("🛍 فروشگاه اکانت\nدسته‌بندی‌ها حتی در صورت اتمام موجودی نیز نمایش داده می‌شوند:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("tgcat_"))
async def telegram_category_products(call: CallbackQuery):
    try: cid = int(call.data.split("_",1)[1])
    except Exception:
        await call.answer("دسته‌بندی نامعتبر", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        cat = await (await db.execute("SELECT name FROM product_categories WHERE id=? AND active=1", (cid,))).fetchone()
        rows = await (await db.execute("SELECT id,name,price_usd,stock FROM products WHERE category_id=? AND active=1 AND stock>0 ORDER BY id DESC", (cid,))).fetchall()
    if not cat:
        await call.answer("دسته‌بندی پیدا نشد", show_alert=True); return
    buttons = [[InlineKeyboardButton(text=f"🛍 {name[:24]} · ${float(price):.2f}", callback_data=f"tgprod_{pid}")] for pid,name,price,stock in rows]
    if not buttons: buttons = []
    buttons += dynamic_inline_rows("account_shop")
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به دسته‌بندی‌ها", callback_data="tg_shop_home")])
    await call.message.edit_text(f"📂 {cat[0]}\nمحصولات موجود:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()


@router.callback_query(F.data == "tg_shop_home")
async def telegram_shop_home_callback(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        cats = await (await db.execute("SELECT id,name FROM product_categories WHERE active=1 ORDER BY name")).fetchall()
        rows = await (await db.execute("SELECT id,name,price_usd,stock FROM products WHERE active=1 AND stock>0 ORDER BY id DESC LIMIT 30")).fetchall()
    buttons = [[InlineKeyboardButton(text=f"📂 {name}", callback_data=f"tgcat_{cid}")] for cid,name in cats]
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    for pid,name,price,stock in rows:
        buttons.append([InlineKeyboardButton(text=f"🛍 {name[:24]} · ${float(price):.2f}", callback_data=f"tgprod_{pid}")])
        if base:
            raw = secrets.token_urlsafe(24)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO customer_web_tokens(token_hash,telegram_id,redirect_path,expires_at) VALUES(?,?,?,?)", (hashlib.sha256(raw.encode()).hexdigest(),call.from_user.id,f"/shop/{pid}",(datetime.utcnow()+timedelta(minutes=15)).isoformat()))
                await db.commit()
            buttons.append([InlineKeyboardButton(text="💳 پرداخت ترکیبی / درگاه", url=f"{base}/auth/telegram/link/{raw}")])
    buttons += dynamic_inline_rows("account_shop")
    buttons.append([InlineKeyboardButton(text="📦 سفارش‌های دیجیتال من", callback_data="tg_my_product_orders")])
    await call.message.edit_text("🛍 فروشگاه اکانت", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()


@router.callback_query(F.data.startswith("tgprod_"))
async def telegram_product_detail(call: CallbackQuery):
    try: pid=int(call.data.split("_",1)[1])
    except: await call.answer("محصول نامعتبر",show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        row=await (await db.execute("SELECT name,description,price_usd,stock,delivery_mode FROM products WHERE id=? AND active=1",(pid,))).fetchone()
        user=await ensure_user(db,call.from_user.id,call.from_user.username,call.from_user.full_name)
        inventory_images=await (await db.execute(
            "SELECT image_url FROM product_inventory WHERE product_id=? AND status='available' AND image_url IS NOT NULL AND image_url!='' ORDER BY id DESC LIMIT 5",
            (pid,),
        )).fetchall()
    if not row or row[3]<1:
        await call.answer("موجودی این محصول تمام شده است.",show_alert=True); return
    need=round(float(row[2]),2)
    base=os.getenv("PUBLIC_BASE_URL","").rstrip("/")
    buttons=[[InlineKeyboardButton(text=("🎁 خرید رایگان (مدیر)" if is_admin(call.from_user.id) else f"💰 خرید با کیف پول (${need:.2f})"),callback_data=f"tg_buy_{pid}")]]
    buttons += dynamic_inline_rows("account_shop")
    if base:
        raw=secrets.token_urlsafe(24)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO customer_web_tokens(token_hash,telegram_id,redirect_path,expires_at) VALUES(?,?,?,?)",(hashlib.sha256(raw.encode()).hexdigest(),call.from_user.id,f"/shop/{pid}",(datetime.utcnow()+timedelta(minutes=15)).isoformat())); await db.commit()
        buttons.append([InlineKeyboardButton(text="💳 پرداخت ترکیبی یا با درگاه",url=f"{base}/auth/telegram/link/{raw}")])
    await call.message.answer(f"<b>{html.escape(row[0])}</b>\n{html.escape(row[1] or '')}\n\nقیمت: ${float(row[2]):.2f}\nموجودی: {row[3]}\nموجودی کیف پول: ${float(user[3] or 0):.2f}",reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    if base:
        for image_row in inventory_images:
            image_url = str(image_row[0] or "").strip()
            if image_url.startswith("/"):
                image_url = base + image_url
            if image_url.startswith(("http://", "https://")):
                try:
                    await call.bot.send_photo(call.message.chat.id, photo=image_url, caption="🖼 تصویر جزئیات اکانت")
                except Exception:
                    logger.warning("Could not send product inventory image: product_id=%s", pid, exc_info=True)
    await call.answer()


@router.callback_query(F.data.startswith("tg_buy_"))
async def telegram_product_wallet_buy(call: CallbackQuery):
    try:
        pid = int(call.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("محصول نامعتبر است.", show_alert=True)
        return

    order_id = None
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            prod = await (await db.execute(
                "SELECT name,price_usd,stock FROM products WHERE id=? AND active=1", (pid,)
            )).fetchone()
            user = await ensure_user(
                db, call.from_user.id, call.from_user.username, call.from_user.full_name
            )
            if not prod or int(prod[2] or 0) < 1:
                await db.rollback()
                await call.answer("موجودی این محصول تمام شده است.", show_alert=True)
                return

            amount = math.ceil(float(prod[1]) * 1_000_000) / 1_000_000
            if amount <= 0:
                await db.rollback()
                await call.answer("قیمت محصول معتبر نیست؛ با پشتیبانی تماس بگیرید.", show_alert=True)
                return
            admin_free = is_admin(call.from_user.id)
            charge_amount = 0.0 if admin_free else amount
            if not admin_free and float(user[3] or 0) < amount:
                await db.rollback()
                await call.answer(
                    "موجودی کیف پول کافی نیست؛ از گزینه پرداخت ترکیبی استفاده کنید.",
                    show_alert=True,
                )
                return

            # خرید کیف پولی دلاری است؛ نرخ لحظه‌ای فقط برای پرداخت ریالی لازم است.
            try:
                rate = float(await get_setting(db, "wallet_usd_to_toman", "100000") or 100000)
            except (TypeError, ValueError):
                rate = 100000.0
            if rate <= 0:
                rate = 100000.0

            now = datetime.utcnow().isoformat()
            cur = await db.execute(
                "INSERT INTO product_orders(product_id,product_name,quantity,customer_name,mobile,email,amount_usd,amount_toman,status,created_at,user_id,wallet_used_usd,gateway_method,gateway_due_toman,currency_rate) "
                "VALUES(?,?,1,?,?,?,?,?,'pending_payment',?,?,?,'wallet',0,?)",
                (
                    pid, prod[0], call.from_user.full_name or call.from_user.username or str(call.from_user.id),
                    "", "", charge_amount, int(round(charge_amount * rate)), now,
                    call.from_user.id, charge_amount, rate,
                ),
            )
            order_id = cur.lastrowid
            changed = await db.execute(
                "UPDATE users SET balance=balance-? WHERE telegram_id=? AND balance>=?",
                (charge_amount, call.from_user.id, charge_amount),
            )
            if changed.rowcount != 1:
                await db.rollback()
                await call.answer("موجودی کیف پول تغییر کرده؛ دوباره تلاش کنید.", show_alert=True)
                return
            await db.execute(
                "INSERT INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)",
                (call.from_user.id, -charge_amount, "admin_free_product_purchase" if admin_free else "product_purchase",
                 f"product-order-{order_id}-debit", now),
            )
            await db.commit()

        try:
            token = await complete_product_order(order_id, "TELEGRAM_WALLET")
        except Exception:
            logger.exception("Telegram wallet product order completion failed: order_id=%s", order_id)
            await refund_product_wallet(order_id)
            await call.answer("پردازش خرید ناموفق بود؛ مبلغ به کیف پول برگشت داده شد.", show_alert=True)
            return

        if token:
            async with aiosqlite.connect(DB_PATH) as db:
                items = await (await db.execute(
                    "SELECT payload FROM product_inventory WHERE order_id=? ORDER BY id", (order_id,)
                )).fetchall()
            payload = "\n\n".join(x[0] for x in items)
            await call.message.answer(
                f"✅ خرید موفق؛ سفارش #{order_id}\n\nاطلاعات محصول شما:\n"
                f"<code>{html.escape(payload)}</code>"
            )
        else:
            await call.message.answer(
                f"✅ مبلغ از کیف پول کسر شد و سفارش #{order_id} ثبت شد. "
                "برای تحویل با پشتیبانی در ارتباط باشید."
            )
        await call.answer("خرید رایگان مدیر ثبت شد." if admin_free else "خرید ثبت شد")
    except Exception:
        logger.exception("Telegram wallet purchase failed: user_id=%s product_id=%s", call.from_user.id, pid)
        await call.answer("خطا در ثبت خرید؛ لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.", show_alert=True)


@router.callback_query(F.data == "tg_my_product_orders")
async def telegram_my_product_orders(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        rows=await (await db.execute("SELECT id,product_name,quantity,status FROM product_orders WHERE user_id=? ORDER BY id DESC LIMIT 20",(call.from_user.id,))).fetchall()
    if not rows:
        await call.message.answer("سفارش دیجیتالی ندارید.")
    else:
        lines=["📦 سفارش‌های دیجیتال شما:"]
        buttons=[]
        for r in rows:
            lines.append(f"#{r[0]} | {html.escape(r[1])} ×{r[2]} | {r[3]}")
            if r[3] == "delivered": buttons.append([InlineKeyboardButton(text=f"دریافت دوباره سفارش #{r[0]}",callback_data=f"tg_order_{r[0]}")])
        await call.message.answer("\n".join(lines),reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None)
    await call.answer()


@router.callback_query(F.data.startswith("tg_order_"))
async def telegram_product_order_details(call: CallbackQuery):
    try: oid=int(call.data.rsplit("_",1)[1])
    except: await call.answer("سفارش نامعتبر است",show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        order=await (await db.execute("SELECT product_name,status FROM product_orders WHERE id=? AND user_id=?",(oid,call.from_user.id))).fetchone()
        items=await (await db.execute("SELECT payload FROM product_inventory WHERE order_id=? AND status='sold' ORDER BY id",(oid,))).fetchall() if order and order[1]=="delivered" else []
    if not order or order[1]!="delivered" or not items:
        await call.answer("اطلاعات تحویل در دسترس نیست",show_alert=True); return
    payload="\n\n".join(x[0] for x in items)
    await call.message.answer(f"📦 سفارش #{oid} - {html.escape(order[0])}\n<code>{html.escape(payload)}</code>")
    await call.answer()


@router.message(Command("shoporders"))
async def telegram_shop_orders(message: Message):
    if not is_admin(message.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute("SELECT id,product_name,customer_name,mobile,amount_toman,status,ref_id FROM product_orders ORDER BY id DESC LIMIT 20")).fetchall()
    if not rows:
        await message.answer("هنوز سفارشی ثبت نشده است.")
        return
    lines = ["🧾 آخرین سفارش‌های فروشگاه:"]
    for r in rows:
        lines.append(f"#{r[0]} | {r[1]} ×{r[2]} | {r[3]} | {int(r[4]):,} تومان | {r[5]} | کد: {r[6] or '-'}")
    await message.answer("\n".join(lines))


@router.message(F.text == "🖥 سرورهای من")
async def my_servers(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
             "SELECT s.id, s.name, s.ip, s.location, s.plan_title, s.status FROM servers s WHERE s.user_id=? "
            "AND NOT EXISTS (SELECT 1 FROM servers d WHERE d.provider_type IS s.provider_type "
            "AND d.provider_id IS s.provider_id AND d.hetzner_id=s.hetzner_id AND d.user_id=s.user_id AND d.id<s.id) "
            "ORDER BY s.id DESC",
            (message.from_user.id,),
        )
        servers = await cur.fetchall()
    if not servers:
        await message.answer("شما هنوز سروری خریداری نکرده‌اید.", reply_markup=back_inline("back_main"))
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🖥 {str(r[1] or 'سرور')} · {str(r[2] or '-')} · #{r[0]}", callback_data=f"svshow_{r[0]}")]
        for r in servers
    ] + dynamic_inline_rows("servers") + [[InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="back_main")]])
    await message.answer("🖥 سرورهای شما\nبرای مدیریت، یک سرور را انتخاب کنید:", reply_markup=kb)


@router.callback_query(F.data == "my_servers_list")
async def my_servers_list_callback(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT s.id, s.name, s.ip FROM servers s WHERE s.user_id=? AND NOT EXISTS ("
            "SELECT 1 FROM servers d WHERE d.provider_type IS s.provider_type AND d.provider_id IS s.provider_id "
            "AND d.hetzner_id=s.hetzner_id AND d.user_id=s.user_id AND d.id<s.id) ORDER BY s.id DESC",
            (call.from_user.id,),
        )
        servers = await cur.fetchall()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🖥 {str(r[1] or 'سرور')} · {str(r[2] or '-')} · #{r[0]}", callback_data=f"svshow_{r[0]}")]
        for r in servers
    ] + dynamic_inline_rows("servers") + [[InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="back_main")]])
    await call.message.edit_text("🖥 سرورهای شما\nیک سرور را برای مدیریت انتخاب کنید:", reply_markup=kb)
    await call.answer()


def server_stats_kb(server_id: int, created_at, expires_at, incoming, outgoing, included):
    total = int(incoming or 0) + int(outgoing or 0)
    remaining_bytes = max(0, int(included or 0) - total) if included else 0
    elapsed = format_elapsed(created_at)
    remaining_time = "نامشخص"
    try:
        exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        now = datetime.now(exp.tzinfo) if exp.tzinfo else datetime.utcnow()
        delta = exp - now
        if delta.total_seconds() <= 0:
            remaining_time = "پایان‌یافته"
        else:
            days = delta.days
            hours = delta.seconds // 3600
            minutes = (delta.seconds % 3600) // 60
            remaining_time = f"{days} روز، {hours} ساعت، {minutes} دقیقه"
    except Exception:
        pass
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⏱ سپری‌شده: {elapsed}", callback_data=f"svstat_{server_id}_elapsed")],
        [InlineKeyboardButton(text=f"⌛ باقی‌مانده: {remaining_time}", callback_data=f"svstat_{server_id}_time")],
        [InlineKeyboardButton(text=f"📤 مصرف‌شده: {format_bytes(total)}", callback_data=f"svstat_{server_id}_used")],
        [InlineKeyboardButton(text=f"📦 باقی‌مانده: {format_bytes(remaining_bytes) if included else 'نامشخص'}", callback_data=f"svstat_{server_id}_left")],
        [InlineKeyboardButton(text="🔄 همگام‌سازی", callback_data=f"svsync_{server_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت به تنظیمات سرور", callback_data=f"svshow_{server_id}")],
    ])


@router.callback_query(F.data.startswith("svstats_"))
async def server_stats_menu(call: CallbackQuery):
    sid = int(call.data.rsplit("_", 1)[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, created_at, expires_at, traffic_in_bytes, traffic_out_bytes, traffic_included_bytes FROM servers WHERE id=?", (sid,))
        row = await cur.fetchone()
    if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
        await call.answer("سرور پیدا نشد یا دسترسی ندارید.", show_alert=True)
        return
    kb = server_stats_kb(sid, row[1], row[2], row[3], row[4], row[5])
    await call.message.edit_text("📊 آمار زمان و حجم سرور\nبرای مشاهده جزئیات، هر مورد را انتخاب کنید:", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("svstat_"))
async def server_stats_detail(call: CallbackQuery):
    parts = call.data.split("_")
    sid, kind = int(parts[1]), parts[2]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, created_at, expires_at, traffic_in_bytes, traffic_out_bytes, traffic_included_bytes FROM servers WHERE id=?", (sid,))
        row = await cur.fetchone()
    if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
        await call.answer("دسترسی غیرمجاز", show_alert=True)
        return
    elapsed = format_elapsed(row[1])
    total = int(row[3] or 0) + int(row[4] or 0)
    included = int(row[5] or 0)
    remaining = max(0, included-total) if included else None
    try:
        exp = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
        now = datetime.now(exp.tzinfo) if exp.tzinfo else datetime.utcnow()
        d = exp-now
        rem_time = "پایان‌یافته" if d.total_seconds() <= 0 else f"{d.days} روز، {d.seconds//3600} ساعت، {(d.seconds%3600)//60} دقیقه"
    except Exception:
        rem_time = "نامشخص"
    values = {"elapsed": f"زمان سپری‌شده: {elapsed}", "time": f"زمان باقی‌مانده: {rem_time}", "used": f"حجم مصرف‌شده: {format_bytes(total)}", "left": f"حجم باقی‌مانده: {format_bytes(remaining) if remaining is not None else 'نامشخص'}"}
    await call.answer(values.get(kind, "اطلاعات سرور"), show_alert=True)


@router.callback_query(F.data.startswith("svsync_"))
async def server_stats_sync(call: CallbackQuery):
    sid = int(call.data.rsplit("_", 1)[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, hetzner_id, provider_type, created_at, expires_at, traffic_in_bytes, traffic_out_bytes, traffic_included_bytes FROM servers WHERE id=?", (sid,))
        row = await cur.fetchone()
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return
        token = await get_token_for_server(db, sid, row[2] or "hetzner")
    incoming, outgoing, included = row[5], row[6], row[7]
    if token:
        try:
            live = await make_provider_api(row[2] or "hetzner", token).get_server(row[1])
            incoming = int(live.get("ingoing_traffic") or 0)
            outgoing = int(live.get("outgoing_traffic") or 0)
            included = TRAFFIC_INCLUDED_BYTES
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE servers SET traffic_in_bytes=?, traffic_out_bytes=?, traffic_included_bytes=?, traffic_synced_at=? WHERE id=?", (incoming, outgoing, included, datetime.utcnow().isoformat(), sid))
                await db.commit()
        except Exception as e:
            await call.answer(f"همگام‌سازی ناموفق: {e}", show_alert=True)
            return
    kb = server_stats_kb(sid, row[3], row[4], incoming, outgoing, included)
    await call.message.edit_text("📊 آمار زمان و حجم سرور\nاطلاعات به‌روزرسانی شد:", reply_markup=kb)
    await call.answer("✅ همگام‌سازی انجام شد")


@router.callback_query(F.data.startswith("svshow_"))
async def show_server_details(call: CallbackQuery):
    sid = int(call.data.rsplit("_", 1)[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, name, ip, location, plan_title, status, created_at, expires_at, traffic_in_bytes, traffic_out_bytes, traffic_included_bytes, traffic_synced_at, deletion_pending_reason, provider_type, COALESCE(is_admin_owned,0) FROM servers WHERE id=?", (sid,))
        row = await cur.fetchone()
    if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
        await call.answer("سرور پیدا نشد یا دسترسی ندارید.", show_alert=True)
        return
    status = row[5] or "نامشخص"
    incoming, outgoing, included = int(row[8] or 0), int(row[9] or 0), int(row[10] or 0)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT hetzner_id, provider_type FROM servers WHERE id=?", (sid,))
        live_row = await cur.fetchone()
        provider_type = (live_row[1] or "hetzner") if live_row else "hetzner"
        token = await get_token_for_server(db, sid, provider_type) if live_row else None
    if live_row and token:
        try:
            api = make_provider_api(provider_type, token)
            live_server = await api.get_server(live_row[0])
            raw_status = str(live_server.get("status") or "unknown").lower()
            status_map = {"running": "🟢 روشن", "off": "🔴 خاموش", "initializing": "🟡 در حال راه‌اندازی", "starting": "🟡 در حال روشن شدن", "stopping": "🟠 در حال خاموش شدن", "rebooting": "🟡 در حال راه‌اندازی مجدد", "unknown": "⚪ نامشخص"}
            status = status_map.get(raw_status, raw_status)
            incoming = int(live_server.get("ingoing_traffic") or 0)
            outgoing = int(live_server.get("outgoing_traffic") or 0)
            included = TRAFFIC_INCLUDED_BYTES
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE servers SET status=?, traffic_in_bytes=?, traffic_out_bytes=?, traffic_included_bytes=?, traffic_synced_at=? WHERE id=?",
                    (raw_status, incoming, outgoing, included, datetime.utcnow().isoformat(), sid),
                )
                await db.commit()
        except Exception:
            status = ("🟢 روشن" if str(status).lower() == "running" else "🔴 خاموش" if str(status).lower() == "off" else f"⚪ {status}")
    total = incoming + outgoing
    remaining = max(0, included - total) if included else 0
    exp_text = row[7] or "نامشخص"
    try:
        exp_text = (iran_time(exp_text).strftime("%Y-%m-%d %H:%M (به وقت ایران)") if iran_time(exp_text) else exp_text)
    except Exception:
        pass
    text = (
        f"🖥 {row[1]}\\nآی‌پی: {row[2]}\\nدیتاسنتر: {row[3]}\\nپلن: {row[4]}\\nوضعیت: {status}\\n"
        f"⏱ زمان سپری‌شده: {format_elapsed(row[6])}\\n"
        f"📊 ترافیک مصرفی: {format_bytes(total)} (ورودی {format_bytes(incoming)} / خروجی {format_bytes(outgoing)})\\n"
        f"📦 حجم مجاز: {format_bytes(included) if included else 'از API برنگشت'}\\n"
        f"🟢 حجم باقی‌مانده: {format_bytes(remaining) if included else 'نامشخص'}\\n"
        f"⏳ اعتبار تا: {exp_text}"
    )
    if row[12]:
        text += f"\\n⚠️ درخواست حذف در انتظار ادمین: {row[12]}"
    await call.message.edit_text(text, reply_markup=server_actions_kb(sid, "my_servers_list", restricted=bool(row[12]) or bool(row[7] and row[7] <= datetime.utcnow().isoformat()), admin_owned=is_admin(call.from_user.id) and bool(row[14]) and row[0] == call.from_user.id))
    await call.answer()


async def _server_is_expired(db, server_db_id):
    row = await (await db.execute("SELECT expires_at, deletion_pending_reason, COALESCE(traffic_powered_off,0) FROM servers WHERE id=?", (server_db_id,))).fetchone()
    if not row:
        return True
    if row[1] or row[2]:
        return True
    try:
        return bool(row[0] and datetime.fromisoformat(row[0]) <= datetime.utcnow())
    except Exception:
        return False


async def _customer_server_operation_blocked(db, server_db_id):
    """Fail closed for customer operations on expired, traffic-stopped or deletion-pending servers."""
    return await _server_is_expired(db, server_db_id)


@router.callback_query(
    F.data.startswith("sv_on_")
    | F.data.startswith("sv_off_")
    | F.data.startswith("sv_reboot_")
    | F.data.startswith("sv_pass_")
    | F.data.startswith("sv_rebuild_")
    | F.data.startswith("sv_del_")
)
async def server_action(call: CallbackQuery):
    _, action, server_db_id = call.data.split("_")
    server_db_id = int(server_db_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, hetzner_id, name, provider_type, provider_id, deletion_pending_reason, COALESCE(is_protected,0), expires_at, COALESCE(traffic_powered_off,0) FROM servers WHERE id=?", (server_db_id,))
        row = await cur.fetchone()
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return
        provider_type = row[3] or "hetzner"
        expired_now = False
        try: expired_now = bool(row[7] and datetime.fromisoformat(row[7]) <= datetime.utcnow())
        except Exception: pass
        restricted_now = bool(row[5]) or expired_now or bool(row[8])
        if row[6] and action in ("off", "del"):
            await call.answer("این سرور متعلق به ادمین و محافظت‌شده است.", show_alert=True)
            return
        if restricted_now and action != "del" and not is_admin(call.from_user.id):
            await call.answer("این سرور به علت پایان مهلت/حجم محدود شده است؛ فقط تمدید یا درخواست حذف مجاز است.", show_alert=True)
            return
        token = await get_token_for_server(db, server_db_id, provider_type)
    if not token:
        await call.answer("اتصال این سرویس‌دهنده در حال حاضر فعال نیست.", show_alert=True)
        return
    api = make_provider_api(provider_type, token)
    hetzner_id = row[1]
    try:
        if action == "on":
            await api.power_action(hetzner_id, "poweron")
            if is_admin(call.from_user.id) and restricted_now:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE servers SET admin_reactivated=1,deletion_pending_reason=NULL,deletion_requested_at=NULL,deletion_notified=0,expired_powered_off_at=NULL,status='running' WHERE id=?", (server_db_id,))
                    await db.commit()
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
            safe_pass = html.escape(new_pass) if new_pass else "از طریق کنسول ارائه‌دهنده بررسی کنید"
            await call.message.answer(
                f"🔑 پسورد جدید سرور {html.escape(row[2])}:\n<code>{safe_pass}</code>", parse_mode=ParseMode.HTML
            )
            await call.answer()
        elif action == "rebuild":
            images = deduplicate_images(await api.images())
            kb_rows = [
                [InlineKeyboardButton(text=img.get("name"), callback_data=f"svrbc_{server_db_id}_{img['id']}")]
                for img in images[:100]
            ]
            kb_rows.append([InlineKeyboardButton(text="🔙 انصراف", callback_data="back_main")])
            await call.message.answer(
                f"⚠️ توجه: نصب مجدد (Rebuild) تمام اطلاعات سرور «{row[2]}» را پاک می‌کند.\n"
                "سیستم‌عامل جدید را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
            )
            await call.answer()
        elif action == "del":
            requested = await request_server_deletion(call.bot, server_db_id, "درخواست حذف از منوی سرور")
            await call.message.edit_text(
                "⏳ درخواست حذف برای ادمین ارسال شد. سرور خاموش می‌ماند تا ادمین تأیید کند."
                if requested else "⚠️ برای این سرور قبلاً درخواست حذف ثبت شده است."
            )
            await call.answer()
            return
    except Exception as e:
        await call.message.answer(f"❌ خطا: {e}")
        await call.answer()


@router.callback_query(F.data.startswith("svadmin_del_"))
async def admin_own_server_delete_prompt(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("دسترسی غیرمجاز", show_alert=True)
        return
    sid = int(call.data.rsplit("_", 1)[1])
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT name,user_id,COALESCE(is_admin_owned,0),COALESCE(is_protected,0) FROM servers WHERE id=?", (sid,))).fetchone()
    if not row or row[1] != call.from_user.id or not row[2] or not row[3]:
        await call.answer("این سرور در مالکیت محافظت‌شده حساب ادمین شما نیست.", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ بله، حذف دائمی سرور", callback_data=f"svadmin_delconfirm_{sid}")],
        [InlineKeyboardButton(text="🔙 بازگشت به سرور", callback_data=f"svshow_{sid}")],
    ])
    await call.message.edit_text(f"⚠️ حذف دائمی سرور «{row[0]}»؟\\nاین عملیات سرور را از ارائه‌دهنده حذف می‌کند و قابل بازگشت نیست.", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("svadmin_delconfirm_"))
async def admin_own_server_delete_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("دسترسی غیرمجاز", show_alert=True)
        return
    sid = int(call.data.rsplit("_", 1)[1])
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT user_id,hetzner_id,name,provider_type,COALESCE(is_admin_owned,0),COALESCE(is_protected,0) FROM servers WHERE id=?", (sid,))).fetchone()
        if not row or row[0] != call.from_user.id or not row[4] or not row[5]:
            await call.answer("مالکیت سرور تأیید نشد؛ حذف انجام نشد.", show_alert=True)
            return
        token = await get_token_for_server(db, sid, row[3] or "hetzner")
    if not token:
        await call.answer("اتصال API مالک سرور پیدا نشد؛ حذف انجام نشد.", show_alert=True)
        return
    try:
        await make_provider_api(row[3] or "hetzner", token).delete_server(row[1])
    except Exception as exc:
        await call.answer(f"حذف از ارائه‌دهنده ناموفق بود: {str(exc)[:150]}", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM servers WHERE id=? AND user_id=? AND is_admin_owned=1 AND is_protected=1", (sid, call.from_user.id))
        await db.commit()
    await call.message.edit_text(f"✅ سرور مدیریتی «{row[2]}» حذف شد.")
    await call.answer("حذف انجام شد")


@router.callback_query(F.data.startswith("delapprove_"))
async def server_delete_approve(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("فقط ادمین می‌تواند حذف را تأیید کند.", show_alert=True)
        return
    sid = int(call.data.replace("delapprove_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT hetzner_id, name, provider_type, deletion_pending_reason, COALESCE(is_protected,0) FROM servers WHERE id=?", (sid,))
        row = await cur.fetchone()
        if not row or not row[3]:
            await call.answer("درخواست حذف پیدا نشد یا قبلاً تعیین تکلیف شده.", show_alert=True)
            return
        if row[4]:
            await call.answer("این سرور محافظت‌شده است و حذف آن مجاز نیست.", show_alert=True)
            return
        token = await get_token_for_server(db, sid, row[2] or "hetzner")
    if not token:
        await call.answer("API مالک سرور پیدا نشد؛ حذف انجام نشد.", show_alert=True)
        return
    try:
        await make_provider_api(row[2] or "hetzner", token).delete_server(row[0])
    except Exception as exc:
        await call.answer(f"حذف از ارائه‌دهنده ناموفق: {str(exc)[:150]}", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM servers WHERE id=?", (sid,))
        await db.commit()
    await call.message.edit_text(f"✅ حذف سرور «{row[1]}» توسط ادمین تأیید و انجام شد.")
    await call.answer("سرور حذف شد")


@router.callback_query(F.data.startswith("delcancel_"))
async def server_delete_cancel(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("فقط ادمین می‌تواند این درخواست را لغو کند.", show_alert=True)
        return
    sid = int(call.data.replace("delcancel_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, hetzner_id, name, provider_type FROM servers WHERE id=?", (sid,))
        row = await cur.fetchone()
        if not row:
            await call.answer("سرور پیدا نشد.", show_alert=True)
            return
        token = await get_token_for_server(db, sid, row[3] or "hetzner")
        await db.execute(
            "UPDATE servers SET deletion_pending_reason=NULL, deletion_requested_at=NULL, deletion_notified=0, expiry_reminder_sent=0, traffic_warning_sent=0, admin_reactivated=0, traffic_limit_reviewed_at=CASE WHEN deletion_pending_reason LIKE '%حجم%' OR deletion_pending_reason LIKE '%ترافیک%' THEN ? ELSE traffic_limit_reviewed_at END WHERE id=?",
            (datetime.utcnow().isoformat(), sid),
        )
        await db.commit()
    if token:
        try:
            await make_provider_api(row[3] or "hetzner", token).power_action(row[1], "poweron")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE servers SET status='running' WHERE id=?", (sid,))
                await db.commit()
        except Exception:
            pass
    try:
        await call.bot.send_message(row[0], f"✅ درخواست حذف سرور «{row[2]}» توسط ادمین لغو شد.")
    except Exception:
        pass
    await call.message.edit_text(f"❌ درخواست حذف سرور «{row[2]}» لغو شد.")
    await call.answer("درخواست لغو شد")


@router.callback_query(F.data.startswith("svrbc_"))
async def server_rebuild_confirm(call: CallbackQuery):
    _, server_db_id, image_id = call.data.split("_")
    server_db_id = int(server_db_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, hetzner_id, name, provider_type, provider_id, deletion_pending_reason FROM servers WHERE id=?", (server_db_id,))
        row = await cur.fetchone()
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return
        provider_type = row[3] or "hetzner"
        if row[5]:
            await call.answer("این سرور در انتظار تأیید حذف است؛ نصب مجدد تا تعیین تکلیف ادمین غیرفعال است.", show_alert=True)
            return
        if not is_admin(call.from_user.id) and await _customer_server_operation_blocked(db, server_db_id):
            await call.answer("این سرور به علت پایان مهلت یا حجم محدود شده است؛ فقط تمدید یا درخواست حذف مجاز است.", show_alert=True)
            return
        token = await get_token_for_server(db, server_db_id, provider_type)
    if not token:
        await call.answer("اتصال این سرویس‌دهنده در حال حاضر فعال نیست.", show_alert=True)
        return
    api = make_provider_api(provider_type, token)
    try:
        new_pass = await api.rebuild_server(row[1], image_id)
    except Exception as e:
        await call.message.answer(f"❌ خطا در نصب مجدد: {e}")
        await call.answer()
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE servers SET os_image=?, root_password=?, status='running' WHERE id=?",
            (image_id, new_pass, server_db_id),
        )
        await db.commit()
    safe_pass = html.escape(new_pass) if new_pass else "از طریق کنسول ارائه‌دهنده بررسی کنید"
    await call.message.edit_text(
        f"✅ سرور «{html.escape(row[2])}» با موفقیت نصب مجدد شد.\n🔑 پسورد جدید:\n<code>{safe_pass}</code>",
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


# ---------------------------------------------------------------------------
# تغییر IP سرور (۲ بار رایگان، سپس هر بار ۵۰ سنت)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("sv_ipchange_"))
async def server_ipchange_start(call: CallbackQuery):
    # Acknowledge immediately; Telegram otherwise keeps the inline button loading.
    await call.answer()
    try:
        server_db_id = int(call.data.replace("sv_ipchange_", ""))
    except (TypeError, ValueError):
        await call.message.answer("❌ شناسه سرور نامعتبر است. لطفاً منوی سرورها را دوباره باز کنید.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, hetzner_id, name, ip_change_count, provider_type, provider_id FROM servers WHERE id=?", (server_db_id,))
        row = await cur.fetchone()
        blocked = bool(row and not is_admin(call.from_user.id) and await _customer_server_operation_blocked(db, server_db_id))
    if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
        await call.message.answer("❌ دسترسی غیرمجاز")
        return
    if blocked:
        await call.message.answer("❌ این سرور به علت پایان مهلت یا حجم محدود شده است؛ ابتدا آن را تمدید کنید.")
        return
    if (row[4] or "hetzner") != "hetzner":
        await call.message.answer("❌ این قابلیت فقط برای سرورهای Hetzner در دسترس است.")
        return
    count = row[3] or 0
    async with aiosqlite.connect(DB_PATH) as db:
        free_count, ip_price = await get_ip_change_config(db)
    cost = 0 if count < free_count else ip_price
    cost_txt = "رایگان" if cost == 0 else fmt_price(cost)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ تایید تغییر IP ({cost_txt})", callback_data=f"ipcf_{server_db_id}")],
            [InlineKeyboardButton(text="🔙 انصراف", callback_data="back_main")],
        ]
    )
    await call.message.answer(
        f"سرور «{html.escape(row[2])}» را تاکنون {count} بار IP تغییر داده‌اید.\n"
        f"دفعات رایگان: {free_count} بار برای هر سرور — بعد از آن هر بار {fmt_price(ip_price)}.\n"
        f"هزینه این تغییر: {cost_txt}\n\n"
        "⚠️ توجه: تغییر IP چند دقیقه طول می‌کشد و IP قبلی برای همیشه از بین می‌رود.",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("ipcf_"))
async def server_ipchange_confirm(call: CallbackQuery):
    # Answer Telegram's callback before any API/network work begins.
    await call.answer("در حال پردازش درخواست تغییر IP…")
    try:
        server_db_id = int(call.data.replace("ipcf_", ""))
    except (TypeError, ValueError):
        await call.message.answer("❌ شناسه سرور نامعتبر است. لطفاً منوی سرورها را دوباره باز کنید.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, hetzner_id, name, ip_change_count, provider_type, provider_id FROM servers WHERE id=?", (server_db_id,))
        row = await cur.fetchone()
        blocked = bool(row and not is_admin(call.from_user.id) and await _customer_server_operation_blocked(db, server_db_id))
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.message.edit_text("❌ دسترسی غیرمجاز")
            return
        if blocked:
            await call.message.edit_text("❌ این سرور به علت پایان مهلت یا حجم محدود شده است؛ ابتدا آن را تمدید کنید.")
            return
        if (row[4] or "hetzner") != "hetzner":
            await call.message.edit_text("❌ این قابلیت فقط برای سرورهای Hetzner در دسترس است.")
            return
        owner_id, hetzner_id, name, count, provider_type, provider_id = row
        count = count or 0
        free_count, ip_price = await get_ip_change_config(db)
        cost = 0 if count < free_count else ip_price
        if cost > 0 and owner_id:
            cur2 = await db.execute("SELECT balance FROM users WHERE telegram_id=?", (owner_id,))
            bal_row = await cur2.fetchone()
            if not bal_row or bal_row[0] < cost:
                await call.message.edit_text(
                    f"❌ موجودی کافی نیست. موجودی فعلی: {fmt_price(bal_row[0] if bal_row else 0)}\nلطفاً ابتدا کیف پول خود را شارژ کنید."
                )
                return
        token = await get_token_for_server(db, server_db_id, "hetzner")
        cur = await db.execute("SELECT provider_id FROM servers WHERE id=?", (server_db_id,))
        provider_id_row = await cur.fetchone()
        provider_id = provider_id_row[0] if provider_id_row else provider_id
    if not token:
        await call.message.edit_text("❌ API مالک این سرور پیدا نشد؛ اگر API حذف شده باشد باید دوباره اضافه شود.")
        return
    api = HetznerAPI(token)
    try:
        await call.message.edit_text("⏳ درخواست تغییر IP ارسال شد؛ در حال انجام عملیات با هتزنر هستم. لطفاً چند دقیقه صبر کنید…")
        new_ip, old_ip_id, old_ip_address = await change_server_ip(api, hetzner_id)
    except Exception as e:
        logger.exception("IP change failed for local server id=%s, Hetzner id=%s", server_db_id, hetzner_id)
        try:
            await call.message.edit_text(f"❌ تغییر IP ناموفق بود. جزئیات API: {html.escape(str(e))}")
        finally:
            await call.answer("تغییر IP ناموفق بود", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cleanup_at = (datetime.utcnow()+timedelta(minutes=30)).isoformat()
        await db.execute(
            "UPDATE servers SET ip=?, ip_change_count=ip_change_count+1, previous_ip_id=?, previous_ip_address=?, previous_ip_cleanup_at=? WHERE id=?",
            (new_ip, old_ip_id, old_ip_address, cleanup_at, server_db_id),
        )
        await db.execute(
            "INSERT INTO pending_ip_cleanup (provider_id, provider_type, ip_id, ip_address, cleanup_at) VALUES (?,?,?,?,?)",
            (provider_id, "hetzner", old_ip_id, old_ip_address, cleanup_at),
        )
        if cost > 0 and owner_id:
            await db.execute("UPDATE users SET balance = balance - ? WHERE telegram_id=?", (cost, owner_id))
        await db.commit()
    cost_line = f"\nهزینه کسرشده: {fmt_price(cost)}" if cost > 0 else "\n(رایگان)"
    await call.message.edit_text(f"✅ IP سرور «{html.escape(name)}» با موفقیت تغییر کرد.\nIP جدید: <code>{html.escape(new_ip)}</code>{cost_line}", parse_mode=ParseMode.HTML)
    await call.answer()


# ---------------------------------------------------------------------------
# تمدید سرویس (دوره ۳۰ روزه)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("sv_renew_"))
async def server_renew_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    server_db_id = int(call.data.replace("sv_renew_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, name, expires_at, monthly_price, hetzner_id, provider_type "
            "FROM servers WHERE id=?", (server_db_id,)
        )
        row = await cur.fetchone()
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return
        owner_id, name, expires_at, monthly_price, remote_id, provider_type = row
        renewal_free = owner_id in ADMIN_IDS
        if (monthly_price is None or float(monthly_price) <= 0) and not renewal_free:
            monthly_price = await resolve_external_hetzner_renewal_price(
                db, server_db_id, remote_id, provider_type or "hetzner"
            )
    if not row:
        await call.answer("سرور پیدا نشد.", show_alert=True)
        return
    renewal_free = owner_id in ADMIN_IDS
    if not expires_at or (not renewal_free and (monthly_price is None or float(monthly_price) <= 0)):
        await call.answer("قیمت این سرور از پلن و قیمت‌گذاری هتزنر قابل تشخیص نیست. لطفاً نوع سرور و پلن فعال آن را در پنل مدیریت بررسی کنید.", show_alert=True)
        return
    monthly_price = float(monthly_price or 0)
    if is_admin(call.from_user.id) and owner_id != call.from_user.id:
        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛠 ادامه تمدید مدیریتی", callback_data=f"admsvrenew_{server_db_id}")],
            [InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_servers")],
        ])
        await call.message.answer(
            f"این سرور متعلق به مشتری {owner_id} است. تمدید از طرف ادمین با ثبت تراکنش و امکان منفی‌شدن کیف پول مالک انجام می‌شود.",
            reply_markup=admin_kb,
        )
        await call.answer()
        return
    exp_dt = datetime.fromisoformat(expires_at)
    renewal_free = owner_id in ADMIN_IDS
    renewal_buttons = []
    if not renewal_free:
        renewal_buttons.append([InlineKeyboardButton(text="🎟 وارد کردن کد تخفیف", callback_data=f"rncoupon_{server_db_id}")])
    renewal_buttons.extend([
        [InlineKeyboardButton(text="✅ تایید تمدید (رایگان ویژه مدیر)" if renewal_free else f"✅ تایید تمدید ({fmt_price(monthly_price)})", callback_data=f"rncf_{server_db_id}")],
        [InlineKeyboardButton(text="🔙 انصراف", callback_data="back_main")],
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=renewal_buttons)
    await call.message.answer(
        f"سرویس «{html.escape(name)}» تا تاریخ {iran_time(exp_dt).strftime('%Y-%m-%d %H:%M')} (به وقت ایران) اعتبار دارد.\n"
        + (f"هزینه تمدید {RENEWAL_PERIOD_DAYS} روز دیگر: رایگان ویژه مدیر" if renewal_free else f"هزینه تمدید {RENEWAL_PERIOD_DAYS} روز دیگر: {fmt_price(monthly_price)}"),
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data.startswith("rncoupon_"))
async def server_renew_coupon_start(call: CallbackQuery, state: FSMContext):
    try:
        sid = int(call.data.replace("rncoupon_", ""))
    except (TypeError, ValueError):
        return await call.answer("شناسه سرور نامعتبر است.", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT user_id FROM servers WHERE id=?", (sid,))).fetchone()
    if not row or row[0] != call.from_user.id:
        return await call.answer("دسترسی غیرمجاز", show_alert=True)
    await state.set_state(RenewCoupon.waiting)
    await state.update_data(server_id=sid)
    await call.message.answer("🎟 کد تخفیف را ارسال کنید؛ کد کمپین: CLOUD7")
    await call.answer()


@router.message(RenewCoupon.waiting)
async def server_renew_coupon_apply(message: Message, state: FSMContext):
    data = await state.get_data()
    sid = int(data.get("server_id") or 0)
    code = (message.text or "").strip().upper()
    if not sid or not code:
        return await message.answer("کد تخفیف معتبر نیست. دوباره ارسال کنید.")
    preview_id = time.time_ns() % 9000000000000000000
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute("SELECT user_id,monthly_price FROM servers WHERE id=?", (sid,))).fetchone()
        if not row or row[0] != message.from_user.id or not row[1] or float(row[1]) <= 0:
            await db.rollback()
            await state.clear()
            return await message.answer("سرور یا قیمت تمدید معتبر نیست. دوباره از فهرست سرورها اقدام کنید.")
        discount, coupon_id, error = await reserve_discount_coupon(
            db, code, "server_renewal", message.from_user.id, float(row[1]), "server_renewal_preview", preview_id
        )
        if error:
            await db.rollback()
            return await message.answer("❌ " + error + " دوباره کد را ارسال کنید.")
        await finalize_coupon_redemption(db, "server_renewal_preview", preview_id, success=False)
        await db.commit()
    await state.clear()
    await state.update_data(server_id=sid, coupon_code=code)
    await message.answer(
        f"✅ کد {html.escape(code)} معتبر است.\nمبلغ تمدید: {fmt_price(float(row[1]))}\nتخفیف: {fmt_price(discount)}\nمبلغ قابل پرداخت: {fmt_price(float(row[1])-discount)}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ تأیید تمدید با تخفیف", callback_data=f"rncf_{sid}")],
            [InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")],
        ])
    )


@router.callback_query(F.data.startswith("rncf_"))
async def server_renew_confirm(call: CallbackQuery, state: FSMContext):
    try:
        server_db_id = int(call.data.replace("rncf_", ""))
    except (TypeError, ValueError):
        await call.answer("شناسه سرور نامعتبر است.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT user_id, hetzner_id, name, expires_at, monthly_price, expired_powered_off_at, provider_type, provider_id, traffic_powered_off, deletion_pending_reason "
            "FROM servers WHERE id=?",
            (server_db_id,),
        )
        row = await cur.fetchone()
        if not row or row[0] != call.from_user.id:
            await db.rollback()
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return

        owner_id, hetzner_id, name, expires_at, monthly_price, expired_off_at, provider_type, provider_id, traffic_off, deletion_pending_reason = row
        provider_type = provider_type or "hetzner"
        admin_free = owner_id in ADMIN_IDS
        if (monthly_price is None or float(monthly_price) <= 0) and not admin_free:
            await db.rollback()
            await call.answer("قیمت تمدید این سرور مشخص نیست؛ لطفاً با پشتیبانی تماس بگیرید.", show_alert=True)
            return
        monthly_price = float(monthly_price or 0)
        if not expires_at:
            await db.rollback()
            await call.answer("تاریخ انقضای سرور مشخص نیست؛ لطفاً با پشتیبانی تماس بگیرید.", show_alert=True)
            return

        token = await get_token_for_server(db, server_db_id, provider_type)
        if not token:
            await db.rollback()
            await call.answer("اتصال API این سرور در دسترس نیست.", show_alert=True)
            return

        renewal_data = await state.get_data()
        coupon_code = str(renewal_data.get("coupon_code") or "").strip().upper() if (not admin_free and int(renewal_data.get("server_id") or 0) == server_db_id) else ""
        coupon_discount = 0.0
        renewal_order_id = time.time_ns() % 9000000000000000000
        if coupon_code:
            coupon_discount, coupon_id, coupon_error = await reserve_discount_coupon(
                db, coupon_code, "server_renewal", owner_id, float(monthly_price), "server_renewal", renewal_order_id
            )
            if coupon_error:
                await db.rollback()
                await state.clear()
                await call.answer(coupon_error, show_alert=True)
                return
        payable_amount = 0.0 if admin_free else max(0.0, float(monthly_price) - coupon_discount)
        bal = await (await db.execute("SELECT balance FROM users WHERE telegram_id=?", (owner_id,))).fetchone()
        if not bal or (not admin_free and float(bal[0] or 0) < payable_amount):
            await db.rollback()
            await call.message.edit_text(
                f"❌ موجودی کافی نیست. مبلغ لازم: {fmt_price(payable_amount)} | موجودی فعلی: {fmt_price(bal[0] if bal else 0)}\\nلطفاً ابتدا کیف پول خود را شارژ کنید."
            )
            await call.answer()
            return

        try:
            base = datetime.fromisoformat(expires_at)
        except Exception:
            await db.rollback()
            await call.answer("تاریخ انقضای سرور نامعتبر است؛ با پشتیبانی تماس بگیرید.", show_alert=True)
            return
        now = datetime.utcnow()
        if base < now:
            base = now
        new_expiry = base + timedelta(days=RENEWAL_PERIOD_DAYS)
        renewal_ref = f"server-renew:{server_db_id}:{expires_at}"

        if admin_free:
            debit = await db.execute("UPDATE users SET balance=balance WHERE telegram_id=?", (owner_id,))
        else:
            debit = await db.execute(
                "UPDATE users SET balance=balance-? WHERE telegram_id=? AND balance>=?",
                (payable_amount, owner_id, payable_amount),
            )
        if debit.rowcount != 1:
            await db.rollback()
            await call.answer("موجودی کیف پول تغییر کرده است؛ دوباره تلاش کنید.", show_alert=True)
            return
        try:
            await db.execute(
                "INSERT INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)",
                (owner_id, -payable_amount, "admin_free_server_renewal" if admin_free else "server_renewal", renewal_ref, datetime.utcnow().isoformat()),
            )
        except Exception:
            await db.rollback()
            await call.answer("این تمدید قبلاً ثبت شده یا تراکنش تکراری است.", show_alert=True)
            return

        await db.execute(
            "UPDATE servers SET expires_at=?, expired_powered_off_at=NULL, traffic_powered_off=0, "
            "deletion_pending_reason=NULL, deletion_requested_at=NULL, deletion_notified=0, "
            "expiry_reminder_sent=0, traffic_warning_sent=0, admin_reactivated=0, "
            "traffic_limit_reviewed_at=CASE WHEN ? LIKE '%ترافیک%' OR ? LIKE '%حجم%' THEN ? ELSE traffic_limit_reviewed_at END "
            "WHERE id=? AND user_id=?",
            (new_expiry.isoformat(), deletion_pending_reason or "", deletion_pending_reason or "",
             datetime.utcnow().isoformat(), server_db_id, owner_id),
        )
        if coupon_code:
            await finalize_coupon_redemption(db, "server_renewal", renewal_order_id, success=True)
        await db.commit()

    await notify_admins_sale(
        "تمدید سرور", renewal_order_id, owner_id, name, payable_amount,
        "رایگان ویژه ادمین" if admin_free else "کیف پول",
        f"سرور: #{server_db_id} | مدت: {RENEWAL_PERIOD_DAYS} روز | تخفیف: {coupon_discount:.2f} USD",
        bot=call.bot,
    )
    await state.clear()
    if (expired_off_at or traffic_off or deletion_pending_reason):
        try:
            await make_provider_api(provider_type, token).power_action(hetzner_id, "poweron")
        except Exception:
            logger.exception("Renewal succeeded but power-on failed for server %s", server_db_id)

    balance_after = float(bal[0] or 0) - payable_amount
    poweron_note = "\\n▶️ درخواست روشن‌کردن مجدد سرور ارسال شد." if (expired_off_at or traffic_off or deletion_pending_reason) else ""
    await call.message.edit_text(
        f"✅ سرویس «{html.escape(name)}» با موفقیت تمدید شد.\\n"
        f"مدت تمدید: {RENEWAL_PERIOD_DAYS} روز\\n"
        f"مبلغ اصلی: {fmt_price(monthly_price)}\\n"
        f"تخفیف: {fmt_price(coupon_discount)}\\n"
        f"مبلغ کسرشده: {fmt_price(payable_amount)}" + (" (رایگان برای ادمین)\\n" if admin_free else "\\n")
        + f"مانده کیف پول: {fmt_price(balance_after)}\\n"
        f"مهلت جدید: {iran_time(new_expiry).strftime('%Y-%m-%d %H:%M')} (به وقت ایران){poweron_note}"
    )
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
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=dynamic_inline_rows("orders") + [[InlineKeyboardButton(text="🔙 بازگشت به منوی اصلی", callback_data="back_main")]]
    )
    if not orders:
        await message.answer("سفارشی ثبت نشده است.", reply_markup=back_kb)
        return
    lines = ["📦 سفارش‌های اخیر شما:\n"]
    for o in orders:
        lines.append(f"• {o[0]} | {o[1]} | {fmt_price(o[2])} | {o[3]}")
    await message.answer("\n".join(lines), reply_markup=back_kb)


# ---------------------------------------------------------------------------
# پنل ادمین: کاربران / سفارش‌ها / آمار
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
        reply_markup=back_inline(),
    )
    await call.answer()


@router.callback_query(F.data == "adm_users")
async def adm_users(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT telegram_id, full_name, balance, is_banned FROM users ORDER BY telegram_id DESC LIMIT 40"
        )
        users = await cur.fetchall()
    if not users:
        await call.message.edit_text("کاربری ثبت نشده است.", reply_markup=back_inline())
        await call.answer()
        return
    kb_rows = []
    for u in users:
        ban_mark = "🚫 " if u[3] else ""
        label = f"{ban_mark}{u[1] or 'بدون‌نام'} | {u[0]} | {fmt_price(u[2])}"
        kb_rows.append([InlineKeyboardButton(text=label, callback_data=f"adu_{u[0]}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")])
    await call.message.edit_text(
        "👥 کاربران (روی هر کاربر بزنید تا کامل مدیریتش کنید):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    await call.answer()


def user_detail_kb(tid: int, banned: bool):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ افزایش موجودی", callback_data=f"aub_add_{tid}"),
                InlineKeyboardButton(text="➖ کاهش موجودی", callback_data=f"aub_sub_{tid}"),
            ],
            [InlineKeyboardButton(text="🖥 سرویس‌های این کاربر", callback_data=f"aus_{tid}")],
            [
                InlineKeyboardButton(
                    text="✅ رفع مسدودیت" if banned else "🚫 مسدود کردن کاربر",
                    callback_data=f"aubn_{tid}",
                )
            ],
            [InlineKeyboardButton(text="🔙 بازگشت به لیست کاربران", callback_data="adm_users")],
        ]
    )


@router.callback_query(F.data.startswith("adu_"))
async def adm_user_detail(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    tid = int(call.data.replace("adu_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        user = await get_user(db, tid)
        if not user:
            await call.answer("کاربر یافت نشد.", show_alert=True)
            return
        cur = await db.execute("SELECT COUNT(*) FROM servers WHERE user_id=?", (tid,))
        server_count = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (tid,))
        order_count = (await cur.fetchone())[0]
    text = (
        f"👤 مدیریت کاربر\n\n"
        f"نام: {user[2]}\n"
        f"یوزرنیم: @{user[1]}\n"
        f"شناسه تلگرام: {user[0]}\n"
        f"موجودی کیف پول: {fmt_price(user[3])}\n"
        f"وضعیت: {'🚫 مسدود' if user[4] else '✅ عادی'}\n"
        f"تعداد سرور: {server_count}\n"
        f"تعداد سفارش: {order_count}"
    )
    await call.message.edit_text(text, reply_markup=user_detail_kb(tid, bool(user[4])))
    await call.answer()


@router.callback_query(F.data.startswith("aubn_"))
async def adm_user_toggle_ban(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    tid = int(call.data.replace("aubn_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        user = await get_user(db, tid)
        new_val = 0 if (user and user[4]) else 1
        await db.execute("UPDATE users SET is_banned=? WHERE telegram_id=?", (new_val, tid))
        await db.commit()
    await call.answer("✅ بروزرسانی شد")
    await adm_user_detail(call)


@router.callback_query(F.data.startswith("aus_"))
async def adm_user_servers(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    tid = int(call.data.replace("aus_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, name, ip, location, plan_title, status FROM servers WHERE user_id=? ORDER BY id DESC", (tid,)
        )
        servers = await cur.fetchall()
    await call.message.edit_text(
        f"🖥 سرویس‌های کاربر {tid}" if servers else f"کاربر {tid} سروری ندارد.",
        reply_markup=back_inline(f"adu_{tid}"),
    )
    for s in servers:
        text = f"🖥 {s[1]}\nآی‌پی: {s[2]}\nدیتاسنتر: {s[3]}\nپلن: {s[4]}\nوضعیت: {s[5]}"
        await call.message.answer(text, reply_markup=server_actions_kb(s[0]))
    await call.answer()


@router.callback_query(F.data.startswith("aub_add_") | F.data.startswith("aub_sub_"))
async def adm_user_balance_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    sign = "add" if call.data.startswith("aub_add_") else "sub"
    tid = int(call.data.split("_")[-1])
    await state.set_state(AdminUserBalance.amount)
    await state.update_data(target_id=tid, sign=sign)
    action_txt = "افزایش" if sign == "add" else "کاهش"
    await call.message.answer(f"مبلغ مورد نظر برای {action_txt} موجودی کاربر {tid} را به دلار وارد کنید:")
    await call.answer()


@router.message(AdminUserBalance.amount)
async def adm_user_balance_apply(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("لطفاً یک عدد معتبر وارد کنید.")
        return
    data = await state.get_data()
    tid, sign = data["target_id"], data["sign"]
    delta = amount if sign == "add" else -amount
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE telegram_id=?", (delta, tid))
        await db.commit()
        user = await get_user(db, tid)
    await state.clear()
    await message.answer(
        f"✅ انجام شد. موجودی جدید کاربر {tid}: {fmt_price(user[3]) if user else '-'}",
        reply_markup=main_menu_kb(True),
    )
    try:
        action_txt = "افزایش" if sign == "add" else "کاهش"
        await message.bot.send_message(tid, f"💰 موجودی کیف پول شما توسط ادمین {action_txt} یافت: {fmt_price(amount)}")
    except Exception:
        pass


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
    await call.message.edit_text("\n".join(lines) if orders else "سفارشی ثبت نشده است.", reply_markup=back_inline())
    await call.answer()


@router.callback_query(F.data == "adm_wallet_reqs")
async def adm_wallet_reqs(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, user_id, amount, method FROM wallet_requests WHERE status='pending' ORDER BY id DESC LIMIT 20"
        )
        reqs = await cur.fetchall()
    if not reqs:
        await call.message.edit_text("درخواست شارژ در انتظاری وجود ندارد.", reply_markup=back_inline())
        await call.answer()
        return
    lines = ["💳 درخواست‌های شارژ در انتظار:\n"]
    for r in reqs:
        lines.append(f"• #{r[0]} | کاربر: {r[1]} | مبلغ: {fmt_price(r[2])} | روش: {r[3]}")
    await call.message.edit_text("\n".join(lines), reply_markup=back_inline())
    await call.answer()


# ---------------------------------------------------------------------------
# پنل ادمین: همه سرورها (دسترسی کامل ادمین + همگام‌سازی با Hetzner)
# ---------------------------------------------------------------------------
async def sync_hetzner_servers(db, api: "HetznerAPI", provider_id=None):
    """Import live Hetzner servers once, without assigning external servers implicitly."""
    try:
        hz_servers = await api.all_servers()
    except Exception as exc:
        logger.warning("Could not sync Hetzner provider %s: %s", provider_id, exc)
        return 0
    try:
        pricing = await api.pricing()
    except Exception as exc:
        logger.warning("Could not load pricing while syncing provider %s: %s", provider_id, exc)
        pricing = {}

    added = 0
    live_ids = {int(s["id"]) for s in hz_servers if s.get("id") is not None}
    for server in hz_servers:
        sid = int(server["id"])
        ip = (server.get("public_net") or {}).get("ipv4", {}).get("ip", "-")
        dc = server.get("datacenter") or {}
        loc = (dc.get("location") or {}).get("name") or dc.get("name") or "-"
        server_created = server.get("created") or datetime.utcnow().isoformat()
        try:
            created_dt = datetime.fromisoformat(str(server_created).replace("Z", "+00:00"))
            if created_dt.tzinfo:
                created_dt = created_dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            created_dt = datetime.utcnow()
        expiry = (created_dt + timedelta(days=RENEWAL_PERIOD_DAYS)).isoformat()

        st_data = server.get("server_type") or {}
        type_name = st_data.get("name") if isinstance(st_data, dict) else str(st_data or "")
        plan = None
        if type_name:
            plan = await (await db.execute(
                "SELECT title, markup_percent FROM plans WHERE provider_type='hetzner' AND server_type=? "
                "AND active=1 AND (location IS NULL OR location='' OR location=?) "
                "ORDER BY CASE WHEN location=? THEN 0 ELSE 1 END, id DESC LIMIT 1",
                (type_name, loc, loc),
            )).fetchone()
        plan_title = (plan[0] or type_name) if plan else "پلن شناسایی‌نشده"
        monthly_price = None
        if plan:
            try:
                monthly_price = await compute_price_usd(db, pricing, type_name, loc, float(plan[1] or 0))
            except Exception as exc:
                logger.warning("Could not calculate renewal price for Hetzner server %s: %s", sid, exc)

        existing = await (await db.execute(
            "SELECT id, user_id FROM servers WHERE provider_type='hetzner' AND (provider_id IS ? OR provider_id IS NULL) AND hetzner_id=? "
            "ORDER BY CASE WHEN user_id IS NOT NULL THEN 0 ELSE 1 END, id",
            (provider_id, sid),
        )).fetchall()
        if existing:
            local_id = existing[0][0]
            for (duplicate_id, _) in existing[1:]:
                await db.execute("UPDATE orders SET server_id=? WHERE server_id=?", (local_id, duplicate_id))
                await db.execute("UPDATE web_server_orders SET server_db_id=? WHERE server_db_id=?", (local_id, duplicate_id))
                await db.execute("DELETE FROM servers WHERE id=?", (duplicate_id,))
            owner = existing[0][1]
            source = "external_assigned" if owner is not None else "external"
            await db.execute(
                "UPDATE servers SET provider_id=?, name=?, ip=?, location=?, status=?, created_at=COALESCE(created_at,?), "
                "expires_at=COALESCE(expires_at,?), monthly_price=CASE WHEN monthly_price IS NULL OR monthly_price<=0 THEN ? ELSE monthly_price END, "
                "plan_title=CASE WHEN plan_title IS NULL OR plan_title='' OR plan_title IN ('ساخته‌شده خارج از ربات','پلن شناسایی‌نشده') THEN ? ELSE plan_title END, "
                "source=? WHERE id=?",
                (provider_id, server.get("name"), ip, loc, server.get("status", "unknown"), server_created,
                 expiry, monthly_price, plan_title, source, local_id),
            )
            continue

        await db.execute(
            "INSERT INTO servers (user_id, hetzner_id, name, ip, location, plan_title, os_image, root_password, status, created_at, expires_at, monthly_price, provider_id, provider_type, is_admin_owned, is_protected, source) "
            "VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?, 'hetzner',0,0,'external')",
            (sid, server.get("name"), ip, loc, plan_title, (server.get("image") or {}).get("name", "-"),
             "-", server.get("status", "unknown"), server_created, expiry, monthly_price, provider_id),
        )
        added += 1

    stale = await (await db.execute(
        "SELECT id, hetzner_id FROM servers WHERE provider_type='hetzner' AND provider_id IS ? AND source='external'",
        (provider_id,),
    )).fetchall()
    for local_id, remote_id in stale:
        if remote_id not in live_ids:
            await db.execute("DELETE FROM servers WHERE id=? AND source='external'", (local_id,))
    await db.commit()
    return added


@router.callback_query(F.data == "adm_servers")
async def adm_servers(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, api_token FROM provider WHERE type='hetzner' ORDER BY id")
        providers = await cur.fetchall()
        added = 0
        for pid, token in providers:
            added += await sync_hetzner_servers(db, HetznerAPI(token), pid)
        pids = [pid for pid, _ in providers]
        if pids:
            marks = ",".join("?" for _ in pids)
            cur = await db.execute(f"SELECT id, hetzner_id, name, ip, user_id, location, status FROM servers WHERE user_id IS NOT NULL OR (provider_id IN ({marks}) AND user_id IS NULL) ORDER BY CASE WHEN user_id IS NOT NULL THEN 0 ELSE 1 END, id DESC", pids)
        else:
            cur = await db.execute("SELECT id, hetzner_id, name, ip, user_id, location, status FROM servers WHERE user_id IS NOT NULL ORDER BY id DESC")
        raw_servers = await cur.fetchall()
        # One visible record per Hetzner server; prefer customer-owned row over imported ownerless row.
        seen = set(); servers = []
        for row in raw_servers:
            key = row[1] if row[1] is not None else ("db", row[0])
            if key in seen:
                continue
            seen.add(key); servers.append((row[0], row[2], row[3], row[4], row[5], row[6]))
        servers = servers[:80]
    if not servers:
        await call.message.edit_text("سروری ثبت نشده است.", reply_markup=back_inline())
        await call.answer()
        return
    note = f"\n(🔄 {added} سرور جدید از پنل هتزنر همگام‌سازی شد)" if added else ""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🖥 {str(s[1] or 'سرور')} · {str(s[2] or '-')} · {'👤'+str(s[3]) if s[3] else 'بدون مالک'} · #{s[0]}", callback_data=f"admsvshow_{s[0]}")]
        for s in servers
    ] + [[InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")]])
    await call.message.edit_text(f"🖥 {len(servers)} سرور — برای مدیریت انتخاب کنید:{note}", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("admsvowner_"))
async def admin_server_owner_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("دسترسی غیرمجاز", show_alert=True)
        return
    sid = int(call.data.rsplit("_", 1)[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name, user_id FROM servers WHERE id=?", (sid,))
        server = await cur.fetchone()
    if not server:
        await call.answer("سرور پیدا نشد.", show_alert=True)
        return
    await state.set_state(AdminServerOwner.waiting_for_user_id)
    await state.update_data(owner_server_id=sid)
    await call.message.answer(
        f"👤 تعیین مالک برای سرور «{server[1] or sid}»\n"
        f"مالک فعلی: {server[2] if server[2] is not None else 'بدون مالک'}\n\n"
        "شناسه عددی تلگرام (Telegram ID) مالک جدید را ارسال کنید. "
        "کاربر باید حداقل یک‌بار ربات را Start کرده باشد.\n"
        "برای لغو، /cancel را بفرستید."
    )
    await call.answer()


@router.message(AdminServerOwner.waiting_for_user_id)
async def admin_server_owner_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.answer("عملیات تعیین مالک لغو شد.", reply_markup=main_menu_kb(True))
        return
    raw_id = (message.text or "").strip()
    if not raw_id.isdigit():
        await message.answer("شناسه تلگرام باید فقط عدد باشد. دوباره ارسال کنید یا /cancel بزنید.")
        return
    target_id = int(raw_id)
    data = await state.get_data()
    sid = data.get("owner_server_id")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name, user_id FROM servers WHERE id=?", (sid,))
        server = await cur.fetchone()
        user = await get_user(db, target_id)
        if not server:
            await state.clear()
            await message.answer("سرور پیدا نشد؛ فهرست سرورها را دوباره باز کنید.")
            return
        if not user:
            await message.answer("این کاربر در ربات ثبت نشده است. از او بخواهید ابتدا ربات را Start کند، سپس شناسه را دوباره بفرستید.")
            return
        old_owner = server[2]
        await db.execute(
            "UPDATE servers SET user_id=?, source=CASE WHEN source IN ('external','external_assigned') THEN 'external_assigned' ELSE source END WHERE id=?",
            (target_id, sid),
        )
        await db.commit()
    await state.clear()
    await message.answer(
        f"✅ مالکیت سرور با موفقیت ثبت شد.\nسرور: {server[1] or sid}\n"
        f"مالک قبلی: {old_owner if old_owner is not None else 'بدون مالک'}\nمالک جدید: {target_id}",
        reply_markup=main_menu_kb(True),
    )
    try:
        await message.bot.send_message(target_id, f"👤 یک سرور به حساب شما اختصاص داده شد: {server[1] or sid} (شناسه سرور: #{sid})")
    except Exception:
        pass
    if old_owner and old_owner != target_id:
        try:
            await message.bot.send_message(old_owner, f"ℹ️ مالکیت سرور «{server[1] or sid}» به کاربر دیگری منتقل شد.")
        except Exception:
            pass


@router.callback_query(F.data.startswith("admsvshow_"))
async def admin_show_server_details(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("دسترسی غیرمجاز", show_alert=True); return
    sid = int(call.data.rsplit("_", 1)[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name, ip, user_id, location, status, created_at, traffic_in_bytes, traffic_out_bytes, traffic_included_bytes, expires_at, deletion_pending_reason FROM servers WHERE id=?", (sid,))
        row = await cur.fetchone()
    if not row:
        await call.answer("سرور پیدا نشد.", show_alert=True); return
    owner = row[2] if row[2] is not None else "بدون مالک (خارج از ربات)"
    used = int(row[6] or 0) + int(row[7] or 0)
    remaining = max(0, int(row[8] or 0) - used) if row[8] else None
    text = (
        f"🖥 {row[0]}\\nIP: {row[1]}\\nمالک: {owner}\\nموقعیت: {row[3]}\\nوضعیت: {row[4]}\\n"
        f"⏱ زمان سپری‌شده: {format_elapsed(row[5])}\\n📊 مصرف: {format_bytes(used)}\\n"
        f"📦 حجم باقی‌مانده: {format_bytes(remaining) if remaining is not None else 'نامشخص'}\\n⏳ اعتبار: {row[9] or '—'}"
    )
    if row[10]:
        text += f"\\n⚠️ حذف در انتظار ادمین: {row[10]}"
    await call.message.edit_text(text, reply_markup=server_actions_kb(sid, "adm_servers"))
    await call.answer()


@router.callback_query(F.data.startswith("admsvrenew_"))
async def admin_server_renew_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("دسترسی غیرمجاز", show_alert=True)
        return
    try:
        sid = int(call.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("شناسه سرور نامعتبر است.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT user_id,name,expires_at,monthly_price,hetzner_id,provider_type FROM servers WHERE id=?",
            (sid,),
        )).fetchone()
        if not row:
            await call.answer("سرور پیدا نشد.", show_alert=True)
            return
        if row[0] is None:
            await call.answer("ابتدا برای این سرور مالک تعیین کنید؛ تمدید مالی بدون مالک مجاز نیست.", show_alert=True)
            return
        owner_is_admin = row[0] in ADMIN_IDS
        price = float(row[3] or 0)
        if price <= 0 and not owner_is_admin:
            price = await resolve_external_hetzner_renewal_price(db, sid, row[4], row[5] or "hetzner")
    if (price is None or float(price) <= 0) and not owner_is_admin:
        await call.answer("قیمت ماهانه این سرور مشخص نیست؛ ابتدا پلن و قیمت آن را در پنل بررسی کنید.", show_alert=True)
        return
    price = float(price or 0)
    nonce = secrets.token_hex(5)
    day1 = "۱ روز · رایگان" if owner_is_admin else f"۱ روز · {fmt_price(price/30)}"
    day3 = "۳ روز · رایگان" if owner_is_admin else f"۳ روز · {fmt_price(price*3/30)}"
    day7 = "۷ روز · رایگان" if owner_is_admin else f"۷ روز · {fmt_price(price*7/30)}"
    day30 = "۳۰ روز · رایگان" if owner_is_admin else f"۳۰ روز · {fmt_price(price)}"
    buttons = [
        [InlineKeyboardButton(text=day1, callback_data=f"admsvrenewdo_{sid}_1_{nonce}"),
         InlineKeyboardButton(text=day3, callback_data=f"admsvrenewdo_{sid}_3_{nonce}")],
        [InlineKeyboardButton(text=day7, callback_data=f"admsvrenewdo_{sid}_7_{nonce}"),
         InlineKeyboardButton(text=day30, callback_data=f"admsvrenewdo_{sid}_30_{nonce}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"admsvshow_{sid}")],
    ]
    await call.message.edit_text(
        f"🛠 تمدید مدیریتی سرور «{html.escape(row[1] or str(sid))}»\n"
        f"مالک: {row[0]}\nقیمت ماهانه: {'رایگان ویژه مدیر' if owner_is_admin else fmt_price(price)}\n\n"
        + ("این سرور متعلق به حساب ادمین است و تمدید آن رایگان ثبت می‌شود." if owner_is_admin else "تمدید مدیریتی از کیف پول مالک کسر می‌شود؛ در صورت ناکافی‌بودن موجودی، مانده می‌تواند منفی شود. این استثنا فقط برای ادمین است."),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admsvrenewdo_"))
async def admin_server_renew_apply(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("دسترسی غیرمجاز", show_alert=True)
        return
    try:
        prefix, sid_s, days_s, nonce = call.data.split("_")
        if prefix != "admsvrenewdo" or not nonce or len(nonce) != 10:
            raise ValueError("invalid callback token")
        sid, days = int(sid_s), int(days_s)
    except (TypeError, ValueError):
        await call.answer("درخواست تمدید نامعتبر است.", show_alert=True)
        return
    if days not in (1, 3, 7, 30):
        await call.answer("مدت تمدید مجاز نیست.", show_alert=True)
        return

    # Resolve missing imported-server price before opening the financial transaction:
    # the resolver may persist plan metadata and commit its own DB work.
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT user_id,hetzner_id,name,expires_at,monthly_price,expired_powered_off_at,"
            "provider_type,traffic_powered_off,deletion_pending_reason FROM servers WHERE id=?",
            (sid,),
        )).fetchone()
        if not row:
            await call.answer("سرور پیدا نشد.", show_alert=True)
            return
        owner_id, provider_server_id, name, expires_at, monthly_price, expired_off, provider_type, traffic_off, deletion_reason = row
        if owner_id is None:
            await call.answer("سرور هنوز مالک ندارد؛ ابتدا مالک را تعیین کنید.", show_alert=True)
            return
        owner_is_admin = owner_id in ADMIN_IDS
        if (monthly_price is None or float(monthly_price) <= 0) and not owner_is_admin:
            monthly_price = await resolve_external_hetzner_renewal_price(db, sid, provider_server_id, provider_type or "hetzner")
        if (monthly_price is None or float(monthly_price) <= 0) and not owner_is_admin:
            await call.answer("قیمت تمدید مشخص نیست؛ تمدید انجام نشد.", show_alert=True)
            return
        monthly_price = float(monthly_price or 0)
        token = await get_token_for_server(db, sid, provider_type or "hetzner")
    if not token:
        await call.answer("API سرور در دسترس نیست؛ تمدید انجام نشد.", show_alert=True)
        return

    amount = 0.0 if owner_is_admin else round(float(monthly_price) * days / 30.0, 2)
    now = datetime.utcnow()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            current = await (await db.execute(
                "SELECT user_id,expires_at,monthly_price,COALESCE(traffic_powered_off,0),deletion_pending_reason "
                "FROM servers WHERE id=?",
                (sid,),
            )).fetchone()
            if not current or current[0] != owner_id:
                await db.rollback()
                await call.answer("مالک سرور تغییر کرده است؛ صفحه را تازه کنید.", show_alert=True)
                return
            base = now
            try:
                if current[1]:
                    previous = datetime.fromisoformat(current[1])
                    if previous > now:
                        base = previous
            except Exception:
                pass
            new_expiry = base + timedelta(days=days)
            ref = f"admin-server-renew:{nonce}"
            balrow = await (await db.execute("SELECT balance FROM users WHERE telegram_id=?", (owner_id,))).fetchone()
            if not balrow:
                await db.rollback()
                await call.answer("حساب کیف پول مالک پیدا نشد.", show_alert=True)
                return
            balance_after = float(balrow[0] or 0) - amount
            await db.execute("UPDATE users SET balance=? WHERE telegram_id=?", (balance_after, owner_id))
            await db.execute(
                "INSERT INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)",
                (owner_id, -amount, "admin_free_server_renewal" if owner_is_admin else "admin_server_renewal", ref, now.isoformat()),
            )
            await db.execute(
                "INSERT INTO admin_server_renewals(server_id,owner_id,admin_id,days,amount_usd,balance_after,created_at,reference) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (sid, owner_id, call.from_user.id, days, amount, balance_after, now.isoformat(), ref),
            )
            await db.execute(
                "UPDATE servers SET expires_at=?,expired_powered_off_at=NULL,traffic_powered_off=0,"
                "deletion_pending_reason=NULL,deletion_requested_at=NULL,deletion_notified=0,"
                "expiry_reminder_sent=0,traffic_warning_sent=0,admin_reactivated=0,"
                "traffic_limit_reviewed_at=CASE WHEN ? LIKE '%ترافیک%' OR ? LIKE '%حجم%' THEN ? ELSE traffic_limit_reviewed_at END "
                "WHERE id=? AND user_id=?",
                (new_expiry.isoformat(), deletion_reason or "", deletion_reason or "", now.isoformat(), sid, owner_id),
            )
            await db.commit()
    except Exception as exc:
        logger.exception("Admin renewal failed for server %s", sid)
        await call.answer("تمدید ثبت نشد؛ تراکنش مالی برگشت داده شد.", show_alert=True)
        return

    await notify_admins_sale(
        "تمدید مدیریتی سرور", ref, owner_id, name, amount,
        "رایگان ویژه ادمین" if owner_is_admin else "کیف پول",
        f"سرور: #{sid} | مدت: {days} روز | ادمین انجام‌دهنده: {call.from_user.id}",
        bot=call.bot,
    )
    power_note = ""
    if expired_off or traffic_off or deletion_reason:
        try:
            await make_provider_api(provider_type or "hetzner", token).power_action(provider_server_id, "poweron")
            power_note = "\\nدرخواست روشن‌کردن مجدد سرور ارسال شد."
        except Exception:
            logger.exception("Admin renewal succeeded but power-on failed for server %s", sid)
            power_note = "\\n⚠️ تمدید ثبت شد اما روشن‌کردن خودکار ناموفق بود؛ ادمین وضعیت سرور را بررسی کند."

    report = (
        f"🛠 تمدید مدیریتی سرور «{html.escape(name or str(sid))}» انجام شد.\\n"
        f"مدت: {days} روز\\nمبلغ کسرشده: {fmt_price(amount)}" + (" (رایگان برای ادمین)\\n" if owner_is_admin else "\\n")
        + f"مانده کیف پول: {fmt_price(balance_after)}\\n"
        f"مهلت جدید: {iran_time(new_expiry).strftime('%Y-%m-%d %H:%M')} (به وقت ایران)"
        f"{power_note}"
    )
    await call.message.edit_text("✅ تمدید مدیریتی ثبت شد.\n" + report)
    try:
        await call.bot.send_message(owner_id, "📢 گزارش تمدید توسط مدیریت:\\n" + report)
    except Exception:
        logger.warning("Could not notify owner %s about admin renewal of server %s", owner_id, sid)
    await call.answer("تمدید با موفقیت ثبت شد.")


# ---------------------------------------------------------------------------
# پنل ادمین: ساخت سرور رایگان مستقیم برای ادمین (بدون کسر کیف پول)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "adm_direct")
async def adm_direct_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token(db)
    if not token:
        await call.message.answer("ابتدا باید یک API Key هتزنر فعال کنید.")
        await call.answer()
        return
    api = HetznerAPI(token)
    try:
        locations = await api.locations()
    except Exception as e:
        await call.message.answer(f"خطا: {e}")
        await call.answer()
        return
    kb_rows = [[InlineKeyboardButton(text=f"{loc['city']}", callback_data=f"adloc_{loc['name']}")] for loc in locations]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")])
    await state.set_state(AdminDirect.location)
    await call.message.edit_text("📍 دیتاسنتر را انتخاب کنید (این سرور رایگان برای شما ساخته می‌شود):", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(AdminDirect.location, F.data.startswith("adloc_"))
async def adm_direct_location(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    location = call.data.replace("adloc_", "")
    await state.update_data(location=location)
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token(db)
    api = HetznerAPI(token)
    types = await api.server_types()
    kb_rows = [
        [InlineKeyboardButton(text=f"{t['name']} | {t['cores']}CPU/{t['memory']}GB/{t['disk']}GB", callback_data=f"adtype_{t['name']}")]
        for t in types[:25]
    ]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")])
    await state.set_state(AdminDirect.type)
    await call.message.edit_text("نوع سرور را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(AdminDirect.type, F.data.startswith("adtype_"))
async def adm_direct_type(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.update_data(server_type=call.data.replace("adtype_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token(db)
    api = HetznerAPI(token)
    images = deduplicate_images(await images_for_plan("hetzner", api, call.data.replace("adtype_", "")))
    kb_rows = [[InlineKeyboardButton(text=img.get("name"), callback_data=f"adimg_{img['id']}")] for img in images[:100]]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")])
    await state.set_state(AdminDirect.image)
    await call.message.edit_text("سیستم‌عامل را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(AdminDirect.image, F.data.startswith("adimg_"))
async def adm_direct_image(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.update_data(image=call.data.replace("adimg_", ""))
    data = await state.get_data()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ ساخت سرور", callback_data="adconfirm")],
            [InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")],
        ]
    )
    await state.set_state(AdminDirect.confirm)
    await call.message.edit_text(
        f"دیتاسنتر: {data['location']}\nنوع: {data['server_type']}\n\nاین سرور بدون کسر از کیف پول ساخته می‌شود. تایید می‌کنید؟",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(AdminDirect.confirm, F.data == "adconfirm")
async def adm_direct_confirm(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    data = await state.get_data()
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token(db)
    api = HetznerAPI(token)
    server_name = f"admin-{secrets.token_hex(3)}"
    try:
        image_ref = await api.resolve_image(data["image"], data["server_type"])
        server, root_password = await api.create_server(
            name=server_name, server_type=data["server_type"], image=image_ref, location=data["location"]
        )
    except Exception as e:
        detail = e.describe() if isinstance(e, HetznerError) else str(e)
        await call.message.answer(f"❌ خطا در ساخت سرور: {html.escape(detail)}")
        await state.clear()
        await call.answer()
        return
    if not root_password:
        root_password = "از طریق دکمه «تغییر پسورد» دریافت کنید"
    ip = extract_server_ip("hetzner", server)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO servers (user_id, hetzner_id, name, ip, location, plan_title, os_image, root_password, status, created_at, expires_at, monthly_price, is_admin_owned, is_protected, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,1,'admin')",
            (
                call.from_user.id, server["id"], server_name, ip, data["location"], "ادمین (رایگان)",
                data["image"], root_password, "running", datetime.utcnow().isoformat(),
                (datetime.utcnow() + timedelta(days=RENEWAL_PERIOD_DAYS)).isoformat(), 0,
            ),
        )
        await db.commit()
    await state.clear()
    await call.message.answer(
        f"✅ سرور ساخته شد!\nنام: {server_name}\nآی‌پی: {ip}\nیوزر: root\nپسورد: {root_password}"
    )
    await call.answer()


# ---------------------------------------------------------------------------
# پنل ادمین: مدیریت چند API Key هتزنر
# ---------------------------------------------------------------------------
async def refund_web_server_wallet(order_id: int, failure_status: str = "payment_failed"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        row=await (await db.execute("SELECT user_id,wallet_used_usd,wallet_refunded,status FROM web_server_orders WHERE id=?",(order_id,))).fetchone()
        if row and row[3] not in ("completed",):
            if row[0] and row[1] and not row[2]:
                await db.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?",(float(row[1]),row[0]))
                await db.execute("INSERT OR IGNORE INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)",(row[0],float(row[1]),"refund",f"web-server-{order_id}-refund",datetime.utcnow().isoformat()))
            status = failure_status if failure_status in ("payment_failed", "creation_failed") else "payment_failed"
            await db.execute("UPDATE web_server_orders SET wallet_refunded=1,status=? WHERE id=?",(status,order_id))
            await finalize_coupon_redemption(db, "server", order_id, False)
        await db.commit()


async def complete_web_server_order(order_id: int, reference: str = ""):
    """Create a paid web server order exactly once and persist it without charging wallet twice."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        row=await (await db.execute("SELECT user_id,provider_type,location,plan_id,plan_title,server_type,image,price_usd,status,gateway_method,wallet_used_usd,gateway_due_usd FROM web_server_orders WHERE id=?",(order_id,))).fetchone()
        if not row or row[8] != "pending_payment":
            await db.commit(); return None
        await db.execute("UPDATE web_server_orders SET status='creating',paid_at=? WHERE id=?",(datetime.utcnow().isoformat(),order_id))
        await finalize_coupon_redemption(db, "server", order_id, True)
        await db.commit()
    user_id,ptype,location,plan_id,plan_title,server_type,image,price,_,gateway_method,wallet_used,gateway_due=row
    server_name=f"srv-{user_id}-{secrets.token_hex(3)}"; order_ref=secrets.token_hex(8); provider_id=None
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            providers=await get_active_providers(db,ptype)
            cur=await db.execute("SELECT markup_percent FROM plans WHERE id=?",(plan_id,)); await cur.fetchone()
        if not providers: raise RuntimeError("هیچ API فعالی برای این سرویس‌دهنده وجود ندارد")
        if ptype=="hetzner":
            created=await create_hetzner_server_with_failover(providers,server_name=server_name,server_type=server_type,image=image,location=location,order_ref=order_ref)
            server,root_password=created["server"],created["root_password"]; provider_id=created["provider_id"]; ptype="hetzner"; image=created.get("image") or image; server_name=server.get("name") or server_name
        else:
            provider_id,token=providers[0]; api=make_provider_api(ptype,token)
            server,root_password=await api.create_server(name=server_name,server_type=server_type,image=image,location=location)
        if not root_password: root_password="از طریق بخش مدیریت سرورها امکان تغییر رمز وجود دارد"
        ip=extract_server_ip(ptype,server); expires=(datetime.utcnow()+timedelta(days=30)).isoformat()
        data={"location":location,"plan_title":plan_title}
        report_amount = 0.0 if user_id in ADMIN_IDS else float(wallet_used or 0) + float(gateway_due or 0)
        payment_label = "رایگان ویژه ادمین" if user_id in ADMIN_IDS else f"{gateway_method} + کیف پول ${float(wallet_used or 0):.2f}"
        server_db_id=await _save_purchase(user_id,server,server_name,ip,root_password,image,data,float(price),expires,ptype,provider_id,charge_wallet=False,order_amount=0.0 if user_id in ADMIN_IDS else float(price),payment_method=payment_label,report_amount=report_amount)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE web_server_orders SET status='completed',server_db_id=? WHERE id=?",(server_db_id,order_id)); await db.commit()
        return server_db_id
    except Exception as exc:
        logger.exception("Paid web server order %s failed during creation",order_id)
        uncertain = bool(getattr(exc, "uncertain", False))
        if ptype != "hetzner":
            error_text = str(exc)
            if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)) or re.search(r"\\((?:500|502|503|504)\\)", error_text):
                uncertain = True
        if uncertain:
            # ممکن است Provider سرور را ساخته باشد ولی پاسخ به ما نرسیده باشد.
            # سفارش را برای تطبیق دستی نگه می‌داریم و سهم کیف پول را خودکار برنمی‌گردانیم.
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE web_server_orders SET status='creation_review' WHERE id=? AND status='creating'",
                    (order_id,),
                )
                await db.commit()
            logger.critical(
                "Paid web server order %s has uncertain provider outcome; wallet refund withheld for manual review: user=%s provider=%s server_name=%s error=%s",
                order_id, user_id, ptype, server_name, str(exc)[:700],
            )
            return None
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE web_server_orders SET status='creation_failed' WHERE id=?",(order_id,)); await db.commit()
        await refund_web_server_wallet(order_id, failure_status="creation_failed")
        logger.critical("Web server order %s paid but creation failed for user %s: %s", order_id, user_id, str(exc)[:500])
        return None


async def get_setting_for_provider_type(provider_type):
    async with aiosqlite.connect(DB_PATH) as db:
        return await get_setting(db, f"priority_provider_{provider_type}")


@router.callback_query(F.data == "adm_token")
async def adm_token_list(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, label, api_token, active, type FROM provider ORDER BY id DESC")
        rows = await cur.fetchall()
    kb_rows = []
    lines = ["🔑 لیست API های سرویس‌دهنده‌های ثبت‌شده:\n"]
    if not rows:
        lines.append("هنوز هیچ API ای اضافه نشده.")
    for r in rows:
        pid, label, token, active, ptype = r
        masked = f"...{token[-4:]}" if token and len(token) > 4 else "----"
        status = "✅ فعال" if active else "⚪️ غیرفعال"
        ptype_label = PROVIDER_LABELS.get(ptype, ptype)
        priority = await get_setting_for_provider_type(ptype)
        is_priority = str(priority or "") == str(pid)
        lines.append(f"#{pid} [{ptype_label}] {label or 'بدون‌نام'} ({masked}) - {status}" + (" | ⭐ اولویت فروش" if is_priority else ""))
        kb_rows.append(
            [
                InlineKeyboardButton(text=f"مدیریت #{pid}", callback_data=f"prov_manage_{pid}"),
                InlineKeyboardButton(text=("غیرفعال‌کردن" if active else "فعال‌سازی")+f" #{pid}", callback_data=f"prov_act_{pid}"),
                InlineKeyboardButton(text=f"⭐ اولویت فروش #{pid}", callback_data=f"prov_priority_{pid}"),
                InlineKeyboardButton(text=f"حذف #{pid}", callback_data=f"prov_del_{pid}"),
            ]
        )
    kb_rows.append([InlineKeyboardButton(text="➕ افزودن API جدید", callback_data="prov_add")])
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")])
    await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(F.data.startswith("prov_manage_"))
async def adm_token_manage(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    try:
        pid = int(call.data.rsplit("_", 1)[1])
    except ValueError:
        await call.answer("شناسه API نامعتبر است", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT label, api_token, active, type FROM provider WHERE id=?", (pid,))
        row = await cur.fetchone()
    if not row:
        await call.answer("API پیدا نشد", show_alert=True)
        return
    label, token, active, ptype = row
    masked = f"...{token[-4:]}" if token and len(token) > 4 else "----"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("غیرفعال‌کردن" if active else "فعال‌سازی"), callback_data=f"prov_act_{pid}")],
        [InlineKeyboardButton(text="حذف این API", callback_data=f"prov_del_{pid}")],
        [InlineKeyboardButton(text="بازگشت به فهرست", callback_data="adm_token")],
    ])
    await call.message.edit_text(f"🔑 مدیریت API #{pid}\nنام: {label or 'بدون‌نام'}\nنوع: {PROVIDER_LABELS.get(ptype, ptype)}\nتوکن: {masked}\nوضعیت: {'فعال' if active else 'غیرفعال'}\n\nحذف API نباید سرورهای مشتریان را حذف کند.", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("prov_act_"))
async def adm_token_activate(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    pid = int(call.data.replace("prov_act_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT active FROM provider WHERE id=?", (pid,))
        row = await cur.fetchone()
        if row:
            await db.execute("UPDATE provider SET active=? WHERE id=?", (0 if row[0] else 1, pid))
            await db.commit()
    await call.answer("وضعیت API تغییر کرد")
    await adm_token_list(call)


@router.callback_query(F.data.startswith("prov_priority_"))
async def adm_token_priority(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("دسترسی ندارید", show_alert=True)
    pid = int(call.data.rsplit("_", 1)[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT type, active FROM provider WHERE id=?", (pid,))
        row = await cur.fetchone()
        if not row:
            return await call.answer("API پیدا نشد", show_alert=True)
        if not row[1]:
            return await call.answer("ابتدا API را فعال کنید؛ اولویت فروش فقط برای API فعال قابل انتخاب است.", show_alert=True)
        await set_setting(db, f"priority_provider_{row[0]}", str(pid))
    await call.answer("⭐ اولویت فروش تنظیم شد")
    await adm_token_list(call)


@router.callback_query(F.data.startswith("prov_del_"))
async def adm_token_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    pid = int(call.data.replace("prov_del_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT type, active FROM provider WHERE id=?", (pid,))
        prow = await cur.fetchone()
        if prow:
            ptype, was_active = prow
            await db.execute("DELETE FROM servers WHERE provider_type=? AND provider_id=? AND user_id IS NULL", (ptype or "hetzner", pid))
            await db.execute("DELETE FROM provider WHERE id=?", (pid,))
            if was_active:
                cur = await db.execute("SELECT id FROM provider WHERE type=? ORDER BY id DESC LIMIT 1", (ptype,))
                fallback = await cur.fetchone()
                if fallback:
                    await db.execute("UPDATE provider SET active=1 WHERE id=?", (fallback[0],))
            await db.commit()
    await call.answer("API حذف شد؛ سرورهای مشتریان حفظ شدند.")
    await adm_token_list(call)


@router.callback_query(F.data == "prov_add")
async def adm_token_add_start(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"provtype_{key}")]
            for key, label in PROVIDER_LABELS.items()
        ]
        + [[InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_token")]]
    )
    await call.message.edit_text("این API متعلق به کدام سرویس‌دهنده است؟", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("provtype_"))
async def adm_token_type_chosen(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    ptype = call.data.replace("provtype_", "")
    await state.update_data(provider_type=ptype)
    await state.set_state(AdminToken.label)
    await call.message.answer(f"یک نام دلخواه برای این اکانت {PROVIDER_LABELS.get(ptype, ptype)} بنویسید (مثلاً: اکانت اصلی):")
    await call.answer()


@router.message(AdminToken.label)
async def adm_token_label(message: Message, state: FSMContext):
    await state.update_data(label=message.text.strip())
    data = await state.get_data()
    ptype = data.get("provider_type", "hetzner")
    await state.set_state(AdminToken.token)
    if ptype == "hetzner":
        await message.answer(
            "🔑 حالا توکن API هتزنر را ارسال کنید.\n"
            "(از پنل Hetzner Cloud > Security > API Tokens با دسترسی Read & Write بسازید)"
        )
    elif ptype == "gcore":
        await message.answer(
            "🔑 برای Gcore باید سه مقدار را به‌این شکل و با : از هم جدا ارسال کنید:\n"
            "PROJECT_ID:REGION_ID:API_KEY\n\n"
            "مثال: 12345:1:abcdEFGH123456\n"
            "این مقادیر را از پنل Gcore Cloud (Cloud > Settings > API Tokens و شناسه پروژه/ریجن) بگیرید."
        )
    else:
        await message.answer(f"🔑 حالا توکن API {PROVIDER_LABELS.get(ptype, ptype)} را ارسال کنید.")


@router.message(AdminToken.token)
async def adm_token_save(message: Message, state: FSMContext):
    data = await state.get_data()
    ptype = data.get("provider_type", "hetzner")
    token = message.text.strip()
    api = make_provider_api(ptype, token)
    try:
        await api.locations()
    except Exception as e:
        await message.answer(f"⚠️ اتصال با این توکن ناموفق بود ({e})؛ دوباره تلاش کنید یا توکن را بررسی کنید.")
        await state.clear()
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM provider WHERE type=? AND active=1", (ptype,))
        active_count = (await cur.fetchone())[0]
        await db.execute(
            "INSERT INTO provider (label, api_token, active, created_at, type) VALUES (?,?,1,?,?)",
            (data.get("label", "بدون‌نام"), token, datetime.utcnow().isoformat(), ptype),
        )
        cur = await db.execute("SELECT last_insert_rowid()")
        new_pid = (await cur.fetchone())[0]
        if not active_count:
            await set_setting(db, f"priority_provider_{ptype}", str(new_pid))
        await db.commit()
    await state.clear()
    await message.answer(f"✅ اتصال جدید ({PROVIDER_LABELS.get(ptype, ptype)}) با موفقیت اضافه و فعال شد.")


# ---------------------------------------------------------------------------
# پنل ادمین: درگاه ارز دیجیتال (OxaPay)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "adm_oxapay")
async def adm_oxapay_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        key = await get_setting(db, "oxapay_api_key")
    status = f"فعال (...{key[-4:]})" if key else "غیرفعال"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ تنظیم / تغییر API Key", callback_data="oxa_set")],
            [InlineKeyboardButton(text="🗑 حذف اتصال", callback_data="oxa_del")],
            [InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")],
        ]
    )
    await call.message.edit_text(
        f"💠 درگاه پرداخت ارز دیجیتال (OxaPay)\n\nوضعیت فعلی: {status}\n\n"
        "با این درگاه، کاربران می‌توانند کیف پول را با ارز دیجیتال (USDT و غیره) و بدون نیاز به تایید دستی شارژ کنند.\n"
        "API Key را از داشبورد OxaPay (بخش Merchant) بگیرید.",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data == "oxa_set")
async def adm_oxapay_set(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminOxapay.waiting)
    await call.message.answer("Merchant API Key مربوط به OxaPay را ارسال کنید:")
    await call.answer()


@router.message(AdminOxapay.waiting)
async def adm_oxapay_save(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        await set_setting(db, "oxapay_api_key", message.text.strip())
    await state.clear()
    await message.answer("✅ درگاه ارز دیجیتال فعال شد.")


@router.callback_query(F.data == "oxa_del")
async def adm_oxapay_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await set_setting(db, "oxapay_api_key", "")
    await call.answer("🗑 حذف شد")
    await adm_oxapay_menu(call)


# ---------------------------------------------------------------------------
# پنل ادمین: درگاه ارز دیجیتال (NOWPayments)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "adm_nowpayments")
async def adm_nowpayments_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        key = await get_setting(db, "nowpayments_api_key")
        pay_currency = await get_setting(db, "nowpayments_pay_currency", NOWPAYMENTS_PAY_CURRENCY)
    status = f"فعال (...{key[-4:]})" if key else "غیرفعال"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ تنظیم / تغییر API Key", callback_data="now_set")],
            [InlineKeyboardButton(text="💱 تغییر ارز پرداخت", callback_data="now_currency")],
            [InlineKeyboardButton(text="📋 لیست ارزهای فعال حساب من", callback_data="now_list_currencies")],
            [InlineKeyboardButton(text="🗑 حذف اتصال", callback_data="now_del")],
            [InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")],
        ]
    )
    await call.message.edit_text(
        f"💠 درگاه پرداخت ارز دیجیتال (NOWPayments)\n\nوضعیت فعلی: {status}\n"
        f"ارز پرداخت فعلی: {pay_currency.upper()}\n\n"
        "کاربران مبلغ را به آدرس تولیدشده واریز می‌کنند و ربات به‌صورت خودکار وضعیت را چک و کیف پول را شارژ می‌کند.\n"
        "API Key را از پنل NOWPayments (بخش Payment Settings) بگیرید.\n\n"
        "⚠️ اگر خطای «400 Bad Request» می‌گیرید، یعنی ارز انتخابی در حساب شما فعال نیست — "
        "از «📋 لیست ارزهای فعال حساب من» ارز درست را پیدا و با «💱 تغییر ارز پرداخت» تنظیم کنید.",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data == "now_currency")
async def adm_nowpayments_currency_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminNowpaymentsCurrency.waiting)
    await call.message.answer(
        "کد ارز پرداخت را وارد کنید (مثلاً usdttrc20 برای تتر-ترون، usdterc20 برای تتر-اتریوم، btc، trx و ...):"
    )
    await call.answer()


@router.message(AdminNowpaymentsCurrency.waiting)
async def adm_nowpayments_currency_save(message: Message, state: FSMContext):
    currency = message.text.strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        await set_setting(db, "nowpayments_pay_currency", currency)
    await state.clear()
    await message.answer(f"✅ ارز پرداخت NOWPayments روی {currency.upper()} تنظیم شد.")


@router.callback_query(F.data == "now_list_currencies")
async def adm_nowpayments_list_currencies(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        key = await get_setting(db, "nowpayments_api_key")
    if not key:
        await call.answer("ابتدا API Key را تنظیم کنید.", show_alert=True)
        return
    try:
        currencies = await nowpayments_available_currencies(key)
    except Exception as e:
        await call.message.answer(f"❌ خطا در دریافت لیست ارزها: {e}")
        await call.answer()
        return
    if not currencies:
        await call.message.answer(
            "هیچ ارزی در حساب شما فعال نیست. وارد داشبورد NOWPayments شوید → Store settings → "
            "Accepted currencies و حداقل یک ارز (مثلاً usdttrc20) را فعال کنید."
        )
    else:
        await call.message.answer("ارزهای فعال حساب شما:\n" + ", ".join(c.upper() for c in currencies))
    await call.answer()


@router.callback_query(F.data == "now_set")
async def adm_nowpayments_set(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminNowpayments.waiting)
    await call.message.answer("API Key مربوط به NOWPayments را ارسال کنید:")
    await call.answer()


@router.message(AdminNowpayments.waiting)
async def adm_nowpayments_save(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        await set_setting(db, "nowpayments_api_key", message.text.strip())
    await state.clear()
    await message.answer("✅ درگاه ارز دیجیتال NOWPayments فعال شد.")


@router.callback_query(F.data == "now_del")
async def adm_nowpayments_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await set_setting(db, "nowpayments_api_key", "")
    await call.answer("🗑 حذف شد")
    await adm_nowpayments_menu(call)



# ---------------------------------------------------------------------------
# پنل ادمین: آدرس واریز دستی (کارت یا ولت)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "adm_manual_addr")
async def adm_manual_addr_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        addr = await get_setting(db, "manual_address", "تنظیم نشده")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ تنظیم / تغییر آدرس", callback_data="addr_set")],
            [InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")],
        ]
    )
    await call.message.edit_text(f"🏦 آدرس واریز دستی فعلی:\n\n{addr}", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "addr_set")
async def adm_manual_addr_set(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminManualAddress.waiting)
    await call.message.answer("شماره کارت یا آدرس ولت ارز دیجیتال را ارسال کنید:")
    await call.answer()


@router.message(AdminManualAddress.waiting)
async def adm_manual_addr_save(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        await set_setting(db, "manual_address", message.text.strip())
    await state.clear()
    await message.answer("✅ آدرس واریز دستی بروزرسانی شد.")


# ---------------------------------------------------------------------------
# پنل ادمین: مدیریت پلن‌ها (بر اساس درصد سود، بدون قیمت ثابت)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "adm_plans")
async def adm_plans(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, title, server_type, markup_percent, active, location, provider_type FROM plans ORDER BY id")
        plans = await cur.fetchall()
        token = await get_active_token(db)  # هتزنر - برای فیلتر موجودی
        enabled_locations = None
        stock_map = None
        if token:
            api = HetznerAPI(token)
            disabled = await get_disabled_locations(db)
            try:
                all_locs = await api.locations()
                enabled_locations = {l["name"] for l in all_locs if l["name"] not in disabled}
            except Exception:
                enabled_locations = set()
            stock_map = await build_stock_map(api)
    visible, hidden_count = [], 0
    for p in plans:
        ptype = p[6] or "hetzner"
        if ptype != "hetzner" or plan_has_stock(stock_map, p[5], p[2], enabled_locations or set()):
            visible.append(p)
        else:
            hidden_count += 1
    lines = ["🧩 پلن‌های فعلی (قیمت‌ها همیشه زنده از سرویس‌دهنده + درصد سود محاسبه می‌شود):\n"]
    for p in visible:
        loc_txt = p[5] if p[5] else "همه لوکیشن‌ها"
        ptype_label = PROVIDER_LABELS.get(p[6] or "hetzner", p[6])
        lines.append(f"• #{p[0]} [{ptype_label}] {p[1]} ({p[2]}) | سود: %{p[3]:g} | {loc_txt} | {'✅' if p[4] else '❌'}")
    if hidden_count:
        lines.append(f"\n⚠️ {hidden_count} پلن هتزنری دیگر به‌دلیل نبود موجودی فعلی در این لیست نمایش داده نشد.")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزودن پلن جدید", callback_data="adm_plan_add")],
            [InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")],
        ]
    )
    await call.message.edit_text("\n".join(lines) if visible else "پلنی با موجودی فعلی وجود ندارد.", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "adm_plan_add")
async def adm_plan_add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        enabled = await get_enabled_provider_types(db)
    if not enabled:
        await call.message.answer("ابتدا باید حداقل یک API سرویس‌دهنده فعال کنید.")
        await call.answer()
        return
    kb_rows = [[InlineKeyboardButton(text=PROVIDER_LABELS.get(p, p), callback_data=f"adplanprov_{p}")] for p in enabled]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")])
    await call.message.edit_text("این پلن برای کدام سرویس‌دهنده است؟", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(F.data.startswith("adplanprov_"))
async def adm_plan_provider_chosen(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    ptype = call.data.replace("adplanprov_", "")
    await state.update_data(provider_type=ptype)
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token_for(db, ptype)
    if not token:
        await call.message.answer("اتصال این سرویس‌دهنده در حال حاضر فعال نیست.")
        await call.answer()
        return
    api = make_provider_api(ptype, token)
    try:
        types = await api.server_types()
    except Exception as e:
        await call.message.answer(f"خطا در دریافت لیست پلن‌ها: {e}")
        await call.answer()
        return
    kb_rows = [
        [
            InlineKeyboardButton(
                text=f"{t['name']} | {t['cores']} CPU / {t['memory']}GB RAM / {t['disk']}GB",
                callback_data=f"adpt_{t['name'] if ptype == 'hetzner' else t['id']}_{t['cores']}_{t['memory']}_{t['disk']}",
            )
        ]
        for t in types[:25]
    ]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")])
    await state.set_state(AdminAddPlan.choosing_type)
    await call.message.edit_text(
        f"یک نوع سرور {PROVIDER_LABELS.get(ptype, ptype)} انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )
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
    await state.set_state(AdminAddPlan.markup)
    await message.answer("درصد سود روی قیمت پایه را وارد کنید (مثلاً 20 برای ۲۰٪):")


@router.message(AdminAddPlan.markup)
async def adm_plan_markup(message: Message, state: FSMContext):
    try:
        markup = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("لطفاً یک عدد معتبر وارد کنید (مثلاً 20).")
        return
    await state.update_data(markup=markup)
    data = await state.get_data()
    ptype = data.get("provider_type", "hetzner")
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token_for(db, ptype)
    kb_rows = [[InlineKeyboardButton(text="🌍 همه لوکیشن‌ها", callback_data="adplanloc_ALL")]]
    if token:
        api = make_provider_api(ptype, token)
        try:
            locations = await api.locations()
            for loc in locations:
                kb_rows.append(
                    [InlineKeyboardButton(text=f"📍 فقط {loc['city']}", callback_data=f"adplanloc_{loc['name']}")]
                )
        except Exception:
            pass
    await state.set_state(AdminAddPlan.location)
    await message.answer(
        "این پلن برای کدام لوکیشن(ها) نمایش داده شود؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


@router.callback_query(AdminAddPlan.location, F.data.startswith("adplanloc_"))
async def adm_plan_location(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    loc = call.data.replace("adplanloc_", "")
    loc_value = None if loc == "ALL" else loc
    data = await state.get_data()
    ptype = data.get("provider_type", "hetzner")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO plans (server_type, title, cpu, ram, disk, markup_percent, active, location, provider_type) VALUES (?,?,?,?,?,?,1,?,?)",
            (data["server_type"], data["title"], data["cpu"], data["ram"], data["disk"], data["markup"], loc_value, ptype),
        )
        await db.commit()
    await state.clear()
    await call.message.edit_text("✅ پلن جدید اضافه شد. قیمت نهایی همیشه زنده محاسبه می‌شود.", reply_markup=back_inline("adm_plans"))
    await call.answer()


# ---------------------------------------------------------------------------
# پنل ادمین: مدیریت لوکیشن‌ها (فعال/غیرفعال کردن هر دیتاسنتر برای فروش)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "adm_locations")
async def adm_locations(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token(db)
        disabled = await get_disabled_locations(db)
    if not token:
        await call.message.edit_text("ابتدا باید یک API Key هتزنر فعال کنید.", reply_markup=back_inline())
        await call.answer()
        return
    api = HetznerAPI(token)
    try:
        locations = await api.locations()
    except Exception as e:
        await call.message.edit_text(f"خطا: {e}", reply_markup=back_inline())
        await call.answer()
        return
    kb_rows = []
    for loc in locations:
        name = loc["name"]
        status = "❌ غیرفعال" if name in disabled else "✅ فعال"
        kb_rows.append(
            [InlineKeyboardButton(text=f"{loc['city']} ({loc['country']}) - {status}", callback_data=f"loct_{name}")]
        )
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")])
    await call.message.edit_text(
        "📍 لوکیشن‌ها را برای فعال/غیرفعال کردن انتخاب کنید:\n(لوکیشن غیرفعال به کاربران برای خرید نمایش داده نمی‌شود)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    await call.answer()


@router.callback_query(F.data.startswith("loct_"))
async def adm_location_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    name = call.data.replace("loct_", "")
    async with aiosqlite.connect(DB_PATH) as db:
        await toggle_location(db, name)
    await call.answer("✅ بروزرسانی شد")
    await adm_locations(call)


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
            await message.bot.send_message(uid, message.text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 خرید سرور", url="https://t.me/Cloud_center_bot")]]))
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"✅ ارسال شد به {sent} کاربر ({failed} ناموفق).")


@router.callback_query(F.data.startswith("dynbtn_"))
async def dynamic_button_callback(call: CallbackQuery, state: FSMContext):
    try:
        button_id = int(call.data.split("_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("دکمه نامعتبر است.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT action,payload FROM dynamic_buttons WHERE id=? AND active=1", (button_id,)
        )).fetchone()
    if not row:
        await call.answer("این دکمه غیرفعال یا حذف شده است.", show_alert=True)
        return
    await call.answer()
    await state.clear()
    await run_dynamic_button_action(str(row[0]), str(row[1] or ""), call.from_user.id, call.message, state)


@router.message(F.text)
async def dynamic_main_menu_button(message: Message, state: FSMContext):
    label = str(message.text or "").strip()
    match = next((x for x in DYNAMIC_BUTTON_CACHE.get("main", []) if x["label"] == label), None)
    if not match:
        return
    await run_dynamic_button_action(match["action"], match["payload"], message.from_user.id, message, state)


# ---------------------------------------------------------------------------
# نگهداری خودکار: هر ۶ ساعت چک ترافیک مصرفی و مهلت سرویس‌ها
# ---------------------------------------------------------------------------
async def check_traffic_usage(bot: Bot):
    """سقف فروشگاه دقیقاً ۲۰ ترابایت است؛ ۱۹.۵ ترابایت فقط هشدار می‌دهد."""
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT id,user_id,hetzner_id,name,traffic_warning_sent,provider_type FROM servers "
            "WHERE hetzner_id IS NOT NULL AND user_id IS NOT NULL AND COALESCE(is_protected,0)=0 AND COALESCE(admin_reactivated,0)=0 AND deletion_pending_reason IS NULL"
        )).fetchall()
    for sid, owner_id, remote_id, name, warning_sent, ptype in rows:
        async with aiosqlite.connect(DB_PATH) as db:
            token = await get_token_for_server(db, sid, ptype or "hetzner")
        if not token:
            continue
        api = make_provider_api(ptype or "hetzner", token)
        try:
            live = await api.get_server(remote_id)
            incoming = int(live.get("ingoing_traffic") or 0)
            outgoing = int(live.get("outgoing_traffic") or 0)
            total = incoming + outgoing
        except Exception as exc:
            logger.warning("Traffic sync failed for server %s: %s", sid, exc)
            continue
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE servers SET traffic_in_bytes=?,traffic_out_bytes=?,traffic_included_bytes=?,traffic_synced_at=? WHERE id=?", (incoming,outgoing,TRAFFIC_INCLUDED_BYTES,now,sid))
            if total >= TRAFFIC_SOFT_LIMIT_BYTES and not warning_sent:
                await db.execute("UPDATE servers SET traffic_warning_sent=1 WHERE id=?", (sid,))
            await db.commit()
        if total >= TRAFFIC_SOFT_LIMIT_BYTES and not warning_sent:
            msg = f"⚠️ هشدار مصرف سرور «{name}»: {format_bytes(total)} از سقف ۲۰ ترابایت مصرف شده است. با رسیدن به ۲۰ ترابایت، سرور خودکار خاموش و برای تمدید/حذف به ادمین ارجاع می‌شود."
            try: await bot.send_message(owner_id, msg)
            except Exception: pass
            await notify_admins(bot, f"{msg}\nمالک: {owner_id} | شناسه سرور: {sid}")
        if total >= TRAFFIC_INCLUDED_BYTES:
            await request_server_deletion(bot, sid, f"سقف قطعی مصرف ۲۰ ترابایت تکمیل شد ({format_bytes(total)} / ۲۰ ترابایت)")


async def check_expirations(bot: Bot):
    now = datetime.utcnow()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT id,user_id,name,expires_at,COALESCE(expiry_reminder_sent,0),deletion_pending_reason "
            "FROM servers WHERE expires_at IS NOT NULL AND user_id IS NOT NULL AND COALESCE(is_protected,0)=0 AND COALESCE(admin_reactivated,0)=0"
        )).fetchall()
    for sid, owner_id, name, expires_at, reminder_sent, pending in rows:
        if pending:
            continue
        try: exp = datetime.fromisoformat(expires_at)
        except Exception: continue
        if exp <= now:
            await request_server_deletion(bot, sid, "پایان مهلت اعتبار سرور؛ برای ادامه سرویس تمدید کنید یا درخواست حذف را ثبت کنید")
            continue
        if exp - now <= timedelta(hours=EXPIRY_REMINDER_HOURS_BEFORE) and not reminder_sent:
            remain = exp - now
            msg = (f"⏳ اخطار پایان مهلت سرور «{name}»: {remain.days} روز و {remain.seconds // 3600} ساعت باقی مانده است.\n"
                   "برای جلوگیری از خاموشی، تمدید کنید.")
            try: await bot.send_message(owner_id, msg)
            except Exception: pass
            await notify_admins(bot, f"{msg}\nمالک: {owner_id} | شناسه سرور: {sid}")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE servers SET expiry_reminder_sent=1 WHERE id=?", (sid,))
                await db.commit()


async def cleanup_old_primary_ips():
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, provider_id, provider_type, ip_id FROM pending_ip_cleanup WHERE cleanup_at<=?", (now,))
        rows = await cur.fetchall()
    for queue_id, provider_id, ptype, ip_id in rows:
        ptype = ptype or "hetzner"
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT api_token FROM provider WHERE id=? AND type=?", (provider_id, ptype))
            row = await cur.fetchone()
        if not row:
            continue
        try:
            if ptype == "hetzner":
                await HetznerAPI(row[0]).delete_primary_ip(ip_id)
            else:
                continue
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM pending_ip_cleanup WHERE id=?", (queue_id,))
                await db.execute(
                    "UPDATE servers SET previous_ip_id=NULL, previous_ip_address=NULL, previous_ip_cleanup_at=NULL WHERE previous_ip_id=?",
                    (ip_id,),
                )
                await db.commit()
        except Exception as exc:
            logger.warning("Old IP cleanup failed for queued IP %s: %s", ip_id, exc)


async def ip_cleanup_poller():
    while True:
        try:
            await cleanup_old_primary_ips()
        except Exception as exc:
            logger.warning("old IP cleanup poll error: %s", exc)
        await asyncio.sleep(60)


async def maintenance_poller(bot: Bot):
    """بررسی فوری در شروع و سپس هر ۱۵ دقیقه برای ترافیک و مهلت همه سرورها."""
    while True:
        try:
            await check_traffic_usage(bot)
        except Exception as e:
            logger.warning(f"traffic check error: {e}")
        try:
            await check_expirations(bot)
        except Exception as e:
            logger.warning(f"expiry check error: {e}")
        try:
            await cleanup_old_primary_ips()
        except Exception as e:
            logger.warning(f"old IP cleanup error: {e}")
        await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# اجرا و بازیابی خودکار وظایف پس‌زمینه
# ---------------------------------------------------------------------------
async def supervise_background_worker(name, worker_factory):
    """اگر یکی از کارهای دوره‌ای با خطای پیش‌بینی‌نشده متوقف شد، آن را دوباره اجرا کن."""
    delay = 5
    while True:
        try:
            logger.info("Background worker starting: %s", name)
            await worker_factory()
            logger.warning("Background worker exited unexpectedly: %s", name)
            delay = 5
        except asyncio.CancelledError:
            logger.info("Background worker cancelled: %s", name)
            raise
        except Exception:
            logger.exception("Background worker crashed: %s", name)
            delay = min(max(delay * 2, 5), 60)
        await asyncio.sleep(delay)


async def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN تنظیم نشده است. فایل .env را بررسی کنید.")
    await init_db()
    await refresh_dynamic_buttons_cache()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    router.message.middleware(MembershipMiddleware())
    router.callback_query.middleware(MembershipMiddleware())

    workers = [
        asyncio.create_task(supervise_background_worker("automatic_backup", lambda: automatic_backup_poller(bot))),
        asyncio.create_task(supervise_background_worker("payment_status", lambda: oxapay_poller(bot))),
        asyncio.create_task(supervise_background_worker("server_maintenance", lambda: maintenance_poller(bot))),
        asyncio.create_task(supervise_background_worker("ip_cleanup", ip_cleanup_poller)),
        asyncio.create_task(supervise_background_worker("dynamic_buttons", dynamic_buttons_poller)),
    ]
    delay = 5
    try:
        while True:
            try:
                logger.info("Telegram polling starting.")
                await dp.start_polling(bot, handle_signals=False)
                logger.error("Telegram polling stopped unexpectedly; retrying.")
                delay = 5
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram polling crashed; retrying.")
                delay = min(max(delay * 2, 5), 60)
            await asyncio.sleep(delay)
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
