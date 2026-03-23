import os
import bcrypt
import stripe
import smtplib
from datetime import datetime
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

load_dotenv()

app = Flask(__name__)

# Production/Development setup
ENV = os.getenv("FLASK_ENV", "development")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
IS_PRODUCTION = ENV == "production"

app.config["ENV"] = ENV
app.config["DEBUG"] = DEBUG

# Security settings
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-12345")
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 3600

# Stripe Setup
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_fake")

# OpenAI
try:
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key and api_key != "sk-fake" and api_key:
        client = OpenAI(api_key=api_key)
    else:
        client = None
        print("⚠️  Warning: OPENAI_API_KEY not set.")
except Exception as e:
    client = None
    print(f"⚠️  Warning: OpenAI initialization failed: {e}")

# Database Config
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Campaign(db.Model):
    __tablename__ = "campaigns"
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False)
    customer_name = db.Column(db.String(200), nullable=False)
    customer_email = db.Column(db.String(200), nullable=False)
    campaign_type = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default="draft")
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
# HELPER FUNCTIONS
# ============================================

def current_business():
    if "user_id" not in session:
        return None
    return Business.query.get(session["user_id"])

def clean_ai_text(text: str) -> str:
    return (text or "").replace("###", "").replace("**", "").strip()

def send_email(to_email: str, subject: str, body: str) -> bool:
    try:
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        sender_email = os.getenv("SENDER_EMAIL")
        sender_password = os.getenv("SENDER_PASSWORD")

        if not all([sender_email, sender_password]):
            print("⚠️ Email not configured")
            return False

        msg = MIMEMultipart()
        msg["From"] = sender_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html"))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"❌ Email error: {e}")
        return False

def generate_ai_message(customer_name: str, restaurant_name: str, campaign_type: str) -> str:
    if not client:
        # Fallback messages if no AI
        fallback_messages = {
            "birthday": f"Happy Birthday {customer_name}! 🎂 Enjoy 20% off your next visit to {restaurant_name}. Use code: BIRTHDAY20",
            "loyalty": f"Thank you for being a loyal customer, {customer_name}! 💳 Claim your exclusive reward at {restaurant_name}.",
            "weekend": f"Hi {customer_name}! 🎉 Special weekend offer at {restaurant_name}. Don't miss out!",
            "promotion": f"Hi {customer_name}! Check out our amazing promotions at {restaurant_name}. Visit us soon!"
        }
        return fallback_messages.get(campaign_type, fallback_messages["promotion"])
    
    try:
        prompt = f"""Create a personalized, friendly restaurant marketing email message.
        Customer: {customer_name}
        Restaurant: {restaurant_name}
        Campaign Type: {campaign_type}
        
        Requirements:
        - Keep it brief (2-3 sentences)
        - Professional and engaging
        - Include emojis
        - Include a call to action
        - Do NOT include markdown formatting
        
        Just write the message, nothing else."""

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.7
        )
        return clean_ai_text(response.choices[0].message.content)
    except Exception as e:
        print(f"❌ AI error: {e}")
        # Return fallback
        fallback_messages = {
            "birthday": f"Happy Birthday {customer_name}! 🎂 Enjoy 20% off your next visit to {restaurant_name}.",
            "loyalty": f"Thank you for being loyal, {customer_name}! 💳 Special reward waiting at {restaurant_name}.",
            "weekend": f"Hi {customer_name}! 🎉 Special weekend offer at {restaurant_name}!",
            "promotion": f"Hi {customer_name}! Check out promotions at {restaurant_name}. Visit us!"
        }
        return fallback_messages.get(campaign_type, fallback_messages["promotion"])

# ============================================
# ROUTES
# ============================================

@app.route("/test")
def test():
    return f"<h1>✅ Flask is working!</h1><p>Environment: {ENV}</p><p>Debug: {DEBUG}</p>"

