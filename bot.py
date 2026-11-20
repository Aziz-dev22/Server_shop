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
import time
import html
import logging
import os
import secrets
import zipfile
import tempfile
from pathlib import Path
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
TRAFFIC_SOFT_LIMIT_BYTES = int(19.5 * 1_000_000_000_000)  # ۱۹.۵ ترابایت -> خاموش‌سازی هشداری
EXPIRY_REMINDER_HOURS_BEFORE = 48   # چند ساعت قبل از پایان مهلت، یادآوری تمدید ارسال شود
EXPIRY_GRACE_HOURS = 2               # چند ساعت بعد از پایان مهلت، قبل از حذف کامل سرور صبر شود
MAINTENANCE_INTERVAL_SECONDS = 6 * 3600  # فاصله چک ترافیک/مهلت (هر ۶ ساعت)
DEFAULT_EUR_USD_RATE = 1.08  # فقط در صورتی که دریافت نرخ زنده ناموفق باشد

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("hetzner_shop_bot")

router = Router()


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
        await _safe_add_column(db, "provider", "type TEXT DEFAULT 'hetzner'")
        await _safe_add_column(db, "plans", "provider_type TEXT DEFAULT 'hetzner'")
        await _safe_add_column(db, "servers", "provider_type TEXT DEFAULT 'hetzner'")
        # سرورها/پلن‌ها/API‌های قبلی همیشه هتزنری بودند -> برای سازگاری صریحاً مقداردهی شود
        await db.execute("UPDATE provider SET type='hetzner' WHERE type IS NULL")
        await db.execute("UPDATE plans SET provider_type='hetzner' WHERE provider_type IS NULL")
        await db.execute("UPDATE servers SET provider_type='hetzner' WHERE provider_type IS NULL")
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
    included = int(live.get("included_traffic") or 0)
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
                f"⚠️ سرور «{name}» خاموش شده و درخواست حذف/تمدید به ادمین ارسال شد.\\nعلت: {reason}\\nتا تصمیم ادمین، سرور حذف نخواهد شد.",
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
        """Fetch all system images, including newer releases, without duplicates."""
        images, page = [], 1
        while True:
            data = await self._get("/images", params={"type": "system", "per_page": 50, "page": page})
            images.extend(data.get("images", []))
            pagination = data.get("meta", {}).get("pagination", {})
            if not pagination.get("next_page"):
                break
            page = pagination["next_page"]
        unique = {str(img.get("id")): img for img in images if img.get("id") is not None}
        return sorted(unique.values(), key=lambda img: (img.get("name") or "").lower())

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
    async with aiosqlite.connect(DB_PATH) as db:
        enabled = await get_setting(db, "tickets_enabled", "1")
        support_id = await get_setting(db, "support_id", "")
        support_message = await get_setting(db, "support_message", "برای ارتباط با پشتیبانی از اطلاعات زیر استفاده کنید.")
    if enabled != "1":
        lines = ["🆘 پشتیبانی", support_message]
        if support_id:
            lines.append(f"شناسه پشتیبانی: {support_id}")
        await message.answer("\n\n".join(lines))
        return
    await state.set_state(SupportTicket.waiting)
    lines = ["پیام خود را برای تیم پشتیبانی بنویسید:"]
    if support_message:
        lines.append(support_message)
    if support_id:
        lines.append(f"ارتباط مستقیم: {support_id}")
    await message.answer("\n\n".join(lines))


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
        return server.get("public_net", {}).get("ipv4", {}).get("ip", "در حال تخصیص")
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
        images = deduplicate_images(await api.images())
    except Exception as e:
        await call.message.answer(f"خطا در دریافت سیستم‌عامل‌ها: {e}")
        await call.answer()
        return
    kb_rows = [[InlineKeyboardButton(text=f"{img.get('name')}", callback_data=f"img_{img['id']}")] for img in images[:100]]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")])
    await state.set_state(BuyFlow.image)
    await call.message.edit_text("💿 سیستم‌عامل را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
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
        if not user or user[3] < price:
            await call.message.edit_text("❌ موجودی کیف پول شما کافی نیست. ابتدا کیف پول را شارژ کنید.")
            await state.clear()
            await call.answer()
            return
        providers = await get_active_providers(db, provider_type)
        provider_id, token = (providers[0] if providers else (None, None))

    if provider_type == "hetzner":
        viable = []
        for pid, ptoken in providers:
            try:
                stock = await available_types_for_location(HetznerAPI(ptoken), data["location"])
                if stock is not None and data["server_type"] in stock:
                    viable.append((pid, ptoken))
            except Exception:
                continue
        if viable:
            provider_id, token = viable[0]
        else:
            await call.message.edit_text("❌ موجودی این پلن در همه APIهای فعال تمام شده یا قابل استعلام نیست. خرید انجام نشد.")
            await state.clear()
            await call.answer()
            return
    if not token:
        await call.message.edit_text("❌ هیچ API فعال برای ساخت سرور پیدا نشد.")
        await state.clear()
        await call.answer()
        return

    await call.message.edit_text("⏳ در حال ساخت سرور شما... لطفاً چند لحظه صبر کنید.")
    api = make_provider_api(provider_type, token)
    server_name = f"srv-{user_id}-{secrets.token_hex(2)}"
    try:
        server, root_password = await api.create_server(
            name=server_name, server_type=data["server_type"], image=data["image"], location=data["location"]
        )
    except Exception as first_error:
        if provider_type == "hetzner":
            # Try other active accounts in priority order if the first provider's create fails.
            created = None
            for fallback_pid, fallback_token in providers:
                if fallback_pid == provider_id:
                    continue
                try:
                    stock = await available_types_for_location(HetznerAPI(fallback_token), data["location"])
                    if stock is None or data["server_type"] not in stock:
                        continue
                    server, root_password = await HetznerAPI(fallback_token).create_server(
                        name=server_name, server_type=data["server_type"], image=data["image"], location=data["location"]
                    )
                    provider_id, token, api = fallback_pid, fallback_token, HetznerAPI(fallback_token)
                    created = True
                    break
                except Exception:
                    continue
            if not created:
                await call.message.answer(f"❌ ساخت سرور در همه APIهای فعال ناموفق بود. خطای اول: {first_error}")
                await state.clear()
                await call.answer()
                return
        else:
            await call.message.answer(f"❌ خطا در ساخت سرور: {first_error}")
            await state.clear()
            await call.answer()
            return

    if not root_password:
        root_password = "از طریق دکمه «تغییر پسورد» در بخش سرورهای من دریافت کنید"

    ip = extract_server_ip(provider_type, server)
    expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE telegram_id=?", (price, user_id))
        await db.execute(
            "INSERT INTO servers (user_id, hetzner_id, name, ip, location, plan_title, os_image, root_password, status, created_at, expires_at, monthly_price, provider_type, provider_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id, server["id"], server_name, ip, data["location"], data["plan_title"],
                data["image"], root_password, "running", datetime.utcnow().isoformat(), expires_at, price, provider_type, provider_id,
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
def server_actions_kb(server_id: int, back_callback: str = "my_servers_list"):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▶️ روشن", callback_data=f"sv_on_{server_id}"),
             InlineKeyboardButton(text="⏹ خاموش", callback_data=f"sv_off_{server_id}"),
             InlineKeyboardButton(text="🔄 ریبوت", callback_data=f"sv_reboot_{server_id}")],
            [InlineKeyboardButton(text="🔑 تغییر پسورد", callback_data=f"sv_pass_{server_id}"),
             InlineKeyboardButton(text="🛠 نصب مجدد", callback_data=f"sv_rebuild_{server_id}")],
            [InlineKeyboardButton(text="🌐 تغییر IP", callback_data=f"sv_ipchange_{server_id}"),
             InlineKeyboardButton(text="⏳ تمدید", callback_data=f"sv_renew_{server_id}")],
            [InlineKeyboardButton(text="📊 نمایش زمان و حجم", callback_data=f"svstats_{server_id}")],
            [InlineKeyboardButton(text="🗑 درخواست حذف", callback_data=f"sv_del_{server_id}")],
            *([[InlineKeyboardButton(text="👤 تعیین / انتقال مالکیت", callback_data=f"admsvowner_{server_id}")]] if back_callback == "adm_servers" else []),
            [InlineKeyboardButton(text="🔙 بازگشت به فهرست", callback_data=back_callback)],
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
    if not servers:
        await message.answer("شما هنوز سروری خریداری نکرده‌اید.", reply_markup=back_inline("back_main"))
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🖥 {str(r[1] or 'سرور')} · {str(r[2] or '-')} · #{r[0]}", callback_data=f"svshow_{r[0]}")]
        for r in servers
    ] + [[InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="back_main")]])
    await message.answer("🖥 سرورهای شما\nبرای مدیریت، یک سرور را انتخاب کنید:", reply_markup=kb)


