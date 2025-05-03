# ai-agent1/app/main.py
import os
import time
import json
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import re
import asyncio # Import asyncio
import aiohttp # Import aiohttp
import sys
import pathlib
import random # Import random để thêm jitter vào retry

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

AGENT_VERSION = "v1.0.7"

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format='%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s',
                    stream=sys.stdout)

logging.info(f"Logging configured successfully for Collector Agent - Version: {AGENT_VERSION}")

CACHE_DIR = pathlib.Path("/cache")

# --- Biến global cho cache cluster summary (giữ nguyên) ---
current_cluster_summary = None
last_cluster_summary_refresh = 0
CLUSTER_SUMMARY_REFRESH_INTERVAL = 3600
# Lock để tránh refresh summary đồng thời
_cluster_summary_lock = asyncio.Lock()

# --- Hàm async để lấy cluster summary ---
async def get_cached_cluster_summary():
    global current_cluster_summary, last_cluster_summary_refresh
    now = time.time()
    async with _cluster_summary_lock:
        if not current_cluster_summary or (now - last_cluster_summary_refresh > CLUSTER_SUMMARY_REFRESH_INTERVAL):
            logging.info("Refreshing cluster summary (version, node count)...")
            # Gọi hàm async từ k8s_monitor
            summary_data = await k8s_monitor.get_cluster_summary()
            current_cluster_summary = summary_data
            last_cluster_summary_refresh = now
            logging.info(f"Cluster summary refreshed: {current_cluster_summary}")
        return current_cluster_summary

# --- Chuyển thành hàm async ---
async def identify_pods_to_investigate(session, monitored_namespaces, start_cycle_time, config):
    """Identifies pods needing investigation asynchronously."""
    restart_threshold = config.get('restart_count_threshold', 5)
    loki_scan_min_level = config.get('loki_scan_min_level', 'INFO')
    loki_url = config.get('loki_url')
    loki_detail_minutes = config.get('loki_detail_log_range_minutes', 30)

    if not loki_url:
        logging.error("LOKI_URL is not configured. Cannot scan Loki.")
        return {}

    # Chạy K8s scan và Loki scan song song
    logging.info("Starting K8s and Loki scans concurrently...")
    k8s_scan_task = asyncio.create_task(k8s_monitor.scan_kubernetes_for_issues(
        monitored_namespaces,
        restart_threshold,
        loki_detail_minutes
    ))

    loki_scan_range_minutes = config.get('loki_scan_range_minutes', 1)
    loki_scan_end_time = start_cycle_time
    loki_scan_start_time = loki_scan_end_time - timedelta(minutes=loki_scan_range_minutes)
    loki_scan_task = asyncio.create_task(loki_client.scan_loki_for_suspicious_logs(
        session, # Truyền session vào
        loki_url,
        loki_scan_start_time,
        loki_scan_end_time,
        monitored_namespaces,
        loki_scan_min_level
    ))

    # Đợi cả hai task hoàn thành
    k8s_problem_pods, loki_suspicious_logs = await asyncio.gather(k8s_scan_task, loki_scan_task)
    logging.info("K8s and Loki scans finished.")

    # Xử lý kết quả (giống như trước)
    pods_to_investigate = {}
    if k8s_problem_pods: # Kiểm tra None hoặc lỗi
        for pod_key, data in k8s_problem_pods.items():
            if pod_key not in pods_to_investigate:
                pods_to_investigate[pod_key] = {"reason": [], "logs": [], "k8s_issue_data": data}
            reason_str = data.get("reason", "Unknown K8s Reason")
            if reason_str not in pods_to_investigate[pod_key]["reason"]:
                 pods_to_investigate[pod_key]["reason"].append(reason_str)

    if loki_suspicious_logs: # Kiểm tra None hoặc lỗi
        for pod_key, logs in loki_suspicious_logs.items():
                if pod_key not in pods_to_investigate:
                    pods_to_investigate[pod_key] = {"reason": [], "logs": [], "k8s_issue_data": None}
                reason_text = f"Loki: Found {len(logs)} suspicious logs (level >= {loki_scan_min_level})"
                if reason_text not in pods_to_investigate[pod_key]["reason"]:
                     pods_to_investigate[pod_key]["reason"].append(reason_text)
                # Chỉ thêm log nếu chưa có log từ K8s (hoặc logic khác tùy ý)
                if not pods_to_investigate[pod_key]["logs"]:
                    pods_to_investigate[pod_key]["logs"].extend(logs)

    logging.info(f"Identified {len(pods_to_investigate)} pods for potential data collection this cycle.")
    return pods_to_investigate

