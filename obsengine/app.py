# ai-agent1/obsengine/app.py
import os
import logging
import sys
import json
import threading
import time
import math
import sqlite3
from flask import Flask, request, jsonify, abort # Giữ lại import Flask cơ bản
from datetime import datetime, timezone, timedelta
# Bỏ các import không cần thiết khác

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
PARENT_DIR = os.path.dirname(APP_DIR)
if PARENT_DIR not in sys.path:
     sys.path.insert(0, PARENT_DIR)

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=log_level,
                    format='%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s',
                    stream=sys.stdout)
logger = logging.getLogger(__name__)

logger.info(f"--- ObsEngine app.py starting execution (Log Level: {log_level}) ---")

try:
    logger.info("Importing obsengine_config...")
    import obsengine_config
    logger.info("Imported obsengine_config.")
    logger.info("Importing db_manager...")
    import db_manager
    logger.info("Imported db_manager.")
    logger.info("Importing ai_providers...")
    import ai_providers
    logger.info("Imported ai_providers.")
    logger.info("Importing notifier...")
    import notifier
    logger.info("Imported notifier.")
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

logger.info("Getting DB path from obsengine_config...")
try:
    DB_PATH = obsengine_config.get_db_path()
    logger.info(f"Database path set to: {DB_PATH}")
except Exception as e:
    logger.critical(f"Failed to get DB path from obsengine_config: {e}", exc_info=True)
    DB_PATH = "/data/fallback_obsengine_data.db"
    logger.warning(f"Using fallback DB path: {DB_PATH}")

def get_initial_global_db_defaults():
     logger.debug("Getting initial GLOBAL DB defaults...")
     defaults = {
         'enable_telegram_alerts': str(getattr(obsengine_config, 'DEFAULT_ENABLE_TELEGRAM_ALERTS', False)).lower(),
         'telegram_chat_id': getattr(obsengine_config, 'DEFAULT_TELEGRAM_CHAT_ID', ''),
         'enable_ai_analysis': str(getattr(obsengine_config, 'DEFAULT_ENABLE_AI_ANALYSIS', False)).lower(),
         'ai_provider': getattr(obsengine_config, 'DEFAULT_AI_PROVIDER', 'gemini').lower(),
         'ai_model_identifier': getattr(obsengine_config, 'DEFAULT_AI_MODEL_IDENTIFIER', 'gemini-1.5-flash'),
         'local_gemini_endpoint': getattr(obsengine_config, 'DEFAULT_LOCAL_GEMINI_ENDPOINT', ''),
         'prompt_template': getattr(obsengine_config, 'DEFAULT_PROMPT_TEMPLATE', "Default Prompt Missing"),
         'alert_severity_levels': getattr(obsengine_config, 'DEFAULT_ALERT_SEVERITY_LEVELS_STR', "WARNING,ERROR,CRITICAL"),
         'alert_cooldown_minutes': str(getattr(obsengine_config, 'DEFAULT_ALERT_COOLDOWN_MINUTES', 30)),
         'default_monitored_namespaces': json.dumps(getattr(obsengine_config, 'DEFAULT_MONITORED_NAMESPACES_STR', "kube-system,default").split(','))
     }
     logger.debug(f"Initial GLOBAL DB defaults generated: {defaults}")
     return defaults

logger.info("Initializing database via db_manager.init_db...")
try:
    if not db_manager.init_db(DB_PATH, get_initial_global_db_defaults()):
        logger.critical("db_manager.init_db returned False! DB operations might fail.")
    else:
        logger.info("Database initialized successfully via db_manager.init_db.")
except Exception as e:
    logger.critical(f"Exception during db_manager.init_db call: {e}", exc_info=True)

