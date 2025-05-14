# -*- coding: utf-8 -*-
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

# --- Các hàm _get_db_connection, init_db, update_agent_heartbeat, ... giữ nguyên ---
# --- Chỉ cần đảm bảo save/load config hoạt động đúng ---

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
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database connection error to {db_path}: {e}")
        return None
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error connecting to database {db_path}: {e}", exc_info=True)
        return None

def init_db(db_path, default_configs={}):
    logging.info(f"[DB Manager] Attempting to initialize/update database schema at {db_path}...")
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection during initialization.")
        return False

    try:
        with conn:
            cursor = conn.cursor()
            logging.info("[DB Manager] Ensuring database tables and indexes exist...")

            logging.debug("[DB Init] Creating/Altering table 'incidents'...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    environment_name TEXT NOT NULL,
                    environment_type TEXT NOT NULL,
                    resource_type TEXT,
                    resource_name TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT,
                    initial_reasons TEXT,
                    environment_context TEXT,
                    sample_logs TEXT,
                    input_prompt TEXT,
                    raw_ai_response TEXT,
                    root_cause TEXT,
                    troubleshooting_steps TEXT
                )""")
            table_info_incidents = cursor.execute("PRAGMA table_info(incidents)").fetchall()
            incident_columns = [col['name'] for col in table_info_incidents]
            if 'environment_name' not in incident_columns:
                logging.warning("[DB Init] 'incidents' table exists without new columns. Adding them.")
                try:
                    cursor.execute("ALTER TABLE incidents ADD COLUMN environment_name TEXT")
                    cursor.execute("ALTER TABLE incidents ADD COLUMN environment_type TEXT")
                    cursor.execute("ALTER TABLE incidents ADD COLUMN resource_type TEXT")
                    cursor.execute("ALTER TABLE incidents ADD COLUMN resource_name TEXT")
                    cursor.execute("ALTER TABLE incidents ADD COLUMN environment_context TEXT")
                    logging.info("[DB Init] Added new columns to 'incidents' table.")
                except sqlite3.OperationalError as alter_err:
                     logging.error(f"[DB Init] Failed to alter 'incidents' table: {alter_err}. Manual migration might be required.")


            logging.debug("[DB Init] Creating indexes for 'incidents'...")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_timestamp ON incidents (timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_env_res ON incidents (environment_name, resource_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_env_type ON incidents (environment_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents (severity)")
            logging.debug("[DB Init] Table 'incidents' and indexes ensured.")

            logging.debug("[DB Init] Creating table 'daily_stats' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    model_calls INTEGER DEFAULT 0,
                    telegram_alerts INTEGER DEFAULT 0,
                    incident_count INTEGER DEFAULT 0
                )""")
            logging.debug("[DB Init] Table 'daily_stats' ensured.")

            logging.debug("[DB Init] Creating table 'available_namespaces' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS available_namespaces (
                    name TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL
                )""")
            logging.debug("[DB Init] Table 'available_namespaces' ensured.")

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

            logging.debug("[DB Init] Creating/Altering table 'alert_cooldown'...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alert_cooldown (
                    resource_identifier TEXT PRIMARY KEY,
                    cooldown_until TEXT NOT NULL
                )""")
            table_info_cooldown = cursor.execute("PRAGMA table_info(alert_cooldown)").fetchall()
            cooldown_columns = [col['name'] for col in table_info_cooldown]
            if 'pod_key' in cooldown_columns and 'resource_identifier' not in cooldown_columns:
                 logging.warning("[DB Init] Renaming 'pod_key' to 'resource_identifier' in 'alert_cooldown'.")
                 try:
                     cursor.execute("ALTER TABLE alert_cooldown RENAME COLUMN pod_key TO resource_identifier")
                     logging.info("[DB Init] Renamed 'pod_key' to 'resource_identifier'.")
                 except sqlite3.OperationalError:
                     logging.error("[DB Init] Failed to rename 'pod_key' column. Manual migration might be required for 'alert_cooldown'.")
            logging.debug("[DB Init] Table 'alert_cooldown' ensured.")


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

            logging.debug("[DB Init] Creating/Altering table 'active_agents'...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS active_agents (
                    agent_id TEXT PRIMARY KEY,
                    environment_name TEXT,
                    environment_type TEXT,
                    first_seen_timestamp TEXT NOT NULL,
                    last_seen_timestamp TEXT NOT NULL,
                    agent_version TEXT,
                    environment_info TEXT,
                    k8s_version TEXT,
                    node_count INTEGER
                )""")
            table_info_agents = cursor.execute("PRAGMA table_info(active_agents)").fetchall()
            agent_columns = [col['name'] for col in table_info_agents]

            if 'cluster_name' in agent_columns and 'environment_name' not in agent_columns:
                 logging.warning("[DB Init] Renaming 'cluster_name' to 'environment_name' in 'active_agents'.")
                 try: cursor.execute("ALTER TABLE active_agents RENAME COLUMN cluster_name TO environment_name")
                 except sqlite3.OperationalError: logging.error("[DB Init] Failed to rename 'cluster_name'. Manual migration needed.")

            if 'environment_type' not in agent_columns:
                logging.debug("[DB Init] Adding column 'environment_type' to 'active_agents'...")
                try: cursor.execute("ALTER TABLE active_agents ADD COLUMN environment_type TEXT")
                except sqlite3.OperationalError as e: logging.warning(f"Could not add environment_type: {e}")
                logging.info("[DB Manager] Added 'environment_type' column to 'active_agents' table.")

            if 'metadata' in agent_columns and 'environment_info' not in agent_columns:
                 logging.warning("[DB Init] Renaming 'metadata' to 'environment_info' in 'active_agents'.")
                 try: cursor.execute("ALTER TABLE active_agents RENAME COLUMN metadata TO environment_info")
                 except sqlite3.OperationalError: logging.error("[DB Init] Failed to rename 'metadata'. Manual migration needed.")
            elif 'environment_info' not in agent_columns:
                 logging.debug("[DB Init] Adding column 'environment_info' to 'active_agents'...")
                 try: cursor.execute("ALTER TABLE active_agents ADD COLUMN environment_info TEXT")
                 except sqlite3.OperationalError as e: logging.warning(f"Could not add environment_info: {e}")
                 logging.info("[DB Manager] Added 'environment_info' column to 'active_agents' table.")

            if 'k8s_version' not in agent_columns:
                logging.debug("[DB Init] Adding column 'k8s_version' to 'active_agents'...")
                try: cursor.execute("ALTER TABLE active_agents ADD COLUMN k8s_version TEXT")
                except sqlite3.OperationalError as e: logging.warning(f"Could not add k8s_version: {e}")
            if 'node_count' not in agent_columns:
                logging.debug("[DB Init] Adding column 'node_count' to 'active_agents'...")
                try: cursor.execute("ALTER TABLE active_agents ADD COLUMN node_count INTEGER")
                except sqlite3.OperationalError as e: logging.warning(f"Could not add node_count: {e}")
            logging.debug("[DB Init] Table 'active_agents' ensured.")

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
        logging.info(f"[DB Manager] Database initialization/check complete at {db_path}")
        return True
    except sqlite3.OperationalError as op_err:
        logging.error(f"[DB Manager] Database operational error during initialization: {op_err}", exc_info=True)
        if "duplicate column name" in str(op_err) or "index already exists" in str(op_err) or "rename column" in str(op_err).lower():
             logging.warning(f"[DB Manager] DB init: {op_err}. This might be expected if columns/indexes already exist or were renamed.")
             return True
        return False
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Generic database error during initialization: {e}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error during DB initialization: {e}", exc_info=True)
        return False
    finally:
        if conn: conn.close()

def update_agent_heartbeat(db_path, agent_id, environment_name, timestamp_iso,
                           agent_version=None, environment_type=None, environment_info=None):
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection for agent heartbeat {agent_id}"); return

    env_info_json = None
    k8s_version = None
    node_count = None
    if environment_info and isinstance(environment_info, dict):
        try:
            env_info_json = json.dumps(environment_info)
            if environment_type == 'kubernetes':
                k8s_version = environment_info.get('kubernetes_info', {}).get('k8s_version')
                node_count = environment_info.get('kubernetes_info', {}).get('node_count')
        except TypeError as json_err:
             logging.error(f"Failed to serialize environment_info for agent {agent_id}: {json_err} - Data: {environment_info}")
             env_info_json = json.dumps({"error": "serialization failed"})
    else:
         env_info_json = str(environment_info) if environment_info is not None else None

    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO active_agents (
                    agent_id, environment_name, environment_type,
                    first_seen_timestamp, last_seen_timestamp, agent_version,
                    environment_info, k8s_version, node_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    environment_name = excluded.environment_name,
                    environment_type = COALESCE(excluded.environment_type, environment_type),
                    last_seen_timestamp = excluded.last_seen_timestamp,
                    agent_version = COALESCE(excluded.agent_version, agent_version),
                    environment_info = COALESCE(excluded.environment_info, environment_info),
                    k8s_version = COALESCE(excluded.k8s_version, k8s_version),
                    node_count = COALESCE(excluded.node_count, node_count)
                WHERE excluded.last_seen_timestamp > active_agents.last_seen_timestamp
            ''', (agent_id, environment_name, environment_type,
                  timestamp_iso, timestamp_iso, agent_version,
                  env_info_json, k8s_version, node_count))
            if cursor.rowcount > 0:
                 logging.debug(f"[DB Manager] Updated heartbeat/info for agent: {agent_id} at {timestamp_iso} (Env: {environment_name}, Type: {environment_type}, Version: {agent_version})")
            else:
                 cursor.execute("SELECT agent_id FROM active_agents WHERE agent_id = ?", (agent_id,))
                 if not cursor.fetchone():
                      logging.error(f"[DB Manager] Failed to INSERT new agent {agent_id} during heartbeat update.")
                 else:
                      logging.debug(f"[DB Manager] Heartbeat for agent {agent_id} not updated (timestamp might not be newer).")

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
                SELECT agent_id, environment_name, environment_type,
                       first_seen_timestamp, last_seen_timestamp,
                       agent_version, environment_info, k8s_version, node_count
                FROM active_agents
                WHERE last_seen_timestamp >= ?
                ORDER BY last_seen_timestamp DESC
            ''', (threshold_iso,))
            rows = cursor.fetchall()
            for row in rows:
                agent_data = dict(row)
                env_info_raw = agent_data.get('environment_info')
                if env_info_raw:
                    try:
                        agent_data['environment_info'] = json.loads(env_info_raw)
                    except json.JSONDecodeError:
                        logging.warning(f"Could not parse environment_info JSON for agent {agent_data['agent_id']}: {env_info_raw[:100]}...")
                        agent_data['environment_info'] = {"raw": env_info_raw}
                    except Exception as parse_err:
                         logging.error(f"Unexpected error parsing environment_info for agent {agent_data['agent_id']}: {parse_err}")
                         agent_data['environment_info'] = {"error": str(parse_err)}
                else:
                     agent_data['environment_info'] = {}
                agents.append(agent_data)
            logging.info(f"[DB Manager] Found {len(agents)} active agents within last {timeout_seconds} seconds.")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error getting active agents: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error getting active agents: {e}", exc_info=True)
    finally:
        if conn: conn.close()
    return agents

def get_agent_info(db_path, agent_id):
    agent_info = None
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection for getting info for agent {agent_id}"); return None
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT agent_id, environment_name, environment_type, environment_info
                FROM active_agents
                WHERE agent_id = ?
            ''', (agent_id,))
            row = cursor.fetchone()
            if row:
                agent_info = dict(row)
                env_info_raw = agent_info.get('environment_info')
                if env_info_raw:
                    try:
                        agent_info['environment_info'] = json.loads(env_info_raw)
                    except (json.JSONDecodeError, TypeError):
                        logging.warning(f"Could not parse environment_info JSON for agent {agent_id} in get_agent_info.")
                        agent_info['environment_info'] = {"raw": env_info_raw}
                else:
                    agent_info['environment_info'] = {}
            else:
                 logging.warning(f"[DB Manager] Agent {agent_id} not found in get_agent_info.")

    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error getting agent info for {agent_id}: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error getting agent info for {agent_id}: {e}", exc_info=True)
    finally:
        if conn: conn.close()
    return agent_info

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
            cursor.execute("SELECT key, value FROM agent_config WHERE agent_id IS NULL")
            rows = cursor.fetchall()
            config_from_db = {row['key']: row['value'] for row in rows}
            logging.debug(f"[DB Manager] Loaded {len(config_from_db)} global config items from DB.")
        except sqlite3.Error as e:
            logging.error(f"[DB Manager] Database error loading global config: {e}")
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
                # --- Đảm bảo value là string hoặc None ---
                # Nếu value là list hoặc dict, chuyển thành JSON string
                if isinstance(value, (list, dict)):
                    value_str = json.dumps(value)
                elif value is not None:
                    value_str = str(value)
                else:
                    value_str = None
                # -----------------------------------------

                if value_str is not None:
                    cursor.execute("INSERT OR REPLACE INTO agent_config (agent_id, key, value) VALUES (NULL, ?, ?)", (key, value_str))
                    value_log = value_str[:50] + '...' if len(value_str) > 50 else value_str
                    logging.info(f"Saved global config key='{key}' value='{value_log}'")
                else:
                    # Có thể muốn xóa key nếu value là None
                    # cursor.execute("DELETE FROM agent_config WHERE agent_id IS NULL AND key = ?", (key,))
                    # logging.info(f"Deleted global config key='{key}' because value was None.")
                    logging.warning(f"Attempted to save None value for global key '{key}'. Ignoring save (or implement delete).")
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
                # --- Xử lý value là list/dict thành JSON string ---
                if isinstance(value, (list, dict)):
                    try:
                        value_str = json.dumps(value)
                    except TypeError as json_err:
                         logging.error(f"Could not serialize value for agent '{agent_id}' key '{key}': {json_err}. Value: {value}")
                         continue # Bỏ qua key này nếu không serialize được
                elif value is not None:
                    value_str = str(value)
                else:
                    value_str = None
                # -------------------------------------------------

                if value_str is not None:
                    cursor.execute("INSERT OR REPLACE INTO agent_config (agent_id, key, value) VALUES (?, ?, ?)", (agent_id, key, value_str))
                    value_log = value_str[:50] + '...' if len(value_str) > 50 else value_str
                    logging.info(f"Saved config for agent='{agent_id}' key='{key}' value='{value_log}'")
                else:
                    # Tùy chọn: Xóa key nếu value là None
                    # cursor.execute("DELETE FROM agent_config WHERE agent_id = ? AND key = ?", (agent_id, key))
                    # logging.info(f"Deleted config for agent='{agent_id}' key='{key}' because value was None.")
                    logging.warning(f"Attempted to save None value for agent '{agent_id}' key '{key}'. Ignoring save (or implement delete).")
        return True, f"Configuration for agent '{agent_id}' saved successfully."
    except sqlite3.Error as e:
        logging.error(f"Database error saving configuration for agent {agent_id}: {e}", exc_info=True)
        return False, f"Database error: {e}"
    except Exception as e:
        logging.error(f"Unexpected error saving configuration for agent {agent_id}: {e}", exc_info=True)
        return False, f"Unexpected error saving configuration for agent {agent_id}."
    finally:
         if conn: conn.close()

def record_incident(db_path, environment_name, environment_type, resource_type, resource_name,
                    severity, summary, initial_reasons, environment_context, sample_logs,
                    input_prompt=None, raw_ai_response=None, root_cause=None, troubleshooting_steps=None):
    timestamp_str = datetime.now(timezone.utc).isoformat()
    resource_identifier = f"{environment_name}/{resource_name}"
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection for recording incident {resource_identifier}"); return
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO incidents (
                    timestamp, environment_name, environment_type, resource_type, resource_name,
                    severity, summary, initial_reasons, environment_context, sample_logs,
                    input_prompt, raw_ai_response, root_cause, troubleshooting_steps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (timestamp_str, environment_name, environment_type, resource_type, resource_name,
                    severity, summary, initial_reasons, environment_context, sample_logs,
                    input_prompt, raw_ai_response, root_cause, troubleshooting_steps))
            logging.info(f"[DB Manager] Recorded incident for {resource_identifier} (Type: {resource_type}, EnvType: {environment_type}) with severity {severity}")

            today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
            cursor.execute('UPDATE daily_stats SET incident_count = incident_count + 1 WHERE date = ?', (today_str,))
            logging.debug(f"[DB Manager] Incremented daily incident count for {today_str}.")

    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error recording incident for {resource_identifier}: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error recording incident for {resource_identifier}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def is_resource_in_cooldown(db_path, resource_identifier):
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection checking cooldown for {resource_identifier}"); return False
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT cooldown_until FROM alert_cooldown WHERE resource_identifier = ?", (resource_identifier,))
            result = cursor.fetchone()
            if result:
                cooldown_until_str = result['cooldown_until']
                if cooldown_until_str > now_iso:
                    logging.info(f"[DB Manager] Resource {resource_identifier} is in cooldown until {cooldown_until_str}.")
                    return True
                else:
                    cursor.execute("DELETE FROM alert_cooldown WHERE resource_identifier = ?", (resource_identifier,))
                    logging.info(f"[DB Manager] Cooldown expired for resource {resource_identifier} (was {cooldown_until_str}).")
                    return False
            return False
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error checking cooldown for {resource_identifier}: {e}"); return False
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error checking cooldown for {resource_identifier}: {e}", exc_info=True); return False
    finally:
        if conn: conn.close()

def set_resource_cooldown(db_path, resource_identifier, cooldown_minutes):
    if cooldown_minutes <= 0:
         logging.debug(f"[DB Manager] Cooldown minutes is {cooldown_minutes}. Skipping setting cooldown for {resource_identifier}.")
         return

    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection setting cooldown for {resource_identifier}"); return
    try:
        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
        cooldown_until_iso = cooldown_until.isoformat()
        with conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO alert_cooldown (resource_identifier, cooldown_until) VALUES (?, ?)",
                            (resource_identifier, cooldown_until_iso))
            logging.info(f"[DB Manager] Set cooldown for resource {resource_identifier} until {cooldown_until_iso} ({cooldown_minutes} minutes)")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error setting cooldown for {resource_identifier}: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error setting cooldown for {resource_identifier}: {e}", exc_info=True)
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

