from flask import Flask, request, redirect, url_for, flash, render_template_string
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from werkzeug.security import check_password_hash
from database import db_session, Admin, User as DBUser, Server, Plan, WalletTransaction, Setting
from config import Config

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
login_manager = LoginManager(app)
login_manager.login_view = "login"

class WebAdmin(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    admin = Admin.query.get(int(user_id))
    return WebAdmin(admin.id, admin.username) if admin else None

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        admin = Admin.query.filter_by(username=request.form.get('username')).first()
        if admin and check_password_hash(admin.password_hash, request.form.get('password')):
            login_user(WebAdmin(admin.id, admin.username))
            return redirect(url_for('dashboard'))
        flash('نام کاربری یا رمز عبور اشتباه است.', 'danger')
    return '''<form method="post">نام کاربری: <input name="username"><br>پسورد: <input type="password" name="password"><br><button type="submit">ورود</button></form>'''

@app.route('/dashboard')
@login_required
def dashboard():
    return "<h1>پنل مدیریت</h1><ul><li><a href='/users'>مدیریت کاربران</a></li><li><a href='/backups'>مدیریت بکاپ</a></li></ul>"

@app.route('/users')
@login_required
def users():
    users_list = DBUser.query.all()
    rows = "".join([f"<tr><td>{u.id}</td><td>{u.username}</td><td>{u.wallet.balance}</td><td><a href='/user_details/{u.id}'>مدیریت</a></td></tr>" for u in users_list])
    return f"<table border='1'><tr><th>ID</th><th>Username</th><th>موجودی</th><th>عملیات</th></tr>{rows}</table>"

@app.route('/user_details/<int:user_id>', methods=['GET', 'POST'])
@login_required
def user_details(user_id):
    user = DBUser.query.get(user_id)
    if request.method == 'POST':
        amount = float(request.form.get('amount'))
        user.wallet.balance += amount
        db_session.add(WalletTransaction(wallet_id=user.wallet.id, amount=amount, description="تراکنش دستی ادمین"))
        db_session.commit()
        flash('موجودی تغییر کرد', 'success')
    
    servers = "".join([f"<li>{s.name} - {s.ip_address} <form method='post' action='/server_action/{s.id}'><button name='act' value='poweroff'>خاموش</button></form></li>" for s in user.servers])
    return f"<h1>مدیریت {user.username}</h1><p>موجودی: {user.wallet.balance}</p><form method='post'>مقدار (مثبت/منفی): <input name='amount' type='number' step='0.01'><button type='submit'>اعمال</button></form><ul>{servers}</ul>"

@app.route('/server_action/<int:server_id>', methods=['POST'])
@login_required
def server_action(server_id):
    # اینجا اکشن‌های هتزنر (تغییر آی‌پی، خاموش/روشن و...) را از طریق hetzner_client صدا می‌زنیم
    flash('دستور ارسال شد', 'info')
    return redirect(request.referrer)

if __name__ == '__main__':
    app.run(port=Config.PANEL_PORT)

