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
BOOST_MULTIPLIER = 2
BOOST_INTERVAL = 12 * 60 * 60

TASK_REWARD = 5_000
TASK_RESET = 24 * 60 * 60

REFERRAL_REWARD = 10_000

PHASE_START = 1790812800
PHASE_END = 1798761600

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().replace("@", "")


# =========================================================
# DATABASE
# =========================================================

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
            referral_rewarded INTEGER DEFAULT 0,
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


# =========================================================
# TELEGRAM BOT USERNAME
# =========================================================

def get_bot_username():

    global BOT_USERNAME

    if BOT_USERNAME:
        return BOT_USERNAME

    if not BOT_TOKEN:
        return ""

    try:

        url = (
            "https://api.telegram.org/bot"
            + BOT_TOKEN
            + "/getMe"
        )

        with urllib.request.urlopen(url, timeout=10) as response:

            data = json.loads(
                response.read().decode("utf-8")
            )

            if data.get("ok"):

                username = data["result"].get(
                    "username",
                    ""
                )

                BOT_USERNAME = username

                return username

    except Exception as e:

        print("BOT USERNAME ERROR:", e)

    return ""


# =========================================================
# TELEGRAM AUTH
# =========================================================

def get_init_data():

    value = request.args.get("initData", "")

    if value:
        return value

    value = request.form.get("initData", "")

    if value:
        return value

    try:

        data = request.get_json(
            silent=True
        ) or {}

        return data.get("initData", "")

    except Exception:

        return ""


def verify_telegram_init_data(init_data):

    if not BOT_TOKEN or not init_data:
        return None

    try:

        parsed = urllib.parse.parse_qs(
            init_data,
            keep_blank_values=True
        )

        received_hash = parsed.get(
            "hash",
            [""]
        )[0]

        if not received_hash:
            return None

        pairs = []

        for key in sorted(parsed.keys()):

            if key == "hash":
                continue

            value = parsed[key][0]

            pairs.append(
                f"{key}={value}"
            )

        data_check_string = "\n".join(pairs)

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

        user_raw = parsed.get(
            "user",
            [""]
        )[0]

        if not user_raw:
            return None

        telegram_user = json.loads(user_raw)

        start_param = parsed.get(
            "start_param",
            [""]
        )[0]

        telegram_user["_start_param"] = start_param

        return telegram_user

    except Exception as e:

        print("AUTH ERROR:", e)

        return None


def get_telegram_user():

    init_data = get_init_data()

    return verify_telegram_init_data(
        init_data
    )


# =========================================================
# USER
# =========================================================

def create_or_update_user(tg_user):

    telegram_id = str(
        tg_user.get("id")
    )

    username = tg_user.get(
        "username",
        ""
    )

    first_name = tg_user.get(
        "first_name",
        ""
    )

    start_param = tg_user.get(
        "_start_param",
        ""
    )

    now = int(time.time())

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    ).fetchone()

    if row is None:

        referral_code = secrets.token_hex(5)

        conn.execute(
            """
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
            """,
            (
                telegram_id,
                username,
                first_name,
                now,
                referral_code
            )
        )

        conn.commit()

        row = conn.execute(
            """
            SELECT *
            FROM users
            WHERE telegram_id = ?
            """,
            (telegram_id,)
        ).fetchone()

        # ---------------------------------------------
        # REFERRAL
        # ---------------------------------------------

        if (
            start_param
            and start_param.startswith("ref_")
        ):

            referral_code_used = start_param[4:]

            inviter = conn.execute(
                """
                SELECT *
                FROM users
                WHERE referral_code = ?
                """,
                (referral_code_used,)
            ).fetchone()

            if (
                inviter
                and str(inviter["telegram_id"])
                != telegram_id
            ):

                conn.execute(
                    """
                    UPDATE users
                    SET balance = balance + ?,
                        referral_count =
                            referral_count + 1,
                        referral_earned =
                            referral_earned + ?
                    WHERE telegram_id = ?
                    """,
                    (
                        REFERRAL_REWARD,
                        REFERRAL_REWARD,
                        inviter["telegram_id"]
                    )
                )

                conn.execute(
                    """
                    UPDATE users
                    SET referred_by = ?,
                        referral_rewarded = 1
                    WHERE telegram_id = ?
                    """,
                    (
                        str(inviter["telegram_id"]),
                        telegram_id
                    )
                )

                conn.commit()

    else:

        conn.execute(
            """
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE telegram_id = ?
            """,
            (
                username,
                first_name,
                telegram_id
            )
        )

        conn.commit()

    row = conn.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    ).fetchone()

    conn.close()

    return row


# =========================================================
# MINING
# =========================================================

def get_mining_info(user):

    now = int(time.time())

    started = int(
        user["mining_started"] or now
    )

    boost = int(
        user["boost"] or 0
    )

    interval = (
        BOOST_INTERVAL
        if boost
        else MINING_INTERVAL
    )

    next_claim = started + interval

    # If phase has ended
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


# =========================================================
# TASKS
# =========================================================

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


