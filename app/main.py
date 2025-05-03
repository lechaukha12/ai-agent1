# ai-agent1/app/main.py
import os
import time
import requests
import json
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import re
import threading
import sys
import pathlib

try:
    import k8s_monitor
    import loki_client
    import config_manager
except ImportError as e:
    print(f"CRITICAL: Failed to import custom module: {e}")
    try:
        logging.critical(f"Failed to import custom module: {e}", exc_info=True)
    except NameError:
        pass
    exit(1)

load_dotenv()

# --- THÊM HẰNG SỐ PHIÊN BẢN ---
AGENT_VERSION = "v1.0.7"
# ----------------------------

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format='%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s',
                    stream=sys.stdout)

logging.info(f"Logging configured successfully for Collector Agent - Version: {AGENT_VERSION}") # Log version khi khởi động

CACHE_DIR = pathlib.Path("/cache")

if not k8s_monitor.initialize_k8s_client():
     logging.warning("Kubernetes client initialization failed. K8s context features will be unavailable.")

current_cluster_summary = None
last_cluster_summary_refresh = 0
CLUSTER_SUMMARY_REFRESH_INTERVAL = 3600

def refresh_namespaces_if_needed(last_refresh_time):
    return last_refresh_time

def get_cached_cluster_summary():
    global current_cluster_summary, last_cluster_summary_refresh
    now = time.time()
    if not current_cluster_summary or (now - last_cluster_summary_refresh > CLUSTER_SUMMARY_REFRESH_INTERVAL):
        logging.info("Refreshing cluster summary (version, node count)...")
        current_cluster_summary = k8s_monitor.get_cluster_summary()
        last_cluster_summary_refresh = now
        logging.info(f"Cluster summary refreshed: {current_cluster_summary}")
    return current_cluster_summary

def identify_pods_to_investigate(monitored_namespaces, start_cycle_time, config):
    restart_threshold = config.get('restart_count_threshold', 5)
    loki_scan_min_level = config.get('loki_scan_min_level', 'INFO')
    loki_url = config.get('loki_url')
    loki_detail_minutes = config.get('loki_detail_log_range_minutes', 30)

    if not loki_url:
        logging.error("LOKI_URL is not configured. Cannot scan Loki.")
        return {}

    k8s_problem_pods = k8s_monitor.scan_kubernetes_for_issues(
        monitored_namespaces,
        restart_threshold,
        loki_detail_minutes
    )

    loki_scan_range_minutes = config.get('loki_scan_range_minutes', 1)
    loki_scan_end_time = start_cycle_time
    loki_scan_start_time = loki_scan_end_time - timedelta(minutes=loki_scan_range_minutes)
    loki_suspicious_logs = loki_client.scan_loki_for_suspicious_logs(
        loki_url,
        loki_scan_start_time,
        loki_scan_end_time,
        monitored_namespaces,
        loki_scan_min_level
    )

    pods_to_investigate = {}

    for pod_key, data in k8s_problem_pods.items():
        if pod_key not in pods_to_investigate:
            pods_to_investigate[pod_key] = {"reason": [], "logs": [], "k8s_issue_data": data}
        reason_str = data.get("reason", "Unknown K8s Reason")
        if reason_str not in pods_to_investigate[pod_key]["reason"]:
             pods_to_investigate[pod_key]["reason"].append(reason_str)

    for pod_key, logs in loki_suspicious_logs.items():
            if pod_key not in pods_to_investigate:
                pods_to_investigate[pod_key] = {"reason": [], "logs": [], "k8s_issue_data": None}
            reason_text = f"Loki: Found {len(logs)} suspicious logs (level >= {loki_scan_min_level})"
            if reason_text not in pods_to_investigate[pod_key]["reason"]:
                 pods_to_investigate[pod_key]["reason"].append(reason_text)
            pods_to_investigate[pod_key]["logs"].extend(logs)

    logging.info(f"Identified {len(pods_to_investigate)} pods for potential data collection this cycle.")
    return pods_to_investigate

