# ai-agent1/app/loki_client.py
import requests
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from retry import retry

LOG_LEVELS = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
PROBLEM_KEYWORDS = ["FAIL", "ERROR", "CRASH", "EXCEPTION", "UNAVAILABLE", "FATAL", "PANIC", "TIMEOUT", "DENIED", "REFUSED", "UNABLE", "UNAUTHORIZED", "OOM", "KILL"]

def _parse_loki_response(response_data):
    log_entries = []
    if 'data' in response_data and 'result' in response_data['data']:
        for stream in response_data['data']['result']:
            stream_labels = stream.get('stream', {})
            for value_pair in stream.get('values', []):
                 if not isinstance(value_pair, (list, tuple)) or len(value_pair) != 2:
                     logging.warning(f"Invalid log entry format: {value_pair}. Skipping.")
                     continue
                 timestamp_ns, log_line = value_pair
                 try:
                    ts_seconds = int(timestamp_ns) / 1e9
                    ts = datetime.fromtimestamp(ts_seconds, tz=timezone.utc)
                    log_entries.append({
                        "timestamp": ts,
                        "message": log_line.strip(),
                        "labels": stream_labels
                    })
                 except (ValueError, TypeError) as e:
                    logging.warning(f"Could not parse timestamp '{timestamp_ns}' or log line: {e}. Log: {log_line[:100]}")
                    continue
    return log_entries

