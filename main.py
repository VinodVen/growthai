import os
import bcrypt
import stripe
import smtplib
import hmac
import hashlib
import base64
from datetime import datetime
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

load_dotenv()

app = Flask(__name__)

ENV = os.getenv("FLASK_ENV", "development")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
IS_PRODUCTION = ENV == "production"

app.config["ENV"] = ENV
app.config["DEBUG"] = DEBUG
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-12345")
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 3600

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
    "come_back":  "Come Back Offer",
    "weekend":    "Weekend Special",
    "lunch":      "Lunch Deal",
    "dinner":     "Dinner Special",
    "birthday":   "Birthday Special",
    "loyalty":    "Loyalty Reward",
    "happy_hour": "Happy Hour",
    "new_item":   "New Item Launch",
    "promotion":  "General Promotion",
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Customer(db.Model):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False)
    first_name = db.Column(db.String(120), nullable=False)
    last_name = db.Column(db.String(120))
    email = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50))
    dob = db.Column(db.String(50))
    unsubscribed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Campaign(db.Model):
    __tablename__ = "campaigns"
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False)
    customer_name = db.Column(db.String(200), nullable=False)
    customer_email = db.Column(db.String(200), nullable=False)
    customer_phone = db.Column(db.String(50))
    campaign_type = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default="draft")
    scheduled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ContactMessage(db.Model):
    __tablename__ = "contact_messages"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# ============================================
# HELPERS
# ============================================

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
            subject = f"Special Offer from {b.business_name}"
            success = send_email(
                campaign.customer_email, subject, campaign.message,
                customer_name=campaign.customer_name,
                business_name=b.business_name,
                campaign_type=campaign.campaign_type,
                unsubscribe_url=unsub_url,
                business_address=b.address or ""
            )
            campaign.status = "sent" if success else "failed"
        if pending:
            db.session.commit()
    except Exception as e:
        print(f"Scheduler error: {e}")