def collect_pod_details(namespace, pod_name, initial_reasons, suspicious_logs_found_in_scan, config):
    logging.info(f"Collecting details for pod: {namespace}/{pod_name} (Initial Reasons: {initial_reasons})")
    loki_url = config.get('loki_url')
    loki_detail_minutes = config.get('loki_detail_log_range_minutes', 30)
    loki_limit = config.get('loki_query_limit', 500)

    if not loki_url:
        logging.error(f"LOKI_URL not configured. Cannot fetch detailed logs for {namespace}/{pod_name}.")
        return [], ""

    pod_info = k8s_monitor.get_pod_info(namespace, pod_name)
    node_info = k8s_monitor.get_node_info(pod_info.get('node_name')) if pod_info else None
    pod_events = k8s_monitor.get_pod_events(namespace, pod_name, since_minutes=loki_detail_minutes + 5)
    k8s_context_str = k8s_monitor.format_k8s_context(pod_info, node_info, pod_events)

    logs_for_analysis = suspicious_logs_found_in_scan
    log_start_time = None
    log_end_time = datetime.now(timezone.utc)

    termination_event_time = None
    if pod_info and pod_info.get('container_statuses'):
        for cs_name, cs_data in pod_info['container_statuses'].items():
            term_details = cs_data.get('terminated_details')
            last_term_details = cs_data.get('last_terminated_details')
            target_term = None
            if term_details and term_details.get('reason') in ["OOMKilled", "Error", "ContainerCannotRun", "DeadlineExceeded"]:
                target_term = term_details
            elif last_term_details and last_term_details.get('reason') in ["OOMKilled", "Error", "ContainerCannotRun", "DeadlineExceeded"]:
                target_term = last_term_details

            if target_term:
                event_ts_str = target_term.get('finished_at') or target_term.get('started_at')
                if event_ts_str:
                    try:
                        event_dt = datetime.fromisoformat(event_ts_str.replace('Z', '+00:00'))
                        if event_dt >= (log_end_time - timedelta(minutes=loki_detail_minutes)):
                            termination_event_time = event_dt
                            logging.info(f"Found relevant termination event for {namespace}/{pod_name}/{cs_name} at {termination_event_time.isoformat()}")
                            break
                    except ValueError:
                        logging.warning(f"Could not parse termination timestamp: {event_ts_str}")

    if termination_event_time:
        time_window_minutes = 5
        log_start_time = termination_event_time - timedelta(minutes=time_window_minutes)
        log_end_time = termination_event_time + timedelta(minutes=time_window_minutes)
        logging.info(f"Adjusting log query time for {namespace}/{pod_name} around event: {log_start_time.isoformat()} to {log_end_time.isoformat()}")
        if logs_for_analysis:
            logs_in_window = [log for log in logs_for_analysis if log_start_time <= log['timestamp'] <= log_end_time]
            if not logs_in_window:
                logging.info(f"Logs from initial scan are outside the event window for {namespace}/{pod_name}. Re-querying Loki.")
                logs_for_analysis = []
            else:
                logs_for_analysis = logs_in_window
                logging.info(f"Using {len(logs_for_analysis)} logs from initial scan within the event window.")

    if not logs_for_analysis:
        if log_start_time is None:
            log_start_time = log_end_time - timedelta(minutes=loki_detail_minutes)

        logging.info(f"Querying Loki for detailed logs for {namespace}/{pod_name}...")
        detailed_logs = loki_client.query_loki_for_pod(
            loki_url,
            namespace,
            pod_name,
            log_start_time,
            log_end_time,
            loki_limit
        )
        logs_for_analysis = detailed_logs

    max_logs_to_send = 100
    if len(logs_for_analysis) > max_logs_to_send:
        logging.warning(f"Trimming logs for {namespace}/{pod_name} from {len(logs_for_analysis)} to {max_logs_to_send}.")
        def get_log_timestamp(log_item):
            ts = log_item.get('timestamp')
            if isinstance(ts, datetime): return ts
            elif isinstance(ts, str):
                try: return datetime.fromisoformat(ts.replace('Z', '+00:00'))
                except ValueError: return datetime.min.replace(tzinfo=timezone.utc)
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            logs_for_analysis.sort(key=get_log_timestamp, reverse=True)
            logs_for_analysis = logs_for_analysis[:max_logs_to_send]
            logs_for_analysis.sort(key=get_log_timestamp)
        except Exception as sort_err:
             logging.error(f"Error sorting logs for {namespace}/{pod_name}: {sort_err}. Sending potentially unsorted/untrimmed logs.")

    serializable_logs = []
    for log_entry in logs_for_analysis:
        entry_copy = log_entry.copy()
        timestamp_val = entry_copy.get('timestamp')
        if isinstance(timestamp_val, datetime):
            entry_copy['timestamp'] = timestamp_val.isoformat()
        elif not isinstance(timestamp_val, str):
             entry_copy['timestamp'] = None
        if 'message' not in entry_copy or not isinstance(entry_copy['message'], str):
            entry_copy['message'] = str(entry_copy.get('message', ''))
        serializable_logs.append(entry_copy)

    return serializable_logs, k8s_context_str

