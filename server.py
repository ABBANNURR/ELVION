import os
import time
import hmac
import hashlib
import json
import sqlite3
import secrets
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

# ============================================================
# ELVION V2 — COMPLETE BACKEND
# ============================================================

PROJECT_NAME = "ELVION"
VERSION = "V2"

# ============================================================
# CORE SETTINGS
# ============================================================

STARTING_BONUS = 10_000

# 1 ELV per minute
MINING_RATE = 1
MINING_RATE_PER_SECOND = MINING_RATE / 60

# Claim every 24 hours
CLAIM_INTERVAL = 24 * 60 * 60

# Boost
BOOST_COST = 500_000
BOOST_MULTIPLIER = 2
BOOST_DURATION = 24 * 60 * 60

# Tasks
TASK_REWARD = 5_000

# Referral
REFERRAL_REWARD = 10_000

# Tokenomics
TOTAL_SUPPLY = 100_000_000

# ============================================================
# LAUNCH
# ============================================================

LAUNCH_DATETIME = datetime(
    2026,
    11,
    1,
    0,
    0,
    0,
    tzinfo=timezone.utc
)

LAUNCH_TIMESTAMP = int(LAUNCH_DATETIME.timestamp())

# ============================================================
# DATABASE
# ============================================================

DB_PATH = os.getenv("DATABASE_PATH", "elvion.db")

# SQLite development/testing only.
# Production will later move to PostgreSQL.

SQLITE_TIMEOUT = 30


# ============================================================
# TELEGRAM
# ============================================================

BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

DEV_MODE = os.getenv(
    "ELVION_DEV_MODE",
    "1"
).lower() in (
    "1",
    "true",
    "yes",
    "on"
)

DEV_USER_ID = 100000001

DEV_TEST_USERS = {
    "100000001": {
        "username": "elvion_test",
        "first_name": "ELVION TEST 1",
    },
    "100000002": {
        "username": "elvion_test_2",
        "first_name": "ELVION TEST 2",
    },
}

# ============================================================
# OFFICIAL ELVION LINKS
# ============================================================

CHANNEL_LINK = "https://t.me/ELVIONOFFICIAL"
GROUP_LINK = "https://t.me/+m0VojVWAuNk4ZWY8"


# ============================================================
# TIME
# ============================================================

def now():
    return int(time.time())


def launch_active():
    return now() >= LAUNCH_TIMESTAMP


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        isolation_level=None
    )

    conn.row_factory = sqlite3.Row

    # Foreign keys
    conn.execute("PRAGMA foreign_keys = ON")

    # Wait instead of immediately returning "database is locked"
    conn.execute("PRAGMA busy_timeout = 30000")

    # WAL greatly reduces read/write locking problems
    conn.execute("PRAGMA journal_mode = WAL")

    # Safer durability
    conn.execute("PRAGMA synchronous = NORMAL")

    return conn


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():
    conn = get_db()

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT UNIQUE NOT NULL,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                balance REAL DEFAULT 0,
                starting_bonus REAL DEFAULT 0,
                mining_started INTEGER DEFAULT 0,
                last_claim INTEGER DEFAULT 0,
                mining_cycles INTEGER DEFAULT 0,
                boost_until INTEGER DEFAULT 0,
                referral_code TEXT UNIQUE,
                referred_by TEXT DEFAULT '',
                referral_count INTEGER DEFAULT 0,
                referral_earned REAL DEFAULT 0,
                wallet_address TEXT DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                reward REAL NOT NULL,
                claimed_at INTEGER NOT NULL,
                UNIQUE(telegram_id, task_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT NOT NULL,
                amount REAL NOT NULL,
                balance_after REAL NOT NULL,
                reason TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS boost_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT NOT NULL,
                cost REAL NOT NULL,
                multiplier REAL NOT NULL,
                started_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )
        """)

    finally:
        conn.close()


# ============================================================
# HELPERS
# ============================================================

def generate_referral_code(conn):
    while True:
        code = secrets.token_hex(5)

        exists = conn.execute(
            """
            SELECT 1
            FROM users
            WHERE referral_code = ?
            """,
            (code,)
        ).fetchone()

        if not exists:
            return code


def add_history(
    conn,
    telegram_id,
    amount,
    balance_after,
    reason
):
    conn.execute(
        """
        INSERT INTO balance_history
        (
            telegram_id,
            amount,
            balance_after,
            reason,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(telegram_id),
            float(amount),
            float(balance_after),
            reason,
            now()
        )
    )


