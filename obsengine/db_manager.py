# ai-agent1/obsengine/db_manager.py (Added Agent Tracking)
import sqlite3
import os
import logging
import json
from datetime import datetime, timedelta, timezone

# Import provider modules to get stats counters (These should be in the same directory)
try:
    import ai_providers
    import notifier
except ImportError:
    logging.error("[DB Manager] Failed to import ai_providers or notifier for stats update.")
    ai_providers = None
    notifier = None

# DB_PATH will be passed to functions or read from env by the main app

def _get_db_connection(db_path):
    # Function remains the same as before
    if not os.path.isfile(db_path):
        logging.warning(f"[DB Manager] Database file not found at {db_path}. Attempting to create directory.")
        db_dir = os.path.dirname(db_path)
        if not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir)
                logging.info(f"[DB Manager] Created directory for database: {db_dir}")
            except OSError as e:
                logging.error(f"[DB Manager] Could not create directory {db_dir}: {e}")
                return None
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database connection error to {db_path}: {e}")
        return None
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error connecting to database {db_path}: {e}", exc_info=True)
        return None

def init_db(db_path, default_configs={}):
    # Function remains largely the same, but adds the active_agents table
    logging.info(f"[DB Manager] Attempting to initialize database at {db_path}...")
    db_dir = os.path.dirname(db_path);
    if not os.path.exists(db_dir):
        try: os.makedirs(db_dir); logging.info(f"[DB Manager] Created directory for database: {db_dir}")
        except OSError as e: logging.error(f"[DB Manager] Could not create DB directory {db_dir}: {e}"); return False

    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection during initialization.")
        return False

    try:
        with conn:
            cursor = conn.cursor()
            logging.info("[DB Manager] Ensuring database tables exist...")
            # Existing tables...
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS incidents (...)
            ''') # Keep existing table definition
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (...)
            ''') # Keep existing table definition
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS available_namespaces (...)
            ''') # Keep existing table definition
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_config (...)
            ''') # Keep existing table definition
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alert_cooldown (...)
            ''') # Keep existing table definition
            cursor.execute('''
                 CREATE TABLE IF NOT EXISTS users (...)
            ''') # Keep existing table definition

            # --- ADDED: active_agents table ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS active_agents (
                    agent_id TEXT PRIMARY KEY,
                    cluster_name TEXT,
                    first_seen_timestamp TEXT NOT NULL,
                    last_seen_timestamp TEXT NOT NULL,
                    agent_version TEXT,  -- Optional: Agent could send its version
                    metadata TEXT        -- Optional: Store other info as JSON string
                )
            ''')
            # ------------------------------------

            # Ensure default configs (no changes needed here)
            if default_configs and isinstance(default_configs, dict):
                for key, value in default_configs.items():
                    cursor.execute("INSERT OR IGNORE INTO agent_config (key, value) VALUES (?, ?)", (key, value))
                logging.info("[DB Manager] Default config values ensured.")
            else:
                logging.warning("[DB Manager] No default configs provided to init_db.")

            logging.info("[DB Manager] Tables ensured (including active_agents).")
        logging.info(f"[DB Manager] Database initialization/check complete at {db_path}")
        return True
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error during initialization: {e}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error during DB initialization: {e}", exc_info=True)
        return False
    finally:
        if conn: conn.close()


# --- Functions for Agent Tracking ---

