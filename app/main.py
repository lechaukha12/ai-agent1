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


# --- Tải biến môi trường từ file .env (cho phát triển cục bộ) ---
load_dotenv()

# --- Cấu hình Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Tải cấu hình từ biến môi trường ---
LOKI_URL = os.environ.get("LOKI_URL", "http://loki-read.monitoring.svc.cluster.local:3100")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
QUERY_INTERVAL_SECONDS = int(os.environ.get("QUERY_INTERVAL_SECONDS", 60))
LOKI_QUERY_RANGE_MINUTES = int(os.environ.get("LOKI_QUERY_RANGE_MINUTES", 5))
LOKI_QUERY_LIMIT = int(os.environ.get("LOKI_QUERY_LIMIT", 1000))
K8S_NAMESPACES_STR = os.environ.get("K8S_NAMESPACES", "kube-system")
K8S_NAMESPACES = [ns.strip() for ns in K8S_NAMESPACES_STR.split(',') if ns.strip()]
MIN_LOG_LEVEL_FOR_GEMINI = os.environ.get("MIN_LOG_LEVEL_FOR_GEMINI", "INFO")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-1.5-flash")
ALERT_SEVERITY_LEVELS_STR = os.environ.get("ALERT_SEVERITY_LEVELS", "ERROR,CRITICAL")
ALERT_SEVERITY_LEVELS = [level.strip().upper() for level in ALERT_SEVERITY_LEVELS_STR.split(',') if level.strip()]

# --- Cấu hình Kubernetes Client ---
try:
    config.load_incluster_config()
    logging.info("Loaded in-cluster Kubernetes config.")
except config.ConfigException:
    try:
        config.load_kube_config()
        logging.info("Loaded local Kubernetes config (kubeconfig).")
    except config.ConfigException:
        logging.error("Could not configure Kubernetes client. Exiting.")
        exit(1)

k8s_core_v1 = client.CoreV1Api()
k8s_apps_v1 = client.AppsV1Api()

# Cấu hình Gemini Client
if not GEMINI_API_KEY:
    logging.error("GEMINI_API_KEY is not set! Ensure it's in your .env file or environment variables.")
    exit(1)
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)

