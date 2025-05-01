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
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
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
LOCAL_GEMINI_ENDPOINT_URL = os.environ.get("LOCAL_GEMINI_ENDPOINT") # Keep for "local" provider

# Default AI settings (used if DB values are missing/invalid or for init)
DEFAULT_ENABLE_AI_ANALYSIS = os.environ.get("ENABLE_AI_ANALYSIS", "true").lower() == "true"
DEFAULT_AI_PROVIDER = os.environ.get("AI_PROVIDER", "gemini").lower()
DEFAULT_AI_MODEL_IDENTIFIER = os.environ.get("AI_MODEL_IDENTIFIER", "gemini-1.5-flash")
# Default monitored namespaces (used for init_db)
DEFAULT_MONITORED_NAMESPACES_STR = os.environ.get("DEFAULT_MONITORED_NAMESPACES", "kube-system,default")


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

# Keep the full template here, logic in send_telegram_alert will handle conditional parts
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
telegram_alerts_counter = 0
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

# Modified init_db to accept default monitored namespaces string
def init_db(default_monitored_namespaces_str):
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
            cursor.execute("PRAGMA table_info(incidents)")
            columns_inc = [column[1] for column in cursor.fetchall()]
            if 'input_prompt' not in columns_inc: cursor.execute("ALTER TABLE incidents ADD COLUMN input_prompt TEXT")
            if 'raw_ai_response' not in columns_inc: cursor.execute("ALTER TABLE incidents ADD COLUMN raw_ai_response TEXT")
            if 'root_cause' not in columns_inc: cursor.execute("ALTER TABLE incidents ADD COLUMN root_cause TEXT")
            if 'troubleshooting_steps' not in columns_inc: cursor.execute("ALTER TABLE incidents ADD COLUMN troubleshooting_steps TEXT")

            cursor.execute("PRAGMA table_info(daily_stats)")
            columns_stats = [column[1] for column in cursor.fetchall()]
            if 'model_calls' not in columns_stats and 'gemini_calls' in columns_stats:
                cursor.execute("ALTER TABLE daily_stats RENAME COLUMN gemini_calls TO model_calls")
            elif 'model_calls' not in columns_stats:
                cursor.execute("ALTER TABLE daily_stats ADD COLUMN model_calls INTEGER DEFAULT 0")

            # --- Initialize default config values if they don't exist ---
            default_configs = {
                'enable_ai_analysis': str(DEFAULT_ENABLE_AI_ANALYSIS).lower(),
                'ai_provider': DEFAULT_AI_PROVIDER,
                'ai_model_identifier': DEFAULT_AI_MODEL_IDENTIFIER,
                'ai_api_key': '', # API Key should NOT have a default in DB
                # Use the passed argument here
                'monitored_namespaces': json.dumps([ns.strip() for ns in default_monitored_namespaces_str.split(',') if ns.strip()])
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

        enable_ai_db = config_from_db.get('enable_ai_analysis', str(DEFAULT_ENABLE_AI_ANALYSIS)).lower()
        current_agent_config = {
            'enable_ai_analysis': enable_ai_db == 'true',
            'ai_provider': config_from_db.get('ai_provider', DEFAULT_AI_PROVIDER).lower(),
            'ai_model_identifier': config_from_db.get('ai_model_identifier', DEFAULT_AI_MODEL_IDENTIFIER),
            'ai_api_key': config_from_db.get('ai_api_key', ''),
            'monitored_namespaces_json': config_from_db.get('monitored_namespaces', '[]')
        }
        last_config_refresh_time = now
        logging.info(f"Current agent config: AI Enabled={current_agent_config['enable_ai_analysis']}, Provider={current_agent_config['ai_provider']}")
    return current_agent_config

def get_monitored_namespaces():
    config = load_agent_config_from_db()
    namespaces_json = config.get('monitored_namespaces_json', '[]')
    # Use the global default string here as fallback
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

            if severity in ALERT_SEVERITY_LEVELS:
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
        time.sleep(STATS_UPDATE_INTERVAL_SECONDS)
        update_daily_stats()

def is_pod_in_cooldown(pod_key):
    conn = get_db_connection()
    if conn is None:
        logging.error(f"Failed to get DB connection checking cooldown for {pod_key}")
        return False
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM alert_cooldown WHERE cooldown_until <= ?", (now_iso,))
            cursor.execute("SELECT cooldown_until FROM alert_cooldown WHERE pod_key = ?", (pod_key,))
            result = cursor.fetchone()
            if result:
                cooldown_until_str = result['cooldown_until']
                if cooldown_until_str > now_iso:
                     logging.info(f"Pod {pod_key} is in cooldown until {cooldown_until_str}.")
                     return True
                else:
                     logging.info(f"Cooldown expired for pod {pod_key} (was {cooldown_until_str}).")
                     return False
            return False
    except sqlite3.Error as e:
        logging.error(f"Database error checking cooldown for {pod_key}: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error checking cooldown for {pod_key}: {e}", exc_info=True)
        return False
    finally:
        if conn: conn.close()

def set_pod_cooldown(pod_key):
    conn = get_db_connection()
    if conn is None:
        logging.error(f"Failed to get DB connection setting cooldown for {pod_key}")
        return
    try:
        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=ALERT_COOLDOWN_MINUTES)
        cooldown_until_iso = cooldown_until.isoformat()
        with conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO alert_cooldown (pod_key, cooldown_until) VALUES (?, ?)",
                           (pod_key, cooldown_until_iso))
            logging.info(f"Set cooldown for pod {pod_key} until {cooldown_until_iso}")
    except sqlite3.Error as e:
        logging.error(f"Database error setting cooldown for {pod_key}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error setting cooldown for {pod_key}: {e}", exc_info=True)
    finally:
        if conn: conn.close()

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
        events = k8s_core_v1.list_namespaced_event(namespace=namespace, field_selector=field_selector, limit=20)
        recent_events = []
        if events and events.items:
                sorted_events = sorted( events.items, key=lambda e: e.last_timestamp or e.metadata.creation_timestamp or datetime(MINYEAR, 1, 1, tzinfo=timezone.utc), reverse=True )
                for event in sorted_events:
                    event_time = event.last_timestamp or event.metadata.creation_timestamp
                    if event_time and event_time >= since_time:
                        recent_events.append({ "time": event_time.isoformat(), "type": event.type, "reason": event.reason, "message": event.message, "count": event.count })
                    if len(recent_events) >= 10 or (event_time and event_time < since_time): break
        return recent_events
    except ApiException as e:
        if e.status != 403: logging.warning(f"Could not list events for pod {namespace}/{pod_name}: {e.status} {e.reason}")
        return []
    except Exception as e: logging.error(f"Unexpected error listing events for pod {namespace}/{pod_name}: {e}", exc_info=True); return []

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

