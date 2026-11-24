# -*- coding: utf-8 -*-
"""CLOUD_CENTER public storefront application.
Run: uvicorn website:app --host 127.0.0.1 --port 8080
Customer-facing routes are isolated from the admin panel process.
"""
import os
import re
import hashlib
import hmac
import secrets
import math
import httpx
import aiosqlite
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import bot as core
from website_common import *

app = FastAPI(title="CLOUD_CENTER Storefront")
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")

@app.on_event("startup")
async def website_startup():
    await core.init_db()

def _zarinpal_endpoints(sandbox):
    if sandbox:
        return "https://sandbox.zarinpal.com/pg/v4/payment/request.json", "https://sandbox.zarinpal.com/pg/v4/payment/verify.json", "https://sandbox.zarinpal.com/pg/StartPay/"
    return "https://payment.zarinpal.com/pg/v4/payment/request.json", "https://payment.zarinpal.com/pg/v4/payment/verify.json", "https://www.zarinpal.com/pg/StartPay/"

async def _create_product_gateway_payment(request: Request, order_id: int, method: str, due_usd: float, due_toman: int, rate: float, email: str):
    base=os.getenv("PUBLIC_BASE_URL",str(request.base_url).rstrip("/")).rstrip("/")
    if method=="zarinpal":
        async with aiosqlite.connect(DB_PATH) as db:
            merchant=await core.get_setting(db,"zarinpal_merchant_id",""); sandbox=(await core.get_setting(db,"zarinpal_sandbox","0")) in ("1","true","yes")
        if not merchant: raise RuntimeError("زرین‌پال پیکربندی نشده است")
        req_url,_,start=_zarinpal_endpoints(sandbox)
        async with httpx.AsyncClient(timeout=25) as client:
            resp=await client.post(req_url,json={"merchant_id":merchant,"amount":int(due_toman)*10,"currency":"IRR","callback_url":base+"/shop/payment/callback","description":f"فروش محصول - سفارش {order_id}","metadata":{"email":email}}); resp.raise_for_status(); data=resp.json()
        d=data.get("data") or {}; authority=d.get("authority")
        if int(d.get("code",0))!=100 or not authority: raise RuntimeError(str(data.get("errors") or data)[:250])
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE product_orders SET authority=?,gateway_track_id=? WHERE id=?",(authority,authority,order_id)); await db.commit()
        return RedirectResponse(start+authority,status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        oxakey=await core.get_setting(db,"oxapay_api_key",""); nowkey=await core.get_setting(db,"nowpayments_api_key",""); pay_currency=await core.get_setting(db,"nowpayments_pay_currency",core.NOWPAYMENTS_PAY_CURRENCY)
    usd_due=round(float(due_usd),2)
    if method=="oxapay":
        if not oxakey: raise RuntimeError("کلید OxaPay تنظیم نشده است")
        track,pay_url=await core.oxapay_create_invoice(oxakey,usd_due,f"p{order_id}",description=f"Product order {order_id}")
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE product_orders SET gateway_track_id=? WHERE id=?",(str(track),order_id)); await db.commit()
        return RedirectResponse(pay_url,status_code=303)
    if method=="nowpayments":
        if not nowkey: raise RuntimeError("کلید NOWPayments تنظیم نشده است")
        payment_id,address,amount,currency=await core.nowpayments_create_payment(nowkey,usd_due,f"p{order_id}",pay_currency,description=f"Product order {order_id}")
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE product_orders SET gateway_track_id=? WHERE id=?",(str(payment_id),order_id)); await db.commit()
        body=f'<div class="card"><h1>پرداخت ارزی سفارش #{order_id}</h1><p>مبلغ پرداخت: {esc(amount)} {esc(currency)}</p><p>آدرس پرداخت:</p><code style="direction:ltr;word-break:break-all">{esc(address)}</code><p>پس از تأیید شبکه، سفارش به‌صورت خودکار تکمیل می‌شود. وضعیت را از حساب کاربری پیگیری کنید.</p><a class="btn" href="/account">حساب کاربری</a></div>'
        return storefront_page("پرداخت ارزی",body)
    raise RuntimeError("روش پرداخت پشتیبانی نمی‌شود")

@app.get("/customer/login", response_class=HTMLResponse)
async def customer_login_page(request: Request):
    bot_username = os.getenv("TELEGRAM_LOGIN_BOT", "").lstrip("@")
    widget = (f'<script async src="https://telegram.org/js/telegram-widget.js?22" data-telegram-login="{html.escape(bot_username)}" data-size="large" data-userpic="false" data-request-access="write" data-onauth="onTelegramAuth(user)"></script>' if bot_username else '<p class="msg err">برای ورود تلگرام، TELEGRAM_LOGIN_BOT را در .env تنظیم کنید.</p>')
    body = '<div class="card"><h1>ورود به CLOUD_CENTER</h1><p>با تلگرام یا ایمیل وارد شوید.</p><div style="text-align:center;padding:18px">' + widget + '</div><form method="post" action="/customer/email/request"><label>ایمیل</label><input name="email" type="email" required placeholder="name@example.com"><button class="btn" type="submit">ارسال کد ورود به ایمیل</button></form><p><a href="/shop">بازگشت به فروشگاه</a></p></div><script>function onTelegramAuth(user){const f=new URLSearchParams();Object.keys(user).forEach(k=>f.append(k,user[k]));fetch("/auth/telegram",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:f}).then(r=>r.json()).then(d=>location.href=d.redirect||"/customer/login").catch(()=>alert("ورود تلگرام ناموفق بود"));}</script>'
    return storefront_page("ورود مشتری", body)

@app.post("/customer/email/request", response_class=HTMLResponse)
async def customer_email_request(request: Request):
    form = await request.form(); email = str(form.get("email", "")).strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return storefront_page("ایمیل نامعتبر", '<div class="card">ایمیل معتبر وارد کنید.</div>')
    code = f"{secrets.randbelow(1000000):06d}"; code_hash = hashlib.sha256(code.encode()).hexdigest()
    async with aiosqlite.connect(DB_PATH) as db:
        recent=await (await db.execute("SELECT created_at FROM customer_login_codes WHERE email=? ORDER BY id DESC LIMIT 1",(email,))).fetchone()
        if recent:
            try:
                if datetime.utcnow()-datetime.fromisoformat(recent[0])<timedelta(seconds=60):
                    return storefront_page("کد ارسال شد",'<div class="card">برای جلوگیری از ارسال مکرر، لطفاً یک دقیقه صبر کنید و سپس دوباره درخواست دهید.</div>')
            except Exception: pass
        await db.execute("DELETE FROM customer_login_codes WHERE email=?", (email,))
        await db.execute("INSERT INTO customer_login_codes(email,code_hash,expires_at,created_at) VALUES(?,?,?,?)", (email,code_hash,(datetime.utcnow()+timedelta(minutes=10)).isoformat(),datetime.utcnow().isoformat()))
        await db.commit()
    try: await send_customer_email_code(email, code)
    except Exception as exc:
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM customer_login_codes WHERE email=?",(email,)); await db.commit()
        return storefront_page("خطای ارسال ایمیل", '<div class="card">ارسال کد انجام نشد: ' + esc(str(exc)[:250]) + '</div>')
    body = '<div class="card"><h1>تأیید ایمیل</h1><p>کد ۶ رقمی به ' + esc(email) + ' ارسال شد.</p><form method="post" action="/customer/email/verify"><input type="hidden" name="email" value="' + esc(email) + '"><label>کد تأیید</label><input name="code" inputmode="numeric" pattern="[0-9]{6}" required><button class="btn green">تأیید و ورود</button></form></div>'
    return storefront_page("تأیید ایمیل", body)

@app.post("/customer/email/verify")
async def customer_email_verify(request: Request):
    form=await request.form(); email=str(form.get("email"," ")).strip().lower(); code=str(form.get("code"," ")).strip()
    async with aiosqlite.connect(DB_PATH) as db:
        row=await (await db.execute("SELECT id,code_hash,expires_at,attempts FROM customer_login_codes WHERE email=? ORDER BY id DESC LIMIT 1",(email,))).fetchone()
        if not row or row[3]>=5 or datetime.fromisoformat(row[2])<datetime.utcnow() or not hmac.compare_digest(row[1],hashlib.sha256(code.encode()).hexdigest()):
            if row: await db.execute("UPDATE customer_login_codes SET attempts=attempts+1 WHERE id=?",(row[0],)); await db.commit()
            return storefront_page("کد نامعتبر", '<div class="card">کد اشتباه یا منقضی شده است. دوباره درخواست کد کنید.</div>')
        user=await (await db.execute("SELECT telegram_id FROM users WHERE lower(email)=?",(email,))).fetchone()
        if user: uid=int(user[0]); await db.execute("UPDATE users SET email_verified=1 WHERE telegram_id=?",(uid,))
        else:
            uid=-secrets.randbelow(900000000)-100000000
            await db.execute("INSERT INTO users(telegram_id,full_name,balance,email,email_verified,created_at) VALUES(?,?,0,?,1,?)",(uid,email,email,datetime.utcnow().isoformat()))
        await db.execute("DELETE FROM customer_login_codes WHERE email=?",(email,)); await db.commit()
    resp=RedirectResponse("/account",status_code=303); await create_customer_session(resp,uid); return resp

@app.post("/auth/telegram")
async def customer_telegram_auth(request: Request):
    form=await request.form(); data={k:str(v) for k,v in form.items()}; received=data.pop("hash",""); token=core.BOT_TOKEN
    if not token or not received: return HTMLResponse('{"error":"telegram auth unavailable"}',status_code=400,media_type="application/json")
    check="\n".join(f"{k}={v}" for k,v in sorted(data.items())); secret=hashlib.sha256(token.encode()).digest(); expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    try: auth_date=int(data.get("auth_date","0")); tid=int(data.get("id","0"))
    except: auth_date=0; tid=0
    now_ts=datetime.utcnow().timestamp()
    if not hmac.compare_digest(expected,received) or auth_date>now_ts+30 or now_ts-auth_date>86400 or tid<=0:
        return HTMLResponse('{"error":"invalid telegram signature"}',status_code=403,media_type="application/json")
    current=get_customer_session(request)
    try:
        if current and int(current["user_id"])<0: uid=await merge_email_account_into_telegram(int(current["user_id"]),{"id":tid,"username":data.get("username"),"first_name":data.get("first_name",""),"last_name":data.get("last_name","")})
        else:
            async with aiosqlite.connect(DB_PATH) as db: await core.ensure_user(db,tid,data.get("username"),(data.get("first_name","")+" "+data.get("last_name","")).strip())
            uid=tid
    except Exception:
        return HTMLResponse('{"error":"account link conflict"}',status_code=409,media_type="application/json")
    resp=HTMLResponse('{"redirect":"/account"}',media_type="application/json"); await create_customer_session(resp,uid); return resp

@app.get("/auth/telegram/link/{token}")
async def telegram_deeplink_login(request: Request, token: str):
    token_hash=hashlib.sha256(token.encode()).hexdigest()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        row=await (await db.execute("SELECT id,telegram_id,redirect_path,expires_at,used_at FROM customer_web_tokens WHERE token_hash=?",(token_hash,))).fetchone()
        if not row or row[4] or datetime.fromisoformat(row[3])<datetime.utcnow():
            await db.rollback(); return storefront_page("لینک نامعتبر",'<div class="card">لینک ورود منقضی یا استفاده شده است.</div>')
        cur=await db.execute("UPDATE customer_web_tokens SET used_at=? WHERE id=? AND used_at IS NULL",(datetime.utcnow().isoformat(),row[0]))
        if cur.rowcount!=1:
            await db.rollback(); return storefront_page("لینک نامعتبر",'<div class="card">این لینک قبلاً استفاده شده است.</div>')
        await db.commit()
    resp=RedirectResponse(row[2] or "/shop",status_code=303); await create_customer_session(resp,row[1]); return resp

@app.get("/account", response_class=HTMLResponse)
async def customer_account(request: Request):
    sess=get_customer_session(request)
    if not sess: return RedirectResponse("/customer/login",status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        u=await (await db.execute("SELECT telegram_id,full_name,balance,email,email_verified FROM users WHERE telegram_id=?",(sess["user_id"],))).fetchone()
        orders=await (await db.execute("SELECT id,product_name,quantity,amount_usd,status,delivery_token_hash FROM product_orders WHERE user_id=? ORDER BY id DESC LIMIT 30",(sess["user_id"],))).fetchall()
        servers=await (await db.execute("SELECT s.id,s.name,s.ip,s.plan_title,s.status,s.expires_at,s.root_password FROM servers s WHERE s.user_id=? "
            "AND NOT EXISTS (SELECT 1 FROM servers d WHERE d.provider_type IS s.provider_type AND d.provider_id IS s.provider_id "
            "AND d.hetzner_id=s.hetzner_id AND d.user_id=s.user_id AND d.id<s.id) ORDER BY s.id DESC LIMIT 30",(sess["user_id"],))).fetchall()
        server_orders=await (await db.execute("SELECT id,plan_title,location,price_usd,status,created_at,server_db_id FROM web_server_orders WHERE user_id=? ORDER BY id DESC LIMIT 30",(sess["user_id"],))).fetchall()
        rate=float(await core.get_usdt_toman_rate(db))
    if not u: return RedirectResponse("/customer/login",status_code=303)
    tr=''.join('<tr><td>#'+str(o[0])+'</td><td>'+esc(o[1])+' ×'+str(o[2])+'</td><td>'+f'${float(o[3] or 0):.2f} USD'+'</td><td>'+esc(o[4])+'</td><td>'+('<a href="/account/order/'+str(o[0])+'">جزئیات / تحویل</a>' if o[5] else '-')+'</td></tr>' for o in orders)
    body='<h1>حساب کاربری</h1><div class="card"><p>ایمیل: '+esc(u[3] or '-')+' | وضعیت: '+('تأییدشده' if u[4] else 'تأیید نشده')+'</p><h2>موجودی کیف پول: $'+f'{float(u[2] or 0):.2f}'+'</h2><p>نرخ فعلی: هر ۱ دلار = '+f'{rate:,.0f}'+' تومان</p><p><a class="btn" href="/shop">فروشگاه</a> <a class="btn gray" href="/servers/buy">خرید سرور</a> <a href="/customer/login?link=telegram">اتصال تلگرام</a> <a href="/customer/logout">خروج</a></p></div><div class="card"><h2>سفارش‌های من</h2><table><tr><th>سفارش</th><th>محصول</th><th>مبلغ</th><th>وضعیت</th><th>تحویل</th></tr>'+ (tr or '<tr><td colspan="5">هنوز سفارشی ندارید.</td></tr>') + '</table></div>'
    svtr=''.join('<tr><td>#'+str(v[0])+'</td><td>'+esc(v[1])+'</td><td>'+esc(v[2])+'</td><td>'+esc(v[3])+'</td><td>'+esc(v[4])+'</td><td>'+esc(core.iran_time(v[5]).strftime("%Y-%m-%d %H:%M") if core.iran_time(v[5]) else v[5])+'</td><td><code>'+esc(v[6] or "از طریق پنل/ربات تغییر رمز دهید")+'</code></td></tr>' for v in servers)
    body += '<div class="card"><h2>سرورهای من</h2><table><tr><th>شناسه</th><th>نام</th><th>IP</th><th>پلن</th><th>وضعیت</th><th>انقضا</th><th>رمز root</th></tr>'+ (svtr or '<tr><td colspan="7">سروری ثبت نشده است.</td></tr>') + '</table></div>'
    sot=''.join('<tr><td>#'+str(x[0])+'</td><td>'+esc(x[1])+'</td><td>'+esc(x[2])+'</td><td>$'+f'{float(x[3]):.2f}'+'</td><td>'+esc(x[4])+'</td><td>'+str(x[6] or '-')+'</td></tr>' for x in server_orders)
    body += '<div class="card"><h2>سفارش‌های سرور</h2><table><tr><th>سفارش</th><th>پلن</th><th>لوکیشن</th><th>مبلغ</th><th>وضعیت</th><th>شناسه سرور</th></tr>'+ (sot or '<tr><td colspan="6">سفارشی ثبت نشده است.</td></tr>') + '</table></div>'
    return storefront_page("حساب کاربری",body,sess)

@app.get("/customer/logout")
async def customer_logout(request: Request):
    token=request.cookies.get("customer_session"); CUSTOMER_SESSIONS.pop(token,None)
    resp=RedirectResponse("/shop",status_code=303); resp.delete_cookie("customer_session"); return resp

@app.get("/account/order/{oid}",response_class=HTMLResponse)
async def customer_order_detail(request: Request,oid:int):
    sess=get_customer_session(request)
    if not sess:return RedirectResponse("/customer/login",status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        row=await (await db.execute("SELECT id,product_name,quantity,status,delivery_token_hash FROM product_orders WHERE id=? AND user_id=?",(oid,sess["user_id"]))).fetchone()
    if not row:return storefront_page("سفارش",'<div class="card">سفارش پیدا نشد.</div>',sess)
    delivery=''
    if row[4] and row[3]=='delivered':
        async with aiosqlite.connect(DB_PATH) as db: items=await (await db.execute("SELECT payload FROM product_inventory WHERE order_id=? ORDER BY id",(oid,))).fetchall()
        delivery=''.join('<pre style="white-space:pre-wrap;direction:ltr;text-align:left">'+esc(x[0])+'</pre>' for x in items)
    return storefront_page("جزئیات سفارش",'<div class="card"><h1>سفارش #'+str(row[0])+'</h1><p>'+esc(row[1])+' × '+str(row[2])+' | وضعیت: '+esc(row[3])+'</p>'+delivery+'</div>',sess)


# ---------------------------------------------------------------------------
# داشبورد
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def public_home(request: Request):
    customer = get_customer_session(request)
    body = """
    <section class="cc-hero">
      <div class="cc-kicker">CLOUD INFRASTRUCTURE • CLOUD_CENTER</div>
      <h1>قدرت میزبانی <span>هوشمند</span></h1>
      <p>سرورهای مجازی و اکانت‌های معتبر با تحویل سریع، مدیریت آسان و پشتیبانی حرفه‌ای؛ همه‌چیز برای شروع یک زیرساخت مطمئن.</p>
      <a class="cc-primary" href="/servers/buy">ساخت سرور <span>‹</span></a>
      <div class="cc-metrics">
        <div class="cc-metric"><strong>تحویل سریع</strong><small>فرآیند سفارش خودکار</small></div>
        <div class="cc-metric"><strong>مدیریت آسان</strong><small>پنل اختصاصی مشتریان</small></div>
        <div class="cc-metric"><strong>پرداخت منعطف</strong><small>کیف پول و درگاه پرداخت</small></div>
        <div class="cc-metric"><strong>پشتیبانی حرفه‌ای</strong><small>همراه شما در تمام مراحل</small></div>
      </div>
    </section>
    <section class="cc-section">
      <div class="cc-section-title"><h2>خدمات CLOUD_CENTER</h2><p>زیرساخت موردنیازت را در چند مرحله انتخاب و تهیه کن.</p></div>
      <div class="cc-feature-grid">
        <a class="cc-feature" href="/servers/buy" style="text-decoration:none"><span class="cc-glow">▣</span><b>خرید سرور مجازی</b><p>انتخاب پلن، موقعیت و سیستم‌عامل و ثبت سفارش از فروشگاه.</p></a>
        <a class="cc-feature" href="/shop" style="text-decoration:none"><span class="cc-glow">◈</span><b>فروشگاه اکانت</b><p>مشاهده محصولات دیجیتال، موجودی و جزئیات پیش از خرید.</p></a>
        <a class="cc-feature" href="/account" style="text-decoration:none"><span class="cc-glow">⌘</span><b>حساب کاربری</b><p>پیگیری سفارش‌ها، سرورها و موجودی کیف پول در یک داشبورد.</p></a>
      </div>
    </section>
    """
    return storefront_page("فروشگاه سرور و اکانت", body, customer)

@app.get("/shop", response_class=HTMLResponse)
async def shop_home(request: Request):
    customer = get_customer_session(request)
    async with aiosqlite.connect(DB_PATH) as db:
        cats = await (await db.execute("SELECT id,name FROM product_categories WHERE active=1 ORDER BY name")).fetchall()
        category_filter = request.query_params.get("category")
        query = "SELECT p.id,p.name,p.description,p.image_url,p.price_usd,p.stock,c.name FROM products p LEFT JOIN product_categories c ON c.id=p.category_id WHERE p.active=1 AND p.stock>0 AND (c.active=1 OR p.category_id IS NULL)"
        args = []
        if category_filter and category_filter.isdigit():
            query += " AND p.category_id=?"; args.append(int(category_filter))
        query += " ORDER BY c.name,p.id DESC"
        rows = await (await db.execute(query, args)).fetchall()
    cards=[]
    for r in rows:
        img=f'<img src="{esc(r[3])}" style="max-width:100%;max-height:180px;object-fit:contain">' if r[3] else ''
        cards.append(f'<div class="card">{img}<h3>{esc(r[1])}</h3><p>{esc(r[6] or "بدون دسته")}</p><p>{esc((r[2] or "")[:220])}</p><b>${float(r[4]):.2f}</b><p>موجودی: {r[5]}</p><a class="btn" href="/shop/{r[0]}">مشاهده و خرید</a></div>')
    cat_html = '<div class="card"><h3>دسته‌بندی‌ها</h3>'+' '.join(f'<a class="btn gray" href="/shop?category={c[0]}">{esc(c[1])}</a>' for c in cats)+'</div>' if cats else ""
    return storefront_page("فروشگاه", '<h1>🛍 فروشگاه</h1>'+cat_html+'<div class="grid">'+(''.join(cards) or '<div class="card">در این دسته فعلاً محصولی موجود نیست.</div>')+'</div>', customer)

@app.get("/shop/{pid}", response_class=HTMLResponse)
async def shop_product(request: Request, pid:int):
    customer=get_customer_session(request)
    async with aiosqlite.connect(DB_PATH) as db:
        r=await (await db.execute("SELECT id,name,description,image_url,price_usd,stock FROM products WHERE id=? AND active=1",(pid,))).fetchone()
        rate=float(await core.get_usdt_toman_rate(db))
        user=await (await db.execute("SELECT balance,email,full_name FROM users WHERE telegram_id=?",(customer["user_id"],))).fetchone() if customer else None
        zarin=bool(await core.get_setting(db,"zarinpal_merchant_id","")); oxapay=bool(await core.get_setting(db,"oxapay_api_key","")); nowp=bool(await core.get_setting(db,"nowpayments_api_key",""))
    if not r: return storefront_page("محصول",'<div class="card">محصول پیدا نشد.</div>',customer)
    if not customer:
        return storefront_page(r[1],f'<div class="card"><h1>{esc(r[1])}</h1><p>{esc(r[2])}</p><h2>${float(r[4]):.2f}</h2><p>برای خرید ابتدا وارد حساب شوید.</p><a class="btn" href="/customer/login">ورود / ثبت‌نام</a></div>',None)
    img=f'<img src="{esc(r[3])}" style="max-width:100%;max-height:300px">' if r[3] else ''
    methods='<option value="wallet">فقط کیف پول</option>'
    if zarin: methods+='<option value="zarinpal">زرین‌پال (ریالی)</option><option value="mixed_zarinpal">کیف پول + مابه‌التفاوت زرین‌پال</option>'
    if oxapay: methods+='<option value="oxapay">OxaPay (ارزی)</option><option value="mixed_oxapay">کیف پول + مابه‌التفاوت OxaPay</option>'
    if nowp: methods+='<option value="nowpayments">NOWPayments (ارزی)</option><option value="mixed_nowpayments">کیف پول + مابه‌التفاوت NOWPayments</option>'
    balance=float(user[0] or 0) if user else 0
    body=f'''<h1>{esc(r[1])}</h1><div class="card">{img}<p>{esc(r[2])}</p><h2>${float(r[4]):.2f}</h2><p>موجودی: {r[5]}</p><p>کیف پول شما: ${balance:.2f} | نرخ تبدیل: هر ۱ دلار = {rate:,.0f} تومان</p><form method="post" action="/shop/{pid}/checkout"><label>نام خریدار</label><input name="customer_name" value="{esc((user[2] or '') if user and user[2] != user[1] else '')}" required><label>موبایل</label><input name="mobile" required pattern="09[0-9]{{9}}" placeholder="09123456789"><label>ایمیل</label><input name="email" type="email" value="{esc((user[1] or '') if user else '')}"><label>تعداد</label><input name="quantity" type="number" min="1" max="{r[5]}" value="1"><label>روش پرداخت</label><select name="payment_method">{methods}</select><button class="btn green">ثبت سفارش و ادامه پرداخت</button></form></div>'''
    return storefront_page(r[1],body,customer)


async def _create_product_gateway_payment(request: Request, order_id: int, method: str, due_usd: float, due_toman: int, rate: float, email: str):
    base=os.getenv("PUBLIC_BASE_URL",str(request.base_url).rstrip("/")).rstrip("/")
    if method=="zarinpal":
        async with aiosqlite.connect(DB_PATH) as db:
            merchant=await core.get_setting(db,"zarinpal_merchant_id",""); sandbox=(await core.get_setting(db,"zarinpal_sandbox","0")) in ("1","true","yes")
        if not merchant: raise RuntimeError("زرین‌پال پیکربندی نشده است")
        req_url,_,start=_zarinpal_endpoints(sandbox)
        async with httpx.AsyncClient(timeout=25) as client:
            resp=await client.post(req_url,json={"merchant_id":merchant,"amount":int(due_toman)*10,"currency":"IRR","callback_url":base+"/shop/payment/callback","description":f"فروش محصول - سفارش {order_id}","metadata":{"email":email}}); resp.raise_for_status(); data=resp.json()
        d=data.get("data") or {}; authority=d.get("authority")
        if int(d.get("code",0))!=100 or not authority: raise RuntimeError(str(data.get("errors") or data)[:250])
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE product_orders SET authority=?,gateway_track_id=? WHERE id=?",(authority,authority,order_id)); await db.commit()
        return RedirectResponse(start+authority,status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        oxakey=await core.get_setting(db,"oxapay_api_key",""); nowkey=await core.get_setting(db,"nowpayments_api_key",""); pay_currency=await core.get_setting(db,"nowpayments_pay_currency",core.NOWPAYMENTS_PAY_CURRENCY)
    usd_due=round(float(due_usd),2)
    if method=="oxapay":
        if not oxakey: raise RuntimeError("کلید OxaPay تنظیم نشده است")
        track,pay_url=await core.oxapay_create_invoice(oxakey,usd_due,f"p{order_id}",description=f"Product order {order_id}")
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE product_orders SET gateway_track_id=? WHERE id=?",(str(track),order_id)); await db.commit()
        return RedirectResponse(pay_url,status_code=303)
    if method=="nowpayments":
        if not nowkey: raise RuntimeError("کلید NOWPayments تنظیم نشده است")
        payment_id,address,amount,currency=await core.nowpayments_create_payment(nowkey,usd_due,f"p{order_id}",pay_currency,description=f"Product order {order_id}")
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE product_orders SET gateway_track_id=? WHERE id=?",(str(payment_id),order_id)); await db.commit()
        body=f'<div class="card"><h1>پرداخت ارزی سفارش #{order_id}</h1><p>مبلغ پرداخت: {esc(amount)} {esc(currency)}</p><p>آدرس پرداخت:</p><code style="direction:ltr;word-break:break-all">{esc(address)}</code><p>پس از تأیید شبکه، سفارش به‌صورت خودکار تکمیل می‌شود. وضعیت را از حساب کاربری پیگیری کنید.</p><a class="btn" href="/account">حساب کاربری</a></div>'
        return storefront_page("پرداخت ارزی",body)
    raise RuntimeError("روش پرداخت پشتیبانی نمی‌شود")

@app.post("/shop/{pid}/checkout")
async def shop_checkout(request: Request,pid:int):
    customer=get_customer_session(request)
    if not customer:return RedirectResponse("/customer/login",status_code=303)
    form=await request.form(); name=str(form.get("customer_name"," ")).strip()[:120]; mobile=str(form.get("mobile"," ")).strip(); email=str(form.get("email"," ")).strip()[:160]
    method=str(form.get("payment_method","wallet")); method=method if method in ("wallet","zarinpal","oxapay","nowpayments","mixed_zarinpal","mixed_oxapay","mixed_nowpayments") else "wallet"
    try: qty=int(form.get("quantity",1))
    except: qty=0
    if not name or not mobile.isdigit() or len(mobile)!=11 or not mobile.startswith("09") or qty<1 or qty>100:return storefront_page("خطا",'<div class="card">اطلاعات واردشده معتبر نیست.</div>',customer)
    order_id=None; due_usd=0.0; due_toman=0; rate=100000; email=email
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        prod=await (await db.execute("SELECT name,price_usd,stock FROM products WHERE id=? AND active=1",(pid,))).fetchone()
        user=await (await db.execute("SELECT balance,email,full_name FROM users WHERE telegram_id=?",(customer["user_id"],))).fetchone()
        rate=float(await core.get_usdt_toman_rate(db, require_fresh=True))
        if not prod or not user or prod[2]<qty or rate<=0:
            await db.rollback();return storefront_page("موجودی",'<div class="card">محصول یا موجودی کافی نیست.</div>',customer)
        total=round(float(prod[1])*qty, 6); wallet_balance=float(user[0] or 0); wallet_used=0.0
        if method=="wallet":
            needed=math.ceil(total*1_000_000)/1_000_000
            if wallet_balance+0.0000001<needed:
                await db.rollback();return storefront_page("موجودی کیف پول",f'<div class="card">موجودی کافی نیست. برای پرداخت ترکیبی، روش «کیف پول + مابه‌التفاوت» را انتخاب کنید. مبلغ لازم: ${needed:.2f}</div>',customer)
            wallet_used=needed
        elif method.startswith("mixed_"):
            needed=math.ceil(total*1_000_000)/1_000_000
            wallet_used=min(max(wallet_balance,0.0),needed)
        due_usd=max(0.0,round(total-wallet_used,6))
        gateway_method=method.split("_",1)[1] if method.startswith("mixed_") else method
        due_toman=int(round(due_usd*rate)) if gateway_method=="zarinpal" else 0
        if due_usd>0 and method=="wallet":
            await db.rollback();return storefront_page("خطا",'<div class="card">پرداخت کیف پول کامل نیست.</div>',customer)
        if due_usd>0 and gateway_method not in ("zarinpal","oxapay","nowpayments"):
            await db.rollback();return storefront_page("خطا",'<div class="card">درگاه پرداخت انتخاب نشده است.</div>',customer)
        cur=await db.execute("INSERT INTO product_orders(product_id,product_name,quantity,customer_name,mobile,email,amount_usd,amount_toman,status,created_at,user_id,wallet_used_usd,gateway_method,gateway_due_toman,currency_rate) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(pid,prod[0],qty,name,mobile,email or user[1] or "",total,int(round(total*rate)),"pending_payment",datetime.utcnow().isoformat(),customer["user_id"],wallet_used,gateway_method if due_usd else "wallet",due_toman,rate))
        order_id=cur.lastrowid
        if wallet_used>0:
            balcur=await db.execute("UPDATE users SET balance=balance-? WHERE telegram_id=? AND balance>=?",(wallet_used,customer["user_id"],wallet_used))
            if balcur.rowcount!=1:
                await db.rollback();return storefront_page("کیف پول",'<div class="card">موجودی کیف پول تغییر کرده است؛ دوباره تلاش کنید.</div>',customer)
            await db.execute("INSERT INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)",(customer["user_id"],-wallet_used,"product_purchase",f"product-order-{order_id}-debit",datetime.utcnow().isoformat()))
        await db.commit()
    if due_usd==0:
        token=await core.complete_product_order(order_id,"WALLET")
        if token:
            base=os.getenv("PUBLIC_BASE_URL",str(request.base_url).rstrip("/")).rstrip("/")
            return storefront_page("خرید موفق",f'<div class="card"><h1>خرید و تحویل موفق</h1><p>سفارش #{order_id} با کیف پول پرداخت شد.</p><a class="btn green" href="/account/order/{order_id}">مشاهده اطلاعات اکانت</a></div>',customer)
        return storefront_page("ثبت سفارش",f'<div class="card">پرداخت انجام شد اما موجودی تحویل نیازمند بررسی پشتیبانی است. شماره سفارش #{order_id}</div>',customer)
    try:
        return await _create_product_gateway_payment(request,order_id,gateway_method,due_usd,due_toman,rate,email)
    except Exception as exc:
        await core.refund_product_wallet(order_id)
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE product_orders SET status='payment_failed' WHERE id=? AND status='pending_payment'",(order_id,)); await db.commit()
        return storefront_page("خطای درگاه",f'<div class="card">ایجاد پرداخت انجام نشد. سهم کیف پول به حساب شما برگشت داده شد. سفارش #{order_id}<p>{esc(str(exc)[:250])}</p></div>',customer)

@app.get("/shop/payment/callback",response_class=HTMLResponse)
async def shop_payment_callback(request:Request):
    authority=request.query_params.get("Authority",""); status=request.query_params.get("Status","").upper()
    if status!="OK" or not authority:
        async with aiosqlite.connect(DB_PATH) as db:
            row=await (await db.execute("SELECT id FROM product_orders WHERE authority=?",(authority,))).fetchone()
            server_row=await (await db.execute("SELECT id FROM web_server_orders WHERE authority=?",(authority,))).fetchone()
        if row: await core.refund_product_wallet(row[0])
        if server_row: await core.refund_web_server_wallet(server_row[0])
        return storefront_page("پرداخت ناموفق",'<div class="card">پرداخت لغو شد یا ناموفق بود. در صورت استفاده از کیف پول، مبلغ آن برگشت داده شده است.</div>')
    async with aiosqlite.connect(DB_PATH) as db:
        order=await (await db.execute("SELECT id,gateway_due_toman,status,amount_toman FROM product_orders WHERE authority=?",(authority,))).fetchone()
        server_order=None
        if not order:
            server_order=await (await db.execute("SELECT id,gateway_due_usd,status,gateway_due_toman FROM web_server_orders WHERE authority=?",(authority,))).fetchone()
        merchant=await core.get_setting(db,"zarinpal_merchant_id",""); sandbox=(await core.get_setting(db,"zarinpal_sandbox","0")) in ("1","true","yes")
        
    if not order and not server_order:return storefront_page("سفارش",'<div class="card">سفارش یافت نشد.</div>')
    is_server=server_order is not None
    if is_server:
        oid,due_usd,st,due=server_order; due=int(due or 0)
        if due<=0:return storefront_page("پرداخت",'<div class="card">مبلغ ریالی این سفارش در زمان ثبت ذخیره نشده است؛ برای جلوگیری از دریافت مبلغ اشتباه، پرداخت خودکار متوقف شد. با پشتیبانی تماس بگیرید.</div>')
    else:
        oid,due,st,total_toman=order
        due=int(due or total_toman)
        if st == "pending":
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE product_orders SET status='pending_payment',gateway_due_toman=? WHERE id=?",(due,oid)); await db.commit()
    if st in ("paid","delivered","paid_stock_issue","completed"):return storefront_page("پرداخت",f'<div class="card">سفارش #{oid} قبلاً پردازش شده است.</div>')
    _,verify_url,_=_zarinpal_endpoints(sandbox)
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp=await client.post(verify_url,json={"merchant_id":merchant,"amount":int(due)*10,"authority":authority});resp.raise_for_status();data=resp.json()
        d=data.get("data") or {};code=int(d.get("code",0))
        if code not in (100,101):raise ValueError(str(data.get("errors") or data)[:250])
        ref=str(d.get("ref_id",""))
        if is_server:
            server_id=await core.complete_web_server_order(oid,ref)
            if server_id:return storefront_page("سرور آماده",f'<div class="card"><h2>پرداخت و ساخت سرور موفق</h2><p>سفارش #{oid} | شناسه سرور #{server_id}</p><a class="btn" href="/account">مشاهده حساب کاربری</a><p>کد پیگیری: {esc(ref)}</p></div>')
            return storefront_page("پرداخت موفق",f'<div class="card">پرداخت انجام شد اما ساخت سرور نیازمند بررسی است. شماره سفارش #{oid}</div>')
        token=await core.complete_product_order(oid,ref)
        if token:return storefront_page("تحویل موفق",f'<div class="card"><h2>پرداخت و تحویل موفق</h2><p>سفارش #{oid} ثبت شد.</p><a class="btn green" href="/account/order/{oid}">مشاهده اطلاعات اکانت</a><p>کد پیگیری: {esc(ref)}</p></div>')
        return storefront_page("پرداخت موفق",f'<div class="card">پرداخت سفارش #{oid} موفق بود. وضعیت سفارش را در حساب کاربری پیگیری کنید. کد پیگیری: {esc(ref)}</div>')
    except Exception as exc:
        core.logger.exception("Zarinpal verify failed")
        return storefront_page("در انتظار بررسی",f'<div class="card">نتیجه پرداخت سفارش #{oid} قابل تأیید نیست؛ با پشتیبانی تماس بگیرید. {esc(str(exc)[:180])}</div>')

@app.get("/servers/buy",response_class=HTMLResponse)
async def web_server_buy(request: Request):
    customer=get_customer_session(request)
    if not customer:return RedirectResponse("/customer/login",status_code=303)
    provider=str(request.query_params.get("provider", "")); location=str(request.query_params.get("location", "")); plan_id=request.query_params.get("plan_id")
    async with aiosqlite.connect(DB_PATH) as db:
        enabled=await core.get_enabled_provider_types(db)
    if not provider:
        opts=''.join(f'<option value="{esc(p)}">{esc(core.PROVIDER_LABELS.get(p,p))}</option>' for p in enabled)
        return storefront_page("خرید سرور",'<div class="card"><h1>خرید سرور مجازی</h1><form method="get"><label>سرویس‌دهنده</label><select name="provider">'+opts+'</select><button class="btn">ادامه</button></form></div>',customer)
    if provider not in enabled:return storefront_page("سرویس‌دهنده",'<div class="card">این سرویس‌دهنده فعال نیست.</div>',customer)
    async with aiosqlite.connect(DB_PATH) as db: token=await core.get_active_token_for(db,provider)
    if not token:return storefront_page("API",'<div class="card">API فعال پیدا نشد.</div>',customer)
    api=core.make_provider_api(provider,token)
    try: locations=await api.locations()
    except Exception as exc:return storefront_page("خطای سرویس‌دهنده",f'<div class="card">دریافت دیتاسنترها ناموفق بود: {esc(exc)}</div>',customer)
    if not location:
        opts=''.join(f'<option value="{esc(x["name"])}">{esc(x.get("city") or x["name"])} {esc(x.get("country") or "")}</option>' for x in locations)
        return storefront_page("انتخاب دیتاسنتر",f'<div class="card"><h1>انتخاب دیتاسنتر</h1><form method="get"><input type="hidden" name="provider" value="{esc(provider)}"><label>موقعیت</label><select name="location">{opts}</select><button class="btn">نمایش پلن‌ها</button></form></div>',customer)
    async with aiosqlite.connect(DB_PATH) as db:
        plans=await (await db.execute("SELECT id,title,cpu,ram,disk,server_type,markup_percent FROM plans WHERE active=1 AND provider_type=? AND (location IS NULL OR location='' OR location=?) ORDER BY id",(provider,location))).fetchall()
        if provider=="hetzner":
            providers=await core.get_active_providers(db,provider)
            pricing=await core.make_provider_api(provider,providers[0][1]).pricing() if providers else None
            stock_sets=[]
            for _,ptoken in providers:
                try: stock_sets.append(await core.available_types_for_location(core.HetznerAPI(ptoken),location))
                except Exception: pass
            available=set().union(*(x for x in stock_sets if x is not None)) if stock_sets and any(x is not None for x in stock_sets) else None
            priced=[]
            for pl in plans:
                price=await core.compute_price_usd(db,pricing,pl[5],location,pl[6]) if pricing else None
                if price is not None and (available is None or pl[5] in available): priced.append((pl,price))
        else:
            types=await api.server_types(); priced=[]
            for pl in plans:
                price=core.compute_price_generic(types,pl[5],pl[6])
                if price is not None: priced.append((pl,price))
    if not plan_id:
        opts=''.join(f'<option value="{pl[0]}">{esc(pl[1])} · {esc(pl[2])} CPU / {esc(pl[3])} RAM / {esc(pl[4])} · ${price:.2f}/ماه</option>' for pl,price in priced)
        return storefront_page("انتخاب پلن",f'<div class="card"><h1>انتخاب پلن</h1><p>دیتاسنتر: {esc(location)}</p><form method="get"><input type="hidden" name="provider" value="{esc(provider)}"><input type="hidden" name="location" value="{esc(location)}"><label>پلن</label><select name="plan_id">{opts}</select><button class="btn">انتخاب سیستم‌عامل</button></form></div>',customer)
    try: plan_id=int(plan_id)
    except: return storefront_page("پلن",'<div class="card">پلن نامعتبر است.</div>',customer)
    chosen=next(((pl,price) for pl,price in priced if pl[0]==plan_id),None)
    if not chosen:return storefront_page("پلن",'<div class="card">پلن فعال یا موجود نیست. لطفاً دوباره انتخاب کنید.</div>',customer)
    pl,price=chosen
    try: images=core.deduplicate_images(await core.images_for_plan(provider,api,pl[5]))
    except Exception as exc:return storefront_page("سیستم‌عامل",f'<div class="card">دریافت سیستم‌عامل‌ها ناموفق بود: {esc(exc)}</div>',customer)
    img_opts=''.join(f'<option value="{esc(i.get("id"))}">{esc(i.get("name") or i.get("description") or i.get("id"))}</option>' for i in images)
    async with aiosqlite.connect(DB_PATH) as db:
        user=await (await db.execute("SELECT balance FROM users WHERE telegram_id=?",(customer["user_id"],))).fetchone()
        rate=float(await core.get_usdt_toman_rate(db))
        zarin=bool(await core.get_setting(db,"zarinpal_merchant_id",""));oxa=bool(await core.get_setting(db,"oxapay_api_key",""));nowp=bool(await core.get_setting(db,"nowpayments_api_key",""))
    methods='<option value="wallet">فقط کیف پول</option>'
    if zarin:methods+='<option value="zarinpal">زرین‌پال</option><option value="mixed_zarinpal">کیف پول + مابه‌التفاوت زرین‌پال</option>'
    if oxa:methods+='<option value="oxapay">OxaPay</option><option value="mixed_oxapay">کیف پول + مابه‌التفاوت OxaPay</option>'
    if nowp:methods+='<option value="nowpayments">NOWPayments</option><option value="mixed_nowpayments">کیف پول + مابه‌التفاوت NOWPayments</option>'
    body=f'<div class="card"><h1>تأیید سفارش سرور</h1><p>{esc(pl[1])} | {esc(location)} | ${price:.2f} در ماه</p><p>موجودی کیف پول: ${float(user[0] or 0):.2f}</p><form method="post" action="/servers/buy/checkout"><input type="hidden" name="provider" value="{esc(provider)}"><input type="hidden" name="location" value="{esc(location)}"><input type="hidden" name="plan_id" value="{plan_id}"><label>سیستم‌عامل</label><select name="image">{img_opts}</select><label>روش پرداخت</label><select name="payment_method">{methods}</select><button class="btn green">ثبت سفارش و پرداخت</button></form></div>'
    return storefront_page("تأیید سرور",body,customer)

@app.post("/servers/buy/checkout")
async def web_server_checkout(request:Request):
    customer=get_customer_session(request)
    if not customer:return RedirectResponse("/customer/login",status_code=303)
    form=await request.form();provider=str(form.get("provider",""));location=str(form.get("location",""));image=str(form.get("image",""));method=str(form.get("payment_method","wallet"))
    try:plan_id=int(form.get("plan_id",0))
    except:plan_id=0
    if method not in ("wallet","zarinpal","oxapay","nowpayments","mixed_zarinpal","mixed_oxapay","mixed_nowpayments"):method="wallet"
    async with aiosqlite.connect(DB_PATH) as db:
        if provider not in await core.get_enabled_provider_types(db):return storefront_page("خطا",'<div class="card">سرویس‌دهنده نامعتبر است.</div>',customer)
        token=await core.get_active_token_for(db,provider);api=core.make_provider_api(provider,token) if token else None
        plan=await (await db.execute("SELECT id,title,server_type,markup_percent FROM plans WHERE id=? AND active=1 AND provider_type=? AND (location IS NULL OR location='' OR location=?)",(plan_id,provider,location))).fetchone()
        user=await (await db.execute("SELECT balance FROM users WHERE telegram_id=?",(customer["user_id"],))).fetchone()
        rate=float(await core.get_usdt_toman_rate(db, require_fresh=True))
        pricing=None;types=None
        if provider=="hetzner":
            providers=await core.get_active_providers(db,provider)
            if providers: pricing=await core.make_provider_api(provider,providers[0][1]).pricing()
            stock_ok=False; stock_unknown=False
            for _,ptoken in providers:
                try:
                    avail=await core.available_types_for_location(core.HetznerAPI(ptoken),location)
                    if avail is None: stock_unknown=True
                    elif plan and plan[2] in avail: stock_ok=True
                except: stock_unknown=True
            if not stock_ok and not stock_unknown:return storefront_page("موجودی",'<div class="card">این پلن اکنون در دیتاسنتر انتخابی موجود نیست.</div>',customer)
            price=await core.compute_price_usd(db,pricing,plan[2],location,plan[3]) if plan and pricing else None
        else:
            types=await api.server_types() if api else []
            price=core.compute_price_generic(types,plan[2],plan[3]) if plan else None
        if not plan or price is None or not image or rate<=0:return storefront_page("خطا",'<div class="card">اطلاعات پلن، قیمت یا سیستم‌عامل معتبر نیست.</div>',customer)
        try:
            images=await core.deduplicate_images(await core.images_for_plan(provider,api,plan[2]))
            if str(image) not in {str(x.get("id")) for x in images}:raise ValueError("OS image invalid")
        except Exception:return storefront_page("سیستم‌عامل",'<div class="card">سیستم‌عامل انتخاب‌شده معتبر نیست.</div>',customer)
        wallet_balance=float(user[0] or 0) if user else 0
        if method=="wallet":
            if wallet_balance+0.000001<price:return storefront_page("کیف پول",f'<div class="card">موجودی کافی نیست؛ مبلغ لازم ${price:.2f} است. روش پرداخت ترکیبی را انتخاب کنید.</div>',customer)
            wallet_used=round(price,2)
        elif method.startswith("mixed_"):wallet_used=min(max(wallet_balance,0.0),round(price,2))
        else:wallet_used=0.0
        gateway_method=method.split("_",1)[1] if method.startswith("mixed_") else method
        due=max(0,round(float(price)-wallet_used,2))
        await db.execute("BEGIN IMMEDIATE")
        due_toman=int(round(due*rate)) if gateway_method=="zarinpal" else 0
        cur=await db.execute("INSERT INTO web_server_orders(user_id,provider_type,location,plan_id,plan_title,server_type,image,price_usd,wallet_used_usd,gateway_method,gateway_due_usd,gateway_due_toman,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(customer["user_id"],provider,location,plan_id,plan[1],plan[2],image,float(price),wallet_used,gateway_method,due,due_toman,"pending_payment",datetime.utcnow().isoformat()))
        oid=cur.lastrowid
        if wallet_used>0:
            changed=await db.execute("UPDATE users SET balance=balance-? WHERE telegram_id=? AND balance>=?",(wallet_used,customer["user_id"],wallet_used))
            if changed.rowcount!=1:await db.rollback();return storefront_page("کیف پول",'<div class="card">موجودی کیف پول تغییر کرده؛ دوباره تلاش کنید.</div>',customer)
            await db.execute("INSERT INTO wallet_transactions(user_id,amount_usd,kind,reference,created_at) VALUES(?,?,?,?,?)",(customer["user_id"],-wallet_used,"server_purchase",f"web-server-{oid}-debit",datetime.utcnow().isoformat()))
        await db.commit()
    if due==0:
        server_id=await core.complete_web_server_order(oid,"WALLET")
        if server_id:return storefront_page("خرید سرور",f'<div class="card"><h1>سرور ساخته شد</h1><p>سفارش #{oid} | شناسه سرور #{server_id}</p><a class="btn" href="/account">حساب کاربری</a></div>',customer)
        return storefront_page("ساخت سرور",f'<div class="card">پرداخت از کیف پول انجام شد اما ساخت سرور ناموفق بود. سهم کیف پول برگشت داده شده است. شماره سفارش #{oid}</div>',customer)
    try:
        if gateway_method=="zarinpal":
            async with aiosqlite.connect(DB_PATH) as db:merchant=await core.get_setting(db,"zarinpal_merchant_id","");sandbox=(await core.get_setting(db,"zarinpal_sandbox","0")) in ("1","true","yes")
            due_toman=int(round(due*rate));req_url,_,start=_zarinpal_endpoints(sandbox);base=os.getenv("PUBLIC_BASE_URL",str(request.base_url).rstrip("/"))
            async with httpx.AsyncClient(timeout=25) as client:
                resp=await client.post(req_url,json={"merchant_id":merchant,"amount":due_toman*10,"currency":"IRR","callback_url":base+"/shop/payment/callback","description":f"خرید سرور سفارش {oid}"});resp.raise_for_status();data=resp.json()
            d=data.get("data") or {};authority=d.get("authority")
            if int(d.get("code",0))!=100 or not authority:raise RuntimeError(str(data.get("errors") or data)[:250])
            async with aiosqlite.connect(DB_PATH) as db:await db.execute("UPDATE web_server_orders SET authority=?,gateway_track_id=? WHERE id=?",(authority,authority,oid));await db.commit()
            return RedirectResponse(start+authority,status_code=303)
        async with aiosqlite.connect(DB_PATH) as db:oxakey=await core.get_setting(db,"oxapay_api_key","");nowkey=await core.get_setting(db,"nowpayments_api_key","");currency=await core.get_setting(db,"nowpayments_pay_currency",core.NOWPAYMENTS_PAY_CURRENCY)
        if gateway_method=="oxapay":
            track,url=await core.oxapay_create_invoice(oxakey,due,f"s{oid}",description=f"Server order {oid}")
            async with aiosqlite.connect(DB_PATH) as db:await db.execute("UPDATE web_server_orders SET gateway_track_id=? WHERE id=?",(str(track),oid));await db.commit()
            return RedirectResponse(url,status_code=303)
        if gateway_method=="nowpayments":
            payment_id,address,amount,curr=await core.nowpayments_create_payment(nowkey,due,f"s{oid}",currency,description=f"Server order {oid}")
            async with aiosqlite.connect(DB_PATH) as db:await db.execute("UPDATE web_server_orders SET gateway_track_id=? WHERE id=?",(str(payment_id),oid));await db.commit()
            return storefront_page("پرداخت ارزی",f'<div class="card"><h1>پرداخت سرور #{oid}</h1><p>{esc(amount)} {esc(curr)}</p><code style="direction:ltr;word-break:break-all">{esc(address)}</code><p>پس از تأیید شبکه، سرور به‌صورت خودکار ساخته می‌شود.</p></div>',customer)
        raise RuntimeError("درگاه نامعتبر")
    except Exception as exc:
        await core.refund_web_server_wallet(oid)
        return storefront_page("خطای پرداخت",f'<div class="card">ساخت فاکتور ناموفق بود؛ سهم کیف پول برگشت داده شد. {esc(str(exc)[:250])}</div>',customer)

@app.get("/shop/delivery/{token}",response_class=HTMLResponse)
async def shop_delivery(token:str):
    token_hash=hashlib.sha256(token.encode()).hexdigest()
    async with aiosqlite.connect(DB_PATH) as db:
        order=await (await db.execute("SELECT id,product_name,customer_name,quantity,ref_id FROM product_orders WHERE delivery_token_hash=? AND status='delivered'",(token_hash,))).fetchone()
        if not order:return storefront_page("لینک تحویل",'<div class="card">لینک تحویل نامعتبر است یا سفارش پیدا نشد.</div>')
        items=await (await db.execute("SELECT payload FROM product_inventory WHERE order_id=? ORDER BY id",(order[0],))).fetchall()
    content=''.join(f'<pre style="white-space:pre-wrap;direction:ltr;text-align:left;background:#06152c;padding:14px;border-radius:10px">{esc(item[0])}</pre>' for item in items)
    response=storefront_page("اطلاعات تحویل",f'<h1>اطلاعات محصول خریداری‌شده</h1><div class="card"><p>سفارش #{order[0]} | {esc(order[1])} | تعداد: {order[3]}</p><p>خریدار: {esc(order[2])}</p>{content}<p>این اطلاعات را در جای امن ذخیره کنید و لینک را در اختیار دیگران قرار ندهید.</p></div>')
    response.headers["Cache-Control"]="no-store, private"
    response.headers["Pragma"]="no-cache"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["X-Robots-Tag"]="noindex, nofollow, noarchive"
    return response
