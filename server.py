# -------------------------------------------------------------------------------------
# Python Flask Server for MongoDB/Email-to-Text Backend
# -------------------------------------------------------------------------------------
import os
import json
import smtplib
import socket  # Required for the IPv4 fix
from email.message import EmailMessage
from datetime import datetime, timezone
import hashlib

# Flask and MongoDB Libraries
from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from dotenv import load_dotenv


# --- Fix for Render/Gmail IPv6 Issues ---
# Force the application to use IPv4. Gmail's DNS often resolves to IPv6,
# which fails on many containerized cloud environments (Error 101).
def force_ipv4():
    old_getaddrinfo = socket.getaddrinfo

    def new_getaddrinfo(*args, **kwargs):
        responses = old_getaddrinfo(*args, **kwargs)
        return [response for response in responses if response[0] == socket.AF_INET]

    socket.getaddrinfo = new_getaddrinfo


force_ipv4()
# ----------------------------------------

# Load environment variables from .env file
load_dotenv()

# --- Configuration and Initialization ---
MONGO_URI = os.getenv("MONGO_URI")
GMAIL_SENDER = os.getenv("GMAIL_SENDER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

try:
    # 1. Server initialization check
    CLIENT = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000,
        tls=True,  # Enable TLS/SSL (standard and sufficient for Render)
    )
    DB = CLIENT.points_tracker_db
    STATE_COLLECTION = DB.app_state
except Exception as e:
    print(f"\n--- CRITICAL MongoDB Initialization Failure ---\nError: {e}\n")
    exit(1)

app = Flask(__name__)
CORS(app)

# --- Shared Constants ---
CARRIER_GATEWAYS = {
    'Verizon': 'vtext.com',
    'AT&T': 'txt.att.net',
    'T-Mobile': 'tmomail.net',
    'Sprint': 'messaging.sprintpcs.com',
    'Boost Mobile': 'sms.alltel.net',
    'MetroPCS': 'mymetropcs.com',
    'Cricket': 'mms.aiowireless.net',
    'US Cellular': 'email.uscc.net',
}
DEFAULT_ADMIN_PASSWORD_HASH = 'a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3'
DEFAULT_PIN = '1234'
DEFAULT_THRESHOLD = 10
DEFAULT_STATE = {
    "scores": {"Lila": 0, "Maryn": 0},
    "currentPin": DEFAULT_PIN,
    "adminPassHash": DEFAULT_ADMIN_PASSWORD_HASH,
    "notifications": [{"phone": "", "carrier": ""}] * 5,
    "changeHistory": {"Lila": [], "Maryn": []},
    "pinThreshold": DEFAULT_THRESHOLD,
    "lastUpdated": datetime.now(timezone.utc).isoformat()
}


def get_state():
    state = STATE_COLLECTION.find_one()
    if state:
        state['_id'] = str(state['_id'])
        return state
    else:
        STATE_COLLECTION.insert_one(DEFAULT_STATE)
        return DEFAULT_STATE.copy()


def update_state(data):
    if '_id' in data:
        del data['_id']
    data['lastUpdated'] = datetime.now(timezone.utc).isoformat()
    STATE_COLLECTION.replace_one({}, data, upsert=True)


def send_email_notification(message_subject, notifications):
    """
    Sends email. Returns True if successful, False otherwise.
    """
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        print("EMAIL LOG: Credentials missing.")
        return False

    recipients = []
    for n in notifications:
        phone = n.get('phone')
        carrier = n.get('carrier')
        if phone and carrier:
            domain = CARRIER_GATEWAYS.get(carrier) or carrier
            phone = "".join(filter(str.isdigit, str(phone)))
            if len(phone) == 10:
                recipients.append(f"{phone}@{domain}")
    if not recipients:
        print("EMAIL LOG: No valid recipients.")
        return False

    msg = EmailMessage()
    msg['Subject'] = "Points Tracker Alert"
    msg['From'] = GMAIL_SENDER
    msg['To'] = ", ".join(recipients)
    msg.set_content(message_subject)

    try:
        print("EMAIL LOG: Connecting to SMTP server (IPv4)...")
        # Using Port 465 (SSL) which is standard for Gmail
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=20) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        print(f"EMAIL SUCCESS: Sent to {len(recipients)} recipients.")
        return True
    except Exception as e:
        print(f"EMAIL ERROR: {e}")
        return False


@app.route('/')
def api_root():
    return jsonify({"status": "Server running", "service": "Points Tracker API"}), 200


@app.route('/api/state', methods=['GET'])
def api_get_state():
    state = get_state()
    return jsonify(state)


@app.route('/api/state', methods=['POST'])
def api_update_state():
    try:
        update_state(request.json)
        return jsonify({"status": "success", "message": "State updated."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/state/send-alert', methods=['POST'])
def api_notify():
    try:
        data = request.json
        message_body = data.get('notificationMessage')
        notifications = data.get('notifications', [])
        if not message_body or not notifications:
            return jsonify({"error": "Missing data."}), 400

        success = send_email_notification(message_body, notifications)

        if success:
            return jsonify({"status": "success"})
        else:
            # Return 500 so the frontend knows the email failed
            return jsonify({"error": "Email failed to send. Check server logs."}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    if 'STATE_COLLECTION' in locals():
        app.run(debug=True, port=5000, host='0.0.0.0')