def get_active_namespaces():
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
    if not namespaces: return
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    if conn is None: logging.error("Failed to get DB connection for updating available namespaces"); return
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
            logging.info(f"Updated available_namespaces table in DB with {len(namespaces)} namespaces.")
    except sqlite3.Error as e: logging.error(f"Database error updating available namespaces: {e}")
    except Exception as e: logging.error(f"Unexpected error updating available namespaces: {e}", exc_info=True)
    finally:
        if conn: conn.close()

def scan_loki_for_suspicious_logs(start_time, end_time, namespaces_to_scan):
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range";
    if not namespaces_to_scan: logging.info("No namespaces to scan in Loki."); return {}

    log_levels_all = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"];
    scan_level_index = -1
    try: scan_level_index = log_levels_all.index(LOKI_SCAN_MIN_LEVEL.upper())
    except ValueError: logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {LOKI_SCAN_MIN_LEVEL}. Defaulting to WARNING."); scan_level_index = log_levels_all.index("WARNING")
    levels_to_scan = log_levels_all[scan_level_index:]

    namespace_regex = "|".join(namespaces_to_scan)
    keywords_to_find = levels_to_scan + ["fail", "crash", "exception", "panic", "fatal", "timeout", "denied", "refused", "unable", "unauthorized"]
    escaped_keywords = [re.escape(k) for k in keywords_to_find];
    regex_pattern = "(?i)(" + "|".join(escaped_keywords) + ")"
    logql_query = f'{{namespace=~"{namespace_regex}"}} |~ `{regex_pattern}`'
    query_limit_scan = 2000

    params = { 'query': logql_query, 'start': int(start_time.timestamp() * 1e9), 'end': int(end_time.timestamp() * 1e9), 'limit': query_limit_scan, 'direction': 'forward' }
    logging.info(f"Scanning Loki for suspicious logs (Level >= {LOKI_SCAN_MIN_LEVEL} or keywords) in {len(namespaces_to_scan)} namespaces...")
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
        try:
            error_detail = e.response.text[:500]
        except Exception:
            pass # Ignore errors trying to get error detail
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


