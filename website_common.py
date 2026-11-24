# -*- coding: utf-8 -*-
"""Shared helpers used only by the independent customer-facing website."""
import asyncio
import html
import os
import secrets
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta

import aiosqlite
from fastapi import Request
from fastapi.responses import HTMLResponse
import bot as core

DB_PATH = core.DB_PATH
CUSTOMER_SESSIONS: dict[str, dict] = {}
CUSTOMER_SESSION_TTL_HOURS = 72

def esc(v) -> str:
    return html.escape(str(v)) if v is not None else "-"

STORE_FRONT_CSS = """
:root{color-scheme:dark;--cc-bg:#07112d;--cc-panel:rgba(25,43,88,.72);--cc-line:rgba(130,165,255,.22);--cc-blue:#2868f0;--cc-cyan:#42c9ff}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0!important;background:radial-gradient(ellipse at 50% 32%,#102d70 0%,#091638 42%,#050b1e 100%)!important;color:#f5f7ff!important;font-family:Vazirmatn,Tahoma,sans-serif!important}
.store-head{position:sticky;top:0;z-index:20;display:flex;justify-content:space-between;align-items:center;gap:14px;padding:14px max(22px,calc((100% - 1180px)/2));border-bottom:1px solid var(--cc-line);background:rgba(5,12,32,.88);backdrop-filter:blur(18px)}
.store-head nav{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.store-head a{color:#e5edff;text-decoration:none;padding:10px 13px;border-radius:12px;font-size:14px}.store-head a:hover{background:#1b3672}.store-brand{font-weight:900;font-size:20px;letter-spacing:.2px;color:#fff}.store-brand b{color:#4386ff}
.store-main{max-width:1180px;margin:0 auto;padding:0 20px 64px}.store-main h1,.store-main h2,.store-main h3{color:#fff}.store-main .card{background:linear-gradient(145deg,rgba(40,62,116,.72),rgba(11,23,54,.86))!important;border:1px solid var(--cc-line)!important;border-radius:22px!important;box-shadow:0 14px 45px rgba(0,0,0,.22),inset 0 1px rgba(255,255,255,.06);color:#eaf0ff!important}.store-main p,.store-main label{color:#c5d2f0}
.store-main input,.store-main select,.store-main textarea{background:#091632!important;color:#fff!important;border:1px solid #304a80!important;border-radius:12px!important;padding:12px!important}
.store-main .btn{display:inline-flex;justify-content:center;align-items:center;gap:8px;background:linear-gradient(135deg,#3981ff,#2056d9)!important;border:0!important;border-radius:14px!important;padding:13px 22px!important;color:white!important;font-weight:800;box-shadow:0 8px 25px rgba(31,99,242,.28)}
.store-main .btn.gray{background:#24375f!important}.store-main table{color:#eaf0ff}.store-main th{color:#9fc5ff}
.cc-hero{position:relative;overflow:hidden;text-align:center;padding:76px 18px 42px;min-height:520px;background-image:linear-gradient(rgba(5,12,35,.08),rgba(5,12,35,.15)),radial-gradient(circle at 50% 48%,rgba(40,111,255,.25),transparent 43%);border-radius:0 0 34px 34px}
.cc-hero:before,.cc-hero:after{content:"";position:absolute;inset:0;pointer-events:none;background-image:linear-gradient(rgba(80,130,255,.06) 1px,transparent 1px),linear-gradient(90deg,rgba(80,130,255,.06) 1px,transparent 1px);background-size:54px 54px;mask-image:linear-gradient(to bottom,transparent,#000 25%,#000 75%,transparent)}
.cc-kicker{color:#71d8ff;font-weight:700;letter-spacing:2px;font-size:12px}.cc-hero h1{position:relative;font-size:clamp(38px,7vw,76px);font-weight:900;line-height:1.35;margin:18px 0}.cc-hero h1 span{color:#3478ff;text-shadow:0 0 35px rgba(48,111,255,.5)}.cc-hero p{position:relative;max-width:700px;margin:0 auto 30px;color:#bdcbed;font-size:clamp(15px,2.3vw,21px);line-height:2}
.cc-primary{position:relative;display:inline-flex;align-items:center;justify-content:center;min-width:245px;padding:17px 30px;border-radius:19px;background:linear-gradient(135deg,#4385ff,#2058df);color:white!important;text-decoration:none;font-size:19px;font-weight:900;box-shadow:0 12px 42px rgba(35,103,255,.38)}
.cc-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;max-width:1050px;margin:38px auto 0;position:relative}.cc-metric{padding:17px;border:1px solid var(--cc-line);border-radius:18px;background:linear-gradient(145deg,rgba(42,65,125,.78),rgba(12,26,61,.9));text-align:right}.cc-metric strong{display:block;font-size:17px;color:#fff}.cc-metric small{color:#a9bde8}.cc-section{padding:42px 0}.cc-section-title{text-align:center;margin-bottom:24px}.cc-section-title h2{font-size:28px}.cc-feature-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.cc-feature{padding:22px;border-radius:20px;background:rgba(23,42,86,.62);border:1px solid var(--cc-line)}.cc-feature b{display:block;margin:8px 0;color:#fff}.cc-feature p{margin:0;color:#aebfe4;line-height:1.9}.cc-glow{color:#54cfff;font-size:25px}
@media(max-width:720px){.store-head{padding:10px 13px;align-items:flex-start}.store-brand{font-size:17px}.store-head nav{gap:2px;justify-content:flex-end}.store-head a{font-size:12px;padding:8px}.store-main{padding:0 13px 40px}.cc-hero{padding:58px 8px 30px;min-height:auto}.cc-metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:30px}.cc-metric{padding:13px}.cc-feature-grid{grid-template-columns:1fr}.cc-section{padding:30px 0}}
"""

