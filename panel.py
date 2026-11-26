# -*- coding: utf-8 -*-
"""
Hetzner Shop - پنل مدیریت تحت وب
از همان دیتابیس و منطق ربات تلگرام (bot.py) استفاده می‌کند، پس هیچ تنظیمی تکراری نیست.
اجرا: uvicorn panel:app --host 0.0.0.0 --port 8088
"""
import html
import asyncio
import os
from pathlib import Path
import secrets
import hashlib
import hmac
import re
import math
import smtplib
from email.message import EmailMessage
import urllib.parse
import io
import zipfile
import sqlite3
import httpx
from fastapi import UploadFile, File
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiosqlite
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import bot as core  # همه منطق و دیتابیس مشترک از bot.py

PANEL_USERNAME = os.getenv("PANEL_USERNAME", "admin")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
DB_PATH = core.DB_PATH

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
UPLOAD_DIR = Path(__file__).resolve().parent / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS: dict[str, dict] = {}
SESSION_TTL_HOURS = 12


# ---------------------------------------------------------------------------
# قالب صفحه و ابزار کمکی
# ---------------------------------------------------------------------------
NAV_ITEMS = [
    ("/admin", "📊 داشبورد"),
    ("/users", "👥 کاربران"),
    ("/servers", "🖥 سرورها"),
    ("/backup", "🗄 بکاپ خودکار و عضویت اجباری"),
    ("/ip-pricing", "🌐 قیمت و دفعات رایگان تغییر IP"),
    ("/server-transfer", "🔁 انتقال سرورها بین APIها"),
    ("/plans", "🧩 پلن‌ها"),
    ("/locations", "📍 لوکیشن‌ها"),
    ("/providers", "🔑 API های Hetzner"),
    ("/support-settings", "🎫 تیکت و پشتیبانی"),
    ("/account-status", "📈 لیمیت، CPU و Usage اکانت‌ها"),
    ("/wallet-requests", "💳 درخواست‌های شارژ"),
    ("/orders", "📦 سفارش‌ها"),
    ("/products", "🛍 فروشگاه محصولات"),
    ("/coupons", "🎟 کدهای تخفیف"),
    ("/product-orders", "🧾 سفارش‌های فروشگاه"),
    ("/admin-wallet-purchase", "🛒 خرید مدیریتی از کیف پول"),
    ("/web-server-orders", "🖥 سفارش‌های سرور وب"),
    ("/settings", "⚙️ تنظیمات درگاه"),
    ("/settings/zarinpal", "💳 زرین‌پال"),
    ("/settings/wallet-rate", "💱 نرخ تبدیل کیف پول"),
    ("/broadcast", "📢 پیام همگانی"),
]


def page(title: str, body: str, path: str = "") -> HTMLResponse:
    nav_html = "".join(
        f'<a class="nav-link{" active" if path == href else ""}" href="{href}"><span>{label}</span></a>'
        for href, label in NAV_ITEMS
    )
    html_doc = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} | Hetzner Shop Panel</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
{BASE_CSS}
</style>
</head>
<body>
<div class="bg-decor"><div class="cloud3"></div></div>
<div class="wrap">
  <div class="sidebar">
    <div class="brand"><span class="brand-icon">🌐</span><div><div class="brand-title">Hetzner Shop</div><div class="brand-sub">پنل مدیریت</div></div></div>
    <nav>{nav_html}</nav>
    <a class="logout" href="/logout">🚪 <span>خروج از حساب</span></a>
  </div>
  <div class="content">
    <div class="page-back"><a href="/admin" onclick="if(history.length>1){{history.back();return false}}">🔙 بازگشت به صفحه قبل</a></div>
    {body}
    {os.getenv("ENAMAD_WIDGET_HTML", "")}
  </div>
