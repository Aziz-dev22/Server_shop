from apscheduler.schedulers.background import BackgroundScheduler
from database import db_session, Server, HetznerAccount
from hetzner_client import HetznerClient
from datetime import datetime, timezone
from config import logger
from bot import bot

def check_traffic_usage():
    logger.info("Scheduler task: Checking Traffic Usage.")
    servers = Server.query.filter(Server.status.in_(["running"])).all()
    
    for srv in servers:
        account = HetznerAccount.query.get(srv.hetzner_account_id)
        if not account: continue
        
        client = HetznerClient(api_token=account.api_token)
        used_gb = client.get_server_metrics(srv.id)
        srv.traffic_used_gb = used_gb
        
        usage_ratio = used_gb / srv.traffic_limit_gb if srv.traffic_limit_gb > 0 else 0
        
        if usage_ratio >= 1.0:
            client.action_server(srv.id, "poweroff")
            srv.status = "off"
            db_session.commit()
            try: bot.send_message(srv.user_id, f"🛑 **اتمام ترافیک:** سرور `{srv.ip_address}` ۱۰۰٪ از ترافیک مجاز خود را مصرف کرده و به‌صورت خودکار خاموش شد.", parse_mode="Markdown")
            except Exception: pass
            
        elif usage_ratio >= 0.95 and not srv.traffic_warning_sent:
            srv.traffic_warning_sent = True
            db_session.commit()
            try: bot.send_message(srv.user_id, f"⚠️ **اخطار ترافیک:** سرور `{srv.ip_address}` بیش از ۹۵٪ ترافیک مجاز خود را مصرف کرده است. در صورت اتمام کامل حجم، سرور خاموش خواهد شد.", parse_mode="Markdown")
            except Exception: pass
            
        elif usage_ratio < 0.95 and srv.traffic_warning_sent:
            srv.traffic_warning_sent = False
            db_session.commit()
        else:
            db_session.commit()

def check_expirations():
    logger.info("Scheduler task: Checking Expirations & Grace Periods.")
    now = datetime.now(timezone.utc)
    servers = Server.query.all()
    
    for srv in servers:
        time_diff = srv.expires_at.replace(tzinfo=timezone.utc) - now
        hours_left = time_diff.total_seconds() / 3600
        
        account = HetznerAccount.query.get(srv.hetzner_account_id)
        if not account: continue
        client = HetznerClient(api_token=account.api_token)

        if hours_left <= -24:
            client.delete_server(srv.id)
            db_session.delete(srv)
            db_session.commit()
            try: bot.send_message(srv.user_id, f"🛑 **حذف نهایی:** مهلت ۲۴ ساعته سرور `{srv.ip_address}` به پایان رسید و سرور برای همیشه پاک شد.", parse_mode="Markdown")
            except Exception: pass
            
        elif hours_left <= 0:
            hours_passed = abs(hours_left)
            if srv.status != "expired":
                client.action_server(srv.id, "poweroff")
                srv.status = "expired"
                srv.grace_notices = 1
                db_session.commit()
                try: bot.send_message(srv.user_id, f"⚠️ **سرور منقضی و خاموش شد!**\nسرور `{srv.ip_address}` منقضی شد. تنها ۲۴ ساعت برای تمدید آن از طریق منوی مدیریت فرصت دارید.", parse_mode="Markdown")
                except Exception: pass
            else:
                expected_notices = int(hours_passed // 6) + 1
                if srv.grace_notices < expected_notices and expected_notices <= 4:
                    srv.grace_notices = expected_notices
                    db_session.commit()
                    hours_remaining = int(24 - hours_passed)
                    try: bot.send_message(srv.user_id, f"⏰ **یادآوری تمدید:** تنها {hours_remaining} ساعت تا حذف دائمی سرور `{srv.ip_address}` زمان باقیست.", parse_mode="Markdown")
                    except Exception: pass

        elif 0 < hours_left <= 48:
            if int(hours_left) in [48, 24]:
                try: bot.send_message(srv.user_id, f"📅 **هشدار انقضا:** سرور `{srv.ip_address}` تا {int(hours_left)} ساعت دیگر منقضی می‌شود. جهت جلوگیری از قطعی، تمدید کنید.", parse_mode="Markdown")
                except Exception: pass

def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_traffic_usage, 'interval', minutes=5)
    scheduler.add_job(check_expirations, 'interval', hours=1)
    scheduler.start()

