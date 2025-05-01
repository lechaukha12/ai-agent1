import os
import logging
import sys
import json
import threading
import time
from flask import Flask, request, jsonify
from datetime import datetime, timezone

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=log_level,
                    format='%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s',
                    stream=sys.stdout)

logger = logging.getLogger(__name__)

logger.info(f"--- ObsEngine app.py starting execution (Log Level: {log_level}) ---")
try:
    logger.info(f"Current sys.path: {sys.path}")
except Exception as path_err:
    logger.error(f"Error getting sys.path: {path_err}")

try:
    logger.info("Importing config_manager...")
    import config_manager
    logger.info("Imported config_manager.")
    try:
        logger.info(f"Imported config_manager from: {config_manager.__file__}")
    except Exception as file_err:
        logger.error(f"Error getting config_manager.__file__: {file_err}")
    try:
        logger.info(f"Attributes in imported config_manager: {dir(config_manager)}")
    except Exception as dir_err:
        logger.error(f"Error getting dir(config_manager): {dir_err}")

    logger.info("Importing db_manager...")
    import db_manager
    logger.info("Imported db_manager.")
    try:
        logger.info(f"Imported db_manager from: {db_manager.__file__}")
    except Exception as file_err:
        logger.error(f"Error getting db_manager.__file__: {file_err}")


    logger.info("Importing ai_providers...")
    import ai_providers
    logger.info("Imported ai_providers.")
    try:
        logger.info(f"Imported ai_providers from: {ai_providers.__file__}")
    except Exception as file_err:
        logger.error(f"Error getting ai_providers.__file__: {file_err}")


    logger.info("Importing notifier...")
    import notifier
    logger.info("Imported notifier.")
    try:
        logger.info(f"Imported notifier from: {notifier.__file__}")
    except Exception as file_err:
        logger.error(f"Error getting notifier.__file__: {file_err}")


except ImportError as e:
    logger.critical(f"Failed to import core module: {e}", exc_info=True)
    sys.exit(f"Core module import failed: {e}")
except Exception as e:
    logger.critical(f"Unexpected error during imports: {e}", exc_info=True)
    sys.exit("Unexpected error during imports.")


logger.info("Creating Flask app instance...")
try:
    app = Flask(__name__)
    logger.info("Flask app instance created.")
except Exception as e:
    logger.critical(f"Failed to create Flask app instance: {e}", exc_info=True)
    sys.exit("Flask app creation failed.")


logger.info("Getting DB path from config_manager...")
try:
    if hasattr(config_manager, 'get_db_path') and callable(config_manager.get_db_path):
        DB_PATH = config_manager.get_db_path()
        logger.info(f"Database path set to: {DB_PATH}")
    else:
        logger.critical("config_manager.get_db_path function not found!")
        try: logger.error(f"config_manager was imported from: {config_manager.__file__}")
        except: pass
        raise AttributeError("module 'config_manager' has no attribute 'get_db_path'")
except Exception as e:
    logger.critical(f"Failed to get DB path from config_manager: {e}", exc_info=True)
    DB_PATH = "/data/fallback_obsengine_data.db"
    logger.warning(f"Using fallback DB path: {DB_PATH}")

def get_initial_db_defaults():
     logger.debug("Getting initial DB defaults...")
     enable_ai_default = 'true'
     ai_provider_default = 'gemini'
     if hasattr(config_manager, 'DEFAULT_ENABLE_AI_ANALYSIS'):
         enable_ai_default = str(config_manager.DEFAULT_ENABLE_AI_ANALYSIS).lower()
     else:
         logger.warning("config_manager.DEFAULT_ENABLE_AI_ANALYSIS not found!")
         try: logger.warning(f"config_manager imported from: {config_manager.__file__}")
         except: pass
     if hasattr(config_manager, 'DEFAULT_AI_PROVIDER'):
         ai_provider_default = config_manager.DEFAULT_AI_PROVIDER
     else:
         logger.warning("config_manager.DEFAULT_AI_PROVIDER not found!")
         try: logger.warning(f"config_manager imported from: {config_manager.__file__}")
         except: pass

     defaults = {
         'enable_ai_analysis': enable_ai_default,
         'ai_provider': ai_provider_default,
     }
     logger.debug(f"Initial DB defaults: {defaults}")
     return defaults