def get_user(conn, telegram_id):
    return conn.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = ?
        """,
        (str(telegram_id),)
    ).fetchone()


# ============================================================
# USER CREATION
# ============================================================

def create_user(
    conn,
    telegram_id,
    username="",
    first_name="",
    referred_by=""
):
    current = now()

    # Before launch, mining begins at launch.
    # After launch, mining begins when account is created.
    mining_start = max(
        current,
        LAUNCH_TIMESTAMP
    )

    referral_code = generate_referral_code(conn)

    conn.execute(
        """
        INSERT INTO users (
            telegram_id,
            username,
            first_name,
            balance,
            starting_bonus,
            mining_started,
            last_claim,
            mining_cycles,
            boost_until,
            referral_code,
            referred_by,
            referral_count,
            referral_earned,
            wallet_address,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(telegram_id),
            username or "",
            first_name or "",
            STARTING_BONUS,
            STARTING_BONUS,
            mining_start,
            0,
            0,
            0,
            referral_code,
            referred_by or "",
            0,
            0,
            "",
            current,
            current
        )
    )

    add_history(
        conn,
        telegram_id,
        STARTING_BONUS,
        STARTING_BONUS,
        "starting_bonus"
    )

    # Referral reward only when a new user is created
    if referred_by and str(referred_by) != str(telegram_id):

        referrer = conn.execute(
            """
            SELECT *
            FROM users
            WHERE referral_code = ?
            """,
            (str(referred_by),)
        ).fetchone()

        if referrer:

            new_balance = (
                float(referrer["balance"])
                + REFERRAL_REWARD
            )

            conn.execute(
                """
                UPDATE users
                SET balance = ?,
                    referral_count = referral_count + 1,
                    referral_earned =
                        referral_earned + ?,
                    updated_at = ?
                WHERE telegram_id = ?
                """,
                (
                    new_balance,
                    REFERRAL_REWARD,
                    current,
                    referrer["telegram_id"]
                )
            )

            add_history(
                conn,
                referrer["telegram_id"],
                REFERRAL_REWARD,
                new_balance,
                "referral_reward"
            )

    return get_user(
        conn,
        telegram_id
    )


# ============================================================
# MINING CALCULATION
# ============================================================

def calculate_mining(user):
    current = now()

    if current < LAUNCH_TIMESTAMP:
        return {
            "active": False,
            "before_launch": True,
            "seconds_mined": 0,
            "available_reward": 0,
            "can_claim": False,
            "seconds_left": LAUNCH_TIMESTAMP - current,
            "rate_per_minute": MINING_RATE,
            "base_rate_per_minute": MINING_RATE,
            "boost_active": False,
            "boost_until": int(
                user["boost_until"] or 0
            ),
            "last_claim": int(
                user["last_claim"] or 0
            ),
            "mining_started": int(
                user["mining_started"] or
                LAUNCH_TIMESTAMP
            ),
            "next_claim": LAUNCH_TIMESTAMP
        }

    mining_started = int(
        user["mining_started"] or current
    )

    last_claim = int(
        user["last_claim"] or 0
    )

    boost_until = int(
        user["boost_until"] or 0
    )

    mining_started = max(
        mining_started,
        LAUNCH_TIMESTAMP
    )

    period_start = max(
        mining_started,
        last_claim if last_claim > 0
        else mining_started
    )

    elapsed = max(
        0,
        current - period_start
    )

    claim_elapsed = min(
        elapsed,
        CLAIM_INTERVAL
    )

    boost_active = boost_until > current

    multiplier = (
        BOOST_MULTIPLIER
        if boost_active
        else 1
    )

    reward = (
        claim_elapsed
        * MINING_RATE_PER_SECOND
        * multiplier
    )

    can_claim = (
        elapsed >= CLAIM_INTERVAL
    )

    seconds_left = max(
        0,
        CLAIM_INTERVAL - elapsed
    )

    return {
        "active": True,
        "before_launch": False,
        "seconds_mined": int(claim_elapsed),
        "available_reward": round(
            reward,
            6
        ),
        "can_claim": can_claim,
        "seconds_left": seconds_left,
        "rate_per_minute":
            MINING_RATE * multiplier,
        "base_rate_per_minute":
            MINING_RATE,
        "boost_active": boost_active,
        "boost_until": boost_until,
        "last_claim": last_claim,
        "mining_started": mining_started,
        "next_claim":
            period_start + CLAIM_INTERVAL
    }


