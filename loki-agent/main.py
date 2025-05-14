# -*- coding: utf-8 -*-
import os
import time
import json
import logging
from datetime import datetime, timedelta, timezone
import re
import asyncio
import aiohttp
import sys
import pathlib
import random

try:
    import config_manager
    import loki_client
except ImportError as e:
    print(f"CRITICAL: Failed to import custom module: {e}")
    try:
        logging.critical(f"Failed to import custom module: {e}", exc_info=True)
    except NameError:
        pass
    exit(1)

AGENT_VERSION = "1.0.0-loki"
ENVIRONMENT_TYPE = "loki_source" # Định danh loại agent mới

# --- Cấu hình Logging ---
log_level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=log_level_str,
                    format='%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s',
                    stream=sys.stdout)
logger = logging.getLogger(__name__)
logger.info(f"Logging configured successfully (Level: {log_level_str})")
# -------------------------

# --- Cấu hình cơ bản từ ENV (chỉ để kết nối ban đầu) ---
OBS_ENGINE_URL = os.environ.get("OBS_ENGINE_URL")
AGENT_ID = os.environ.get("AGENT_ID")
ENVIRONMENT_NAME = os.environ.get("ENVIRONMENT_NAME", "loki-environment") # Tên môi trường chung cho nguồn Loki này
# -------------------------------------------------------

CACHE_DIR = pathlib.Path(os.environ.get("CACHE_DIR", "/tmp/loki_agent_cache"))

# --- Các hàm xử lý cache và gửi dữ liệu (tương tự Linux Agent) ---
def _ensure_cache_dir():
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except OSError as e:
        logger.error(f"Could not create cache directory {CACHE_DIR}: {e}")
        return False

def _cache_failed_payload(payload):
    if not _ensure_cache_dir(): return
    try:
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        # Dùng service_name (nếu có) hoặc resource_name làm phần định danh file cache
        service_name = payload.get("labels", {}).get("service_name", None)
        resource_name_safe = re.sub(r'[^\w\-.]', '_', service_name or payload.get("resource_name", "unknown_log"))
        env_name_safe = re.sub(r'[^\w\-.]', '_', payload.get("environment_name", "unknown_env"))
        filename = CACHE_DIR / f"failed_{timestamp_str}_{env_name_safe}_{resource_name_safe}.json"
        with open(filename, 'w', encoding='utf-8') as f: json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.warning(f"Failed to send payload for {payload.get('environment_name')}/{resource_name_safe}. Cached to {filename}")
    except Exception as e: logger.error(f"Error caching failed payload: {e}", exc_info=True)

async def _send_cached_payloads_async(session, obs_engine_url):
    if not CACHE_DIR or not os.path.exists(CACHE_DIR):
        logger.debug("Cache directory does not exist, skipping resend.")
        return
    sent_count = 0
    failed_count = 0
    resend_tasks = []
    try:
        cached_files_names = os.listdir(CACHE_DIR)
        json_files = sorted([CACHE_DIR / f for f in cached_files_names if f.startswith("failed_") and f.endswith(".json")])
        if not json_files: return
        logger.info(f"Found {len(json_files)} cached payloads. Attempting to resend asynchronously...")
        for filepath in json_files:
             resend_tasks.append(asyncio.create_task(_process_single_cached_file(session, obs_engine_url, filepath)))
        results = await asyncio.gather(*resend_tasks)
        for success, filepath in results:
             if success: sent_count += 1
             else: failed_count += 1
        if sent_count > 0 or failed_count > 0:
             logger.info(f"Finished processing cached payloads. Sent: {sent_count}, Failed: {failed_count}")
    except Exception as e:
        logger.error(f"Error during async cached payload processing: {e}", exc_info=True)

