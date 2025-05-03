# ai-agent1/app/config_manager.py
import os
import json
import logging
import time
import aiohttp # Sử dụng aiohttp
import asyncio # Sử dụng asyncio

DEFAULT_LOKI_URL = "http://loki-read.monitoring.svc.cluster.local:3100"
DEFAULT_OBS_ENGINE_URL = ""
DEFAULT_SCAN_INTERVAL_SECONDS = 30
DEFAULT_RESTART_COUNT_THRESHOLD = 5
DEFAULT_LOKI_SCAN_MIN_LEVEL = "INFO"
DEFAULT_LOKI_DETAIL_LOG_RANGE_MINUTES = 30
DEFAULT_LOKI_QUERY_LIMIT = 500
DEFAULT_MONITORED_NAMESPACES_STR = "kube-system,default"
DEFAULT_K8S_CLUSTER_NAME = "unknown-cluster"

AGENT_ID = os.environ.get("AGENT_ID")
if not AGENT_ID:
    logging.warning("AGENT_ID environment variable not set. Using K8S_CLUSTER_NAME as fallback.")
    AGENT_ID = os.environ.get("K8S_CLUSTER_NAME", DEFAULT_K8S_CLUSTER_NAME)

_current_agent_config = {}
_last_config_refresh_time = 0
CONFIG_REFRESH_INTERVAL_SECONDS = int(os.environ.get("CONFIG_REFRESH_INTERVAL_SECONDS", 60))
# Lock để tránh refresh config đồng thời
_config_refresh_lock = asyncio.Lock()

# Hàm async để fetch config từ API với retry
async def _fetch_config_from_api_async(session, obs_engine_url, agent_id, max_retries=2, initial_delay=5):
    if not obs_engine_url:
        logging.warning("[Config Manager] OBS_ENGINE_URL not configured. Cannot fetch config from API.")
        return None
    if not agent_id:
        logging.warning("[Config Manager] AGENT_ID not configured. Cannot fetch config from API.")
        return None

    endpoint = f"{obs_engine_url}/api/agents/{agent_id}/config"
    retries = 0
    delay = initial_delay
    last_exception = None

    while retries <= max_retries:
        try:
            logging.info(f"[Config Manager] Fetching config from ObsEngine API (Attempt {retries+1}): {endpoint}")
            async with session.get(endpoint, timeout=15) as response:
                if response.status >= 400:
                    if response.status in [429, 500, 502, 503, 504]:
                        response.raise_for_status() # Trigger retry
                    else:
                        error_text = await response.text()
                        logging.error(f"[Config Manager] HTTP error {response.status} fetching config: {error_text[:200]}")
                        return None # Non-retryable error
                config_data = await response.json()
                logging.info(f"[Config Manager] Successfully fetched config from API for agent {agent_id}.")
                return config_data
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exception = e
            retries += 1
            logging.warning(f"[Config Manager] Request error fetching config ({type(e).__name__}). Retrying ({retries}/{max_retries}) in {delay}s...")
            if retries > max_retries:
                logging.error(f"[Config Manager] Config fetch failed after {max_retries} retries: {last_exception}")
                return None
            await asyncio.sleep(delay)
            delay *= 2
        except json.JSONDecodeError as e:
            logging.error(f"[Config Manager] Error decoding JSON config from API: {e}")
            return None
        except Exception as e:
            logging.error(f"[Config Manager] Unexpected error fetching config from API: {e}", exc_info=True)
            return None

    logging.error(f"[Config Manager] Config fetch failed definitively after retries: {last_exception}")
    return None


def _load_config_from_env_defaults():
    # Hàm này giữ nguyên, không cần async
    config = {}
    config['loki_url'] = os.environ.get("LOKI_URL", DEFAULT_LOKI_URL)
    config['obs_engine_url'] = os.environ.get("OBS_ENGINE_URL", DEFAULT_OBS_ENGINE_URL)
    config['cluster_name'] = os.environ.get("K8S_CLUSTER_NAME", DEFAULT_K8S_CLUSTER_NAME)
    config['agent_id'] = AGENT_ID

    try:
        config['scan_interval_seconds'] = int(os.environ.get("SCAN_INTERVAL_SECONDS", DEFAULT_SCAN_INTERVAL_SECONDS))
        if config['scan_interval_seconds'] < 10: config['scan_interval_seconds'] = 10
    except ValueError: config['scan_interval_seconds'] = DEFAULT_SCAN_INTERVAL_SECONDS

    try:
        config['restart_count_threshold'] = int(os.environ.get("RESTART_COUNT_THRESHOLD", DEFAULT_RESTART_COUNT_THRESHOLD))
        if config['restart_count_threshold'] < 1: config['restart_count_threshold'] = 1
    except ValueError: config['restart_count_threshold'] = DEFAULT_RESTART_COUNT_THRESHOLD

    config['loki_scan_min_level'] = os.environ.get("LOKI_SCAN_MIN_LEVEL", DEFAULT_LOKI_SCAN_MIN_LEVEL).upper()
    valid_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
    if config['loki_scan_min_level'] not in valid_levels: config['loki_scan_min_level'] = DEFAULT_LOKI_SCAN_MIN_LEVEL

    try: config['loki_detail_log_range_minutes'] = int(os.environ.get("LOKI_DETAIL_LOG_RANGE_MINUTES", DEFAULT_LOKI_DETAIL_LOG_RANGE_MINUTES))
    except ValueError: config['loki_detail_log_range_minutes'] = DEFAULT_LOKI_DETAIL_LOG_RANGE_MINUTES

    try: config['loki_query_limit'] = int(os.environ.get("LOKI_QUERY_LIMIT", DEFAULT_LOKI_QUERY_LIMIT))
    except ValueError: config['loki_query_limit'] = DEFAULT_LOKI_QUERY_LIMIT

    monitored_ns_str_env = os.environ.get("MONITORED_NAMESPACES", DEFAULT_MONITORED_NAMESPACES_STR)
    config['monitored_namespaces_env'] = [ns.strip() for ns in monitored_ns_str_env.split(',') if ns.strip()]

    logging.debug(f"Loaded default/fallback configuration from environment: {config}")
    return config