# --- Hàm Query Loki ---
# (Giữ nguyên)
def query_loki(start_time, end_time):
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range"
    if not K8S_NAMESPACES: logging.error("No namespaces configured in K8S_NAMESPACES. Exiting."); return None
    namespace_regex = "|".join(K8S_NAMESPACES)
    logql_query = f'{{namespace=~"{namespace_regex}"}}'
    params = {'query': logql_query, 'start': int(start_time.timestamp() * 1e9), 'end': int(end_time.timestamp() * 1e9), 'limit': LOKI_QUERY_LIMIT, 'direction': 'forward'}
    logging.info(f"Querying Loki: {logql_query} from {start_time} to {end_time}")
    try:
        headers = {'Accept': 'application/json'}
        response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=30); response.raise_for_status(); data = response.json()
        if 'data' in data and 'result' in data['data']:
            log_entries = []
            for stream in data['data']['result']:
                for timestamp_ns, log_line in stream['values']: log_entries.append({"timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc), "message": log_line.strip(), "labels": stream.get('stream', {})})
            log_entries.sort(key=lambda x: x['timestamp']); logging.info(f"Received {len(log_entries)} log entries from Loki for namespaces: {K8S_NAMESPACES_STR}"); return log_entries
        else: logging.warning(f"No 'result' data found in Loki response (Status: {response.status_code}). Response: {response.text[:500]}"); return []
    except requests.exceptions.RequestException as e: error_message = f"Error querying Loki API: {e}"; logging.error(error_message); return []
    except json.JSONDecodeError as e: logging.error(f"Error decoding Loki JSON response: {e} - Response text: {response.text[:500]}"); return []
    except Exception as e: logging.error(f"An unexpected error occurred during Loki query: {e}", exc_info=True); return []


# --- Hàm tiền xử lý và lọc log ---
# (Giữ nguyên)
def preprocess_and_filter(log_entries):
    filtered_logs = []
    log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
    min_level_index = -1
    try: min_level_index = log_levels.index(MIN_LOG_LEVEL_FOR_GEMINI.upper())
    except ValueError: logging.warning(f"Invalid MIN_LOG_LEVEL_FOR_GEMINI: {MIN_LOG_LEVEL_FOR_GEMINI}. Defaulting to INFO."); min_level_index = log_levels.index("INFO")
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
    logging.info(f"Filtered {len(log_entries)} logs down to {len(filtered_logs)} for potential Gemini analysis (Min Level: {MIN_LOG_LEVEL_FOR_GEMINI}).")
    return filtered_logs

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


# --- Hàm tương tác với Gemini ---
def analyze_with_gemini(log_batch, k8s_context=""):
    """
    Gửi một lô log và ngữ cảnh K8s đến Gemini để phân tích.
    Cố gắng xử lý JSON bị lỗi nhẹ.
    """
    if not log_batch: return None
    first_log_namespace = log_batch[0].get('labels', {}).get('namespace', 'unknown')
    log_text = "\n".join([f"[{entry['timestamp'].isoformat()}] {entry.get('labels', {}).get('pod', 'unknown_pod')}: {entry['message']}" for entry in log_batch])
    prompt = f"""
    Phân tích các dòng log Kubernetes sau đây từ namespace '{first_log_namespace}'.
    Hãy xem xét **ngữ cảnh Kubernetes** được cung cấp dưới đây để đưa ra phân tích chính xác hơn.
    Xác định mức độ nghiêm trọng tổng thể cho lô log này (chọn một: INFO, WARNING, ERROR, CRITICAL).
    Nếu mức độ nghiêm trọng là ERROR hoặc CRITICAL, hãy cung cấp một bản tóm tắt ngắn gọn (1-2 câu) bằng **tiếng Việt** giải thích vấn đề cốt lõi được phát hiện, kết hợp thông tin từ log và ngữ cảnh.
    Tập trung vào các tác động tiềm ẩn đến sự ổn định của cluster hoặc tính khả dụng của ứng dụng. Nếu mức độ nghiêm trọng là INFO hoặc WARNING, bản tóm tắt có thể ngắn gọn hoặc null.
    Các dòng log:
    --- START LOGS ---
    {log_text[:25000]}
    --- END LOGS ---
    {k8s_context[:5000]}
    Chỉ trả lời bằng định dạng JSON với các khóa "severity" và "summary". Ví dụ: {{"severity": "CRITICAL", "summary": "Pod 'coredns-xyz' trong namespace 'kube-system' liên tục khởi động lại (restart count cao) và log báo lỗi kết nối upstream. Kiểm tra cấu hình CoreDNS và kết nối mạng của node."}}
    """
    logging.info(f"Sending {len(log_batch)} logs ({len(log_text)} chars) and context ({len(k8s_context)} chars) from namespace '{first_log_namespace}' to Gemini for analysis...")
    try:
        # --- BẮT ĐẦU THAY ĐỔI: Giới hạn max_output_tokens ---
        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=250 # Giới hạn số token trả về để tránh bị cắt ngang
            ),
            request_options={'timeout': 90}
        )
        # --- KẾT THÚC THAY ĐỔI ---

        if not response.parts:
                logging.warning("Gemini response has no parts.");
                if hasattr(response, 'prompt_feedback') and response.prompt_feedback: logging.warning(f"Gemini prompt feedback: {response.prompt_feedback}")
                return None

        response_text = response.text.strip()
        # --- BẮT ĐẦU THAY ĐỔI: Log toàn bộ response thô khi có lỗi parse ---
        logging.info(f"Received response from Gemini (raw): {response_text}") # Log trước khi parse

        # Cố gắng dọn dẹp response trước khi parse
        cleaned_response_text = response_text
        if cleaned_response_text.startswith("```json"):
            cleaned_response_text = cleaned_response_text.strip("```json").strip("`").strip()
        elif cleaned_response_text.startswith("```"):
                cleaned_response_text = cleaned_response_text.strip("```").strip()
        # Thử tìm JSON hợp lệ đầu tiên trong chuỗi (phòng trường hợp có text thừa)
        match = re.search(r'\{.*\}', cleaned_response_text, re.DOTALL)
        if match:
            json_string_to_parse = match.group(0)
        else:
            json_string_to_parse = cleaned_response_text # Dùng bản gốc nếu không tìm thấy {}

        try:
            analysis_result = json.loads(json_string_to_parse)
            if "severity" in analysis_result:
                logging.info(f"Successfully parsed Gemini JSON: {analysis_result}")
                return analysis_result
            else:
                logging.warning(f"Gemini response JSON missing 'severity' key. Raw response: {response_text}")
                severity = "WARNING"; summary_vi = "Không thể phân tích JSON từ Gemini (thiếu key 'severity'). Phản hồi thô: " + response_text[:200]
                if "CRITICAL" in response_text.upper(): severity = "CRITICAL"
                elif "ERROR" in response_text.upper(): severity = "ERROR"
                return {"severity": severity, "summary": summary_vi}

        except json.JSONDecodeError as json_err:
            # Log lỗi cụ thể và toàn bộ response thô
            logging.warning(f"Failed to decode Gemini response as JSON: {json_err}. Raw response: {response_text}")
            severity = "WARNING"; summary_vi = f"Phản hồi Gemini không phải JSON hợp lệ ({json_err}): " + response_text[:200]
            if "CRITICAL" in response_text.upper(): severity = "CRITICAL"
            elif "ERROR" in response_text.upper(): severity = "ERROR"
            return {"severity": severity, "summary": summary_vi}
        # --- KẾT THÚC THAY ĐỔI ---

    except Exception as e: logging.error(f"Error calling Gemini API: {e}", exc_info=True); return None

