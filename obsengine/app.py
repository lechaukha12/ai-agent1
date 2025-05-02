# ai-agent1/obsengine/app.py
import os
import logging
import sys
import json
import threading
import time
import math
import sqlite3
from flask import Flask, request, jsonify, abort
from datetime import datetime, timezone, timedelta
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
# --- FIX: Import parse_quantity and format_quantity from utils ---
# Import the correct functions for parsing and formatting resource quantities
from kubernetes.utils.quantity import parse_quantity, format_quantity
# --- Removed incorrect import ---
# The following import path is incorrect in recent versions of the kubernetes client
# from kubernetes.client.models.resource_quantity import ResourceQuantity

# Get the directory of the current script
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Add the script's directory to the Python path if not already present
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
# Get the parent directory of the script's directory
PARENT_DIR = os.path.dirname(APP_DIR)
# Add the parent directory to the Python path if not already present
if PARENT_DIR not in sys.path:
     sys.path.insert(0, PARENT_DIR)

# Configure logging
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=log_level,
                    format='%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s',
                    stream=sys.stdout)
logger = logging.getLogger(__name__)

logger.info(f"--- ObsEngine app.py starting execution (Log Level: {log_level}) ---")

# Import custom modules, log critical errors if imports fail
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
k8s_version_api_obs = None
try:
    logger.info("Attempting to initialize K8s client for ObsEngine...")
    # Try loading in-cluster configuration first (for running inside Kubernetes)
    config.load_incluster_config()
    k8s_core_v1_obs = client.CoreV1Api()
    k8s_version_api_obs = client.VersionApi()
    k8s_client_initialized_obs = True
    logger.info("K8s client initialized successfully for ObsEngine (in-cluster).")
except config.ConfigException:
    # If in-cluster config fails, try loading from kubeconfig file (for local development)
    logger.warning("In-cluster K8s config failed for ObsEngine, trying kubeconfig...")
    try:
        config.load_kube_config()
        k8s_core_v1_obs = client.CoreV1Api()
        k8s_version_api_obs = client.VersionApi()
        k8s_client_initialized_obs = True
        logger.info("K8s client initialized successfully for ObsEngine (kubeconfig).")
    except Exception as e_kube:
        logger.error(f"Could not configure K8s client for ObsEngine (kubeconfig failed: {e_kube}). K8s API features might be limited.")
except Exception as e_global:
     logger.error(f"Unexpected error initializing K8s client for ObsEngine: {e_global}", exc_info=True)

# Get database path from configuration
logger.info("Getting DB path from obsengine_config...")
try:
    DB_PATH = obsengine_config.get_db_path()
    logger.info(f"Database path set to: {DB_PATH}")
except Exception as e:
    logger.critical(f"Failed to get DB path from obsengine_config: {e}", exc_info=True)
    # Fallback DB path if config fails
    DB_PATH = "/data/fallback_obsengine_data.db"
    logger.warning(f"Using fallback DB path: {DB_PATH}")

# Function to get initial default values for global database settings
def get_initial_global_db_defaults():
     logger.debug("Getting initial GLOBAL DB defaults...")
     # Use getattr to safely access config constants with defaults
     defaults = {
         'enable_telegram_alerts': str(getattr(obsengine_config, 'DEFAULT_ENABLE_TELEGRAM_ALERTS', False)).lower(),
         'telegram_chat_id': getattr(obsengine_config, 'DEFAULT_TELEGRAM_CHAT_ID', ''),
         'enable_ai_analysis': str(getattr(obsengine_config, 'DEFAULT_ENABLE_AI_ANALYSIS', True)).lower(),
         'ai_provider': getattr(obsengine_config, 'DEFAULT_AI_PROVIDER', 'gemini').lower(),
         'ai_model_identifier': getattr(obsengine_config, 'DEFAULT_AI_MODEL_IDENTIFIER', 'gemini-1.5-flash'),
         'local_gemini_endpoint': getattr(obsengine_config, 'DEFAULT_LOCAL_GEMINI_ENDPOINT', ''),
         'prompt_template': getattr(obsengine_config, 'DEFAULT_PROMPT_TEMPLATE', "Default Prompt Missing"),
         'alert_severity_levels': getattr(obsengine_config, 'DEFAULT_ALERT_SEVERITY_LEVELS_STR', "WARNING,ERROR,CRITICAL"),
         'alert_cooldown_minutes': str(getattr(obsengine_config, 'DEFAULT_ALERT_COOLDOWN_MINUTES', 30)),
     }
     logger.debug(f"Initial GLOBAL DB defaults generated: {defaults}")
     return defaults

# Initialize the database schema and default configurations
logger.info("Initializing database via db_manager.init_db...")
try:
    if not db_manager.init_db(DB_PATH, get_initial_global_db_defaults()):
        logger.critical("db_manager.init_db returned False! DB operations might fail. Check permissions, DB path, and DB logs.")
    else:
        logger.info("Database initialized successfully via db_manager.init_db.")
except Exception as e:
    logger.critical(f"Exception during db_manager.init_db call: {e}", exc_info=True)

