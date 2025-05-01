import os
import logging
import sys
import json
import threading
import time
import math
import sqlite3
from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
PARENT_DIR = os.path.dirname(APP_DIR)
if PARENT_DIR not in sys.path:
     sys.path.insert(0, PARENT_DIR)

# Configure logging
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=log_level,
                    format='%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s',
                    stream=sys.stdout)
logger = logging.getLogger(__name__)

logger.info(f"--- ObsEngine app.py starting execution (Log Level: {log_level}) ---")
logger.info(f"Current sys.path after modification: {sys.path}")

# Import custom modules with logging
try:
    logger.info("Importing obsengine_config...")
    import obsengine_config
    logger.info("Imported obsengine_config.")
    try: logger.info(f"Imported obsengine_config from: {obsengine_config.__file__}")
    except: pass
    try: logger.info(f"Attributes in imported obsengine_config: {dir(obsengine_config)}")
    except: pass

    logger.info("Importing db_manager...")
    import db_manager
    logger.info("Imported db_manager.")
    try: logger.info(f"Imported db_manager from: {db_manager.__file__}")
    except: pass

    logger.info("Importing ai_providers...")
    import ai_providers
    logger.info("Imported ai_providers.")
    try: logger.info(f"Imported ai_providers from: {ai_providers.__file__}")
    except: pass

    logger.info("Importing notifier...")
    import notifier
    logger.info("Imported notifier.")
    try: logger.info(f"Imported notifier from: {notifier.__file__}")
    except: pass

except ImportError as e:
    logger.critical(f"Failed to import core module: {e}", exc_info=True)
    sys.exit(f"Core module import failed: {e}")
except Exception as e:
    logger.critical(f"Unexpected error during imports: {e}", exc_info=True)
    sys.exit("Unexpected error during imports.")

# Create Flask app instance
logger.info("Creating Flask app instance...")
try:
    app = Flask(__name__)
    logger.info("Flask app instance created.")
except Exception as e:
    logger.critical(f"Failed to create Flask app instance: {e}", exc_info=True)
    sys.exit("Flask app creation failed.")

# Initialize Kubernetes client
k8s_client_initialized_obs = False
k8s_core_v1_obs = None
try:
    logger.info("Attempting to initialize K8s client for ObsEngine...")
    config.load_incluster_config()
    k8s_core_v1_obs = client.CoreV1Api()
    k8s_client_initialized_obs = True
    logger.info("K8s client initialized successfully for ObsEngine (in-cluster).")
except config.ConfigException:
    logger.warning("In-cluster K8s config failed for ObsEngine, trying kubeconfig...")
    try:
        config.load_kube_config()
        k8s_core_v1_obs = client.CoreV1Api()
        k8s_client_initialized_obs = True
        logger.info("K8s client initialized successfully for ObsEngine (kubeconfig).")
    except Exception as e_kube:
        logger.error(f"Could not configure K8s client for ObsEngine (kubeconfig failed: {e_kube}). Namespace API will rely on DB cache.")
except Exception as e_global:
     logger.error(f"Unexpected error initializing K8s client for ObsEngine: {e_global}", exc_info=True)

# Get Database Path
logger.info("Getting DB path from obsengine_config...")
try:
    if hasattr(obsengine_config, 'get_db_path') and callable(obsengine_config.get_db_path):
        DB_PATH = obsengine_config.get_db_path()
        logger.info(f"Database path set to: {DB_PATH}")
    else:
        logger.critical("obsengine_config.get_db_path function not found!")
        try: logger.error(f"obsengine_config was imported from: {obsengine_config.__file__}")
        except: pass
        raise AttributeError("module 'obsengine_config' has no attribute 'get_db_path'")
except Exception as e:
    logger.critical(f"Failed to get DB path from obsengine_config: {e}", exc_info=True)
    DB_PATH = "/data/fallback_obsengine_data.db"
    logger.warning(f"Using fallback DB path: {DB_PATH}")

