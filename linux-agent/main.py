import os
import time
import json
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import re
import asyncio
import aiohttp
import sys
import pathlib
import random
import argparse # Thêm thư viện argparse

try:
    import config_manager
    import linux_monitor
    import log_reader
except ImportError as e:
    print(f"CRITICAL: Failed to import custom module: {e}")
    try:
        logging.critical(f"Failed to import custom module: {e}", exc_info=True)
    except NameError:
        pass
    exit(1)

# Load .env file first if it exists, command-line args will override
load_dotenv()

AGENT_VERSION = "1.0.0-linux"
ENVIRONMENT_TYPE = "linux"

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format='%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s',
                    stream=sys.stdout)

logging.info(f"Logging configured successfully for Collector Agent - Type: {ENVIRONMENT_TYPE} - Version: {AGENT_VERSION}")

CACHE_DIR = pathlib.Path("/cache")

current_system_info = None
last_system_info_refresh = 0
SYSTEM_INFO_REFRESH_INTERVAL = 3600
_system_info_lock = asyncio.Lock()

# --- Các hàm async khác (get_cached_system_info, identify_resources_to_investigate, ...) giữ nguyên ---
# --- Chỉ cập nhật hàm main và các hàm gọi config ---
async def get_cached_system_info():
    global current_system_info, last_system_info_refresh
    now = time.time()
    async with _system_info_lock:
        if not current_system_info or (now - last_system_info_refresh > SYSTEM_INFO_REFRESH_INTERVAL):
            logging.info("Refreshing system info...")
            summary_data = await linux_monitor.get_system_info()
            current_system_info = summary_data
            last_system_info_refresh = now
            logging.info(f"System info refreshed: {current_system_info}")
        return current_system_info if current_system_info else {}

async def identify_resources_to_investigate(session, start_cycle_time, config):
    resources_to_investigate = {}
    hostname = config.get('hostname', 'unknown-host')

    cpu_threshold = config.get('cpu_threshold_percent', 90.0)
    mem_threshold = config.get('mem_threshold_percent', 90.0)
    disk_thresholds = config.get('disk_thresholds', {'/': 90.0})
    monitored_services = config.get('monitored_services', [])
    monitored_logs = config.get('monitored_logs', [])
    log_scan_keywords = config.get('log_scan_keywords', ["ERROR", "WARN", "CRITICAL", "FATAL", "PANIC", "EXCEPTION", "Traceback"])
    log_scan_minutes = config.get('log_scan_range_minutes', 5)

    scan_tasks = []

    async def check_system_resources():
        reasons = []
        try:
            cpu_percent = await linux_monitor.get_cpu_usage()
            if cpu_percent is not None and cpu_percent >= cpu_threshold:
                reasons.append(f"High CPU Usage: {cpu_percent:.1f}% (Threshold: {cpu_threshold}%)")

            mem_info = await linux_monitor.get_memory_usage()
            if mem_info and mem_info['percent'] >= mem_threshold:
                reasons.append(f"High Memory Usage: {mem_info['percent']:.1f}% (Threshold: {mem_threshold}%)")

            for path, threshold in disk_thresholds.items():
                disk_info = await linux_monitor.get_disk_usage(path)
                if disk_info and disk_info['percent'] >= threshold:
                    reasons.append(f"High Disk Usage ({path}): {disk_info['percent']:.1f}% (Threshold: {threshold}%)")

            if reasons:
                if hostname not in resources_to_investigate:
                    resources_to_investigate[hostname] = {"reason": [], "logs": [], "resource_type": "host"}
                resources_to_investigate[hostname]["reason"].extend(reasons)
        except Exception as e:
            logging.error(f"Error checking system resources: {e}", exc_info=True)
    scan_tasks.append(asyncio.create_task(check_system_resources()))

    async def check_monitored_services():
        for service_name in monitored_services:
            try:
                status = await linux_monitor.check_service_status(service_name)
                if status not in ['active', 'activating']:
                    reason = f"Service Status '{status}': {service_name}"
                    if service_name not in resources_to_investigate:
                        resources_to_investigate[service_name] = {"reason": [], "logs": [], "resource_type": "service"}
                    resources_to_investigate[service_name]["reason"].append(reason)
            except Exception as e:
                logging.error(f"Error checking service {service_name}: {e}", exc_info=True)
    scan_tasks.append(asyncio.create_task(check_monitored_services()))

    async def scan_logs():
        log_scan_start_time = start_cycle_time - timedelta(minutes=log_scan_minutes)
        for log_config in monitored_logs:
            log_path = log_config.get("path")
            log_type = log_config.get("type", "file")
            unit = log_config.get("unit")

            if not log_path and log_type == "file":
                logging.warning("Skipping log config without path for type 'file'")
                continue

            try:
                found_logs = []
                if log_type == "file":
                    found_logs = await log_reader.scan_log_file(log_path, log_scan_keywords, log_scan_start_time)
                elif log_type == "journal":
                    found_logs = await log_reader.scan_journal(log_scan_keywords, log_scan_start_time, unit=unit)

                if found_logs:
                    resource_key = unit if log_type == "journal" and unit else log_path if log_path else "system_journal"
                    resource_type = "service" if log_type == "journal" and unit else "log"

                    reason = f"Found {len(found_logs)} suspicious entries in {resource_key}"
                    if resource_key not in resources_to_investigate:
                        resources_to_investigate[resource_key] = {"reason": [], "logs": [], "resource_type": resource_type}

                    if reason not in resources_to_investigate[resource_key]["reason"]:
                        resources_to_investigate[resource_key]["reason"].append(reason)
                    if not resources_to_investigate[resource_key]["logs"]:
                         resources_to_investigate[resource_key]["logs"].extend(found_logs)

            except Exception as e:
                log_identifier = unit if log_type == "journal" else log_path
                logging.error(f"Error scanning log {log_identifier}: {e}", exc_info=True)
    scan_tasks.append(asyncio.create_task(scan_logs()))

    await asyncio.gather(*scan_tasks)

    logging.info(f"Identified {len(resources_to_investigate)} resources for potential data collection this cycle.")
    return resources_to_investigate

