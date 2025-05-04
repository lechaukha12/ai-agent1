import os
import json
import logging
import time
import aiohttp
import asyncio
import socket

DEFAULT_OBS_ENGINE_URL = ""
DEFAULT_SCAN_INTERVAL_SECONDS = 60
DEFAULT_CPU_THRESHOLD = 90.0
DEFAULT_MEM_THRESHOLD = 90.0
DEFAULT_DISK_THRESHOLDS = {'/': 90.0}
DEFAULT_MONITORED_SERVICES = []
DEFAULT_MONITORED_LOGS = []
DEFAULT_LOG_SCAN_KEYWORDS = ["ERROR", "WARN", "CRITICAL", "FATAL", "PANIC", "EXCEPTION", "Traceback"]
DEFAULT_LOG_SCAN_RANGE_MINUTES = 5
DEFAULT_LOG_CONTEXT_MINUTES = 30

try:
    DEFAULT_HOSTNAME = socket.gethostname()
except Exception:
    DEFAULT_HOSTNAME = "unknown-linux-host"

# --- Biến global để lưu trữ args từ CLI ---
_cli_args = None
# ----------------------------------------

_current_agent_config = {}
_last_config_refresh_time = 0
CONFIG_REFRESH_INTERVAL_SECONDS = int(os.environ.get("CONFIG_REFRESH_INTERVAL_SECONDS", 60))
_config_refresh_lock = asyncio.Lock()

# --- Hàm khởi tạo với args từ CLI ---
def init_with_args(args):
    """Stores command line arguments for later use in config loading."""
    global _cli_args
    _cli_args = args
    logging.debug(f"Initialized config manager with CLI args: {_cli_args}")
# -----------------------------------

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
                        response.raise_for_status()
                    else:
                        error_text = await response.text()
                        logging.error(f"[Config Manager] HTTP error {response.status} fetching config: {error_text[:200]}")
                        return None
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

def _parse_json_env_var(var_name, default_value):
    var_value = os.environ.get(var_name)
    if var_value:
        try:
            return json.loads(var_value)
        except json.JSONDecodeError:
            logging.warning(f"Failed to parse JSON from environment variable '{var_name}'. Using default. Value: {var_value[:100]}...")
            return default_value
    return default_value

# --- Đổi tên hàm và cập nhật logic ưu tiên ---
def _load_initial_config():
    """Loads initial config prioritizing CLI args, then ENV vars, then defaults."""
    config = {}
    global _cli_args

    # Ưu tiên CLI -> ENV -> Default
    config['obs_engine_url'] = getattr(_cli_args, 'obsengine', None) or os.environ.get("OBS_ENGINE_URL", DEFAULT_OBS_ENGINE_URL)
    config['agent_id'] = getattr(_cli_args, 'agent_id', None) or os.environ.get("AGENT_ID", DEFAULT_HOSTNAME) # Dùng hostname làm default ID nếu cả CLI và ENV đều thiếu
    config['environment_name'] = getattr(_cli_args, 'env_name', None) or os.environ.get("ENVIRONMENT_NAME", DEFAULT_HOSTNAME)

    config['hostname'] = DEFAULT_HOSTNAME # Luôn lưu hostname

    # Các config khác ưu tiên ENV -> Default (hoặc có thể thêm CLI args nếu muốn)
    try:
        config['scan_interval_seconds'] = int(os.environ.get("SCAN_INTERVAL_SECONDS", DEFAULT_SCAN_INTERVAL_SECONDS))
        if config['scan_interval_seconds'] < 10: config['scan_interval_seconds'] = 10
    except ValueError: config['scan_interval_seconds'] = DEFAULT_SCAN_INTERVAL_SECONDS

    try: config['cpu_threshold_percent'] = float(os.environ.get("CPU_THRESHOLD_PERCENT", DEFAULT_CPU_THRESHOLD))
    except ValueError: config['cpu_threshold_percent'] = DEFAULT_CPU_THRESHOLD

    try: config['mem_threshold_percent'] = float(os.environ.get("MEM_THRESHOLD_PERCENT", DEFAULT_MEM_THRESHOLD))
    except ValueError: config['mem_threshold_percent'] = DEFAULT_MEM_THRESHOLD

    config['disk_thresholds'] = _parse_json_env_var("DISK_THRESHOLDS", DEFAULT_DISK_THRESHOLDS)
    if not isinstance(config['disk_thresholds'], dict):
        logging.warning("DISK_THRESHOLDS from env is not a valid JSON object. Using default.")
        config['disk_thresholds'] = DEFAULT_DISK_THRESHOLDS

    config['monitored_services'] = _parse_json_env_var("MONITORED_SERVICES", DEFAULT_MONITORED_SERVICES)
    if not isinstance(config['monitored_services'], list):
        logging.warning("MONITORED_SERVICES from env is not a valid JSON array. Using default.")
        config['monitored_services'] = DEFAULT_MONITORED_SERVICES

    config['monitored_logs'] = _parse_json_env_var("MONITORED_LOGS", DEFAULT_MONITORED_LOGS)
    if not isinstance(config['monitored_logs'], list):
        logging.warning("MONITORED_LOGS from env is not a valid JSON array. Using default.")
        config['monitored_logs'] = DEFAULT_MONITORED_LOGS

    config['log_scan_keywords'] = _parse_json_env_var("LOG_SCAN_KEYWORDS", DEFAULT_LOG_SCAN_KEYWORDS)
    if not isinstance(config['log_scan_keywords'], list):
        logging.warning("LOG_SCAN_KEYWORDS from env is not a valid JSON array. Using default.")
        config['log_scan_keywords'] = DEFAULT_LOG_SCAN_KEYWORDS

    try: config['log_scan_range_minutes'] = int(os.environ.get("LOG_SCAN_RANGE_MINUTES", DEFAULT_LOG_SCAN_RANGE_MINUTES))
    except ValueError: config['log_scan_range_minutes'] = DEFAULT_LOG_SCAN_RANGE_MINUTES

    try: config['log_context_minutes'] = int(os.environ.get("LOG_CONTEXT_MINUTES", DEFAULT_LOG_CONTEXT_MINUTES))
    except ValueError: config['log_context_minutes'] = DEFAULT_LOG_CONTEXT_MINUTES

    logging.debug(f"Loaded initial configuration (CLI/ENV/Default): {config}")
    return config
