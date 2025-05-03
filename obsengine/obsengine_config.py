# ai-agent1/obsengine/obsengine_config.py
import os
import time
import json
import logging

try:
    import db_manager
except ImportError:
    logging.critical("[Config Manager - ObsEngine] Failed to import db_manager. Config loading from DB will fail.")
    db_manager = None

# --- Default Configuration Values ---
DEFAULT_DB_PATH = "/data/obsengine_data.db"
# --- THAY ĐỔI Ở ĐÂY ---
DEFAULT_ENABLE_AI_ANALYSIS = False # Đặt thành False để tắt AI mặc định
# ---------------------
DEFAULT_AI_PROVIDER = "gemini" # Vẫn giữ Gemini làm provider mặc định nếu bật AI sau
DEFAULT_AI_MODEL_IDENTIFIER = "gemini-1.5-flash"
DEFAULT_LOCAL_GEMINI_ENDPOINT = ""
DEFAULT_MONITORED_NAMESPACES_STR = "kube-system,default"
DEFAULT_PROMPT_TEMPLATE = """
Phân tích tình huống của pod Kubernetes '{namespace}/{pod_name}'.
**Ưu tiên xem xét ngữ cảnh Kubernetes** được cung cấp dưới đây vì nó có thể là lý do chính bạn được gọi.
Kết hợp với các dòng log sau đây (nếu có) để đưa ra phân tích đầy đủ.

**Lý do ban đầu được phát hiện (Initial Reasons):** {initial_reasons}

1.  Xác định mức độ nghiêm trọng tổng thể (chọn một: INFO, WARNING, ERROR, CRITICAL). Dựa vào cả Initial Reasons và nội dung logs/context.
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
DEFAULT_ENABLE_TELEGRAM_ALERTS = False
DEFAULT_ALERT_SEVERITY_LEVELS_STR = "WARNING,ERROR,CRITICAL"
DEFAULT_ALERT_COOLDOWN_MINUTES = 30
DEFAULT_STATS_UPDATE_INTERVAL_SECONDS = 300
DEFAULT_SCAN_INTERVAL_SECONDS = 30
DEFAULT_RESTART_COUNT_THRESHOLD = 5
DEFAULT_LOKI_SCAN_MIN_LEVEL = "INFO"

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

        # Ưu tiên giá trị từ DB, sau đó mới đến default trong code
        enable_ai_db = config_from_db.get('enable_ai_analysis', str(DEFAULT_ENABLE_AI_ANALYSIS)).lower()
        processed_config['enable_ai_analysis'] = enable_ai_db == 'true'
        processed_config['ai_provider'] = config_from_db.get('ai_provider', DEFAULT_AI_PROVIDER).lower()
        processed_config['ai_model_identifier'] = config_from_db.get('ai_model_identifier', DEFAULT_AI_MODEL_IDENTIFIER)
        processed_config['local_gemini_endpoint'] = config_from_db.get('local_gemini_endpoint', DEFAULT_LOCAL_GEMINI_ENDPOINT)
        processed_config['ai_api_key'] = gemini_api_key_env or config_from_db.get('ai_api_key', '')

        processed_config['prompt_template'] = config_from_db.get('prompt_template', DEFAULT_PROMPT_TEMPLATE)

        enable_telegram_db = config_from_db.get('enable_telegram_alerts', str(DEFAULT_ENABLE_TELEGRAM_ALERTS)).lower()
        processed_config['enable_telegram_alerts'] = enable_telegram_db == 'true'
        processed_config['telegram_bot_token'] = telegram_bot_token_env or config_from_db.get('telegram_bot_token', '')
        processed_config['telegram_chat_id'] = telegram_chat_id_env or config_from_db.get('telegram_chat_id', '')

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

        processed_config['default_scan_interval_seconds'] = DEFAULT_SCAN_INTERVAL_SECONDS
        processed_config['default_restart_count_threshold'] = DEFAULT_RESTART_COUNT_THRESHOLD
        processed_config['default_loki_scan_min_level'] = DEFAULT_LOKI_SCAN_MIN_LEVEL
        processed_config['default_monitored_namespaces_str'] = DEFAULT_MONITORED_NAMESPACES_STR

        _current_obsengine_config = processed_config
        _last_config_refresh_time = now
        logging.info("[Config Manager - ObsEngine] Configuration refreshed/loaded.")
        logging.debug(f"[Config Manager - ObsEngine] Current Config (Secrets Masked): "
                      f"DB={processed_config.get('db_path')}, "
                      f"AI Enabled={processed_config.get('enable_ai_analysis')}, " # Log giá trị đã xử lý
                      f"Provider={processed_config.get('ai_provider')}, "
                      f"Model={processed_config.get('ai_model_identifier')}, "
                      f"Local Endpoint={processed_config.get('local_gemini_endpoint')}, "
                      f"AI Key Set={'Yes' if processed_config.get('ai_api_key') else 'No'}, "
                      f"Telegram Enabled={processed_config.get('enable_telegram_alerts')}, "
                      f"TG Token Set={'Yes' if processed_config.get('telegram_bot_token') else 'No'}, "
                      f"TG Chat ID Set={'Yes' if processed_config.get('telegram_chat_id') else 'No'}, "
                      f"Alert Levels={processed_config.get('alert_severity_levels')}, "
                      f"Cooldown={processed_config.get('alert_cooldown_minutes')}m")

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
        'local_gemini_endpoint': config.get('local_gemini_endpoint', DEFAULT_LOCAL_GEMINI_ENDPOINT),
        'ai_api_key': config.get('ai_api_key', ''),
        'prompt_template': config.get('prompt_template', DEFAULT_PROMPT_TEMPLATE)
    }

def get_alert_config():
    config = get_config()
    return {
        'enable_telegram_alerts': config.get('enable_telegram_alerts', DEFAULT_ENABLE_TELEGRAM_ALERTS),
        'telegram_bot_token': config.get('telegram_bot_token', ''),
        'telegram_chat_id': config.get('telegram_chat_id', ''),
        'alert_severity_levels': config.get('alert_severity_levels', []),
        'alert_cooldown_minutes': config.get('alert_cooldown_minutes', DEFAULT_ALERT_COOLDOWN_MINUTES)
    }

def get_stats_update_interval():
     config = get_config()
     return config.get('stats_update_interval_seconds', DEFAULT_STATS_UPDATE_INTERVAL_SECONDS)

def get_agent_defaults():
    config = get_config()
    return {
        'scan_interval_seconds': config.get('default_scan_interval_seconds', DEFAULT_SCAN_INTERVAL_SECONDS),
        'restart_count_threshold': config.get('default_restart_count_threshold', DEFAULT_RESTART_COUNT_THRESHOLD),
        'loki_scan_min_level': config.get('default_loki_scan_min_level', DEFAULT_LOKI_SCAN_MIN_LEVEL),
        'monitored_namespaces': [ns.strip() for ns in config.get('default_monitored_namespaces_str', DEFAULT_MONITORED_NAMESPACES_STR).split(',') if ns.strip()]
    }