# --- Hàm gửi cảnh báo Telegram ---
# (Giữ nguyên)
def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: logging.warning("Telegram Bot Token or Chat ID is not configured. Skipping alert."); return
    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    max_len = 4096; truncated_message = message[:max_len-50] + "..." if len(message) > max_len else message
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': truncated_message, 'parse_mode': 'Markdown'}
    try:
        response = requests.post(telegram_api_url, json=payload, timeout=10); response.raise_for_status()
        logging.info(f"Sent alert to Telegram. Response: {response.json()}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending Telegram alert: {e}")
        if e.response is not None: logging.error(f"Telegram API Response Status: {e.response.status_code}\nTelegram API Response Body: {e.response.text}")
    except Exception as e: logging.error(f"An unexpected error occurred during Telegram send: {e}", exc_info=True)

# --- Vòng lặp chính của Agent ---
# (Giữ nguyên)
def main_loop():
    last_query_time = datetime.now(timezone.utc) - timedelta(minutes=LOKI_QUERY_RANGE_MINUTES)
    while True:
        current_time = datetime.now(timezone.utc); start_query = last_query_time + timedelta(seconds=1); end_query = current_time
        if start_query >= end_query: logging.info(f"Start time {start_query} is after or equal to end time {end_query}. Skipping query cycle."); last_query_time = end_query; time.sleep(QUERY_INTERVAL_SECONDS); continue
        log_entries = query_loki(start_query, end_query)
        if log_entries is None: logging.error("Configuration error detected in query_loki. Stopping agent."); break
        if log_entries:
            logs_to_analyze = preprocess_and_filter(log_entries)
            if logs_to_analyze:
                logs_by_pod = {}
                for log in logs_to_analyze:
                    labels = log.get('labels', {}); ns = labels.get('namespace', 'unknown_namespace'); pod = labels.get('pod', 'unknown_pod')
                    if ns == 'unknown_namespace' or pod == 'unknown_pod': continue
                    pod_key = f"{ns}/{pod}";
                    if pod_key not in logs_by_pod: logs_by_pod[pod_key] = []
                    logs_by_pod[pod_key].append(log)
                for pod_key, pod_logs in logs_by_pod.items():
                    namespace, pod_name = pod_key.split('/', 1); logging.info(f"Processing {len(pod_logs)} logs for pod '{pod_key}'")
                    pod_info = get_pod_info(namespace, pod_name); node_info = None; pod_events = []
                    if pod_info: node_info = get_node_info(pod_info.get('node_name')); pod_events = get_pod_events(namespace, pod_name)
                    k8s_context_str = format_k8s_context(pod_info, node_info, pod_events)
                    batch_size = 30
                    for i in range(0, len(pod_logs), batch_size):
                        batch = pod_logs[i:i+batch_size]; analysis_result = analyze_with_gemini(batch, k8s_context_str)
                        if analysis_result:
                            severity = analysis_result.get("severity", "UNKNOWN").upper(); summary = analysis_result.get("summary", "N/A")
                            logging.info(f"Gemini analysis result for '{pod_key}': Severity={severity}, Summary={summary}")
                            if severity in ALERT_SEVERITY_LEVELS:
                                sample_logs = "\n".join([f"- `{log['message'][:150]}`" for log in batch[:3]])
                                alert_message = f"""🚨 *Cảnh báo Log K8s (Pod: {pod_key})* 🚨\n*Mức độ:* `{severity}`\n*Tóm tắt:* {summary}\n*Khoảng thời gian log:* `{start_query.strftime('%Y-%m-%d %H:%M:%S')}` - `{end_query.strftime('%Y-%m-%d %H:%M:%S')}` UTC\n*Log mẫu:*\n{sample_logs}\n\n_Vui lòng kiểm tra log và trạng thái pod/node/events trên K8s để biết thêm chi tiết._"""
                                send_telegram_alert(alert_message)
                        else: logging.warning(f"Gemini analysis failed or returned no result for a batch in pod '{pod_key}'.")
                        time.sleep(3)
        last_query_time = end_query
        elapsed_time = (datetime.now(timezone.utc) - current_time).total_seconds()
        sleep_time = max(0, QUERY_INTERVAL_SECONDS - elapsed_time)
        logging.info(f"Cycle finished in {elapsed_time:.2f}s. Sleeping for {sleep_time:.2f} seconds...")
        time.sleep(sleep_time)

if __name__ == "__main__":
    logging.info(f"Starting Kubernetes Log Monitoring Agent for namespaces: {K8S_NAMESPACES_STR}")
    logging.info(f"Minimum log level for Gemini analysis: {MIN_LOG_LEVEL_FOR_GEMINI}")
    logging.info(f"Alerting for severity levels: {ALERT_SEVERITY_LEVELS_STR}")
    if not K8S_NAMESPACES: logging.error("K8S_NAMESPACES environment variable is not set or is empty. Exiting."); exit(1)
    if not all([LOKI_URL, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]): logging.error("One or more required environment variables are missing. Ensure they are set. Exiting."); exit(1)
    try: main_loop()
    except KeyboardInterrupt: logging.info("Agent stopped by user.")
    except Exception as e: logging.error(f"Unhandled exception in main loop: {e}", exc_info=True)

