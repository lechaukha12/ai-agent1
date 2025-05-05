# -*- coding: utf-8 -*-
import os
import time
import json
import logging

try:
    import db_manager
except ImportError:
    logging.critical("[Config Manager - ObsEngine] Failed to import db_manager. Config loading from DB will fail.")
    db_manager = None


DEFAULT_DB_PATH = "/data/obsengine_data.db"
DEFAULT_ENABLE_AI_ANALYSIS = False
DEFAULT_AI_PROVIDER = "gemini"
DEFAULT_AI_MODEL_IDENTIFIER = "gemini-1.5-flash"
DEFAULT_LOCAL_GEMINI_ENDPOINT = "" # Giữ giá trị mặc định là rỗng
DEFAULT_MONITORED_NAMESPACES_STR = "kube-system,default"
DEFAULT_PROMPT_TEMPLATE = """
Phân tích tình huống của tài nguyên '{resource_name}' (Loại: {resource_type}) trong môi trường '{environment_name}' (Loại: {environment_type}).
**Ưu tiên xem xét Ngữ cảnh Môi trường** được cung cấp dưới đây vì nó có thể chứa lý do chính.
Kết hợp với các dòng log sau đây (nếu có) để đưa ra phân tích đầy đủ.

**Lý do ban đầu được phát hiện (Initial Reasons):** {initial_reasons}

1.  Xác định mức độ nghiêm trọng tổng thể (chọn một: INFO, WARNING, ERROR, CRITICAL). Dựa vào cả Initial Reasons và nội dung logs/context.
2.  Nếu mức độ nghiêm trọng là WARNING, ERROR hoặc CRITICAL:
    a. Cung cấp một bản tóm tắt ngắn gọn (1-2 câu) bằng **tiếng Việt** giải thích vấn đề cốt lõi.
    b. Đề xuất **nguyên nhân gốc có thể xảy ra** (potential root causes) (ngắn gọn, dạng gạch đầu dòng nếu có nhiều).
    c. Đề xuất các **bước khắc phục sự cố** (suggested troubleshooting steps) (ngắn gọn, dạng gạch đầu dòng).

Ngữ cảnh Môi trường:
--- START CONTEXT ---
{environment_context}
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
DEFAULT_ENABLE_TELEGRAM_ALERTS = False
DEFAULT_ALERT_SEVERITY_LEVELS_STR = "WARNING,ERROR,CRITICAL"
DEFAULT_ALERT_COOLDOWN_MINUTES = 30
DEFAULT_STATS_UPDATE_INTERVAL_SECONDS = 300
DEFAULT_SCAN_INTERVAL_SECONDS = 60 # Default chung cho các agent
# Các default khác cho từng loại agent (K8s, Linux, Loki)
DEFAULT_RESTART_COUNT_THRESHOLD = 5
DEFAULT_LOKI_SCAN_MIN_LEVEL = "INFO"
DEFAULT_CPU_THRESHOLD = 90.0
DEFAULT_MEM_THRESHOLD = 90.0
DEFAULT_DISK_THRESHOLDS = {'/': 90.0}
DEFAULT_MONITORED_SERVICES = []
DEFAULT_MONITORED_LOGS = []
DEFAULT_LOKI_URL = ''
DEFAULT_LOGQL_QUERIES = []
DEFAULT_LOG_SCAN_RANGE_MINUTES = 5
DEFAULT_LOG_CONTEXT_MINUTES = 30
DEFAULT_LOKI_QUERY_LIMIT = 500


_current_obsengine_config = {}
_last_config_refresh_time = 0
CONFIG_REFRESH_INTERVAL_SECONDS = int(os.environ.get("CONFIG_REFRESH_INTERVAL_SECONDS", 60))

def _get_env_var(key, default=None):
    return os.environ.get(key, default)

def get_config(force_refresh=False):
    global _current_obsengine_config, _last_config_refresh_time
    now = time.time()

    if force_refresh or not _current_obsengine_config or (now - _last_config_refresh_time >= CONFIG_REFRESH_INTERVAL_SECONDS):
        logging.info("[Config Manager - ObsEngine] Refreshing configuration...")

        db_path_env = _get_env_var("DB_PATH")
        gemini_api_key_env = _get_env_var("GEMINI_API_KEY")
        telegram_bot_token_env = _get_env_var("TELEGRAM_BOT_TOKEN")
        telegram_chat_id_env = _get_env_var("TELEGRAM_CHAT_ID")

        db_path_for_load = db_path_env or DEFAULT_DB_PATH
        config_from_db = {}
        if db_manager:
            try:
                config_from_db = db_manager.load_all_global_config(db_path_for_load)
                if not config_from_db:
                    logging.warning("[Config Manager - ObsEngine] Failed to load config from DB or DB is empty. Using defaults/env vars.")
            except AttributeError:
                 logging.critical("[Config Manager - ObsEngine] db_manager module is missing the 'load_all_global_config' function! Using defaults/env vars.", exc_info=True)
                 config_from_db = {}
            except Exception as db_load_err:
                 logging.error(f"[Config Manager - ObsEngine] Error loading config from DB: {db_load_err}. Using defaults/env vars.", exc_info=True)
                 config_from_db = {}
        else:
            logging.error("[Config Manager - ObsEngine] db_manager not available. Cannot load settings from database. Using defaults/env vars.")

        processed_config = {}
        processed_config['db_path'] = db_path_for_load

        enable_ai_db = config_from_db.get('enable_ai_analysis', str(DEFAULT_ENABLE_AI_ANALYSIS)).lower()
        processed_config['enable_ai_analysis'] = enable_ai_db == 'true'
        processed_config['ai_provider'] = config_from_db.get('ai_provider', DEFAULT_AI_PROVIDER).lower()
        processed_config['ai_model_identifier'] = config_from_db.get('ai_model_identifier', DEFAULT_AI_MODEL_IDENTIFIER)
        # --- Đọc local_gemini_endpoint từ DB, fallback về default ---
        processed_config['local_gemini_endpoint'] = config_from_db.get('local_gemini_endpoint', DEFAULT_LOCAL_GEMINI_ENDPOINT)
        # ----------------------------------------------------------
        processed_config['ai_api_key'] = gemini_api_key_env or '' # API Key chỉ lấy từ ENV

        processed_config['prompt_template'] = config_from_db.get('prompt_template', DEFAULT_PROMPT_TEMPLATE)

        enable_telegram_db = config_from_db.get('enable_telegram_alerts', str(DEFAULT_ENABLE_TELEGRAM_ALERTS)).lower()
        processed_config['enable_telegram_alerts'] = enable_telegram_db == 'true'
        processed_config['telegram_bot_token'] = telegram_bot_token_env or '' # Token chỉ lấy từ ENV
        processed_config['telegram_chat_id'] = telegram_chat_id_env or config_from_db.get('telegram_chat_id', '') # Chat ID có thể từ DB hoặc ENV

        alert_levels_str = config_from_db.get('alert_severity_levels', DEFAULT_ALERT_SEVERITY_LEVELS_STR)
        processed_config['alert_severity_levels_str'] = alert_levels_str
        processed_config['alert_severity_levels'] = [
            level.strip().upper() for level in alert_levels_str.split(',') if level.strip()
        ]
        try:
            cooldown_minutes = int(config_from_db.get('alert_cooldown_minutes', DEFAULT_ALERT_COOLDOWN_MINUTES))
            processed_config['alert_cooldown_minutes'] = cooldown_minutes if cooldown_minutes >= 0 else DEFAULT_ALERT_COOLDOWN_MINUTES
        except (ValueError, TypeError):
            logging.warning(f"Invalid or missing alert_cooldown_minutes value in DB. Using default: {DEFAULT_ALERT_COOLDOWN_MINUTES}")
            processed_config['alert_cooldown_minutes'] = DEFAULT_ALERT_COOLDOWN_MINUTES

        try:
             processed_config['stats_update_interval_seconds'] = int(_get_env_var("STATS_UPDATE_INTERVAL_SECONDS", DEFAULT_STATS_UPDATE_INTERVAL_SECONDS))
        except ValueError:
             logging.warning(f"Invalid STATS_UPDATE_INTERVAL_SECONDS value. Using default: {DEFAULT_STATS_UPDATE_INTERVAL_SECONDS}")
             processed_config['stats_update_interval_seconds'] = DEFAULT_STATS_UPDATE_INTERVAL_SECONDS

        # --- Load các default cho agent (để dùng khi get_agent_config) ---
        processed_config['default_scan_interval_seconds'] = config_from_db.get('default_scan_interval_seconds', str(DEFAULT_SCAN_INTERVAL_SECONDS))
        # K8s defaults
        processed_config['default_monitored_namespaces_str'] = config_from_db.get('default_monitored_namespaces', json.dumps(DEFAULT_MONITORED_NAMESPACES_STR.split(','))) # Lưu dạng JSON string trong DB
        processed_config['default_restart_count_threshold'] = config_from_db.get('default_restart_count_threshold', str(DEFAULT_RESTART_COUNT_THRESHOLD))
        processed_config['default_loki_scan_min_level'] = config_from_db.get('default_loki_scan_min_level', DEFAULT_LOKI_SCAN_MIN_LEVEL)
        # Linux defaults
        processed_config['default_cpu_threshold_percent'] = config_from_db.get('default_cpu_threshold_percent', str(DEFAULT_CPU_THRESHOLD))
        processed_config['default_mem_threshold_percent'] = config_from_db.get('default_mem_threshold_percent', str(DEFAULT_MEM_THRESHOLD))
        processed_config['default_disk_thresholds'] = config_from_db.get('default_disk_thresholds', json.dumps(DEFAULT_DISK_THRESHOLDS))
        processed_config['default_monitored_services'] = config_from_db.get('default_monitored_services', json.dumps(DEFAULT_MONITORED_SERVICES))
        processed_config['default_monitored_logs'] = config_from_db.get('default_monitored_logs', json.dumps(DEFAULT_MONITORED_LOGS))
        # Loki defaults
        processed_config['default_loki_url'] = config_from_db.get('default_loki_url', DEFAULT_LOKI_URL)
        processed_config['default_logql_queries'] = config_from_db.get('default_logql_queries', json.dumps(DEFAULT_LOGQL_QUERIES))
        processed_config['default_log_scan_range_minutes'] = config_from_db.get('default_log_scan_range_minutes', str(DEFAULT_LOG_SCAN_RANGE_MINUTES))
        processed_config['default_loki_query_limit'] = config_from_db.get('default_loki_query_limit', str(DEFAULT_LOKI_QUERY_LIMIT))
        # -------------------------------------------------------------------

        _current_obsengine_config = processed_config
        _last_config_refresh_time = now
        logging.info("[Config Manager - ObsEngine] Configuration refreshed/loaded.")
        # Log config debug (ẩn key và token)
        log_config_debug = {k: ('********' if k in ['ai_api_key', 'telegram_bot_token'] else v) for k, v in processed_config.items()}
        logging.debug(f"[Config Manager - ObsEngine] Current Config (Secrets Masked): {log_config_debug}")


    return _current_obsengine_config.copy()

def get_db_path():
    config = get_config()
    return config.get('db_path', DEFAULT_DB_PATH)

def get_ai_config():
    config = get_config()
    return {
        'enable_ai_analysis': config.get('enable_ai_analysis', DEFAULT_ENABLE_AI_ANALYSIS),
        'ai_provider': config.get('ai_provider', DEFAULT_AI_PROVIDER),
        'ai_model_identifier': config.get('ai_model_identifier', DEFAULT_AI_MODEL_IDENTIFIER),
        'local_gemini_endpoint': config.get('local_gemini_endpoint', DEFAULT_LOCAL_GEMINI_ENDPOINT), # Trả về endpoint đã load
        'ai_api_key': config.get('ai_api_key', ''), # Lấy từ ENV (đã xử lý trong get_config)
        'prompt_template': config.get('prompt_template', DEFAULT_PROMPT_TEMPLATE)
    }

def get_alert_config():
    config = get_config()
    return {
        'enable_telegram_alerts': config.get('enable_telegram_alerts', DEFAULT_ENABLE_TELEGRAM_ALERTS),
        'telegram_bot_token': config.get('telegram_bot_token', ''), # Lấy từ ENV
        'telegram_chat_id': config.get('telegram_chat_id', ''), # Lấy từ ENV hoặc DB
        'alert_severity_levels': config.get('alert_severity_levels', []),
        'alert_cooldown_minutes': config.get('alert_cooldown_minutes', DEFAULT_ALERT_COOLDOWN_MINUTES)
    }

def get_stats_update_interval():
     config = get_config()
     # Đọc giá trị đã được chuyển thành int trong get_config
     return config.get('stats_update_interval_seconds', DEFAULT_STATS_UPDATE_INTERVAL_SECONDS)

def get_agent_defaults():
    """Lấy các giá trị default global cho agent config."""
    config = get_config()
    # Trả về các giá trị default đã được load từ DB hoặc code defaults
    # Cần parse lại JSON string cho các list/dict
    defaults = {
        'scan_interval_seconds': int(config.get('default_scan_interval_seconds', DEFAULT_SCAN_INTERVAL_SECONDS)),
        # K8s
        'restart_count_threshold': int(config.get('default_restart_count_threshold', DEFAULT_RESTART_COUNT_THRESHOLD)),
        'loki_scan_min_level': config.get('default_loki_scan_min_level', DEFAULT_LOKI_SCAN_MIN_LEVEL),
        'monitored_namespaces': json.loads(config.get('default_monitored_namespaces_str', '[]')),
        # Linux
        'cpu_threshold_percent': float(config.get('default_cpu_threshold_percent', DEFAULT_CPU_THRESHOLD)),
        'mem_threshold_percent': float(config.get('default_mem_threshold_percent', DEFAULT_MEM_THRESHOLD)),
        'disk_thresholds': json.loads(config.get('default_disk_thresholds', '{}')),
        'monitored_services': json.loads(config.get('default_monitored_services', '[]')),
        'monitored_logs': json.loads(config.get('default_monitored_logs', '[]')),
        # Loki
        'loki_url': config.get('default_loki_url', DEFAULT_LOKI_URL),
        'logql_queries': json.loads(config.get('default_logql_queries', '[]')),
        'log_scan_range_minutes': int(config.get('default_log_scan_range_minutes', DEFAULT_LOG_SCAN_RANGE_MINUTES)),
        'loki_query_limit': int(config.get('default_loki_query_limit', DEFAULT_LOKI_QUERY_LIMIT)),
        # Chung
        'log_context_minutes': int(config.get('default_log_context_minutes', DEFAULT_LOG_CONTEXT_MINUTES))
    }
    # Xử lý lỗi parse JSON nếu có
    for key in ['monitored_namespaces', 'disk_thresholds', 'monitored_services', 'monitored_logs', 'logql_queries']:
        if not isinstance(defaults[key], (list, dict)):
            logging.warning(f"Failed to parse default JSON for '{key}'. Using empty default.")
            defaults[key] = [] if key != 'disk_thresholds' else {}

    return defaults