# Get Initial DB Defaults
def get_initial_db_defaults():
     logger.debug("Getting initial DB defaults...")
     try:
         logger.debug(f"Attributes inside get_initial_db_defaults: {dir(obsengine_config)}")
     except Exception as dir_err:
         logger.error(f"Error getting dir(obsengine_config) inside function: {dir_err}")

     # Define defaults locally first
     enable_ai_default = 'true'
     ai_provider_default = 'gemini'
     monitored_ns_default = "kube-system,default"
     loki_scan_min_level_default = 'INFO'
     scan_interval_seconds_default = '30'
     restart_count_threshold_default = '5'
     alert_severity_levels_default = "WARNING,ERROR,CRITICAL"
     alert_cooldown_minutes_default = '30'
     enable_telegram_alerts_default = 'false'
     prompt_template_default = "Default Prompt Missing"

     # Safely get values from obsengine_config if available
     if hasattr(obsengine_config, 'DEFAULT_ENABLE_AI_ANALYSIS'):
         enable_ai_default = str(obsengine_config.DEFAULT_ENABLE_AI_ANALYSIS).lower()
     else:
         logger.warning("obsengine_config.DEFAULT_ENABLE_AI_ANALYSIS not found!")
         try: logger.warning(f"obsengine_config imported from: {obsengine_config.__file__}")
         except: pass
     if hasattr(obsengine_config, 'DEFAULT_AI_PROVIDER'):
         ai_provider_default = obsengine_config.DEFAULT_AI_PROVIDER
     else:
         logger.warning("obsengine_config.DEFAULT_AI_PROVIDER not found!")
         try: logger.warning(f"obsengine_config imported from: {obsengine_config.__file__}")
         except: pass
     if hasattr(obsengine_config, 'DEFAULT_MONITORED_NAMESPACES_STR'):
         monitored_ns_default = obsengine_config.DEFAULT_MONITORED_NAMESPACES_STR
     else:
         logger.warning("obsengine_config.DEFAULT_MONITORED_NAMESPACES_STR not found!")
         try: logger.warning(f"obsengine_config imported from: {obsengine_config.__file__}")
         except: pass
     if hasattr(obsengine_config, 'DEFAULT_LOKI_SCAN_MIN_LEVEL'):
         loki_scan_min_level_default = obsengine_config.DEFAULT_LOKI_SCAN_MIN_LEVEL
     if hasattr(obsengine_config, 'DEFAULT_SCAN_INTERVAL_SECONDS'):
         scan_interval_seconds_default = str(obsengine_config.DEFAULT_SCAN_INTERVAL_SECONDS)
     if hasattr(obsengine_config, 'DEFAULT_RESTART_COUNT_THRESHOLD'):
         restart_count_threshold_default = str(obsengine_config.DEFAULT_RESTART_COUNT_THRESHOLD)
     if hasattr(obsengine_config, 'DEFAULT_ALERT_SEVERITY_LEVELS_STR'):
         alert_severity_levels_default = obsengine_config.DEFAULT_ALERT_SEVERITY_LEVELS_STR
     if hasattr(obsengine_config, 'DEFAULT_ALERT_COOLDOWN_MINUTES'):
         alert_cooldown_minutes_default = str(obsengine_config.DEFAULT_ALERT_COOLDOWN_MINUTES)
     if hasattr(obsengine_config, 'DEFAULT_ENABLE_TELEGRAM_ALERTS'):
         enable_telegram_alerts_default = str(obsengine_config.DEFAULT_ENABLE_TELEGRAM_ALERTS).lower()
     if hasattr(obsengine_config, 'DEFAULT_PROMPT_TEMPLATE'):
         prompt_template_default = obsengine_config.DEFAULT_PROMPT_TEMPLATE

     defaults = {
         'enable_ai_analysis': enable_ai_default,
         'ai_provider': ai_provider_default,
         'monitored_namespaces': monitored_ns_default,
         'loki_scan_min_level': loki_scan_min_level_default,
         'scan_interval_seconds': scan_interval_seconds_default,
         'restart_count_threshold': restart_count_threshold_default,
         'alert_severity_levels': alert_severity_levels_default,
         'alert_cooldown_minutes': alert_cooldown_minutes_default,
         'enable_telegram_alerts': enable_telegram_alerts_default,
         'prompt_template': prompt_template_default,
     }
     logger.debug(f"Initial DB defaults generated: {defaults}")
     return defaults

# Initialize Database
logger.info("Initializing database via db_manager.init_db...")
try:
    if not db_manager.init_db(DB_PATH, get_initial_db_defaults()):
        logger.critical("db_manager.init_db returned False! Check permissions, DB path, and DB logs.")
    else:
        logger.info("Database initialized successfully via db_manager.init_db.")
except Exception as e:
    logger.critical(f"Exception during db_manager.init_db call: {e}", exc_info=True)

# Background Tasks Thread
def periodic_background_tasks_thread():
    try:
        interval = 300
        if hasattr(obsengine_config, 'get_stats_update_interval') and callable(obsengine_config.get_stats_update_interval):
            interval = obsengine_config.get_stats_update_interval()
        else:
             logger.error("obsengine_config.get_stats_update_interval function not found! Using fallback interval 300s.")
             try: logger.error(f"obsengine_config imported from: {obsengine_config.__file__}")
             except: pass

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


# --- Flask Routes ---

@app.route('/healthz')
def healthz():
    """Health check endpoint."""
    logger.debug("Health check endpoint called.")
    return "OK", 200

