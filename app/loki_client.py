# ai-agent1/app/loki_client.py
import requests
import json
import logging
import re
from datetime import datetime, timezone

def scan_loki_for_suspicious_logs(loki_url, start_time, end_time, namespaces_to_scan, loki_scan_min_level):
    """Scans Loki for logs matching keywords or minimum severity level."""
    loki_api_endpoint = f"{loki_url}/loki/api/v1/query_range"
    if not namespaces_to_scan:
        logging.info("[Loki Client] No namespaces to scan in Loki.")
        return {}

    log_levels_all = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
    scan_level_index = -1
    try:
        scan_level_index = log_levels_all.index(loki_scan_min_level.upper())
    except ValueError:
        logging.warning(f"[Loki Client] Invalid LOKI_SCAN_MIN_LEVEL: {loki_scan_min_level}. Defaulting to WARNING.")
        scan_level_index = log_levels_all.index("WARNING")
    levels_to_scan = log_levels_all[scan_level_index:]

    namespace_regex = "|".join(namespaces_to_scan)
    keywords_to_find = levels_to_scan + ["fail", "crash", "exception", "panic", "fatal", "timeout", "denied", "refused", "unable", "unauthorized", "error"]
    escaped_keywords = [re.escape(k) for k in sorted(list(set(keywords_to_find)), key=len, reverse=True)]
    regex_pattern = "(?i)(" + "|".join(escaped_keywords) + ")"
    logql_query = f'{{namespace=~"{namespace_regex}"}} |~ `{regex_pattern}`'
    query_limit_scan = 2000

    params = {
        'query': logql_query,
        'start': int(start_time.timestamp() * 1e9),
        'end': int(end_time.timestamp() * 1e9),
        'limit': query_limit_scan,
        'direction': 'forward'
    }
    logging.info(f"[Loki Client] Scanning Loki (Level >= {loki_scan_min_level} or keywords) in {len(namespaces_to_scan)} namespaces...")
    suspicious_logs_by_pod = {}
    try:
        headers = {'Accept': 'application/json'}
        response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        if 'data' in data and 'result' in data['data']:
            count = 0
            for stream in data['data']['result']:
                labels = stream.get('stream', {})
                ns = labels.get('namespace')
                pod_name = labels.get('pod')
                if not ns or not pod_name: continue
                pod_key = f"{ns}/{pod_name}"
                if pod_key not in suspicious_logs_by_pod: suspicious_logs_by_pod[pod_key] = []
                for timestamp_ns, log_line in stream['values']:
                    log_entry = {
                        "timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc),
                        "message": log_line.strip(),
                        "labels": labels
                    }
                    suspicious_logs_by_pod[pod_key].append(log_entry)
                    count += 1
            logging.info(f"[Loki Client] Loki scan found {count} suspicious log entries across {len(suspicious_logs_by_pod)} pods.")
        else:
            logging.info("[Loki Client] Loki scan found no suspicious log entries matching the criteria.")
        return suspicious_logs_by_pod
    except requests.exceptions.HTTPError as e:
        error_detail = ""
        try: error_detail = e.response.text[:500]
        except Exception: pass
        logging.error(f"[Loki Client] Error scanning Loki (HTTP {e.response.status_code}): {e}. Response: {error_detail}")
        return {}
    except requests.exceptions.RequestException as e:
        logging.error(f"[Loki Client] Error scanning Loki (Request failed): {e}")
        return {}
    except json.JSONDecodeError as e:
        logging.error(f"[Loki Client] Error decoding Loki scan response (Invalid JSON): {e}")
        return {}
    except Exception as e:
        logging.error(f"[Loki Client] Unexpected error during Loki scan: {e}", exc_info=True)
        return {}

def query_loki_for_pod(loki_url, namespace, pod_name, start_time, end_time, query_limit):
    """Queries Loki for detailed logs of a specific pod within a time range."""
    loki_api_endpoint = f"{loki_url}/loki/api/v1/query_range"
    logql_query = f'{{namespace="{namespace}", pod="{pod_name}"}}'
    params = {
        'query': logql_query,
        'start': int(start_time.timestamp() * 1e9),
        'end': int(end_time.timestamp() * 1e9),
        'limit': query_limit,
        'direction': 'forward'
    }
    logging.info(f"[Loki Client] Querying Loki for pod '{namespace}/{pod_name}' from {start_time} to {end_time}")
    try:
        headers = {'Accept': 'application/json'}
        response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=45)
        response.raise_for_status()
        data = response.json()
        if 'data' in data and 'result' in data['data']:
            log_entries = []
            for stream in data['data']['result']:
                stream_labels = stream.get('stream', {})
                for timestamp_ns, log_line in stream['values']:
                    log_entries.append({
                        "timestamp": datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc),
                        "message": log_line.strip(),
                        "labels": stream_labels
                    })
            log_entries.sort(key=lambda x: x['timestamp'])
            logging.info(f"[Loki Client] Received {len(log_entries)} log entries from Loki for pod '{namespace}/{pod_name}'.")
            return log_entries
        else:
            logging.warning(f"[Loki Client] No 'result' data found in Loki response for pod '{namespace}/{pod_name}'.")
            return []
    except requests.exceptions.RequestException as e:
        logging.error(f"[Loki Client] Error querying Loki for pod '{namespace}/{pod_name}': {e}")
        return []
    except json.JSONDecodeError as e:
        logging.error(f"[Loki Client] Error decoding Loki JSON response for pod '{namespace}/{pod_name}': {e}")
        return []
    except Exception as e:
        logging.error(f"[Loki Client] Unexpected error querying Loki for pod '{namespace}/{pod_name}': {e}", exc_info=True)
        return []

def preprocess_and_filter(log_entries, loki_scan_min_level):
    """Filters log entries based on severity level or keywords."""
    filtered_logs = []
    log_levels = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
    min_level_index = -1
    try:
        min_level_index = log_levels.index(loki_scan_min_level.upper())
    except ValueError:
        logging.warning(f"[Loki Client] Invalid LOKI_SCAN_MIN_LEVEL: {loki_scan_min_level}. Defaulting to WARNING.")
        min_level_index = log_levels.index("WARNING")
    keywords_indicating_problem = ["FAIL", "ERROR", "CRASH", "EXCEPTION", "UNAVAILABLE", "FATAL", "PANIC", "TIMEOUT", "DENIED", "REFUSED", "UNABLE", "UNAUTHORIZED"]

    for entry in log_entries:
        log_line = entry['message']
        log_line_upper = log_line.upper()
        level_detected = False
        for i, level in enumerate(log_levels):
            if f" {level} " in f" {log_line_upper} " or \
               log_line_upper.startswith(level+":") or \
               f"[{level}]" in log_line_upper or \
               f"level={level.lower()}" in log_line_upper or \
               f"\"level\":\"{level.lower()}\"" in log_line_upper:
                if i >= min_level_index:
                    filtered_logs.append(entry)
                    level_detected = True
                    break
        if not level_detected:
            if any(keyword in log_line_upper for keyword in keywords_indicating_problem):
                    warning_index = log_levels.index("WARNING")
                    if min_level_index <= warning_index:
                        filtered_logs.append(entry)
    logging.info(f"[Loki Client] Filtered {len(log_entries)} logs down to {len(filtered_logs)} relevant logs (Scan Level: {loki_scan_min_level}).")
    return filtered_logs

