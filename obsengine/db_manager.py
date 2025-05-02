# ai-agent1/obsengine/db_manager.py
import sqlite3
import os
import logging
import json
from datetime import datetime, timedelta, timezone

# Import providers for stats (handle potential ImportError)
try:
    import ai_providers
    import notifier
except ImportError:
    logging.error("[DB Manager] Failed to import ai_providers or notifier for stats update.")
    ai_providers = None
    notifier = None

# --- Database Connection ---
def _get_db_connection(db_path):
    """Gets a connection to the SQLite database."""
    # Ensure directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir)
            logging.info(f"[DB Manager] Created directory for database: {db_dir}")
        except OSError as e:
            logging.error(f"[DB Manager] Could not create DB directory {db_dir}: {e}")
            return None
    # Connect to the database
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row # Return rows as dict-like objects
        # Enable foreign keys if needed in the future
        # conn.execute("PRAGMA foreign_keys = ON")
        return conn
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database connection error to {db_path}: {e}")
        return None
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error connecting to database {db_path}: {e}", exc_info=True)
        return None

# --- Database Initialization ---
def init_db(db_path, default_configs={}):
    """Initializes the database schema and default global configurations."""
    logging.info(f"[DB Manager] Attempting to initialize database at {db_path}...")
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection during initialization.")
        return False

    try:
        with conn:
            cursor = conn.cursor()
            logging.info("[DB Manager] Ensuring database tables exist...")

            # --- Table Definitions ---
            # Incidents Table
            logging.debug("[DB Manager] Creating table 'incidents' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    pod_key TEXT NOT NULL, -- Format: cluster_name/namespace/pod_name
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
            logging.debug("[DB Manager] Table 'incidents' ensured.")

            # Daily Stats Table
            logging.debug("[DB Manager] Creating table 'daily_stats' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY, -- YYYY-MM-DD
                    model_calls INTEGER DEFAULT 0,
                    telegram_alerts INTEGER DEFAULT 0,
                    incident_count INTEGER DEFAULT 0
                )""")
            logging.debug("[DB Manager] Table 'daily_stats' ensured.")

            # Available Namespaces Cache (for fallback)
            logging.debug("[DB Manager] Creating table 'available_namespaces' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS available_namespaces (
                    name TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL
                )""")
            logging.debug("[DB Manager] Table 'available_namespaces' ensured.")

            # --- MODIFIED: Configuration Table ---
            # Stores both global and agent-specific settings.
            # agent_id is NULL for global settings.
            logging.debug("[DB Manager] Creating table 'agent_config' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_config (
                    agent_id TEXT,          -- NULL for global, specific ID/cluster_name otherwise
                    key TEXT NOT NULL,      -- Config key (e.g., 'scan_interval_seconds')
                    value TEXT,             -- Config value
                    PRIMARY KEY (agent_id, key) -- Composite primary key
                )""")
            logging.debug("[DB Manager] Table 'agent_config' ensured.")

            # Add index for faster lookup of global settings
            # This might fail if the table exists but the column doesn't (old schema)
            logging.debug("[DB Manager] Creating index 'idx_global_config' on agent_config(agent_id) if not exists...")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_global_config ON agent_config (agent_id) WHERE agent_id IS NULL")
            logging.debug("[DB Manager] Index 'idx_global_config' ensured.")

            # Add index for faster lookup of agent-specific settings
            logging.debug("[DB Manager] Creating index 'idx_agent_config_key' on agent_config(agent_id, key) if not exists...")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_config_key ON agent_config (agent_id, key)")
            logging.debug("[DB Manager] Index 'idx_agent_config_key' ensured.")
            # --- END MODIFICATION ---

            # Alert Cooldown Table
            logging.debug("[DB Manager] Creating table 'alert_cooldown' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alert_cooldown (
                    pod_key TEXT PRIMARY KEY, -- Format: cluster_name/namespace/pod_name
                    cooldown_until TEXT NOT NULL
                )""")
            logging.debug("[DB Manager] Table 'alert_cooldown' ensured.")

            # Users Table (for Portal)
            logging.debug("[DB Manager] Creating table 'users' if not exists...")
            cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL
                    )""")
            logging.debug("[DB Manager] Table 'users' ensured.")

            # Active Agents Table
            logging.debug("[DB Manager] Creating table 'active_agents' if not exists...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS active_agents (
                    agent_id TEXT PRIMARY KEY, -- Unique identifier for the agent instance (could be cluster_name or a generated UUID)
                    cluster_name TEXT,         -- Name of the cluster the agent belongs to
                    first_seen_timestamp TEXT NOT NULL,
                    last_seen_timestamp TEXT NOT NULL,
                    agent_version TEXT,        -- Version of the agent software
                    metadata TEXT              -- JSON string for additional metadata
                )""")
            logging.debug("[DB Manager] Table 'active_agents' ensured.")
            # --- End Table Definitions ---

            # Ensure default GLOBAL configs (agent_id is NULL)
            if default_configs and isinstance(default_configs, dict):
                inserted_count = 0
                logging.debug("[DB Manager] Inserting default global config values if they don't exist...")
                for key, value in default_configs.items():
                    # Insert global config only if it doesn't exist
                    cursor.execute("""
                        INSERT OR IGNORE INTO agent_config (agent_id, key, value)
                        VALUES (NULL, ?, ?)
                    """, (key, str(value))) # Ensure value is string
                    if cursor.rowcount > 0:
                        inserted_count += 1
                if inserted_count > 0:
                     logging.info(f"[DB Manager] Inserted {inserted_count} new default global config values.")
                else:
                     logging.debug("[DB Manager] Default global config values already exist or none provided.")
            else:
                logging.warning("[DB Manager] No default global configs provided to init_db.")

            logging.info("[DB Manager] All tables and indexes ensured.")
        logging.info(f"[DB Manager] Database initialization/check complete at {db_path}")
        return True
    except sqlite3.OperationalError as op_err:
        # Specific handling for "no such column" which indicates schema mismatch
        if "no such column: agent_id" in str(op_err):
             logging.error(f"[DB Manager] Database schema error during initialization: {op_err}. "
                           f"The 'agent_config' table likely exists with an old structure missing the 'agent_id' column. "
                           f"Consider manually deleting the database file '{db_path}' if this is a new deployment or schema update.", exc_info=True)
        else:
             logging.error(f"[DB Manager] Database operational error during initialization: {op_err}", exc_info=True)
        return False
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Generic database error during initialization: {e}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error during DB initialization: {e}", exc_info=True)
        return False
    finally:
        if conn: conn.close()

