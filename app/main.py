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
# Bỏ K8S_NAMESPACES và EXCLUDED_NAMESPACES khỏi env agent, sẽ đọc từ DB
LOKI_SCAN_MIN_LEVEL = os.environ.get("LOKI_SCAN_MIN_LEVEL", "WARNING")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-1.5-flash")
ALERT_SEVERITY_LEVELS_STR = os.environ.get("ALERT_SEVERITY_LEVELS", "WARNING,ERROR,CRITICAL")
ALERT_SEVERITY_LEVELS = [level.strip().upper() for level in ALERT_SEVERITY_LEVELS_STR.split(',') if level.strip()]
RESTART_COUNT_THRESHOLD = int(os.environ.get("RESTART_COUNT_THRESHOLD", 5))
DB_PATH = os.environ.get("DB_PATH", "/data/agent_stats.db")
STATS_UPDATE_INTERVAL_SECONDS = int(os.environ.get("STATS_UPDATE_INTERVAL_SECONDS", 300))
LOCAL_GEMINI_ENDPOINT_URL = os.environ.get("LOCAL_GEMINI_ENDPOINT")
USE_LOCAL_MODEL_STR = os.environ.get("USE_LOCAL_MODEL", "false").lower()
USE_LOCAL_MODEL = USE_LOCAL_MODEL_STR == "true"
PROMPT_TEMPLATE = os.environ.get("PROMPT_TEMPLATE", """...""") # Giữ template
# --- THÊM CẤU HÌNH MỚI ---
NAMESPACE_REFRESH_INTERVAL_SECONDS = int(os.environ.get("NAMESPACE_REFRESH_INTERVAL_SECONDS", 3600)) # Quét lại NS mỗi giờ
DEFAULT_MONITORED_NAMESPACES = os.environ.get("DEFAULT_MONITORED_NAMESPACES", "kube-system,default") # NS mặc định nếu DB trống
# --- KẾT THÚC THAY ĐỔI ---


try: HCM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception as e: logging.error(f"Could not load timezone 'Asia/Ho_Chi_Minh': {e}."); HCM_TZ = timezone.utc

# --- Cấu hình Kubernetes Client ---
try: config.load_incluster_config(); logging.info("Loaded in-cluster Kubernetes config.")
except config.ConfigException:
    try: config.load_kube_config(); logging.info("Loaded local Kubernetes config (kubeconfig).")
    except config.ConfigException: logging.error("Could not configure Kubernetes client. Exiting."); exit(1)
k8s_core_v1 = client.CoreV1Api(); k8s_apps_v1 = client.AppsV1Api()

# --- Cấu hình Gemini Client (Giữ nguyên) ---
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
def init_db():
    db_dir = os.path.dirname(DB_PATH);
    if not os.path.exists(db_dir):
        try: os.makedirs(db_dir); logging.info(f"Created directory for database: {db_dir}")
        except OSError as e: logging.error(f"Could not create directory {db_dir}: {e}"); return False
    conn = None
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            cursor = conn.cursor()
            logging.info("Ensuring database tables exist...")
            cursor.execute(''' CREATE TABLE IF NOT EXISTS incidents (...) ''') # Giữ nguyên
            cursor.execute(''' CREATE TABLE IF NOT EXISTS daily_stats (...) ''') # Giữ nguyên
            cursor.execute(''' CREATE TABLE IF NOT EXISTS users (...) ''') # Giữ nguyên
            # === THAY ĐỔI: Thêm bảng available_namespaces và agent_config ===
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
            # === KẾT THÚC THAY ĐỔI ===
            # Logic kiểm tra/thêm cột cũ giữ nguyên
            cursor.execute("PRAGMA table_info(incidents)")
            columns_inc = [column[1] for column in cursor.fetchall()]
            if 'input_prompt' not in columns_inc: cursor.execute("ALTER TABLE incidents ADD COLUMN input_prompt TEXT")
            if 'raw_ai_response' not in columns_inc: cursor.execute("ALTER TABLE incidents ADD COLUMN raw_ai_response TEXT")
            cursor.execute("PRAGMA table_info(daily_stats)")
            columns_stats = [column[1] for column in cursor.fetchall()]
            if 'model_calls' not in columns_stats and 'gemini_calls' in columns_stats: cursor.execute("ALTER TABLE daily_stats RENAME COLUMN gemini_calls TO model_calls")
            elif 'model_calls' not in columns_stats: cursor.execute("ALTER TABLE daily_stats ADD COLUMN model_calls INTEGER DEFAULT 0")

            logging.info("Tables ensured.")
            # Không tạo user ở đây nữa, để portal quản lý hoặc tạo thủ công
        logging.info(f"Database initialization/check complete at {DB_PATH}")
        return True
    except sqlite3.Error as e: logging.error(f"Database error during initialization: {e}"); return False
    except Exception as e: logging.error(f"Unexpected error during DB initialization: {e}", exc_info=True); return False

