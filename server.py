import os
import time
import sqlite3
import hashlib
import hmac
import secrets

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

DB_FILE = "elvion.db"

TOTAL_SUPPLY = 100_000_000

MINING_REWARD = 1_000
MINING_INTERVAL = 24 * 60 * 60

BOOST_COST = 500_000
BOOST_MULTIPLIER = 2
BOOST_INTERVAL = 12 * 60 * 60

TASK_REWARD = 5_000
TASK_RESET = 24 * 60 * 60

REFERRAL_REWARD = 10_000

# Mining phase: 1 October 2026 -> 1 January 2027
PHASE_END = 1798761600

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").replace("@", "")

TELEGRAM_CHANNEL = "https://t.me/ELVIONOfficial"
TELEGRAM_GROUP = "https://t.me/ELVIONOFFICIALGROUP"


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            balance INTEGER DEFAULT 0,
            mining_started INTEGER DEFAULT 0,
            mining_cycles INTEGER DEFAULT 0,
            boost INTEGER DEFAULT 0,
            tasks_reset INTEGER DEFAULT 0,
            referral_code TEXT UNIQUE,
            referred_by TEXT DEFAULT '',
            referral_rewarded INTEGER DEFAULT 0,
            referral_count INTEGER DEFAULT 0,
            referral_earned INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


init_db()


def make_referral_code(telegram_id):
    return hashlib.sha256(
        str(telegram_id).encode()
    ).hexdigest()[:10]


def get_referral_link(code):
    if not BOT_USERNAME:
        return ""

    return f"https://t.me/{BOT_USERNAME}?start=ref_{code}"


def get_start_param():
    data = request.form.get("initData", "")

    if not data:
        data = request.args.get("initData", "")

    if not data:
        return ""

    for item in data.split("&"):
        if item.startswith("start_param="):
            return item.split("=", 1)[1]

    return ""


def verify_telegram_data(init_data):
    if not init_data or not BOT_TOKEN:
        return None

    try:
        pairs = {}

        for item in init_data.split("&"):
            if "=" not in item:
                continue

            key, value = item.split("=", 1)
            pairs[key] = value

        received_hash = pairs.pop("hash", None)

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={pairs[key]}"
            for key in sorted(pairs)
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
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

        if "user" not in pairs:
            return None

        import json
        return json.loads(pairs["user"])

    except Exception:
        return None


def get_telegram_user():
    init_data = request.form.get("initData", "")

    if not init_data:
        init_data = request.args.get("initData", "")

    user = verify_telegram_data(init_data)

    if user:
        return user

    # Development fallback.
    # Real Telegram Mini App requests should contain valid initData.
    return None


