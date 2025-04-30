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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") # Vẫn đọc key Gemini
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", 30))
LOKI_SCAN_RANGE_MINUTES = int(os.environ.get("LOKI_SCAN_RANGE_MINUTES", 1))
LOKI_DETAIL_LOG_RANGE_MINUTES = int(os.environ.get("LOKI_DETAIL_LOG_RANGE_MINUTES", 30))
LOKI_QUERY_LIMIT = int(os.environ.get("LOKI_QUERY_LIMIT", 500))
# Bỏ K8S_NAMESPACES_STR
EXCLUDED_NAMESPACES_STR = os.environ.get("EXCLUDED_NAMESPACES", "kube-node-lease,kube-public")
EXCLUDED_NAMESPACES = {ns.strip() for ns in EXCLUDED_NAMESPACES_STR.split(',') if ns.strip()}
LOKI_SCAN_MIN_LEVEL = os.environ.get("LOKI_SCAN_MIN_LEVEL", "WARNING")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-1.5-flash") # Vẫn đọc tên model Gemini
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
PROMPT_TEMPLATE = os.environ.get("PROMPT_TEMPLATE", """
Phân tích tình huống của pod Kubernetes '{namespace}/{pod_name}'.
**Ưu tiên xem xét ngữ cảnh Kubernetes** được cung cấp dưới đây vì nó có thể là lý do chính bạn được gọi.
Kết hợp với các dòng log sau đây (nếu có) để đưa ra phân tích đầy đủ.
Xác định mức độ nghiêm trọng tổng thể (chọn một: INFO, WARNING, ERROR, CRITICAL).
Nếu mức độ nghiêm trọng là WARNING, ERROR hoặc CRITICAL, hãy cung cấp một bản tóm tắt ngắn gọn (1-2 câu) bằng **tiếng Việt** giải thích vấn đề cốt lõi, kết hợp thông tin từ ngữ cảnh và log.
Tập trung vào các tác động tiềm ẩn.

Ngữ cảnh Kubernetes:
--- START CONTEXT ---
{k8s_context}
--- END CONTEXT ---

Các dòng log (có thể không có):
--- START LOGS ---
{log_text}
--- END LOGS ---

Chỉ trả lời bằng định dạng JSON với các khóa "severity" và "summary". Ví dụ: {{"severity": "CRITICAL", "summary": "Pod 'kube-system/oomkill-test-pod' bị Terminated với lý do OOMKilled và có Event OOMKilled gần đây. Cần kiểm tra giới hạn bộ nhớ và code ứng dụng."}}
""")


try: HCM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception as e: logging.error(f"Could not load timezone 'Asia/Ho_Chi_Minh': {e}."); HCM_TZ = timezone.utc

# --- Cấu hình Kubernetes Client ---
try: config.load_incluster_config(); logging.info("Loaded in-cluster Kubernetes config.")
except config.ConfigException:
    try: config.load_kube_config(); logging.info("Loaded local Kubernetes config (kubeconfig).")
    except config.ConfigException: logging.error("Could not configure Kubernetes client. Exiting."); exit(1)
k8s_core_v1 = client.CoreV1Api(); k8s_apps_v1 = client.AppsV1Api()

# --- Cấu hình Gemini Client (chỉ khi cần dùng Gemini thật) ---
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