def record_incident(pod_key, severity, summary, initial_reasons, k8s_context, sample_logs, input_prompt=None, raw_ai_response=None): # ... (Giữ nguyên) ...
    timestamp_str = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute(''' INSERT INTO incidents (timestamp, pod_key, severity, summary, initial_reasons, k8s_context, sample_logs, input_prompt, raw_ai_response) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ''', (timestamp_str, pod_key, severity, summary, initial_reasons, k8s_context, sample_logs, input_prompt, raw_ai_response))
            if severity in ALERT_SEVERITY_LEVELS:
                today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                cursor.execute(''' INSERT INTO daily_stats (date, incident_count) VALUES (?, 1) ON CONFLICT(date) DO UPDATE SET incident_count = incident_count + 1 ''', (today_str,))
            logging.info(f"Recorded data for {pod_key} with severity {severity}")
    except sqlite3.Error as e: logging.error(f"Database error recording incident for {pod_key}: {e}")
    except Exception as e: logging.error(f"Unexpected error recording incident for {pod_key}: {e}", exc_info=True)
def update_daily_stats(): # ... (Giữ nguyên) ...
    global model_calls_counter, telegram_alerts_counter
    if model_calls_counter == 0 and telegram_alerts_counter == 0: return
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d'); calls_to_add = model_calls_counter; alerts_to_add = telegram_alerts_counter
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
            cursor.execute(''' UPDATE daily_stats SET model_calls = model_calls + ?, telegram_alerts = telegram_alerts + ? WHERE date = ? ''', (calls_to_add, alerts_to_add, today_str))
            logging.info(f"Updated daily stats for {today_str}: +{calls_to_add} Model calls, +{alerts_to_add} Telegram alerts.")
            model_calls_counter = 0; telegram_alerts_counter = 0
    except sqlite3.Error as e: logging.error(f"Database error updating daily stats: {e}")
    except Exception as e: logging.error(f"Unexpected error updating daily stats: {e}", exc_info=True)
def periodic_stat_update(): # ... (Giữ nguyên) ...
    while True: time.sleep(STATS_UPDATE_INTERVAL_SECONDS); update_daily_stats()

# --- Các hàm lấy thông tin Kubernetes (Giữ nguyên) ---
def get_pod_info(namespace, pod_name): # ... (Giữ nguyên) ...
    try:
        pod = k8s_core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        info = {"name": pod.metadata.name,"namespace": pod.metadata.namespace,"status": pod.status.phase,"node_name": pod.spec.node_name,"start_time": pod.status.start_time.isoformat() if pod.status.start_time else "N/A","restarts": sum(cs.restart_count for cs in pod.status.container_statuses) if pod.status.container_statuses else 0,"conditions": {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message} for cond in pod.status.conditions} if pod.status.conditions else {},"container_statuses": {}}
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                state_info = "N/A";
                if cs.state:
                    if cs.state.running: state_info = "Running"
                    elif cs.state.waiting: state_info = f"Waiting ({cs.state.waiting.reason})"
                    elif cs.state.terminated: state_info = f"Terminated ({cs.state.terminated.reason}, ExitCode: {cs.state.terminated.exit_code})"
                info["container_statuses"][cs.name] = {"ready": cs.ready,"restart_count": cs.restart_count,"state": state_info}
        return info
    except ApiException as e: logging.warning(f"Could not get pod info for {namespace}/{pod_name}: {e.status} {e.reason}"); return None
    except Exception as e: logging.error(f"Unexpected error getting pod info for {namespace}/{pod_name}: {e}", exc_info=True); return None
def get_node_info(node_name): # ... (Giữ nguyên) ...
    if not node_name: return None
    try:
        node = k8s_core_v1.read_node(name=node_name)
        conditions = {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message} for cond in node.status.conditions} if node.status.conditions else {}
        info = {"name": node.metadata.name,"conditions": conditions,"allocatable_cpu": node.status.allocatable.get('cpu', 'N/A'),"allocatable_memory": node.status.allocatable.get('memory', 'N/A'),"kubelet_version": node.status.node_info.kubelet_version}
        return info
    except ApiException as e: logging.warning(f"Could not get node info for {node_name}: {e.status} {e.reason}"); return None
    except Exception as e: logging.error(f"Unexpected error getting node info for {node_name}: {e}", exc_info=True); return None
