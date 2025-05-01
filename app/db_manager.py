# ai-agent1/app/db_manager.py
import sqlite3
import os
import logging
import json
from datetime import datetime, timedelta, timezone

# Import provider modules to get stats counters
try:
    import ai_providers
    import notifier
except ImportError:
    logging.error("[DB Manager] Failed to import ai_providers or notifier for stats update.")
    ai_providers = None
    notifier = None

# --- Constants ---
# DB Path will be passed during initialization or read from env here
# Let's read from env here for simplicity within this module
DB_PATH = os.environ.get("DB_PATH", "/data/agent_stats.db")

# --- Helper Functions ---
def _get_db_connection():
    """Establishes a connection to the SQLite database. Internal use."""
    if not os.path.isfile(DB_PATH):
        logging.warning(f"[DB Manager] Database file not found at {DB_PATH}. Attempting to create directory.")
        db_dir = os.path.dirname(DB_PATH)
        if not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir)
                logging.info(f"[DB Manager] Created directory for database: {db_dir}")
            except OSError as e:
                logging.error(f"[DB Manager] Could not create directory {db_dir}: {e}")
                return None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database connection error: {e}")
        return None
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error connecting to database: {e}", exc_info=True)
        return None

# --- Public Database Functions ---

def init_db(default_configs):
    """
    Initializes the database schema and default agent configuration.

    Args:
        default_configs (dict): A dictionary containing the default key-value
                                pairs for the agent_config table.
    """
    logging.info("[DB Manager] Attempting to initialize database...")
    db_dir = os.path.dirname(DB_PATH);
    if not os.path.exists(db_dir):
        try: os.makedirs(db_dir); logging.info(f"[DB Manager] Created directory for database: {db_dir}")
        except OSError as e: logging.error(f"[DB Manager] Could not create directory {db_dir}: {e}"); return False

    conn = _get_db_connection()
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection during initialization.")
        return False

    try:
        with conn:
            cursor = conn.cursor()
            logging.info("[DB Manager] Ensuring database tables exist...")
            # Incidents Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, pod_key TEXT NOT NULL, severity TEXT NOT NULL,
                    summary TEXT, initial_reasons TEXT, k8s_context TEXT, sample_logs TEXT,
                    input_prompt TEXT, raw_ai_response TEXT, root_cause TEXT, troubleshooting_steps TEXT
                )
            ''')
            # Daily Stats Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY, model_calls INTEGER DEFAULT 0,
                    telegram_alerts INTEGER DEFAULT 0, incident_count INTEGER DEFAULT 0
                )
            ''')
            # Available Namespaces Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS available_namespaces ( name TEXT PRIMARY KEY, last_seen TEXT NOT NULL )
            ''')
            # Agent Config Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_config ( key TEXT PRIMARY KEY, value TEXT )
            ''')
            # Alert Cooldown Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alert_cooldown ( pod_key TEXT PRIMARY KEY, cooldown_until TEXT NOT NULL )
            ''')
            # Users Table (for Portal)
            cursor.execute('''
                 CREATE TABLE IF NOT EXISTS users (
                     id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL
                 )
            ''')

            # Initialize default config values if they don't exist
            if default_configs and isinstance(default_configs, dict):
                for key, value in default_configs.items():
                    cursor.execute("INSERT OR IGNORE INTO agent_config (key, value) VALUES (?, ?)", (key, value))
                logging.info("[DB Manager] Default config values ensured.")
            else:
                logging.warning("[DB Manager] No default configs provided to init_db.")

            logging.info("[DB Manager] Tables ensured.")
        logging.info(f"[DB Manager] Database initialization/check complete at {DB_PATH}")
        return True
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error during initialization: {e}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error during DB initialization: {e}", exc_info=True)
        return False
    finally:
        if conn: conn.close()

def load_all_config():
    """Loads all key-value pairs from the agent_config table."""
    config_from_db = {}
    conn = _get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM agent_config")
            rows = cursor.fetchall()
            config_from_db = {row['key']: row['value'] for row in rows}
            conn.close()
            logging.debug(f"[DB Manager] Loaded {len(config_from_db)} config items from DB.")
        except sqlite3.Error as e:
            logging.error(f"[DB Manager] Database error loading agent config: {e}")
            if conn: conn.close()
        except Exception as e:
            logging.error(f"[DB Manager] Unexpected error loading agent config: {e}", exc_info=True)
            if conn: conn.close()
    else:
        logging.error("[DB Manager] Failed to get DB connection for loading config.")
    return config_from_db


def record_incident(pod_key, severity, summary, initial_reasons, k8s_context, sample_logs,
                    alert_severity_levels, # Pass relevant config directly
                    input_prompt=None, raw_ai_response=None, root_cause=None, troubleshooting_steps=None):
    """Records an incident into the database and updates stats if severity matches."""
    timestamp_str = datetime.now(timezone.utc).isoformat()
    conn = _get_db_connection()
    if conn is None: logging.error(f"[DB Manager] Failed to get DB connection for recording incident {pod_key}"); return
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO incidents (
                    timestamp, pod_key, severity, summary, initial_reasons,
                    k8s_context, sample_logs, input_prompt, raw_ai_response,
                    root_cause, troubleshooting_steps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (timestamp_str, pod_key, severity, summary, initial_reasons,
                  k8s_context, sample_logs, input_prompt, raw_ai_response,
                  root_cause, troubleshooting_steps))

            # Update incident count in daily stats if severity is relevant
            if severity in alert_severity_levels:
                today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
                cursor.execute('''
                    UPDATE daily_stats SET incident_count = incident_count + 1 WHERE date = ?
                ''', (today_str,))
            logging.info(f"[DB Manager] Recorded data for {pod_key} with severity {severity}")
    except sqlite3.Error as e: logging.error(f"[DB Manager] Database error recording incident for {pod_key}: {e}")
    except Exception as e: logging.error(f"[DB Manager] Unexpected error recording incident for {pod_key}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def update_daily_stats():
    """Updates daily statistics (model calls, telegram alerts) in the database."""
    calls_to_add = 0
    alerts_to_add = 0

    if ai_providers:
        calls_to_add = ai_providers.get_and_reset_model_calls()
    else:
        logging.warning("[DB Manager] ai_providers module not available for getting stats.")

    if notifier:
        alerts_to_add = notifier.get_and_reset_telegram_alerts()
    else:
        logging.warning("[DB Manager] notifier module not available for getting stats.")


    if calls_to_add == 0 and alerts_to_add == 0: return

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    conn = _get_db_connection()
    if conn is None: logging.error("[DB Manager] Failed to get DB connection for updating daily stats"); return
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
            cursor.execute('''
                UPDATE daily_stats
                SET model_calls = model_calls + ?, telegram_alerts = telegram_alerts + ?
                WHERE date = ?
            ''', (calls_to_add, alerts_to_add, today_str))
            logging.info(f"[DB Manager] Updated daily stats for {today_str}: +{calls_to_add} Model calls, +{alerts_to_add} Telegram alerts.")
    except sqlite3.Error as e: logging.error(f"[DB Manager] Database error updating daily stats: {e}")
    except Exception as e: logging.error(f"[DB Manager] Unexpected error updating daily stats: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def is_pod_in_cooldown(pod_key):
    """Checks if a pod is currently in the alert cooldown period."""
    conn = _get_db_connection()
    if conn is None: logging.error(f"[DB Manager] Failed to get DB connection checking cooldown for {pod_key}"); return False
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT cooldown_until FROM alert_cooldown WHERE pod_key = ?", (pod_key,))
            result = cursor.fetchone()
            if result:
                cooldown_until_str = result['cooldown_until']
                if cooldown_until_str > now_iso:
                    logging.info(f"[DB Manager] Pod {pod_key} is in cooldown until {cooldown_until_str}.")
                    return True
                else:
                    cursor.execute("DELETE FROM alert_cooldown WHERE pod_key = ?", (pod_key,))
                    logging.info(f"[DB Manager] Cooldown expired for pod {pod_key} (was {cooldown_until_str}).")
                    return False
            return False
    except sqlite3.Error as e: logging.error(f"[DB Manager] Database error checking cooldown for {pod_key}: {e}"); return False
    except Exception as e: logging.error(f"[DB Manager] Unexpected error checking cooldown for {pod_key}: {e}", exc_info=True); return False
    finally:
        if conn: conn.close()

def set_pod_cooldown(pod_key, cooldown_minutes):
    """Sets the alert cooldown period for a specific pod."""
    conn = _get_db_connection()
    if conn is None: logging.error(f"[DB Manager] Failed to get DB connection setting cooldown for {pod_key}"); return
    try:
        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
        cooldown_until_iso = cooldown_until.isoformat()
        with conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO alert_cooldown (pod_key, cooldown_until) VALUES (?, ?)",
                           (pod_key, cooldown_until_iso))
            logging.info(f"[DB Manager] Set cooldown for pod {pod_key} until {cooldown_until_iso} ({cooldown_minutes} minutes)")
    except sqlite3.Error as e: logging.error(f"[DB Manager] Database error setting cooldown for {pod_key}: {e}")
    except Exception as e: logging.error(f"[DB Manager] Unexpected error setting cooldown for {pod_key}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def update_available_namespaces_in_db(namespaces):
    """Updates the list of available namespaces in the database."""
    if not namespaces: logging.info("[DB Manager] No active namespaces provided to update in DB."); return
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = _get_db_connection()
    if conn is None: logging.error("[DB Manager] Failed to get DB connection for updating available namespaces"); return
    try:
        with conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(namespaces))
            if namespaces: cursor.execute(f"DELETE FROM available_namespaces WHERE name NOT IN ({placeholders})", tuple(namespaces))
            else: cursor.execute("DELETE FROM available_namespaces")
            for ns in namespaces: cursor.execute("INSERT OR REPLACE INTO available_namespaces (name, last_seen) VALUES (?, ?)", (ns, timestamp))
            logging.info(f"[DB Manager] Updated available_namespaces table in DB with {len(namespaces)} namespaces.")
    except sqlite3.Error as e: logging.error(f"[DB Manager] Database error updating available namespaces: {e}")
    except Exception as e: logging.error(f"[DB Manager] Unexpected error updating available namespaces: {e}", exc_info=True)
    finally:
        if conn: conn.close()