def scan_kubernetes_for_issues(namespaces_to_scan):
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
                        if cs.restart_count >= RESTART_COUNT_THRESHOLD: issue_found = True; reason = f"Container '{cs.name}' restart {cs.restart_count} lần (>= ngưỡng {RESTART_COUNT_THRESHOLD})"; break
                        if cs.state and cs.state.waiting and cs.state.waiting.reason in ["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "CreateContainerError"]: issue_found = True; reason = f"Container '{cs.name}' đang ở trạng thái Waiting với lý do '{cs.state.waiting.reason}'"; break
                        if cs.state and cs.state.terminated and cs.state.terminated.reason in ["OOMKilled", "Error", "ContainerCannotRun"]:
                            is_recent_termination = cs.state.terminated.finished_at and (datetime.now(timezone.utc) - cs.state.terminated.finished_at) < timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
                            if pod.spec.restart_policy != "Always" or is_recent_termination: issue_found = True; reason = f"Container '{cs.name}' bị Terminated với lý do '{cs.state.terminated.reason}'"; break
                if issue_found:
                    logging.warning(f"Phát hiện pod có vấn đề tiềm ẩn (K8s Scan): {pod_key}. Lý do: {reason}")
                    if pod_key not in problematic_pods: problematic_pods[pod_key] = { "namespace": ns, "pod_name": pod.metadata.name, "reason": f"K8s: {reason}" }
        except ApiException as e: logging.error(f"API Error scanning namespace {ns}: {e.status} {e.reason}")
        except Exception as e: logging.error(f"Unexpected error scanning namespace {ns}: {e}", exc_info=True)
    logging.info(f"Finished K8s scan. Found {len(problematic_pods)} potentially problematic pods from K8s state.")
    return problematic_pods

def query_loki_for_pod(namespace, pod_name, start_time, end_time):
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

def preprocess_and_filter(log_entries):
    filtered_logs = []; log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]; min_level_index = -1
    try: min_level_index = log_levels.index(LOKI_SCAN_MIN_LEVEL.upper())
    except ValueError: logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {LOKI_SCAN_MIN_LEVEL}. Defaulting to WARNING."); min_level_index = log_levels.index("WARNING")
    keywords_indicating_problem = ["FAIL", "ERROR", "CRASH", "EXCEPTION", "UNAVAILABLE", "FATAL", "PANIC", "TIMEOUT", "DENIED", "REFUSED", "UNABLE", "UNAUTHORIZED"]
    for entry in log_entries:
        log_line = entry['message']; log_line_upper = log_line.upper(); level_detected = False
        for i, level in enumerate(log_levels):
            if f" {level} " in f" {log_line_upper} " or log_line_upper.startswith(level+":") or f"[{level}]" in log_line_upper or f"level={level.lower()}" in log_line_upper or f"\"level\":\"{level.lower()}\"" in log_line_upper:
                if i >= min_level_index: filtered_logs.append(entry); level_detected = True; break
        if not level_detected:
            if any(keyword in log_line_upper for keyword in keywords_indicating_problem):
                    warning_index = log_levels.index("WARNING")
                    if min_level_index <= warning_index: filtered_logs.append(entry)
    logging.info(f"Filtered {len(log_entries)} logs down to {len(filtered_logs)} relevant logs (Scan Level: {LOKI_SCAN_MIN_LEVEL}).")
    return filtered_logs

