import os
import time
import requests
import google.generativeai as genai
import json
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import re

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

# Cấu hình Gemini Client
if not GEMINI_API_KEY:
    logging.error("GEMINI_API_KEY is not set! Ensure it's in your .env file or environment variables.")
    exit(1)
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)

# --- Hàm Query Loki ---
def query_loki(start_time, end_time):
    """
    Truy vấn Loki để lấy log trong khoảng thời gian và các namespace cụ thể.
    """
    loki_api_endpoint = f"{LOKI_URL}/loki/api/v1/query_range"

    if not K8S_NAMESPACES:
        logging.error("No namespaces configured in K8S_NAMESPACES. Exiting.")
        return None

    namespace_regex = "|".join(K8S_NAMESPACES)
    logql_query = f'{{namespace=~"{namespace_regex}"}}'

    params = {
        'query': logql_query,
        'start': int(start_time.timestamp() * 1e9),
        'end': int(end_time.timestamp() * 1e9),
        'limit': LOKI_QUERY_LIMIT,
        'direction': 'forward'
    }
    logging.info(f"Querying Loki: {logql_query} from {start_time} to {end_time}")
    try:
        headers = {'Accept': 'application/json'}
        response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        if 'data' in data and 'result' in data['data']:
            log_entries = []
            for stream in data['data']['result']:
                for timestamp_ns, log_line in stream['values']:
                    log_entries.append({
                        "timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc),
                        "message": log_line.strip(),
                        "labels": stream.get('stream', {})
                    })
            log_entries.sort(key=lambda x: x['timestamp'])
            logging.info(f"Received {len(log_entries)} log entries from Loki for namespaces: {K8S_NAMESPACES_STR}")
            return log_entries
        else:
            logging.warning(f"No 'result' data found in Loki response (Status: {response.status_code}). Response: {response.text[:500]}")
            return []

    except requests.exceptions.RequestException as e:
        error_message = f"Error querying Loki API: {e}"
        if e.response is not None:
            error_message += f" | Status: {e.response.status_code} | Response: {e.response.text[:500]}"
        logging.error(error_message)
        return []
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding Loki JSON response: {e} - Response text: {response.text[:500]}")
        return []
    except Exception as e:
        logging.error(f"An unexpected error occurred during Loki query: {e}", exc_info=True)
        return []

# --- Hàm tiền xử lý và lọc log ---
def preprocess_and_filter(log_entries):
    """
    Lọc log dựa trên mức độ ưu tiên cấu hình (MIN_LOG_LEVEL_FOR_GEMINI).
    """
    filtered_logs = []
    log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
    min_level_index = -1
    try:
        min_level_index = log_levels.index(MIN_LOG_LEVEL_FOR_GEMINI.upper())
    except ValueError:
        logging.warning(f"Invalid MIN_LOG_LEVEL_FOR_GEMINI: {MIN_LOG_LEVEL_FOR_GEMINI}. Defaulting to INFO.")
        min_level_index = log_levels.index("INFO")

    keywords_indicating_problem = ["FAIL", "ERROR", "CRASH", "EXCEPTION", "UNAVAILABLE", "FATAL", "PANIC"]

    for entry in log_entries:
        log_line = entry['message']
        log_line_upper = log_line.upper()
        level_detected = False

        # Kiểm tra level log
        for i, level in enumerate(log_levels):
            if f" {level} " in f" {log_line_upper} " or \
                log_line_upper.startswith(level+":") or \
                f"[{level}]" in log_line_upper or \
                f"level={level.lower()}" in log_line_upper or \
                f"\"level\":\"{level.lower()}\"" in log_line_upper:
                if i >= min_level_index:
                    filtered_logs.append(entry)
                    level_detected = True
                    break

        # Nếu không phát hiện level log phù hợp, kiểm tra từ khóa lỗi
        if not level_detected:
            if any(keyword in log_line_upper for keyword in keywords_indicating_problem):
                    warning_index = log_levels.index("WARNING")
                    if min_level_index <= warning_index:
                        filtered_logs.append(entry)

    logging.info(f"Filtered {len(log_entries)} logs down to {len(filtered_logs)} for potential Gemini analysis (Min Level: {MIN_LOG_LEVEL_FOR_GEMINI}).")
    return filtered_logs

