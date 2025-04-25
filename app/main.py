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


# --- Tải biến môi trường từ file .env (cho phát triển cục bộ) ---
load_dotenv()

# --- Cấu hình Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Tải cấu hình từ biến môi trường ---
LOKI_URL = os.environ.get("LOKI_URL", "http://loki-read.monitoring.svc.cluster.local:3100")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
# --- THAY ĐỔI: Cập nhật giá trị mặc định ---
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", 30)) # Mặc định 30s
# --- KẾT THÚC THAY ĐỔI ---
# Khoảng thời gian quét log Loki chung
LOKI_SCAN_RANGE_MINUTES = int(os.environ.get("LOKI_SCAN_RANGE_MINUTES", 1)) # Quét log 1 phút gần nhất
# Khoảng thời gian lấy log chi tiết cho pod có vấn đề
LOKI_DETAIL_LOG_RANGE_MINUTES = int(os.environ.get("LOKI_DETAIL_LOG_RANGE_MINUTES", 30)) # Lấy log 30 phút
LOKI_QUERY_LIMIT = int(os.environ.get("LOKI_QUERY_LIMIT", 500))
K8S_NAMESPACES_STR = os.environ.get("K8S_NAMESPACES", "kube-system")
K8S_NAMESPACES = [ns.strip() for ns in K8S_NAMESPACES_STR.split(',') if ns.strip()]
# Mức log tối thiểu để Loki scan tìm vấn đề
LOKI_SCAN_MIN_LEVEL = os.environ.get("LOKI_SCAN_MIN_LEVEL", "WARNING") # Chỉ quét tìm WARNING/ERROR trở lên
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-1.5-flash")
ALERT_SEVERITY_LEVELS_STR = os.environ.get("ALERT_SEVERITY_LEVELS", "WARNING,ERROR,CRITICAL") # Cảnh báo cả WARNING
ALERT_SEVERITY_LEVELS = [level.strip().upper() for level in ALERT_SEVERITY_LEVELS_STR.split(',') if level.strip()]
RESTART_COUNT_THRESHOLD = int(os.environ.get("RESTART_COUNT_THRESHOLD", 5))

try:
HCM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception as e:
logging.error(f"Could not load timezone 'Asia/Ho_Chi_Minh': {e}. Ensure timezone data is available.")
HCM_TZ = timezone.utc

# --- Cấu hình Kubernetes Client ---
try: config.load_incluster_config(); logging.info("Loaded in-cluster Kubernetes config.")
except config.ConfigException:
try: config.load_kube_config(); logging.info("Loaded local Kubernetes config (kubeconfig).")
except config.ConfigException: logging.error("Could not configure Kubernetes client. Exiting."); exit(1)

k8s_core_v1 = client.CoreV1Api()
k8s_apps_v1 = client.AppsV1Api()

# Cấu hình Gemini Client
if not GEMINI_API_KEY: logging.error("GEMINI_API_KEY is not set!"); exit(1)
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)

# --- Các hàm lấy thông tin Kubernetes ---
# (Giữ nguyên: get_pod_info, get_node_info, get_pod_events, format_k8s_context)
def get_pod_info(namespace, pod_name):
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

def get_node_info(node_name):
if not node_name: return None
try:
    node = k8s_core_v1.read_node(name=node_name)
    conditions = {cond.type: {"status": cond.status, "reason": cond.reason, "message": cond.message} for cond in node.status.conditions} if node.status.conditions else {}
    info = {"name": node.metadata.name,"conditions": conditions,"allocatable_cpu": node.status.allocatable.get('cpu', 'N/A'),"allocatable_memory": node.status.allocatable.get('memory', 'N/A'),"kubelet_version": node.status.node_info.kubelet_version}
    return info
except ApiException as e: logging.warning(f"Could not get node info for {node_name}: {e.status} {e.reason}"); return None
except Exception as e: logging.error(f"Unexpected error getting node info for {node_name}: {e}", exc_info=True); return None

def get_pod_events(namespace, pod_name, since_minutes=15):
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

# --- HÀM MỚI: Quét Loki tìm log đáng ngờ ---
def scan_loki_for_suspicious_logs(start_time, end_time):
"""Quét Loki tìm các log đạt ngưỡng LOKI_SCAN_MIN_LEVEL."""
loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range"
if not K8S_NAMESPACES: logging.error("No namespaces configured."); return {}

