import os
import bcrypt
import stripe
import smtplib
import hmac
import hashlib
import base64
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

load_dotenv()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

ENV = os.getenv("FLASK_ENV", "development")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
IS_PRODUCTION = ENV == "production"

app.config["ENV"] = ENV
app.config["DEBUG"] = DEBUG
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-12345")
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 30  # 30 days

# Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_fake")
STRIPE_PRICE_STARTER = os.getenv("STRIPE_PRICE_ID_STARTER") or os.getenv("STRIPE_PRICE_ID")
STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_ID_PRO") or os.getenv("STRIPE_PRICE_ID")

# OpenAI
try:
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        client = OpenAI(api_key=api_key)
    else:
        client = None
        print("Warning: OPENAI_API_KEY not set.")
except Exception as e:
    client = None
    print(f"Warning: OpenAI initialization failed: {e}")

# Twilio SMS
try:
    from twilio.rest import Client as TwilioClient
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_phone = os.getenv("TWILIO_PHONE_NUMBER")
    if twilio_sid and twilio_token:
        twilio_client = TwilioClient(twilio_sid, twilio_token)
    else:
        twilio_client = None
except Exception as e:
    twilio_client = None
    twilio_phone = None
    print(f"Warning: Twilio not configured: {e}")

# Plan limits
PLAN_LIMITS = {
    "free":    {"customers": 50,  "campaigns_month": 10},
    "starter": {"customers": 500, "campaigns_month": None},
    "pro":     {"customers": None,"campaigns_month": None},
}

CAMPAIGN_TYPES = {
    "come_back":   "Come Back Offer",
    "weekend":     "Weekend Special",
    "lunch":       "Lunch Deal",
    "dinner":      "Dinner Special",
    "birthday":    "Birthday Special",
    "loyalty":     "Loyalty Reward",
    "happy_hour":  "Happy Hour",
    "new_item":    "New Item Launch",
    "promotion":   "General Promotion",
    "invitation":  "Business Invitation",
    "custom":      "Custom Message",
}

# Database
db_url = os.getenv("DATABASE_URL", "sqlite:///restaurant.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_size": 10,
    "pool_recycle": 3600,
    "pool_pre_ping": True,
}

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ============================================
# MODELS
# ============================================

class Business(db.Model):
    __tablename__ = "businesses"
    id = db.Column(db.Integer, primary_key=True)
    business_name = db.Column(db.String(200), nullable=False)
    owner_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    plan = db.Column(db.String(50), default="free")
    stripe_customer_id = db.Column(db.String(200))
    address = db.Column(db.String(300))
    phone = db.Column(db.String(50))
    website = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Customer(db.Model):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False)
    first_name = db.Column(db.String(120), nullable=False)
    last_name = db.Column(db.String(120))
    email = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(50))
    dob = db.Column(db.String(50))
    unsubscribed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Campaign(db.Model):
    __tablename__ = "campaigns"
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False)
    customer_name = db.Column(db.String(200), nullable=False)
    customer_email = db.Column(db.String(200), nullable=True)
    customer_phone = db.Column(db.String(50))
    campaign_type = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default="draft")
    scheduled_at = db.Column(db.DateTime, nullable=True)
    open_count = db.Column(db.Integer, default=0)
    opened_at = db.Column(db.DateTime, nullable=True)
    click_count = db.Column(db.Integer, default=0)
    clicked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ContactMessage(db.Model):
    __tablename__ = "contact_messages"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Promotion(db.Model):
    __tablename__ = "promotions"
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.String(50))
    category = db.Column(db.String(50), default="promotion")
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AutomationRule(db.Model):
    __tablename__ = "automation_rules"
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False)
    rule_type = db.Column(db.String(50), nullable=False)  # birthday, reengagement, welcome
    active = db.Column(db.Boolean, default=False)
    message_template = db.Column(db.Text)
    days_threshold = db.Column(db.Integer, default=30)  # days before birthday or days since contact
    last_run = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class CampaignTypeModel(db.Model):
    __tablename__ = "campaign_type_options"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    label = db.Column(db.String(100), nullable=False)
    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()
    # Safely add columns that may be missing from existing PostgreSQL databases
    from sqlalchemy import text
    migrations = [
        "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS address VARCHAR(300)",
        "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS phone VARCHAR(50)",
        "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS website VARCHAR(200)",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS unsubscribed BOOLEAN DEFAULT FALSE",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMP",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS open_count INTEGER DEFAULT 0",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS opened_at TIMESTAMP",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS click_count INTEGER DEFAULT 0",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS clicked_at TIMESTAMP",
        "ALTER TABLE customers ALTER COLUMN email DROP NOT NULL",
        "ALTER TABLE campaigns ALTER COLUMN customer_email DROP NOT NULL",
    ]
    for sql in migrations:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(sql))
        except Exception as e:
            print(f"Migration skipped: {e}")
    # Seed default campaign types if table is empty
    if CampaignTypeModel.query.count() == 0:
        defaults = [
            ("come_back",     "Come Back Offer",       0),
            ("weekend",       "Weekend Special",        1),
            ("lunch",         "Lunch Deal",             2),
            ("dinner",        "Dinner Special",         3),
            ("birthday",      "Birthday Special",       4),
            ("loyalty",       "Loyalty Reward",         5),
            ("happy_hour",    "Happy Hour",             6),
            ("new_item",      "New Item Launch",        7),
            ("promotion",     "General Promotion",      8),
            ("invitation",    "Business Invitation",    9),
            ("custom",        "Custom Message",         10),
            ("hotel_stay",    "Hotel Stay Offer",       11),
            ("hotel_upgrade", "Room Upgrade Deal",      12),
            ("hotel_event",   "Event / Package Deal",   13),
            ("hotel_loyalty", "Guest Loyalty Reward",   14),
        ]
        for key, label, order in defaults:
            db.session.add(CampaignTypeModel(key=key, label=label, sort_order=order))
        db.session.commit()
    else:
        # Ensure newer campaign types exist on existing deployments
        new_types = [
            ("invitation",    "Business Invitation",    9),
            ("hotel_stay",    "Hotel Stay Offer",       11),
            ("hotel_upgrade", "Room Upgrade Deal",      12),
            ("hotel_event",   "Event / Package Deal",   13),
            ("hotel_loyalty", "Guest Loyalty Reward",   14),
        ]
        changed = False
        for key, label, order in new_types:
            if not CampaignTypeModel.query.filter_by(key=key).first():
                db.session.add(CampaignTypeModel(key=key, label=label, sort_order=order))
                changed = True
        if changed:
            db.session.commit()

# ============================================
# HELPERS
# ============================================

def get_campaign_types():
    types = CampaignTypeModel.query.filter_by(active=True).order_by(CampaignTypeModel.sort_order, CampaignTypeModel.label).all()
    return {t.key: t.label for t in types}

def get_segment_customers(business_id, segment):
    """Return list of Customer objects matching the given segment."""
    now = datetime.utcnow()
    base = Customer.query.filter_by(business_id=business_id, unsubscribed=False)

    if segment == "new":
        cutoff = now - timedelta(days=30)
        return base.filter(Customer.created_at >= cutoff).all()

    elif segment == "inactive":
        cutoff = now - timedelta(days=60)
        # Customers who have never been contacted, or last campaign > 60 days ago
        all_customers = base.all()
        result = []
        for c in all_customers:
            last = Campaign.query.filter_by(
                business_id=business_id, customer_email=c.email
            ).order_by(Campaign.created_at.desc()).first()
            if not last or last.created_at < cutoff:
                result.append(c)
        return result

    elif segment == "birthday_month":
        month_str = now.strftime("-%m-")
        alt_month = f"-{now.month}-"  # handles single digit months without leading zero
        all_customers = base.all()
        return [c for c in all_customers if c.dob and (
            month_str in c.dob or alt_month in c.dob
        )]

    elif segment == "vip":
        all_customers = base.all()
        result = []
        for c in all_customers:
            count = Campaign.query.filter_by(
                business_id=business_id, customer_email=c.email
            ).count()
            if count >= 3:
                result.append(c)
        return result

    elif segment == "sms_only":
        return base.filter(Customer.phone.isnot(None), Customer.phone != "",
                           (Customer.email.is_(None)) | (Customer.email == "")).all()

    else:  # "all"
        return base.all()

def current_business():
    if "user_id" not in session:
        return None
    return Business.query.get(session["user_id"])

def get_plan_limit(plan, limit_type):
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]).get(limit_type)

def clean_ai_text(text):
    return (text or "").replace("###", "").replace("**", "").strip()

def get_unsubscribe_token(campaign_id):
    secret = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    sig = hmac.new(secret, str(campaign_id).encode(), hashlib.sha256).hexdigest()[:20]
    token = base64.urlsafe_b64encode(f"{campaign_id}:{sig}".encode()).decode()
    return token

def verify_unsubscribe_token(token):
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        campaign_id, sig = decoded.split(":", 1)
        secret = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
        expected = hmac.new(secret, campaign_id.encode(), hashlib.sha256).hexdigest()[:20]
        if hmac.compare_digest(sig, expected):
            return int(campaign_id)
    except Exception:
        pass
    return None

def process_scheduled_campaigns():
    """Send any campaigns whose scheduled_at time has passed."""
    try:
        now = datetime.utcnow()
        pending = Campaign.query.filter(
            Campaign.status == "scheduled",
            Campaign.scheduled_at <= now
        ).all()
        for campaign in pending:
            b = Business.query.get(campaign.business_id)
            if not b:
                continue
            token = get_unsubscribe_token(campaign.id)
            unsub_url = url_for("unsubscribe", token=token, _external=True)
            pixel_url = url_for("track_open", campaign_id=campaign.id, _external=True)
            click_url = url_for("track_click", campaign_id=campaign.id, _external=True)
            subject = f"Special Offer from {b.business_name}"
            success = send_email(
                campaign.customer_email, subject, campaign.message,
                customer_name=campaign.customer_name,
                business_name=b.business_name,
                campaign_type=campaign.campaign_type,
                unsubscribe_url=unsub_url,
                business_address=b.address or "",
                business_phone=b.phone or "",
                business_website=b.website or "",
                tracking_pixel_url=pixel_url,
                click_tracking_url=click_url,
                business_reply_email=b.email
            )
            campaign.status = "sent" if success else "failed"
        if pending:
            db.session.commit()
    except Exception as e:
        print(f"Scheduler error: {e}")