# Function to run periodic background tasks (stats update, agent cleanup)
def periodic_background_tasks_thread():
    try:
        # Get intervals from config or use defaults
        interval = int(getattr(obsengine_config, 'DEFAULT_STATS_UPDATE_INTERVAL_SECONDS', 300))
        agent_cleanup_interval = 3600 # Cleanup inactive agents every hour
        agent_timeout = 86400 # Consider agents inactive after 24 hours
        logger.info(f"Starting periodic background tasks thread. Stats Interval: {interval}s, Agent Cleanup Interval: {agent_cleanup_interval}s")
        last_agent_cleanup_time = time.time()

        while True:
            # Wait for the specified interval
            time.sleep(interval)
            logger.debug("Triggering periodic stats update...")
            try:
                # Update daily statistics (AI calls, alerts)
                db_manager.update_daily_stats(DB_PATH)
            except Exception as update_err:
                 logger.error(f"Error during db_manager.update_daily_stats: {update_err}", exc_info=True)

            # Check if it's time to clean up inactive agents
            current_time = time.time()
            if current_time - last_agent_cleanup_time >= agent_cleanup_interval:
                logger.debug("Triggering periodic agent cleanup...")
                try:
                    # Remove records of agents not seen recently
                    deleted_count = db_manager.cleanup_inactive_agents(DB_PATH, agent_timeout)
                    logger.debug(f"Agent cleanup finished. Removed {deleted_count} inactive agents.")
                    last_agent_cleanup_time = current_time
                except Exception as cleanup_err:
                    logger.error(f"Error during db_manager.cleanup_inactive_agents: {cleanup_err}", exc_info=True)

    except Exception as thread_init_err:
         logger.error(f"Error initializing periodic_background_tasks_thread: {thread_init_err}", exc_info=True)

# Start the background tasks thread
logger.info("Attempting to start background tasks thread...")
try:
    background_thread = threading.Thread(target=periodic_background_tasks_thread, daemon=True)
    background_thread.start()
    logger.info("Background tasks thread started.")
except Exception as e:
    logger.error(f"Failed to start background tasks thread: {e}", exc_info=True)


# Health check endpoint
@app.route('/healthz')
def healthz():
    logger.debug("Health check endpoint called.")
    return "OK", 200

