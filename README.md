
```markdown
# Server Shop 🚀

[🇬🇧 English](#english) | [🇮🇷 فارسی](#فارسی)

---

<a id="english"></a>
## 🇬🇧 English

A complete, production-ready Telegram Bot and Web Panel for automated Hetzner Cloud server sales and management. 

### Features
- **Telegram Bot:** Buy servers, Power On/Off, Reboot, Reset Password, Wallet system.
- **Web Admin Panel:** Manage Hetzner API tokens, define plans (auto +20% profit), and view statistics.
- **Multi-Account:** Distribute servers across multiple Hetzner accounts automatically.
- **Smart Scheduler:** 
  - Auto-shuts down servers at 100% traffic (sends warning at 95%).
  - Grace period support (24h) with 6-hour interval renewal reminders.
  - Auto-terminates servers if not renewed after the grace period.

### Installation
Run the following command in your Linux terminal (Ubuntu/Debian):

```bash
git clone [https://github.com/Aziz-dev22/server_shop.git](https://github.com/Aziz-dev22/server_shop.git) && cd server_shop && chmod +x install.sh && ./install.sh

```
### How to Run
After successful installation, start the application (Bot + Web Panel + Scheduler) using:
```bash
source venv/bin/activate
python3 main.py

```
### Configuration
 1. Open the Web Panel using your server IP and the port you set during installation (e.g., http://YOUR_IP:5000).
 2. Login with the Admin credentials you created.
 3. Add your Hetzner Cloud API tokens from the dashboard.
 4. Add your server plans to start selling.
<a id="فارسی"></a>
## 🇮🇷 فارسی
یک ربات تلگرامی و پنل مدیریت تحت وب کامل و آماده‌ی استفاده برای فروش و مدیریت خودکار سرورهای ابری هتزنر (Hetzner).
### ویژگی‌ها
 * **ربات تلگرام:** خرید سرور، روشن/خاموش کردن، ریستارت، تغییر رمز عبور (روت) و سیستم کیف پول.
 * **پنل مدیریت تحت وب:** مدیریت توکن‌های API هتزنر، تعریف پلن‌های فروش (با محاسبه خودکار ۲۰٪ سود) و مشاهده آمار کلی.
 * **پشتیبانی از چند اکانت:** ساخت و توزیع خودکار سرورها بین چندین اکانت مختلف هتزنر برای دور زدن محدودیت‌ها.
 * **زمان‌بندی هوشمند (Scheduler):**
   * خاموش کردن خودکار سرور در صورت اتمام ترافیک (ارسال پیام اخطار به کاربر در صورت مصرف ۹۵٪ حجم).
   * پشتیبانی از مهلت تمدید ۲۴ ساعته (Grace Period) در زمان انقضا، همراه با ارسال پیام یادآوری هر ۶ ساعت.
   * حذف دائمی سرور از هتزنر در صورت عدم تمدید پس از پایان مهلت ۲۴ ساعته.
### نصب
کد تک‌خطی زیر را کپی کرده و در ترمینال سرور لینوکسی خود (اوبونتو/دبیان) اجرا کنید:
```bash
git clone https://github.com/Aziz-dev22/server_shop.git && cd server_shop && chmod +x install.sh && ./install.sh

```
### نحوه اجرا
پس از پایان موفقیت‌آمیز نصب، برای اجرای همزمان سیستم (ربات + پنل وب + زمان‌بند) دستورات زیر را وارد کنید:
```bash
source venv/bin/activate
python3 main.py

```
### پیکربندی و راه‌اندازی
۱. با استفاده از آی‌پی سرور و پورتی که هنگام نصب وارد کردید، پنل وب را باز کنید (مثال: http://YOUR_IP:5000).
۲. با نام کاربری و رمز عبوری که در مراحل نصب تعیین کردید وارد شوید.
۳. از قسمت داشبورد، توکن‌های API اکانت‌های هتزنر خود را اضافه کنید.
۴. پلن‌های سرور خود را تعریف کنید تا فروش از طریق ربات آغاز شود.
```

```
