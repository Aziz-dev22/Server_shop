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
import html
import logging
import os
import secrets
from datetime import datetime, timedelta

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
HETZNER_API = "https://api.hetzner.cloud/v1"
OXAPAY_API = "https://api.oxapay.com/v1"
NOWPAYMENTS_API = "https://api.nowpayments.io/v1"
NOWPAYMENTS_PAY_CURRENCY = "usdttrc20"  # ارز پرداختی ثابت: تتر روی شبکه ترون (کارمزد کم و پرکاربرد)

IP_CHANGE_FREE_COUNT = 2       # تعداد دفعات رایگان تغییر IP برای هر سرور
IP_CHANGE_PRICE = 0.5          # هزینه هر تغییر IP بعد از دفعات رایگان (دلار)
RENEWAL_PERIOD_DAYS = 30       # طول هر دوره تمدید سرویس
TRAFFIC_SOFT_LIMIT_BYTES = int(19.5 * 1_000_000_000_000)  # ۱۹.۵ ترابایت -> خاموش‌سازی هشداری
EXPIRY_REMINDER_HOURS_BEFORE = 48   # چند ساعت قبل از پایان مهلت، یادآوری تمدید ارسال شود
EXPIRY_GRACE_HOURS = 2               # چند ساعت بعد از پایان مهلت، قبل از حذف کامل سرور صبر شود
MAINTENANCE_INTERVAL_SECONDS = 6 * 3600  # فاصله چک ترافیک/مهلت (هر ۶ ساعت)
DEFAULT_EUR_USD_RATE = 1.08  # فقط در صورتی که دریافت نرخ زنده ناموفق باشد

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
"""


async def _safe_add_column(db, table, coldef):
    try:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
    except Exception:
        pass


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # سازگاری با نصب‌های قبلی (اضافه کردن ستون‌های جدید در صورت نبود)
        await _safe_add_column(db, "provider", "label TEXT")
        await _safe_add_column(db, "provider", "active INTEGER DEFAULT 0")
        await _safe_add_column(db, "plans", "markup_percent REAL DEFAULT 20")
        await _safe_add_column(db, "wallet_requests", "method TEXT DEFAULT 'manual'")
        await _safe_add_column(db, "wallet_requests", "track_id TEXT")
        await _safe_add_column(db, "plans", "location TEXT")
        await _safe_add_column(db, "servers", "provider_id INTEGER")
        await _safe_add_column(db, "servers", "server_type TEXT")
        await _safe_add_column(db, "servers", "markup_percent REAL")
        await _safe_add_column(db, "servers", "expires_at TEXT")
        await _safe_add_column(db, "servers", "ip_change_count INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "traffic_powered_off INTEGER DEFAULT 0")
        await _safe_add_column(db, "servers", "expired_powered_off_at TEXT")
        await _safe_add_column(db, "servers", "monthly_price REAL")
        await _safe_add_column(db, "users", "is_banned INTEGER DEFAULT 0")
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


async def get_active_token(db):
    cur = await db.execute("SELECT api_token FROM provider WHERE active=1 LIMIT 1")
    row = await cur.fetchone()
    return row[0] if row else None


async def get_active_provider(db):
    cur = await db.execute("SELECT id, api_token FROM provider WHERE active=1 LIMIT 1")
    row = await cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


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

    async def pricing(self):
        data = await self._get("/pricing")
        return data.get("pricing", {})

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

    async def create_server(self, name, server_type, image, location):
        payload = {"name": name, "server_type": server_type, "image": image, "location": location}
        data = await self._post("/servers", json=payload)
        return data["server"], data.get("root_password")

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

    async def unassign_primary_ip(self, primary_ip_id):
        return await self._post(f"/primary_ips/{primary_ip_id}/actions/unassign")

    async def delete_primary_ip(self, primary_ip_id):
        return await self._delete(f"/primary_ips/{primary_ip_id}")

    async def create_primary_ip(self, name, ip_type, datacenter, assignee_id):
        payload = {
            "type": ip_type, "name": name, "datacenter": datacenter,
            "assignee_type": "server", "assignee_id": assignee_id,
        }
        data = await self._post("/primary_ips", json=payload)
        return data["primary_ip"]


async def change_server_ip(api: "HetznerAPI", hetzner_id: int) -> str:
    """IP اصلی سرور را عوض می‌کند: IP قدیم آزاد و حذف می‌شود و یک IP جدید ساخته و متصل می‌شود."""
    server = await api.get_server(hetzner_id)
    old_ip_info = server.get("public_net", {}).get("ipv4")
    datacenter = server.get("datacenter", {}).get("name")
    if not old_ip_info or not datacenter:
        raise RuntimeError("این سرور IPv4 اصلی قابل تغییر ندارد.")
    old_primary_id = old_ip_info["id"]
    await api.unassign_primary_ip(old_primary_id)
    await api.delete_primary_ip(old_primary_id)
    new_name = f"ip-{hetzner_id}-{secrets.token_hex(2)}"
    new_ip = await api.create_primary_ip(new_name, "ipv4", datacenter, hetzner_id)
    return new_ip["ip"]


async def available_types_for_location(api: "HetznerAPI", location_name: str):
    """مجموعه نام سرورتایپ‌هایی که هم‌اکنون در این دیتاسنتر موجودی برای فروش دارند."""
    try:
        types = await api.server_types()
        id_to_name = {t["id"]: t["name"] for t in types}
        dcs = await api.datacenters()
    except Exception:
        return None  # نامشخص -> فیلتر انجام نشود
    available_ids = set()
    for dc in dcs:
        if dc.get("location", {}).get("name") == location_name:
            available_ids.update(dc.get("server_types", {}).get("available", []))
    return {id_to_name[i] for i in available_ids if i in id_to_name}


async def build_stock_map(api: "HetznerAPI"):
    """یک‌بار همه دیتاسنترها را می‌خواند و map از نام‌لوکیشن -> مجموعه سرورتایپ‌های موجود می‌سازد."""
    try:
        types = await api.server_types()
        id_to_name = {t["id"]: t["name"] for t in types}
        dcs = await api.datacenters()
    except Exception:
        return None
    stock_map = {}
    for dc in dcs:
        loc = dc.get("location", {}).get("name")
        if not loc:
            continue
        names = {id_to_name[i] for i in dc.get("server_types", {}).get("available", []) if i in id_to_name}
        stock_map.setdefault(loc, set()).update(names)
    return stock_map


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


# ---------------------------------------------------------------------------
# درگاه پرداخت ارز دیجیتال (OxaPay)
# ---------------------------------------------------------------------------
async def oxapay_create_invoice(api_key: str, amount_usd: float, order_id: str):
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{OXAPAY_API}/payment/invoice",
            headers={"merchant_api_key": api_key, "Content-Type": "application/json"},
            json={
                "amount": amount_usd,
                "currency": "USD",
                "lifetime": 60,
                "order_id": order_id,
                "description": "شارژ کیف پول",
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
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE wallet_requests SET status='approved' WHERE id=?", (req_id,))
                        await db.execute("UPDATE users SET balance = balance + ? WHERE telegram_id=?", (amount, user_id))
                        await db.commit()
                    try:
                        await bot.send_message(
                            user_id, f"✅ پرداخت ارزی شما تایید شد و مبلغ {fmt_price(amount)} به کیف پول اضافه شد."
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"crypto poller error: {e}")


# ---------------------------------------------------------------------------
# درگاه پرداخت ارز دیجیتال (NOWPayments)
# ---------------------------------------------------------------------------
async def nowpayments_create_payment(api_key: str, amount_usd: float, order_id: str, pay_currency: str = None):
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
                "order_description": "شارژ کیف پول",
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
def main_menu_kb(admin: bool):
    rows = [
        [KeyboardButton(text="🛒 خرید سرور"), KeyboardButton(text="🖥 سرورهای من")],
        [KeyboardButton(text="📦 سفارش‌های من"), KeyboardButton(text="💰 کیف پول")],
        [KeyboardButton(text="👤 پروفایل"), KeyboardButton(text="🆘 پشتیبانی")],
    ]
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
    label = State()
    token = State()


class AdminAddPlan(StatesGroup):
    choosing_type = State()
    title = State()
    markup = State()
    location = State()


class AdminUserBalance(StatesGroup):
    amount = State()


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


class SupportTicket(StatesGroup):
    waiting = State()


class AdminDirect(StatesGroup):
    location = State()
    type = State()
    image = State()
    confirm = State()


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
async def adm_back(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
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
            inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت به منوی اصلی", callback_data="back_main")]]
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
        inline_keyboard=[
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
# خرید سرور (قیمت‌ها خودکار و زنده از Hetzner + درصد سود ادمین)
# ---------------------------------------------------------------------------
@router.message(F.text == "🛒 خرید سرور")
async def buy_start(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token(db)
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
    async with aiosqlite.connect(DB_PATH) as db:
        disabled = await get_disabled_locations(db)
    locations = [loc for loc in locations if loc["name"] not in disabled]
    if not locations:
        await message.answer("در حال حاضر هیچ دیتاسنتری برای فروش فعال نیست.")
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
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token(db)
        cur = await db.execute(
            "SELECT id, title, cpu, ram, disk, server_type, markup_percent FROM plans "
            "WHERE active=1 AND (location IS NULL OR location='' OR location=?)",
            (location,),
        )
        plans = await cur.fetchall()
        if not plans:
            await call.message.answer("در حال حاضر پلنی برای این دیتاسنتر تعریف نشده است.")
            await call.answer()
            return
        api = HetznerAPI(token)
        try:
            pricing = await api.pricing()
        except Exception as e:
            await call.message.answer(f"خطا در دریافت قیمت‌ها از Hetzner: {e}")
            await call.answer()
            return
        # فقط پلن‌هایی که هم‌اکنون در این دیتاسنتر موجودی سخت‌افزاری دارند نمایش داده شوند
        stock = await available_types_for_location(api, location)
        kb_rows = []
        for p in plans:
            plan_id, title, cpu, ram, disk, server_type, markup = p
            if stock is not None and server_type not in stock:
                continue
            price = await compute_price_usd(db, pricing, server_type, location, markup)
            if price is None:
                continue
            kb_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{title} | {cpu} / {ram} / {disk} - {fmt_price(price)}/ماه",
                        callback_data=f"plan_{plan_id}",
                    )
                ]
            )
    if not kb_rows:
        await call.message.answer("پلنی برای این دیتاسنتر در دسترس نیست.")
        await call.answer()
        return
    await state.update_data(location=location, pricing=pricing)
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")])
    await state.set_state(BuyFlow.plan)
    await call.message.edit_text("📦 یک پلن انتخاب کنید (قیمت‌ها ماهانه و زنده هستند):", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(BuyFlow.plan, F.data.startswith("plan_"))
async def buy_plan(call: CallbackQuery, state: FSMContext):
    plan_id = int(call.data.replace("plan_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, server_type, title, markup_percent FROM plans WHERE id=?", (plan_id,))
        plan = await cur.fetchone()
        token = await get_active_token(db)
    if not plan or not token:
        await call.answer("خطا در دریافت اطلاعات پلن", show_alert=True)
        return
    await state.update_data(plan_id=plan[0], server_type=plan[1], plan_title=plan[2], markup=plan[3])
    api = HetznerAPI(token)
    try:
        images = await api.images()
    except Exception as e:
        await call.message.answer(f"خطا در دریافت سیستم‌عامل‌ها: {e}")
        await call.answer()
        return
    kb_rows = [[InlineKeyboardButton(text=f"{img.get('name')}", callback_data=f"img_{img['id']}")] for img in images[:20]]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")])
    await state.set_state(BuyFlow.image)
    await call.message.edit_text("💿 سیستم‌عامل را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(BuyFlow.image, F.data.startswith("img_"))
async def buy_image(call: CallbackQuery, state: FSMContext):
    image_id = call.data.replace("img_", "")
    await state.update_data(image=image_id)
    data = await state.get_data()
    async with aiosqlite.connect(DB_PATH) as db:
        price = await compute_price_usd(db, data["pricing"], data["server_type"], data["location"], data["markup"])
    if price is None:
        await call.message.answer("متاسفانه قیمت این پلن در حال حاضر در دسترس نیست.")
        await call.answer()
        return
    await state.update_data(price=price)
    text = (
        "🧾 خلاصه سفارش:\n\n"
        f"دیتاسنتر: {data['location']}\n"
        f"پلن: {data['plan_title']}\n"
        f"قیمت ماهانه: {fmt_price(price)}\n\n"
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
        # محاسبه قیمت نهایی دوباره برای دقت بیشتر لحظه خرید
        price = await compute_price_usd(db, data["pricing"], data["server_type"], data["location"], data["markup"])
        if price is None:
            price = data.get("price")
        user = await get_user(db, user_id)
        if not user or user[3] < price:
            await call.message.edit_text("❌ موجودی کیف پول شما کافی نیست. ابتدا کیف پول را شارژ کنید.")
            await state.clear()
            await call.answer()
            return
        token = await get_active_token(db)

    await call.message.edit_text("⏳ در حال ساخت سرور شما... لطفاً چند لحظه صبر کنید.")
    api = HetznerAPI(token)
    server_name = f"srv-{user_id}-{secrets.token_hex(2)}"
    try:
        server, root_password = await api.create_server(
            name=server_name, server_type=data["server_type"], image=data["image"], location=data["location"]
        )
    except Exception as e:
        await call.message.answer(f"❌ خطا در ساخت سرور: {e}")
        await state.clear()
        await call.answer()
        return

    if not root_password:
        root_password = "از طریق دکمه «تغییر پسورد» در بخش سرورهای من دریافت کنید"

    ip = server.get("public_net", {}).get("ipv4", {}).get("ip", "در حال تخصیص")
    expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE telegram_id=?", (price, user_id))
        await db.execute(
            "INSERT INTO servers (user_id, hetzner_id, name, ip, location, plan_title, os_image, root_password, status, created_at, expires_at, monthly_price) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id, server["id"], server_name, ip, data["location"], data["plan_title"],
                data["image"], root_password, "running", datetime.utcnow().isoformat(), expires_at, price,
            ),
        )
        cur = await db.execute("SELECT last_insert_rowid()")
        server_db_id = (await cur.fetchone())[0]
        await db.execute(
            "INSERT INTO orders (user_id, server_id, plan_title, location, price, status, created_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, server_db_id, data["plan_title"], data["location"], price, "completed", datetime.utcnow().isoformat()),
        )
        await db.commit()

    await state.clear()
    await call.message.answer(
        "✅ سرور شما با موفقیت ساخته شد!\n\n"
        f"نام: {server_name}\n"
        f"آی‌پی: {ip}\n"
        f"یوزر: root\n"
        f"پسورد: {root_password}\n"
        f"مبلغ کسر شده: {fmt_price(price)}\n\n"
        "برای مدیریت سرور به بخش «سرورهای من» بروید."
    )
    await call.answer()


# ---------------------------------------------------------------------------
# سرورهای من (کاربر) - و پایه مشترک برای پنل ادمین
# ---------------------------------------------------------------------------
def server_actions_kb(server_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="▶️ روشن", callback_data=f"sv_on_{server_id}"),
                InlineKeyboardButton(text="⏹ خاموش", callback_data=f"sv_off_{server_id}"),
                InlineKeyboardButton(text="🔄 ریبوت", callback_data=f"sv_reboot_{server_id}"),
            ],
            [
                InlineKeyboardButton(text="🔑 تغییر پسورد", callback_data=f"sv_pass_{server_id}"),
                InlineKeyboardButton(text="🛠 نصب مجدد (Rebuild)", callback_data=f"sv_rebuild_{server_id}"),
            ],
            [
                InlineKeyboardButton(text="🌐 تغییر IP", callback_data=f"sv_ipchange_{server_id}"),
                InlineKeyboardButton(text="⏳ تمدید سرویس", callback_data=f"sv_renew_{server_id}"),
            ],
            [
                InlineKeyboardButton(text="🗑 حذف", callback_data=f"sv_del_{server_id}"),
            ],
        ]
    )


@router.message(F.text == "🖥 سرورهای من")
async def my_servers(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, name, ip, location, plan_title, status FROM servers WHERE user_id=? ORDER BY id DESC",
            (message.from_user.id,),
        )
        servers = await cur.fetchall()
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت به منوی اصلی", callback_data="back_main")]]
    )
    if not servers:
        await message.answer("شما هنوز سروری خریداری نکرده‌اید.", reply_markup=back_kb)
        return
    for s in servers:
        text = f"🖥 {s[1]}\nآی‌پی: {s[2]}\nدیتاسنتر: {s[3]}\nپلن: {s[4]}\nوضعیت: {s[5]}"
        await message.answer(text, reply_markup=server_actions_kb(s[0]))
    await message.answer("برای بازگشت:", reply_markup=back_kb)


@router.callback_query(F.data.startswith("sv_"))
async def server_action(call: CallbackQuery):
    _, action, server_db_id = call.data.split("_")
    server_db_id = int(server_db_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, hetzner_id, name FROM servers WHERE id=?", (server_db_id,))
        row = await cur.fetchone()
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return
        token = await get_active_token(db)
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
            safe_pass = html.escape(new_pass) if new_pass else "از طریق کنسول Hetzner بررسی کنید"
            await call.message.answer(
                f"🔑 پسورد جدید سرور {html.escape(row[2])}:\n<code>{safe_pass}</code>", parse_mode=ParseMode.HTML
            )
            await call.answer()
        elif action == "rebuild":
            images = await api.images()
            kb_rows = [
                [InlineKeyboardButton(text=img.get("name"), callback_data=f"svrbc_{server_db_id}_{img['id']}")]
                for img in images[:20]
            ]
            kb_rows.append([InlineKeyboardButton(text="🔙 انصراف", callback_data="back_main")])
            await call.message.answer(
                f"⚠️ توجه: نصب مجدد (Rebuild) تمام اطلاعات سرور «{row[2]}» را پاک می‌کند.\n"
                "سیستم‌عامل جدید را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
            )
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


@router.callback_query(F.data.startswith("svrbc_"))
async def server_rebuild_confirm(call: CallbackQuery):
    _, server_db_id, image_id = call.data.split("_")
    server_db_id = int(server_db_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, hetzner_id, name FROM servers WHERE id=?", (server_db_id,))
        row = await cur.fetchone()
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return
        token = await get_active_token(db)
    api = HetznerAPI(token)
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
    safe_pass = html.escape(new_pass) if new_pass else "از طریق کنسول Hetzner بررسی کنید"
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
    server_db_id = int(call.data.replace("sv_ipchange_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, hetzner_id, name, ip_change_count FROM servers WHERE id=?", (server_db_id,))
        row = await cur.fetchone()
    if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
        await call.answer("دسترسی غیرمجاز", show_alert=True)
        return
    count = row[3] or 0
    cost = 0 if count < IP_CHANGE_FREE_COUNT else IP_CHANGE_PRICE
    cost_txt = "رایگان" if cost == 0 else fmt_price(cost)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ تایید تغییر IP ({cost_txt})", callback_data=f"ipcf_{server_db_id}")],
            [InlineKeyboardButton(text="🔙 انصراف", callback_data="back_main")],
        ]
    )
    await call.message.answer(
        f"سرور «{html.escape(row[2])}» را تاکنون {count} بار IP تغییر داده‌اید.\n"
        f"دفعات رایگان: {IP_CHANGE_FREE_COUNT} بار — بعد از آن هر بار {fmt_price(IP_CHANGE_PRICE)}.\n"
        f"هزینه این تغییر: {cost_txt}\n\n"
        "⚠️ توجه: تغییر IP چند دقیقه طول می‌کشد و IP قبلی برای همیشه از بین می‌رود.",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data.startswith("ipcf_"))
async def server_ipchange_confirm(call: CallbackQuery):
    server_db_id = int(call.data.replace("ipcf_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, hetzner_id, name, ip_change_count FROM servers WHERE id=?", (server_db_id,))
        row = await cur.fetchone()
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return
        owner_id, hetzner_id, name, count = row
        count = count or 0
        cost = 0 if count < IP_CHANGE_FREE_COUNT else IP_CHANGE_PRICE
        if cost > 0 and owner_id:
            cur2 = await db.execute("SELECT balance FROM users WHERE telegram_id=?", (owner_id,))
            bal_row = await cur2.fetchone()
            if not bal_row or bal_row[0] < cost:
                await call.message.edit_text(
                    f"❌ موجودی کافی نیست. موجودی فعلی: {fmt_price(bal_row[0] if bal_row else 0)}\nلطفاً ابتدا کیف پول خود را شارژ کنید."
                )
                await call.answer()
                return
        token = await get_active_token(db)
    if not token:
        await call.message.edit_text("❌ هیچ API فعالی برای هتزنر تنظیم نشده.")
        await call.answer()
        return
    api = HetznerAPI(token)
    try:
        new_ip = await change_server_ip(api, hetzner_id)
    except Exception as e:
        await call.message.edit_text(f"❌ خطا در تغییر IP: {e}")
        await call.answer()
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE servers SET ip=?, ip_change_count=ip_change_count+1 WHERE id=?", (new_ip, server_db_id))
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
async def server_renew_start(call: CallbackQuery):
    server_db_id = int(call.data.replace("sv_renew_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, name, expires_at, monthly_price FROM servers WHERE id=?", (server_db_id,))
        row = await cur.fetchone()
    if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
        await call.answer("دسترسی غیرمجاز", show_alert=True)
        return
    owner_id, name, expires_at, monthly_price = row
    if not expires_at or not monthly_price:
        await call.answer("این سرویس تحت سیستم تمدید خودکار نیست.", show_alert=True)
        return
    exp_dt = datetime.fromisoformat(expires_at)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ تایید تمدید ({fmt_price(monthly_price)})", callback_data=f"rncf_{server_db_id}")],
            [InlineKeyboardButton(text="🔙 انصراف", callback_data="back_main")],
        ]
    )
    await call.message.answer(
        f"سرویس «{html.escape(name)}» تا تاریخ {exp_dt.strftime('%Y-%m-%d %H:%M')} (UTC) اعتبار دارد.\n"
        f"هزینه تمدید {RENEWAL_PERIOD_DAYS} روز دیگر: {fmt_price(monthly_price)}",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data.startswith("rncf_"))
async def server_renew_confirm(call: CallbackQuery):
    server_db_id = int(call.data.replace("rncf_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, hetzner_id, name, expires_at, monthly_price, expired_powered_off_at FROM servers WHERE id=?",
            (server_db_id,),
        )
        row = await cur.fetchone()
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return
        owner_id, hetzner_id, name, expires_at, monthly_price, expired_off_at = row
        if not expires_at or not monthly_price:
            await call.answer("این سرویس تحت سیستم تمدید خودکار نیست.", show_alert=True)
            return
        cur2 = await db.execute("SELECT balance FROM users WHERE telegram_id=?", (owner_id,))
        bal_row = await cur2.fetchone()
        if not bal_row or bal_row[0] < monthly_price:
            await call.message.edit_text(
                f"❌ موجودی کافی نیست. موجودی فعلی: {fmt_price(bal_row[0] if bal_row else 0)}\nلطفاً ابتدا کیف پول خود را شارژ کنید."
            )
            await call.answer()
            return
        token = await get_active_token(db)
        base = datetime.fromisoformat(expires_at)
        now = datetime.utcnow()
        if base < now:
            base = now
        new_expiry = base + timedelta(days=RENEWAL_PERIOD_DAYS)
        was_expired_off = bool(expired_off_at)
        await db.execute("UPDATE users SET balance = balance - ? WHERE telegram_id=?", (monthly_price, owner_id))
        await db.execute(
            "UPDATE servers SET expires_at=?, expired_powered_off_at=NULL, traffic_powered_off=0 WHERE id=?",
            (new_expiry.isoformat(), server_db_id),
        )
        await db.commit()
    if was_expired_off and token:
        try:
            await HetznerAPI(token).power_action(hetzner_id, "poweron")
        except Exception:
            pass
    poweron_note = "\n▶️ سرور مجدداً روشن شد." if was_expired_off else ""
    await call.message.edit_text(
        f"✅ سرویس «{html.escape(name)}» با موفقیت تمدید شد.\nمهلت جدید: {new_expiry.strftime('%Y-%m-%d %H:%M')} (UTC){poweron_note}"
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
        inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت به منوی اصلی", callback_data="back_main")]]
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
async def sync_hetzner_servers(db, api: "HetznerAPI"):
    """سرورهایی که مستقیماً در پنل هتزنر ساخته شده‌اند (نه از طریق ربات) را وارد دیتابیس محلی می‌کند
    تا ادمین بتواند از طریق ربات آن‌ها را هم مدیریت کند."""
    try:
        hz_servers = await api.all_servers()
    except Exception:
        return 0
    cur = await db.execute("SELECT hetzner_id FROM servers WHERE hetzner_id IS NOT NULL")
    known_ids = {r[0] for r in await cur.fetchall()}
    added = 0
    for s in hz_servers:
        if s["id"] in known_ids:
            continue
        ip = s.get("public_net", {}).get("ipv4", {}).get("ip", "-")
        loc = s.get("datacenter", {}).get("location", {}).get("name", "-")
        await db.execute(
            "INSERT INTO servers (user_id, hetzner_id, name, ip, location, plan_title, os_image, root_password, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                None, s["id"], s.get("name"), ip, loc, "ساخته‌شده در پنل هتزنر (خارج از ربات)",
                s.get("image", {}).get("name") if s.get("image") else "-", "-", s.get("status", "unknown"),
                datetime.utcnow().isoformat(),
            ),
        )
        added += 1
    await db.commit()
    return added


@router.callback_query(F.data == "adm_servers")
async def adm_servers(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token(db)
        added = 0
        if token:
            added = await sync_hetzner_servers(db, HetznerAPI(token))
        cur = await db.execute(
            "SELECT id, name, ip, user_id, location, status FROM servers ORDER BY id DESC LIMIT 40"
        )
        servers = await cur.fetchall()
    if not servers:
        await call.message.edit_text("سروری ثبت نشده است.", reply_markup=back_inline())
        await call.answer()
        return
    note = f"\n(🔄 {added} سرور جدید از پنل هتزنر همگام‌سازی شد)" if added else ""
    await call.message.edit_text(
        f"🖥 {len(servers)} سرور (شامل سرورهای ساخته‌شده خارج از ربات) - با دکمه‌های مدیریت کامل:{note}",
        reply_markup=back_inline(),
    )
    for s in servers:
        owner = s[3] if s[3] else "بدون مالک (خارج از ربات)"
        text = f"🖥 {s[1]}\nآی‌پی: {s[2]}\nمالک: {owner}\nدیتاسنتر: {s[4]}\nوضعیت: {s[5]}"
        await call.message.answer(text, reply_markup=server_actions_kb(s[0]))
    await call.answer()


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
    images = await api.images()
    kb_rows = [[InlineKeyboardButton(text=img.get("name"), callback_data=f"adimg_{img['id']}")] for img in images[:20]]
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
        server, root_password = await api.create_server(
            name=server_name, server_type=data["server_type"], image=data["image"], location=data["location"]
        )
    except Exception as e:
        await call.message.answer(f"❌ خطا در ساخت سرور: {e}")
        await state.clear()
        await call.answer()
        return
    if not root_password:
        root_password = "از طریق دکمه «تغییر پسورد» دریافت کنید"
    ip = server.get("public_net", {}).get("ipv4", {}).get("ip", "در حال تخصیص")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO servers (user_id, hetzner_id, name, ip, location, plan_title, os_image, root_password, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                call.from_user.id, server["id"], server_name, ip, data["location"], "ادمین (رایگان)",
                data["image"], root_password, "running", datetime.utcnow().isoformat(),
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
@router.callback_query(F.data == "adm_token")
async def adm_token_list(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, label, api_token, active FROM provider ORDER BY id DESC")
        rows = await cur.fetchall()
    kb_rows = []
    lines = ["🔑 لیست API های Hetzner ثبت‌شده:\n"]
    if not rows:
        lines.append("هنوز هیچ API ای اضافه نشده.")
    for r in rows:
        pid, label, token, active = r
        masked = f"...{token[-4:]}" if token and len(token) > 4 else "----"
        status = "✅ فعال" if active else "⚪️ غیرفعال"
        lines.append(f"#{pid} {label or 'بدون‌نام'} ({masked}) - {status}")
        kb_rows.append(
            [
                InlineKeyboardButton(text=f"فعال‌سازی #{pid}", callback_data=f"prov_act_{pid}"),
                InlineKeyboardButton(text=f"حذف #{pid}", callback_data=f"prov_del_{pid}"),
            ]
        )
    kb_rows.append([InlineKeyboardButton(text="➕ افزودن API جدید", callback_data="prov_add")])
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="adm_back")])
    await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()


@router.callback_query(F.data.startswith("prov_act_"))
async def adm_token_activate(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    pid = int(call.data.replace("prov_act_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE provider SET active=0")
        await db.execute("UPDATE provider SET active=1 WHERE id=?", (pid,))
        await db.commit()
    await call.answer("✅ فعال شد")
    await adm_token_list(call)


@router.callback_query(F.data.startswith("prov_del_"))
async def adm_token_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    pid = int(call.data.replace("prov_del_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM provider WHERE id=?", (pid,))
        await db.commit()
    await call.answer("🗑 حذف شد")
    await adm_token_list(call)


@router.callback_query(F.data == "prov_add")
async def adm_token_add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminToken.label)
    await call.message.answer("یک نام دلخواه برای این اکانت Hetzner بنویسید (مثلاً: اکانت اصلی):")
    await call.answer()


@router.message(AdminToken.label)
async def adm_token_label(message: Message, state: FSMContext):
    await state.update_data(label=message.text.strip())
    await state.set_state(AdminToken.token)
    await message.answer(
        "🔑 حالا توکن API هتزنر را ارسال کنید.\n"
        "(از پنل Hetzner Cloud > Security > API Tokens با دسترسی Read & Write بسازید)"
    )


@router.message(AdminToken.token)
async def adm_token_save(message: Message, state: FSMContext):
    data = await state.get_data()
    token = message.text.strip()
    api = HetznerAPI(token)
    try:
        await api.locations()
    except Exception as e:
        await message.answer(f"⚠️ اتصال با این توکن ناموفق بود ({e})؛ دوباره تلاش کنید یا توکن را بررسی کنید.")
        await state.clear()
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE provider SET active=0")
        await db.execute(
            "INSERT INTO provider (label, api_token, active, created_at) VALUES (?,?,1,?)",
            (data.get("label", "بدون‌نام"), token, datetime.utcnow().isoformat()),
        )
        await db.commit()
    await state.clear()
    await message.answer("✅ اتصال جدید با موفقیت اضافه و فعال شد.")


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
        cur = await db.execute("SELECT id, title, server_type, markup_percent, active, location FROM plans ORDER BY id")
        plans = await cur.fetchall()
        token = await get_active_token(db)
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
        if plan_has_stock(stock_map, p[5], p[2], enabled_locations or set()):
            visible.append(p)
        else:
            hidden_count += 1
    lines = ["🧩 پلن‌هایی که هم‌اکنون موجودی سخت‌افزاری دارند (قیمت به‌صورت زنده از Hetzner + درصد سود محاسبه می‌شود):\n"]
    for p in visible:
        loc_txt = p[5] if p[5] else "همه لوکیشن‌ها"
        lines.append(f"• #{p[0]} {p[1]} ({p[2]}) | سود: %{p[3]:g} | {loc_txt} | {'✅' if p[4] else '❌'}")
    if hidden_count:
        lines.append(f"\n⚠️ {hidden_count} پلن دیگر به‌دلیل نبود موجودی فعلی هتزنر در این لیست نمایش داده نشد.")
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
        token = await get_active_token(db)
    if not token:
        await call.message.answer("ابتدا باید یک API Key هتزنر فعال کنید.")
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
    await state.set_state(AdminAddPlan.markup)
    await message.answer("درصد سود روی قیمت پایه Hetzner را وارد کنید (مثلاً 20 برای ۲۰٪):")


@router.message(AdminAddPlan.markup)
async def adm_plan_markup(message: Message, state: FSMContext):
    try:
        markup = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("لطفاً یک عدد معتبر وارد کنید (مثلاً 20).")
        return
    await state.update_data(markup=markup)
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token(db)
    kb_rows = [[InlineKeyboardButton(text="🌍 همه لوکیشن‌ها", callback_data="adplanloc_ALL")]]
    if token:
        api = HetznerAPI(token)
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO plans (server_type, title, cpu, ram, disk, markup_percent, active, location) VALUES (?,?,?,?,?,?,1,?)",
            (data["server_type"], data["title"], data["cpu"], data["ram"], data["disk"], data["markup"], loc_value),
        )
        await db.commit()
    await state.clear()
    await call.message.edit_text("✅ پلن جدید اضافه شد. قیمت نهایی همیشه زنده از Hetzner محاسبه می‌شود.", reply_markup=back_inline("adm_plans"))
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
            await message.bot.send_message(uid, message.text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"✅ ارسال شد به {sent} کاربر ({failed} ناموفق).")


# ---------------------------------------------------------------------------
# نگهداری خودکار: هر ۶ ساعت چک ترافیک مصرفی و مهلت سرویس‌ها
# ---------------------------------------------------------------------------
async def check_traffic_usage(bot: Bot):
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token(db)
        if not token:
            return
        cur = await db.execute(
            "SELECT id, user_id, hetzner_id, name, traffic_powered_off FROM servers "
            "WHERE hetzner_id IS NOT NULL AND user_id IS NOT NULL"
        )
        rows = await cur.fetchall()
    if not rows:
        return
    api = HetznerAPI(token)
    for server_db_id, user_id, hetzner_id, name, traffic_off in rows:
        try:
            server = await api.get_server(hetzner_id)
        except Exception:
            continue
        total_bytes = (server.get("outgoing_traffic") or 0) + (server.get("ingoing_traffic") or 0)
        included = server.get("included_traffic")
        if total_bytes >= TRAFFIC_SOFT_LIMIT_BYTES and not traffic_off:
            try:
                await api.power_action(hetzner_id, "poweroff")
            except Exception:
                pass
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE servers SET traffic_powered_off=1, status='off' WHERE id=?", (server_db_id,))
                await db.commit()
            try:
                await bot.send_message(
                    user_id,
                    f"⚠️ سرور «{name}» بیش از ۱۹.۵ ترابایت ترافیک مصرف کرده و به‌صورت خودکار خاموش شد.\n"
                    "لطفاً هرچه سریع‌تر از اطلاعات خود بکاپ بگیرید. در صورت اتمام کامل حجم مجاز، این سرور برای همیشه حذف خواهد شد.",
                )
            except Exception:
                pass
        elif traffic_off and included and total_bytes >= included:
            try:
                await api.delete_server(hetzner_id)
            except Exception:
                pass
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM servers WHERE id=?", (server_db_id,))
                await db.commit()
            try:
                await bot.send_message(user_id, f"🗑 سرور «{name}» به‌دلیل اتمام کامل حجم ترافیک مجاز، حذف شد.")
            except Exception:
                pass


async def check_expirations(bot: Bot):
    now = datetime.utcnow()
    async with aiosqlite.connect(DB_PATH) as db:
        token = await get_active_token(db)
        cur = await db.execute(
            "SELECT id, user_id, hetzner_id, name, expires_at, expired_powered_off_at FROM servers "
            "WHERE expires_at IS NOT NULL AND user_id IS NOT NULL"
        )
        rows = await cur.fetchall()
    if not rows:
        return
    api = HetznerAPI(token) if token else None
    for server_db_id, user_id, hetzner_id, name, expires_at, expired_off_at in rows:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
        except Exception:
            continue
        if expired_off_at:
            # قبلاً به‌خاطر پایان مهلت خاموش شده -> بررسی سپری‌شدن مهلت گریس ۲ ساعته برای حذف کامل
            try:
                off_dt = datetime.fromisoformat(expired_off_at)
            except Exception:
                continue
            if now - off_dt >= timedelta(hours=EXPIRY_GRACE_HOURS):
                if api:
                    try:
                        await api.delete_server(hetzner_id)
                    except Exception:
                        pass
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM servers WHERE id=?", (server_db_id,))
                    await db.commit()
                try:
                    await bot.send_message(user_id, f"🗑 سرور «{name}» به‌دلیل عدم تمدید مهلت، برای همیشه حذف شد.")
                except Exception:
                    pass
        elif exp_dt <= now:
            # مهلت تمام شده و هنوز خاموش نشده -> خاموش کن و شمارش معکوس حذف را شروع کن
            if api:
                try:
                    await api.power_action(hetzner_id, "poweroff")
                except Exception:
                    pass
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE servers SET expired_powered_off_at=?, status='off' WHERE id=?", (now.isoformat(), server_db_id)
                )
                await db.commit()
            try:
                await bot.send_message(
                    user_id,
                    f"⛔ مهلت سرویس «{name}» به پایان رسید و سرور خاموش شد.\n"
                    f"در صورت عدم تمدید ظرف {EXPIRY_GRACE_HOURS} ساعت آینده، این سرور برای همیشه حذف خواهد شد.\n"
                    "برای تمدید به «🖥 سرورهای من» مراجعه کنید.",
                )
            except Exception:
                pass
        elif exp_dt - now <= timedelta(hours=EXPIRY_REMINDER_HOURS_BEFORE):
            remaining = exp_dt - now
            try:
                await bot.send_message(
                    user_id,
                    f"⏳ مهلت سرویس «{name}» تا {remaining.days} روز و {remaining.seconds // 3600} ساعت دیگر به پایان می‌رسد.\n"
                    "برای جلوگیری از خاموش شدن سرویس، از بخش «🖥 سرورهای من» تمدید کنید.",
                )
            except Exception:
                pass


async def maintenance_poller(bot: Bot):
    """هر ۶ ساعت یک‌بار ترافیک مصرفی و مهلت سرویس‌های همه سرورها را چک می‌کند."""
    while True:
        await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)
        try:
            await check_traffic_usage(bot)
        except Exception as e:
            logger.warning(f"traffic check error: {e}")
        try:
            await check_expirations(bot)
        except Exception as e:
            logger.warning(f"expiry check error: {e}")


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
    asyncio.create_task(oxapay_poller(bot))
    asyncio.create_task(maintenance_poller(bot))
    logger.info("Bot started.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