def task_claimed(
    telegram_id,
    task_id
):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM task_claims
        WHERE telegram_id = ?
        AND task_id = ?
        """,
        (
            telegram_id,
            task_id
        )
    ).fetchone()

    conn.close()

    return row is not None


# =========================================================
# ROUTES
# =========================================================

@app.route("/")
def home():

    return send_file(
        "index.html"
    )


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


# =========================================================
# USER API
# =========================================================

@app.route("/api/user")
def api_user():

    tg_user = get_telegram_user()

    if not tg_user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    mining = get_mining_info(user)

    return jsonify({
        "success": True,

        "user": {
            "telegram_id": user["telegram_id"],
            "username": user["username"],
            "first_name": user["first_name"],
            "balance": user["balance"],
            "mining_started": user["mining_started"],
            "mining_cycles": user["mining_cycles"],
            "boost": user["boost"],
            "referral_code": user["referral_code"],
            "referral_count": user["referral_count"],
            "referral_earned": user["referral_earned"],
            "wallet_address": user["wallet_address"]
        },

        "mining": mining,

        "mining_end": mining["next_claim"],
        "next_claim": mining["next_claim"],
        "seconds_left": mining["seconds_left"]
    })


# =========================================================
# ENERGY
# =========================================================

@app.route("/api/energy")
def api_energy():

    tg_user = get_telegram_user()

    if not tg_user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    return jsonify({
        "success": True,
        "energy": 2000
    })


# =========================================================
# CLAIM
# =========================================================

@app.route("/api/claim", methods=["POST"])
def api_claim():

    tg_user = get_telegram_user()

    if not tg_user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    now = int(time.time())

    mining = get_mining_info(user)

    if now >= PHASE_END:

        return jsonify({
            "success": False,
            "error": "Mining phase has ended"
        })

    if mining["seconds_left"] > 0:

        return jsonify({
            "success": False,
            "error": "Mining cycle is not complete",
            "seconds_left": mining["seconds_left"],
            "next_claim": mining["next_claim"]
        })

    conn = db()

    reward = MINING_REWARD

    new_balance = (
        int(user["balance"])
        + reward
    )

    new_cycles = (
        int(user["mining_cycles"])
        + 1
    )

    conn.execute(
        """
        UPDATE users
        SET balance = ?,
            mining_started = ?,
            mining_cycles = ?
        WHERE telegram_id = ?
        """,
        (
            new_balance,
            now,
            new_cycles,
            user["telegram_id"]
        )
    )

    conn.commit()

    conn.close()

    interval = (
        BOOST_INTERVAL
        if user["boost"]
        else MINING_INTERVAL
    )

    return jsonify({
        "success": True,
        "reward": reward,
        "balance": new_balance,
        "mining_cycles": new_cycles,
        "mining_end": now + interval,
        "next_claim": now + interval
    })


# =========================================================
# TASKS
# =========================================================

@app.route("/api/tasks")
def api_tasks():

    tg_user = get_telegram_user()

    if not tg_user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    result = []

    for task_id, task in TASKS.items():

        claimed = task_claimed(
            user["telegram_id"],
            task_id
        )

        result.append({
            **task,
            "claimed": claimed,
            "completed": claimed
        })

    return jsonify({
        "success": True,
        "tasks": result,
        "reset": TASK_RESET
    })


# =========================================================
# TASK CLAIM
# =========================================================

@app.route(
    "/api/task/claim",
    methods=["POST"]
)
def api_task_claim():

    tg_user = get_telegram_user()

    if not tg_user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    try:

        body = request.get_json(
            silent=True
        ) or {}

        task_id = body.get(
            "task_id"
        )

    except Exception:

        task_id = None

    if task_id not in TASKS:

        return jsonify({
            "success": False,
            "error": "Invalid task"
        })

    if task_claimed(
        user["telegram_id"],
        task_id
    ):

        return jsonify({
            "success": False,
            "error": "Task already claimed"
        })

    task = TASKS[task_id]

    now = int(time.time())

    conn = db()

    conn.execute(
        """
        INSERT INTO task_claims (
            telegram_id,
            task_id,
            claimed_at
        )
        VALUES (?, ?, ?)
        """,
        (
            user["telegram_id"],
            task_id,
            now
        )
    )

    conn.execute(
        """
        UPDATE users
        SET balance = balance + ?
        WHERE telegram_id = ?
        """,
        (
            task["reward"],
            user["telegram_id"]
        )
    )

    conn.commit()

    row = conn.execute(
        """
        SELECT balance
        FROM users
        WHERE telegram_id = ?
        """,
        (
            user["telegram_id"],
        )
    ).fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "reward": task["reward"],
        "balance": row["balance"]
    })


# =========================================================
# BOOST
# =========================================================

@app.route(
    "/api/boost",
    methods=["POST"]
)
def api_boost():

    tg_user = get_telegram_user()

    if not tg_user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    if int(user["boost"]) == 1:

        return jsonify({
            "success": False,
            "error": "Boost is already active"
        })

    balance = int(
        user["balance"]
    )

    if balance < BOOST_COST:

        return jsonify({
            "success": False,
            "error":
                "You need 500,000 ELVION"
        })

    conn = db()

    conn.execute(
        """
        UPDATE users
        SET balance = balance - ?,
            boost = 1
        WHERE telegram_id = ?
        """,
        (
            BOOST_COST,
            user["telegram_id"]
        )
    )

    conn.commit()

    row = conn.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = ?
        """,
        (
            user["telegram_id"],
        )
    ).fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "boost": 1,
        "balance": row["balance"],
        "interval": BOOST_INTERVAL
    })


# =========================================================
# REFERRALS
# =========================================================

@app.route("/api/referrals")
def api_referrals():

    tg_user = get_telegram_user()

    if not tg_user:

        return jsonify({
            "success": False,
            "error": "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    bot_username = get_bot_username()

    referral_link = ""

    if bot_username:

        referral_link = (
            "https://t.me/"
            + bot_username
            + "?startapp=ref_"
            + user["referral_code"]
        )

    return jsonify({
        "success": True,
        "referral_code": user["referral_code"],
        "referral_link": referral_link,
        "referral_count": user["referral_count"],
        "referral_earned": user["referral_earned"],
        "reward": REFERRAL_REWARD
    })


# =========================================================
# TOKENOMICS
# =========================================================

@app.route("/api/tokenomics")
def api_tokenomics():

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


# =========================================================
# WALLET SAVE
# =========================================================

@app.route(
    "/api/walle