def _ensure_cache_dir():
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logging.error(f"Could not create cache directory {CACHE_DIR}: {e}")

def _cache_failed_payload(payload):
    _ensure_cache_dir()
    try:
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        pod_key_safe = re.sub(r'[^\w\-.]', '_', payload.get("pod_key", "unknown_pod"))
        filename = CACHE_DIR / f"failed_{timestamp_str}_{pod_key_safe}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logging.warning(f"Failed to send payload for {payload.get('pod_key')}. Cached to {filename}")
    except Exception as e:
        logging.error(f"Error caching failed payload: {e}", exc_info=True)

def _send_cached_payloads(obs_engine_url):
    _ensure_cache_dir()
    sent_count = 0
    failed_count = 0
    try:
        cached_files = sorted(CACHE_DIR.glob("failed_*.json"))
        if not cached_files:
            return

        logging.info(f"Found {len(cached_files)} cached payloads. Attempting to resend...")

        for filepath in cached_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    payload = json.load(f)

                agent_id = payload.get("agent_id")
                if not agent_id:
                     config = config_manager.get_config()
                     agent_id = config.get('agent_id')
                     payload['agent_id'] = agent_id

                if not agent_id:
                     logging.error(f"Cannot resend cached payload {filepath}: Missing agent_id.")
                     failed_count += 1
                     continue

                # --- THÊM AGENT_VERSION VÀO PAYLOAD GỬI LẠI ---
                payload['agent_version'] = AGENT_VERSION
                # -----------------------------------------

                success = send_data_to_obs_engine(
                    obs_engine_url,
                    payload.get("cluster_name"),
                    agent_id,
                    payload.get("pod_key"),
                    payload.get("initial_reasons"),
                    payload.get("k8s_context"),
                    payload.get("logs"),
                    payload.get("cluster_info"),
                    payload.get("agent_version"), # Truyền version đã thêm
                    allow_cache=False
                )

                if success:
                    logging.info(f"Successfully resent cached payload: {filepath.name}")
                    try:
                        filepath.unlink()
                        sent_count += 1
                    except OSError as e:
                        logging.error(f"Error deleting cached file {filepath}: {e}")
                else:
                    logging.warning(f"Failed to resend cached payload: {filepath.name}. Will retry later.")
                    failed_count += 1

            except json.JSONDecodeError:
                logging.error(f"Error decoding cached file {filepath}. Deleting invalid file.")
                try: filepath.unlink()
                except OSError as e: logging.error(f"Error deleting invalid cached file {filepath}: {e}")
            except Exception as e:
                logging.error(f"Unexpected error processing cached file {filepath}: {e}", exc_info=True)
                failed_count += 1
            time.sleep(0.5)

        if sent_count > 0 or failed_count > 0:
             logging.info(f"Finished processing cached payloads. Sent: {sent_count}, Failed: {failed_count}")

    except Exception as e:
        logging.error(f"Error during cached payload processing: {e}", exc_info=True)