def update_agent_heartbeat(db_path, agent_id, cluster_name, timestamp_iso, agent_version=None, metadata=None):
    """Updates the last_seen timestamp for an agent or inserts a new record."""
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection for agent heartbeat {agent_id}"); return
    try:
        metadata_json = json.dumps(metadata) if metadata else None
        with conn:
            cursor = conn.cursor()
            # Use INSERT OR REPLACE (or separate INSERT OR IGNORE + UPDATE)
            cursor.execute('''
                INSERT INTO active_agents (agent_id, cluster_name, first_seen_timestamp, last_seen_timestamp, agent_version, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    last_seen_timestamp = excluded.last_seen_timestamp,
                    cluster_name = excluded.cluster_name, -- Update cluster name if it changes
                    agent_version = excluded.agent_version, -- Update version if provided
                    metadata = excluded.metadata -- Update metadata if provided
            ''', (agent_id, cluster_name, timestamp_iso, timestamp_iso, agent_version, metadata_json))
            logging.debug(f"[DB Manager] Updated heartbeat for agent: {agent_id} at {timestamp_iso}")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error updating agent heartbeat for {agent_id}: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error updating agent heartbeat for {agent_id}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def get_active_agents(db_path, timeout_seconds=300):
    """Retrieves agents seen within the specified timeout period."""
    agents = []
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection for getting active agents"); return agents

    try:
        threshold_time = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        threshold_iso = threshold_time.isoformat()
        with conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT agent_id, cluster_name, first_seen_timestamp, last_seen_timestamp, agent_version, metadata
                FROM active_agents
                WHERE last_seen_timestamp >= ?
                ORDER BY last_seen_timestamp DESC
            ''', (threshold_iso,))
            rows = cursor.fetchall()
            for row in rows:
                agent_data = dict(row)
                # Optionally parse metadata JSON back into dict
                if agent_data.get('metadata'):
                    try:
                        agent_data['metadata'] = json.loads(agent_data['metadata'])
                    except json.JSONDecodeError:
                        logging.warning(f"Could not parse metadata JSON for agent {agent_data['agent_id']}")
                        # Keep raw string or set to None/Error indicator
                agents.append(agent_data)
            logging.info(f"[DB Manager] Found {len(agents)} active agents within last {timeout_seconds} seconds.")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error getting active agents: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error getting active agents: {e}", exc_info=True)
    finally:
        if conn: conn.close()
    return agents

def cleanup_inactive_agents(db_path, timeout_seconds=86400): # Default: 1 day
    """Removes agents not seen for longer than the timeout period."""
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection for cleaning up agents"); return 0

    deleted_count = 0
    try:
        threshold_time = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        threshold_iso = threshold_time.isoformat()
        with conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM active_agents
                WHERE last_seen_timestamp < ?
            ''', (threshold_iso,))
            deleted_count = cursor.rowcount
            if deleted_count > 0:
                logging.info(f"[DB Manager] Cleaned up {deleted_count} inactive agents (older than {timeout_seconds} seconds).")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error cleaning up inactive agents: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error cleaning up inactive agents: {e}", exc_info=True)
    finally:
        if conn: conn.close()
    return deleted_count


# --- Existing functions (load_all_config, record_incident, update_daily_stats, etc.) ---
# Keep the existing functions like load_all_config, record_incident, update_daily_stats,
# is_pod_in_cooldown, set_pod_cooldown as they are still needed by ObsEngine.
# Remember to pass db_path to them when calling from app.py

def load_all_config(db_path):
    # Function remains the same
    config_from_db = {}
    conn = _get_db_connection(db_path)
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

def record_incident(db_path, pod_key, severity, summary, initial_reasons, k8s_context, sample_logs,
                    alert_severity_levels,
                    input_prompt=None, raw_ai_response=None, root_cause=None, troubleshooting_steps=None):
    # Function remains the same
    timestamp_str = datetime.now(timezone.utc).isoformat()
    conn = _get_db_connection(db_path)
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

def update_daily_stats(db_path):
    # Function remains the same
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
    conn = _get_db_connection(db_path)
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

def is_pod_in_cooldown(db_path, pod_key):
    # Function remains the same
    conn = _get_db_connection(db_path)
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

def set_pod_cooldown(db_path, pod_key, cooldown_minutes):
    # Function remains the same
    conn = _get_db_connection(db_path)
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

# Keep this function if Portal needs it via ObsEngine API
def update_available_namespaces_in_db(db_path, namespaces):
    # Function remains the same
    if not namespaces: logging.info("[DB Manager] No active namespaces provided to update in DB."); return
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = _get_db_connection(db_path)
    if conn is None: logging.error("[DB Manager] Failed to get DB connection for updating available namespaces"); return
    try:
        with conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(namespaces))
            if namespaces:
                cursor.execute(f"DELETE FROM available_namespaces WHERE name NOT IN ({placeholders})", tuple(namespaces))
            else:
                cursor.execute("DELETE FROM available_namespaces")
            for ns in namespaces:
                cursor.execute("INSERT OR REPLACE INTO available_namespaces (name, last_seen) VALUES (?, ?)", (ns, timestamp))
            logging.info(f"[DB Manager] Updated available_namespaces table in DB with {len(namespaces)} namespaces.")
    except sqlite3.Error as e: logging.error(f"[DB Manager] Database error updating available namespaces: {e}")
    except Exception as e: logging.error(f"[DB Manager] Unexpected error updating available namespaces: {e}", exc_info=True)
    finally:
        if conn: conn.close()