# ============================================================
# TELEGRAM AUTH
# ============================================================

def verify_telegram_init_data(init_data):

    if not BOT_TOKEN:
        return None

    try:
        pairs = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = pairs.pop(
            "hash",
            None
        )

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={pairs[key]}"
            for key in sorted(pairs.keys())
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
            pairs.get("user", "{}")
        )

        if not user_data.get("id"):
            return None

        return user_data

    except Exception:
        return None


def authenticate():

    # Development testing mode
    if DEV_MODE:

        dev_id = request.args.get(
            "dev_id",
            ""
        ).strip()

        if dev_id:

            test_user = DEV_TEST_USERS.get(
                dev_id,
                {
                    "username":
                        f"elvion_{dev_id}",
                    "first_name":
                        "ELVION TEST"
                }
            )

            return {
                "id": int(dev_id),
                "username":
                    test_user["username"],
                "first_name":
                    test_user["first_name"]
            }

        return {
            "id": DEV_USER_ID,
            "username": "elvion_test",
            "first_name": "ELVION"
        }

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    ).strip()

    if not init_data:
        init_data = request.args.get(
            "init_data",
            ""
        ).strip()

    if not init_data:
        return None

    return verify_telegram_init_data(
        init_data
    )


def get_authenticated_user():

    tg_user = authenticate()

    if not tg_user:
        return None

    telegram_id = str(
        tg_user["id"]
    )

    conn = get_db()

    try:

        user = get_user(
            conn,
            telegram_id
        )

        if not user:

            user = create_user(
                conn,
                telegram_id,
                tg_user.get(
                    "username",
                    ""
                ),
                tg_user.get(
                    "first_name",
                    ""
                ),
                request.args.get(
                    "ref",
                    ""
                )
            )

            return user

        # Update profile only if changed.
        username = tg_user.get(
            "username",
            user["username"] or ""
        )

        first_name = tg_user.get(
            "first_name",
            user["first_name"] or ""
        )

        if (
            username != user["username"]
            or
            first_name != user["first_name"]
        ):
            conn.execute(
                """
                UPDATE users
                SET username = ?,
                    first_name = ?,
                    updated_at = ?
                WHERE telegram_id = ?
                """,
                (
                    username,
                    first_name,
                    now(),
                    telegram_id
                )
            )

            user = get_user(
                conn,
                telegram_id
            )

        return user

    finally:
        conn.close()


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


# ============================================================
# HEALTH
# ============================================================

@app.route("/api/health")
def health():

    database_ok = False

    conn = None

    try:
        conn = get_db()
        conn.execute(
            "SELECT 1"
        ).fetchone()
        database_ok = True

    except Exception:
        database_ok = False

    finally:
        if conn:
            conn.close()

    return jsonify({
        "success": database_ok,
        "project": PROJECT_NAME,
        "version": VERSION,
        "status":
            "online"
            if database_ok
            else "database_error",
        "database": "sqlite-testing",
        "telegram_auth":
            bool(BOT_TOKEN)
            and not DEV_MODE,
        "bot_username":
            os.getenv(
                "TELEGRAM_BOT_USERNAME",
                ""
            ),
        "launch_date": "2026-11-01",
        "launch_timestamp":
            LAUNCH_TIMESTAMP,
        "server_time": now()
    })