# --- CẬP NHẬT HÀM NÀY ---
def send_data_to_obs_engine(obs_engine_url, cluster_name, agent_id, pod_key, initial_reasons, k8s_context, logs, cluster_info, agent_version, allow_cache=True):
# -----------------------
    if not obs_engine_url:
        logging.error(f"ObsEngine URL not configured. Cannot send data for {pod_key}.")
        return False
    if not agent_id:
        logging.error(f"AGENT_ID not configured. Cannot send data for {pod_key}.")
        return False

    payload = {
        "cluster_name": cluster_name,
        "agent_id": agent_id,
        "pod_key": pod_key,
        "collection_timestamp": datetime.now(timezone.utc).isoformat(),
        "initial_reasons": initial_reasons,
        "k8s_context": k8s_context,
        "logs": logs,
        "cluster_info": cluster_info,
        "agent_version": agent_version # Thêm version vào payload
    }

    log_payload = {k: v for k, v in payload.items() if k not in ['logs', 'k8s_context']}
    log_payload_size = len(json.dumps(payload))
    logging.debug(f"Sending payload to ObsEngine (Agent: {agent_id}, Pod: {pod_key}, Size: ~{log_payload_size} bytes, Version: {agent_version}): {json.dumps(log_payload)}")

    try:
        headers = {'Content-Type': 'application/json'}
        response = requests.post(obs_engine_url + "/collect", headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        logging.info(f"Successfully sent data for {pod_key} from cluster {cluster_name} (Agent: {agent_id}) to ObsEngine. Status: {response.status_code}")
        return True
    except requests.exceptions.Timeout:
        logging.error(f"Timeout sending data for {pod_key} from cluster {cluster_name} (Agent: {agent_id}) to {obs_engine_url}.")
        if allow_cache: _cache_failed_payload(payload)
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending data for {pod_key} from cluster {cluster_name} (Agent: {agent_id}) to ObsEngine: {e}")
        if e.response is not None:
            try:
                logging.error(f"ObsEngine Response Status: {e.response.status_code}")
                response_text = e.response.text[:500] + ('...' if len(e.response.text) > 500 else '')
                logging.error(f"ObsEngine Response Body: {response_text}")
            except Exception as log_err:
                logging.error(f"Could not log ObsEngine response details: {log_err}")
        if allow_cache: _cache_failed_payload(payload)
        return False
    except Exception as e:
        logging.error(f"Unexpected error sending data for {pod_key} from cluster {cluster_name} (Agent: {agent_id}): {e}", exc_info=True)
        if allow_cache: _cache_failed_payload(payload)
        return False

def perform_monitoring_cycle(last_namespace_refresh_time):
    start_cycle_time_dt = datetime.now(timezone.utc)
    cycle_start_ts_perf = time.perf_counter()
    logging.info("--- Starting new monitoring cycle (Collector Agent) ---")

    config = config_manager.get_config()
    current_scan_interval = config.get('scan_interval_seconds', 30)
    obs_engine_url = config.get('obs_engine_url')
    cluster_name = config.get('cluster_name')
    agent_id = config.get('agent_id')

    if obs_engine_url:
        _send_cached_payloads(obs_engine_url)
    else:
        logging.warning("OBS_ENGINE_URL not set, skipping resend of cached payloads.")

    cluster_summary = get_cached_cluster_summary()

    if not obs_engine_url:
        logging.error("OBS_ENGINE_URL is not configured. Agent cannot forward data.")
        sleep_time = max(0, current_scan_interval - (time.perf_counter() - cycle_start_ts_perf))
        logging.info(f"--- Cycle finished early (ObsEngine URL missing). Sleeping for {sleep_time:.2f}s ---")
        time.sleep(sleep_time)
        return last_namespace_refresh_time
    if not agent_id:
         logging.error("AGENT_ID is not configured. Agent cannot identify itself to ObsEngine.")
         sleep_time = max(0, current_scan_interval - (time.perf_counter() - cycle_start_ts_perf))
         logging.info(f"--- Cycle finished early (AGENT_ID missing). Sleeping for {sleep_time:.2f}s ---")
         time.sleep(sleep_time)
         return last_namespace_refresh_time

    if not cluster_name or cluster_name == config_manager.DEFAULT_K8S_CLUSTER_NAME:
        logging.warning(f"K8S_CLUSTER_NAME is not set or using default '{config_manager.DEFAULT_K8S_CLUSTER_NAME}'. Data sent might be harder to distinguish.")

    monitored_namespaces = config.get('monitored_namespaces', [])

    if not monitored_namespaces:
        logging.warning("No namespaces configured for monitoring. Skipping K8s/Loki scan.")
        cycle_duration = time.perf_counter() - cycle_start_ts_perf
        sleep_time = max(0, current_scan_interval - cycle_duration)
        logging.info(f"--- Cycle finished early (no namespaces) in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
        time.sleep(sleep_time)
        return last_namespace_refresh_time

    logging.info(f"Agent ID: {agent_id} | Cluster Name: {cluster_name}")
    logging.info(f"Currently monitoring {len(monitored_namespaces)} namespaces: {', '.join(monitored_namespaces)}")
    pods_to_investigate = identify_pods_to_investigate(monitored_namespaces, start_cycle_time_dt, config)

    collection_count = 0
    for pod_key, data in pods_to_investigate.items():
        collection_count += 1
        try:
            namespace, pod_name = pod_key.split('/', 1)
        except ValueError:
             logging.error(f"Invalid pod_key format found: {pod_key}. Skipping.")
             continue

        initial_reasons = data.get("reason", [])
        suspicious_logs_found_in_scan = data.get("logs", [])

        try:
            logs_collected, k8s_context_str = collect_pod_details(
                namespace, pod_name, "; ".join(initial_reasons), suspicious_logs_found_in_scan, config
            )
            # --- TRUYỀN AGENT_VERSION VÀO HÀM GỬI ---
            send_data_to_obs_engine(
                obs_engine_url,
                cluster_name,
                agent_id,
                pod_key,
                initial_reasons,
                k8s_context_str,
                logs_collected,
                cluster_summary,
                AGENT_VERSION # Truyền hằng số version
            )
            # ---------------------------------------
        except Exception as collection_err:
            logging.error(f"Error during data collection/sending loop for {pod_key}: {collection_err}", exc_info=True)

        time.sleep(0.2)

    if collection_count > 0:
         logging.info(f"Finished data collection attempt for {collection_count} pods.")

    cycle_duration = time.perf_counter() - cycle_start_ts_perf
    sleep_time = max(0, current_scan_interval - cycle_duration)
    logging.info(f"--- Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
    time.sleep(sleep_time)
    return last_namespace_refresh_time

def main_loop():
    last_namespace_refresh_time = 0
    logging.info("Entering main loop for Collector Agent...")
    while True:
        try:
            logging.debug("Calling perform_monitoring_cycle...")
            last_namespace_refresh_time = perform_monitoring_cycle(last_namespace_refresh_time)
            logging.debug("perform_monitoring_cycle finished.")
        except Exception as cycle_err:
             logging.critical(f"Unhandled exception in monitoring cycle: {cycle_err}", exc_info=True)
             try:
                 config = config_manager.get_config()
                 sleep_interval = config.get('scan_interval_seconds', 30)
             except Exception:
                 sleep_interval = 30
             logging.info(f"Sleeping for {sleep_interval} seconds before next cycle after error.")
             time.sleep(sleep_interval)
             last_namespace_refresh_time = 0

if __name__ == "__main__":
    logging.info(f"Collector Agent script execution started. Version: {AGENT_VERSION}") # Log version ở đây nữa
    logging.info("Loading initial agent configuration...")
    initial_config = config_manager.get_config(force_refresh=True)
    logging.info("Initial agent configuration loaded.")
    logging.info(f"Agent ID: {initial_config.get('agent_id', 'Not Set')}")
    logging.info(f"Loki URL: {initial_config.get('loki_url', 'Not Set')}")
    logging.info(f"ObsEngine URL: {initial_config.get('obs_engine_url', 'Not Set')}")
    logging.info(f"Cluster Name: {initial_config.get('cluster_name', 'Not Set')}")
    logging.info(f"Scan Interval: {initial_config.get('scan_interval_seconds', 'Default')}s")
    logging.info(f"Monitored Namespaces: {initial_config.get('monitored_namespaces', 'Default')}")

    logging.info("Starting main monitoring loop...")
    try:
        main_loop()
    except KeyboardInterrupt:
        logging.info("Agent stopped by user (KeyboardInterrupt).")
    except Exception as main_err:
        logging.critical(f"Unhandled exception in main execution: {main_err}", exc_info=True)
    finally:
        logging.info("Collector Agent shutdown complete.")