logger.info("Initializing database via db_manager.init_db...")
try:
    if not db_manager.init_db(DB_PATH, get_initial_db_defaults()):
        logger.critical("db_manager.init_db returned False! Check permissions, DB path, and DB logs.")
    else:
        logger.info("Database initialized successfully via db_manager.init_db.")
except Exception as e:
    logger.critical(f"Exception during db_manager.init_db call: {e}", exc_info=True)

def periodic_stat_update_thread():
    try:
        interval = 300
        if hasattr(config_manager, 'get_stats_update_interval') and callable(config_manager.get_stats_update_interval):
            interval = config_manager.get_stats_update_interval()
            logger.info(f"Starting periodic stats update thread. Interval: {interval} seconds.")
        else:
             logger.error("config_manager.get_stats_update_interval function not found! Using fallback interval 300s.")
             try: logger.error(f"config_manager imported from: {config_manager.__file__}")
             except: pass


        while True:
            time.sleep(interval)
            logger.debug("Triggering periodic stats update...")
            try:
                db_manager.update_daily_stats(DB_PATH)
            except Exception as update_err:
                 logger.error(f"Error during db_manager.update_daily_stats: {update_err}", exc_info=True)
    except Exception as thread_init_err:
         logger.error(f"Error initializing periodic_stat_update_thread: {thread_init_err}", exc_info=True)

logger.info("Attempting to start background stats thread...")
try:
    stats_thread = threading.Thread(target=periodic_stat_update_thread, daemon=True)
    stats_thread.start()
    logger.info("Background stats thread started.")
except Exception as e:
    logger.error(f"Failed to start stats thread: {e}", exc_info=True)


@app.route('/healthz')
def healthz():
    logger.debug("Health check endpoint called.")
    return "OK", 200