# ============================================================
# USER
# ============================================================

@app.route("/api/user")
def api_user():

    user = get_authenticated_user()

    if not user:
        return jsonify({
            "success": False,
            "error":
                "Telegram authentication required"
        }), 401

    mining = calculate_mining(
        user
    )

    return jsonify({

        "success": True,

        "project": PROJECT_NAME,

        "server_time": now(),

        "launch": {
            "active": launch_active(),
            "date": "2026-11-01",
            "timestamp": LAUNCH_TIMESTAMP
        },

        "user": {

            "telegram_id":
                str(user["telegram_id"]),

            "username":
                user["username"],

            "first_name":
                user["first_name"],

            "balance":
                round(
                    float(user["balance"]),
                    6
                ),

            "starting_bonus":
                float(
                    user["starting_bonus"]
                ),

            "mining_cycles":
                int(
                    user["mining_cycles"]
                ),

            "referral_code":
                user["referral_code"],

            "referral_count":
                int(
                    user["referral_count"]
                ),

            "referral_earned":
                float(
                    user["referral_earned"]
                ),

            "wallet_address":
                user["wallet_address"]
                or "",

            "boost_until":
                int(
                    user["boost_until"]
                    or 0
                )
        },

        "mining": mining
    })


# ============================================================
# CLAIM
# ============================================================

