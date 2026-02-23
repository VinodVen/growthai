from flask import Flask, render_template, request, redirect, session
from dotenv import load_dotenv
from openai import OpenAI
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

# ==========================
# Load Environment Variables
# ==========================
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = Flask(__name__)
app.secret_key = "super_secret_demo_key"

# Demo storage
restaurants = {}
campaign_history = []

# ==========================
# Landing Page
# ==========================
@app.route("/")
def landing():
    return render_template("landing.html")

# ==========================
# Register
# ==========================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        business_name = request.form["business_name"]
        owner_name = request.form["owner_name"]
        email = request.form["email"]
        password = request.form["password"]

        restaurants[email] = {
            "business_name": business_name,
            "owner_name": owner_name,
            "password": password
        }

        session["user"] = email
        return redirect("/dashboard")

    return render_template("index.html")

# ==========================
# Login
# ==========================
@app.route("/login", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect("/dashboard")

    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        if email in restaurants and restaurants[email]["password"] == password:
            session["user"] = email
            return redirect("/dashboard")
        else:
            return "Invalid Credentials"

    return render_template("login.html")

# ==========================
# Email Function
# ==========================
def send_email(to_email, subject, message):
    sender_email = os.getenv("EMAIL_USER")
    sender_password = os.getenv("EMAIL_PASS")

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

# ==========================
# Dashboard
# ==========================
@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user" not in session:
        return redirect("/login")

    user_email = session["user"]
    business_name = restaurants[user_email]["business_name"]

    promotion_message = None

    if request.method == "POST":

        # Generate Campaign
        if "generate_campaign" in request.form:
            first_name = request.form["first_name"]
            customer_email = request.form["customer_email"]
            campaign_type = request.form["campaign_type"]

            if campaign_type == "birthday":
                prompt = f"Create a birthday promotion for {first_name} offering 30% discount."
            elif campaign_type == "loyalty":
                prompt = f"Create a loyalty reward promotion for {first_name}."
            else:
                prompt = f"Create a weekend promotion for {first_name} offering 20% discount."

            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a restaurant marketing assistant."},
                        {"role": "user", "content": prompt}
                    ]
                )

                promotion_message = response.choices[0].message.content

                campaign_history.append({
                    "name": first_name,
                    "email": customer_email,
                    "type": campaign_type,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M")
                })

            except Exception as e:
                promotion_message = f"AI Error: {str(e)}"

        # Send Email
        elif "send_email" in request.form:
            customer_email = request.form["customer_email"]
            message = request.form["promotion_message"]

            result = send_email(
                customer_email,
                "Special Offer from " + business_name,
                message
            )

            if result is True:
                promotion_message = "✅ Email Sent Successfully!"
            else:
                promotion_message = f"Email Error: {result}"

    return render_template(
        "dashboard.html",
        promotion_message=promotion_message,
        total_campaigns=len(campaign_history),
        business_name=business_name,
        campaigns=campaign_history[-5:]
    )

# ==========================
# Logout
# ==========================
@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/")

# ==========================
# Run Server
# ==========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