@app.route('/collect', methods=['POST'])
def collect_data():
    """Endpoint to receive data from collector agents."""
    received_time = datetime.now(timezone.utc)
    logger.info("--- /collect endpoint hit ---")

    # Validate request type
    if not request.is_json:
        logger.error("Received non-JSON request to /collect")
        return jsonify({"error": "Request must be JSON"}), 400

    # Parse JSON data
    try:
        data = request.get_json()
        logger.debug(f"Received raw data (keys): {list(data.keys())}")
    except Exception as e:
        logger.error(f"Error getting or parsing JSON data: {e}", exc_info=True)
        return jsonify({"error": "Failed to parse JSON data"}), 400

    # Validate required fields
    required_fields = ["pod_key", "k8s_context", "logs", "initial_reasons", "collection_timestamp", "agent_id", "cluster_name"]
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        logger.error(f"Validation failed: {error_msg}")
        return jsonify({"error": error_msg}), 400

    # Extract data
    pod_key = data.get("pod_key")
    k8s_context = data.get("k8s_context")
    logs = data.get("logs", [])
    initial_reasons = data.get("initial_reasons", [])
    collection_timestamp_str = data.get("collection_timestamp") # Not currently used, but received
    agent_id = data.get("agent_id")
    cluster_name = data.get("cluster_name")

    logger.info(f"Processing data for pod: {pod_key} from Agent: {agent_id} (Cluster: {cluster_name})")

    # Update agent heartbeat
    try:
        db_manager.update_agent_heartbeat(DB_PATH, agent_id, cluster_name, received_time.isoformat())
    except Exception as heartbeat_err:
        logger.error(f"Failed to update heartbeat for agent {agent_id}: {heartbeat_err}", exc_info=True)

    # Main processing block
    try:
        # Load configurations
        logger.debug(f"[{pod_key} @ {cluster_name}] Loading configurations...")
        ai_config = obsengine_config.get_ai_config()
        alert_config = obsengine_config.get_alert_config()
        db_path_local = obsengine_config.get_db_path() # Use config function to get path
        logger.debug(f"[{pod_key} @ {cluster_name}] Configurations loaded.")

        # Perform AI analysis (or rule-based fallback)
        logger.debug(f"[{pod_key} @ {cluster_name}] Performing analysis...")
        analysis_result, final_prompt, raw_response_text = ai_providers.perform_analysis(
            logs, k8s_context, "; ".join(initial_reasons), ai_config, ai_config.get('prompt_template')
        )
        logger.debug(f"[{pod_key} @ {cluster_name}] Analysis complete.")

        # Extract analysis results
        severity = analysis_result.get("severity", "UNKNOWN").upper()
        summary = analysis_result.get("summary", "N/A")
        root_cause_raw = analysis_result.get("root_cause", "N/A")
        steps_raw = analysis_result.get("troubleshooting_steps", "N/A")
        # Ensure root_cause and steps are strings
        root_cause = "\n".join(root_cause_raw) if isinstance(root_cause_raw, list) else str(root_cause_raw)
        steps = "\n".join(steps_raw) if isinstance(steps_raw, list) else str(steps_raw)

        logger.info(f"[{pod_key} @ {cluster_name}] Analysis result: Severity={severity}")
        logger.debug(f"[{pod_key} @ {cluster_name}] Summary: {summary}")

        # Record the incident
        logger.debug(f"[{pod_key} @ {cluster_name}] Recording incident...")
        sample_logs_str = "\n".join([f"- {log.get('message', '')[:150]}" for log in logs[:5]]) if logs else "-"
        db_manager.record_incident(
            db_path_local, pod_key, severity, summary, "; ".join(initial_reasons),
            k8s_context, sample_logs_str, alert_config.get('alert_severity_levels', []),
            final_prompt, raw_response_text, root_cause, steps
        )
        logger.debug(f"[{pod_key} @ {cluster_name}] Incident recorded.")

        # Check alert conditions
        alert_levels = alert_config.get('alert_severity_levels', [])
        cooldown_minutes = alert_config.get('alert_cooldown_minutes', 30)
        logger.debug(f"[{pod_key} @ {cluster_name}] Checking alert conditions (Severity: {severity}, Levels: {alert_levels})...")

        if severity in alert_levels:
            logger.debug(f"[{pod_key} @ {cluster_name}] Severity meets threshold. Checking cooldown...")
            if not db_manager.is_pod_in_cooldown(db_path_local, pod_key):
                logger.info(f"[{pod_key} @ {cluster_name}] Not in cooldown. Processing alert.")
                if alert_config.get('enable_telegram_alerts'):
                    bot_token = alert_config.get('telegram_bot_token')
                    chat_id = alert_config.get('telegram_chat_id')
                    if bot_token and chat_id:
                        logger.debug(f"[{pod_key} @ {cluster_name}] Sending Telegram alert...")
                        # Ensure notifier has HCM_TZ defined or handles timezone conversion
                        alert_time_hcm = received_time.astimezone(getattr(notifier, 'HCM_TZ', timezone.utc))
                        time_format = '%Y-%m-%d %H:%M:%S %Z'
                        alert_data = {
                            'pod_key': f"{cluster_name}/{pod_key}", # Prepend cluster name
                            'cluster_name': cluster_name,
                            'severity': severity,
                            'summary': summary,
                            'root_cause': root_cause,
                            'troubleshooting_steps': steps,
                            'initial_reasons': "; ".join(initial_reasons),
                            'alert_time': alert_time_hcm.strftime(time_format),
                            'sample_logs': sample_logs_str
                        }
                        alert_sent = notifier.send_telegram_alert(
                            bot_token, chat_id, alert_data, ai_config.get('enable_ai_analysis')
                        )
                        if alert_sent:
                            logger.debug(f"[{pod_key} @ {cluster_name}] Alert sent. Setting cooldown...")
                            db_manager.set_pod_cooldown(db_path_local, pod_key, cooldown_minutes)
                        else:
                            logger.warning(f"[{pod_key} @ {cluster_name}] Telegram alert sending failed, cooldown NOT set.")
                    else:
                        logger.warning(f"[{pod_key} @ {cluster_name}] Telegram alerts enabled but token/chat_id missing. Setting cooldown anyway.")
                        db_manager.set_pod_cooldown(db_path_local, pod_key, cooldown_minutes)
                else:
                    logger.info(f"[{pod_key} @ {cluster_name}] Telegram alerts disabled. Setting cooldown.")
                    db_manager.set_pod_cooldown(db_path_local, pod_key, cooldown_minutes)
            else:
                logger.info(f"[{pod_key} @ {cluster_name}] Pod is in cooldown. Alert processing skipped.")
        else:
            logger.info(f"[{pod_key} @ {cluster_name}] Severity does not meet alert threshold. No alert needed.")

        logger.info(f"--- Successfully processed data for {pod_key} from Agent {agent_id} ---")
        return jsonify({"message": f"Data processed successfully for {pod_key}"}), 200

    # --- Exception Handling for /collect ---
    # Specific handling for AttributeError, potentially from config issues
    except AttributeError as ae:
        logger.error(f"--- AttributeError processing data for pod {pod_key} from Agent {agent_id}: {ae} ---", exc_info=True)
        # Log current config attributes if the error seems related to obsengine_config
        if 'obsengine_config' in str(ae):
            try:
                logger.error(f"Attributes currently in obsengine_config: {dir(obsengine_config)}")
            except Exception as dir_err:
                 logger.error(f"Could not get dir(obsengine_config): {dir_err}")
        return jsonify({"error": f"Internal server error (AttributeError) processing data for {pod_key}"}), 500
    # General exception handling
    except Exception as e:
        # Ensure this block is correctly indented relative to the main try block
        logger.error(f"--- Error processing data for pod {pod_key} from Agent {agent_id}: {e} ---", exc_info=True)
        return jsonify({"error": f"Internal server error processing data for {pod_key}"}), 500
    # --- End Exception Handling for /collect ---


@app.route('/api/agents/status', methods=['GET'])
def get_agent_status():
    """API endpoint to get the status of active agents."""
    logger.info("--- /api/agents/status endpoint hit ---")
    try:
        agent_timeout = 300 # Consider making this configurable
        active_agents = db_manager.get_active_agents(DB_PATH, agent_timeout)
        logger.info(f"Returning status for {len(active_agents)} active agents.")
        return jsonify({"active_agents": active_agents}), 200
    except Exception as e:
        logger.error(f"Error getting agent status: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve agent status"}), 500

@app.route('/api/incidents', methods=['GET'])
def get_incidents_api():
    """API endpoint to retrieve incidents with filtering and pagination."""
    logger.info("--- /api/incidents endpoint hit ---")
    try:
        # Get query parameters
        pod_filter = request.args.get('pod', default="", type=str).strip()
        severity_filter = request.args.get('severity', default="", type=str).upper().strip()
        start_date_str = request.args.get('start_date', default=None, type=str)
        end_date_str = request.args.get('end_date', default=None, type=str)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('limit', 20, type=int)
        offset = (page - 1) * per_page

        conn = db_manager._get_db_connection(DB_PATH)
        if conn is None:
            return jsonify({"error": "Database connection failed."}), 500

        incidents = []
        total_count = 0
        try:
            cursor = conn.cursor()
            base_query = "FROM incidents WHERE 1=1"
            params = []

            # Apply filters
            if pod_filter:
                base_query += " AND pod_key LIKE ?"
                params.append(f"%{pod_filter}%")
            if severity_filter:
                base_query += " AND severity = ?"
                params.append(severity_filter)
            if start_date_str:
                try:
                    start_dt_utc = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                    base_query += " AND timestamp >= ?"
                    params.append(start_dt_utc.isoformat())
                except ValueError:
                    logger.warning(f"Invalid start_date format: {start_date_str}. Ignoring filter.")
            if end_date_str:
                try:
                    end_dt_utc = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                    base_query += " AND timestamp <= ?"
                    params.append(end_dt_utc.isoformat())
                except ValueError:
                    logger.warning(f"Invalid end_date format: {end_date_str}. Ignoring filter.")

            # Get total count
            count_query = f"SELECT COUNT(*) as count {base_query}"
            cursor.execute(count_query, tuple(params))
            count_result = cursor.fetchone()
            total_count = count_result['count'] if count_result else 0

            # Get paginated data
            data_query = f"""
                SELECT id, timestamp, pod_key, severity, summary, initial_reasons,
                       k8s_context, sample_logs, input_prompt, raw_ai_response,
                       root_cause, troubleshooting_steps
                {base_query} ORDER BY timestamp DESC LIMIT ? OFFSET ?
            """
            params_data = params + [per_page, offset]
            cursor.execute(data_query, tuple(params_data))
            rows = cursor.fetchall()
            incidents = [dict(row) for row in rows]

        except sqlite3.Error as db_err:
            logger.error(f"Database error fetching incidents: {db_err}", exc_info=True)
            return jsonify({"error": f"Database error: {db_err}"}), 500
        finally:
            if conn:
                conn.close()

        # Calculate pagination details
        total_pages = math.ceil(total_count / per_page) if per_page > 0 else 0
        pagination = {"page": page, "per_page": per_page, "total_items": total_count, "total_pages": total_pages}

        logger.info(f"Returning {len(incidents)} incidents (Page {page}/{total_pages})")
        return jsonify({"incidents": incidents, "pagination": pagination})

    except Exception as e:
        logger.error(f"Error in /api/incidents: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve incidents"}), 500