@router.callback_query(F.data == "my_servers_list")
async def my_servers_list_callback(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name, ip FROM servers WHERE user_id=? ORDER BY id DESC", (call.from_user.id,))
        servers = await cur.fetchall()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🖥 {str(r[1] or 'سرور')} · {str(r[2] or '-')} · #{r[0]}", callback_data=f"svshow_{r[0]}")]
        for r in servers
    ] + [[InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="back_main")]])
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
            included = int(live.get("included_traffic") or 0)
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
        cur = await db.execute("SELECT user_id, name, ip, location, plan_title, status, created_at, expires_at, traffic_in_bytes, traffic_out_bytes, traffic_included_bytes, traffic_synced_at, deletion_pending_reason, provider_type FROM servers WHERE id=?", (sid,))
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
            included = int(live_server.get("included_traffic") or 0)
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
        exp_text = datetime.fromisoformat(exp_text).strftime("%Y-%m-%d %H:%M UTC")
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
    await call.message.edit_text(text, reply_markup=server_actions_kb(sid, "adm_servers" if is_admin(call.from_user.id) else "my_servers_list"))
    await call.answer()


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
        cur = await db.execute("SELECT user_id, hetzner_id, name, provider_type, provider_id, deletion_pending_reason FROM servers WHERE id=?", (server_db_id,))
        row = await cur.fetchone()
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return
        provider_type = row[3] or "hetzner"
        if row[5] and action != "del":
            await call.answer("این سرور در انتظار تأیید حذف است و تا تعیین تکلیف ادمین خاموش می‌ماند. برای تمدید از دکمه تمدید استفاده کنید.", show_alert=True)
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