async def _process_single_cached_file(session, obs_engine_url, filepath):
     try:
         with open(filepath, 'r', encoding='utf-8') as f:
             payload = json.load(f)

         # Lấy Agent ID và Env Name từ biến môi trường hiện tại khi gửi lại
         payload['agent_id'] = AGENT_ID
         payload['environment_name'] = ENVIRONMENT_NAME
         payload['agent_version'] = AGENT_VERSION
         payload['environment_type'] = payload.get('environment_type', ENVIRONMENT_TYPE)

         if not AGENT_ID:
              logger.error(f"Cannot resend cached payload {filepath.name}: Missing AGENT_ID env var.")
              return False, filepath

         success = await send_log_batch_to_obs_engine(
             session, obs_engine_url,
             payload.get("resource_name"), # Thường là service_name
             payload.get("initial_reasons", ["Resent from cache"]),
             payload.get("logs", []),
             payload.get("labels", {}), # Gửi kèm labels nếu có
             allow_cache=False
         )

         if success:
             logger.info(f"Successfully resent cached payload: {filepath.name}")
             try: filepath.unlink()
             except OSError as e: logger.error(f"Error deleting cached file {filepath}: {e}")
             return True, filepath
         else:
             logger.warning(f"Failed to resend cached payload: {filepath.name}. Will retry later.")
             return False, filepath
     except json.JSONDecodeError:
         logger.error(f"Error decoding cached file {filepath}. Deleting invalid file.")
         try: filepath.unlink()
         except OSError as e: logger.error(f"Error deleting invalid cached file {filepath}: {e}")
         return False, filepath
     except Exception as e:
         logger.error(f"Unexpected error processing cached file {filepath}: {e}", exc_info=True)
         return False, filepath

async def send_log_batch_to_obs_engine(
        session, obs_engine_url,
        resource_name, # Tên của service (từ label) hoặc tên query
        initial_reasons, # Lý do tìm thấy log (ví dụ: "Matched keyword 'ERROR'")
        log_batch, # Danh sách các log entry tìm thấy
        labels={}, # Các label của stream log từ Loki
        allow_cache=True, max_retries=2, initial_delay=5):
    """Gửi một lô log tìm thấy từ Loki về ObsEngine."""
    if not obs_engine_url: logger.error(f"ObsEngine URL not configured..."); return False
    if not AGENT_ID: logger.error(f"AGENT_ID not configured..."); return False
    if not ENVIRONMENT_NAME: logger.warning(f"ENVIRONMENT_NAME not set."); return False
    if not resource_name: logger.error(f"Resource Name (e.g., service_name) not provided."); return False

    endpoint = obs_engine_url.rstrip('/') + "/collect"

    # Tạo payload theo cấu trúc chung, điều chỉnh cho phù hợp với log từ Loki
    payload = {
        "environment_name": ENVIRONMENT_NAME,
        "agent_id": AGENT_ID,
        "environment_type": ENVIRONMENT_TYPE,
        "resource_type": "log_stream", # Hoặc "docker_container" nếu có label container_name
        "resource_name": resource_name, # Sử dụng service_name hoặc tên query
        "collection_timestamp": datetime.now(timezone.utc).isoformat(),
        "initial_reasons": initial_reasons if isinstance(initial_reasons, list) else [str(initial_reasons)],
        "environment_context": f"Logs collected from Loki stream with labels: {json.dumps(labels)}", # Ngữ cảnh đơn giản
        "logs": log_batch, # Danh sách các log entry
        "environment_info": {"source": "Loki"}, # Thông tin môi trường đơn giản
        "agent_version": AGENT_VERSION
    }

    log_payload_preview = {k: v for k, v in payload.items() if k not in ['logs', 'environment_context']}
    try: log_payload_size = len(json.dumps(payload))
    except Exception: log_payload_size = -1
    logger.debug(f"Attempting to send log batch (Agent: {AGENT_ID}, Env: {ENVIRONMENT_NAME}, Resource: {resource_name}, Logs: {len(log_batch)}, Size: ~{log_payload_size} bytes)")

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
                         logger.error(f"ObsEngine returned non-retryable error {response.status} for {ENVIRONMENT_NAME}/{resource_name}: {error_text[:500]}")
                         if allow_cache: _cache_failed_payload(payload) # Cache lại nếu gửi lỗi
                         return False
                logger.info(f"Successfully sent log batch for {ENVIRONMENT_NAME}/{resource_name} (Agent: {AGENT_ID}). Status: {response.status}")
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exception = e
            retries += 1
            logger.warning(f"Error sending log batch for {ENVIRONMENT_NAME}/{resource_name} ({type(e).__name__}). Retrying ({retries}/{max_retries}) in {delay}s...")
            if retries > max_retries:
                logger.error(f"Send log batch failed after {max_retries} retries for {ENVIRONMENT_NAME}/{resource_name}: {last_exception}")
                if allow_cache: _cache_failed_payload(payload)
                return False
            await asyncio.sleep(delay + random.uniform(0, 1))
            delay *= 2
        except Exception as e:
            logger.error(f"Unexpected error sending log batch for {ENVIRONMENT_NAME}/{resource_name}: {e}", exc_info=True)
            if allow_cache: _cache_failed_payload(payload)
            return False

    logger.error(f"Send log batch failed definitively after retries for {ENVIRONMENT_NAME}/{resource_name}: {last_exception}")
    if allow_cache: _cache_failed_payload(payload)
    return False