def create_user(telegram_user):
    telegram_id = str(telegram_user.get("id"))

    username = telegram_user.get("username", "")
    first_name = telegram_user.get("first_name", "")

    conn = get_db()

    existing = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE users
            SET username = ?, first_name = ?
            WHERE telegram_id = ?
        """, (
            username,
            first_name,
            telegram_id
        ))

        conn.commit()

        user = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()

        conn.close()

        return user

    referral_code = make_referral_code(telegram_id)

    conn.execute("""
        INSERT INTO users (
            telegram_id,
            username,
            first_name,
            balance,
            mining_started,
            mining_cycles,
            boost,
            tasks_reset,
            referral_code,
            referred_by,
            referral_rewarded,
            referral_count,
            referral_earned
        )
        VALUES (?, ?, ?, 0, ?, 0, 0, ?, ?, '', 0, 0, 0)
    """, (
        telegram_id,
        username,
        first_name,
        int(time.time()),
        int(time.time()),
        referral_code
    ))

    conn.commit()

    start_param = get_start_param()

    if start_param.startswith("ref_"):
        inviter_code = start_param[4:]

        if inviter_code != referral_code:
            inviter = conn.execute(
                "SELECT * FROM users WHERE referral_code = ?",
                (inviter_code,)
            ).fetchone()

            if inviter:
                conn.execute("""
                    UPDATE users
                    SET balance = balance + ?,
                        referral_count = referral_count + 1,
                        referral_earned = referral_earned + ?
                    WHERE telegram_id = ?
                """, (
                    REFERRAL_REWARD,
                    REFERRAL_REWARD,
                    inviter["telegram_id"]
                ))

                conn.execute("""
                    UPDATE users
                    SET referred_by = ?,
                        referral_rewarded = 1
                    WHERE telegram_id = ?
                """, (
                    inviter["telegram_id"],
                    telegram_id
                ))

                conn.commit()

    user = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    conn.close()

    return user


def current_user():
    telegram_user = get_telegram_user()

    if not telegram_user:
        return None

    return create_user(telegram_user)


def user_dict(user):
    if not user:
        return None

    return {
        "telegram_id": user["telegram_id"],
        "username": user["username"],
        "first_name": user["first_name"],
        "balance": user["balance"],
        "mining_started": user["mining_started"],
        "mining_cycles": user["mining_cycles"],
        "boost": user["boost"],
        "tasks_reset": user["tasks_reset"],
        "referral_code": user["referral_code"],
        "referral_count": user["referral_count"],
        "referral_earned": user["referral_earned"],
        "referral_link": get_referral_link(
            user["referral_code"]
        ),
        "phase_end": PHASE_END
    }


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/api/user", methods=["POST"])
def api_user():
    user = current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Invalid Telegram session"
        }), 401

    return jsonify({
        "success": True,
        "user": user_dict(user)
    })


@app.route("/api/energy", methods=["POST"])
def api_energy():
    user = current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Invalid Telegram session"
        }), 401

    now = int(time.time())

    elapsed = now - user["mining_started"]

    interval = (
        BOOST_INTERVAL
        if user["boost"]
        else MINING_INTERVAL
    )

    completed = elapsed // interval

    if completed > 0 and now < PHASE_END:
        conn = get_db()

        reward = (
            MINING_REWARD
            * BOOST_MULTIPLIER
            * completed
            if user["boost"]
            else MINING_REWARD * completed
        )

        conn.execute("""
            UPDATE users
            SET balance = balance + ?,
                mining_started = ?,
                mining_cycles = mining_cycles + ?
            WHERE telegram_id = ?
        """, (
            reward,
            user["mining_started"] + (completed * interval),
            completed,
            user["telegram_id"]
        ))

        conn.commit()

        user = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (user["telegram_id"],)
        ).fetchone()

        conn.close()

    remaining = max(
        0,
        interval - (
            int(time.time()) - user["mining_started"]
        )
    )

    return jsonify({
        "success": True,
        "balance": user["balance"],
        "remaining": remaining,
        "interval": interval,
        "boost": bool(user["boost"]),
        "phase_end": PHASE_END
    })


@app.route("/api/claim", methods=["POST"])
def api_claim():
    user = current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Invalid Telegram session"
        }), 401

    now = int(time.time())

    if now >= PHASE_END:
        return jsonify({
            "success": False,
            "error": "Mining phase has ended"
        }), 400

    interval = (
        BOOST_INTERVAL
        if user["boost"]
        else MINING_INTERVAL
    )

    elapsed = now - user["mining_started"]

    if elapsed < interval:
        return jsonify({
            "success": False,
            "error": "Mining cycle is not complete yet"
        }), 400

    cycles = elapsed // interval

    reward_per_cycle = (
        MINING_REWARD * BOOST_MULTIPLIER
        if user["boost"]
        else MINING_REWARD
    )

    reward = cycles * reward_per_cycle

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET balance = balance + ?,
            mining_started = ?,
            mining_cycles = mining_cycles + ?
        WHERE telegram_id = ?
    """, (
        reward,
        user["mining_started"] + cycles * interval,
        cycles,
        user["telegram_id"]
    ))

    conn.commit()

    updated = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (user["telegram_id"],)
    ).fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "reward": reward,
        "user": user_dict(updated)
    })