@app.route('/api/stats', methods=['GET'])
def get_stats_api():
    """API endpoint to retrieve various statistics."""
    logger.info("--- /api/stats endpoint hit ---")
    try:
        days = request.args.get('days', 1, type=int)
        if days not in [1, 7, 30]:
            days = 1 # Default to 1 day if invalid
        logger.debug(f"Calculating stats for last {days} days.")

        end_date_utc = datetime.now(timezone.utc)
        start_date_utc = end_date_utc - timedelta(days=days)
        # Ensure chart data covers at least 7 days for better visualization
        chart_days = max(days, 7)
        chart_start_date_utc = end_date_utc - timedelta(days=chart_days)

        conn = db_manager._get_db_connection(DB_PATH)
        if conn is None:
            return jsonify({"error": "Database connection failed."}), 500

        stats = {}
        try:
            cursor = conn.cursor()

            # Daily stats for line chart (last 7 or 30 days)
            cursor.execute('''
                SELECT date, model_calls, telegram_alerts, incident_count
                FROM daily_stats
                WHERE date >= ? AND date <= ? ORDER BY date ASC
            ''', (chart_start_date_utc.strftime('%Y-%m-%d'), end_date_utc.strftime('%Y-%m-%d')))
            daily_stats_rows = cursor.fetchall()
            stats['daily_stats_for_chart'] = [dict(row) for row in daily_stats_rows]
            logger.debug(f"Fetched {len(stats['daily_stats_for_chart'])} daily stat records for chart.")

            # Totals for the selected period (1, 7, or 30 days)
            cursor.execute('''
                SELECT SUM(model_calls) as total_model_calls,
                       SUM(telegram_alerts) as total_telegram_alerts,
                       SUM(incident_count) as total_incidents
                FROM daily_stats WHERE date >= ? AND date <= ?
            ''', (start_date_utc.strftime('%Y-%m-%d'), end_date_utc.strftime('%Y-%m-%d')))
            totals_row = cursor.fetchone()
            stats['totals'] = {
                "model_calls": totals_row['total_model_calls'] if totals_row and totals_row['total_model_calls'] else 0,
                "telegram_alerts": totals_row['total_telegram_alerts'] if totals_row and totals_row['total_telegram_alerts'] else 0,
                "incidents": totals_row['total_incidents'] if totals_row and totals_row['total_incidents'] else 0,
            }
            logger.debug(f"Calculated totals for last {days} days: {stats['totals']}")

            # Severity distribution for today (only if days=1)
            today_start_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            today_end_utc = today_start_utc + timedelta(days=1) - timedelta(microseconds=1)
            cursor.execute('''
                SELECT severity, COUNT(*) as count FROM incidents
                WHERE timestamp >= ? AND timestamp <= ? GROUP BY severity
            ''', (today_start_utc.isoformat(), today_end_utc.isoformat()))
            severity_rows_today = cursor.fetchall()
            stats['severity_distribution_today'] = {row['severity']: row['count'] for row in severity_rows_today if row['severity']}
            logger.debug(f"Calculated severity distribution for today: {stats['severity_distribution_today']}")

            # Top 5 problematic pods for the selected period
            cursor.execute('''
                SELECT pod_key, COUNT(*) as count FROM incidents
                WHERE timestamp >= ? AND timestamp <= ?
                GROUP BY pod_key ORDER BY count DESC LIMIT 5
            ''', (start_date_utc.isoformat(), end_date_utc.isoformat()))
            top_pods_rows = cursor.fetchall()
            stats['top_problematic_pods'] = {row['pod_key']: row['count'] for row in top_pods_rows}
            logger.debug(f"Calculated top pods for last {days} days: {stats['top_problematic_pods']}")

            # Namespace distribution for the selected period
            cursor.execute('''
                SELECT SUBSTR(pod_key, 1, INSTR(pod_key, '/') - 1) as namespace, COUNT(*) as count
                FROM incidents WHERE timestamp >= ? AND timestamp <= ? AND INSTR(pod_key, '/') > 0
                GROUP BY namespace ORDER BY count DESC
            ''', (start_date_utc.isoformat(), end_date_utc.isoformat()))
            namespace_rows = cursor.fetchall()
            stats['namespace_distribution'] = {row['namespace']: row['count'] for row in namespace_rows if row['namespace']}
            logger.debug(f"Calculated namespace distribution for last {days} days: {stats['namespace_distribution']}")

        except sqlite3.Error as db_err:
            logger.error(f"Database error fetching stats: {db_err}", exc_info=True)
            return jsonify({"error": f"Database error: {db_err}"}), 500
        finally:
            if conn:
                conn.close()

        logger.info(f"Returning stats data for last {days} days.")
        return jsonify(stats)

    except Exception as e:
        logger.error(f"Error in /api/stats: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve stats"}), 500