async def collect_resource_details(session, resource_name, resource_type, initial_reasons, suspicious_logs_found_in_scan, config):
    logging.info(f"Collecting details for resource: {resource_name} (Type: {resource_type}, Initial Reasons: {initial_reasons})")

    environment_context_parts = []
    logs_for_analysis = suspicious_logs_found_in_scan or []
    environment_info = {}
    hostname = config.get('hostname', 'unknown-host')
    log_context_minutes = config.get('log_context_minutes', 30)
    log_context_window = timedelta(minutes=log_context_minutes)
    now_utc = datetime.now(timezone.utc)
    context_start_time = now_utc - log_context_window
    context_end_time = now_utc

    try:
        environment_info = await get_cached_system_info()
        environment_context_parts.append("--- System Info ---")
        environment_context_parts.append(json.dumps(environment_info, indent=2))

        if resource_type == "host":
            environment_context_parts.append("\n--- Current Resource Usage ---")
            cpu = await linux_monitor.get_cpu_usage()
            mem = await linux_monitor.get_memory_usage()
            disks = await linux_monitor.get_all_disk_usage()
            net = await linux_monitor.get_network_stats()
            environment_context_parts.append(f"CPU Usage: {cpu:.1f}%" if cpu is not None else "CPU Usage: N/A")
            environment_context_parts.append(f"Memory Usage: {mem['percent']:.1f}% (Used: {mem['used_gb']:.1f}GB, Total: {mem['total_gb']:.1f}GB)" if mem else "Memory Usage: N/A")
            environment_context_parts.append("Disk Usage:")
            for path, usage in disks.items():
                 environment_context_parts.append(f"  {path}: {usage['percent']:.1f}% (Used: {usage['used_gb']:.1f}GB, Total: {usage['total_gb']:.1f}GB)")
            environment_context_parts.append("Network Stats:")
            for iface, stats in net.items():
                 environment_context_parts.append(f"  {iface}: IPs={stats.get('ips', 'N/A')}, Sent={stats.get('sent_gb', 0):.2f}GB, Recv={stats.get('recv_gb', 0):.2f}GB")

            if not logs_for_analysis:
                 syslog_path = "/var/log/syslog"
                 if os.path.exists(syslog_path):
                     logs_for_analysis = await log_reader.get_log_context(syslog_path, now_utc, log_context_minutes * 60)
                 else:
                     logs_for_analysis = await log_reader.get_journal_context(now_utc, log_context_minutes * 60)

        elif resource_type == "service":
            environment_context_parts.append(f"\n--- Service Details: {resource_name} ---")
            service_details = await linux_monitor.get_service_details(resource_name)
            environment_context_parts.append(json.dumps(service_details, indent=2))
            if not logs_for_analysis:
                logs_for_analysis = await log_reader.get_journal_context(now_utc, log_context_minutes * 60, unit=resource_name)

        elif resource_type == "process":
             environment_context_parts.append(f"\n--- Process Details: {resource_name} ---")
             try:
                 pid = int(resource_name)
                 proc_info = await linux_monitor.get_process_info(pid)
                 environment_context_parts.append(json.dumps(proc_info, indent=2))
             except ValueError:
                 pids = await linux_monitor.find_processes_by_name(resource_name)
                 for pid in pids[:5]:
                     proc_info = await linux_monitor.get_process_info(pid)
                     environment_context_parts.append(f"  PID {pid}:")
                     environment_context_parts.append(json.dumps(proc_info, indent=2))

        elif resource_type == "log":
            environment_context_parts.append(f"\n--- Log Context: {resource_name} ---")
            if not logs_for_analysis:
                 if os.path.exists(resource_name):
                     logs_for_analysis = await log_reader.get_log_context(resource_name, now_utc, log_context_minutes * 60)
                 else:
                     environment_context_parts.append(f"Log file not found: {resource_name}")

    except Exception as e:
        logging.error(f"Error collecting details for {resource_name}: {e}", exc_info=True)
        environment_context_parts.append(f"\n--- ERROR collecting details: {e} ---")

    environment_context_str = "\n".join(environment_context_parts)

    max_logs_to_send = 100
    if len(logs_for_analysis) > max_logs_to_send:
        logging.warning(f"Trimming logs for {resource_name} from {len(logs_for_analysis)} to {max_logs_to_send}.")
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
             logging.error(f"Error sorting logs for {resource_name}: {sort_err}.")

    serializable_logs = []
    for log_entry in logs_for_analysis:
        entry_copy = log_entry.copy()
        timestamp_val = entry_copy.get('timestamp')
        if isinstance(timestamp_val, datetime): entry_copy['timestamp'] = timestamp_val.isoformat()
        elif not isinstance(timestamp_val, str): entry_copy['timestamp'] = None
        if 'message' not in entry_copy or not isinstance(entry_copy['message'], str): entry_copy['message'] = str(entry_copy.get('message', ''))
        serializable_logs.append(entry_copy)

    return serializable_logs, environment_context_str, environment_info