def determine_severity_from_rules(initial_reasons, log_batch):
    severity = "INFO"
    reasons_upper = initial_reasons.upper() if initial_reasons else ""

    if "OOMKILLED" in reasons_upper or "ERROR" in reasons_upper or "FAILED" in reasons_upper:
        severity = "CRITICAL"
    elif "CRASHLOOPBACKOFF" in reasons_upper:
         severity = "ERROR"
    elif "UNSCHEDULABLE" in reasons_upper or "IMAGEPULLBACKOFF" in reasons_upper or "BACKOFF" in reasons_upper or "WAITING" in reasons_upper:
        severity = "WARNING"

    if severity in ["INFO", "WARNING"]:
        critical_keywords = ["CRITICAL", "ALERT", "EMERGENCY", "PANIC", "FATAL"]
        error_keywords = ["ERROR", "FAIL", "EXCEPTION", "DENIED", "REFUSED", "UNAUTHORIZED"]
        warning_keywords = ["WARNING", "WARN", "TIMEOUT", "UNABLE", "SLOW"]

        for entry in log_batch[:20]:
            log_upper = entry['message'].upper()
            if any(kw in log_upper for kw in critical_keywords):
                severity = "CRITICAL"; break
            if any(kw in log_upper for kw in error_keywords):
                severity = "ERROR"; break
            if severity == "INFO" and any(kw in log_upper for kw in warning_keywords):
                 severity = "WARNING"

    logging.info(f"Rule-based severity determined: {severity}")
    return severity

def get_default_analysis(severity, initial_reasons):
     summary = f"Phát hiện sự cố tiềm ẩn. Lý do ban đầu: {initial_reasons or 'Không có'}. Mức độ ước tính: {severity}. (Phân tích AI bị tắt)."
     root_cause = "Không có phân tích AI."
     troubleshooting_steps = "Kiểm tra ngữ cảnh Kubernetes và log chi tiết thủ công."
     return {"severity": severity, "summary": summary, "root_cause": root_cause, "troubleshooting_steps": troubleshooting_steps}

def call_gemini_api(api_key, model_identifier, prompt):
    global gemini_model_instance
    try:
        needs_reinit = (
            not gemini_model_instance or
            genai.API_KEY != api_key or
            (hasattr(gemini_model_instance, 'model_name') and gemini_model_instance.model_name != model_identifier)
        )

        if needs_reinit:
            logging.info(f"Initializing/Re-initializing Gemini client for model {model_identifier}")
            if not api_key:
                raise ValueError("Cannot initialize Gemini client: API key is missing.")
            genai.configure(api_key=api_key)
            gemini_model_instance = genai.GenerativeModel(model_identifier)

        response = gemini_model_instance.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(temperature=0.2, max_output_tokens=500),
            request_options={'timeout': 90}
        )
        return response
    except Exception as e:
        logging.error(f"Error calling Gemini API: {e}", exc_info=True)
        gemini_model_instance = None
        raise

def call_local_api(endpoint_url, prompt):
     try:
        response = requests.post(endpoint_url, json={"prompt": prompt}, timeout=120)
        response.raise_for_status()
        try:
            parsed_json = response.json()
            return parsed_json, response.text
        except json.JSONDecodeError:
             logging.warning(f"Local API response from {endpoint_url} is not valid JSON. Returning raw text.")
             return None, response.text
     except requests.exceptions.RequestException as e:
          logging.error(f"Error calling local AI endpoint {endpoint_url}: {e}")
          raise