@app.route('/api/config/all', methods=['GET'])
def get_all_config_api():
    """API endpoint to get all non-sensitive configuration."""
    logger.info("--- /api/config/all endpoint hit ---")
    try:
        # Use the config loading function which handles DB and env vars
        current_config = obsengine_config.get_config()

        # Define keys to exclude
        sensitive_keys = ['ai_api_key', 'telegram_bot_token']
        # Create a new dict excluding sensitive keys
        safe_config = {k: v for k, v in current_config.items() if k not in sensitive_keys}

        # Add the string version of alert levels for easier display
        if 'alert_severity_levels' in safe_config and isinstance(safe_config['alert_severity_levels'], list):
            safe_config['alert_severity_levels_str'] = ','.join(safe_config['alert_severity_levels'])

        logger.info("Returning non-sensitive configuration.")
        return jsonify(safe_config), 200
    # Correctly indented except block for the try block above
    except Exception as e:
        logger.error(f"Error getting all config: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve configuration"}), 500
    # --- End of corrected except block ---


@app.route('/api/namespaces', methods=['GET'])
def get_available_namespaces_api():
    """API endpoint to get available Kubernetes namespaces."""
    logger.info("--- /api/namespaces endpoint hit ---")
    namespaces = []

    # Try fetching from K8s API first if client is initialized
    if k8s_client_initialized_obs and k8s_core_v1_obs:
        try:
            excluded_namespaces_str = obsengine_config._get_env_var("EXCLUDED_NAMESPACES", "kube-node-lease,kube-public")
            excluded_namespaces = {ns.strip() for ns in excluded_namespaces_str.split(',') if ns.strip()}
            all_namespaces = k8s_core_v1_obs.list_namespace(watch=False, timeout_seconds=15)

            # Check if items attribute exists and is iterable
            if hasattr(all_namespaces, 'items') and all_namespaces.items is not None:
                for ns in all_namespaces.items:
                    if ns.status.phase == "Active" and ns.metadata.name not in excluded_namespaces:
                        namespaces.append(ns.metadata.name)
            else:
                 logger.warning("K8s API response for namespaces did not contain 'items'.")

            namespaces.sort()
            logger.info(f"Fetched {len(namespaces)} namespaces directly from K8s API.")
            # Update the DB cache asynchronously or periodically if desired
            # For now, just return the live data
            # Consider adding: db_manager.update_available_namespaces_in_db(DB_PATH, namespaces)
            return jsonify(namespaces), 200
        except ApiException as e:
            logger.warning(f"K8s API error fetching namespaces: {e.status} {e.reason}. Falling back to DB cache.")
        except Exception as e:
            logger.warning(f"Unexpected error fetching namespaces from K8s API: {e}. Falling back to DB cache.", exc_info=True) # Added exc_info

    # Fallback to DB cache if K8s API fails or is not initialized
    logger.info("Falling back to fetching available namespaces from DB cache.")
    conn = db_manager._get_db_connection(DB_PATH)
    if conn is None:
        return jsonify({"error": "Database connection failed."}), 500
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