@router.callback_query(F.data.startswith("delapprove_"))
async def server_delete_approve(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("فقط ادمین می‌تواند حذف را تأیید کند.", show_alert=True)
        return
    sid = int(call.data.replace("delapprove_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT hetzner_id, name, provider_type, deletion_pending_reason FROM servers WHERE id=?", (sid,))
        row = await cur.fetchone()
        if not row or not row[3]:
            await call.answer("درخواست حذف پیدا نشد یا قبلاً تعیین تکلیف شده.", show_alert=True)
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
            "UPDATE servers SET deletion_pending_reason=NULL, deletion_requested_at=NULL, deletion_notified=0, traffic_limit_reviewed_at=CASE WHEN deletion_pending_reason LIKE '%حجم%' OR deletion_pending_reason LIKE '%ترافیک%' THEN ? ELSE traffic_limit_reviewed_at END WHERE id=?",
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
    if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
        await call.message.answer("❌ دسترسی غیرمجاز")
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
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.message.edit_text("❌ دسترسی غیرمجاز")
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
            "SELECT user_id, hetzner_id, name, expires_at, monthly_price, expired_powered_off_at, provider_type, provider_id, traffic_powered_off, deletion_pending_reason FROM servers WHERE id=?",
            (server_db_id,),
        )
        row = await cur.fetchone()
        if not row or (row[0] != call.from_user.id and not is_admin(call.from_user.id)):
            await call.answer("دسترسی غیرمجاز", show_alert=True)
            return
        owner_id, hetzner_id, name, expires_at, monthly_price, expired_off_at, provider_type, provider_id, traffic_off, deletion_pending_reason = row
        provider_type = provider_type or "hetzner"
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
        token = await get_token_for_server(db, server_db_id, provider_type)
        base = datetime.fromisoformat(expires_at)
        now = datetime.utcnow()
        if base < now:
            base = now
        new_expiry = base + timedelta(days=RENEWAL_PERIOD_DAYS)
        was_expired_off = bool(expired_off_at)
        await db.execute("UPDATE users SET balance = balance - ? WHERE telegram_id=?", (monthly_price, owner_id))
        await db.execute(
            "UPDATE servers SET expires_at=?, expired_powered_off_at=NULL, traffic_powered_off=0, deletion_pending_reason=NULL, deletion_requested_at=NULL, deletion_notified=0, traffic_limit_reviewed_at=CASE WHEN ? LIKE '%ترافیک%' OR ? LIKE '%حجم%' THEN ? ELSE traffic_limit_reviewed_at END WHERE id=?",
            (new_expiry.isoformat(), deletion_pending_reason or "", deletion_pending_reason or "", datetime.utcnow().isoformat(), server_db_id),
        )
        await db.commit()
    if (was_expired_off or traffic_off or deletion_pending_reason) and token:
        try:
            await make_provider_api(provider_type, token).power_action(hetzner_id, "poweron")
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
async def sync_hetzner_servers(db, api: "HetznerAPI", provider_id=None):
    """Sync only live servers from one connected API; preserve customer-owned rows."""
    try:
        hz_servers = await api.all_servers()
    except Exception as exc:
        logger.warning("Could not sync Hetzner provider %s: %s", provider_id, exc)
        return 0
    cur = await db.execute("SELECT id, hetzner_id FROM servers WHERE provider_type='hetzner' AND provider_id IS ? AND user_id IS NULL", (provider_id,))
    known_rows = await cur.fetchall()
    known_ids = {r[1] for r in known_rows}
    live_ids = {server["id"] for server in hz_servers}
    added = 0
    for server in hz_servers:
        sid = server["id"]
        ip = (server.get("public_net") or {}).get("ipv4", {}).get("ip", "-")
        loc = (server.get("datacenter") or {}).get("location", {}).get("name", "-")
        server_created = server.get("created") or datetime.utcnow().isoformat()
        if sid in known_ids:
            await db.execute("UPDATE servers SET name=?, ip=?, location=?, status=?, created_at=? WHERE provider_type='hetzner' AND provider_id IS ? AND hetzner_id=? AND user_id IS NULL", (server.get("name"), ip, loc, server.get("status", "unknown"), server_created, provider_id, sid))
            continue
        await db.execute("INSERT INTO servers (user_id, hetzner_id, name, ip, location, plan_title, os_image, root_password, status, created_at, provider_id, provider_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,'hetzner')", (None, sid, server.get("name"), ip, loc, "ساخته‌شده خارج از ربات", (server.get("image") or {}).get("name", "-"), "-", server.get("status", "unknown"), server_created, provider_id))
        added += 1
    for local_id, sid in known_rows:
        if sid not in live_ids:
            await db.execute("DELETE FROM servers WHERE id=? AND user_id IS NULL", (local_id,))
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
        await db.execute("UPDATE servers SET user_id=? WHERE id=?", (target_id, sid))
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
    images = deduplicate_images(await api.images())
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
        cur = await db.execute(
            "SELECT id, user_id, hetzner_id, name, traffic_powered_off, provider_type, traffic_limit_reviewed_at FROM servers "
            "WHERE hetzner_id IS NOT NULL AND user_id IS NOT NULL AND provider_type='hetzner'"
        )
        rows = await cur.fetchall()
    for server_db_id, user_id, hetzner_id, name, traffic_off, provider_type, traffic_reviewed_at in rows:
        async with aiosqlite.connect(DB_PATH) as db:
            token = await get_token_for_server(db, server_db_id, provider_type or "hetzner")
        if not token:
            continue
        api = HetznerAPI(token)
        try:
            server = await api.get_server(hetzner_id)
        except Exception:
            continue
        incoming = int(server.get("ingoing_traffic") or 0)
        outgoing = int(server.get("outgoing_traffic") or 0)
        total_bytes = incoming + outgoing
        included = int(server.get("included_traffic") or 0)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE servers SET traffic_in_bytes=?, traffic_out_bytes=?, traffic_included_bytes=?, traffic_synced_at=? WHERE id=?",
                (incoming, outgoing, included, datetime.utcnow().isoformat(), server_db_id),
            )
            await db.commit()
        # Soft limit: power off at 19.5 TB, but never delete automatically.
        if total_bytes >= TRAFFIC_SOFT_LIMIT_BYTES and not traffic_off and not traffic_reviewed_at:
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
                    f"⚠️ سرور «{name}» به حد هشدار ترافیک ({format_bytes(total_bytes)}) رسیده و خاموش شده است. "
                    "در صورت رسیدن به حجم مجاز، حذف فقط پس از تأیید ادمین انجام خواهد شد.",
                )
            except Exception:
                pass
        hard_limit = included if included > 0 else int(20 * 1_000_000_000_000)
        if total_bytes < TRAFFIC_SOFT_LIMIT_BYTES and traffic_reviewed_at:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE servers SET traffic_limit_reviewed_at=NULL WHERE id=?", (server_db_id,))
                await db.commit()
        elif total_bytes >= hard_limit and not traffic_reviewed_at:
            await request_server_deletion(bot, server_db_id, f"حجم مجاز ترافیک تکمیل شد ({format_bytes(total_bytes)} / {format_bytes(hard_limit)})")


async def check_expirations(bot: Bot):
    now = datetime.utcnow()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, user_id, hetzner_id, name, expires_at, expired_powered_off_at, provider_type FROM servers "
            "WHERE expires_at IS NOT NULL AND user_id IS NOT NULL"
        )
        rows = await cur.fetchall()
        tokens_by_type = {}
        for ptype in ("hetzner", "digitalocean", "vultr", "gcore"):
            tokens_by_type[ptype] = await get_active_token_for(db, ptype)
    if not rows:
        return
    for server_db_id, user_id, hetzner_id, name, expires_at, expired_off_at, provider_type in rows:
        provider_type = provider_type or "hetzner"
        token = tokens_by_type.get(provider_type)
        api = make_provider_api(provider_type, token) if token else None
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
                await request_server_deletion(bot, server_db_id, "پایان مهلت سرویس و عدم تمدید پس از مهلت ارفاقی")
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
        try:
            await cleanup_old_primary_ips()
        except Exception as e:
            logger.warning(f"old IP cleanup error: {e}")


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
    router.message.middleware(MembershipMiddleware())
    router.callback_query.middleware(MembershipMiddleware())
    asyncio.create_task(automatic_backup_poller(bot))
    asyncio.create_task(oxapay_poller(bot))
    asyncio.create_task(maintenance_poller(bot))
    asyncio.create_task(ip_cleanup_poller())
    logger.info("Bot started.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