@app.route('/collect', methods=['POST'])
def collect_data():
    received_time = datetime.now(timezone.utc)
    logger.info("--- /collect endpoint hit ---")

    if not request.is_json:
        logger.error("Received non-JSON request to /collect")
        return jsonify({"error": "Request must be JSON"}), 400

    try:
        data = request.get_json()
        logger.debug(f"Received raw data (keys): {list(data.keys())}")
    except Exception as e:
        logger.error(f"Error getting or parsing JSON data: {e}", exc_info=True)
        return jsonify({"error": "Failed to parse JSON data"}), 400

    required_fields = ["pod_key", "k8s_context", "logs", "initial_reasons", "collection_timestamp"]
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        logger.error(f"Validation failed: {error_msg}")
        return jsonify({"error": error_msg}), 400

    pod_key = data.get("pod_key")
    k8s_context = data.get("k8s_context")
    logs = data.get("logs", [])
    initial_reasons = data.get("initial_reasons", [])
    collection_timestamp_str = data.get("collection_timestamp")

    logger.info(f"Processing data for pod: {pod_key}")
    logger.debug(f"Initial Reasons: {initial_reasons}")
    logger.debug(f"Number of Logs Received: {len(logs)}")
    logger.debug(f"Collection Timestamp: {collection_timestamp_str}")

    try:
        logger.debug(f"[{pod_key}] Loading configurations...")
        ai_config = config_manager.get_ai_config()
        alert_config = config_manager.get_alert_config()
        db_path_local = config_manager.get_db_path()
        logger.debug(f"[{pod_key}] Configurations loaded.")

        logger.debug(f"[{pod_key}] Performing analysis...")
        analysis_result, final_prompt, raw_response_text = ai_providers.perform_analysis(
            logs, k8s_context, "; ".join(initial_reasons), ai_config, ai_config.get('prompt_template')
        )
        logger.debug(f"[{pod_key}] Analysis complete.")

        severity = analysis_result.get("severity", "UNKNOWN").upper()
        summary = analysis_result.get("summary", "N/A")

        # --- FIX: Ensure root_cause and steps are strings ---
        root_cause_raw = analysis_result.get("root_cause", "N/A")
        steps_raw = analysis_result.get("troubleshooting_steps", "N/A")

        root_cause = "\n".join(root_cause_raw) if isinstance(root_cause_raw, list) else str(root_cause_raw)
        steps = "\n".join(steps_raw) if isinstance(steps_raw, list) else str(steps_raw)
        # ----------------------------------------------------

        logger.info(f"[{pod_key}] Analysis result: Severity={severity}")
        logger.debug(f"[{pod_key}] Summary: {summary}")
        # Log the processed string versions
        logger.debug(f"[{pod_key}] Root Cause (processed): {root_cause}")
        logger.debug(f"[{pod_key}] Steps (processed): {steps}")


        logger.debug(f"[{pod_key}] Recording incident...")
        sample_logs_str = "\n".join([f"- {log.get('message', '')[:150]}" for log in logs[:5]]) if logs else "-"
        db_manager.record_incident(
            db_path_local, pod_key, severity, summary, "; ".join(initial_reasons),
            k8s_context, sample_logs_str, alert_config.get('alert_severity_levels', []),
            final_prompt, raw_response_text,
            root_cause, # Pass the guaranteed string version
            steps       # Pass the guaranteed string version
        )
        logger.debug(f"[{pod_key}] Incident recorded.")

        alert_levels = alert_config.get('alert_severity_levels', [])
        cooldown_minutes = alert_config.get('alert_cooldown_minutes', 30)

        logger.debug(f"[{pod_key}] Checking alert conditions (Severity: {severity}, Threshold: {alert_levels})...")
        if severity in alert_levels:
            logger.debug(f"[{pod_key}] Checking cooldown...")
            if not db_manager.is_pod_in_cooldown(db_path_local, pod_key):
                logger.info(f"[{pod_key}] Severity meets threshold and not in cooldown. Processing alert.")
                if alert_config.get('enable_telegram_alerts'):
                    bot_token = alert_config.get('telegram_bot_token')
                    chat_id = alert_config.get('telegram_chat_id')
                    if bot_token and chat_id:
                        logger.debug(f"[{pod_key}] Sending Telegram alert...")
                        alert_time_hcm = received_time.astimezone(notifier.HCM_TZ)
                        time_format = '%Y-%m-%d %H:%M:%S %Z'
                        alert_data = {
                            'pod_key': pod_key, 'severity': severity, 'summary': summary,
                            'root_cause': root_cause, # Use processed string
                            'troubleshooting_steps': steps, # Use processed string
                            'initial_reasons': "; ".join(initial_reasons),
                            'alert_time': alert_time_hcm.strftime(time_format),
                            'sample_logs': sample_logs_str
                        }
                        alert_sent = notifier.send_telegram_alert(
                            bot_token, chat_id, alert_data, ai_config.get('enable_ai_analysis')
                        )
                        if alert_sent:
                            logger.debug(f"[{pod_key}] Alert sent. Setting cooldown...")
                            db_manager.set_pod_cooldown(db_path_local, pod_key, cooldown_minutes)
                        else:
                            logger.warning(f"[{pod_key}] Telegram alert sending failed, cooldown NOT set.")
                    else:
                        logger.warning(f"[{pod_key}] Telegram alerts enabled but token/chat_id missing. Setting cooldown anyway.")
                        db_manager.set_pod_cooldown(db_path_local, pod_key, cooldown_minutes)
                else:
                    logger.info(f"[{pod_key}] Telegram alerts disabled. Setting cooldown.")
                    db_manager.set_pod_cooldown(db_path_local, pod_key, cooldown_minutes)
            else:
                logger.info(f"[{pod_key}] Pod is in cooldown. Alert processing skipped.")
        else:
             logger.info(f"[{pod_key}] Severity does not meet alert threshold. No alert needed.")

        logger.info(f"--- Successfully processed data for {pod_key} ---")
        return jsonify({"message": f"Data processed successfully for {pod_key}"}), 200

    except AttributeError as ae:
         logger.error(f"--- AttributeError processing data for pod {pod_key}: {ae} ---", exc_info=True)
         if 'config_manager' in str(ae):
             try:
                 logger.error(f"Attributes currently in config_manager: {dir(config_manager)}")
             except Exception as dir_err:
                 logger.error(f"Could not get dir(config_manager) on error: {dir_err}")
         return jsonify({"error": f"Internal server error (AttributeError) processing data for {pod_key}"}), 500
    except Exception as e:
        logger.error(f"--- Error processing data for pod {pod_key}: {e} ---", exc_info=True)
        return jsonify({"error": f"Internal server error processing data for {pod_key}"}), 500


if __name__ == '__main__':
    flask_port = int(os.environ.get("FLASK_PORT", 8080))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    log_level_name = logging.getLevelName(logging.getLogger().getEffectiveLevel())
    logger.info(f"Starting Flask development server on 0.0.0.0:{flask_port} | Debug: {debug_mode} | Log Level: {log_level_name}")
    app.run(host='0.0.0.0', port=flask_port, debug=debug_mode)

logger.info("--- ObsEngine app.py finished parsing (module level) ---")