# ------------------------------------------

async def get_config(session, force_refresh=False):
    global _current_agent_config, _last_config_refresh_time
    now = time.time()

    async with _config_refresh_lock:
        needs_refresh = force_refresh or not _current_agent_config or (now - _last_config_refresh_time >= CONFIG_REFRESH_INTERVAL_SECONDS)

        if not needs_refresh:
            return _current_agent_config.copy()

        logging.info("[Config Manager] Refreshing configuration (async)...")
        # Sử dụng hàm mới để load config ban đầu
        initial_config = _load_initial_config()
        api_config = await _fetch_config_from_api_async(session, initial_config.get('obs_engine_url'), initial_config.get('agent_id'))

        merged_config = initial_config.copy() # Bắt đầu với config đã load (ưu tiên CLI/ENV)

        if api_config and isinstance(api_config, dict):
            logging.info("[Config Manager] Merging API config with initial config.")

            # Ghi đè các giá trị từ API nếu có và hợp lệ
            # Lưu ý: Không ghi đè obs_engine_url, agent_id, environment_name từ API
            # vì chúng thường được cố định khi agent khởi chạy (từ CLI/ENV).
            try:
                scan_interval_api = int(api_config.get('scan_interval_seconds'))
                if scan_interval_api >= 10: merged_config['scan_interval_seconds'] = scan_interval_api
            except (ValueError, TypeError, KeyError): pass

            try:
                cpu_thresh_api = float(api_config.get('cpu_threshold_percent'))
                merged_config['cpu_threshold_percent'] = cpu_thresh_api
            except (ValueError, TypeError, KeyError): pass

            try:
                mem_thresh_api = float(api_config.get('mem_threshold_percent'))
                merged_config['mem_threshold_percent'] = mem_thresh_api
            except (ValueError, TypeError, KeyError): pass

            disk_thresh_api = api_config.get('disk_thresholds')
            if isinstance(disk_thresh_api, dict):
                 merged_config['disk_thresholds'] = disk_thresh_api

            mon_services_api = api_config.get('monitored_services')
            if isinstance(mon_services_api, list):
                 merged_config['monitored_services'] = [str(s) for s in mon_services_api]

            mon_logs_api = api_config.get('monitored_logs')
            if isinstance(mon_logs_api, list):
                 valid_logs = []
                 for log_conf in mon_logs_api:
                     if isinstance(log_conf, dict) and (log_conf.get("path") or log_conf.get("type") == "journal"):
                         valid_logs.append(log_conf)
                     else:
                         logging.warning(f"Ignoring invalid log configuration entry from API: {log_conf}")
                 merged_config['monitored_logs'] = valid_logs

            log_keywords_api = api_config.get('log_scan_keywords')
            if isinstance(log_keywords_api, list):
                 merged_config['log_scan_keywords'] = [str(k) for k in log_keywords_api]

            try:
                log_scan_min_api = int(api_config.get('log_scan_range_minutes'))
                merged_config['log_scan_range_minutes'] = log_scan_min_api
            except (ValueError, TypeError, KeyError): pass

            try:
                log_ctx_min_api = int(api_config.get('log_context_minutes'))
                merged_config['log_context_minutes'] = log_ctx_min_api
            except (ValueError, TypeError, KeyError): pass
        else:
            logging.warning("[Config Manager] Failed to fetch config from API or API returned invalid data. Using initial config (CLI/ENV/Default).")

        _current_agent_config = merged_config
        _last_config_refresh_time = now
        logging.info("[Config Manager] Configuration refreshed/loaded (async).")
        logging.info(f"[Config Manager] Current effective config: EnvName={_current_agent_config.get('environment_name')}, ScanInterval={_current_agent_config.get('scan_interval_seconds')}")

    return _current_agent_config.copy()

def get_environment_name():
    # Lấy từ config đã cache (đã ưu tiên CLI/ENV)
    return _current_agent_config.get('environment_name', DEFAULT_HOSTNAME)

def get_agent_id():
    # Lấy từ config đã cache (đã ưu tiên CLI/ENV)
    return _current_agent_config.get('agent_id', DEFAULT_HOSTNAME)