# --- Logic chính của Agent ---
async def perform_monitoring_cycle(session, config):
    """Thực hiện một chu kỳ quét Loki dựa trên cấu hình."""
    start_cycle_time_dt = datetime.now(timezone.utc)
    cycle_start_ts_perf = time.perf_counter()
    logger.info(f"--- Starting new monitoring cycle (Loki Agent) ---")

    loki_url = config.get('loki_url')
    queries_config = config.get('logql_queries', []) # Danh sách các cấu hình query
    scan_interval = config.get('scan_interval_seconds', 60)
    scan_range_minutes = config.get('log_scan_range_minutes', 5)
    limit_per_query = config.get('loki_query_limit', 500) # Giới hạn log cho mỗi query

    if not loki_url:
        logger.error("Loki URL not found in configuration from ObsEngine. Skipping cycle.")
        return scan_interval # Trả về interval để sleep

    if not queries_config:
        logger.warning("No LogQL queries found in configuration from ObsEngine. Skipping scan.")
        # Vẫn gửi heartbeat nếu không có query
        await send_heartbeat(session, config.get('obs_engine_url'))
        return scan_interval

    scan_end_time = start_cycle_time_dt
    scan_start_time = scan_end_time - timedelta(minutes=scan_range_minutes)

    query_tasks = []
    for query_conf in queries_config:
        logql = query_conf.get("query")
        # Lấy service_name từ config query hoặc thử parse từ LogQL (đơn giản)
        service_name = query_conf.get("service_name")
        if not service_name and logql:
            match = re.search(r'service_name="([^"]+)"', logql)
            if match: service_name = match.group(1)
            else: service_name = f"query_{queries_config.index(query_conf)}" # Tên mặc định

        if logql:
            query_tasks.append(
                loki_client.execute_loki_query(
                    session=session,
                    loki_url=loki_url,
                    logql_query=logql,
                    start_time=scan_start_time,
                    end_time=scan_end_time,
                    limit=limit_per_query,
                    query_identifier=service_name # Dùng service_name làm định danh
                )
            )
        else:
            logger.warning(f"Skipping query config without 'query': {query_conf}")

    if not query_tasks:
        logger.warning("No valid queries to execute.")
        await send_heartbeat(session, config.get('obs_engine_url'))
        return scan_interval

    logger.info(f"Executing {len(query_tasks)} Loki queries...")
    query_results = await asyncio.gather(*query_tasks)
    logger.info("Finished executing Loki queries.")

    found_logs_count = 0
    send_tasks = []

    for result in query_results:
        query_identifier = result.get("query_identifier", "unknown_query")
        logs = result.get("logs", [])
        labels = result.get("labels", {}) # Lấy labels từ kết quả query (nếu loki_client trả về)

        if logs:
            found_logs_count += len(logs)
            logger.info(f"Found {len(logs)} log entries for '{query_identifier}'. Preparing to send.")
            # Gửi từng batch log tìm thấy cho mỗi query/service
            send_tasks.append(
                send_log_batch_to_obs_engine(
                    session=session,
                    obs_engine_url=config.get('obs_engine_url'),
                    resource_name=query_identifier, # Sử dụng service_name/query_id làm resource_name
                    initial_reasons=[f"Logs found matching query for {query_identifier}"],
                    log_batch=logs,
                    labels=labels
                )
            )

    if not send_tasks: # Nếu không có task gửi log (không tìm thấy log nào)
        logger.info("No relevant logs found in this cycle. Sending heartbeat.")
        await send_heartbeat(session, config.get('obs_engine_url'))
    elif send_tasks:
        await asyncio.gather(*send_tasks) # Gửi các batch log song song
        logger.info(f"Finished sending {len(send_tasks)} log batches.")

    cycle_duration = time.perf_counter() - cycle_start_ts_perf
    sleep_time = max(0, scan_interval - cycle_duration)
    logger.info(f"--- Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f} seconds... ---")
    return sleep_time