def periodic_background_tasks_thread():
    try:
        interval = int(getattr(obsengine_config, 'DEFAULT_STATS_UPDATE_INTERVAL_SECONDS', 300))
        agent_cleanup_interval = 3600
        agent_timeout = 86400
        logger.info(f"Starting periodic background tasks thread. Stats Interval: {interval}s, Agent Cleanup Interval: {agent_cleanup_interval}s")
        last_agent_cleanup_time = time.time()

        while True:
            time.sleep(interval)
            logger.debug("Triggering periodic stats update...")
            try:
                db_manager.update_daily_stats(DB_PATH)
            except Exception as update_err:
                 logger.error(f"Error during db_manager.update_daily_stats: {update_err}", exc_info=True)

            current_time = time.time()
            if current_time - last_agent_cleanup_time >= agent_cleanup_interval:
                logger.debug("Triggering periodic agent cleanup...")
                try:
                    deleted_count = db_manager.cleanup_inactive_agents(DB_PATH, agent_timeout)
                    logger.debug(f"Agent cleanup finished. Removed {deleted_count} inactive agents.")
                    last_agent_cleanup_time = current_time
                except Exception as cleanup_err:
                    logger.error(f"Error during db_manager.cleanup_inactive_agents: {cleanup_err}", exc_info=True)

    except Exception as thread_init_err:
         logger.error(f"Error initializing periodic_background_tasks_thread: {thread_init_err}", exc_info=True)

logger.info("Attempting to start background tasks thread...")
try:
    background_thread = threading.Thread(target=periodic_background_tasks_thread, daemon=True)
    background_thread.start()
    logger.info("Background tasks thread started.")