def analyze_incident(log_batch, k8s_context, initial_reasons):
    global model_calls_counter
    config = load_agent_config_from_db()

    if not config.get('enable_ai_analysis', False):
        logging.info("AI analysis is disabled by configuration.")
        severity = determine_severity_from_rules(initial_reasons, log_batch)
        analysis_result = get_default_analysis(severity, initial_reasons)
        return analysis_result, None, None

    # --- AI Analysis Enabled ---
    with db_lock: model_calls_counter += 1

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
        final_prompt = PROMPT_TEMPLATE.format(namespace=namespace, pod_name=pod_name, k8s_context=k8s_context[:10000], log_text=log_text[:20000])
    except KeyError as e:
        logging.error(f"Missing placeholder in PROMPT_TEMPLATE: {e}. Using default prompt structure.")
        final_prompt = f"Phân tích pod {namespace}/{pod_name}. Ngữ cảnh K8s: {k8s_context[:10000]}. Logs: {log_text[:20000]}. Chỉ trả lời bằng JSON với khóa 'severity', 'summary', 'root_cause', 'troubleshooting_steps'."

    provider = config.get('ai_provider', 'none')
    api_key = config.get('ai_api_key', '')
    model_id = config.get('ai_model_identifier', '')
    analysis_result = None
    raw_response_text = None
    analysis_failed = False

    try:
        if provider == "gemini":
            if not api_key:
                logging.warning("Gemini provider selected but API key is missing in config. Falling back.")
                analysis_failed = True
            elif not model_id:
                logging.warning("Gemini provider selected but model identifier is missing in config. Falling back.")
                analysis_failed = True
            else:
                logging.info(f"Calling Gemini API (Model: {model_id}) for pod {namespace}/{pod_name}")
                response = call_gemini_api(api_key, model_id, final_prompt)
                if not response.parts:
                    logging.warning("Gemini response has no parts. Raw response: %s", response)
                    summary = "Gemini không trả về nội dung."
                    try:
                        finish_reason = response.candidates[0].finish_reason if response.candidates else "UNKNOWN"
                        safety_ratings = response.candidates[0].safety_ratings if response.candidates else []
                        logging.warning(f"Gemini Finish Reason: {finish_reason}, Safety Ratings: {safety_ratings}")
                        if finish_reason.name == 'SAFETY': summary = "Phản hồi bị chặn bởi bộ lọc an toàn Gemini."
                        elif finish_reason.name == 'MAX_TOKENS': summary = "Phản hồi Gemini bị cắt do đạt giới hạn token."
                        else: summary = f"Gemini không trả về nội dung (Lý do: {finish_reason.name})."
                    except Exception as inner_e: logging.error(f"Error extracting finish reason: {inner_e}")
                    analysis_result = {"severity": "WARNING", "summary": summary}
                    analysis_failed = True
                else:
                    raw_response_text = response.text.strip()
                    cleaned_response_text = raw_response_text
                    if cleaned_response_text.startswith("```json"): cleaned_response_text = cleaned_response_text.strip("```json").strip("`").strip()
                    elif cleaned_response_text.startswith("```"): cleaned_response_text = cleaned_response_text.strip("```").strip()
                    match = re.search(r'\{.*\}', cleaned_response_text, re.DOTALL); json_string_to_parse = match.group(0) if match else cleaned_response_text
                    try:
                        analysis_result = json.loads(json_string_to_parse)
                        if not isinstance(analysis_result, dict): raise ValueError("Parsed response is not a dictionary.")
                    except (json.JSONDecodeError, ValueError) as json_err:
                        logging.warning(f"Failed to decode/validate Gemini JSON: {json_err}. Raw: {raw_response_text}")
                        analysis_result = None
                        analysis_failed = True

        elif provider == "local":
            if not LOCAL_GEMINI_ENDPOINT_URL: raise ValueError("Local provider selected but LOCAL_GEMINI_ENDPOINT is not configured.")
            logging.info(f"Calling local AI endpoint ({LOCAL_GEMINI_ENDPOINT_URL}) for pod {namespace}/{pod_name}")
            analysis_result, raw_response_text = call_local_api(LOCAL_GEMINI_ENDPOINT_URL, final_prompt)
            if analysis_result is None:
                 analysis_failed = True

        # elif provider == "openai": ...

        else:
            logging.warning(f"Unsupported AI provider '{provider}' configured or provider is 'none'. Falling back to rule-based analysis.")
            analysis_failed = True

    except Exception as e:
        logging.error(f"Error during AI analysis call (Provider: {provider}): {e}", exc_info=True)
        analysis_failed = True

    if analysis_failed:
        logging.warning(f"AI analysis failed or provider unsupported/misconfigured. Falling back to rule-based analysis for {namespace}/{pod_name}.")
        severity = determine_severity_from_rules(initial_reasons, log_batch)
        analysis_result = get_default_analysis(severity, initial_reasons)
        analysis_result["summary"] = f"Phân tích AI thất bại hoặc bị tắt. Lý do ban đầu: {initial_reasons or 'Không có'}. Mức độ ước tính: {severity}."
        if config.get('enable_ai_analysis', False):
             with db_lock:
                  if model_calls_counter > 0: model_calls_counter -= 1

    if not isinstance(analysis_result, dict):
         severity = determine_severity_from_rules(initial_reasons, log_batch)
         analysis_result = get_default_analysis(severity, initial_reasons)
    else:
         analysis_result.setdefault("severity", "WARNING")
         analysis_result.setdefault("summary", "N/A")
         analysis_result.setdefault("root_cause", "N/A")
         analysis_result.setdefault("troubleshooting_steps", "N/A")

    return analysis_result, final_prompt, raw_response_text