# --- HÀM MỚI ĐƯỢC THÊM: Kết nối DB ---
def get_db_connection():
    """
    Establishes a connection to the SQLite database.
    Creates the database directory if it doesn't exist.
    Returns a connection object or None if connection fails.
    """
    if not os.path.isfile(DB_PATH):
        logging.warning(f"Database file not found at {DB_PATH}. Attempting to create directory.")
        db_dir = os.path.dirname(DB_PATH)
        if not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir)
                logging.info(f"Created directory for database: {db_dir}")
            except OSError as e:
                logging.error(f"Could not create directory {db_dir}: {e}")
                return None # Cannot proceed if directory creation fails
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        # Use Row factory for dictionary-like access (optional but good practice)
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

    conn = get_db_connection() # Use the new function
    if conn is None:
        logging.error("Failed to get DB connection during initialization.")
        return False

    try:
        with conn: # Use 'with' statement for automatic commit/rollback
            cursor = conn.cursor()
            logging.info("Ensuring database tables exist...")
            # Create incidents table if not exists
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
                    raw_ai_response TEXT
                )
            ''')
            # Create daily_stats table if not exists
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    model_calls INTEGER DEFAULT 0,
                    telegram_alerts INTEGER DEFAULT 0,
                    incident_count INTEGER DEFAULT 0
                )
            ''')
            # Create available_namespaces table if not exists
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS available_namespaces (
                    name TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL
                )
            ''')
            # Create agent_config table if not exists
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            # Logic to check/add old columns (remains the same)
            cursor.execute("PRAGMA table_info(incidents)")
            columns_inc = [column[1] for column in cursor.fetchall()]
            if 'input_prompt' not in columns_inc: cursor.execute("ALTER TABLE incidents ADD COLUMN input_prompt TEXT")
            if 'raw_ai_response' not in columns_inc: cursor.execute("ALTER TABLE incidents ADD COLUMN raw_ai_response TEXT")

            cursor.execute("PRAGMA table_info(daily_stats)")
            columns_stats = [column[1] for column in cursor.fetchall()]
            if 'model_calls' not in columns_stats and 'gemini_calls' in columns_stats: cursor.execute("ALTER TABLE daily_stats RENAME COLUMN gemini_calls TO model_calls")
            elif 'model_calls' not in columns_stats: cursor.execute("ALTER TABLE daily_stats ADD COLUMN model_calls INTEGER DEFAULT 0")

            logging.info("Tables ensured.")
        logging.info(f"Database initialization/check complete at {DB_PATH}")
        return True
    except sqlite3.Error as e: logging.error(f"Database error during initialization: {e}", exc_info=True); return False
    except Exception as e: logging.error(f"Unexpected error during DB initialization: {e}", exc_info=True); return False
    finally:
        if conn: conn.close() # Ensure connection is closed

def record_incident(pod_key, severity, summary, initial_reasons, k8s_context, sample_logs, input_prompt=None, raw_ai_response=None):
    """Records an incident into the database."""
    timestamp_str = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    if conn is None: logging.error(f"Failed to get DB connection for recording incident {pod_key}"); return
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO incidents (timestamp, pod_key, severity, summary, initial_reasons, k8s_context, sample_logs, input_prompt, raw_ai_response)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (timestamp_str, pod_key, severity, summary, initial_reasons, k8s_context, sample_logs, input_prompt, raw_ai_response))

            # Update daily stats if it's an alerting severity
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
    # Only update if there's something to update
    if model_calls_counter == 0 and telegram_alerts_counter == 0: return

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    # Atomically read and reset counters
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
            # Ensure the row for today exists
            cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
            # Update the counters
            cursor.execute('''
                UPDATE daily_stats
                SET model_calls = model_calls + ?, telegram_alerts = telegram_alerts + ?
                WHERE date = ?
            ''', (calls_to_add, alerts_to_add, today_str))
            logging.info(f"Updated daily stats for {today_str}: +{calls_to_add} Model calls, +{alerts_to_add} Telegram alerts.")
    except sqlite3.Error as e:
        logging.error(f"Database error updating daily stats: {e}")
        # If update fails, try to restore counters (best effort)
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
        # Limit the number of events fetched initially
        events = k8s_core_v1.list_namespaced_event(namespace=namespace, field_selector=field_selector, limit=20) # Increased limit slightly
        recent_events = []
        if events and events.items:
                # Sort by last timestamp or creation timestamp, handling potential None values
                sorted_events = sorted(
                    events.items,
                    key=lambda e: e.last_timestamp or e.metadata.creation_timestamp or datetime(MINYEAR, 1, 1, tzinfo=timezone.utc),
                    reverse=True
                )
                for event in sorted_events:
                    event_time = event.last_timestamp or event.metadata.creation_timestamp
                    # Ensure event time is valid and recent enough
                    if event_time and event_time >= since_time:
                        recent_events.append({
                            "time": event_time.isoformat(),
                            "type": event.type,
                            "reason": event.reason,
                            "message": event.message,
                            "count": event.count
                        })
                    # Stop if we have enough events or go too far back in time
                    if len(recent_events) >= 10 or (event_time and event_time < since_time):
                        break
        return recent_events
    except ApiException as e:
        # Don't log loudly for Forbidden errors (common if RBAC is slightly off)
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
                # Identify problematic pod conditions (e.g., not Ready, Unschedulable)
                problematic_conditions = [
                    f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})"
                    for ctype, cinfo in pod_info['conditions'].items()
                    if cinfo.get('status') != 'True' # Most conditions are True when healthy
                ]
                if problematic_conditions:
                    context_str += f"  Điều kiện Pod bất thường: {', '.join(problematic_conditions)}\n"
    if node_info:
        context_str += f"Node: {node_info['name']}\n"
        if node_info.get('conditions'):
                # Identify problematic node conditions (e.g., not Ready, MemoryPressure, DiskPressure)
                problematic_conditions = [
                    f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})"
                    for ctype, cinfo in node_info['conditions'].items()
                    # Node Ready should be True, others should be False when healthy
                    if (ctype == 'Ready' and cinfo.get('status') != 'True') or \
                       (ctype != 'Ready' and cinfo.get('status') != 'False')
                ]
                if problematic_conditions:
                    context_str += f"  Điều kiện Node bất thường: {', '.join(problematic_conditions)}\n"
    if pod_events:
        context_str += "Sự kiện Pod gần đây (tối đa 10):\n"
        for event in pod_events:
            # Truncate long messages
            message_preview = event['message'][:150] + ('...' if len(event['message']) > 150 else '')
            context_str += f"  - [{event['time']}] {event['type']} {event['reason']} (x{event.get('count',1)}): {message_preview}\n"
    context_str += "--- Kết thúc ngữ cảnh ---\n"
    return context_str

# --- Hàm Lấy danh sách namespace động từ K8s ---
def get_active_namespaces():
    """Fetches the list of active, non-excluded namespaces from the K8s API."""
    active_namespaces = []
    try:
        # Use a reasonable timeout
        all_namespaces = k8s_core_v1.list_namespace(watch=False, timeout_seconds=60)
        for ns in all_namespaces.items:
            # Check if namespace is active and not in the exclusion list
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
    if not namespaces: return # Nothing to update
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    if conn is None: logging.error("Failed to get DB connection for updating available namespaces"); return
    try:
        with conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(namespaces))
            # Delete namespaces from the DB that are no longer active/present in the fetched list
            # This implicitly handles excluded namespaces as they won't be in the 'namespaces' list
            cursor.execute(f"DELETE FROM available_namespaces WHERE name NOT IN ({placeholders})", tuple(namespaces))
            # Insert or update the namespaces currently active
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
    conn = get_db_connection() # Use the defined function
    if conn is None:
        logging.warning("DB connection failed when reading monitored namespaces, using default.")
        return default_namespaces

    monitored = []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM agent_config WHERE key = 'monitored_namespaces'")
        result = cursor.fetchone()
        conn.close() # Close connection after reading

        if result and result['value']:
            try:
                # Try parsing as JSON first (preferred format)
                loaded_value = json.loads(result['value'])
                if isinstance(loaded_value, list):
                    monitored = loaded_value
                    logging.info(f"Read monitored namespaces from DB (JSON): {monitored}")
                else:
                    # Handle case where value is not a list (fallback or log warning)
                    logging.warning("Value for monitored_namespaces in DB is not a list (expected JSON list). Using default.")
                    monitored = default_namespaces
            except json.JSONDecodeError:
                # Fallback to CSV parsing if JSON fails (for backward compatibility)
                monitored = [ns.strip() for ns in result['value'].split(',') if ns.strip()]
                logging.info(f"Read monitored namespaces from DB (CSV fallback): {monitored}")
        else:
            # No config found in DB, use default
            logging.warning("No monitored_namespaces config found in DB. Using default.")
            monitored = default_namespaces

    except sqlite3.Error as e:
        logging.error(f"Database error reading monitored_namespaces: {e}")
        monitored = default_namespaces # Fallback to default on DB error
    except Exception as e:
        logging.error(f"Unexpected error reading monitored_namespaces: {e}", exc_info=True)
        monitored = default_namespaces # Fallback to default on other errors

    # Final check to ensure it returns a list
    return monitored if isinstance(monitored, list) else default_namespaces


# --- Hàm Quét Loki ---
def scan_loki_for_suspicious_logs(start_time, end_time, namespaces_to_scan):
    """Scans Loki for logs matching minimum level or specific keywords within given namespaces."""
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range";
    if not namespaces_to_scan:
        logging.info("No namespaces to scan in Loki.")
        return {}

    # Determine log levels to scan based on LOKI_SCAN_MIN_LEVEL
    log_levels_all = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"];
    scan_level_index = -1
    try: scan_level_index = log_levels_all.index(LOKI_SCAN_MIN_LEVEL.upper())
    except ValueError:
        logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {LOKI_SCAN_MIN_LEVEL}. Defaulting to WARNING.")
        scan_level_index = log_levels_all.index("WARNING")
    levels_to_scan = log_levels_all[scan_level_index:]

    # Build LogQL query
    # *** FIX: Remove re.escape for namespace names ***
    namespace_regex = "|".join(namespaces_to_scan) # Directly join names with pipe
    # Keywords indicating potential issues (case-insensitive)
    # Keep re.escape for keywords as they might contain special regex chars
    keywords_to_find = levels_to_scan + ["fail", "crash", "exception", "panic", "fatal", "timeout", "denied", "refused", "unable", "unauthorized"]
    escaped_keywords = [re.escape(k) for k in keywords_to_find];
    # Use (?i) for case-insensitive matching in LogQL regex
    regex_pattern = "(?i)(" + "|".join(escaped_keywords) + ")"
    # Combine namespace and keyword filters
    # Ensure namespace_regex is properly quoted for LogQL
    logql_query = f'{{namespace=~"{namespace_regex}"}} |~ `{regex_pattern}`'
    # Use a reasonable limit for the initial scan
    query_limit_scan = 2000

    params = {
        'query': logql_query,
        'start': int(start_time.timestamp() * 1e9), # Nanoseconds for Loki
        'end': int(end_time.timestamp() * 1e9),
        'limit': query_limit_scan,
        'direction': 'forward' # Process logs chronologically
    }

    logging.info(f"Scanning Loki for suspicious logs (Level >= {LOKI_SCAN_MIN_LEVEL} or keywords) in {len(namespaces_to_scan)} namespaces: {logql_query[:200]}...")
    suspicious_logs_by_pod = {}
    try:
        headers = {'Accept': 'application/json'};
        response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=60);
        response.raise_for_status(); # Raise exception for bad status codes (4xx or 5xx)
        data = response.json()

        if 'data' in data and 'result' in data['data']:
            count = 0
            for stream in data['data']['result']:
                labels = stream.get('stream', {});
                ns = labels.get('namespace');
                pod_name = labels.get('pod')
                # Ensure we have namespace and pod labels
                if not ns or not pod_name: continue

                pod_key = f"{ns}/{pod_name}"
                if pod_key not in suspicious_logs_by_pod:
                    suspicious_logs_by_pod[pod_key] = []

                # Process log entries for this stream
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
        # Provide more context for HTTP errors
        error_detail = ""
        try: error_detail = e.response.text[:500] # Get first 500 chars of response body
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

# --- Hàm Quét K8s (Giữ nguyên) ---
def scan_kubernetes_for_issues(namespaces_to_scan):
    """Scans specified Kubernetes namespaces for pods with potential issues."""
    problematic_pods = {}
    logging.info(f"Scanning {len(namespaces_to_scan)} Kubernetes namespaces for problematic pods...")
    for ns in namespaces_to_scan:
        try:
            # Fetch pods in the current namespace
            pods = k8s_core_v1.list_namespaced_pod(namespace=ns, watch=False, timeout_seconds=60)
            for pod in pods.items:
                pod_key = f"{ns}/{pod.metadata.name}";
                issue_found = False;
                reason = ""

                # 1. Check Pod Phase
                if pod.status.phase in ["Failed", "Unknown"]:
                    issue_found = True;
                    reason = f"Trạng thái Pod là {pod.status.phase}"
                # 2. Check Pending Pods for Unschedulable status
                elif pod.status.phase == "Pending" and pod.status.conditions:
                        scheduled_condition = next((c for c in pod.status.conditions if c.type == "PodScheduled"), None)
                        # If PodScheduled condition is False and reason is Unschedulable
                        if scheduled_condition and scheduled_condition.status == "False" and scheduled_condition.reason == "Unschedulable":
                            issue_found = True;
                            reason = f"Pod không thể lên lịch (Unschedulable)"

                # 3. Check Container Statuses (if pod phase seems ok)
                if not issue_found and pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        # High restart count
                        if cs.restart_count >= RESTART_COUNT_THRESHOLD:
                            issue_found = True;
                            reason = f"Container '{cs.name}' restart {cs.restart_count} lần (>= ngưỡng {RESTART_COUNT_THRESHOLD})"
                            break # Found an issue, no need to check other containers
                        # Problematic waiting states
                        if cs.state and cs.state.waiting and cs.state.waiting.reason in ["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "CreateContainerError"]:
                            issue_found = True;
                            reason = f"Container '{cs.name}' đang ở trạng thái Waiting với lý do '{cs.state.waiting.reason}'"
                            break
                        # Problematic terminated states (check if recent or non-restarting)
                        if cs.state and cs.state.terminated and cs.state.terminated.reason in ["OOMKilled", "Error", "ContainerCannotRun"]:
                            # Consider it an issue if restartPolicy isn't Always, OR if it terminated recently
                            is_recent_termination = cs.state.terminated.finished_at and \
                                                    (datetime.now(timezone.utc) - cs.state.terminated.finished_at) < timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
                            if pod.spec.restart_policy != "Always" or is_recent_termination:
                                issue_found = True;
                                reason = f"Container '{cs.name}' bị Terminated với lý do '{cs.state.terminated.reason}'"
                                break

                # If any issue was found for this pod
                if issue_found:
                    logging.warning(f"Phát hiện pod có vấn đề tiềm ẩn (K8s Scan): {pod_key}. Lý do: {reason}")
                    if pod_key not in problematic_pods:
                        problematic_pods[pod_key] = {
                            "namespace": ns,
                            "pod_name": pod.metadata.name,
                            "reason": f"K8s: {reason}" # Prefix reason with source
                        }
        except ApiException as e:
            logging.error(f"API Error scanning namespace {ns}: {e.status} {e.reason}")
        except Exception as e:
            logging.error(f"Unexpected error scanning namespace {ns}: {e}", exc_info=True)

    logging.info(f"Finished K8s scan. Found {len(problematic_pods)} potentially problematic pods from K8s state.")
    return problematic_pods

# --- Hàm Query Loki cho pod (Giữ nguyên) ---
def query_loki_for_pod(namespace, pod_name, start_time, end_time):
    """Queries Loki for all logs of a specific pod within a time range."""
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range";
    # Simple query for all logs from the specific pod
    logql_query = f'{{namespace="{namespace}", pod="{pod_name}"}}'
    params = {
        'query': logql_query,
        'start': int(start_time.timestamp() * 1e9),
        'end': int(end_time.timestamp() * 1e9),
        'limit': LOKI_QUERY_LIMIT, # Use configured limit for detailed query
        'direction': 'forward'
    }
    logging.info(f"Querying Loki for pod '{namespace}/{pod_name}' from {start_time} to {end_time}")
    try:
        headers = {'Accept': 'application/json'};
        response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=45); # Slightly shorter timeout for specific query
        response.raise_for_status();
        data = response.json()

        if 'data' in data and 'result' in data['data']:
            log_entries = [];
            for stream in data['data']['result']:
                # Add labels from the stream to each log entry
                stream_labels = stream.get('stream', {})
                for timestamp_ns, log_line in stream['values']:
                    log_entries.append({
                        "timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc),
                        "message": log_line.strip(),
                        "labels": stream_labels # Include stream labels
                    })
            # Sort logs by timestamp after collecting from all streams
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

# --- Hàm lọc log (Giữ nguyên) ---
def preprocess_and_filter(log_entries):
    """Filters log entries based on LOKI_SCAN_MIN_LEVEL and problem keywords."""
    filtered_logs = [];
    log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"];
    min_level_index = -1
    try: min_level_index = log_levels.index(LOKI_SCAN_MIN_LEVEL.upper())
    except ValueError:
        logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {LOKI_SCAN_MIN_LEVEL}. Defaulting to WARNING.");
        min_level_index = log_levels.index("WARNING")

    # Keywords that often indicate problems, even if log level is low
    keywords_indicating_problem = ["FAIL", "ERROR", "CRASH", "EXCEPTION", "UNAVAILABLE", "FATAL", "PANIC", "TIMEOUT", "DENIED", "REFUSED", "UNABLE", "UNAUTHORIZED"]

    for entry in log_entries:
        log_line = entry['message'];
        log_line_upper = log_line.upper();
        level_detected = False

        # 1. Check for standard log levels
        for i, level in enumerate(log_levels):
            # Check common log level formats (e.g., " INFO ", "INFO:", "[INFO]", "level=info", "\"level\":\"info\"")
            if f" {level} " in f" {log_line_upper} " or \
               log_line_upper.startswith(level+":") or \
               f"[{level}]" in log_line_upper or \
               f"level={level.lower()}" in log_line_upper or \
               f"\"level\":\"{level.lower()}\"" in log_line_upper:
                # If detected level is at or above the minimum scan level
                if i >= min_level_index:
                    filtered_logs.append(entry);
                    level_detected = True;
                    break # Found a qualifying level, move to next log entry

        # 2. If no qualifying level detected, check for problem keywords
        if not level_detected:
            if any(keyword in log_line_upper for keyword in keywords_indicating_problem):
                    # Include keyword-matched logs only if min scan level is WARNING or lower
                    warning_index = log_levels.index("WARNING")
                    if min_level_index <= warning_index:
                        filtered_logs.append(entry)

    logging.info(f"Filtered {len(log_entries)} logs down to {len(filtered_logs)} relevant logs (Scan Level: {LOKI_SCAN_MIN_LEVEL}).")
    return filtered_logs


# --- Hàm phân tích (Giữ nguyên) ---
def analyze_incident(log_batch, k8s_context=""):
    """Analyzes logs and context using either local model or Gemini API."""
    global model_calls_counter;
    with db_lock: # Use lock for thread safety when incrementing counter
        model_calls_counter += 1

    if not log_batch and not k8s_context:
        logging.warning("analyze_incident called with no logs and no context. Skipping analysis.")
        return None, None, None # Return None for all expected values

    # --- Extract namespace/pod_name for prompt formatting ---
    namespace = "unknown"; pod_name = "unknown_pod"
    # Prefer labels from logs if available
    if log_batch and log_batch[0].get('labels'):
        labels = log_batch[0].get('labels', {});
        namespace = labels.get('namespace', namespace);
        pod_name = labels.get('pod', pod_name)
    # Fallback to parsing from k8s_context if no logs or labels
    elif k8s_context:
        match_ns = re.search(r"Pod:\s*([\w.-]+)/", k8s_context); # More robust regex
        match_pod = re.search(r"Pod:\s*[\w.-]+/([\w.-]+)\n", k8s_context);
        if match_ns: namespace = match_ns.group(1);
        if match_pod: pod_name = match_pod.group(1)

    # --- Prepare Log Text ---
    log_text = "N/A"
    if log_batch:
        # Limit number of logs and length of each log line for the prompt
        limited_logs = [f"[{entry['timestamp'].isoformat()}] {entry['message'][:500]}" for entry in log_batch[:15]]; # Max 15 logs, 500 chars each
        log_text = "\n".join(limited_logs)

    # --- Format the Prompt ---
    final_prompt = None # Initialize final_prompt
    try:
        # Limit context and log text length significantly to avoid exceeding model limits
        final_prompt = PROMPT_TEMPLATE.format(
            namespace=namespace,
            pod_name=pod_name,
            k8s_context=k8s_context[:10000], # Limit context length
            log_text=log_text[:20000]      # Limit log text length
        )
    except KeyError as e:
        logging.error(f"Missing placeholder in PROMPT_TEMPLATE: {e}. Using default prompt structure.")
        # Fallback prompt if template formatting fails
        final_prompt = f"Phân tích pod {namespace}/{pod_name}. Ngữ cảnh K8s: {k8s_context[:10000]}. Logs: {log_text[:20000]}. Chỉ trả lời bằng JSON với khóa 'severity' và 'summary'."

    # --- Call Analysis Endpoint ---
    analysis_result = None;
    raw_response_text = None # Store the raw response for debugging/recording

    logging.info(f"[DEBUG] Analysis check: USE_LOCAL_MODEL={USE_LOCAL_MODEL}, LOCAL_GEMINI_ENDPOINT_URL={LOCAL_GEMINI_ENDPOINT_URL}, gemini_model configured={gemini_model is not None}")

    if USE_LOCAL_MODEL:
        if LOCAL_GEMINI_ENDPOINT_URL:
            logging.info(f"Attempting to call local analysis endpoint: {LOCAL_GEMINI_ENDPOINT_URL} for pod {namespace}/{pod_name}")
            try:
                # Send prompt to local endpoint
                response = requests.post(
                    LOCAL_GEMINI_ENDPOINT_URL,
                    json={"prompt": final_prompt},
                    timeout=120 # Generous timeout for local model
                )
                response.raise_for_status() # Check for HTTP errors
                raw_response_text = response.text # Store raw response
                analysis_result = response.json() # Parse JSON response
                logging.info(f"Received response from local endpoint: {analysis_result}")

                # Basic validation of local model response
                if not isinstance(analysis_result, dict):
                     raise ValueError("Local response is not a dictionary.")
                if "severity" not in analysis_result:
                    logging.warning("Local model response missing 'severity'. Defaulting to WARNING.")
                    analysis_result["severity"] = "WARNING"
                if "summary" not in analysis_result:
                    analysis_result["summary"] = "Local model không cung cấp tóm tắt."

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
            # Configuration error: Local model selected but no endpoint URL
            logging.error("USE_LOCAL_MODEL is true, but LOCAL_GEMINI_ENDPOINT is not configured.")
            analysis_result = {"severity": "ERROR", "summary": "Lỗi cấu hình: Đã bật dùng model local nhưng thiếu URL endpoint."}

    elif gemini_model: # Use Google Gemini API
        logging.info(f"Attempting to call Google Gemini API ({GEMINI_MODEL_NAME}) for pod {namespace}/{pod_name}")
        try:
            # Call Gemini API
            response = gemini_model.generate_content(
                final_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2, # Low temperature for more deterministic response
                    max_output_tokens=300 # Limit response length
                ),
                request_options={'timeout': 90} # API call timeout
            )

            # Check if response has content
            if not response.parts:
                logging.warning("Gemini response has no parts. Raw response: %s", response)
                # Attempt to get candidate information if available
                try:
                    finish_reason = response.candidates[0].finish_reason if response.candidates else "UNKNOWN"
                    safety_ratings = response.candidates[0].safety_ratings if response.candidates else []
                    logging.warning(f"Gemini Finish Reason: {finish_reason}, Safety Ratings: {safety_ratings}")
                    if finish_reason.name == 'SAFETY':
                         summary = "Phản hồi bị chặn bởi bộ lọc an toàn Gemini."
                    elif finish_reason.name == 'MAX_TOKENS':
                         summary = "Phản hồi Gemini bị cắt do đạt giới hạn token."
                    else:
                         summary = f"Gemini không trả về nội dung (Lý do: {finish_reason.name})."
                    analysis_result = {"severity": "WARNING", "summary": summary}
                except Exception as inner_e:
                     logging.error(f"Error trying to extract finish reason/safety rating: {inner_e}")
                     analysis_result = {"severity": "WARNING", "summary": "Gemini không trả về nội dung."}

            else:
                raw_response_text = response.text.strip() # Store raw response text
                logging.info(f"Received response from Gemini (raw): {raw_response_text}")

                # --- Clean and Parse Gemini Response ---
                cleaned_response_text = raw_response_text
                # Remove markdown code blocks if present
                if cleaned_response_text.startswith("```json"):
                    cleaned_response_text = cleaned_response_text.strip("```json").strip("`").strip()
                elif cleaned_response_text.startswith("```"):
                     cleaned_response_text = cleaned_response_text.strip("```").strip()

                # Attempt to extract JSON object using regex (more robust)
                match = re.search(r'\{.*\}', cleaned_response_text, re.DOTALL)
                json_string_to_parse = match.group(0) if match else cleaned_response_text

                try:
                    # Parse the extracted/cleaned JSON string
                    analysis_result = json.loads(json_string_to_parse)
                    # Validate parsed JSON
                    if not isinstance(analysis_result, dict):
                         raise ValueError("Parsed response is not a dictionary.")
                    if "severity" not in analysis_result:
                        logging.warning("Gemini JSON response missing 'severity'. Defaulting to WARNING.")
                        analysis_result["severity"] = "WARNING"
                    if "summary" not in analysis_result:
                        analysis_result["summary"] = "Gemini không cung cấp tóm tắt trong JSON."
                    logging.info(f"Successfully parsed Gemini JSON: {analysis_result}")

                except json.JSONDecodeError as json_err:
                    # Handle cases where the response is not valid JSON
                    logging.warning(f"Failed to decode Gemini response as JSON: {json_err}. Raw response: {raw_response_text}")
                    # Fallback: Try to infer severity from raw text, provide error summary
                    severity = "WARNING"; # Default fallback severity
                    summary_vi = f"Phản hồi Gemini không phải JSON hợp lệ ({json_err}): {raw_response_text[:200]}" # Truncated raw response
                    # Simple check for severity keywords in the raw text
                    if "CRITICAL" in raw_response_text.upper(): severity = "CRITICAL"
                    elif "ERROR" in raw_response_text.upper(): severity = "ERROR"
                    analysis_result = {"severity": severity, "summary": summary_vi}
                except ValueError as val_err:
                     logging.warning(f"Validation error after parsing Gemini response: {val_err}. Raw response: {raw_response_text}")
                     analysis_result = {"severity": "WARNING", "summary": f"Lỗi xử lý phản hồi Gemini: {val_err}"}


        except Exception as e:
            # Catch other potential errors during Gemini API call
            logging.error(f"Error calling Gemini API: {e}", exc_info=True)
            analysis_result = {"severity": "ERROR", "summary": f"Lỗi gọi Gemini API: {e}"}

    else:
        # No analysis endpoint configured (neither local nor remote)
        logging.warning("No analysis endpoint available (local or remote). Skipping analysis.")
        analysis_result = {"severity": "INFO", "summary": "Phân tích bị bỏ qua do thiếu cấu hình endpoint."}

    # Return the analysis result dictionary and the raw response text
    return analysis_result, final_prompt, raw_response_text


# --- Hàm gửi cảnh báo Telegram (Giữ nguyên) ---
def send_telegram_alert(message):
    """Sends an alert message to the configured Telegram chat."""
    global telegram_alerts_counter;
    with db_lock: # Use lock for thread safety
        telegram_alerts_counter += 1

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram Bot Token or Chat ID is not configured. Skipping alert.")
        return

    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage";
    # Telegram message length limit is 4096 characters
    max_len = 4096;
    # Truncate message if it exceeds the limit, adding an indicator
    truncated_message = message
    if len(message) > max_len:
        truncated_message = message[:max_len-50] + "\n\n_[... message truncated due to length limit]_"

    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': truncated_message,
        'parse_mode': 'Markdown' # Use Markdown for formatting
    }
    try:
        response = requests.post(telegram_api_url, json=payload, timeout=10); # 10-second timeout
        response.raise_for_status(); # Raise HTTPError for bad responses (4xx or 5xx)
        logging.info(f"Sent alert to Telegram. Response: {response.json()}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending Telegram alert: {e}");
    except Exception as e:
        # Catch any other unexpected errors during send
        logging.error(f"An unexpected error occurred during Telegram send: {e}", exc_info=True)


# --- Vòng lặp chính (Sửa để dùng list namespace động từ DB) ---
def main_loop():
    """Main monitoring loop for the agent."""
    recently_alerted_pods = {} # {pod_key: last_alert_time_utc}
    last_namespace_refresh_time = 0 # Force refresh on first run

    while True:
        start_cycle_time = datetime.now(timezone.utc)
        logging.info("--- Starting new monitoring cycle (Parallel Scan - DB Configured Namespaces) ---")

        # --- Refresh Namespaces Periodically ---
        current_time_secs = time.time()
        if current_time_secs - last_namespace_refresh_time >= NAMESPACE_REFRESH_INTERVAL_SECONDS:
                logging.info("Refreshing list of active namespaces from Kubernetes API...")
                all_active_namespaces = get_active_namespaces()
                update_available_namespaces_in_db(all_active_namespaces)
                last_namespace_refresh_time = current_time_secs # Update last refresh time

        # --- Get Namespaces to Monitor ---
        monitored_namespaces = get_monitored_namespaces_from_db()
        if not monitored_namespaces:
                logging.warning("No namespaces configured for monitoring in DB. Skipping cycle.")
                time.sleep(SCAN_INTERVAL_SECONDS); continue # Wait before next cycle
        logging.info(f"Currently monitoring {len(monitored_namespaces)} namespaces based on DB config: {', '.join(monitored_namespaces)}")

        # --- Parallel Scans ---
        # 1. Scan K8s for problematic pod states
        k8s_problem_pods = scan_kubernetes_for_issues(monitored_namespaces)

        # 2. Scan Loki for suspicious logs (recent timeframe)
        loki_scan_end_time = start_cycle_time;
        loki_scan_start_time = loki_scan_end_time - timedelta(minutes=LOKI_SCAN_RANGE_MINUTES)
        loki_suspicious_logs = scan_loki_for_suspicious_logs(loki_scan_start_time, loki_scan_end_time, monitored_namespaces)

        # --- Combine Results and Identify Pods for Investigation ---
        pods_to_investigate = {} # {pod_key: {"reason": [reasons...], "logs": [log_entries...]}}

        # Add pods found by K8s scan
        for pod_key, data in k8s_problem_pods.items():
            if pod_key not in pods_to_investigate:
                pods_to_investigate[pod_key] = {"reason": [], "logs": []}
            pods_to_investigate[pod_key]["reason"].append(data["reason"]) # Add K8s reason

        # Add pods found by Loki scan
        for pod_key, logs in loki_suspicious_logs.items():
                if pod_key not in pods_to_investigate:
                    pods_to_investigate[pod_key] = {"reason": [], "logs": []}
                # Add Loki reason and the logs found
                pods_to_investigate[pod_key]["reason"].append(f"Loki: Phát hiện {len(logs)} log đáng ngờ (>= {LOKI_SCAN_MIN_LEVEL})")
                pods_to_investigate[pod_key]["logs"].extend(logs) # Add logs found during scan

        logging.info(f"Total pods to investigate this cycle: {len(pods_to_investigate)}")

        # --- Investigate Each Identified Pod ---
        for pod_key, data in pods_to_investigate.items():
            namespace, pod_name = pod_key.split('/', 1);
            initial_reasons = "; ".join(data["reason"]); # Combine all initial detection reasons
            suspicious_logs_found_in_scan = data["logs"] # Logs found in the initial quick scan

            now_utc = datetime.now(timezone.utc)

            # --- Cooldown Check ---
            if pod_key in recently_alerted_pods:
                last_alert_time = recently_alerted_pods[pod_key];
                cooldown_duration = timedelta(minutes=30) # 30-minute cooldown
                if now_utc < last_alert_time + cooldown_duration:
                    logging.info(f"Pod {pod_key} is in cooldown period (last alert: {last_alert_time}). Skipping analysis.")
                    continue # Skip this pod for now
                else:
                    # Cooldown expired, remove from dict
                    del recently_alerted_pods[pod_key]
                    logging.info(f"Cooldown expired for pod {pod_key}.")

            logging.info(f"Investigating pod: {pod_key} (Initial Reasons: {initial_reasons})")

            # --- Gather Context ---
            # Get K8s info (Pod, Node, Events)
            pod_info = get_pod_info(namespace, pod_name);
            node_info = None;
            pod_events = []
            if pod_info:
                node_info = get_node_info(pod_info.get('node_name'));
                # Get recent events for context
                pod_events = get_pod_events(namespace, pod_name, since_minutes=LOKI_DETAIL_LOG_RANGE_MINUTES + 5)
            k8s_context_str = format_k8s_context(pod_info, node_info, pod_events)

            # --- Gather Logs for Analysis ---
            logs_for_analysis = suspicious_logs_found_in_scan # Start with logs from initial scan
            # If initial scan didn't find logs (e.g., issue detected only by K8s), query Loki for detailed logs
            if not logs_for_analysis:
                logging.info(f"No logs found in initial scan for {pod_key}. Querying Loki for detailed logs...")
                log_end_time = datetime.now(timezone.utc);
                log_start_time = log_end_time - timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
                detailed_logs = query_loki_for_pod(namespace, pod_name, log_start_time, log_end_time)
                # Filter the detailed logs based on configured level/keywords
                logs_for_analysis = preprocess_and_filter(detailed_logs)

            # --- Perform Analysis ---
            analysis_start_time = time.time()
            # Call analysis function (returns result dict, prompt used, raw response)
            analysis_result, final_prompt, raw_response_text = analyze_incident(logs_for_analysis, k8s_context_str)
            analysis_duration = time.time() - analysis_start_time
            logging.info(f"Analysis for {pod_key} took {analysis_duration:.2f} seconds.")

            # --- Process Analysis Result ---
            if analysis_result:
                severity = analysis_result.get("severity", "UNKNOWN").upper();
                summary = analysis_result.get("summary", "N/A")
                logging.info(f"Analysis result for '{pod_key}': Severity={severity}, Summary={summary}")

                # Prepare sample logs string for DB/alert
                sample_logs_str = "\n".join([f"- `{log['message'][:150]}`" for log in logs_for_analysis[:5]]) if logs_for_analysis else "-"

                # Record the incident details in the database
                record_incident(
                    pod_key, severity, summary, initial_reasons,
                    k8s_context_str, sample_logs_str,
                    final_prompt, # Record the prompt sent to the model
                    raw_response_text # Record the raw response from the model
                )

                # --- Send Alert if Necessary ---
                if severity in ALERT_SEVERITY_LEVELS:
                    alert_time_hcm = datetime.now(HCM_TZ); # Use local timezone for alert message
                    time_format = '%Y-%m-%d %H:%M:%S %Z'
                    alert_message = f"""🚨 *Cảnh báo K8s/Log (Pod: {pod_key})* 🚨
*Mức độ:* `{severity}`
*Tóm tắt:* {summary}
*Lý do phát hiện ban đầu:* {initial_reasons}
*Thời gian phát hiện:* `{alert_time_hcm.strftime(time_format)}`
*Log mẫu (nếu có):*
{sample_logs_str if sample_logs_str != "-" else "- Không có log mẫu liên quan."}

_Vui lòng kiểm tra trạng thái pod/node/events và log trên Loki để biết thêm chi tiết._"""
                    send_telegram_alert(alert_message)
                    # Add to recently alerted dict with current UTC time
                    recently_alerted_pods[pod_key] = now_utc
            else:
                # Analysis failed or returned None
                logging.warning(f"Analysis failed or returned no result for pod '{pod_key}'.")

            # Small delay between processing pods to avoid overwhelming APIs/DB
            time.sleep(2) # Reduced sleep time

        # --- End of Cycle ---
        cycle_duration = (datetime.now(timezone.utc) - start_cycle_time).total_seconds()
        sleep_time = max(0, SCAN_INTERVAL_SECONDS - cycle_duration)
        logging.info(f"--- Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
        time.sleep(sleep_time)

# --- Main Execution ---
if __name__ == "__main__":
    if not init_db():
        logging.error("Failed to initialize database. Exiting.")
        exit(1)

    # Start the periodic stats update thread
    stats_thread = threading.Thread(target=periodic_stat_update, daemon=True);
    stats_thread.start();
    logging.info("Started periodic stats update thread.")

    logging.info(f"Starting Kubernetes Log Monitoring Agent (Parallel Scan - DB Configured Namespaces)")
    logging.info(f"Loki scan minimum level: {LOKI_SCAN_MIN_LEVEL}")
    logging.info(f"Alerting for severity levels: {ALERT_SEVERITY_LEVELS_STR}")
    logging.info(f"Restart count threshold: {RESTART_COUNT_THRESHOLD}")

    # Log which analysis endpoint is being used
    if USE_LOCAL_MODEL:
            if LOCAL_GEMINI_ENDPOINT_URL: logging.info(f"Using local analysis endpoint: {LOCAL_GEMINI_ENDPOINT_URL}")
            else: logging.error("USE_LOCAL_MODEL is true, but LOCAL_GEMINI_ENDPOINT is not configured!")
    elif gemini_model: logging.info(f"Using Google Gemini model: {GEMINI_MODEL_NAME}")
    else: logging.warning("No analysis endpoint configured (Gemini API key might be missing or invalid)!")

    # --- Configuration Checks ---
    # Check essential environment variables
    required_vars = ["LOKI_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
            logging.error(f"Missing required environment variables: {', '.join(missing_vars)}. Ensure they are set. Exiting.")
            exit(1)

    # Check if at least one analysis endpoint is usable
    if not LOCAL_GEMINI_ENDPOINT_URL and not gemini_model:
            logging.error("No usable analysis endpoint configured. Either set USE_LOCAL_MODEL=true and provide LOCAL_GEMINI_ENDPOINT, or set USE_LOCAL_MODEL=false and provide a valid GEMINI_API_KEY. Exiting.")
            exit(1)
    if USE_LOCAL_MODEL and not LOCAL_GEMINI_ENDPOINT_URL:
         logging.error("USE_LOCAL_MODEL is true, but LOCAL_GEMINI_ENDPOINT is missing. Exiting.")
         exit(1)
    if not USE_LOCAL_MODEL and not gemini_model:
         logging.error("USE_LOCAL_MODEL is false, but Gemini model failed to initialize (check GEMINI_API_KEY). Exiting.")
         exit(1)


    # --- Start Main Loop ---
    try:
        main_loop()
    except KeyboardInterrupt:
        logging.info("Agent stopped by user (KeyboardInterrupt).")
    except Exception as main_err:
        logging.critical(f"Unhandled exception in main loop: {main_err}", exc_info=True)
    finally:
        logging.info("Performing final stats update before exiting...")
        update_daily_stats() # Ensure latest stats are written
        logging.info("Agent shutdown complete.")