# --- Hàm tương tác với Gemini ---
def analyze_with_gemini(log_batch):
    """
    Gửi một lô log đến Gemini để phân tích mức độ nghiêm trọng và tóm tắt.
    """
    if not log_batch:
        return None

    first_log_namespace = log_batch[0].get('labels', {}).get('namespace', 'unknown')
    log_text = "\n".join([f"[{entry['timestamp'].isoformat()}] {entry.get('labels', {}).get('pod', 'unknown_pod')}: {entry['message']}" for entry in log_batch])

    # --- BẮT ĐẦU THAY ĐỔI: Cập nhật Prompt ---
    prompt = f"""
    Phân tích các dòng log Kubernetes sau đây từ namespace '{first_log_namespace}'.
    Xác định mức độ nghiêm trọng tổng thể cho lô log này (chọn một: INFO, WARNING, ERROR, CRITICAL).
    Nếu mức độ nghiêm trọng là ERROR hoặc CRITICAL, hãy cung cấp một bản tóm tắt ngắn gọn (1-2 câu) bằng **tiếng Việt** giải thích vấn đề cốt lõi được phát hiện trong các log này.
    Tập trung vào các tác động tiềm ẩn đến sự ổn định của cluster hoặc tính khả dụng của ứng dụng. Nếu mức độ nghiêm trọng là INFO hoặc WARNING, bản tóm tắt có thể ngắn gọn hoặc null.

    Các dòng log:
    --- START LOGS ---
    {log_text[:30000]}
    --- END LOGS ---

    Chỉ trả lời bằng định dạng JSON với các khóa "severity" và "summary". Ví dụ: {{"severity": "CRITICAL", "summary": "Pod etcd 'etcd-0' đang gặp lỗi bầu chọn leader thường xuyên, có khả năng ảnh hưởng đến tính khả dụng của API server."}}
    """
    # --- KẾT THÚC THAY ĐỔI ---

    logging.info(f"Sending {len(log_batch)} logs ({len(log_text)} chars) from namespace '{first_log_namespace}' to Gemini for analysis...")
    try:
        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2
            ),
            request_options={'timeout': 60}
        )

        if not response.parts:
                logging.warning("Gemini response has no parts.")
                if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                    logging.warning(f"Gemini prompt feedback: {response.prompt_feedback}")
                return None

        response_text = response.text.strip()
        logging.info(f"Received response from Gemini: {response_text}")

        try:
            if response_text.startswith("```json"):
                response_text = response_text.strip("```json").strip("`").strip()
            elif response_text.startswith("```"):
                    response_text = response_text.strip("```").strip()

            analysis_result = json.loads(response_text)
            if "severity" in analysis_result:
                return analysis_result
            else:
                logging.warning(f"Gemini response JSON missing 'severity' key: {response_text}")
                severity = "WARNING"
                if "CRITICAL" in response_text.upper(): severity = "CRITICAL"
                elif "ERROR" in response_text.upper(): severity = "ERROR"
                # Cố gắng trích xuất tóm tắt tiếng Việt nếu có thể
                summary_vi = "Không thể phân tích JSON từ Gemini, phản hồi thô: " + response_text[:200]
                # (Bạn có thể thêm logic phức tạp hơn để cố gắng tìm tóm tắt ở đây nếu muốn)
                return {"severity": severity, "summary": summary_vi}

        except json.JSONDecodeError:
            logging.warning(f"Failed to decode Gemini response as JSON: {response_text}")
            severity = "WARNING"
            if "CRITICAL" in response_text.upper(): severity = "CRITICAL"
            elif "ERROR" in response_text.upper(): severity = "ERROR"
                # Cố gắng trích xuất tóm tắt tiếng Việt nếu có thể
            summary_vi = "Phản hồi Gemini không phải JSON hợp lệ: " + response_text[:200]
            # (Bạn có thể thêm logic phức tạp hơn để cố gắng tìm tóm tắt ở đây nếu muốn)
            return {"severity": severity, "summary": summary_vi}

    except Exception as e:
        logging.error(f"Error calling Gemini API: {e}", exc_info=True)
        return None