def _ensure_cache_dir():
    try: CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e: logging.error(f"Could not create cache directory {CACHE_DIR}: {e}")

def _cache_failed_payload(payload):
    _ensure_cache_dir()
    try:
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        resource_name_safe = re.sub(r'[^\w\-.]', '_', payload.get("resource_name", "unknown_resource"))
        env_name_safe = re.sub(r'[^\w\-.]', '_', payload.get("environment_name", "unknown_env"))
        filename = CACHE_DIR / f"failed_{timestamp_str}_{env_name_safe}_{resource_name_safe}.json"
        with open(filename, 'w', encoding='utf-8') as f: json.dump(payload, f, ensure_ascii=False, indent=2)
        logging.warning(f"Failed to send payload for {payload.get('environment_name')}/{payload.get('resource_name')}. Cached to {filename}")
    except Exception as e: logging.error(f"Error caching failed payload: {e}", exc_info=True)

async def _send_cached_payloads_async(session, obs_engine_url):
    _ensure_cache_dir()
    sent_count = 0
    failed_count = 0
    resend_tasks = []

    try:
        cached_files = sorted(CACHE_DIR.glob("failed_*.json"))
        if not cached_files: return

        logging.info(f"Found {len(cached_files)} cached payloads. Attempting to resend asynchronously...")

        for filepath in cached_files:
             resend_tasks.append(asyncio.create_task(_process_single_cached_file(session, obs_engine_url, filepath)))

        results = await asyncio.gather(*resend_tasks)

        for success, filepath in results:
             if success: sent_count += 1
             else: failed_count += 1

        if sent_count > 0 or failed_count > 0:
             logging.info(f"Finished processing cached payloads. Sent: {sent_count}, Failed: {failed_count}")

    except Exception as e:
        logging.error(f"Error during async cached payload processing: {e}", exc_info=True)

