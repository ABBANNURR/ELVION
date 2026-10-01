import os
import time
import hmac
import hashlib
import json
import sqlite3
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify


# ==========================================
# ELVION BACKEND
# ==========================================

app = Flask(__name__)

DATABASE = "elvion.db"

MAX_ENERGY = 2000
ENERGY_RESTORE_SECONDS = 3
CLAIM_INTERVAL = 24 * 60 * 60

DEFAULT_MINING_RATE = 1
BOOST_COST = 500_000


# ==========================================
# DATABASE
# ==========================================

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            balance INTEGER DEFAULT 0,
            energy INTEGER DEFAULT 2000,
            mining_rate INTEGER DEFAULT 1,
            last_energy_update INTEGER DEFAULT 0,
            last_claim INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


init_database()


# ==========================================
# TELEGRAM SECURITY
# ==========================================

def verify_telegram_init_data(init_data):

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")

    if not bot_token:
        return None

    try:

        parsed = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = parsed.pop("hash", None)

        if not received_hash:
            return None

        auth_date = parsed.get("auth_date")

        if not auth_date:
            return None

        # Prevent very old login data
        if int(time.time()) - int(auth_date) > 86400:
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(parsed.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        user_data = parsed.get("user")

        if not user_data:
            return None

        return json.loads(user_data)

    except Exception:
        return None


# ==========================================
# USER DATABASE
# ==========================================

def create_or_get_user(user):

    telegram_id = int(user["id"])

    now = int(time.time())

    conn = get_db()

    existing = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if existing:

        conn.execute("""
            UPDATE users
            SET username = ?,
                first_name = ?,
                last_name = ?,
                updated_at = ?
            WHERE telegram_id = ?
        """, (
            user.get("username", ""),
            user.get("first_name", ""),
            user.get("last_name", ""),
            now,
            telegram_id
        ))

    else:

        conn.execute("""
            INSERT INTO users (
                telegram_id,
                username,
                first_name,
                last_name,
                balance,
                energy,
                mining_rate,
                last_energy_update,
                last_claim,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            telegram_id,
            user.get("username", ""),
            user.get("first_name", ""),
            user.get("last_name", ""),
            0,
            MAX_ENERGY,
            DEFAULT_MINING_RATE,
            now,
            0,
            now,
            now
        ))

    conn.commit()

    row = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    conn.close()

    return row


# ==========================================
# ENERGY RECOVERY
# ==========================================

def restore_energy(user):

    now = int(time.time())

    energy = user["energy"]
    last_update = user["last_energy_update"]

    if energy >= MAX_ENERGY:
        return energy

    elapsed = now - last_update

    if elapsed < ENERGY_RESTORE_SECONDS:
        return energy

    restored = elapsed // ENERGY_RESTORE_SECONDS

    new_energy = min(
        MAX_ENERGY,
        energy + restored
    )

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET energy = ?,
            last_energy_update = ?,
            updated_at = ?
        WHERE telegram_id = ?
    """, (
        new_energy,
        now,
        now,
        user["telegram_id"]
    ))

    conn.commit()
    conn.close()

    return new_energy


# ==========================================
# AUTHENTICATION HELPER
# ==========================================

def authenticate():

    data = request.get_json(silent=True) or {}

    init_data = data.get("initData")

    if not init_data:

        init_data = request.args.get("initData")

    if not init_data:
        return None

    user = verify_telegram_init_data(init_data)

    if not user:
        return None

    return user


# ==========================================
# HOME
# ==========================================

@app.route("/")
def home():

    return jsonify({
        "project": "ELVION",
        "status": "online",
        "message": "ELVION backend is running"
    })


# ==========================================
# USER API
# ==========================================

@app.route("/api/user", methods=["GET"])
def get_user():

    user = authenticate()

    if not user:

        return jsonify({
            "success": False,
            "error": "Invalid Telegram authentication"
        }), 401

    db_user = create_or_get_user(user)

    energy = restore_energy(db_user)

    conn = get_db()

    fresh_user = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (int(user["id"]),)
    ).fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "user": {
            "telegram_id": fresh_user["telegram_id"],
            "username": fresh_user["username"],
            "first_name": fresh_user["first_name"],
            "last_name": fresh_user["last_name"]
        },
        "balance": fresh_user["balance"],
        "energy": energy,
        "mining_rate": fresh_user["mining_rate"],
        "last_claim": fresh_user["last_claim"]
    })


# ==========================================
# TAP / MINING
# ==========================================

@app.route("/api/tap", methods=["POST"])
def tap():

    user = authenticate()

    if not user:

        return jsonify({
            "success": False,
            "error": "Invalid Telegram authentication"
        }), 401

    db_user = create_or_get_user(user)

    energy = restore_energy(db_user)

    if energy <= 0:

        return jsonify({
            "success": False,
            "error": "No energy",
            "energy": 0
        }), 400

    conn = get_db()

    fresh_user = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (int(user["id"]),)
    ).fetchone()

    reward = fresh_user["mining_rate"]

    new_balance = (
        fresh_user["balance"] + reward
    )

    new_energy = (
        fresh_user["energy"] - 1
    )

    now = int(time.time())

    conn.execute("""
        UPDATE users
        SET balance = ?,
            energy = ?,
            last_energy_update = ?,
            updated_at = ?
        WHERE telegram_id = ?
    """, (
        new_balance,
        new_energy,
        now,
        now,
        int(user["id"])
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "reward": reward,
        "balance": new_balance,
        "energy": new_energy
    })


# ==========================================
# CLAIM
# ==========================================

@app.route("/api/claim", methods=["POST"])
def claim():

    user = authenticate()

    if not user:

        return jsonify({
            "success": False,
            "error": "Invalid Telegram authentication"
        }), 401

    db_user = create_or_get_user(user)

    now = int(time.time())

    last_claim = db_user["last_claim"]

    if last_claim:

        elapsed = now - last_claim

        if elapsed < CLAIM_INTERVAL:

            remaining = CLAIM_INTERVAL - elapsed

            return jsonify({
                "success": False,
                "error": "Claim is not ready",
                "remaining": remaining
            }), 400

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET last_claim = ?,
            updated_at = ?
        WHERE telegram_id = ?
    """, (
        now,
        now,
        int(user["id"])
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "message": "24-hour claim completed",
        "claimed_at": now
    })


# ==========================================
# HEALTH CHECK
# ==========================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "project": "ELVION"
    })


# ==========================================
# RUN SERVER
# ==========================================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    print("")
    print("================================")
    print("        ELVION BACKEND")
    print("================================")
    print("Server starting...")
    print("Database: elvion.db")
    print("Telegram authentication: ENABLED")
    print("24-hour claiming: ENABLED")
    print("Mining system: ENABLED")
    print("================================")
    print("")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
  )