# --- Chuyển thành hàm async ---
async def collect_pod_details(session, namespace, pod_name, initial_reasons, suspicious_logs_found_in_scan, config):
    """Collects pod details asynchronously."""
    logging.info(f"Collecting details for pod: {namespace}/{pod_name} (Initial Reasons: {initial_reasons})")
    loki_url = config.get('loki_url')
    loki_detail_minutes = config.get('loki_detail_log_range_minutes', 30)
    loki_limit = config.get('loki_query_limit', 500)

    if not loki_url:
        logging.error(f"LOKI_URL not configured. Cannot fetch detailed logs for {namespace}/{pod_name}.")
        return [], ""

    # Lấy thông tin K8s song song
    logging.debug(f"Fetching K8s details concurrently for {namespace}/{pod_name}...")
    pod_info_task = asyncio.create_task(k8s_monitor.get_pod_info(namespace, pod_name))
    # Node info phụ thuộc pod_info, nên sẽ lấy sau
    events_task = asyncio.create_task(k8s_monitor.get_pod_events(namespace, pod_name, since_minutes=loki_detail_minutes + 10)) # Tăng time window events

    pod_info = await pod_info_task
    node_info = None
    if pod_info and pod_info.get('node_name'):
        node_info = await k8s_monitor.get_node_info(pod_info.get('node_name'))

    pod_events = await events_task
    logging.debug(f"Finished fetching K8s details for {namespace}/{pod_name}.")

    # Format context K8s (cần await vì nó gọi get_controller_info và get_namespace_resource_info async)
    k8s_context_str = await k8s_monitor.format_k8s_context(pod_info, node_info, pod_events)

    # Xử lý logic lấy log (giống như trước, nhưng gọi hàm query_loki_for_pod async)
    logs_for_analysis = suspicious_logs_found_in_scan
    log_start_time = None
    log_end_time = datetime.now(timezone.utc)

    termination_event_time = None
    if pod_info and pod_info.get('container_statuses'):
        # ... (logic tìm termination_event_time giữ nguyên) ...
        for cs_name, cs_data in pod_info['container_statuses'].items():
            term_details = cs_data.get('terminated_details')
            last_term_details = cs_data.get('last_terminated_details')
            target_term = None
            if term_details and term_details.get('reason') in ["OOMKilled", "Error", "ContainerCannotRun", "DeadlineExceeded"]: target_term = term_details
            elif last_term_details and last_term_details.get('reason') in ["OOMKilled", "Error", "ContainerCannotRun", "DeadlineExceeded"]: target_term = last_term_details
            if target_term:
                event_ts_str = target_term.get('finished_at') or target_term.get('started_at')
                if event_ts_str:
                    try:
                        event_dt = datetime.fromisoformat(event_ts_str.replace('Z', '+00:00'))
                        if event_dt >= (log_end_time - timedelta(minutes=loki_detail_minutes)):
                            termination_event_time = event_dt; break
                    except ValueError: pass

    if termination_event_time:
        time_window_minutes = 5
        log_start_time = termination_event_time - timedelta(minutes=time_window_minutes)
        log_end_time = termination_event_time + timedelta(minutes=time_window_minutes)
        logging.info(f"Adjusting log query time for {namespace}/{pod_name} around event: {log_start_time.isoformat()} to {log_end_time.isoformat()}")
        if logs_for_analysis:
            logs_in_window = [log for log in logs_for_analysis if log_start_time <= log['timestamp'] <= log_end_time]
            if not logs_in_window: logs_for_analysis = []
            else: logs_for_analysis = logs_in_window

    if not logs_for_analysis:
        if log_start_time is None:
            log_start_time = datetime.now(timezone.utc) - timedelta(minutes=loki_detail_minutes)
            log_end_time = datetime.now(timezone.utc) # Cập nhật lại end_time

        logging.info(f"Querying Loki for detailed logs for {namespace}/{pod_name} (async)...")
        # Gọi hàm async từ loki_client
        detailed_logs = await loki_client.query_loki_for_pod(
            session, loki_url, namespace, pod_name,
            log_start_time, log_end_time, loki_limit
        )
        logs_for_analysis = detailed_logs

    # Xử lý giới hạn và serialize log (giữ nguyên)
    max_logs_to_send = 100
    if len(logs_for_analysis) > max_logs_to_send:
        # ... (logic trim log giữ nguyên) ...
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
             logging.error(f"Error sorting logs for {namespace}/{pod_name}: {sort_err}.")


    serializable_logs = []
    for log_entry in logs_for_analysis:
        entry_copy = log_entry.copy()
        timestamp_val = entry_copy.get('timestamp')
        if isinstance(timestamp_val, datetime): entry_copy['timestamp'] = timestamp_val.isoformat()
        elif not isinstance(timestamp_val, str): entry_copy['timestamp'] = None
        if 'message' not in entry_copy or not isinstance(entry_copy['message'], str): entry_copy['message'] = str(entry_copy.get('message', ''))
        serializable_logs.append(entry_copy)

    return serializable_logs, k8s_context_str

