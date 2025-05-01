# ai-agent1/obsengine/config_manager.py

import os
import time
import json
import logging

# Import db_manager to load config from the database
try:
    # Assuming db_manager.py is in the same directory within obsengine
    import db_manager
except ImportError:
    logging.critical("[Config Manager - ObsEngine] Failed to import db_manager. Config loading from DB will fail.")
    db_manager = None

# --- Constants for Defaults (ObsEngine Specific) ---
# These defaults are used if settings are missing from both Env Vars and DB
DEFAULT_DB_PATH = "/data/obsengine_data.db" # Default path for ObsEngine's data/DB
DEFAULT_ENABLE_AI_ANALYSIS = True
DEFAULT_AI_PROVIDER = "gemini"
DEFAULT_AI_MODEL_IDENTIFIER = "gemini-1.5-flash"
DEFAULT_LOCAL_GEMINI_ENDPOINT = "" # Default if using 'local' provider but endpoint not set
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
DEFAULT_ENABLE_TELEGRAM_ALERTS = False
DEFAULT_ALERT_SEVERITY_LEVELS_STR = "WARNING,ERROR,CRITICAL"
DEFAULT_ALERT_COOLDOWN_MINUTES = 30
DEFAULT_STATS_UPDATE_INTERVAL_SECONDS = 300 # Interval for ObsEngine to update its own stats

# --- Global State for Config Cache ---
_current_obsengine_config = {}
_last_config_refresh_time = 0
# Read refresh interval from env, default to 60s. ObsEngine might need this if config changes often via Portal.
CONFIG_REFRESH_INTERVAL_SECONDS = int(os.environ.get("CONFIG_REFRESH_INTERVAL_SECONDS", 60))

# --- Helper Function to Safely Get Env Var ---
def _get_env_var(key, default=None):
    """Gets an environment variable, returning default if not set."""
    return os.environ.get(key, default)

# --- Public Functions ---

