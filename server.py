import os
import time
import hmac
import hashlib
import sqlite3
import json
import secrets
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")

DB_FILE = "elvion.db"

# =========================
# ELVION SETTINGS
# =========================

TOTAL_SUPPLY = 100_000_000

MINING_REWARD = 1_000
MINING_INTERVAL = 24 * 60 * 60

BOOST_COST = 500_000
BOOST_MULTIPLIER = 2
BOOST_INTERVAL = 12 * 60 * 60

TASK_REWARD = 5_000
TASK_RESET = 24 * 60 * 60

REFERRAL_REWARD = 10_000

# 3-month mining phase
# 1 October 2026 -> 1 January 2027
PHASE_END = 1798761600

TASKS = {
    "telegram_channel": {
        "title": "Join ELVION Channel",
        "url": "https://t.me/ELVIONOfficial"
    },
    "telegram_group": {
        "title": "Join ELVION Group",
        "url": "https://t.me/ELVIONOFFICIALGROUP"
    }
}


# =========================
# DATABASE
# =========================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def add_column_if_missing(conn, table, column, definition):

    columns = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    names = [
        row["name"]
        for row in columns
    ]

    if column not in names:

        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            balance INTEGER DEFAULT 0,
            mining_started_at INTEGER DEFAULT 0,
            mining_cycles INTEGER DEFAULT 0,
            boosted INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_claims (
            telegram_id INTEGER,
            task_key TEXT,
            last_claim INTEGER DEFAULT 0,
            PRIMARY KEY (telegram_id, task_key)
        )
    """)

    # Referral migration
    add_column_if_missing(
        conn,
        "users",
        "referral_code",
        "TEXT DEFAULT ''"
    )

    add_column_if_missing(
        conn,
        "users",
        "referred_by",
        "INTEGER DEFAULT NULL"
    )

    add_column_if_missing(
        conn,
        "users",
        "referral_rewarded",
        "INTEGER DEFAULT 0"
    )

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_referral_code
        ON users(referral_code)
    """)

    conn.commit()
    conn.close()


init_db()


# =========================
# TELEGRAM AUTH
# =========================

def verify_telegram(init_data):

    if not BOT_TOKEN or not init_data:
        return None

    try:

        data = dict(
            parse_qsl(init_data)
        )

        received_hash = data.pop(
            "hash",
            None
        )

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={data[key]}"
            for key in sorted(data)
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

        user_data = json.loads(
            data.get("user", "{}")
        )

        if not user_data.get("id"):
            return None

        return user_data

    except Exception:
        return None


def get_user_from_request():

    init_data = request.args.get(
        "initData",
        ""
    )

    if not init_data and request.is_json:

        body = request.get_json(
            silent=True
        ) or {}

        init_data = body.get(
            "initData",
            ""
        )

    return verify_telegram(
        init_data
    )


# =========================
# REFERRAL
# =========================

def create_referral_code():

    return secrets.token_hex(5)


def get_referral_link(user):

    code = user["referral_code"]

    if BOT_USERNAME:

        return (
            f"https://t.me/"
            f"{BOT_USERNAME}"
            f"?start=ref_{code}"
        )

    return ""


def get_start_param():

    init_data = request.args.get(
        "initData",
        ""
    )

    if not init_data and request.is_json:

        body = request.get_json(
            silent=True
        ) or {}

        init_data = body.get(
            "initData",
            ""
        )

    if not init_data:
        return ""

    try:

        data = dict(
            parse_qsl(init_data)
        )

        return data.get(
            "start_param",
            ""
        )

    except Exception:

        return ""


def process_referral(
    conn,
    new_user_id,
    start_param
):

    if not start_param:
        return

    if not start_param.startswith(
        "ref_"
    ):
        return

    code = start_param[4:].strip()

    if not code:
        return

    inviter = conn.execute(
        """
        SELECT *
        FROM users
        WHERE referral_code = ?
        """,
        (code,)
    ).fetchone()

    if not inviter:
        return

    if int(
        inviter["telegram_id"]
    ) == int(new_user_id):

        return

    current = conn.execute(
        """
        SELECT referred_by
        FROM users
        WHERE telegram_id = ?
        """,
        (new_user_id,)
    ).fetchone()

    if not current:
        return

    if current["referred_by"]:
        return

    conn.execute(
        """
        UPDATE users
        SET referred_by = ?
        WHERE telegram_id = ?
        """,
        (
            inviter["telegram_id"],
            new_user_id
        )
    )

    # Reward inviter once
    conn.execute(
        """
        UPDATE users
        SET balance = balance + ?
        WHERE telegram_id = ?
        """,
        (
            REFERRAL_REWARD,
            inviter["telegram_id"]
        )
    )

    conn.execute(
        """
        UPDATE users
        SET referral_rewarded = 1
        WHERE telegram_id = ?
        """,
        (new_user_id,)
    )


# =========================
# USER
# =========================