def build_html_email(business_name, customer_name, message, campaign_type, unsubscribe_url="", business_address=""):
    unsub_html = ""
    if unsubscribe_url:
        unsub_html = f' · <a href="{unsubscribe_url}" style="color:#aaa;font-size:11px;">Unsubscribe</a>'
    address_line = business_address if business_address else "8105 Rasor Blvd Suite 280 · Plano, TX 75024"
    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;background:#f5f5f5;">
    <div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:30px;border-radius:12px;text-align:center;margin-bottom:20px;">
        <h1 style="color:white;margin:0;font-size:26px;">{business_name}</h1>
        <p style="color:rgba(255,255,255,0.9);margin:8px 0 0 0;">Special Offer Just For You</p>
    </div>
    <div style="background:white;padding:30px;border-radius:12px;margin-bottom:20px;">
        <p style="font-size:16px;color:#333;">Hi <strong>{customer_name}</strong>,</p>
        <p style="font-size:16px;color:#555;line-height:1.7;">{message}</p>
        <div style="text-align:center;margin-top:25px;">
            <p style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;padding:14px 30px;border-radius:8px;display:inline-block;font-size:16px;font-weight:bold;">
                Visit Us Today!
            </p>
        </div>
    </div>
    <p style="text-align:center;color:#999;font-size:12px;">
        You received this because you're a valued customer of {business_name}.<br>
        {business_name} · {address_line}{unsub_html}
    </p>
    </body></html>
    """

def send_email(to_email, subject, body, customer_name="", business_name="", campaign_type="promotion", unsubscribe_url="", business_address=""):
    try:
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        sender_email = os.getenv("SENDER_EMAIL")
        sender_password = os.getenv("SENDER_PASSWORD")

        if not all([sender_email, sender_password]):
            print("Email not configured")
            return False

        msg = MIMEMultipart("alternative")
        msg["From"] = sender_email
        msg["To"] = to_email
        msg["Subject"] = subject
        if unsubscribe_url:
            msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg.attach(MIMEText(body, "plain"))
        html_body = build_html_email(business_name or "GrowthAI", customer_name or "Valued Customer", body, campaign_type, unsubscribe_url, business_address)
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
        "happy_hour": f"Hi {customer_name}! Happy Hour at {business_name} — amazing drinks and bites at special prices. Come join us!",
        "new_item":   f"Hi {customer_name}! We just launched something exciting at {business_name}. Come be the first to try it!",
        "promotion":  f"Hi {customer_name}! Special promotion at {business_name} just for you. Don't miss out — visit us soon!",
    }

    if not client:
        return fallbacks.get(campaign_type, fallbacks["promotion"])

    prompts = {
        "come_back":  f"Write a warm 'we miss you, come back' offer for {customer_name} from {business_name}. Include a discount to return.",
        "weekend":    f"Write a weekend special promotion for {customer_name} from {business_name}. Make it exciting.",
        "lunch":      f"Write a lunch deal promotion for {customer_name} from {business_name}. Make it appetizing.",
        "dinner":     f"Write a dinner special for {customer_name} from {business_name}. Make it feel exclusive and special.",
        "birthday":   f"Write a birthday offer for {customer_name} from {business_name}. Include a discount.",
        "loyalty":    f"Write a loyalty reward message for {customer_name} from {business_name}. Thank them warmly.",
        "happy_hour": f"Write a happy hour promotion for {customer_name} from {business_name}. Make it fun.",
        "new_item":   f"Write a new menu item announcement for {customer_name} from {business_name}. Make it exciting.",
        "promotion":  f"Write a general promotion for {customer_name} from {business_name}. Make it compelling.",
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
            # If no customers yet, send to import page first
            if Customer.query.filter_by(business_id=b.id).count() == 0:
                return redirect("/upload-customers")
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
    total_customers = Customer.query.filter_by(business_id=b.id).count()
    total_campaigns = Campaign.query.filter_by(business_id=b.id).count()
    sent_campaigns = Campaign.query.filter_by(business_id=b.id, status="sent").count()
    campaigns = Campaign.query.filter_by(business_id=b.id).order_by(Campaign.created_at.desc()).all()
    customer_limit = get_plan_limit(b.plan, "customers")
    return render_template(
        "dashboard.html",
        business_name=b.business_name,
        total_customers=total_customers,
        total_campaigns=total_campaigns,
        sent_campaigns=sent_campaigns,
        plan=b.plan,
        campaigns=campaigns,
        customer_limit=customer_limit,
        campaign_types=CAMPAIGN_TYPES,
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
        email = request.form.get("email", "").strip()
        if not first_name or not email:
            flash("First name and email required.", "error")
            return redirect("/add-customer")
        customer = Customer(
            business_id=b.id,
            first_name=first_name,
            last_name=request.form.get("last_name", "").strip(),
            email=email,
            phone=request.form.get("phone", "").strip(),
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
    return render_template("campaigns.html", campaigns=campaigns_list, campaign_types=CAMPAIGN_TYPES)

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
        if not customer_name or not customer_email:
            flash("Name and email required.", "error")
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
    return render_template("create_campaign.html", campaign_types=CAMPAIGN_TYPES, customers=customers_list)

@app.route("/campaign/<int:campaign_id>")
def view_campaign(campaign_id):
    b = current_business()
    if not b:
        return redirect("/login")
    campaign = Campaign.query.get(campaign_id)
    if not campaign or campaign.business_id != b.id:
        flash("Campaign not found.", "error")
        return redirect("/campaigns")
    return render_template("view_campaign.html", campaign=campaign, plan=b.plan, campaign_types=CAMPAIGN_TYPES)

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

@app.route("/send-campaign/<int:campaign_id>", methods=["POST"])
def send_campaign(campaign_id):
    b = current_business()
    if not b:
        return redirect("/login")
    campaign = Campaign.query.get(campaign_id)
    if not campaign or campaign.business_id != b.id:
        flash("Campaign not found.", "error")
        return redirect("/campaigns")
    # Check if customer is unsubscribed
    customer = Customer.query.filter_by(business_id=b.id, email=campaign.customer_email).first()
    if customer and customer.unsubscribed:
        flash("This customer has unsubscribed and cannot receive emails.", "error")
        return redirect(f"/campaign/{campaign.id}")
    token = get_unsubscribe_token(campaign.id)
    unsub_url = url_for("unsubscribe", token=token, _external=True)
    subject = f"Special Offer from {b.business_name}"
    success = send_email(
        campaign.customer_email, subject, campaign.message,
        customer_name=campaign.customer_name,
        business_name=b.business_name,
        campaign_type=campaign.campaign_type,
        unsubscribe_url=unsub_url,
        business_address=b.address or ""
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

@app.route("/bulk-send", methods=["GET", "POST"])
def bulk_send():
    b = current_business()
    if not b:
        return redirect("/login")
    if request.method == "POST":
        campaign_type = request.form.get("campaign_type", "promotion")
        use_ai = request.form.get("use_ai") == "on"
        custom_message = request.form.get("message", "").strip()
        scheduled_at_str = request.form.get("scheduled_at", "").strip()
        customers_list = Customer.query.filter_by(business_id=b.id).all()
        if not customers_list:
            flash("No customers to send to. Add customers first.", "error")
            return redirect("/customers")
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
        for customer in customers_list:
            if customer.unsubscribed:
                skipped_unsub += 1
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
                subject = f"Special Offer from {b.business_name}"
                if send_email(customer.email, subject, msg, customer_name=customer.first_name, business_name=b.business_name, campaign_type=campaign_type, unsubscribe_url=unsub_url, business_address=b.address or ""):
                    campaign.status = "sent"
                    sent_count += 1
        db.session.commit()
        unsub_note = f" ({skipped_unsub} skipped — unsubscribed)" if skipped_unsub else ""
        if scheduled_at:
            flash(f"Scheduled {len(customers_list) - skipped_unsub} campaigns for {scheduled_at.strftime('%b %d at %I:%M %p')} UTC{unsub_note}.", "success")
        else:
            flash(f"Bulk send complete! Sent to {sent_count}/{len(customers_list)} customers{unsub_note}.", "success")
        return redirect("/campaigns")
    customers_count = Customer.query.filter_by(business_id=b.id).count()
    return render_template("bulk_send.html", campaign_types=CAMPAIGN_TYPES, customers_count=customers_count)

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
    campaign = Campaign.query.get(campaign_id)
    if not campaign or campaign.business_id != b.id:
        flash("Campaign not found.", "error")
        return redirect("/campaigns")
    subject = f"[TEST] {b.business_name} — {CAMPAIGN_TYPES.get(campaign.campaign_type, 'Campaign')}"
    success = send_email(
        b.email, subject, campaign.message,
        customer_name=b.owner_name,
        business_name=b.business_name,
        campaign_type=campaign.campaign_type,
        business_address=b.address or ""
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
                email=email or f"noemail_{added}@placeholder.com",
                phone=phone,
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
            email=email or f"nophone_{added}@placeholder.com",
            phone=phone,
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

@app.route("/load-demo", methods=["POST"])
def load_demo():
    b = current_business()
    if not b:
        return redirect("/login")

    DEMO_CUSTOMERS = [
        {"first": "James",    "last": "Martinez",  "email": "james.martinez@demo.com",   "phone": "+12145550101"},
        {"first": "Priya",    "last": "Sharma",    "email": "priya.sharma@demo.com",     "phone": "+12145550102"},
        {"first": "Carlos",   "last": "Reyes",     "email": "carlos.reyes@demo.com",     "phone": "+12145550103"},
        {"first": "Ashley",   "last": "Thompson",  "email": "ashley.t@demo.com",         "phone": "+12145550104"},
        {"first": "Michael",  "last": "Chen",      "email": "michael.chen@demo.com",     "phone": "+12145550105"},
        {"first": "Fatima",   "last": "Al-Hassan", "email": "fatima.h@demo.com",         "phone": "+12145550106"},
        {"first": "David",    "last": "Williams",  "email": "david.w@demo.com",          "phone": "+12145550107"},
        {"first": "Sofia",    "last": "Nguyen",    "email": "sofia.nguyen@demo.com",     "phone": "+12145550108"},
        {"first": "Kevin",    "last": "Johnson",   "email": "kevin.j@demo.com",          "phone": "+12145550109"},
        {"first": "Maria",    "last": "Garcia",    "email": "maria.garcia@demo.com",     "phone": "+12145550110"},
        {"first": "Tyler",    "last": "Brooks",    "email": "tyler.brooks@demo.com",     "phone": "+12145550111"},
        {"first": "Aisha",    "last": "Patel",     "email": "aisha.patel@demo.com",      "phone": "+12145550112"},
        {"first": "Ryan",     "last": "Kim",       "email": "ryan.kim@demo.com",         "phone": "+12145550113"},
        {"first": "Jessica",  "last": "Davis",     "email": "jessica.d@demo.com",        "phone": "+12145550114"},
        {"first": "Brandon",  "last": "Lee",       "email": "brandon.lee@demo.com",      "phone": "+12145550115"},
        {"first": "Natalie",  "last": "Robinson",  "email": "natalie.r@demo.com",        "phone": "+12145550116"},
        {"first": "Omar",     "last": "Hassan",    "email": "omar.hassan@demo.com",      "phone": "+12145550117"},
        {"first": "Lauren",   "last": "Mitchell",  "email": "lauren.m@demo.com",         "phone": "+12145550118"},
        {"first": "Ethan",    "last": "Cooper",    "email": "ethan.c@demo.com",          "phone": "+12145550119"},
        {"first": "Rachel",   "last": "Torres",    "email": "rachel.t@demo.com",         "phone": "+12145550120"},
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

@app.route("/settings", methods=["GET", "POST"])
def settings():
    b = current_business()
    if not b:
        return redirect("/login")
    if request.method == "POST":
        b.business_name = request.form.get("business_name", b.business_name).strip() or b.business_name
        b.owner_name = request.form.get("owner_name", b.owner_name).strip() or b.owner_name
        b.address = request.form.get("address", "").strip()
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

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.getenv("DEBUG", "False").lower() == "true"
    app.run(host="127.0.0.1", port=port, debug=debug_mode, use_reloader=False)