def _send_auto_campaign(business, customer, campaign_type, message):
    """Helper: create + send a campaign for an automation rule."""
    if not customer.email and not customer.phone:
        return
    campaign = Campaign(
        business_id=business.id,
        customer_name=f"{customer.first_name} {customer.last_name or ''}".strip(),
        customer_email=customer.email or "",
        customer_phone=customer.phone or "",
        campaign_type=campaign_type,
        message=message,
        status="draft",
    )
    db.session.add(campaign)
    db.session.flush()
    if customer.email:
        token = get_unsubscribe_token(campaign.id)
        unsub_url = url_for("unsubscribe", token=token, _external=True)
        pixel_url = url_for("track_open", campaign_id=campaign.id, _external=True)
        click_url = url_for("track_click", campaign_id=campaign.id, _external=True)
        success = send_email(
            customer.email, f"A message from {business.business_name}", message,
            customer_name=campaign.customer_name,
            business_name=business.business_name,
            campaign_type=campaign_type,
            unsubscribe_url=unsub_url,
            business_address=business.address or "",
            business_phone=business.phone or "",
            business_website=business.website or "",
            tracking_pixel_url=pixel_url,
            click_tracking_url=click_url,
            business_reply_email=business.email,
        )
        campaign.status = "sent" if success else "failed"
    elif customer.phone:
        success = send_sms(customer.phone, message)
        campaign.status = "sent" if success else "failed"

def run_automations(business_id):
    """Run all active automation rules for a business."""
    try:
        today = datetime.utcnow()
        rules = AutomationRule.query.filter_by(business_id=business_id, active=True).all()
        b = Business.query.get(business_id)
        if not b:
            return
        for rule in rules:
            # Only run each rule once per day
            if rule.last_run and (today - rule.last_run).total_seconds() < 82800:
                continue

            if rule.rule_type == "birthday":
                customers = Customer.query.filter_by(business_id=business_id).filter(
                    Customer.dob != None, Customer.dob != "", Customer.unsubscribed == False
                ).all()
                for c in customers:
                    try:
                        dob = datetime.strptime(c.dob, "%Y-%m-%d")
                        bday = dob.replace(year=today.year)
                        if bday < today.replace(hour=0, minute=0, second=0):
                            bday = bday.replace(year=today.year + 1)
                        days_until = (bday - today).days
                        if days_until != rule.days_threshold:
                            continue
                        # Check not already sent this year
                        already_sent = Campaign.query.filter_by(
                            business_id=business_id,
                            customer_email=c.email or "",
                            campaign_type="birthday",
                        ).filter(Campaign.created_at >= today.replace(month=1, day=1, hour=0, minute=0, second=0)).first()
                        if already_sent:
                            continue
                        msg = rule.message_template or generate_ai_message(c.first_name, b.business_name, "birthday")
                        msg = msg.replace("{name}", c.first_name).replace("{business}", b.business_name)
                        _send_auto_campaign(b, c, "birthday", msg)
                    except Exception:
                        pass

            elif rule.rule_type == "reengagement":
                cutoff = datetime(today.year, today.month, today.day) - timedelta(days=rule.days_threshold)
                customers = Customer.query.filter_by(business_id=business_id, unsubscribed=False).all()
                for c in customers:
                    last_campaign = Campaign.query.filter_by(
                        business_id=business_id,
                        customer_email=c.email or "",
                    ).filter(Campaign.status == "sent").order_by(Campaign.created_at.desc()).first()
                    if last_campaign and last_campaign.created_at > cutoff:
                        continue
                    if not last_campaign and c.created_at > cutoff:
                        continue
                    msg = rule.message_template or generate_ai_message(c.first_name, b.business_name, "come_back")
                    msg = msg.replace("{name}", c.first_name).replace("{business}", b.business_name)
                    _send_auto_campaign(b, c, "come_back", msg)

            elif rule.rule_type == "welcome":
                # Send welcome to customers added in the last 24h who haven't received one
                cutoff_welcome = datetime(today.year, today.month, today.day, today.hour, today.minute) - timedelta(hours=24)
                new_customers = Customer.query.filter_by(business_id=business_id, unsubscribed=False).filter(
                    Customer.created_at >= cutoff_welcome
                ).all()
                for c in new_customers:
                    already = Campaign.query.filter_by(
                        business_id=business_id,
                        customer_email=c.email or "",
                        campaign_type="loyalty",
                    ).filter(Campaign.created_at >= c.created_at).first()
                    if already:
                        continue
                    msg = rule.message_template or f"Welcome to {b.business_name}, {c.first_name}! 🎉 We're so glad to have you. Look forward to exclusive offers just for you."
                    msg = msg.replace("{name}", c.first_name).replace("{business}", b.business_name)
                    _send_auto_campaign(b, c, "loyalty", msg)

            rule.last_run = today
        db.session.commit()
    except Exception as e:
        print(f"Automation error: {e}")