@retry(tries=3, delay=2, backoff=2, exceptions=(requests.exceptions.RequestException, requests.exceptions.HTTPError))
def _make_loki_request(loki_api_endpoint, params, headers, timeout=90):
    logging.debug(f"Making Loki request to {loki_api_endpoint} with params: {params}")
    response = requests.get(loki_api_endpoint, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response

def scan_loki_for_suspicious_logs(loki_url, start_time, end_time, namespaces_to_scan, loki_scan_min_level):
    loki_api_endpoint = f"{loki_url}/loki/api/v1/query_range"
    if not namespaces_to_scan:
        logging.info("[Loki Client] No namespaces provided for Loki scan.")
        return {}
    try:
        scan_level_index = LOG_LEVELS.index(loki_scan_min_level.upper())
    except ValueError:
        logging.warning(f"[Loki Client] Invalid LOKI_SCAN_MIN_LEVEL: {loki_scan_min_level}. Defaulting to WARNING.")
        scan_level_index = LOG_LEVELS.index("WARNING")
    levels_to_scan = LOG_LEVELS[scan_level_index:]
    keywords_to_find = sorted(list(set(levels_to_scan + PROBLEM_KEYWORDS)), key=len, reverse=True)
    escaped_keywords = [re.escape(k) for k in keywords_to_find]
    regex_pattern = "(?i)(" + "|".join(escaped_keywords) + ")"
    namespace_regex = "|".join(namespaces_to_scan)

    logql_query = f'{{namespace=~"{namespace_regex}"}} |~ `{regex_pattern}`'
    query_limit_scan = 1000
    params = {
        'query': logql_query,
        'start': int(start_time.timestamp() * 1e9),
        'end': int(end_time.timestamp() * 1e9),
        'limit': query_limit_scan,
        'direction': 'forward'
    }
    logging.info(f"[Loki Client] Scanning Loki (Level >= {loki_scan_min_level} or keywords) in {len(namespaces_to_scan)} namespaces...")
    logging.debug(f"[Loki Client] Scan LogQL Query: {logql_query}")
    logging.debug(f"[Loki Client] Scan Time Range: {start_time.isoformat()} to {end_time.isoformat()}")

    suspicious_logs_by_pod = {}
    try:
        headers = {'Accept': 'application/json'}
        response = _make_loki_request(loki_api_endpoint, params, headers, timeout=90)
        data = response.json()
        log_entries = _parse_loki_response(data)
        count = 0
        for entry in log_entries:
            labels = entry.get('labels', {})
            ns = labels.get('namespace')
            pod_name = labels.get('pod')
            if not ns or not pod_name:
                logging.debug(f"Skipping log entry with missing namespace/pod labels: {entry.get('message', '')[:100]}")
                continue
            if not (start_time <= entry['timestamp'] <= end_time):
                 logging.debug(f"Skipping log entry outside requested time range: {entry['timestamp']}")
                 continue

            pod_key = f"{ns}/{pod_name}"
            if pod_key not in suspicious_logs_by_pod:
                suspicious_logs_by_pod[pod_key] = []
            suspicious_logs_by_pod[pod_key].append(entry)
            count += 1
        if count > 0:
            logging.info(f"[Loki Client] Loki scan found {count} suspicious log entries across {len(suspicious_logs_by_pod)} pods.")
        else:
            logging.info("[Loki Client] Loki scan found no suspicious log entries matching the criteria.")
        for pod_key in suspicious_logs_by_pod:
             suspicious_logs_by_pod[pod_key].sort(key=lambda x: x.get('timestamp', datetime.min.replace(tzinfo=timezone.utc)))
        return suspicious_logs_by_pod
    except requests.exceptions.HTTPError as e:
        error_detail = ""
        try: error_detail = e.response.text[:500] + ('...' if len(e.response.text) > 500 else '')
        except Exception: pass
        logging.error(f"[Loki Client] Error scanning Loki (HTTP {e.response.status_code}) after retries: {e}. Response: {error_detail}")
        return {}
    except requests.exceptions.RequestException as e:
        logging.error(f"[Loki Client] Error scanning Loki (Request failed) after retries: {e}")
        return {}
    except json.JSONDecodeError as e:
        logging.error(f"[Loki Client] Error decoding Loki scan response (Invalid JSON): {e}")
        return {}
    except Exception as e:
        logging.error(f"[Loki Client] Unexpected error during Loki scan: {e}", exc_info=True)
        return {}

@retry(tries=3, delay=1, backoff=2, exceptions=(requests.exceptions.RequestException, requests.exceptions.HTTPError))
def query_loki_for_pod(loki_url, namespace, pod_name, start_time, end_time, query_limit):
    loki_api_endpoint = f"{loki_url}/loki/api/v1/query_range"
    logql_query = f'{{namespace="{namespace}", pod="{pod_name}"}}'
    params = {
        'query': logql_query,
        'start': int(start_time.timestamp() * 1e9),
        'end': int(end_time.timestamp() * 1e9),
        'limit': query_limit,
        'direction': 'forward'
    }
    logging.info(f"[Loki Client] Querying detailed logs for pod '{namespace}/{pod_name}' from {start_time.isoformat()} to {end_time.isoformat()}")
    logging.debug(f"[Loki Client] Detail LogQL Query: {logql_query}")
    try:
        headers = {'Accept': 'application/json'}
        response = _make_loki_request(loki_api_endpoint, params, headers, timeout=60)
        data = response.json()
        log_entries = _parse_loki_response(data)
        filtered_entries = [entry for entry in log_entries if start_time <= entry['timestamp'] <= end_time]
        filtered_entries.sort(key=lambda x: x.get('timestamp', datetime.min.replace(tzinfo=timezone.utc)))
        logging.info(f"[Loki Client] Received {len(filtered_entries)} detailed log entries from Loki for pod '{namespace}/{pod_name}'.")
        return filtered_entries
    except requests.exceptions.HTTPError as e:
        error_detail = ""
        try: error_detail = e.response.text[:500] + ('...' if len(e.response.text) > 500 else '')
        except Exception: pass
        logging.error(f"[Loki Client] Error querying Loki for pod '{namespace}/{pod_name}' (HTTP {e.response.status_code}) after retries: {e}. Response: {error_detail}")
        return []
    except requests.exceptions.RequestException as e:
        logging.error(f"[Loki Client] Error querying Loki for pod '{namespace}/{pod_name}' (Request failed) after retries: {e}")
        return []
    except json.JSONDecodeError as e:
        logging.error(f"[Loki Client] Error decoding Loki JSON response for pod '{namespace}/{pod_name}': {e}")
        return []
    except Exception as e:
        logging.error(f"[Loki Client] Unexpected error querying Loki for pod '{namespace}/{pod_name}': {e}", exc_info=True)
        return []

