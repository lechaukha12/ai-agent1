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
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    stream=sys.stdout)

logging.info("Logging configured successfully for Collector Agent.")

LOKI_URL = os.environ.get("LOKI_URL", "http://loki-read.monitoring.svc.cluster.local:3100")
LOKI_SCAN_RANGE_MINUTES = int(os.environ.get("LOKI_SCAN_RANGE_MINUTES", 1))
LOKI_DETAIL_LOG_RANGE_MINUTES = int(os.environ.get("LOKI_DETAIL_LOG_RANGE_MINUTES", 30))
LOKI_QUERY_LIMIT = int(os.environ.get("LOKI_QUERY_LIMIT", 500))
EXCLUDED_NAMESPACES_STR = os.environ.get("EXCLUDED_NAMESPACES", "kube-node-lease,kube-public")
EXCLUDED_NAMESPACES = {ns.strip() for ns in EXCLUDED_NAMESPACES_STR.split(',') if ns.strip()}
NAMESPACE_REFRESH_INTERVAL_SECONDS = int(os.environ.get("NAMESPACE_REFRESH_INTERVAL_SECONDS", 3600))

if not k8s_monitor.initialize_k8s_client():
     logging.warning("Kubernetes client initialization failed. K8s context features will be unavailable.")

def refresh_namespaces_if_needed(last_refresh_time):
    current_time_secs = time.time()
    if current_time_secs - last_refresh_time >= NAMESPACE_REFRESH_INTERVAL_SECONDS:
        logging.info("Refreshing list of active namespaces from Kubernetes API (for logging purposes)...")
        all_active_namespaces = k8s_monitor.get_active_namespaces(EXCLUDED_NAMESPACES)
        logging.info(f"Refreshed available namespaces list. Found {len(all_active_namespaces)} active.")
        return current_time_secs
    return last_refresh_time

def identify_pods_to_investigate(monitored_namespaces, start_cycle_time, config):
    restart_threshold = config.get('restart_count_threshold', 5)
    loki_scan_min_level = config.get('loki_scan_min_level', 'INFO')
    loki_url = config.get('loki_url')

    if not loki_url:
        logging.error("LOKI_URL is not configured. Cannot scan Loki.")
        return {}

    k8s_problem_pods = k8s_monitor.scan_kubernetes_for_issues(
        monitored_namespaces,
        restart_threshold,
        LOKI_DETAIL_LOG_RANGE_MINUTES
    )

    loki_scan_end_time = start_cycle_time
    loki_scan_start_time = loki_scan_end_time - timedelta(minutes=LOKI_SCAN_RANGE_MINUTES)
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
            pods_to_investigate[pod_key] = {"reason": [], "logs": []}
        reason_str = data.get("reason", "Unknown K8s Reason")
        if reason_str not in pods_to_investigate[pod_key]["reason"]:
             pods_to_investigate[pod_key]["reason"].append(reason_str)

    for pod_key, logs in loki_suspicious_logs.items():
            if pod_key not in pods_to_investigate:
                pods_to_investigate[pod_key] = {"reason": [], "logs": []}
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

    if not logs_for_analysis:
        logging.info(f"No logs found in initial scan for {namespace}/{pod_name}. Querying Loki for detailed logs...")
        log_end_time = datetime.now(timezone.utc)
        log_start_time = log_end_time - timedelta(minutes=loki_detail_minutes)
        detailed_logs = loki_client.query_loki_for_pod(
            loki_url,
            namespace,
            pod_name,
            log_start_time,
            log_end_time,
            loki_limit
        )
        logs_for_analysis = detailed_logs
    else:
        logging.info(f"Using {len(logs_for_analysis)} logs found during initial scan for {namespace}/{pod_name}.")

    max_logs_to_send = 100
    if len(logs_for_analysis) > max_logs_to_send:
        logging.warning(f"Trimming logs for {namespace}/{pod_name} from {len(logs_for_analysis)} to {max_logs_to_send}.")
        def get_log_timestamp(log_item):
            ts = log_item.get('timestamp')
            if isinstance(ts, datetime):
                return ts
            elif isinstance(ts, str):
                try:
                    return datetime.fromisoformat(ts.replace('Z', '+00:00'))
                except ValueError:
                    return datetime.min.replace(tzinfo=timezone.utc)
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