# --- Agent Tracking Functions (Keep as is) ---
def update_agent_heartbeat(db_path, agent_id, cluster_name, timestamp_iso, agent_version=None, metadata=None):
    """Updates the last seen timestamp for an agent or registers a new one."""
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection for agent heartbeat {agent_id}"); return
    try:
        metadata_json = json.dumps(metadata) if metadata else None
        with conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO active_agents (agent_id, cluster_name, first_seen_timestamp, last_seen_timestamp, agent_version, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    last_seen_timestamp = excluded.last_seen_timestamp,
                    cluster_name = excluded.cluster_name,
                    agent_version = COALESCE(excluded.agent_version, agent_version), -- Keep old version if new is null
                    metadata = COALESCE(excluded.metadata, metadata) -- Keep old metadata if new is null
            ''', (agent_id, cluster_name, timestamp_iso, timestamp_iso, agent_version, metadata_json))
            logging.debug(f"[DB Manager] Updated heartbeat for agent: {agent_id} at {timestamp_iso}")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error updating agent heartbeat for {agent_id}: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error updating agent heartbeat for {agent_id}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def get_active_agents(db_path, timeout_seconds=300):
    """Retrieves a list of agents seen within the timeout period."""
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
                if agent_data.get('metadata'):
                    try:
                        agent_data['metadata'] = json.loads(agent_data['metadata'])
                    except json.JSONDecodeError:
                        logging.warning(f"Could not parse metadata JSON for agent {agent_data['agent_id']}")
                        agent_data['metadata'] = None # Set to None if invalid JSON
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
            cursor.execute('DELETE FROM active_agents WHERE last_seen_timestamp < ?', (threshold_iso,))
            deleted_count = cursor.rowcount
            if deleted_count > 0:
                logging.info(f"[DB Manager] Cleaned up {deleted_count} inactive agents (older than {timeout_seconds} seconds).")
            # Optionally, clean up agent-specific config for deleted agents
            # cursor.execute('DELETE FROM agent_config WHERE agent_id NOT IN (SELECT agent_id FROM active_agents)')
            # logging.info(f"Cleaned up config for {cursor.rowcount} inactive agents.")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error cleaning up inactive agents: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error cleaning up inactive agents: {e}", exc_info=True)
    finally:
        if conn: conn.close()
    return deleted_count

# --- Global Configuration Functions ---
def load_all_global_config(db_path):
    """Loads all global configuration settings (where agent_id is NULL)."""
    config_from_db = {}
    conn = _get_db_connection(db_path)
    if conn:
        try:
            cursor = conn.cursor()
            # Select only global settings
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
    """Saves global configuration settings (agent_id = NULL)."""
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
                    # Use INSERT OR REPLACE for global settings (agent_id IS NULL)
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


# --- Agent-Specific Configuration Functions ---

def load_agent_config(db_path, agent_id):
    """Loads configuration settings specifically for a given agent_id."""
    config_from_db = {}
    conn = _get_db_connection(db_path)
    if conn:
        try:
            cursor = conn.cursor()
            # Select settings for the specific agent_id
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
    """Saves configuration settings for a specific agent_id."""
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
                    # Use INSERT OR REPLACE with the specific agent_id
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


# --- Incident and Cooldown Functions ---
def record_incident(db_path, pod_key, severity, summary, initial_reasons, k8s_context, sample_logs,
                    alert_severity_levels, # No longer used for counting, but kept for function signature consistency
                    input_prompt=None, raw_ai_response=None, root_cause=None, troubleshooting_steps=None):
    """Records an incident in the database and updates the daily incident count."""
    timestamp_str = datetime.now(timezone.utc).isoformat()
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection for recording incident {pod_key}"); return
    try:
        with conn:
            cursor = conn.cursor()
            # Insert the incident details
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

            # --- FIX: Always update daily incident count, regardless of severity ---
            today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            # Ensure the row for today exists in daily_stats
            cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
            # Increment the incident count for today
            cursor.execute('UPDATE daily_stats SET incident_count = incident_count + 1 WHERE date = ?', (today_str,))
            logging.debug(f"[DB Manager] Incremented daily incident count for {today_str}.")
            # --- END FIX ---

    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error recording incident for {pod_key}: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error recording incident for {pod_key}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def is_pod_in_cooldown(db_path, pod_key):
    """Checks if a specific pod (including cluster) is currently in alert cooldown."""
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection checking cooldown for {pod_key}"); return False # Assume not in cooldown if DB fails
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
                    # Cooldown expired, remove the entry
                    cursor.execute("DELETE FROM alert_cooldown WHERE pod_key = ?", (pod_key,))
                    logging.info(f"[DB Manager] Cooldown expired for pod {pod_key} (was {cooldown_until_str}).")
                    return False
            return False # No cooldown entry found
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error checking cooldown for {pod_key}: {e}"); return False
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error checking cooldown for {pod_key}: {e}", exc_info=True); return False
    finally:
        if conn: conn.close()

def set_pod_cooldown(db_path, pod_key, cooldown_minutes):
    """Sets the alert cooldown period for a specific pod."""
    if cooldown_minutes <= 0:
         logging.debug(f"[DB Manager] Cooldown minutes is {cooldown_minutes}. Skipping setting cooldown for {pod_key}.")
         return # Don't set cooldown if duration is zero or negative

    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error(f"[DB Manager] Failed to get DB connection setting cooldown for {pod_key}"); return
    try:
        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
        cooldown_until_iso = cooldown_until.isoformat()
        with conn:
            cursor = conn.cursor()
            # Insert or replace the cooldown entry
            cursor.execute("INSERT OR REPLACE INTO alert_cooldown (pod_key, cooldown_until) VALUES (?, ?)",
                            (pod_key, cooldown_until_iso))
            logging.info(f"[DB Manager] Set cooldown for pod {pod_key} until {cooldown_until_iso} ({cooldown_minutes} minutes)")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error setting cooldown for {pod_key}: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error setting cooldown for {pod_key}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

# --- Stats and Namespace Cache Functions (Keep as is) ---
def update_daily_stats(db_path):
    """Updates daily counters for model calls and Telegram alerts."""
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
        return # No need to update if counters are zero

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection for updating daily stats"); return
    try:
        with conn:
            cursor = conn.cursor()
            # Ensure the row for today exists
            cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
            # Update the counters
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
    """Updates the cached list of available namespaces in the database."""
    if not namespaces:
        logging.info("[DB Manager] No active namespaces provided to update in DB cache."); return

    timestamp = datetime.now(timezone.utc).isoformat()
    conn = _get_db_connection(db_path)
    if conn is None:
        logging.error("[DB Manager] Failed to get DB connection for updating available namespaces cache"); return
    try:
        with conn:
            cursor = conn.cursor()
            # Prepare placeholders for efficient deletion check
            placeholders = ','.join('?' * len(namespaces))
            # Delete namespaces from cache that are no longer in the provided list
            cursor.execute(f"DELETE FROM available_namespaces WHERE name NOT IN ({placeholders})", tuple(namespaces))
            # Insert or update the current list of namespaces
            for ns in namespaces:
                cursor.execute("INSERT OR REPLACE INTO available_namespaces (name, last_seen) VALUES (?, ?)", (ns, timestamp))
            logging.info(f"[DB Manager] Updated available_namespaces cache in DB with {len(namespaces)} namespaces.")
    except sqlite3.Error as e:
        logging.error(f"[DB Manager] Database error updating available namespaces cache: {e}")
    except Exception as e:
        logging.error(f"[DB Manager] Unexpected error updating available namespaces cache: {e}", exc_info=True)
    finally:
        if conn: conn.close()
