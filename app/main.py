import os
import time
import requests
import google.generativeai as genai
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
    logging.error("zoneinfo module not found. Please use Python 3.9+ or install pytz and uncomment the fallback.")
    exit(1)
import sqlite3
import threading

# --- Tải biến môi trường ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Cấu hình ---
LOKI_URL = os.environ.get("LOKI_URL", "http://loki-read.monitoring.svc.cluster.local:3100")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", 30))
LOKI_SCAN_RANGE_MINUTES = int(os.environ.get("LOKI_SCAN_RANGE_MINUTES", 1))
LOKI_DETAIL_LOG_RANGE_MINUTES = int(os.environ.get("LOKI_DETAIL_LOG_RANGE_MINUTES", 30))
LOKI_QUERY_LIMIT = int(os.environ.get("LOKI_QUERY_LIMIT", 500))
EXCLUDED_NAMESPACES_STR = os.environ.get("EXCLUDED_NAMESPACES", "kube-node-lease,kube-public")
EXCLUDED_NAMESPACES = {ns.strip() for ns in EXCLUDED_NAMESPACES_STR.split(',') if ns.strip()}
LOKI_SCAN_MIN_LEVEL = os.environ.get("LOKI_SCAN_MIN_LEVEL", "WARNING")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-1.5-flash")
ALERT_SEVERITY_LEVELS_STR = os.environ.get("ALERT_SEVERITY_LEVELS", "WARNING,ERROR,CRITICAL")
ALERT_SEVERITY_LEVELS = [level.strip().upper() for level in ALERT_SEVERITY_LEVELS_STR.split(',') if level.strip()]
RESTART_COUNT_THRESHOLD = int(os.environ.get("RESTART_COUNT_THRESHOLD", 5))
DB_PATH = os.environ.get("DB_PATH", "/data/agent_stats.db")
STATS_UPDATE_INTERVAL_SECONDS = int(os.environ.get("STATS_UPDATE_INTERVAL_SECONDS", 300))
NAMESPACE_REFRESH_INTERVAL_SECONDS = int(os.environ.get("NAMESPACE_REFRESH_INTERVAL_SECONDS", 3600))
DEFAULT_MONITORED_NAMESPACES = os.environ.get("DEFAULT_MONITORED_NAMESPACES", "kube-system,default")
LOCAL_GEMINI_ENDPOINT_URL = os.environ.get("LOCAL_GEMINI_ENDPOINT")
USE_LOCAL_MODEL_STR = os.environ.get("USE_LOCAL_MODEL", "false").lower()
USE_LOCAL_MODEL = USE_LOCAL_MODEL_STR == "true"