def send_data_to_obs_engine(obs_engine_url, cluster_name, pod_key, initial_reasons, k8s_context, logs):
    if not obs_engine_url:
        logging.error(f"ObsEngine URL not configured. Cannot send data for {pod_key}.")
        return False

    payload = {
        "cluster_name": cluster_name,
        "agent_id": cluster_name,
        "pod_key": pod_key,
        "collection_timestamp": datetime.now(timezone.utc).isoformat(),
        "initial_reasons": initial_reasons,
        "k8s_context": k8s_context,
        "logs": logs
    }
    logging.debug(f"Sending payload to ObsEngine: {json.dumps(payload, indent=2)}")

    try:
        headers = {'Content-Type': 'application/json'}
        response = requests.post(obs_engine_url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        logging.info(f"Successfully sent data for {pod_key} from cluster {cluster_name} to ObsEngine. Status: {response.status_code}")
        return True
    except requests.exceptions.Timeout:
        logging.error(f"Timeout sending data for {pod_key} from cluster {cluster_name} to {obs_engine_url}.")
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending data for {pod_key} from cluster {cluster_name} to ObsEngine: {e}")
        if e.response is not None:
            try:
                logging.error(f"ObsEngine Response Status: {e.response.status_code}")
                response_text = e.response.text[:500] + ('...' if len(e.response.text) > 500 else '')
                logging.error(f"ObsEngine Response Body: {response_text}")
            except Exception as log_err:
                logging.error(f"Could not log ObsEngine response details: {log_err}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error sending data for {pod_key} from cluster {cluster_name}: {e}", exc_info=True)
        return False

def perform_monitoring_cycle(last_namespace_refresh_time):
    start_cycle_time_dt = datetime.now(timezone.utc)
    cycle_start_ts_perf = time.perf_counter()
    logging.info("--- Starting new monitoring cycle (Collector Agent) ---")

    config = config_manager.get_config()
    current_scan_interval = config.get('scan_interval_seconds', 30)
    obs_engine_url = config.get('obs_engine_url')
    cluster_name = config.get('cluster_name')

    if not obs_engine_url:
        logging.error("OBS_ENGINE_URL is not configured. Agent cannot forward data.")
        cycle_duration = time.perf_counter() - cycle_start_ts_perf
        sleep_time = max(0, current_scan_interval - cycle_duration)
        logging.info(f"--- Cycle finished early (ObsEngine URL missing) in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f}s ---")
        time.sleep(sleep_time)
        return last_namespace_refresh_time

    if not cluster_name or cluster_name == config_manager.DEFAULT_K8S_CLUSTER_NAME:
        logging.warning(f"K8S_CLUSTER_NAME is not set or using default '{config_manager.DEFAULT_K8S_CLUSTER_NAME}'. Data sent might be harder to distinguish.")


    last_namespace_refresh_time = refresh_namespaces_if_needed(last_namespace_refresh_time)
    monitored_namespaces = config_manager.get_monitored_namespaces()

    if not monitored_namespaces:
        logging.warning("No namespaces configured for monitoring. Skipping K8s/Loki scan.")
        cycle_duration = time.perf_counter() - cycle_start_ts_perf
        sleep_time = max(0, current_scan_interval - cycle_duration)
        logging.info(f"--- Cycle finished early (no namespaces) in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
        time.sleep(sleep_time)
        return last_namespace_refresh_time

    logging.info(f"Agent ID/Cluster Name: {cluster_name}")
    logging.info(f"Currently monitoring {len(monitored_namespaces)} namespaces: {', '.join(monitored_namespaces)}")
    pods_to_investigate = identify_pods_to_investigate(monitored_namespaces, start_cycle_time_dt, config)

    collection_count = 0
    for pod_key, data in pods_to_investigate.items():
        collection_count += 1
        namespace, pod_name = pod_key.split('/', 1)
        initial_reasons = data.get("reason", [])
        suspicious_logs_found_in_scan = data.get("logs", [])

        try:
            logs_collected, k8s_context_str = collect_pod_details(
                namespace, pod_name, "; ".join(initial_reasons), suspicious_logs_found_in_scan, config
            )
            send_data_to_obs_engine(
                obs_engine_url,
                cluster_name,
                pod_key,
                initial_reasons,
                k8s_context_str,
                logs_collected
            )
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
    logging.info("Collector Agent script execution started.")
    logging.info("Loading initial agent configuration...")
    initial_config = config_manager.get_config(force_refresh=True)
    logging.info("Initial agent configuration loaded.")
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

