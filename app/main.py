# ai-agent1/app/main.py

# --- Imports ---
import os
import time
import requests # Needed for Loki calls
import json
import logging
from datetime import datetime, timedelta, timezone, MINYEAR
from dotenv import load_dotenv
import re
from kubernetes import client, config, watch
from kubernetes.client.exceptions import ApiException
try:
    from zoneinfo import ZoneInfo
except ImportError:
    logging.error("zoneinfo module not found. Please use Python 3.9+ or install pytz.")
    exit(1)
import sqlite3
import threading
import ai_providers # <<< AI logic module
import notifier    # <<< Notification logic module

# --- Basic Setup ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Static Config from Environment (Defaults/Fallbacks) ---
LOKI_URL = os.environ.get("LOKI_URL", "http://loki-read.monitoring.svc.cluster.local:3100")
LOKI_SCAN_RANGE_MINUTES = int(os.environ.get("LOKI_SCAN_RANGE_MINUTES", 1))
LOKI_DETAIL_LOG_RANGE_MINUTES = int(os.environ.get("LOKI_DETAIL_LOG_RANGE_MINUTES", 30))
LOKI_QUERY_LIMIT = int(os.environ.get("LOKI_QUERY_LIMIT", 500))
EXCLUDED_NAMESPACES_STR = os.environ.get("EXCLUDED_NAMESPACES", "kube-node-lease,kube-public")
EXCLUDED_NAMESPACES = {ns.strip() for ns in EXCLUDED_NAMESPACES_STR.split(',') if ns.strip()}
DB_PATH = os.environ.get("DB_PATH", "/data/agent_stats.db")
STATS_UPDATE_INTERVAL_SECONDS = int(os.environ.get("STATS_UPDATE_INTERVAL_SECONDS", 300))
NAMESPACE_REFRESH_INTERVAL_SECONDS = int(os.environ.get("NAMESPACE_REFRESH_INTERVAL_SECONDS", 3600))
CONFIG_REFRESH_INTERVAL_SECONDS = int(os.environ.get("CONFIG_REFRESH_INTERVAL_SECONDS", 60))
LOCAL_GEMINI_ENDPOINT_URL = os.environ.get("LOCAL_GEMINI_ENDPOINT")

# Default AI settings
DEFAULT_ENABLE_AI_ANALYSIS = os.environ.get("ENABLE_AI_ANALYSIS", "true").lower() == "true"
DEFAULT_AI_PROVIDER = os.environ.get("AI_PROVIDER", "gemini").lower()
DEFAULT_AI_MODEL_IDENTIFIER = os.environ.get("AI_MODEL_IDENTIFIER", "gemini-1.5-flash")
# Default monitored namespaces
DEFAULT_MONITORED_NAMESPACES_STR = os.environ.get("DEFAULT_MONITORED_NAMESPACES", "kube-system,default")
# Default Telegram Alert setting
DEFAULT_ENABLE_TELEGRAM_ALERTS = False
# Default General settings
DEFAULT_LOKI_SCAN_MIN_LEVEL = "WARNING"
DEFAULT_SCAN_INTERVAL_SECONDS = 30
DEFAULT_RESTART_COUNT_THRESHOLD = 5
DEFAULT_ALERT_SEVERITY_LEVELS_STR = "WARNING,ERROR,CRITICAL"
DEFAULT_ALERT_COOLDOWN_MINUTES = 30

PROMPT_TEMPLATE = os.environ.get("PROMPT_TEMPLATE", """
Phân tích tình huống của pod Kubernetes '{namespace}/{pod_name}'.
**Ưu tiên xem xét ngữ cảnh Kubernetes** được cung cấp dưới đây vì nó có thể là lý do chính bạn được gọi.
Kết hợp với các dòng log sau đây (nếu có) để đưa ra phân tích đầy đủ.

1.  Xác định mức độ nghiêm trọng tổng thể (chọn một: INFO, WARNING, ERROR, CRITICAL).
2.  Nếu mức độ nghiêm trọng là WARNING, ERROR hoặc CRITICAL:
    a. Cung cấp một bản tóm tắt ngắn gọn (1-2 câu) bằng **tiếng Việt** giải thích vấn đề cốt lõi.
    b. Đề xuất **nguyên nhân gốc có thể xảy ra** (potential root causes) (ngắn gọn, dạng gạch đầu dòng nếu có nhiều).
    c. Đề xuất các **bước khắc phục sự cố** (suggested troubleshooting steps) (ngắn gọn, dạng gạch đầu dòng).

Ngữ cảnh Kubernetes:
--- START CONTEXT ---
{k8s_context}
--- END CONTEXT ---

Các dòng log (có thể không có):
--- START LOGS ---
{log_text}
--- END LOGS ---

Chỉ trả lời bằng định dạng JSON với các khóa "severity", "summary", "root_cause", và "troubleshooting_steps".
Ví dụ:
{{
  "severity": "CRITICAL",
  "summary": "Pod 'kube-system/oomkill-pod' bị Terminated với lý do OOMKilled.",
  "root_cause": "- Giới hạn bộ nhớ (memory limit) quá thấp.\\n- Ứng dụng bị rò rỉ bộ nhớ (memory leak).",
  "troubleshooting_steps": "- Tăng memory limit cho pod.\\n- Phân tích memory profile của ứng dụng.\\n- Kiểm tra lại logic cấp phát/giải phóng bộ nhớ trong code."
}}
""")

# --- Global State ---
current_agent_config = {} # Cache for DB config
last_config_refresh_time = 0


# --- Timezone ---
try: HCM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception as e: logging.error(f"Could not load timezone 'Asia/Ho_Chi_Minh': {e}."); HCM_TZ = timezone.utc

# --- Kubernetes Client ---
try: config.load_incluster_config(); logging.info("Loaded in-cluster Kubernetes config.")
except config.ConfigException:
    try: config.load_kube_config(); logging.info("Loaded local Kubernetes config (kubeconfig).")
    except config.ConfigException: logging.error("Could not configure Kubernetes client. Exiting."); exit(1)
k8s_core_v1 = client.CoreV1Api()
k8s_apps_v1 = client.AppsV1Api()

# --- Database Functions ---
def get_db_connection():
    """Establishes a connection to the SQLite database."""
    if not os.path.isfile(DB_PATH):
        logging.warning(f"Database file not found at {DB_PATH}. Attempting to create directory.")
        db_dir = os.path.dirname(DB_PATH)
        if not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir)
                logging.info(f"Created directory for database: {db_dir}")
            except OSError as e:
                logging.error(f"Could not create directory {db_dir}: {e}")
                return None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logging.error(f"Database connection error: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error connecting to database: {e}", exc_info=True)
        return None