def get_pod_events(namespace, pod_name, since_minutes=15): # ... (Giữ nguyên) ...
    try:
        since_time = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        field_selector = f"involvedObject.kind=Pod,involvedObject.name={pod_name},involvedObject.namespace={namespace}"
        events = k8s_core_v1.list_namespaced_event(namespace=namespace, field_selector=field_selector, limit=10)
        recent_events = []
        if events and events.items:
                sorted_events = sorted(events.items, key=lambda e: e.last_timestamp or e.metadata.creation_timestamp or datetime(MINYEAR, 1, 1, tzinfo=timezone.utc), reverse=True)
                for event in sorted_events:
                    event_time = event.last_timestamp or event.metadata.creation_timestamp
                    if event_time and event_time >= since_time: recent_events.append({"time": event_time.isoformat(),"type": event.type,"reason": event.reason,"message": event.message,"count": event.count})
                    if len(recent_events) >= 10 or (event_time and event_time < since_time): break
        return recent_events
    except ApiException as e:
        if e.status != 403: logging.warning(f"Could not list events for pod {namespace}/{pod_name}: {e.status} {e.reason}")
        return []
    except Exception as e: logging.error(f"Unexpected error listing events for pod {namespace}/{pod_name}: {e}", exc_info=True); return []
def format_k8s_context(pod_info, node_info, pod_events): # ... (Giữ nguyên) ...
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
                problematic_conditions = [f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})" for ctype, cinfo in pod_info['conditions'].items() if cinfo.get('status') != 'True']
                if problematic_conditions: context_str += f"  Điều kiện Pod bất thường: {', '.join(problematic_conditions)}\n"
    if node_info:
        context_str += f"Node: {node_info['name']}\n"
        if node_info.get('conditions'):
                problematic_conditions = [f"{ctype}({cinfo['status']}-{cinfo.get('reason','N/A')})" for ctype, cinfo in node_info['conditions'].items() if cinfo.get('status') != ('True' if ctype == 'Ready' else 'False')]
                if problematic_conditions: context_str += f"  Điều kiện Node bất thường: {', '.join(problematic_conditions)}\n"
    if pod_events:
        context_str += "Sự kiện Pod gần đây (tối đa 10):\n"
        for event in pod_events: context_str += f"  - [{event['time']}] {event['type']} {event['reason']} (x{event.get('count',1)}): {event['message'][:150]}\n"
    context_str += "--- Kết thúc ngữ cảnh ---\n"; return context_str

# --- Hàm Lấy danh sách namespace động từ K8s ---
def get_active_namespaces():
    active_namespaces = []
    try:
        all_namespaces = k8s_core_v1.list_namespace(watch=False, timeout_seconds=60)
        for ns in all_namespaces.items:
            if ns.status.phase == "Active":
                active_namespaces.append(ns.metadata.name)
        logging.info(f"Found {len(active_namespaces)} active namespaces in cluster.")
    except ApiException as e: logging.error(f"API Error listing namespaces: {e.status} {e.reason}.")
    except Exception as e: logging.error(f"Unexpected error listing namespaces: {e}", exc_info=True)
    return active_namespaces

# --- HÀM MỚI: Cập nhật danh sách namespace trong DB ---
def update_available_namespaces_in_db(namespaces):
    """Cập nhật bảng available_namespaces với danh sách mới."""
    if not namespaces: return
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            cursor = conn.cursor()
            # Xóa các namespace cũ không còn active (có thể không cần thiết)
            # cursor.execute("DELETE FROM available_namespaces WHERE name NOT IN ({})".format(','.join('?'*len(namespaces))), tuple(namespaces))
            # Thêm hoặc cập nhật namespace mới
            for ns in namespaces:
                cursor.execute("INSERT OR REPLACE INTO available_namespaces (name, last_seen) VALUES (?, ?)", (ns, timestamp))
            logging.info(f"Updated available_namespaces table in DB with {len(namespaces)} namespaces.")
    except sqlite3.Error as e:
        logging.error(f"Database error updating available namespaces: {e}")
    except Exception as e:
        logging.error(f"Unexpected error updating available namespaces: {e}", exc_info=True)

# --- HÀM MỚI: Đọc danh sách namespace cần giám sát từ DB ---
def get_monitored_namespaces_from_db():
    """Đọc cấu hình monitored_namespaces từ bảng agent_config."""
    default_namespaces = [ns.strip() for ns in DEFAULT_MONITORED_NAMESPACES.split(',') if ns.strip()]
    conn = get_db_connection()
    if conn is None:
        logging.warning("DB connection failed, using default monitored namespaces.")
        return default_namespaces
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM agent_config WHERE key = 'monitored_namespaces'")
        result = cursor.fetchone()
        conn.close()
        if result and result['value']:
            # Parse chuỗi JSON hoặc chuỗi phẩy cách
            try:
                # Ưu tiên parse JSON nếu portal lưu dạng JSON array
                monitored = json.loads(result['value'])
                if isinstance(monitored, list):
                        logging.info(f"Read monitored namespaces from DB (JSON): {monitored}")
                        return monitored
            except json.JSONDecodeError:
                # Nếu không phải JSON, thử parse dạng phẩy cách
                monitored = [ns.strip() for ns in result['value'].split(',') if ns.strip()]
                logging.info(f"Read monitored namespaces from DB (CSV): {monitored}")
                return monitored
        else:
            logging.warning("No monitored_namespaces config found in DB. Using default.")
            return default_namespaces
    except sqlite3.Error as e:
        logging.error(f"Database error reading monitored_namespaces: {e}")
        if conn: conn.close()
        return default_namespaces # Trả về default nếu lỗi DB
    except Exception as e:
            logging.error(f"Unexpected error reading monitored_namespaces: {e}", exc_info=True)
            if conn: conn.close()
            return default_namespaces


