from flask import Flask, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from werkzeug.security import check_password_hash
from database import db_session, Admin, User as DBUser, Server, Plan, HetznerAccount
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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        admin = Admin.query.filter_by(username=username).first()
        if admin and check_password_hash(admin.password_hash, password):
            login_user(WebAdmin(admin.id, admin.username))
            return redirect(url_for('dashboard'))
        flash('Invalid credentials')
    return '''
        <form method="post" style="max-width:300px; margin:100px auto; padding:20px; border:1px solid #ccc; font-family:sans-serif;">
            <h2>Server Shop Admin</h2>
            <input type="text" name="username" placeholder="Username" required style="width:100%; margin-bottom:10px; padding:5px;"><br>
            <input type="password" name="password" placeholder="Password" required style="width:100%; margin-bottom:10px; padding:5px;"><br>
            <button type="submit" style="width:100%; padding:8px; background:#007bff; color:white; border:none;">Login</button>
        </form>
    '''

@app.route('/dashboard')
@login_required
def dashboard():
    total_users = DBUser.query.count()
    total_servers = Server.query.count()
    plans = Plan.query.all()
    accounts = HetznerAccount.query.all()
    
    plan_rows = "".join([f"<tr><td>{p.name}</td><td>{p.server_type}</td><td>${p.base_price}</td><td><b>${p.final_price}</b></td></tr>" for p in plans])
    acc_rows = "".join([f"<tr><td>{a.name}</td><td>{a.api_token[:10]}...</td><td>{'Active' if a.is_active else 'Inactive'}</td></tr>" for a in accounts])
    
    return f'''
        <div style="font-family:sans-serif; padding:20px;">
            <h1>Server Shop - Admin Dashboard</h1>
            <p>Active Servers: {total_servers} | Registered Users: {total_users}</p>
            <hr>
            
            <h3>Manage Hetzner API Accounts</h3>
            <form method="post" action="/add-account" style="margin-bottom:15px;">
                <input type="text" name="name" placeholder="Account Name (e.g. Acc1)" required>
                <input type="text" name="api_token" placeholder="Hetzner API Token" required>
                <button type="submit">Add Account</button>
            </form>
            <table border="1" cellpadding="5" style="border-collapse:collapse; width:100%; max-width:600px;">
                <tr style="background:#f4f4f4;"><th>Account Name</th><th>Token (Masked)</th><th>Status</th></tr>
                {acc_rows}
            </table>
            <hr>

            <h3>Add Hosting Plan</h3>
            <form method="post" action="/add-plan" style="margin-bottom:15px;">
                <input type="text" name="name" placeholder="Plan Name" required>
                <input type="text" name="type" placeholder="Type (e.g. cx22)" required>
                <input type="number" step="0.01" name="base_price" placeholder="Base Price ($)" required>
                <button type="submit">Add Plan (+20% Profit Auto)</button>
            </form>
            <table border="1" cellpadding="5" style="border-collapse:collapse; width:100%; max-width:600px;">
                <tr style="background:#f4f4f4;"><th>Name</th><th>Type</th><th>Base Price</th><th>Retail Price</th></tr>
                {plan_rows}
            </table>
        </div>
    '''

@app.route('/add-account', methods=['POST'])
@login_required
def add_account():
    name = request.form.get('name')
    token = request.form.get('api_token')
    new_acc = HetznerAccount(name=name, api_token=token)
    db_session.add(new_acc)
    db_session.commit()
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
    return redirect(url_for('dashboard'))

@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()