def save_user(tg_user):

    telegram_id = int(
        tg_user["id"]
    )

    now = int(time.time())

    conn = get_db()

    user = conn.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    ).fetchone()

    if user is None:

        referral_code = (
            create_referral_code()
        )

        conn.execute(
            """
            INSERT INTO users (
                telegram_id,
                username,
                first_name,
                last_name,
                balance,
                mining_started_at,
                mining_cycles,
                boosted,
                referral_code,
                referred_by,
                referral_rewarded
            )
            VALUES (
                ?, ?, ?, ?, 0, ?, 0, 0, ?, NULL, 0
            )
            """,
            (
                telegram_id,
                tg_user.get(
                    "username",
                    ""
                ),
                tg_user.get(
                    "first_name",
                    ""
                ),
                tg_user.get(
                    "last_name",
                    ""
                ),
                now,
                referral_code
            )
        )

        process_referral(
            conn,
            telegram_id,
            get_start_param()
        )

    else:

        if not user["referral_code"]:

            conn.execute(
                """
                UPDATE users
                SET referral_code = ?
                WHERE telegram_id = ?
                """,
                (
                    create_referral_code(),
                    telegram_id
                )
            )

        conn.execute(
            """
            UPDATE users
            SET username = ?,
                first_name = ?,
                last_name = ?
            WHERE telegram_id = ?
            """,
            (
                tg_user.get(
                    "username",
                    ""
                ),
                tg_user.get(
                    "first_name",
                    ""
                ),
                tg_user.get(
                    "last_name",
                    ""
                ),
                telegram_id
            )
        )

    conn.commit()

    user = conn.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    ).fetchone()

    conn.close()

    return user


# =========================
# MINING
# =========================

def mining_interval(user):

    if user["boosted"]:
        return BOOST_INTERVAL

    return MINING_INTERVAL