# Endpoint to receive data from collector agents
@app.route('/collect', methods=['POST'])
def collect_data():
    received_time = datetime.now(timezone.utc)
    logger.info("--- /collect endpoint hit ---")
    # Ensure request is JSON
    if not request.is_json:
        logger.error("Received non-JSON request to /collect")
        return jsonify({"error": "Request must be JSON"}), 400
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

    # Extract data from JSON payload
    pod_key = data.get("pod_key") # Format: namespace/pod_name
    k8s_context = data.get("k8s_context")
    logs = data.get("logs", [])
    initial_reasons = data.get("initial_reasons", [])
    agent_id = data.get("agent_id")
    cluster_name = data.get("cluster_name")

    # Create a full key including the cluster name for uniqueness across clusters
    full_pod_key = f"{cluster_name}/{pod_key}" if cluster_name else pod_key
    logger.info(f"Processing data for pod: {full_pod_key} from Agent: {agent_id}")

    # Update the agent's last seen timestamp (heartbeat)
    try:
        db_manager.update_agent_heartbeat(DB_PATH, agent_id, cluster_name, received_time.isoformat())
    except Exception as heartbeat_err:
        logger.error(f"Failed to update heartbeat for agent {agent_id}: {heartbeat_err}", exc_info=True)

    try:
        # Load current global configurations
        global_ai_config = obsengine_config.get_ai_config()
        global_alert_config = obsengine_config.get_alert_config()
        db_path_local = obsengine_config.get_db_path() # Ensure using the potentially updated path

        # For now, always use global config. Agent-specific config override is not implemented here yet.
        current_ai_config = global_ai_config
        current_alert_config = global_alert_config

        # Perform AI analysis (or rule-based fallback)
        logger.debug(f"[{full_pod_key}] Performing analysis (using global config)...")
        analysis_result, final_prompt, raw_response_text = ai_providers.perform_analysis(
            logs, k8s_context, "; ".join(initial_reasons), current_ai_config, current_ai_config.get('prompt_template')
        )
        logger.debug(f"[{full_pod_key}] Analysis complete.")

        # Extract results from analysis
        severity = analysis_result.get("severity", "UNKNOWN").upper()
        summary = analysis_result.get("summary", "N/A")
        root_cause_raw = analysis_result.get("root_cause", "N/A")
        steps_raw = analysis_result.get("troubleshooting_steps", "N/A")
        # Handle potential list format for root cause/steps
        root_cause = "\n".join(root_cause_raw) if isinstance(root_cause_raw, list) else str(root_cause_raw)
        steps = "\n".join(steps_raw) if isinstance(steps_raw, list) else str(steps_raw)

        logger.info(f"[{full_pod_key}] Analysis result: Severity={severity}")

        # Record the incident in the database
        logger.debug(f"[{full_pod_key}] Recording incident...")
        # Format sample logs for storage
        sample_logs_str = "\n".join([f"- {log.get('message', '')[:150]}" for log in logs[:5]]) if logs else "-"
        db_manager.record_incident(
            db_path_local, full_pod_key, severity, summary, "; ".join(initial_reasons),
            k8s_context, sample_logs_str, global_alert_config.get('alert_severity_levels', []), # Pass alert levels (though not used for counting anymore)
            final_prompt, raw_response_text, root_cause, steps
        )
        logger.debug(f"[{full_pod_key}] Incident recorded.")

        # Check if an alert needs to be sent based on severity and cooldown
        alert_levels = current_alert_config.get('alert_severity_levels', [])
        cooldown_minutes = current_alert_config.get('alert_cooldown_minutes', 30)
        logger.debug(f"[{full_pod_key}] Checking alert conditions (Severity: {severity}, Levels: {alert_levels})...")

        if severity in alert_levels:
            logger.debug(f"[{full_pod_key}] Checking cooldown...")
            if not db_manager.is_pod_in_cooldown(db_path_local, full_pod_key):
                logger.info(f"[{full_pod_key}] Not in cooldown. Processing alert.")
                # Check if Telegram alerts are enabled globally
                if global_alert_config.get('enable_telegram_alerts'):
                    bot_token = global_alert_config.get('telegram_bot_token')
                    chat_id = global_alert_config.get('telegram_chat_id')
                    if bot_token and chat_id:
                        logger.debug(f"[{full_pod_key}] Sending Telegram alert...")
                        # Format timestamp for display in local timezone (HCM)
                        alert_time_hcm = received_time.astimezone(getattr(notifier, 'HCM_TZ', timezone.utc))
                        time_format = '%Y-%m-%d %H:%M:%S %Z'
                        alert_data = {
                            'pod_key': full_pod_key, 'cluster_name': cluster_name,
                            'severity': severity, 'summary': summary, 'root_cause': root_cause,
                            'troubleshooting_steps': steps, 'initial_reasons': "; ".join(initial_reasons),
                            'alert_time': alert_time_hcm.strftime(time_format), 'sample_logs': sample_logs_str
                        }
                        # Send the alert via the notifier module
                        alert_sent = notifier.send_telegram_alert(
                            bot_token, chat_id, alert_data, current_ai_config.get('enable_ai_analysis')
                        )
                        if alert_sent:
                            logger.debug(f"[{full_pod_key}] Alert sent. Setting cooldown...")
                            # Set cooldown only if alert was sent successfully
                            db_manager.set_pod_cooldown(db_path_local, full_pod_key, cooldown_minutes)
                        else:
                            logger.warning(f"[{full_pod_key}] Telegram alert sending failed, cooldown NOT set.")
                    else:
                        # If enabled but token/chat_id missing, log warning and set cooldown anyway to prevent spam if fixed later
                        logger.warning(f"[{full_pod_key}] Telegram alerts enabled but token/chat_id missing. Setting cooldown anyway.")
                        db_manager.set_pod_cooldown(db_path_local, full_pod_key, cooldown_minutes)
                else:
                    # If Telegram alerts are disabled, still set cooldown to respect the setting
                    logger.info(f"[{full_pod_key}] Telegram alerts disabled. Setting cooldown.")
                    db_manager.set_pod_cooldown(db_path_local, full_pod_key, cooldown_minutes)
            else:
                # Pod is currently in cooldown period
                logger.info(f"[{full_pod_key}] Pod is in cooldown. Alert processing skipped.")
        else:
            # Severity level does not warrant an alert
            logger.info(f"[{full_pod_key}] Severity does not meet alert threshold. No alert needed.")

        logger.info(f"--- Successfully processed data for {full_pod_key} from Agent {agent_id} ---")
        return jsonify({"message": f"Data processed successfully for {full_pod_key}"}), 200

    except AttributeError as ae:
        # Catch potential errors if expected attributes are missing from data
        logger.error(f"--- AttributeError processing data for pod {full_pod_key} from Agent {agent_id}: {ae} ---", exc_info=True)
        return jsonify({"error": f"Internal server error (AttributeError) processing data for {full_pod_key}"}), 500
    except Exception as e:
        # Catch any other unexpected errors during processing
        logger.error(f"--- Error processing data for pod {full_pod_key} from Agent {agent_id}: {e} ---", exc_info=True)
        return jsonify({"error": f"Internal server error processing data for {full_pod_key}"}), 500

# API endpoint to get the status of active agents
@app.route('/api/agents/status', methods=['GET'])
def get_agent_status():
    logger.info("--- /api/agents/status endpoint hit ---")
    try:
        agent_timeout = 300 # Consider agents active if seen in the last 5 minutes
        active_agents = db_manager.get_active_agents(DB_PATH, agent_timeout)
        logger.info(f"Returning status for {len(active_agents)} active agents.")
        return jsonify({"active_agents": active_agents}), 200
    except Exception as e:
        logger.error(f"Error getting agent status: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve agent status"}), 500