@app.route("/", methods=["GET"])
def landing():
    try:
        return render_template("landing.html")
    except Exception as e:
        print(f"❌ Error rendering landing.html: {e}")
        return f"<h1>Error: {e}</h1>", 500

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        message = request.form.get("message", "").strip()

        if not all([name, email, message]):
            flash("All fields required.", "error")
            return redirect("/contact")

        contact_msg = ContactMessage(name=name, email=email, message=message)
        try:
            db.session.add(contact_msg)
            db.session.commit()
            flash("✅ Message sent!", "success")
            return redirect("/")
        except Exception as e:
            db.session.rollback()
            flash("Error saving message.", "error")
            return redirect("/contact")

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

        existing = Business.query.filter_by(email=email).first()
        if existing:
            flash("Email already registered.", "error")
            return redirect("/login")

        hashed_password = bcrypt.hashpw(
            raw_password.encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")

        b = Business(
            business_name=business_name,
            owner_name=owner_name,
            email=email,
            password=hashed_password
        )

        try:
            db.session.add(b)
            db.session.commit()
            session["user_id"] = b.id
            session.permanent = True
            flash(f"✅ Welcome {owner_name}!", "success")
            return redirect("/dashboard")
        except Exception as e:
            db.session.rollback()
            flash("Error creating account.", "error")
            return redirect("/register")

    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect("/dashboard")

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        b = Business.query.filter_by(email=email).first()

        if b and bcrypt.checkpw(password.encode("utf-8"), b.password.encode("utf-8")):
            session["user_id"] = b.id
            session.permanent = True
            flash(f"✅ Welcome back, {b.owner_name}!", "success")
            return redirect("/dashboard")

        flash("❌ Invalid email or password.", "error")
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

    total_customers = Customer.query.filter_by(business_id=b.id).count()
    total_campaigns = Campaign.query.filter_by(business_id=b.id).count()
    sent_campaigns = Campaign.query.filter_by(business_id=b.id, status="sent").count()
    campaigns = Campaign.query.filter_by(business_id=b.id).order_by(Campaign.created_at.desc()).all()

    return render_template(
        "dashboard.html",
        business_name=b.business_name,
        total_customers=total_customers,
        total_campaigns=total_campaigns,
        sent_campaigns=sent_campaigns,
        plan=b.plan,
        campaigns=campaigns
    )

@app.route("/customers")
def customers():
    b = current_business()
    if not b:
        return redirect("/login")

    customers_list = Customer.query.filter_by(business_id=b.id).order_by(Customer.created_at.desc()).all()
    return render_template("customers.html", customers=customers_list)

@app.route("/add-customer", methods=["GET", "POST"])
def add_customer():
    b = current_business()
    if not b:
        return redirect("/login")

    if request.method == "POST":
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
            flash(f"✅ Customer {first_name} added!", "success")
            return redirect("/customers")
        except Exception as e:
            db.session.rollback()
            flash("Error adding customer.", "error")
            return redirect("/add-customer")

    return render_template("add_customer.html")

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
            flash("✅ Customer deleted.", "success")
        except Exception as e:
            db.session.rollback()
            flash("Error deleting customer.", "error")
    else:
        flash("❌ Unauthorized.", "error")

    return redirect("/customers")

@app.route("/campaigns")
def campaigns():
    b = current_business()
    if not b:
        return redirect("/login")

    campaigns_list = Campaign.query.filter_by(business_id=b.id).order_by(Campaign.created_at.desc()).all()
    return render_template("campaigns.html", campaigns=campaigns_list)

@app.route("/create-campaign", methods=["GET", "POST"])
def create_campaign():
    b = current_business()
    if not b:
        return redirect("/login")

    if request.method == "POST":
        customer_name = request.form.get("customer_name", "").strip()
        customer_email = request.form.get("customer_email", "").strip()
        campaign_type = request.form.get("campaign_type", "promotion").strip()
        use_ai = request.form.get("use_ai") == "on"
        message = request.form.get("message", "").strip()

        if not customer_name or not customer_email:
            flash("Name and email required.", "error")
            return redirect("/create-campaign")

        if use_ai and not message:
            message = generate_ai_message(customer_name, b.business_name, campaign_type)
        elif not message:
            flash("Provide message or use AI.", "error")
            return redirect("/create-campaign")

        campaign = Campaign(
            business_id=b.id,
            customer_name=customer_name,
            customer_email=customer_email,
            campaign_type=campaign_type,
            message=message,
            status="draft"
        )

        try:
            db.session.add(campaign)
            db.session.commit()
            flash("✅ Campaign created!", "success")
            return redirect(f"/campaign/{campaign.id}")
        except Exception as e:
            db.session.rollback()
            flash("Error creating campaign.", "error")
            print(f"❌ Error: {e}")
            return redirect("/create-campaign")

    return render_template("create_campaign.html")

@app.route("/campaign/<int:campaign_id>")
def view_campaign(campaign_id):
    b = current_business()
    if not b:
        return redirect("/login")

    campaign = Campaign.query.get(campaign_id)
    if not campaign or campaign.business_id != b.id:
        flash("Campaign not found.", "error")
        return redirect("/campaigns")

    return render_template("view_campaign.html", campaign=campaign)

@app.route("/send-campaign/<int:campaign_id>", methods=["POST"])
def send_campaign(campaign_id):
    b = current_business()
    if not b:
        return redirect("/login")

    campaign = Campaign.query.get(campaign_id)
    if not campaign or campaign.business_id != b.id:
        flash("Campaign not found.", "error")
        return redirect("/campaigns")

    subject = f"🍽️ Special Offer from {b.business_name}"
    success = send_email(campaign.customer_email, subject, campaign.message)

    if success:
        campaign.status = "sent"
        try:
            db.session.commit()
            flash("✅ Campaign sent!", "success")
        except Exception as e:
            db.session.rollback()
            flash("Error updating campaign.", "error")
    else:
        flash("⚠️ Email not sent. Check configuration.", "error")

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
            flash("✅ Campaign deleted.", "success")
        except Exception as e:
            db.session.rollback()
            flash("Error deleting campaign.", "error")
    else:
        flash("❌ Unauthorized.", "error")

    return redirect("/campaigns")

# ============================================
# NEW: AI MESSAGE GENERATION API
# ============================================

@app.route("/generate-message", methods=["POST"])
def generate_message():
    """API endpoint for real-time AI message generation"""
    b = current_business()
    if not b:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    try:
        data = request.get_json()
        customer_name = data.get("customer_name", "").strip()
        campaign_type = data.get("campaign_type", "promotion").strip()

        if not customer_name:
            return jsonify({"success": False, "error": "Customer name required"})

        # Generate message
        message = generate_ai_message(customer_name, b.business_name, campaign_type)

        return jsonify({
            "success": True,
            "message": message
        })
    except Exception as e:
        print(f"❌ Error in generate_message: {e}")
        return jsonify({"success": False, "error": str(e)})

# ============================================
# ERROR HANDLERS
# ============================================

@app.errorhandler(403)
def forbidden(e):
    return render_template("404.html"), 403

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500

@app.route("/upgrade")
def upgrade():
    b = current_business()
    if not b:
        return redirect("/login")
    return render_template("upgrade.html", plan=b.plan, business_name=b.business_name)

# ============================================
# APP START
# ============================================

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.getenv("DEBUG", "False").lower() == "true"
    
    print(f"\n🚀 Starting GrowthAI")
    print(f"📍 Environment: {ENV}")
    print(f"🐛 Debug Mode: {debug_mode}")
    print(f"🌐 Running on http://127.0.0.1:{port}")
    print(f"Press CTRL+C to stop\n")
    
    app.run(host="127.0.0.1", port=port, debug=debug_mode, use_reloader=False)