def user_status(user):

    now = int(
        time.time()
    )

    started = int(
        user["mining_started_at"]
    )

    interval = mining_interval(
        user
    )

    elapsed = now - started

    remaining = max(
        0,
        interval - elapsed
    )

    conn = get_db()

    referrals = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM users
        WHERE referred_by = ?
        """,
        (
            user["telegram_id"],
        )
    ).fetchone()

    conn.close()

    return {

        "balance":
            user["balance"],

        "mining_cycles":
            user["mining_cycles"],

        "boosted":
            bool(user["boosted"]),

        "mining_interval":
            interval,

        "remaining_seconds":
            remaining,

        "claim_ready":
            elapsed >= interval,

        "phase_end":
            PHASE_END,

        "referral_count":
            referrals["total"],

        "referral_reward":
            REFERRAL_REWARD,

        "referral_link":
            get_referral_link(user)
    }


# =========================
# HOME
# =========================

@app.route("/")
def home():

    return send_file(
        "index.html"
    )


@app.route("/health")
def health():

    return jsonify({
        "status": "online",
        "project": "ELVION"
    })


# =========================
# USER API
# =========================

@app.route("/api/user")
def api_user():

    tg_user = (
        get_user_from_request()
    )

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    user = save_user(
        tg_user
    )

    return jsonify({
        "telegram_id":
            user["telegram_id"],

        "username":
            user["username"],

        "first_name":
            user["first_name"],

        "last_name":
            user["last_name"],

        **user_status(user)
    })


# =========================
# MINING CLAIM
# =========================

@app.route(
    "/api/claim",
    methods=["POST"]
)
def claim():

    tg_user = (
        get_user_from_request()
    )

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    user = save_user(
        tg_user
    )

    now = int(
        time.time()
    )

    # Phase ended
    if now >= PHASE_END:

        return jsonify({
            "success": False,
            "message":
                "The mining phase has ended."
        }), 400

    interval = mining_interval(
        user
    )

    elapsed = (
        now -
        user["mining_started_at"]
    )

    if elapsed < interval:

        return jsonify({
            "success": False,
            "message":
                "Mining is still running",
            "remaining_seconds":
                interval - elapsed
        }), 400

    conn = get_db()

    new_balance = (
        user["balance"]
        + MINING_REWARD
    )

    new_cycles = (
        user["mining_cycles"]
        + 1
    )

    conn.execute(
        """
        UPDATE users
        SET balance = ?,
            mining_cycles = ?,
            mining_started_at = ?
        WHERE telegram_id = ?
        """,
        (
            new_balance,
            new_cycles,
            now,
            user["telegram_id"]
        )
    )

    conn.commit()
    conn.close()

    return jsonify({

        "success":
            True,

        "reward":
            MINING_REWARD,

        "balance":
            new_balance,

        "mining_cycles":
            new_cycles,

        "remaining_seconds":
            interval
    })


# =========================
# TASKS
# =========================

@app.route("/api/tasks")
def tasks():

    tg_user = (
        get_user_from_request()
    )

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    user = save_user(
        tg_user
    )

    now = int(
        time.time()
    )

    conn = get_db()

    output = []

    for key, task in TASKS.items():

        row = conn.execute(
            """
            SELECT last_claim
            FROM task_claims
            WHERE telegram_id = ?
            AND task_key = ?
            """,
            (
                user["telegram_id"],
                key
            )
        ).fetchone()

        last_claim = (
            row["last_claim"]
            if row
            else 0
        )

        available = (
            now - last_claim
            >= TASK_RESET
        )

        output.append({

            "key":
                key,

            "title":
                task["title"],

            "url":
                task["url"],

            "reward":
                TASK_REWARD,

            "available":
                available
        })

    conn.close()

    return jsonify({
        "tasks":
            output
    })


@app.route(
    "/api/task/claim",
    methods=["POST"]
)
def task_claim():

    tg_user = (
        get_user_from_request()
    )

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    body = (
        request.get_json(
            silent=True
        ) or {}
    )

    task_key = body.get(
        "task_key"
    )

    if task_key not in TASKS:

        return jsonify({
            "success": False,
            "message":
                "Invalid task"
        }), 400

    user = save_user(
        tg_user
    )

    now = int(
        time.time()
    )

    conn = get_db()

    row = conn.execute(
        """
        SELECT last_claim
        FROM task_claims
        WHERE telegram_id = ?
        AND task_key = ?
        """,
        (
            user["telegram_id"],
            task_key
        )
    ).fetchone()

    if row:

        if (
            now -
            row["last_claim"]
            <
            TASK_RESET
        ):

            conn.close()

            return jsonify({
                "success":
                    False,

                "message":
                    "Task already claimed"
            }), 400

    new_balance = (
        user["balance"]
        + TASK_REWARD
    )

    conn.execute(
        """
        UPDATE users
        SET balance = ?
        WHERE telegram_id = ?
        """,
        (
            new_balance,
            user["telegram_id"]
        )
    )

    conn.execute(
        """
        INSERT OR REPLACE INTO task_claims (
            telegram_id,
            task_key,
            last_claim
        )
        VALUES (?, ?, ?)
        """,
        (
            user["telegram_id"],
            task_key,
            now
        )
    )

    conn.commit()
    conn.close()

    return jsonify({

        "success":
            True,

        "reward":
            TASK_REWARD,

        "balance":
            new_balance
    })


# =========================
# BOOST
# =========================

@app.route(
    "/api/boost",
    methods=["POST"]
)
def boost():

    tg_user = (
        get_user_from_request()
    )

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    user = save_user(
        tg_user
    )

    if user["boosted"]:

        return jsonify({
            "success":
                False,

            "message":
                "Boost is already active"
        }), 400

    if (
        user["balance"]
        <
        BOOST_COST
    ):

        return jsonify({
            "success":
                False,

            "message":
                "Not enough ELVION"
        }), 400

    new_balance = (
        user["balance"]
        -
        BOOST_COST
    )

    conn = get_db()

    conn.execute(
        """
        UPDATE users
        SET balance = ?,
            boosted = 1
        WHERE telegram_id = ?
        """,
        (
            new_balance,
            user["telegram_id"]
        )
    )

    conn.commit()
    conn.close()

    return jsonify({

        "success":
            True,

        "balance":
            new_balance,

        "boosted":
            True,

        "multiplier":
            BOOST_MULTIPLIER
    })


# =========================
# REFERRALS
# =========================

@app.route("/api/referrals")
def referrals():

    tg_user = (
        get_user_from_request()
    )

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    user = save_user(
        tg_user
    )

    conn = get_db()

    rows = conn.execute(
        """
        SELECT username,
               first_name,
               telegram_id
        FROM users
        WHERE referred_by = ?
        ORDER BY telegram_id DESC
        LIMIT 100
        """,
        (
            user["telegram_id"],
        )
    ).fetchall()

    conn.close()

    return jsonify({

        "count":
            len(rows),

        "reward_per_referral":
            REFERRAL_REWARD,

        "total_earned":
            len(rows)
            *
            REFERRAL_REWARD,

        "referral_link":
            get_referral_link(user)
    })


# =========================
# TOKENOMICS
# =========================

@app.route("/api/tokenomics")
def tokenomics():

    return jsonify({

        "name":
            "ELVION",

        "symbol":
            "ELVION",

        "total_supply":
            TOTAL_SUPPLY,

        "phase_end":
            PHASE_END,

        "allocations": [

            {
                "name":
                    "Community & Mining",

                "percentage":
                    60,

                "amount":
                    60_000_000
            },

            {
                "name":
                    "Liquidity",

                "percentage":
                    10,

                "amount":
                    10_000_000
            },

            {
                "name":
                    "Ecosystem",

                "percentage":
                    10,

                "amount":
                    10_000_000
            },

            {
                "name":
                    "Treasury / Reserve",

                "percentage":
                    8,

                "amount":
                    8_000_000
            },

            {
                "name":
                    "Team",

                "percentage":
                    7,

                "amount":
                    7_000_000
            },

            {
                "name":
                    "Ma