# API endpoint to retrieve recorded incidents with filtering and pagination
@app.route('/api/incidents', methods=['GET'])
def get_incidents_api():
    logger.info("--- /api/incidents endpoint hit ---")
    try:
        # Get filter parameters from query string
        pod_filter = request.args.get('pod', default="", type=str).strip()
        severity_filter = request.args.get('severity', default="", type=str).upper().strip()
        start_date_str = request.args.get('start_date', default=None, type=str)
        end_date_str = request.args.get('end_date', default=None, type=str)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('limit', 20, type=int)
        offset = (page - 1) * per_page

        # Connect to the database
        conn = db_manager._get_db_connection(DB_PATH)
        if conn is None:
            return jsonify({"error": "Database connection failed."}), 500

        incidents = []
        total_count = 0
        try:
            cursor = conn.cursor()
            # Build the base query and parameters dynamically based on filters
            base_query = "FROM incidents WHERE 1=1"
            params = []
            if pod_filter:
                base_query += " AND pod_key LIKE ?"
                params.append(f"%{pod_filter}%")
            if severity_filter:
                base_query += " AND severity = ?"
                params.append(severity_filter)
            if start_date_str:
                try:
                    # Convert date string to UTC ISO format for comparison
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

            # Query for the total count matching the filters
            count_query = f"SELECT COUNT(*) as count {base_query}"
            cursor.execute(count_query, tuple(params))
            count_result = cursor.fetchone()
            total_count = count_result['count'] if count_result else 0

            # Query for the actual incident data with pagination
            data_query = f"SELECT id, timestamp, pod_key, severity, summary, initial_reasons, k8s_context, sample_logs, input_prompt, raw_ai_response, root_cause, troubleshooting_steps {base_query} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params_data = params + [per_page, offset]
            cursor.execute(data_query, tuple(params_data))
            rows = cursor.fetchall()
            incidents = [dict(row) for row in rows] # Convert rows to dictionaries

        except sqlite3.Error as db_err:
            logger.error(f"Database error fetching incidents: {db_err}", exc_info=True)
            return jsonify({"error": f"Database error: {db_err}"}), 500
        finally:
            if conn: conn.close() # Ensure connection is closed

        # Calculate pagination details
        total_pages = math.ceil(total_count / per_page) if per_page > 0 else 0
        pagination = {"page": page, "per_page": per_page, "total_items": total_count, "total_pages": total_pages}
        logger.info(f"Returning {len(incidents)} incidents (Page {page}/{total_pages})")
        return jsonify({"incidents": incidents, "pagination": pagination})
    except Exception as e:
        logger.error(f"Error in /api/incidents: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve incidents"}), 500


# API endpoint to get statistical data for the dashboard
@app.route('/api/stats', methods=['GET'])
def get_stats_api():
    logger.info("--- /api/stats endpoint hit ---")
    try:
        # Get the desired time range (1, 7, or 30 days)
        days = request.args.get('days', 1, type=int)
        if days not in [1, 7, 30]: days = 1 # Default to 1 day if invalid
        logger.debug(f"Calculating stats for last {days} days.")

        # Calculate date ranges for queries
        end_date_utc = datetime.now(timezone.utc)
        start_date_utc = end_date_utc - timedelta(days=days)
        # Ensure chart data always covers at least 7 days for better visualization
        chart_days = max(days, 7)
        chart_start_date_utc = end_date_utc - timedelta(days=chart_days)

        conn = db_manager._get_db_connection(DB_PATH)
        if conn is None: return jsonify({"error": "Database connection failed."}), 500

        stats = {}
        try:
            cursor = conn.cursor()
            # Get daily stats for the line chart (covering at least 7 days)
            cursor.execute('SELECT date, model_calls, telegram_alerts, incident_count FROM daily_stats WHERE date >= ? AND date <= ? ORDER BY date ASC',
                           (chart_start_date_utc.strftime('%Y-%m-%d'), end_date_utc.strftime('%Y-%m-%d')))
            daily_stats_rows = cursor.fetchall()
            stats['daily_stats_for_chart'] = [dict(row) for row in daily_stats_rows]

            # Get total counts for the selected period (1, 7, or 30 days)
            cursor.execute('SELECT SUM(model_calls) as total_model_calls, SUM(telegram_alerts) as total_telegram_alerts, SUM(incident_count) as total_incidents FROM daily_stats WHERE date >= ? AND date <= ?',
                           (start_date_utc.strftime('%Y-%m-%d'), end_date_utc.strftime('%Y-%m-%d')))
            totals_row = cursor.fetchone()
            stats['totals'] = {
                "model_calls": totals_row['total_model_calls'] or 0,
                "telegram_alerts": totals_row['total_telegram_alerts'] or 0,
                "incidents": totals_row['total_incidents'] or 0,
            }

            # Get severity distribution for today only
            today_start_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            today_end_utc = today_start_utc + timedelta(days=1) - timedelta(microseconds=1)
            cursor.execute('SELECT severity, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? GROUP BY severity',
                           (today_start_utc.isoformat(), today_end_utc.isoformat()))
            severity_rows_today = cursor.fetchall()
            stats['severity_distribution_today'] = {row['severity']: row['count'] for row in severity_rows_today if row['severity']}

            # Get top 5 problematic pods for the selected period
            cursor.execute('SELECT pod_key, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? GROUP BY pod_key ORDER BY count DESC LIMIT 5',
                           (start_date_utc.isoformat(), end_date_utc.isoformat()))
            top_pods_rows = cursor.fetchall()
            stats['top_problematic_pods'] = {row['pod_key']: row['count'] for row in top_pods_rows}

            # Get incident count by namespace for the selected period (extract namespace from pod_key)
            # This query assumes pod_key format: cluster/namespace/pod
            cursor.execute('''
                SELECT
                    CASE
                        WHEN INSTR(pod_key, '/') > 0 THEN SUBSTR(pod_key, INSTR(pod_key, '/') + 1, INSTR(SUBSTR(pod_key, INSTR(pod_key, '/') + 1), '/') - 1)
                        ELSE NULL
                    END as namespace,
                    COUNT(*) as count
                FROM incidents
                WHERE timestamp >= ? AND timestamp <= ?
                  AND INSTR(pod_key, '/') > 0
                  AND INSTR(SUBSTR(pod_key, INSTR(pod_key, '/') + 1), '/') > 0
                GROUP BY namespace
                ORDER BY count DESC
            ''', (start_date_utc.isoformat(), end_date_utc.isoformat()))
            namespace_rows = cursor.fetchall()
            stats['namespace_distribution'] = {row['namespace']: row['count'] for row in namespace_rows if row['namespace']}

        except sqlite3.Error as db_err:
            logger.error(f"Database error fetching stats: {db_err}", exc_info=True)
            return jsonify({"error": f"Database error: {db_err}"}), 500
        finally:
            if conn: conn.close()

        logger.info(f"Returning stats data for last {days} days.")
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error in /api/stats: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve stats"}), 500


