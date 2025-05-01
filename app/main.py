# ai-agent1/app/main.py

import os
import time
import requests
import json
import logging
from datetime import datetime, timedelta, timezone, MINYEAR
from dotenv import load_dotenv
import re
try:
    from zoneinfo import ZoneInfo
except ImportError:
    logging.error("zoneinfo module not found. Please use Python 3.9+ or install pytz.")
    exit(1)
import sqlite3 # Keep for potential direct DB interaction if needed, though mostly via db_manager
import threading
import sys

try:
    import ai_providers
    import notifier
    import k8s_monitor
    import db_manager
    import loki_client
    import config_manager
except ImportError as e:
    print(f"CRITICAL: Failed to import custom module: {e}")
    try: logging.critical(f"Failed to import custom module: {e}", exc_info=True)
    except NameError: pass
    exit(1)

load_dotenv()
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    stream=sys.stdout)

logging.info("Logging configured successfully.")

# --- Static Config & Defaults ---
LOKI_URL = os.environ.get("LOKI_URL", "http://loki-read.monitoring.svc.cluster.local:3100")
LOKI_SCAN_RANGE_MINUTES = int(os.environ.get("LOKI_SCAN_RANGE_MINUTES", 1))
LOKI_DETAIL_LOG_RANGE_MINUTES = int(os.environ.get("LOKI_DETAIL_LOG_RANGE_MINUTES", 30))
LOKI_QUERY_LIMIT = int(os.environ.get("LOKI_QUERY_LIMIT", 500))
EXCLUDED_NAMESPACES_STR = os.environ.get("EXCLUDED_NAMESPACES", "kube-node-lease,kube-public")
EXCLUDED_NAMESPACES = {ns.strip() for ns in EXCLUDED_NAMESPACES_STR.split(',') if ns.strip()}
STATS_UPDATE_INTERVAL_SECONDS = int(os.environ.get("STATS_UPDATE_INTERVAL_SECONDS", 300))
NAMESPACE_REFRESH_INTERVAL_SECONDS = int(os.environ.get("NAMESPACE_REFRESH_INTERVAL_SECONDS", 3600))

DEFAULT_MONITORED_NAMESPACES_STR = os.environ.get("DEFAULT_MONITORED_NAMESPACES", "kube-system,default")
DEFAULT_PROMPT_TEMPLATE = """
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
"""

# --- Timezone ---
try: HCM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception as e: logging.error(f"Could not load timezone 'Asia/Ho_Chi_Minh': {e}. Defaulting to UTC."); HCM_TZ = timezone.utc

# --- K8s Client Initialization Call ---
if not k8s_monitor.initialize_k8s_client():
     logging.warning("Kubernetes client initialization failed. K8s features will be unavailable.")

# --- Background Stats Update ---
def periodic_stat_update():
    while True:
        time.sleep(STATS_UPDATE_INTERVAL_SECONDS)
        logging.debug("Triggering periodic stats update...")
        db_manager.update_daily_stats()

# --- AI Analysis Wrapper ---
def analyze_incident(log_batch, k8s_context, initial_reasons, config):
    prompt_template = config.get('prompt_template', DEFAULT_PROMPT_TEMPLATE)
    return ai_providers.perform_analysis(
        log_batch, k8s_context, initial_reasons, config, prompt_template
    )

# --- Main Monitoring Cycle ---
def refresh_namespaces_if_needed(last_refresh_time):
    current_time_secs = time.time()
    if current_time_secs - last_refresh_time >= NAMESPACE_REFRESH_INTERVAL_SECONDS:
        logging.info("Refreshing list of active namespaces from Kubernetes API...")
        all_active_namespaces = k8s_monitor.get_active_namespaces(EXCLUDED_NAMESPACES)
        db_manager.update_available_namespaces_in_db(all_active_namespaces)
        return current_time_secs
    return last_refresh_time

def identify_pods_to_investigate(monitored_namespaces, start_cycle_time, config):
    k8s_problem_pods = k8s_monitor.scan_kubernetes_for_issues(
        monitored_namespaces,
        config['restart_count_threshold'],
        LOKI_DETAIL_LOG_RANGE_MINUTES
    )
    loki_scan_end_time = start_cycle_time; loki_scan_start_time = loki_scan_end_time - timedelta(minutes=LOKI_SCAN_RANGE_MINUTES)
    loki_suspicious_logs = loki_client.scan_loki_for_suspicious_logs(
        LOKI_URL,
        loki_scan_start_time,
        loki_scan_end_time,
        monitored_namespaces,
        config['loki_scan_min_level']
    )

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
    logging.info(f"Investigating pod: {namespace}/{pod_name} (Initial Reasons: {initial_reasons})")
    pod_info = k8s_monitor.get_pod_info(namespace, pod_name)
    node_info = k8s_monitor.get_node_info(pod_info.get('node_name')) if pod_info else None
    pod_events = k8s_monitor.get_pod_events(namespace, pod_name, since_minutes=LOKI_DETAIL_LOG_RANGE_MINUTES + 5)
    k8s_context_str = k8s_monitor.format_k8s_context(pod_info, node_info, pod_events)

    logs_for_analysis = suspicious_logs_found_in_scan
    if not logs_for_analysis:
        logging.info(f"No logs found in initial scan for {namespace}/{pod_name}. Querying Loki for detailed logs...")
        log_end_time = datetime.now(timezone.utc); log_start_time = log_end_time - timedelta(minutes=LOKI_DETAIL_LOG_RANGE_MINUTES)
        detailed_logs = loki_client.query_loki_for_pod(
            LOKI_URL,
            namespace,
            pod_name,
            log_start_time,
            log_end_time,
            LOKI_QUERY_LIMIT
        )
        logs_for_analysis = loki_client.preprocess_and_filter(detailed_logs, config['loki_scan_min_level'])
    else:
        logging.info(f"Using {len(logs_for_analysis)} logs found during initial scan for {namespace}/{pod_name}.")

    return logs_for_analysis, k8s_context_str