# --- Helper function to save config to DB ---
def _save_config_to_db(config_dict):
    """Saves a dictionary of configuration key-value pairs to the database."""
    conn = db_manager._get_db_connection(DB_PATH)
    if conn is None:
        logger.error("Failed to get DB connection for saving config.")
        return False, "Database connection failed"
    try:
        with conn:
            cursor = conn.cursor()
            for key, value in config_dict.items():
                value_str = str(value) if value is not None else None # Convert to string, handle None
                if value_str is not None: # Only save non-None values
                    cursor.execute("INSERT OR REPLACE INTO agent_config (key, value) VALUES (?, ?)", (key, value_str))
                    # Log safely, truncate long values
                    value_log = value_str[:50] + '...' if len(value_str) > 50 else value_str
                    logger.info(f"Saved config key='{key}' value='{value_log}'")
                else:
                    logger.warning(f"Attempted to save None value for key '{key}'. Ignoring.")
        # Force config refresh on next request
        obsengine_config._last_config_refresh_time = 0
        return True, "Configuration saved successfully."
    except sqlite3.Error as e:
        logger.error(f"Database error saving configuration: {e}", exc_info=True)
        return False, f"Database error: {e}"
    except Exception as e:
        logger.error(f"Unexpected error saving configuration: {e}", exc_info=True)
        return False, "Unexpected error saving configuration."
    finally:
         if conn:
             conn.close()

# --- API Endpoints for Saving Configuration ---

@app.route('/api/config/general', methods=['POST'])
def save_general_config_api():
    """API endpoint to save general agent settings."""
    logger.info("--- POST /api/config/general endpoint hit ---")
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    logger.debug(f"Received general config data: {data}")

    config_to_save = {}
    validation_errors = []
    valid_log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
    valid_severity_levels = ["INFO", "WARNING", "ERROR", "CRITICAL"]

    try:
        # Validate Scan Interval
        scan_interval = int(data.get('scan_interval_seconds', obsengine_config.DEFAULT_SCAN_INTERVAL_SECONDS))
        if scan_interval < 10:
            validation_errors.append("Scan interval must be >= 10")
        else:
            config_to_save['scan_interval_seconds'] = str(scan_interval)

        # Validate Restart Threshold
        restart_threshold = int(data.get('restart_count_threshold', obsengine_config.DEFAULT_RESTART_COUNT_THRESHOLD))
        if restart_threshold < 1:
            validation_errors.append("Restart threshold must be >= 1")
        else:
            config_to_save['restart_count_threshold'] = str(restart_threshold)

        # Validate Loki Scan Level
        scan_level = data.get('loki_scan_min_level', obsengine_config.DEFAULT_LOKI_SCAN_MIN_LEVEL).upper()
        if scan_level not in valid_log_levels:
            validation_errors.append(f"Invalid Loki scan level: {scan_level}")
        else:
            config_to_save['loki_scan_min_level'] = scan_level

        # Validate Alert Cooldown
        cooldown = int(data.get('alert_cooldown_minutes', obsengine_config.DEFAULT_ALERT_COOLDOWN_MINUTES))
        if cooldown < 0: # Allow 0 for no cooldown
            validation_errors.append("Alert cooldown cannot be negative")
        else:
            config_to_save['alert_cooldown_minutes'] = str(cooldown)

        # Validate Alert Severity Levels
        alert_levels_str = data.get('alert_severity_levels', obsengine_config.DEFAULT_ALERT_SEVERITY_LEVELS_STR)
        alert_levels_list = [lvl.strip().upper() for lvl in alert_levels_str.split(',') if lvl.strip()]
        if not alert_levels_list:
            validation_errors.append("Alert severity levels cannot be empty")
        else:
            invalid_levels_found = False
            for lvl in alert_levels_list:
                if lvl not in valid_severity_levels:
                    validation_errors.append(f"Invalid alert severity level: {lvl}")
                    invalid_levels_found = True
            if not invalid_levels_found:
                config_to_save['alert_severity_levels'] = ','.join(alert_levels_list)

    except (ValueError, TypeError) as e:
        logger.error(f"Validation error processing general config: {e}")
        return jsonify({"error": f"Invalid input data type: {e}"}), 400

    # Check validation results
    if validation_errors:
        logger.error(f"Validation failed for general config: {validation_errors}")
        return jsonify({"error": "Validation failed", "details": validation_errors}), 400

    # Save to DB
    success, message = _save_config_to_db(config_to_save)
    if success:
        return jsonify({"message": message}), 200
    else:
        return jsonify({"error": message}), 500