# API endpoint to get all non-sensitive global configurations
@app.route('/api/config/all', methods=['GET'])
def get_all_config_api():
    logger.info("--- GET /api/config/all endpoint hit ---")
    try:
        # Load config from DB
        current_config = db_manager.load_all_global_config(DB_PATH)
        # Load sensitive keys from environment variables (higher priority)
        current_config['ai_api_key'] = obsengine_config._get_env_var("GEMINI_API_KEY", "")
        current_config['telegram_bot_token'] = obsengine_config._get_env_var("TELEGRAM_BOT_TOKEN", "")

        # Exclude sensitive keys before returning
        sensitive_keys_to_exclude = ['ai_api_key', 'telegram_bot_token']
        safe_config = {k: v for k, v in current_config.items() if k not in sensitive_keys_to_exclude}

        # Process alert levels string into a list for easier frontend use
        alert_levels_str = safe_config.get('alert_severity_levels')
        if alert_levels_str:
            safe_config['alert_severity_levels_str'] = alert_levels_str # Keep the original string
            try:
                safe_config['alert_severity_levels'] = [lvl.strip().upper() for lvl in alert_levels_str.split(',') if lvl.strip()]
            except Exception:
                safe_config['alert_severity_levels'] = [] # Fallback to empty list on error
        else:
            safe_config['alert_severity_levels'] = []

        # Add placeholder for monitored namespaces (handled by agent config now)
        safe_config['monitored_namespaces'] = []

        logger.info("Returning non-sensitive GLOBAL configuration.")
        return jsonify(safe_config), 200
    except Exception as e:
        logger.error(f"Error getting all global config: {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve global configuration"}), 500


# API endpoint to get available Kubernetes namespaces
@app.route('/api/namespaces', methods=['GET'])
def get_available_namespaces_api():
    logger.info("--- /api/namespaces endpoint hit ---")
    namespaces = []
    # Try fetching directly from K8s API if client is initialized
    if k8s_client_initialized_obs and k8s_core_v1_obs:
        try:
            # Get excluded namespaces from environment variable
            excluded_namespaces_str = obsengine_config._get_env_var("EXCLUDED_NAMESPACES", "kube-node-lease,kube-public")
            excluded_namespaces = {ns.strip() for ns in excluded_namespaces_str.split(',') if ns.strip()}

            # List all namespaces
            all_namespaces = k8s_core_v1_obs.list_namespace(watch=False, timeout_seconds=15)

            # Filter active and non-excluded namespaces
            if hasattr(all_namespaces, 'items') and all_namespaces.items is not None:
                 for ns in all_namespaces.items:
                     if ns.status.phase == "Active" and ns.metadata.name not in excluded_namespaces:
                         namespaces.append(ns.metadata.name)
            else:
                 logger.warning("K8s API response for namespaces did not contain 'items'.")

            namespaces.sort()
            logger.info(f"Fetched {len(namespaces)} namespaces directly from K8s API.")
            # Update the DB cache asynchronously (optional, could be done in background thread)
            # threading.Thread(target=db_manager.update_available_namespaces_in_db, args=(DB_PATH, namespaces)).start()
            return jsonify(namespaces), 200
        except ApiException as e:
            # Log API errors but fall back to DB cache
            logger.warning(f"K8s API error fetching namespaces: {e.status} {e.reason}. Falling back to DB cache.")
        except Exception as e:
            # Log other errors but fall back to DB cache
            logger.warning(f"Unexpected error fetching namespaces from K8s API: {e}. Falling back to DB cache.", exc_info=True)

    # Fallback: Fetch namespaces from the database cache
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


# API endpoint to save global AI configuration
@app.route('/api/config/ai', methods=['POST'])
def save_global_ai_config_api():
    logger.info("--- POST /api/config/ai (Global) endpoint hit ---")
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json(); logger.debug(f"Received global AI config data: {data}")

    config_to_save = {}
    validation_errors = []
    valid_providers = ['gemini', 'local', 'openai', 'groq', 'deepseek', 'none']

    # Validate 'enable_ai_analysis'
    enable_ai = data.get('enable_ai_analysis')
    if not isinstance(enable_ai, bool):
        validation_errors.append("'enable_ai_analysis' must be a boolean")
    else:
        config_to_save['enable_ai_analysis'] = str(enable_ai).lower() # Store as 'true' or 'false'

    # Validate 'ai_provider'
    provider = data.get('ai_provider', obsengine_config.DEFAULT_AI_PROVIDER).lower()
    if provider not in valid_providers:
        validation_errors.append(f"Invalid AI provider: {provider}")
    else:
        config_to_save['ai_provider'] = provider

    # Save 'ai_model_identifier' (no strict validation here)
    model_id = data.get('ai_model_identifier', '')
    config_to_save['ai_model_identifier'] = model_id.strip()

    # Handle API Key (only save if provided, don't validate content)
    # Sensitive keys like API keys should ideally be managed via environment variables or secrets management,
    # but this allows updating via API if necessary.
    if 'ai_api_key' in data and data['ai_api_key']:
         # In a real application, encrypt this before saving or avoid saving it directly
         # config_to_save['ai_api_key'] = data['ai_api_key'] # Storing directly is NOT recommended
         logger.warning("Received AI API key via API. Storing keys in DB is not recommended. Use environment variables.")
         # For now, we won't save it to DB via this API to encourage better practices.
         # The obsengine_config module will prioritize env vars anyway.

    if validation_errors:
        logger.error(f"Validation failed for global AI config: {validation_errors}")
        return jsonify({"error": "Validation failed", "details": validation_errors}), 400

    # Save the validated non-sensitive config to the database
    success, message = db_manager.save_global_config(DB_PATH, config_to_save)

    if success:
        obsengine_config._last_config_refresh_time = 0 # Force config refresh on next request
        return jsonify({"message": message}), 200
    else:
        return jsonify({"error": message}), 500