# --- Hàm Quét Loki (Giữ nguyên) ---
def scan_loki_for_suspicious_logs(start_time, end_time, namespaces_to_scan): # ... (Giữ nguyên) ...
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range";
    if not namespaces_to_scan: logging.info("No namespaces to scan in Loki."); return {}
    log_levels_all = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]; scan_level_index = -1
    try: scan_level_index = log_levels_all.index(LOKI_SCAN_MIN_LEVEL.upper())
    except ValueError: logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {LOKI_SCAN_MIN_LEVEL}. Defaulting to WARNING."); scan_level_index = log_levels_all.index("WARNING")
    levels_to_scan = log_levels_all[scan_level_index:]
    namespace_regex = "|".join(namespaces_to_scan)
    keywords_to_find = levels_to_scan + ["fail", "crash", "exception", "panic", "fatal"]
    escaped_keywords = [re.escape(k) for k in keywords_to_find]; regex_pattern = "(?i)(" + "|".join(escaped_keywords) + ")"
    logql_query = f'{{namespace=~"{namespace_regex}"}} |~ `{regex_pattern}`'; query_limit_scan = 2000
    params = {'query': logql_query,'start': int(start_time.timestamp() * 1e9),'end': int(end_time.timestamp() * 1e9),'limit': query_limit_scan,'direction': 'forward'}
    logging.info(f"Scanning Loki for suspicious logs (Level >= {LOKI_SCAN_MIN_LEVEL} or keywords) in {len(namespaces_to_scan)} namespaces: {logql_query[:200]}..."); suspicious_logs_by_pod = {}
    try:
        headers = {'Accept': 'application/json'}; response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=60); response.raise_for_status(); data = response.json()
        if 'data' in data and 'result' in data['data']:
            count = 0
            for stream in data['data']['result']:
                labels = stream.get('stream', {}); ns = labels.get('namespace'); pod_name = labels.get('pod')
                if not ns or not pod_name: continue
                pod_key = f"{ns}/{pod_name}"
                if pod_key not in suspicious_logs_by_pod: suspicious_logs_by_pod[pod_key] = []
                for timestamp_ns, log_line in stream['values']:
                    log_entry = {"timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc), "message": log_line.strip(), "labels": labels}
                    suspicious_logs_by_pod[pod_key].append(log_entry); count += 1
            logging.info(f"Loki scan found {count} suspicious log entries across {len(suspicious_logs_by_pod)} pods.")
        else: logging.info("Loki scan found no suspicious log entries.")
        return suspicious_logs_by_pod
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 400: error_detail = e.response.text[:500]; logging.error(f"Error scanning Loki (400 Bad Request): Invalid LogQL query? Query: '{logql_query}'. Loki Response: {error_detail}")
        else: logging.error(f"Error scanning Loki (HTTP Error {e.response.status_code}): {e}")
        return {}
    except requests.exceptions.RequestException as e: logging.error(f"Error scanning Loki: {e}"); return {}
    except json.JSONDecodeError as e: logging.error(f"Error decoding Loki scan response: {e}"); return {}
    except Exception as e: logging.error(f"Unexpected error during Loki scan: {e}", exc_info=True); return {}

# --- Hàm Quét K8s (Giữ nguyên) ---
def scan_kubernetes_for_issues(namespaces_to_scan): # ... (Giữ nguyên) ...
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
                        if cs.state:
                            if cs.state.waiting and cs.state.waiting.reason in ["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull"]: issue_found = True; reason = f"Container '{cs.name}' đang ở trạng thái Waiting với lý do '{cs.state.waiting.reason}'"; break
                            if cs.state.terminated and cs.state.terminated.reason in ["OOMKilled", "Error", "ContainerCannotRun"]:
                                    if pod.spec.restart_policy != "Always" or (cs.state.terminated.finished_at and (datetime.now(timezone.utc) - cs.state.terminated.finished_at) < timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)): issue_found = True; reason = f"Container '{cs.name}' bị Terminated với lý do '{cs.state.terminated.reason}'"; break
                if issue_found:
                    logging.warning(f"Phát hiện pod có vấn đề tiềm ẩn (K8s Scan): {pod_key}. Lý do: {reason}")
                    if pod_key not in problematic_pods: problematic_pods[pod_key] = {"namespace": ns, "pod_name": pod.metadata.name, "reason": f"K8s: {reason}"}
        except ApiException as e: logging.error(f"API Error scanning namespace {ns}: {e.status} {e.reason}")
        except Exception as e: logging.error(f"Unexpected error scanning namespace {ns}: {e}", exc_info=True)
    logging.info(f"Finished K8s scan. Found {len(problematic_pods)} potentially problematic pods from K8s state.")
    return problematic_pods

