# -*- coding: utf-8 -*-
"""
Hetzner Shop - پنل مدیریت تحت وب
از همان دیتابیس و منطق ربات تلگرام (bot.py) استفاده می‌کند، پس هیچ تنظیمی تکراری نیست.
اجرا: uvicorn panel:app --host 0.0.0.0 --port 8088
"""
import html
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta

import aiosqlite
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import bot as core  # همه منطق و دیتابیس مشترک از bot.py

PANEL_USERNAME = os.getenv("PANEL_USERNAME", "admin")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
DB_PATH = core.DB_PATH

app = FastAPI()
SESSIONS: dict[str, dict] = {}
SESSION_TTL_HOURS = 12


# ---------------------------------------------------------------------------
# قالب صفحه و ابزار کمکی
# ---------------------------------------------------------------------------
NAV_ITEMS = [
    ("/", "📊 داشبورد"),
    ("/users", "👥 کاربران"),
    ("/servers", "🖥 سرورها"),
    ("/plans", "🧩 پلن‌ها"),
    ("/locations", "📍 لوکیشن‌ها"),
    ("/providers", "🔑 API های Hetzner"),
    ("/wallet-requests", "💳 درخواست‌های شارژ"),
    ("/orders", "📦 سفارش‌ها"),
    ("/settings", "⚙️ تنظیمات درگاه"),
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
    {body}
  </div>
</div>
</body>
</html>"""
    return HTMLResponse(html_doc)


BASE_CSS = """
  * { box-sizing: border-box; }
  :root {
    --bg-1:#0a1628; --bg-2:#0f2544; --card:#12294a; --card-2:#173a68; --border:#26466e;
    --txt:#eef4fc; --txt-dim:#93aed0; --accent:#2f9bf0; --accent-2:#5ec3ff; --accent-3:#8fe0ff;
    --green:#2ee6a8; --red:#ff5c72; --yellow:#ffc857; --pink:#7bc8f6;
  }
  html, body { height:100%; }
  body {
    font-family: 'Vazirmatn', Tahoma, sans-serif; color:var(--txt); margin:0;
    background: linear-gradient(180deg, #061020 0%, #0c2140 30%, #103766 62%, #1c5a9e 100%);
  }
  .bg-decor { position:fixed; inset:0; pointer-events:none; z-index:0; overflow:hidden; }
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
        resp = RedirectResponse("/", status_code=303)
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
# داشبورد
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
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
    body = f"""
    <h1><span class="page-icon icon-purple">📊</span> داشبورد</h1>
    <div class="stat-grid">
      <div class="stat-box"><div class="stat-icon icon-purple">👥</div><div><div class="num">{users_c}</div><div class="lbl">کاربران</div></div></div>
      <div class="stat-box"><div class="stat-icon icon-blue">🖥</div><div><div class="num">{servers_c}</div><div class="lbl">سرورها</div></div></div>
      <div class="stat-box"><div class="stat-icon icon-teal">📦</div><div><div class="num">{orders_c}</div><div class="lbl">سفارش‌ها</div></div></div>
      <div class="stat-box"><div class="stat-icon icon-yellow">💰</div><div><div class="num">{core.fmt_price(revenue)}</div><div class="lbl">درآمد کل</div></div></div>
      <div class="stat-box"><div class="stat-icon icon-pink">💳</div><div><div class="num">{pending}</div><div class="lbl">درخواست شارژ در انتظار</div></div></div>
    </div>
    <div class="card">
      <h3><span class="mini-icon icon-blue">🌐</span> وضعیت اتصال Hetzner</h3>
      <p>{"<span class='badge ok'>✅ متصل</span>" if token else "<span class='badge off'>❌ هیچ API فعالی تنظیم نشده</span> — از بخش «API های Hetzner» اضافه کنید."}</p>
    </div>
    """
    return page("داشبورد", body, "/")


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
            "SELECT id, name, ip, location, plan_title, status FROM servers WHERE user_id=? ORDER BY id DESC", (tid,)
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
# سرورها
# ---------------------------------------------------------------------------
def server_action_buttons(server_id: int) -> str:
    return (
        f"<form class='inline' method='post' action='/servers/{server_id}/action/on'><button class='btn small green' type='submit'>روشن</button></form>"
        f"<form class='inline' method='post' action='/servers/{server_id}/action/off'><button class='btn small gray' type='submit'>خاموش</button></form>"
        f"<form class='inline' method='post' action='/servers/{server_id}/action/reboot'><button class='btn small' type='submit'>ریبوت</button></form>"
        f"<form class='inline' method='post' action='/servers/{server_id}/action/pass'><button class='btn small' type='submit'>تغییر پسورد</button></form>"
        f"<a class='btn small gray' href='/servers/{server_id}/rebuild'>Rebuild</a>"
        f"<form class='inline' method='post' action='/servers/{server_id}/action/ipchange' onsubmit=\"return confirm('تغییر IP انجام شود؟ دفعات رایگان تمام‌شده باشد هزینه دارد.')\"><button class='btn small gray' type='submit'>تغییر IP</button></form>"
        f"<a class='btn small gray' href='/servers/{server_id}/renew'>تمدید</a>"
        f"<form class='inline' method='post' action='/servers/{server_id}/action/del' onsubmit=\"return confirm('حذف قطعی این سرور؟')\"><button class='btn small red' type='submit'>حذف</button></form>"
    )


@app.get("/servers", response_class=HTMLResponse)
async def servers_list(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    added = 0
    async with aiosqlite.connect(DB_PATH) as db:
        token = await core.get_active_token(db)
        if token and request.query_params.get("sync") == "1":
            added = await core.sync_hetzner_servers(db, core.HetznerAPI(token))
        cur = await db.execute(
            "SELECT id, name, ip, user_id, location, status, root_password FROM servers ORDER BY id DESC LIMIT 200"
        )
        servers = await cur.fetchall()
    note = f'<div class="msg ok">{added} سرور جدید از پنل هتزنر همگام‌سازی شد.</div>' if added else ""
    rows = "".join(
        f"<tr><td>{esc(s[1])}</td><td>{esc(s[2])}</td><td>{s[3] or 'بدون مالک'}</td><td>{esc(s[4])}</td>"
        f"<td>{esc(s[5])}</td><td>{server_action_buttons(s[0])}</td></tr>"
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


@app.post("/servers/{server_id}/action/{action}")
async def server_action_do(request: Request, server_id: int, action: str):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT hetzner_id, name, user_id, ip_change_count FROM servers WHERE id=?", (server_id,))
        row = await cur.fetchone()
        token = await core.get_active_token(db)
    if not row or not token:
        return RedirectResponse("/servers")
    api = core.HetznerAPI(token)
    referer = request.headers.get("referer", "/servers")
    try:
        if action == "on":
            await api.power_action(row[0], "poweron")
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
            owner_id, count = row[2], (row[3] or 0)
            cost = 0 if count < core.IP_CHANGE_FREE_COUNT else core.IP_CHANGE_PRICE
            if cost > 0 and owner_id:
                async with aiosqlite.connect(DB_PATH) as db:
                    cur2 = await db.execute("SELECT balance FROM users WHERE telegram_id=?", (owner_id,))
                    bal_row = await cur2.fetchone()
                if not bal_row or bal_row[0] < cost:
                    sep = "&" if "?" in referer else "?"
                    return RedirectResponse(f"{referer}{sep}err={urllib.parse.quote('موجودی کیف پول کاربر کافی نیست')}", status_code=303)
            new_ip = await core.change_server_ip(api, row[0])
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE servers SET ip=?, ip_change_count=ip_change_count+1 WHERE id=?", (new_ip, server_id))
                if cost > 0 and owner_id:
                    await db.execute("UPDATE users SET balance = balance - ? WHERE telegram_id=?", (cost, owner_id))
                await db.commit()
        elif action == "del":
            await api.delete_server(row[0])
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM servers WHERE id=?", (server_id,))
                await db.commit()
    except Exception as e:
        sep = "&" if "?" in referer else "?"
        return RedirectResponse(f"{referer}{sep}err={urllib.parse.quote(str(e))}", status_code=303)
    return RedirectResponse(referer, status_code=303)


@app.get("/servers/{server_id}/renew", response_class=HTMLResponse)
async def server_renew_form(request: Request, server_id: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name, expires_at, monthly_price, user_id FROM servers WHERE id=?", (server_id,))
        row = await cur.fetchone()
    if not row:
        return RedirectResponse("/servers")
    name, expires_at, monthly_price, owner_id = row
    if not expires_at or not monthly_price:
        return page("تمدید سرویس", f"<h1><span class='page-icon icon-blue'>⏳</span> تمدید سرویس</h1><div class='card'>سرور «{esc(name)}» تحت سیستم تمدید خودکار نیست.</div>", "/servers")
    exp_dt = datetime.fromisoformat(expires_at)
    body = f"""
    <h1><span class="page-icon icon-blue">⏳</span> تمدید سرویس «{esc(name)}»</h1>
    <div class="card">
      <p>مهلت فعلی تا: <b>{exp_dt.strftime('%Y-%m-%d %H:%M')} (UTC)</b></p>
      <p>هزینه تمدید {core.RENEWAL_PERIOD_DAYS} روز دیگر: <b>{core.fmt_price(monthly_price)}</b> (از کیف پول کاربر {owner_id or '-'} کسر می‌شود)</p>
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
        cur = await db.execute(
            "SELECT user_id, hetzner_id, expires_at, monthly_price, expired_powered_off_at FROM servers WHERE id=?",
            (server_id,),
        )
        row = await cur.fetchone()
        if not row:
            return RedirectResponse("/servers")
        owner_id, hetzner_id, expires_at, monthly_price, expired_off_at = row
        if not expires_at or not monthly_price:
            return RedirectResponse("/servers")
        cur2 = await db.execute("SELECT balance FROM users WHERE telegram_id=?", (owner_id,))
        bal_row = await cur2.fetchone()
        if not bal_row or bal_row[0] < monthly_price:
            return RedirectResponse(f"/servers/{server_id}/renew?err=" + urllib.parse.quote("موجودی کیف پول کاربر کافی نیست"), status_code=303)
        token = await core.get_active_token(db)
        base = datetime.fromisoformat(expires_at)
        now = datetime.utcnow()
        if base < now:
            base = now
        new_expiry = base + timedelta(days=core.RENEWAL_PERIOD_DAYS)
        was_expired_off = bool(expired_off_at)
        await db.execute("UPDATE users SET balance = balance - ? WHERE telegram_id=?", (monthly_price, owner_id))
        await db.execute(
            "UPDATE servers SET expires_at=?, expired_powered_off_at=NULL, traffic_powered_off=0 WHERE id=?",
            (new_expiry.isoformat(), server_id),
        )
        await db.commit()
    if was_expired_off and token:
        try:
            await core.HetznerAPI(token).power_action(hetzner_id, "poweron")
        except Exception:
            pass
    return RedirectResponse("/servers", status_code=303)


@app.get("/servers/{server_id}/rebuild", response_class=HTMLResponse)
async def server_rebuild_form(request: Request, server_id: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name FROM servers WHERE id=?", (server_id,))
        row = await cur.fetchone()
        token = await core.get_active_token(db)
    if not row or not token:
        return RedirectResponse("/servers")
    api = core.HetznerAPI(token)
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
        cur = await db.execute("SELECT hetzner_id FROM servers WHERE id=?", (server_id,))
        row = await cur.fetchone()
        token = await core.get_active_token(db)
    if row and token and image:
        api = core.HetznerAPI(token)
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
    plans = [p for p in all_plans if core.plan_has_stock(stock_map, p[8], p[2], enabled_locations)]
    hidden_count = len(all_plans) - len(plans)
    rows = "".join(
        f"<tr><td>{esc(p[1])}</td><td>{esc(p[2])}</td><td>{esc(p[3])}/{esc(p[4])}/{esc(p[5])}</td><td>%{p[6]:g}</td>"
        f"<td>{esc(p[8]) if p[8] else 'همه'}</td><td>{'✅' if p[7] else '❌'}</td>"
        f"<td><form class='inline' method='post' action='/plans/{p[0]}/toggle'><button class='btn small gray' type='submit'>{'غیرفعال' if p[7] else 'فعال'}</button></form>"
        f"<form class='inline' method='post' action='/plans/{p[0]}/delete' onsubmit=\"return confirm('حذف این پلن؟')\"><button class='btn small red' type='submit'>حذف</button></form></td></tr>"
        for p in plans
    )
    note = f'<div class="msg ok">⚠️ {hidden_count} پلن دیگر به‌دلیل نبود موجودی فعلی هتزنر نمایش داده نشد.</div>' if hidden_count else ""
    body = f"""
    <h1><span class="page-icon icon-teal">🧩</span> پلن‌ها ({len(plans)})</h1>
    {note}
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
      <tr><th>عنوان</th><th>نوع</th><th>مشخصات</th><th>سود</th><th>لوکیشن</th><th>وضعیت</th><th>عملیات</th></tr>
      {rows or "<tr><td colspan=7>پلنی با موجودی فعلی وجود ندارد.</td></tr>"}
    </table></div>
    """
    return page("پلن‌ها", body, "/plans")


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
        cur = await db.execute("SELECT id, label, api_token, active FROM provider ORDER BY id DESC")
        rows_db = await cur.fetchall()
    err = request.query_params.get("err")
    msg = f'<div class="msg err">اتصال ناموفق بود: {esc(err)}</div>' if err else ""
    rows = "".join(
        f"<tr><td>{esc(r[1] or 'بدون‌نام')}</td><td>...{esc(r[2][-4:]) if r[2] else '-'}</td>"
        f"<td>{'✅ فعال' if r[3] else '⚪️ غیرفعال'}</td>"
        f"<td><form class='inline' method='post' action='/providers/{r[0]}/activate'><button class='btn small green' type='submit'>فعال‌سازی</button></form>"
        f"<form class='inline' method='post' action='/providers/{r[0]}/delete' onsubmit=\"return confirm('حذف این اتصال؟')\"><button class='btn small red' type='submit'>حذف</button></form></td></tr>"
        for r in rows_db
    )
    body = f"""
    <h1><span class="page-icon icon-yellow">🔑</span> API های Hetzner</h1>
    {msg}
    <div class="card">
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
      <tr><th>نام</th><th>توکن</th><th>وضعیت</th><th>عملیات</th></tr>
      {rows or "<tr><td colspan=4>هنوز اتصالی اضافه نشده.</td></tr>"}
    </table></div>
    """
    return page("API های Hetzner", body, "/providers")


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
        await db.execute("UPDATE provider SET active=0")
        await db.execute(
            "INSERT INTO provider (label, api_token, active, created_at) VALUES (?,?,1,?)",
            (label, token, datetime.utcnow().isoformat()),
        )
        await db.commit()
    return RedirectResponse("/providers", status_code=303)


@app.post("/providers/{pid}/activate")
async def providers_activate(request: Request, pid: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE provider SET active=0")
        await db.execute("UPDATE provider SET active=1 WHERE id=?", (pid,))
        await db.commit()
    return RedirectResponse("/providers", status_code=303)


@app.post("/providers/{pid}/delete")
async def providers_delete(request: Request, pid: int):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM provider WHERE id=?", (pid,))
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
        <textarea name="text" rows="6" required></textarea>
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
    for (uid,) in users:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            pass
    await bot.session.close()
    return RedirectResponse(f"/broadcast?sent={sent}", status_code=303)


@app.on_event("startup")
async def on_startup():
    await core.init_db()
    if not PANEL_PASSWORD:
        core.logger.warning("PANEL_PASSWORD تنظیم نشده - پنل وب غیرقابل ورود خواهد بود. آن را در .env تنظیم کنید.")