# API endpoint to save global Telegram configuration
@app.route('/api/config/telegram', methods=['POST'])
def save_global_telegram_config_api():
    logger.info("--- POST /api/config/telegram (Global) endpoint hit ---")
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json(); logger.debug(f"Received global Telegram config data: {data}")

    config_to_save = {}
    validation_errors = []

    # Validate 'enable_telegram_alerts'
    enable_alerts = data.get('enable_telegram_alerts')
    if not isinstance(enable_alerts, bool):
        validation_errors.append("'enable_telegram_alerts' must be a boolean")
    else:
        config_to_save['enable_telegram_alerts'] = str(enable_alerts).lower() # Store as 'true' or 'false'

    # Validate 'telegram_chat_id'
    chat_id = data.get('telegram_chat_id', '').strip()
    if not chat_id:
        validation_errors.append("Telegram Chat ID cannot be empty")
    else:
        config_to_save['telegram_chat_id'] = chat_id

    # Handle Bot Token (only save if provided)
    if 'telegram_bot_token' in data and data['telegram_bot_token']:
         logger.warning("Received Telegram Bot Token via API. Storing tokens in DB is not recommended. Use environment variables.")
         # config_to_save['telegram_bot_token'] = data['telegram_bot_token'] # Storing directly is NOT recommended

    if validation_errors:
        logger.error(f"Validation failed for global Telegram config: {validation_errors}")
        return jsonify({"error": "Validation failed", "details": validation_errors}), 400

    # Save validated non-sensitive config to the database
    success, message = db_manager.save_global_config(DB_PATH, config_to_save)

    if success:
        obsengine_config._last_config_refresh_time = 0 # Force config refresh
        return jsonify({"message": message}), 200
    else:
        return jsonify({"error": message}), 500


# API endpoint to get the effective configuration for a specific agent
@app.route('/api/agents/<agent_id>/config', methods=['GET'])
def get_agent_config_api(agent_id):
    logger.info(f"--- GET /api/agents/{agent_id}/config endpoint hit ---")
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    try:
        # Load agent-specific settings from DB
        agent_config = db_manager.load_agent_config(DB_PATH, agent_id)
        # Load global settings from DB (as defaults)
        global_config = db_manager.load_all_global_config(DB_PATH)

        # Merge configurations: Agent settings override global settings
        merged_config = {
            # Start with global defaults or hardcoded defaults
            'scan_interval_seconds': int(global_config.get('scan_interval_seconds', getattr(obsengine_config, 'DEFAULT_SCAN_INTERVAL_SECONDS', 30))),
            'restart_count_threshold': int(global_config.get('restart_count_threshold', getattr(obsengine_config, 'DEFAULT_RESTART_COUNT_THRESHOLD', 5))),
            'loki_scan_min_level': global_config.get('loki_scan_min_level', getattr(obsengine_config, 'DEFAULT_LOKI_SCAN_MIN_LEVEL', 'INFO')).upper(),
            'monitored_namespaces': [], # Default to empty list
        }

        # Override with agent-specific settings if they exist and are valid
        if 'scan_interval_seconds' in agent_config:
            try: merged_config['scan_interval_seconds'] = int(agent_config['scan_interval_seconds'])
            except (ValueError, TypeError): logger.warning(f"Invalid scan_interval_seconds '{agent_config['scan_interval_seconds']}' for agent {agent_id}. Using global/default."); pass
        if 'restart_count_threshold' in agent_config:
            try: merged_config['restart_count_threshold'] = int(agent_config['restart_count_threshold'])
            except (ValueError, TypeError): logger.warning(f"Invalid restart_count_threshold '{agent_config['restart_count_threshold']}' for agent {agent_id}. Using global/default."); pass
        if 'loki_scan_min_level' in agent_config:
             merged_config['loki_scan_min_level'] = agent_config['loki_scan_min_level'].upper() # Assume agent value is valid string

        # Load and parse monitored namespaces (stored as JSON string)
        ns_json_string = agent_config.get('monitored_namespaces')
        if ns_json_string:
            try:
                merged_config['monitored_namespaces'] = json.loads(ns_json_string)
                # Ensure it's a list after parsing
                if not isinstance(merged_config['monitored_namespaces'], list):
                    logger.warning(f"Decoded monitored_namespaces for agent {agent_id} is not a list. Resetting to empty.")
                    merged_config['monitored_namespaces'] = []
            except json.JSONDecodeError:
                logger.warning(f"Could not decode monitored_namespaces JSON for agent {agent_id}: {ns_json_string}. Resetting to empty.")
                merged_config['monitored_namespaces'] = []

        logger.info(f"Returning merged configuration for agent {agent_id}.")
        return jsonify(merged_config), 200
    except Exception as e:
        logger.error(f"Error getting config for agent {agent_id}: {e}", exc_info=True)
        return jsonify({"error": f"Failed to retrieve configuration for agent {agent_id}"}), 500

