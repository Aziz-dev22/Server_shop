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
<div class="bg-decor"></div>
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
    --bg-1:#0b0e1a; --bg-2:#12162a; --card:#161b30; --card-2:#1b2140; --border:#262d4d;
    --txt:#e8eaf6; --txt-dim:#9aa2c4; --accent:#7c6df2; --accent-2:#4f7cff; --accent-3:#22c9c9;
    --green:#22c98f; --red:#f2495c; --yellow:#f2b84b;
  }
  html, body { height:100%; }
  body {
    font-family: 'Vazirmatn', Tahoma, sans-serif; background:var(--bg-1); color:var(--txt); margin:0;
    background-image: radial-gradient(circle at 15% 0%, rgba(124,109,242,0.16), transparent 40%),
                       radial-gradient(circle at 85% 15%, rgba(34,201,201,0.12), transparent 45%);
    background-attachment: fixed;
  }
  .wrap { display:flex; min-height:100vh; position:relative; z-index:1; }
  .sidebar {
    width:250px; background:linear-gradient(180deg, var(--bg-2), var(--bg-1) 85%);
    padding:22px 16px; flex-shrink:0; border-left:1px solid var(--border);
    display:flex; flex-direction:column; position:sticky; top:0; height:100vh;
  }
  .brand { display:flex; align-items:center; gap:10px; padding:6px 8px 22px; }
  .brand-icon { font-size:26px; filter:drop-shadow(0 0 10px rgba(124,109,242,.6)); }
  .brand-title { font-weight:800; font-size:16px; background:linear-gradient(90deg, var(--accent-2), var(--accent-3)); -webkit-background-clip:text; background-clip:text; color:transparent; }
  .brand-sub { font-size:11px; color:var(--txt-dim); margin-top:2px; }
  nav { display:flex; flex-direction:column; gap:3px; flex:1; }
  .nav-link {
    display:flex; align-items:center; padding:10px 14px; color:var(--txt-dim); text-decoration:none;
    border-radius:10px; font-size:13.5px; font-weight:500; transition: all .15s ease; border:1px solid transparent;
  }
  .nav-link:hover { background:var(--card-2); color:var(--txt); border-color:var(--border); }
  .nav-link.active {
    background:linear-gradient(90deg, rgba(124,109,242,.22), rgba(79,124,255,.12));
    color:#fff; border-color:rgba(124,109,242,.4); box-shadow: inset 0 0 0 1px rgba(124,109,242,.15);
  }
  .logout { display:flex; align-items:center; gap:6px; margin-top:14px; padding:10px 14px; color:var(--red); text-decoration:none; font-size:13px; border-radius:10px; transition: background .15s; }
  .logout:hover { background:rgba(242,73,92,.1); }
  .content { flex:1; padding:32px 36px; max-width:1180px; }
  h1 { font-size:21px; margin:0 0 18px; color:#fff; font-weight:700; display:flex; align-items:center; gap:8px; }
  h3 { font-size:14.5px; color:#fff; margin:0 0 14px; font-weight:600; }
  .card {
    background:linear-gradient(180deg, var(--card), var(--card-2) 260%);
    border-radius:16px; padding:20px 22px; margin-bottom:20px; border:1px solid var(--border);
    box-shadow: 0 8px 24px -12px rgba(0,0,0,.45);
  }
  table { width:100%; border-collapse: collapse; font-size:13px; }
  th, td { text-align:right; padding:10px 8px; border-bottom:1px solid var(--border); }
  th { color:var(--txt-dim); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.3px; }
  tr:hover td { background:rgba(124,109,242,.04); }
  .btn {
    display:inline-block; background:linear-gradient(135deg, var(--accent-2), var(--accent));
    color:#fff; border:none; padding:9px 18px; border-radius:10px; font-size:13.5px; font-weight:600;
    cursor:pointer; text-decoration:none; transition: transform .12s ease, box-shadow .12s ease; font-family:inherit;
    box-shadow: 0 6px 16px -6px rgba(124,109,242,.6);
  }
  .btn:hover { transform: translateY(-1px); box-shadow: 0 10px 20px -6px rgba(124,109,242,.7); }
  .btn.small { padding:5px 11px; font-size:12px; margin:2px; box-shadow:none; }
  .btn.red { background:linear-gradient(135deg, #ff6b7a, var(--red)); }
  .btn.green { background:linear-gradient(135deg, #35e0a1, var(--green)); }
  .btn.gray { background:linear-gradient(135deg, #4a5178, #333a5c); box-shadow:none; }
  input, select, textarea {
    background:var(--bg-1); border:1px solid var(--border); color:var(--txt); padding:10px 12px;
    border-radius:10px; font-size:13.5px; width:100%; margin-bottom:12px; font-family:inherit; transition: border-color .15s;
  }
  input:focus, select:focus, textarea:focus { outline:none; border-color:var(--accent); }
  label { font-size:12.5px; color:var(--txt-dim); display:block; margin-bottom:5px; font-weight:500; }
  .stat-grid { display:flex; gap:16px; flex-wrap:wrap; }
  .stat-box {
    background:linear-gradient(160deg, var(--card), var(--card-2)); border:1px solid var(--border); border-radius:16px;
    padding:18px 24px; min-width:150px; position:relative; overflow:hidden; box-shadow: 0 8px 24px -14px rgba(0,0,0,.5);
  }
  .stat-box::before { content:""; position:absolute; inset:0 auto auto 0; width:100%; height:3px; background:linear-gradient(90deg, var(--accent-2), var(--accent-3)); }
  .stat-box .num { font-size:24px; color:#fff; font-weight:800; }
  .stat-box .lbl { font-size:12px; color:var(--txt-dim); margin-top:5px; }
  .badge { padding:3px 10px; border-radius:20px; font-size:11px; font-weight:600; }
  .badge.ok { background:rgba(34,201,143,.15); color:var(--green); }
  .badge.off { background:rgba(242,73,92,.15); color:var(--red); }
  .msg { padding:11px 16px; border-radius:10px; margin-bottom:16px; font-size:13px; font-weight:500; border:1px solid transparent; }
  .msg.ok { background:rgba(34,201,143,.1); color:var(--green); border-color:rgba(34,201,143,.25); }
  .msg.err { background:rgba(242,73,92,.1); color:var(--red); border-color:rgba(242,73,92,.25); }
  form.inline { display:inline; }
  .login-wrap { display:flex; align-items:center; justify-content:center; height:100vh; }
  .login-box {
    background:linear-gradient(180deg, var(--card), var(--card-2)); padding:38px; border-radius:20px; width:340px;
    border:1px solid var(--border); box-shadow: 0 20px 60px -20px rgba(0,0,0,.6);
  }
  .bg-decor { position:fixed; inset:0; pointer-events:none; z-index:0; }
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
        f"<style>{BASE_CSS}</style></head><body><div class='bg-decor'></div>{body}</body></html>"
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
    <h1>📊 داشبورد</h1>
    <div class="stat-grid">
      <div class="stat-box"><div class="num">{users_c}</div><div class="lbl">کاربران</div></div>
      <div class="stat-box"><div class="num">{servers_c}</div><div class="lbl">سرورها</div></div>
      <div class="stat-box"><div class="num">{orders_c}</div><div class="lbl">سفارش‌ها</div></div>
      <div class="stat-box"><div class="num">{core.fmt_price(revenue)}</div><div class="lbl">درآمد کل</div></div>
      <div class="stat-box"><div class="num">{pending}</div><div class="lbl">درخواست شارژ در انتظار</div></div>
    </div>
    <div class="card">
      <p>وضعیت اتصال Hetzner: {"✅ متصل" if token else "❌ هیچ API فعالی تنظیم نشده — از بخش «API های Hetzner» اضافه کنید."}</p>
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
    <h1>👥 کاربران ({len(users)})</h1>
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
    <h1>👤 مدیریت کاربر {tid}</h1>
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
    <h1>🖥 سرورها ({len(servers)})</h1>
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
        cur = await db.execute("SELECT hetzner_id, name FROM servers WHERE id=?", (server_id,))
        row = await cur.fetchone()
        token = await core.get_active_token(db)
    if not row or not token:
        return RedirectResponse("/servers")
    api = core.HetznerAPI(token)
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
        elif action == "del":
            await api.delete_server(row[0])
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM servers WHERE id=?", (server_id,))
                await db.commit()
    except Exception:
        pass
    referer = request.headers.get("referer", "/servers")
    return RedirectResponse(referer, status_code=303)


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
    <h1>🛠 نصب مجدد سرور «{esc(row[0])}»</h1>
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
    <h1>🧩 پلن‌ها ({len(plans)})</h1>
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
        return page("لوکیشن‌ها", "<h1>📍 لوکیشن‌ها</h1><div class='card'>ابتدا یک API Hetzner فعال کنید.</div>", "/locations")
    api = core.HetznerAPI(token)
    try:
        locations = await api.locations()
    except Exception as e:
        return page("لوکیشن‌ها", f"<h1>📍 لوکیشن‌ها</h1><div class='card'>خطا: {esc(e)}</div>", "/locations")
    rows = "".join(
        f"<tr><td>{esc(l['city'])}</td><td>{esc(l['country'])}</td>"
        f"<td>{'<span class=badge off>غیرفعال</span>' if l['name'] in disabled else '<span class=badge ok>فعال</span>'}</td>"
        f"<td><form method='post' action='/locations/{l['name']}/toggle'><button class='btn small gray' type='submit'>{'فعال کردن' if l['name'] in disabled else 'غیرفعال کردن'}</button></form></td></tr>"
        for l in locations
    )
    body = f"""
    <h1>📍 لوکیشن‌ها</h1>
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
    <h1>🔑 API های Hetzner</h1>
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
    <h1>💳 درخواست‌های شارژ کیف پول</h1>
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
    <h1>📦 سفارش‌ها ({len(orders)})</h1>
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
        addr = await core.get_setting(db, "manual_address", "")
    ok = request.query_params.get("ok")
    msg = '<div class="msg ok">ذخیره شد.</div>' if ok else ""
    body = f"""
    <h1>⚙️ تنظیمات درگاه پرداخت</h1>
    {msg}
    <div class="card">
      <h3>💠 درگاه ارز دیجیتال (OxaPay)</h3>
      <p style="color:var(--txt-dim);font-size:13px;">وضعیت فعلی: {"فعال (..." + esc(oxa_key[-4:]) + ")" if oxa_key else "غیرفعال"}</p>
      <form method="post" action="/settings/oxapay">
        <label>Merchant API Key</label>
        <input name="key" value="{esc(oxa_key or '')}">
        <button class="btn" type="submit">ذخیره</button>
      </form>
    </div>
    <div class="card">
      <h3>💠 درگاه ارز دیجیتال (NOWPayments)</h3>
      <p style="color:var(--txt-dim);font-size:13px;">وضعیت فعلی: {"فعال (..." + esc(now_key[-4:]) + ")" if now_key else "غیرفعال"}
        &nbsp;|&nbsp; ارز پرداخت: {core.NOWPAYMENTS_PAY_CURRENCY.upper()}</p>
      <form method="post" action="/settings/nowpayments">
        <label>API Key</label>
        <input name="key" value="{esc(now_key or '')}">
        <button class="btn" type="submit">ذخیره</button>
      </form>
    </div>
    <div class="card">
      <h3>🏦 آدرس واریز دستی (کارت/ولت)</h3>
      <form method="post" action="/settings/manual-address">
        <label>متن آدرس/شماره کارت</label>
        <textarea name="address" rows="3">{esc(addr or '')}</textarea>
        <button class="btn" type="submit">ذخیره</button>
      </form>
    </div>
    """
    return page("تنظیمات", body, "/settings")


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
    <h1>📢 پیام همگانی</h1>
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
