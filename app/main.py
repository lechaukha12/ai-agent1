import os
import time
import requests
import google.generativeai as genai
# import openai # Uncomment if adding OpenAI support
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

# --- Basic Setup ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Static Config from Environment (Defaults/Fallbacks) ---
LOKI_URL = os.environ.get("LOKI_URL", "http://loki-read.monitoring.svc.cluster.local:3100")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") # Still needed here for initial check
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")     # Still needed here for initial check
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", 30))
LOKI_SCAN_RANGE_MINUTES = int(os.environ.get("LOKI_SCAN_RANGE_MINUTES", 1))
LOKI_DETAIL_LOG_RANGE_MINUTES = int(os.environ.get("LOKI_DETAIL_LOG_RANGE_MINUTES", 30))
LOKI_QUERY_LIMIT = int(os.environ.get("LOKI_QUERY_LIMIT", 500))
EXCLUDED_NAMESPACES_STR = os.environ.get("EXCLUDED_NAMESPACES", "kube-node-lease,kube-public")
EXCLUDED_NAMESPACES = {ns.strip() for ns in EXCLUDED_NAMESPACES_STR.split(',') if ns.strip()}
LOKI_SCAN_MIN_LEVEL = os.environ.get("LOKI_SCAN_MIN_LEVEL", "WARNING")
ALERT_SEVERITY_LEVELS_STR = os.environ.get("ALERT_SEVERITY_LEVELS", "WARNING,ERROR,CRITICAL")
ALERT_SEVERITY_LEVELS = [level.strip().upper() for level in ALERT_SEVERITY_LEVELS_STR.split(',') if level.strip()]
RESTART_COUNT_THRESHOLD = int(os.environ.get("RESTART_COUNT_THRESHOLD", 5))
DB_PATH = os.environ.get("DB_PATH", "/data/agent_stats.db")
STATS_UPDATE_INTERVAL_SECONDS = int(os.environ.get("STATS_UPDATE_INTERVAL_SECONDS", 300))
NAMESPACE_REFRESH_INTERVAL_SECONDS = int(os.environ.get("NAMESPACE_REFRESH_INTERVAL_SECONDS", 3600))
CONFIG_REFRESH_INTERVAL_SECONDS = int(os.environ.get("CONFIG_REFRESH_INTERVAL_SECONDS", 60))
ALERT_COOLDOWN_MINUTES = int(os.environ.get("ALERT_COOLDOWN_MINUTES", 30))
LOCAL_GEMINI_ENDPOINT_URL = os.environ.get("LOCAL_GEMINI_ENDPOINT")

# Default AI settings (used if DB values are missing/invalid or for init)
DEFAULT_ENABLE_AI_ANALYSIS = os.environ.get("ENABLE_AI_ANALYSIS", "true").lower() == "true"
DEFAULT_AI_PROVIDER = os.environ.get("AI_PROVIDER", "gemini").lower()
DEFAULT_AI_MODEL_IDENTIFIER = os.environ.get("AI_MODEL_IDENTIFIER", "gemini-1.5-flash")
# Default monitored namespaces (used for init_db)
DEFAULT_MONITORED_NAMESPACES_STR = os.environ.get("DEFAULT_MONITORED_NAMESPACES", "kube-system,default")
# Default Telegram Alert setting
DEFAULT_ENABLE_TELEGRAM_ALERTS = False # Default to disabled

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

DEFAULT_TELEGRAM_ALERT_TEMPLATE = """🚨 *Cảnh báo K8s/Log (Pod: {pod_key})* 🚨
*Mức độ:* `{severity}`
*Tóm tắt:* {summary}
*Nguyên nhân gốc có thể:*
{root_cause}
*Đề xuất khắc phục:*
{troubleshooting_steps}
*Lý do phát hiện ban đầu:* {initial_reasons}
*Thời gian phát hiện:* `{alert_time}`
*Log mẫu (nếu có):*
{sample_logs}

_Vui lòng kiểm tra chi tiết trên dashboard hoặc Loki/Kubernetes._"""
TELEGRAM_ALERT_TEMPLATE = os.environ.get("TELEGRAM_ALERT_TEMPLATE", DEFAULT_TELEGRAM_ALERT_TEMPLATE)

