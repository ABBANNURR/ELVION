import os
import time
import json
import hmac
import hashlib
import sqlite3
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

DB_NAME = "elvion.db"

MAX_ENERGY = 2000
ENERGY_RESTORE_SECONDS = 3
CLAIM_INTERVAL = 24 * 60 * 60
DEFAULT_MINING_RATE = 1
BOOST_COST = 500_000
BOOST_MULTIPLIER = 2


def get_db():
    db = sqlite3.connect(DB_NAME)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            balance INTEGER DEFAULT 0,
            energy INTEGER DEFAULT 2000,
            mining_rate INTEGER DEFAULT 1,
            last_energy_update INTEGER DEFAULT 0,
            last_claim INTEGER DEFAULT 0,
            boosted INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0
        )
    """)

    db.commit()
    db.close()


def verify_telegram_init_data(init_data):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")

    if not bot_token:
        return None

    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = data.pop("hash", None)

        if not received_hash:
            return None

        auth_date = int(data.get("auth_date", 0))

        if time.time() - auth_date > 86400:
            return None

        data_check_string = "\n".join(
            f"{key}={data[key]}"
            for key in sorted(data)
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

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        user = json.loads(data["user"])

        return user

    except Exception:
        return None


def restore_energy(row):
    now = int(time.time())

    energy = row["energy"]
    last_update = row["last_energy_update"]

    if last_update <= 0:
        return energy

    elapsed = now - last_update
    restored = elapsed // ENERGY_RESTORE_SECONDS

    if restored <= 0:
        return energy

    energy = min(
        MAX_ENERGY,
        energy + restored
    )

    return energy


def create_or_get_user(user):
    db = get_db()

    telegram_id = int(user["id"])
    now = int(time.time())

    existing = db.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if existing:
        db.execute("""
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

        db.commit()

        row = db.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()

        db.close()
        return row

    db.execute("""
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
            boosted,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        0,
        now,
        now
    ))

    db.commit()

    row = db.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    db.close()

    return row


def authenticate():
    data = request.get_json(silent=True) or {}

    init_data = (
        data.get("initData")
        or request.args.get("initData")
        or request.headers.get("X-Telegram-Init-Data")
    )

    if not init_data:
        return None

    return verify_telegram_init_data(init_data)


@app.route("/")
def home():
    return send_file("index.html")


@app.route("/health")
def health():
    return jsonify({
        "status": "online",
        "project": "ELVION"
    })


@app.route("/api/user", methods=["GET", "POST"])
def api_user():
    user = authenticate()

    if not user:
        return jsonify({
            "error": "Telegram authentication failed"
        }), 401

    row = create_or_get_user(user)

    energy = restore_energy(row)

    db = get_db()

    db.execute("""
        UPDATE users
        SET energy = ?,
            last_energy_update = ?,
            updated_at = ?
        WHERE telegram_id = ?
    """, (
        energy,
        int(time.time()),
        int(time.time()),
        int(user["id"])
    ))

    db.commit()
    db.close()

    return jsonify({
        "ok": True,
        "user": {
            "id": int(user["id"]),
            "username": user.get("username", ""),
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", "")
        },
        "balance": row["balance"],
        "energy": energy,
        "max_energy": MAX_ENERGY,
        "mining_rate": row["mining_rate"],
        "last_claim": row["last_claim"],
        "boosted": bool(row["boosted"])
    })


@app.route("/api/tap", methods=["POST"])
def api_tap():
    user = authenticate()

    if not user:
        return jsonify({
            "error": "Telegram authentication failed"
        }), 401

    telegram_id = int(user["id"])

    db = get_db()

    row = db.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if not row:
        row = create_or_get_user(user)

    energy = restore_energy(row)

    if energy <= 0:
        db.close()

        return jsonify({
            "error": "No energy",
            "energy": 0
        }), 400

    reward = int(row["mining_rate"])
    new_balance = int(row["balance"]) + reward
    new_energy = energy - 1
    now = int(time.time())

    db.execute("""
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
        telegram_id
    ))

    db.commit()
    db.close()

    return jsonify({
        "ok": True,
        "balance": new_balance,
        "energy": new_energy,
        "reward": reward
    })


@app.route("/api/claim", methods=["POST"])
def api_claim():
    user = authenticate()

    if not user:
        return jsonify({
            "error": "Telegram authentication failed"
        }), 401

    telegram_id = int(user["id"])
    now = int(time.time())

    db = get_db()

    row = db.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if not row:
        row = create_or_get_user(user)

    last_claim = int(row["last_claim"])

    if last_claim and now - last_claim < CLAIM_INTERVAL:
        remaining = CLAIM_INTERVAL - (now - last_claim)

        db.close()

        return jsonify({
            "ok": False,
            "claimed": False,
            "remaining": remaining
        }), 400

    claim_reward = 10_000

    new_balance = int(row["balance"]) + claim_reward

    db.execute("""
        UPDATE users
        SET balance = ?,
            last_claim = ?,
            updated_at = ?
        WHERE telegram_id = ?
    """, (
        new_balance,
        now,
        now,
        telegram_id
    ))

    db.commit()
    db.close()

    return jsonify({
        "ok": True,
        "claimed": True,
        "reward": claim_reward,
        "balance": new_balance,
        "last_claim": now
    })


@app.route("/api/boost", methods=["POST"])
def api_boost():
    user = authenticate()

    if not user:
        return jsonify({
            "error": "Telegram authentication failed"
        }), 401

    telegram_id = int(user["id"])

    db = get_db()

    row = db.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if not row:
        row = create_or_get_user(user)

    if row["boosted"]:
        db.close()

        return jsonify({
            "error": "Boost already active"
        }), 400

    if int(row["balance"]) < BOOST_COST:
        db.close()

        return jsonify({
            "error": "Not enough ELVION"
        }), 400

    new_balance = int(row["balance"]) - BOOST_COST
    new_rate = int(row["mining_rate"]) * BOOST_MULTIPLIER

    db.execute("""
        UPDATE users
        SET balance = ?,
            mining_rate = ?,
            boosted = 1,
            updated_at = ?
        WHERE telegram_id = ?
    """, (
        new_balance,
        new_rate,
        int(time.time()),
        telegram_id
    ))

    db.commit()
    db.close()

    return jsonify({
        "ok": True,
        "balance": new_balance,
        "mining_rate": new_rate,
        "boosted": True
    })


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )
