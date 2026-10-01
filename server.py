import os
import time
import json
import hmac
import hashlib
import sqlite3
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

TOTAL_SUPPLY = 100_000_000
COMMUNITY_ALLOCATION = 60_000_000

MINING_REWARD = 1_000
MINING_INTERVAL = 24 * 60 * 60

TASK_REWARD = 5_000
TASK_RESET = 24 * 60 * 60

BOOST_COST = 500_000
BOOST_MULTIPLIER = 2

DB_FILE = "elvion.db"

TASKS = {
    "telegram_channel": {
        "title": "Join ELVION Channel",
        "url": "https://t.me/ELVIONOfficial",
        "reward": TASK_REWARD,
    },
    "telegram_group": {
        "title": "Join ELVION Group",
        "url": "https://t.me/ELVIONOFFICIALGROUP",
        "reward": TASK_REWARD,
    },
}

X_URL = os.getenv("ELVION_X_URL", "").strip()

if X_URL:
    TASKS["x_follow"] = {
        "title": "Follow ELVION on X",
        "url": X_URL,
        "reward": TASK_REWARD,
    }


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            balance INTEGER DEFAULT 0,
            mining_started_at INTEGER DEFAULT 0,
            mining_cycles INTEGER DEFAULT 0,
            boosted INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_claims (
            telegram_id INTEGER NOT NULL,
            task_key TEXT NOT NULL,
            last_claim INTEGER DEFAULT 0,
            PRIMARY KEY (telegram_id, task_key)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY,
            community_distributed INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        INSERT OR IGNORE INTO stats
        (id, community_distributed)
        VALUES (1, 0)
    """)

    columns = {
        row["name"]
        for row in cur.execute(
            "PRAGMA table_info(users)"
        ).fetchall()
    }

    required_columns = {
        "username": "TEXT DEFAULT ''",
        "first_name": "TEXT DEFAULT ''",
        "last_name": "TEXT DEFAULT ''",
        "balance": "INTEGER DEFAULT 0",
        "mining_started_at": "INTEGER DEFAULT 0",
        "mining_cycles": "INTEGER DEFAULT 0",
        "boosted": "INTEGER DEFAULT 0",
        "created_at": "INTEGER DEFAULT 0",
        "updated_at": "INTEGER DEFAULT 0",
    }

    for column, definition in required_columns.items():
        if column not in columns:
            cur.execute(
                f"ALTER TABLE users ADD COLUMN {column} {definition}"
            )

    conn.commit()
    conn.close()


init_db()


def verify_telegram_init_data(init_data):
    if not BOT_TOKEN:
        return None

    if not init_data:
        return None

    try:
        parsed = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = parsed.pop(
            "hash",
            None
        )

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={parsed[key]}"
            for key in sorted(parsed)
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

        auth_date = int(
            parsed.get("auth_date", "0")
        )

        if int(time.time()) - auth_date > 86400:
            return None

        telegram_user = json.loads(
            parsed.get("user", "{}")
        )

        if not telegram_user.get("id"):
            return None

        return telegram_user

    except Exception:
        return None


def get_init_data():

    if request.method == "POST":

        data = request.get_json(
            silent=True
        ) or {}

        return data.get(
            "initData",
            ""
        )

    return request.args.get(
        "initData",
        ""
    )


def authenticated_user():
    return verify_telegram_init_data(
        get_init_data()
    )


def create_or_update_user(tg_user):

    telegram_id = int(
        tg_user["id"]
    )

    username = tg_user.get(
        "username",
        ""
    )

    first_name = tg_user.get(
        "first_name",
        ""
    )

    last_name = tg_user.get(
        "last_name",
        ""
    )

    now = int(time.time())

    conn = db()
    cur = conn.cursor()

    user = cur.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    ).fetchone()

    if not user:

        cur.execute(
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
                created_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?,
                0, ?, 0, 0, ?, ?
            )
            """,
            (
                telegram_id,
                username,
                first_name,
                last_name,
                now,
                now,
                now
            )
        )

    else:

        mining_started_at = (
            user["mining_started_at"]
        )

        if not mining_started_at:
            mining_started_at = now

        cur.execute(
            """
            UPDATE users
            SET username = ?,
                first_name = ?,
                last_name = ?,
                mining_started_at = ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (
                username,
                first_name,
                last_name,
                mining_started_at,
                now,
                telegram_id
            )
        )

    conn.commit()

    user = cur.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    ).fetchone()

    conn.close()

    return user


def mining_interval(user):

    if user["boosted"]:
        return (
            MINING_INTERVAL //
            BOOST_MULTIPLIER
        )

    return MINING_INTERVAL


def get_status(user):

    now = int(time.time())

    started = int(
        user["mining_started_at"] or now
    )

    interval = mining_interval(user)

    elapsed = max(
        0,
        now - started
    )

    remaining = max(
        0,
        interval - elapsed
    )

    claim_ready = (
        elapsed >= interval
    )

    conn = db()

    stats = conn.execute(
        """
        SELECT community_distributed
        FROM stats
        WHERE id = 1
        """
    ).fetchone()

    community_distributed = int(
        stats["community_distributed"] or 0
    )

    conn.close()

    community_remaining = max(
        0,
        COMMUNITY_ALLOCATION -
        community_distributed
    )

    return {
        "balance": int(
            user["balance"]
        ),
        "mining_started_at": started,
        "mining_interval": interval,
        "mining_reward": MINING_REWARD,
        "remaining_seconds": remaining,
        "claim_ready": claim_ready,
        "mining_cycles": int(
            user["mining_cycles"]
        ),
        "boosted": bool(
            user["boosted"]
        ),
        "community_distributed":
            community_distributed,
        "community_remaining":
            community_remaining,
        "total_supply":
            TOTAL_SUPPLY,
    }


@app.route("/")
def home():

    return send_from_directory(
        ".",
        "index.html"
    )


@app.route("/health")
def health():

    return jsonify({
        "status": "online",
        "project": "ELVION",
        "message":
            "ELVION backend is running"
    })


@app.route(
    "/api/user",
    methods=["GET"]
)
def api_user():

    tg_user = authenticated_user()

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    status = get_status(user)

    return jsonify({
        "telegram_id":
            int(user["telegram_id"]),
        "username":
            user["username"],
        "first_name":
            user["first_name"],
        "last_name":
            user["last_name"],
        **status
    })


@app.route(
    "/api/status",
    methods=["GET"]
)
def api_status():

    tg_user = authenticated_user()

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    return jsonify(
        get_status(user)
    )


@app.route(
    "/api/claim",
    methods=["POST"]
)
def api_claim():

    tg_user = authenticated_user()

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    now = int(time.time())

    started = int(
        user["mining_started_at"] or now
    )

    interval = mining_interval(
        user
    )

    elapsed = now - started

    if elapsed < interval:

        return jsonify({
            "success": False,
            "message":
                "Mining is still running",
            "remaining_seconds":
                interval - elapsed,
            "claim_ready":
                False
        }), 400

    conn = db()

    try:

        cur = conn.cursor()

        stats = cur.execute(
            """
            SELECT community_distributed
            FROM stats
            WHERE id = 1
            """
        ).fetchone()

        community_distributed = int(
            stats["community_distributed"] or 0
        )

        community_remaining = max(
            0,
            COMMUNITY_ALLOCATION -
            community_distributed
        )

        if community_remaining <= 0:

            return jsonify({
                "success": False,
                "message":
                    "Community mining allocation has been reached."
            }), 400

        reward = min(
            MINING_REWARD,
            community_remaining
        )

        new_balance = (
            int(user["balance"])
            + reward
        )

        new_cycles = (
            int(user["mining_cycles"])
            + 1
        )

        cur.execute(
            """
            UPDATE users
            SET balance = ?,
                mining_cycles = ?,
                mining_started_at = ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (
                new_balance,
                new_cycles,
                now,
                now,
                int(user["telegram_id"])
            )
        )

        cur.execute(
            """
            UPDATE stats
            SET community_distributed =
                community_distributed + ?
            WHERE id = 1
            """,
            (reward,)
        )

        conn.commit()

        return jsonify({
            "success": True,
            "message":
                f"You claimed {reward:,} ELVION",
            "reward":
                reward,
            "balance":
                new_balance,
            "next_mining_started_at":
                now,
            "next_claim_seconds":
                interval
        })

    except Exception as e:

        conn.rollback()

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

    finally:
        conn.close()


@app.route(
    "/api/tasks",
    methods=["GET"]
)
def api_tasks():

    tg_user = authenticated_user()

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    telegram_id = int(
        user["telegram_id"]
    )

    now = int(time.time())

    conn = db()

    result = []

    for key, task in TASKS.items():

        row = conn.execute(
            """
            SELECT last_claim
            FROM task_claims
            WHERE telegram_id = ?
              AND task_key = ?
            """,
            (
                telegram_id,
                key
            )
        ).fetchone()

        last_claim = (
            int(row["last_claim"])
            if row
            else 0
        )

        available = (
            last_claim == 0
            or now - last_claim >=
               TASK_RESET
        )

        remaining = 0

        if not available:

            remaining = (
                TASK_RESET -
                (now - last_claim)
            )

        result.append({
            "key": key,
            "title": task["title"],
            "url": task["url"],
            "reward": task["reward"],
            "available": available,
            "remaining_seconds":
                max(0, remaining)
        })

    conn.close()

    return jsonify({
        "tasks": result,
        "reset_seconds":
            TASK_RESET
    })


@app.route(
    "/api/task/claim",
    methods=["POST"]
)
def api_task_claim():

    tg_user = authenticated_user()

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    data = request.get_json(
        silent=True
    ) or {}

    task_key = data.get(
        "task_key",
        ""
    )

    if task_key not in TASKS:

        return jsonify({
            "success": False,
            "message":
                "Invalid task"
        }), 400

    user = create_or_update_user(
        tg_user
    )

    telegram_id = int(
        user["telegram_id"]
    )

    now = int(time.time())

    conn = db()

    try:

        cur = conn.cursor()

        row = cur.execute(
            """
            SELECT last_claim
            FROM task_claims
            WHERE telegram_id = ?
              AND task_key = ?
            """,
            (
                telegram_id,
                task_key
            )
        ).fetchone()

        if row:

            last_claim = int(
                row["last_claim"]
            )

            if (
                now - last_claim
                < TASK_RESET
            ):

                return jsonify({
                    "success": False,
                    "message":
                        "Task reward is not ready yet",
                    "remaining_seconds":
                        TASK_RESET -
                        (now - last_claim)
                }), 400

        stats = cur.execute(
            """
            SELECT community_distributed
            FROM stats
            WHERE id = 1
            """
        ).fetchone()

        community_distributed = int(
            stats["community_distributed"] or 0
        )

        community_remaining = max(
            0,
            COMMUNITY_ALLOCATION -
            community_distributed
        )

        if community_remaining <= 0:

            return jsonify({
                "success": False,
                "message":
                    "Community reward allocation has been reached."
            }), 400

        reward = min(
            TASKS[task_key]["reward"],
            community_remaining
        )

        new_balance = (
            int(user["balance"])
            + reward
        )

        cur.execute(
            """
            UPDATE users
            SET balance = ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (
                new_balance,
                now,
                telegram_id
            )
        )

        cur.execute(
            """
            INSERT INTO task_claims
            (
                telegram_id,
                task_key,
                last_claim
            )
            VALUES (?, ?, ?)
            ON CONFLICT(
                telegram_id,
                task_key
            )
            DO UPDATE SET
                last_claim =
                    excluded.last_claim
            """,
            (
                telegram_id,
                task_key,
                now
            )
        )

        cur.execute(
            """
            UPDATE stats
            SET community_distributed =
                community_distributed + ?
            WHERE id = 1
            """,
            (reward,)
        )

        conn.commit()

        return jsonify({
            "success": True,
            "reward":
                reward,
            "balance":
                new_balance,
            "message":
                f"You received {reward:,} ELVION"
        })

    except Exception as e:

        conn.rollback()

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

    finally:
        conn.close()


@app.route(
    "/api/boost",
    methods=["POST"]
)
def api_boost():

    tg_user = authenticated_user()

    if not tg_user:

        return jsonify({
            "error":
                "Telegram authentication failed"
        }), 401

    user = create_or_update_user(
        tg_user
    )

    telegram_id = int(
        user["telegram_id"]
    )

    if user["boosted"]:

        return jsonify({
            "success": False,
            "message":
                "Boost is already active."
        }), 400

    balance = int(
        user["balance"]
    )

    if balance < BOOST_COST:

        return jsonify({
            "success": False,
            "message":
                f"You need {BOOST_COST:,} ELVION."
        }), 400

    now = int(time.time())

    conn = db()

    try:

        new_balance = (
            balance -
            BOOST_COST
        )

        conn.execute(
            """
            UPDATE users
            SET balance = ?,
                boosted = 1,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (
                new_balance,
                now,
                telegram_id
            )
        )

        conn.commit()

        return jsonify({
            "success": True,
            "message":
                "2x Mining Boost activated!",
            "balance":
                new_balance,
            "boosted":
                True,
            "multiplier":
                BOOST_MULTIPLIER
        })

    except Exception as e:

        conn.rollback()

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

    finally:
        conn.close()


@app.route(
    "/api/tokenomics",
    methods=["GET"]
)
def api_tokenomics():

    return jsonify({

        "name": "ELVION",

        "symbol": "ELVION",

        "total_supply":
            TOTAL_SUPPLY,

        "allocations": [

            {
                "name":
                    "Community & Mining",
                "percentage": 60,
                "amount":
                    60_000_000
            },

            {
                "name":
                    "Liquidity",
                "percentage": 10,
                "amount":
                    10_000_000
            },

            {
   