except Exception as e:
    logger.error(f"Failed to start background tasks thread: {e}", exc_info=True)

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

    required_fields = [
        "environment_name", "agent_id", "environment_type", "resource_type",
        "resource_name", "collection_timestamp", "initial_reasons",
        "environment_context", "logs", "environment_info", "agent_version"
    ]
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        logger.error(f"Validation failed: {error_msg}")
        return jsonify({"error": error_msg}), 400

    environment_name = data.get("environment_name")
    agent_id = data.get("agent_id")
    environment_type = data.get("environment_type")
    resource_type = data.get("resource_type")
    resource_name = data.get("resource_name")
    collection_timestamp = data.get("collection_timestamp") # Giữ nguyên
    initial_reasons = data.get("initial_reasons", [])
    environment_context = data.get("environment_context") # Thay cho k8s_context
    logs = data.get("logs", [])
    environment_info = data.get("environment_info") # Thay cho cluster_info
    agent_version = data.get("agent_version")

    if not agent_id:
         logger.error("Validation failed: 'agent_id' is missing or empty.")
         return jsonify({"error": "Missing required field: agent_id"}), 400
    if not environment_name:
         logger.error("Validation failed: 'environment_name' is missing or empty.")
         return jsonify({"error": "Missing required field: environment_name"}), 400
    if not environment_type:
         logger.error("Validation failed: 'environment_type' is missing or empty.")
         return jsonify({"error": "Missing required field: environment_type"}), 400
    if not resource_name:
         logger.error("Validation failed: 'resource_name' is missing or empty.")
         return jsonify({"error": "Missing required field: resource_name"}), 400

    resource_identifier = f"{environment_name}/{resource_name}"
    logger.info(f"Processing data for resource: {resource_identifier} (Type: {resource_type}, Env Type: {environment_type}) from Agent: {agent_id} (Version: {agent_version})")

    try:
        db_manager.update_agent_heartbeat(
            DB_PATH, agent_id, environment_name,
            received_time.isoformat(),
            agent_version=agent_version,
            environment_type=environment_type,
            environment_info=environment_info
        )
    except Exception as heartbeat_err:
        logger.error(f"Failed to update heartbeat/info for agent {agent_id}: {heartbeat_err}", exc_info=True)

    try:
        current_ai_config = obsengine_config.get_ai_config()
        current_alert_config = obsengine_config.get_alert_config()
        db_path_local = obsengine_config.get_db_path()

        logger.debug(f"[{resource_identifier}] Performing analysis (Agent: {agent_id})...")
        analysis_result, final_prompt, raw_response_text = ai_providers.perform_analysis(
            logs=logs,
            environment_context=environment_context,
            initial_reasons_list=initial_reasons,
            config=current_ai_config,
            prompt_template=current_ai_config.get('prompt_template'),
            environment_name=environment_name,
            resource_name=resource_name,
            environment_type=environment_type,
            resource_type=resource_type
        )
        logger.debug(f"[{resource_identifier}] Analysis complete.")

        severity = analysis_result.get("severity", "UNKNOWN").upper()
        summary = analysis_result.get("summary", "N/A")
        root_cause_raw = analysis_result.get("root_cause", "N/A")
        steps_raw = analysis_result.get("troubleshooting_steps", "N/A")
        root_cause = "\n".join(root_cause_raw) if isinstance(root_cause_raw, list) else str(root_cause_raw)
        steps = "\n".join(steps_raw) if isinstance(steps_raw, list) else str(steps_raw)
        logger.info(f"[{resource_identifier}] Analysis result: Severity={severity}")

        logger.debug(f"[{resource_identifier}] Recording incident...")
        sample_logs_str = "\n".join([f"- {log.get('message', '')[:150]}" for log in logs[:5]]) if logs else "-"
        initial_reasons_str = "; ".join(initial_reasons)
        db_manager.record_incident(
            db_path=db_path_local,
            environment_name=environment_name,
            environment_type=environment_type,
            resource_type=resource_type,
            resource_name=resource_name,
            severity=severity,
            summary=summary,
            initial_reasons=initial_reasons_str,
            environment_context=environment_context,
            sample_logs=sample_logs_str,
            input_prompt=final_prompt,
            raw_ai_response=raw_response_text,
            root_cause=root_cause,
            troubleshooting_steps=steps
        )
        logger.debug(f"[{resource_identifier}] Incident recorded.")

        alert_levels = current_alert_config.get('alert_severity_levels', [])
        cooldown_minutes = current_alert_config.get('alert_cooldown_minutes', 30)
        logger.debug(f"[{resource_identifier}] Checking alert conditions (Severity: {severity}, Levels: {alert_levels})...")

        if severity in alert_levels:
            logger.debug(f"[{resource_identifier}] Checking cooldown...")
            if not db_manager.is_resource_in_cooldown(db_path_local, resource_identifier):
                logger.info(f"[{resource_identifier}] Not in cooldown. Processing alert.")
                if current_alert_config.get('enable_telegram_alerts'):
                    bot_token = current_alert_config.get('telegram_bot_token')
                    chat_id = current_alert_config.get('telegram_chat_id')
                    if bot_token and chat_id:
                        logger.debug(f"[{resource_identifier}] Sending Telegram alert...")
                        alert_time_hcm = received_time.astimezone(getattr(notifier, 'HCM_TZ', timezone.utc))
                        time_format = '%Y-%m-%d %H:%M:%S %Z'
                        alert_data = {
                            'resource_identifier': resource_identifier,
                            'environment_name': environment_name,
                            'resource_name': resource_name,
                            'environment_type': environment_type,
                            'resource_type': resource_type,
                            'agent_id': agent_id,
                            'severity': severity,
                            'summary': summary,
                            'root_cause': root_cause,
                            'troubleshooting_steps': steps,
                            'initial_reasons': initial_reasons_str,
                            'alert_time': alert_time_hcm.strftime(time_format),
                            'sample_logs': sample_logs_str
                        }
                        alert_sent = notifier.send_telegram_alert(
                            bot_token, chat_id, alert_data, current_ai_config.get('enable_ai_analysis')
                        )
                        if alert_sent:
                            logger.debug(f"[{resource_identifier}] Alert sent. Setting cooldown...")
                            db_manager.set_resource_cooldown(db_path_local, resource_identifier, cooldown_minutes)
                        else: logger.warning(f"[{resource_identifier}] Telegram alert sending failed, cooldown NOT set.")
                    else:
                        logger.warning(f"[{resource_identifier}] Telegram alerts enabled but token/chat_id missing. Setting cooldown anyway.")
                        db_manager.set_resource_cooldown(db_path_local, resource_identifier, cooldown_minutes)
                else:
                    logger.info(f"[{resource_identifier}] Telegram alerts disabled. Setting cooldown.")
                    db_manager.set_resource_cooldown(db_path_local, resource_identifier, cooldown_minutes)
            else: logger.info(f"[{resource_identifier}] Resource is in cooldown. Alert processing skipped.")
        else: logger.info(f"[{resource_identifier}] Severity does not meet alert threshold. No alert needed.")

        logger.info(f"--- Successfully processed data for {resource_identifier} from Agent {agent_id} ---")
        return jsonify({"message": f"Data processed successfully for {resource_identifier}"}), 200

    except AttributeError as ae:
        logger.error(f"--- AttributeError processing data for resource {resource_identifier} from Agent {agent_id}: {ae} ---", exc_info=True)
        return jsonify({"error": f"Internal server error (AttributeError) processing data for {resource_identifier}"}), 500
    except Exception as e:
        logger.error(f"--- Error processing data for resource {resource_identifier} from Agent {agent_id}: {e} ---", exc_info=True)
        return jsonify({"error": f"Internal server error processing data for {resource_identifier}"}), 500

