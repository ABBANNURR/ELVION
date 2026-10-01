import os
import time
import json
import hmac
import hashlib
import sqlite3
import secrets
import urllib.parse
import urllib.request

from flask import Flask, request, jsonify, send_file, Response

app = Flask(__name__)

DB_FILE = "elvion.db"

TOTAL_SUPPLY = 100_000_000
MINING_REWARD = 1_000
MINING_INTERVAL = 24 * 60 * 60

BOOST_COST = 500_000
BOOST_INTERVAL = 12 * 60 * 60

TASK_REWARD = 5_000
REFERRAL_REWARD = 10_000

PHASE_START = 1790812800
PHASE_END = 1798761600

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().replace("@", "")


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id TEXT UNIQUE NOT NULL,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        balance INTEGER DEFAULT 0,
        mining_started INTEGER DEFAULT 0,
        mining_cycles INTEGER DEFAULT 0,
        boost INTEGER DEFAULT 0,
        referral_code TEXT UNIQUE,
        referred_by TEXT DEFAULT '',
        referral_count INTEGER DEFAULT 0,
        referral_earned INTEGER DEFAULT 0,
        wallet_address TEXT DEFAULT ''
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS task_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        claimed_at INTEGER NOT NULL,
        UNIQUE(telegram_id, task_id)
    )
    """)

    conn.commit()
    conn.close()


init_db()


def get_bot_username():
    global BOT_USERNAME

    if BOT_USERNAME:
        return BOT_USERNAME

    if not BOT_TOKEN:
        return ""

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"

        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())

        if data.get("ok"):
            BOT_USERNAME = data["result"].get("username", "")
            return BOT_USERNAME

    except Exception as e:
        print("Bot username error:", e)

    return ""


def get_init_data():
    value = request.args.get("initData", "")
    if value:
        return value

    value = request.form.get("initData", "")
    if value:
        return value

    try:
        body = request.get_json(silent=True) or {}
        return body.get("initData", "")
    except Exception:
        return ""


def verify_telegram(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    try:
        parsed = urllib.parse.parse_qs(
            init_data,
            keep_blank_values=True
        )

        received_hash = parsed.get("hash", [""])[0]

        if not received_hash:
            return None

        check = []

        for key in sorted(parsed.keys()):
            if key == "hash":
                continue

            check.append(
                f"{key}={parsed[key][0]}"
            )

        data_check_string = "\n".join(check)

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

        user_raw = parsed.get("user", [""])[0]

        if not user_raw:
            return None

        user = json.loads(user_raw)

        user["_start_param"] = parsed.get(
            "start_param",
            [""]
        )[0]

        return user

    except Exception as e:
        print("Telegram auth error:", e)
        return None


def telegram_user():
    return verify_telegram(get_init_data())


def create_user(tg):
    telegram_id = str(tg["id"])
    username = tg.get("username", "")
    first_name = tg.get("first_name", "")
    start_param = tg.get("_start_param", "")

    now = int(time.time())

    conn = db()

    user = conn.execute(
        "SELECT * FROM users WHERE telegram_id=?",
        (telegram_id,)
    ).fetchone()

    if user is None:

        referral_code = secrets.token_hex(5)

        conn.execute("""
        INSERT INTO users (
            telegram_id,
            username,
            first_name,
            balance,
            mining_started,
            mining_cycles,
            boost,
            referral_code
        )
        VALUES (?, ?, ?, 0, ?, 0, 0, ?)
        """, (
            telegram_id,
            username,
            first_name,
            now,
            referral_code
        ))

        conn.commit()

        if start_param.startswith("ref_"):

            ref_code = start_param[4:]

            inviter = conn.execute(
                "SELECT * FROM users WHERE referral_code=?",
                (ref_code,)
            ).fetchone()

            if inviter and str(inviter["telegram_id"]) != telegram_id:

                conn.execute("""
                UPDATE users
                SET balance = balance + ?,
                    referral_count = referral_count + 1,
                    referral_earned = referral_earned + ?
                WHERE telegram_id=?
                """, (
                    REFERRAL_REWARD,
                    REFERRAL_REWARD,
                    inviter["telegram_id"]
                ))

                conn.execute("""
                UPDATE users
                SET referred_by=?
                WHERE telegram_id=?
                """, (
                    inviter["telegram_id"],
                    telegram_id
                ))

                conn.commit()

    else:

        conn.execute("""
        UPDATE users
        SET username=?,
            first_name=?
        WHERE telegram_id=?
        """, (
            username,
            first_name,
            telegram_id
        ))

        conn.commit()

    user = conn.execute(
        "SELECT * FROM users WHERE telegram_id=?",
        (telegram_id,)
    ).fetchone()

    conn.close()

    return user


def mining_info(user):
    now = int(time.time())

    started = int(user["mining_started"])

    interval = (
        12 * 60 * 60
        if int(user["boost"]) == 1
        else MINING_INTERVAL
    )

    next_claim = started + interval

    if now >= PHASE_END:
        return {
            "active": False,
            "completed": True,
            "next_claim": PHASE_END,
            "seconds_left": 0,
            "interval": interval
        }

    seconds_left = max(
        0,
        next_claim - now
    )

    return {
        "active": True,
        "completed": seconds_left == 0,
        "next_claim": next_claim,
        "seconds_left": seconds_left,
        "interval": interval
    }


TASKS = {
    "telegram_channel": {
        "id": "telegram_channel",
        "name": "Join ELVION Telegram Channel",
        "url": "https://t.me/ELVIONOfficial",
        "reward": TASK_REWARD
    },
    "telegram_group": {
        "id": "telegram_group",
        "name": "Join ELVION Telegram Group",
        "url": "https://t.me/ELVIONOFFICIALGROUP",
        "reward": TASK_REWARD
    }
}


@app.route("/")
def home():
    return send_file("index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "success": True,
        "status": "online",
        "project": "ELVION",
        "telegram_auth": bool(BOT_TOKEN),
        "bot_username": get_bot_username(),
        "time": int(time.time())
    })


@app.route("/api/user")
def user_api():

    tg = telegram_user()

    if not tg:
        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_user(tg)
    mining = mining_info(user)

    return jsonify({
        "success": True,
        "user": {
            "telegram_id": user["telegram_id"],
            "username": user["username"],
            "first_name": user["first_name"],
            "balance": user["balance"],
            "mining_cycles": user["mining_cycles"],
            "boost": user["boost"],
            "referral_code": user["referral_code"],
            "referral_count": user["referral_count"],
            "referral_earned": user["referral_earned"],
            "wallet_address": user["wallet_address"]
        },
        "mining": mining
    })


@app.route("/api/claim", methods=["POST"])
def claim():

    tg = telegram_user()

    if not tg:
        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_user(tg)
    info = mining_info(user)

    if int(time.time()) >= PHASE_END:
        return jsonify({
            "success": False,
            "error": "Mining phase has ended"
        })

    if info["seconds_left"] > 0:
        return jsonify({
            "success": False,
            "error": "Mining cycle is not complete",
            "seconds_left": info["seconds_left"]
        })

    now = int(time.time())

    interval = (
        12 * 60 * 60
        if int(user["boost"]) == 1
        else MINING_INTERVAL
    )

    new_balance = int(user["balance"]) + MINING_REWARD
    new_cycles = int(user["mining_cycles"]) + 1

    conn = db()

    conn.execute("""
    UPDATE users
    SET balance=?,
        mining_started=?,
        mining_cycles=?
    WHERE telegram_id=?
    """, (
        new_balance,
        now,
        new_cycles,
        user["telegram_id"]
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "reward": MINING_REWARD,
        "balance": new_balance,
        "mining_cycles": new_cycles,
        "next_claim": now + interval,
        "mining_end": now + interval
    })


@app.route("/api/tasks")
def tasks_api():

    tg = telegram_user()

    if not tg:
        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_user(tg)

    conn = db()

    result = []

    for task_id, task in TASKS.items():

        row = conn.execute("""
        SELECT id FROM task_claims
        WHERE telegram_id=? AND task_id=?
        """, (
            user["telegram_id"],
            task_id
        )).fetchone()

        result.append({
            **task,
            "claimed": row is not None
        })

    conn.close()

    return jsonify({
        "success": True,
        "tasks": result
    })


@app.route("/api/task/claim", methods=["POST"])
def task_claim():

    tg = telegram_user()

    if not tg:
        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_user(tg)

    body = request.get_json(silent=True) or {}
    task_id = body.get("task_id")

    if task_id not in TASKS:
        return jsonify({
            "success": False,
            "error": "Invalid task"
        })

    conn = db()

    exists = conn.execute("""
    SELECT id FROM task_claims
    WHERE telegram_id=? AND task_id=?
    """, (
        user["telegram_id"],
        task_id
    )).fetchone()

    if exists:
        conn.close()
        return jsonify({
            "success": False,
            "error": "Task already claimed"
        })

    reward = TASKS[task_id]["reward"]

    conn.execute("""
    INSERT INTO task_claims (
        telegram_id,
        task_id,
        claimed_at
    )
    VALUES (?, ?, ?)
    """, (
        user["telegram_id"],
        task_id,
        int(time.time())
    ))

    conn.execute("""
    UPDATE users
    SET balance=balance+?
    WHERE telegram_id=?
    """, (
        reward,
        user["telegram_id"]
    ))

    conn.commit()

    balance = conn.execute(
        "SELECT balance FROM users WHERE telegram_id=?",
        (user["telegram_id"],)
    ).fetchone()["balance"]

    conn.close()

    return jsonify({
        "success": True,
        "reward": reward,
        "balance": balance
    })


@app.route("/api/boost", methods=["POST"])
def boost():

    tg = telegram_user()

    if not tg:
        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_user(tg)

    if int(user["boost"]) == 1:
        return jsonify({
            "success": False,
            "error": "Boost already active"
        })

    if int(user["balance"]) < BOOST_COST:
        return jsonify({
            "success": False,
            "error": "You need 500,000 ELVION"
        })

    conn = db()

    conn.execute("""
    UPDATE users
    SET balance=balance-?,
        boost=1
    WHERE telegram_id=?
    """, (
        BOOST_COST,
        user["telegram_id"]
    ))

    conn.commit()

    balance = conn.execute(
        "SELECT balance FROM users WHERE telegram_id=?",
        (user["telegram_id"],)
    ).fetchone()["balance"]

    conn.close()

    return jsonify({
        "success": True,
        "boost": 1,
        "balance": balance
    })


@app.route("/api/referrals")
def referrals():

    tg = telegram_user()

    if not tg:
        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_user(tg)
    username = get_bot_username()

    link = ""

    if username:
        link = (
            f"https://t.me/{username}"
            f"?startapp=ref_{user['referral_code']}"
        )

    return jsonify({
        "success": True,
        "referral_code": user["referral_code"],
        "referral_link": link,
        "referral_count": user["referral_count"],
        "referral_earned": user["referral_earned"],
        "reward": REFERRAL_REWARD
    })


@app.route("/api/wallet", methods=["POST"])
def wallet():

    tg = telegram_user()

    if not tg:
        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_user(tg)

    body = request.get_json(silent=True) or {}
    address = str(
        body.get("wallet_address", "")
    ).strip()

    if not address:
        return jsonify({
            "success": False,
            "error": "Wallet address missing"
        })

    conn = db()

    conn.execute("""
    UPDATE users
    SET wallet_address=?
    WHERE telegram_id=?
    """, (
        address,
        user["telegram_id"]
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "wallet_address": address
    })


@app.route("/api/tokenomics")
def tokenomics():

    return jsonify({
        "success": True,
        "total_supply": TOTAL_SUPPLY,
        "distribution": {
            "Community & Mining": 60,
            "Liquidity": 10,
            "Ecosystem": 10,
            "Treasury / Reserve": 8,
            "Team": 7,
            "Marketing": 5
        }
    })


@app.route("/tonconnect-manifest.json")
def manifest():

    return Response(
        json.dumps({
            "url": "https://elvion-tyb7.onrender.com",
            "name": "ELVION",
            "iconUrl": "https://elvion-tyb7.onrender.com/icon.png"
        }),
        mimetype="application/json"
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
