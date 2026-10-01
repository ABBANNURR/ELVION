import os
import time
import sqlite3
import hashlib
import hmac
import json
import urllib.parse

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

# =========================
# CONFIG
# =========================

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

# 1 October 2026 -> 1 January 2027
PHASE_START = 1790812800
PHASE_END = 1798761600

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().replace("@", "")

TELEGRAM_CHANNEL = "https://t.me/ELVIONOfficial"
TELEGRAM_GROUP = "https://t.me/ELVIONOFFICIALGROUP"

TASKS = {
    "telegram_channel": {
        "title": "Join ELVION Telegram Channel",
        "url": TELEGRAM_CHANNEL,
        "reward": TASK_REWARD
    },
    "telegram_group": {
        "title": "Join ELVION Telegram Group",
        "url": TELEGRAM_GROUP,
        "reward": TASK_REWARD
    }
}


# =========================
# DATABASE
# =========================

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
            referral_code TEXT UNIQUE,
            referred_by TEXT DEFAULT '',
            referral_rewarded INTEGER DEFAULT 0,
            referral_count INTEGER DEFAULT 0,
            referral_earned INTEGER DEFAULT 0
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


# =========================
# TELEGRAM AUTH
# =========================

def get_init_data():

    init_data = request.args.get("initData", "")

    if not init_data:
        init_data = request.form.get("initData", "")

    if not init_data:
        try:
            body = request.get_json(silent=True) or {}
            init_data = body.get("initData", "")
        except Exception:
            pass

    return init_data


def verify_telegram_init_data(init_data):

    if not init_data:
        return None

    if not BOT_TOKEN:
        return None

    try:

        parsed = urllib.parse.parse_qsl(
            init_data,
            keep_blank_values=True
        )

        data = dict(parsed)

        received_hash = data.pop("hash", None)

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={data[key]}"
            for key in sorted(data.keys())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode("utf-8"),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        user_json = data.get("user")

        if not user_json:
            return None

        return json.loads(user_json)

    except Exception:
        return None


def get_telegram_user():

    init_data = get_init_data()

    return verify_telegram_init_data(init_data)


# =========================
# REFERRAL
# =========================

def make_referral_code(telegram_id):

    return hashlib.sha256(
        str(telegram_id).encode("utf-8")
    ).hexdigest()[:10]


def get_start_param():

    init_data = get_init_data()

    if not init_data:
        return ""

    try:

        parsed = urllib.parse.parse_qs(init_data)

        return parsed.get(
            "start_param",
            [""]
        )[0]

    except Exception:

        return ""


def referral_link(code):

    if not BOT_USERNAME:
        return ""

    return f"https://t.me/{BOT_USERNAME}?startapp=ref_{code}"


# =========================
# USER
# =========================

def get_user(telegram_id):

    conn = get_db()

    user = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (str(telegram_id),)
    ).fetchone()

    conn.close()

    return user


def create_or_update_user(telegram_user):

    telegram_id = str(telegram_user.get("id"))

    username = telegram_user.get(
        "username",
        ""
    )

    first_name = telegram_user.get(
        "first_name",
        ""
    )

    conn = get_db()

    existing = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()

    if existing:

        conn.execute("""
            UPDATE users
            SET username = ?,
                first_name = ?
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

    code = make_referral_code(telegram_id)

    now = int(time.time())

    conn.execute("""
        INSERT INTO users (
            telegram_id,
            username,
            first_name,
            balance,
            mining_started,
            mining_cycles,
            boost,
            referral_code,
            referred_by,
            referral_rewarded,
            referral_count,
            referral_earned
        )
        VALUES (
            ?, ?, ?, 0, ?, 0, 0, ?, '', 0, 0, 0
        )
    """, (
        telegram_id,
        username,
        first_name,
        now,
        code
    ))

    conn.commit()

    # Process referral
    start_param = get_start_param()

    if start_param.startswith("ref_"):

        inviter_code = start_param[4:]

        if inviter_code != code:

            inviter = conn.execute(
                """
                SELECT *
                FROM users
                WHERE referral_code = ?
                """,
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

    return create_or_update_user(
        telegram_user
    )


def user_json(user):

    if not user:
        return None

    return {
        "telegram_id": str(user["telegram_id"]),
        "username": user["username"] or "",
        "first_name": user["first_name"] or "",
        "balance": int(user["balance"]),
        "mining_started": int(user["mining_started"]),
        "mining_cycles": int(user["mining_cycles"]),
        "boost": bool(user["boost"]),
        "referral_code": user["referral_code"],
        "referral_count": int(user["referral_count"]),
        "referral_earned": int(user["referral_earned"]),
        "referral_link": referral_link(
            user["referral_code"]
        ),
        "phase_end": PHASE_END
    }


# =========================
# BASIC ROUTES
# =========================

@app.route("/")
def index():

    return send_from_directory(
        ".",
        "index.html"
    )


@app.route("/api/health")
def health():

    return jsonify({
        "success": True,
        "status": "online",
        "project": "ELVION",
        "telegram_auth": bool(BOT_TOKEN),
        "time": int(time.time())
    })


# =========================
# USER API
# =========================

@app.route("/api/user", methods=["POST", "GET"])
def api_user():

    user = current_user()

    if not user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    return jsonify({
        "success": True,
        "user": user_json(user)
    })


# =========================
# MINING
# =========================

@app.route("/api/energy", methods=["POST", "GET"])
def api_energy():

    user = current_user()

    if not user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    now = int(time.time())

    interval = (
        BOOST_INTERVAL
        if user["boost"]
        else MINING_INTERVAL
    )

    elapsed = now - int(
        user["mining_started"]
    )

    remaining = max(
        0,
        interval - elapsed
    )

    return jsonify({
        "success": True,
        "balance": int(user["balance"]),
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
            "error": "Telegram authentication failed"
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

    elapsed = now - int(
        user["mining_started"]
    )

    cycles = elapsed // interval

    if cycles < 1:

        return jsonify({
            "success": False,
            "error": "Mining cycle is not complete yet"
        }), 400

    reward_per_cycle = (
        MINING_REWARD * BOOST_MULTIPLIER
        if user["boost"]
        else MINING_REWARD
    )

    reward = cycles * reward_per_cycle

    new_start = (
        int(user["mining_started"])
        + cycles * interval
    )

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET balance = balance + ?,
            mining_started = ?,
            mining_cycles = mining_cycles + ?
        WHERE telegram_id = ?
    """, (
        reward,
        new_start,
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
        "user": user_json(updated)
    })