def process_analysis_and_alert(pod_key, initial_reasons, logs_for_analysis, k8s_context_str, config):
    namespace, pod_name = pod_key.split('/', 1);
    analysis_start_time = time.time()

    analysis_result, final_prompt, raw_response_text = analyze_incident(
        logs_for_analysis, k8s_context_str, initial_reasons, config
    )

    analysis_duration = time.time() - analysis_start_time
    logging.info(f"Analysis processing for {pod_key} took {analysis_duration:.2f} seconds.")

    if analysis_result is None:
        logging.error(f"CRITICAL: analyze_incident returned None for analysis_result for pod {pod_key}. Using default.")
        fallback_severity = ai_providers.determine_severity_from_rules(initial_reasons, logs_for_analysis)
        analysis_result = ai_providers.get_default_analysis(fallback_severity, initial_reasons)
        raw_response_text = raw_response_text or "[Analysis result was None]"

    severity = (analysis_result.get("severity") or "UNKNOWN").upper()
    summary = analysis_result.get("summary") or "N/A"
    root_cause_str = analysis_result.get("root_cause") or "N/A"
    steps_str = analysis_result.get("troubleshooting_steps") or "N/A"

    logging.info(f"Analysis result for '{pod_key}': Severity={severity}, Summary={summary[:100]}...")

    sample_logs_str = "\n".join([f"- {log['message'][:150]}" for log in logs_for_analysis[:5]]) if logs_for_analysis else "-"

    # --- FIX: Ensure raw_response_text is a string or None before saving ---
    db_raw_response = None
    if raw_response_text is not None:
        if isinstance(raw_response_text, str):
            db_raw_response = raw_response_text
        else:
            try:
                # Attempt to convert other types to string representation
                db_raw_response = str(raw_response_text)
                logging.warning(f"Converted non-string raw_response_text to string for DB storage (Pod: {pod_key}). Original type: {type(raw_response_text)}")
            except Exception as str_conv_err:
                logging.error(f"Failed to convert raw_response_text to string for DB storage (Pod: {pod_key}): {str_conv_err}. Storing placeholder.")
                db_raw_response = "[Error converting raw response to string]"
    # --- END FIX ---

    db_manager.record_incident(
        pod_key, severity, summary, initial_reasons, k8s_context_str,
        sample_logs_str, config.get('alert_severity_levels', []),
        final_prompt, db_raw_response, root_cause_str, steps_str # Use db_raw_response
    )

    alert_cooldown_minutes = config.get('alert_cooldown_minutes', config_manager.DEFAULT_ALERT_COOLDOWN_MINUTES)

    if severity in config.get('alert_severity_levels', []):
        if config.get('enable_telegram_alerts', False):
            bot_token = config.get('telegram_bot_token')
            chat_id = config.get('telegram_chat_id')
            if bot_token and chat_id:
                alert_time_hcm = datetime.now(HCM_TZ); time_format = '%Y-%m-%d %H:%M:%S %Z'
                alert_data = {
                    'pod_key': pod_key, 'severity': severity, 'summary': summary,
                    'root_cause': root_cause_str, 'troubleshooting_steps': steps_str,
                    'initial_reasons': initial_reasons, 'alert_time': alert_time_hcm.strftime(time_format),
                    'sample_logs': sample_logs_str
                }
                alert_sent = notifier.send_telegram_alert(
                    bot_token, chat_id, alert_data, config.get('enable_ai_analysis', False)
                )
                db_manager.set_pod_cooldown(pod_key, alert_cooldown_minutes)
                if not alert_sent:
                     logging.warning(f"Telegram alert sending failed for {pod_key}, but cooldown was set.")
            else:
                logging.warning(f"Telegram alerts enabled for {pod_key} (severity {severity}) but token/chat_id missing in config.")
        else:
            logging.info(f"Telegram alerts disabled. Skipping alert for {pod_key} (severity {severity}).")
            db_manager.set_pod_cooldown(pod_key, alert_cooldown_minutes)
    else:
        logging.info(f"Severity '{severity}' for {pod_key} does not meet alert threshold {config.get('alert_severity_levels', [])}. No alert sent.")
        db_manager.set_pod_cooldown(pod_key, alert_cooldown_minutes)