# --- Hàm Query Loki cho pod (Giữ nguyên) ---
def query_loki_for_pod(namespace, pod_name, start_time, end_time): # ... (Giữ nguyên) ...
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range"; logql_query = f'{{namespace="{namespace}", pod="{pod_name}"}}'
    params = {'query': logql_query, 'start': int(start_time.timestamp() * 1e9), 'end': int(end_time.timestamp() * 1e9), 'limit': LOKI_QUERY_LIMIT, 'direction': 'forward'}
    logging.info(f"Querying Loki for pod '{namespace}/{pod_name}' from {start_time} to {end_time}")
    try:
        headers = {'Accept': 'application/json'}; response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=45); response.raise_for_status(); data = response.json()
        if 'data' in data and 'result' in data['data']:
            log_entries = [];
            for stream in data['data']['result']:
                for timestamp_ns, log_line in stream['values']: log_entries.append({"timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc), "message": log_line.strip(), "labels": stream.get('stream', {})})
            log_entries.sort(key=lambda x: x['timestamp']); logging.info(f"Received {len(log_entries)} log entries from Loki for pod '{namespace}/{pod_name}'."); return log_entries
        else: logging.warning(f"No 'result' data found in Loki response for pod '{namespace}/{pod_name}'."); return []
    except requests.exceptions.RequestException as e: logging.error(f"Error querying Loki for pod '{namespace}/{pod_name}': {e}"); return []
    except json.JSONDecodeError as e: logging.error(f"Error decoding Loki JSON response for pod '{namespace}/{pod_name}': {e}"); return []
    except Exception as e: logging.error(f"Unexpected error querying Loki for pod '{namespace}/{pod_name}': {e}", exc_info=True); return []

# --- Hàm lọc log (Giữ nguyên) ---
def preprocess_and_filter(log_entries): # ... (Giữ nguyên) ...
    filtered_logs = []; log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]; min_level_index = -1
    try: min_level_index = log_levels.index(LOKI_SCAN_MIN_LEVEL.upper())
    except ValueError: logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {LOKI_SCAN_MIN_LEVEL}. Defaulting to WARNING."); min_level_index = log_levels.index("WARNING")
    keywords_indicating_problem = ["FAIL", "ERROR", "CRASH", "EXCEPTION", "UNAVAILABLE", "FATAL", "PANIC"]
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


# --- Hàm phân tích (Giữ nguyên) ---
def analyze_incident(log_batch, k8s_context=""): # ... (Giữ nguyên) ...
    global model_calls_counter; model_calls_counter += 1
    if not log_batch and not k8s_context: logging.warning("analyze_incident called with no logs and no context. Skipping."); return None
    namespace = "unknown"; pod_name = "unknown"
    if log_batch: labels = log_batch[0].get('labels', {}); namespace = labels.get('namespace', 'unknown'); pod_name = labels.get('pod', 'unknown_pod')
    elif k8s_context: match_ns = re.search(r"Pod: (.*?)/", k8s_context); match_pod = re.search(r"Pod: .*?/(.*?)\n", k8s_context);
    if match_ns: namespace = match_ns.group(1);
    if match_pod: pod_name = match_pod.group(1)
    log_text = "N/A"
    if log_batch: limited_logs = [f"[{entry['timestamp'].isoformat()}] {entry['message'][:500]}" for entry in log_batch[:15]]; log_text = "\n".join(limited_logs)
    try: final_prompt = PROMPT_TEMPLATE.format(namespace=namespace, pod_name=pod_name, k8s_context=k8s_context[:10000], log_text=log_text[:20000])
    except KeyError as e: logging.error(f"Missing placeholder in PROMPT_TEMPLATE: {e}. Using default."); final_prompt = f"Phân tích pod {namespace}/{pod_name}. Context: {k8s_context[:10000]}. Logs: {log_text[:20000]}. Trả lời JSON."
    analysis_result = None; logging.info(f"[DEBUG] Checking analysis endpoint. USE_LOCAL_MODEL = {USE_LOCAL_MODEL} (Type: {type(USE_LOCAL_MODEL)})")
    if USE_LOCAL_MODEL:
        if LOCAL_GEMINI_ENDPOINT_URL:
            logging.info(f"Attempting to call local endpoint: {LOCAL_GEMINI_ENDPOINT_URL} for pod {namespace}/{pod_name}")
            try:
                response = requests.post( LOCAL_GEMINI_ENDPOINT_URL, json={"prompt": final_prompt}, timeout=120); response.raise_for_status(); analysis_result = response.json(); logging.info(f"Received response from local endpoint: {analysis_result}")
                if "severity" not in analysis_result: analysis_result["severity"] = "WARNING"
                if "summary" not in analysis_result: analysis_result["summary"] = "Local model không cung cấp tóm tắt."
            except requests.exceptions.RequestException as e: logging.error(f"Error calling local AI endpoint: {e}"); analysis_result = {"severity": "ERROR", "summary": f"Lỗi kết nối đến local AI service: {e}"}
            except json.JSONDecodeError as e: logging.error(f"Error decoding JSON response from local endpoint: {e}. Response: {response.text[:500]}"); analysis_result = {"severity": "ERROR", "summary": f"Local AI service trả về không phải JSON: {response.text[:200]}"}
            except Exception as e: logging.error(f"Unexpected error with local AI endpoint: {e}", exc_info=True); analysis_result = {"severity": "ERROR", "summary": f"Lỗi không xác định với local AI service: {e}"}
        else: logging.error("USE_LOCAL_MODEL is true, but LOCAL_GEMINI_ENDPOINT is not configured."); analysis_result = {"severity": "ERROR", "summary": "Lỗi cấu hình: Đã bật dùng model local nhưng thiếu URL endpoint."}
    elif gemini_model:
        logging.info(f"Attempting to call Google Gemini API for pod {namespace}/{pod_name}")
        try:
            response = gemini_model.generate_content(final_prompt, generation_config=genai.types.GenerationConfig(temperature=0.2, max_output_tokens=300), request_options={'timeout': 90})
            if not response.parts: logging.warning("Gemini response has no parts."); return None
            response_text = response.text.strip(); logging.info(f"Received response from Gemini (raw): {response_text}")
            cleaned_response_text = response_text
            if cleaned_response_text.startswith("```json"): cleaned_response_text = cleaned_response_text.strip("```json").strip("`").strip()
            elif cleaned_response_text.startswith("```"): cleaned_response_text = cleaned_response_text.strip("```").strip()
            match = re.search(r'\{.*\}', cleaned_response_text, re.DOTALL); json_string_to_parse = match.group(0) if match else cleaned_response_text
            try:
                analysis_result = json.loads(json_string_to_parse)
                if "severity" not in analysis_result: analysis_result["severity"] = "WARNING"
                if "summary" not in analysis_result: analysis_result["summary"] = "Không có tóm tắt."
                logging.info(f"Successfully parsed Gemini JSON: {analysis_result}")
            except json.JSONDecodeError as json_err:
                logging.warning(f"Failed to decode Gemini response as JSON: {json_err}. Raw response: {response_text}")
                severity = "WARNING"; summary_vi = f"Phản hồi Gemini không phải JSON hợp lệ ({json_err}): {response_text[:200]}"
                if "CRITICAL" in response_text.upper(): severity = "CRITICAL"
                elif "ERROR" in response_text.upper(): severity = "ERROR"
                analysis_result = {"severity": severity, "summary": summary_vi}
        except Exception as e: logging.error(f"Error calling Gemini API: {e}", exc_info=True); analysis_result = {"severity": "ERROR", "summary": f"Lỗi gọi Gemini API: {e}"}
    else: logging.warning("No analysis endpoint available (local or remote). Skipping analysis."); analysis_result = {"severity": "INFO", "summary": "Phân tích bị bỏ qua do thiếu cấu hình endpoint."}
    return analysis_result

# --- Hàm gửi cảnh báo Telegram (Giữ nguyên) ---
def send_telegram_alert(message): # ... (Giữ nguyên) ...
    global telegram_alerts_counter; telegram_alerts_counter += 1
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: logging.warning("Telegram Bot Token or Chat ID is not configured. Skipping alert."); return
    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"; max_len = 4096; truncated_message = message[:max_len-50] + "..." if len(message) > max_len else message
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': truncated_message, 'parse_mode': 'Markdown'}
    try: response = requests.post(telegram_api_url, json=payload, timeout=10); response.raise_for_status(); logging.info(f"Sent alert to Telegram. Response: {response.json()}")
    except requests.exceptions.RequestException as e: logging.error(f"Error sending Telegram alert: {e}");
    except Exception as e: logging.error(f"An unexpected error occurred during Telegram send: {e}", exc_info=True)


# --- Vòng lặp chính (Sửa để dùng list namespace động từ DB) ---
def main_loop():
    recently_alerted_pods = {}
    last_namespace_refresh_time = time.time() - NAMESPACE_REFRESH_INTERVAL_SECONDS - 1 # Đảm bảo quét lần đầu

    while True:
        start_cycle_time = datetime.now(timezone.utc)
        logging.info("--- Starting new monitoring cycle (Parallel Scan - DB Configured Namespaces) ---")

        # --- THAY ĐỔI: Lấy danh sách namespace cần giám sát ---
        # Lấy danh sách namespace động từ K8s định kỳ và cập nhật DB
        current_time = time.time()
        if current_time - last_namespace_refresh_time >= NAMESPACE_REFRESH_INTERVAL_SECONDS:
                logging.info("Refreshing list of active namespaces from Kubernetes API...")
                all_active_namespaces = get_active_namespaces()
                update_available_namespaces_in_db(all_active_namespaces)
                last_namespace_refresh_time = current_time

        # Đọc danh sách namespace cần theo dõi từ DB
        monitored_namespaces = get_monitored_namespaces_from_db()
        if not monitored_namespaces:
                logging.warning("No namespaces configured for monitoring in DB. Skipping cycle.")
                time.sleep(SCAN_INTERVAL_SECONDS)
                continue
        logging.info(f"Currently monitoring {len(monitored_namespaces)} namespaces based on DB config: {', '.join(monitored_namespaces)}")
        # --- KẾT THÚC THAY ĐỔI ---

        # 1. Quét K8s tìm pod có vấn đề (chỉ trong các namespace được theo dõi)
        k8s_problem_pods = scan_kubernetes_for_issues(monitored_namespaces)

        # 2. Quét Loki tìm log đáng ngờ (chỉ trong các namespace được theo dõi)
        loki_scan_end_time = start_cycle_time
        loki_scan_start_time = loki_scan_end_time - timedelta(minutes=LOKI_SCAN_RANGE_MINUTES)
        loki_suspicious_logs = scan_loki_for_suspicious_logs(loki_scan_start_time, loki_scan_end_time, monitored_namespaces)

        # 3. Gom danh sách pod cần điều tra (Giữ nguyên)
        pods_to_investigate = {}
        for pod_key, data in k8s_problem_pods.items():
            if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
            pods_to_investigate[pod_key]["reason"].append(data["reason"])
        for pod_key, logs in loki_suspicious_logs.items():
                if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
                pods_to_investigate[pod_key]["reason"].append(f"Loki: Phát hiện {len(logs)} log đáng ngờ (>= {LOKI_SCAN_MIN_LEVEL})")
                pods_to_investigate[pod_key]["logs"].extend(logs)
        logging.info(f"Total pods to investigate this cycle: {len(pods_to_investigate)}")

        # 4. Xử lý từng pod (Giữ nguyên logic)
        for pod_key, data in pods_to_investigate.items():
            # ... (Giữ nguyên toàn bộ logic xử lý pod, lấy context, lấy log, phân tích, gửi cảnh báo, cooldown, record_incident) ...
            namespace, pod_name = pod_key.split('/', 1); initial_reasons = "; ".join(data["reason"]); suspicious_logs_found = data["logs"]
            now_utc = datetime.now(timezone.utc)
            if pod_key in recently_alerted_pods:
                last_alert_time = recently_alerted_pods[pod_key]; cooldown_duration = timedelta(minutes=30)
                if now_utc < last_alert_time + cooldown_duration: logging.info(f"Pod {pod_key} is in cooldown period. Skipping analysis."); continue
                else: del recently_alerted_pods[pod_key]
            logging.info(f"Investigating pod: {pod_key} (Initial Reasons: {initial_reasons})")
            pod_info = get_pod_info(namespace, pod_name); node_info = None; pod_events = []
            if pod_info: node_info = get_node_info(pod_info.get('node_name')); pod_events = get_pod_events(namespace, pod_name, since_minutes=LOKI_DETAIL_LOG_RANGE_MINUTES + 5)
            k8s_context_str = format_k8s_context(pod_info, node_info, pod_events)
            logs_for_analysis = suspicious_logs_found
            if not logs_for_analysis:
                log_end_time = datetime.now(timezone.utc); log_start_time = log_end_time - timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
                detailed_logs = query_loki_for_pod(namespace, pod_name, log_start_time, log_end_time)
                logs_for_analysis = preprocess_and_filter(detailed_logs)
            analysis_start_time = time.time()
            analysis_result = analyze_incident(logs_for_analysis, k8s_context_str)
            analysis_duration = time.time() - analysis_start_time
            logging.info(f"Analysis for {pod_key} took {analysis_duration:.2f} seconds.")
            if analysis_result:
                severity = analysis_result.get("severity", "UNKNOWN").upper(); summary = analysis_result.get("summary", "N/A")
                logging.info(f"Analysis result for '{pod_key}': Severity={severity}, Summary={summary}")
                # --- THAY ĐỔI: Truyền prompt và response vào record_incident ---
                # Lấy prompt đã format (cần lưu lại từ hàm analyze_incident, hoặc tạo lại)
                try:
                        formatted_prompt = PROMPT_TEMPLATE.format(
                            namespace=namespace, pod_name=pod_name,
                            k8s_context=k8s_context_str[:10000],
                            log_text="\n".join([f"[{entry['timestamp'].isoformat()}] {entry['message'][:500]}" for entry in logs_for_analysis[:15]]) if logs_for_analysis else "N/A"
                        )
                except KeyError: formatted_prompt = "Lỗi tạo prompt từ template"
                # Lấy raw response (cần sửa hàm analyze_incident để trả về cả raw_response)
                # Tạm thời để là kết quả JSON phân tích được
                raw_response_text = json.dumps(analysis_result) if analysis_result else None
                # --- KẾT THÚC THAY ĐỔI ---

                # Ghi lại tất cả kết quả phân tích vào DB
                record_incident(pod_key, severity, summary, initial_reasons, k8s_context_str,
                                "\n".join([f"- `{log['message'][:150]}`" for log in logs_for_analysis[:5]]) if logs_for_analysis else "-",
                                formatted_prompt, # Lưu prompt
                                raw_response_text # Lưu kết quả JSON
                                )

                if severity in ALERT_SEVERITY_LEVELS:
                    sample_logs = "\n".join([f"- `{log['message'][:150]}`" for log in logs_for_analysis[:5]])
                    alert_time_hcm = datetime.now(HCM_TZ); time_format = '%Y-%m-%d %H:%M:%S %Z'
                    alert_message = f"""🚨 *Cảnh báo K8s/Log (Pod: {pod_key})* 🚨\n*Mức độ:* `{severity}`\n*Tóm tắt:* {summary}\n*Lý do phát hiện ban đầu:* {initial_reasons}\n*Thời gian phát hiện:* `{alert_time_hcm.strftime(time_format)}`\n*Log mẫu (nếu có):*\n{sample_logs if sample_logs else "- Không có log mẫu liên quan."}\n\n_Vui lòng kiểm tra trạng thái pod/node/events và log trên Loki để biết thêm chi tiết._"""
                    send_telegram_alert(alert_message)
                    recently_alerted_pods[pod_key] = now_utc
            else: logging.warning(f"Analysis failed or returned no result for pod '{pod_key}'.")
            time.sleep(5)


        # Ngủ đến hết chu kỳ
        cycle_duration = (datetime.now(timezone.utc) - start_cycle_time).total_seconds()
        sleep_time = max(0, SCAN_INTERVAL_SECONDS - cycle_duration)
        logging.info(f"--- Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
        time.sleep(sleep_time)

if __name__ == "__main__":
    if not init_db(): logging.error("Failed to initialize database. Exiting."); exit(1)
    stats_thread = threading.Thread(target=periodic_stat_update, daemon=True); stats_thread.start(); logging.info("Started periodic stats update thread.")
    logging.info(f"Starting Kubernetes Log Monitoring Agent (Parallel Scan - DB Configured Namespaces)") # Cập nhật log
    logging.info(f"Loki scan minimum level: {LOKI_SCAN_MIN_LEVEL}")
    logging.info(f"Alerting for severity levels: {ALERT_SEVERITY_LEVELS_STR}")
    logging.info(f"Restart count threshold: {RESTART_COUNT_THRESHOLD}")
    # Cập nhật log khởi động
    if USE_LOCAL_MODEL:
            if LOCAL_GEMINI_ENDPOINT_URL: logging.info(f"Using local analysis endpoint: {LOCAL_GEMINI_ENDPOINT_URL}")
            else: logging.error("USE_LOCAL_MODEL is true, but LOCAL_GEMINI_ENDPOINT is not configured!")
    elif gemini_model: logging.info(f"Using Google Gemini model: {GEMINI_MODEL_NAME}")
    else: logging.warning("No analysis endpoint configured (Gemini API key might be missing or invalid)!")

    # Bỏ kiểm tra K8S_NAMESPACES
    if not all([LOKI_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
            logging.error("One or more required environment variables are missing (LOKI_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). Ensure they are set. Exiting.")
            exit(1)
    # Kiểm tra điều kiện cấu hình endpoint
    if not LOCAL_GEMINI_ENDPOINT_URL and not gemini_model:
            logging.error("No usable analysis endpoint configured. Either set USE_LOCAL_MODEL=true and provide LOCAL_GEMINI_ENDPOINT, or set USE_LOCAL_MODEL=false and provide GEMINI_API_KEY. Exiting.")
            exit(1)

    try: main_loop()
    except KeyboardInterrupt: logging.info("Agent stopped by user.")
    finally: logging.info("Performing final stats update before exiting..."); update_daily_stats(); logging.info("Agent shutdown complete.")