# --- Hàm gửi cảnh báo Telegram ---
def send_telegram_alert(message):
    """
    Gửi tin nhắn cảnh báo đến Telegram Bot.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram Bot Token or Chat ID is not configured. Skipping alert.")
        return

    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    max_len = 4096
    truncated_message = message[:max_len-50] + "..." if len(message) > max_len else message

    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': truncated_message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(telegram_api_url, json=payload, timeout=10)
        response.raise_for_status()
        logging.info(f"Sent alert to Telegram. Response: {response.json()}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending Telegram alert: {e}")
        if e.response is not None:
                logging.error(f"Telegram API Response Status: {e.response.status_code}")
                logging.error(f"Telegram API Response Body: {e.response.text}")
    except Exception as e:
            logging.error(f"An unexpected error occurred during Telegram send: {e}", exc_info=True)

# --- Vòng lặp chính của Agent ---
def main_loop():
    """
    Vòng lặp chính: Query Loki, phân tích, gửi cảnh báo.
    """
    last_query_time = datetime.now(timezone.utc) - timedelta(minutes=LOKI_QUERY_RANGE_MINUTES)

    while True:
        current_time = datetime.now(timezone.utc)
        start_query = last_query_time + timedelta(seconds=1)
        end_query = current_time
        if start_query >= end_query:
                logging.info(f"Start time {start_query} is after or equal to end time {end_query}. Skipping query cycle.")
                last_query_time = end_query
                time.sleep(QUERY_INTERVAL_SECONDS)
                continue

        # 1. Query Loki
        log_entries = query_loki(start_query, end_query)

        if log_entries is None:
            logging.error("Configuration error detected in query_loki. Stopping agent.")
            break

        if log_entries:
            # 2. Tiền xử lý và lọc log
            logs_to_analyze = preprocess_and_filter(log_entries)

            if logs_to_analyze:
                logs_by_namespace = {}
                for log in logs_to_analyze:
                    ns = log.get('labels', {}).get('namespace', 'unknown')
                    if ns not in logs_by_namespace:
                        logs_by_namespace[ns] = []
                    logs_by_namespace[ns].append(log)

                # 3. Phân tích với Gemini cho từng namespace
                for namespace, ns_logs in logs_by_namespace.items():
                    logging.info(f"Processing {len(ns_logs)} logs for namespace '{namespace}'")
                    batch_size = 50
                    for i in range(0, len(ns_logs), batch_size):
                        batch = ns_logs[i:i+batch_size]
                        analysis_result = analyze_with_gemini(batch)

                        if analysis_result:
                            severity = analysis_result.get("severity", "UNKNOWN").upper()
                            summary = analysis_result.get("summary", "N/A") # Tóm tắt giờ sẽ là tiếng Việt

                            logging.info(f"Gemini analysis result for '{namespace}': Severity={severity}, Summary={summary}")

                            # 4. Quyết định và gửi cảnh báo
                            if severity in ALERT_SEVERITY_LEVELS:
                                sample_logs = "\n".join([f"- `{log['message'][:150]}`" for log in batch[:3]])

                                alert_message = f"""🚨 *Cảnh báo Log K8s (Namespace: {namespace})* 🚨
*Mức độ:* `{severity}`
*Tóm tắt:* {summary}
*Khoảng thời gian:* `{start_query.strftime('%Y-%m-%d %H:%M:%S')}` - `{end_query.strftime('%Y-%m-%d %H:%M:%S')}` UTC
*Log mẫu:*
{sample_logs}

_Vui lòng kiểm tra log trên Loki để biết thêm chi tiết._
"""
                                send_telegram_alert(alert_message)
                        else:
                            logging.warning(f"Gemini analysis failed or returned no result for a batch in namespace '{namespace}'.")
                        time.sleep(2)

        # Cập nhật thời gian query cuối cùng
        last_query_time = end_query

        elapsed_time = (datetime.now(timezone.utc) - current_time).total_seconds()
        sleep_time = max(0, QUERY_INTERVAL_SECONDS - elapsed_time)
        logging.info(f"Cycle finished in {elapsed_time:.2f}s. Sleeping for {sleep_time:.2f} seconds...")
        time.sleep(sleep_time)

if __name__ == "__main__":
    logging.info(f"Starting Kubernetes Log Monitoring Agent for namespaces: {K8S_NAMESPACES_STR}")
    logging.info(f"Minimum log level for Gemini analysis: {MIN_LOG_LEVEL_FOR_GEMINI}")
    logging.info(f"Alerting for severity levels: {ALERT_SEVERITY_LEVELS_STR}")
    if not K8S_NAMESPACES:
            logging.error("K8S_NAMESPACES environment variable is not set or is empty. Exiting.")
            exit(1)
    if not all([LOKI_URL, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
            logging.error("One or more required environment variables are missing (LOKI_URL, GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). Ensure they are set in the environment or .env file. Exiting.")
            exit(1)

    try:
        main_loop()
    except KeyboardInterrupt:
        logging.info("Agent stopped by user.")
    except Exception as e:
        logging.error(f"Unhandled exception in main loop: {e}", exc_info=True)
        # send_telegram_alert(f"🚨 AGENT CRASHED! Unhandled exception: {e}")

