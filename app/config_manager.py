# ai-agent1/app/config_manager.py
import os
import time
import json
import logging

# Import db_manager to load raw config
try:
    import db_manager
except ImportError:
    logging.critical("[Config Manager] Failed to import db_manager. Config loading will fail.")
    db_manager = None

# --- Constants for Defaults (Copied from main.py for fallback) ---
# It might be better to define defaults in one place and import them,
# but for simplicity, we define them here for this module's use.
DEFAULT_ENABLE_AI_ANALYSIS = os.environ.get("ENABLE_AI_ANALYSIS", "true").lower() == "true"
DEFAULT_AI_PROVIDER = os.environ.get("AI_PROVIDER", "gemini").lower()
DEFAULT_AI_MODEL_IDENTIFIER = os.environ.get("AI_MODEL_IDENTIFIER", "gemini-1.5-flash")
DEFAULT_MONITORED_NAMESPACES_STR = os.environ.get("DEFAULT_MONITORED_NAMESPACES", "kube-system,default")
DEFAULT_ENABLE_TELEGRAM_ALERTS = False
DEFAULT_LOKI_SCAN_MIN_LEVEL = "WARNING"
DEFAULT_SCAN_INTERVAL_SECONDS = 30
DEFAULT_RESTART_COUNT_THRESHOLD = 5
DEFAULT_ALERT_SEVERITY_LEVELS_STR = "WARNING,ERROR,CRITICAL"
DEFAULT_ALERT_COOLDOWN_MINUTES = 30
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
LOCAL_GEMINI_ENDPOINT_URL = os.environ.get("LOCAL_GEMINI_ENDPOINT") # Get endpoint from env

# --- Global State for Config Cache ---
_current_agent_config = {}
_last_config_refresh_time = 0
CONFIG_REFRESH_INTERVAL_SECONDS = int(os.environ.get("CONFIG_REFRESH_INTERVAL_SECONDS", 60))

# --- Public Functions ---

def get_config(force_refresh=False):
    """
    Loads agent configuration from the database, caches it, and returns it.
    Refreshes the cache if it's empty, expired, or force_refresh is True.
    """
    global _current_agent_config, _last_config_refresh_time
    now = time.time()

    if force_refresh or not _current_agent_config or (now - _last_config_refresh_time >= CONFIG_REFRESH_INTERVAL_SECONDS):
        logging.info("[Config Manager] Refreshing agent configuration from database...")
        if not db_manager:
             logging.error("[Config Manager] db_manager not available. Cannot load config.")
             # Return potentially stale cache or an empty dict if never loaded
             return _current_agent_config if _current_agent_config else {}

        config_from_db = db_manager.load_all_config()
        if not config_from_db:
             logging.warning("[Config Manager] Failed to load config from DB or DB is empty. Using defaults for processing.")

        # Process loaded config with defaults
        enable_ai_db = config_from_db.get('enable_ai_analysis', str(DEFAULT_ENABLE_AI_ANALYSIS)).lower()
        enable_telegram_db = config_from_db.get('enable_telegram_alerts', str(DEFAULT_ENABLE_TELEGRAM_ALERTS)).lower()
        default_prompt_fallback = DEFAULT_PROMPT_TEMPLATE

        processed_config = {
            'enable_ai_analysis': enable_ai_db == 'true',
            'ai_provider': config_from_db.get('ai_provider', DEFAULT_AI_PROVIDER).lower(),
            'ai_model_identifier': config_from_db.get('ai_model_identifier', DEFAULT_AI_MODEL_IDENTIFIER),
            'ai_api_key': config_from_db.get('ai_api_key', ''),
            'monitored_namespaces_json': config_from_db.get('monitored_namespaces', '[]'),
            'loki_scan_min_level': config_from_db.get('loki_scan_min_level', DEFAULT_LOKI_SCAN_MIN_LEVEL).upper(),
            'scan_interval_seconds': int(config_from_db.get('scan_interval_seconds', DEFAULT_SCAN_INTERVAL_SECONDS)),
            'restart_count_threshold': int(config_from_db.get('restart_count_threshold', DEFAULT_RESTART_COUNT_THRESHOLD)),
            'alert_severity_levels_str': config_from_db.get('alert_severity_levels', DEFAULT_ALERT_SEVERITY_LEVELS_STR),
            'alert_cooldown_minutes': int(config_from_db.get('alert_cooldown_minutes', DEFAULT_ALERT_COOLDOWN_MINUTES)),
            'enable_telegram_alerts': enable_telegram_db == 'true',
            'telegram_bot_token': config_from_db.get('telegram_bot_token', ''),
            'telegram_chat_id': config_from_db.get('telegram_chat_id', ''),
            'prompt_template': config_from_db.get('prompt_template', default_prompt_fallback),
            'local_gemini_endpoint': LOCAL_GEMINI_ENDPOINT_URL # Still needed for ai_providers
        }
        # Update derived values
        processed_config['alert_severity_levels'] = [
            level.strip().upper() for level in processed_config['alert_severity_levels_str'].split(',') if level.strip()
        ]

        _current_agent_config = processed_config # Update cache
        _last_config_refresh_time = now
        logging.info(f"[Config Manager] Agent configuration refreshed/loaded.")
        logging.info(f"[Config Manager] Current: AI Enabled={_current_agent_config['enable_ai_analysis']}, Provider={_current_agent_config['ai_provider']}, Telegram Alerts Enabled={_current_agent_config['enable_telegram_alerts']}")

    return _current_agent_config

def get_monitored_namespaces():
    """Gets the list of namespaces to monitor from the cached config."""
    config = get_config() # Get potentially cached config
    namespaces_json = config.get('monitored_namespaces_json', '[]')
    default_namespaces_list = [ns.strip() for ns in DEFAULT_MONITORED_NAMESPACES_STR.split(',') if ns.strip()]
    try:
        loaded_value = json.loads(namespaces_json)
        if isinstance(loaded_value, list) and loaded_value:
            monitored = [str(ns).strip() for ns in loaded_value if isinstance(ns, str) and str(ns).strip()]
            if monitored:
                logging.debug(f"[Config Manager] Using monitored namespaces: {monitored}")
                return monitored
            else:
                logging.warning("[Config Manager] Monitored namespaces list from DB is empty after cleaning. Using default.")
                return default_namespaces_list
        else:
            logging.warning("[Config Manager] Value for monitored_namespaces in DB is not a non-empty list. Using default.")
            return default_namespaces_list
    except json.JSONDecodeError:
        logging.warning(f"[Config Manager] Failed to decode monitored_namespaces JSON from DB: {namespaces_json}. Using default.")
        return default_namespaces_list
    except Exception as e:
        logging.error(f"[Config Manager] Unexpected error processing monitored_namespaces from DB: {e}. Using default.", exc_info=True)
        return default_namespaces_list