# --- API Endpoints cho Portal (KHÔNG cần @login_required) ---

@app.route('/api/agents/status', methods=['GET'])
# @login_required # Bỏ decorator này
def get_agent_status():
    logger.info("--- /api/agents/status endpoint hit ---")
    try:
        agent_timeout = 300
        active_agents = db_manager.get_active_agents(DB_PATH, agent_timeout)
        logger.info(f"Returning status for {len(active_agents)} active agents.")
        return jsonify({"active_agents": active_agents}), 200
    except Exception as e:
        logger.error(f"Error getting agent status: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve agent status"}), 500

@app.route('/api/incidents', methods=['GET'])
# @login_required # Bỏ decorator này
def get_incidents_api():
    logger.info("--- /api/incidents endpoint hit ---")
    try:
        resource_filter = request.args.get('resource', default="", type=str).strip()
        env_filter = request.args.get('environment', default="", type=str).strip()
        env_type_filter = request.args.get('env_type', default="", type=str).strip()
        severity_filter = request.args.get('severity', default="", type=str).upper().strip()
        start_date_str = request.args.get('start_date', default=None, type=str)
        end_date_str = request.args.get('end_date', default=None, type=str)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('limit', 20, type=int)
        offset = (page - 1) * per_page

        conn = db_manager._get_db_connection(DB_PATH)
        if conn is None: return jsonify({"error": "Database connection failed."}), 500
        incidents = []; total_count = 0
        try:
            cursor = conn.cursor();
            base_query = "FROM incidents WHERE 1=1"; params = []
            if resource_filter: base_query += " AND resource_name LIKE ?"; params.append(f"%{resource_filter}%")
            if env_filter: base_query += " AND environment_name = ?"; params.append(env_filter)
            if env_type_filter: base_query += " AND environment_type = ?"; params.append(env_type_filter)
            if severity_filter: base_query += " AND severity = ?"; params.append(severity_filter)
            if start_date_str:
                try: start_dt_utc = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')); base_query += " AND timestamp >= ?"; params.append(start_dt_utc.isoformat())
                except ValueError: logger.warning(f"Invalid start_date format: {start_date_str}. Ignoring filter.")
            if end_date_str:
                try: end_dt_utc = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')); base_query += " AND timestamp <= ?"; params.append(end_dt_utc.isoformat())
                except ValueError: logger.warning(f"Invalid end_date format: {end_date_str}. Ignoring filter.")

            count_query = f"SELECT COUNT(*) as count {base_query}"; cursor.execute(count_query, tuple(params)); count_result = cursor.fetchone(); total_count = count_result['count'] if count_result else 0
            data_query = f"""
                SELECT id, timestamp, environment_name, environment_type, resource_type, resource_name,
                       severity, summary, initial_reasons, environment_context, sample_logs,
                       input_prompt, raw_ai_response, root_cause, troubleshooting_steps
                {base_query} ORDER BY timestamp DESC LIMIT ? OFFSET ?
            """;
            params_data = params + [per_page, offset]; cursor.execute(data_query, tuple(params_data)); rows = cursor.fetchall(); incidents = [dict(row) for row in rows]
        except sqlite3.Error as db_err: logger.error(f"Database error fetching incidents: {db_err}", exc_info=True); return jsonify({"error": f"Database error: {db_err}"}), 500
        finally:
            if conn: conn.close()
        total_pages = math.ceil(total_count / per_page) if per_page > 0 else 0; pagination = {"page": page, "per_page": per_page, "total_items": total_count, "total_pages": total_pages}; logger.info(f"Returning {len(incidents)} incidents (Page {page}/{total_pages})"); return jsonify({"incidents": incidents, "pagination": pagination})
    except Exception as e: logger.error(f"Error in /api/incidents: {e}", exc_info=True); return jsonify({"error": "Failed to retrieve incidents"}), 500