# --- Cache logic (giữ nguyên, vẫn dùng I/O đồng bộ) ---
def _ensure_cache_dir():
    try: CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e: logging.error(f"Could not create cache directory {CACHE_DIR}: {e}")

def _cache_failed_payload(payload):
    _ensure_cache_dir()
    try:
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        pod_key_safe = re.sub(r'[^\w\-.]', '_', payload.get("pod_key", "unknown_pod"))
        filename = CACHE_DIR / f"failed_{timestamp_str}_{pod_key_safe}.json"
        with open(filename, 'w', encoding='utf-8') as f: json.dump(payload, f, ensure_ascii=False, indent=2)
        logging.warning(f"Failed to send payload for {payload.get('pod_key')}. Cached to {filename}")
    except Exception as e: logging.error(f"Error caching failed payload: {e}", exc_info=True)

# --- Chuyển thành hàm async ---
async def _send_cached_payloads_async(session, obs_engine_url):
    """Sends cached payloads asynchronously."""
    _ensure_cache_dir()
    sent_count = 0
    failed_count = 0
    resend_tasks = []

    try:
        cached_files = sorted(CACHE_DIR.glob("failed_*.json"))
        if not cached_files: return

        logging.info(f"Found {len(cached_files)} cached payloads. Attempting to resend asynchronously...")

        # Tạo task gửi lại cho mỗi file cache
        for filepath in cached_files:
             resend_tasks.append(asyncio.create_task(_process_single_cached_file(session, obs_engine_url, filepath)))

        # Đợi tất cả task hoàn thành
        results = await asyncio.gather(*resend_tasks)

        # Đếm kết quả
        for success, filepath in results:
             if success: sent_count += 1
             else: failed_count += 1

        if sent_count > 0 or failed_count > 0:
             logging.info(f"Finished processing cached payloads. Sent: {sent_count}, Failed: {failed_count}")

    except Exception as e:
        logging.error(f"Error during async cached payload processing: {e}", exc_info=True)

# --- Hàm helper async để xử lý 1 file cache ---
async def _process_single_cached_file(session, obs_engine_url, filepath):
     """Processes and attempts to resend a single cached payload file."""
     try:
         with open(filepath, 'r', encoding='utf-8') as f:
             payload = json.load(f)

         agent_id = payload.get("agent_id") or config_manager.get_agent_id() # Lấy agent_id
         payload['agent_id'] = agent_id
         payload['agent_version'] = AGENT_VERSION # Đảm bảo có version

         if not agent_id:
              logging.error(f"Cannot resend cached payload {filepath.name}: Missing agent_id.")
              return False, filepath # Trả về False và filepath

         # Gọi hàm send async (không cache lại)
         success = await send_data_to_obs_engine_async(
             session, obs_engine_url,
             payload.get("cluster_name"), agent_id, payload.get("pod_key"),
             payload.get("initial_reasons"), payload.get("k8s_context"),
             payload.get("logs"), payload.get("cluster_info"), payload.get("agent_version"),
             allow_cache=False
         )

         if success:
             logging.info(f"Successfully resent cached payload: {filepath.name}")
             try: filepath.unlink()
             except OSError as e: logging.error(f"Error deleting cached file {filepath}: {e}")
             return True, filepath # Trả về True và filepath
         else:
             logging.warning(f"Failed to resend cached payload: {filepath.name}. Will retry later.")
             return False, filepath # Trả về False và filepath

     except json.JSONDecodeError:
         logging.error(f"Error decoding cached file {filepath}. Deleting invalid file.")
         try: filepath.unlink()
         except OSError as e: logging.error(f"Error deleting invalid cached file {filepath}: {e}")
         return False, filepath # Coi như thất bại
     except Exception as e:
         logging.error(f"Unexpected error processing cached file {filepath}: {e}", exc_info=True)
         return False, filepath # Coi như thất bại

# --- Chuyển thành hàm async, thêm retry ---
async def send_data_to_obs_engine_async(session, obs_engine_url, cluster_name, agent_id, pod_key, initial_reasons, k8s_context, logs, cluster_info, agent_version, allow_cache=True, max_retries=2, initial_delay=5):
    """Sends data to ObsEngine asynchronously with retry."""
    if not obs_engine_url: logging.error(f"ObsEngine URL not configured..."); return False
    if not agent_id: logging.error(f"AGENT_ID not configured..."); return False

    endpoint = obs_engine_url.rstrip('/') + "/collect" # Đảm bảo đúng endpoint
    payload = {
        "cluster_name": cluster_name, "agent_id": agent_id, "pod_key": pod_key,
        "collection_timestamp": datetime.now(timezone.utc).isoformat(),
        "initial_reasons": initial_reasons, "k8s_context": k8s_context, "logs": logs,
        "cluster_info": cluster_info, "agent_version": agent_version
    }

    log_payload = {k: v for k, v in payload.items() if k not in ['logs', 'k8s_context']}
    try: log_payload_size = len(json.dumps(payload))
    except Exception: log_payload_size = -1
    logging.debug(f"Attempting to send payload (Agent: {agent_id}, Pod: {pod_key}, Size: ~{log_payload_size} bytes, Version: {agent_version})")

    retries = 0
    delay = initial_delay
    last_exception = None

    while retries <= max_retries:
        try:
            headers = {'Content-Type': 'application/json'}
            async with session.post(endpoint, headers=headers, json=payload, timeout=30) as response:
                if response.status >= 400:
                    if response.status in [429, 500, 502, 503, 504]:
                         response.raise_for_status() # Trigger retry
                    else:
                         # Log lỗi không retry và trả về False
                         error_text = await response.text()
                         logging.error(f"ObsEngine returned non-retryable error {response.status} for {pod_key}: {error_text[:500]}")
                         if allow_cache: _cache_failed_payload(payload)
                         return False
                # Nếu thành công (2xx)
                logging.info(f"Successfully sent data for {pod_key} (Agent: {agent_id}). Status: {response.status}")
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exception = e
            retries += 1
            logging.warning(f"Error sending data for {pod_key} ({type(e).__name__}). Retrying ({retries}/{max_retries}) in {delay}s...")
            if retries > max_retries:
                logging.error(f"Send data failed after {max_retries} retries for {pod_key}: {last_exception}")
                if allow_cache: _cache_failed_payload(payload)
                return False
            await asyncio.sleep(delay + random.uniform(0, 1)) # Thêm jitter
            delay *= 2
        except Exception as e:
            logging.error(f"Unexpected error sending data for {pod_key}: {e}", exc_info=True)
            if allow_cache: _cache_failed_payload(payload)
            return False # Lỗi không mong muốn, không retry

    logging.error(f"Send data failed definitively after retries for {pod_key}: {last_exception}")
    if allow_cache: _cache_failed_payload(payload)
    return False