# *** MODIFIED send_telegram_alert ***
def send_telegram_alert(alert_data):
    global telegram_alerts_counter
    with db_lock: telegram_alerts_counter += 1

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram Bot Token or Chat ID not configured. Skipping alert.")
        return

    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Check current AI analysis status from cached config
    ai_enabled = current_agent_config.get('enable_ai_analysis', False)

    # Determine the summary to use
    summary = alert_data.get('summary', 'N/A')
    if not ai_enabled:
        # Use a simpler summary if AI is disabled
        severity = alert_data.get('severity', 'UNKNOWN')
        initial_reasons = alert_data.get('initial_reasons', 'Không có')
        summary = f"Phát hiện sự cố {severity}. Lý do: {initial_reasons}. (AI tắt)"


    # Base message parts
    message_lines = [
        f"🚨 *Cảnh báo K8s/Log (Pod: {alert_data.get('pod_key', 'N/A')})* 🚨",
        f"*Mức độ:* `{alert_data.get('severity', 'UNKNOWN')}`",
        f"*Tóm tắt:* {summary}" # Use the determined summary
    ]

    # Conditionally add AI-specific sections ONLY if AI is enabled
    if ai_enabled:
        root_cause = alert_data.get('root_cause', '')
        steps = alert_data.get('troubleshooting_steps', '')

        default_root_cause = "Không có phân tích AI."
        default_steps = "Kiểm tra ngữ cảnh Kubernetes và log chi tiết thủ công."

        if root_cause and root_cause != "N/A" and root_cause != default_root_cause:
             message_lines.append(f"*Nguyên nhân gốc có thể:*\n{root_cause}")
        if steps and steps != "N/A" and steps != default_steps:
             message_lines.append(f"*Đề xuất khắc phục:*\n{steps}")

    # Add remaining common parts
    message_lines.extend([
        f"*Lý do phát hiện ban đầu:* {alert_data.get('initial_reasons', 'N/A')}",
        f"*Thời gian phát hiện:* `{alert_data.get('alert_time', 'N/A')}`",
        f"*Log mẫu (nếu có):*\n{alert_data.get('sample_logs', '-')}",
        "\n_Vui lòng kiểm tra chi tiết trên dashboard hoặc Loki/Kubernetes._"
    ])

    message = "\n".join(message_lines)

    # Truncate message if it exceeds Telegram limit
    max_len = 4096
    truncated_message = message
    if len(message) > max_len:
        truncated_message = message[:max_len - 50] + "\n\n_[... message truncated ...]_"

    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': truncated_message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(telegram_api_url, json=payload, timeout=10)
        response.raise_for_status()
        logging.info(f"Sent alert to Telegram. Response: {response.json().get('ok', False)}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending Telegram alert: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during Telegram send: {e}", exc_info=True)


def refresh_namespaces_if_needed(last_refresh_time):
    current_time_secs = time.time()
    if current_time_secs - last_refresh_time >= NAMESPACE_REFRESH_INTERVAL_SECONDS:
        logging.info("Refreshing list of active namespaces from Kubernetes API...")
        all_active_namespaces = get_active_namespaces()
        update_available_namespaces_in_db(all_active_namespaces)
        return current_time_secs
    return last_refresh_time