@app.route('/api/stats', methods=['GET'])
# @login_required # Bỏ decorator này
def get_stats_api():
    logger.info("--- /api/stats endpoint hit ---")
    try:
        days = request.args.get('days', 1, type=int);
        env_filter = request.args.get('environment', default=None, type=str)
        env_type_filter = request.args.get('env_type', default=None, type=str)
        if days not in [1, 7, 30]: days = 1
        logger.debug(f"Calculating stats for last {days} days (Env: {env_filter or 'All'}, Type: {env_type_filter or 'All'}).")
        end_date_utc = datetime.now(timezone.utc); start_date_utc = end_date_utc - timedelta(days=days); chart_days = max(days, 7); chart_start_date_utc = end_date_utc - timedelta(days=chart_days)
        conn = db_manager._get_db_connection(DB_PATH);
        if conn is None: return jsonify({"error": "Database connection failed."}), 500
        stats = {};
        try:
            cursor = conn.cursor();
            daily_where_clause = "WHERE date >= ? AND date <= ?"
            daily_params = [chart_start_date_utc.strftime('%Y-%m-%d'), end_date_utc.strftime('%Y-%m-%d')]
            cursor.execute(f'SELECT date, model_calls, telegram_alerts, incident_count FROM daily_stats {daily_where_clause} ORDER BY date ASC', tuple(daily_params)); daily_stats_rows = cursor.fetchall(); stats['daily_stats_for_chart'] = [dict(row) for row in daily_stats_rows];

            incident_where_clause = "WHERE timestamp >= ? AND timestamp <= ?"
            incident_params = [start_date_utc.isoformat(), end_date_utc.isoformat()]
            if env_filter: incident_where_clause += " AND environment_name = ?"; incident_params.append(env_filter)
            if env_type_filter: incident_where_clause += " AND environment_type = ?"; incident_params.append(env_type_filter)

            # Lấy tổng incident từ bảng incidents đã lọc
            cursor.execute(f'SELECT COUNT(*) as count FROM incidents {incident_where_clause}', tuple(incident_params));
            total_incidents_filtered = cursor.fetchone()['count'] or 0

            cursor.execute('SELECT SUM(model_calls) as total_model_calls, SUM(telegram_alerts) as total_telegram_alerts FROM daily_stats WHERE date >= ? AND date <= ?', (start_date_utc.strftime('%Y-%m-%d'), end_date_utc.strftime('%Y-%m-%d'))); totals_calls_alerts = cursor.fetchone()
            stats['totals'] = {
                "model_calls": totals_calls_alerts['total_model_calls'] if totals_calls_alerts else 0,
                "telegram_alerts": totals_calls_alerts['total_telegram_alerts'] if totals_calls_alerts else 0,
                "incidents": total_incidents_filtered, # Sử dụng tổng đã lọc
            }

            today_start_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0); today_end_utc = today_start_utc + timedelta(days=1) - timedelta(microseconds=1);
            today_where_clause = "WHERE timestamp >= ? AND timestamp <= ?"
            today_params = [today_start_utc.isoformat(), today_end_utc.isoformat()]
            if env_filter: today_where_clause += " AND environment_name = ?"; today_params.append(env_filter)
            if env_type_filter: today_where_clause += " AND environment_type = ?"; today_params.append(env_type_filter)
            cursor.execute(f'SELECT severity, COUNT(*) as count FROM incidents {today_where_clause} GROUP BY severity', tuple(today_params)); severity_rows_today = cursor.fetchall(); stats['severity_distribution_today'] = {row['severity']: row['count'] for row in severity_rows_today if row['severity']};

            cursor.execute(f'SELECT resource_name, COUNT(*) as count FROM incidents {incident_where_clause} GROUP BY resource_name ORDER BY count DESC LIMIT 5', tuple(incident_params)); top_resources_rows = cursor.fetchall(); stats['top_problematic_resources'] = {row['resource_name']: row['count'] for row in top_resources_rows};

            cursor.execute(f'SELECT environment_name, COUNT(*) as count FROM incidents {incident_where_clause} GROUP BY environment_name ORDER BY count DESC', tuple(incident_params)); env_rows = cursor.fetchall(); stats['environment_distribution'] = {row['environment_name']: row['count'] for row in env_rows if row['environment_name']};
        except sqlite3.Error as db_err: logger.error(f"Database error fetching stats: {db_err}", exc_info=True); return jsonify({"error": f"Database error: {db_err}"}), 500
        finally:
            if conn: conn.close()
        logger.info(f"Returning stats data for last {days} days."); return jsonify(stats)
    except Exception as e: logger.error(f"Error in /api/stats: {e}", exc_info=True); return jsonify({"error": "Failed to retrieve stats"}), 500

