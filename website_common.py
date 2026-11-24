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
:root{color-scheme:dark;--cc-bg:#0b1429;--cc-panel:#111d38;--cc-line:rgba(140,174,255,.17);--cc-blue:#2868f0;--cc-cyan:#45caff;--cc-muted:#9aaaca}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:100px}body{margin:0!important;min-height:100vh;background:#0b1429!important;background-image:radial-gradient(ellipse at 50% 12%,rgba(25,75,170,.28),transparent 42%),radial-gradient(rgba(90,130,210,.12) 1px,transparent 1px)!important;background-size:auto,24px 24px;color:#f5f7ff!important;font-family:Vazirmatn,Tahoma,sans-serif!important}
.store-head{position:sticky;top:10px;z-index:30;display:flex;justify-content:space-between;align-items:center;gap:14px;max-width:1240px;margin:12px auto;padding:10px 16px;border:1px solid rgba(130,165,255,.18);border-radius:26px;background:rgba(8,16,37,.9);backdrop-filter:blur(20px);box-shadow:0 14px 45px rgba(0,0,0,.25)}
.store-head nav{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.store-head a{color:#dce7ff;text-decoration:none;padding:10px 13px;border-radius:12px;font-size:14px;transition:.2s}.store-head a:hover{background:#1b3672;color:#fff}.store-brand{font-weight:900;font-size:19px;letter-spacing:.2px;color:#fff!important;display:flex;align-items:center;gap:10px}.store-brand b{display:grid;place-items:center;width:43px;height:43px;border-radius:14px;color:#65aaff;background:linear-gradient(145deg,#18396f,#0d1d3e);border:1px solid #2859a2;box-shadow:0 0 25px rgba(38,111,255,.18);font-size:14px}
.store-main{max-width:1180px;margin:0 auto;padding:0 20px 64px}.store-main h1,.store-main h2,.store-main h3{color:#fff}.store-main .card{background:linear-gradient(145deg,rgba(20,35,68,.94),rgba(13,24,48,.96))!important;border:1px solid var(--cc-line)!important;border-radius:24px!important;box-shadow:0 18px 55px rgba(0,0,0,.2),inset 0 1px rgba(255,255,255,.035);color:#eaf0ff!important}.store-main p,.store-main label{color:#aebbd6}
.store-main input,.store-main select,.store-main textarea{background:#0a1731!important;color:#fff!important;border:1px solid #2b416e!important;border-radius:13px!important;padding:13px!important}
.store-main .btn{display:inline-flex;justify-content:center;align-items:center;gap:8px;background:linear-gradient(135deg,#3981ff,#2056d9)!important;border:0!important;border-radius:14px!important;padding:13px 22px!important;color:white!important;font-weight:800;box-shadow:0 8px 25px rgba(31,99,242,.28);text-decoration:none}.store-main .btn.gray{background:#24375f!important}.store-main table{color:#eaf0ff;display:block;overflow-x:auto;white-space:nowrap}.store-main th{color:#9fc5ff}
.cc-hero{position:relative;isolation:isolate;overflow:hidden;text-align:center;padding:78px 18px 38px;min-height:650px;border-radius:0 0 38px 38px;background:radial-gradient(ellipse at 50% 42%,rgba(35,99,220,.32),transparent 48%),linear-gradient(180deg,rgba(7,17,42,.15),#0b1429)}
.cc-hero:before{content:"";position:absolute;z-index:-1;inset:0;pointer-events:none;background-image:linear-gradient(rgba(80,130,255,.055) 1px,transparent 1px),linear-gradient(90deg,rgba(80,130,255,.055) 1px,transparent 1px);background-size:52px 52px;mask-image:linear-gradient(to bottom,transparent,#000 20%,#000 78%,transparent)}
.cc-kicker{color:#71d8ff;font-weight:800;letter-spacing:2px;font-size:12px}.cc-hero h1{position:relative;font-size:clamp(40px,7vw,78px);font-weight:900;line-height:1.35;margin:22px 0}.cc-hero h1 span{color:#3478ff;text-shadow:0 0 35px rgba(48,111,255,.5)}.cc-hero>p{position:relative;max-width:760px;margin:0 auto 30px;color:#aebbd6;font-size:clamp(15px,2.3vw,21px);line-height:2}
.cc-primary{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:20px;min-width:260px;padding:18px 32px;border-radius:20px;background:linear-gradient(135deg,#4385ff,#2058df);color:white!important;text-decoration:none;font-size:20px;font-weight:900;box-shadow:0 12px 42px rgba(35,103,255,.38);transition:transform .2s,box-shadow .2s}.cc-primary:hover{transform:translateY(-3px);box-shadow:0 18px 48px rgba(35,103,255,.5)}
.cc-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px;max-width:900px;margin:54px auto 0;position:relative}.cc-metric{display:flex;align-items:center;gap:14px;padding:17px 20px;border:1px solid rgba(145,179,255,.24);border-radius:21px;background:linear-gradient(145deg,rgba(37,57,104,.8),rgba(13,27,61,.92));text-align:right;box-shadow:inset 0 1px rgba(255,255,255,.07)}.cc-metric:before{content:"";width:10px;height:10px;flex:0 0 10px;border-radius:50%;background:#16d68a;box-shadow:0 0 14px #16d68a}.cc-metric strong{display:block;font-size:17px;color:#fff}.cc-metric small{color:#a9bde8}
.cc-section{padding:64px 0;border-top:1px solid rgba(130,165,255,.08)}.cc-section-title{text-align:center;margin-bottom:30px}.cc-section-title h2{font-size:clamp(27px,4vw,40px);font-weight:900}.cc-section-title p{color:#9eaccb;font-size:17px;line-height:1.9}.cc-feature-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.cc-feature{display:block;padding:25px;border-radius:23px;background:linear-gradient(145deg,rgba(20,35,68,.96),rgba(13,24,48,.96));border:1px solid var(--cc-line);text-decoration:none;transition:transform .2s,border-color .2s}.cc-feature:hover{transform:translateY(-5px);border-color:rgba(70,150,255,.6)}.cc-feature b{display:block;margin:12px 0;color:#fff;font-size:19px}.cc-feature p{margin:0;color:#aebbd6;line-height:1.9}.cc-glow{display:grid;place-items:center;width:52px;height:52px;border-radius:16px;color:#63baff;background:rgba(37,104,220,.18);font-size:25px}
.cc-process{display:grid;grid-template-columns:repeat(4,1fr);gap:13px}.cc-step{position:relative;padding:22px;border:1px solid var(--cc-line);border-radius:20px;background:rgba(17,31,59,.8)}.cc-step i{display:grid;place-items:center;width:38px;height:38px;border-radius:12px;background:#17346a;color:#7cbaff;font-style:normal;font-weight:900}.cc-step b{display:block;margin:13px 0 7px}.cc-step p{color:#9eaccb;line-height:1.8;margin:0}
.cc-planet{height:230px;max-width:760px;margin:25px auto 0;border-radius:50%;position:relative;background:radial-gradient(ellipse at 45% 35%,#5d9df4 0%,#2454a3 36%,#101f45 68%,transparent 69%);filter:drop-shadow(0 0 45px rgba(46,119,255,.25))}
.store-footer{max-width:1180px;margin:20px auto 0;padding:30px 20px 45px;border-top:1px solid rgba(130,165,255,.12);text-align:center;color:#8292b3}.store-footer a{color:#a9c7ff;text-decoration:none;margin:0 10px}

/* CLOUD_CENTER motion layer */
.cc-hero{background:radial-gradient(ellipse at 50% 42%,rgba(35,99,220,.32),transparent 48%),linear-gradient(180deg,rgba(7,17,42,.15),#0b1429)}
.cc-hero:after{content:"";position:absolute;z-index:-1;width:520px;height:520px;left:50%;top:90px;transform:translateX(-50%);border-radius:50%;background:radial-gradient(circle,rgba(31,107,255,.17),rgba(30,93,220,.06) 45%,transparent 70%);filter:blur(12px);animation:cc-breathe 6s ease-in-out infinite}
.cc-visual{height:245px;max-width:560px;margin:0 auto 12px;position:relative;display:grid;place-items:center;perspective:900px}
.cc-globe{width:174px;height:174px;border-radius:50%;position:relative;overflow:hidden;border:1px solid rgba(108,177,255,.7);background:radial-gradient(circle at 35% 28%,#4b9fff 0,#174e9c 24%,#0a1e4b 64%,#07132d 100%);box-shadow:0 0 35px rgba(37,125,255,.55),inset -18px -16px 35px rgba(0,0,0,.55),inset 8px 7px 22px rgba(127,194,255,.32);animation:cc-float 5s ease-in-out infinite}
.cc-globe:before,.cc-globe:after{content:"";position:absolute;inset:-15%;border:1px solid rgba(120,196,255,.55);border-radius:50%;transform:rotate(-28deg) scaleX(.42);box-shadow:0 0 18px rgba(58,157,255,.2)}
.cc-globe:after{transform:rotate(58deg) scaleX(.7);border-color:rgba(73,151,255,.4)}
.cc-globe-grid{position:absolute;inset:0;background:repeating-linear-gradient(0deg,transparent 0 22px,rgba(114,190,255,.22) 23px 24px),repeating-linear-gradient(90deg,transparent 0 22px,rgba(114,190,255,.2) 23px 24px);border-radius:50%;mask-image:radial-gradient(circle,#000 40%,transparent 72%);animation:cc-grid-drift 14s linear infinite}
.cc-orbit{position:absolute;width:320px;height:110px;border:1px solid rgba(91,168,255,.42);border-radius:50%;transform:rotate(-24deg);animation:cc-orbit-spin 12s linear infinite}
.cc-orbit.two{width:390px;height:145px;transform:rotate(32deg);animation-duration:17s;animation-direction:reverse;border-color:rgba(73,218,255,.28)}
.cc-orbit i{position:absolute;width:11px;height:11px;border-radius:50%;background:#65d8ff;box-shadow:0 0 18px 5px rgba(53,165,255,.8);top:15px;left:48px}
.cc-orbit.two i{top:auto;left:auto;right:42px;bottom:15px;background:#4c8cff}
.cc-float-card{position:absolute;padding:10px 14px;border-radius:14px;border:1px solid rgba(123,178,255,.3);background:rgba(9,24,54,.88);box-shadow:0 10px 30px rgba(0,0,0,.3);color:#d9eaff;font-size:12px;backdrop-filter:blur(10px);animation:cc-float 4.5s ease-in-out infinite}
.cc-float-card b{display:block;color:#fff;font-size:13px}.cc-float-card small{color:#8db9ff}
.cc-float-card.left{left:2%;top:35px}.cc-float-card.right{right:1%;bottom:28px;animation-delay:-2s}
.cc-pulse-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#21e6a0;box-shadow:0 0 12px #21e6a0;margin-left:6px}
.cc-metric,.cc-feature,.cc-step{animation:cc-rise .7s both;animation-delay:var(--delay,0s)}
.cc-metric:nth-child(2){--delay:.08s}.cc-metric:nth-child(3){--delay:.16s}.cc-metric:nth-child(4){--delay:.24s}
.cc-feature:hover .cc-glow{animation:cc-icon-pop .55s ease}
.cc-planet{overflow:hidden;background:radial-gradient(ellipse at 45% 35%,#5d9df4 0%,#2454a3 36%,#101f45 68%,transparent 69%);animation:cc-planet-glow 5s ease-in-out infinite}
.cc-planet:before{content:"";position:absolute;inset:10% 20%;border:1px solid rgba(151,210,255,.55);border-radius:50%;transform:rotate(-18deg) scaleY(.45);box-shadow:0 0 22px rgba(65,153,255,.3)}
.cc-planet:after{content:"";position:absolute;left:0;right:0;top:50%;height:1px;background:linear-gradient(90deg,transparent,rgba(123,202,255,.7),transparent);box-shadow:0 0 20px rgba(78,159,255,.8);animation:cc-scan 3.5s ease-in-out infinite}
@keyframes cc-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-12px)}}
@keyframes cc-breathe{0%,100%{opacity:.55;scale:.9}50%{opacity:1;scale:1.08}}
@keyframes cc-orbit-spin{from{rotate:0deg}to{rotate:360deg}}
@keyframes cc-grid-drift{from{background-position:0 0}to{background-position:0 48px}}
@keyframes cc-rise{from{opacity:0;translate:0 22px}to{opacity:1;translate:0 0}}
@keyframes cc-icon-pop{50%{transform:scale(1.13) rotate(5deg)}}
@keyframes cc-planet-glow{0%,100%{filter:drop-shadow(0 0 28px rgba(46,119,255,.18))}50%{filter:drop-shadow(0 0 58px rgba(46,119,255,.42))}}
@keyframes cc-scan{0%,100%{transform:translateY(-55px);opacity:.25}50%{transform:translateY(55px);opacity:1}}
@media(max-width:720px){.cc-visual{height:205px;max-width:390px;margin-bottom:8px}.cc-globe{width:138px;height:138px}.cc-orbit{width:260px;height:88px}.cc-orbit.two{width:310px;height:115px}.cc-float-card{font-size:10px;padding:8px 10px}.cc-float-card.left{left:0;top:22px}.cc-float-card.right{right:0;bottom:16px}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation-duration:.01ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important;transition-duration:.01ms!important}}

@media(max-width:720px){.store-head{top:6px;margin:6px 8px;padding:8px 10px;border-radius:22px;align-items:center}.store-brand{font-size:14px}.store-brand b{width:38px;height:38px}.store-head nav{gap:1px;justify-content:flex-end}.store-head a{font-size:11px;padding:8px 7px}.store-main{padding:0 13px 40px}.cc-hero{padding:58px 8px 30px;min-height:auto}.cc-metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:34px}.cc-metric{padding:12px 10px;gap:8px}.cc-metric strong{font-size:13px}.cc-metric small{font-size:10px}.cc-feature-grid,.cc-process{grid-template-columns:1fr}.cc-section{padding:38px 0}.cc-planet{height:170px}}
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
    doc = "<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='theme-color' content='#0b1429'><meta name='description' content='فروش سرور مجازی و اکانت با تحویل سریع در CLOUD_CENTER'><link rel='preconnect' href='https://fonts.googleapis.com'><link href='https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700;800;900&display=swap' rel='stylesheet'><title>" + html.escape(title) + " | CLOUD_CENTER</title><style>" + BASE_STORE_CSS + STORE_FRONT_CSS + "</style></head><body><header class='store-head'><a class='store-brand' href='/'><b>CC</b><span>CLOUD_CENTER</span></a><nav><a href='/'>خانه</a><a href='/shop'>فروشگاه اکانت</a><a href='/servers/buy'>ساخت سرور</a>" + account + "</nav></header><main class='store-main'>" + body + "</main><footer class='store-footer'><p>زیرساخت ابری، سریع و مطمئن با CLOUD_CENTER</p><p><a href='/shop'>فروشگاه</a><a href='/servers/buy'>خرید سرور</a><a href='/account'>حساب مشتری</a></p><small>© CLOUD_CENTER — تمامی حقوق محفوظ است.</small></footer>" + os.getenv("ENAMAD_WIDGET_HTML", "") + "</body></html>"
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