# API endpoint to save general configuration settings for a specific agent
@app.route('/api/agents/<agent_id>/config/general', methods=['POST'])
def save_agent_general_config_api(agent_id):
    logger.info(f"--- POST /api/agents/{agent_id}/config/general endpoint hit ---")
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json(); logger.debug(f"Received general config data for agent {agent_id}: {data}")

    config_to_save = {}
    validation_errors = []
    valid_log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]

    try:
        # Validate and prepare scan_interval_seconds
        if 'scan_interval_seconds' in data:
            scan_interval = int(data['scan_interval_seconds'])
            if scan_interval < 10: validation_errors.append("Scan interval must be >= 10")
            else: config_to_save['scan_interval_seconds'] = str(scan_interval) # Store as string

        # Validate and prepare restart_count_threshold
        if 'restart_count_threshold' in data:
            restart_threshold = int(data['restart_count_threshold'])
            if restart_threshold < 1: validation_errors.append("Restart threshold must be >= 1")
            else: config_to_save['restart_count_threshold'] = str(restart_threshold) # Store as string

        # Validate and prepare loki_scan_min_level
        if 'loki_scan_min_level' in data:
            scan_level = data['loki_scan_min_level'].upper()
            if scan_level not in valid_log_levels: validation_errors.append(f"Invalid Loki scan level: {scan_level}")
            else: config_to_save['loki_scan_min_level'] = scan_level

    except (ValueError, TypeError) as e:
        # Catch errors if input cannot be converted to int
        logger.error(f"Validation error processing general config for agent {agent_id}: {e}")
        return jsonify({"error": f"Invalid input data type: {e}"}), 400

    if validation_errors:
        logger.error(f"Validation failed for agent {agent_id} general config: {validation_errors}")
        return jsonify({"error": "Validation failed", "details": validation_errors}), 400

    # Only save if there are valid settings to save
    if not config_to_save:
        return jsonify({"message": "No general settings provided to save."}), 200

    # Save the agent-specific configuration to the database
    success, message = db_manager.save_agent_config(DB_PATH, agent_id, config_to_save)
    if success:
        return jsonify({"message": message}), 200
    else:
        return jsonify({"error": message}), 500

# API endpoint to save the list of monitored namespaces for a specific agent
@app.route('/api/agents/<agent_id>/config/namespaces', methods=['POST'])
def save_agent_namespaces_api(agent_id):
    logger.info(f"--- POST /api/agents/{agent_id}/config/namespaces endpoint hit ---")
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json(); logger.debug(f"Received monitored namespaces data for agent {agent_id}: {data}")

    namespaces = data.get('namespaces')
    # Validate that 'namespaces' is a list
    if not isinstance(namespaces, list):
        return jsonify({"error": "'namespaces' must be a list"}), 400

    # Clean and prepare the list for saving (store as JSON string)
    cleaned_namespaces = [str(ns).strip() for ns in namespaces if isinstance(ns, str) and str(ns).strip()]
    value_to_save = json.dumps(cleaned_namespaces)
    config_to_save = {'monitored_namespaces': value_to_save}

    # Save the agent-specific configuration
    success, message = db_manager.save_agent_config(DB_PATH, agent_id, config_to_save)
    if success:
        return jsonify({"message": message}), 200
    else:
        return jsonify({"error": message}), 500

# Helper function to parse Kubernetes resource quantity strings (e.g., "1Gi", "500m")
def _parse_resource_quantity(quantity_str):
    """Parses a Kubernetes resource quantity string (e.g., '1Gi', '500m') into a Decimal."""
    if not quantity_str:
        return 0 # Return 0 if the string is empty or None
    try:
        # Use the imported parse_quantity function
        return parse_quantity(quantity_str)
    except Exception as e:
        # Log a warning if parsing fails and return 0
        logger.warning(f"Could not parse resource quantity: {quantity_str}. Error: {e}")
        return 0