</div>
</body>
</html>"""
    return HTMLResponse(html_doc)


BASE_CSS = """
  * { box-sizing: border-box; }
  .page-back{display:flex;justify-content:flex-start;margin:0 0 16px}
  .page-back a{display:inline-flex;align-items:center;gap:8px;padding:9px 15px;border:1px solid var(--border);border-radius:12px;background:rgba(47,155,240,.10);color:var(--txt);text-decoration:none;font-weight:700}
  .page-back a:hover{background:rgba(47,155,240,.20)}
  :root {
    --bg-1:#0a1628; --bg-2:#0f2544; --card:#12294a; --card-2:#173a68; --border:#26466e;
    --txt:#eef4fc; --txt-dim:#93aed0; --accent:#2f9bf0; --accent-2:#5ec3ff; --accent-3:#8fe0ff;
    --green:#2ee6a8; --red:#ff5c72; --yellow:#ffc857; --pink:#7bc8f6;
  }
  html, body { height:100%; }
  body {
    font-family: 'Vazirmatn', Tahoma, sans-serif; color:var(--txt); margin:0;
    background: radial-gradient(ellipse at 12% 12%, rgba(94,195,255,.22), transparent 34%), radial-gradient(ellipse at 88% 24%, rgba(125,109,242,.20), transparent 32%), radial-gradient(ellipse at 60% 92%, rgba(46,230,168,.10), transparent 36%), linear-gradient(135deg, #050b18 0%, #0a1d38 38%, #102e55 72%, #07162b 100%); background-attachment:fixed;
  }
  .bg-decor { position:fixed; inset:0; pointer-events:none; z-index:0; overflow:hidden; background-image:radial-gradient(rgba(143,224,255,.13) 1px, transparent 1px); background-size:28px 28px; mask-image:linear-gradient(to bottom, black, transparent 88%); }
  .bg-decor::before, .bg-decor::after {
    content:""; position:absolute; border-radius:50%; filter:blur(4px); opacity:.9;
  }
  /* ابرهای محو و طبیعی در آسمان */
  .bg-decor::before {
    width:640px; height:180px; top:8%; left:-8%;
    background:radial-gradient(ellipse at center, rgba(255,255,255,.55), rgba(255,255,255,0) 72%);
    filter:blur(18px); animation: driftCloud1 55s linear infinite;
  }
  .bg-decor::after {
    width:560px; height:150px; top:38%; right:-10%;
    background:radial-gradient(ellipse at center, rgba(220,240,255,.4), rgba(220,240,255,0) 72%);
    filter:blur(22px); animation: driftCloud2 70s linear infinite;
  }
  .cloud3 {
    position:absolute; width:480px; height:130px; top:68%; left:20%;
    background:radial-gradient(ellipse at center, rgba(255,255,255,.28), rgba(255,255,255,0) 72%);
    filter:blur(20px); animation: driftCloud1 85s linear infinite reverse;
  }
  @keyframes driftCloud1 { 0% { transform:translateX(-6%); } 50% { transform:translateX(4%); } 100% { transform:translateX(-6%); } }
  @keyframes driftCloud2 { 0% { transform:translateX(4%); } 50% { transform:translateX(-6%); } 100% { transform:translateX(4%); } }
  @keyframes fadeInUp { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:translateY(0); } }
  .wrap { display:flex; min-height:100vh; position:relative; z-index:1; }
  .sidebar {
    width:256px; background:linear-gradient(180deg, rgba(10,22,40,.88), rgba(6,14,28,.94) 85%);
    backdrop-filter: blur(14px); padding:22px 16px; flex-shrink:0; border-left:1px solid var(--border);
    display:flex; flex-direction:column; position:sticky; top:0; height:100vh;
  }
  .brand { display:flex; align-items:center; gap:11px; padding:6px 8px 24px; }
  .brand-icon {
    font-size:22px; width:42px; height:42px; display:flex; align-items:center; justify-content:center;
    border-radius:13px; background:linear-gradient(135deg, var(--accent), var(--accent-2));
    box-shadow: 0 8px 22px -6px rgba(47,155,240,.7);
  }
  .brand-title { font-weight:800; font-size:16px; background:linear-gradient(90deg, #fff, var(--accent-3)); -webkit-background-clip:text; background-clip:text; color:transparent; }
  .brand-sub { font-size:11px; color:var(--txt-dim); margin-top:2px; }
  nav { display:flex; flex-direction:column; gap:4px; flex:1; }
  .nav-link {
    display:flex; align-items:center; padding:11px 14px; color:var(--txt-dim); text-decoration:none;
    border-radius:12px; font-size:13.5px; font-weight:500; transition: all .18s ease; border:1px solid transparent;
  }
  .nav-link:hover { background:var(--card-2); color:#fff; border-color:var(--border); transform:translateX(-2px); }
  .nav-link.active {
    background:linear-gradient(90deg, rgba(47,155,240,.32), rgba(94,195,255,.16));
    color:#fff; border-color:rgba(94,195,255,.5); box-shadow: inset 0 0 0 1px rgba(94,195,255,.22), 0 6px 16px -8px rgba(47,155,240,.55);
  }
  .logout { display:flex; align-items:center; gap:6px; margin-top:14px; padding:11px 14px; color:var(--red); text-decoration:none; font-size:13px; border-radius:12px; transition: background .15s; }
  .logout:hover { background:rgba(255,92,114,.12); }
  .content { flex:1; padding:34px 38px; max-width:1200px; animation: fadeInUp .35s ease; }
  h1 { font-size:22px; margin:0 0 20px; color:#fff; font-weight:800; display:flex; align-items:center; gap:10px; }
  h3 { font-size:14.5px; color:#fff; margin:0 0 14px; font-weight:700; display:flex; align-items:center; gap:8px; }
  .page-icon, .mini-icon {
    display:inline-flex; align-items:center; justify-content:center; border-radius:12px; flex-shrink:0;
  }
  .page-icon { width:38px; height:38px; font-size:19px; }
  .mini-icon { width:26px; height:26px; font-size:14px; border-radius:9px; }
  .icon-purple { background:linear-gradient(135deg, #6ba9ff, #3b6fd6); box-shadow:0 6px 16px -6px rgba(107,169,255,.7); }
  .icon-blue   { background:linear-gradient(135deg, #2f9bf0, #1c6fc2); box-shadow:0 6px 16px -6px rgba(47,155,240,.7); }
  .icon-teal   { background:linear-gradient(135deg, #4fd8e8, #1ea3b8); box-shadow:0 6px 16px -6px rgba(79,216,232,.6); }
  .icon-green  { background:linear-gradient(135deg, #2ee6a8, #17b88a); box-shadow:0 6px 16px -6px rgba(46,230,168,.6); }
  .icon-yellow { background:linear-gradient(135deg, #ffc857, #e5a52a); box-shadow:0 6px 16px -6px rgba(255,200,87,.6); }
  .icon-pink   { background:linear-gradient(135deg, #8fe0ff, #5ec3ff); box-shadow:0 6px 16px -6px rgba(143,224,255,.6); }
  .icon-red    { background:linear-gradient(135deg, #ff5c72, #d93a52); box-shadow:0 6px 16px -6px rgba(255,92,114,.6); }
  .card {
    background:linear-gradient(165deg, rgba(18,41,74,.88), rgba(15,37,68,.92) 260%);
    backdrop-filter: blur(10px);
    border-radius:18px; padding:22px 24px; margin-bottom:20px; border:1px solid var(--border);
    box-shadow: 0 10px 30px -16px rgba(0,10,25,.6); transition: box-shadow .2s ease, transform .2s ease;
    animation: fadeInUp .4s ease;
  }
  .card:hover { box-shadow: 0 16px 40px -18px rgba(0,10,25,.7); }
  table { width:100%; border-collapse: collapse; font-size:13px; }
  th, td { text-align:right; padding:11px 9px; border-bottom:1px solid var(--border); }
  th { color:var(--txt-dim); font-weight:700; font-size:11.5px; text-transform:uppercase; letter-spacing:.4px; }
  tbody tr { transition: background .15s ease; }
  tbody tr:hover { background:rgba(94,195,255,.07); }
  .btn {
    display:inline-block; background:linear-gradient(135deg, var(--accent-2), var(--accent));
    color:#04182e; border:none; padding:10px 20px; border-radius:11px; font-size:13.5px; font-weight:700;
    cursor:pointer; text-decoration:none; transition: transform .14s ease, box-shadow .14s ease, filter .14s ease; font-family:inherit;
    box-shadow: 0 8px 20px -8px rgba(94,195,255,.7);
  }
  .btn:hover { transform: translateY(-2px); filter:brightness(1.08); box-shadow: 0 12px 26px -8px rgba(94,195,255,.8); }
  .btn:active { transform: translateY(0); }
  .btn.small { padding:6px 12px; font-size:12px; margin:2px; box-shadow:none; border-radius:8px; color:#04182e; }
  .btn.red { background:linear-gradient(135deg, #ff7a8c, var(--red)); box-shadow:0 8px 20px -8px rgba(255,92,114,.6); color:#fff; }
  .btn.green { background:linear-gradient(135deg, #4cf0b8, var(--green)); box-shadow:0 8px 20px -8px rgba(46,230,168,.6); color:#04182e; }
  .btn.gray { background:linear-gradient(135deg, #6a86ad, #435878); box-shadow:none; color:#fff; }
  input, select, textarea {
    background:rgba(6,16,32,.55); border:1px solid var(--border); color:var(--txt); padding:11px 13px;
    border-radius:11px; font-size:13.5px; width:100%; margin-bottom:12px; font-family:inherit; transition: border-color .15s, box-shadow .15s;
  }
  input:focus, select:focus, textarea:focus { outline:none; border-color:var(--accent); box-shadow: 0 0 0 3px rgba(47,155,240,.18); }
  label { font-size:12.5px; color:var(--txt-dim); display:block; margin-bottom:5px; font-weight:600; }
  .stat-grid { display:flex; gap:18px; flex-wrap:wrap; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:18px; align-items:start; }
  .stat-box {
    background:linear-gradient(165deg, rgba(18,41,74,.88), rgba(15,37,68,.92)); backdrop-filter: blur(10px);
    border:1px solid var(--border); border-radius:18px;
    padding:20px 24px; min-width:165px; position:relative; overflow:hidden; box-shadow: 0 10px 30px -16px rgba(0,10,25,.6);
    display:flex; align-items:center; gap:14px; transition: transform .2s ease;
  }
  .stat-box:hover { transform: translateY(-3px); }
  .stat-box .stat-icon { font-size:22px; width:48px; height:48px; border-radius:14px; display:flex; align-items:center; justify-content:center; flex-shrink:0; }
  .stat-box .num { font-size:23px; color:#fff; font-weight:800; line-height:1.2; }
  .stat-box .lbl { font-size:12px; color:var(--txt-dim); margin-top:3px; }
  .badge { padding:4px 12px; border-radius:20px; font-size:11px; font-weight:700; }
  .badge.ok { background:rgba(46,230,168,.15); color:var(--green); }
  .badge.off { background:rgba(255,92,114,.15); color:var(--red); }
  .msg { padding:12px 18px; border-radius:12px; margin-bottom:18px; font-size:13px; font-weight:600; border:1px solid transparent; }
  .msg.ok { background:rgba(46,230,168,.1); color:var(--green); border-color:rgba(46,230,168,.25); }
  .msg.err { background:rgba(255,92,114,.1); color:var(--red); border-color:rgba(255,92,114,.25); }
  form.inline { display:inline; }
  .login-wrap { display:flex; align-items:center; justify-content:center; height:100vh; position:relative; z-index:1; }
  .login-box {
    background:linear-gradient(165deg, rgba(18,41,74,.9), rgba(15,37,68,.94)); backdrop-filter: blur(12px);
    padding:40px; border-radius:22px; width:350px;
    border:1px solid var(--border); box-shadow: 0 24px 70px -20px rgba(0,10,25,.7); animation: fadeInUp .4s ease;
  }

  /* Premium animated glass dashboard theme */
  :root { --bg-1:#050816; --bg-2:#0b1025; --card:#0d1730; --card-2:#151d42; --border:rgba(89,137,255,.26); --txt:#f4f6ff; --txt-dim:#a4b0d0; --accent:#6957ff; --accent-2:#20c8ff; --accent-3:#9deaff; --green:#19e6b3; --red:#ff547b; --yellow:#ffc45e; --pink:#d16bff; }
  html { scroll-behavior:smooth; }
  body { background:radial-gradient(ellipse at 8% 4%,rgba(104,65,255,.26),transparent 34%),radial-gradient(ellipse at 90% 18%,rgba(0,194,255,.18),transparent 31%),radial-gradient(ellipse at 52% 100%,rgba(214,48,255,.12),transparent 42%),linear-gradient(135deg,#030611 0%,#080d20 42%,#0b1230 72%,#040714 100%); background-size:160% 160%; animation:auroraShift 22s ease-in-out infinite; background-attachment:fixed; overflow-x:hidden; }
  @keyframes auroraShift { 0%,100%{background-position:0% 0%} 50%{background-position:100% 100%} }
  .bg-decor { opacity:.6; background-image:radial-gradient(rgba(121,173,255,.24) 1px,transparent 1px); background-size:34px 34px; }
  .bg-decor::before { width:60vw;height:38vw;top:-14%;left:-14%;background:radial-gradient(ellipse,rgba(98,61,255,.22),transparent 68%);filter:blur(45px);animation:orbFloat 18s ease-in-out infinite; }
  .bg-decor::after { width:55vw;height:36vw;right:-15%;top:35%;background:radial-gradient(ellipse,rgba(0,195,255,.16),transparent 68%);filter:blur(48px);animation:orbFloat 24s ease-in-out infinite reverse; }
  .cloud3 { width:50vw;height:30vw;left:30%;top:68%;background:radial-gradient(ellipse,rgba(194,54,255,.12),transparent 70%);filter:blur(50px);animation:orbFloat 28s ease-in-out infinite; }
  @keyframes orbFloat { 0%,100%{transform:translate3d(0,0,0) scale(1)} 50%{transform:translate3d(5vw,-2vh,0) scale(1.12)} }
  .sidebar { width:270px; padding:24px 16px; background:linear-gradient(180deg,rgba(7,12,30,.94),rgba(5,9,22,.91)); border-left:1px solid rgba(118,139,255,.24); box-shadow:-18px 0 60px rgba(0,0,0,.15); backdrop-filter:blur(24px); }
  .brand { padding:8px 9px 27px; border-bottom:1px solid rgba(130,151,255,.14); margin-bottom:18px; }
  .brand-icon { border-radius:15px; background:linear-gradient(135deg,#8b5cff,#315dff 55%,#15d9ff); box-shadow:0 0 28px rgba(101,89,255,.5); }
  .brand-title { background:linear-gradient(100deg,#fff,#a9baff 45%,#62e5ff); -webkit-background-clip:text; background-clip:text; }
  nav { gap:7px; }
  .nav-link { padding:12px 14px; border-radius:13px; border-color:transparent; position:relative; overflow:hidden; transition:all .24s ease; }
  .nav-link:hover { background:linear-gradient(100deg,rgba(108,83,255,.18),rgba(24,196,255,.08)); border-color:rgba(117,143,255,.22); box-shadow:0 8px 24px rgba(0,0,0,.12); transform:translateX(-3px); }
  .nav-link.active { background:linear-gradient(100deg,rgba(113,75,255,.36),rgba(30,176,255,.15)); border-color:rgba(139,130,255,.46); box-shadow:inset 3px 0 0 #8c70ff,0 8px 26px rgba(70,72,255,.12); }
  .content { flex:1; min-width:0; max-width:none; padding:34px clamp(18px,3vw,48px); animation:fadeInUp .45s ease both; }
  h1 { font-size:clamp(20px,2vw,27px); letter-spacing:-.4px; }
  .page-icon { background:linear-gradient(135deg,#885bff,#347dff 65%,#21d6ff); box-shadow:0 0 28px rgba(99,94,255,.32); }
  .card,.stat-box,.login-box { background:linear-gradient(145deg,rgba(17,27,57,.88),rgba(8,17,39,.88)); border:1px solid rgba(109,139,255,.24); backdrop-filter:blur(20px); box-shadow:0 18px 48px -28px rgba(0,0,0,.95),inset 0 1px 0 rgba(255,255,255,.055); }
  .card { border-radius:20px; padding:24px; position:relative; overflow:hidden; }
  .card::before { content:""; position:absolute; left:-30%; top:-75%; width:70%; height:140%; background:linear-gradient(90deg,transparent,rgba(127,145,255,.045),transparent); transform:rotate(25deg); pointer-events:none; }
  .card:hover { border-color:rgba(112,151,255,.5); box-shadow:0 22px 55px -30px rgba(28,82,255,.38),inset 0 1px 0 rgba(255,255,255,.06); }
  .stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,205px),1fr)); gap:16px; margin-bottom:22px; }
  .stat-box { min-width:0; border-radius:18px; padding:20px; overflow:hidden; }
  .stat-box:nth-child(4n+1) { border-top-color:rgba(156,111,255,.7); } .stat-box:nth-child(4n+2) { border-top-color:rgba(32,200,255,.7); } .stat-box:nth-child(4n+3) { border-top-color:rgba(25,230,179,.7); } .stat-box:nth-child(4n) { border-top-color:rgba(255,196,94,.7); }
  .stat-box:hover { transform:translateY(-5px); box-shadow:0 20px 38px -25px rgba(82,106,255,.65); }
  .stat-box .stat-icon { box-shadow:0 0 25px rgba(101,110,255,.18); } .stat-box .num { background:linear-gradient(90deg,#fff,#b9d7ff); -webkit-background-clip:text; background-clip:text; color:transparent; }
  .icon-purple { background:linear-gradient(135deg,#a16bff,#6744ff); } .icon-blue { background:linear-gradient(135deg,#23d7ff,#3376ff); } .icon-teal { background:linear-gradient(135deg,#36f0dc,#009eaf); } .icon-pink { background:linear-gradient(135deg,#f084ff,#a44cff); }
  .btn { background:linear-gradient(115deg,#9a6bff,#5266ff 55%,#20c8ff); color:#fff; box-shadow:0 8px 26px -10px rgba(108,93,255,.8); } .btn:hover { box-shadow:0 12px 32px -10px rgba(32,200,255,.55); } .btn.small { color:#fff; }
  input,select,textarea { background:rgba(4,9,25,.68); border-color:rgba(110,141,255,.28); } input:focus,select:focus,textarea:focus { border-color:#8c83ff; box-shadow:0 0 0 3px rgba(112,91,255,.2); }
  th,td { border-bottom-color:rgba(109,139,255,.16); } tbody tr:hover { background:rgba(102,117,255,.09); } .login-box { border-radius:24px; } .logout:hover { background:rgba(255,84,123,.13); }
  .content > h1 { padding-bottom:12px; border-bottom:1px solid rgba(126,151,255,.14); }
  @media (min-width:1500px) { .content { padding:42px clamp(34px,3.5vw,68px); } }
  @media (max-width:1050px) { .sidebar { width:230px; } .content { padding:26px 22px; } .stat-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
  @media (max-width:760px) { .wrap { flex-direction:column; } .sidebar { position:relative; width:100%; height:auto; padding:12px 12px 8px; border-left:0; border-bottom:1px solid rgba(118,139,255,.25); } .brand { padding:3px 5px 12px; margin-bottom:10px; } nav { flex-direction:row; gap:7px; overflow-x:auto; overflow-y:hidden; padding:2px 1px 9px; scrollbar-width:thin; } .nav-link { flex:0 0 auto; white-space:nowrap; padding:9px 12px; font-size:12px; } .logout { align-self:flex-start; margin-top:0; padding:7px 10px; } .content { width:100%; padding:18px 12px 28px; } h1 { font-size:19px; } .card { padding:15px; border-radius:16px; } .stat-grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; } .stat-box { padding:13px 11px; gap:9px; border-radius:14px; } .stat-box .stat-icon { width:36px;height:36px;font-size:17px; } .stat-box .num { font-size:18px;overflow-wrap:anywhere; } .stat-box .lbl { font-size:10.5px; } .grid { grid-template-columns:minmax(0,1fr);gap:12px; } table { min-width:650px; } .login-wrap { min-height:100dvh;height:auto;padding:18px 0; } .login-box { width:min(390px,calc(100vw - 24px));padding:27px 20px; } }
  @media (max-width:390px) { .content { padding:15px 9px 22px; } .stat-grid { grid-template-columns:minmax(0,1fr); } .card { padding:12px; } .nav-link { padding:8px 10px; } }
  @media (prefers-reduced-motion:reduce) { *,*::before,*::after { animation-duration:.01ms !important;animation-iteration-count:1 !important;scroll-behavior:auto !important;transition-duration:.01ms !important; } }

  .dashboard-hero { min-height:164px; margin:0 0 22px; padding:27px clamp(18px,3vw,32px); display:flex; align-items:center; justify-content:space-between; gap:20px; position:relative; overflow:hidden; border:1px solid rgba(117,145,255,.38); border-radius:22px; background:linear-gradient(105deg,rgba(17,27,67,.96),rgba(33,36,95,.74) 48%,rgba(8,40,75,.9)),radial-gradient(ellipse at 75% 50%,rgba(31,206,255,.25),transparent 48%); box-shadow:0 20px 48px -30px rgba(68,81,255,.7),inset 0 1px 0 rgba(255,255,255,.08); }
  .dashboard-hero::before { content:""; position:absolute; inset:0; background:linear-gradient(115deg,transparent 25%,rgba(130,120,255,.10),transparent 65%); transform:translateX(-100%); animation:heroShine 9s ease-in-out infinite; pointer-events:none; }
  @keyframes heroShine { 0%,35%{transform:translateX(-100%)} 70%,100%{transform:translateX(100%)} }
  .hero-copy { position:relative; z-index:1; } .hero-kicker { color:#83e7ff; font-size:10px; font-weight:800; letter-spacing:2px; }
  .hero-copy h2 { margin:10px 0 7px; font-size:clamp(21px,2.7vw,32px); line-height:1.4; color:#fff; } .hero-copy p { margin:0; font-size:13px; color:#bac8ee; }
  .hero-art { width:124px; height:105px; flex:0 0 124px; position:relative; display:grid; place-items:center; }
  .hero-server { width:64px;height:64px;display:grid;place-items:center;border-radius:19px;font-size:34px;color:#dffaff;background:linear-gradient(145deg,rgba(43,202,255,.32),rgba(112,75,255,.35));border:1px solid rgba(112,220,255,.65);box-shadow:0 0 32px rgba(44,174,255,.35);animation:serverFloat 4s ease-in-out infinite; }
  .hero-art > span { position:absolute; top:0; right:18px; color:#d5b7ff; font-size:24px; animation:twinkle 3s ease-in-out infinite; }
  .hero-orbit { position:absolute; width:104px;height:58px;border:1px solid rgba(90,204,255,.45);border-radius:50%;transform:rotate(-28deg);box-shadow:0 0 18px rgba(58,140,255,.15); }
  @keyframes serverFloat { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-6px)} } @keyframes twinkle { 0%,100%{opacity:.45;transform:scale(.85)} 50%{opacity:1;transform:scale(1.15)} }
  @media (max-width:760px) { .dashboard-hero { min-height:140px; padding:20px 16px; border-radius:17px; } .hero-copy p { max-width:30ch; line-height:1.9; } .hero-art { width:76px; height:80px; flex-basis:76px; } .hero-server { width:48px;height:48px;font-size:27px;border-radius:14px; } .hero-orbit { width:72px;height:44px; } .hero-art > span { right:0; top:2px; } }
  @media (max-width:390px) { .dashboard-hero { align-items:flex-start; } .hero-art { display:none; } }


  /* Reference-matched neon dashboard skin; all routes and business logic stay unchanged */
  .topbar-mini { display:flex;justify-content:space-between;align-items:center;gap:12px;margin:0 0 14px;padding:7px 2px;color:#a9caff;font-size:11px; }.topbar-date {display:flex;align-items:center;gap:8px;}.topbar-sep {color:#3964a3;}.live-dot {width:7px;height:7px;border-radius:50%;background:#00f0bf;box-shadow:0 0 10px #00f0bf;display:inline-block;animation:livePulse 2s infinite;}@keyframes livePulse{50%{opacity:.45;box-shadow:0 0 2px #00f0bf;}}

  :root { --bg-1:#02091b; --bg-2:#06142e; --card:#071b3a; --card-2:#0b2550; --border:rgba(27,105,230,.48); --txt:#f4f7ff; --txt-dim:#a6c7f5; --accent:#5c36ff; --accent-2:#00c8ff; --accent-3:#65eaff; --green:#00f0c8; --red:#ff4f8b; --yellow:#ffbd24; --pink:#d42cff; }
  body { background:radial-gradient(ellipse at 35% -10%,rgba(22,87,226,.28),transparent 45%),radial-gradient(ellipse at 100% 90%,rgba(139,20,255,.18),transparent 35%),linear-gradient(130deg,#010616,#03112a 55%,#020719); background-attachment:fixed; }
  .bg-decor { opacity:.65; background-image:radial-gradient(rgba(46,143,255,.24) .8px,transparent .8px); background-size:26px 26px; mask-image:linear-gradient(to bottom,black,transparent 94%); }
  .bg-decor::before { width:65vw;height:30vw;top:-14%;left:-15%;background:radial-gradient(ellipse,rgba(55,38,255,.24),transparent 70%);filter:blur(48px);animation:nebula 20s ease-in-out infinite; }
  .bg-decor::after { width:50vw;height:40vw;top:50%;right:-15%;background:radial-gradient(ellipse,rgba(0,174,255,.14),transparent 70%);filter:blur(55px);animation:nebula 25s ease-in-out infinite reverse; }
  .cloud3 { width:50vw;height:32vw;left:25%;top:80%;background:radial-gradient(ellipse,rgba(190,0,255,.14),transparent 70%);filter:blur(50px);animation:nebula 28s ease-in-out infinite; }
  @keyframes nebula { 0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(4vw,-3vh) scale(1.12)} }
  .wrap { direction:ltr; gap:0; }
  .sidebar { direction:rtl; width:236px; padding:18px 14px; background:linear-gradient(180deg,rgba(2,11,31,.97),rgba(1,8,23,.96));border-left:1px solid rgba(0,150,255,.35);border-right:0;box-shadow:12px 0 42px rgba(0,0,0,.2); }
  .brand { padding:7px 6px 22px; border-bottom:1px solid rgba(41,115,255,.2); margin-bottom:14px; }
  .brand-icon { background:linear-gradient(135deg,#ae40ff,#234eff 60%,#00d9ff);border:1px solid rgba(70,205,255,.5);box-shadow:0 0 28px rgba(63,74,255,.6); }
  .brand-title { text-transform:uppercase;letter-spacing:.5px;background:linear-gradient(90deg,#ff316f,#ff5277 48%,#fff);-webkit-background-clip:text;background-clip:text; }
  nav { gap:5px; }
  .nav-link { color:#d4e5ff;padding:11px 12px;border-radius:11px;font-size:13px;border:1px solid transparent;transition:all .22s ease; }
  .nav-link::before { content:'›';color:#30baff;font-size:20px;margin-left:9px;line-height:12px; }
  .nav-link:hover { background:linear-gradient(90deg,rgba(37,68,255,.28),rgba(0,192,255,.08));border-color:rgba(38,136,255,.3);transform:translateX(-2px); }
  .nav-link.active { color:white;background:linear-gradient(100deg,rgba(44,55,255,.88),rgba(0,136,255,.5));border-color:rgba(70,151,255,.75);box-shadow:0 6px 24px rgba(26,72,255,.25),inset 0 0 0 1px rgba(255,255,255,.06); }
  .nav-link.active::before { color:#fff; }
  .logout { margin-top:12px;color:#ff7ca9; }
  .content { direction:rtl;flex:1;min-width:0;max-width:none;padding:20px clamp(14px,1.8vw,28px) 32px; }
  .content > h1 { border:0;margin:0 0 15px;padding:0;font-size:21px; }
  .page-icon { background:linear-gradient(135deg,#3b55ff,#00bfff);box-shadow:0 0 22px rgba(0,153,255,.35); }
  .card,.stat-box,.login-box { background:linear-gradient(145deg,rgba(3,24,56,.94),rgba(2,14,38,.96));border:1px solid rgba(0,113,255,.48);box-shadow:0 12px 28px rgba(0,0,0,.18),inset 0 1px 0 rgba(93,171,255,.08);backdrop-filter:blur(18px); }
  .card { border-radius:16px;padding:19px;margin-bottom:16px; }
  .stat-grid { display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:17px; }
  .stat-box { min-width:0;min-height:126px;position:relative;overflow:hidden;border-radius:16px;padding:16px;display:flex;align-items:flex-start;gap:13px;transition:transform .25s,border-color .25s,box-shadow .25s; }
  .stat-box::after { content:'';position:absolute;left:-30%;right:-30%;bottom:-38px;height:60px;border-top:1.5px solid var(--glow,#00d9ff);border-radius:50%;filter:drop-shadow(0 0 5px var(--glow,#00d9ff));opacity:.75;transform:rotate(-5deg); }
  .stat-box:nth-child(1){--glow:#00ffcf;border-color:rgba(0,255,207,.65);background:linear-gradient(135deg,rgba(0,118,104,.28),rgba(1,23,49,.96));}
  .stat-box:nth-child(2){--glow:#00c8ff;border-color:rgba(0,168,255,.7);background:linear-gradient(135deg,rgba(0,75,154,.3),rgba(1,23,49,.96));}
  .stat-box:nth-child(3){--glow:#e12cff;border-color:rgba(206,44,255,.7);background:linear-gradient(135deg,rgba(101,19,147,.3),rgba(1,23,49,.96));}
  .stat-box:nth-child(4){--glow:#ffc21e;border-color:rgba(255,183,0,.7);background:linear-gradient(135deg,rgba(143,91,0,.28),rgba(1,23,49,.96));}
  .stat-box:nth-child(n+5) { display:none; }
  .stat-box:hover { transform:translateY(-4px);box-shadow:0 15px 32px rgba(0,88,255,.16); }
  .stat-box .stat-icon { flex:0 0 52px;width:52px;height:52px;border-radius:14px;display:grid;place-items:center;font-size:24px;border:1px solid currentColor;box-shadow:0 0 22px rgba(0,194,255,.2); }
  .icon-purple { background:linear-gradient(135deg,rgba(177,31,255,.4),rgba(60,25,255,.5));color:#e4b5ff; }
  .icon-blue { background:linear-gradient(135deg,rgba(0,196,255,.32),rgba(0,78,255,.55));color:#6be9ff; }
  .icon-teal,.icon-green { background:linear-gradient(135deg,rgba(0,255,205,.26),rgba(0,133,119,.45));color:#5affdf; }
  .icon-yellow { background:linear-gradient(135deg,rgba(255,188,30,.34),rgba(190,108,0,.48));color:#ffe18a; }
  .icon-pink { background:linear-gradient(135deg,rgba(211,37,255,.35),rgba(104,20,255,.48));color:#f3a8ff; }
  .stat-box .num { font-size:clamp(20px,1.7vw,28px);font-weight:800;color:#68eaff;line-height:1.45;overflow-wrap:anywhere; }
  .stat-box:nth-child(3) .num { color:#f1a5ff; }.stat-box:nth-child(4) .num { color:#ffe09a;font-size:clamp(16px,1.4vw,23px); }
  .stat-box .lbl { color:#b8d6ff;font-size:12px; }
  .dashboard-hero { min-height:165px;margin:0 0 17px;padding:25px 28px;display:flex;align-items:center;position:relative;overflow:hidden;border:1px solid rgba(0,133,255,.65);border-radius:17px;background:linear-gradient(90deg,rgba(1,12,38,.22),rgba(2,10,34,.04) 48%,rgba(2,9,30,.05)),url('/static/dashboard-banner.jpg') center 48%/cover no-repeat;box-shadow:0 10px 30px rgba(0,25,92,.3),inset 0 0 35px rgba(0,18,74,.2); }
  .dashboard-hero::before { content:'';position:absolute;inset:0;background:linear-gradient(90deg,rgba(1,8,29,.8),rgba(1,8,29,.48) 42%,transparent 100%);pointer-events:none; }
  .hero-copy { position:relative;z-index:1;max-width:72%; }.hero-kicker { color:#7ceaff;font-size:10px;font-weight:800;letter-spacing:2px;text-shadow:0 0 12px #009dff; }
  .hero-copy h2 { margin:9px 0 8px;font-size:clamp(20px,2vw,30px);line-height:1.6;color:#fff;text-shadow:0 2px 15px #00123c; }.hero-copy p { margin:0;color:#dcecff;font-size:13px;text-shadow:0 2px 10px #00123c; }
  .hero-art { display:none; }
  .dashboard-columns { display:grid;grid-template-columns:minmax(0,1.6fr) minmax(230px,.8fr) minmax(220px,.72fr);gap:15px;align-items:start; }
  .dashboard-main,.dashboard-middle,.dashboard-side { min-width:0;display:flex;flex-direction:column;gap:15px; }
  .dash-card-title { display:flex;align-items:center;justify-content:space-between;gap:10px;font-weight:800;font-size:14px;margin-bottom:16px;color:#f3f7ff; }
  .dash-pill { font-size:10px;border-radius:20px;padding:4px 9px;color:#5bffe0;background:rgba(0,228,188,.1);border:1px solid rgba(0,228,188,.26);white-space:nowrap; }
  .server-visual { height:170px;position:relative;display:flex;align-items:flex-end;justify-content:space-around;padding:15px 8px 0;border-bottom:1px solid rgba(0,151,255,.45);background:repeating-linear-gradient(to bottom,transparent 0,transparent 37px,rgba(36,113,255,.12) 38px);overflow:hidden; }
  .server-visual::after { content:'';position:absolute;inset:10px 0 0;background:linear-gradient(180deg,rgba(0,206,255,.03),rgba(0,206,255,.08));clip-path:polygon(0 75%,12% 55%,25% 64%,39% 38%,52% 53%,66% 20%,80% 40%,100% 32%,100% 100%,0 100%);border-top:2px solid #00e9e0;filter:drop-shadow(0 0 8px #00d9ff); }
  .server-bar { width:8%;max-width:38px;min-width:13px;z-index:1;border-radius:5px 5px 0 0;background:linear-gradient(180deg,#00f1da,#1476ff 90%);box-shadow:0 0 13px rgba(0,194,255,.35);animation:barPulse 5s ease-in-out infinite alternate; }
  .server-bar:nth-child(even){background:linear-gradient(180deg,#dc2dff,#5b39ff);height:36%!important;animation-delay:-2s;}
  @keyframes barPulse {from{filter:brightness(.75)}to{filter:brightness(1.25)}}
  .legend-row { display:flex;gap:16px;flex-wrap:wrap;color:#c8ddff;font-size:11px;margin-top:12px; }.legend-dot { display:inline-block;width:8px;height:8px;border-radius:50%;margin-left:5px;background:#00efd0;box-shadow:0 0 8px currentColor; }.legend-dot.purple { background:#b52aff; }
  .gauge-wrap { display:grid;place-items:center;padding:5px 0 12px; }.gauge { width:144px;height:144px;border-radius:50%;display:grid;place-items:center;position:relative;background:conic-gradient(#00f3d5 0 42%,#00c8ff 42% 65%,#583cff 65% 82%,rgba(19,69,135,.35) 82% 100%);box-shadow:0 0 24px rgba(0,177,255,.17); }
  .gauge::before { content:'';position:absolute;inset:10px;border-radius:50%;background:#04132d;border:1px solid rgba(0,191,255,.2); }.gauge-center { position:relative;text-align:center;font-size:22px;font-weight:800;color:#7ffaff; }.gauge-center small { display:block;font-size:10px;font-weight:500;color:#c8dfff; }
  .quick-list,.activity-list { display:flex;flex-direction:column;gap:8px; }.quick-link,.activity-item { display:flex;align-items:center;gap:10px;min-width:0;padding:10px;border-radius:12px;text-decoration:none;color:#eaf3ff;background:linear-gradient(100deg,rgba(23,55,150,.42),rgba(4,26,60,.72));border:1px solid rgba(37,109,255,.24);transition:all .2s; }.quick-link:hover { transform:translateX(-3px);border-color:rgba(0,203,255,.6);background:linear-gradient(100deg,rgba(57,45,190,.55),rgba(4,43,80,.8)); }
  .quick-icon { width:38px;height:38px;flex:0 0 38px;display:grid;place-items:center;border-radius:10px;font-size:18px;background:linear-gradient(135deg,rgba(0,190,255,.25),rgba(88,52,255,.45));border:1px solid rgba(0,184,255,.45); }.quick-link strong,.activity-item strong { display:block;font-size:11px; }.quick-link small,.activity-item small { display:block;color:#a9c8f5;font-size:10px;margin-top:3px; }
  .activity-item { padding:9px;background:rgba(7,33,72,.7); }.activity-icon { width:32px;height:32px;flex:0 0 32px;display:grid;place-items:center;border-radius:9px;background:linear-gradient(135deg,rgba(0,221,184,.3),rgba(0,91,255,.35)); }
  .recent-table { overflow:auto; } .recent-table table { min-width:510px;font-size:11px; }.recent-table th,.recent-table td { padding:10px 7px; }.recent-table .btn { padding:5px 8px;font-size:10px; }
  .side-promo { min-height:190px;background:linear-gradient(180deg,rgba(2,10,34,.15),rgba(2,10,34,.75)),url('/static/dashboard-brand.jpg') center/cover no-repeat;border-color:rgba(139,38,255,.5);display:flex;flex-direction:column;justify-content:flex-end;text-align:center; }.side-promo h3 { justify-content:center;font-size:17px;text-shadow:0 0 12px #c13cff; }.side-promo p {font-size:11px;color:#d8dfff;margin:0;}
  .system-list { display:grid;grid-template-columns:1fr auto;gap:0;font-size:11px; }.system-list span { padding:10px 0;border-bottom:1px solid rgba(40,104,196,.2); }.system-list span:nth-child(even){color:#74eaff;text-align:left;}
  .btn { background:linear-gradient(110deg,#6937ff,#215eff 60%,#00bce8);border:1px solid rgba(103,178,255,.32);box-shadow:0 5px 18px rgba(42,72,255,.2); }.btn:hover { filter:brightness(1.15);box-shadow:0 8px 22px rgba(0,157,255,.22); }
  input,select,textarea { background:rgba(1,9,28,.78);border-color:rgba(30,112,230,.48);color:#f2f7ff; } input:focus,select:focus,textarea:focus { border-color:#00caff;box-shadow:0 0 0 3px rgba(0,185,255,.14); }
  th,td { border-bottom-color:rgba(31,104,214,.24); } tbody tr:hover { background:rgba(0,143,255,.08); }
  @media (min-width:1500px) { .sidebar { width:250px; } .dashboard-columns { grid-template-columns:minmax(0,1.65fr) minmax(250px,.8fr) minmax(230px,.72fr); } }
  @media (max-width:1250px) { .sidebar { width:215px; }.dashboard-columns { grid-template-columns:minmax(0,1.4fr) minmax(220px,.8fr); }.dashboard-side { grid-column:1/-1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr)); }.side-promo { min-height:210px; } }
  @media (max-width:850px) { .wrap { flex-direction:column; }.sidebar { position:relative;width:100%;height:auto;padding:10px 12px 7px;border:0;border-bottom:1px solid rgba(0,150,255,.35); }.brand { padding:2px 4px 10px;margin-bottom:8px; }.brand-icon { width:35px;height:35px;font-size:18px; }.brand-title {font-size:15px;} nav { flex-direction:row;overflow-x:auto;overflow-y:hidden;gap:6px;padding:2px 0 8px;scrollbar-width:thin; } .nav-link { flex:0 0 auto;white-space:nowrap;padding:8px 10px;font-size:12px; }.nav-link::before {display:none;}.logout {margin-top:3px;padding:7px 10px;align-self:flex-start;}.content {width:100%;padding:16px 12px 26px;}.dashboard-columns {grid-template-columns:minmax(0,1fr) minmax(0,1fr);}.dashboard-main {grid-column:1/-1;}.dashboard-side {grid-column:auto;display:flex;}.stat-grid {grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;}.stat-box {min-height:110px;padding:13px;}.dashboard-hero {min-height:150px;background-position:center;}.hero-copy {max-width:88%;} }
  @media (max-width:560px) { .content {padding:13px 10px 24px;}.content>h1 {font-size:18px;}.dashboard-hero {min-height:145px;padding:18px 15px;background-position:56% center;}.dashboard-hero::before {background:linear-gradient(90deg,rgba(1,8,29,.88),rgba(1,8,29,.62) 70%,rgba(1,8,29,.25));}.hero-copy {max-width:100%;}.hero-copy h2 {font-size:20px;}.hero-copy p {font-size:11px;line-height:1.9;}.hero-kicker {font-size:8px;letter-spacing:1.3px;}.stat-box {gap:9px;padding:11px;min-height:100px;}.stat-box .stat-icon {width:37px;height:37px;flex-basis:37px;font-size:18px;border-radius:10px;}.stat-box .num {font-size:20px;}.stat-box .lbl {font-size:10px;}.dashboard-columns {grid-template-columns:minmax(0,1fr);}.dashboard-main,.dashboard-middle,.dashboard-side {grid-column:auto;}.server-visual {height:135px;}.gauge {width:128px;height:128px;}.card {padding:14px;border-radius:14px;}.side-promo {min-height:180px;} }
  @media (max-width:350px) { .stat-grid {grid-template-columns:minmax(0,1fr);}.stat-box {min-height:80px;}.stat-box:nth-child(n+5){display:flex;} }
  @media (prefers-reduced-motion:reduce) { *,*::before,*::after {animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important;scroll-behavior:auto!important;} }


"""


def esc(v) -> str:
    return html.escape(str(v)) if v is not None else "-"


def get_session(request: Request):
    token = request.cookies.get("panel_session")
    if not token or token not in SESSIONS:
        return None
    sess = SESSIONS[token]
    if datetime.utcnow() > sess["expires"]:
        SESSIONS.pop(token, None)
        return None
    return sess


# ---------------------------------------------------------------------------
# ورود / خروج
# ---------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    err = request.query_params.get("err")
    msg = '<div class="msg err">نام کاربری یا رمز عبور اشتباه است.</div>' if err else ""
    body = f"""
    <div class="login-wrap"><div class="login-box">
    <div style="text-align:center;margin-bottom:22px;">
      <div style="font-size:34px;filter:drop-shadow(0 0 12px rgba(124,109,242,.6));">🌐</div>
      <div style="font-weight:800;font-size:18px;margin-top:8px;background:linear-gradient(90deg,var(--accent-2),var(--accent-3));-webkit-background-clip:text;background-clip:text;color:transparent;">Hetzner Shop Panel</div>
      <div style="color:var(--txt-dim);font-size:12.5px;margin-top:4px;">ورود به پنل مدیریت</div>
    </div>
    {msg}
    <form method="post" action="/login">
      <label>نام کاربری</label>
      <input name="username" required autofocus>
      <label>رمز عبور</label>
      <input name="password" type="password" required>
      <button class="btn" style="width:100%;margin-top:8px;" type="submit">ورود به پنل</button>
    </form>
    </div></div>
    """
    return HTMLResponse(
        f"<html lang='fa' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>ورود</title><link rel='preconnect' href='https://fonts.googleapis.com'>"
        f"<link href='https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700;800&display=swap' rel='stylesheet'>"
        f"<style>{BASE_CSS}</style></head><body><div class='bg-decor'><div class='cloud3'></div></div>{body}</body></html>"
    )


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    if not PANEL_PASSWORD:
        return RedirectResponse("/login?err=1", status_code=303)
    if username == PANEL_USERNAME and password == PANEL_PASSWORD:
        token = secrets.token_hex(24)
        SESSIONS[token] = {"user": username, "expires": datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)}
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie("panel_session", token, httponly=True, max_age=SESSION_TTL_HOURS * 3600)
        return resp
    return RedirectResponse("/login?err=1", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("panel_session")
    SESSIONS.pop(token, None)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("panel_session")
    return resp


# ---------------------------------------------------------------------------
# احراز هویت مشتریان: تلگرام و ایمیل
# ---------------------------------------------------------------------------
@app.get("/admin", response_class=HTMLResponse)
async def dashboard(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        users_c = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        servers_c = (await (await db.execute("SELECT COUNT(*) FROM servers")).fetchone())[0]
        orders_c = (await (await db.execute("SELECT COUNT(*) FROM orders")).fetchone())[0]
        revenue = (await (await db.execute("SELECT COALESCE(SUM(price),0) FROM orders")).fetchone())[0]
        pending = (await (await db.execute("SELECT COUNT(*) FROM wallet_requests WHERE status='pending'")).fetchone())[0]
        token = await core.get_active_token(db)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name, ip, status, created_at, id FROM servers ORDER BY id DESC LIMIT 5")
        recent_servers = await cur.fetchall()
        cur = await db.execute("SELECT status, COUNT(*) FROM servers GROUP BY status")
        status_counts = {str(row[0] or "unknown").lower(): row[1] for row in await cur.fetchall()}
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE COALESCE(is_banned,0)=0")
        active_users = (await cur.fetchone())[0]
    server_rows = "".join(
        f"<tr><td>{i:02d}</td><td>{esc(row[0])}</td><td>{esc(row[1])}</td>"
        f"<td><span class='badge {'ok' if str(row[2] or '').lower() in ('running','active','on','ready') else 'off'}'>{'فعال' if str(row[2] or '').lower() in ('running','active','on','ready') else esc(row[2] or 'نامشخص')}</span></td>"
        f"<td>{esc((row[3] or '-')[:10])}</td><td><a class='btn small' href='/servers'>مدیریت</a></td></tr>"
        for i, row in enumerate(recent_servers, 1)
    ) or "<tr><td colspan='6'>هنوز سروری ثبت نشده است.</td></tr>"
    running_count = sum(v for k,v in status_counts.items() if k in ('running','active','on','ready'))
    stopped_count = max(0, servers_c-running_count)
    bars = ''.join(f"<span class='server-bar' style='height:{height}%'></span>" for height in (35,51,43,67,56,78,62,88,73,82))
    body = f"""
    <div class="topbar-mini"><div class="topbar-date"><span class="live-dot"></span> مرکز کنترل مدیریت <span class="topbar-sep">/</span> وضعیت زنده پنل</div><a class="btn small" href="/admin">⟳ بروزرسانی</a></div>
    <h1><span class="page-icon icon-purple">⌂</span> داشبورد</h1>
    <section class="dashboard-hero"><div class="hero-copy"><span class="hero-kicker">HETZNER SHOP · CONTROL CENTER</span><h2>به پنل مدیریت Hetzner خوش آمدید 👑</h2><p>مدیریت سرورها، کاربران و همه سرویس‌ها؛ همه‌چیز در یک نگاه.</p></div></section>
    <div class="stat-grid">
      <div class="stat-box"><div class="stat-icon icon-teal">▤</div><div><div class="num">{servers_c}</div><div class="lbl">کل سرورها</div></div></div>
      <div class="stat-box"><div class="stat-icon icon-blue">♙</div><div><div class="num">{active_users}</div><div class="lbl">کاربران فعال</div></div></div>
      <div class="stat-box"><div class="stat-icon icon-pink">▣</div><div><div class="num">{orders_c}</div><div class="lbl">کل سفارش‌ها</div></div></div>
      <div class="stat-box"><div class="stat-icon icon-yellow">＄</div><div><div class="num">{core.fmt_price(revenue)}</div><div class="lbl">درآمد کل</div></div></div>
    </div>
    <div class="dashboard-columns">
      <div class="dashboard-main">
        <section class="card"><div class="dash-card-title"><span>◈ وضعیت سرورها</span><span class="dash-pill">{servers_c} سرور ثبت‌شده</span></div><div class="server-visual" aria-label="نمای گرافیکی سرورها">{bars}</div><div class="legend-row"><span><i class="legend-dot"></i> فعال: {running_count}</span><span><i class="legend-dot purple"></i> متوقف یا نامشخص: {stopped_count}</span></div></section>
        <section class="card"><div class="dash-card-title"><span>▤ آخرین سرورها</span><a class="btn small" href="/servers">مشاهده همه</a></div><div class="recent-table"><table><thead><tr><th>#</th><th>نام سرور</th><th>IP آدرس</th><th>وضعیت</th><th>تاریخ</th><th>عملیات</th></tr></thead><tbody>{server_rows}</tbody></table></div></section>
        <section class="card"><div class="dash-card-title"><span>▣ اطلاعات سیستم</span><span class="dash-pill">آنلاین</span></div><div class="system-list"><span>نسخه پنل</span><span>Hetzner Shop</span><span>تعداد کاربران</span><span>{users_c}</span><span>تعداد سرورها</span><span>{servers_c}</span><span>درخواست شارژ در انتظار</span><span>{pending}</span><span>اتصال API</span><span>{'متصل' if token else 'تنظیم نشده'}</span></div></section>
      </div>
      <div class="dashboard-middle">
        <section class="card"><div class="dash-card-title"><span>◉ وضعیت کلی</span></div><div class="gauge-wrap"><div class="gauge"><div class="gauge-center">{round(running_count*100/servers_c) if servers_c else 0}%<small>سرورهای فعال</small></div></div></div><div class="legend-row"><span><i class="legend-dot"></i> فعال {running_count}</span><span><i class="legend-dot purple"></i> دیگر {stopped_count}</span></div></section>
        <section class="card"><div class="dash-card-title"><span>⚡ عملیات سریع</span></div><div class="quick-list"><a class="quick-link" href="/servers"><span class="quick-icon">☁</span><span><strong>مدیریت سرورها</strong><small>مشاهده و مدیریت سرورها</small></span></a><a class="quick-link" href="/users"><span class="quick-icon">♙</span><span><strong>مدیریت کاربران</strong><small>فهرست کاربران و حساب‌ها</small></span></a><a class="quick-link" href="/providers"><span class="quick-icon">▣</span><span><strong>مدیریت APIها</strong><small>افزودن و تنظیم API جدید</small></span></a><a class="quick-link" href="/backup"><span class="quick-icon">⬡</span><span><strong>تنظیمات بکاپ</strong><small>پشتیبان‌گیری و عضویت اجباری</small></span></a></div></section>
      </div>
      <div class="dashboard-side">
        <section class="card side-promo"><h3>HETZNER SHOP</h3><p>Powering Your Dreams · Fast · Secure · Reliable</p></section>
        <section class="card"><div class="dash-card-title"><span>◷ آخرین فعالیت‌ها</span></div><div class="activity-list"><div class="activity-item"><span class="activity-icon">✦</span><span><strong>وضعیت پنل</strong><small>پنل مدیریت آماده استفاده است</small></span></div><div class="activity-item"><span class="activity-icon">▤</span><span><strong>سرورها</strong><small>{servers_c} سرور در پایگاه داده</small></span></div><div class="activity-item"><span class="activity-icon">♙</span><span><strong>کاربران</strong><small>{users_c} حساب ثبت‌شده</small></span></div><div class="activity-item"><span class="activity-icon">◈</span><span><strong>بکاپ و امنیت</strong><small>تنظیمات را از بخش بکاپ بررسی کنید</small></span></div></div></section>
      </div>
    </div>
    """
    return page("داشبورد", body, "/admin")


# ---------------------------------------------------------------------------
# کاربران
# ---------------------------------------------------------------------------
@app.get("/users", response_class=HTMLResponse)
async def users_list(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT telegram_id, username, full_name, balance, is_banned FROM users ORDER BY telegram_id DESC LIMIT 300"
        )
        users = await cur.fetchall()
    rows = "".join(
        f"<tr><td>{u[0]}</td><td>{esc(u[2])}</td><td>@{esc(u[1])}</td><td>{core.fmt_price(u[3])}</td>"
        f"<td>{'<span class=badge off>مسدود</span>' if u[4] else '<span class=badge ok>عادی</span>'}</td>"
        f"<td><a class='btn small' href='/users/{u[0]}'>مدیریت</a></td></tr>"
        for u in users
    )
    body = f"""
    <h1><span class="page-icon icon-purple">👥</span> کاربران ({len(users)})</h1>
    <div class="card"><table>
      <tr><th>شناسه</th><th>نام</th><th>یوزرنیم</th><th>موجودی</th><th>وضعیت</th><th></th></tr>
      {rows or "<tr><td colspan=6>کاربری ثبت نشده.</td></tr>"}
    </table></div>
    """
    return page("کاربران", body, "/users")


@app.get("/users/{tid}", response_class=HTMLResponse)
async def user_detail(request: Request, tid: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        user = await core.get_user(db, tid)
        if not user:
            return page("کاربر یافت نشد", "<h1>کاربر یافت نشد</h1>", "/users")
        cur = await db.execute(
            "SELECT s.id, s.name, s.ip, s.location, s.plan_title, s.status FROM servers s WHERE s.user_id=? "
            "AND NOT EXISTS (SELECT 1 FROM servers d WHERE d.provider_type IS s.provider_type AND d.provider_id IS s.provider_id "
            "AND d.hetzner_id=s.hetzner_id AND d.user_id=s.user_id AND d.id<s.id) ORDER BY s.id DESC", (tid,)
        )
        servers = await cur.fetchall()
    ok = request.query_params.get("ok")
    msg = '<div class="msg ok">با موفقیت انجام شد.</div>' if ok else ""
    srv_rows = "".join(
        f"<tr><td>{esc(s[1])}</td><td>{esc(s[2])}</td><td>{esc(s[3])}</td><td>{esc(s[4])}</td><td>{esc(s[5])}</td>"
        f"<td>{server_action_buttons(s[0])}</td></tr>"
        for s in servers
    )
    body = f"""
    <h1><span class="page-icon icon-purple">👤</span> مدیریت کاربر {tid}</h1>
    {msg}
    <div class="card">
      <p>نام: {esc(user[2])} &nbsp; | &nbsp; یوزرنیم: @{esc(user[1])}</p>
      <p>موجودی فعلی: <b>{core.fmt_price(user[3])}</b> &nbsp; | &nbsp; وضعیت: {"🚫 مسدود" if user[4] else "✅ عادی"}</p>
      <form class="inline" method="post" action="/users/{tid}/ban">
        <button class="btn {'green' if user[4] else 'red'} small" type="submit">{"✅ رفع مسدودیت" if user[4] else "🚫 مسدود کردن"}</button>
      </form>
    </div>
    <div class="card">
      <h3>تغییر موجودی کیف پول</h3>
      <form method="post" action="/users/{tid}/balance">
        <label>مبلغ (دلار)</label>
        <input name="amount" type="number" step="0.01" min="0.01" required>
        <label>نوع عملیات</label>
        <select name="sign">
          <option value="add">➕ افزایش</option>
          <option value="sub">➖ کاهش</option>
        </select>
        <button class="btn" type="submit">اعمال</button>
      </form>
    </div>
    <div class="card">
      <h3>سرویس‌های این کاربر ({len(servers)})</h3>
      <table>
        <tr><th>نام</th><th>آی‌پی</th><th>دیتاسنتر</th><th>پلن</th><th>وضعیت</th><th></th></tr>
        {srv_rows or "<tr><td colspan=6>سروری ندارد.</td></tr>"}
      </table>
    </div>
    """
    return page(f"کاربر {tid}", body, "/users")


@app.post("/users/{tid}/balance")
async def user_balance_update(request: Request, tid: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    try:
        amount = float(form.get("amount", 0))
    except ValueError:
        amount = 0
    sign = form.get("sign", "add")
    delta = amount if sign == "add" else -amount
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE telegram_id=?", (delta, tid))
        await db.commit()
    if core.BOT_TOKEN and amount:
        try:
            bot = Bot(token=core.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            action_txt = "افزایش" if sign == "add" else "کاهش"
            await bot.send_message(tid, f"💰 موجودی کیف پول شما توسط ادمین {action_txt} یافت: {core.fmt_price(amount)}")
            await bot.session.close()
        except Exception:
            pass
    return RedirectResponse(f"/users/{tid}?ok=1", status_code=303)


@app.post("/users/{tid}/ban")
async def user_ban_toggle(request: Request, tid: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        user = await core.get_user(db, tid)
        new_val = 0 if (user and user[4]) else 1
        await db.execute("UPDATE users SET is_banned=? WHERE telegram_id=?", (new_val, tid))
        await db.commit()
    return RedirectResponse(f"/users/{tid}?ok=1", status_code=303)


# ---------------------------------------------------------------------------
# بکاپ کامل دیتابیس و بازیابی
# ---------------------------------------------------------------------------
@app.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request):
    if not get_session(request):
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        backup_channel = await core.get_setting(db, "backup_channel", "") or ""
        interval = int(await core.get_setting(db, "backup_interval_minutes", "60") or "60")
        backup_enabled = (await core.get_setting(db, "backup_enabled", "0")) == "1"
        last_epoch = float(await core.get_setting(db, "backup_last_epoch", "0") or "0")
        join_channel = await core.get_setting(db, "mandatory_channel", "") or ""
        join_link = await core.get_setting(db, "mandatory_channel_link", "") or ""
        join_enabled = (await core.get_setting(db, "mandatory_enabled", "1" if join_channel else "0")) == "1"
        last_test = await core.get_setting(db, "backup_last_result", "") or ""
    def fmt_minutes(value):
        return f"{value // 60} ساعت و {value % 60} دقیقه" if value >= 60 and value % 60 else (f"{value // 60} ساعت" if value >= 60 else f"{value} دقیقه")
    next_text = "در انتظار اولین بکاپ" if not last_epoch else datetime.fromtimestamp(last_epoch + interval*60).strftime("%Y-%m-%d %H:%M:%S")
    last_text = datetime.fromtimestamp(last_epoch).strftime("%Y-%m-%d %H:%M:%S") if last_epoch else "هنوز بکاپی ارسال نشده"
    flash = ""
    result = request.query_params.get("result", "")
    if result == "ok": flash = '<div class="msg ok">✅ بکاپ آزمایشی با موفقیت به کانال ارسال شد.</div>'
    elif result == "error": flash = '<div class="msg err">❌ ارسال بکاپ ناموفق بود؛ جزئیات خطا در پیام زیر آمده است.</div>'
    err = request.query_params.get("detail", "")
    if err: flash += f'<div class="msg err">{esc(err)}</div>'
    body = f"""
    <h1>💾 بکاپ و عضویت اجباری</h1>{flash}
    <div class="grid">
      <div class="card"><h3>🗄 بکاپ خودکار</h3><p>وضعیت: <b style="color:{'#2ee6a8' if backup_enabled else '#ff5c72'}">{'فعال' if backup_enabled else 'غیرفعال'}</b></p><p>کانال مقصد: <b>{esc(backup_channel or 'تنظیم نشده')}</b></p><p>فاصله زمانی: <b>{esc(fmt_minutes(interval))}</b></p><p>آخرین بکاپ: <b>{esc(last_text)}</b></p><p>بکاپ بعدی: <b>{esc(next_text)}</b></p><p>آخرین نتیجه: {esc(last_test or 'هنوز اجرا نشده')}</p>
      <form method="post" action="/backup/settings"><label>شناسه کانال بکاپ (@channel یا -100...)</label><input name="backup_channel" value="{esc(backup_channel)}" placeholder="@my_channel"><label>فاصله بکاپ (مثلاً 30m یا 2h)</label><input name="backup_interval" value="{esc(str(interval) + 'm')}" required><button class="btn" type="submit">💾 ذخیره تنظیمات</button></form>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px"><form method="post" action="/backup/toggle"><input type="hidden" name="enabled" value="{'0' if backup_enabled else '1'}"><button class="btn {'red' if backup_enabled else 'green'}" type="submit">{'⏸ غیرفعال‌کردن بکاپ' if backup_enabled else '▶️ فعال‌کردن بکاپ'}</button></form><form method="post" action="/backup/test"><button class="btn" type="submit">🧪 ارسال بکاپ آزمایشی</button></form></div></div>
      <div class="card"><h3>📢 عضویت اجباری</h3><p>وضعیت: <b style="color:{'#2ee6a8' if join_enabled and join_channel else '#ff5c72'}">{'فعال' if join_enabled and join_channel else 'غیرفعال'}</b></p><p>کانال: <b>{esc(join_channel or 'تنظیم نشده')}</b></p><p>لینک عضویت: <b>{esc(join_link or 'تنظیم نشده')}</b></p>
      <form method="post" action="/backup/join-settings"><label>شناسه کانال عضویت اجباری</label><input name="join_channel" value="{esc(join_channel)}" placeholder="@my_channel"><label>لینک عضویت</label><input name="join_link" value="{esc(join_link)}" placeholder="https://t.me/my_channel"><button class="btn" type="submit">💾 ذخیره کانال</button></form>
      <form method="post" action="/backup/join-toggle" style="margin-top:12px"><input type="hidden" name="enabled" value="{'0' if join_enabled else '1'}"><button class="btn {'red' if join_enabled else 'green'}" type="submit">{'⏸ غیرفعال‌کردن عضویت اجباری' if join_enabled else '▶️ فعال‌کردن عضویت اجباری'}</button></form><p>برای بررسی عضویت، ربات باید به کانال دسترسی داشته باشد و برای کانال خصوصی لینک دعوت معتبر وارد شود.</p></div>
    </div>
    <div class="card"><h3>⬇️ بکاپ دیتابیس / بازیابی</h3><p>این بخش برای دانلود یا بازیابی دیتابیس است؛ بکاپ آزمایشی بالا، فایل ZIP کامل پروژه را به کانال می‌فرستد.</p><a class="btn" href="/backup/download">⬇️ دانلود بکاپ دیتابیس</a><hr><form method="post" action="/backup/restore" enctype="multipart/form-data"><input type="file" name="backup" accept=".zip" required><button class="btn red" type="submit" onclick="return confirm('هشدار: دیتابیس فعلی با نسخه داخل بکاپ جایگزین می‌شود. ادامه می‌دهید؟')">♻️ بازیابی بکاپ دیتابیس</button></form></div>
    """
    return page("بکاپ و عضویت اجباری", body, "/backup")

@app.post("/backup/settings")
async def backup_settings_save(request: Request):
    if not get_session(request): return RedirectResponse("/login")
    form = await request.form()
    channel = str(form.get("backup_channel", "")).strip()
    raw = str(form.get("backup_interval", "60m")).strip().lower().replace(" ", "")
    try:
        if raw.endswith("m"): minutes = int(raw[:-1])
        elif raw.endswith("h"): minutes = int(raw[:-1]) * 60
        else: minutes = int(raw)
        if not 5 <= minutes <= 43200: raise ValueError()
    except Exception:
        return RedirectResponse("/backup?result=error&detail=" + urllib.parse.quote("فاصله نامعتبر است؛ مثل 30m یا 2h وارد کن (حداقل ۵ دقیقه)."), status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        await core.set_setting(db, "backup_channel", channel)
        await core.set_setting(db, "backup_interval_minutes", str(minutes))
        if not channel: await core.set_setting(db, "backup_enabled", "0")
    return RedirectResponse("/backup", status_code=303)

@app.post("/backup/toggle")
async def backup_toggle(request: Request):
    if not get_session(request): return RedirectResponse("/login")
    form = await request.form(); enabled = str(form.get("enabled", "0")) == "1"
    async with aiosqlite.connect(DB_PATH) as db:
        channel = await core.get_setting(db, "backup_channel", "")
        if enabled and not channel:
            return RedirectResponse("/backup?result=error&detail=" + urllib.parse.quote("ابتدا کانال بکاپ را ذخیره کن."), status_code=303)
        await core.set_setting(db, "backup_enabled", "1" if enabled else "0")
    return RedirectResponse("/backup", status_code=303)

@app.post("/backup/join-settings")
async def join_settings_save(request: Request):
    if not get_session(request): return RedirectResponse("/login")
    form = await request.form(); channel = str(form.get("join_channel", "")).strip(); link = str(form.get("join_link", "")).strip()
    if link and not link.startswith("https://t.me/"):
        return RedirectResponse("/backup?result=error&detail=" + urllib.parse.quote("لینک عضویت باید با https://t.me/ شروع شود."), status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        await core.set_setting(db, "mandatory_channel", channel)
        await core.set_setting(db, "mandatory_channel_link", link)
        if not channel: await core.set_setting(db, "mandatory_enabled", "0")
    return RedirectResponse("/backup", status_code=303)

@app.post("/backup/join-toggle")
async def join_toggle(request: Request):
    if not get_session(request): return RedirectResponse("/login")
    form = await request.form(); enabled = str(form.get("enabled", "0")) == "1"
    async with aiosqlite.connect(DB_PATH) as db:
        channel = await core.get_setting(db, "mandatory_channel", "")
        if enabled and not channel:
            return RedirectResponse("/backup?result=error&detail=" + urllib.parse.quote("ابتدا کانال عضویت اجباری را ذخیره کن."), status_code=303)
        await core.set_setting(db, "mandatory_enabled", "1" if enabled else "0")
    return RedirectResponse("/backup", status_code=303)

@app.post("/backup/test")
async def backup_test(request: Request):
    if not get_session(request): return RedirectResponse("/login")
    if not core.BOT_TOKEN: return RedirectResponse("/backup?result=error&detail=" + urllib.parse.quote("BOT_TOKEN در تنظیمات سرور تنظیم نشده است."), status_code=303)
    async with aiosqlite.connect(DB_PATH) as db: channel = await core.get_setting(db, "backup_channel", "")
    if not channel: return RedirectResponse("/backup?result=error&detail=" + urllib.parse.quote("ابتدا کانال بکاپ را تنظیم کن."), status_code=303)
    bot = Bot(token=core.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await core.send_project_backup(bot, channel)
        async with aiosqlite.connect(DB_PATH) as db:
            await core.set_setting(db, "backup_last_epoch", str(datetime.now().timestamp()))
            await core.set_setting(db, "backup_last_result", "ارسال آزمایشی موفق در " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return RedirectResponse("/backup?result=ok", status_code=303)
    except Exception as exc:
        async with aiosqlite.connect(DB_PATH) as db: await core.set_setting(db, "backup_last_result", "خطا: " + str(exc)[:300])
        return RedirectResponse("/backup?result=error&detail=" + urllib.parse.quote(str(exc)[:250]), status_code=303)
    finally:
        await bot.session.close()

@app.get("/backup/download")
async def backup_download(request: Request):
    if not get_session(request):
        return RedirectResponse("/login")
    dbfile = Path(DB_PATH).resolve()
    if not dbfile.exists():
        return HTMLResponse("فایل دیتابیس پیدا نشد.", status_code=404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(dbfile, arcname="shop.db")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": "attachment; filename=hetzner-shop-backup.zip"})

@app.post("/backup/restore")
async def backup_restore(request: Request, backup: UploadFile = File(...)):
    if not get_session(request):
        return RedirectResponse("/login")
    raw = await backup.read()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
            if "shop.db" not in names:
                raise ValueError("فایل shop.db در بکاپ نیست")
            db_bytes = zf.read("shop.db")
        # Validate SQLite integrity before replacing the live DB.
        temp = io.BytesIO(db_bytes)
        tmp_path = str(Path(DB_PATH).resolve()) + ".restore-check"
        Path(tmp_path).write_bytes(temp.getvalue())
        con = sqlite3.connect(tmp_path)
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        con.close()
        if integrity != "ok":
            raise ValueError("دیتابیس بکاپ سالم نیست")
        live = Path(DB_PATH).resolve()
        if live.exists():
            import shutil
            shutil.copy2(live, str(live) + ".before-restore.bak")
        live.write_bytes(db_bytes)
        Path(tmp_path).unlink(missing_ok=True)
    except Exception as exc:
        return HTMLResponse(f"بازیابی انجام نشد: {esc(exc)} <a href='/backup'>بازگشت</a>", status_code=400)
    return RedirectResponse("/backup?restored=1", status_code=303)

# ---------------------------------------------------------------------------
# انتقال ارتباط سرور به API مقصد پس از انتقال واقعی در کنسول Hetzner
# ---------------------------------------------------------------------------
@app.get("/server-transfer", response_class=HTMLResponse)
async def server_transfer_page(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    error = request.query_params.get("err", "")
    success = request.query_params.get("ok", "")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, label, type, active FROM provider ORDER BY id DESC")
        providers = await cur.fetchall()
        cur = await db.execute("""SELECT s.id, s.hetzner_id, s.name, s.ip, s.user_id, s.provider_id,
            p.label FROM servers s LEFT JOIN provider p ON p.id=s.provider_id
            WHERE s.hetzner_id IS NOT NULL AND COALESCE(s.provider_type,'hetzner')='hetzner'
            ORDER BY s.id DESC LIMIT 1000""")
        servers = await cur.fetchall()
    provider_options = "".join(
        f'<option value="{int(p[0])}">{esc(p[1] or "بدون‌نام")} (API #{int(p[0])})</option>'
        for p in providers if (p[2] or "hetzner") == "hetzner"
    )
    seen = set()
    server_options = []
    for row in servers:
        sid, hzid, name, ip, uid, pid, label = row
        key = str(hzid)
        if key in seen: continue
        seen.add(key)
        owner = f"مالک {uid}" if uid is not None else "بدون مالک"
        server_options.append(
            f'<option value="{int(sid)}">{esc(name or "سرور")} | {esc(ip or "بدون IP")} | Hetzner ID: {int(hzid)} | {esc(owner)} | API فعلی: {esc(label or "نامشخص")}</option>'
        )
    flash = (f'<div class="msg err">{esc(error)}</div>' if error else "") + (f'<div class="msg ok">{esc(success)}</div>' if success else "")
    body = f"""
    <h1><span class="page-icon icon-blue">🔁</span> انتقال سرورها بین APIها</h1>
    {flash}
    <div class="card">
      <h3>انتقال پس از انجام مراحل در Hetzner</h3>
      <p>این ابزار سرور را در خود Hetzner جابه‌جا نمی‌کند. ابتدا اکانت مقصد را در کنسول Hetzner به پروژه دعوت کن، دعوت را بپذیر و سرور را از پروژه مبدأ به پروژه مقصد منتقل کن.</p>
      <p><b>فقط پس از انتقال واقعی:</b> سرور و API مقصد را انتخاب کن. پنل بررسی می‌کند که توکن API مقصد بتواند همان سرور را بخواند؛ سپس ارتباط دیتابیس را به API مقصد تغییر می‌دهد.</p>
      <p class="msg err">تا پیش از موفقیت بررسی دسترسی مقصد، اطلاعات دیتابیس تغییر نمی‌کند. توکن مبدأ را حذف نکن مگر پس از اطمینان از مدیریت کامل سرورها با API مقصد.</p>
      <form method="post" action="/server-transfer">
        <label>سرور منتقل‌شده</label>
        <select name="server_row_id" required>{''.join(server_options) or '<option value="">سروری موجود نیست</option>'}</select>
        <label>API مقصد (Hetzner)</label>
        <select name="provider_id" required>{provider_options or '<option value="">ابتدا API مقصد را اضافه کنید</option>'}</select>
        <button class="btn" type="submit">🔎 بررسی دسترسی و ثبت API مقصد</button>
      </form>
    </div>
    """
    return page("انتقال سرورها بین APIها", body, "/server-transfer")


@app.post("/server-transfer")
async def server_transfer_submit(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    try:
        server_row_id = int(form.get("server_row_id", "0"))
        provider_id = int(form.get("provider_id", "0"))
    except (TypeError, ValueError):
        return RedirectResponse("/server-transfer?err=" + urllib.parse.quote("سرور یا API مقصد نامعتبر است."), status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT hetzner_id, provider_type FROM servers WHERE id=?", (server_row_id,))
        server = await cur.fetchone()
        cur = await db.execute("SELECT api_token, type, label FROM provider WHERE id=?", (provider_id,))
        provider = await cur.fetchone()
    if not server or not server[0]:
        return RedirectResponse("/server-transfer?err=" + urllib.parse.quote("سرور انتخاب‌شده پیدا نشد."), status_code=303)
    if not provider or (provider[1] or "hetzner") != "hetzner":
        return RedirectResponse("/server-transfer?err=" + urllib.parse.quote("API مقصد باید یک اتصال Hetzner معتبر باشد."), status_code=303)
    try:
        api = core.HetznerAPI(provider[0])
        await api.get_server(int(server[0]))
    except Exception as exc:
        return RedirectResponse("/server-transfer?err=" + urllib.parse.quote("API مقصد هنوز به این سرور دسترسی ندارد؛ انتقال در دیتابیس انجام نشد. جزئیات: " + str(exc)[:180]), status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        # Update all duplicate DB rows for this Hetzner server, preserving owner/order/expiry.
        await db.execute("UPDATE servers SET provider_id=?, provider_type='hetzner' WHERE hetzner_id=? AND COALESCE(provider_type,'hetzner')='hetzner'", (provider_id, int(server[0])))
        await db.commit()
    return RedirectResponse("/server-transfer?ok=" + urllib.parse.quote(f"دسترسی API مقصد ({provider[2] or provider_id}) تأیید شد و ارتباط سرور در دیتابیس به‌روزرسانی شد."), status_code=303)


# ---------------------------------------------------------------------------
# سرورها
# ---------------------------------------------------------------------------
def server_action_buttons(server_id: int, deletion_pending_reason: str = "") -> str:
    if deletion_pending_reason:
        delete_button = (
            f"<a class='btn small green' href='/servers/{server_id}/delete-approve'>✅ تأیید حذف</a>"
            f"<a class='btn small gray' href='/servers/{server_id}/renew'>⏳ تمدید</a>"
        )
        if "درخواست حذف از پنل" in deletion_pending_reason:
            delete_button += f"<a class='btn small gray' href='/servers/{server_id}/delete-cancel'>لغو درخواست</a>"
    else:
        delete_button = (
            f"<form class='inline' method='post' action='/servers/{server_id}/action/del'>"
            f"<button class='btn small red' type='submit'>درخواست حذف</button></form>"
        )
    return (
        f"<a class='btn small gray' href='/servers/{server_id}/usage'>📊 زمان و حجم / همگام‌سازی</a>"
        f"<form class='inline' method='post' action='/servers/{server_id}/action/on'><button class='btn small green' type='submit'>روشن</button></form>"
        f"<form class='inline' method='post' action='/servers/{server_id}/action/off'><button class='btn small gray' type='submit'>خاموش</button></form>"
        f"<form class='inline' method='post' action='/servers/{server_id}/action/reboot'><button class='btn small' type='submit'>ریبوت</button></form>"
        f"<form class='inline' method='post' action='/servers/{server_id}/action/pass'><button class='btn small' type='submit'>تغییر پسورد</button></form>"
        f"<a class='btn small gray' href='/servers/{server_id}/rebuild'>Rebuild</a>"
        f"<form class='inline' method='post' action='/servers/{server_id}/action/ipchange'><button class='btn small gray' type='submit'>تغییر IP</button></form>"
        f"<a class='btn small gray' href='/servers/{server_id}/renew'>تمدید</a>"
        + delete_button
    )


@app.get("/servers", response_class=HTMLResponse)
async def servers_list(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    added = 0
    async with aiosqlite.connect(DB_PATH) as db:
        if request.query_params.get("sync") == "1":
            cur_providers = await db.execute("SELECT id, api_token FROM provider WHERE type='hetzner' ORDER BY id")
            for provider_id, provider_token in await cur_providers.fetchall():
                added += await core.sync_hetzner_servers(db, core.HetznerAPI(provider_token), provider_id)
        cur = await db.execute(
            "SELECT id, hetzner_id, provider_type, name, ip, user_id, location, status, root_password, created_at, expires_at, traffic_in_bytes, traffic_out_bytes, traffic_included_bytes, deletion_pending_reason FROM servers ORDER BY CASE WHEN user_id IS NOT NULL THEN 0 ELSE 1 END, id DESC LIMIT 1000"
        )
        raw_servers = await cur.fetchall()
        # Deduplicate imported/unowned rows against customer-owned rows by provider + provider server ID.
        servers, seen = [], set()
        for r in raw_servers:
            key = (str(r[2] or 'hetzner'), str(r[1])) if r[1] is not None else ('db', str(r[0]))
            if key in seen:
                continue
            seen.add(key)
            servers.append((r[0], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12], r[13], r[14]))
    note = f'<div class="msg ok">{added} سرور جدید از پنل هتزنر همگام‌سازی شد.</div>' if added else ""
    rows = "".join(
        f"<tr><td>{esc(s[1])}</td><td>{esc(s[2])}</td><td>{s[3] or 'بدون مالک'}</td><td>{esc(s[4])}</td>"
        f"<td>{esc(s[5])}</td><td>{server_action_buttons(s[0], str(s[12] or ''))}</td></tr>"
        for s in servers
    )
    body = f"""
    <h1><span class="page-icon icon-blue">🖥</span> سرورها ({len(servers)})</h1>
    {note}
    <div class="card">
      <a class="btn" href="/servers?sync=1">🔄 همگام‌سازی با Hetzner (پیدا کردن سرورهای خارج از ربات)</a>
    </div>
    <div class="card"><table>
      <tr><th>نام</th><th>آی‌پی</th><th>مالک</th><th>دیتاسنتر</th><th>وضعیت</th><th>عملیات</th></tr>
      {rows or "<tr><td colspan=6>سروری ثبت نشده.</td></tr>"}
    </table></div>
    """
    return page("سرورها", body, "/servers")


@app.get("/servers/{server_id}/usage", response_class=HTMLResponse)
async def server_usage_page(request: Request, server_id: int):
    if not get_session(request):
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name, hetzner_id, provider_type, created_at, expires_at, traffic_in_bytes, traffic_out_bytes, traffic_included_bytes, traffic_synced_at, deletion_pending_reason, previous_ip_address, previous_ip_cleanup_at FROM servers WHERE id=?", (server_id,))
        row = await cur.fetchone()
        if not row:
            return RedirectResponse("/servers")
        ptype = row[2] or "hetzner"
        token = await core.get_token_for_server(db, server_id, ptype)
        live = None
        error = None
        if token:
            try:
                live = await core.sync_server_usage(db, server_id, core.make_provider_api(ptype, token))
                row = await (await db.execute("SELECT name, hetzner_id, provider_type, created_at, expires_at, traffic_in_bytes, traffic_out_bytes, traffic_included_bytes, traffic_synced_at, deletion_pending_reason, previous_ip_address, previous_ip_cleanup_at FROM servers WHERE id=?", (server_id,))).fetchone()
            except Exception as exc:
                error = str(exc)
    incoming, outgoing, included = int(row[5] or 0), int(row[6] or 0), int(row[7] or 0)
    used = incoming + outgoing
    remaining = max(0, included - used) if included else None
    body = f"""
    <h1>📊 زمان و حجم سرور «{esc(row[0])}»</h1>
    <div class='card'>
      <div class='system-list'>
        <span>زمان سپری‌شده</span><strong>{esc(core.format_elapsed(row[3]))}</strong>
        <span>ترافیک مصرف‌شده</span><strong>{esc(core.format_bytes(used))} (ورودی {esc(core.format_bytes(incoming))} / خروجی {esc(core.format_bytes(outgoing))})</strong>
        <span>حجم مجاز از API</span><strong>{esc(core.format_bytes(included)) if included else 'API مقدار مجاز را برنگرداند'}</strong>
        <span>حجم باقی‌مانده</span><strong>{esc(core.format_bytes(remaining)) if remaining is not None else 'نامشخص'}</strong>
        <span>آخرین همگام‌سازی</span><strong>{esc(row[8] or '—')}</strong>
        <span>اعتبار سرویس</span><strong>{esc(row[4] or '—')}</strong>
        <span>درخواست حذف</span><strong>{esc(row[9] or 'ندارد')}</strong>
        <span>IP قبلی در انتظار آزادسازی</span><strong>{esc(row[10] or 'ندارد')} {('تا '+esc(row[11])) if row[11] else ''}</strong>
      </div>
      {f"<div class='msg err'>همگام‌سازی ناموفق: {esc(error)}</div>" if error else ""}
      <a class='btn' href='/servers/{server_id}/usage'>🔄 همگام‌سازی مجدد</a>
      <a class='btn gray' href='/servers'>بازگشت به سرورها</a>
    </div>
    """
    return page("زمان و حجم سرور", body, "/servers")


@app.get("/ip-pricing", response_class=HTMLResponse)
async def ip_pricing_page(request: Request):
    if not get_session(request): return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        free_count, price = await core.get_ip_change_config(db)
    flash = ""
    result = request.query_params.get("result", "")
    detail = request.query_params.get("detail", "")
    if result == "ok": flash = '<div class="msg ok">✅ تنظیمات تغییر IP ذخیره شد.</div>'
    elif result == "error": flash = '<div class="msg err">❌ مقادیر واردشده معتبر نیستند.</div>'
    if detail: flash += f'<div class="msg err">{esc(detail)}</div>'
    body = f"""
    <h1>🌐 قیمت و دفعات رایگان تغییر IP</h1>{flash}
    <div class="card"><p>این تنظیمات برای ربات تلگرام و پنل وب مشترک است و در دیتابیس ذخیره می‌شود.</p>
      <form method="post" action="/ip-pricing">
        <label>دفعات رایگان برای هر سرور</label>
        <input type="number" name="free_count" min="0" max="100000" step="1" value="{free_count}" required>
        <small>۰ یعنی هیچ تغییر IP رایگانی وجود ندارد. شمارش فعلی به ازای هر سرور است.</small>
        <label style="margin-top:14px">قیمت هر تغییر پس از سهمیه (دلار)</label>
        <input type="number" name="price" min="0" max="1000000" step="0.01" value="{price:.2f}" required>
        <small>قیمت صفر یعنی حتی پس از اتمام سهمیه نیز هزینه‌ای دریافت نشود.</small>
        <button class="btn" type="submit" style="margin-top:16px">💾 ذخیره تنظیمات</button>
      </form>
    </div>
    """
    return page("قیمت و دفعات رایگان تغییر IP", body, "/ip-pricing")

@app.post("/ip-pricing")
async def ip_pricing_save(request: Request):
    if not get_session(request): return RedirectResponse("/login")
    form = await request.form()
    try:
        free_count = int(str(form.get("free_count", "")).strip())
        price = float(str(form.get("price", "")).strip().replace(",", "."))
        if not 0 <= free_count <= 100000 or not 0 <= price <= 1000000: raise ValueError()
    except (TypeError, ValueError):
        return RedirectResponse("/ip-pricing?result=error", status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        await core.set_setting(db, "ip_change_free_count", str(free_count))
        await core.set_setting(db, "ip_change_price", f"{price:.6f}".rstrip("0").rstrip("."))
    return RedirectResponse("/ip-pricing?result=ok", status_code=303)


@app.post("/servers/{server_id}/action/{action}")
async def server_action_do(request: Request, server_id: int, action: str):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT hetzner_id, name, user_id, ip_change_count, provider_type, provider_id, deletion_pending_reason, COALESCE(is_protected,0), expires_at FROM servers WHERE id=?", (server_id,))
        row = await cur.fetchone()
        token = await core.get_token_for_server(db, server_id, row[4] or "hetzner") if row else None

    if not row or not token:
        return RedirectResponse("/servers")
    if len(row) > 7 and row[7] and action in ("off", "del"):
        referer = request.headers.get("referer", "/servers")
        sep = "&" if "?" in referer else "?"
        return RedirectResponse(f"{referer}{sep}err=" + urllib.parse.quote("این سرور محافظت‌شده است و خاموش/حذف نمی‌شود."), status_code=303)
    if row[6] and action not in ("del", "on", "renew"):
        referer = request.headers.get("referer", "/servers")
        sep = "&" if "?" in referer else "?"
        return RedirectResponse(f"{referer}{sep}err={urllib.parse.quote('سرور در انتظار تأیید حذف است و تا تعیین تکلیف خاموش می‌ماند.')}", status_code=303)
    api = core.make_provider_api(row[4] or "hetzner", token)
    referer = request.headers.get("referer", "/servers")
    try:
        if action == "on":
            await api.power_action(row[0], "poweron")
            expired_now = False
            try: expired_now = bool(row[8] and datetime.fromisoformat(row[8]) <= datetime.utcnow())
            except Exception: pass
            if row[6] or expired_now:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE servers SET admin_reactivated=1,deletion_pending_reason=NULL,deletion_requested_at=NULL,deletion_notified=0,expired_powered_off_at=NULL,status='running' WHERE id=?", (server_id,))
                    await db.commit()
        elif action == "off":
            await api.power_action(row[0], "poweroff")
        elif action == "reboot":
            await api.power_action(row[0], "reboot")
        elif action == "pass":
            new_pass = await api.reset_password(row[0])
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE servers SET root_password=? WHERE id=?", (new_pass, server_id))
                await db.commit()
        elif action == "ipchange":
            if (row[4] or "hetzner") != "hetzner":
                sep = "&" if "?" in referer else "?"
                return RedirectResponse(f"{referer}{sep}err={urllib.parse.quote('تغییر IP فقط برای سرورهای Hetzner در دسترس است')}", status_code=303)
            owner_id, count = row[2], (row[3] or 0)
            async with aiosqlite.connect(DB_PATH) as db:
                free_count, ip_price = await core.get_ip_change_config(db)
            cost = 0 if count < free_count else ip_price
            if cost > 0 and owner_id:
                async with aiosqlite.connect(DB_PATH) as db:
                    cur2 = await db.execute("SELECT balance FROM users WHERE telegram_id=?", (owner_id,))
                    bal_row = await cur2.fetchone()
                if not bal_row or bal_row[0] < cost:
                    sep = "&" if "?" in referer else "?"
                    return RedirectResponse(f"{referer}{sep}err={urllib.parse.quote('موجودی کیف پول کاربر کافی نیست')}", status_code=303)
            new_ip, old_ip_id, old_ip_address = await core.change_server_ip(api, row[0])
            async with aiosqlite.connect(DB_PATH) as db:
                cleanup_at = (datetime.utcnow()+timedelta(minutes=30)).isoformat()
                await db.execute(
                    "UPDATE servers SET ip=?, ip_change_count=ip_change_count+1, previous_ip_id=?, previous_ip_address=?, previous_ip_cleanup_at=? WHERE id=?",
                    (new_ip, old_ip_id, old_ip_address, cleanup_at, server_id),
                )
                await db.execute(
                    "INSERT INTO pending_ip_cleanup (provider_id, provider_type, ip_id, ip_address, cleanup_at) VALUES (?,?,?,?,?)",
                    (row[5], "hetzner", old_ip_id, old_ip_address, cleanup_at),
                )
                if cost > 0 and owner_id:
                    await db.execute("UPDATE users SET balance = balance - ? WHERE telegram_id=?", (cost, owner_id))
                await db.commit()
        elif action == "del":
            try:
                await api.power_action(row[0], "poweroff")
            except Exception:
                pass
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE servers SET deletion_pending_reason=?, deletion_requested_at=?, deletion_notified=1, status='off' WHERE id=?",
                    ("درخواست حذف از پنل ادمین", datetime.utcnow().isoformat(), server_id),
                )
                await db.commit()
    except Exception as e:
        sep = "&" if "?" in referer else "?"
        return RedirectResponse(f"{referer}{sep}err={urllib.parse.quote(str(e))}", status_code=303)
    return RedirectResponse(referer, status_code=303)


@app.get("/servers/{server_id}/delete-approve")
async def server_delete_approve(request: Request, server_id: int):
    if not get_session(request):
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT hetzner_id, provider_type, deletion_pending_reason, COALESCE(is_protected,0) FROM servers WHERE id=?", (server_id,))
        row = await cur.fetchone()
        if not row or not row[2] or row[3]:
            return RedirectResponse("/servers?err=" + urllib.parse.quote("سرور محافظت‌شده است یا درخواست حذف ندارد"), status_code=303)
        token = await core.get_token_for_server(db, server_id, row[1] or "hetzner")
    if not token:
        return RedirectResponse("/servers?err=" + urllib.parse.quote("API مالک سرور پیدا نشد"), status_code=303)
    try:
        await core.make_provider_api(row[1] or "hetzner", token).delete_server(row[0])
    except Exception as exc:
        return RedirectResponse("/servers?err=" + urllib.parse.quote(str(exc)), status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM servers WHERE id=?", (server_id,))
        await db.commit()
    return RedirectResponse("/servers?deleted=1", status_code=303)


@app.get("/servers/{server_id}/delete-cancel")
async def server_delete_cancel(request: Request, server_id: int):
    if not get_session(request):
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT hetzner_id, provider_type, deletion_pending_reason FROM servers WHERE id=?", (server_id,)
        )
        row = await cur.fetchone()
        if not row:
            return RedirectResponse("/servers", status_code=303)
        if not row[2] or "درخواست حذف از پنل" not in row[2]:
            return RedirectResponse("/servers", status_code=303)
        token = await core.get_token_for_server(db, server_id, row[1] or "hetzner")
        await db.execute(
            "UPDATE servers SET deletion_pending_reason=NULL, deletion_requested_at=NULL, deletion_notified=0, expiry_reminder_sent=0, traffic_warning_sent=0, admin_reactivated=0, traffic_limit_reviewed_at=CASE WHEN deletion_pending_reason LIKE '%حجم%' OR deletion_pending_reason LIKE '%ترافیک%' THEN ? ELSE traffic_limit_reviewed_at END WHERE id=?",
            (datetime.utcnow().isoformat(), server_id),
        )
        await db.commit()
    if token:
        try:
            await core.make_provider_api(row[1] or "hetzner", token).power_action(row[0], "poweron")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE servers SET status='running' WHERE id=?", (server_id,))
                await db.commit()
        except Exception:
            pass
    return RedirectResponse("/servers?cancelled=1", status_code=303)


@app.get("/servers/{server_id}/renew", response_class=HTMLResponse)
async def server_renew_form(request: Request, server_id: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name, expires_at, monthly_price, user_id, deletion_pending_reason FROM servers WHERE id=?", (server_id,))
        row = await cur.fetchone()
    if not row:
        return RedirectResponse("/servers")
    name, expires_at, monthly_price, owner_id, pending_reason = row
    if not expires_at or monthly_price is None or float(monthly_price) <= 0:
        return page("تمدید سرویس", f"<h1><span class='page-icon icon-blue'>⏳</span> تمدید سرویس</h1><div class='card'>قیمت تمدید سرور «{esc(name)}» هنوز به پلن فعال متصل نشده است؛ ابتدا پلن و قیمت آن را در مدیریت بررسی کنید.</div>", "/servers")
    exp_dt = datetime.fromisoformat(expires_at)
    body = f"""
    <h1><span class="page-icon icon-blue">⏳</span> تمدید سرویس «{esc(name)}»</h1>
    <div class="card">
      <p>مهلت فعلی تا: <b>{core.iran_time(exp_dt).strftime('%Y-%m-%d %H:%M')} (به وقت ایران)</b></p>
      <p>هزینه تمدید {core.RENEWAL_PERIOD_DAYS} روز دیگر: <b>{core.fmt_price(monthly_price)}</b> (از کیف پول کاربر {owner_id or '-'} کسر می‌شود)</p>
      {f"<p class='msg err'>درخواست حذف در انتظار بررسی: {esc(pending_reason)}</p>" if pending_reason else ""}
      <form method="post" action="/servers/{server_id}/renew">
        <button class="btn green" type="submit">✅ تایید و تمدید</button>
      </form>
    </div>
    """
    return page("تمدید سرویس", body, "/servers")


@app.post("/servers/{server_id}/renew")
async def server_renew_do(request: Request, server_id: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT user_id, hetzner_id, expires_at, monthly_price, expired_powered_off_at, provider_type, traffic_powered_off, deletion_pending_reason FROM servers WHERE id=?",
            (server_id,),
        )
        row = await cur.fetchone()
        if not row:
            return RedirectResponse("/servers")
        owner_id, hetzner_id, expires_at, monthly_price, expired_off_at, provider_type, traffic_off, deletion_pending_reason = row
        if not expires_at or monthly_price is None or float(monthly_price) <= 0:
            return RedirectResponse("/servers?err=" + urllib.parse.quote("قیمت تمدید سرور به پلن فعال متصل نشده است"), status_code=303)
        cur2 = await db.execute("SELECT balance FROM users WHERE telegram_id=?", (owner_id,)) if owner_id is not None else None
        bal_row = await cur2.fetchone() if cur2 else None
        # تمدید از پنل توسط ادمین انجام می‌شود و باید همیشه در دفتر مالی ثبت شود.
        if owner_id is not None and float(monthly_price) > 0 and not bal_row:
            await db.rollback()
            return RedirectResponse("/servers?err=" + urllib.parse.quote("حساب کیف پول مالک پیدا نشد؛ تمدید انجام نشد"), status_code=303)
        token = await core.get_token_for_server(db, server_id, provider_type or "hetzner")
        base = datetime.fromisoformat(expires_at)
        now = datetime.utcnow()
        if base < now:
            base = now
        new_expiry = base + timedelta(days=core.RENEWAL_PERIOD_DAYS)
        was_expired_off = bool(expired_off_at)
        if owner_id is not None and monthly_price > 0:
            if not bal_row:
                await db.rollback()
                return RedirectResponse("/servers?err=" + urllib.parse.quote("حساب کیف پول مالک پیدا نشد"), status_code=303)
            # تمدید از پنل توسط ادمین انجام می‌شود؛ موجودی می‌تواند منفی شود.
            await db.execute("UPDATE users SET balance=balance-? WHERE telegram_id=?", (float(monthly_price), owner_id))
            balance_after = float(bal_row[0] or 0) - float(monthly_price)
            admin_ref = f"panel-admin-server-renew:{server_id}:{expires_at}"
            await db.execute(
                "INSERT INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)",
                (owner_id, -float(monthly_price), "admin_server_renewal", admin_ref, datetime.utcnow().isoformat()),
            )
            await db.execute(
                "INSERT INTO admin_wallet_actions(admin_actor,target_user_id,action,amount_usd,reference,details,created_at) VALUES(?,?,?,?,?,?,?)",
                (str(sess.get("username") or PANEL_USERNAME), owner_id, "server_renewal", float(monthly_price), admin_ref,
                 f"server_id={server_id};days={core.RENEWAL_PERIOD_DAYS};balance_after={balance_after:.6f}",
                 datetime.utcnow().isoformat()),
            )
        await db.execute(
            "UPDATE servers SET expires_at=?, expired_powered_off_at=NULL, traffic_powered_off=0, deletion_pending_reason=NULL, deletion_requested_at=NULL, deletion_notified=0, expiry_reminder_sent=0, traffic_warning_sent=0, admin_reactivated=0, traffic_limit_reviewed_at=CASE WHEN ? LIKE '%ترافیک%' OR ? LIKE '%حجم%' THEN ? ELSE traffic_limit_reviewed_at END WHERE id=?",
            (new_expiry.isoformat(), deletion_pending_reason or "", deletion_pending_reason or "", datetime.utcnow().isoformat(), server_id),
        )
        await db.commit()
    power_on_error = ""
    if (was_expired_off or traffic_off or deletion_pending_reason) and token:
        try:
            await core.make_provider_api(provider_type or "hetzner", token).power_action(hetzner_id, "poweron")
        except Exception as exc:
            power_on_error = str(exc)[:250]
            core.logger.exception("Admin renewal succeeded but powering server %s on failed", server_id)
    if owner_id is not None and monthly_price > 0 and core.BOT_TOKEN:
        try:
            bot_client = Bot(token=core.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            try:
                await bot_client.send_message(
                    owner_id,
                    "📢 سرور شما توسط مدیریت تمدید شد.\n"
                    f"شناسه سرور: #{server_id}\n"
                    f"مدت تمدید: {core.RENEWAL_PERIOD_DAYS} روز\n"
                    f"مبلغ کسرشده از کیف پول: {core.fmt_price(0 if admin_free_renewal else monthly_price)}"
                    + (" (رایگان ویژه مدیر)\n" if admin_free_renewal else "\n")
                    + f"مانده جدید کیف پول: {core.fmt_price(balance_after)}\n"
                    f"مهلت جدید: {core.iran_time(new_expiry).strftime('%Y-%m-%d %H:%M')} (به وقت ایران)\n"
                    + (f"⚠️ روشن‌کردن خودکار سرور ناموفق بود: {power_on_error}" if power_on_error else ""),
                )
            finally:
                await bot_client.session.close()
        except Exception:
            core.logger.exception("Could not notify owner %s after panel renewal", owner_id)
    return RedirectResponse("/servers", status_code=303)


@app.get("/servers/{server_id}/rebuild", response_class=HTMLResponse)
async def server_rebuild_form(request: Request, server_id: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name, provider_type, deletion_pending_reason FROM servers WHERE id=?", (server_id,))
        row = await cur.fetchone()
        token = await core.get_token_for_server(db, server_id, row[1] or "hetzner") if row and not row[2] else None
    if not row or not token:
        return RedirectResponse("/servers")
    api = core.make_provider_api(row[1] or "hetzner", token)
    try:
        images = await api.images()
    except Exception as e:
        return page("خطا", f"<h1>خطا</h1><p>{esc(e)}</p>", "/servers")
    options = "".join(f"<option value='{img['id']}'>{esc(img.get('name'))}</option>" for img in images[:30])
    body = f"""
    <h1><span class="page-icon icon-red">🛠</span> نصب مجدد سرور «{esc(row[0])}»</h1>
    <div class="card">
      <p style="color:#ff8080;">⚠️ توجه: این عملیات تمام اطلاعات سرور را پاک می‌کند.</p>
      <form method="post" action="/servers/{server_id}/rebuild">
        <label>سیستم‌عامل جدید</label>
        <select name="image">{options}</select>
        <button class="btn red" type="submit">نصب مجدد</button>
      </form>
    </div>
    """
    return page("نصب مجدد", body, "/servers")


@app.post("/servers/{server_id}/rebuild")
async def server_rebuild_do(request: Request, server_id: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    image = form.get("image")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT hetzner_id, provider_type, deletion_pending_reason FROM servers WHERE id=?", (server_id,))
        row = await cur.fetchone()
        token = await core.get_token_for_server(db, server_id, row[1] or "hetzner") if row and not row[2] else None
    if row and token and image:
        api = core.make_provider_api(row[1] or "hetzner", token)
        try:
            new_pass = await api.rebuild_server(row[0], image)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE servers SET os_image=?, root_password=?, status='running' WHERE id=?",
                    (image, new_pass, server_id),
                )
                await db.commit()
        except Exception:
            pass
    return RedirectResponse("/servers", status_code=303)


# ---------------------------------------------------------------------------
# پلن‌ها
# ---------------------------------------------------------------------------
@app.get("/plans", response_class=HTMLResponse)
async def plans_list(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, title, server_type, cpu, ram, disk, markup_percent, active, location FROM plans ORDER BY id DESC")
        all_plans = await cur.fetchall()
        token = await core.get_active_token(db)
        disabled = await core.get_disabled_locations(db)
    query_err = request.query_params.get("err")
    query_ok = request.query_params.get("ok")
    type_options, loc_options = "", "<option value='ALL'>🌍 همه لوکیشن‌ها</option>"
    stock_map, enabled_locations = None, set()
    if token:
        api = core.HetznerAPI(token)
        try:
            types = await api.server_types()
            type_options = "".join(
                f"<option value='{t['name']}|{t['cores']}|{t['memory']}|{t['disk']}'>{t['name']} | {t['cores']}CPU/{t['memory']}GB/{t['disk']}GB</option>"
                for t in types[:40]
            )
            locations = await api.locations()
            loc_options += "".join(f"<option value='{l['name']}'>{esc(l['city'])}</option>" for l in locations)
            enabled_locations = {l["name"] for l in locations if l["name"] not in disabled}
        except Exception:
            pass
        stock_map = await core.build_stock_map(api)
    plans = all_plans
    def plan_stock_label(plan_row):
        if stock_map is None:
            return "⚠️ نامشخص"
        return "✅ موجود" if core.plan_has_stock(stock_map, plan_row[8], plan_row[2], enabled_locations) else "❌ ناموجود"
    rows = "".join(
        f"<tr><td>{esc(p[1])}</td><td>{esc(p[2])}</td><td>{esc(p[3])}/{esc(p[4])}/{esc(p[5])}</td><td>%{p[6]:g}</td>"
        f"<td>{esc(p[8]) if p[8] else 'همه'}</td><td>{plan_stock_label(p)}</td><td>{'✅ فعال' if p[7] else '⚪ غیرفعال'}</td>"
        f"<td><form class='inline' method='post' action='/plans/{p[0]}/toggle'><button class='btn small gray' type='submit'>{'غیرفعال' if p[7] else 'فعال'}</button></form>"
        f"<form class='inline' method='post' action='/plans/{p[0]}/delete' onsubmit=\"return confirm('حذف این پلن؟')\"><button class='btn small red' type='submit'>حذف</button></form></td></tr>"
        for p in plans
    )
    note = '<div class="msg">وضعیت موجودی از API فعلی Hetzner خوانده می‌شود؛ در صورت خطای API، وضعیت نامشخص است.</div>' if stock_map is None else ""
    body = f"""
    <h1><span class="page-icon icon-teal">🧩</span> پلن‌ها ({len(plans)})</h1>
    {note}
    {f'<div class="msg err">{esc(query_err)}</div>' if query_err else ''}
    {f'<div class="msg ok">{esc(query_ok)}</div>' if query_ok else ''}
    <form method="post" action="/plans/sync"><button class="btn" type="submit">🔄 همگام‌سازی موجودی پلن‌ها از Hetzner</button></form>
    <div class="card">
      <h3>افزودن پلن جدید</h3>
      <form method="post" action="/plans/add">
        <label>نوع سرور Hetzner</label>
        <select name="type_combo" required>{type_options or "<option>ابتدا یک API Hetzner فعال کنید</option>"}</select>
        <label>عنوان نمایشی</label>
        <input name="title" required placeholder="مثلاً: پلن اقتصادی">
        <label>درصد سود (٪)</label>
        <input name="markup" type="number" step="0.1" value="20" required>
        <label>لوکیشن</label>
        <select name="location">{loc_options}</select>
        <button class="btn" type="submit">افزودن پلن</button>
      </form>
    </div>
    <div class="card"><table>
      <tr><th>عنوان</th><th>نوع</th><th>مشخصات</th><th>سود</th><th>لوکیشن</th><th>موجودی</th><th>فعال‌بودن</th><th>عملیات</th></tr>
      {rows or "<tr><td colspan=8>هنوز پلنی تعریف نشده است.</td></tr>"}
    </table></div>
    """
    return page("پلن‌ها", body, "/plans")


@app.post("/plans/sync")
async def plans_sync(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        token = await core.get_active_token(db)
    if not token:
        return RedirectResponse("/plans?err=" + urllib.parse.quote("هیچ API فعال Hetzner وجود ندارد."), status_code=303)
    try:
        stock = await core.build_stock_map(core.HetznerAPI(token))
        if stock is None:
            return RedirectResponse("/plans?err=" + urllib.parse.quote("استعلام موجودی از Hetzner ناموفق بود."), status_code=303)
    except Exception as exc:
        return RedirectResponse("/plans?err=" + urllib.parse.quote("استعلام موجودی ناموفق بود: " + str(exc)[:150]), status_code=303)
    return RedirectResponse("/plans?ok=" + urllib.parse.quote("موجودی پلن‌ها از Hetzner دوباره استعلام شد."), status_code=303)


@app.post("/plans/add")
async def plans_add(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    combo = str(form.get("type_combo", ""))
    parts = combo.split("|")
    if len(parts) != 4:
        return RedirectResponse("/plans", status_code=303)
    server_type, cores, memory, disk = parts
    title = str(form.get("title", "")).strip() or server_type
    try:
        markup = float(form.get("markup", 20))
    except ValueError:
        markup = 20
    loc = str(form.get("location", "ALL"))
    loc_value = None if loc == "ALL" else loc
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO plans (server_type, title, cpu, ram, disk, markup_percent, active, location) VALUES (?,?,?,?,?,?,1,?)",
            (server_type, title, f"{cores} core", f"{memory} GB", f"{disk} GB", markup, loc_value),
        )
        await db.commit()
    return RedirectResponse("/plans", status_code=303)


@app.post("/plans/{plan_id}/toggle")
async def plans_toggle(request: Request, plan_id: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT active FROM plans WHERE id=?", (plan_id,))
        row = await cur.fetchone()
        if row:
            await db.execute("UPDATE plans SET active=? WHERE id=?", (0 if row[0] else 1, plan_id))
            await db.commit()
    return RedirectResponse("/plans", status_code=303)


@app.post("/plans/{plan_id}/delete")
async def plans_delete(request: Request, plan_id: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM plans WHERE id=?", (plan_id,))
        await db.commit()
    return RedirectResponse("/plans", status_code=303)


# ---------------------------------------------------------------------------
# لوکیشن‌ها
# ---------------------------------------------------------------------------
@app.get("/locations", response_class=HTMLResponse)
async def locations_list(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        token = await core.get_active_token(db)
        disabled = await core.get_disabled_locations(db)
    if not token:
        return page("لوکیشن‌ها", "<h1><span class='page-icon icon-pink'>📍</span> لوکیشن‌ها</h1><div class='card'>ابتدا یک API Hetzner فعال کنید.</div>", "/locations")
    api = core.HetznerAPI(token)
    try:
        locations = await api.locations()
    except Exception as e:
        return page("لوکیشن‌ها", f"<h1><span class='page-icon icon-pink'>📍</span> لوکیشن‌ها</h1><div class='card'>خطا: {esc(e)}</div>", "/locations")
    rows = "".join(
        f"<tr><td>{esc(l['city'])}</td><td>{esc(l['country'])}</td>"
        f"<td>{'<span class=badge off>غیرفعال</span>' if l['name'] in disabled else '<span class=badge ok>فعال</span>'}</td>"
        f"<td><form method='post' action='/locations/{l['name']}/toggle'><button class='btn small gray' type='submit'>{'فعال کردن' if l['name'] in disabled else 'غیرفعال کردن'}</button></form></td></tr>"
        for l in locations
    )
    body = f"""
    <h1><span class="page-icon icon-pink">📍</span> لوکیشن‌ها</h1>
    <div class="card"><table>
      <tr><th>شهر</th><th>کشور</th><th>وضعیت</th><th></th></tr>
      {rows}
    </table></div>
    """
    return page("لوکیشن‌ها", body, "/locations")


@app.post("/locations/{name}/toggle")
async def locations_toggle(request: Request, name: str):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        await core.toggle_location(db, name)
    return RedirectResponse("/locations", status_code=303)


# ---------------------------------------------------------------------------
# API های Hetzner
# ---------------------------------------------------------------------------
@app.get("/providers", response_class=HTMLResponse)
async def providers_list(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, label, api_token, active, COALESCE(type,'hetzner') FROM provider ORDER BY id DESC")
        rows_db = await cur.fetchall()
        priorities = {}
        for ptype in {r[4] for r in rows_db}:
            priorities[ptype] = await core.get_setting(db, f"priority_provider_{ptype}", "")
    err = request.query_params.get("err")
    msg = f'<div class="msg err">اتصال ناموفق بود: {esc(err)}</div>' if err else ""
    rows = "".join(
        f"<tr><td>{esc(r[1] or 'بدون‌نام')}</td><td>...{esc(r[2][-4:]) if r[2] else '-'}</td>"
        f"<td>{'✅ فعال' if r[3] else '⚪️ غیرفعال'}</td>"
        f"<td>{'⭐ اولویت فروش' if str(priorities.get(r[4], '')) == str(r[0]) else '—'}</td>"
        f"<td><a class='btn small' href='/account-status?pid={r[0]}'>📈 لیمیت و Usage</a> "
        f"<form class='inline' method='post' action='/providers/{r[0]}/activate'><button class='btn small green' type='submit'>{'غیرفعال‌کردن' if r[3] else 'فعال‌سازی'}</button></form> "
        f"<form class='inline' method='post' action='/providers/{r[0]}/priority'><button class='btn small' type='submit'>⭐ اولویت فروش</button></form> "
        f"<form class='inline' method='post' action='/providers/{r[0]}/delete' onsubmit=\"return confirm('حذف این اتصال؟')\"><button class='btn small red' type='submit'>حذف</button></form></td></tr>"
        for r in rows_db
    )
    body = f"""
    <h1><span class="page-icon icon-yellow">🔑</span> API های Hetzner</h1>
    {msg}
    <div class="card">
      <p class="muted">APIها مستقل از هم فعال می‌مانند. اولویت فروش فقط تعیین می‌کند خریدهای جدید از کدام اکانت شروع شوند؛ مدیریت سرورهای قبلی به API مالک همان سرور متصل می‌ماند.</p>
      <h3>افزودن اتصال جدید</h3>
      <form method="post" action="/providers/add">
        <label>نام دلخواه</label>
        <input name="label" placeholder="مثلاً: اکانت اصلی">
        <label>API Token (دسترسی Read &amp; Write)</label>
        <input name="token" required>
        <button class="btn" type="submit">افزودن و فعال‌سازی</button>
      </form>
    </div>
    <div class="card"><table>
      <tr><th>نام</th><th>توکن</th><th>وضعیت</th><th>اولویت</th><th>عملیات</th></tr>
      {rows or "<tr><td colspan=5>هنوز اتصالی اضافه نشده.</td></tr>"}
    </table></div>
    """
    return page("API های Hetzner", body, "/providers")


def _limit_value(limits, names):
    for item in limits or []:
        if str(item.get("name", "")).lower() in names:
            values = item.get("values") or {}
            return values.get("current"), values.get("max")
    return None, None


@app.get("/account-status", response_class=HTMLResponse)
async def account_status(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    pid = request.query_params.get("pid", "").strip()
    async with aiosqlite.connect(DB_PATH) as db:
        if pid.isdigit():
            cur = await db.execute("SELECT id, label, api_token, active, type FROM provider WHERE id=?", (int(pid),))
        else:
            cur = await db.execute("SELECT id, label, api_token, active, type FROM provider ORDER BY id DESC")
        accounts = await cur.fetchall()
    if not accounts:
        return page("وضعیت اکانت‌ها", "<h1>📈 وضعیت اکانت‌ها</h1><div class='card'>هنوز API اضافه نشده است.</div>", "/account-status")

    cards = []
    for account_id, label, token, active, provider_type in accounts:
        name = esc(label or f"اکانت #{account_id}")
        if (provider_type or "hetzner") != "hetzner":
            cards.append(f"<section class='card'><h2>{name}</h2><p>این صفحه در حال حاضر برای Hetzner Cloud طراحی شده است.</p></section>")
            continue
        try:
            api = core.HetznerAPI(token)
            limits, servers, pricing = await __import__('asyncio').gather(api.limits(), api.all_servers(), api.pricing())
            server_types = await api.server_types()
            type_by_name = {str(t.get("name")): t for t in server_types}
            used_cores = 0
            used_ram = 0.0
            estimated_monthly = 0.0
            estimate_complete = True
            currency = pricing.get("currency", "EUR")
            for server in servers:
                st = server.get("server_type") or {}
                used_cores += int(st.get("cores") or 0)
                used_ram += float(st.get("memory") or 0)
                type_info = type_by_name.get(str(st.get("name") or ""), {})
                loc = (server.get("location") or {}).get("name")
                price_row = next((x for x in (type_info.get("prices") or []) if x.get("location") == loc), None)
                monthly = (price_row or {}).get("price_monthly", {}).get("gross")
                if monthly is None:
                    estimate_complete = False
                else:
                    estimated_monthly += float(monthly)
            server_current, server_max = _limit_value(limits, {"servers", "server"})
            core_current, core_max = _limit_value(limits, {"cores", "cpu", "vcpus", "vcpus_per_server"})
            # Some Hetzner Cloud API versions do not expose project limits. Allow admin to
            # record the values shown in the Hetzner Console, while still showing live usage.
            async with aiosqlite.connect(DB_PATH) as db:
                manual_server_limit = await core.get_setting(db, f"provider_{account_id}_server_limit", "")
                manual_core_limit = await core.get_setting(db, f"provider_{account_id}_core_limit", "")
            if server_max is None and str(manual_server_limit).isdigit():
                server_max = int(manual_server_limit)
            if core_max is None and str(manual_core_limit).isdigit():
                core_max = int(manual_core_limit)
            # API limit current value is authoritative where supplied; calculated totals are fallback only.
            if server_current is None:
                server_current = len(servers)
            if core_current is None:
                core_current = used_cores
            limits_rows = "".join(
                f"<tr><td>{esc(str(item.get('description') or item.get('name') or 'منبع'))}</td>"
                f"<td>{esc(str((item.get('values') or {}).get('current', '—')))}</td>"
                f"<td>{esc(str((item.get('values') or {}).get('max', '—')))}</td></tr>"
                for item in limits
            )
            estimate_text = f"{estimated_monthly:.2f} {esc(currency)} / ماه" if estimate_complete else f"حداقل {estimated_monthly:.2f} {esc(currency)} / ماه؛ قیمت بعضی سرورها قابل محاسبه نبود"
            cards.append(f"""
            <section class='card'>
              <h2>🔑 {name} {'<span class="dash-pill">فعال</span>' if active else '<span class="dash-pill">غیرفعال</span>'}</h2>
              <div class='system-list'>
                <span>سرورهای فعلی</span><strong>{len(servers)} (حد API: {esc(str(server_max if server_max is not None else 'از API برنگشت'))})</strong>
                <span>CPU هسته‌های مصرف‌شده</span><strong>{used_cores} (حد API: {esc(str(core_max if core_max is not None else 'از API برنگشت'))})</strong>
                <span>RAM مجموع سرورها</span><strong>{used_ram:.1f} GB</strong>
                <span>هزینه ماهانه تخمینی سرورها</span><strong>{estimate_text}</strong>
              </div>
              <p class='msg'>⚠️ API رسمی Hetzner Cloud مبلغ بدهی/صورتحساب واقعی یا usage مالی جاری را ارائه نمی‌کند. عدد بالا فقط برآورد قیمت ماهانه سرورهای موجود است و شامل همه منابع اضافی/تخفیف‌ها/صورتحساب نهایی نیست.</p>
              <h3>جزئیات لیمیت‌های گزارش‌شده توسط API</h3>
              <div class='table-wrap'><table><tr><th>منبع</th><th>مصرف فعلی</th><th>حداکثر</th></tr>{limits_rows or '<tr><td colspan="3">API اطلاعات لیمیت برنگرداند.</td></tr>'}</table></div>
              <h3>ثبت لیمیت از کنسول Hetzner (در صورت نبود مقدار خودکار)</h3>
              <form method='post' action='/account-status/{account_id}/limits'>
                <label>حداکثر تعداد سرور</label><input name='server_limit' type='number' min='0' value='{esc(str(server_max) if server_max is not None else str(manual_server_limit))}'>
                <label>حداکثر تعداد هسته CPU</label><input name='core_limit' type='number' min='0' value='{esc(str(core_max) if core_max is not None else str(manual_core_limit))}'>
                <button class='btn' type='submit'>💾 ذخیره لیمیت دستی</button>
              </form>
              <p><a target='_blank' rel='noopener' href='https://console.hetzner.cloud/'>باز کردن Hetzner Cloud Console برای بررسی لیمیت و صورتحساب واقعی ↗</a></p>
            </section>""")
        except Exception as exc:
            cards.append(f"<section class='card'><h2>🔑 {name}</h2><div class='msg err'>خواندن اطلاعات این اکانت ناموفق بود: {esc(str(exc)[:300])}</div></section>")
    body = "<h1>📈 لیمیت، CPU و Usage اکانت‌ها</h1><p class='muted'>اطلاعات هر API به‌صورت جداگانه از Hetzner استعلام می‌شود.</p>" + "".join(cards)
    return page("وضعیت اکانت‌ها", body, "/account-status")


@app.post("/account-status/{pid}/limits")
async def account_status_save_limits(request: Request, pid: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    server_limit = str(form.get("server_limit", "")).strip()
    core_limit = str(form.get("core_limit", "")).strip()
    if (server_limit and not server_limit.isdigit()) or (core_limit and not core_limit.isdigit()):
        return RedirectResponse(f"/account-status?pid={pid}", status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM provider WHERE id=?", (pid,))
        if not await cur.fetchone():
            return RedirectResponse("/account-status", status_code=303)
        await core.set_setting(db, f"provider_{pid}_server_limit", server_limit)
        await core.set_setting(db, f"provider_{pid}_core_limit", core_limit)
        await db.commit()
    return RedirectResponse(f"/account-status?pid={pid}", status_code=303)


@app.post("/providers/add")
async def providers_add(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    label = str(form.get("label", "")).strip() or "بدون‌نام"
    token = str(form.get("token", "")).strip()
    api = core.HetznerAPI(token)
    try:
        await api.locations()
    except Exception as e:
        return RedirectResponse(f"/providers?err={urllib.parse.quote(str(e))}", status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM provider WHERE type='hetzner' AND active=1")
        active_count = (await cur.fetchone())[0]
        await db.execute(
            "INSERT INTO provider (label, api_token, active, created_at, type) VALUES (?,?,1,?,'hetzner')",
            (label, token, datetime.utcnow().isoformat()),
        )
        cur = await db.execute("SELECT last_insert_rowid()")
        new_pid = (await cur.fetchone())[0]
        if not active_count:
            await core.set_setting(db, "priority_provider_hetzner", str(new_pid))
        await db.commit()
    return RedirectResponse("/providers", status_code=303)


@app.post("/providers/{pid}/activate")
async def providers_activate(request: Request, pid: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT active FROM provider WHERE id=?", (pid,))
        row = await cur.fetchone()
        if row:
            await db.execute("UPDATE provider SET active=? WHERE id=?", (0 if row[0] else 1, pid))
            if not row[0]:
                cur = await db.execute("SELECT type FROM provider WHERE id=?", (pid,))
                ptype = (await cur.fetchone())[0] or "hetzner"
                current_priority = await core.get_setting(db, f"priority_provider_{ptype}", "")
                if not current_priority:
                    await core.set_setting(db, f"priority_provider_{ptype}", str(pid))
        await db.commit()
    return RedirectResponse("/providers", status_code=303)


@app.post("/providers/{pid}/priority")
async def providers_priority(request: Request, pid: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT type, active FROM provider WHERE id=?", (pid,))
        row = await cur.fetchone()
        if row and row[1]:
            await core.set_setting(db, f"priority_provider_{row[0] or 'hetzner'}", str(pid))
    return RedirectResponse("/providers", status_code=303)


@app.post("/providers/{pid}/delete")
async def providers_delete(request: Request, pid: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
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
    return RedirectResponse("/providers", status_code=303)


# ---------------------------------------------------------------------------
# درخواست‌های شارژ کیف پول
# ---------------------------------------------------------------------------
@app.get("/wallet-requests", response_class=HTMLResponse)
async def wallet_requests_list(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, user_id, amount, method, status, created_at FROM wallet_requests ORDER BY id DESC LIMIT 100"
        )
        reqs = await cur.fetchall()
    rows = "".join(
        f"<tr><td>#{r[0]}</td><td>{r[1]}</td><td>{core.fmt_price(r[2])}</td><td>{esc(r[3])}</td><td>{esc(r[4])}</td>"
        f"<td>"
        + (
            f"<form class='inline' method='post' action='/wallet-requests/{r[0]}/approve'><button class='btn small green' type='submit'>تایید</button></form>"
            f"<form class='inline' method='post' action='/wallet-requests/{r[0]}/reject'><button class='btn small red' type='submit'>رد</button></form>"
            if r[4] == "pending"
            else "-"
        )
        + "</td></tr>"
        for r in reqs
    )
    body = f"""
    <h1><span class="page-icon icon-green">💳</span> درخواست‌های شارژ کیف پول</h1>
    <div class="card"><table>
      <tr><th>#</th><th>کاربر</th><th>مبلغ</th><th>روش</th><th>وضعیت</th><th>عملیات</th></tr>
      {rows or "<tr><td colspan=6>درخواستی ثبت نشده.</td></tr>"}
    </table></div>
    """
    return page("درخواست‌های شارژ", body, "/wallet-requests")


@app.post("/wallet-requests/{req_id}/approve")
async def wallet_request_approve(request: Request, req_id: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, amount, status FROM wallet_requests WHERE id=?", (req_id,))
        row = await cur.fetchone()
        if row and row[2] == "pending":
            await db.execute("UPDATE wallet_requests SET status='approved' WHERE id=?", (req_id,))
            await db.execute("UPDATE users SET balance = balance + ? WHERE telegram_id=?", (row[1], row[0]))
            await db.commit()
            if core.BOT_TOKEN:
                try:
                    bot = Bot(token=core.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                    await bot.send_message(row[0], f"✅ کیف پول شما به مبلغ {core.fmt_price(row[1])} شارژ شد.")
                    await bot.session.close()
                except Exception:
                    pass
    return RedirectResponse("/wallet-requests", status_code=303)


@app.post("/wallet-requests/{req_id}/reject")
async def wallet_request_reject(request: Request, req_id: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, status FROM wallet_requests WHERE id=?", (req_id,))
        row = await cur.fetchone()
        if row and row[1] == "pending":
            await db.execute("UPDATE wallet_requests SET status='rejected' WHERE id=?", (req_id,))
            await db.commit()
            if core.BOT_TOKEN:
                try:
                    bot = Bot(token=core.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                    await bot.send_message(row[0], "❌ درخواست شارژ کیف پول شما رد شد.")
                    await bot.session.close()
                except Exception:
                    pass
    return RedirectResponse("/wallet-requests", status_code=303)


# ---------------------------------------------------------------------------
# سفارش‌ها
# ---------------------------------------------------------------------------
@app.get("/orders", response_class=HTMLResponse)
async def orders_list(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, user_id, plan_title, location, price, status, created_at FROM orders ORDER BY id DESC LIMIT 200"
        )
        orders = await cur.fetchall()
    rows = "".join(
        f"<tr><td>#{o[0]}</td><td>{o[1]}</td><td>{esc(o[2])}</td><td>{esc(o[3])}</td><td>{core.fmt_price(o[4])}</td><td>{esc(o[5])}</td></tr>"
        for o in orders
    )
    body = f"""
    <h1><span class="page-icon icon-teal">📦</span> سفارش‌ها ({len(orders)})</h1>
    <div class="card"><table>
      <tr><th>#</th><th>کاربر</th><th>پلن</th><th>لوکیشن</th><th>قیمت</th><th>وضعیت</th></tr>
      {rows or "<tr><td colspan=6>سفارشی ثبت نشده.</td></tr>"}
    </table></div>
    """
    return page("سفارش‌ها", body, "/orders")


# ---------------------------------------------------------------------------
# تنظیمات درگاه (OxaPay / آدرس واریز دستی)
# ---------------------------------------------------------------------------
@app.get("/support-settings", response_class=HTMLResponse)
async def support_settings_page(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        enabled = await core.get_setting(db, "tickets_enabled", "1")
        support_id = await core.get_setting(db, "support_id", "")
        support_message = await core.get_setting(db, "support_message", "برای ارتباط با پشتیبانی از اطلاعات زیر استفاده کنید.")
        cur = await db.execute("SELECT id, user_id, message, created_at, COALESCE(status,'open'), reply FROM tickets ORDER BY id DESC LIMIT 100")
        tickets = await cur.fetchall()
    ticket_rows = "".join(f"<tr><td>#{r[0]}</td><td>{esc(r[1])}</td><td>{esc((r[2] or '')[:200])}</td><td>{'باز' if r[4]=='open' else 'پاسخ‌داده‌شده'}</td><td>{esc((r[5] or '')[:200])}</td></tr>" for r in tickets)
    body = f"""
    <h1>🎫 تیکت و پشتیبانی</h1>
    <div class='card'><h3>تنظیمات پشتیبانی</h3>
      <form method='post' action='/support-settings/save'>
        <label>وضعیت سیستم تیکت</label><select name='tickets_enabled'><option value='1' {'selected' if enabled=='1' else ''}>فعال</option><option value='0' {'selected' if enabled!='1' else ''}>غیرفعال</option></select>
        <label>آیدی یا لینک پشتیبانی (مثلاً @support یا https://t.me/support)</label><input name='support_id' value='{esc(support_id)}' placeholder='@support'>
        <label>متن نمایشی برای کاربر</label><textarea name='support_message' rows='4'>{esc(support_message)}</textarea>
        <button class='btn' type='submit'>💾 ذخیره تنظیمات</button>
      </form>
    </div>
    <div class='card'><h3>آخرین تیکت‌ها</h3><div class='table-wrap'><table><tr><th>شناسه</th><th>کاربر</th><th>پیام</th><th>وضعیت</th><th>آخرین پاسخ</th></tr>{ticket_rows or '<tr><td colspan="5">هنوز تیکتی ثبت نشده.</td></tr>'}</table></div>
    <p class='muted'>برای پاسخ دادن به تیکت‌ها از منوی «🎫 تیکت‌ها و تنظیمات پشتیبانی» در ربات تلگرام استفاده کنید.</p></div>
    """
    return page("تیکت و پشتیبانی", body, "/support-settings")


@app.post("/support-settings/save")
async def support_settings_save(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    async with aiosqlite.connect(DB_PATH) as db:
        await core.set_setting(db, "tickets_enabled", "1" if str(form.get("tickets_enabled", "0")) == "1" else "0")
        await core.set_setting(db, "support_id", str(form.get("support_id", "")).strip())
        await core.set_setting(db, "support_message", str(form.get("support_message", "")).strip())
    return RedirectResponse("/support-settings", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        oxa_key = await core.get_setting(db, "oxapay_api_key")
        now_key = await core.get_setting(db, "nowpayments_api_key")
        now_currency = await core.get_setting(db, "nowpayments_pay_currency", core.NOWPAYMENTS_PAY_CURRENCY)
        addr = await core.get_setting(db, "manual_address", "")
    ok = request.query_params.get("ok")
    active_currencies = request.query_params.get("currencies")
    msg = '<div class="msg ok">ذخیره شد.</div>' if ok else ""
    currencies_msg = ""
    if active_currencies is not None:
        currencies_msg = (
            f'<div class="msg ok">ارزهای فعال حساب شما: {esc(active_currencies) or "هیچ‌کدام — از داشبورد NOWPayments یک ارز فعال کنید."}</div>'
        )
    elif request.query_params.get("cur_err"):
        currencies_msg = f'<div class="msg err">خطا در دریافت لیست ارزها: {esc(request.query_params.get("cur_err"))}</div>'
    body = f"""
    <h1><span class="page-icon icon-purple">⚙️</span> تنظیمات درگاه پرداخت</h1>
    {msg}
    <div class="card">
      <h3><span class="mini-icon icon-blue">💠</span> درگاه ارز دیجیتال (OxaPay)</h3>
      <p style="color:var(--txt-dim);font-size:13px;">وضعیت فعلی: {"<span class='badge ok'>فعال (..." + esc(oxa_key[-4:]) + ")</span>" if oxa_key else "<span class='badge off'>غیرفعال</span>"}</p>
      <form method="post" action="/settings/oxapay">
        <label>Merchant API Key</label>
        <input name="key" value="{esc(oxa_key or '')}">
        <button class="btn" type="submit">ذخیره</button>
      </form>
    </div>
    <div class="card">
      <h3><span class="mini-icon icon-teal">💠</span> درگاه ارز دیجیتال (NOWPayments)</h3>
      <p style="color:var(--txt-dim);font-size:13px;">وضعیت فعلی: {"<span class='badge ok'>فعال (..." + esc(now_key[-4:]) + ")</span>" if now_key else "<span class='badge off'>غیرفعال</span>"}</p>
      {currencies_msg}
      <form method="post" action="/settings/nowpayments">
        <label>API Key</label>
        <input name="key" value="{esc(now_key or '')}">
        <label>کد ارز پرداخت (مثلاً usdttrc20، usdterc20، btc، trx)</label>
        <input name="currency" value="{esc(now_currency or '')}">
        <button class="btn" type="submit">ذخیره</button>
      </form>
      <a class="btn gray small" href="/settings/nowpayments/currencies" style="margin-top:4px;">📋 مشاهده ارزهای فعال حساب من</a>
      <p style="color:var(--txt-dim);font-size:12px;margin-top:10px;">⚠️ اگر خطای «400 Bad Request» می‌گیرید یعنی ارز انتخابی روی حساب NOWPayments شما فعال نیست — روی دکمه بالا بزنید تا لیست درست را ببینید.</p>
    </div>
    <div class="card">
      <h3><span class="mini-icon icon-yellow">🏦</span> آدرس واریز دستی (کارت/ولت)</h3>
      <form method="post" action="/settings/manual-address">
        <label>متن آدرس/شماره کارت</label>
        <textarea name="address" rows="3">{esc(addr or '')}</textarea>
        <button class="btn" type="submit">ذخیره</button>
      </form>
    </div>
    """
    return page("تنظیمات", body, "/settings")


@app.get("/settings/nowpayments/currencies")
async def settings_nowpayments_currencies(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        now_key = await core.get_setting(db, "nowpayments_api_key")
    if not now_key:
        return RedirectResponse("/settings?cur_err=" + urllib.parse.quote("ابتدا API Key را ذخیره کنید"), status_code=303)
    try:
        currencies = await core.nowpayments_available_currencies(now_key)
    except Exception as e:
        return RedirectResponse(f"/settings?cur_err={urllib.parse.quote(str(e))}", status_code=303)
    return RedirectResponse(f"/settings?currencies={urllib.parse.quote(', '.join(c.upper() for c in currencies))}", status_code=303)


@app.post("/settings/oxapay")
async def settings_oxapay(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    async with aiosqlite.connect(DB_PATH) as db:
        await core.set_setting(db, "oxapay_api_key", str(form.get("key", "")).strip())
    return RedirectResponse("/settings?ok=1", status_code=303)


@app.post("/settings/nowpayments")
async def settings_nowpayments(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    async with aiosqlite.connect(DB_PATH) as db:
        await core.set_setting(db, "nowpayments_api_key", str(form.get("key", "")).strip())
        currency = str(form.get("currency", "")).strip().lower()
        if currency:
            await core.set_setting(db, "nowpayments_pay_currency", currency)
    return RedirectResponse("/settings?ok=1", status_code=303)




@app.post("/settings/manual-address")
async def settings_manual_address(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    async with aiosqlite.connect(DB_PATH) as db:
        await core.set_setting(db, "manual_address", str(form.get("address", "")).strip())
    return RedirectResponse("/settings?ok=1", status_code=303)


# ---------------------------------------------------------------------------
# پیام همگانی
# ---------------------------------------------------------------------------
@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_form(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    sent = request.query_params.get("sent")
    msg = f'<div class="msg ok">پیام برای {esc(sent)} کاربر ارسال شد.</div>' if sent else ""
    body = f"""
    <h1><span class="page-icon icon-pink">📢</span> پیام همگانی</h1>
    {msg}
    <div class="card">
      <form method="post" action="/broadcast/send">
        <label>متن پیام</label>
        <textarea name="text" rows="8" required>☁️ <b>CLOUD_CENTER</b>

🔥 <b>جشنواره تخفیف ویژه سرورها شروع شد!</b>

🎁 با کد تخفیف زیر، از <b>۷٪ تخفیف</b> بهره‌مند شوید:

🏷 <b>کد تخفیف: <code>CLOUD7</code></b>

✅ شامل خرید سرور جدید و تمدید سرورهای فعال

💎 سرور خود را با هزینه کمتر تهیه یا تمدید کنید.
⏳ فرصت استفاده از این تخفیف را از دست ندهید!

🚀 <b>CLOUD_CENTER | قدرت، سرعت و اطمینان</b></textarea>
        <label><input type="checkbox" name="add_buy_button" value="1" checked> افزودن دکمه «🛒 خرید سرور» زیر پیام</label>
        <button class="btn" type="submit">ارسال به همه کاربران</button>
      </form>
    </div>
    """
    return page("پیام همگانی", body, "/broadcast")


@app.post("/broadcast/send")
async def broadcast_send(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    text = str(form.get("text", "")).strip()
    if not text or not core.BOT_TOKEN:
        return RedirectResponse("/broadcast", status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT telegram_id FROM users")
        users = await cur.fetchall()
    bot = Bot(token=core.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    sent = 0
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 خرید سرور", url="https://t.me/Cloud_center_bot")]]) if form.get("add_buy_button") == "1" else None
    for (uid,) in users:
        try:
            await bot.send_message(uid, text, reply_markup=keyboard)
            sent += 1
        except Exception:
            pass
    await bot.session.close()
    return RedirectResponse(f"/broadcast?sent={sent}", status_code=303)



# --- فروشگاه محصولات و زرین‌پال ---
def _shop_page(title, body):
    return page(title, body, "/products")

@app.get("/products",response_class=HTMLResponse)
async def products_admin(request:Request):
    if not get_session(request): return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cats=await (await db.execute("SELECT id,name,active FROM product_categories ORDER BY name")).fetchall()
        prods=await (await db.execute("SELECT p.id,p.name,c.name,p.price_usd,p.stock,p.active,p.delivery_mode FROM products p LEFT JOIN product_categories c ON c.id=p.category_id WHERE p.active=1 ORDER BY p.id DESC")).fetchall()
    opts=''.join(f'<option value="{c[0]}">{esc(c[1])}</option>' for c in cats)
    catrows=''.join(f'<tr><td>{esc(c[1])}</td><td>{"فعال" if c[2] else "غیرفعال"}</td><td><form method="post" action="/product-categories/{c[0]}/toggle"><button class="btn small gray">تغییر وضعیت</button></form></td></tr>' for c in cats)
    rows=''.join(f'<tr><td>#{r[0]}</td><td>{esc(r[1])}</td><td>{esc(r[2] or "-")}</td><td>${float(r[3]):.2f}</td><td>{r[4]}</td><td>{"تحویل خودکار" if r[6]=="automatic" else "دستی"}</td><td>{"فعال" if r[5] else "غیرفعال"}</td><td><a class="btn small" href="/products/{r[0]}/inventory">مدیریت اکانت‌ها</a> <a class="btn small" href="/products/{r[0]}/edit">ویرایش</a> <form style="display:inline" method="post" action="/products/{r[0]}/toggle"><button class="btn small gray">تغییر وضعیت</button></form></td></tr>' for r in prods)
    body=f'''<h1>مدیریت فروشگاه</h1><div class="grid"><div class="card"><h3>دسته جدید</h3><form method="post" action="/product-categories/add"><input name="name" required placeholder="نام دسته"><button class="btn">ثبت</button></form></div><div class="card"><h3>محصول جدید</h3><form method="post" action="/products/add"><label>نام</label><input name="name" required><label>دسته</label><select name="category_id"><option value="">بدون دسته</option>{opts}</select><label>توضیحات</label><textarea name="description"></textarea><label>آدرس تصویر</label><input name="image_url"><label>قیمت دلار (USD)</label><input type="number" name="price" min="0.01" step="0.01" required><label>موجودی اولیه (برای تحویل دستی)</label><input type="number" name="stock" min="0" value="1" required><label>روش تحویل</label><select name="delivery_mode"><option value="manual">دستی</option><option value="automatic">خودکار از موجودی اکانت‌های آماده</option></select><button class="btn green">ذخیره محصول</button></form></div></div><div class="card"><h3>دسته‌بندی‌ها</h3><table>{catrows}</table></div><div class="card"><h3>محصولات</h3><table><tr><th>شناسه</th><th>نام</th><th>دسته</th><th>قیمت</th><th>موجودی</th><th>تحویل</th><th>وضعیت</th><th>عملیات</th></tr>{rows}</table></div>'''
    return page("فروشگاه",body,"/products")

@app.post("/product-categories/add")
async def category_add(request:Request):
    if not get_session(request): return RedirectResponse("/login")
    f=await request.form(); name=str(f.get("name","")).strip()[:80]
    if name:
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR IGNORE INTO product_categories(name,created_at) VALUES(?,?)",(name,datetime.utcnow().isoformat())); await db.commit()
    return RedirectResponse("/products",status_code=303)

@app.post("/product-categories/{cid}/toggle")
async def category_toggle(request:Request,cid:int):
    if not get_session(request): return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE product_categories SET active=1-active WHERE id=?",(cid,)); await db.commit()
    return RedirectResponse("/products",status_code=303)

@app.post("/products/add")
async def product_add(request:Request):
    if not get_session(request): return RedirectResponse("/login")
    f=await request.form(); name=str(f.get("name","")).strip()[:150]; desc=str(f.get("description", ""))[:5000]; img=str(f.get("image_url", ""))[:1000]; mode=str(f.get("delivery_mode","manual")); mode=mode if mode in ("manual","automatic") else "manual"
    try: price=float(f.get("price",0)); stock=int(f.get("stock",1)); cid=int(f.get("category_id")) if f.get("category_id") else None
    except: return RedirectResponse("/products",status_code=303)
    if name and price>0 and stock>=0:
        if mode == "automatic": stock = 0
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT INTO products(category_id,name,description,image_url,price_usd,stock,created_at,updated_at,delivery_mode) VALUES(?,?,?,?,?,?,?,?,?)",(cid,name,desc,img,price,stock,datetime.utcnow().isoformat(),datetime.utcnow().isoformat(),mode)); await db.commit()
    return RedirectResponse("/products",status_code=303)

@app.get("/products/{pid}/edit",response_class=HTMLResponse)
async def product_edit_form(request:Request,pid:int):
    if not get_session(request): return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        r=await (await db.execute("SELECT id,name,category_id,description,image_url,price_usd,stock,delivery_mode FROM products WHERE id=?",(pid,))).fetchone(); cats=await (await db.execute("SELECT id,name FROM product_categories")).fetchall()
    if not r:return RedirectResponse("/products")
    opts='<option value="">بدون دسته</option>'+''.join(f'<option value="{c[0]}" {"selected" if c[0]==r[2] else ""}>{esc(c[1])}</option>' for c in cats)
    body=f'<h1>ویرایش محصول</h1><div class="card"><form method="post" action="/products/{pid}/edit"><label>نام</label><input name="name" value="{esc(r[1])}" required><label>دسته</label><select name="category_id">{opts}</select><label>توضیحات</label><textarea name="description">{esc(r[3])}</textarea><label>تصویر URL</label><input name="image_url" value="{esc(r[4])}"><label>قیمت دلار (USD)</label><input type="number" name="price" value="{r[5]}" min="0.01" step="0.01"><label>موجودی دستی (در حالت خودکار از اکانت‌های آماده محاسبه می‌شود)</label><input type="number" name="stock" value="{r[6]}" min="0"><label>روش تحویل</label><select name="delivery_mode"><option value="manual" {"selected" if r[7]!="automatic" else ""}>دستی</option><option value="automatic" {"selected" if r[7]=="automatic" else ""}>خودکار از موجودی اکانت‌های آماده</option></select><button class="btn green">ذخیره</button></form></div>'
    return page("ویرایش محصول",body,"/products")

@app.post("/products/{pid}/edit")
async def product_edit_save(request:Request,pid:int):
    if not get_session(request): return RedirectResponse("/login")
    f=await request.form(); mode=str(f.get("delivery_mode","manual")); mode=mode if mode in ("manual","automatic") else "manual"
    try: price=float(f.get("price",0)); stock=int(f.get("stock",1)); cid=int(f.get("category_id")) if f.get("category_id") else None
    except: return RedirectResponse("/products",status_code=303)
    if price>0 and stock>=0:
        async with aiosqlite.connect(DB_PATH) as db:
            if mode == "automatic":
                await db.execute("UPDATE products SET name=?,category_id=?,description=?,image_url=?,price_usd=?,stock=(SELECT COUNT(*) FROM product_inventory WHERE product_id=? AND status='available'),delivery_mode=?,updated_at=? WHERE id=?",(str(f.get("name",""))[:150],cid,str(f.get("description",""))[:5000],str(f.get("image_url",""))[:1000],price,pid,mode,datetime.utcnow().isoformat(),pid))
            else:
                await db.execute("UPDATE products SET name=?,category_id=?,description=?,image_url=?,price_usd=?,stock=?,delivery_mode=?,updated_at=? WHERE id=?",(str(f.get("name",""))[:150],cid,str(f.get("description",""))[:5000],str(f.get("image_url",""))[:1000],price,stock,mode,datetime.utcnow().isoformat(),pid))
            await db.commit()
    return RedirectResponse("/products",status_code=303)

@app.post("/products/{pid}/toggle")
async def product_toggle(request:Request,pid:int):
    if not get_session(request): return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE products SET active=1-active,updated_at=? WHERE id=?",(datetime.utcnow().isoformat(),pid)); await db.commit()
    return RedirectResponse("/products",status_code=303)

async def _store_inventory_photo(photo: UploadFile | None) -> str:
    if not photo or not photo.filename:
        return ""
    allowed = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    ext = allowed.get((photo.content_type or "").lower())
    if not ext:
        raise ValueError("فرمت تصویر باید JPG، PNG، WEBP یا GIF باشد.")
    raw = await photo.read(5 * 1024 * 1024 + 1)
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise ValueError("حجم تصویر باید حداکثر ۵ مگابایت باشد.")
    filename = secrets.token_hex(16) + ext
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / filename).write_bytes(raw)
    return "/static/uploads/" + filename


@app.get("/products/{pid}/inventory",response_class=HTMLResponse)
async def product_inventory_page(request:Request,pid:int):
    if not get_session(request):
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        product=await (await db.execute("SELECT name,delivery_mode FROM products WHERE id=?",(pid,))).fetchone()
        rows=await (await db.execute(
            "SELECT id,payload,status,order_id,created_at,image_url FROM product_inventory "
            "WHERE product_id=? ORDER BY id DESC LIMIT 300",(pid,)
        )).fetchall()
        count=await (await db.execute(
            "SELECT COUNT(*) FROM product_inventory WHERE product_id=? AND status='available'",(pid,)
        )).fetchone()
    if not product:
        return RedirectResponse("/products")
    tr=[]
    for r in rows:
        photo=f'<a href="{esc(r[5])}" target="_blank"><img src="{esc(r[5])}" style="width:76px;height:60px;object-fit:contain;border-radius:8px"></a>' if r[5] else "—"
        payload_preview=esc((r[1] or "")[:180])
        if r[2]=="available":
            actions=f'''
            <details><summary class="btn small">ویرایش اکانت</summary>
              <form method="post" enctype="multipart/form-data" action="/products/{pid}/inventory/{r[0]}/edit">
                <label>مشخصات اکانت</label><textarea name="payload" rows="5" required>{esc(r[1])}</textarea>
                <label>آدرس تصویر (اختیاری)</label><input name="image_url" value="{esc(r[5] or "")}">
                <label>یا بارگذاری تصویر جدید</label><input type="file" name="photo" accept="image/png,image/jpeg,image/webp,image/gif">
                <button class="btn green">ذخیره تغییرات</button>
              </form>
            </details>
            <form method="post" action="/products/{pid}/inventory/{r[0]}/delete" onsubmit="return confirm('این اکانت از موجودی حذف شود؟')">
              <button class="btn small gray">حذف اکانت</button>
            </form>'''
        else:
            actions='<span>ثبت‌شده در سفارش؛ برای حفظ سوابق قابل ویرایش یا حذف نیست</span>'
        tr.append(
            f'<tr><td>#{r[0]}</td><td>{photo}</td><td><pre style="white-space:pre-wrap;max-width:320px">{payload_preview}</pre></td>'
            f'<td>{"آماده فروش" if r[2]=="available" else "فروخته‌شده"}</td><td>{r[3] or "-"}</td>'
            f'<td>{esc(r[4] or "")}</td><td>{actions}</td></tr>'
        )
    body=f'''<h1>مدیریت اکانت‌های: {esc(product[0])}</h1>
    <div class="card"><p>اکانت‌های آماده فروش: <b>{count[0]}</b> | هر ردیف یک اکانت مستقل است. تصویر هر اکانت جداگانه در صفحه جزئیات فروشگاه نمایش داده می‌شود.</p>
    <form method="post" enctype="multipart/form-data" action="/products/{pid}/inventory/add">
      <label>مشخصات کامل یک اکانت</label>
      <textarea name="items" rows="7" required maxlength="10000" placeholder="نام کاربری، رمز عبور، توضیحات و جزئیات اکانت"></textarea>
      <label>آدرس تصویر (اختیاری)</label><input name="image_url" maxlength="1000" placeholder="https://...">
      <label>یا بارگذاری تصویر جزئیات (حداکثر ۵ مگابایت)</label>
      <input type="file" name="photo" accept="image/png,image/jpeg,image/webp,image/gif">
      <button class="btn green">ثبت اکانت جدید</button>
    </form></div>
    <div class="card"><h3>فهرست اکانت‌ها</h3><div style="overflow-x:auto"><table>
    <tr><th>شناسه</th><th>تصویر</th><th>مشخصات (بخشی)</th><th>وضعیت</th><th>سفارش</th><th>زمان ثبت</th><th>عملیات</th></tr>
    {''.join(tr) or '<tr><td colspan="7">هنوز اکانتی ثبت نشده است.</td></tr>'}
    </table></div></div><p><a href="/products">بازگشت به فروشگاه</a></p>'''
    return page("مدیریت اکانت‌ها",body,"/products")


@app.post("/products/{pid}/inventory/add")
async def product_inventory_add(request:Request,pid:int,photo:UploadFile|None=File(default=None)):
    if not get_session(request):
        return RedirectResponse("/login")
    f=await request.form()
    payload=str(f.get("items","")).strip()
    image_url=str(f.get("image_url","")).strip()[:1000]
    if not payload or len(payload)>10000:
        return RedirectResponse(f"/products/{pid}/inventory",status_code=303)
    try:
        uploaded=await _store_inventory_photo(photo)
        if uploaded:
            image_url=uploaded
    except ValueError:
        return page("تصویر نامعتبر",'<div class="card">فرمت یا حجم تصویر معتبر نیست. حداکثر ۵ مگابایت مجاز است.</div>',f"/products/{pid}/inventory")
    async with aiosqlite.connect(DB_PATH) as db:
        exists=await (await db.execute("SELECT id FROM products WHERE id=?",(pid,))).fetchone()
        if exists:
            now=datetime.utcnow().isoformat()
            await db.execute(
                "INSERT INTO product_inventory(product_id,payload,status,created_at,image_url) VALUES(?,?,'available',?,?)",
                (pid,payload,now,image_url),
            )
            await db.execute(
                "UPDATE products SET stock=(SELECT COUNT(*) FROM product_inventory WHERE product_id=? AND status='available'),delivery_mode='automatic',active=1,updated_at=? WHERE id=?",
                (pid,now,pid),
            )
            await db.commit()
    return RedirectResponse(f"/products/{pid}/inventory",status_code=303)


@app.post("/products/{pid}/inventory/{item_id}/edit")
async def product_inventory_edit(request:Request,pid:int,item_id:int,photo:UploadFile|None=File(default=None)):
    if not get_session(request):
        return RedirectResponse("/login")
    f=await request.form()
    payload=str(f.get("payload","")).strip()
    image_url=str(f.get("image_url","")).strip()[:1000]
    if not payload or len(payload)>10000:
        return RedirectResponse(f"/products/{pid}/inventory",status_code=303)
    try:
        uploaded=await _store_inventory_photo(photo)
        if uploaded:
            image_url=uploaded
    except ValueError:
        return page("تصویر نامعتبر",'<div class="card">فرمت یا حجم تصویر معتبر نیست.</div>',f"/products/{pid}/inventory")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE product_inventory SET payload=?,image_url=? WHERE id=? AND product_id=? AND status='available'",
            (payload,image_url,item_id,pid),
        )
        await db.commit()
    return RedirectResponse(f"/products/{pid}/inventory",status_code=303)


@app.post("/products/{pid}/inventory/{item_id}/delete")
async def product_inventory_delete(request:Request,pid:int,item_id:int):
    if not get_session(request):
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        now=datetime.utcnow().isoformat()
        await db.execute(
            "DELETE FROM product_inventory WHERE id=? AND product_id=? AND status='available'",
            (item_id,pid),
        )
        await db.execute(
            "UPDATE products SET stock=(SELECT COUNT(*) FROM product_inventory WHERE product_id=? AND status='available'),updated_at=? WHERE id=?",
            (pid,now,pid),
        )
        await db.execute(
            "UPDATE products SET active=0 WHERE id=? AND stock<=0",
            (pid,),
        )
        await db.commit()
    return RedirectResponse(f"/products/{pid}/inventory",status_code=303)


@app.get("/admin-wallet-purchase", response_class=HTMLResponse)
async def admin_wallet_purchase_page(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        users = await (await db.execute("SELECT telegram_id,full_name,username,balance FROM users ORDER BY telegram_id DESC LIMIT 500")).fetchall()
        products = await (await db.execute("SELECT id,name,price_usd,stock,delivery_mode FROM products WHERE active=1 ORDER BY id DESC")).fetchall()
    user_opts = "".join(
        f'<option value="{u[0]}">{u[0]} · {esc(u[1] or u[2] or "-")} · موجودی ${float(u[3] or 0):.2f}</option>'
        for u in users
    )
    product_opts = "".join(
        f'<option value="{p[0]}">#{p[0]} · {esc(p[1])} · ${float(p[2]):.2f} · موجودی {p[3]} · {("خودکار" if p[4]=="automatic" else "دستی")}</option>'
        for p in products
    )
    body = f"""
    <h1>خرید مدیریتی از کیف پول مشتری</h1>
    <div class="card">
      <p>این فرم فقط برای ادمین است. مبلغ از کیف پول مشتری کسر می‌شود و حتی اگر موجودی کافی نباشد، مانده منفی خواهد شد. مشتری به این قابلیت دسترسی ندارد.</p>
      <form method="post" action="/admin-wallet-purchase">
        <label>شناسه تلگرام / شناسه حساب مشتری</label>
        <input name="user_id" type="number" required placeholder="Telegram ID یا شناسه حساب ایمیلی">
        <label>محصول</label><select name="product_id" required>{product_opts}</select>
        <label>تعداد</label><input name="quantity" type="number" min="1" max="50" value="1" required>
        <button class="btn green">تأیید خرید و کسر از کیف پول مشتری</button>
      </form>
    </div>
    <div class="card"><h3>مشتریان ثبت‌شده</h3><select onchange="document.querySelector('[name=user_id]').value=this.value"><option value="">انتخاب سریع مشتری</option>{user_opts}</select></div>
    """
    return page("خرید مدیریتی", body, "/admin-wallet-purchase")


@app.post("/admin-wallet-purchase")
async def admin_wallet_purchase_do(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    try:
        user_id = int(form.get("user_id", 0))
        product_id = int(form.get("product_id", 0))
        quantity = int(form.get("quantity", 1))
    except (TypeError, ValueError):
        return RedirectResponse("/admin-wallet-purchase", status_code=303)
    if user_id == 0 or product_id <= 0 or quantity < 1 or quantity > 50:
        return RedirectResponse("/admin-wallet-purchase", status_code=303)
    order_id = None
    total = 0.0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        user = await (await db.execute("SELECT balance,full_name,email FROM users WHERE telegram_id=?", (user_id,))).fetchone()
        product = await (await db.execute("SELECT name,price_usd,stock,delivery_mode FROM products WHERE id=? AND active=1", (product_id,))).fetchone()
        if not user or not product or int(product[2] or 0) < quantity:
            await db.rollback()
            return page("خطا", '<div class="card">مشتری یا محصول معتبر نیست، یا موجودی محصول کافی نیست.</div>', "/admin-wallet-purchase")
        if product[3] == "automatic":
            available_items = await (await db.execute(
                "SELECT COUNT(*) FROM product_inventory WHERE product_id=? AND status='available'",
                (product_id,),
            )).fetchone()
            if not available_items or int(available_items[0] or 0) < quantity:
                await db.rollback()
                return page("خطا", '<div class="card">تعداد اکانت‌های آماده تحویل کافی نیست؛ خرید ثبت نشد و کیف پول تغییری نکرد.</div>', "/admin-wallet-purchase")
        total = round(float(product[1]) * quantity, 6)
        if total <= 0:
            await db.rollback()
            return page("خطا", '<div class="card">قیمت محصول معتبر نیست.</div>', "/admin-wallet-purchase")
        admin_free = user_id in core.ADMIN_IDS
        charge_total = 0.0 if admin_free else total
        # خرید مدیریتی از کیف پول دلاری است؛ نرخ ذخیره‌شده فقط برای گزارش معادل ریالی استفاده می‌شود.
        try:
            rate = float(await core.get_setting(db, "wallet_usd_to_toman", "100000") or 100000)
        except (TypeError, ValueError):
            rate = 100000.0
        if rate <= 0:
            rate = 100000.0
        now = datetime.utcnow().isoformat()
        cur = await db.execute(
            "INSERT INTO product_orders(product_id,product_name,quantity,customer_name,mobile,email,amount_usd,amount_toman,status,created_at,user_id,wallet_used_usd,gateway_method,gateway_due_toman,currency_rate) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (product_id, product[0], quantity, user[1] or str(user_id), "", user[2] or "", charge_total,
             int(round(charge_total * rate)), "pending_payment", now, user_id, charge_total,
             "admin_free" if admin_free else "wallet", 0, rate),
        )
        order_id = cur.lastrowid
        reference = f"admin-product-order-{order_id}-debit"
        balance_after = float(user[0] or 0) - charge_total
        await db.execute("UPDATE users SET balance=balance-? WHERE telegram_id=?", (charge_total, user_id))
        await db.execute(
            "INSERT INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)",
            (user_id, -charge_total, "admin_free_product_purchase" if admin_free else "admin_product_purchase", reference, now),
        )
        await db.execute(
            "INSERT INTO admin_wallet_actions(admin_actor,target_user_id,action,amount_usd,reference,details,created_at) VALUES(?,?,?,?,?,?,?)",
            (str(sess.get("username") or PANEL_USERNAME), user_id, "product_purchase_free" if admin_free else "product_purchase", charge_total, reference,
             f"product_id={product_id};product={product[0]};quantity={quantity};order_id={order_id};admin_free={int(admin_free)};balance_after={balance_after:.6f}", now),
        )
        await db.commit()
    try:
        token = await core.complete_product_order(order_id, "ADMIN_WALLET")
    except Exception:
        core.logger.exception("Admin wallet product order completion failed: order_id=%s", order_id)
        try:
            await core.refund_product_wallet(order_id)
        except Exception:
            core.logger.exception("Could not refund failed admin wallet product order: order_id=%s", order_id)
        return page(
            "خطا در تحویل سفارش",
            f'<div class="card"><h2>پردازش سفارش #{order_id} کامل نشد</h2>'
            '<p>خطا ثبت شد. در صورت کسر مبلغ، سیستم تلاش کرد آن را به کیف پول مشتری برگرداند. '
            'لطفاً وضعیت سفارش و تراکنش را بررسی کنید.</p></div>',
            "/admin-wallet-purchase",
        )
    async with aiosqlite.connect(DB_PATH) as db:
        order = await (await db.execute("SELECT status FROM product_orders WHERE id=?", (order_id,))).fetchone()
        items = await (await db.execute("SELECT payload FROM product_inventory WHERE order_id=? ORDER BY id", (order_id,))).fetchall()
    delivered = "\n\n".join(x[0] for x in items)
    status = order[0] if order else "unknown"
    delivery_notice = ""
    if token and user_id > 0 and core.BOT_TOKEN:
        try:
            base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
            if base:
                bot_client = Bot(token=core.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                try:
                    await bot_client.send_message(
                        user_id,
                        f"✅ خرید شما توسط مدیریت ثبت و تحویل شد. سفارش #{order_id} · {esc(product[0])} × {quantity}\n"
                        f"مبلغ ثبت‌شده: ${total:.2f}\n"
                        f'<a href="{base}/shop/delivery/{token}">دریافت مشخصات اکانت</a>',
                    )
                    delivery_notice = "<p>لینک تحویل برای مشتری در تلگرام ارسال شد.</p>"
                finally:
                    await bot_client.session.close()
        except Exception:
            delivery_notice = "<p>⚠️ ارسال پیام تلگرام ناموفق بود؛ لینک تحویل را از پنل به مشتری بدهید.</p>"
    delivery_html = f'<div class="card"><h3>اطلاعات تحویل</h3><pre style="white-space:pre-wrap;direction:ltr;text-align:left">{esc(delivered)}</pre></div>' if delivered else ""
    message = (
        f'<div class="card"><h2>سفارش مدیریتی #{order_id} ثبت شد</h2>'
        f'<p>مشتری: {user_id} | محصول: {esc(product[0])} × {quantity}</p>'
        f'<p>مبلغ کسرشده: ${charge_total:.2f}{" (خرید رایگان مدیر)" if admin_free else ""} | مانده جدید: ${balance_after:.2f}</p>'
        f'<p>وضعیت سفارش: {esc(status)}</p>'
        f'<p>ثبت مالی و نام ادمین در گزارش تراکنش‌ها ذخیره شد.</p>{delivery_notice}</div>{delivery_html}'
    )
    return page("خرید مدیریتی ثبت شد", message, "/admin-wallet-purchase")



@app.get("/admin-server-purchase", response_class=HTMLResponse)
async def admin_server_purchase_page(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        users = await (await db.execute("SELECT telegram_id,full_name,email,balance FROM users ORDER BY telegram_id DESC LIMIT 500")).fetchall()
        providers = await core.get_enabled_provider_types(db)
        plans = await (await db.execute("SELECT id,title,provider_type,location,server_type,cpu,ram,disk FROM plans WHERE active=1 ORDER BY provider_type,title")).fetchall()
    user_opts = "".join(f'<option value="{u[0]}">{u[0]} · {esc(u[1] or u[2] or "-")} · موجودی USD {float(u[3] or 0):.2f}</option>' for u in users)
    provider_opts = "".join(f'<option value="{esc(x)}">{esc(core.PROVIDER_LABELS.get(x,x))}</option>' for x in providers)
    plan_opts = "".join(f'<option value="{pl[0]}">{esc(pl[2])} · #{pl[0]} · {esc(pl[1])} · {esc(pl[5] or "-")} CPU / {esc(pl[6] or "-")} RAM · {esc(pl[3] or "همه لوکیشن‌ها")}</option>' for pl in plans)
    body = f"""
    <h1>خرید سرور برای مشتری توسط ادمین</h1>
    <div class="card"><p>این عملیات فقط برای ادمین است. مبلغ از کیف پول مشتری کسر می‌شود و مانده می‌تواند منفی شود. اگر ساخت سرور ناموفق باشد، مبلغ کیف پول برمی‌گردد.</p>
    <form method="post" action="/admin-server-purchase">
    <label>شناسه حساب مشتری (تلگرام یا شناسه داخلی حساب ایمیلی)</label><input name="user_id" type="number" required>
    <label>سرویس‌دهنده</label><select name="provider" required>{provider_opts}</select>
    <label>پلن فعال</label><select name="plan_id" required>{plan_opts}</select>
    <label>لوکیشن مطابق پلن</label><input name="location" required placeholder="مثلاً nbg1">
    <label>شناسه سیستم‌عامل / Image ID</label><input name="image" required>
    <button class="btn green">ثبت خرید و ساخت سرور</button></form></div>
    <div class="card"><h3>انتخاب سریع مشتری</h3><select onchange="document.querySelector('[name=user_id]').value=this.value"><option value="">انتخاب مشتری</option>{user_opts}</select></div>
    """
    return page("خرید سرور مدیریتی", body, "/admin-server-purchase")


@app.post("/admin-server-purchase")
async def admin_server_purchase_do(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    form = await request.form()
    try:
        user_id = int(form.get("user_id", 0))
        plan_id = int(form.get("plan_id", 0))
    except (TypeError, ValueError):
        return RedirectResponse("/admin-server-purchase", status_code=303)
    provider = str(form.get("provider", "")).strip()
    location = str(form.get("location", "")).strip()
    image_id = str(form.get("image", "")).strip()
    if not user_id or plan_id <= 0 or not provider or not location or not image_id:
        return RedirectResponse("/admin-server-purchase", status_code=303)

    async with aiosqlite.connect(DB_PATH) as db:
        if provider not in await core.get_enabled_provider_types(db):
            return page("خطا", '<div class="card">سرویس‌دهنده فعال نیست.</div>', "/admin-server-purchase")
        plan = await (await db.execute("SELECT id,title,server_type,markup_percent FROM plans WHERE id=? AND active=1 AND provider_type=? AND (location IS NULL OR location='' OR location=?)", (plan_id, provider, location))).fetchone()
        user = await (await db.execute("SELECT balance FROM users WHERE telegram_id=?", (user_id,))).fetchone()
        providers = await core.get_active_providers(db, provider)
        token = await core.get_active_token_for(db, provider)
        api = core.make_provider_api(provider, token) if token else None
        if not plan or not user or not api:
            return page("خطا", '<div class="card">مشتری، پلن یا API معتبر نیست.</div>', "/admin-server-purchase")
        if provider == "hetzner":
            pricing = await core.make_provider_api(provider, providers[0][1]).pricing() if providers else None
            price = await core.compute_price_usd(db, pricing, plan[2], location, plan[3]) if pricing else None
        else:
            types = await api.server_types()
            price = core.compute_price_generic(types, plan[2], plan[3])
        if price is None or float(price) <= 0:
            return page("قیمت نامعتبر", '<div class="card">قیمت پلن از API قابل محاسبه نیست.</div>', "/admin-server-purchase")
        try:
            images = await core.deduplicate_images(await core.images_for_plan(provider, api, plan[2]))
            if image_id not in {str(x.get("id")) for x in images}:
                raise ValueError("invalid image")
        except Exception:
            return page("سیستم‌عامل نامعتبر", '<div class="card">Image ID انتخاب‌شده معتبر نیست.</div>', "/admin-server-purchase")
        if provider == "hetzner":
            available_sets = []
            for _, provider_token in providers:
                try:
                    available_sets.append(await core.available_types_for_location(core.HetznerAPI(provider_token), location))
                except Exception:
                    pass
            known_sets = [x for x in available_sets if x is not None]
            if known_sets and not any(plan[2] in x for x in known_sets):
                return page("موجودی ناموجود", '<div class="card">این پلن در لوکیشن انتخابی موجود نیست.</div>', "/admin-server-purchase")
            if not providers:
                return page("API نامعتبر", '<div class="card">API فعال برای Hetzner وجود ندارد.</div>', "/admin-server-purchase")

    price = round(float(price), 6)
    reference = "admin-server-purchase:" + secrets.token_hex(12)
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        current = await (await db.execute("SELECT balance FROM users WHERE telegram_id=?", (user_id,))).fetchone()
        if not current:
            await db.rollback()
            return page("خطا", '<div class="card">حساب مشتری پیدا نشد.</div>', "/admin-server-purchase")
        balance_after = float(current[0] or 0) - price
        await db.execute("UPDATE users SET balance=balance-? WHERE telegram_id=?", (price, user_id))
        await db.execute("INSERT INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)", (user_id, -price, "admin_server_purchase", reference + ":debit", now))
        await db.execute("INSERT INTO admin_wallet_actions(admin_actor,target_user_id,action,amount_usd,reference,details,created_at) VALUES(?,?,?,?,?,?,?)", (str(sess.get("username") or PANEL_USERNAME), user_id, "server_purchase_pending", price, reference, f"plan_id={plan_id};provider={provider};location={location};balance_after={balance_after:.6f}", now))
        await db.commit()

    server = None
    server_db_id = None
    server_name = "admin-" + str(user_id) + "-" + secrets.token_hex(3)
    try:
        if provider == "hetzner":
            created = await core.create_hetzner_server_with_failover(providers, server_name=server_name, server_type=plan[2], image=image_id, location=location, order_ref=secrets.token_hex(8))
            server, root_password = created["server"], created["root_password"]
            provider_id = created["provider_id"]
            image_id = created.get("image") or image_id
            server_name = server.get("name") or server_name
        else:
            server, root_password = await api.create_server(name=server_name, server_type=plan[2], image=image_id, location=location)
            provider_id = providers[0][0] if providers else None
        if not root_password:
            root_password = "از طریق پنل مدیریت امکان تغییر رمز وجود دارد"
        ip = core.extract_server_ip(provider, server)
        expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()
        server_db_id = await core._save_purchase(user_id, server, server_name, ip, root_password, image_id, {"location": location, "plan_title": plan[1]}, price, expires_at, provider, provider_id, charge_wallet=False)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE admin_wallet_actions SET action='server_purchase',details=? WHERE reference=?", (f"server_db_id={server_db_id};remote_id={server.get('id')};plan_id={plan_id};provider={provider};location={location};balance_after={balance_after:.6f}", reference))
                await db.commit()
        except Exception:
            core.logger.exception("Could not update admin server purchase audit row %s", reference)
        notification_error = ""
        if user_id > 0 and core.BOT_TOKEN:
            bot_client = Bot(token=core.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            try:
                await bot_client.send_message(user_id, f"سرور شما توسط مدیریت خریداری شد. شناسه: #{server_db_id} · IP: {esc(ip)} · مبلغ کسرشده: {price:.2f} USD · مانده: {balance_after:.2f} USD")
            except Exception as notify_exc:
                notification_error = str(notify_exc)[:200]
                core.logger.exception("Admin server purchase notification failed for user %s", user_id)
            finally:
                await bot_client.session.close()
        notification_html = f'<p>⚠️ ارسال پیام به مشتری ناموفق بود: {esc(notification_error)}</p>' if notification_error else ""
        return page("خرید سرور موفق", f'<div class="card"><h2>سرور برای مشتری ساخته شد</h2><p>مشتری: {user_id}</p><p>سرور: #{server_db_id} · {esc(server_name)}</p><p>IP: {esc(ip)}</p><p>مبلغ: {price:.2f} USD · مانده: {balance_after:.2f} USD</p><p>پسورد root: <code>{esc(root_password)}</code></p>{notification_html}</div>', "/admin-server-purchase")
    except Exception as exc:
        if server_db_id is not None:
            core.logger.exception("Admin server purchase created and saved, but a post-save action failed: server_db_id=%s", server_db_id)
            return page("خرید سرور ثبت شد", f'<div class="card">سرور #{server_db_id} ساخته و در حساب مشتری ثبت شده است. خطای جانبی: {esc(str(exc)[:300])}</div>', "/admin-server-purchase")
        if server is not None:
            core.logger.critical("Admin server purchase remote server created but DB save failed: user=%s remote_id=%s provider=%s error=%s", user_id, server.get("id"), provider, str(exc)[:500])
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE admin_wallet_actions SET action='server_purchase_db_failure',details=? WHERE reference=?",
                        (f"user_id={user_id};remote_id={server.get('id')};server_name={server_name};provider={provider};plan_id={plan_id};location={location};error={str(exc)[:500]}", reference),
                    )
                    await db.commit()
            except Exception:
                core.logger.exception("Could not persist DB-failure audit for admin server purchase %s", reference)
            return page("نیازمند بررسی فوری", f'<div class="card">سرور در سرویس‌دهنده ساخته شده اما ثبت دیتابیس ناموفق بوده است؛ مبلغ برگشت داده نشد تا سرور بدون مالک نماند. مشتری: {user_id} · شناسه سرویس‌دهنده: {esc(server.get("id"))} · کد پیگیری: {esc(reference)} · خطا: {esc(str(exc)[:300])}</div>', "/admin-server-purchase")

        # Timeout/قطع ارتباط یا پاسخ 5xx ممکن است پس از ایجاد سرور رخ داده باشد.
        # در نتیجه مبهم، وجه را نگه می‌داریم تا ادمین وجود سرور را در Provider بررسی کند.
        uncertain = bool(getattr(exc, "uncertain", False))
        if provider != "hetzner":
            error_text = str(exc)
            if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)) or re.search(r"\\((?:500|502|503|504)\\)", error_text):
                uncertain = True
        if uncertain:
            review_details = f"user_id={user_id};provider={provider};plan_id={plan_id};location={location};server_name={server_name};error={str(exc)[:700]};manual_provider_check_required=1"
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE admin_wallet_actions SET action='server_purchase_review',details=? WHERE reference=?",
                        (review_details, reference),
                    )
                    await db.commit()
            except Exception:
                core.logger.exception("Could not persist uncertain server-purchase audit %s", reference)
            core.logger.critical(
                "Admin server purchase outcome uncertain; wallet debit retained for manual reconciliation: user=%s provider=%s reference=%s error=%s",
                user_id, provider, reference, str(exc)[:500],
            )
            return page("نیازمند بررسی پرداخت و سرور", f'<div class="card">نتیجه درخواست ساخت سرور قطعی نیست؛ ممکن است سرور در سرویس‌دهنده ساخته شده باشد. برای جلوگیری از ساخت تکراری، مبلغ {price:.2f} USD فعلاً به کیف پول برنگشت و درخواست برای بررسی ادمین ثبت شد. مشتری: {user_id} · سرویس‌دهنده: {esc(provider)} · نام سرور: {esc(server_name)} · کد پیگیری: {esc(reference)} · خطا: {esc(str(exc)[:300])}</div>', "/admin-server-purchase")

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?", (price, user_id))
            await db.execute("INSERT OR IGNORE INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)", (user_id, price, "refund", reference + ":refund", datetime.utcnow().isoformat()))
            await db.execute("INSERT OR IGNORE INTO admin_wallet_actions(admin_actor,target_user_id,action,amount_usd,reference,details,created_at) VALUES(?,?,?,?,?,?,?)", (str(sess.get("username") or PANEL_USERNAME), user_id, "server_purchase_refund", price, reference + ":refund", f"reason={str(exc)[:300]};debit_reference={reference}", datetime.utcnow().isoformat()))
            await db.commit()
        return page("ساخت سرور ناموفق", f'<div class="card">ساخت سرور ناموفق بود؛ مبلغ {price:.2f} USD به کیف پول مشتری برگشت داده شد. خطا: {esc(str(exc)[:300])}</div>', "/admin-server-purchase")


@app.get("/product-orders",response_class=HTMLResponse)
async def product_orders(request:Request):
    if not get_session(request): return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        rows=await (await db.execute("SELECT id,product_name,quantity,customer_name,mobile,amount_usd,status,ref_id,created_at,gateway_method,wallet_used_usd FROM product_orders ORDER BY id DESC LIMIT 300")).fetchall()
    notice=request.query_params.get("notice","")
    flash = '<div class="msg ok">عملیات سفارش ثبت شد.</div>' if notice=="ok" else ('<div class="msg err">بازپرداخت درگاه خارجی باید جداگانه در پنل همان درگاه انجام شود؛ وضعیت سفارش برای پیگیری علامت‌گذاری شد.</div>' if notice=="gateway" else ('<div class="msg err">تغییر وضعیت مجاز نبود یا سفارش قبلاً نهایی شده است.</div>' if notice=="invalid" else ""))
    tr=''.join(f'<tr><td>#{r[0]}</td><td>{esc(r[1])}</td><td>{r[2]}</td><td>{esc(r[3])}</td><td>{esc(r[4])}</td><td>${float(r[5] or 0):.2f}</td><td>{esc(r[6])}</td><td>{esc(r[7] or "-")}</td><td>{esc((core.iran_time(r[8]).strftime("%Y-%m-%d %H:%M") if core.iran_time(r[8]) else r[8]))}</td><td><small>درگاه: {esc(r[9] or "-")} | کیف پول: ${float(r[10] or 0):.2f}</small><form method="post" action="/product-orders/{r[0]}/update"><select name="status"><option value="delivered">تحویل‌شده (دستی)</option><option value="refunded">بازپرداخت / بررسی بازپرداخت</option></select><input name="delivery_note" value="" placeholder="یادداشت تحویل یا علت بازپرداخت"><button class="btn small">ثبت</button></form></td></tr>' for r in rows)
    return page("سفارش فروشگاه",flash+'<h1>سفارش‌های فروشگاه</h1><div class="card"><p>سفارش‌های پرداخت‌نشده را از این بخش به‌عنوان پرداخت‌شده علامت نزنید. تحویل دستی فقط برای سفارش پرداخت‌شده مجاز است. بازپرداخت درگاه‌های خارجی نیازمند اقدام جداگانه در پنل درگاه است.</p><table><tr><th>شناسه</th><th>محصول</th><th>تعداد</th><th>خریدار</th><th>موبایل</th><th>مبلغ USD</th><th>وضعیت</th><th>کد پیگیری</th><th>زمان ایران</th><th>مدیریت</th></tr>'+tr+'</table></div>',"/product-orders")

@app.post("/product-orders/{oid}/update")
async def product_order_update(request:Request,oid:int):
    sess=get_session(request)
    if not sess: return RedirectResponse("/login")
    f=await request.form()
    status=str(f.get("status",""))
    note=str(f.get("delivery_note",""))[:2000].strip()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        row=await (await db.execute("SELECT user_id,wallet_used_usd,wallet_refunded,status,gateway_method,amount_usd,product_id,quantity FROM product_orders WHERE id=?",(oid,))).fetchone()
        if not row:
            await db.rollback()
            return RedirectResponse("/product-orders?notice=invalid",status_code=303)
        user_id,wallet_used,wallet_refunded,current_status,gateway_method,total,product_id,quantity=row
        if status=="delivered":
            if current_status not in ("paid","paid_stock_issue") or not note:
                await db.rollback()
                return RedirectResponse("/product-orders?notice=invalid",status_code=303)
            await db.execute("UPDATE product_orders SET status='delivered',delivery_note=? WHERE id=?",(note,oid))
            await db.execute("UPDATE products SET active=0 WHERE id=(SELECT product_id FROM product_orders WHERE id=?) AND stock<=0",(oid,))
            await db.execute("INSERT OR IGNORE INTO admin_wallet_actions(admin_actor,target_user_id,action,amount_usd,reference,details,created_at) VALUES(?,?,?,?,?,?,?)",
                (str(sess.get("username") or PANEL_USERNAME), int(user_id or 0), "manual_product_delivery", 0.0, f"admin-product-order:{oid}:delivery", f"order_id={oid};note={note[:500]}", datetime.utcnow().isoformat()))
        elif status=="refunded":
            if current_status not in ("paid","delivered","paid_stock_issue","refund_review"):
                await db.rollback()
                return RedirectResponse("/product-orders?notice=invalid",status_code=303)
            if current_status=="delivered":
                # سفارش تحویل‌شده، بدون ثبت برگشت فیزیکی/لغو دسترسی، قابل بازپرداخت خودکار نیست.
                await db.rollback()
                return RedirectResponse("/product-orders?notice=invalid",status_code=303)
            if current_status=="paid":
                product_row=await (await db.execute("SELECT delivery_mode FROM products WHERE id=?",(product_id,))).fetchone()
                if product_row and product_row[0]=="manual":
                    await db.execute("UPDATE products SET stock=stock+?,active=1,updated_at=? WHERE id=?",(int(quantity or 0),datetime.utcnow().isoformat(),product_id))
            if gateway_method=="wallet":
                if not wallet_refunded and user_id and float(wallet_used or 0)>0:
                    await db.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?",(float(wallet_used),user_id))
                    await db.execute("INSERT OR IGNORE INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)",(user_id,float(wallet_used),"refund",f"admin-product-order-{oid}-refund",datetime.utcnow().isoformat()))
                await db.execute("UPDATE product_orders SET wallet_refunded=1,status='refunded',delivery_note=? WHERE id=?",(note,oid))
                await db.execute("INSERT OR IGNORE INTO admin_wallet_actions(admin_actor,target_user_id,action,amount_usd,reference,details,created_at) VALUES(?,?,?,?,?,?,?)",
                    (str(sess.get("username") or PANEL_USERNAME), int(user_id or 0), "product_refund", float(wallet_used or 0), f"admin-product-order:{oid}:refund", f"order_id={oid};gateway=wallet;balance_refund={float(wallet_used or 0):.6f};note={note[:500]}", datetime.utcnow().isoformat()))
            else:
                # پرداخت خارجی را از API درگاه نمی‌توان بدون تأیید ادمین برگشت زد.
                if not wallet_refunded and user_id and float(wallet_used or 0)>0:
                    await db.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?",(float(wallet_used),user_id))
                    await db.execute("INSERT OR IGNORE INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)",(user_id,float(wallet_used),"refund",f"admin-product-order-{oid}-refund",datetime.utcnow().isoformat()))
                await db.execute("UPDATE product_orders SET wallet_refunded=1,status='refund_review',delivery_note=? WHERE id=?",(note,oid))
                await db.execute("INSERT OR IGNORE INTO admin_wallet_actions(admin_actor,target_user_id,action,amount_usd,reference,details,created_at) VALUES(?,?,?,?,?,?,?)",
                    (str(sess.get("username") or PANEL_USERNAME), int(user_id or 0), "product_refund_review", float(wallet_used or 0), f"admin-product-order:{oid}:refund-review", f"order_id={oid};gateway={gateway_method};wallet_refund={float(wallet_used or 0):.6f};external_refund_required=1;note={note[:500]}", datetime.utcnow().isoformat()))
                await db.commit()
                return RedirectResponse("/product-orders?notice=gateway",status_code=303)
        else:
            await db.rollback()
            return RedirectResponse("/product-orders?notice=invalid",status_code=303)
        await db.commit()
    return RedirectResponse("/product-orders?notice=ok",status_code=303)


@app.get("/web-server-orders",response_class=HTMLResponse)
async def web_server_orders_admin(request:Request):
    if not get_session(request):return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        rows=await (await db.execute("SELECT id,user_id,provider_type,plan_title,location,price_usd,wallet_used_usd,gateway_method,status,server_db_id,created_at FROM web_server_orders ORDER BY id DESC LIMIT 300")).fetchall()
    tr=''.join(f'<tr><td>#{r[0]}</td><td>{r[1]}</td><td>{esc(r[2])}</td><td>{esc(r[3])}</td><td>{esc(r[4])}</td><td>${float(r[5]):.2f}</td><td>${float(r[6] or 0):.2f}</td><td>{esc(r[7])}</td><td>{esc(r[8])}</td><td>{r[9] or "-"}</td><td>{esc(core.iran_time(r[10]).strftime("%Y-%m-%d %H:%M") if core.iran_time(r[10]) else r[10])}</td></tr>' for r in rows)
    return page("سفارش‌های سرور وب",'<h1>سفارش‌های سرور وب</h1><div class="card"><table><tr><th>شناسه</th><th>کاربر</th><th>Provider</th><th>پلن</th><th>لوکیشن</th><th>قیمت</th><th>کیف پول</th><th>درگاه</th><th>وضعیت</th><th>سرور</th><th>زمان</th></tr>'+tr+'</table></div>',"/web-server-orders")


@app.get("/settings/zarinpal",response_class=HTMLResponse)
async def zarinpal_settings(request:Request):
    if not get_session(request): return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db: merchant=await core.get_setting(db,"zarinpal_merchant_id",""); sandbox=await core.get_setting(db,"zarinpal_sandbox","0")
    return page("زرین‌پال",f'<h1>تنظیمات زرین‌پال</h1><div class="card"><form method="post"><label>Merchant ID</label><input name="merchant" value="{esc(merchant)}" required><label><input type="checkbox" name="sandbox" value="1" {"checked" if sandbox in ("1","true") else ""}> حالت آزمایشی</label><button class="btn green">ذخیره</button></form><p>برای پرداخت واقعی دامنه HTTPS و تنظیم PUBLIC_BASE_URL الزامی است.</p></div>',"/settings")

@app.post("/settings/zarinpal")
async def zarinpal_settings_save(request:Request):
    if not get_session(request): return RedirectResponse("/login")
    f=await request.form()
    async with aiosqlite.connect(DB_PATH) as db: await core.set_setting(db,"zarinpal_merchant_id",str(f.get("merchant", "")).strip()); await core.set_setting(db,"zarinpal_sandbox","1" if f.get("sandbox") else "0")
    return RedirectResponse("/settings/zarinpal",status_code=303)



@app.get("/settings/wallet-rate",response_class=HTMLResponse)
async def wallet_rate_settings(request:Request):
    if not get_session(request): return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        try: rate=await core.get_usdt_toman_rate(db)
        except Exception: rate=await core.get_setting(db,"wallet_usd_to_toman","100000")
        mode=await core.get_setting(db,"wallet_rate_mode","auto")
        updated=await core.get_setting(db,"wallet_rate_source_updated_at","")
        source=await core.get_setting(db,"wallet_rate_source","NerkhAra")
        count=await core.get_setting(db,"wallet_rate_sources_count","")
    status=("خودکار · "+esc(source)+" · "+(esc(updated) if updated else "هنوز دریافت نشده")+((" · "+esc(count)+" منبع") if count else "")) if mode!="manual" else "دستی توسط مدیر"
    return page("نرخ تبدیل کیف پول",f'<h1>نرخ تبدیل کیف پول</h1><div class="card"><p>قیمت‌ها و کیف پول برحسب دلار هستند. برای پرداخت ریالی، هر دلار معادل نرخ لحظه‌ای USDT بازار ایران محاسبه می‌شود. منبع خودکار: NerkhAra؛ واحد API تومان است و نرخ تقریباً هر ۱۰ دقیقه تازه‌سازی می‌شود.</p><p><b>وضعیت:</b> {status}</p><p><b>نرخ فعلی:</b> {float(rate):,.0f} تومان برای هر ۱ دلار/تتر</p><form method="post"><label>نرخ دستی (تومان برای هر دلار)</label><input name="rate" type="number" min="1" step="1" value="{esc(rate)}"><button class="btn" name="mode" value="manual">ذخیره و فعال‌کردن نرخ دستی</button><button class="btn green" name="mode" value="auto">فعال‌کردن نرخ خودکار تتر</button></form><p><small>اگر نرخ زنده دریافت نشود و داده معتبر کافی قدیمی باشد، پرداخت ریالی جدید متوقف می‌شود؛ سفارش‌های ثبت‌شده با نرخ زمان ثبت خودشان تسویه می‌شوند.</small></p><p><a href="https://nerkhara.com" target="_blank" rel="noopener">منبع نرخ: NerkhAra</a></p></div>',"/settings/wallet-rate")


@app.post("/settings/wallet-rate")
async def wallet_rate_settings_save(request:Request):
    if not get_session(request): return RedirectResponse("/login")
    form=await request.form()
    mode=str(form.get("mode","manual"))
    async with aiosqlite.connect(DB_PATH) as db:
        if mode=="auto":
            await core.set_setting(db,"wallet_rate_mode","auto")
        else:
            try: rate=int(form.get("rate",0))
            except: rate=0
            if rate<1: return RedirectResponse("/settings/wallet-rate",status_code=303)
            await core.set_setting(db,"wallet_usd_to_toman",str(rate))
            await core.set_setting(db,"wallet_rate_mode","manual")
    return RedirectResponse("/settings/wallet-rate",status_code=303)




@app.get("/coupons", response_class=HTMLResponse)
async def coupons_admin(request: Request):
    if not get_session(request): return RedirectResponse("/login")
    labels = {"server_purchase":"خرید سرور","server_renewal":"تمدید سرور","server_purchase,server_renewal":"خرید و تمدید سرور","product_purchase":"خرید اکانت / محصول","wallet_topup":"شارژ کیف پول"}
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT c.id,c.code,c.applies_to,c.discount_type,c.discount_value,c.min_order_usd,c.max_uses,c.max_uses_per_user,c.starts_at,c.expires_at,c.active,"
            "(SELECT COUNT(*) FROM coupon_redemptions r WHERE r.coupon_id=c.id AND r.status='used'),"
            "(SELECT COUNT(*) FROM coupon_redemptions r WHERE r.coupon_id=c.id AND r.status='reserved') "
            "FROM discount_coupons c ORDER BY c.id DESC"
        )).fetchall()
    table = "".join(
        "<tr><td><b>"+esc(x[1])+"</b></td><td>"+esc(labels.get(x[2],x[2]))+"</td><td>"+
        (str(x[4])+"%" if x[3]=="percent" else "$"+format(float(x[4]),".2f"))+
        "</td><td>$"+format(float(x[5] or 0),".2f")+"</td><td>"+str(x[11])+" استفاده"+
        (" (در انتظار "+str(x[12])+")" if x[12] else "")+"</td><td>"+("فعال" if x[10] else "غیرفعال")+
        "</td><td><form method='post' action='/coupons/"+str(x[0])+"/toggle' style='display:inline'><button class='btn small'>تغییر وضعیت</button></form> "+
        "<form method='post' action='/coupons/"+str(x[0])+"/delete' style='display:inline' onsubmit='return confirm(\"حذف کد؟\")'><button class='btn small danger'>حذف</button></form></td></tr>"
        for x in rows
    ) or "<tr><td colspan='7'>هنوز کد تخفیفی ایجاد نشده است.</td></tr>"
    body = """
    <h1>🎟 مدیریت کدهای تخفیف</h1>
    <div class="card"><h2>ایجاد کد جدید</h2>
    <form method="post" action="/coupons">
      <label>روش ساخت کد</label><select name="code_mode"><option value="manual">کد دلخواه خودم</option><option value="random">تولید تصادفی</option></select>
      <label>کد دلخواه (در حالت تصادفی خالی بگذارید)</label><input name="code" maxlength="32" placeholder="مثلاً CLOUD20">
      <label>قابل استفاده برای</label><select name="applies_to">
        <option value="server_purchase,server_renewal">خرید و تمدید سرور (یک کد مشترک)</option>
        <option value="server_purchase">فقط خرید سرور</option><option value="server_renewal">فقط تمدید سرور</option>
        <option value="product_purchase">خرید اکانت / محصول</option><option value="wallet_topup">شارژ کیف پول</option>
      </select>
      <label>نوع تخفیف</label><select name="discount_type"><option value="percent">درصدی</option><option value="fixed">مبلغ ثابت (USD)</option></select>
      <label>مقدار تخفیف</label><input name="discount_value" type="number" min="0.01" step="0.01" required>
      <label>حداقل مبلغ سفارش (USD)</label><input name="min_order_usd" type="number" min="0" step="0.01" value="0">
      <label>حداکثر کل استفاده (خالی = نامحدود)</label><input name="max_uses" type="number" min="1">
      <label>حداکثر استفاده برای هر مشتری (خالی = نامحدود)</label><input name="max_uses_per_user" type="number" min="1">
      <label>شروع اعتبار (اختیاری)</label><input name="starts_at" type="datetime-local">
      <label>پایان اعتبار (اختیاری)</label><input name="expires_at" type="datetime-local">
      <button class="btn green">ایجاد کد تخفیف</button>
    </form></div>
    <div class="card"><h2>کدهای ساخته‌شده</h2><div class="table-wrap"><table><thead><tr><th>کد</th><th>نوع خرید</th><th>تخفیف</th><th>حداقل سفارش</th><th>استفاده</th><th>وضعیت</th><th>عملیات</th></tr></thead><tbody>""" + table + """</tbody></table></div></div>
    """
    return page("کدهای تخفیف", body, "/coupons")


@app.post("/coupons")
async def coupon_create(request: Request):
    if not get_session(request): return RedirectResponse("/login")
    f = await request.form()
    mode = str(f.get("code_mode","manual"))
    code = ("CC-"+secrets.token_hex(4)).upper() if mode=="random" else str(f.get("code","")).strip().upper()
    scope = str(f.get("applies_to",""))
    kind = str(f.get("discount_type","percent"))
    try:
        value=float(f.get("discount_value",0)); minimum=float(f.get("min_order_usd",0) or 0)
        max_uses=int(f.get("max_uses")) if str(f.get("max_uses","")).strip() else None
        per_user=int(f.get("max_uses_per_user")) if str(f.get("max_uses_per_user","")).strip() else None
    except Exception: return RedirectResponse("/coupons",status_code=303)
    if not code or len(code)>32 or not code.replace("-","").replace("_","").isalnum() or scope not in ("server_purchase","server_renewal","server_purchase,server_renewal","product_purchase","wallet_topup") or kind not in ("percent","fixed") or value<=0 or minimum<0 or (kind=="percent" and value>100) or (max_uses is not None and max_uses<1) or (per_user is not None and per_user<1):
        return RedirectResponse("/coupons",status_code=303)
    def parse_dt(v):
        v=str(v or "").strip()
        return datetime.fromisoformat(v).isoformat() if v else None
    try:
        starts=parse_dt(f.get("starts_at")); expires=parse_dt(f.get("expires_at"))
        if starts and expires and starts>=expires: return RedirectResponse("/coupons",status_code=303)
    except Exception: return RedirectResponse("/coupons",status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO discount_coupons(code,applies_to,discount_type,discount_value,min_order_usd,max_uses,max_uses_per_user,starts_at,expires_at,active,created_at) VALUES(?,?,?,?,?,?,?,?,?,1,?)",
                (code,scope,kind,value,minimum,max_uses,per_user,starts,expires,datetime.utcnow().isoformat()))
            await db.commit()
        except Exception:
            await db.rollback()
    return RedirectResponse("/coupons",status_code=303)


@app.post("/coupons/{coupon_id}/toggle")
async def coupon_toggle(request: Request, coupon_id: int):
    if not get_session(request): return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE discount_coupons SET active=CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?",(coupon_id,))
        await db.commit()
    return RedirectResponse("/coupons",status_code=303)


@app.post("/coupons/{coupon_id}/delete")
async def coupon_delete(request: Request, coupon_id: int):
    if not get_session(request): return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        used=await (await db.execute("SELECT COUNT(*) FROM coupon_redemptions WHERE coupon_id=? AND status IN ('reserved','used')",(coupon_id,))).fetchone()
        if not used[0]:
            await db.execute("DELETE FROM discount_coupons WHERE id=?",(coupon_id,))
            await db.commit()
        else:
            await db.execute("UPDATE discount_coupons SET active=0 WHERE id=?",(coupon_id,))
            await db.commit()
    return RedirectResponse("/coupons",status_code=303)

@app.on_event("startup")
async def on_startup():
    await core.init_db()
    if not PANEL_PASSWORD:
        core.logger.warning("PANEL_PASSWORD تنظیم نشده - پنل وب غیرقابل ورود خواهد بود. آن را در .env تنظیم کنید.")


# ریشه پنل مدیریت مستقل از فروشگاه عمومی است.
@app.get("/", include_in_schema=False)
async def admin_root(request: Request):
    return RedirectResponse("/admin" if get_session(request) else "/login", status_code=303)