async def _process_single_cached_file(session, obs_engine_url, filepath):
     try:
         with open(filepath, 'r', encoding='utf-8') as f:
             payload = json.load(f)

         # Lấy config hiện tại để đảm bảo agent_id và env_name đúng nhất
         # (Vì giá trị trong file cache có thể cũ nếu agent được cấu hình lại)
         current_config = await config_manager.get_config(session) # Lấy config hiện tại
         agent_id = current_config.get('agent_id')
         env_name = current_config.get('environment_name')

         payload['agent_id'] = agent_id
         payload['environment_name'] = env_name
         payload['agent_version'] = AGENT_VERSION
         payload['environment_type'] = payload.get('environment_type', ENVIRONMENT_TYPE)

         if not agent_id:
              logging.error(f"Cannot resend cached payload {filepath.name}: Missing agent_id in current config.")
              return False, filepath

         success = await send_data_to_obs_engine_async(
             session, obs_engine_url,
             env_name, # Sử dụng env_name từ config hiện tại
             agent_id, # Sử dụng agent_id từ config hiện tại
             payload.get("environment_type"),
             payload.get("resource_type"),
             payload.get("resource_name"),
             payload.get("initial_reasons"),
             payload.get("environment_context"),
             payload.get("logs"),
             payload.get("environment_info"),
             payload.get("agent_version"),
             allow_cache=False
         )

         if success:
             logging.info(f"Successfully resent cached payload: {filepath.name}")
             try: filepath.unlink()
             except OSError as e: logging.error(f"Error deleting cached file {filepath}: {e}")
             return True, filepath
         else:
             logging.warning(f"Failed to resend cached payload: {filepath.name}. Will retry later.")
             return False, filepath

     except json.JSONDecodeError:
         logging.error(f"Error decoding cached file {filepath}. Deleting invalid file.")
         try: filepath.unlink()
         except OSError as e: logging.error(f"Error deleting invalid cached file {filepath}: {e}")
         return False, filepath
     except Exception as e:
         logging.error(f"Unexpected error processing cached file {filepath}: {e}", exc_info=True)
         return False, filepath

async def send_data_to_obs_engine_async(
        session, obs_engine_url,
        environment_name, agent_id, environment_type, resource_type, resource_name,
        initial_reasons, environment_context, logs, environment_info, agent_version,
        allow_cache=True, max_retries=2, initial_delay=5):
    if not obs_engine_url: logging.error(f"ObsEngine URL not configured..."); return False
    if not agent_id: logging.error(f"AGENT_ID not configured..."); return False
    if not environment_name: logging.warning(f"Environment Name not provided for agent {agent_id}."); environment_name = "unknown-linux-host"
    if not environment_type: logging.error(f"ENVIRONMENT_TYPE not provided for agent {agent_id}. Cannot send data."); return False
    if not resource_name: logging.error(f"Resource Name not provided for agent {agent_id}. Cannot send data."); return False

    endpoint = obs_engine_url.rstrip('/') + "/collect"

    payload = {
        "environment_name": environment_name,
        "agent_id": agent_id,
        "environment_type": environment_type,
        "resource_type": resource_type,
        "resource_name": resource_name,
        "collection_timestamp": datetime.now(timezone.utc).isoformat(),
        "initial_reasons": initial_reasons,
        "environment_context": environment_context,
        "logs": logs,
        "environment_info": environment_info,
        "agent_version": agent_version
    }

    log_payload_preview = {k: v for k, v in payload.items() if k not in ['logs', 'environment_context']}
    try: log_payload_size = len(json.dumps(payload))
    except Exception: log_payload_size = -1
    logging.debug(f"Attempting to send payload (Agent: {agent_id}, Env: {environment_name}, Resource: {resource_name}, Size: ~{log_payload_size} bytes, Version: {agent_version})")

    retries = 0
    delay = initial_delay
    last_exception = None

    while retries <= max_retries:
        try:
            headers = {'Content-Type': 'application/json'}
            async with session.post(endpoint, headers=headers, json=payload, timeout=30) as response:
                if response.status >= 400:
                    if response.status in [429, 500, 502, 503, 504]:
                         response.raise_for_status()
                    else:
                         error_text = await response.text()
                         logging.error(f"ObsEngine returned non-retryable error {response.status} for {environment_name}/{resource_name}: {error_text[:500]}")
                         if allow_cache: _cache_failed_payload(payload)
                         return False
                logging.info(f"Successfully sent data for {environment_name}/{resource_name} (Agent: {agent_id}). Status: {response.status}")
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exception = e
            retries += 1
            logging.warning(f"Error sending data for {environment_name}/{resource_name} ({type(e).__name__}). Retrying ({retries}/{max_retries}) in {delay}s...")
            if retries > max_retries:
                logging.error(f"Send data failed after {max_retries} retries for {environment_name}/{resource_name}: {last_exception}")
                if allow_cache: _cache_failed_payload(payload)
                return False
            await asyncio.sleep(delay + random.uniform(0, 1))
            delay *= 2
        except Exception as e:
            logging.error(f"Unexpected error sending data for {environment_name}/{resource_name}: {e}", exc_info=True)
            if allow_cache: _cache_failed_payload(payload)
            return False

    logging.error(f"Send data failed definitively after retries for {environment_name}/{resource_name}: {last_exception}")
    if allow_cache: _cache_failed_payload(payload)
    return False