@app.route('/api/config/ai', methods=['POST'])
def save_ai_config_api():
    """API endpoint to save AI analysis settings."""
    logger.info("--- POST /api/config/ai endpoint hit ---")
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    logger.debug(f"Received AI config data: {data}")

    config_to_save = {}
    validation_errors = []
    # Define valid providers (update as needed)
    valid_providers = ['gemini', 'local', 'openai', 'groq', 'deepseek', 'none']

    # Validate Enable AI toggle
    enable_ai = data.get('enable_ai_analysis')
    if not isinstance(enable_ai, bool):
        validation_errors.append("'enable_ai_analysis' must be a boolean")
    else:
        config_to_save['enable_ai_analysis'] = str(enable_ai).lower()

    # Validate AI Provider
    provider = data.get('ai_provider', obsengine_config.DEFAULT_AI_PROVIDER).lower()
    if provider not in valid_providers:
        validation_errors.append(f"Invalid AI provider: {provider}")
    else:
        config_to_save['ai_provider'] = provider

    # Validate Model Identifier (optional for 'local' and 'none')
    model_id = data.get('ai_model_identifier', '').strip()
    config_to_save['ai_model_identifier'] = model_id # Save even if empty
    if enable_ai and provider not in ['local', 'none'] and not model_id:
         validation_errors.append("Model Identifier is required for this AI provider when AI is enabled.")

    # NOTE: API Key is NOT saved via this endpoint. It's managed by environment variables/secrets.

    # Check validation results
    if validation_errors:
        logger.error(f"Validation failed for AI config: {validation_errors}")
        return jsonify({"error": "Validation failed", "details": validation_errors}), 400

    # Save to DB
    success, message = _save_config_to_db(config_to_save)
    if success:
        return jsonify({"message": message}), 200
    else:
        return jsonify({"error": message}), 500

@app.route('/api/config/telegram', methods=['POST'])
def save_telegram_config_api():
    """API endpoint to save Telegram alert settings."""
    logger.info("--- POST /api/config/telegram endpoint hit ---")
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    logger.debug(f"Received Telegram config data: {data}")

    config_to_save = {}
    validation_errors = []

    # Validate Enable Telegram toggle
    enable_alerts = data.get('enable_telegram_alerts')
    if not isinstance(enable_alerts, bool):
        validation_errors.append("'enable_telegram_alerts' must be a boolean")
    else:
        config_to_save['enable_telegram_alerts'] = str(enable_alerts).lower()

    # Validate Chat ID (required)
    chat_id = data.get('telegram_chat_id', '').strip()
    if not chat_id:
        validation_errors.append("Telegram Chat ID cannot be empty")
    else:
        config_to_save['telegram_chat_id'] = chat_id

    # NOTE: Bot Token is NOT saved via this endpoint. It's managed by environment variables/secrets.

    # Check validation results
    if validation_errors:
        logger.error(f"Validation failed for Telegram config: {validation_errors}")
        return jsonify({"error": "Validation failed", "details": validation_errors}), 400

    # Save to DB
    success, message = _save_config_to_db(config_to_save)
    if success:
        return jsonify({"message": message}), 200
    else:
        return jsonify({"error": message}), 500

@app.route('/api/config/monitored_namespaces', methods=['POST'])
def save_monitored_namespaces_api():
    """API endpoint to save the list of monitored namespaces."""
    logger.info("--- POST /api/config/monitored_namespaces endpoint hit ---")
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    logger.debug(f"Received monitored namespaces data: {data}")

    namespaces = data.get('namespaces')
    if not isinstance(namespaces, list):
        return jsonify({"error": "'namespaces' must be a list"}), 400

    # Clean and validate the list
    cleaned_namespaces = [str(ns).strip() for ns in namespaces if isinstance(ns, str) and str(ns).strip()]

    # Save as a JSON string in the database
    value_to_save = json.dumps(cleaned_namespaces)
    config_to_save = {'monitored_namespaces': value_to_save}

    # Save to DB
    success, message = _save_config_to_db(config_to_save)
    if success:
        return jsonify({"message": message}), 200
    else:
        return jsonify({"error": message}), 500


# --- Main Execution ---
if __name__ == '__main__':
    flask_port = int(os.environ.get("FLASK_PORT", 8080))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    log_level_name = logging.getLevelName(logging.getLogger().getEffectiveLevel())
    logger.info(f"Starting Flask development server on 0.0.0.0:{flask_port} | Debug: {debug_mode} | Log Level: {log_level_name}")
    # Use use_reloader=False if running directly with python for stability with threads
    app.run(host='0.0.0.0', port=flask_port, debug=debug_mode, use_reloader=False if not debug_mode else True)

logger.info("--- ObsEngine app.py finished parsing (module level) ---")