def identify_pods_to_investigate(monitored_namespaces, start_cycle_time):
    k8s_problem_pods = scan_kubernetes_for_issues(monitored_namespaces)
    loki_scan_end_time = start_cycle_time; loki_scan_start_time = loki_scan_end_time - timedelta(minutes=LOKI_SCAN_RANGE_MINUTES)
    loki_suspicious_logs = scan_loki_for_suspicious_logs(loki_scan_start_time, loki_scan_end_time, monitored_namespaces)
    pods_to_investigate = {}
    for pod_key, data in k8s_problem_pods.items():
        if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
        pods_to_investigate[pod_key]["reason"].append(data["reason"])
    for pod_key, logs in loki_suspicious_logs.items():
            if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
            reason_text = f"Loki: Phát hiện {len(logs)} log đáng ngờ (>= {LOKI_SCAN_MIN_LEVEL})"
            if reason_text not in pods_to_investigate[pod_key]["reason"]:
                 pods_to_investigate[pod_key]["reason"].append(reason_text)
            pods_to_investigate[pod_key]["logs"].extend(logs)
    logging.info(f"Total pods to investigate this cycle: {len(pods_to_investigate)}")
    return pods_to_investigate

def investigate_pod_details(namespace, pod_name, initial_reasons, suspicious_logs_found_in_scan):
    logging.info(f"Investigating pod: {namespace}/{pod_name} (Initial Reasons: {initial_reasons})")
    pod_info = get_pod_info(namespace, pod_name); node_info = None; pod_events = []
    if pod_info: node_info = get_node_info(pod_info.get('node_name')); pod_events = get_pod_events(namespace, pod_name, since_minutes=LOKI_DETAIL_LOG_RANGE_MINUTES + 5)
    k8s_context_str = format_k8s_context(pod_info, node_info, pod_events)
    logs_for_analysis = suspicious_logs_found_in_scan
    if not logs_for_analysis:
        logging.info(f"No logs found in initial scan for {namespace}/{pod_name}. Querying Loki for detailed logs...")
        log_end_time = datetime.now(timezone.utc); log_start_time = log_end_time - timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
        detailed_logs = query_loki_for_pod(namespace, pod_name, log_start_time, log_end_time)
        logs_for_analysis = preprocess_and_filter(detailed_logs)
    return logs_for_analysis, k8s_context_str

def process_analysis_and_alert(pod_key, initial_reasons, logs_for_analysis, k8s_context_str):
    namespace, pod_name = pod_key.split('/', 1);
    analysis_start_time = time.time()
    analysis_result, final_prompt, raw_response_text = analyze_incident(logs_for_analysis, k8s_context_str, initial_reasons)
    analysis_duration = time.time() - analysis_start_time
    logging.info(f"Analysis for {pod_key} took {analysis_duration:.2f} seconds.")

    severity = analysis_result.get("severity", "UNKNOWN").upper();
    summary = analysis_result.get("summary", "N/A")
    root_cause_str = analysis_result.get("root_cause", "N/A")
    steps_str = analysis_result.get("troubleshooting_steps", "N/A")

    logging.info(f"Analysis result for '{pod_key}': Severity={severity}, Summary={summary}, RootCause={root_cause_str[:100]}..., Steps={steps_str[:100]}...")

    sample_logs_str = "\n".join([f"- `{log['message'][:150]}`" for log in logs_for_analysis[:5]]) if logs_for_analysis else "-"

    record_incident( pod_key, severity, summary, initial_reasons, k8s_context_str, sample_logs_str, final_prompt, raw_response_text, root_cause_str, steps_str )

    if severity in ALERT_SEVERITY_LEVELS:
        alert_time_hcm = datetime.now(HCM_TZ); time_format = '%Y-%m-%d %H:%M:%S %Z'
        alert_data = {
            'pod_key': pod_key, 'severity': severity, 'summary': summary, 'root_cause': root_cause_str,
            'troubleshooting_steps': steps_str, 'initial_reasons': initial_reasons,
            'alert_time': alert_time_hcm.strftime(time_format),
            'sample_logs': sample_logs_str if sample_logs_str != "-" else "- Không có log mẫu liên quan."
        }
        send_telegram_alert(alert_data) # Call the modified function
        set_pod_cooldown(pod_key)