@app.route('/api/config/all', methods=['GET'])
# @login_required # Bỏ decorator này
def get_all_config_api():
    logger.info("--- GET /api/config/all endpoint hit ---")
    try:
        current_config = db_manager.load_all_global_config(DB_PATH)
        current_config['ai_api_key'] = obsengine_config._get_env_var("GEMINI_API_KEY", "")
        current_config['telegram_bot_token'] = obsengine_config._get_env_var("TELEGRAM_BOT_TOKEN", "")
        sensitive_keys_to_exclude = ['ai_api_key', 'telegram_bot_token']
        safe_config = {k: v for k, v in current_config.items() if k not in sensitive_keys_to_exclude}
        alert_levels_str = safe_config.get('alert_severity_levels')
        if alert_levels_str:
            safe_config['alert_severity_levels_str'] = alert_levels_str
            try: safe_config['alert_severity_levels'] = [lvl.strip().upper() for lvl in alert_levels_str.split(',') if lvl.strip()]
            except Exception: safe_config['alert_severity_levels'] = []
        else: safe_config['alert_severity_levels'] = []

        ns_json_string = safe_config.get('default_monitored_namespaces')
        if ns_json_string:
             try:
                 safe_config['default_monitored_namespaces'] = json.loads(ns_json_string)
                 if not isinstance(safe_config['default_monitored_namespaces'], list):
                      safe_config['default_monitored_namespaces'] = []
             except json.JSONDecodeError:
                 logger.warning(f"Could not decode global default_monitored_namespaces JSON: {ns_json_string}")
                 safe_config['default_monitored_namespaces'] = []
        else:
             safe_config['default_monitored_namespaces'] = []

        logger.info("Returning non-sensitive GLOBAL configuration.")
        return jsonify(safe_config), 200
    except Exception as e:
        logger.error(f"Error getting all global config: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve global configuration"}), 500