def perform_monitoring_cycle(last_namespace_refresh_time):
    start_cycle_time = datetime.now(timezone.utc)
    cycle_start_ts = time.time()
    logging.info("--- Starting new monitoring cycle ---")

    config = config_manager.get_config()
    current_scan_interval = config.get('scan_interval_seconds', config_manager.DEFAULT_SCAN_INTERVAL_SECONDS)

    last_namespace_refresh_time = refresh_namespaces_if_needed(last_namespace_refresh_time)
    monitored_namespaces = config_manager.get_monitored_namespaces()

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
        if db_manager.is_pod_in_cooldown(pod_key): continue
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
    last_namespace_refresh_time = 0
    logging.info("Entering main loop...")
    while True:
        try:
            logging.debug("Calling perform_monitoring_cycle...")
            last_namespace_refresh_time = perform_monitoring_cycle(last_namespace_refresh_time)
            logging.debug("perform_monitoring_cycle finished.")
        except Exception as cycle_err:
             logging.critical(f"Unhandled exception in monitoring cycle: {cycle_err}", exc_info=True)
             try:
                 config = config_manager.get_config()
                 sleep_interval = config.get('scan_interval_seconds', config_manager.DEFAULT_SCAN_INTERVAL_SECONDS)
             except Exception:
                 sleep_interval = 30
             logging.info(f"Sleeping for {sleep_interval} seconds before next cycle after error.")
             time.sleep(sleep_interval)
             last_namespace_refresh_time = 0


if __name__ == "__main__":
    logging.info("Script execution started.")

    logging.info("Initializing database...")
    db_default_configs = {
        'enable_ai_analysis': str(config_manager.DEFAULT_ENABLE_AI_ANALYSIS).lower(),
        'ai_provider': config_manager.DEFAULT_AI_PROVIDER,
        'ai_model_identifier': config_manager.DEFAULT_AI_MODEL_IDENTIFIER,
        'ai_api_key': '',
        'monitored_namespaces': json.dumps([ns.strip() for ns in DEFAULT_MONITORED_NAMESPACES_STR.split(',') if ns.strip()]),
        'loki_scan_min_level': config_manager.DEFAULT_LOKI_SCAN_MIN_LEVEL,
        'scan_interval_seconds': str(config_manager.DEFAULT_SCAN_INTERVAL_SECONDS),
        'restart_count_threshold': str(config_manager.DEFAULT_RESTART_COUNT_THRESHOLD),
        'alert_severity_levels': config_manager.DEFAULT_ALERT_SEVERITY_LEVELS_STR,
        'alert_cooldown_minutes': str(config_manager.DEFAULT_ALERT_COOLDOWN_MINUTES),
        'enable_telegram_alerts': str(config_manager.DEFAULT_ENABLE_TELEGRAM_ALERTS).lower(),
        'telegram_bot_token': '',
        'telegram_chat_id': '',
        'prompt_template': DEFAULT_PROMPT_TEMPLATE
    }
    if not db_manager.init_db(db_default_configs):
        logging.critical("Failed to initialize database. Exiting.")
        exit(1)
    logging.info("Database initialized successfully.")

    logging.info("Loading initial agent configuration...")
    initial_config = config_manager.get_config(force_refresh=True)
    logging.info("Initial agent configuration loaded.")

    logging.info("Starting periodic stats update thread...")
    stats_thread = threading.Thread(target=periodic_stat_update, daemon=True);
    stats_thread.start();
    logging.info("Started periodic stats update thread.")

    logging.info(f"Starting Kubernetes Log Monitoring Agent")
    logging.info(f"Loki URL: {LOKI_URL}")

    logging.info(f"Initial Config - Scan Interval: {initial_config.get('scan_interval_seconds')}s")
    logging.info(f"Initial Config - Loki Scan Level: {initial_config.get('loki_scan_min_level')}")
    logging.info(f"Initial Config - Alert Levels: {initial_config.get('alert_severity_levels_str')}")
    logging.info(f"Initial Config - Restart Threshold: {initial_config.get('restart_count_threshold')}")
    logging.info(f"Initial Config - Alert Cooldown: {initial_config.get('alert_cooldown_minutes')}m")
    logging.info(f"Initial Config - AI Enabled: {initial_config.get('enable_ai_analysis')}, Provider: {initial_config.get('ai_provider')}, Model: {initial_config.get('ai_model_identifier')}")
    logging.info(f"Initial Config - Telegram Alerts Enabled: {initial_config.get('enable_telegram_alerts')}")

    logging.info("Starting main monitoring loop...")
    try:
        main_loop()
    except KeyboardInterrupt:
        logging.info("Agent stopped by user (KeyboardInterrupt).")
    except Exception as main_err:
        logging.critical(f"Unhandled exception in main loop: {main_err}", exc_info=True)
    finally:
        logging.info("Performing final stats update before exiting...")
        db_manager.update_daily_stats()
        logging.info("Agent shutdown complete.")