BASE_STORE_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#07112d;color:#f5f7ff;font-family:Vazirmatn,Tahoma,sans-serif}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px;margin:20px 0}
.card{padding:22px;margin:14px 0;border-radius:18px;background:linear-gradient(145deg,rgba(40,62,116,.72),rgba(11,23,54,.86));border:1px solid rgba(130,165,255,.22);color:#eaf0ff}
.btn{display:inline-flex;align-items:center;justify-content:center;padding:11px 18px;margin:4px;border:0;border-radius:12px;background:linear-gradient(135deg,#3981ff,#2056d9);color:#fff;text-decoration:none;font-weight:800;cursor:pointer}
.btn.gray{background:#24375f}.btn.green{background:#087d66}
input,select,textarea{display:block;width:100%;padding:12px;margin:8px 0 14px;border-radius:10px;border:1px solid #304a80;background:#091632;color:#fff;font:inherit}
label{display:block;color:#c5d2f0;margin:8px 0 4px}
table{width:100%;border-collapse:collapse;overflow:auto}th,td{padding:10px;border-bottom:1px solid rgba(130,165,255,.2);text-align:right}
.msg{padding:12px;border-radius:10px;margin:12px 0}.msg.err{background:#63243b;color:#ffdce5}
pre{white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:720px){.card{padding:14px}.grid{grid-template-columns:1fr}.store-head{gap:6px}.store-head nav{justify-content:flex-end}}
"""

def get_customer_session(request: Request):
    token = request.cookies.get("customer_session")
    if not token or token not in CUSTOMER_SESSIONS:
        return None
    sess = CUSTOMER_SESSIONS[token]
    if sess["expires"] < datetime.utcnow():
        CUSTOMER_SESSIONS.pop(token, None)
        return None
    return sess

def storefront_page(title: str, body: str, customer=None):
    account = '<a href="/account">حساب من</a>' if customer else '<a href="/customer/login">ورود / ثبت‌نام</a>'
    doc = "<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='theme-color' content='#07112d'><meta name='description' content='فروش سرور مجازی و اکانت با تحویل سریع در CLOUD_CENTER'><link rel='preconnect' href='https://fonts.googleapis.com'><link href='https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700;800;900&display=swap' rel='stylesheet'><title>" + html.escape(title) + " | CLOUD_CENTER</title><style>" + BASE_STORE_CSS + STORE_FRONT_CSS + "</style></head><body><header class='store-head'><a class='store-brand' href='/'><b>CLOUD</b>_CENTER</a><nav><a href='/shop'>فروشگاه اکانت</a><a href='/servers/buy'>خرید سرور</a>" + account + "</nav></header><main class='store-main'>" + body + os.getenv("ENAMAD_WIDGET_HTML", "") + "</main></body></html>"
    return HTMLResponse(doc)

def _send_customer_email_code_sync(email: str, code: str):
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("تنظیمات SMTP تکمیل نشده است؛ SMTP_HOST را در فایل .env تنظیم کنید.")
    msg = EmailMessage()
    msg["Subject"] = "کد ورود CLOUD_CENTER"
    msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", ""))
    msg["To"] = email
    msg.set_content(f"کد ورود شما: {code}\nاین کد تا ۱۰ دقیقه معتبر است.")
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls()
        user = os.getenv("SMTP_USER", "")
        if user:
            smtp.login(user, os.getenv("SMTP_PASSWORD", ""))
        smtp.send_message(msg)

async def send_customer_email_code(email: str, code: str):
    await asyncio.to_thread(_send_customer_email_code_sync, email, code)

async def create_customer_session(response, user_id: int):
    token = secrets.token_urlsafe(32)
    CUSTOMER_SESSIONS[token] = {"user_id": int(user_id), "expires": datetime.utcnow() + timedelta(hours=CUSTOMER_SESSION_TTL_HOURS)}
    response.set_cookie("customer_session", token, httponly=True, secure=os.getenv("COOKIE_SECURE", "0") == "1", samesite="lax", max_age=CUSTOMER_SESSION_TTL_HOURS * 3600)
    return response

async def merge_email_account_into_telegram(email_user_id: int, telegram_data: dict):
    tid = int(telegram_data["id"])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        source = await (await db.execute("SELECT telegram_id,email,balance FROM users WHERE telegram_id=?", (email_user_id,))).fetchone()
        if not source:
            await db.rollback()
            raise ValueError("حساب ایمیل پیدا نشد")
        existing = await (await db.execute("SELECT telegram_id,email FROM users WHERE telegram_id=?", (tid,))).fetchone()
        if existing and existing[0] != email_user_id:
            if existing[1] and existing[1].lower() != str(source[1] or "").lower():
                await db.rollback()
                raise ValueError("این تلگرام قبلاً به ایمیل دیگری متصل شده است")
            await db.execute("UPDATE users SET balance=balance+?,email=COALESCE(email,?),email_verified=1 WHERE telegram_id=?", (float(source[2] or 0), source[1], tid))
            for table in ("servers", "orders", "wallet_requests", "tickets", "product_orders", "wallet_transactions"):
                try:
                    await db.execute(f"UPDATE {table} SET user_id=? WHERE user_id=?", (tid, email_user_id))
                except Exception:
                    pass
            await db.execute("DELETE FROM users WHERE telegram_id=?", (email_user_id,))
        else:
            await db.execute("UPDATE users SET telegram_id=?,username=?,full_name=? WHERE telegram_id=?", (tid, telegram_data.get("username"), (telegram_data.get("first_name", "") + " " + telegram_data.get("last_name", "")).strip(), email_user_id))
        await db.commit()
    return tid