def get_config(force_refresh=False):
    """
    Loads ObsEngine configuration from environment variables (for secrets)
    and the database (for portal-managed settings), caches it, and returns it.
    Refreshes the cache if it's empty, expired, or force_refresh is True.
    """
    global _current_obsengine_config, _last_config_refresh_time
    now = time.time()

    if force_refresh or not _current_obsengine_config or (now - _last_config_refresh_time >= CONFIG_REFRESH_INTERVAL_SECONDS):
        logging.info("[Config Manager - ObsEngine] Refreshing configuration...")

        # 1. Load Secrets/Essentials from Environment Variables
        db_path = _get_env_var("DB_PATH", DEFAULT_DB_PATH)
        gemini_api_key = _get_env_var("GEMINI_API_KEY", "") # Expecting this from K8s Secret
        telegram_bot_token = _get_env_var("TELEGRAM_BOT_TOKEN", "") # Expecting this from K8s Secret
        telegram_chat_id = _get_env_var("TELEGRAM_CHAT_ID", "") # Expecting this from K8s Secret
        # Potentially other secrets like OpenAI key, etc.

        # 2. Load Configurable Settings from Database
        config_from_db = {}
        if db_manager:
            config_from_db = db_manager.load_all_config(db_path)
            if not config_from_db:
                logging.warning("[Config Manager - ObsEngine] Failed to load config from DB or DB is empty. Using defaults where applicable.")
        else:
            logging.error("[Config Manager - ObsEngine] db_manager not available. Cannot load settings from database. Using defaults.")

        # 3. Combine and Process Configuration with Defaults
        processed_config = {}

        # Database Path
        processed_config['db_path'] = db_path

        # AI Settings (DB overrides default, Env Var overrides DB for key)
        enable_ai_db = config_from_db.get('enable_ai_analysis', str(DEFAULT_ENABLE_AI_ANALYSIS)).lower()
        processed_config['enable_ai_analysis'] = enable_ai_db == 'true'
        processed_config['ai_provider'] = config_from_db.get('ai_provider', DEFAULT_AI_PROVIDER).lower()
        processed_config['ai_model_identifier'] = config_from_db.get('ai_model_identifier', DEFAULT_AI_MODEL_IDENTIFIER)
        processed_config['local_gemini_endpoint'] = config_from_db.get('local_gemini_endpoint', DEFAULT_LOCAL_GEMINI_ENDPOINT) # Endpoint for 'local' provider
        # API Key: Prioritize Env Var > DB value (though DB shouldn't store it ideally) > Default (empty)
        processed_config['ai_api_key'] = gemini_api_key or config_from_db.get('ai_api_key', '') # Use Env Var if set

        # Prompt Template (DB overrides default)
        processed_config['prompt_template'] = config_from_db.get('prompt_template', DEFAULT_PROMPT_TEMPLATE)

        # Telegram Settings (DB overrides default, Env Vars override DB for token/chat_id)
        enable_telegram_db = config_from_db.get('enable_telegram_alerts', str(DEFAULT_ENABLE_TELEGRAM_ALERTS)).lower()
        processed_config['enable_telegram_alerts'] = enable_telegram_db == 'true'
        # Secrets: Prioritize Env Vars > DB values > Default (empty)
        processed_config['telegram_bot_token'] = telegram_bot_token or config_from_db.get('telegram_bot_token', '')
        processed_config['telegram_chat_id'] = telegram_chat_id or config_from_db.get('telegram_chat_id', '')

        # Alerting Logic Settings (DB overrides default)
        alert_levels_str = config_from_db.get('alert_severity_levels', DEFAULT_ALERT_SEVERITY_LEVELS_STR)
        processed_config['alert_severity_levels_str'] = alert_levels_str
        processed_config['alert_severity_levels'] = [
            level.strip().upper() for level in alert_levels_str.split(',') if level.strip()
        ]
        try:
            cooldown_minutes = int(config_from_db.get('alert_cooldown_minutes', DEFAULT_ALERT_COOLDOWN_MINUTES))
            processed_config['alert_cooldown_minutes'] = cooldown_minutes if cooldown_minutes >= 0 else DEFAULT_ALERT_COOLDOWN_MINUTES
        except ValueError:
            logging.warning(f"Invalid alert_cooldown_minutes value in DB. Using default: {DEFAULT_ALERT_COOLDOWN_MINUTES}")
            processed_config['alert_cooldown_minutes'] = DEFAULT_ALERT_COOLDOWN_MINUTES

        # ObsEngine specific settings (can be from Env Var or defaults)
        try:
             processed_config['stats_update_interval_seconds'] = int(_get_env_var("STATS_UPDATE_INTERVAL_SECONDS", DEFAULT_STATS_UPDATE_INTERVAL_SECONDS))
        except ValueError:
             logging.warning(f"Invalid STATS_UPDATE_INTERVAL_SECONDS value. Using default: {DEFAULT_STATS_UPDATE_INTERVAL_SECONDS}")
             processed_config['stats_update_interval_seconds'] = DEFAULT_STATS_UPDATE_INTERVAL_SECONDS


        # Update cache
        _current_obsengine_config = processed_config
        _last_config_refresh_time = now
        logging.info("[Config Manager - ObsEngine] Configuration refreshed/loaded.")
        logging.debug(f"[Config Manager - ObsEngine] Current Config (Secrets Masked): "
                      f"DB={processed_config['db_path']}, "
                      f"AI Enabled={processed_config['enable_ai_analysis']}, "
                      f"Provider={processed_config['ai_provider']}, "
                      f"Model={processed_config['ai_model_identifier']}, "
                      f"AI Key Set={'Yes' if processed_config['ai_api_key'] else 'No'}, "
                      f"Telegram Enabled={processed_config['enable_telegram_alerts']}, "
                      f"TG Token Set={'Yes' if processed_config['telegram_bot_token'] else 'No'}, "
                      f"TG Chat ID Set={'Yes' if processed_config['telegram_chat_id'] else 'No'}")

    # Return a copy to prevent accidental modification of the cache
    return _current_obsengine_config.copy()

# --- Convenience functions to get specific parts of the config ---

def get_db_path():
    """Returns the configured database path."""
    config = get_config()
    return config.get('db_path', DEFAULT_DB_PATH)

def get_ai_config():
    """Returns a dictionary containing only AI-related configurations."""
    config = get_config()
    return {
        'enable_ai_analysis': config.get('enable_ai_analysis', DEFAULT_ENABLE_AI_ANALYSIS),
        'ai_provider': config.get('ai_provider', DEFAULT_AI_PROVIDER),
        'ai_model_identifier': config.get('ai_model_identifier', DEFAULT_AI_MODEL_IDENTIFIER),
        'local_gemini_endpoint': config.get('local_gemini_endpoint', DEFAULT_LOCAL_GEMINI_ENDPOINT),
        'ai_api_key': config.get('ai_api_key', ''), # Includes the key from env/db
        'prompt_template': config.get('prompt_template', DEFAULT_PROMPT_TEMPLATE)
    }

def get_alert_config():
    """Returns a dictionary containing only alerting-related configurations."""
    config = get_config()
    return {
        'enable_telegram_alerts': config.get('enable_telegram_alerts', DEFAULT_ENABLE_TELEGRAM_ALERTS),
        'telegram_bot_token': config.get('telegram_bot_token', ''), # Includes the token from env/db
        'telegram_chat_id': config.get('telegram_chat_id', ''), # Includes the chat_id from env/db
        'alert_severity_levels': config.get('alert_severity_levels', []), # The processed list
        'alert_cooldown_minutes': config.get('alert_cooldown_minutes', DEFAULT_ALERT_COOLDOWN_MINUTES)
    }

def get_stats_update_interval():
     """Returns the interval for updating ObsEngine's own stats."""
     config = get_config()
     return config.get('stats_update_interval_seconds', DEFAULT_STATS_UPDATE_INTERVAL_SECONDS)