async def perform_monitoring_cycle(session):
    start_cycle_time_dt = datetime.now(timezone.utc)
    cycle_start_ts_perf = time.perf_counter()
    logging.info(f"--- Starting new monitoring cycle (Collector Agent - {ENVIRONMENT_TYPE}) ---")

    # Lấy config (đã bao gồm giá trị từ CLI args nếu có)
    config = await config_manager.get_config(session)
    current_scan_interval = config.get('scan_interval_seconds', 60)
    obs_engine_url = config.get('obs_engine_url')
    environment_name = config.get('environment_name')
    agent_id = config.get('agent_id')

    if obs_engine_url:
        await _send_cached_payloads_async(session, obs_engine_url)
    else:
        logging.warning("OBS_ENGINE_URL not set, skipping resend of cached payloads.")

    if not obs_engine_url or not agent_id:
        logging.error("OBS_ENGINE_URL or AGENT_ID is not configured. Agent cannot operate correctly.")
        sleep_time = max(0, current_scan_interval - (time.perf_counter() - cycle_start_ts_perf))
        logging.info(f"--- Cycle finished early (config missing). Sleeping for {sleep_time:.2f}s ---")
        await asyncio.sleep(sleep_time)
        return

    if not environment_name:
        logging.warning(f"ENVIRONMENT_NAME not set.")

    logging.info(f"Agent ID: {agent_id} | Environment Name: {environment_name} | Type: {ENVIRONMENT_TYPE}")
    logging.info(f"Scan Interval: {current_scan_interval}s")

    resources_to_investigate = await identify_resources_to_investigate(session, start_cycle_time_dt, config)

    collection_tasks = []
    if resources_to_investigate:
         logging.info(f"Creating data collection tasks for {len(resources_to_investigate)} resources...")
         for resource_key, data in resources_to_investigate.items():
             try:
                 resource_type = data.get("resource_type", "unknown")
                 initial_reasons = data.get("reason", [])
                 suspicious_logs = data.get("logs", [])

                 collection_tasks.append(asyncio.create_task(
                     process_single_resource(session, resource_key, resource_type, initial_reasons, suspicious_logs, config, obs_engine_url, agent_id, environment_name)
                 ))
             except Exception as task_create_err:
                  logging.error(f"Error creating task for resource {resource_key}: {task_create_err}", exc_info=True)

    if collection_tasks:
        await asyncio.gather(*collection_tasks)
        logging.info(f"Finished processing {len(collection_tasks)} resources.")

    cycle_duration = time.perf_counter() - cycle_start_ts_perf
    sleep_time = max(0, current_scan_interval - cycle_duration)
    logging.info(f"--- Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
    await asyncio.sleep(sleep_time)

async def process_single_resource(session, resource_name, resource_type, initial_reasons, suspicious_logs, config, obs_engine_url, agent_id, environment_name):
    try:
        logs_collected, environment_context_str, environment_info = await collect_resource_details(
            session, resource_name, resource_type, "; ".join(initial_reasons), suspicious_logs, config
        )
        await send_data_to_obs_engine_async(
            session, obs_engine_url, environment_name, agent_id, ENVIRONMENT_TYPE, resource_type, resource_name,
            initial_reasons, environment_context_str, logs_collected,
            environment_info, AGENT_VERSION
        )
    except Exception as collection_err:
        logging.error(f"Error during async data collection/sending loop for {environment_name}/{resource_name}: {collection_err}", exc_info=True)

async def main_loop(session):
    logging.info(f"Entering main loop for Collector Agent ({ENVIRONMENT_TYPE})...")
    while True:
        try:
            logging.debug("Calling perform_monitoring_cycle...")
            await perform_monitoring_cycle(session)
            logging.debug("perform_monitoring_cycle finished.")
        except asyncio.CancelledError:
             logging.info("Main loop cancelled.")
             break
        except Exception as cycle_err:
             logging.critical(f"Unhandled exception in monitoring cycle: {cycle_err}", exc_info=True)
             try:
                 # Lấy lại config phòng trường hợp lỗi liên quan đến config cũ
                 config = await config_manager.get_config(session, force_refresh=True)
                 sleep_interval = config.get('scan_interval_seconds', 60)
             except Exception:
                 sleep_interval = 60
             logging.info(f"Sleeping for {sleep_interval} seconds before next cycle after error.")
             await asyncio.sleep(sleep_interval)

async def main():
    # --- Thêm xử lý Argument Parser ---
    parser = argparse.ArgumentParser(description=f"ObsEngine Linux Agent v{AGENT_VERSION}")
    parser.add_argument("--obsengine", help="URL of the ObsEngine API endpoint")
    parser.add_argument("--agent-id", help="Unique ID for this agent (overrides ENV)")
    parser.add_argument("--env-name", help="Name of the environment this agent is running in (overrides ENV)")
    # Thêm các argument khác nếu muốn cấu hình qua CLI, ví dụ:
    # parser.add_argument("--scan-interval", type=int, help="Scan interval in seconds")
    args = parser.parse_args()
    # ---------------------------------

    logging.info(f"Collector Agent script execution started. Type: {ENVIRONMENT_TYPE}, Version: {AGENT_VERSION}")

    # --- Khởi tạo Config Manager với Args ---
    config_manager.init_with_args(args)
    # ---------------------------------------

    async with aiohttp.ClientSession() as session:
        logging.info("Loading initial agent configuration...")
        # Lấy config lần đầu (đã bao gồm giá trị từ args)
        initial_config = await config_manager.get_config(session, force_refresh=True)
        logging.info("Initial agent configuration loaded.")
        logging.info(f"Agent ID: {initial_config.get('agent_id', 'Not Set')}")
        logging.info(f"ObsEngine URL: {initial_config.get('obs_engine_url', 'Not Set')}")
        logging.info(f"Environment Name: {initial_config.get('environment_name', 'Not Set')}")
        logging.info(f"Scan Interval: {initial_config.get('scan_interval_seconds', 'Default')}s")
        logging.info(f"Monitored Services: {initial_config.get('monitored_services', 'Default')}")
        logging.info(f"Monitored Logs: {initial_config.get('monitored_logs', 'Default')}")

        # Kiểm tra cấu hình thiết yếu
        if not initial_config.get('obs_engine_url'):
            logging.critical("CRITICAL: ObsEngine URL is not configured via --obsengine argument or OBS_ENGINE_URL environment variable. Agent cannot send data. Exiting.")
            sys.exit(1)
        if not initial_config.get('agent_id'):
            logging.critical("CRITICAL: Agent ID is not configured via --agent-id argument or AGENT_ID environment variable. Exiting.")
            sys.exit(1)


        logging.info("Starting main monitoring loop...")
        await main_loop(session)

if __name__ == "__main__":
    if os.geteuid() != 0:
        logging.warning("Agent not running as root. Some monitoring features (e.g., reading certain logs, detailed process info) might be limited.")

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Agent stopped by user (KeyboardInterrupt).")
    except Exception as main_err:
        logging.critical(f"Unhandled exception in main execution: {main_err}", exc_info=True)
    finally:
        logging.info("Collector Agent shutdown complete.")

