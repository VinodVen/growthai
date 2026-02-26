import os
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
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super_secret_demo_key")

# ==========================
# OpenAI
# ==========================
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ==========================
# Database Config (Render Postgres)
# ==========================
db_url = os.getenv("DATABASE_URL", "")
# Render sometimes provides postgres://, SQLAlchemy wants postgresql://
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
    password = db.Column(db.String(200), nullable=False)  # NOTE: For real app, hash this.

class Customer(db.Model):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False)
    first_name = db.Column(db.String(120), nullable=False)
    last_name = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50), nullable=True)
    dob = db.Column(db.String(50), nullable=True)
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

# Auto-create tables (simple approach)
with app.app_context():
    db.create_all()

# ==========================
# Helpers
# ==========================
def current_business():
    if "user_id" not in session:
        return None
    return Business.query.get(session["user_id"])

def send_email(to_email, subject, message):
    sender_email = os.getenv("EMAIL_USER")
    sender_password = os.getenv("EMAIL_PASS")

    if not sender_email or not sender_password:
        return "Missing EMAIL_USER or EMAIL_PASS in environment variables."

    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
        return True
    except Exception as e:
        return str(e)

def clean_ai_text(text: str) -> str:
    return (text or "").replace("###", "").replace("**", "").strip()

# ==========================
# Routes
# ==========================
@app.route("/")
def landing():
    return render_template("landing.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        message = request.form["message"]

        print("Contact Form:", name, email, message)

        flash("✅ Thank you! We will contact you soon.", "success")
        return redirect("/contact")

    return render_template("contact.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        business_name = request.form["business_name"]
        owner_name = request.form["owner_name"]
        email = request.form["email"]
        password = request.form["password"]

        existing = Business.query.filter_by(email=email).first()
        if existing:
            return "Email already registered. Please login."

        b = Business(
            business_name=business_name,
            owner_name=owner_name,
            email=email,
            password=password
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
        if b and b.password == password:
            session["user_id"] = b.id
            return redirect("/dashboard")

        return "Invalid Credentials"

    return render_template("login.html")

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    b = current_business()
    if not b:
        return redirect("/login")

    promotion_message = None

    if request.method == "POST":

        # Generate Campaign
        if "generate_campaign" in request.form:
            first_name = request.form["first_name"]
            last_name = request.form.get("last_name", "")
            customer_email = request.form["customer_email"]
            phone = request.form.get("phone", "")
            dob = request.form.get("dob", "")
            campaign_type = request.form["campaign_type"]

            # Save customer
            cust = Customer(
                business_id=b.id,
                first_name=first_name,
                last_name=last_name,
                email=customer_email,
                phone=phone,
                dob=dob
            )
            db.session.add(cust)
            db.session.commit()

            # Prompt for SMALL BUSINESS SaaS (not only restaurants)
            business_context = f"Business name: {b.business_name}."
            if campaign_type == "birthday":
                prompt = f"{business_context} Create a short birthday promotion for {first_name} with 30% off. Keep it under 80 words."
            elif campaign_type == "loyalty":
                prompt = f"{business_context} Create a loyalty reward promotion for {first_name}. Keep it under 80 words."
            else:
                prompt = f"{business_context} Create a weekend promotion for {first_name} with 20% off. Keep it under 80 words."

            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a small business marketing assistant. Write short, friendly promotions."},
                        {"role": "user", "content": prompt}
                    ]
                )

                promotion_message = clean_ai_text(response.choices[0].message.content)

                # Save campaign
                camp = Campaign(
                    business_id=b.id,
                    customer_name=first_name,
                    customer_email=customer_email,
                    campaign_type=campaign_type,
                    message=promotion_message
                )
                db.session.add(camp)
                db.session.commit()

            except Exception as e:
                promotion_message = f"AI Error: {str(e)}"

        # Send Email
        elif "send_email" in request.form:
            customer_email = request.form["customer_email"]
            message = request.form["promotion_message"]

            result = send_email(
                customer_email,
                f"Special Offer from {b.business_name}",
                message
            )

            if result is True:
                promotion_message = "✅ Email Sent Successfully!"
            else:
                promotion_message = f"Email Error: {result}"

    campaigns = (
        Campaign.query.filter_by(business_id=b.id)
        .order_by(Campaign.created_at.desc())
        .limit(5)
        .all()
    )

    total_campaigns = Campaign.query.filter_by(business_id=b.id).count()

    return render_template(
        "dashboard.html",
        promotion_message=promotion_message,
        total_campaigns=total_campaigns,
        business_name=b.business_name,
        campaigns=campaigns
    )

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect("/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)