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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") # Cần key này để chạy Gemini thật
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", 30))
LOKI_SCAN_RANGE_MINUTES = int(os.environ.get("LOKI_SCAN_RANGE_MINUTES", 1))
LOKI_DETAIL_LOG_RANGE_MINUTES = int(os.environ.get("LOKI_DETAIL_LOG_RANGE_MINUTES", 30))
LOKI_QUERY_LIMIT = int(os.environ.get("LOKI_QUERY_LIMIT", 500))
K8S_NAMESPACES_STR = os.environ.get("K8S_NAMESPACES", "kube-system")
K8S_NAMESPACES = [ns.strip() for ns in K8S_NAMESPACES_STR.split(',') if ns.strip()]
LOKI_SCAN_MIN_LEVEL = os.environ.get("LOKI_SCAN_MIN_LEVEL", "INFO") # Đặt INFO để lấy nhiều dữ liệu hơn
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-1.5-flash")
ALERT_SEVERITY_LEVELS_STR = os.environ.get("ALERT_SEVERITY_LEVELS", "WARNING,ERROR,CRITICAL")
ALERT_SEVERITY_LEVELS = [level.strip().upper() for level in ALERT_SEVERITY_LEVELS_STR.split(',') if level.strip()]
RESTART_COUNT_THRESHOLD = int(os.environ.get("RESTART_COUNT_THRESHOLD", 5))
DB_PATH = os.environ.get("DB_PATH", "/data/agent_stats.db")
STATS_UPDATE_INTERVAL_SECONDS = int(os.environ.get("STATS_UPDATE_INTERVAL_SECONDS", 300))
# Không cần endpoint local và công tắc nữa vì chỉ dùng Gemini thật
# LOCAL_GEMINI_ENDPOINT_URL = os.environ.get("LOCAL_GEMINI_ENDPOINT")
# USE_LOCAL_MODEL_STR = os.environ.get("USE_LOCAL_MODEL", "false").lower()
# USE_LOCAL_MODEL = USE_LOCAL_MODEL_STR == "true"
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

# --- Cấu hình Gemini Client (Bắt buộc phải có key) ---
gemini_model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        logging.info(f"Using Google Gemini model: {GEMINI_MODEL_NAME}")
    except Exception as e:
        logging.error(f"Failed to configure Gemini client: {e}", exc_info=True)
        # Dừng agent nếu không config được Gemini
        exit(1)
else:
    logging.error("GEMINI_API_KEY is not set. Agent cannot run without an analysis model. Exiting.")
    exit(1)


