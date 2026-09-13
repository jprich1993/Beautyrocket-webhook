import os
import sqlite3
import requests
import psycopg
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

SQLITE_DATABASE_FILE = "greeted_users.db"

INITIAL_GREETING = (
    "Hi! 👋💕 I’m glad you’re enjoying my content! "
    "Let’s be online friends! Feel free to follow me, "
    "ask me questions, or send me suggestions for what "
    "I should film next! 🎥✨"
)


def get_database_connection():
    if DATABASE_URL:
        return psycopg.connect(DATABASE_URL)

    return sqlite3.connect(SQLITE_DATABASE_FILE)


def initialize_database():
    connection = get_database_connection()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS greeted_users (
            instagram_user_id TEXT PRIMARY KEY,
            greeted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    connection.commit()
    connection.close()

    if DATABASE_URL:
        print("PostgreSQL database initialized successfully.")
    else:
        print("SQLite database initialized successfully.")


def has_been_greeted(instagram_user_id):
    connection = get_database_connection()

    if DATABASE_URL:
        result = connection.execute(
            """
            SELECT instagram_user_id
            FROM greeted_users
            WHERE instagram_user_id = %s
            """,
            (instagram_user_id,),
        ).fetchone()
    else:
        result = connection.execute(
            """
            SELECT instagram_user_id
            FROM greeted_users
            WHERE instagram_user_id = ?
            """,
            (instagram_user_id,),
        ).fetchone()

    connection.close()

    return result is not None


def mark_as_greeted(instagram_user_id):
    connection = get_database_connection()

    if DATABASE_URL:
        connection.execute(
            """
            INSERT INTO greeted_users (instagram_user_id)
            VALUES (%s)
            ON CONFLICT (instagram_user_id) DO NOTHING
            """,
            (instagram_user_id,),
        )
    else:
        connection.execute(
            """
            INSERT OR IGNORE INTO greeted_users (instagram_user_id)
            VALUES (?)
            """,
            (instagram_user_id,),
        )

    connection.commit()
    connection.close()


def send_instagram_message(recipient_id, text):
    url = "https://graph.instagram.com/v24.0/me/messages"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30,
    )

    print("Instagram API status:", response.status_code)
    print("Instagram API response:", response.text)

    return response.ok


def get_messaging_events(entry):
    """
    Support both Instagram webhook message formats:

    1. entry[].messaging[]
       Used by normal Instagram messaging events.

    2. entry[].changes[].value
       Used by Meta's dashboard webhook test.
    """

    events = list(entry.get("messaging", []))

    for change in entry.get("changes", []):
        if change.get("field") == "messages":
            value = change.get("value", {})

            if value:
                events.append(value)

    return events


@app.get("/")
def health_check():
    return "Beautyrocket webhook is running."


@app.get("/webhook")
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("Webhook verified successfully.")
        return challenge, 200

    print("Webhook verification failed.")
    return "Forbidden", 403


@app.post("/webhook")
def receive_webhook():
    data = request.get_json(silent=True) or {}

    print("Incoming webhook:")
    print(data)

    if data.get("object") != "instagram":
        return "", 200

    for entry in data.get("entry", []):

        for messaging_event in get_messaging_events(entry):

            message = messaging_event.get("message", {})

            if message.get("is_echo"):
                print("Ignoring own outgoing message.")
                continue

            sender = messaging_event.get("sender", {})
            sender_id = sender.get("id")
            message_text = message.get("text")

            if not sender_id:
                print("No sender ID found.")
                continue

            print(
                f"Incoming message from {sender_id}: "
                f"{message_text or '[non-text message]'}"
            )

            if has_been_greeted(sender_id):
                print(
                    "Conversation already greeted; "
                    "waiting for Sheila."
                )
                continue

            print(f"Sending initial greeting to {sender_id}.")

            message_sent = send_instagram_message(
                sender_id,
                INITIAL_GREETING,
            )

            if message_sent:
                mark_as_greeted(sender_id)
                print(f"User {sender_id} marked as greeted.")
            else:
                print(
                    f"Greeting failed for {sender_id}; "
                    "user was not marked as greeted."
                )

    return "", 200


# Initialize the database when the application starts.
# This is required for both local Python execution and
# production servers such as Gunicorn on Render.
initialize_database()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )