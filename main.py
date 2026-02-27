import os
import bcrypt
import stripe
from datetime import datetime
from flask import Flask, render_template, request, redirect, session, url_for, flash
from dotenv import load_dotenv
from openai import OpenAI
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import smtplib
from email.mime.text import MIMEText

# ==========================
# Load Environment Variables
# ==========================
load_dotenv()

app = Flask(__name__)

# Security
app.secret_key = os.getenv("FLASK_SECRET_KEY")
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True

# ==========================
# Stripe Setup
# ==========================
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# ==========================
# OpenAI
# ==========================
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ==========================
# Database Config
# ==========================
db_url = os.getenv("DATABASE_URL", "")

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ==========================
# Models
# ==========================
class Business(db.Model):
    __tablename__ = "businesses"
    id = db.Column(db.Integer, primary_key=True)
    business_name = db.Column(db.String(200), nullable=False)
    owner_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

    plan = db.Column(db.String(50), default="free")
    stripe_customer_id = db.Column(db.String(200))

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

# ==========================
# Helpers
# ==========================
def current_business():
    if "user_id" not in session:
        return None
    return Business.query.get(session["user_id"])

def clean_ai_text(text: str) -> str:
    return (text or "").replace("###", "").replace("**", "").strip()

# ==========================
# Routes
# ==========================
@app.route("/")
def landing():
    return render_template("landing.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        business_name = request.form["business_name"]
        owner_name = request.form["owner_name"]
        email = request.form["email"]
        raw_password = request.form["password"]

        if len(raw_password) < 6:
            return "Password must be at least 6 characters."

        existing = Business.query.filter_by(email=email).first()
        if existing:
            return "Email already registered."

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

        db.session.add(b)
        db.session.commit()

        session["user_id"] = b.id
        return redirect("/dashboard")

    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect("/dashboard")

    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        b = Business.query.filter_by(email=email).first()

        if b and bcrypt.checkpw(password.encode("utf-8"), b.password.encode("utf-8")):
            session["user_id"] = b.id
            return redirect("/dashboard")

        return "Invalid Credentials"

    return render_template("login.html")

# ==========================
# Stripe Upgrade
# ==========================
@app.route("/upgrade")
def upgrade():
    b = current_business()
    if not b:
        return redirect("/login")

    checkout_session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="subscription",
        line_items=[{
            "price": os.getenv("STRIPE_PRICE_ID"),
            "quantity": 1
        }],
        success_url=url_for("success", _external=True),
        cancel_url=url_for("dashboard", _external=True),
        customer_email=b.email
    )

    return redirect(checkout_session.url)

@app.route("/success")
def success():
    b = current_business()
    if b:
        b.plan = "pro"
        db.session.commit()
    return "🎉 Upgrade Successful! You are now Pro."

# ==========================
# Dashboard
# ==========================
@app.route("/dashboard")
def dashboard():
    b = current_business()
    if not b:
        return redirect("/login")

    total_campaigns = Campaign.query.filter_by(business_id=b.id).count()

    return render_template(
        "dashboard.html",
        business_name=b.business_name,
        total_campaigns=total_campaigns,
        plan=b.plan
    )

# ==========================
# Admin Panel
# ==========================
@app.route("/admin")
def admin():
    # Only first registered user acts as admin
    if session.get("user_id") != 1:
        return "Unauthorized"

    businesses = Business.query.all()
    contacts = ContactMessage.query.order_by(ContactMessage.created_at.desc()).all()

    return render_template(
        "admin.html",
        businesses=businesses,
        contacts=contacts
    )

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect("/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)