# v1.0.6: Updated Prompt Template to ask for root cause and troubleshooting steps
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
  "root_cause": "- Giới hạn bộ nhớ (memory limit) quá thấp.\n- Ứng dụng bị rò rỉ bộ nhớ (memory leak).",
  "troubleshooting_steps": "- Tăng memory limit cho pod.\n- Phân tích memory profile của ứng dụng.\n- Kiểm tra lại logic cấp phát/giải phóng bộ nhớ trong code."
}}
""")

# v1.0.6: Added Telegram Alert Template from environment variable
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


try: HCM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception as e: logging.error(f"Could not load timezone 'Asia/Ho_Chi_Minh': {e}."); HCM_TZ = timezone.utc

# --- Cấu hình Kubernetes Client ---
try: config.load_incluster_config(); logging.info("Loaded in-cluster Kubernetes config.")
except config.ConfigException:
    try: config.load_kube_config(); logging.info("Loaded local Kubernetes config (kubeconfig).")
    except config.ConfigException: logging.error("Could not configure Kubernetes client. Exiting."); exit(1)
k8s_core_v1 = client.CoreV1Api(); k8s_apps_v1 = client.AppsV1Api()

# --- Cấu hình Gemini Client ---
gemini_model = None
if not USE_LOCAL_MODEL:
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)
            logging.info(f"Using Google Gemini model: {GEMINI_MODEL_NAME}")
        except Exception as e: logging.error(f"Failed to configure Gemini client: {e}", exc_info=True)
    else: logging.error("USE_LOCAL_MODEL is false, but GEMINI_API_KEY is not set. Analysis will be skipped.")
elif not LOCAL_GEMINI_ENDPOINT_URL:
        logging.error("USE_LOCAL_MODEL is true, but LOCAL_GEMINI_ENDPOINT is not set. Analysis will be skipped.")


# --- Logic Database ---
model_calls_counter = 0; telegram_alerts_counter = 0; db_lock = threading.Lock()

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

def init_db():
    """Initializes the database and ensures all necessary tables exist."""
    db_dir = os.path.dirname(DB_PATH);
    if not os.path.exists(db_dir):
        try: os.makedirs(db_dir); logging.info(f"Created directory for database: {db_dir}")
        except OSError as e: logging.error(f"Could not create directory {db_dir}: {e}"); return False

    conn = get_db_connection()
    if conn is None:
        logging.error("Failed to get DB connection during initialization.")
        return False

    try:
        with conn:
            cursor = conn.cursor()
            logging.info("Ensuring database tables exist...")
            # v1.0.6: Added root_cause and troubleshooting_steps columns
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
                    root_cause TEXT,          -- Added in v1.0.6
                    troubleshooting_steps TEXT -- Added in v1.0.6
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    model_calls INTEGER DEFAULT 0,
                    telegram_alerts INTEGER DEFAULT 0,
                    incident_count INTEGER DEFAULT 0
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS available_namespaces (
                    name TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')

            # --- Add new columns if they don't exist (for upgrades) ---
            cursor.execute("PRAGMA table_info(incidents)")
            columns_inc = [column[1] for column in cursor.fetchall()]
            if 'input_prompt' not in columns_inc:
                logging.info("Adding 'input_prompt' column to incidents table.")
                cursor.execute("ALTER TABLE incidents ADD COLUMN input_prompt TEXT")
            if 'raw_ai_response' not in columns_inc:
                logging.info("Adding 'raw_ai_response' column to incidents table.")
                cursor.execute("ALTER TABLE incidents ADD COLUMN raw_ai_response TEXT")
            # v1.0.6: Add new columns
            if 'root_cause' not in columns_inc:
                logging.info("Adding 'root_cause' column to incidents table.")
                cursor.execute("ALTER TABLE incidents ADD COLUMN root_cause TEXT")
            if 'troubleshooting_steps' not in columns_inc:
                logging.info("Adding 'troubleshooting_steps' column to incidents table.")
                cursor.execute("ALTER TABLE incidents ADD COLUMN troubleshooting_steps TEXT")
            # --- End Add new columns ---

            cursor.execute("PRAGMA table_info(daily_stats)")
            columns_stats = [column[1] for column in cursor.fetchall()]
            if 'model_calls' not in columns_stats and 'gemini_calls' in columns_stats:
                logging.info("Renaming 'gemini_calls' to 'model_calls' in daily_stats table.")
                cursor.execute("ALTER TABLE daily_stats RENAME COLUMN gemini_calls TO model_calls")
            elif 'model_calls' not in columns_stats:
                logging.info("Adding 'model_calls' column to daily_stats table.")
                cursor.execute("ALTER TABLE daily_stats ADD COLUMN model_calls INTEGER DEFAULT 0")

            logging.info("Tables ensured.")
        logging.info(f"Database initialization/check complete at {DB_PATH}")
        return True
    except sqlite3.Error as e: logging.error(f"Database error during initialization: {e}", exc_info=True); return False
    except Exception as e: logging.error(f"Unexpected error during DB initialization: {e}", exc_info=True); return False
    finally:
        if conn: conn.close()

# v1.0.6: Added root_cause and troubleshooting_steps parameters
def record_incident(pod_key, severity, summary, initial_reasons, k8s_context, sample_logs,
                    input_prompt=None, raw_ai_response=None, root_cause=None, troubleshooting_steps=None):
    """Records an incident into the database, including new fields."""
    timestamp_str = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    if conn is None: logging.error(f"Failed to get DB connection for recording incident {pod_key}"); return
    try:
        with conn:
            cursor = conn.cursor()
            # v1.0.6: Updated INSERT statement
            cursor.execute('''
                INSERT INTO incidents (
                    timestamp, pod_key, severity, summary, initial_reasons,
                    k8s_context, sample_logs, input_prompt, raw_ai_response,
                    root_cause, troubleshooting_steps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (timestamp_str, pod_key, severity, summary, initial_reasons,
                  k8s_context, sample_logs, input_prompt, raw_ai_response,
                  root_cause, troubleshooting_steps)) # Pass new values

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
    """Updates the daily statistics (model calls, alerts) in the database."""
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
    """Periodically calls update_daily_stats."""
    while True:
        time.sleep(STATS_UPDATE_INTERVAL_SECONDS)
        update_daily_stats()

# --- Các hàm lấy thông tin Kubernetes ---
def get_pod_info(namespace, pod_name):
    """Fetches detailed information about a specific pod."""
    try:
        pod = k8s_core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        info = {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "status": pod.status.phase,
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
                info["container_statuses"][cs.name] = {
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "state": state_info
                }
        return info
    except ApiException as e:
        logging.warning(f"Could not get pod info for {namespace}/{pod_name}: {e.status} {e.reason}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error getting pod info for {namespace}/{pod_name}: {e}", exc_info=True)
        return None

def get_node_info(node_name):
    """Fetches information about a specific node."""
    if not node_name: return None
    try:
        node = k8s_core_v1.read_node(name=node_name)
        conditions = {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message} for cond in node.status.conditions} if node.status.conditions else {}
        info = {
            "name": node.metadata.name,
            "conditions": conditions,
            "allocatable_cpu": node.status.allocatable.get('cpu', 'N/A'),
            "allocatable_memory": node.status.allocatable.get('memory', 'N/A'),
            "kubelet_version": node.status.node_info.kubelet_version
        }
        return info
    except ApiException as e:
        logging.warning(f"Could not get node info for {node_name}: {e.status} {e.reason}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error getting node info for {node_name}: {e}", exc_info=True)
        return None

def get_pod_events(namespace, pod_name, since_minutes=15):
    """Fetches recent events related to a specific pod."""
    try:
        since_time = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        field_selector = f"involvedObject.kind=Pod,involvedObject.name={pod_name},involvedObject.namespace={namespace}"
        events = k8s_core_v1.list_namespaced_event(namespace=namespace, field_selector=field_selector, limit=20)
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
                            "time": event_time.isoformat(),
                            "type": event.type,
                            "reason": event.reason,
                            "message": event.message,
                            "count": event.count
                        })
                    if len(recent_events) >= 10 or (event_time and event_time < since_time):
                        break
        return recent_events
    except ApiException as e:
        if e.status != 403:
            logging.warning(f"Could not list events for pod {namespace}/{pod_name}: {e.status} {e.reason}")
        return []
    except Exception as e:
        logging.error(f"Unexpected error listing events for pod {namespace}/{pod_name}: {e}", exc_info=True)
        return []

def format_k8s_context(pod_info, node_info, pod_events):
    """Formats Kubernetes context information into a readable string."""
    context_str = "\n--- Ngữ cảnh Kubernetes ---\n"
    if pod_info:
        context_str += f"Pod: {pod_info['namespace']}/{pod_info['name']}\n"
        context_str += f"  Trạng thái: {pod_info['status']}\n"
        context_str += f"  Node: {pod_info['node_name']}\n"
        context_str += f"  Số lần khởi động lại: {pod_info['restarts']}\n"
        if pod_info.get('container_statuses'):
                context_str += "  Trạng thái Container:\n"
                for name, status in pod_info['container_statuses'].items():
                    context_str += f"    - {name}: {status['state']} (Ready: {status['ready']}, Restarts: {status['restart_count']})\n"
        if pod_info.get('conditions'):
                problematic_conditions = [
                    f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})"
                    for ctype, cinfo in pod_info['conditions'].items()
                    if cinfo.get('status') != 'True'
                ]
                if problematic_conditions:
                    context_str += f"  Điều kiện Pod bất thường: {', '.join(problematic_conditions)}\n"
    if node_info:
        context_str += f"Node: {node_info['name']}\n"
        if node_info.get('conditions'):
                problematic_conditions = [
                    f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})"
                    for ctype, cinfo in node_info['conditions'].items()
                    if (ctype == 'Ready' and cinfo.get('status') != 'True') or \
                       (ctype != 'Ready' and cinfo.get('status') != 'False')
                ]
                if problematic_conditions:
                    context_str += f"  Điều kiện Node bất thường: {', '.join(problematic_conditions)}\n"
    if pod_events:
        context_str += "Sự kiện Pod gần đây (tối đa 10):\n"
        for event in pod_events:
            message_preview = event['message'][:150] + ('...' if len(event['message']) > 150 else '')
            context_str += f"  - [{event['time']}] {event['type']} {event['reason']} (x{event.get('count',1)}): {message_preview}\n"
    context_str += "--- Kết thúc ngữ cảnh ---\n"
    return context_str

# --- Hàm Lấy danh sách namespace động từ K8s ---
def get_active_namespaces():
    """Fetches the list of active, non-excluded namespaces from the K8s API."""
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

# --- HÀM MỚI: Cập nhật danh sách namespace trong DB ---
def update_available_namespaces_in_db(namespaces):
    """Updates the available_namespaces table in the database."""
    if not namespaces: return
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    if conn is None: logging.error("Failed to get DB connection for updating available namespaces"); return
    try:
        with conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(namespaces))
            cursor.execute(f"DELETE FROM available_namespaces WHERE name NOT IN ({placeholders})", tuple(namespaces))
            for ns in namespaces:
                cursor.execute("INSERT OR REPLACE INTO available_namespaces (name, last_seen) VALUES (?, ?)", (ns, timestamp))
            logging.info(f"Updated available_namespaces table in DB with {len(namespaces)} namespaces.")
    except sqlite3.Error as e: logging.error(f"Database error updating available namespaces: {e}")
    except Exception as e: logging.error(f"Unexpected error updating available namespaces: {e}", exc_info=True)
    finally:
        if conn: conn.close()

# --- HÀM MỚI: Đọc danh sách namespace cần giám sát từ DB ---
def get_monitored_namespaces_from_db():
    """Reads the list of namespaces to monitor from the agent_config table."""
    default_namespaces = [ns.strip() for ns in DEFAULT_MONITORED_NAMESPACES.split(',') if ns.strip()]
    conn = get_db_connection()
    if conn is None:
        logging.warning("DB connection failed when reading monitored namespaces, using default.")
        return default_namespaces

    monitored = []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM agent_config WHERE key = 'monitored_namespaces'")
        result = cursor.fetchone()
        conn.close()

        if result and result['value']:
            try:
                loaded_value = json.loads(result['value'])
                if isinstance(loaded_value, list):
                    monitored = loaded_value
                    logging.info(f"Read monitored namespaces from DB (JSON): {monitored}")
                else:
                    logging.warning("Value for monitored_namespaces in DB is not a list (expected JSON list). Using default.")
                    monitored = default_namespaces
            except json.JSONDecodeError:
                monitored = [ns.strip() for ns in result['value'].split(',') if ns.strip()]
                logging.info(f"Read monitored namespaces from DB (CSV fallback): {monitored}")
        else:
            logging.warning("No monitored_namespaces config found in DB. Using default.")
            monitored = default_namespaces

    except sqlite3.Error as e:
        logging.error(f"Database error reading monitored_namespaces: {e}")
        monitored = default_namespaces
    except Exception as e:
        logging.error(f"Unexpected error reading monitored_namespaces: {e}", exc_info=True)
        monitored = default_namespaces

    return monitored if isinstance(monitored, list) else default_namespaces


# --- Hàm Quét Loki ---
def scan_loki_for_suspicious_logs(start_time, end_time, namespaces_to_scan):
    """Scans Loki for logs matching minimum level or specific keywords within given namespaces."""
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range";
    if not namespaces_to_scan:
        logging.info("No namespaces to scan in Loki.")
        return {}

    log_levels_all = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"];
    scan_level_index = -1
    try: scan_level_index = log_levels_all.index(LOKI_SCAN_MIN_LEVEL.upper())
    except ValueError:
        logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {LOKI_SCAN_MIN_LEVEL}. Defaulting to WARNING.")
        scan_level_index = log_levels_all.index("WARNING")
    levels_to_scan = log_levels_all[scan_level_index:]

    # Build LogQL query
    namespace_regex = "|".join(namespaces_to_scan) # No escaping needed here for =~
    keywords_to_find = levels_to_scan + ["fail", "crash", "exception", "panic", "fatal", "timeout", "denied", "refused", "unable", "unauthorized"]
    escaped_keywords = [re.escape(k) for k in keywords_to_find]; # Escape keywords for regex
    regex_pattern = "(?i)(" + "|".join(escaped_keywords) + ")"
    logql_query = f'{{namespace=~"{namespace_regex}"}} |~ `{regex_pattern}`'
    query_limit_scan = 2000

    params = {
        'query': logql_query,
        'start': int(start_time.timestamp() * 1e9),
        'end': int(end_time.timestamp() * 1e9),
        'limit': query_limit_scan,
        'direction': 'forward'
    }

    logging.info(f"Scanning Loki for suspicious logs (Level >= {LOKI_SCAN_MIN_LEVEL} or keywords) in {len(namespaces_to_scan)} namespaces: {logql_query[:200]}...")
    suspicious_logs_by_pod = {}
    try:
        headers = {'Accept': 'application/json'};
        response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=60);
        response.raise_for_status();
        data = response.json()

        if 'data' in data and 'result' in data['data']:
            count = 0
            for stream in data['data']['result']:
                labels = stream.get('stream', {});
                ns = labels.get('namespace');
                pod_name = labels.get('pod')
                if not ns or not pod_name: continue

                pod_key = f"{ns}/{pod_name}"
                if pod_key not in suspicious_logs_by_pod:
                    suspicious_logs_by_pod[pod_key] = []

                for timestamp_ns, log_line in stream['values']:
                    log_entry = {
                        "timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc),
                        "message": log_line.strip(),
                        "labels": labels
                    }
                    suspicious_logs_by_pod[pod_key].append(log_entry);
                    count += 1
            logging.info(f"Loki scan found {count} suspicious log entries across {len(suspicious_logs_by_pod)} pods.")
        else:
            logging.info("Loki scan found no suspicious log entries matching the criteria.")
        return suspicious_logs_by_pod

    except requests.exceptions.HTTPError as e:
        error_detail = ""
        try: error_detail = e.response.text[:500]
        except: pass
        logging.error(f"Error scanning Loki (HTTP {e.response.status_code}): {e}. Response: {error_detail}")
        return {}
    except requests.exceptions.RequestException as e:
        logging.error(f"Error scanning Loki (Request failed): {e}")
        return {}
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding Loki scan response (Invalid JSON): {e}")
        return {}
    except Exception as e:
        logging.error(f"Unexpected error during Loki scan: {e}", exc_info=True)
        return {}

# --- Hàm Quét K8s ---
def scan_kubernetes_for_issues(namespaces_to_scan):
    """Scans specified Kubernetes namespaces for pods with potential issues."""
    problematic_pods = {}
    logging.info(f"Scanning {len(namespaces_to_scan)} Kubernetes namespaces for problematic pods...")
    for ns in namespaces_to_scan:
        try:
            pods = k8s_core_v1.list_namespaced_pod(namespace=ns, watch=False, timeout_seconds=60)
            for pod in pods.items:
                pod_key = f"{ns}/{pod.metadata.name}";
                issue_found = False;
                reason = ""

                if pod.status.phase in ["Failed", "Unknown"]:
                    issue_found = True;
                    reason = f"Trạng thái Pod là {pod.status.phase}"
                elif pod.status.phase == "Pending" and pod.status.conditions:
                        scheduled_condition = next((c for c in pod.status.conditions if c.type == "PodScheduled"), None)
                        if scheduled_condition and scheduled_condition.status == "False" and scheduled_condition.reason == "Unschedulable":
                            issue_found = True;
                            reason = f"Pod không thể lên lịch (Unschedulable)"

                if not issue_found and pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        if cs.restart_count >= RESTART_COUNT_THRESHOLD:
                            issue_found = True;
                            reason = f"Container '{cs.name}' restart {cs.restart_count} lần (>= ngưỡng {RESTART_COUNT_THRESHOLD})"
                            break
                        if cs.state and cs.state.waiting and cs.state.waiting.reason in ["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "CreateContainerError"]:
                            issue_found = True;
                            reason = f"Container '{cs.name}' đang ở trạng thái Waiting với lý do '{cs.state.waiting.reason}'"
                            break
                        if cs.state and cs.state.terminated and cs.state.terminated.reason in ["OOMKilled", "Error", "ContainerCannotRun"]:
                            is_recent_termination = cs.state.terminated.finished_at and \
                                                    (datetime.now(timezone.utc) - cs.state.terminated.finished_at) < timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
                            if pod.spec.restart_policy != "Always" or is_recent_termination:
                                issue_found = True;
                                reason = f"Container '{cs.name}' bị Terminated với lý do '{cs.state.terminated.reason}'"
                                break

                if issue_found:
                    logging.warning(f"Phát hiện pod có vấn đề tiềm ẩn (K8s Scan): {pod_key}. Lý do: {reason}")
                    if pod_key not in problematic_pods:
                        problematic_pods[pod_key] = {
                            "namespace": ns,
                            "pod_name": pod.metadata.name,
                            "reason": f"K8s: {reason}"
                        }
        except ApiException as e:
            logging.error(f"API Error scanning namespace {ns}: {e.status} {e.reason}")
        except Exception as e:
            logging.error(f"Unexpected error scanning namespace {ns}: {e}", exc_info=True)

    logging.info(f"Finished K8s scan. Found {len(problematic_pods)} potentially problematic pods from K8s state.")
    return problematic_pods

# --- Hàm Query Loki cho pod ---
def query_loki_for_pod(namespace, pod_name, start_time, end_time):
    """Queries Loki for all logs of a specific pod within a time range."""
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range";
    logql_query = f'{{namespace="{namespace}", pod="{pod_name}"}}'
    params = {
        'query': logql_query,
        'start': int(start_time.timestamp() * 1e9),
        'end': int(end_time.timestamp() * 1e9),
        'limit': LOKI_QUERY_LIMIT,
        'direction': 'forward'
    }
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
                for timestamp_ns, log_line in stream['values']:
                    log_entries.append({
                        "timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc),
                        "message": log_line.strip(),
                        "labels": stream_labels
                    })
            log_entries.sort(key=lambda x: x['timestamp']);
            logging.info(f"Received {len(log_entries)} log entries from Loki for pod '{namespace}/{pod_name}'.");
            return log_entries
        else:
            logging.warning(f"No 'result' data found in Loki response for pod '{namespace}/{pod_name}'.");
            return []
    except requests.exceptions.RequestException as e:
        logging.error(f"Error querying Loki for pod '{namespace}/{pod_name}': {e}");
        return []
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding Loki JSON response for pod '{namespace}/{pod_name}': {e}");
        return []
    except Exception as e:
        logging.error(f"Unexpected error querying Loki for pod '{namespace}/{pod_name}': {e}", exc_info=True);
        return []

# --- Hàm lọc log ---
def preprocess_and_filter(log_entries):
    """Filters log entries based on LOKI_SCAN_MIN_LEVEL and problem keywords."""
    filtered_logs = [];
    log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"];
    min_level_index = -1
    try: min_level_index = log_levels.index(LOKI_SCAN_MIN_LEVEL.upper())
    except ValueError:
        logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {LOKI_SCAN_MIN_LEVEL}. Defaulting to WARNING.");
        min_level_index = log_levels.index("WARNING")

    keywords_indicating_problem = ["FAIL", "ERROR", "CRASH", "EXCEPTION", "UNAVAILABLE", "FATAL", "PANIC", "TIMEOUT", "DENIED", "REFUSED", "UNABLE", "UNAUTHORIZED"]

    for entry in log_entries:
        log_line = entry['message'];
        log_line_upper = log_line.upper();
        level_detected = False

        for i, level in enumerate(log_levels):
            if f" {level} " in f" {log_line_upper} " or \
               log_line_upper.startswith(level+":") or \
               f"[{level}]" in log_line_upper or \
               f"level={level.lower()}" in log_line_upper or \
               f"\"level\":\"{level.lower()}\"" in log_line_upper:
                if i >= min_level_index:
                    filtered_logs.append(entry);
                    level_detected = True;
                    break

        if not level_detected:
            if any(keyword in log_line_upper for keyword in keywords_indicating_problem):
                    warning_index = log_levels.index("WARNING")
                    if min_level_index <= warning_index:
                        filtered_logs.append(entry)

    logging.info(f"Filtered {len(log_entries)} logs down to {len(filtered_logs)} relevant logs (Scan Level: {LOKI_SCAN_MIN_LEVEL}).")
    return filtered_logs


# --- Hàm phân tích ---
# v1.0.6: Updated to parse and return root_cause and troubleshooting_steps
def analyze_incident(log_batch, k8s_context=""):
    """Analyzes logs and context using AI, returns analysis, prompt, and raw response."""
    global model_calls_counter;
    with db_lock:
        model_calls_counter += 1

    if not log_batch and not k8s_context:
        logging.warning("analyze_incident called with no logs and no context. Skipping analysis.")
        # Return None for all expected values
        return None, None, None, None, None # result, prompt, raw_response, root_cause, steps

    namespace = "unknown"; pod_name = "unknown_pod"
    if log_batch and log_batch[0].get('labels'):
        labels = log_batch[0].get('labels', {});
        namespace = labels.get('namespace', namespace);
        pod_name = labels.get('pod', pod_name)
    elif k8s_context:
        match_ns = re.search(r"Pod:\s*([\w.-]+)/", k8s_context);
        match_pod = re.search(r"Pod:\s*[\w.-]+/([\w.-]+)\n", k8s_context);
        if match_ns: namespace = match_ns.group(1);
        if match_pod: pod_name = match_pod.group(1)

    log_text = "N/A"
    if log_batch:
        limited_logs = [f"[{entry['timestamp'].isoformat()}] {entry['message'][:500]}" for entry in log_batch[:15]];
        log_text = "\n".join(limited_logs)

    final_prompt = None
    try:
        final_prompt = PROMPT_TEMPLATE.format(
            namespace=namespace,
            pod_name=pod_name,
            k8s_context=k8s_context[:10000],
            log_text=log_text[:20000]
        )
    except KeyError as e:
        logging.error(f"Missing placeholder in PROMPT_TEMPLATE: {e}. Using default prompt structure.")
        final_prompt = f"Phân tích pod {namespace}/{pod_name}. Ngữ cảnh K8s: {k8s_context[:10000]}. Logs: {log_text[:20000]}. Chỉ trả lời bằng JSON với khóa 'severity', 'summary', 'root_cause', 'troubleshooting_steps'."

    analysis_result = None;
    raw_response_text = None
    # v1.0.6: Initialize new fields
    root_cause = None
    troubleshooting_steps = None

    logging.info(f"[DEBUG] Analysis check: USE_LOCAL_MODEL={USE_LOCAL_MODEL}, LOCAL_GEMINI_ENDPOINT_URL={LOCAL_GEMINI_ENDPOINT_URL}, gemini_model configured={gemini_model is not None}")

    if USE_LOCAL_MODEL:
        if LOCAL_GEMINI_ENDPOINT_URL:
            logging.info(f"Attempting to call local analysis endpoint: {LOCAL_GEMINI_ENDPOINT_URL} for pod {namespace}/{pod_name}")
            try:
                response = requests.post(
                    LOCAL_GEMINI_ENDPOINT_URL,
                    json={"prompt": final_prompt},
                    timeout=120
                )
                response.raise_for_status()
                raw_response_text = response.text
                analysis_result = response.json()
                logging.info(f"Received response from local endpoint: {analysis_result}")

                if not isinstance(analysis_result, dict):
                     raise ValueError("Local response is not a dictionary.")
                # Basic validation, default new fields if missing
                if "severity" not in analysis_result: analysis_result["severity"] = "WARNING"
                if "summary" not in analysis_result: analysis_result["summary"] = "Local model không cung cấp tóm tắt."
                root_cause = analysis_result.get("root_cause", "N/A") # Get new field or default
                troubleshooting_steps = analysis_result.get("troubleshooting_steps", "N/A") # Get new field or default

            except requests.exceptions.RequestException as e:
                logging.error(f"Error calling local AI endpoint: {e}")
                analysis_result = {"severity": "ERROR", "summary": f"Lỗi kết nối đến local AI service: {e}"}
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding JSON response from local endpoint: {e}. Response: {raw_response_text[:500]}")
                analysis_result = {"severity": "ERROR", "summary": f"Local AI service trả về không phải JSON: {raw_response_text[:200]}"}
            except Exception as e:
                logging.error(f"Unexpected error with local AI endpoint: {e}", exc_info=True)
                analysis_result = {"severity": "ERROR", "summary": f"Lỗi không xác định với local AI service: {e}"}
        else:
            logging.error("USE_LOCAL_MODEL is true, but LOCAL_GEMINI_ENDPOINT is not configured.")
            analysis_result = {"severity": "ERROR", "summary": "Lỗi cấu hình: Đã bật dùng model local nhưng thiếu URL endpoint."}

    elif gemini_model:
        logging.info(f"Attempting to call Google Gemini API ({GEMINI_MODEL_NAME}) for pod {namespace}/{pod_name}")
        try:
            response = gemini_model.generate_content(
                final_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=500 # Increased token limit slightly for new fields
                ),
                request_options={'timeout': 90}
            )

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
            else:
                raw_response_text = response.text.strip()
                logging.info(f"Received response from Gemini (raw): {raw_response_text}")

                cleaned_response_text = raw_response_text
                if cleaned_response_text.startswith("```json"):
                    cleaned_response_text = cleaned_response_text.strip("```json").strip("`").strip()
                elif cleaned_response_text.startswith("```"):
                     cleaned_response_text = cleaned_response_text.strip("```").strip()

                match = re.search(r'\{.*\}', cleaned_response_text, re.DOTALL)
                json_string_to_parse = match.group(0) if match else cleaned_response_text

                try:
                    analysis_result = json.loads(json_string_to_parse)
                    if not isinstance(analysis_result, dict):
                         raise ValueError("Parsed response is not a dictionary.")
                    # Validate and get fields, providing defaults if missing
                    if "severity" not in analysis_result: analysis_result["severity"] = "WARNING"
                    if "summary" not in analysis_result: analysis_result["summary"] = "N/A"
                    root_cause = analysis_result.get("root_cause", "N/A") # Get new field or default
                    troubleshooting_steps = analysis_result.get("troubleshooting_steps", "N/A") # Get new field or default
                    logging.info(f"Successfully parsed Gemini JSON: {analysis_result}")

                except (json.JSONDecodeError, ValueError) as json_err:
                    logging.warning(f"Failed to decode/validate Gemini response as JSON: {json_err}. Raw response: {raw_response_text}")
                    severity = "WARNING";
                    summary_vi = f"Phản hồi Gemini không đúng định dạng JSON ({json_err}): {raw_response_text[:200]}"
                    if "CRITICAL" in raw_response_text.upper(): severity = "CRITICAL"
                    elif "ERROR" in raw_response_text.upper(): severity = "ERROR"
                    analysis_result = {"severity": severity, "summary": summary_vi}
                    # Set defaults for new fields on error
                    root_cause = "Không thể phân tích từ phản hồi."
                    troubleshooting_steps = "Kiểm tra phản hồi thô từ AI."

        except Exception as e:
            logging.error(f"Error calling Gemini API: {e}", exc_info=True)
            analysis_result = {"severity": "ERROR", "summary": f"Lỗi gọi Gemini API: {e}"}
            root_cause = "Lỗi API"
            troubleshooting_steps = "Kiểm tra kết nối và API key."

    else:
        logging.warning("No analysis endpoint available (local or remote). Skipping analysis.")
        analysis_result = {"severity": "INFO", "summary": "Phân tích bị bỏ qua do thiếu cấu hình endpoint."}
        root_cause = "N/A"
        troubleshooting_steps = "N/A"

    # v1.0.6: Return the new fields as well
    return analysis_result, final_prompt, raw_response_text, root_cause, troubleshooting_steps


# --- Hàm gửi cảnh báo Telegram ---
# v1.0.6: Modified to use template and accept more data
def send_telegram_alert(alert_data):
    """Sends an alert message to Telegram using a configurable template."""
    global telegram_alerts_counter;
    with db_lock:
        telegram_alerts_counter += 1

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram Bot Token or Chat ID is not configured. Skipping alert.")
        return

    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage";

    try:
        # Format the message using the template and provided data
        # Ensure all expected keys are present in alert_data, provide defaults if necessary
        message = TELEGRAM_ALERT_TEMPLATE.format(
            pod_key=alert_data.get('pod_key', 'N/A'),
            severity=alert_data.get('severity', 'UNKNOWN'),
            summary=alert_data.get('summary', 'N/A'),
            root_cause=alert_data.get('root_cause', 'N/A'),
            troubleshooting_steps=alert_data.get('troubleshooting_steps', 'N/A'),
            initial_reasons=alert_data.get('initial_reasons', 'N/A'),
            alert_time=alert_data.get('alert_time', 'N/A'),
            sample_logs=alert_data.get('sample_logs', '-'),
            # Add other placeholders here if needed in the template
        )
    except KeyError as e:
        logging.error(f"Missing key '{e}' in alert_data for Telegram template. Using default message format.")
        # Fallback to a simpler format if template formatting fails
        message = f"🚨 *Cảnh báo K8s/Log (Pod: {alert_data.get('pod_key', 'N/A')})* 🚨\n" \
                  f"*Mức độ:* `{alert_data.get('severity', 'UNKNOWN')}`\n" \
                  f"*Tóm tắt:* {alert_data.get('summary', 'N/A')}\n" \
                  f"_Lỗi định dạng template cảnh báo, vui lòng kiểm tra cấu hình TELEGRAM_ALERT_TEMPLATE._"
    except Exception as format_err:
         logging.error(f"Error formatting Telegram alert message: {format_err}", exc_info=True)
         message = f"🚨 *Cảnh báo K8s/Log (Pod: {alert_data.get('pod_key', 'N/A')})* 🚨\n" \
                   f"*Mức độ:* `{alert_data.get('severity', 'UNKNOWN')}`\n" \
                   f"*Tóm tắt:* {alert_data.get('summary', 'N/A')}\n" \
                   f"_Lỗi không xác định khi định dạng template cảnh báo._"


    # Truncate message if it exceeds Telegram limit
    max_len = 4096;
    truncated_message = message
    if len(message) > max_len:
        truncated_message = message[:max_len-50] + "\n\n_[... message truncated due to length limit]_"

    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': truncated_message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(telegram_api_url, json=payload, timeout=10);
        response.raise_for_status();
        logging.info(f"Sent alert to Telegram. Response: {response.json()}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending Telegram alert: {e}");
    except Exception as e:
        logging.error(f"An unexpected error occurred during Telegram send: {e}", exc_info=True)


# --- Vòng lặp chính ---
def main_loop():
    """Main monitoring loop for the agent."""
    recently_alerted_pods = {}
    last_namespace_refresh_time = 0

    while True:
        start_cycle_time = datetime.now(timezone.utc)
        logging.info("--- Starting new monitoring cycle (v1.0.6) ---") # Version bump in log

        current_time_secs = time.time()
        if current_time_secs - last_namespace_refresh_time >= NAMESPACE_REFRESH_INTERVAL_SECONDS:
                logging.info("Refreshing list of active namespaces from Kubernetes API...")
                all_active_namespaces = get_active_namespaces()
                update_available_namespaces_in_db(all_active_namespaces)
                last_namespace_refresh_time = current_time_secs

        monitored_namespaces = get_monitored_namespaces_from_db()
        if not monitored_namespaces:
                logging.warning("No namespaces configured for monitoring in DB. Skipping cycle.")
                time.sleep(SCAN_INTERVAL_SECONDS); continue
        logging.info(f"Currently monitoring {len(monitored_namespaces)} namespaces based on DB config: {', '.join(monitored_namespaces)}")

        k8s_problem_pods = scan_kubernetes_for_issues(monitored_namespaces)
        loki_scan_end_time = start_cycle_time;
        loki_scan_start_time = loki_scan_end_time - timedelta(minutes=LOKI_SCAN_RANGE_MINUTES)
        loki_suspicious_logs = scan_loki_for_suspicious_logs(loki_scan_start_time, loki_scan_end_time, monitored_namespaces)

        pods_to_investigate = {}
        for pod_key, data in k8s_problem_pods.items():
            if pod_key not in pods_to_investigate:
                pods_to_investigate[pod_key] = {"reason": [], "logs": []}
            pods_to_investigate[pod_key]["reason"].append(data["reason"])
        for pod_key, logs in loki_suspicious_logs.items():
                if pod_key not in pods_to_investigate:
                    pods_to_investigate[pod_key] = {"reason": [], "logs": []}
                pods_to_investigate[pod_key]["reason"].append(f"Loki: Phát hiện {len(logs)} log đáng ngờ (>= {LOKI_SCAN_MIN_LEVEL})")
                pods_to_investigate[pod_key]["logs"].extend(logs)
        logging.info(f"Total pods to investigate this cycle: {len(pods_to_investigate)}")

        for pod_key, data in pods_to_investigate.items():
            namespace, pod_name = pod_key.split('/', 1);
            initial_reasons = "; ".join(data["reason"]);
            suspicious_logs_found_in_scan = data["logs"]

            now_utc = datetime.now(timezone.utc)
            if pod_key in recently_alerted_pods:
                last_alert_time = recently_alerted_pods[pod_key];
                cooldown_duration = timedelta(minutes=30)
                if now_utc < last_alert_time + cooldown_duration:
                    logging.info(f"Pod {pod_key} is in cooldown period (last alert: {last_alert_time}). Skipping analysis.")
                    continue
                else:
                    del recently_alerted_pods[pod_key]
                    logging.info(f"Cooldown expired for pod {pod_key}.")

            logging.info(f"Investigating pod: {pod_key} (Initial Reasons: {initial_reasons})")

            pod_info = get_pod_info(namespace, pod_name);
            node_info = None;
            pod_events = []
            if pod_info:
                node_info = get_node_info(pod_info.get('node_name'));
                pod_events = get_pod_events(namespace, pod_name, since_minutes=LOKI_DETAIL_LOG_RANGE_MINUTES + 5)
            k8s_context_str = format_k8s_context(pod_info, node_info, pod_events)

            logs_for_analysis = suspicious_logs_found_in_scan
            if not logs_for_analysis:
                logging.info(f"No logs found in initial scan for {pod_key}. Querying Loki for detailed logs...")
                log_end_time = datetime.now(timezone.utc);
                log_start_time = log_end_time - timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
                detailed_logs = query_loki_for_pod(namespace, pod_name, log_start_time, log_end_time)
                logs_for_analysis = preprocess_and_filter(detailed_logs)

            analysis_start_time = time.time()
            # v1.0.6: Get new fields from analyze_incident
            analysis_result, final_prompt, raw_response_text, root_cause, troubleshooting_steps = analyze_incident(logs_for_analysis, k8s_context_str)
            analysis_duration = time.time() - analysis_start_time
            logging.info(f"Analysis for {pod_key} took {analysis_duration:.2f} seconds.")

            if analysis_result:
                severity = analysis_result.get("severity", "UNKNOWN").upper();
                summary = analysis_result.get("summary", "N/A")
                # Use the potentially parsed root_cause and steps, fallback if None
                root_cause_str = root_cause if root_cause is not None else "N/A"
                steps_str = troubleshooting_steps if troubleshooting_steps is not None else "N/A"

                logging.info(f"Analysis result for '{pod_key}': Severity={severity}, Summary={summary}, RootCause={root_cause_str[:100]}..., Steps={steps_str[:100]}...")

                sample_logs_str = "\n".join([f"- `{log['message'][:150]}`" for log in logs_for_analysis[:5]]) if logs_for_analysis else "-"

                # v1.0.6: Pass new fields to record_incident
                record_incident(
                    pod_key, severity, summary, initial_reasons,
                    k8s_context_str, sample_logs_str,
                    final_prompt, raw_response_text,
                    root_cause_str, steps_str # Pass parsed values
                )

                if severity in ALERT_SEVERITY_LEVELS:
                    alert_time_hcm = datetime.now(HCM_TZ);
                    time_format = '%Y-%m-%d %H:%M:%S %Z'
                    # v1.0.6: Prepare data dictionary for alert template
                    alert_data = {
                        'pod_key': pod_key,
                        'severity': severity,
                        'summary': summary,
                        'root_cause': root_cause_str,
                        'troubleshooting_steps': steps_str,
                        'initial_reasons': initial_reasons,
                        'alert_time': alert_time_hcm.strftime(time_format),
                        'sample_logs': sample_logs_str if sample_logs_str != "-" else "- Không có log mẫu liên quan."
                        # Add more fields here if needed by the template
                    }
                    send_telegram_alert(alert_data) # Pass the dictionary
                    recently_alerted_pods[pod_key] = now_utc
            else:
                logging.warning(f"Analysis failed or returned no result for pod '{pod_key}'.")

            time.sleep(2)

        cycle_duration = (datetime.now(timezone.utc) - start_cycle_time).total_seconds()
        sleep_time = max(0, SCAN_INTERVAL_SECONDS - cycle_duration)
        logging.info(f"--- Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
        time.sleep(sleep_time)

# --- Main Execution ---
if __name__ == "__main__":
    if not init_db():
        logging.error("Failed to initialize database. Exiting.")
        exit(1)

    stats_thread = threading.Thread(target=periodic_stat_update, daemon=True);
    stats_thread.start();
    logging.info("Started periodic stats update thread.")

    logging.info(f"Starting Kubernetes Log Monitoring Agent (v1.0.6)") # Version bump
    logging.info(f"Loki scan minimum level: {LOKI_SCAN_MIN_LEVEL}")
    logging.info(f"Alerting for severity levels: {ALERT_SEVERITY_LEVELS_STR}")
    logging.info(f"Restart count threshold: {RESTART_COUNT_THRESHOLD}")
    logging.info(f"Using Telegram Alert Template: {'Configured' if TELEGRAM_ALERT_TEMPLATE != DEFAULT_TELEGRAM_ALERT_TEMPLATE else 'Default'}")

    if USE_LOCAL_MODEL:
            if LOCAL_GEMINI_ENDPOINT_URL: logging.info(f"Using local analysis endpoint: {LOCAL_GEMINI_ENDPOINT_URL}")
            else: logging.error("USE_LOCAL_MODEL is true, but LOCAL_GEMINI_ENDPOINT is not configured!")
    elif gemini_model: logging.info(f"Using Google Gemini model: {GEMINI_MODEL_NAME}")
    else: logging.warning("No analysis endpoint configured (Gemini API key might be missing or invalid)!")

    required_vars = ["LOKI_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
            logging.error(f"Missing required environment variables: {', '.join(missing_vars)}. Ensure they are set. Exiting.")
            exit(1)

    if not LOCAL_GEMINI_ENDPOINT_URL and not gemini_model:
            logging.error("No usable analysis endpoint configured. Exiting.")
            exit(1)
    if USE_LOCAL_MODEL and not LOCAL_GEMINI_ENDPOINT_URL:
         logging.error("USE_LOCAL_MODEL is true, but LOCAL_GEMINI_ENDPOINT is missing. Exiting.")
         exit(1)
    if not USE_LOCAL_MODEL and not gemini_model:
         logging.error("USE_LOCAL_MODEL is false, but Gemini model failed to initialize (check GEMINI_API_KEY). Exiting.")
         exit(1)

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