# --- Logic Database ---
model_calls_counter = 0; telegram_alerts_counter = 0; db_lock = threading.Lock()
def init_db():
    db_dir = os.path.dirname(DB_PATH);
    if not os.path.exists(db_dir):
        try: os.makedirs(db_dir); logging.info(f"Created directory for database: {db_dir}")
        except OSError as e: logging.error(f"Could not create directory {db_dir}: {e}"); return False
    try:
        with db_lock:
            conn = sqlite3.connect(DB_PATH, timeout=10); cursor = conn.cursor()
            # --- BẮT ĐẦU THAY ĐỔI: Thêm cột vào bảng incidents ---
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
                    input_prompt TEXT,       -- Lưu prompt đã gửi cho AI
                    raw_ai_response TEXT   -- Lưu câu trả lời thô từ AI
                )
            ''')
            # Kiểm tra và thêm cột mới nếu chưa có (để tương thích ngược)
            cursor.execute("PRAGMA table_info(incidents)")
            columns = [column[1] for column in cursor.fetchall()]
            if 'input_prompt' not in columns:
                logging.warning("Adding column 'input_prompt' to incidents table.")
                cursor.execute("ALTER TABLE incidents ADD COLUMN input_prompt TEXT")
            if 'raw_ai_response' not in columns:
                logging.warning("Adding column 'raw_ai_response' to incidents table.")
                cursor.execute("ALTER TABLE incidents ADD COLUMN raw_ai_response TEXT")
            # --- KẾT THÚC THAY ĐỔI ---

            cursor.execute(''' CREATE TABLE IF NOT EXISTS daily_stats ( date TEXT PRIMARY KEY, model_calls INTEGER DEFAULT 0, telegram_alerts INTEGER DEFAULT 0, incident_count INTEGER DEFAULT 0 ) ''')
            cursor.execute("PRAGMA table_info(daily_stats)")
            columns_stats = [column[1] for column in cursor.fetchall()]
            if 'model_calls' not in columns_stats and 'gemini_calls' in columns_stats: cursor.execute("ALTER TABLE daily_stats RENAME COLUMN gemini_calls TO model_calls")
            elif 'model_calls' not in columns_stats: cursor.execute("ALTER TABLE daily_stats ADD COLUMN model_calls INTEGER DEFAULT 0")
            conn.commit(); conn.close(); logging.info(f"Database initialized successfully at {DB_PATH}"); return True
    except sqlite3.Error as e: logging.error(f"Database error during initialization: {e}"); return False
    except Exception as e: logging.error(f"Unexpected error during DB initialization: {e}", exc_info=True); return False

# --- BẮT ĐẦU THAY ĐỔI: Cập nhật hàm record_incident ---
def record_incident(pod_key, severity, summary, initial_reasons, k8s_context, sample_logs, input_prompt=None, raw_ai_response=None):
    """Ghi lại một sự cố vào database, bao gồm cả input và output của AI."""
    timestamp_str = datetime.now(timezone.utc).isoformat()
    try:
        with db_lock:
            conn = sqlite3.connect(DB_PATH, timeout=10); cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO incidents (timestamp, pod_key, severity, summary, initial_reasons, k8s_context, sample_logs, input_prompt, raw_ai_response)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (timestamp_str, pod_key, severity, summary, initial_reasons, k8s_context, sample_logs, input_prompt, raw_ai_response))

            # Cập nhật số lượng incident trong ngày (chỉ khi severity đáng cảnh báo?)
            # Hoặc cứ ghi nhận mọi severity để có dữ liệu đa dạng? -> Quyết định: Ghi nhận mọi severity vào incidents, chỉ tăng count nếu đáng báo động
            if severity in ALERT_SEVERITY_LEVELS:
                today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                cursor.execute(''' INSERT INTO daily_stats (date, incident_count) VALUES (?, 1)
                                    ON CONFLICT(date) DO UPDATE SET incident_count = incident_count + 1 ''', (today_str,))

            conn.commit(); conn.close(); logging.info(f"Recorded data for {pod_key} with severity {severity}")
    except sqlite3.Error as e: logging.error(f"Database error recording incident for {pod_key}: {e}")
    except Exception as e: logging.error(f"Unexpected error recording incident for {pod_key}: {e}", exc_info=True)
# --- KẾT THÚC THAY ĐỔI ---

def update_daily_stats(): # ... (Giữ nguyên) ...
    global model_calls_counter, telegram_alerts_counter
    if model_calls_counter == 0 and telegram_alerts_counter == 0: return
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d'); calls_to_add = model_calls_counter; alerts_to_add = telegram_alerts_counter
    try:
        with db_lock:
            conn = sqlite3.connect(DB_PATH, timeout=10); cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO daily_stats (date) VALUES (?)', (today_str,))
            cursor.execute(''' UPDATE daily_stats SET model_calls = model_calls + ?, telegram_alerts = telegram_alerts + ? WHERE date = ? ''', (calls_to_add, alerts_to_add, today_str))
            conn.commit(); conn.close()
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

# --- Hàm Quét Loki (Giữ nguyên) ---
def scan_loki_for_suspicious_logs(start_time, end_time): # ... (Giữ nguyên) ...
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range";
    if not K8S_NAMESPACES: logging.error("No namespaces configured."); return {}
    log_levels_all = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]; scan_level_index = -1
    try: scan_level_index = log_levels_all.index(LOKI_SCAN_MIN_LEVEL.upper())
    except ValueError: logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {LOKI_SCAN_MIN_LEVEL}. Defaulting to WARNING."); scan_level_index = log_levels_all.index("WARNING")
    levels_to_scan = log_levels_all[scan_level_index:]
    namespace_regex = "|".join(K8S_NAMESPACES)
    keywords_to_find = levels_to_scan + ["fail", "crash", "exception", "panic", "fatal"]
    escaped_keywords = [re.escape(k) for k in keywords_to_find]; regex_pattern = "(?i)(" + "|".join(escaped_keywords) + ")"
    logql_query = f'{{namespace=~"{namespace_regex}"}} |~ `{regex_pattern}`'; query_limit_scan = 2000
    params = {'query': logql_query,'start': int(start_time.timestamp() * 1e9),'end': int(end_time.timestamp() * 1e9),'limit': query_limit_scan,'direction': 'forward'}
    logging.info(f"Scanning Loki for suspicious logs (Level >= {LOKI_SCAN_MIN_LEVEL} or keywords): {logql_query[:200]}..."); suspicious_logs_by_pod = {}
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
def scan_kubernetes_for_issues(): # ... (Giữ nguyên) ...
    problematic_pods = {}
    logging.info(f"Scanning Kubernetes namespaces {K8S_NAMESPACES_STR} for problematic pods...")
    for ns in K8S_NAMESPACES:
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


# --- Hàm phân tích (Chỉ còn gọi Gemini thật) ---
def analyze_incident(log_batch, k8s_context=""):
    """Phân tích log và context K8s bằng Google Gemini API."""
    global model_calls_counter
    model_calls_counter += 1

    if not gemini_model: # Kiểm tra xem Gemini client đã được cấu hình chưa
            logging.warning("Gemini model not configured. Skipping analysis.")
            return {"severity": "INFO", "summary": "Phân tích bị bỏ qua do thiếu cấu hình Gemini."}

    if not log_batch and not k8s_context:
        logging.warning("analyze_incident called with no logs and no context. Skipping.")
        return None

    # Xác định thông tin pod
    namespace = "unknown"; pod_name = "unknown"
    if log_batch:
            labels = log_batch[0].get('labels', {})
            namespace = labels.get('namespace', 'unknown')
            pod_name = labels.get('pod', 'unknown_pod')
    elif k8s_context:
            match_ns = re.search(r"Pod: (.*?)/", k8s_context); match_pod = re.search(r"Pod: .*?/(.*?)\n", k8s_context)
            if match_ns: namespace = match_ns.group(1)
            if match_pod: pod_name = match_pod.group(1)

    log_text = "N/A"
    if log_batch:
            limited_logs = [f"[{entry['timestamp'].isoformat()}] {entry['message'][:500]}" for entry in log_batch[:15]]
            log_text = "\n".join(limited_logs)

    # Tạo prompt cuối cùng bằng cách format template
    try:
        final_prompt = PROMPT_TEMPLATE.format(
            namespace=namespace,
            pod_name=pod_name,
            k8s_context=k8s_context[:10000],
            log_text=log_text[:20000]
        )
    except KeyError as e:
            logging.error(f"Missing placeholder in PROMPT_TEMPLATE: {e}. Using default prompt structure.")
            final_prompt = f"Phân tích pod {namespace}/{pod_name}. Context: {k8s_context[:10000]}. Logs: {log_text[:20000]}. Trả lời JSON."

    analysis_result = None
    logging.info(f"Sending analysis request to Google Gemini API for pod {namespace}/{pod_name}")
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
    except Exception as e:
        logging.error(f"Error calling Gemini API: {e}", exc_info=True)
        analysis_result = {"severity": "ERROR", "summary": f"Lỗi gọi Gemini API: {e}"}

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


# --- Vòng lặp chính (Giữ nguyên logic gọi hàm analyze_incident) ---
def main_loop():
    recently_alerted_pods = {}
    while True:
        start_cycle_time = datetime.now(timezone.utc); logging.info("--- Starting new monitoring cycle (Parallel Scan - Gemini) ---") # Cập nhật log
        k8s_problem_pods = scan_kubernetes_for_issues()
        loki_scan_end_time = start_cycle_time; loki_scan_start_time = loki_scan_end_time - timedelta(minutes=LOKI_SCAN_RANGE_MINUTES)
        loki_suspicious_logs = scan_loki_for_suspicious_logs(loki_scan_start_time, loki_scan_end_time)
        pods_to_investigate = {}
        for pod_key, data in k8s_problem_pods.items():
            if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
            pods_to_investigate[pod_key]["reason"].append(data["reason"])
        for pod_key, logs in loki_suspicious_logs.items():
                if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
                pods_to_investigate[pod_key]["reason"].append(f"Loki: Phát hiện {len(logs)} log đáng ngờ (>= {LOKI_SCAN_MIN_LEVEL})")
                pods_to_investigate[pod_key]["logs"].extend(logs)
        logging.info(f"Total pods to investigate this cycle: {len(pods_to_investigate)}")

        for pod_key, data in pods_to_investigate.items():
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

            # Gọi hàm phân tích (chỉ gọi Gemini thật)
            analysis_start_time = time.time()
            analysis_result = analyze_incident(logs_for_analysis, k8s_context_str)
            analysis_duration = time.time() - analysis_start_time
            logging.info(f"Analysis for {pod_key} took {analysis_duration:.2f} seconds.")

            if analysis_result:
                severity = analysis_result.get("severity", "UNKNOWN").upper(); summary = analysis_result.get("summary", "N/A")
                # --- BẮT ĐẦU THAY ĐỔI: Gọi record_incident với prompt và response ---
                # Lấy prompt đã format (cần lưu lại từ hàm analyze_incident, hoặc tạo lại)
                # Tạm thời tạo lại để đơn giản
                try:
                        formatted_prompt = PROMPT_TEMPLATE.format(
                            namespace=namespace, pod_name=pod_name,
                            k8s_context=k8s_context_str[:10000],
                            log_text="\n".join([f"[{entry['timestamp'].isoformat()}] {entry['message'][:500]}" for entry in logs_for_analysis[:15]]) if logs_for_analysis else "N/A"
                        )
                except KeyError:
                        formatted_prompt = "Lỗi tạo prompt từ template"

                # Lấy raw response (cần sửa hàm analyze_incident để trả về cả raw_response)
                # Tạm thời để là None
                raw_response_text = None # Cần sửa analyze_incident để trả về cái này

                # Chỉ ghi vào DB nếu severity đạt ngưỡng cảnh báo HOẶC ở mức INFO trở lên (để thu thập data)
                # Quyết định: Ghi lại tất cả các kết quả phân tích để có dữ liệu huấn luyện đa dạng
                record_incident(pod_key, severity, summary, initial_reasons, k8s_context_str,
                                "\n".join([f"- `{log['message'][:150]}`" for log in logs_for_analysis[:5]]) if logs_for_analysis else "-",
                                formatted_prompt, # Lưu prompt
                                json.dumps(analysis_result) if analysis_result else None # Lưu kết quả JSON phân tích được
                                )
                # --- KẾT THÚC THAY ĐỔI ---

                if severity in ALERT_SEVERITY_LEVELS:
                    sample_logs = "\n".join([f"- `{log['message'][:150]}`" for log in logs_for_analysis[:5]])
                    alert_time_hcm = datetime.now(HCM_TZ); time_format = '%Y-%m-%d %H:%M:%S %Z'
                    alert_message = f"""🚨 *Cảnh báo K8s/Log (Pod: {pod_key})* 🚨\n*Mức độ:* `{severity}`\n*Tóm tắt:* {summary}\n*Lý do phát hiện ban đầu:* {initial_reasons}\n*Thời gian phát hiện:* `{alert_time_hcm.strftime(time_format)}`\n*Log mẫu (nếu có):*\n{sample_logs if sample_logs else "- Không có log mẫu liên quan."}\n\n_Vui lòng kiểm tra trạng thái pod/node/events và log trên Loki để biết thêm chi tiết._"""
                    send_telegram_alert(alert_message)
                    recently_alerted_pods[pod_key] = now_utc
            else: logging.warning(f"Analysis failed or returned no result for pod '{pod_key}'.")
            time.sleep(5)

        cycle_duration = (datetime.now(timezone.utc) - start_cycle_time).total_seconds()
        sleep_time = max(0, SCAN_INTERVAL_SECONDS - cycle_duration)
        logging.info(f"--- Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
        time.sleep(sleep_time)

if __name__ == "__main__":
    if not init_db(): logging.error("Failed to initialize database. Exiting."); exit(1)
    stats_thread = threading.Thread(target=periodic_stat_update, daemon=True); stats_thread.start(); logging.info("Started periodic stats update thread.")
    logging.info(f"Starting Kubernetes Log Monitoring Agent (Parallel Scan - Data Collection Mode) for namespaces: {K8S_NAMESPACES_STR}") # Cập nhật log
    logging.info(f"Loki scan minimum level: {LOKI_SCAN_MIN_LEVEL}")
    logging.info(f"Alerting for severity levels: {ALERT_SEVERITY_LEVELS_STR}")
    logging.info(f"Restart count threshold: {RESTART_COUNT_THRESHOLD}")
    if gemini_model: logging.info(f"Using Google Gemini model: {GEMINI_MODEL_NAME}") # Chỉ log nếu dùng Gemini
    else: logging.error("Gemini model could not be configured. Check API Key. Exiting."); exit(1)

    if not K8S_NAMESPACES: logging.error("K8S_NAMESPACES environment variable is not set or is empty. Exiting."); exit(1)
    if not all([LOKI_URL, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]): # Bắt buộc phải có Gemini Key
            logging.error("One or more required environment variables are missing (LOKI_URL, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). Ensure they are set. Exiting.")
            exit(1)
    try: main_loop()
    except KeyboardInterrupt: logging.info("Agent stopped by user.")
    finally: logging.info("Performing final stats update before exiting..."); update_daily_stats(); logging.info("Agent shutdown complete.")
