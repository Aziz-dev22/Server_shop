import os
from flask import Flask, request, redirect, url_for, flash, render_template_string, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from werkzeug.security import check_password_hash
from database import db_session, Admin, User as DBUser, Server, Plan, HetznerAccount, WalletTransaction, backup_database
from config import Config

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class WebAdmin(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    admin = Admin.query.get(int(user_id))
    if admin:
        return WebAdmin(admin.id, admin.username)
    return None

# ==========================================
# قالب اصلی HTML با طراحی مدرن و شیشه‌ای
# ==========================================
BASE_TEMPLATE = """
<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>پنل مدیریت Server Shop</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet">
    <style>
        @font-face {
            font-family: 'Vazirmatn';
            src: url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.0.0/Vazirmatn-Regular.woff2') format('woff2');
        }
        body { 
            background: linear-gradient(135deg, #0f2027, #203a43, #2c5364); 
            color: #fff; 
            font-family: 'Vazirmatn', Tahoma, sans-serif; 
            min-height: 100vh;
            padding-bottom: 50px;
        }
        .navbar { background: rgba(0,0,0,0.4) !important; backdrop-filter: blur(10px); border-bottom: 1px solid rgba(255,255,255,0.1); }
        .glass-card { 
            background: rgba(255, 255, 255, 0.05); 
            backdrop-filter: blur(15px); 
            border-radius: 15px; 
            padding: 25px; 
            border: 1px solid rgba(255,255,255,0.1); 
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
            margin-bottom: 25px;
        }
        .table { color: #fff; vertical-align: middle; }
        .table-hover tbody tr:hover { color: #fff; background-color: rgba(255,255,255,0.1); }
        .table thead th { border-bottom: 2px solid rgba(255,255,255,0.2); }
        .table td, .table th { border-color: rgba(255,255,255,0.1); padding: 12px; }
        .form-control { background: rgba(0,0,0,0.2) !important; color: #fff !important; border: 1px solid rgba(255,255,255,0.2); }
        .form-control:focus { box-shadow: none; border-color: #0dcaf0; }
        .form-control::placeholder { color: #aaa; }
        .btn-custom { background: linear-gradient(45deg, #00b4db, #0083b0); border: none; color: white; }
        .btn-custom:hover { background: linear-gradient(45deg, #0083b0, #00b4db); color: white; }
    </style>
</head>
<body>
    {% if current_user.is_authenticated %}
    <nav class="navbar navbar-expand-lg navbar-dark mb-4">
      <div class="container">
        <a class="navbar-brand fw-bold text-info" href="/dashboard">🚀 Server Shop</a>
        <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
            <span class="navbar-toggler-icon"></span>
        </button>
        <div class="collapse navbar-collapse" id="navbarNav">
          <ul class="navbar-nav me-auto">
            <li class="nav-item"><a class="nav-link" href="/dashboard">داشبورد و پلن‌ها</a></li>
            <li class="nav-item"><a class="nav-link" href="/users">کاربران</a></li>
            <li class="nav-item"><a class="nav-link" href="/transactions">تراکنش‌ها</a></li>
            <li class="nav-item"><a class="nav-link" href="/backups">بکاپ‌گیری</a></li>
            <li class="nav-item"><a class="nav-link text-danger" href="/logout">خروج</a></li>
          </ul>
        </div>
      </div>
    </nav>
    {% endif %}

    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="alert alert-{{ category if category != 'message' else 'info' }} glass-card p-3 mb-4">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}
        
        {{ content|safe }}
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

def render_page(content):
    return render_template_string(BASE_TEMPLATE, content=content)

@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        admin = Admin.query.filter_by(username=username).first()
        if admin and check_password_hash(admin.password_hash, password):
            login_user(WebAdmin(admin.id, admin.username))
            return redirect(url_for('dashboard'))
        flash('نام کاربری یا رمز عبور اشتباه است.', 'danger')
    
    content = '''
        <div class="row justify-content-center mt-5">
            <div class="col-md-4">
                <div class="glass-card text-center">
                    <h2 class="mb-4 text-info fw-bold">ورود به مدیریت</h2>
                    <form method="post">
                        <div class="mb-3">
                            <input type="text" class="form-control" name="username" placeholder="نام کاربری" required>
                        </div>
                        <div class="mb-4">
                            <input type="password" class="form-control" name="password" placeholder="رمز عبور" required>
                        </div>
                        <button type="submit" class="btn btn-custom w-100 py-2">ورود به پنل</button>
                    </form>
                </div>
            </div>
        </div>
    '''
    return render_page(content)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    total_users = DBUser.query.count()
    total_servers = Server.query.count()
    plans = Plan.query.all()
    accounts = HetznerAccount.query.all()
    
    plan_rows = "".join([f"<tr><td>{p.name}</td><td>{p.server_type}</td><td>${p.base_price}</td><td class='text-success fw-bold'>${p.final_price}</td></tr>" for p in plans])
    acc_rows = "".join([f"<tr><td>{a.name}</td><td><code class='text-light'>{a.api_token[:10]}...</code></td><td>{'<span class="badge bg-success">فعال</span>' if a.is_active else '<span class="badge bg-danger">غیرفعال</span>'}</td></tr>" for a in accounts])
    
    content = f'''
        <div class="row mb-4">
            <div class="col-md-6">
                <div class="glass-card text-center">
                    <h4 class="text-info">سرورهای فعال</h4>
                    <h2 class="display-4 fw-bold">{total_servers}</h2>
                </div>
            </div>
            <div class="col-md-6">
                <div class="glass-card text-center">
                    <h4 class="text-info">کل کاربران ربات</h4>
                    <h2 class="display-4 fw-bold">{total_users}</h2>
                </div>
            </div>
        </div>

        <div class="glass-card">
            <h4 class="mb-4 text-info border-bottom pb-2">تنظیمات اکانت‌های هتزنر (API)</h4>
            <form method="post" action="/add-account" class="row g-3 mb-4">
                <div class="col-md-4">
                    <input type="text" class="form-control" name="name" placeholder="نام اکانت (مثلا Account 1)" required>
                </div>
                <div class="col-md-6">
                    <input type="text" class="form-control" name="api_token" placeholder="توکن API هتزنر" required>
                </div>
                <div class="col-md-2">
                    <button type="submit" class="btn btn-custom w-100">افزودن اکانت</button>
                </div>
            </form>
            <div class="table-responsive">
                <table class="table table-hover">
                    <thead><tr><th>نام اکانت</th><th>توکن (مخفی)</th><th>وضعیت</th></tr></thead>
                    <tbody>{acc_rows}</tbody>
                </table>
            </div>
        </div>

        <div class="glass-card">
            <h4 class="mb-4 text-info border-bottom pb-2">مدیریت پلن‌های فروش</h4>
            <p class="text-muted small">سیستم به صورت خودکار 20% سود روی قیمت پایه شما لحاظ می‌کند.</p>
            <form method="post" action="/add-plan" class="row g-3 mb-4">
                <div class="col-md-4">
                    <input type="text" class="form-control" name="name" placeholder="نام پلن (مثلا پلن اقتصادی)" required>
                </div>
                <div class="col-md-3">
                    <input type="text" class="form-control" name="type" placeholder="نوع (مثلا cx22)" required>
                </div>
                <div class="col-md-3">
                    <input type="number" step="0.01" class="form-control" name="base_price" placeholder="قیمت خرید شما ($)" required>
                </div>
                <div class="col-md-2">
                    <button type="submit" class="btn btn-custom w-100">افزودن پلن</button>
                </div>
            </form>
            <div class="table-responsive">
                <table class="table table-hover">
                    <thead><tr><th>نام پلن</th><th>نوع سرور</th><th>قیمت پایه</th><th>قیمت فروش (+20%)</th></tr></thead>
                    <tbody>{plan_rows}</tbody>
                </table>
            </div>
        </div>
    '''
    return render_page(content)

@app.route('/users')
@login_required
def users():
    all_users = DBUser.query.order_by(DBUser.created_at.desc()).all()
    user_rows = ""
    for u in all_users:
        balance = u.wallet.balance if u.wallet else 0.0
        server_count = len(u.servers)
        user_rows += f"<tr><td>{u.id}</td><td>@{u.username or '---'}</td><td class='text-warning fw-bold'>${balance:.2f}</td><td><span class='badge bg-primary'>{server_count} سرور</span></td><td>{u.created_at.strftime('%Y-%m-%d')}</td></tr>"
    
    content = f'''
        <div class="glass-card">
            <h4 class="mb-4 text-info border-bottom pb-2">مدیریت کاربران</h4>
            <div class="table-responsive">
                <table class="table table-hover text-center">
                    <thead><tr><th>آیدی تلگرام</th><th>یوزرنیم</th><th>موجودی کیف پول</th><th>تعداد سرور</th><th>تاریخ عضویت</th></tr></thead>
                    <tbody>{user_rows}</tbody>
                </table>
            </div>
        </div>
    '''
    return render_page(content)

@app.route('/transactions')
@login_required
def transactions():
    txs = WalletTransaction.query.order_by(WalletTransaction.created_at.desc()).limit(50).all()
    tx_rows = ""
    for tx in txs:
        color = "text-success" if tx.amount > 0 else "text-danger"
        tx_type_fa = "افزایش موجودی" if tx.tx_type == "deposit" else "خرید" if tx.tx_type == "purchase" else "تمدید" if tx.tx_type == "renewal" else tx.tx_type
        user_id = tx.wallet.user_id if tx.wallet else "N/A"
        tx_rows += f"<tr><td>{user_id}</td><td><span class='badge bg-secondary'>{tx_type_fa}</span></td><td class='{color} fw-bold' dir='ltr'>${tx.amount:.2f}</td><td>{tx.description}</td><td dir='ltr'>{tx.created_at.strftime('%Y-%m-%d %H:%M')}</td></tr>"
    
    content = f'''
        <div class="glass-card">
            <h4 class="mb-4 text-info border-bottom pb-2">آخرین تراکنش‌ها (50 رکورد اخیر)</h4>
            <div class="table-responsive">
                <table class="table table-hover text-center">
                    <thead><tr><th>آیدی کاربر</th><th>نوع</th><th>مبلغ</th><th>توضیحات</th><th>تاریخ</th></tr></thead>
                    <tbody>{tx_rows}</tbody>
                </table>
            </div>
        </div>
    '''
    return render_page(content)

@app.route('/backups')
@login_required
def backups():
    import glob
    backup_dir = Config.BACKUP_DIR
    files = []
    if os.path.exists(backup_dir):
        files = sorted(glob.glob(os.path.join(backup_dir, "*.db")), reverse=True)
    
    file_rows = ""
    for f in files:
        fname = os.path.basename(f)
        size = round(os.path.getsize(f) / 1024, 2)
        file_rows += f"<tr><td dir='ltr'>{fname}</td><td>{size} KB</td><td><a href='/download_backup/{fname}' class='btn btn-sm btn-success'>دانلود</a></td></tr>"
        
    content = f'''
        <div class="glass-card">
            <div class="d-flex justify-content-between align-items-center mb-4 border-bottom pb-2">
                <h4 class="text-info m-0">مدیریت بکاپ دیتابیس</h4>
                <a href="/create_backup" class="btn btn-primary">➕ ساخت بکاپ جدید همین الان</a>
            </div>
            <div class="table-responsive">
                <table class="table table-hover text-center">
                    <thead><tr><th>نام فایل</th><th>حجم</th><th>عملیات</th></tr></thead>
                    <tbody>{file_rows if file_rows else '<tr><td colspan="3">هیچ بکاپی یافت نشد.</td></tr>'}</tbody>
                </table>
            </div>
        </div>
    '''
    return render_page(content)

@app.route('/create_backup')
@login_required
def create_backup():
    if backup_database():
        flash("بکاپ با موفقیت ایجاد شد.", "success")
    else:
        flash("خطا در ایجاد بکاپ.", "danger")
    return redirect(url_for('backups'))

@app.route('/download_backup/<filename>')
@login_required
def download_backup(filename):
    filepath = os.path.join(Config.BACKUP_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    flash("فایل یافت نشد.", "danger")
    return redirect(url_for('backups'))

@app.route('/add-account', methods=['POST'])
@login_required
def add_account():
    name = request.form.get('name')
    token = request.form.get('api_token')
    new_acc = HetznerAccount(name=name, api_token=token)
    db_session.add(new_acc)
    db_session.commit()
    flash('اکانت API با موفقیت اضافه شد.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/add-plan', methods=['POST'])
@login_required
def add_plan():
    name = request.form.get('name')
    srv_type = request.form.get('type')
    base_price = float(request.form.get('base_price'))
    final_price = round(base_price * 1.20, 2)
    new_plan = Plan(name=name, server_type=srv_type, base_price=base_price, final_price=final_price)
    db_session.add(new_plan)
    db_session.commit()
    flash('پلن جدید ایجاد شد.', 'success')
    return redirect(url_for('dashboard'))

@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()