# API endpoint to get general information about the Kubernetes cluster
@app.route('/api/cluster/info', methods=['GET'])
def get_cluster_info_api():
    logger.info("--- /api/cluster/info endpoint hit ---")
    # Check if the K8s client was initialized successfully
    if not k8s_client_initialized_obs:
        logger.error("K8s client not initialized in ObsEngine. Cannot fetch cluster info.")
        return jsonify({"error": "Kubernetes client not available"}), 503

    # Default structure for the response
    cluster_info = {
        "kubernetes_version": "N/A",
        "platform": "N/A",
        "total_nodes": 0,
        "total_cpu_capacity": "N/A",
        "total_memory_capacity": "N/A",
        "os_image_sample": "N/A",
        "kernel_version_sample": "N/A",
        "kubelet_version_sample": "N/A"
    }

    try:
        # --- Get Kubernetes version info ---
        try:
            if k8s_version_api_obs:
                version_info = k8s_version_api_obs.get_code()
                cluster_info["kubernetes_version"] = version_info.git_version or "N/A"
                cluster_info["platform"] = version_info.platform or "N/A"
                logger.debug(f"Fetched K8s version: {cluster_info['kubernetes_version']}")
            else:
                logger.warning("K8s Version API client not available.")
        except ApiException as e:
             logger.error(f"K8s API error fetching version info: {e.status} {e.reason}", exc_info=True)
             # Allow continuing to fetch node info even if version fails
        except Exception as e:
             logger.error(f"Unexpected error fetching K8s version info: {e}", exc_info=True)

        # --- Get node information ---
        nodes = None
        try:
            if k8s_core_v1_obs:
                nodes = k8s_core_v1_obs.list_node(timeout_seconds=30)
                cluster_info["total_nodes"] = len(nodes.items) if nodes and nodes.items else 0
                logger.debug(f"Fetched {cluster_info['total_nodes']} nodes.")
            else:
                logger.warning("K8s Core V1 API client not available for node info.")
        except ApiException as e:
             logger.error(f"K8s API error listing nodes: {e.status} {e.reason}", exc_info=True)
             # Return potentially partial info if node listing fails
             return jsonify(cluster_info), 200
        except Exception as e:
             logger.error(f"Unexpected error listing nodes: {e}", exc_info=True)
             # Return potentially partial info
             return jsonify(cluster_info), 200

        # --- Process node information ---
        if nodes and nodes.items:
            total_cpu = 0
            total_memory_bytes = 0
            os_images = {}
            kernel_versions = {}
            kubelet_versions = {}

            # Iterate through each node to aggregate capacity and sample info
            for node in nodes.items:
                node_name = node.metadata.name if node.metadata else "unknown-node"
                logger.debug(f"Processing node: {node_name}")
                try:
                    # Sum up CPU and Memory capacity
                    if node.status and node.status.capacity:
                        cpu_str = node.status.capacity.get('cpu')
                        mem_str = node.status.capacity.get('memory')
                        logger.debug(f"  Node {node_name}: Raw capacity CPU='{cpu_str}', Mem='{mem_str}'")
                        # Use the fixed _parse_resource_quantity helper
                        parsed_cpu = _parse_resource_quantity(cpu_str)
                        parsed_mem = _parse_resource_quantity(mem_str)
                        logger.debug(f"  Node {node_name}: Parsed capacity CPU={parsed_cpu}, Mem={parsed_mem}")
                        total_cpu += parsed_cpu
                        total_memory_bytes += parsed_mem
                    else:
                        logger.warning(f"Node {node_name} missing status or capacity info.")

                    # Collect samples of OS, Kernel, Kubelet versions
                    if node.status and node.status.node_info:
                        os_img = node.status.node_info.os_image
                        kernel_v = node.status.node_info.kernel_version
                        kubelet_v = node.status.node_info.kubelet_version
                        # Count occurrences of each version
                        if os_img: os_images[os_img] = os_images.get(os_img, 0) + 1
                        if kernel_v: kernel_versions[kernel_v] = kernel_versions.get(kernel_v, 0) + 1
                        if kubelet_v: kubelet_versions[kubelet_v] = kubelet_versions.get(kubelet_v, 0) + 1
                    else:
                        logger.warning(f"Node {node_name} missing status or node_info.")
                except Exception as node_proc_err:
                    # Log error processing a specific node but continue with others
                    logger.error(f"Error processing node {node_name}: {node_proc_err}", exc_info=True)

            # Format total CPU and Memory
            cluster_info["total_cpu_capacity"] = f"{total_cpu:.2f}" if total_cpu else "N/A"

            # --- Dòng 902 ĐÃ SỬA ---
            # Use 'Gi' as a reasonable default unit for total cluster memory
            cluster_info["total_memory_capacity"] = format_quantity(total_memory_bytes, suffix='Gi') if total_memory_bytes else "N/A"
            # ------------------------

            # Find the most common OS, Kernel, Kubelet version as samples
            if os_images: cluster_info["os_image_sample"] = max(os_images, key=os_images.get)
            if kernel_versions: cluster_info["kernel_version_sample"] = max(kernel_versions, key=kernel_versions.get)
            if kubelet_versions: cluster_info["kubelet_version_sample"] = max(kubelet_versions, key=kubelet_versions.get)
            logger.debug(f"Aggregated node info: CPU={cluster_info['total_cpu_capacity']}, Mem={cluster_info['total_memory_capacity']}")

        return jsonify(cluster_info), 200

    except Exception as e:
        # Handle any other unexpected errors during the overall process
        logger.error(f"Unexpected error fetching cluster info: {e}", exc_info=True)
        # Return 500 only for truly unexpected errors in the main try block
        return jsonify({"error": "Failed to retrieve cluster information"}), 500


# Main execution block when the script is run directly
if __name__ == '__main__':
    # Get Flask port and debug mode from environment variables
    flask_port = int(os.environ.get("FLASK_PORT", 8080))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    log_level_name = logging.getLevelName(logging.getLogger().getEffectiveLevel())
    logger.info(f"Starting ObsEngine Flask server on 0.0.0.0:{flask_port} | Debug: {debug_mode} | Log Level: {log_level_name}")
    # Run the Flask application
    # use_reloader=debug_mode helps in development by automatically restarting the server on code changes
    app.run(host='0.0.0.0', port=flask_port, debug=debug_mode, use_reloader=debug_mode)

logger.info("--- ObsEngine app.py finished parsing (module level) ---")