@app.route("/api/tasks", methods=["POST"])
def api_tasks():
    user = current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Invalid Telegram session"
        }), 401

    now = int(time.time())

    if now - user["tasks_reset"] >= TASK_RESET:
        conn = get_db()

        conn.execute("""
            UPDATE users
            SET tasks_reset = ?
            WHERE telegram_id = ?
        """, (
            now,
            user["telegram_id"]
        ))

        conn.commit()
        conn.close()

    return jsonify({
        "success": True,
        "tasks": [
            {
                "id": "telegram_channel",
                "title": "Join ELVION Telegram Channel",
                "url": TELEGRAM_CHANNEL,
                "reward": TASK_REWARD
            },
            {
                "id": "telegram_group",
                "title": "Join ELVION Telegram Group",
                "url": TELEGRAM_GROUP,
                "reward": TASK_REWARD
            }
        ],
        "reset": TASK_RESET
    })


@app.route("/api/task/claim", methods=["POST"])
def api_task_claim():
    user = current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Invalid Telegram session"
        }), 401

    data = request.get_json(silent=True) or {}

    task_id = data.get("task_id", "")

    if task_id not in [
        "telegram_channel",
        "telegram_group"
    ]:
        return jsonify({
            "success": False,
            "error": "Invalid task"
        }), 400

    now = int(time.time())

    if now - user["tasks_reset"] < TASK_RESET:
        return jsonify({
            "success": False,
            "error": "Task already claimed"
        }), 400

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET balance = balance + ?,
            tasks_reset = ?
        WHERE telegram_id = ?
    """, (
        TASK_REWARD,
        now,
        user["telegram_id"]
    ))

    conn.commit()

    updated = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (user["telegram_id"],)
    ).fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "reward": TASK_REWARD,
        "user": user_dict(updated)
    })


@app.route("/api/boost", methods=["POST"])
def api_boost():
    user = current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Invalid Telegram session"
        }), 401

    if user["boost"]:
        return jsonify({
            "success": False,
            "error": "Boost already active"
        }), 400

    if user["balance"] < BOOST_COST:
        return jsonify({
            "success": False,
            "error": "Not enough ELVION"
        }), 400

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET balance = balance - ?,
            boost = 1
        WHERE telegram_id = ?
    """, (
        BOOST_COST,
        user["telegram_id"]
    ))

    conn.commit()

    updated = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (user["telegram_id"],)
    ).fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "user": user_dict(updated)
    })


@app.route("/api/referrals", methods=["POST"])
def api_referrals():
    user = current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Invalid Telegram session"
        }), 401

    return jsonify({
        "success": True,
        "count": user["referral_count"],
        "earned": user["referral_earned"],
        "reward": REFERRAL_REWARD,
        "link": get_referral_link(
            user["referral_code"]
        )
    })


@app.route("/api/tokenomics", methods=["GET"])
def api_tokenomics():
    return jsonify({
        "success": True,
        "total_supply": TOTAL_SUPPLY,
        "symbol": "ELVION",
        "phase_start": 1790812800,
        "phase_end": PHASE_END,
        "allocations": [
            {
                "name": "Community & Mining",
                "percentage": 60,
                "amount": 60_000_000
            },
            {
                "name": "Liquidity",
                "percentage": 10,
                "amount": 10_000_000
            },
            {
                "name": "Ecosystem",
                "percentage": 10,
                "amount": 10_000_000
            },
            {
                "name": "Treasury / Reserve",
                "percentage": 8,
                "amount": 8_000_000
            },
            {
                "name": "Team",
                "percentage": 7,
                "amount": 7_000_000
            },
            {
                "name": "Marketing",
                "percentage": 5,
                "amount": 5_000_000
            }
        ]
    })


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "project": "ELVION",
        "time": int(time.time())
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000))
    )