log_levels_all = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
scan_level_index = -1
try: scan_level_index = log_levels_all.index(LOKI_SCAN_MIN_LEVEL.upper())
except ValueError: logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {LOKI_SCAN_MIN_LEVEL}. Defaulting to WARNING."); scan_level_index = log_levels_all.index("WARNING")
levels_to_scan = log_levels_all[scan_level_index:]

namespace_regex = "|".join(K8S_NAMESPACES)
level_filters = [f'|~ "(?i){level}"' for level in levels_to_scan]
keyword_filters = ['|= "(?i)fail"', '|= "(?i)error"', '|= "(?i)crash"', '|= "(?i)exception"', '|= "(?i)panic"']
logql_query = f'{{namespace=~"{namespace_regex}"}} ({ "|".join(level_filters + keyword_filters) })'
query_limit_scan = 2000

params = {
    'query': logql_query,
    'start': int(start_time.timestamp() * 1e9),
    'end': int(end_time.timestamp() * 1e9),
    'limit': query_limit_scan,
    'direction': 'forward'
}
logging.info(f"Scanning Loki for suspicious logs (Level >= {LOKI_SCAN_MIN_LEVEL}): {logql_query[:200]}...")
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
                log_entry = {"timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc), "message": log_line.strip(), "labels": labels}
                suspicious_logs_by_pod[pod_key].append(log_entry); count += 1
        logging.info(f"Loki scan found {count} suspicious log entries across {len(suspicious_logs_by_pod)} pods.")
    else:
        logging.info("Loki scan found no suspicious log entries.")
    return suspicious_logs_by_pod
except requests.exceptions.RequestException as e: logging.error(f"Error scanning Loki: {e}"); return {}
except json.JSONDecodeError as e: logging.error(f"Error decoding Loki scan response: {e}"); return {}
except Exception as e: logging.error(f"Unexpected error during Loki scan: {e}", exc_info=True); return {}


# --- HÀM MỚI: Quét Kubernetes tìm Pod có vấn đề ---
def scan_kubernetes_for_issues():
"""Quét các namespace được cấu hình để tìm Pod có dấu hiệu bất thường."""
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


# --- Hàm Query Loki cho pod cụ thể ---
def query_loki_for_pod(namespace, pod_name, start_time, end_time):
loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range"
logql_query = f'{{namespace="{namespace}", pod="{pod_name}"}}'
params = {'query': logql_query, 'start': int(start_time.timestamp() * 1e9), 'end': int(end_time.timestamp() * 1e9), 'limit': LOKI_QUERY_LIMIT, 'direction': 'forward'}
logging.info(f"Querying Loki for pod '{namespace}/{pod_name}' from {start_time} to {end_time}")
try:
    headers = {'Accept': 'application/json'}
    response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=45); response.raise_for_status(); data = response.json()
    if 'data' in data and 'result' in data['data']:
        log_entries = []
        for stream in data['data']['result']:
            for timestamp_ns, log_line in stream['values']: log_entries.append({"timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc), "message": log_line.strip(), "labels": stream.get('stream', {})})
        log_entries.sort(key=lambda x: x['timestamp']); logging.info(f"Received {len(log_entries)} log entries from Loki for pod '{namespace}/{pod_name}'."); return log_entries
    else: logging.warning(f"No 'result' data found in Loki response for pod '{namespace}/{pod_name}'."); return []
except requests.exceptions.RequestException as e: logging.error(f"Error querying Loki for pod '{namespace}/{pod_name}': {e}"); return []
except json.JSONDecodeError as e: logging.error(f"Error decoding Loki JSON response for pod '{namespace}/{pod_name}': {e}"); return []
except Exception as e: logging.error(f"Unexpected error querying Loki for pod '{namespace}/{pod_name}': {e}", exc_info=True); return []


# --- Hàm tiền xử lý và lọc log ---
def preprocess_and_filter(log_entries):
filtered_logs = []
log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
min_level_index = -1
# --- THAY ĐỔI: Sử dụng LOKI_SCAN_MIN_LEVEL để lọc ---
try: min_level_index = log_levels.index(LOKI_SCAN_MIN_LEVEL.upper())
except ValueError: logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {LOKI_SCAN_MIN_LEVEL}. Defaulting to WARNING."); min_level_index = log_levels.index("WARNING")
# --- KẾT THÚC THAY ĐỔI ---
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


# --- Hàm tương tác với Gemini ---
def analyze_with_gemini(log_batch, k8s_context=""):
if not log_batch and not k8s_context: logging.warning("analyze_with_gemini called with no logs and no context. Skipping."); return None
first_log_namespace = "N/A"; pod_name_in_log = "N/A"
if log_batch:
        first_log_namespace = log_batch[0].get('labels', {}).get('namespace', 'unknown')
        pod_name_in_log = log_batch[0].get('labels', {}).get('pod', 'unknown_pod')
elif k8s_context:
        match_ns = re.search(r"Pod: (.*?)/", k8s_context)
        match_pod = re.search(r"Pod: .*?/(.*?)\n", k8s_context)
        if match_ns: first_log_namespace = match_ns.group(1)
        if match_pod: pod_name_in_log = match_pod.group(1)

log_text = "N/A"
if log_batch: log_text = "\n".join([f"[{entry['timestamp'].isoformat()}] {entry.get('labels', {}).get('pod', 'unknown_pod')}: {entry['message']}" for entry in log_batch])

prompt = f"""
Phân tích tình huống của pod Kubernetes '{first_log_namespace}/{pod_name_in_log}'.
**Ưu tiên xem xét ngữ cảnh Kubernetes** được cung cấp dưới đây vì nó có thể là lý do chính bạn được gọi.
Kết hợp với các dòng log sau đây (nếu có) để đưa ra phân tích đầy đủ.
Xác định mức độ nghiêm trọng tổng thể (chọn một: INFO, WARNING, ERROR, CRITICAL).
Nếu mức độ nghiêm trọng là WARNING, ERROR hoặc CRITICAL, hãy cung cấp một bản tóm tắt ngắn gọn (1-2 câu) bằng **tiếng Việt** giải thích vấn đề cốt lõi, kết hợp thông tin từ ngữ cảnh và log.
Tập trung vào các tác động tiềm ẩn.
Ngữ cảnh Kubernetes:
--- START CONTEXT ---
{k8s_context[:10000]}
--- END CONTEXT ---
Các dòng log (có thể không có):
--- START LOGS ---
{log_text[:20000]}
--- END LOGS ---
Chỉ trả lời bằng định dạng JSON với các khóa "severity" và "summary". Ví dụ: {{"severity": "CRITICAL", "summary": "Pod 'kube-system/oomkill-test-pod' bị Terminated với lý do OOMKilled và có Event OOMKilled gần đây. Cần kiểm tra giới hạn bộ nhớ và code ứng dụng."}}
"""
logging.info(f"Sending logs ({len(log_text)} chars) and context ({len(k8s_context)} chars) for pod '{first_log_namespace}/{pod_name_in_log}' to Gemini for analysis...")
try:
    response = gemini_model.generate_content(prompt, generation_config=genai.types.GenerationConfig(temperature=0.2, max_output_tokens=300), request_options={'timeout': 90})
    if not response.parts: logging.warning("Gemini response has no parts."); return None
    response_text = response.text.strip(); logging.info(f"Received response from Gemini (raw): {response_text}")
    cleaned_response_text = response_text
    if cleaned_response_text.startswith("```json"): cleaned_response_text = cleaned_response_text.strip("```json").strip("`").strip()
    elif cleaned_response_text.startswith("```"): cleaned_response_text = cleaned_response_text.strip("```").strip()
    match = re.search(r'\{.*\}', cleaned_response_text, re.DOTALL); json_string_to_parse = match.group(0) if match else cleaned_response_text
    try:
        analysis_result = json.loads(json_string_to_parse)
        if "severity" in analysis_result: logging.info(f"Successfully parsed Gemini JSON: {analysis_result}"); return analysis_result
        else: logging.warning(f"Gemini response JSON missing 'severity' key. Raw response: {response_text}"); severity = "WARNING"; summary_vi = "Không thể phân tích JSON từ Gemini (thiếu key 'severity'). Phản hồi thô: " + response_text[:200]; return {"severity": severity, "summary": summary_vi}
    except json.JSONDecodeError as json_err: logging.warning(f"Failed to decode Gemini response as JSON: {json_err}. Raw response: {response_text}"); severity = "WARNING"; summary_vi = f"Phản hồi Gemini không phải JSON hợp lệ ({json_err}): " + response_text[:200]; return {"severity": severity, "summary": summary_vi}
except Exception as e: logging.error(f"Error calling Gemini API: {e}", exc_info=True); return None


# --- Hàm gửi cảnh báo Telegram ---
def send_telegram_alert(message):
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: logging.warning("Telegram Bot Token or Chat ID is not configured. Skipping alert."); return
telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"; max_len = 4096; truncated_message = message[:max_len-50] + "..." if len(message) > max_len else message
payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': truncated_message, 'parse_mode': 'Markdown'}
try: response = requests.post(telegram_api_url, json=payload, timeout=10); response.raise_for_status(); logging.info(f"Sent alert to Telegram. Response: {response.json()}")
except requests.exceptions.RequestException as e: logging.error(f"Error sending Telegram alert: {e}");
except Exception as e: logging.error(f"An unexpected error occurred during Telegram send: {e}", exc_info=True)


# --- Vòng lặp chính MỚI của Agent (Quét Song Song) ---
def main_loop():
"""
Vòng lặp chính: Quét K8s và Loki song song, gom kết quả, phân tích, gửi cảnh báo.
"""
recently_alerted_pods = {} # Format: {"ns/pod": alert_timestamp}

while True:
    start_cycle_time = datetime.now(timezone.utc)
    logging.info("--- Starting new monitoring cycle (Parallel Scan) ---")

    # 1. Quét K8s tìm pod có vấn đề về trạng thái/restart
    k8s_problem_pods = scan_kubernetes_for_issues() # Trả về dict {"ns/pod": {"reason": ...}}

    # 2. Quét Loki tìm log đáng ngờ trong khoảng thời gian gần nhất
    loki_scan_end_time = start_cycle_time
    loki_scan_start_time = loki_scan_end_time - timedelta(minutes=LOKI_SCAN_RANGE_MINUTES)
    loki_suspicious_logs = scan_loki_for_suspicious_logs(loki_scan_start_time, loki_scan_end_time) # Trả về dict {"ns/pod": [log_entry,...]}

    # 3. Gom danh sách các pod cần điều tra (từ K8s hoặc Loki)
    pods_to_investigate = {} # Format: {"ns/pod": {"reason": "...", "logs": [...]}}
    # Thêm từ K8s scan
    for pod_key, data in k8s_problem_pods.items():
        if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
        pods_to_investigate[pod_key]["reason"].append(data["reason"])

    # Thêm từ Loki scan
    for pod_key, logs in loki_suspicious_logs.items():
            if pod_key not in pods_to_investigate: pods_to_investigate[pod_key] = {"reason": [], "logs": []}
            # --- THAY ĐỔI: Sử dụng LOKI_SCAN_MIN_LEVEL trong lý do ---
            pods_to_investigate[pod_key]["reason"].append(f"Loki: Phát hiện {len(logs)} log đáng ngờ (>= {LOKI_SCAN_MIN_LEVEL})")
            # --- KẾT THÚC THAY ĐỔI ---
            pods_to_investigate[pod_key]["logs"].extend(logs) # Thêm các log đáng ngờ đã tìm thấy

    logging.info(f"Total pods to investigate this cycle: {len(pods_to_investigate)}")

    # 4. Xử lý từng pod cần điều tra
    for pod_key, data in pods_to_investigate.items():
        namespace, pod_name = pod_key.split('/', 1)
        initial_reasons = "; ".join(data["reason"]) # Lý do phát hiện ban đầu
        suspicious_logs_found = data["logs"] # Log đáng ngờ tìm thấy từ Loki scan

        # Kiểm tra cooldown
        now_utc = datetime.now(timezone.utc)
        if pod_key in recently_alerted_pods:
            last_alert_time = recently_alerted_pods[pod_key]
            cooldown_duration = timedelta(minutes=30) # Cooldown 30 phút
            if now_utc < last_alert_time + cooldown_duration:
                logging.info(f"Pod {pod_key} is in cooldown period. Skipping analysis.")
                continue
            else:
                # Hết cooldown, xóa khỏi dict
                del recently_alerted_pods[pod_key]

        logging.info(f"Investigating pod: {pod_key} (Initial Reasons: {initial_reasons})")

        # 5. Lấy ngữ cảnh K8s chi tiết
        pod_info = get_pod_info(namespace, pod_name)
        node_info = None
        pod_events = []
        if pod_info:
            node_info = get_node_info(pod_info.get('node_name'))
            pod_events = get_pod_events(namespace, pod_name, since_minutes=LOKI_DETAIL_LOG_RANGE_MINUTES + 5)
        k8s_context_str = format_k8s_context(pod_info, node_info, pod_events)

        # 6. Lấy log chi tiết hơn nếu cần (hoặc dùng log đã có)
        logs_for_analysis = suspicious_logs_found # Ưu tiên dùng log đáng ngờ đã tìm thấy
        if not logs_for_analysis:
            # Nếu không có log đáng ngờ từ scan, lấy log gần đây để có thêm thông tin
            log_end_time = datetime.now(timezone.utc)
            log_start_time = log_end_time - timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
            detailed_logs = query_loki_for_pod(namespace, pod_name, log_start_time, log_end_time)
            # Lọc log chi tiết này (ví dụ chỉ lấy INFO trở lên nếu MIN_LOG_LEVEL là INFO)
            # --- THAY ĐỔI: Dùng preprocess_and_filter ở đây ---
            logs_for_analysis = preprocess_and_filter(detailed_logs) # Áp dụng bộ lọc chung
            # --- KẾT THÚC THAY ĐỔI ---


        # 7. Phân tích với Gemini
        analysis_result = analyze_with_gemini(logs_for_analysis, k8s_context_str)

        # 8. Xử lý kết quả và gửi cảnh báo
        if analysis_result:
            severity = analysis_result.get("severity", "UNKNOWN").upper()
            summary = analysis_result.get("summary", "N/A")
            logging.info(f"Gemini analysis result for '{pod_key}': Severity={severity}, Summary={summary}")

            if severity in ALERT_SEVERITY_LEVELS:
                sample_logs = "\n".join([f"- `{log['message'][:150]}`" for log in logs_for_analysis[:5]]) # Lấy 5 log mẫu

                alert_time_hcm = datetime.now(HCM_TZ)
                time_format = '%Y-%m-%d %H:%M:%S %Z'

                alert_message = f"""🚨 *Cảnh báo K8s/Log (Pod: {pod_key})* 🚨
*Mức độ:* `{severity}`
*Tóm tắt:* {summary}
*Lý do phát hiện ban đầu:* {initial_reasons}
*Thời gian phát hiện:* `{alert_time_hcm.strftime(time_format)}`
*Log mẫu (nếu có):*
{sample_logs if sample_logs else "- Không có log mẫu liên quan."}

_Vui lòng kiểm tra trạng thái pod/node/events và log trên Loki để biết thêm chi tiết._"""
                send_telegram_alert(alert_message)
                # Cập nhật thời gian cảnh báo cuối cùng cho pod này
                recently_alerted_pods[pod_key] = now_utc
        else:
            logging.warning(f"Gemini analysis failed or returned no result for pod '{pod_key}'.")

        time.sleep(5) # Nghỉ giữa các pod

    # Ngủ đến hết chu kỳ
    cycle_duration = (datetime.now(timezone.utc) - start_cycle_time).total_seconds()
    sleep_time = max(0, SCAN_INTERVAL_SECONDS - cycle_duration)
    logging.info(f"--- Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
    time.sleep(sleep_time)


if __name__ == "__main__":
logging.info(f"Starting Kubernetes Log Monitoring Agent (Parallel Scan Logic) for namespaces: {K8S_NAMESPACES_STR}")
logging.info(f"Loki scan minimum level: {LOKI_SCAN_MIN_LEVEL}")
logging.info(f"Alerting for severity levels: {ALERT_SEVERITY_LEVELS_STR}")
logging.info(f"Restart count threshold: {RESTART_COUNT_THRESHOLD}")
if not K8S_NAMESPACES: logging.error("K8S_NAMESPACES environment variable is not set or is empty. Exiting."); exit(1)
if not all([LOKI_URL, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]): logging.error("One or more required environment variables are missing. Ensure they are set. Exiting."); exit(1)
try: main_loop()
except KeyboardInterrupt: logging.info("Agent stopped by user.")
except Exception as e: logging.error(f"Unhandled exception in main loop: {e}", exc_info=True)