def perform_monitoring_cycle(last_namespace_refresh_time):
    start_cycle_time = datetime.now(timezone.utc)
    logging.info("--- Starting new monitoring cycle ---")

    load_agent_config_from_db() # Refresh config at start of cycle

    last_namespace_refresh_time = refresh_namespaces_if_needed(last_namespace_refresh_time)

    monitored_namespaces = get_monitored_namespaces()
    if not monitored_namespaces:
        logging.warning("No namespaces configured for monitoring. Skipping cycle.")
        cycle_duration = (datetime.now(timezone.utc) - start_cycle_time).total_seconds()
        sleep_time = max(0, SCAN_INTERVAL_SECONDS - cycle_duration)
        logging.info(f"--- Cycle finished early (no namespaces). Sleeping for {sleep_time:.2f} seconds... ---")
        time.sleep(sleep_time)
        return last_namespace_refresh_time

    logging.info(f"Currently monitoring {len(monitored_namespaces)} namespaces: {', '.join(monitored_namespaces)}")

    pods_to_investigate = identify_pods_to_investigate(monitored_namespaces, start_cycle_time)

    for pod_key, data in pods_to_investigate.items():
        if is_pod_in_cooldown(pod_key):
            continue

        namespace, pod_name = pod_key.split('/', 1);
        initial_reasons = "; ".join(data["reason"]);
        suspicious_logs_found_in_scan = data["logs"]

        try:
            logs_for_analysis, k8s_context_str = investigate_pod_details( namespace, pod_name, initial_reasons, suspicious_logs_found_in_scan )
            process_analysis_and_alert( pod_key, initial_reasons, logs_for_analysis, k8s_context_str )
        except Exception as investigation_err:
            logging.error(f"Error during investigation/analysis for {pod_key}: {investigation_err}", exc_info=True)

        time.sleep(1)

    cycle_duration = (datetime.now(timezone.utc) - start_cycle_time).total_seconds()
    sleep_time = max(0, SCAN_INTERVAL_SECONDS - cycle_duration)
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
             logging.info(f"Sleeping for {SCAN_INTERVAL_SECONDS} seconds before next cycle after error.")
             time.sleep(SCAN_INTERVAL_SECONDS)
             last_namespace_refresh_time = 0


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
    logging.info(f"Loki URL: {LOKI_URL}")
    logging.info(f"Loki scan minimum level: {LOKI_SCAN_MIN_LEVEL}")
    logging.info(f"Alerting for severity levels: {ALERT_SEVERITY_LEVELS_STR}")
    logging.info(f"Restart count threshold: {RESTART_COUNT_THRESHOLD}")
    logging.info(f"Alert cooldown: {ALERT_COOLDOWN_MINUTES} minutes")
    logging.info(f"Config refresh interval: {CONFIG_REFRESH_INTERVAL_SECONDS} seconds")

    initial_config = current_agent_config
    logging.info(f"Initial AI Config: Enabled={initial_config.get('enable_ai_analysis')}, Provider={initial_config.get('ai_provider')}, Model={initial_config.get('ai_model_identifier')}")

    required_telegram_vars = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing_telegram_vars = [var for var in required_telegram_vars if not globals().get(var)]
    if missing_telegram_vars:
            logging.error(f"Missing required environment variables for Telegram: {', '.join(missing_telegram_vars)}. Ensure they are set. Exiting.")
            exit(1)

    # Log warnings for potential AI misconfigurations, but don't exit
    if initial_config.get('enable_ai_analysis'):
         provider = initial_config.get('ai_provider')
         api_key = initial_config.get('ai_api_key')
         model_id = initial_config.get('ai_model_identifier')
         if provider == 'local' and not LOCAL_GEMINI_ENDPOINT_URL:
              logging.warning("AI analysis enabled with 'local' provider, but LOCAL_GEMINI_ENDPOINT env var is not set.")
         elif provider not in ['local', 'none'] and not api_key:
              logging.warning(f"AI analysis enabled with '{provider}' provider, but 'ai_api_key' is missing in the database configuration.")
         elif provider not in ['local', 'none'] and not model_id:
              logging.warning(f"AI analysis enabled with '{provider}' provider, but 'ai_model_identifier' is missing in the database configuration.")

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

