# -------------------------------------------------------------------------------------
# Python Flask Server for MongoDB/Email-to-Text Backend
# -------------------------------------------------------------------------------------
import os
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime, timezone
import hashlib

# Flask and MongoDB Libraries
from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from dotenv import load_dotenv

# --- Fix for SSL/TLS Handshake Errors ---
import ssl

# We keep this import, but rely on pymongo's internal handling now.
# ----------------------------------------

# Load environment variables from .env file
load_dotenv()

# --- Configuration and Initialization ---

# MongoDB Atlas Setup (Fetched from .env)
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("MONGO_URI environment variable not set. Please check your .env file.")

# Email Credentials (Fetched from .env)
GMAIL_SENDER = os.getenv("GMAIL_SENDER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
    print("WARNING: GMAIL credentials not set. Email notifications will be skipped.")

# MongoDB Connection
# CRITICAL FIX: Removed conflicting tls_cert_reqs and tls_context arguments
try:
    CLIENT = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000,
        tls=True,  # Enable TLS/SSL
        tlsAllowInvalidCertificates=False,
    )
    # Use a dedicated database and collection
    DB = CLIENT.points_tracker_db
    STATE_COLLECTION = DB.app_state

except Exception as e:
    print(f"\n--- CRITICAL MongoDB Initialization Failure ---\nError during MongoClient setup: {e}\n")
    # Exit cleanly if connection fails before app.run
    exit(1)

# --- Flask Server Setup ---
app = Flask(__name__)
CORS(app)  # Enable CORS for frontend communication

# --- Shared Constants (Must match Angular frontend) ---
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
DEFAULT_ADMIN_PASSWORD_HASH = 'a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3'  # SHA-256 hash for "123"
DEFAULT_PIN = '1234'
DEFAULT_THRESHOLD = 10

# --- Default State for First Run ---
DEFAULT_STATE = {
    "scores": {"Lila": 0, "Maryn": 0},
    "currentPin": DEFAULT_PIN,
    "adminPassHash": DEFAULT_ADMIN_PASSWORD_HASH,
    "notifications": [{"phone": "", "carrier": ""}] * 5,
    "changeHistory": {"Lila": [], "Maryn": []},
    "pinThreshold": DEFAULT_THRESHOLD,
    "lastUpdated": datetime.now(timezone.utc).isoformat()
}


# --- Helper Functions ---

def get_state():
    """Retrieves the single application state document from MongoDB."""
    state = STATE_COLLECTION.find_one()
    if state:
        # MongoDB uses '_id' (ObjectID), which needs to be converted for JSON serialization
        state['_id'] = str(state['_id'])
        return state
    else:
        # If no document exists, insert the default state and return it
        STATE_COLLECTION.insert_one(DEFAULT_STATE)
        return DEFAULT_STATE.copy()


def update_state(data):
    """Updates the application state document in MongoDB."""
    # Ensure MongoDB doesn't try to save the synthetic _id field if sent from client
    if '_id' in data:
        del data['_id']

    data['lastUpdated'] = datetime.now(timezone.utc).isoformat()

    # Set upsert=True to create the document if it doesn't exist (initial save)
    STATE_COLLECTION.replace_one({}, data, upsert=True)


# --- Email Notification Function ---

def send_email_notification(message_subject, notifications):
    """Sends email-to-text notifications using stored credentials."""
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        print("EMAIL SKIP: Credentials missing.")
        return

    recipients = []
    for n in notifications:
        phone = n.get('phone')
        carrier = n.get('carrier')

        if phone and carrier:
            # Determine the domain
            domain = CARRIER_GATEWAYS.get(carrier) or carrier

            # Basic sanitation and formatting
            phone = "".join(filter(str.isdigit, str(phone)))
            if len(phone) == 10:
                email_address = f"{phone}@{domain}"
                recipients.append(email_address)

    if not recipients:
        print("EMAIL SKIP: No valid email-to-text recipients configured.")
        return

    msg = EmailMessage()
    msg['Subject'] = "Points Tracker Alert"
    msg['From'] = GMAIL_SENDER
    msg['To'] = ", ".join(recipients)
    msg.set_content(message_subject)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.ehlo()
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        print(f"EMAIL SUCCESS: Notification sent to {len(recipients)} recipients.")
    except smtplib.SMTPAuthenticationError as e:
        print(f"EMAIL ERROR: SMTP Authentication Failed. Check Gmail App Password in .env. Error: {e}")
    except Exception as e:
        print(f"EMAIL ERROR: Failed to send email: {e}")


# --- API Routes ---

@app.route('/')
def api_root():
    """Diagnostic route to confirm the server is running."""
    return jsonify({"status": "Server running", "service": "Points Tracker API"}), 200


@app.route('/api/state', methods=['GET'])
def api_get_state():
    """Endpoint for the client to retrieve the application state on load."""
    state = get_state()
    # Flask/MongoDB handles the primary data, no notification is sent here.
    return jsonify(state)


@app.route('/api/state', methods=['POST'])
def api_update_state():
    """Endpoint for the client to push the full state update (scores, pin, admin settings)."""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON payload provided."}), 400

        # Update MongoDB
        update_state(data)

        return jsonify({"status": "success", "message": "State updated."})
    except Exception as e:
        print(f"Error processing state update: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/state/notify', methods=['POST'])
def api_notify():
    """Endpoint triggered by the client specifically for notifications."""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON payload provided."}), 400

        # The Angular client sends the necessary message and notification list here
        message_body = data.get('notificationMessage')
        notifications = data.get('notifications', [])

        if not message_body or not notifications:
            return jsonify({"error": "Missing notificationMessage or recipients."}), 400

        # Trigger the email sending function
        send_email_notification(message_body, notifications)

        return jsonify({"status": "success", "message": "Notification triggered."})

    except Exception as e:
        print(f"Error processing notification trigger: {e}")
        return jsonify({"error": str(e)}), 500


# --- Run Server ---

if __name__ == '__main__':
    # Initial check (already attempted in the setup above, so we skip the repetitive check here)

    # We must ensure the Flask app is only run if the connection attempt was successful
    # Note: We rely on the try/except block above setting up the STATE_COLLECTION global.
    if 'STATE_COLLECTION' in locals():
        print("\n--- Starting Flask Server ---\n")
        # CRITICAL FIX: Run on 0.0.0.0 to allow access from the external Canvas host
        app.run(debug=True, port=5000, host='0.0.0.0')
    else:
        print("\n--- Failed to start Flask Server due to MongoDB initialization error. Check logs above. ---\n")
        # The initialization attempt already called exit(1) if it failed in the setup try/except block.
        pass