@app.route(
    "/api/claim",
    methods=["POST"]
)
def claim():

    user = get_authenticated_user()

    if not user:
        return jsonify({
            "success": False,
            "error":
                "Authentication required"
        }), 401

    if not launch_active():
        return jsonify({
            "success": False,
            "error":
                "Mining has not launched yet",
            "launch_date":
                "2026-11-01"
        }), 400

    conn = get_db()

    try:

        current_user = get_user(
            conn,
            user["telegram_id"]
        )

        mining = calculate_mining(
            current_user
        )

        if not mining["can_claim"]:

            return jsonify({
                "success": False,
                "error":
                    "24-hour mining cycle is not complete",
                "mining": mining
            }), 400

        reward = float(
            mining["available_reward"]
        )

        if reward <= 0:

            return jsonify({
                "success": False,
                "error":
                    "No mining reward available"
            }), 400

        new_balance = (
            float(current_user["balance"])
            + reward
        )

        current = now()

        conn.execute("BEGIN IMMEDIATE")

        conn.execute(
            """
            UPDATE users
            SET balance = ?,
                last_claim = ?,
                mining_started = ?,
                mining_cycles =
                    mining_cycles + 1,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (
                new_balance,
                current,
                current,
                current,
                user["telegram_id"]
            )
        )

        add_history(
            conn,
            user["telegram_id"],
            reward,
            new_balance,
            "mining_claim"
        )

        conn.commit()

        updated = get_user(
            conn,
            user["telegram_id"]
        )

        return jsonify({
            "success": True,
            "claimed":
                round(reward, 6),
            "balance":
                round(
                    new_balance,
                    6
                ),
            "mining":
                calculate_mining(
                    updated
                )
        })

    except Exception as e:

        try:
            conn.rollback()
        except Exception:
            pass

        return jsonify({
            "success": False,
            "error":
                "Claim failed",
            "details":
                str(e)
        }), 500

    finally:
        conn.close()


# ============================================================
# TASKS
# ============================================================

TASKS = [

    {
        "id":
            "telegram_channel",

        "title":
            "Join ELVION Official Channel",

        "description":
            "Join the official ELVION Telegram Channel.",

        "reward":
            TASK_REWARD,

        "url":
            CHANNEL_LINK
    },

    {
        "id":
            "telegram_group",

        "title":
            "Join ELVION Community",

        "description":
            "Join the official ELVION community group.",

        "reward":
            TASK_REWARD,

        "url":
            GROUP_LINK
    }
]


@app.route("/api/tasks")
def tasks():

    user = get_authenticated_user()

    if not user:
        return jsonify({
            "success": False,
            "error":
                "Authentication required"
        }), 401

    conn = get_db()

    try:

        claimed_rows = conn.execute(
            """
            SELECT task_id
            FROM task_claims
            WHERE telegram_id = ?
            """,
            (user["telegram_id"],)
        ).fetchall()

        claimed = {
            row["task_id"]
            for row in claimed_rows
        }

        result = []

        for task in TASKS:

            item = dict(task)

            item["claimed"] = (
                item["id"]
                in claimed
            )

            result.append(item)

        return jsonify({
            "success": True,
            "tasks": result
        })

    finally:
        conn.close()


# ============================================================
# TASK CLAIM
# ============================================================

@app.route(
    "/api/task/claim",
    methods=["POST"]
)
def task_claim():

    user = get_authenticated_user()

    if not user:
        return jsonify({
            "success": False,
            "error":
                "Authentication required"
        }), 401

    data = request.get_json(
        silent=True
    ) or {}

    task_id = str(
        data.get(
            "task_id",
            ""
        )
    ).strip()

    task = next(
        (
            t for t in TASKS
            if t["id"] == task_id
        ),
        None
    )

    if not task:
        return jsonify({
            "success": False,
            "error":
                "Invalid task"
        }), 400

    conn = get_db()

    try:

        conn.execute(
            "BEGIN IMMEDIATE"
        )

        existing = conn.execute(
            """
            SELECT 1
            FROM task_claims
            WHERE telegram_id = ?
            AND task_id = ?
            """,
            (
                user["telegram_id"],
                task_id
            )
        ).fetchone()

        if existing:
            conn.rollback()

            return jsonify({
                "success": False,
                "error":
                    "Task already claimed"
            }), 400

        current_user = get_user(
            conn,
            user["telegram_id"]
        )

        new_balance = (
            float(
                current_user["balance"]
            )
            + task["reward"]
        )

        conn.execute(
            """
            UPDATE users
            SET balance = ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (
                new_balance,
                now(),
                user["telegram_id"]
            )
        )

        conn.execute(
            """
            INSERT INTO task_claims
            (
                telegram_id,
                task_id,
                reward,
                claimed_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                user["telegram_id"],
                task_id,
                task["reward"],
                now()
            )
        )

        add_history(
            conn,
            user["telegram_id"],
            task["reward"],
            new_balance,
            "task_reward"
        )

        conn.commit()

        return jsonify({
            "success": True,
            "task_id": task_id,
            "reward": task["reward"],
            "balance":
                round(
                    new_balance,
                    6
                )
        })

    except Exception as e:

        try:
            conn.rollback()
        except Exception:
            pass

        return jsonify({
            "success": False,
            "error":
                "Task claim failed",
            "details":
                str(e)
        }), 500

    finally:
        conn.close()


# ============================================================
# BOOST
# ============================================================

@app.route(
    "/api/boost",
    methods=["POST"]
)
def boost():

    user = get_authenticated_user()

    if not user:
        return jsonify({
            "success": False,
            "error":
                "Authentication required"
        }), 401

    conn = get_db()

    try:

        # One atomic write transaction
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        current_user = get_user(
            conn,
            user["telegram_id"]
        )

        if not current_user:
            conn.rollback()

            return jsonify({
                "success": False,
                "error":
                    "User not found"
            }), 404

        balance = float(
            current_user["balance"]
        )

        current = now()

        old_until = int(
            current_user["boost_until"]
            or 0
        )

        if balance < BOOST_COST:

            conn.rollback()

            return jsonify({
                "success": False,
                "error":
                    "Insufficient ELV balance",
                "required":
                    BOOST_COST,
                "balance":
                    round(
                        balance,
                        6
                    )
            }), 400

        # If an existing boost is active,
        # extend from its expiry.
        start_time = max(
            current,
            old_until
        )

        expires = (
            start_time
            + BOOST_DURATION
        )

        new_balance = (
            balance
            - BOOST_COST
        )

        conn.execute(
            """
            UPDATE users
            SET balance = ?,
                boost_until = ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (
                new_balance,
                expires,
                current,
                user["telegram_id"]
            )
        )

        conn.execute(
            """
            INSERT INTO boost_history
            (
                telegram_id,
                cost,
                multiplier,
                started_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user["telegram_id"],
                BOOST_COST,
                BOOST_MULTIPLIER,
                start_time,
                expires
            )
        )

        add_history(
            conn,
            user["telegram_id"],
            -BOOST_COST,
            new_balance,
            "boost_purchase"
        )

        conn.commit()

        return jsonify({
            "success": True,
            "cost":
                BOOST_COST,
            "multiplier":
                BOOST_MULTIPLIER,
            "duration_hours":
                24,
            "boost_until":
                expires,
            "balance":
                round(
                    new_balance,
                    6
                )
        })

    except Exception as e:

        try:
            conn.rollback()
        except Exception:
            pass

        return jsonify({
            "success": False,
            "error":
                "Boost failed",
            "details":
                str(e)
        }), 500

    finally:
        conn.close()


# ============================================================
# REFERRALS
# ============================================================

@app.route("/api/referrals")
def referrals():

    user = get_authenticated_user()

    if not user:
        return jsonify({
            "success": False,
            "error":
                "Authentication required"
        }), 401

    return jsonify({

        "success": True,

        "referral_code":
            user["referral_code"],

        "referral_count":
            int(
                user["referral_count"]
            ),

        "referral_earned":
            float(
                user["referral_earned"]
            ),

        "reward_per_referral":
            REFERRAL_REWARD
    })


# ============================================================
# LEADERBOARD
# ============================================================

@app.route("/api/leaderboard")
def leaderboard():

    category = request.args.get(
        "category",
        "global"
    ).lower()

    conn = get_db()

    try:

        if category == "referrers":

            rows = conn.execute(
                """
                SELECT
                    username,
                    first_name,
                    balance,
                    referral_count,
                    referral_earned,
                    mining_cycles
                FROM users
                ORDER BY
                    referral_count DESC,
                    referral_earned DESC,
                    balance DESC
                LIMIT 100
                """
            ).fetchall()

        elif category == "miners":

            rows = conn.execute(
                """
                SELECT
                    username,
                    first_name,
                    balance,
                    referral_count,
                    referral_earned,
                    mining_cycles
                FROM users
                ORDER BY
                    mining_cycles DESC,
                    balance DESC
                LIMIT 100
                """
            ).fetchall()

        else:

            rows = conn.execute(
                """
                SELECT
                    username,
                    first_name,
                    balance,
                    referral_count,
                    referral_earned,
                    mining_cycles
                FROM users
                ORDER BY
                    balance DESC
                LIMIT 100
                """
            ).fetchall()

        results = []

        for index, row in enumerate(
            rows,
            start=1
        ):

            results.append({

                "rank": index,

                "username":
                    row["username"],

                "first_name":
                    row["first_name"],

                "balance":
                    round(
                        float(
                            row["balance"]
                        ),
                        6
                    ),

                "referral_count":
                    int(
                        row["referral_count"]
                    ),

                "referral_earned":
                    float(
                        row["referral_earned"]
                    ),

                "mining_cycles":
                    int(
                        row["mining_cycles"]
                    )
            })

        return jsonify({
            "success": True,
            "category": category,
            "leaderboard": results
        })

    finally:
        conn.close()


# ============================================================
# HISTORY
# ============================================================

@app.route("/api/history")
def history():

    user = get_authenticated_user()

    if not user:
        return jsonify({
            "success": False,
            "error":
                "Authentication required"
        }), 401

    try:
        limit = int(
            request.args.get(
                "limit",
                50
            )
        )
    except ValueError:
        limit = 50

    limit = max(
        1,
        min(
            limit,
            100
        )
    )

    conn = get_db()

    try:

        rows = conn.execute(
            """
            SELECT
                amount,
                balance_after,
                reason,
                created_at
            FROM balance_history
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                user["telegram_id"],
                limit
            )
        ).fetchall()

        return jsonify({
            "success": True,
            "history": [
                {
                    "amount":
                        round(
                            float(
                                row["amount"]
                            ),
                            6
                        ),

                    "balance_after":
                        round(
                            float(
                                row["balance_after"]
                            ),
                            6
                        ),

                    "reason":
                        row["reason"],

                    "created_at":
                        int(
                            row["created_at"]
                        )
                }

                for row in rows
            ]
        })

    finally:
        conn.close()