@app.route('/api/namespaces', methods=['GET'])
# @login_required # Bỏ decorator này
def get_available_namespaces_api():
    logger.info("--- /api/namespaces endpoint hit (used for K8s agent config) ---");
    logger.info("Fetching available namespaces from DB cache.")
    conn = db_manager._get_db_connection(DB_PATH)
    if conn is None:
        return jsonify({"error": "Database connection failed."}), 500
    namespaces = []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM available_namespaces ORDER BY name ASC")
        rows = cursor.fetchall()
        namespaces = [row['name'] for row in rows]
        logger.info(f"Fetched {len(namespaces)} namespaces from DB cache.")
        return jsonify(namespaces), 200
    except sqlite3.Error as db_err:
        logger.error(f"Database error fetching available namespaces from cache: {db_err}", exc_info=True)
        return jsonify({"error": f"Database cache error: {db_err}"}), 500
    except Exception as e:
        logger.error(f"Unexpected error fetching available namespaces from cache: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve available namespaces"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/config/ai', methods=['POST'])
# @login_required # Bỏ decorator này
# @admin_required # Quyền admin sẽ được kiểm tra ở Portal
def save_global_ai_config_api():
    # Lưu ý: API này nên có cơ chế bảo mật riêng nếu không dùng login session
    # Ví dụ: API key, token bí mật... Tạm thời bỏ qua để đơn giản
    logger.info("--- POST /api/config/ai (Global) endpoint hit ---")
    # if current_user.role != 'admin': return jsonify({"error": "Admin privileges required"}), 403 # Bỏ check này
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json(); logger.debug(f"Received global AI config data: {data}")
    config_to_save = {}; validation_errors = []; valid_providers = ['gemini', 'local', 'openai', 'groq', 'deepseek', 'none']
    enable_ai = data.get('enable_ai_analysis');
    if not isinstance(enable_ai, bool): validation_errors.append("'enable_ai_analysis' must be a boolean")
    else: config_to_save['enable_ai_analysis'] = str(enable_ai).lower()
    provider = data.get('ai_provider', obsengine_config.DEFAULT_AI_PROVIDER).lower();
    if provider not in valid_providers: validation_errors.append(f"Invalid AI provider: {provider}")
    else: config_to_save['ai_provider'] = provider
    model_id = data.get('ai_model_identifier', ''); config_to_save['ai_model_identifier'] = model_id.strip();
    if validation_errors: logger.error(f"Validation failed for global AI config: {validation_errors}"); return jsonify({"error": "Validation failed", "details": validation_errors}), 400
    success, message = db_manager.save_global_config(DB_PATH, config_to_save);
    if success: obsengine_config._last_config_refresh_time = 0; return jsonify({"message": message}), 200
    else: return jsonify({"error": message}), 500

@app.route('/api/config/telegram', methods=['POST'])
# @login_required # Bỏ decorator này
# @admin_required # Quyền admin sẽ được kiểm tra ở Portal
def save_global_telegram_config_api():
    logger.info("--- POST /api/config/telegram (Global) endpoint hit ---")
    # if current_user.role != 'admin': return jsonify({"error": "Admin privileges required"}), 403 # Bỏ check này
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json(); logger.debug(f"Received global Telegram config data: {data}")
    config_to_save = {}; validation_errors = []
    enable_alerts = data.get('enable_telegram_alerts');
    if not isinstance(enable_alerts, bool): validation_errors.append("'enable_telegram_alerts' must be a boolean")
    else: config_to_save['enable_telegram_alerts'] = str(enable_alerts).lower()
    chat_id = data.get('telegram_chat_id', '').strip();
    if not chat_id: validation_errors.append("Telegram Chat ID cannot be empty")
    else: config_to_save['telegram_chat_id'] = chat_id
    if validation_errors: logger.error(f"Validation failed for global Telegram config: {validation_errors}"); return jsonify({"error": "Validation failed", "details": validation_errors}), 400
    success, message = db_manager.save_global_config(DB_PATH, config_to_save);
    if success: obsengine_config._last_config_refresh_time = 0; return jsonify({"message": message}), 200
    else: return jsonify({"error": message}), 500

@app.route('/api/agents/<agent_id>/config', methods=['GET'])
# @login_required # Bỏ decorator này
def get_agent_config_api(agent_id):
    logger.info(f"--- GET /api/agents/{agent_id}/config endpoint hit ---")
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    try:
        global_config = db_manager.load_all_global_config(DB_PATH)
        agent_overrides = db_manager.load_agent_config(DB_PATH, agent_id)
        agent_info = db_manager.get_agent_info(DB_PATH, agent_id) # Lấy thông tin agent

        # Lấy default từ global config
        merged_config = {
            'scan_interval_seconds': int(global_config.get('default_scan_interval_seconds', 30)),
            'restart_count_threshold': int(global_config.get('default_restart_count_threshold', 5)),
            'loki_scan_min_level': global_config.get('default_loki_scan_min_level', 'INFO'),
            'monitored_namespaces': json.loads(global_config.get('default_monitored_namespaces', '[]')),
        }

        # Ghi đè bằng config của agent
        for key, value in agent_overrides.items():
             try:
                 if key in ['scan_interval_seconds', 'restart_count_threshold']:
                     merged_config[key] = int(value)
                 elif key == 'monitored_namespaces':
                     ns_list = json.loads(value)
                     if isinstance(ns_list, list): merged_config[key] = ns_list
                 elif key == 'loki_scan_min_level':
                     merged_config[key] = value.upper()
                 else: merged_config[key] = value # Giữ các config khác dạng string
             except (ValueError, TypeError, json.JSONDecodeError) as parse_err:
                  logger.warning(f"Error parsing agent config key '{key}' for agent {agent_id}: {parse_err}. Value: '{value}'")
                  merged_config[key] = value

        # Thêm thông tin agent vào kết quả trả về cho Agent
        merged_config['environment_type'] = agent_info.get('environment_type', 'unknown') if agent_info else 'unknown'
        merged_config['environment_info'] = agent_info.get('environment_info', {}) if agent_info else {}

        logger.info(f"Returning merged configuration for agent {agent_id}.")
        return jsonify(merged_config), 200
    except Exception as e:
        logger.error(f"Error getting config for agent {agent_id}: {e}", exc_info=True)
        return jsonify({"error": f"Failed to retrieve configuration for agent {agent_id}"}), 500

@app.route('/api/agents/<agent_id>/config/general', methods=['POST'])
# @login_required # Bỏ decorator này
# @admin_required # Quyền admin sẽ được kiểm tra ở Portal
def save_agent_general_config_api(agent_id):
    # API này cần cơ chế bảo mật riêng
    logger.info(f"--- POST /api/agents/{agent_id}/config/general endpoint hit ---")
    # if current_user.role != 'admin': return jsonify({"error": "Admin privileges required"}), 403 # Bỏ check
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json(); logger.debug(f"Received general config data for agent {agent_id}: {data}")
    config_to_save = {}; validation_errors = []; valid_log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
    try:
        if 'scan_interval_seconds' in data:
            scan_interval = int(data['scan_interval_seconds']);
            if scan_interval < 10: validation_errors.append("Scan interval must be >= 10")
            else: config_to_save['scan_interval_seconds'] = str(scan_interval)

        agent_info = db_manager.get_agent_info(DB_PATH, agent_id)
        if agent_info and agent_info.get('environment_type') == 'kubernetes':
            if 'restart_count_threshold' in data:
                restart_threshold = int(data['restart_count_threshold']);
                if restart_threshold < 1: validation_errors.append("Restart threshold must be >= 1")
                else: config_to_save['restart_count_threshold'] = str(restart_threshold)

        if 'loki_scan_min_level' in data:
            scan_level = data['loki_scan_min_level'].upper();
            if scan_level not in valid_log_levels: validation_errors.append(f"Invalid Loki scan level: {scan_level}")
            else: config_to_save['loki_scan_min_level'] = scan_level

    except (ValueError, TypeError) as e: logger.error(f"Validation error processing general config for agent {agent_id}: {e}"); return jsonify({"error": f"Invalid input data type: {e}"}), 400
    if validation_errors: logger.error(f"Validation failed for agent {agent_id} general config: {validation_errors}"); return jsonify({"error": "Validation failed", "details": validation_errors}), 400
    if not config_to_save: return jsonify({"message": "No general settings provided to save."}), 200
    success, message = db_manager.save_agent_config(DB_PATH, agent_id, config_to_save)
    if success: return jsonify({"message": message}), 200
    else: return jsonify({"error": message}), 500

@app.route('/api/agents/<agent_id>/config/namespaces', methods=['POST'])
# @login_required # Bỏ decorator này
# @admin_required # Quyền admin sẽ được kiểm tra ở Portal
def save_agent_namespaces_api(agent_id):
    # API này cần cơ chế bảo mật riêng
    # if current_user.role != 'admin': return jsonify({"error": "Admin privileges required"}), 403 # Bỏ check
    agent_info = db_manager.get_agent_info(DB_PATH, agent_id)
    if not agent_info or agent_info.get('environment_type') != 'kubernetes':
        return jsonify({"error": "Namespace configuration is only applicable to Kubernetes agents."}), 400

    logger.info(f"--- POST /api/agents/{agent_id}/config/namespaces endpoint hit ---")
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json(); logger.debug(f"Received monitored namespaces data for agent {agent_id}: {data}")
    namespaces = data.get('namespaces')
    if not isinstance(namespaces, list): return jsonify({"error": "'namespaces' must be a list"}), 400
    cleaned_namespaces = [str(ns).strip() for ns in namespaces if isinstance(ns, str) and str(ns).strip()]
    value_to_save = json.dumps(cleaned_namespaces)
    config_to_save = {'monitored_namespaces': value_to_save}
    success, message = db_manager.save_agent_config(DB_PATH, agent_id, config_to_save)
    if success: return jsonify({"message": message}), 200
    else: return jsonify({"error": message}), 500


# --- API lấy thông tin cluster (chỉ dùng bởi Portal) ---
# Endpoint này có thể cần @login_required nếu Portal yêu cầu
# @app.route('/api/cluster/info')
# @login_required
# def get_cluster_info_proxy():
#     logger.info("Forwarding request to ObsEngine: GET /api/cluster/info")
#     # Endpoint này không tồn tại trong ObsEngine, Portal nên lấy thông tin này từ /api/agents/status
#     # Hoặc tạo endpoint mới trong ObsEngine nếu cần tổng hợp thông tin cluster
#     return jsonify({"error": "Endpoint not implemented in ObsEngine"}), 501


if __name__ == '__main__':
    flask_port = int(os.environ.get("FLASK_PORT", 8080))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    log_level_name = logging.getLevelName(logging.getLogger().getEffectiveLevel())
    logger.info(f"Starting ObsEngine Flask server on 0.0.0.0:{flask_port} | Debug: {debug_mode} | Log Level: {log_level_name}")
    app.run(host='0.0.0.0', port=flask_port, debug=debug_mode, use_reloader=debug_mode)

logger.info("--- ObsEngine app.py finished parsing (module level) ---")