def init_db(default_monitored_namespaces_str):
    """Initializes the database schema and default configuration."""
    db_dir = os.path.dirname(DB_PATH);
    if not os.path.exists(db_dir):
        try: os.makedirs(db_dir); logging.info(f"Created directory for database: {db_dir}")
        except OSError as e: logging.error(f"Could not create directory {db_dir}: {e}"); return False

    conn = get_db_connection()
    if conn is None: return False

    try:
        with conn: # Use transaction
            cursor = conn.cursor()
            logging.info("Ensuring database tables exist...")

            # <<< START: FIXED SQL STATEMENTS >>>
            # --- Incidents Table ---
            cursor.execute('''
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
                )
            ''')
            # --- Daily Stats Table ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    model_calls INTEGER DEFAULT 0,
                    telegram_alerts INTEGER DEFAULT 0,
                    incident_count INTEGER DEFAULT 0
                )
            ''')
            # --- Available Namespaces Table ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS available_namespaces (
                    name TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL
                )
            ''')
            # --- Agent Config Table ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            # --- Alert Cooldown Table ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alert_cooldown (
                    pod_key TEXT PRIMARY KEY,
                    cooldown_until TEXT NOT NULL
                )
            ''')
            # --- Users Table (for Portal) ---
            cursor.execute('''
                 CREATE TABLE IF NOT EXISTS users (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     username TEXT UNIQUE NOT NULL,
                     password TEXT NOT NULL
                 )
            ''')
            # <<< END: FIXED SQL STATEMENTS >>>

            # --- Initialize default config values if they don't exist ---
            default_configs = {
                # AI
                'enable_ai_analysis': str(DEFAULT_ENABLE_AI_ANALYSIS).lower(),
                'ai_provider': DEFAULT_AI_PROVIDER,
                'ai_model_identifier': DEFAULT_AI_MODEL_IDENTIFIER,
                'ai_api_key': '',
                # Namespaces
                'monitored_namespaces': json.dumps([ns.strip() for ns in default_monitored_namespaces_str.split(',') if ns.strip()]),
                # General
                'loki_scan_min_level': DEFAULT_LOKI_SCAN_MIN_LEVEL,
                'scan_interval_seconds': str(DEFAULT_SCAN_INTERVAL_SECONDS),
                'restart_count_threshold': str(DEFAULT_RESTART_COUNT_THRESHOLD),
                'alert_severity_levels': DEFAULT_ALERT_SEVERITY_LEVELS_STR,
                'alert_cooldown_minutes': str(DEFAULT_ALERT_COOLDOWN_MINUTES),
                # Telegram
                'enable_telegram_alerts': str(DEFAULT_ENABLE_TELEGRAM_ALERTS).lower(),
                'telegram_bot_token': '',
                'telegram_chat_id': ''
            }

            for key, value in default_configs.items():
                cursor.execute("INSERT OR IGNORE INTO agent_config (key, value) VALUES (?, ?)", (key, value))

            logging.info("Tables ensured and default config initialized.")
        logging.info(f"Database initialization/check complete at {DB_PATH}")
        return True
    except sqlite3.Error as e: logging.error(f"Database error during initialization: {e}", exc_info=True); return False
    except Exception as e: logging.error(f"Unexpected error during DB initialization: {e}", exc_info=True); return False
    finally:
        if conn: conn.close()

# --- Remaining functions (load_agent_config_from_db, get_monitored_namespaces, etc.) ---
# ... (No changes needed in the rest of the file from the previous version) ...
def load_agent_config_from_db():
    """Loads agent configuration from the database and caches it."""
    global current_agent_config, last_config_refresh_time
    now = time.time()
    # Reload config if cache is empty or refresh interval has passed
    if not current_agent_config or (now - last_config_refresh_time >= CONFIG_REFRESH_INTERVAL_SECONDS):
        logging.info("Refreshing agent configuration from database...")
        config_from_db = {}
        conn = get_db_connection()
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT key, value FROM agent_config")
                rows = cursor.fetchall()
                config_from_db = {row['key']: row['value'] for row in rows}
                conn.close()
                logging.info(f"Successfully loaded {len(config_from_db)} config items from DB.")
            except sqlite3.Error as e:
                logging.error(f"Database error loading agent config: {e}")
                if conn: conn.close()
            except Exception as e:
                logging.error(f"Unexpected error loading agent config: {e}", exc_info=True)
                if conn: conn.close()
        else:
            logging.error("Failed to get DB connection for loading config.")

        # --- Process loaded config with defaults ---
        enable_ai_db = config_from_db.get('enable_ai_analysis', str(DEFAULT_ENABLE_AI_ANALYSIS)).lower()
        enable_telegram_db = config_from_db.get('enable_telegram_alerts', str(DEFAULT_ENABLE_TELEGRAM_ALERTS)).lower()

        current_agent_config = {
            # AI Config
            'enable_ai_analysis': enable_ai_db == 'true',
            'ai_provider': config_from_db.get('ai_provider', DEFAULT_AI_PROVIDER).lower(),
            'ai_model_identifier': config_from_db.get('ai_model_identifier', DEFAULT_AI_MODEL_IDENTIFIER),
            'ai_api_key': config_from_db.get('ai_api_key', ''),
            # Namespace Config
            'monitored_namespaces_json': config_from_db.get('monitored_namespaces', '[]'),
            # General Config (Read from DB or use defaults)
            'loki_scan_min_level': config_from_db.get('loki_scan_min_level', DEFAULT_LOKI_SCAN_MIN_LEVEL).upper(),
            'scan_interval_seconds': int(config_from_db.get('scan_interval_seconds', DEFAULT_SCAN_INTERVAL_SECONDS)),
            'restart_count_threshold': int(config_from_db.get('restart_count_threshold', DEFAULT_RESTART_COUNT_THRESHOLD)),
            'alert_severity_levels_str': config_from_db.get('alert_severity_levels', DEFAULT_ALERT_SEVERITY_LEVELS_STR),
            'alert_cooldown_minutes': int(config_from_db.get('alert_cooldown_minutes', DEFAULT_ALERT_COOLDOWN_MINUTES)),
            # Telegram Config
            'enable_telegram_alerts': enable_telegram_db == 'true',
            'telegram_bot_token': config_from_db.get('telegram_bot_token', ''), # Don't fallback to env here after init
            'telegram_chat_id': config_from_db.get('telegram_chat_id', ''),       # Don't fallback to env here after init
        }
        # Update derived values
        current_agent_config['alert_severity_levels'] = [
            level.strip().upper() for level in current_agent_config['alert_severity_levels_str'].split(',') if level.strip()
        ]

        last_config_refresh_time = now
        logging.info(f"Current agent config: AI Enabled={current_agent_config['enable_ai_analysis']}, Provider={current_agent_config['ai_provider']}, Telegram Alerts Enabled={current_agent_config['enable_telegram_alerts']}")
    return current_agent_config

def get_monitored_namespaces():
    """Gets the list of namespaces to monitor from the cached config."""
    config = load_agent_config_from_db()
    namespaces_json = config.get('monitored_namespaces_json', '[]')
    default_namespaces_list = [ns.strip() for ns in DEFAULT_MONITORED_NAMESPACES_STR.split(',') if ns.strip()]
    try:
        loaded_value = json.loads(namespaces_json)
        if isinstance(loaded_value, list) and loaded_value:
            monitored = [str(ns).strip() for ns in loaded_value if isinstance(ns, str) and str(ns).strip()]
            if monitored:
                 logging.debug(f"Using monitored namespaces from DB: {monitored}")
                 return monitored
            else:
                 logging.warning("Monitored namespaces list from DB is empty after cleaning. Using default.")
                 return default_namespaces_list
        else:
            logging.warning("Value for monitored_namespaces in DB is not a non-empty list. Using default.")
            return default_namespaces_list
    except json.JSONDecodeError:
        logging.warning(f"Failed to decode monitored_namespaces JSON from DB: {namespaces_json}. Using default.")
        return default_namespaces_list
    except Exception as e:
        logging.error(f"Unexpected error processing monitored_namespaces from DB: {e}. Using default.", exc_info=True)
        return default_namespaces_list


def record_incident(pod_key, severity, summary, initial_reasons, k8s_context, sample_logs,
                    input_prompt=None, raw_ai_response=None, root_cause=None, troubleshooting_steps=None):
    """Records an incident into the database."""
    timestamp_str = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    if conn is None: logging.error(f"Failed to get DB connection for recording incident {pod_key}"); return
    try:
        with conn: # Use transaction
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
            config = load_agent_config_from_db() # Get current alert levels
            if severity in config.get('alert_severity_levels', []):
                today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                # Ensure the date entry exists before updating
                cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
                cursor.execute('''
                    UPDATE daily_stats SET incident_count = incident_count + 1 WHERE date = ?
                ''', (today_str,))
            logging.info(f"Recorded data for {pod_key} with severity {severity}")
    except sqlite3.Error as e: logging.error(f"Database error recording incident for {pod_key}: {e}")
    except Exception as e: logging.error(f"Unexpected error recording incident for {pod_key}: {e}", exc_info=True)
    finally:
        if conn: conn.close()


def update_daily_stats():
    """Updates daily statistics (model calls, telegram alerts) in the database."""
    # Get and reset model calls count from the ai_providers module
    calls_to_add = ai_providers.get_and_reset_model_calls()

    # Get and reset telegram alerts count from the notifier module
    alerts_to_add = notifier.get_and_reset_telegram_alerts()

    if calls_to_add == 0 and alerts_to_add == 0:
        return # Nothing to update

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    conn = get_db_connection()
    if conn is None:
        logging.error("Failed to get DB connection for updating daily stats")
        return

    try:
        with conn: # Use transaction
            cursor = conn.cursor()
            # Ensure the date entry exists
            cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
            # Update counters for the specific date
            cursor.execute('''
                UPDATE daily_stats
                SET model_calls = model_calls + ?, telegram_alerts = telegram_alerts + ?
                WHERE date = ?
            ''', (calls_to_add, alerts_to_add, today_str))
            logging.info(f"Updated daily stats for {today_str}: +{calls_to_add} Model calls, +{alerts_to_add} Telegram alerts.")
    except sqlite3.Error as e:
        logging.error(f"Database error updating daily stats: {e}")
    except Exception as e:
        logging.error(f"Unexpected error updating daily stats: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def periodic_stat_update():
    """Periodically calls update_daily_stats."""
    while True:
        time.sleep(STATS_UPDATE_INTERVAL_SECONDS)
        update_daily_stats()

def is_pod_in_cooldown(pod_key):
    """Checks if a pod is currently in the alert cooldown period."""
    conn = get_db_connection()
    if conn is None:
        logging.error(f"Failed to get DB connection checking cooldown for {pod_key}")
        return False # Assume not in cooldown if DB fails
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        with conn: # Use transaction
            cursor = conn.cursor()
            cursor.execute("SELECT cooldown_until FROM alert_cooldown WHERE pod_key = ?", (pod_key,))
            result = cursor.fetchone()
            if result:
                cooldown_until_str = result['cooldown_until']
                if cooldown_until_str > now_iso:
                     logging.info(f"Pod {pod_key} is in cooldown until {cooldown_until_str}.")
                     return True
                else:
                     cursor.execute("DELETE FROM alert_cooldown WHERE pod_key = ?", (pod_key,))
                     logging.info(f"Cooldown expired for pod {pod_key} (was {cooldown_until_str}).")
                     return False
            return False
    except sqlite3.Error as e:
        logging.error(f"Database error checking cooldown for {pod_key}: {e}")
        return False # Assume not in cooldown if DB error
    except Exception as e:
        logging.error(f"Unexpected error checking cooldown for {pod_key}: {e}", exc_info=True)
        return False
    finally:
        if conn: conn.close()


def set_pod_cooldown(pod_key):
    """Sets the alert cooldown period for a specific pod."""
    config = load_agent_config_from_db()
    cooldown_minutes = config.get('alert_cooldown_minutes', DEFAULT_ALERT_COOLDOWN_MINUTES) # Get from config

    conn = get_db_connection()
    if conn is None:
        logging.error(f"Failed to get DB connection setting cooldown for {pod_key}")
        return
    try:
        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
        cooldown_until_iso = cooldown_until.isoformat()
        with conn: # Use transaction
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO alert_cooldown (pod_key, cooldown_until) VALUES (?, ?)",
                           (pod_key, cooldown_until_iso))
            logging.info(f"Set cooldown for pod {pod_key} until {cooldown_until_iso} ({cooldown_minutes} minutes)")
    except sqlite3.Error as e:
        logging.error(f"Database error setting cooldown for {pod_key}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error setting cooldown for {pod_key}: {e}", exc_info=True)
    finally:
        if conn: conn.close()


# --- Kubernetes Info Functions ---
def get_pod_info(namespace, pod_name):
    """Fetches detailed information about a specific pod."""
    try:
        pod = k8s_core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        info = {
            "name": pod.metadata.name, "namespace": pod.metadata.namespace, "status": pod.status.phase,
            "node_name": pod.spec.node_name,
            "start_time": pod.status.start_time.isoformat() if pod.status.start_time else "N/A",
            "restarts": sum(cs.restart_count for cs in pod.status.container_statuses) if pod.status.container_statuses else 0,
            "conditions": {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message} for cond in pod.status.conditions} if pod.status.conditions else {},
            "container_statuses": {}
        }
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                state_info = "N/A";
                if cs.state:
                    if cs.state.running: state_info = "Running"
                    elif cs.state.waiting: state_info = f"Waiting ({cs.state.waiting.reason})"
                    elif cs.state.terminated: state_info = f"Terminated ({cs.state.terminated.reason}, ExitCode: {cs.state.terminated.exit_code})"
                info["container_statuses"][cs.name] = {"ready": cs.ready, "restart_count": cs.restart_count, "state": state_info}
        return info
    except ApiException as e: logging.warning(f"Could not get pod info for {namespace}/{pod_name}: {e.status} {e.reason}"); return None
    except Exception as e: logging.error(f"Unexpected error getting pod info for {namespace}/{pod_name}: {e}", exc_info=True); return None

def get_node_info(node_name):
    """Fetches detailed information about a specific node."""
    if not node_name: return None
    try:
        node = k8s_core_v1.read_node(name=node_name)
        conditions = {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message} for cond in node.status.conditions} if node.status.conditions else {}
        info = { "name": node.metadata.name, "conditions": conditions,
                 "allocatable_cpu": node.status.allocatable.get('cpu', 'N/A'), "allocatable_memory": node.status.allocatable.get('memory', 'N/A'),
                 "kubelet_version": node.status.node_info.kubelet_version }
        return info
    except ApiException as e: logging.warning(f"Could not get node info for {node_name}: {e.status} {e.reason}"); return None
    except Exception as e: logging.error(f"Unexpected error getting node info for {node_name}: {e}", exc_info=True); return None

def get_pod_events(namespace, pod_name, since_minutes=15):
    """Fetches recent events related to a specific pod."""
    try:
        since_time = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        field_selector = f"involvedObject.kind=Pod,involvedObject.name={pod_name},involvedObject.namespace={namespace}"
        events = k8s_core_v1.list_namespaced_event(namespace=namespace, field_selector=field_selector, limit=20) # Limit to 20 most recent
        recent_events = []
        if events and events.items:
                sorted_events = sorted(
                    events.items,
                    key=lambda e: e.last_timestamp or e.metadata.creation_timestamp or datetime(MINYEAR, 1, 1, tzinfo=timezone.utc),
                    reverse=True
                )
                for event in sorted_events:
                    event_time = event.last_timestamp or event.metadata.creation_timestamp
                    if event_time and event_time >= since_time:
                        recent_events.append({
                            "time": event_time.isoformat(), "type": event.type, "reason": event.reason,
                            "message": event.message, "count": event.count
                        })
                    if len(recent_events) >= 10 or (event_time and event_time < since_time): break
        return recent_events
    except ApiException as e:
        if e.status != 403: logging.warning(f"Could not list events for pod {namespace}/{pod_name}: {e.status} {e.reason}")
        return []
    except Exception as e:
        logging.error(f"Unexpected error listing events for pod {namespace}/{pod_name}: {e}", exc_info=True)
        return []

def format_k8s_context(pod_info, node_info, pod_events):
    """Formats Kubernetes context information into a string for the AI prompt."""
    context_str = "\n--- Ngữ cảnh Kubernetes ---\n"
    if pod_info:
        context_str += f"Pod: {pod_info['namespace']}/{pod_info['name']}\n"
        context_str += f"  Trạng thái: {pod_info['status']}\n"
        context_str += f"  Node: {pod_info['node_name']}\n"
        context_str += f"  Số lần khởi động lại: {pod_info['restarts']}\n"
        if pod_info.get('container_statuses'):
                context_str += "  Trạng thái Container:\n"
                for name, status in pod_info['container_statuses'].items(): context_str += f"    - {name}: {status['state']} (Ready: {status['ready']}, Restarts: {status['restart_count']})\n"
        if pod_info.get('conditions'):
                problematic_conditions = [ f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})" for ctype, cinfo in pod_info['conditions'].items() if cinfo.get('status') != 'True' ]
                if problematic_conditions: context_str += f"  Điều kiện Pod bất thường: {', '.join(problematic_conditions)}\n"
    if node_info:
        context_str += f"Node: {node_info['name']}\n"
        if node_info.get('conditions'):
                problematic_conditions = [ f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})" for ctype, cinfo in node_info['conditions'].items() if (ctype == 'Ready' and cinfo.get('status') != 'True') or (ctype != 'Ready' and cinfo.get('status') != 'False') ]
                if problematic_conditions: context_str += f"  Điều kiện Node bất thường: {', '.join(problematic_conditions)}\n"
    if pod_events:
        context_str += "Sự kiện Pod gần đây (tối đa 10):\n"
        for event in pod_events:
            message_preview = event['message'][:150] + ('...' if len(event['message']) > 150 else '')
            context_str += f"  - [{event['time']}] {event['type']} {event['reason']} (x{event.get('count',1)}): {message_preview}\n"
    context_str += "--- Kết thúc ngữ cảnh ---\n"
    return context_str

# --- Namespace Management ---
def get_active_namespaces():
    """Gets a list of active, non-excluded namespaces from the K8s API."""
    active_namespaces = []
    try:
        all_namespaces = k8s_core_v1.list_namespace(watch=False, timeout_seconds=60)
        for ns in all_namespaces.items:
            if ns.status.phase == "Active" and ns.metadata.name not in EXCLUDED_NAMESPACES:
                active_namespaces.append(ns.metadata.name)
        logging.info(f"Found {len(active_namespaces)} active and non-excluded namespaces in cluster.")
    except ApiException as e:
        logging.error(f"API Error listing namespaces: {e.status} {e.reason}. Check RBAC permissions.")
    except Exception as e:
        logging.error(f"Unexpected error listing namespaces: {e}", exc_info=True)
    return active_namespaces

def update_available_namespaces_in_db(namespaces):
    """Updates the list of available namespaces in the database."""
    if not namespaces:
        logging.info("No active namespaces provided to update in DB.")
        return

    timestamp = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    if conn is None:
        logging.error("Failed to get DB connection for updating available namespaces")
        return

    try:
        with conn: # Use transaction
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(namespaces))
            if namespaces:
                 cursor.execute(f"DELETE FROM available_namespaces WHERE name NOT IN ({placeholders})", tuple(namespaces))
            else:
                 cursor.execute("DELETE FROM available_namespaces")

            for ns in namespaces:
                cursor.execute("INSERT OR REPLACE INTO available_namespaces (name, last_seen) VALUES (?, ?)", (ns, timestamp))
            logging.info(f"Updated available_namespaces table in DB with {len(namespaces)} namespaces.")
    except sqlite3.Error as e:
        logging.error(f"Database error updating available namespaces: {e}")
    except Exception as e:
        logging.error(f"Unexpected error updating available namespaces: {e}", exc_info=True)
    finally:
        if conn: conn.close()


# --- Loki & K8s Scanning ---
def scan_loki_for_suspicious_logs(start_time, end_time, namespaces_to_scan, loki_scan_min_level):
    """Scans Loki for logs matching keywords or minimum severity level."""
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range";
    if not namespaces_to_scan: logging.info("No namespaces to scan in Loki."); return {}

    log_levels_all = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"];
    scan_level_index = -1
    try: scan_level_index = log_levels_all.index(loki_scan_min_level.upper())
    except ValueError: logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {loki_scan_min_level}. Defaulting to WARNING."); scan_level_index = log_levels_all.index("WARNING")
    levels_to_scan = log_levels_all[scan_level_index:]

    namespace_regex = "|".join(namespaces_to_scan)
    keywords_to_find = levels_to_scan + ["fail", "crash", "exception", "panic", "fatal", "timeout", "denied", "refused", "unable", "unauthorized", "error"]
    escaped_keywords = [re.escape(k) for k in sorted(list(set(keywords_to_find)), key=len, reverse=True)];
    regex_pattern = "(?i)(" + "|".join(escaped_keywords) + ")"
    logql_query = f'{{namespace=~"{namespace_regex}"}} |~ `{regex_pattern}`'
    query_limit_scan = 2000

    params = { 'query': logql_query, 'start': int(start_time.timestamp() * 1e9), 'end': int(end_time.timestamp() * 1e9), 'limit': query_limit_scan, 'direction': 'forward' }
    logging.info(f"Scanning Loki for suspicious logs (Level >= {loki_scan_min_level} or keywords) in {len(namespaces_to_scan)} namespaces...")
    suspicious_logs_by_pod = {}
    try:
        headers = {'Accept': 'application/json'}
        response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        if 'data' in data and 'result' in data['data']:
            count = 0
            for stream in data['data']['result']:
                labels = stream.get('stream', {}); ns = labels.get('namespace'); pod_name = labels.get('pod')
                if not ns or not pod_name: continue
                pod_key = f"{ns}/{pod_name}"
                if pod_key not in suspicious_logs_by_pod: suspicious_logs_by_pod[pod_key] = []
                for timestamp_ns, log_line in stream['values']:
                    log_entry = { "timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc), "message": log_line.strip(), "labels": labels }
                    suspicious_logs_by_pod[pod_key].append(log_entry); count += 1
            logging.info(f"Loki scan found {count} suspicious log entries across {len(suspicious_logs_by_pod)} pods.")
        else: logging.info("Loki scan found no suspicious log entries matching the criteria.")
        return suspicious_logs_by_pod
    except requests.exceptions.HTTPError as e:
        error_detail = ""
        try: error_detail = e.response.text[:500]
        except Exception: pass
        logging.error(f"Error scanning Loki (HTTP {e.response.status_code}): {e}. Response: {error_detail}")
        return {}
    except requests.exceptions.RequestException as e: logging.error(f"Error scanning Loki (Request failed): {e}"); return {}
    except json.JSONDecodeError as e: logging.error(f"Error decoding Loki scan response (Invalid JSON): {e}"); return {}
    except Exception as e: logging.error(f"Unexpected error during Loki scan: {e}", exc_info=True); return {}

def scan_kubernetes_for_issues(namespaces_to_scan, restart_threshold):
    """Scans Kubernetes namespaces for pods with common issues."""
    problematic_pods = {}
    logging.info(f"Scanning {len(namespaces_to_scan)} Kubernetes namespaces for problematic pods...")
    for ns in namespaces_to_scan:
        try:
            pods = k8s_core_v1.list_namespaced_pod(namespace=ns, watch=False, timeout_seconds=60)
            for pod in pods.items:
                pod_key = f"{ns}/{pod.metadata.name}"; issue_found = False; reason = ""
                if pod.status.phase in ["Failed", "Unknown"]: issue_found = True; reason = f"Trạng thái Pod là {pod.status.phase}"
                elif pod.status.phase == "Pending" and pod.status.conditions:
                        scheduled_condition = next((c for c in pod.status.conditions if c.type == "PodScheduled"), None)
                        if scheduled_condition and scheduled_condition.status == "False" and scheduled_condition.reason == "Unschedulable": issue_found = True; reason = f"Pod không thể lên lịch (Unschedulable)"
                if not issue_found and pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        if cs.restart_count >= restart_threshold: issue_found = True; reason = f"Container '{cs.name}' restart {cs.restart_count} lần (>= ngưỡng {restart_threshold})"; break
                        if cs.state and cs.state.waiting and cs.state.waiting.reason in ["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "CreateContainerError"]: issue_found = True; reason = f"Container '{cs.name}' đang ở trạng thái Waiting với lý do '{cs.state.waiting.reason}'"; break
                        if cs.state and cs.state.terminated and cs.state.terminated.reason in ["OOMKilled", "Error", "ContainerCannotRun"]:
                            is_recent_termination = False
                            if cs.state.terminated.finished_at:
                                try:
                                    finished_at_aware = cs.state.terminated.finished_at
                                    if finished_at_aware.tzinfo is None: finished_at_aware = finished_at_aware.replace(tzinfo=timezone.utc)
                                    is_recent_termination = (datetime.now(timezone.utc) - finished_at_aware) < timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
                                except Exception as time_err: logging.warning(f"Could not parse or compare finished_at time for {pod_key}/{cs.name}: {time_err}")
                            if pod.spec.restart_policy != "Always" or is_recent_termination: issue_found = True; reason = f"Container '{cs.name}' bị Terminated với lý do '{cs.state.terminated.reason}'"; break
                if issue_found:
                    logging.warning(f"Phát hiện pod có vấn đề tiềm ẩn (K8s Scan): {pod_key}. Lý do: {reason}")
                    if pod_key not in problematic_pods: problematic_pods[pod_key] = { "namespace": ns, "pod_name": pod.metadata.name, "reason": f"K8s: {reason}" }
        except ApiException as e: logging.error(f"API Error scanning namespace {ns}: {e.status} {e.reason}")
        except Exception as e: logging.error(f"Unexpected error scanning namespace {ns}: {e}", exc_info=True)
    logging.info(f"Finished K8s scan. Found {len(problematic_pods)} potentially problematic pods from K8s state.")
    return problematic_pods

def query_loki_for_pod(namespace, pod_name, start_time, end_time):
    """Queries Loki for detailed logs of a specific pod within a time range."""
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range"; logql_query = f'{{namespace="{namespace}", pod="{pod_name}"}}'
    params = { 'query': logql_query, 'start': int(start_time.timestamp() * 1e9), 'end': int(end_time.timestamp() * 1e9), 'limit': LOKI_QUERY_LIMIT, 'direction': 'forward' }
    logging.info(f"Querying Loki for pod '{namespace}/{pod_name}' from {start_time} to {end_time}")
    try:
        headers = {'Accept': 'application/json'};
        response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=45);
        response.raise_for_status();
        data = response.json()
        if 'data' in data and 'result' in data['data']:
            log_entries = [];
            for stream in data['data']['result']:
                stream_labels = stream.get('stream', {})
                for timestamp_ns, log_line in stream['values']: log_entries.append({ "timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc), "message": log_line.strip(), "labels": stream_labels })
            log_entries.sort(key=lambda x: x['timestamp']); logging.info(f"Received {len(log_entries)} log entries from Loki for pod '{namespace}/{pod_name}'."); return log_entries
        else: logging.warning(f"No 'result' data found in Loki response for pod '{namespace}/{pod_name}'."); return []
    except requests.exceptions.RequestException as e: logging.error(f"Error querying Loki for pod '{namespace}/{pod_name}': {e}"); return []
    except json.JSONDecodeError as e: logging.error(f"Error decoding Loki JSON response for pod '{namespace}/{pod_name}': {e}"); return []
    except Exception as e: logging.error(f"Unexpected error querying Loki for pod '{namespace}/{pod_name}': {e}", exc_info=True); return []

def preprocess_and_filter(log_entries, loki_scan_min_level):
    """Filters log entries based on severity level or keywords."""
    filtered_logs = []; log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]; min_level_index = -1
    try: min_level_index = log_levels.index(loki_scan_min_level.upper())
    except ValueError: logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {loki_scan_min_level}. Defaulting to WARNING."); min_level_index = log_levels.index("WARNING")
    keywords_indicating_problem = ["FAIL", "ERROR", "CRASH", "EXCEPTION", "UNAVAILABLE", "FATAL", "PANIC", "TIMEOUT", "DENIED", "REFUSED", "UNABLE", "UNAUTHORIZED"]
    for entry in log_entries:
        log_line = entry['message']; log_line_upper = log_line.upper(); level_detected = False
        for i, level in enumerate(log_levels):
            if f" {level} " in f" {log_line_upper} " or \
               log_line_upper.startswith(level+":") or \
               f"[{level}]" in log_line_upper or \
               f"level={level.lower()}" in log_line_upper or \
               f"\"level\":\"{level.lower()}\"" in log_line_upper:
                if i >= min_level_index: filtered_logs.append(entry); level_detected = True; break
        if not level_detected:
            if any(keyword in log_line_upper for keyword in keywords_indicating_problem):
                    warning_index = log_levels.index("WARNING")
                    if min_level_index <= warning_index: filtered_logs.append(entry)
    logging.info(f"Filtered {len(log_entries)} logs down to {len(filtered_logs)} relevant logs (Scan Level: {loki_scan_min_level}).")
    return filtered_logs

# --- AI Analysis & Alerting ---
def determine_severity_from_rules(initial_reasons, log_batch):
    """Determines a fallback severity based on initial reasons and log keywords."""
    severity = "INFO" # Default severity
    reasons_upper = initial_reasons.upper() if initial_reasons else ""

    if "OOMKILLED" in reasons_upper or "FAILED" in reasons_upper: severity = "CRITICAL"
    elif "ERROR" in reasons_upper or "CRASHLOOPBACKOFF" in reasons_upper: severity = "ERROR"
    elif "UNSCHEDULABLE" in reasons_upper or "IMAGEPULLBACKOFF" in reasons_upper or "BACKOFF" in reasons_upper or "WAITING" in reasons_upper: severity = "WARNING"

    if severity in ["INFO", "WARNING"]:
        critical_keywords = ["CRITICAL", "ALERT", "EMERGENCY", "PANIC", "FATAL"]
        error_keywords = ["ERROR", "EXCEPTION", "DENIED", "REFUSED", "UNAUTHORIZED"]
        warning_keywords = ["WARNING", "WARN", "TIMEOUT", "UNABLE", "SLOW"]

        for entry in log_batch[:20]:
            log_upper = entry['message'].upper()
            if any(kw in log_upper for kw in critical_keywords): severity = "CRITICAL"; break
            if any(kw in log_upper for kw in error_keywords): severity = "ERROR";
            if severity == "INFO" and any(kw in log_upper for kw in warning_keywords): severity = "WARNING"

    logging.info(f"Rule-based severity determined: {severity}")
    return severity

def get_default_analysis(severity, initial_reasons):
     """Generates a default analysis structure when AI is disabled or fails."""
     summary = f"Phát hiện sự cố tiềm ẩn. Lý do ban đầu: {initial_reasons or 'Không có'}. Mức độ ước tính: {severity}."
     root_cause = "Không có phân tích AI."
     troubleshooting_steps = "Kiểm tra ngữ cảnh Kubernetes và log chi tiết thủ công."
     return {"severity": severity, "summary": summary, "root_cause": root_cause, "troubleshooting_steps": troubleshooting_steps}


def analyze_incident(log_batch, k8s_context, initial_reasons):
    """
    Prepares data and calls the AI analysis provider or uses rule-based analysis.
    """
    config = load_agent_config_from_db() # Get current config

    if not config.get('enable_ai_analysis', False):
        logging.info("AI analysis is disabled by configuration.")
        severity = determine_severity_from_rules(initial_reasons, log_batch)
        analysis_result = get_default_analysis(severity, initial_reasons)
        return analysis_result, None, None

    namespace = "unknown"; pod_name = "unknown_pod"
    if log_batch and log_batch[0].get('labels'):
        labels = log_batch[0].get('labels', {}); namespace = labels.get('namespace', namespace); pod_name = labels.get('pod', pod_name)
    elif k8s_context:
        match_ns = re.search(r"Pod:\s*([\w.-]+)/", k8s_context); match_pod = re.search(r"Pod:\s*[\w.-]+/([\w.-]+)\n", k8s_context);
        if match_ns: namespace = match_ns.group(1);
        if match_pod: pod_name = match_pod.group(1)

    log_text = "N/A"
    if log_batch:
        limited_logs = [f"[{entry['timestamp'].isoformat()}] {entry['message'][:500]}" for entry in log_batch[:15]];
        log_text = "\n".join(limited_logs)

    final_prompt = None
    try:
        final_prompt = PROMPT_TEMPLATE.format(
            namespace=namespace, pod_name=pod_name,
            k8s_context=k8s_context[:10000], log_text=log_text[:20000]
        )
    except KeyError as e:
        logging.error(f"Missing placeholder in PROMPT_TEMPLATE: {e}. Using default prompt structure.")
        final_prompt = f"Phân tích pod {namespace}/{pod_name}. Ngữ cảnh K8s: {k8s_context[:10000]}. Logs: {log_text[:20000]}. Chỉ trả lời bằng JSON với khóa 'severity', 'summary', 'root_cause', 'troubleshooting_steps'."

    provider = config.get('ai_provider', 'none')
    api_key = config.get('ai_api_key', '')
    model_id = config.get('ai_model_identifier', '')
    local_endpoint = LOCAL_GEMINI_ENDPOINT_URL

    analysis_result = None
    raw_response_text = None
    analysis_failed = False

    try:
        analysis_result, raw_response_text, analysis_failed = ai_providers.call_ai_analysis(
            provider, api_key, model_id, local_endpoint, final_prompt
        )
    except Exception as e:
        logging.error(f"Unexpected error occurred while preparing for or handling result from ai_providers.call_ai_analysis: {e}", exc_info=True)
        analysis_failed = True
        analysis_result = None
        raw_response_text = None

    if analysis_failed or analysis_result is None:
        if analysis_result is None and not analysis_failed:
             logging.warning(f"AI analysis function returned successfully but result is None (likely parsing failed). Falling back for {namespace}/{pod_name}.")
        elif analysis_failed:
             logging.warning(f"AI analysis failed or provider unsupported/misconfigured. Falling back to rule-based analysis for {namespace}/{pod_name}.")

        severity = determine_severity_from_rules(initial_reasons, log_batch)
        analysis_result = get_default_analysis(severity, initial_reasons)
        analysis_result["summary"] = f"Phân tích AI thất bại hoặc bị tắt. Lý do ban đầu: {initial_reasons or 'Không có'}. Mức độ ước tính: {severity}."

    if not isinstance(analysis_result, dict):
         logging.error(f"CRITICAL: Analysis result is not a dictionary after processing for {namespace}/{pod_name}. Final fallback.")
         severity = determine_severity_from_rules(initial_reasons, log_batch)
         analysis_result = get_default_analysis(severity, initial_reasons)
         raw_response_text = "[Fallback due to invalid result type]"
    else:
         analysis_result.setdefault("severity", "WARNING")
         analysis_result.setdefault("summary", "N/A")
         analysis_result.setdefault("root_cause", "N/A")
         analysis_result.setdefault("troubleshooting_steps", "N/A")

    return analysis_result, final_prompt, raw_response_text


# --- Main Monitoring Cycle ---
def refresh_namespaces_if_needed(last_refresh_time):
    """Refreshes the list of available namespaces from K8s if the interval has passed."""
    current_time_secs = time.time()
    if current_time_secs - last_refresh_time >= NAMESPACE_REFRESH_INTERVAL_SECONDS:
        logging.info("Refreshing list of active namespaces from Kubernetes API...")
        all_active_namespaces = get_active_namespaces()
        update_available_namespaces_in_db(all_active_namespaces)
        return current_time_secs
    return last_refresh_time


def identify_pods_to_investigate(monitored_namespaces, start_cycle_time, config):
    """Identifies pods requiring investigation based on K8s state and Loki logs."""
    k8s_problem_pods = scan_kubernetes_for_issues(monitored_namespaces, config['restart_count_threshold'])
    loki_scan_end_time = start_cycle_time; loki_scan_start_time = loki_scan_end_time - timedelta(minutes=LOKI_SCAN_RANGE_MINUTES)
    loki_suspicious_logs = scan_loki_for_suspicious_logs(loki_scan_start_time, loki_scan_end_time, monitored_namespaces, config['loki_scan_min_level'])

    pods_to_investigate = {}
    for pod_key, data in k8s_problem_pods.items():
        if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
        pods_to_investigate[pod_key]["reason"].append(data["reason"])
    for pod_key, logs in loki_suspicious_logs.items():
            if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
            reason_text = f"Loki: Phát hiện {len(logs)} log đáng ngờ (>= {config['loki_scan_min_level']})"
            if reason_text not in pods_to_investigate[pod_key]["reason"]:
                 pods_to_investigate[pod_key]["reason"].append(reason_text)
            pods_to_investigate[pod_key]["logs"].extend(logs)

    logging.info(f"Total pods to investigate this cycle: {len(pods_to_investigate)}")
    return pods_to_investigate


def investigate_pod_details(namespace, pod_name, initial_reasons, suspicious_logs_found_in_scan, config):
    """Gathers detailed K8s context and logs for a specific pod."""
    logging.info(f"Investigating pod: {namespace}/{pod_name} (Initial Reasons: {initial_reasons})")
    pod_info = get_pod_info(namespace, pod_name); node_info = None; pod_events = []
    if pod_info: node_info = get_node_info(pod_info.get('node_name')); pod_events = get_pod_events(namespace, pod_name, since_minutes=LOKI_DETAIL_LOG_RANGE_MINUTES + 5)
    k8s_context_str = format_k8s_context(pod_info, node_info, pod_events)

    logs_for_analysis = suspicious_logs_found_in_scan
    if not logs_for_analysis:
        logging.info(f"No logs found in initial scan for {namespace}/{pod_name}. Querying Loki for detailed logs...")
        log_end_time = datetime.now(timezone.utc); log_start_time = log_end_time - timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
        detailed_logs = query_loki_for_pod(namespace, pod_name, log_start_time, log_end_time)
        logs_for_analysis = preprocess_and_filter(detailed_logs, config['loki_scan_min_level'])
    else:
        logging.info(f"Using {len(logs_for_analysis)} logs found during initial scan for {namespace}/{pod_name}.")

    return logs_for_analysis, k8s_context_str


def process_analysis_and_alert(pod_key, initial_reasons, logs_for_analysis, k8s_context_str, config):
    """Processes the analysis result, records it, and sends alerts if needed."""
    namespace, pod_name = pod_key.split('/', 1);
    analysis_start_time = time.time()

    # Call analyze_incident which handles AI call via ai_providers
    analysis_result, final_prompt, raw_response_text = analyze_incident(logs_for_analysis, k8s_context_str, initial_reasons)

    analysis_duration = time.time() - analysis_start_time
    logging.info(f"Analysis processing for {pod_key} took {analysis_duration:.2f} seconds.")

    severity = analysis_result.get("severity", "UNKNOWN").upper()
    summary = analysis_result.get("summary", "N/A")
    root_cause_str = analysis_result.get("root_cause", "N/A")
    steps_str = analysis_result.get("troubleshooting_steps", "N/A")

    logging.info(f"Analysis result for '{pod_key}': Severity={severity}, Summary={summary[:100]}...")

    sample_logs_str = "\n".join([f"- {log['message'][:150]}" for log in logs_for_analysis[:5]]) if logs_for_analysis else "-"

    # Record incident details
    record_incident(
        pod_key, severity, summary, initial_reasons, k8s_context_str,
        sample_logs_str, final_prompt, raw_response_text, root_cause_str, steps_str
    )

    # --- Send Alert via Notifier Module ---
    if severity in config.get('alert_severity_levels', []):
        if config.get('enable_telegram_alerts', False):
            bot_token = config.get('telegram_bot_token')
            chat_id = config.get('telegram_chat_id')
            if bot_token and chat_id:
                alert_time_hcm = datetime.now(HCM_TZ); time_format = '%Y-%m-%d %H:%M:%S %Z'
                alert_data = {
                    'pod_key': pod_key, 'severity': severity, 'summary': summary,
                    'root_cause': root_cause_str,
                    'troubleshooting_steps': steps_str,
                    'initial_reasons': initial_reasons,
                    'alert_time': alert_time_hcm.strftime(time_format),
                    'sample_logs': sample_logs_str
                }
                # Call the notifier function
                alert_sent = notifier.send_telegram_alert(
                    bot_token, chat_id, alert_data, config.get('enable_ai_analysis', False)
                )
                # Set cooldown only AFTER attempting to send alert
                if alert_sent: # Optional: Only set cooldown if alert was likely sent
                     set_pod_cooldown(pod_key)
                else:
                     logging.warning(f"Telegram alert sending failed for {pod_key}, cooldown not set immediately.")
            else:
                logging.warning(f"Telegram alerts enabled for {pod_key} (severity {severity}) but token/chat_id missing in config.")
        else:
            logging.info(f"Telegram alerts disabled. Skipping alert for {pod_key} (severity {severity}).")
            # Set cooldown even if alerts are off, to prevent re-analysis spam
            set_pod_cooldown(pod_key)
    else:
        logging.info(f"Severity '{severity}' for {pod_key} does not meet alert threshold {config.get('alert_severity_levels', [])}. No alert sent.")
        # Consider if cooldown should be set for non-alerted severities too


def perform_monitoring_cycle(last_namespace_refresh_time):
    """Performs a single monitoring cycle: scan, investigate, analyze, alert."""
    start_cycle_time = datetime.now(timezone.utc)
    cycle_start_ts = time.time()
    logging.info("--- Starting new monitoring cycle ---")

    config = load_agent_config_from_db()
    current_scan_interval = config.get('scan_interval_seconds', DEFAULT_SCAN_INTERVAL_SECONDS)
    last_namespace_refresh_time = refresh_namespaces_if_needed(last_namespace_refresh_time)
    monitored_namespaces = get_monitored_namespaces()

    if not monitored_namespaces:
        logging.warning("No namespaces configured for monitoring. Skipping cycle.")
        cycle_duration = time.time() - cycle_start_ts
        sleep_time = max(0, current_scan_interval - cycle_duration)
        logging.info(f"--- Cycle finished early (no namespaces) in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
        time.sleep(sleep_time)
        return last_namespace_refresh_time

    logging.info(f"Currently monitoring {len(monitored_namespaces)} namespaces: {', '.join(monitored_namespaces)}")
    pods_to_investigate = identify_pods_to_investigate(monitored_namespaces, start_cycle_time, config)

    investigation_count = 0
    for pod_key, data in pods_to_investigate.items():
        if is_pod_in_cooldown(pod_key): continue
        investigation_count += 1
        namespace, pod_name = pod_key.split('/', 1);
        initial_reasons = "; ".join(data["reason"]);
        suspicious_logs_found_in_scan = data["logs"]

        try:
            logs_for_analysis, k8s_context_str = investigate_pod_details(
                namespace, pod_name, initial_reasons, suspicious_logs_found_in_scan, config
            )
            process_analysis_and_alert(
                pod_key, initial_reasons, logs_for_analysis, k8s_context_str, config
            )
        except Exception as investigation_err:
            logging.error(f"Error during investigation/analysis loop for {pod_key}: {investigation_err}", exc_info=True)

        time.sleep(0.5)

    if investigation_count > 0:
         logging.info(f"Finished investigating {investigation_count} pods.")

    cycle_duration = time.time() - cycle_start_ts
    sleep_time = max(0, current_scan_interval - cycle_duration)
    logging.info(f"--- Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
    time.sleep(sleep_time)
    return last_namespace_refresh_time


def main_loop():
    """The main loop that continuously runs monitoring cycles."""
    last_namespace_refresh_time = 0
    while True:
        try:
            last_namespace_refresh_time = perform_monitoring_cycle(last_namespace_refresh_time)
        except Exception as cycle_err:
             logging.critical(f"Unhandled exception in monitoring cycle: {cycle_err}", exc_info=True)
             config = current_agent_config or {}
             sleep_interval = config.get('scan_interval_seconds', DEFAULT_SCAN_INTERVAL_SECONDS)
             logging.info(f"Sleeping for {sleep_interval} seconds before next cycle after error.")
             time.sleep(sleep_interval)
             last_namespace_refresh_time = 0


# --- Main Execution ---
if __name__ == "__main__":
    if not init_db(DEFAULT_MONITORED_NAMESPACES_STR):
        logging.error("Failed to initialize database. Exiting.")
        exit(1)

    load_agent_config_from_db() # Load initial config

    stats_thread = threading.Thread(target=periodic_stat_update, daemon=True);
    stats_thread.start();
    logging.info("Started periodic stats update thread.")

    logging.info(f"Starting Kubernetes Log Monitoring Agent")
    logging.info(f"Loki URL: {LOKI_URL}")

    initial_config = current_agent_config
    logging.info(f"Initial Config - Scan Interval: {initial_config.get('scan_interval_seconds')}s")
    logging.info(f"Initial Config - Loki Scan Level: {initial_config.get('loki_scan_min_level')}")
    logging.info(f"Initial Config - Alert Levels: {initial_config.get('alert_severity_levels_str')}")
    logging.info(f"Initial Config - Restart Threshold: {initial_config.get('restart_count_threshold')}")
    logging.info(f"Initial Config - Alert Cooldown: {initial_config.get('alert_cooldown_minutes')}m")
    logging.info(f"Initial Config - AI Enabled: {initial_config.get('enable_ai_analysis')}, Provider: {initial_config.get('ai_provider')}, Model: {initial_config.get('ai_model_identifier')}")
    logging.info(f"Initial Config - Telegram Alerts Enabled: {initial_config.get('enable_telegram_alerts')}")

    try:
        main_loop()
    except KeyboardInterrupt:
        logging.info("Agent stopped by user (KeyboardInterrupt).")
    except Exception as main_err:
        logging.critical(f"Unhandled exception in main loop: {main_err}", exc_info=True)
    finally:
        logging.info("Performing final stats update before exiting...")
        update_daily_stats()
        logging.info("Agent shutdown complete.")