# Chuyển thành hàm async
async def get_config(session, force_refresh=False):
    """Gets the agent configuration asynchronously, fetching from API if needed."""
    global _current_agent_config, _last_config_refresh_time
    now = time.time()

    # Sử dụng lock để đảm bảo chỉ một coroutine refresh config tại một thời điểm
    async with _config_refresh_lock:
        needs_refresh = force_refresh or not _current_agent_config or (now - _last_config_refresh_time >= CONFIG_REFRESH_INTERVAL_SECONDS)

        if not needs_refresh:
            return _current_agent_config.copy() # Trả về cache nếu không cần refresh

        logging.info("[Config Manager] Refreshing configuration (async)...")
        env_defaults = _load_config_from_env_defaults()
        # Gọi hàm fetch async
        api_config = await _fetch_config_from_api_async(session, env_defaults.get('obs_engine_url'), env_defaults.get('agent_id'))

        merged_config = env_defaults.copy()

        if api_config and isinstance(api_config, dict): # Kiểm tra api_config hợp lệ
            logging.info("[Config Manager] Merging API config with environment defaults.")
            try:
                scan_interval_api = int(api_config.get('scan_interval_seconds'))
                if scan_interval_api >= 10: merged_config['scan_interval_seconds'] = scan_interval_api
            except (ValueError, TypeError, KeyError): pass

            try:
                restart_threshold_api = int(api_config.get('restart_count_threshold'))
                if restart_threshold_api >= 1: merged_config['restart_count_threshold'] = restart_threshold_api
            except (ValueError, TypeError, KeyError): pass

            loki_scan_level_api = api_config.get('loki_scan_min_level', '').upper()
            valid_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
            if loki_scan_level_api in valid_levels: merged_config['loki_scan_min_level'] = loki_scan_level_api

            monitored_ns_api = api_config.get('monitored_namespaces')
            if isinstance(monitored_ns_api, list) and monitored_ns_api:
                merged_config['monitored_namespaces'] = [str(ns).strip() for ns in monitored_ns_api if isinstance(ns, str) and str(ns).strip()]
            else:
                merged_config['monitored_namespaces'] = merged_config.get('monitored_namespaces_env', [])
                if not merged_config['monitored_namespaces']:
                     merged_config['monitored_namespaces'] = [ns.strip() for ns in DEFAULT_MONITORED_NAMESPACES_STR.split(',') if ns.strip()]
        else:
            logging.warning("[Config Manager] Failed to fetch config from API or API returned invalid data. Using environment defaults/fallbacks.")
            merged_config['monitored_namespaces'] = merged_config.get('monitored_namespaces_env', [])
            if not merged_config['monitored_namespaces']:
                 merged_config['monitored_namespaces'] = [ns.strip() for ns in DEFAULT_MONITORED_NAMESPACES_STR.split(',') if ns.strip()]

        merged_config.pop('monitored_namespaces_env', None)

        _current_agent_config = merged_config
        _last_config_refresh_time = now
        logging.info("[Config Manager] Configuration refreshed/loaded (async).")
        logging.info(f"[Config Manager] Current effective config: ScanInterval={_current_agent_config.get('scan_interval_seconds')}, RestartThreshold={_current_agent_config.get('restart_count_threshold')}, LokiLevel={_current_agent_config.get('loki_scan_min_level')}, Namespaces={_current_agent_config.get('monitored_namespaces')}")

    return _current_agent_config.copy()

# Các hàm helper này không cần async vì chúng chỉ đọc từ config đã cache
def get_monitored_namespaces():
    # Lưu ý: hàm này có thể trả về config cũ nếu get_config chưa được gọi trong vòng lặp async
    # Nên gọi get_config(session) trước trong vòng lặp chính để đảm bảo config mới nhất
    return _current_agent_config.get('monitored_namespaces', [])

def get_cluster_name():
    return _current_agent_config.get('cluster_name', DEFAULT_K8S_CLUSTER_NAME)

def get_agent_id():
    return _current_agent_config.get('agent_id', AGENT_ID)