# =========================
# TASKS
# =========================

@app.route("/api/tasks", methods=["POST", "GET"])
def api_tasks():

    user = current_user()

    if not user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    now = int(time.time())

    conn = get_db()

    output = []

    for task_id, task in TASKS.items():

        previous = conn.execute("""
            SELECT claimed_at
            FROM task_claims
            WHERE telegram_id = ?
              AND task_id = ?
            ORDER BY claimed_at DESC
            LIMIT 1
        """, (
            user["telegram_id"],
            task_id
        )).fetchone()

        claimed = False

        if previous:

            if now - int(
                previous["claimed_at"]
            ) < TASK_RESET:

                claimed = True

        output.append({
            "id": task_id,
            "title": task["title"],
            "url": task["url"],
            "reward": task["reward"],
            "claimed": claimed
        })

    conn.close()

    return jsonify({
        "success": True,
        "tasks": output,
        "reset": TASK_RESET
    })


@app.route("/api/task/claim", methods=["POST"])
def api_task_claim():

    user = current_user()

    if not user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    data = request.get_json(
        silent=True
    ) or {}

    task_id = data.get(
        "task_id",
        ""
    )

    if task_id not in TASKS:

        return jsonify({
            "success": False,
            "error": "Invalid task"
        }), 400

    now = int(time.time())

    conn = get_db()

    previous = conn.execute("""
        SELECT claimed_at
        FROM task_claims
        WHERE telegram_id = ?
          AND task_id = ?
        ORDER BY claimed_at DESC
        LIMIT 1
    """, (
        user["telegram_id"],
        task_id
    )).fetchone()

    if previous:

        if now - int(
            previous["claimed_at"]
        ) < TASK_RESET:

            conn.close()

            return jsonify({
                "success": False,
                "error": "Task already claimed today"
            }), 400

    conn.execute("""
        INSERT OR REPLACE INTO task_claims (
            telegram_id,
            task_id,
            claimed_at
        )
        VALUES (?, ?, ?)
    """, (
        user["telegram_id"],
        task_id,
        now
    ))

    conn.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE telegram_id = ?
    """, (
        TASK_REWARD,
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
        "user": user_json(updated)
    })


# =========================
# BOOST
# =========================

@app.route("/api/boost", methods=["POST"])
def api_boost():

    user = current_user()

    if not user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    if user["boost"]:

        return jsonify({
            "success": False,
            "error": "Boost is already active"
        }), 400

    if int(user["balance"]) < BOOST_COST:

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
        "user": user_json(updated)
    })


# =========================
# REFERRAL
# =========================

@app.route("/api/referrals", methods=["POST", "GET"])
def api_referrals():

    user = current_user()

    if not user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    return jsonify({
        "success": True,
        "count": int(
            user["referral_count"]
        ),
        "earned": int(
            user["referral_earned"]
        ),
        "reward": REFERRAL_REWARD,
        "link": referral_link(
            user["referral_code"]
        )
    })


# =========================
# TOKENOMICS
# =========================

@app.route("/api/tokenomics")
def api_tokenomics():

    return jsonify({
        "success": True,
        "total_supply": TOTAL_SUPPLY,
        "symbol": "ELVION",
        "phase_start": PHASE_START,
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


# =========================
# RUN
# =========================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "5000"
            )
        )
    )