# ============================================================
# WALLET
# ============================================================

@app.route(
    "/api/wallet",
    methods=["GET", "POST"]
)
def wallet():

    user = get_authenticated_user()

    if not user:
        return jsonify({
            "success": False,
            "error":
                "Authentication required"
        }), 401

    conn = get_db()

    try:

        if request.method == "POST":

            data = request.get_json(
                silent=True
            ) or {}

            wallet_address = str(
                data.get(
                    "wallet_address",
                    ""
                )
            ).strip()

            if len(wallet_address) > 200:

                return jsonify({
                    "success": False,
                    "error":
                        "Invalid wallet address"
                }), 400

            conn.execute(
                "BEGIN IMMEDIATE"
            )

            conn.execute(
                """
                UPDATE users
                SET wallet_address = ?,
                    updated_at = ?
                WHERE telegram_id = ?
                """,
                (
                    wallet_address,
                    now(),
                    user["telegram_id"]
                )
            )

            conn.commit()

        updated = get_user(
            conn,
            user["telegram_id"]
        )

        return jsonify({
            "success": True,
            "wallet_address":
                updated["wallet_address"]
                or ""
        })

    except Exception as e:

        try:
            conn.rollback()
        except Exception:
            pass

        return jsonify({
            "success": False,
            "error":
                "Wallet update failed",
            "details":
                str(e)
        }), 500

    finally:
        conn.close()