async def send_heartbeat(session, obs_engine_url):
    """Gửi tín hiệu heartbeat đến ObsEngine."""
    logger.debug("Sending heartbeat...")
    try:
        await send_log_batch_to_obs_engine(
            session, obs_engine_url,
            resource_name=ENVIRONMENT_NAME, # Dùng tên môi trường làm resource
            initial_reasons=["Heartbeat"],
            log_batch=[],
            labels={"agent_type": "loki-agent"},
            allow_cache=False
        )
    except Exception as hb_err:
        logger.error(f"Error sending heartbeat: {hb_err}", exc_info=True)

async def main_loop(session):
    logger.info(f"Entering main loop for Loki Agent...")
    while True:
        sleep_interval = 60 # Default sleep interval if config fetch fails
        try:
            logger.debug("Fetching configuration...")
            config = await config_manager.get_config(session, OBS_ENGINE_URL, AGENT_ID)

            if config:
                logger.debug("Calling perform_monitoring_cycle...")
                sleep_interval = await perform_monitoring_cycle(session, config)
                logger.debug("perform_monitoring_cycle finished.")
            else:
                logger.error("Failed to fetch configuration. Retrying after default interval.")
                # Không gửi heartbeat nếu không lấy được config

        except asyncio.CancelledError:
             logger.info("Main loop cancelled.")
             break
        except Exception as cycle_err:
             logger.critical(f"Unhandled exception in monitoring cycle: {cycle_err}", exc_info=True)
             # Lấy lại scan_interval từ config cũ nếu có, hoặc dùng default
             try:
                 sleep_interval = config_manager.get_last_config().get('scan_interval_seconds', 60)
             except Exception:
                 sleep_interval = 60
             logger.info(f"Sleeping for {sleep_interval} seconds before next cycle after error.")

        await asyncio.sleep(sleep_interval)

async def main():
    logger.info(f"Loki Agent script execution started. Version: {AGENT_VERSION}")

    # Kiểm tra các biến môi trường cần thiết
    if not OBS_ENGINE_URL:
        logger.critical("CRITICAL: OBS_ENGINE_URL environment variable is not set. Agent cannot connect. Exiting.")
        sys.exit(1)
    if not AGENT_ID:
        logger.critical("CRITICAL: AGENT_ID environment variable is not set. Agent cannot identify itself. Exiting.")
        sys.exit(1)

    # Tạo thư mục cache nếu chưa có
    _ensure_cache_dir()

    async with aiohttp.ClientSession() as session:
        logger.info(f"Agent ID: {AGENT_ID}")
        logger.info(f"ObsEngine URL: {OBS_ENGINE_URL}")
        logger.info(f"Environment Name: {ENVIRONMENT_NAME}")

        # Gửi lại cache trước khi bắt đầu vòng lặp chính
        await _send_cached_payloads_async(session, OBS_ENGINE_URL)

        logger.info("Starting main monitoring loop...")
        await main_loop(session)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Agent stopped by user (KeyboardInterrupt).")
    except Exception as main_err:
        logger.critical(f"Unhandled exception in main execution: {main_err}", exc_info=True)
    finally:
        logger.info("Loki Agent shutdown complete.")
