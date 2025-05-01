import os
import json
import logging
import time

DEFAULT_LOKI_URL = "http://loki-read.monitoring.svc.cluster.local:3100"
DEFAULT_OBS_ENGINE_URL = ""
DEFAULT_SCAN_INTERVAL_SECONDS = 30
DEFAULT_RESTART_COUNT_THRESHOLD = 5
DEFAULT_LOKI_SCAN_MIN_LEVEL = "INFO"
DEFAULT_MONITORED_NAMESPACES_STR = "kube-system,default"
# Thêm default cho cluster name (có thể là 'unknown' hoặc rỗng)
DEFAULT_K8S_CLUSTER_NAME = "unknown-cluster"

_current_agent_config = {}
_last_config_refresh_time = 0
CONFIG_REFRESH_INTERVAL_SECONDS = int(os.environ.get("CONFIG_REFRESH_INTERVAL_SECONDS", 60))

def _load_config_from_env():
    config = {}
    config['loki_url'] = os.environ.get("LOKI_URL", DEFAULT_LOKI_URL)
    config['obs_engine_url'] = os.environ.get("OBS_ENGINE_URL", DEFAULT_OBS_ENGINE_URL)
    # Đọc tên cụm cluster từ biến môi trường
    config['cluster_name'] = os.environ.get("K8S_CLUSTER_NAME", DEFAULT_K8S_CLUSTER_NAME)

    try:
        config['scan_interval_seconds'] = int(os.environ.get("SCAN_INTERVAL_SECONDS", DEFAULT_SCAN_INTERVAL_SECONDS))
        if config['scan_interval_seconds'] < 10:
             logging.warning(f"SCAN_INTERVAL_SECONDS ({config['scan_interval_seconds']}) is less than 10. Adjusting to 10.")
             config['scan_interval_seconds'] = 10
    except ValueError:
        logging.warning(f"Invalid SCAN_INTERVAL_SECONDS value. Using default: {DEFAULT_SCAN_INTERVAL_SECONDS}")
        config['scan_interval_seconds'] = DEFAULT_SCAN_INTERVAL_SECONDS

    try:
        config['restart_count_threshold'] = int(os.environ.get("RESTART_COUNT_THRESHOLD", DEFAULT_RESTART_COUNT_THRESHOLD))
        if config['restart_count_threshold'] < 1:
             logging.warning(f"RESTART_COUNT_THRESHOLD ({config['restart_count_threshold']}) is less than 1. Adjusting to 1.")
             config['restart_count_threshold'] = 1
    except ValueError:
        logging.warning(f"Invalid RESTART_COUNT_THRESHOLD value. Using default: {DEFAULT_RESTART_COUNT_THRESHOLD}")
        config['restart_count_threshold'] = DEFAULT_RESTART_COUNT_THRESHOLD

    config['loki_scan_min_level'] = os.environ.get("LOKI_SCAN_MIN_LEVEL", DEFAULT_LOKI_SCAN_MIN_LEVEL).upper()
    valid_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
    if config['loki_scan_min_level'] not in valid_levels:
        logging.warning(f"Invalid LOKI_SCAN_MIN_LEVEL: {config['loki_scan_min_level']}. Using default: {DEFAULT_LOKI_SCAN_MIN_LEVEL}")
        config['loki_scan_min_level'] = DEFAULT_LOKI_SCAN_MIN_LEVEL

    monitored_ns_str = os.environ.get("MONITORED_NAMESPACES", DEFAULT_MONITORED_NAMESPACES_STR)
    try:
        loaded_ns = json.loads(monitored_ns_str)
        if isinstance(loaded_ns, list):
            config['monitored_namespaces'] = [str(ns).strip() for ns in loaded_ns if isinstance(ns, str) and str(ns).strip()]
        else:
            raise ValueError("JSON value is not a list")
    except (json.JSONDecodeError, ValueError):
        logging.debug(f"Could not parse MONITORED_NAMESPACES as JSON ('{monitored_ns_str}'). Falling back to comma-separated.")
        config['monitored_namespaces'] = [ns.strip() for ns in monitored_ns_str.split(',') if ns.strip()]

    if not config['monitored_namespaces']:
        logging.warning("Monitored namespaces list is empty after parsing. Using default.")
        config['monitored_namespaces'] = [ns.strip() for ns in DEFAULT_MONITORED_NAMESPACES_STR.split(',') if ns.strip()]

    logging.debug(f"Loaded configuration from environment: {config}")
    return config


def get_config(force_refresh=False):
    global _current_agent_config, _last_config_refresh_time
    now = time.time()

    logging.debug("[Config Manager] Loading configuration from environment variables...")
    _current_agent_config = _load_config_from_env()
    _last_config_refresh_time = now
    logging.debug("[Config Manager] Configuration loaded.")

    return _current_agent_config.copy()

def get_monitored_namespaces():
    config = get_config()
    return config.get('monitored_namespaces', [])

# Thêm hàm tiện ích để lấy cluster_name
def get_cluster_name():
    config = get_config()
    return config.get('cluster_name', DEFAULT_K8S_CLUSTER_NAME)