# --- Chuyển thành hàm async ---
async def perform_monitoring_cycle(session):
    """Executes one round of monitoring asynchronously."""
    start_cycle_time_dt = datetime.now(timezone.utc)
    cycle_start_ts_perf = time.perf_counter()
    logging.info("--- Starting new monitoring cycle (Collector Agent - Async) ---")

    # Lấy config bất đồng bộ
    config = await config_manager.get_config(session)
    current_scan_interval = config.get('scan_interval_seconds', 30)
    obs_engine_url = config.get('obs_engine_url')
    cluster_name = config.get('cluster_name')
    agent_id = config.get('agent_id')

    # Gửi lại cache bất đồng bộ
    if obs_engine_url:
        await _send_cached_payloads_async(session, obs_engine_url)
    else:
        logging.warning("OBS_ENGINE_URL not set, skipping resend of cached payloads.")

    # Lấy cluster summary bất đồng bộ
    cluster_summary_task = asyncio.create_task(get_cached_cluster_summary())

    if not obs_engine_url or not agent_id:
        logging.error("OBS_ENGINE_URL or AGENT_ID is not configured. Agent cannot operate correctly.")
        # Vẫn đợi cluster summary xong trước khi sleep
        await cluster_summary_task
        sleep_time = max(0, current_scan_interval - (time.perf_counter() - cycle_start_ts_perf))
        logging.info(f"--- Cycle finished early (config missing). Sleeping for {sleep_time:.2f}s ---")
        await asyncio.sleep(sleep_time)
        return

    if not cluster_name or cluster_name == config_manager.DEFAULT_K8S_CLUSTER_NAME:
        logging.warning(f"K8S_CLUSTER_NAME not set or using default.")

    monitored_namespaces = config.get('monitored_namespaces', [])

    if not monitored_namespaces:
        logging.warning("No namespaces configured for monitoring. Skipping K8s/Loki scan.")
        await cluster_summary_task # Đảm bảo task hoàn thành
        cycle_duration = time.perf_counter() - cycle_start_ts_perf
        sleep_time = max(0, current_scan_interval - cycle_duration)
        logging.info(f"--- Cycle finished early (no namespaces) in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
        await asyncio.sleep(sleep_time)
        return

    logging.info(f"Agent ID: {agent_id} | Cluster Name: {cluster_name}")
    logging.info(f"Currently monitoring {len(monitored_namespaces)} namespaces: {', '.join(monitored_namespaces)}")

    # Xác định pod cần điều tra (đã bao gồm K8s và Loki scan song song)
    pods_to_investigate = await identify_pods_to_investigate(session, monitored_namespaces, start_cycle_time_dt, config)

    # Đợi lấy cluster summary (nếu chưa xong)
    cluster_summary = await cluster_summary_task

    # Thu thập và gửi dữ liệu cho các pod song song
    collection_tasks = []
    if pods_to_investigate:
         logging.info(f"Creating data collection tasks for {len(pods_to_investigate)} pods...")
         for pod_key, data in pods_to_investigate.items():
             try:
                 namespace, pod_name = pod_key.split('/', 1)
                 initial_reasons = data.get("reason", [])
                 suspicious_logs = data.get("logs", [])
                 # Tạo task cho mỗi pod
                 collection_tasks.append(asyncio.create_task(
                     process_single_pod(session, namespace, pod_name, initial_reasons, suspicious_logs, config, cluster_summary, obs_engine_url, agent_id, cluster_name)
                 ))
             except ValueError:
                  logging.error(f"Invalid pod_key format found: {pod_key}. Skipping.")
                  continue
             except Exception as task_create_err:
                  logging.error(f"Error creating task for pod {pod_key}: {task_create_err}", exc_info=True)

    # Đợi tất cả các task thu thập/gửi hoàn thành
    if collection_tasks:
        await asyncio.gather(*collection_tasks)
        logging.info(f"Finished processing {len(collection_tasks)} pods.")

    cycle_duration = time.perf_counter() - cycle_start_ts_perf
    sleep_time = max(0, current_scan_interval - cycle_duration)
    logging.info(f"--- Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
    await asyncio.sleep(sleep_time) # Dùng asyncio.sleep

# --- Hàm helper async để xử lý một pod ---
async def process_single_pod(session, namespace, pod_name, initial_reasons, suspicious_logs, config, cluster_summary, obs_engine_url, agent_id, cluster_name):
    """Collects details and sends data for a single pod asynchronously."""
    pod_key = f"{namespace}/{pod_name}"
    try:
        logs_collected, k8s_context_str = await collect_pod_details(
            session, namespace, pod_name, "; ".join(initial_reasons), suspicious_logs, config
        )
        await send_data_to_obs_engine_async(
            session, obs_engine_url, cluster_name, agent_id, pod_key,
            initial_reasons, k8s_context_str, logs_collected,
            cluster_summary, AGENT_VERSION
        )
    except Exception as collection_err:
        logging.error(f"Error during async data collection/sending loop for {pod_key}: {collection_err}", exc_info=True)

# --- Chuyển thành hàm async ---
async def main_loop(session):
    """Runs the monitoring cycle repeatedly asynchronously."""
    logging.info("Entering main loop for Collector Agent (async)...")
    while True:
        try:
            logging.debug("Calling perform_monitoring_cycle...")
            # Truyền session vào
            await perform_monitoring_cycle(session)
            logging.debug("perform_monitoring_cycle finished.")
        except asyncio.CancelledError:
             logging.info("Main loop cancelled.")
             break
        except Exception as cycle_err:
             logging.critical(f"Unhandled exception in monitoring cycle: {cycle_err}", exc_info=True)
             try:
                 # Lấy config đồng bộ ở đây vì đang ở exception handler
                 config = await config_manager.get_config(session, force_refresh=True)
                 sleep_interval = config.get('scan_interval_seconds', 30)
             except Exception:
                 sleep_interval = 30
             logging.info(f"Sleeping for {sleep_interval} seconds before next cycle after error.")
             await asyncio.sleep(sleep_interval) # Dùng asyncio.sleep

# --- Cập nhật điểm vào chính ---
async def main():
    """Main entry point for the async agent."""
    logging.info(f"Collector Agent script execution started. Version: {AGENT_VERSION}")

    # Khởi tạo K8s client trước
    if not await k8s_monitor.initialize_k8s_client():
        logging.critical("Failed to initialize Kubernetes client. Exiting.")
        return # Thoát nếu không kết nối được K8s

    # Tạo ClientSession để tái sử dụng
    async with aiohttp.ClientSession() as session:
        logging.info("Loading initial agent configuration...")
        # Lấy config ban đầu
        initial_config = await config_manager.get_config(session, force_refresh=True)
        logging.info("Initial agent configuration loaded.")
        logging.info(f"Agent ID: {initial_config.get('agent_id', 'Not Set')}")
        logging.info(f"Loki URL: {initial_config.get('loki_url', 'Not Set')}")
        logging.info(f"ObsEngine URL: {initial_config.get('obs_engine_url', 'Not Set')}")
        logging.info(f"Cluster Name: {initial_config.get('cluster_name', 'Not Set')}")
        logging.info(f"Scan Interval: {initial_config.get('scan_interval_seconds', 'Default')}s")
        logging.info(f"Monitored Namespaces: {initial_config.get('monitored_namespaces', 'Default')}")

        logging.info("Starting main monitoring loop...")
        await main_loop(session) # Chạy vòng lặp chính

if __name__ == "__main__":
    try:
        asyncio.run(main()) # Chạy hàm main async
    except KeyboardInterrupt:
        logging.info("Agent stopped by user (KeyboardInterrupt).")
    except Exception as main_err:
        logging.critical(f"Unhandled exception in main execution: {main_err}", exc_info=True)
    finally:
        # --- Đảm bảo K8s client được đóng ---
        if k8s_monitor.k8s_client_initialized and k8s_monitor.k8s_core_v1:
             try:
                 # Cần chạy hàm shutdown bất đồng bộ
                 async def shutdown_k8s():
                     if k8s_monitor.k8s_core_v1.api_client:
                         await k8s_monitor.k8s_core_v1.api_client.close()
                         logging.info("Kubernetes API client closed.")
                 asyncio.run(shutdown_k8s())
             except Exception as close_err:
                 logging.error(f"Error closing Kubernetes client: {close_err}")
        # ---------------------------------
        logging.info("Collector Agent shutdown complete.")