# --- Global State ---
model_calls_counter = 0
telegram_alerts_counter = 0 # Counter for alerts *actually sent*
db_lock = threading.Lock()
current_agent_config = {} # Cache for DB config
last_config_refresh_time = 0
gemini_model_instance = None # Cache Gemini client

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
    # ... (no changes needed in get_db_connection) ...
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
    # ... (no changes needed in table creation) ...
    db_dir = os.path.dirname(DB_PATH);
    if not os.path.exists(db_dir):
        try: os.makedirs(db_dir); logging.info(f"Created directory for database: {db_dir}")
        except OSError as e: logging.error(f"Could not create directory {db_dir}: {e}"); return False

    conn = get_db_connection()
    if conn is None: return False

    try:
        with conn:
            cursor = conn.cursor()
            logging.info("Ensuring database tables exist...")
            # --- Incidents Table ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, pod_key TEXT NOT NULL, severity TEXT NOT NULL,
                    summary TEXT, initial_reasons TEXT, k8s_context TEXT, sample_logs TEXT,
                    input_prompt TEXT, raw_ai_response TEXT, root_cause TEXT, troubleshooting_steps TEXT
                )
            ''')
            # --- Daily Stats Table ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY, model_calls INTEGER DEFAULT 0,
                    telegram_alerts INTEGER DEFAULT 0, incident_count INTEGER DEFAULT 0
                )
            ''')
            # --- Available Namespaces Table ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS available_namespaces ( name TEXT PRIMARY KEY, last_seen TEXT NOT NULL )
            ''')
            # --- Agent Config Table ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_config ( key TEXT PRIMARY KEY, value TEXT )
            ''')
            # --- Alert Cooldown Table ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alert_cooldown ( pod_key TEXT PRIMARY KEY, cooldown_until TEXT NOT NULL )
            ''')

            # --- Add missing columns (for upgrades) ---
            # ... (no changes needed here) ...

            # --- Initialize default config values if they don't exist ---
            default_configs = {
                'enable_ai_analysis': str(DEFAULT_ENABLE_AI_ANALYSIS).lower(),
                'ai_provider': DEFAULT_AI_PROVIDER,
                'ai_model_identifier': DEFAULT_AI_MODEL_IDENTIFIER,
                'ai_api_key': '', # API Key should NOT have a default in DB
                'monitored_namespaces': json.dumps([ns.strip() for ns in default_monitored_namespaces_str.split(',') if ns.strip()]),
                # Add Telegram defaults
                'enable_telegram_alerts': str(DEFAULT_ENABLE_TELEGRAM_ALERTS).lower(), # Add default for Telegram toggle
                'telegram_bot_token': '', # Keep these empty in DB by default
                'telegram_chat_id': ''
            }
            # Add other general defaults if not managed by Portal
            general_defaults = {
                'loki_scan_min_level': LOKI_SCAN_MIN_LEVEL,
                'scan_interval_seconds': str(SCAN_INTERVAL_SECONDS),
                'restart_count_threshold': str(RESTART_COUNT_THRESHOLD),
                'alert_severity_levels': ALERT_SEVERITY_LEVELS_STR,
                'alert_cooldown_minutes': str(ALERT_COOLDOWN_MINUTES)
            }
            default_configs.update(general_defaults)

            for key, value in default_configs.items():
                cursor.execute("INSERT OR IGNORE INTO agent_config (key, value) VALUES (?, ?)", (key, value))

            logging.info("Tables ensured and default config initialized.")
        logging.info(f"Database initialization/check complete at {DB_PATH}")
        return True
    except sqlite3.Error as e: logging.error(f"Database error during initialization: {e}", exc_info=True); return False
    except Exception as e: logging.error(f"Unexpected error during DB initialization: {e}", exc_info=True); return False
    finally:
        if conn: conn.close()

def load_agent_config_from_db():
    global current_agent_config, last_config_refresh_time
    now = time.time()
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

        # --- Process loaded config ---
        enable_ai_db = config_from_db.get('enable_ai_analysis', str(DEFAULT_ENABLE_AI_ANALYSIS)).lower()
        enable_telegram_db = config_from_db.get('enable_telegram_alerts', str(DEFAULT_ENABLE_TELEGRAM_ALERTS)).lower() # Get Telegram toggle

        current_agent_config = {
            # AI Config
            'enable_ai_analysis': enable_ai_db == 'true',
            'ai_provider': config_from_db.get('ai_provider', DEFAULT_AI_PROVIDER).lower(),
            'ai_model_identifier': config_from_db.get('ai_model_identifier', DEFAULT_AI_MODEL_IDENTIFIER),
            'ai_api_key': config_from_db.get('ai_api_key', ''),
            # Namespace Config
            'monitored_namespaces_json': config_from_db.get('monitored_namespaces', '[]'),
            # General Config (Read from DB or use env defaults)
            'loki_scan_min_level': config_from_db.get('loki_scan_min_level', LOKI_SCAN_MIN_LEVEL).upper(),
            'scan_interval_seconds': int(config_from_db.get('scan_interval_seconds', SCAN_INTERVAL_SECONDS)),
            'restart_count_threshold': int(config_from_db.get('restart_count_threshold', RESTART_COUNT_THRESHOLD)),
            'alert_severity_levels_str': config_from_db.get('alert_severity_levels', ALERT_SEVERITY_LEVELS_STR),
            'alert_cooldown_minutes': int(config_from_db.get('alert_cooldown_minutes', ALERT_COOLDOWN_MINUTES)),
            # Telegram Config
            'enable_telegram_alerts': enable_telegram_db == 'true', # Store boolean value
            'telegram_bot_token': config_from_db.get('telegram_bot_token', TELEGRAM_BOT_TOKEN), # Get from DB or fallback to Env
            'telegram_chat_id': config_from_db.get('telegram_chat_id', TELEGRAM_CHAT_ID),       # Get from DB or fallback to Env
        }
        # Update derived values
        current_agent_config['alert_severity_levels'] = [
            level.strip().upper() for level in current_agent_config['alert_severity_levels_str'].split(',') if level.strip()
        ]

        last_config_refresh_time = now
        logging.info(f"Current agent config: AI Enabled={current_agent_config['enable_ai_analysis']}, Provider={current_agent_config['ai_provider']}, Telegram Alerts Enabled={current_agent_config['enable_telegram_alerts']}") # Log Telegram status
    return current_agent_config

def get_monitored_namespaces():
    # ... (no changes needed in get_monitored_namespaces) ...
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
    # ... (no changes needed in record_incident) ...
    timestamp_str = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    if conn is None: logging.error(f"Failed to get DB connection for recording incident {pod_key}"); return
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

            # Use the alert severity levels from the current config
            config = load_agent_config_from_db()
            if severity in config.get('alert_severity_levels', []):
                today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                cursor.execute('''
                    INSERT INTO daily_stats (date, incident_count) VALUES (?, 1)
                    ON CONFLICT(date) DO UPDATE SET incident_count = incident_count + 1
                ''', (today_str,))
            logging.info(f"Recorded data for {pod_key} with severity {severity}")
    except sqlite3.Error as e: logging.error(f"Database error recording incident for {pod_key}: {e}")
    except Exception as e: logging.error(f"Unexpected error recording incident for {pod_key}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def update_daily_stats():
    # ... (no changes needed in update_daily_stats) ...
    global model_calls_counter, telegram_alerts_counter
    if model_calls_counter == 0 and telegram_alerts_counter == 0: return

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    with db_lock:
        calls_to_add = model_calls_counter
        alerts_to_add = telegram_alerts_counter
        model_calls_counter = 0
        telegram_alerts_counter = 0

    conn = get_db_connection()
    if conn is None: logging.error("Failed to get DB connection for updating daily stats"); return
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
            cursor.execute('''
                UPDATE daily_stats
                SET model_calls = model_calls + ?, telegram_alerts = telegram_alerts + ?
                WHERE date = ?
            ''', (calls_to_add, alerts_to_add, today_str))
            logging.info(f"Updated daily stats for {today_str}: +{calls_to_add} Model calls, +{alerts_to_add} Telegram alerts.")
    except sqlite3.Error as e:
        logging.error(f"Database error updating daily stats: {e}")
        with db_lock:
            model_calls_counter += calls_to_add
            telegram_alerts_counter += alerts_to_add
    except Exception as e:
        logging.error(f"Unexpected error updating daily stats: {e}", exc_info=True)
        with db_lock:
            model_calls_counter += calls_to_add
            telegram_alerts_counter += alerts_to_add
    finally:
        if conn: conn.close()

def periodic_stat_update():
    while True:
        time.sleep(STATS_UPDATE_INTERVAL_SECONDS) # Use global env var default
        update_daily_stats()

def is_pod_in_cooldown(pod_key):
    # ... (no changes needed in is_pod_in_cooldown) ...
    conn = get_db_connection()
    if conn is None:
        logging.error(f"Failed to get DB connection checking cooldown for {pod_key}")
        return False
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        with conn:
            cursor = conn.cursor()
            # Clean up expired cooldowns first
            cursor.execute("DELETE FROM alert_cooldown WHERE cooldown_until <= ?", (now_iso,))
            # Check if this pod still has an active cooldown
            cursor.execute("SELECT cooldown_until FROM alert_cooldown WHERE pod_key = ?", (pod_key,))
            result = cursor.fetchone()
            if result:
                cooldown_until_str = result['cooldown_until']
                # Compare ISO strings directly
                if cooldown_until_str > now_iso:
                     logging.info(f"Pod {pod_key} is in cooldown until {cooldown_until_str}.")
                     return True
                else:
                     # This case should ideally be handled by the DELETE above, but good for logging
                     logging.info(f"Cooldown expired for pod {pod_key} (was {cooldown_until_str}).")
                     return False
            # No cooldown entry found
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
    # ... (no changes needed in set_pod_cooldown, uses config value) ...
    config = load_agent_config_from_db()
    cooldown_minutes = config.get('alert_cooldown_minutes', 30) # Get from config

    conn = get_db_connection()
    if conn is None:
        logging.error(f"Failed to get DB connection setting cooldown for {pod_key}")
        return
    try:
        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
        cooldown_until_iso = cooldown_until.isoformat()
        with conn:
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
# ... (no changes needed in get_pod_info, get_node_info, get_pod_events, format_k8s_context) ...
def get_pod_info(namespace, pod_name):
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
    try:
        since_time = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        field_selector = f"involvedObject.kind=Pod,involvedObject.name={pod_name},involvedObject.namespace={namespace}"
        # Limit the number of events returned by the API
        events = k8s_core_v1.list_namespaced_event(namespace=namespace, field_selector=field_selector, limit=20) # Limit to 20 most recent
        recent_events = []
        if events and events.items:
                # Sort by timestamp (lastTimestamp first, fallback to creationTimestamp)
                sorted_events = sorted(
                    events.items,
                    key=lambda e: e.last_timestamp or e.metadata.creation_timestamp or datetime(MINYEAR, 1, 1, tzinfo=timezone.utc), # Handle potential None timestamps
                    reverse=True
                )
                for event in sorted_events:
                    event_time = event.last_timestamp or event.metadata.creation_timestamp
                    # Ensure event time is valid and within the desired window
                    if event_time and event_time >= since_time:
                        recent_events.append({
                            "time": event_time.isoformat(),
                            "type": event.type,
                            "reason": event.reason,
                            "message": event.message,
                            "count": event.count # Include count if available
                        })
                    # Stop if we have enough events or go past the time window
                    if len(recent_events) >= 10 or (event_time and event_time < since_time):
                        break
        return recent_events
    except ApiException as e:
        # Don't log error for Forbidden, as it's a common RBAC issue
        if e.status != 403:
            logging.warning(f"Could not list events for pod {namespace}/{pod_name}: {e.status} {e.reason}")
        # Return empty list on error
        return []
    except Exception as e:
        logging.error(f"Unexpected error listing events for pod {namespace}/{pod_name}: {e}", exc_info=True)
        return []

def format_k8s_context(pod_info, node_info, pod_events):
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
                # Filter for conditions that are NOT 'True' (or 'False' for non-Ready types)
                problematic_conditions = [
                    f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})"
                    for ctype, cinfo in pod_info['conditions'].items()
                    if cinfo.get('status') != 'True'
                ]
                if problematic_conditions: context_str += f"  Điều kiện Pod bất thường: {', '.join(problematic_conditions)}\n"
    if node_info:
        context_str += f"Node: {node_info['name']}\n"
        if node_info.get('conditions'):
                # Filter for Ready condition != True OR other conditions != False
                problematic_conditions = [
                    f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})"
                    for ctype, cinfo in node_info['conditions'].items()
                    if (ctype == 'Ready' and cinfo.get('status') != 'True') or \
                       (ctype != 'Ready' and cinfo.get('status') != 'False') # e.g., MemoryPressure=True is bad
                ]
                if problematic_conditions: context_str += f"  Điều kiện Node bất thường: {', '.join(problematic_conditions)}\n"
    if pod_events:
        context_str += "Sự kiện Pod gần đây (tối đa 10):\n"
        for event in pod_events: # Already sorted and limited
            message_preview = event['message'][:150] + ('...' if len(event['message']) > 150 else '')
            context_str += f"  - [{event['time']}] {event['type']} {event['reason']} (x{event.get('count',1)}): {message_preview}\n"
    context_str += "--- Kết thúc ngữ cảnh ---\n"
    return context_str

# --- Namespace Management ---
# ... (no changes needed in get_active_namespaces, update_available_namespaces_in_db) ...
def get_active_namespaces():
    active_namespaces = []
    try:
        # Use timeout to prevent hanging indefinitely
        all_namespaces = k8s_core_v1.list_namespace(watch=False, timeout_seconds=60)
        for ns in all_namespaces.items:
            # Check if namespace is active and not in the excluded list
            if ns.status.phase == "Active" and ns.metadata.name not in EXCLUDED_NAMESPACES:
                active_namespaces.append(ns.metadata.name)
        logging.info(f"Found {len(active_namespaces)} active and non-excluded namespaces in cluster.")
    except ApiException as e:
        logging.error(f"API Error listing namespaces: {e.status} {e.reason}. Check RBAC permissions.")
    except Exception as e:
        logging.error(f"Unexpected error listing namespaces: {e}", exc_info=True)
    return active_namespaces

def update_available_namespaces_in_db(namespaces):
    if not namespaces:
        logging.info("No active namespaces provided to update in DB.")
        # Optionally clear the table if no namespaces are found
        # conn = get_db_connection() ... cursor.execute("DELETE FROM available_namespaces") ...
        return

    timestamp = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    if conn is None:
        logging.error("Failed to get DB connection for updating available namespaces")
        return

    try:
        with conn:
            cursor = conn.cursor()
            # Use placeholders for safe deletion
            placeholders = ','.join('?' * len(namespaces))
            # Delete namespaces from DB that are no longer present in the cluster list
            if namespaces: # Avoid "DELETE ... WHERE name NOT IN ()" error
                 cursor.execute(f"DELETE FROM available_namespaces WHERE name NOT IN ({placeholders})", tuple(namespaces))
            else:
                 # If the provided list is empty, clear the table
                 cursor.execute("DELETE FROM available_namespaces")

            # Insert or update the current list of namespaces
            for ns in namespaces:
                cursor.execute("INSERT OR REPLACE INTO available_namespaces (name, last_seen) VALUES (?, ?)", (ns, timestamp))
            logging.info(f"Updated available_namespaces table in DB with {len(namespaces)} namespaces.")
    except sqlite3.Error as e:
        logging.error(f"Database error updating available namespaces: {e}")
    except Exception as e:
        logging.error(f"Unexpected error updating available namespaces: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

# --- Loki & K8s Scanning ---
def scan_loki_for_suspicious_logs(start_time, end_time, namespaces_to_scan, loki_scan_min_level):
    # ... (uses loki_scan_min_level from config) ...
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range";
    if not namespaces_to_scan: logging.info("No namespaces to scan in Loki."); return {}

    log_levels_all = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"];
    scan_level_index = -1
    try: scan_level_index = log_levels_all.index(loki_scan_min_level.upper()) # Use passed value
    except ValueError: logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {loki_scan_min_level}. Defaulting to WARNING."); scan_level_index = log_levels_all.index("WARNING")
    levels_to_scan = log_levels_all[scan_level_index:]

    namespace_regex = "|".join(namespaces_to_scan)
    # Common keywords indicating potential issues
    keywords_to_find = levels_to_scan + ["fail", "crash", "exception", "panic", "fatal", "timeout", "denied", "refused", "unable", "unauthorized", "error"]
    # Remove duplicates and escape for regex
    escaped_keywords = [re.escape(k) for k in sorted(list(set(keywords_to_find)), key=len, reverse=True)]; # Sort by length desc to match longer first
    regex_pattern = "(?i)(" + "|".join(escaped_keywords) + ")" # Case-insensitive search
    # LogQL query: Select namespaces and filter by regex pattern
    logql_query = f'{{namespace=~"{namespace_regex}"}} |~ `{regex_pattern}`'
    # Use a higher limit for scanning than detailed query
    query_limit_scan = 2000

    params = { 'query': logql_query, 'start': int(start_time.timestamp() * 1e9), 'end': int(end_time.timestamp() * 1e9), 'limit': query_limit_scan, 'direction': 'forward' }
    logging.info(f"Scanning Loki for suspicious logs (Level >= {loki_scan_min_level} or keywords) in {len(namespaces_to_scan)} namespaces...")
    suspicious_logs_by_pod = {}
    try:
        headers = {'Accept': 'application/json'}
        response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=60) # Increased timeout for scan
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        if 'data' in data and 'result' in data['data']:
            count = 0
            for stream in data['data']['result']:
                labels = stream.get('stream', {}); ns = labels.get('namespace'); pod_name = labels.get('pod')
                if not ns or not pod_name: continue # Skip if essential labels missing
                pod_key = f"{ns}/{pod_name}"
                if pod_key not in suspicious_logs_by_pod: suspicious_logs_by_pod[pod_key] = []
                for timestamp_ns, log_line in stream['values']:
                    log_entry = {
                        "timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc),
                        "message": log_line.strip(),
                        "labels": labels # Store labels for context
                    }
                    suspicious_logs_by_pod[pod_key].append(log_entry); count += 1
            logging.info(f"Loki scan found {count} suspicious log entries across {len(suspicious_logs_by_pod)} pods.")
        else: logging.info("Loki scan found no suspicious log entries matching the criteria.")
        return suspicious_logs_by_pod
    except requests.exceptions.HTTPError as e:
        error_detail = ""
        try: error_detail = e.response.text[:500] # Get first 500 chars of error response
        except Exception: pass # Ignore errors trying to get error detail
        logging.error(f"Error scanning Loki (HTTP {e.response.status_code}): {e}. Response: {error_detail}")
        return {} # Return empty dict on HTTP error
    except requests.exceptions.RequestException as e:
        logging.error(f"Error scanning Loki (Request failed): {e}")
        return {}
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding Loki scan response (Invalid JSON): {e}")
        return {}
    except Exception as e:
        logging.error(f"Unexpected error during Loki scan: {e}", exc_info=True)
        return {}

def scan_kubernetes_for_issues(namespaces_to_scan, restart_threshold):
    # ... (uses restart_threshold from config) ...
    problematic_pods = {}
    logging.info(f"Scanning {len(namespaces_to_scan)} Kubernetes namespaces for problematic pods...")
    for ns in namespaces_to_scan:
        try:
            pods = k8s_core_v1.list_namespaced_pod(namespace=ns, watch=False, timeout_seconds=60)
            for pod in pods.items:
                pod_key = f"{ns}/{pod.metadata.name}"; issue_found = False; reason = ""
                # 1. Check Pod Phase
                if pod.status.phase in ["Failed", "Unknown"]: issue_found = True; reason = f"Trạng thái Pod là {pod.status.phase}"
                # 2. Check Unschedulable Pending Pods
                elif pod.status.phase == "Pending" and pod.status.conditions:
                        scheduled_condition = next((c for c in pod.status.conditions if c.type == "PodScheduled"), None)
                        if scheduled_condition and scheduled_condition.status == "False" and scheduled_condition.reason == "Unschedulable": issue_found = True; reason = f"Pod không thể lên lịch (Unschedulable)"
                # 3. Check Container Statuses (if not already found issue)
                if not issue_found and pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        # High Restart Count
                        if cs.restart_count >= restart_threshold: # Use config value
                            issue_found = True; reason = f"Container '{cs.name}' restart {cs.restart_count} lần (>= ngưỡng {restart_threshold})"; break
                        # Bad Waiting States
                        if cs.state and cs.state.waiting and cs.state.waiting.reason in ["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "CreateContainerError"]:
                            issue_found = True; reason = f"Container '{cs.name}' đang ở trạng thái Waiting với lý do '{cs.state.waiting.reason}'"; break
                        # Bad Terminated States (check recent only if restartPolicy is Always)
                        if cs.state and cs.state.terminated and cs.state.terminated.reason in ["OOMKilled", "Error", "ContainerCannotRun"]:
                            is_recent_termination = cs.state.terminated.finished_at and (datetime.now(timezone.utc) - cs.state.terminated.finished_at) < timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
                            # Consider it an issue if restart policy isn't Always OR if it terminated recently
                            if pod.spec.restart_policy != "Always" or is_recent_termination:
                                issue_found = True; reason = f"Container '{cs.name}' bị Terminated với lý do '{cs.state.terminated.reason}'"; break
                # Store if issue found
                if issue_found:
                    logging.warning(f"Phát hiện pod có vấn đề tiềm ẩn (K8s Scan): {pod_key}. Lý do: {reason}")
                    if pod_key not in problematic_pods: problematic_pods[pod_key] = { "namespace": ns, "pod_name": pod.metadata.name, "reason": f"K8s: {reason}" }
        except ApiException as e: logging.error(f"API Error scanning namespace {ns}: {e.status} {e.reason}")
        except Exception as e: logging.error(f"Unexpected error scanning namespace {ns}: {e}", exc_info=True)
    logging.info(f"Finished K8s scan. Found {len(problematic_pods)} potentially problematic pods from K8s state.")
    return problematic_pods

def query_loki_for_pod(namespace, pod_name, start_time, end_time):
    # ... (no changes needed in query_loki_for_pod) ...
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range"; logql_query = f'{{namespace="{namespace}", pod="{pod_name}"}}'
    params = { 'query': logql_query, 'start': int(start_time.timestamp() * 1e9), 'end': int(end_time.timestamp() * 1e9), 'limit': LOKI_QUERY_LIMIT, 'direction': 'forward' }
    logging.info(f"Querying Loki for pod '{namespace}/{pod_name}' from {start_time} to {end_time}")
    try:
        headers = {'Accept': 'application/json'}; response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=45); response.raise_for_status(); data = response.json()
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
    # ... (uses loki_scan_min_level from config) ...
    filtered_logs = []; log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]; min_level_index = -1
    try: min_level_index = log_levels.index(loki_scan_min_level.upper()) # Use passed value
    except ValueError: logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {loki_scan_min_level}. Defaulting to WARNING."); min_level_index = log_levels.index("WARNING")
    keywords_indicating_problem = ["FAIL", "ERROR", "CRASH", "EXCEPTION", "UNAVAILABLE", "FATAL", "PANIC", "TIMEOUT", "DENIED", "REFUSED", "UNABLE", "UNAUTHORIZED"]
    for entry in log_entries:
        log_line = entry['message']; log_line_upper = log_line.upper(); level_detected = False
        # Check for standard log level indicators
        for i, level in enumerate(log_levels):
            # More robust level detection (common formats)
            if f" {level} " in f" {log_line_upper} " or \
               log_line_upper.startswith(level+":") or \
               f"[{level}]" in log_line_upper or \
               f"level={level.lower()}" in log_line_upper or \
               f"\"level\":\"{level.lower()}\"" in log_line_upper:
                if i >= min_level_index: # Check if level meets minimum requirement
                    filtered_logs.append(entry); level_detected = True; break
        # If no level detected, check for keywords if minimum level allows WARNING or lower
        if not level_detected:
            if any(keyword in log_line_upper for keyword in keywords_indicating_problem):
                    warning_index = log_levels.index("WARNING")
                    if min_level_index <= warning_index: # Only include keyword matches if min level is WARNING or lower
                        filtered_logs.append(entry)
    logging.info(f"Filtered {len(log_entries)} logs down to {len(filtered_logs)} relevant logs (Scan Level: {loki_scan_min_level}).")
    return filtered_logs

# --- AI Analysis & Alerting ---
def determine_severity_from_rules(initial_reasons, log_batch):
    # ... (no changes needed in determine_severity_from_rules) ...
    severity = "INFO" # Default severity
    reasons_upper = initial_reasons.upper() if initial_reasons else ""

    # Prioritize K8s reasons for critical/error states
    if "OOMKILLED" in reasons_upper or "ERROR" in reasons_upper or "FAILED" in reasons_upper:
        severity = "CRITICAL"
    elif "CRASHLOOPBACKOFF" in reasons_upper:
         severity = "ERROR"
    elif "UNSCHEDULABLE" in reasons_upper or "IMAGEPULLBACKOFF" in reasons_upper or "BACKOFF" in reasons_upper or "WAITING" in reasons_upper:
        severity = "WARNING"

    # If still INFO or WARNING, check logs for higher severity keywords
    if severity in ["INFO", "WARNING"]:
        critical_keywords = ["CRITICAL", "ALERT", "EMERGENCY", "PANIC", "FATAL"]
        error_keywords = ["ERROR", "FAIL", "EXCEPTION", "DENIED", "REFUSED", "UNAUTHORIZED"]
        warning_keywords = ["WARNING", "WARN", "TIMEOUT", "UNABLE", "SLOW"] # Less severe warnings

        # Check only a limited number of recent logs for performance
        for entry in log_batch[:20]: # Check first 20 relevant logs
            log_upper = entry['message'].upper()
            if any(kw in log_upper for kw in critical_keywords):
                severity = "CRITICAL"; break # Highest severity, stop checking
            if any(kw in log_upper for kw in error_keywords):
                severity = "ERROR"; break # Found error, stop checking (unless critical found later)
            # Only upgrade to WARNING if current severity is INFO
            if severity == "INFO" and any(kw in log_upper for kw in warning_keywords):
                 severity = "WARNING" # Found warning, continue checking for potential errors/criticals

    logging.info(f"Rule-based severity determined: {severity}")
    return severity

def get_default_analysis(severity, initial_reasons):
     # ... (no changes needed in get_default_analysis) ...
     summary = f"Phát hiện sự cố tiềm ẩn. Lý do ban đầu: {initial_reasons or 'Không có'}. Mức độ ước tính: {severity}. (Phân tích AI bị tắt)."
     root_cause = "Không có phân tích AI."
     troubleshooting_steps = "Kiểm tra ngữ cảnh Kubernetes và log chi tiết thủ công."
     return {"severity": severity, "summary": summary, "root_cause": root_cause, "troubleshooting_steps": troubleshooting_steps}

def call_gemini_api(api_key, model_identifier, prompt):
    # ... (no changes needed in call_gemini_api) ...
    global gemini_model_instance
    try:
        # Check if re-initialization is needed
        needs_reinit = (
            not gemini_model_instance or
            genai.API_KEY != api_key or # API key changed
            # Check if model name attribute exists and if it differs
            (hasattr(gemini_model_instance, 'model_name') and gemini_model_instance.model_name != model_identifier)
        )

        if needs_reinit:
            logging.info(f"Initializing/Re-initializing Gemini client for model {model_identifier}")
            if not api_key:
                raise ValueError("Cannot initialize Gemini client: API key is missing.")
            genai.configure(api_key=api_key)
            gemini_model_instance = genai.GenerativeModel(model_identifier)

        # Make the API call
        response = gemini_model_instance.generate_content(
            prompt,
            # Configure generation parameters (optional, adjust as needed)
            generation_config=genai.types.GenerationConfig(
                temperature=0.2, # Lower temperature for more deterministic results
                max_output_tokens=500 # Limit response length
            ),
            # Set a timeout for the request
            request_options={'timeout': 90} # 90 seconds timeout
        )
        return response
    except Exception as e:
        logging.error(f"Error calling Gemini API: {e}", exc_info=True)
        gemini_model_instance = None # Reset instance on error
        raise # Re-raise the exception to be handled by the caller

def call_local_api(endpoint_url, prompt):
     # ... (no changes needed in call_local_api) ...
     try:
        response = requests.post(endpoint_url, json={"prompt": prompt}, timeout=120) # Longer timeout for local models
        response.raise_for_status() # Raise HTTPError for bad responses
        try:
            # Try to parse as JSON first
            parsed_json = response.json()
            # Return the parsed JSON and the raw text
            return parsed_json, response.text
        except json.JSONDecodeError:
             # If JSON parsing fails, return None for JSON and the raw text
             logging.warning(f"Local API response from {endpoint_url} is not valid JSON. Returning raw text.")
             return None, response.text
     except requests.exceptions.RequestException as e:
          logging.error(f"Error calling local AI endpoint {endpoint_url}: {e}")
          raise # Re-raise for the caller

def analyze_incident(log_batch, k8s_context, initial_reasons):
    # ... (no changes needed in analyze_incident, uses config values) ...
    global model_calls_counter
    config = load_agent_config_from_db() # Get current config

    # --- Check if AI Analysis is Enabled ---
    if not config.get('enable_ai_analysis', False):
        logging.info("AI analysis is disabled by configuration.")
        # Determine severity based on rules if AI is off
        severity = determine_severity_from_rules(initial_reasons, log_batch)
        analysis_result = get_default_analysis(severity, initial_reasons)
        return analysis_result, None, None # No prompt or raw response when AI is off

    # --- AI Analysis Enabled ---
    with db_lock: model_calls_counter += 1 # Increment counter only if AI is enabled

    # --- Prepare Prompt ---
    namespace = "unknown"; pod_name = "unknown_pod"
    # Try to extract namespace/pod from logs first
    if log_batch and log_batch[0].get('labels'):
        labels = log_batch[0].get('labels', {}); namespace = labels.get('namespace', namespace); pod_name = labels.get('pod', pod_name)
    # Fallback to extracting from K8s context if logs don't have labels
    elif k8s_context:
        match_ns = re.search(r"Pod:\s*([\w.-]+)/", k8s_context); match_pod = re.search(r"Pod:\s*[\w.-]+/([\w.-]+)\n", k8s_context);
        if match_ns: namespace = match_ns.group(1);
        if match_pod: pod_name = match_pod.group(1)

    log_text = "N/A"
    if log_batch:
        # Limit log lines and length per line for the prompt
        limited_logs = [f"[{entry['timestamp'].isoformat()}] {entry['message'][:500]}" for entry in log_batch[:15]]; # Max 15 lines, 500 chars each
        log_text = "\n".join(limited_logs)

    final_prompt = None
    try:
        # Limit context and log text length significantly to avoid exceeding model limits
        final_prompt = PROMPT_TEMPLATE.format(
            namespace=namespace,
            pod_name=pod_name,
            k8s_context=k8s_context[:10000], # Limit K8s context size
            log_text=log_text[:20000]      # Limit log text size
        )
    except KeyError as e:
        logging.error(f"Missing placeholder in PROMPT_TEMPLATE: {e}. Using default prompt structure.")
        # Fallback prompt structure
        final_prompt = f"Phân tích pod {namespace}/{pod_name}. Ngữ cảnh K8s: {k8s_context[:10000]}. Logs: {log_text[:20000]}. Chỉ trả lời bằng JSON với khóa 'severity', 'summary', 'root_cause', 'troubleshooting_steps'."

    # --- Call Appropriate AI Provider ---
    provider = config.get('ai_provider', 'none')
    api_key = config.get('ai_api_key', '')
    model_id = config.get('ai_model_identifier', '')
    analysis_result = None
    raw_response_text = None
    analysis_failed = False

    try:
        if provider == "gemini":
            if not api_key: raise ValueError("Gemini provider selected but API key is missing in config.")
            if not model_id: raise ValueError("Gemini provider selected but model identifier is missing in config.")
            logging.info(f"Calling Gemini API (Model: {model_id}) for pod {namespace}/{pod_name}")
            response = call_gemini_api(api_key, model_id, final_prompt)
            # Handle potential empty or blocked responses
            if not response.parts:
                logging.warning("Gemini response has no parts. Raw response: %s", response)
                summary = "Gemini không trả về nội dung."
                try: # Try to get more details about why it failed
                    finish_reason = response.candidates[0].finish_reason if response.candidates else "UNKNOWN"
                    safety_ratings = response.candidates[0].safety_ratings if response.candidates else []
                    logging.warning(f"Gemini Finish Reason: {finish_reason}, Safety Ratings: {safety_ratings}")
                    if finish_reason.name == 'SAFETY': summary = "Phản hồi bị chặn bởi bộ lọc an toàn Gemini."
                    elif finish_reason.name == 'MAX_TOKENS': summary = "Phản hồi Gemini bị cắt do đạt giới hạn token."
                    else: summary = f"Gemini không trả về nội dung (Lý do: {finish_reason.name})."
                except Exception as inner_e: logging.error(f"Error extracting finish reason: {inner_e}")
                # Create a fallback result indicating the issue
                analysis_result = {"severity": "WARNING", "summary": summary, "root_cause": "N/A", "troubleshooting_steps": "Kiểm tra cấu hình Gemini hoặc prompt."}
                raw_response_text = str(response) # Store raw response object as string
                analysis_failed = True # Mark as failed for potential counter decrement
            else:
                raw_response_text = response.text.strip()
                # Clean potential markdown code blocks
                cleaned_response_text = raw_response_text
                if cleaned_response_text.startswith("```json"): cleaned_response_text = cleaned_response_text.strip("```json").strip("`").strip()
                elif cleaned_response_text.startswith("```"): cleaned_response_text = cleaned_response_text.strip("```").strip()
                # Extract JSON part more robustly
                match = re.search(r'\{.*\}', cleaned_response_text, re.DOTALL); json_string_to_parse = match.group(0) if match else cleaned_response_text
                try:
                    analysis_result = json.loads(json_string_to_parse)
                    if not isinstance(analysis_result, dict): raise ValueError("Parsed response is not a dictionary.")
                except (json.JSONDecodeError, ValueError) as json_err:
                    logging.warning(f"Failed to decode/validate Gemini JSON: {json_err}. Raw: {raw_response_text}")
                    analysis_result = None # Reset result
                    analysis_failed = True # Mark as failed

        elif provider == "local":
            if not LOCAL_GEMINI_ENDPOINT_URL: raise ValueError("Local provider selected but LOCAL_GEMINI_ENDPOINT is not configured.")
            logging.info(f"Calling local AI endpoint ({LOCAL_GEMINI_ENDPOINT_URL}) for pod {namespace}/{pod_name}")
            analysis_result, raw_response_text = call_local_api(LOCAL_GEMINI_ENDPOINT_URL, final_prompt)
            if analysis_result is None: # Check if JSON parsing failed in call_local_api
                 analysis_failed = True

        # elif provider == "openai": ... # Add other providers here

        else: # Includes 'none' or unsupported providers
            logging.warning(f"Unsupported AI provider '{provider}' configured or provider is 'none'. Falling back to rule-based analysis.")
            analysis_failed = True

    except Exception as e:
        logging.error(f"Error during AI analysis call (Provider: {provider}): {e}", exc_info=True)
        analysis_failed = True

    # --- Fallback if AI failed ---
    if analysis_failed:
        logging.warning(f"AI analysis failed or provider unsupported/misconfigured. Falling back to rule-based analysis for {namespace}/{pod_name}.")
        severity = determine_severity_from_rules(initial_reasons, log_batch)
        analysis_result = get_default_analysis(severity, initial_reasons)
        # Update summary to indicate AI failure
        analysis_result["summary"] = f"Phân tích AI thất bại hoặc bị tắt. Lý do ban đầu: {initial_reasons or 'Không có'}. Mức độ ước tính: {severity}."
        # Decrement counter if AI was enabled but failed
        if config.get('enable_ai_analysis', False):
             with db_lock:
                  if model_calls_counter > 0: model_calls_counter -= 1 # Decrement if we incremented earlier

    # --- Ensure result format ---
    if not isinstance(analysis_result, dict):
         # This should ideally not happen if fallback logic works, but as a safeguard
         logging.error(f"Analysis result is not a dictionary for {namespace}/{pod_name}. Falling back again.")
         severity = determine_severity_from_rules(initial_reasons, log_batch)
         analysis_result = get_default_analysis(severity, initial_reasons)
    else:
         # Ensure all required keys exist in the final result
         analysis_result.setdefault("severity", "WARNING") # Default severity if missing
         analysis_result.setdefault("summary", "N/A")
         analysis_result.setdefault("root_cause", "N/A")
         analysis_result.setdefault("troubleshooting_steps", "N/A")

    return analysis_result, final_prompt, raw_response_text

def send_telegram_alert(alert_data):
    global telegram_alerts_counter # Use the global counter

    # --- Check if Telegram Alerts are Enabled ---
    config = load_agent_config_from_db()
    if not config.get('enable_telegram_alerts', False):
        logging.info(f"Telegram alerts are disabled by configuration. Skipping alert for {alert_data.get('pod_key', 'N/A')}.")
        return # Exit without sending or incrementing counter

    # --- Proceed only if enabled ---
    bot_token = config.get('telegram_bot_token')
    chat_id = config.get('telegram_chat_id')

    if not bot_token or not chat_id:
        logging.warning("Telegram Bot Token or Chat ID not configured in DB/Env. Skipping alert.")
        return

    # --- Increment counter *only if* sending is attempted ---
    with db_lock:
        telegram_alerts_counter += 1

    telegram_api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # Check current AI analysis status from cached config for message formatting
    ai_enabled = config.get('enable_ai_analysis', False)

    # Determine the summary to use
    summary = alert_data.get('summary', 'N/A')
    if not ai_enabled and "Phân tích AI thất bại" not in summary and "Phân tích AI bị tắt" not in summary:
        # Use a simpler summary if AI is disabled and wasn't already handled by fallback
        severity = alert_data.get('severity', 'UNKNOWN')
        initial_reasons = alert_data.get('initial_reasons', 'Không có')
        summary = f"Phát hiện sự cố {severity}. Lý do: {initial_reasons}. (AI tắt)"

    # Base message parts
    message_lines = [
        f"🚨 *Cảnh báo K8s/Log (Pod: {alert_data.get('pod_key', 'N/A')})* 🚨",
        f"*Mức độ:* `{alert_data.get('severity', 'UNKNOWN')}`",
        f"*Tóm tắt:* {summary}" # Use the determined summary
    ]

    # Conditionally add AI-specific sections ONLY if AI is enabled AND results are meaningful
    if ai_enabled:
        root_cause = alert_data.get('root_cause', '')
        steps = alert_data.get('troubleshooting_steps', '')
        # Check if the AI results are not the generic fallback messages
        default_root_cause = "Không có phân tích AI."
        default_steps = "Kiểm tra ngữ cảnh Kubernetes và log chi tiết thủ công."
        ai_meaningful = root_cause not in ["N/A", default_root_cause] or steps not in ["N/A", default_steps]

        if ai_meaningful:
             if root_cause and root_cause != "N/A":
                 message_lines.append(f"*Nguyên nhân gốc có thể:*\n{root_cause}")
             if steps and steps != "N/A":
                 message_lines.append(f"*Đề xuất khắc phục:*\n{steps}")
        # else: The summary already indicates AI failure/disabled state

    # Add remaining common parts
    message_lines.extend([
        f"*Lý do phát hiện ban đầu:* {alert_data.get('initial_reasons', 'N/A')}",
        f"*Thời gian phát hiện:* `{alert_data.get('alert_time', 'N/A')}`",
        # Ensure sample logs are formatted reasonably
        f"*Log mẫu (nếu có):*\n{alert_data.get('sample_logs', '-').replace('`', '')}", # Remove backticks from logs
        "\n_Vui lòng kiểm tra chi tiết trên dashboard hoặc Loki/Kubernetes._"
    ])

    message = "\n".join(message_lines)

    # Truncate message if it exceeds Telegram limit (4096 chars)
    max_len = 4096
    truncated_message = message
    if len(message.encode('utf-8')) > max_len: # Check byte length for safety
        # Simple truncation (might cut mid-character, but safer than erroring)
        # A more robust approach would involve character-aware truncation
        truncated_message = message[:max_len - 100] + "\n\n_[... message truncated ...]_"
        logging.warning(f"Alert message for {alert_data.get('pod_key')} exceeded Telegram limit and was truncated.")


    payload = {
        'chat_id': chat_id,
        'text': truncated_message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(telegram_api_url, json=payload, timeout=15) # Slightly longer timeout
        response.raise_for_status()
        logging.info(f"Sent alert to Telegram for {alert_data.get('pod_key')}. Response OK: {response.json().get('ok', False)}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending Telegram alert for {alert_data.get('pod_key')}: {e}")
        # Decrement counter if sending failed
        with db_lock:
            if telegram_alerts_counter > 0: telegram_alerts_counter -= 1
    except Exception as e:
        logging.error(f"An unexpected error occurred during Telegram send for {alert_data.get('pod_key')}: {e}", exc_info=True)
        # Decrement counter on unexpected error too
        with db_lock:
            if telegram_alerts_counter > 0: telegram_alerts_counter -= 1


# --- Main Monitoring Cycle ---
def refresh_namespaces_if_needed(last_refresh_time):
    # ... (uses NAMESPACE_REFRESH_INTERVAL_SECONDS from env) ...
    current_time_secs = time.time()
    if current_time_secs - last_refresh_time >= NAMESPACE_REFRESH_INTERVAL_SECONDS:
        logging.info("Refreshing list of active namespaces from Kubernetes API...")
        all_active_namespaces = get_active_namespaces()
        update_available_namespaces_in_db(all_active_namespaces)
        return current_time_secs # Return new refresh time
    return last_refresh_time # Return old time if not refreshed

def identify_pods_to_investigate(monitored_namespaces, start_cycle_time, config):
    # Pass config down to scanners
    k8s_problem_pods = scan_kubernetes_for_issues(monitored_namespaces, config['restart_count_threshold'])
    loki_scan_end_time = start_cycle_time; loki_scan_start_time = loki_scan_end_time - timedelta(minutes=LOKI_SCAN_RANGE_MINUTES)
    loki_suspicious_logs = scan_loki_for_suspicious_logs(loki_scan_start_time, loki_scan_end_time, monitored_namespaces, config['loki_scan_min_level'])

    pods_to_investigate = {}
    # Combine results from K8s scan
    for pod_key, data in k8s_problem_pods.items():
        if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
        pods_to_investigate[pod_key]["reason"].append(data["reason"])
    # Combine results from Loki scan
    for pod_key, logs in loki_suspicious_logs.items():
            if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
            reason_text = f"Loki: Phát hiện {len(logs)} log đáng ngờ (>= {config['loki_scan_min_level']})"
            # Avoid duplicate Loki reasons if pod also had K8s issue
            if reason_text not in pods_to_investigate[pod_key]["reason"]:
                 pods_to_investigate[pod_key]["reason"].append(reason_text)
            # Add logs found during scan (will be used if detailed query isn't needed)
            pods_to_investigate[pod_key]["logs"].extend(logs) # Add logs found in scan

    logging.info(f"Total pods to investigate this cycle: {len(pods_to_investigate)}")
    return pods_to_investigate

def investigate_pod_details(namespace, pod_name, initial_reasons, suspicious_logs_found_in_scan, config):
    # Pass config down
    logging.info(f"Investigating pod: {namespace}/{pod_name} (Initial Reasons: {initial_reasons})")
    pod_info = get_pod_info(namespace, pod_name); node_info = None; pod_events = []
    if pod_info: node_info = get_node_info(pod_info.get('node_name')); pod_events = get_pod_events(namespace, pod_name, since_minutes=LOKI_DETAIL_LOG_RANGE_MINUTES + 5)
    k8s_context_str = format_k8s_context(pod_info, node_info, pod_events)

    # Use logs from initial scan if available, otherwise query Loki for more detail
    logs_for_analysis = suspicious_logs_found_in_scan
    if not logs_for_analysis:
        logging.info(f"No logs found in initial scan for {namespace}/{pod_name}. Querying Loki for detailed logs...")
        log_end_time = datetime.now(timezone.utc); log_start_time = log_end_time - timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
        detailed_logs = query_loki_for_pod(namespace, pod_name, log_start_time, log_end_time)
        logs_for_analysis = preprocess_and_filter(detailed_logs, config['loki_scan_min_level']) # Use config value
    # else: Logs from scan are already filtered implicitly by the scan query

    return logs_for_analysis, k8s_context_str

# --- Updated process_analysis_and_alert ---
def process_analysis_and_alert(pod_key, initial_reasons, logs_for_analysis, k8s_context_str, config):
    # Pass config down
    namespace, pod_name = pod_key.split('/', 1);
    analysis_start_time = time.time()
    # analyze_incident already uses the latest config internally
    analysis_result, final_prompt, raw_response_text = analyze_incident(logs_for_analysis, k8s_context_str, initial_reasons)
    analysis_duration = time.time() - analysis_start_time
    logging.info(f"Analysis for {pod_key} took {analysis_duration:.2f} seconds.")

    # --- FIX: Safely get values from analysis_result, defaulting to "N/A" if key missing OR value is None ---
    severity = (analysis_result.get("severity") or "UNKNOWN").upper() # Ensure uppercase after getting value
    summary = analysis_result.get("summary") or "N/A"
    root_cause_str = analysis_result.get("root_cause") or "N/A"
    steps_str = analysis_result.get("troubleshooting_steps") or "N/A"
    # --- END FIX ---

    # Logging is now safe because variables are guaranteed to be strings
    logging.info(f"Analysis result for '{pod_key}': Severity={severity}, Summary={summary[:100]}..., RootCause={root_cause_str[:100]}..., Steps={steps_str[:100]}...")

    # Prepare sample logs for DB and alert
    sample_logs_str = "\n".join([f"- `{log['message'][:150]}`" for log in logs_for_analysis[:5]]) if logs_for_analysis else "-"

    # Record incident details in the database (using the safe variables)
    record_incident( pod_key, severity, summary, initial_reasons, k8s_context_str, sample_logs_str, final_prompt, raw_response_text, root_cause_str, steps_str )

    # Check if severity warrants an alert based on current config
    if severity in config.get('alert_severity_levels', []):
        alert_time_hcm = datetime.now(HCM_TZ); time_format = '%Y-%m-%d %H:%M:%S %Z'
        alert_data = {
            'pod_key': pod_key, 'severity': severity, 'summary': summary, # Use safe summary
            'root_cause': root_cause_str, # Use safe root_cause
            'troubleshooting_steps': steps_str, # Use safe steps
            'initial_reasons': initial_reasons,
            'alert_time': alert_time_hcm.strftime(time_format),
            'sample_logs': sample_logs_str if sample_logs_str != "-" else "- Không có log mẫu liên quan."
        }
        # send_telegram_alert will check the enable_telegram_alerts flag internally
        send_telegram_alert(alert_data)
        set_pod_cooldown(pod_key) # Set cooldown after attempting to send alert


def perform_monitoring_cycle(last_namespace_refresh_time):
    start_cycle_time = datetime.now(timezone.utc)
    logging.info("--- Starting new monitoring cycle ---")

    # Load config at the beginning of the cycle
    config = load_agent_config_from_db()
    current_scan_interval = config.get('scan_interval_seconds', SCAN_INTERVAL_SECONDS) # Use interval from config

    # Refresh namespaces if needed
    last_namespace_refresh_time = refresh_namespaces_if_needed(last_namespace_refresh_time)

    # Get currently monitored namespaces from config
    monitored_namespaces = get_monitored_namespaces()
    if not monitored_namespaces:
        logging.warning("No namespaces configured for monitoring. Skipping cycle.")
        cycle_duration = (datetime.now(timezone.utc) - start_cycle_time).total_seconds()
        sleep_time = max(0, current_scan_interval - cycle_duration)
        logging.info(f"--- Cycle finished early (no namespaces). Sleeping for {sleep_time:.2f} seconds... ---")
        time.sleep(sleep_time)
        return last_namespace_refresh_time

    logging.info(f"Currently monitoring {len(monitored_namespaces)} namespaces: {', '.join(monitored_namespaces)}")

    # Identify pods needing investigation using current config
    pods_to_investigate = identify_pods_to_investigate(monitored_namespaces, start_cycle_time, config)

    # Investigate each identified pod
    for pod_key, data in pods_to_investigate.items():
        # Check cooldown *before* investigation
        if is_pod_in_cooldown(pod_key):
            continue

        namespace, pod_name = pod_key.split('/', 1);
        initial_reasons = "; ".join(data["reason"]);
        suspicious_logs_found_in_scan = data["logs"]

        try:
            # Investigate and get logs/context
            logs_for_analysis, k8s_context_str = investigate_pod_details( namespace, pod_name, initial_reasons, suspicious_logs_found_in_scan, config )
            # Analyze, record, and potentially alert (Pass config down)
            process_analysis_and_alert( pod_key, initial_reasons, logs_for_analysis, k8s_context_str, config )
        except Exception as investigation_err:
            logging.error(f"Error during investigation/analysis for {pod_key}: {investigation_err}", exc_info=True)

        # Small delay between processing pods to avoid overwhelming APIs
        time.sleep(1) # Consider making this configurable if needed

    # Calculate sleep time based on actual cycle duration and configured interval
    cycle_duration = (datetime.now(timezone.utc) - start_cycle_time).total_seconds()
    sleep_time = max(0, current_scan_interval - cycle_duration)
    logging.info(f"--- Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
    time.sleep(sleep_time)
    return last_namespace_refresh_time


def main_loop():
    last_namespace_refresh_time = 0
    while True:
        try:
            last_namespace_refresh_time = perform_monitoring_cycle(last_namespace_refresh_time)
        except Exception as cycle_err:
             logging.critical(f"Unhandled exception in monitoring cycle: {cycle_err}", exc_info=True)
             # Use the scan interval from the last known config, or default if config failed
             config = current_agent_config or {}
             sleep_interval = config.get('scan_interval_seconds', SCAN_INTERVAL_SECONDS)
             logging.info(f"Sleeping for {sleep_interval} seconds before next cycle after error.")
             time.sleep(sleep_interval)
             last_namespace_refresh_time = 0 # Force namespace refresh after major error


# --- Main Execution ---
if __name__ == "__main__":
    # Pass the default monitored namespaces string to init_db
    if not init_db(DEFAULT_MONITORED_NAMESPACES_STR):
        logging.error("Failed to initialize database. Exiting.")
        exit(1)

    load_agent_config_from_db() # Load initial config

    stats_thread = threading.Thread(target=periodic_stat_update, daemon=True);
    stats_thread.start();
    logging.info("Started periodic stats update thread.")

    logging.info(f"Starting Kubernetes Log Monitoring Agent")
    logging.info(f"Loki URL: {LOKI_URL}") # Log initial Loki URL from env

    # Log initial config values read from DB/defaults
    initial_config = current_agent_config
    logging.info(f"Initial Config - Scan Interval: {initial_config.get('scan_interval_seconds')}s")
    logging.info(f"Initial Config - Loki Scan Level: {initial_config.get('loki_scan_min_level')}")
    logging.info(f"Initial Config - Alert Levels: {initial_config.get('alert_severity_levels_str')}")
    logging.info(f"Initial Config - Restart Threshold: {initial_config.get('restart_count_threshold')}")
    logging.info(f"Initial Config - Alert Cooldown: {initial_config.get('alert_cooldown_minutes')}m")
    logging.info(f"Initial Config - AI Enabled: {initial_config.get('enable_ai_analysis')}, Provider: {initial_config.get('ai_provider')}, Model: {initial_config.get('ai_model_identifier')}")
    logging.info(f"Initial Config - Telegram Alerts Enabled: {initial_config.get('enable_telegram_alerts')}") # Log initial Telegram status

    # Check if essential Telegram vars are set (either in Env or DB after initial load)
    # We rely on the config loading logic now, so this check might be less critical here,
    # but kept for safety during initial startup before first DB read potentially.
    # if not initial_config.get('telegram_bot_token') or not initial_config.get('telegram_chat_id'):
    #     logging.warning("Telegram Bot Token or Chat ID might be missing in initial config (DB/Env). Alerts may fail until configured via Portal.")
        # Don't exit, allow Portal configuration

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