# ============================================================
# TOKENOMICS
# ============================================================

@app.route("/api/tokenomics")
def tokenomics():

    return jsonify({

        "success": True,

        "project":
            PROJECT_NAME,

        "total_supply":
            TOTAL_SUPPLY,

        "mining_rate":
            MINING_RATE,

        "claim_interval_hours":
            24,

        "starting_bonus":
            STARTING_BONUS,

        "boost_cost":
            BOOST_COST,

        "boost_multiplier":
            BOOST_MULTIPLIER,

        "boost_duration_hours":
            24,

        "task_reward":
            TASK_REWARD,

        "referral_reward":
            REFERRAL_REWARD
    })


# ============================================================
# TON CONNECT MANIFEST
# ============================================================

@app.route(
    "/tonconnect-manifest.json"
)
def tonconnect_manifest():

    return jsonify({

        "url":
            "https://elvion.app",

        "name":
            "ELVION",

        "iconUrl":
            "https://elvion.app/icon.png"
    })


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "success": False,
        "error":
            "Endpoint not found"
    }), 404


@app.errorhandler(500)
def internal_error(error):

    return jsonify({
        "success": False,
        "error":
            "Internal server error"
    }), 500


# ============================================================
# STARTUP
# ============================================================

init_db()


if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    print("")
    print("======================================")
    print("          ELVION BACKEND V2")
    print("======================================")
    print(f"Project:       {PROJECT_NAME}")
    print(f"Version:       {VERSION}")
    print(f"Database:      {DB_PATH}")
    print("Mining:        1 ELV / minute")
    print("Claim:         Every 24 hours")
    print(
        f"Starting Bonus:{STARTING_BONUS:,} ELV"
    )
    print(
        f"Boost:         {BOOST_COST:,} ELV"
    )
    print(
        "Boost Multiplier: 2x / 24 hours"
    )
    print(
        "Launch:        1 November 2026"
    )
    print(
        f"Dev Mode:      {DEV_MODE}"
    )
    print(
        "SQLite:        WAL + 30s timeout"
    )
    print("======================================")
    print("")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