def build_html_email(business_name, customer_name, message, campaign_type, unsubscribe_url="", business_address="", business_phone="", business_website="", tracking_pixel_url="", click_tracking_url=""):
    unsub_html = ""
    if unsubscribe_url:
        unsub_html = f' · <a href="{unsubscribe_url}" style="color:#aaa;font-size:11px;">Unsubscribe</a>'
    address_line = business_address if business_address else "8105 Rasor Blvd Suite 280 · Plano, TX 75024"
    contact_parts = [address_line]
    if business_phone:
        contact_parts.append(business_phone)
    if business_website:
        site = business_website if business_website.startswith("http") else f"https://{business_website}"
        contact_parts.append(f'<a href="{site}" style="color:#aaa;">{business_website}</a>')
    contact_line = " · ".join(contact_parts)
    cta_labels = {
        "come_back":  "Come Back Today!",
        "weekend":    "Visit Us This Weekend!",
        "lunch":      "Join Us for Lunch!",
        "dinner":     "Reserve Your Table!",
        "birthday":   "Claim Your Birthday Gift!",
        "loyalty":    "Claim Your Reward!",
        "happy_hour": "Join Happy Hour!",
        "new_item":   "Try It Today!",
        "promotion":  "Claim This Offer!",
    }
    cta_text = cta_labels.get(campaign_type, "Visit Us Today!")
    header_labels = {
        "come_back":  "We Miss You!",
        "weekend":    "Weekend Special",
        "lunch":      "Lunch Special",
        "dinner":     "Dinner Special",
        "birthday":   "Happy Birthday!",
        "loyalty":    "You're a VIP!",
        "happy_hour": "Happy Hour!",
        "new_item":   "Something New!",
        "promotion":  "Special Offer",
    }
    header_text = header_labels.get(campaign_type, "Special Offer Just For You")
    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;background:#f5f5f5;">
    <div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:30px;border-radius:12px;text-align:center;margin-bottom:20px;">
        <h1 style="color:white;margin:0;font-size:26px;">{business_name}</h1>
        <p style="color:rgba(255,255,255,0.9);margin:8px 0 0 0;">{header_text}</p>
    </div>
    <div style="background:white;padding:30px;border-radius:12px;margin-bottom:20px;">
        <p style="font-size:16px;color:#333;">Hi <strong>{customer_name}</strong>,</p>
        <p style="font-size:16px;color:#555;line-height:1.7;">{message}</p>
        <div style="text-align:center;margin-top:25px;">
            {'<a href="' + click_tracking_url + '" style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;padding:14px 30px;border-radius:8px;display:inline-block;font-size:16px;font-weight:bold;text-decoration:none;">' + cta_text + '</a>' if click_tracking_url else '<p style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;padding:14px 30px;border-radius:8px;display:inline-block;font-size:16px;font-weight:bold;">' + cta_text + '</p>'}
        </div>
    </div>
    <p style="text-align:center;color:#999;font-size:12px;">
        You received this because you're a valued customer of {business_name}.<br>
        {business_name} · {contact_line}{unsub_html}
    </p>
    {f'<img src="{tracking_pixel_url}" width="1" height="1" style="display:none;" alt="">' if tracking_pixel_url else ''}
    </body></html>
    """

def send_email(to_email, subject, body, customer_name="", business_name="", campaign_type="promotion", unsubscribe_url="", business_address="", business_phone="", business_website="", tracking_pixel_url="", click_tracking_url="", business_reply_email=""):
    from_name = business_name or "Revvio"
    html_body = build_html_email(from_name, customer_name or "Valued Customer", body, campaign_type, unsubscribe_url, business_address, business_phone, business_website, tracking_pixel_url, click_tracking_url)

    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    if sendgrid_key:
        # Option 2: SendGrid — sends from mail.revvio.ai with business name
        try:
            import sendgrid as sg_module
            from sendgrid.helpers.mail import Mail, Email, To, ReplyTo
            from_email = os.getenv("SENDGRID_FROM_EMAIL", "noreply@mail.revvio.ai")
            message = Mail(
                from_email=Email(from_email, from_name),
                to_emails=To(to_email),
                subject=subject,
                plain_text_content=body,
                html_content=html_body,
            )
            if business_reply_email:
                message.reply_to = ReplyTo(business_reply_email, from_name)
            if unsubscribe_url:
                message.header = {"List-Unsubscribe": f"<{unsubscribe_url}>"}
            sg = sg_module.SendGridAPIClient(api_key=sendgrid_key)
            response = sg.send(message)
            return response.status_code in (200, 202)
        except Exception as e:
            print(f"SendGrid error: {e}")
            return False
    else:
        # Option 1: SMTP — From name is business name, Reply-To is business email
        try:
            smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
            smtp_port = int(os.getenv("SMTP_PORT", 587))
            sender_email = os.getenv("SENDER_EMAIL")
            sender_password = os.getenv("SENDER_PASSWORD")
            if not all([sender_email, sender_password]):
                print("Email not configured")
                return False
            msg = MIMEMultipart("alternative")
            msg["From"] = f'"{from_name}" <{sender_email}>'
            msg["To"] = to_email
            msg["Subject"] = subject
            if business_reply_email:
                msg["Reply-To"] = business_reply_email
            if unsubscribe_url:
                msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(html_body, "html"))
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender_email, sender_password)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"Email error: {e}")
            return False

def send_sms(to_phone, message):
    if not twilio_client or not twilio_phone:
        return False
    try:
        twilio_client.messages.create(body=message, from_=twilio_phone, to=to_phone)
        return True
    except Exception as e:
        print(f"SMS error: {e}")
        return False

def generate_ai_message(customer_name, business_name, campaign_type):
    fallbacks = {
        "come_back":  f"We miss you, {customer_name}! Come back to {business_name} and enjoy 15% off your next visit. We'd love to see you again!",
        "weekend":    f"Hi {customer_name}! Weekend special at {business_name} — amazing food and great deals this weekend only!",
        "lunch":      f"Hi {customer_name}! Join us for lunch at {business_name} today. Fresh food, great prices, and a warm welcome!",
        "dinner":     f"Hi {customer_name}! Special dinner offer tonight at {business_name}. Reserve your table and enjoy an unforgettable evening!",
        "birthday":   f"Happy Birthday {customer_name}! Enjoy 20% off your next visit to {business_name}. Use code: BIRTHDAY20",
        "loyalty":    f"Thank you for being a loyal customer, {customer_name}! Your exclusive reward is waiting at {business_name}.",
        "happy_hour":    f"Hi {customer_name}! Happy Hour at {business_name} — amazing drinks and bites at special prices. Come join us!",
        "new_item":      f"Hi {customer_name}! We just launched something exciting at {business_name}. Come be the first to try it!",
        "promotion":     f"Hi {customer_name}! Special promotion at {business_name} just for you. Don't miss out — visit us soon!",
        "hotel_stay":    f"Hi {customer_name}! Exclusive stay offer at {business_name}. Book now and save on your next visit!",
        "hotel_upgrade": f"Hi {customer_name}! Enjoy a complimentary room upgrade on your next stay at {business_name}. Limited availability!",
        "hotel_event":   f"Hi {customer_name}! Special event package at {business_name} — the perfect getaway. Book before it's gone!",
        "hotel_loyalty": f"Thank you for being a loyal guest, {customer_name}! You've earned exclusive rewards at {business_name}.",
    }

    if not client:
        return fallbacks.get(campaign_type, fallbacks["promotion"])

    prompts = {
        "come_back":     f"Write a warm 'we miss you, come back' offer for {customer_name} from {business_name}. Include a discount to return.",
        "weekend":       f"Write a weekend special promotion for {customer_name} from {business_name}. Make it exciting.",
        "lunch":         f"Write a lunch deal promotion for {customer_name} from {business_name}. Make it appetizing.",
        "dinner":        f"Write a dinner special for {customer_name} from {business_name}. Make it feel exclusive and special.",
        "birthday":      f"Write a birthday offer for {customer_name} from {business_name}. Include a discount.",
        "loyalty":       f"Write a loyalty reward message for {customer_name} from {business_name}. Thank them warmly.",
        "happy_hour":    f"Write a happy hour promotion for {customer_name} from {business_name}. Make it fun.",
        "new_item":      f"Write a new menu item announcement for {customer_name} from {business_name}. Make it exciting.",
        "promotion":     f"Write a general promotion for {customer_name} from {business_name}. Make it compelling.",
        "hotel_stay":    f"Write a special hotel stay offer for {customer_name} from {business_name} hotel. Include a discount on room rate.",
        "hotel_upgrade": f"Write a room upgrade offer for {customer_name} from {business_name} hotel. Make it feel luxurious and exclusive.",
        "hotel_event":   f"Write an event/package deal promotion for {customer_name} from {business_name} hotel. Make it sound exciting.",
        "hotel_loyalty": f"Write a guest loyalty reward message for {customer_name} from {business_name} hotel. Thank them warmly.",
    }

    prompt = prompts.get(campaign_type, prompts["promotion"]) + " Keep it under 3 sentences. No markdown. Include emojis."

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.7
        )
        return clean_ai_text(response.choices[0].message.content)
    except Exception as e:
        print(f"AI error: {e}")
        return fallbacks.get(campaign_type, fallbacks["promotion"])

# ============================================
# ROUTES
# ============================================

@app.route("/test")
def test():
    return f"<h1>Flask is working!</h1><p>Environment: {ENV}</p>"

@app.route("/", methods=["GET"])
def landing():
    try:
        return render_template("landing.html")
    except Exception as e:
        return f"<h1>Error: {e}</h1>", 500

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        message = request.form.get("message", "").strip()
        if not all([name, email, message]):
            flash("All fields required.", "error")
            return redirect("/contact")
        try:
            db.session.add(ContactMessage(name=name, email=email, message=message))
            db.session.commit()
            flash("Message sent!", "success")
            return redirect("/")
        except:
            db.session.rollback()
            flash("Error saving message.", "error")
    return render_template("contact.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect("/dashboard")
    if request.method == "POST":
        business_name = request.form.get("business_name", "").strip()
        owner_name = request.form.get("owner_name", "").strip()
        email = request.form.get("email", "").strip()
        raw_password = request.form.get("password", "")
        if not all([business_name, owner_name, email, raw_password]):
            flash("All fields required.", "error")
            return redirect("/register")
        if len(raw_password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return redirect("/register")
        if Business.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return redirect("/login")
        hashed = bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        b = Business(business_name=business_name, owner_name=owner_name, email=email, password=hashed)
        try:
            db.session.add(b)
            db.session.commit()
            session["user_id"] = b.id
            session.permanent = True
            flash(f"Welcome {owner_name}!", "success")
            return redirect("/dashboard")
        except:
            db.session.rollback()
            flash("Error creating account.", "error")
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        b = Business.query.filter_by(email=email).first()
        if b and bcrypt.checkpw(password.encode("utf-8"), b.password.encode("utf-8")):
            session["user_id"] = b.id
            session.permanent = True
            flash(f"Welcome back, {b.owner_name}!", "success")
            return redirect("/dashboard")
        flash("Invalid email or password.", "error")
        return redirect("/login")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("You've been logged out.", "success")
    return redirect("/")

@app.route("/dashboard")
def dashboard():
    b = current_business()
    if not b:
        return redirect("/login")
    process_scheduled_campaigns()
    run_automations(b.id)
    total_customers = Customer.query.filter_by(business_id=b.id).count()
    total_campaigns = Campaign.query.filter_by(business_id=b.id).count()
    sent_campaigns = Campaign.query.filter_by(business_id=b.id, status="sent").count()
    campaigns = Campaign.query.filter_by(business_id=b.id).order_by(Campaign.created_at.desc()).all()
    customer_limit = get_plan_limit(b.plan, "customers")
    # Birthday radar — customers with birthdays in the next 7 days
    today = datetime.utcnow()
    birthday_customers = []
    all_customers = Customer.query.filter_by(business_id=b.id).filter(Customer.dob != None, Customer.dob != "").all()
    for c in all_customers:
        try:
            dob = datetime.strptime(c.dob, "%Y-%m-%d")
            this_year_bday = dob.replace(year=today.year)
            if this_year_bday < today:
                this_year_bday = this_year_bday.replace(year=today.year + 1)
            days_until = (this_year_bday - today).days
            if 0 <= days_until <= 7:
                birthday_customers.append({"customer": c, "days": days_until})
        except Exception:
            pass
    birthday_customers.sort(key=lambda x: x["days"])
    return render_template(
        "dashboard.html",
        business_name=b.business_name,
        total_customers=total_customers,
        total_campaigns=total_campaigns,
        sent_campaigns=sent_campaigns,
        plan=b.plan,
        campaigns=campaigns,
        customer_limit=customer_limit,
        campaign_types=get_campaign_types(),
        birthday_customers=birthday_customers,
    )

@app.route("/customers")
def customers():
    b = current_business()
    if not b:
        return redirect("/login")
    customers_list = Customer.query.filter_by(business_id=b.id).order_by(Customer.created_at.desc()).all()
    return render_template("customers.html", customers=customers_list, plan=b.plan)

@app.route("/add-customer", methods=["GET", "POST"])
def add_customer():
    b = current_business()
    if not b:
        return redirect("/login")
    if request.method == "POST":
        customer_limit = get_plan_limit(b.plan, "customers")
        if customer_limit:
            count = Customer.query.filter_by(business_id=b.id).count()
            if count >= customer_limit:
                flash(f"Customer limit reached ({customer_limit}). Upgrade your plan.", "error")
                return redirect("/upgrade")
        first_name = request.form.get("first_name", "").strip()
        email = request.form.get("email", "").strip() or None
        phone = request.form.get("phone", "").strip()
        if not first_name:
            flash("First name is required.", "error")
            return redirect("/add-customer")
        if not email and not phone:
            flash("Please provide at least a phone number or email.", "error")
            return redirect("/add-customer")
        customer = Customer(
            business_id=b.id,
            first_name=first_name,
            last_name=request.form.get("last_name", "").strip(),
            email=email,
            phone=phone,
            dob=request.form.get("dob", "").strip()
        )
        try:
            db.session.add(customer)
            db.session.commit()
            flash(f"Customer {first_name} added!", "success")
            return redirect("/customers")
        except:
            db.session.rollback()
            flash("Error adding customer.", "error")
    return render_template("add_customer.html")

@app.route("/edit-customer/<int:customer_id>", methods=["POST"])
def edit_customer(customer_id):
    b = current_business()
    if not b:
        return redirect("/login")
    customer = Customer.query.get(customer_id)
    if not customer or customer.business_id != b.id:
        flash("Customer not found.", "error")
        return redirect("/customers")
    customer.first_name = request.form.get("first_name", customer.first_name).strip() or customer.first_name
    customer.last_name = request.form.get("last_name", "").strip()
    customer.email = request.form.get("email", customer.email).strip() or customer.email
    customer.phone = request.form.get("phone", "").strip()
    customer.dob = request.form.get("dob", "").strip()
    try:
        db.session.commit()
        flash("Customer updated.", "success")
    except:
        db.session.rollback()
        flash("Error updating customer.", "error")
    return redirect("/customers")

@app.route("/delete-customer/<int:customer_id>", methods=["POST"])
def delete_customer(customer_id):
    b = current_business()
    if not b:
        return redirect("/login")
    customer = Customer.query.get(customer_id)
    if customer and customer.business_id == b.id:
        try:
            db.session.delete(customer)
            db.session.commit()
            flash("Customer deleted.", "success")
        except:
            db.session.rollback()
            flash("Error deleting customer.", "error")
    return redirect("/customers")

@app.route("/campaigns")
def campaigns():
    b = current_business()
    if not b:
        return redirect("/login")
    campaigns_list = Campaign.query.filter_by(business_id=b.id).order_by(Campaign.created_at.desc()).all()
    return render_template("campaigns.html", campaigns=campaigns_list, campaign_types=get_campaign_types())

@app.route("/create-campaign", methods=["GET", "POST"])
def create_campaign():
    b = current_business()
    if not b:
        return redirect("/login")
    if request.method == "POST":
        customer_name = request.form.get("customer_name", "").strip()
        customer_email = request.form.get("customer_email", "").strip()
        customer_phone = request.form.get("customer_phone", "").strip()
        campaign_type = request.form.get("campaign_type", "promotion").strip()
        use_ai = request.form.get("use_ai") == "on"
        message = request.form.get("message", "").strip()
        scheduled_at_str = request.form.get("scheduled_at", "").strip()
        if not customer_name:
            flash("Customer name is required.", "error")
            return redirect("/create-campaign")
        if not customer_email and not customer_phone:
            flash("Please provide at least an email or phone number.", "error")
            return redirect("/create-campaign")
        if use_ai and not message:
            message = generate_ai_message(customer_name, b.business_name, campaign_type)
        elif not message:
            flash("Provide message or use AI.", "error")
            return redirect("/create-campaign")
        scheduled_at = None
        if scheduled_at_str:
            try:
                scheduled_at = datetime.strptime(scheduled_at_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                pass
        campaign_status = "scheduled" if scheduled_at else "draft"
        campaign = Campaign(
            business_id=b.id,
            customer_name=customer_name,
            customer_email=customer_email,
            customer_phone=customer_phone,
            campaign_type=campaign_type,
            message=message,
            status=campaign_status,
            scheduled_at=scheduled_at
        )
        try:
            db.session.add(campaign)
            db.session.commit()
            if scheduled_at:
                flash(f"Campaign scheduled for {scheduled_at.strftime('%b %d at %I:%M %p')}!", "success")
            else:
                flash("Campaign created!", "success")
            return redirect(f"/campaign/{campaign.id}")
        except:
            db.session.rollback()
            flash("Error creating campaign.", "error")
    customers_list = Customer.query.filter_by(business_id=b.id).all()
    # Pre-load a saved promotion if ?promo_id= passed
    promo_id = request.args.get("promo_id")
    selected_promo = None
    if promo_id:
        p = Promotion.query.get(int(promo_id))
        if p and p.business_id == b.id:
            selected_promo = {"id": p.id, "name": p.name, "description": p.description, "price": p.price, "category": p.category}
    return render_template("create_campaign.html", campaign_types=get_campaign_types(), customers=customers_list, selected_promo=selected_promo)

@app.route("/campaign/<int:campaign_id>")
def view_campaign(campaign_id):
    b = current_business()
    if not b:
        return redirect("/login")
    campaign = Campaign.query.get(campaign_id)
    if not campaign or campaign.business_id != b.id:
        flash("Campaign not found.", "error")
        return redirect("/campaigns")
    return render_template("view_campaign.html", campaign=campaign, plan=b.plan, campaign_types=get_campaign_types())

@app.route("/unsubscribe/<token>")
def unsubscribe(token):
    campaign_id = verify_unsubscribe_token(token)
    if not campaign_id:
        return render_template("404.html"), 404
    campaign = Campaign.query.get(campaign_id)
    if campaign:
        # Mark all customers with this email (for this business) as unsubscribed
        Customer.query.filter_by(
            business_id=campaign.business_id,
            email=campaign.customer_email
        ).update({"unsubscribed": True})
        db.session.commit()
    return render_template("unsubscribed.html")

@app.route("/track/open/<int:campaign_id>")
def track_open(campaign_id):
    campaign = Campaign.query.get(campaign_id)
    if campaign:
        campaign.open_count = (campaign.open_count or 0) + 1
        if not campaign.opened_at:
            campaign.opened_at = datetime.utcnow()
        try:
            db.session.commit()
        except:
            db.session.rollback()
    # Return a 1x1 transparent GIF
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    from flask import Response
    return Response(pixel, mimetype="image/gif", headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

@app.route("/track/click/<int:campaign_id>")
def track_click(campaign_id):
    campaign = Campaign.query.get(campaign_id)
    destination = "/"
    if campaign:
        campaign.click_count = (campaign.click_count or 0) + 1
        if not campaign.clicked_at:
            campaign.clicked_at = datetime.utcnow()
        try:
            db.session.commit()
        except:
            db.session.rollback()
        b = Business.query.get(campaign.business_id)
        if b and b.website:
            destination = b.website if b.website.startswith("http") else f"https://{b.website}"
    return redirect(destination)

@app.route("/send-campaign/<int:campaign_id>", methods=["POST"])
def send_campaign(campaign_id):
    b = current_business()
    if not b:
        return redirect("/login")
    campaign = Campaign.query.get(campaign_id)
    if not campaign or campaign.business_id != b.id:
        flash("Campaign not found.", "error")
        return redirect("/campaigns")
    if session.get("is_demo"):
        flash("Demo mode — emails are not sent. Sign up for a free account to send real campaigns!", "success")
        return redirect(f"/campaign/{campaign.id}")
    # Check if customer is unsubscribed
    customer = Customer.query.filter_by(business_id=b.id, email=campaign.customer_email).first()
    if customer and customer.unsubscribed:
        flash("This customer has unsubscribed and cannot receive emails.", "error")
        return redirect(f"/campaign/{campaign.id}")
    token = get_unsubscribe_token(campaign.id)
    unsub_url = url_for("unsubscribe", token=token, _external=True)
    pixel_url = url_for("track_open", campaign_id=campaign.id, _external=True)
    click_url = url_for("track_click", campaign_id=campaign.id, _external=True)
    subject = f"Special Offer from {b.business_name}"
    success = send_email(
        campaign.customer_email, subject, campaign.message,
        customer_name=campaign.customer_name,
        business_name=b.business_name,
        campaign_type=campaign.campaign_type,
        unsubscribe_url=unsub_url,
        business_address=b.address or "",
        business_phone=b.phone or "",
        business_website=b.website or "",
        tracking_pixel_url=pixel_url,
        click_tracking_url=click_url,
        business_reply_email=b.email
    )
    if success:
        campaign.status = "sent"
        try:
            db.session.commit()
            flash("Campaign sent successfully!", "success")
        except:
            db.session.rollback()
            flash("Error updating campaign.", "error")
    else:
        flash("Email not sent. Check email configuration.", "error")
    return redirect(f"/campaign/{campaign.id}")

@app.route("/send-sms/<int:campaign_id>", methods=["POST"])
def send_sms_campaign(campaign_id):
    b = current_business()
    if not b:
        return redirect("/login")
    if session.get("is_demo"):
        flash("Demo mode — SMS not sent. Sign up for a free account to send real campaigns!", "success")
        return redirect(f"/campaign/{campaign_id}")
    if b.plan == "free":
        flash("SMS requires Starter or Pro plan.", "error")
        return redirect("/upgrade")
    campaign = Campaign.query.get(campaign_id)
    if not campaign or campaign.business_id != b.id:
        flash("Campaign not found.", "error")
        return redirect("/campaigns")
    if not campaign.customer_phone:
        flash("No phone number for this customer.", "error")
        return redirect(f"/campaign/{campaign.id}")
    success = send_sms(campaign.customer_phone, campaign.message)
    if success:
        flash("SMS sent!", "success")
    else:
        flash("SMS not sent. Check Twilio configuration in Render environment.", "error")
    return redirect(f"/campaign/{campaign.id}")

@app.route("/quick-sms", methods=["GET", "POST"])
def quick_sms():
    b = current_business()
    if not b:
        return redirect("/login")
    if b.plan == "free":
        flash("Quick SMS requires Starter or Pro plan.", "error")
        return redirect("/upgrade")
    if request.method == "POST":
        recipient_name = request.form.get("recipient_name", "").strip() or "Customer"
        phone = request.form.get("phone", "").strip()
        message = request.form.get("message", "").strip()
        use_ai = request.form.get("use_ai") == "on"
        campaign_type = request.form.get("campaign_type", "promotion")
        if not phone:
            flash("Phone number is required.", "error")
            return redirect("/quick-sms")
        if use_ai and not message:
            message = generate_ai_message(recipient_name, b.business_name, campaign_type)
        if not message:
            flash("Enter a message or enable AI generation.", "error")
            return redirect("/quick-sms")
        success = send_sms(phone, message)
        # Save as a campaign record for tracking
        campaign = Campaign(
            business_id=b.id,
            customer_name=recipient_name,
            customer_email="",
            customer_phone=phone,
            campaign_type=campaign_type,
            message=message,
            status="sent" if success else "failed",
        )
        try:
            db.session.add(campaign)
            db.session.commit()
        except Exception:
            db.session.rollback()
        if success:
            flash(f"SMS sent to {phone}!", "success")
        else:
            flash("SMS not sent. Check Twilio configuration.", "error")
        return redirect("/quick-sms")
    customers_list = Customer.query.filter_by(business_id=b.id).filter(Customer.phone != None).filter(Customer.phone != "").order_by(Customer.first_name).all()
    return render_template("quick_sms.html", campaign_types=get_campaign_types(), customers=customers_list, plan=b.plan)

@app.route("/bulk-send", methods=["GET", "POST"])
def bulk_send():
    b = current_business()
    if not b:
        return redirect("/login")
    if session.get("is_demo") and request.method == "POST":
        flash("Demo mode — emails not sent. Sign up for a free account to send real campaigns!", "success")
        return redirect("/bulk-send")
    if request.method == "POST":
        campaign_type = request.form.get("campaign_type", "promotion")
        use_ai = request.form.get("use_ai") == "on"
        custom_message = request.form.get("message", "").strip()
        scheduled_at_str = request.form.get("scheduled_at", "").strip()
        segment = request.form.get("segment", "all")
        customers_list = get_segment_customers(b.id, segment)
        if not customers_list:
            flash("No customers match that segment. Try a different one.", "error")
            return redirect("/bulk-send")
        if not custom_message and not use_ai:
            flash("Enter a message or enable AI generation.", "error")
            return redirect("/bulk-send")
        scheduled_at = None
        if scheduled_at_str:
            try:
                scheduled_at = datetime.strptime(scheduled_at_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                pass
        sent_count = 0
        skipped_unsub = 0
        skipped_no_email = 0
        for customer in customers_list:
            if customer.unsubscribed:
                skipped_unsub += 1
                continue
            if not customer.email:
                skipped_no_email += 1
                continue
            msg = generate_ai_message(customer.first_name, b.business_name, campaign_type) if (use_ai and not custom_message) else custom_message
            campaign_status = "scheduled" if scheduled_at else "draft"
            campaign = Campaign(
                business_id=b.id,
                customer_name=customer.first_name,
                customer_email=customer.email,
                customer_phone=customer.phone,
                campaign_type=campaign_type,
                message=msg,
                status=campaign_status,
                scheduled_at=scheduled_at
            )
            db.session.add(campaign)
            if not scheduled_at:
                db.session.flush()
                token = get_unsubscribe_token(campaign.id)
                unsub_url = url_for("unsubscribe", token=token, _external=True)
                pixel_url = url_for("track_open", campaign_id=campaign.id, _external=True)
                click_url = url_for("track_click", campaign_id=campaign.id, _external=True)
                subject = f"Special Offer from {b.business_name}"
                if send_email(customer.email, subject, msg, customer_name=customer.first_name, business_name=b.business_name, campaign_type=campaign_type, unsubscribe_url=unsub_url, business_address=b.address or "", business_phone=b.phone or "", business_website=b.website or "", tracking_pixel_url=pixel_url, click_tracking_url=click_url, business_reply_email=b.email):
                    campaign.status = "sent"
                    sent_count += 1
        db.session.commit()
        notes = []
        if skipped_unsub: notes.append(f"{skipped_unsub} unsubscribed")
        if skipped_no_email: notes.append(f"{skipped_no_email} phone-only (no email)")
        note_str = f" ({', '.join(notes)} skipped)" if notes else ""
        if scheduled_at:
            flash(f"Scheduled {len(customers_list) - skipped_unsub - skipped_no_email} campaigns for {scheduled_at.strftime('%b %d at %I:%M %p')} UTC{note_str}.", "success")
        else:
            flash(f"Bulk send complete! Sent to {sent_count}/{len(customers_list)} customers{note_str}.", "success")
        return redirect("/campaigns")
    customers_count = Customer.query.filter_by(business_id=b.id, unsubscribed=False).count()
    return render_template("bulk_send.html", campaign_types=get_campaign_types(), customers_count=customers_count)

@app.route("/edit-campaign/<int:campaign_id>", methods=["POST"])
def edit_campaign(campaign_id):
    b = current_business()
    if not b:
        return redirect("/login")
    campaign = Campaign.query.get(campaign_id)
    if not campaign or campaign.business_id != b.id:
        flash("Campaign not found.", "error")
        return redirect("/campaigns")
    if campaign.status == "sent":
        flash("Cannot edit a sent campaign.", "error")
        return redirect(f"/campaign/{campaign.id}")
    campaign.message = request.form.get("message", campaign.message).strip() or campaign.message
    campaign.campaign_type = request.form.get("campaign_type", campaign.campaign_type)
    scheduled_at_str = request.form.get("scheduled_at", "").strip()
    if scheduled_at_str:
        try:
            campaign.scheduled_at = datetime.strptime(scheduled_at_str, "%Y-%m-%dT%H:%M")
            campaign.status = "scheduled"
        except ValueError:
            pass
    elif campaign.status == "scheduled":
        campaign.status = "draft"
        campaign.scheduled_at = None
    try:
        db.session.commit()
        flash("Campaign updated.", "success")
    except:
        db.session.rollback()
        flash("Error updating campaign.", "error")
    return redirect(f"/campaign/{campaign.id}")

@app.route("/clone-campaign/<int:campaign_id>", methods=["POST"])
def clone_campaign(campaign_id):
    b = current_business()
    if not b:
        return redirect("/login")
    original = Campaign.query.get(campaign_id)
    if not original or original.business_id != b.id:
        flash("Campaign not found.", "error")
        return redirect("/campaigns")
    clone = Campaign(
        business_id=b.id,
        customer_name=original.customer_name,
        customer_email=original.customer_email,
        customer_phone=original.customer_phone,
        campaign_type=original.campaign_type,
        message=original.message,
        status="draft"
    )
    try:
        db.session.add(clone)
        db.session.commit()
        flash("Campaign cloned as a new draft.", "success")
        return redirect(f"/campaign/{clone.id}")
    except:
        db.session.rollback()
        flash("Error cloning campaign.", "error")
        return redirect(f"/campaign/{campaign_id}")

@app.route("/test-email/<int:campaign_id>", methods=["POST"])
def test_email(campaign_id):
    b = current_business()
    if not b:
        return redirect("/login")
    if session.get("is_demo"):
        flash("Demo mode — test email not sent. Sign up for a free account!", "success")
        return redirect(f"/campaign/{campaign_id}")
    campaign = Campaign.query.get(campaign_id)
    if not campaign or campaign.business_id != b.id:
        flash("Campaign not found.", "error")
        return redirect("/campaigns")
    subject = f"[TEST] {b.business_name} — {get_campaign_types().get(campaign.campaign_type, 'Campaign')}"
    success = send_email(
        b.email, subject, campaign.message,
        customer_name=b.owner_name,
        business_name=b.business_name,
        campaign_type=campaign.campaign_type,
        business_address=b.address or "",
        business_phone=b.phone or "",
        business_website=b.website or "",
        business_reply_email=b.email
    )
    if success:
        flash(f"Test email sent to {b.email}!", "success")
    else:
        flash("Test email failed. Check email configuration.", "error")
    return redirect(f"/campaign/{campaign.id}")

@app.route("/delete-campaign/<int:campaign_id>", methods=["POST"])
def delete_campaign(campaign_id):
    b = current_business()
    if not b:
        return redirect("/login")
    campaign = Campaign.query.get(campaign_id)
    if campaign and campaign.business_id == b.id:
        try:
            db.session.delete(campaign)
            db.session.commit()
            flash("Campaign deleted.", "success")
        except:
            db.session.rollback()
            flash("Error deleting campaign.", "error")
    return redirect("/campaigns")

@app.route("/upload-customers", methods=["GET", "POST"])
def upload_customers():
    b = current_business()
    if not b:
        return redirect("/login")

    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Please select a file.", "error")
            return redirect("/upload-customers")

        filename = file.filename.lower()
        rows = []

        try:
            if filename.endswith(".csv"):
                import csv, io
                content = file.read().decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(content))
                rows = list(reader)
            elif filename.endswith((".xlsx", ".xls")):
                import openpyxl, io
                wb = openpyxl.load_workbook(io.BytesIO(file.read()))
                ws = wb.active
                headers = [str(cell.value).strip().lower() if cell.value else "" for cell in ws[1]]
                for row in ws.iter_rows(min_row=2, values_only=True):
                    rows.append({headers[i]: (str(v).strip() if v else "") for i, v in enumerate(row)})
            else:
                flash("Only CSV or Excel (.xlsx) files supported.", "error")
                return redirect("/upload-customers")
        except Exception as e:
            flash(f"Error reading file: {e}", "error")
            return redirect("/upload-customers")

        # Auto-detect columns
        def find_col(row, keywords):
            for key in row.keys():
                if any(kw in key.lower() for kw in keywords):
                    return row.get(key, "").strip()
            return ""

        added = 0
        skipped = 0
        for row in rows:
            email = find_col(row, ["email", "e-mail", "mail"])
            phone = find_col(row, ["phone", "mobile", "cell", "tel", "number"])
            first_name = find_col(row, ["first", "fname", "name"]) or "Customer"
            last_name = find_col(row, ["last", "lname", "surname"])

            if not email and not phone:
                skipped += 1
                continue

            # Check plan limits
            customer_limit = get_plan_limit(b.plan, "customers")
            if customer_limit:
                count = Customer.query.filter_by(business_id=b.id).count()
                if count >= customer_limit:
                    flash(f"Customer limit reached ({customer_limit}). Upgrade to import more.", "error")
                    break

            customer = Customer(
                business_id=b.id,
                first_name=first_name,
                last_name=last_name,
                email=email or None,
                phone=phone or None,
            )
            db.session.add(customer)
            added += 1

        try:
            db.session.commit()
            flash(f"Imported {added} customers! ({skipped} rows skipped — no email or phone found)", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving customers: {e}", "error")

        return redirect("/customers")

    return render_template("upload_customers.html")

@app.route("/paste-customers", methods=["POST"])
def paste_customers():
    b = current_business()
    if not b:
        return redirect("/login")

    raw = request.form.get("contacts", "").strip()
    default_name = request.form.get("default_name", "Customer").strip() or "Customer"

    if not raw:
        flash("Please paste some contacts.", "error")
        return redirect("/upload-customers")

    import re
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    added = 0

    for line in lines:
        # Check plan limit
        customer_limit = get_plan_limit(b.plan, "customers")
        if customer_limit:
            count = Customer.query.filter_by(business_id=b.id).count()
            if count >= customer_limit:
                flash(f"Customer limit reached ({customer_limit}). Upgrade to import more.", "error")
                break

        email = ""
        phone = ""

        # Detect if line is email
        if "@" in line:
            email = line
        else:
            # Clean and use as phone
            phone = re.sub(r"[^\d\+\-\(\)\s]", "", line).strip()
            if len(re.sub(r"\D", "", phone)) < 7:
                continue  # skip if less than 7 digits

        customer = Customer(
            business_id=b.id,
            first_name=default_name,
            email=email or None,
            phone=phone or None,
        )
        db.session.add(customer)
        added += 1

    try:
        db.session.commit()
        flash(f"Imported {added} contacts successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving contacts: {e}", "error")

    return redirect("/customers")

@app.route("/generate-message", methods=["POST"])
def generate_message():
    b = current_business()
    if not b:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        data = request.get_json()
        customer_name = data.get("customer_name", "").strip()
        campaign_type = data.get("campaign_type", "promotion").strip()
        if not customer_name:
            return jsonify({"success": False, "error": "Customer name required"})
        message = generate_ai_message(customer_name, b.business_name, campaign_type)
        return jsonify({"success": True, "message": message})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/promotions", methods=["GET", "POST"])
def promotions():
    b = current_business()
    if not b:
        return redirect("/login")
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        price = request.form.get("price", "").strip()
        category = request.form.get("category", "promotion").strip()
        if not name:
            flash("Name is required.", "error")
            return redirect("/promotions")
        db.session.add(Promotion(
            business_id=b.id, name=name, description=description,
            price=price, category=category
        ))
        db.session.commit()
        flash(f"'{name}' saved to your promotions library!", "success")
        return redirect("/promotions")
    promos = Promotion.query.filter_by(business_id=b.id, active=True).order_by(Promotion.created_at.desc()).all()
    return render_template("promotions.html", promotions=promos, campaign_types=get_campaign_types())

@app.route("/promotions/delete/<int:promo_id>", methods=["POST"])
def delete_promotion(promo_id):
    b = current_business()
    if not b:
        return redirect("/login")
    p = Promotion.query.get(promo_id)
    if p and p.business_id == b.id:
        db.session.delete(p)
        db.session.commit()
        flash("Promotion deleted.", "success")
    return redirect("/promotions")

@app.route("/promotions/edit/<int:promo_id>", methods=["POST"])
def edit_promotion(promo_id):
    b = current_business()
    if not b:
        return redirect("/login")
    p = Promotion.query.get(promo_id)
    if not p or p.business_id != b.id:
        flash("Not found.", "error")
        return redirect("/promotions")
    p.name = request.form.get("name", p.name).strip() or p.name
    p.description = request.form.get("description", "").strip()
    p.price = request.form.get("price", "").strip()
    p.category = request.form.get("category", p.category)
    db.session.commit()
    flash("Updated.", "success")
    return redirect("/promotions")

@app.route("/api/promotions")
def api_promotions():
    b = current_business()
    if not b:
        return jsonify([])
    promos = Promotion.query.filter_by(business_id=b.id, active=True).order_by(Promotion.created_at.desc()).all()
    return jsonify([{"id": p.id, "name": p.name, "description": p.description, "price": p.price, "category": p.category} for p in promos])

AUTOMATION_DEFAULTS = {
    "birthday": {
        "label": "Birthday Campaign",
        "icon": "🎂",
        "desc": "Automatically sends a birthday message to guests on (or before) their birthday.",
        "default_msg": "Happy Birthday {name}! 🎉 As our special guest, enjoy a complimentary gift on your next visit to {business}. You deserve to be celebrated!",
        "default_days": 0,
        "days_label": "Days before birthday to send",
    },
    "reengagement": {
        "label": "Re-engagement Campaign",
        "icon": "🔄",
        "desc": "Automatically reaches out to guests you haven't contacted in a while.",
        "default_msg": "Hi {name}, we miss you at {business}! 😊 It's been a while — come back and enjoy an exclusive offer just for you. We'd love to see you again!",
        "default_days": 30,
        "days_label": "Days since last contact",
    },
    "welcome": {
        "label": "Welcome Message",
        "icon": "👋",
        "desc": "Automatically sends a warm welcome to every new guest added to your list.",
        "default_msg": "Welcome to {business}, {name}! 🎉 We're thrilled to have you. Stay tuned for exclusive offers and updates just for our valued guests.",
        "default_days": 0,
        "days_label": "",
    },
}

@app.route("/automations", methods=["GET", "POST"])
def automations():
    b = current_business()
    if not b:
        return redirect("/login")
    if request.method == "POST":
        rule_type = request.form.get("rule_type")
        if rule_type not in AUTOMATION_DEFAULTS:
            flash("Invalid automation type.", "error")
            return redirect("/automations")
        active = request.form.get("active") == "on"
        message_template = request.form.get("message_template", "").strip()
        days_threshold = int(request.form.get("days_threshold", 0) or 0)
        rule = AutomationRule.query.filter_by(business_id=b.id, rule_type=rule_type).first()
        if rule:
            rule.active = active
            rule.message_template = message_template
            rule.days_threshold = days_threshold
        else:
            rule = AutomationRule(
                business_id=b.id, rule_type=rule_type, active=active,
                message_template=message_template, days_threshold=days_threshold
            )
            db.session.add(rule)
        db.session.commit()
        status = "enabled" if active else "disabled"
        flash(f"{AUTOMATION_DEFAULTS[rule_type]['label']} {status}.", "success")
        return redirect("/automations")
    rules = {r.rule_type: r for r in AutomationRule.query.filter_by(business_id=b.id).all()}
    return render_template("automations.html", rules=rules, defaults=AUTOMATION_DEFAULTS)

@app.route("/api/segment-count")
def segment_count():
    b = current_business()
    if not b:
        return jsonify({"count": 0})
    segment = request.args.get("segment", "all")
    customers = get_segment_customers(b.id, segment)
    sample = [{"name": f"{c.first_name} {c.last_name or ''}".strip(), "email": c.email or "", "phone": c.phone or ""} for c in customers[:5]]
    return jsonify({"count": len(customers), "sample": sample})

@app.route("/ai-ideas", methods=["POST"])
def ai_ideas():
    b = current_business()
    if not b:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        data = request.get_json()
        description = data.get("description", "").strip()
        customer_name = data.get("customer_name", "a valued customer").strip() or "a valued customer"
        campaign_type = data.get("campaign_type", "promotion").strip()
        if not description:
            return jsonify({"success": False, "error": "Description required"})
        if not client:
            return jsonify({"success": False, "error": "AI not configured"})
        prompt = (
            f"You are a marketing expert for a small business called '{b.business_name}'.\n"
            f"The business owner wants to promote this: \"{description}\"\n"
            f"The customer's name is {customer_name}.\n"
            f"Write 3 different short SMS/email marketing messages (each under 3 sentences, max 160 chars, with emojis, no markdown).\n"
            f"Return ONLY a JSON array of 3 strings, no explanation. Example: [\"msg1\", \"msg2\", \"msg3\"]"
        )
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.8
        )
        import json as json_module
        raw = clean_ai_text(response.choices[0].message.content)
        # Extract JSON array from response
        start = raw.find("[")
        end = raw.rfind("]") + 1
        ideas = json_module.loads(raw[start:end]) if start >= 0 else [raw]
        return jsonify({"success": True, "ideas": ideas[:3]})
    except Exception as e:
        print(f"AI ideas error: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/load-demo", methods=["POST"])
def load_demo():
    b = current_business()
    if not b:
        return redirect("/login")

    # Birthdays: some within the next 7 days (relative to today) so Birthday Radar shows live
    _today = datetime.utcnow()
    def _bday(month, day, year=1988):
        return f"{year}-{month:02d}-{day:02d}"
    def _soon(days_ahead, year=1990):
        d = _today + timedelta(days=days_ahead)
        return f"{year}-{d.month:02d}-{d.day:02d}"

    DEMO_CUSTOMERS = [
        {"first": "James",    "last": "Martinez",  "email": "james.martinez@demo.com",   "phone": "+12145550101", "dob": _soon(1)},
        {"first": "Priya",    "last": "Sharma",    "email": "priya.sharma@demo.com",     "phone": "+12145550102", "dob": _soon(3)},
        {"first": "Carlos",   "last": "Reyes",     "email": "carlos.reyes@demo.com",     "phone": "+12145550103", "dob": _bday(4, 12)},
        {"first": "Ashley",   "last": "Thompson",  "email": "ashley.t@demo.com",         "phone": "+12145550104", "dob": _bday(6, 22)},
        {"first": "Michael",  "last": "Chen",      "email": "michael.chen@demo.com",     "phone": "+12145550105", "dob": _bday(8, 5)},
        {"first": "Fatima",   "last": "Al-Hassan", "email": "fatima.h@demo.com",         "phone": "+12145550106", "dob": _bday(9, 18)},
        {"first": "David",    "last": "Williams",  "email": "david.w@demo.com",          "phone": "+12145550107", "dob": _bday(11, 30)},
        {"first": "Sofia",    "last": "Nguyen",    "email": "sofia.nguyen@demo.com",     "phone": "+12145550108", "dob": _soon(5)},
        {"first": "Kevin",    "last": "Johnson",   "email": "kevin.j@demo.com",          "phone": "+12145550109", "dob": _bday(2, 14)},
        {"first": "Maria",    "last": "Garcia",    "email": "maria.garcia@demo.com",     "phone": "+12145550110", "dob": _bday(7, 4)},
        {"first": "Tyler",    "last": "Brooks",    "email": "tyler.brooks@demo.com",     "phone": "+12145550111", "dob": _bday(10, 31)},
        {"first": "Aisha",    "last": "Patel",     "email": "aisha.patel@demo.com",      "phone": "+12145550112", "dob": _soon(2)},
        {"first": "Ryan",     "last": "Kim",       "email": "ryan.kim@demo.com",         "phone": "+12145550113", "dob": _bday(5, 20)},
        {"first": "Jessica",  "last": "Davis",     "email": "jessica.d@demo.com",        "phone": "+12145550114", "dob": _bday(12, 1)},
        {"first": "Brandon",  "last": "Lee",       "email": "brandon.lee@demo.com",      "phone": "+12145550115", "dob": _bday(3, 8)},
        {"first": "Natalie",  "last": "Robinson",  "email": "natalie.r@demo.com",        "phone": "+12145550116", "dob": _bday(1, 25)},
        {"first": "Omar",     "last": "Hassan",    "email": "omar.hassan@demo.com",      "phone": "+12145550117", "dob": _bday(6, 15)},
        {"first": "Lauren",   "last": "Mitchell",  "email": "lauren.m@demo.com",         "phone": "+12145550118", "dob": _bday(4, 3)},
        {"first": "Ethan",    "last": "Cooper",    "email": "ethan.c@demo.com",          "phone": "+12145550119", "dob": _bday(8, 27)},
        {"first": "Rachel",   "last": "Torres",    "email": "rachel.t@demo.com",         "phone": "+12145550120", "dob": _bday(11, 11)},
    ]

    DEMO_CAMPAIGNS = [
        {"name": "James Martinez",   "email": "james.martinez@demo.com",  "phone": "+12145550101", "type": "come_back",  "status": "sent",
         "msg": "Hey James! We miss you at {}! Come back this week and enjoy 15% off your next visit. Use code: COMEBACK15 — we'd love to see you again!"},
        {"name": "Priya Sharma",     "email": "priya.sharma@demo.com",    "phone": "+12145550102", "type": "birthday",   "status": "sent",
         "msg": "Happy Birthday Priya! From all of us at {}, we hope your day is amazing! Enjoy 20% off your next visit — our gift to you. Use code: BDAY20"},
        {"name": "Carlos Reyes",     "email": "carlos.reyes@demo.com",    "phone": "+12145550103", "type": "weekend",    "status": "sent",
         "msg": "Carlos, this weekend only — {} is running an exclusive special just for our regulars! Come in Saturday or Sunday for amazing deals. See you soon!"},
        {"name": "Ashley Thompson",  "email": "ashley.t@demo.com",        "phone": "+12145550104", "type": "loyalty",    "status": "sent",
         "msg": "Ashley, you are one of our most valued customers at {}! As a thank-you for your loyalty, here's a special reward waiting for you — ask us when you visit!"},
        {"name": "Michael Chen",     "email": "michael.chen@demo.com",    "phone": "+12145550105", "type": "lunch",      "status": "sent",
         "msg": "Michael, join us for lunch at {}! This week we have incredible daily specials, fresh ingredients, and your favorite dishes ready to go. Come see us!"},
        {"name": "Fatima Al-Hassan", "email": "fatima.h@demo.com",        "phone": "+12145550106", "type": "new_item",   "status": "sent",
         "msg": "Fatima, exciting news! {} just launched something new and we think you're going to love it. Come in and be one of the first to try it!"},
        {"name": "David Williams",   "email": "david.w@demo.com",         "phone": "+12145550107", "type": "happy_hour", "status": "sent",
         "msg": "David! Happy Hour at {} is happening this week — amazing drinks, great bites, and unbeatable prices. Bring a friend and make it a night!"},
        {"name": "Sofia Nguyen",     "email": "sofia.nguyen@demo.com",    "phone": "+12145550108", "type": "dinner",     "status": "sent",
         "msg": "Sofia, treat yourself tonight! {} has a special dinner offer just for you — a memorable evening with exceptional food. Reserve your spot today!"},
        {"name": "Kevin Johnson",    "email": "kevin.j@demo.com",         "phone": "+12145550109", "type": "promotion",  "status": "sent",
         "msg": "Kevin, {} has an exclusive promotion running this week just for our VIP customers! Don't miss out — visit us and mention this message at the door."},
        {"name": "Maria Garcia",     "email": "maria.garcia@demo.com",    "phone": "+12145550110", "type": "come_back",  "status": "draft",
         "msg": "Maria, it's been a while and we miss you! Come back to {} this week and we'll make sure you feel like a VIP. See you soon!"},
        {"name": "Tyler Brooks",     "email": "tyler.brooks@demo.com",    "phone": "+12145550111", "type": "weekend",    "status": "draft",
         "msg": "Tyler! Big weekend coming up at {}. We have something special planned and want YOU there. Saturday and Sunday only — come check it out!"},
    ]

    # Clear existing demo data first (customers with @demo.com emails)
    existing_emails = [c["email"] for c in DEMO_CUSTOMERS]
    Customer.query.filter(
        Customer.business_id == b.id,
        Customer.email.in_(existing_emails)
    ).delete(synchronize_session=False)
    Campaign.query.filter(
        Campaign.business_id == b.id,
        Campaign.customer_email.in_(existing_emails)
    ).delete(synchronize_session=False)

    # Add demo customers
    for c in DEMO_CUSTOMERS:
        db.session.add(Customer(
            business_id=b.id,
            first_name=c["first"],
            last_name=c["last"],
            email=c["email"],
            phone=c["phone"],
            dob=c.get("dob"),
        ))

    # Add demo campaigns
    for c in DEMO_CAMPAIGNS:
        db.session.add(Campaign(
            business_id=b.id,
            customer_name=c["name"],
            customer_email=c["email"],
            customer_phone=c["phone"],
            campaign_type=c["type"],
            message=c["msg"].format(b.business_name),
            status=c["status"],
        ))

    try:
        db.session.commit()
        flash(f"Demo data loaded! 20 customers and {len(DEMO_CAMPAIGNS)} campaigns added. You're ready to record.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error loading demo data: {e}", "error")

    return redirect("/dashboard")

DEMO_EMAIL = "demo@revvio.ai"

@app.route("/demo-login")
def demo_login():
    """One-click demo: creates/resets the demo account and logs in."""
    # Find or create demo business
    demo = Business.query.filter_by(email=DEMO_EMAIL).first()
    if not demo:
        hashed = bcrypt.hashpw(b"demo-revvio-2024", bcrypt.gensalt()).decode("utf-8")
        demo = Business(
            business_name="Mario's Grill",
            owner_name="Demo User",
            email=DEMO_EMAIL,
            password=hashed,
            plan="starter",
            address="123 Main St, Dallas, TX 75201"
        )
        db.session.add(demo)
        db.session.flush()  # get demo.id before commit

    # Wipe and re-seed demo data
    Customer.query.filter(Customer.business_id == demo.id).delete(synchronize_session=False)
    Campaign.query.filter(Campaign.business_id == demo.id).delete(synchronize_session=False)

    DEMO_CUSTOMERS = [
        {"first": "James",   "last": "Martinez",  "email": "james.martinez@demo.com",  "phone": "+12145550101"},
        {"first": "Priya",   "last": "Sharma",    "email": "priya.sharma@demo.com",    "phone": "+12145550102"},
        {"first": "Carlos",  "last": "Reyes",     "email": "carlos.reyes@demo.com",    "phone": "+12145550103"},
        {"first": "Ashley",  "last": "Thompson",  "email": "ashley.t@demo.com",        "phone": "+12145550104"},
        {"first": "Michael", "last": "Chen",      "email": "michael.chen@demo.com",    "phone": "+12145550105"},
        {"first": "Fatima",  "last": "Al-Hassan", "email": "fatima.h@demo.com",        "phone": "+12145550106"},
        {"first": "David",   "last": "Williams",  "email": "david.w@demo.com",         "phone": "+12145550107"},
        {"first": "Sofia",   "last": "Nguyen",    "email": "sofia.nguyen@demo.com",    "phone": "+12145550108"},
        {"first": "Kevin",   "last": "Johnson",   "email": "kevin.j@demo.com",         "phone": "+12145550109"},
        {"first": "Maria",   "last": "Garcia",    "email": "maria.garcia@demo.com",    "phone": "+12145550110"},
        {"first": "Tyler",   "last": "Brooks",    "email": "tyler.brooks@demo.com",    "phone": "+12145550111"},
        {"first": "Aisha",   "last": "Patel",     "email": "aisha.patel@demo.com",     "phone": "+12145550112"},
        {"first": "Ryan",    "last": "Kim",       "email": "ryan.kim@demo.com",        "phone": "+12145550113"},
        {"first": "Jessica", "last": "Davis",     "email": "jessica.d@demo.com",       "phone": "+12145550114"},
        {"first": "Brandon", "last": "Lee",       "email": "brandon.lee@demo.com",     "phone": "+12145550115"},
        {"first": "Natalie", "last": "Robinson",  "email": "natalie.r@demo.com",       "phone": "+12145550116"},
        {"first": "Omar",    "last": "Hassan",    "email": "omar.hassan@demo.com",     "phone": "+12145550117"},
        {"first": "Lauren",  "last": "Mitchell",  "email": "lauren.m@demo.com",        "phone": "+12145550118"},
        {"first": "Ethan",   "last": "Cooper",    "email": "ethan.c@demo.com",         "phone": "+12145550119"},
        {"first": "Rachel",  "last": "Torres",    "email": "rachel.t@demo.com",        "phone": "+12145550120"},
    ]
    DEMO_CAMPAIGNS = [
        {"name": "James Martinez",   "email": "james.martinez@demo.com",  "phone": "+12145550101", "type": "come_back",  "status": "sent",
         "msg": "Hey James! We miss you at Mario's Grill! Come back this week and enjoy 15% off your next visit. Use code: COMEBACK15"},
        {"name": "Priya Sharma",     "email": "priya.sharma@demo.com",    "phone": "+12145550102", "type": "birthday",   "status": "sent",
         "msg": "Happy Birthday Priya! From all of us at Mario's Grill, enjoy 20% off your next visit. Use code: BDAY20"},
        {"name": "Carlos Reyes",     "email": "carlos.reyes@demo.com",    "phone": "+12145550103", "type": "weekend",    "status": "sent",
         "msg": "Carlos, this weekend only — Mario's Grill is running an exclusive special for our regulars! Come in Saturday or Sunday."},
        {"name": "Ashley Thompson",  "email": "ashley.t@demo.com",        "phone": "+12145550104", "type": "loyalty",    "status": "sent",
         "msg": "Ashley, you're one of our most valued customers at Mario's Grill! Your exclusive loyalty reward is waiting — ask us when you visit!"},
        {"name": "Michael Chen",     "email": "michael.chen@demo.com",    "phone": "+12145550105", "type": "lunch",      "status": "sent",
         "msg": "Michael, join us for lunch at Mario's Grill! Fresh daily specials and your favorite dishes ready to go. Come see us!"},
        {"name": "Fatima Al-Hassan", "email": "fatima.h@demo.com",        "phone": "+12145550106", "type": "new_item",   "status": "sent",
         "msg": "Fatima, exciting news! Mario's Grill just launched something new and we think you're going to love it. Come in and be first to try it!"},
        {"name": "David Williams",   "email": "david.w@demo.com",         "phone": "+12145550107", "type": "happy_hour", "status": "sent",
         "msg": "David! Happy Hour at Mario's Grill this week — amazing drinks, great bites, unbeatable prices. Bring a friend!"},
        {"name": "Sofia Nguyen",     "email": "sofia.nguyen@demo.com",    "phone": "+12145550108", "type": "dinner",     "status": "sent",
         "msg": "Sofia, treat yourself tonight! Mario's Grill has a special dinner offer just for you — exceptional food and a memorable evening."},
        {"name": "Kevin Johnson",    "email": "kevin.j@demo.com",         "phone": "+12145550109", "type": "promotion",  "status": "sent",
         "msg": "Kevin, Mario's Grill has an exclusive promotion this week for VIP customers! Visit us and mention this message at the door."},
        {"name": "Maria Garcia",     "email": "maria.garcia@demo.com",    "phone": "+12145550110", "type": "come_back",  "status": "draft",
         "msg": "Maria, it's been a while and we miss you! Come back to Mario's Grill this week and we'll treat you like a VIP."},
        {"name": "Tyler Brooks",     "email": "tyler.brooks@demo.com",    "phone": "+12145550111", "type": "weekend",    "status": "draft",
         "msg": "Tyler! Big weekend at Mario's Grill — something special planned and we want YOU there. Saturday and Sunday only!"},
    ]

    for c in DEMO_CUSTOMERS:
        db.session.add(Customer(
            business_id=demo.id, first_name=c["first"], last_name=c["last"],
            email=c["email"], phone=c["phone"]
        ))
    for c in DEMO_CAMPAIGNS:
        db.session.add(Campaign(
            business_id=demo.id, customer_name=c["name"], customer_email=c["email"],
            customer_phone=c["phone"], campaign_type=c["type"],
            message=c["msg"], status=c["status"]
        ))
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return f"Demo setup error: {e}", 500

    session["user_id"] = demo.id
    session["is_demo"] = True
    session.permanent = True
    return redirect("/dashboard")


@app.route("/analytics")
def analytics():
    b = current_business()
    if not b:
        return redirect("/login")

    from datetime import timedelta

    all_campaigns = Campaign.query.filter_by(business_id=b.id).all()
    sent = [c for c in all_campaigns if c.status == "sent"]

    total_sent = len(sent)
    total_opens = sum(c.open_count or 0 for c in sent)
    total_clicks = sum(c.click_count or 0 for c in sent)
    open_rate = round((total_opens / total_sent * 100), 1) if total_sent else 0
    click_rate = round((total_clicks / total_sent * 100), 1) if total_sent else 0

    # Per campaign type breakdown
    type_stats = {}
    for c in sent:
        t = c.campaign_type
        if t not in type_stats:
            type_stats[t] = {"sent": 0, "opens": 0, "clicks": 0}
        type_stats[t]["sent"] += 1
        type_stats[t]["opens"] += c.open_count or 0
        type_stats[t]["clicks"] += c.click_count or 0
    for t in type_stats:
        s = type_stats[t]["sent"]
        type_stats[t]["open_rate"] = round(type_stats[t]["opens"] / s * 100, 1) if s else 0
        type_stats[t]["click_rate"] = round(type_stats[t]["clicks"] / s * 100, 1) if s else 0
        type_stats[t]["label"] = get_campaign_types().get(t, t)

    # Last 30 days daily sends
    today = datetime.utcnow().date()
    days_30 = [(today - timedelta(days=i)) for i in range(29, -1, -1)]
    daily_map = {}
    for c in sent:
        d = c.created_at.date()
        daily_map[d] = daily_map.get(d, 0) + 1
    daily_labels = [d.strftime("%b %d") for d in days_30]
    daily_data = [daily_map.get(d, 0) for d in days_30]

    # Top 5 customers by opens + clicks
    customer_engagement = {}
    for c in sent:
        key = (c.customer_name, c.customer_email)
        if key not in customer_engagement:
            customer_engagement[key] = {"opens": 0, "clicks": 0, "campaigns": 0}
        customer_engagement[key]["opens"] += c.open_count or 0
        customer_engagement[key]["clicks"] += c.click_count or 0
        customer_engagement[key]["campaigns"] += 1
    top_customers = sorted(
        [{"name": k[0], "email": k[1], **v} for k, v in customer_engagement.items()],
        key=lambda x: x["opens"] + x["clicks"], reverse=True
    )[:5]

    return render_template("analytics.html",
        total_sent=total_sent,
        total_opens=total_opens,
        total_clicks=total_clicks,
        open_rate=open_rate,
        click_rate=click_rate,
        type_stats=type_stats,
        daily_labels=daily_labels,
        daily_data=daily_data,
        top_customers=top_customers,
        campaign_types=get_campaign_types(),
    )

@app.route("/settings", methods=["GET", "POST"])
def settings():
    b = current_business()
    if not b:
        return redirect("/login")
    if request.method == "POST":
        b.business_name = request.form.get("business_name", b.business_name).strip() or b.business_name
        b.owner_name = request.form.get("owner_name", b.owner_name).strip() or b.owner_name
        b.address = request.form.get("address", "").strip()
        b.phone = request.form.get("phone", "").strip()
        b.website = request.form.get("website", "").strip()
        try:
            db.session.commit()
            flash("Settings saved!", "success")
        except:
            db.session.rollback()
            flash("Error saving settings.", "error")
        return redirect("/settings")
    return render_template("settings.html", business=b)

@app.route("/upgrade")
def upgrade():
    b = current_business()
    if not b:
        return redirect("/login")
    return render_template("upgrade.html", plan=b.plan, business_name=b.business_name)

@app.route("/upgrade/starter", methods=["POST"])
def upgrade_starter():
    b = current_business()
    if not b:
        return redirect("/login")
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_STARTER, "quantity": 1}],
            success_url=url_for("upgrade_success", plan="starter", _external=True),
            cancel_url=url_for("upgrade", _external=True),
            customer_email=b.email
        )
        return redirect(checkout_session.url)
    except Exception as e:
        flash(f"Error: {e}", "error")
        return redirect("/upgrade")

@app.route("/upgrade/pro", methods=["POST"])
def upgrade_pro():
    b = current_business()
    if not b:
        return redirect("/login")
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_PRO, "quantity": 1}],
            success_url=url_for("upgrade_success", plan="pro", _external=True),
            cancel_url=url_for("upgrade", _external=True),
            customer_email=b.email
        )
        return redirect(checkout_session.url)
    except Exception as e:
        flash(f"Error: {e}", "error")
        return redirect("/upgrade")

@app.route("/upgrade/success")
def upgrade_success():
    b = current_business()
    plan = request.args.get("plan", "pro")
    if b:
        b.plan = plan
        db.session.commit()
    flash(f"You're now on the {plan.title()} plan!", "success")
    return redirect("/dashboard")

@app.errorhandler(403)
def forbidden(e):
    return render_template("404.html"), 403

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500

# ============================================
# ADMIN PORTAL
# ============================================

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

def admin_required():
    return session.get("is_admin") is True

@app.route("/admin")
def admin_index():
    return redirect("/admin/login")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if admin_required():
        return redirect("/admin/dashboard")
    if request.method == "POST":
        pw = request.form.get("password", "")
        if ADMIN_PASSWORD and pw == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect("/admin/dashboard")
        flash("Invalid admin password.", "error")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect("/admin/login")

@app.route("/admin/dashboard")
def admin_dashboard():
    if not admin_required():
        return redirect("/admin/login")
    businesses = Business.query.order_by(Business.created_at.desc()).all()
    stats = []
    for b in businesses:
        customer_count = Customer.query.filter_by(business_id=b.id).count()
        campaign_count = Campaign.query.filter_by(business_id=b.id).count()
        sent_count = Campaign.query.filter_by(business_id=b.id, status="sent").count()
        total_opens = db.session.query(db.func.sum(Campaign.open_count)).filter_by(business_id=b.id).scalar() or 0
        stats.append({
            "business": b,
            "customers": customer_count,
            "campaigns": campaign_count,
            "sent": sent_count,
            "opens": total_opens,
        })
    total_businesses = len(businesses)
    total_customers = Customer.query.count()
    total_sent = Campaign.query.filter_by(status="sent").count()
    total_opens = db.session.query(db.func.sum(Campaign.open_count)).scalar() or 0
    plan_counts = {
        "free": sum(1 for b in businesses if b.plan == "free"),
        "starter": sum(1 for b in businesses if b.plan == "starter"),
        "pro": sum(1 for b in businesses if b.plan == "pro"),
    }
    all_campaign_types = CampaignTypeModel.query.order_by(CampaignTypeModel.sort_order, CampaignTypeModel.label).all()
    return render_template("admin_dashboard.html",
        stats=stats,
        total_businesses=total_businesses,
        total_customers=total_customers,
        total_sent=total_sent,
        total_opens=total_opens,
        plan_counts=plan_counts,
        campaign_types=all_campaign_types,
    )

@app.route("/admin/campaign-types/add", methods=["POST"])
def admin_add_campaign_type():
    if not admin_required():
        return redirect("/admin/login")
    key = request.form.get("key", "").strip().lower().replace(" ", "_")
    label = request.form.get("label", "").strip()
    if not key or not label:
        flash("Key and label are required.", "error")
        return redirect("/admin/dashboard")
    if CampaignTypeModel.query.filter_by(key=key).first():
        flash(f"Key '{key}' already exists.", "error")
        return redirect("/admin/dashboard")
    max_order = db.session.query(db.func.max(CampaignTypeModel.sort_order)).scalar() or 0
    db.session.add(CampaignTypeModel(key=key, label=label, sort_order=max_order + 1))
    db.session.commit()
    flash(f"Campaign type '{label}' added.", "success")
    return redirect("/admin/dashboard")

@app.route("/admin/campaign-types/edit/<int:type_id>", methods=["POST"])
def admin_edit_campaign_type(type_id):
    if not admin_required():
        return redirect("/admin/login")
    ct = CampaignTypeModel.query.get(type_id)
    if not ct:
        flash("Not found.", "error")
        return redirect("/admin/dashboard")
    ct.label = request.form.get("label", ct.label).strip() or ct.label
    ct.active = request.form.get("active") == "on"
    db.session.commit()
    flash("Updated.", "success")
    return redirect("/admin/dashboard")

@app.route("/admin/campaign-types/delete/<int:type_id>", methods=["POST"])
def admin_delete_campaign_type(type_id):
    if not admin_required():
        return redirect("/admin/login")
    ct = CampaignTypeModel.query.get(type_id)
    if ct:
        db.session.delete(ct)
        db.session.commit()
        flash(f"Deleted '{ct.label}'.", "success")
    return redirect("/admin/dashboard")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.getenv("DEBUG", "False").lower() == "true"
    app.run(host="127.0.0.1", port=port, debug=debug_mode, use_reloader=False)
