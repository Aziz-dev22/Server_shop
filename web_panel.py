from flask import Flask, request, redirect, url_for, flash, render_template_string, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from database import db_session, Admin, User as DBUser, Server, Plan, HetznerAccount, WalletTransaction, Setting, restore_backup, backup_database
from config import Config
import os

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

@app.route('/')
@login_required
def index():
    return redirect(url_for('dashboard'))

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    if request.method == 'POST':
        # آپدیت تنظیمات (قوانین و پشتیبانی)
        for key in ['rules', 'support_msg']:
            val = request.form.get(key)
            s = Setting.query.filter_by(key=key).first()
            if s: s.value = val
            else: db_session.add(Setting(key=key, value=val))
        db_session.commit()
        flash('تنظیمات ذخیره شد', 'success')
    
    plans = Plan.query.all()
    accounts = HetznerAccount.query.all()
    return render_template_string("""
        <h1>داشبورد مدیریت</h1>
        <h3>پلن‌ها</h3>
        <table border="1">{% for p in plans %}<tr><td>{{p.name}}</td><td>${{p.final_price}}</td></tr>{% endfor %}</table>
        <h3>تنظیمات ربات</h3>
        <form method="post">
            <textarea name="rules" placeholder="قوانین">{{Setting.query.filter_by(key='rules').first().value if Setting.query.filter_by(key='rules').first() else ''}}</textarea>
            <button type="submit">ذخیره</button>
        </form>
    """, plans=plans, accounts=accounts)

@app.route('/users', methods=['GET', 'POST'])
@login_required
def users():
    if request.method == 'POST':
        u_id = request.form.get('user_id')
        amt = float(request.form.get('amount'))
        user = DBUser.query.get(u_id)
        if user:
            user.wallet.balance += amt
            db_session.commit()
            flash('موجودی تغییر کرد', 'success')
    all_users = DBUser.query.all()
    return render_template_string("<h1>مدیریت کاربران</h1>{% for u in all_users %}<li>{{u.username}} - موجودی: {{u.wallet.balance}}</li>{% endfor %}", all_users=all_users)

@app.route('/backups')
@login_required
def backups():
    # مدیریت بکاپ‌ها
    return "<h1>مدیریت بکاپ‌ها</h1>"

if __name__ == '__main__':
    app.run(port=Config.PANEL_PORT)
