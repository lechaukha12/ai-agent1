# ai-agent1/obsengine/db_manager.py
import sqlite3
import os
import logging
import json
from datetime import datetime, timedelta, timezone

try:
    import ai_providers
    import notifier
except ImportError:
    logging.error("[DB Manager] Failed to import ai_providers or notifier for stats update.")
    ai_providers = None
    notifier = None

def _get_db_connection(db_path):
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir)
            logging.info(f"[DB Manager] Created directory for database: {db_dir}")
        except OSError as e:
            logging.error(f"[DB Manager] Could not create DB directory {db_dir}: {e}")
            return None
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database connection error to {db_path}: {e}")
        return None
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error connecting to database {db_path}: {e}", exc_info=True)
        return None

# --- CẬP NHẬT init_db VỚI LOG CHI TIẾT HƠN ---
def init_db(db_path, default_configs={}):
    logging.info(f"[DB Manager] Attempting to initialize database at {db_path}...")
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection during initialization.")
        return False

    try:
        with conn: # Sử dụng transaction
            cursor = conn.cursor()
            logging.info("[DB Manager] Ensuring database tables and indexes exist...")

            # Incidents Table
            logging.debug("[DB Init] Creating table 'incidents' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    pod_key TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT,
                    initial_reasons TEXT,
                    k8s_context TEXT,
                    sample_logs TEXT,
                    input_prompt TEXT,
                    raw_ai_response TEXT,
                    root_cause TEXT,
                    troubleshooting_steps TEXT
                )""")
            logging.debug("[DB Init] Creating index 'idx_incidents_timestamp'...")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_timestamp ON incidents (timestamp)")
            logging.debug("[DB Init] Creating index 'idx_incidents_pod_key'...")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_pod_key ON incidents (pod_key)")
            logging.debug("[DB Init] Creating index 'idx_incidents_severity'...")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents (severity)")
            logging.debug("[DB Init] Table 'incidents' and indexes ensured.")

            # Daily Stats Table
            logging.debug("[DB Init] Creating table 'daily_stats' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    model_calls INTEGER DEFAULT 0,
                    telegram_alerts INTEGER DEFAULT 0,
                    incident_count INTEGER DEFAULT 0
                )""")
            logging.debug("[DB Init] Table 'daily_stats' ensured.")

            # Available Namespaces Cache
            logging.debug("[DB Init] Creating table 'available_namespaces' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS available_namespaces (
                    name TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL
                )""")
            logging.debug("[DB Init] Table 'available_namespaces' ensured.")

            # Configuration Table
            logging.debug("[DB Init] Creating table 'agent_config' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_config (
                    agent_id TEXT,
                    key TEXT NOT NULL,
                    value TEXT,
                    PRIMARY KEY (agent_id, key)
                )""")
            logging.debug("[DB Init] Creating index 'idx_agent_config_agent_id'...")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_config_agent_id ON agent_config (agent_id)")
            logging.debug("[DB Init] Table 'agent_config' and indexes ensured.")

            # Alert Cooldown Table
            logging.debug("[DB Init] Creating table 'alert_cooldown' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alert_cooldown (
                    pod_key TEXT PRIMARY KEY,
                    cooldown_until TEXT NOT NULL
                )""")
            logging.debug("[DB Init] Table 'alert_cooldown' ensured.")

            # Users Table
            logging.debug("[DB Init] Creating table 'users' if not exists...")
            cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'user',
                        fullname TEXT
                    )""")
            logging.debug("[DB Init] Table 'users' ensured.")

            # Active Agents Table
            logging.debug("[DB Init] Creating/Altering table 'active_agents' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS active_agents (
                    agent_id TEXT PRIMARY KEY,
                    cluster_name TEXT,
                    first_seen_timestamp TEXT NOT NULL,
                    last_seen_timestamp TEXT NOT NULL,
                    agent_version TEXT,
                    metadata TEXT,
                    k8s_version TEXT,
                    node_count INTEGER
                )""")
            # Add columns if they don't exist
            table_info = cursor.execute("PRAGMA table_info(active_agents)").fetchall()
            column_names = [col['name'] for col in table_info]
            if 'k8s_version' not in column_names:
                logging.debug("[DB Init] Adding column 'k8s_version' to 'active_agents'...")
                cursor.execute("ALTER TABLE active_agents ADD COLUMN k8s_version TEXT")
                logging.info("[DB Manager] Added 'k8s_version' column to 'active_agents' table.")
            if 'node_count' not in column_names:
                logging.debug("[DB Init] Adding column 'node_count' to 'active_agents'...")
                cursor.execute("ALTER TABLE active_agents ADD COLUMN node_count INTEGER")
                logging.info("[DB Manager] Added 'node_count' column to 'active_agents' table.")
            logging.debug("[DB Init] Table 'active_agents' ensured.")

            # Ensure default GLOBAL configs
            if default_configs and isinstance(default_configs, dict):
                inserted_count = 0
                logging.debug("[DB Init] Inserting default global config values if they don't exist...")
                for key, value in default_configs.items():
                    cursor.execute("""
                        INSERT OR IGNORE INTO agent_config (agent_id, key, value)
                        VALUES (NULL, ?, ?)
                    """, (key, str(value)))
                    if cursor.rowcount > 0:
                        inserted_count += 1
                if inserted_count > 0:
                     logging.info(f"[DB Manager] Inserted {inserted_count} new default global config values.")
                else:
                     logging.debug("[DB Init] Default global config values already exist or none provided.")
            else:
                logging.warning("[DB Manager] No default global configs provided to init_db.")

            logging.info("[DB Manager] All tables and indexes ensured successfully within transaction.")
        # Kết thúc transaction (tự động commit nếu không có lỗi)
        logging.info(f"[DB Manager] Database initialization/check complete at {db_path}")
        return True
    except sqlite3.OperationalError as op_err:
        # Log lỗi cụ thể hơn
        logging.error(f"[DB Manager] Database operational error during initialization: {op_err}", exc_info=True)
        # Kiểm tra các lỗi cụ thể nếu cần
        if "duplicate column name" in str(op_err) or "index already exists" in str(op_err):
             logging.warning(f"[DB Manager] DB init: {op_err}. This is expected if columns/indexes already exist.")
             # Có thể coi là thành công nếu lỗi chỉ là do đã tồn tại
             # return True # Tuy nhiên, để an toàn, vẫn trả về False nếu có lỗi
        return False
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Generic database error during initialization: {e}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error during DB initialization: {e}", exc_info=True)
        return False
    finally:
        if conn: conn.close()

# --- Giữ nguyên các hàm khác ---
def update_agent_heartbeat(db_path, agent_id, cluster_name, timestamp_iso, agent_version=None, metadata=None, cluster_info=None):
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection for agent heartbeat {agent_id}"); return

    metadata_json = json.dumps(metadata) if metadata else None
    k8s_version = cluster_info.get('k8s_version', None) if cluster_info else None
    node_count = cluster_info.get('node_count', None) if cluster_info else None

    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO active_agents (
                    agent_id, cluster_name, first_seen_timestamp, last_seen_timestamp,
                    agent_version, metadata, k8s_version, node_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    last_seen_timestamp = excluded.last_seen_timestamp,
                    cluster_name = excluded.cluster_name,
                    agent_version = COALESCE(excluded.agent_version, agent_version),
                    metadata = COALESCE(excluded.metadata, metadata),
                    k8s_version = COALESCE(excluded.k8s_version, k8s_version),
                    node_count = COALESCE(excluded.node_count, node_count)
            ''', (agent_id, cluster_name, timestamp_iso, timestamp_iso,
                  agent_version, metadata_json, k8s_version, node_count))
            logging.debug(f"[DB Manager] Updated heartbeat/info for agent: {agent_id} at {timestamp_iso} (k8s: {k8s_version}, nodes: {node_count}, version: {agent_version})") # Log thêm version
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error updating agent heartbeat for {agent_id}: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error updating agent heartbeat for {agent_id}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def get_active_agents(db_path, timeout_seconds=300):
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
                SELECT agent_id, cluster_name, first_seen_timestamp, last_seen_timestamp,
                       agent_version, metadata, k8s_version, node_count
                FROM active_agents
                WHERE last_seen_timestamp >= ?
                ORDER BY last_seen_timestamp DESC
            ''', (threshold_iso,))
            rows = cursor.fetchall()
            for row in rows:
                agent_data = dict(row)
                if agent_data.get('metadata'):
                    try:
                        agent_data['metadata'] = json.loads(agent_data['metadata'])
                    except json.JSONDecodeError:
                        logging.warning(f"Could not parse metadata JSON for agent {agent_data['agent_id']}")
                        agent_data['metadata'] = None
                agents.append(agent_data)
            logging.info(f"[DB Manager] Found {len(agents)} active agents within last {timeout_seconds} seconds.")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error getting active agents: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error getting active agents: {e}", exc_info=True)
    finally:
        if conn: conn.close()
    return agents

def cleanup_inactive_agents(db_path, timeout_seconds=86400):
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection for cleaning up agents"); return 0
    deleted_count = 0
    try:
        threshold_time = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        threshold_iso = threshold_time.isoformat()
        with conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM active_agents WHERE last_seen_timestamp < ?', (threshold_iso,))
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

def load_all_global_config(db_path):
    config_from_db = {}
    conn = _get_db_connection(db_path)
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM agent_config WHERE agent_id IS NULL") # Query bảng agent_config
            rows = cursor.fetchall()
            config_from_db = {row['key']: row['value'] for row in rows}
            logging.debug(f"[DB Manager] Loaded {len(config_from_db)} global config items from DB.")
        except sqlite3.Error as e: # Bắt lỗi cụ thể của SQLite
            logging.error(f"[DB Manager] Database error loading global config: {e}") # Log lỗi
        except Exception as e:
            logging.error(f"[DB Manager] Unexpected error loading global config: {e}", exc_info=True)
        finally:
            if conn: conn.close()
    else:
        logging.error("[DB Manager] Failed to get DB connection for loading global config.")
    return config_from_db

def save_global_config(db_path, config_dict):
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("Failed to get DB connection for saving global config.")
        return False, "Database connection failed"
    try:
        with conn:
            cursor = conn.cursor()
            for key, value in config_dict.items():
                value_str = str(value) if value is not None else None
                if value_str is not None:
                    cursor.execute("INSERT OR REPLACE INTO agent_config (agent_id, key, value) VALUES (NULL, ?, ?)", (key, value_str))
                    value_log = value_str[:50] + '...' if len(value_str) > 50 else value_str
                    logging.info(f"Saved global config key='{key}' value='{value_log}'")
                else:
                    logging.warning(f"Attempted to save None value for global key '{key}'. Ignoring.")
        return True, "Global configuration saved successfully."
    except sqlite3.Error as e:
        logging.error(f"Database error saving global configuration: {e}", exc_info=True)
        return False, f"Database error: {e}"
    except Exception as e:
        logging.error(f"Unexpected error saving global configuration: {e}", exc_info=True)
        return False, "Unexpected error saving global configuration."
    finally:
         if conn: conn.close()

def load_agent_config(db_path, agent_id):
    config_from_db = {}
    conn = _get_db_connection(db_path)
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM agent_config WHERE agent_id = ?", (agent_id,))
            rows = cursor.fetchall()
            config_from_db = {row['key']: row['value'] for row in rows}
            logging.debug(f"[DB Manager] Loaded {len(config_from_db)} config items for agent '{agent_id}'.")
        except sqlite3.Error as e:
            logging.error(f"[DB Manager] Database error loading config for agent {agent_id}: {e}")
        except Exception as e:
            logging.error(f"[DB Manager] Unexpected error loading config for agent {agent_id}: {e}", exc_info=True)
        finally:
            if conn: conn.close()
    else:
        logging.error(f"[DB Manager] Failed to get DB connection for loading config for agent {agent_id}.")
    return config_from_db

def save_agent_config(db_path, agent_id, config_dict):
    if not agent_id:
        logging.error("[DB Manager] Cannot save agent config: agent_id is missing.")
        return False, "Agent ID is required."

    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"Failed to get DB connection for saving config for agent {agent_id}.")
        return False, "Database connection failed"
    try:
        with conn:
            cursor = conn.cursor()
            for key, value in config_dict.items():
                value_str = str(value) if value is not None else None
                if value_str is not None:
                    cursor.execute("INSERT OR REPLACE INTO agent_config (agent_id, key, value) VALUES (?, ?, ?)", (agent_id, key, value_str))
                    value_log = value_str[:50] + '...' if len(value_str) > 50 else value_str
                    logging.info(f"Saved config for agent='{agent_id}' key='{key}' value='{value_log}'")
                else:
                    logging.warning(f"Attempted to save None value for agent '{agent_id}' key '{key}'. Ignoring.")
        return True, f"Configuration for agent '{agent_id}' saved successfully."
    except sqlite3.Error as e:
        logging.error(f"Database error saving configuration for agent {agent_id}: {e}", exc_info=True)
        return False, f"Database error: {e}"
    except Exception as e:
        logging.error(f"Unexpected error saving configuration for agent {agent_id}: {e}", exc_info=True)
        return False, f"Unexpected error saving configuration for agent {agent_id}."
    finally:
         if conn: conn.close()

def record_incident(db_path, pod_key, severity, summary, initial_reasons, k8s_context, sample_logs,
                    alert_severity_levels,
                    input_prompt=None, raw_ai_response=None, root_cause=None, troubleshooting_steps=None):
    timestamp_str = datetime.now(timezone.utc).isoformat()
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection for recording incident {pod_key}"); return
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
            logging.info(f"[DB Manager] Recorded incident for {pod_key} with severity {severity}")

            today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
            cursor.execute('UPDATE daily_stats SET incident_count = incident_count + 1 WHERE date = ?', (today_str,))
            logging.debug(f"[DB Manager] Incremented daily incident count for {today_str}.")

    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error recording incident for {pod_key}: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error recording incident for {pod_key}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def is_pod_in_cooldown(db_path, pod_key):
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection checking cooldown for {pod_key}"); return False
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
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error checking cooldown for {pod_key}: {e}"); return False
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error checking cooldown for {pod_key}: {e}", exc_info=True); return False
    finally:
        if conn: conn.close()

def set_pod_cooldown(db_path, pod_key, cooldown_minutes):
    if cooldown_minutes <= 0:
         logging.debug(f"[DB Manager] Cooldown minutes is {cooldown_minutes}. Skipping setting cooldown for {pod_key}.")
         return

    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection setting cooldown for {pod_key}"); return
    try:
        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
        cooldown_until_iso = cooldown_until.isoformat()
        with conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO alert_cooldown (pod_key, cooldown_until) VALUES (?, ?)",
                            (pod_key, cooldown_until_iso))
            logging.info(f"[DB Manager] Set cooldown for pod {pod_key} until {cooldown_until_iso} ({cooldown_minutes} minutes)")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error setting cooldown for {pod_key}: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error setting cooldown for {pod_key}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def update_daily_stats(db_path):
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

    if calls_to_add == 0 and alerts_to_add == 0:
        logging.debug("[DB Manager] No new model calls or alerts to update in daily stats.")
        return

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection for updating daily stats"); return
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
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error updating daily stats: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error updating daily stats: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def update_available_namespaces_in_db(db_path, namespaces):
    if not namespaces:
        logging.info("[DB Manager] No active namespaces provided to update in DB cache."); return

    timestamp = datetime.now(timezone.utc).isoformat()
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection for updating available namespaces cache"); return
    try:
        with conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(namespaces))
            cursor.execute(f"DELETE FROM available_namespaces WHERE name NOT IN ({placeholders})", tuple(namespaces))
            for ns in namespaces:
                cursor.execute("INSERT OR REPLACE INTO available_namespaces (name, last_seen) VALUES (?, ?)", (ns, timestamp))
            logging.info(f"[DB Manager] Updated available_namespaces cache in DB with {len(namespaces)} namespaces.")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error updating available namespaces cache: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error updating available namespaces cache: {e}", exc_info=True)
    finally:
        if conn: conn.close()

