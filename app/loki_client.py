# ai-agent1/app/loki_client.py
import aiohttp # Sử dụng aiohttp thay cho requests
import json
import logging
import re
from datetime import datetime, timezone, timedelta
import asyncio # Import asyncio để dùng sleep

LOG_LEVELS = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
PROBLEM_KEYWORDS = ["FAIL", "ERROR", "CRASH", "EXCEPTION", "UNAVAILABLE", "FATAL", "PANIC", "TIMEOUT", "DENIED", "REFUSED", "UNABLE", "UNAUTHORIZED", "OOM", "KILL"]

def _parse_loki_response(response_data):
    log_entries = []
    if isinstance(response_data, dict) and 'data' in response_data and 'result' in response_data['data']:
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
    elif isinstance(response_data, dict) and response_data.get('status') == 'error':
         # Log lỗi từ Loki nếu có
         logging.error(f"[Loki Client] Loki API returned error: {response_data.get('message', 'Unknown error')}")
    else:
         logging.warning(f"[Loki Client] Unexpected Loki response format: {str(response_data)[:200]}")

    return log_entries

# Hàm async để gọi Loki API với retry
async def _make_loki_request_async(session, loki_api_endpoint, params, headers, timeout=90, max_retries=2, initial_delay=2):
    """Helper function to make async Loki requests with retry."""
    retries = 0
    delay = initial_delay
    last_exception = None

    while retries <= max_retries:
        try:
            logging.debug(f"Making async Loki request (Attempt {retries + 1}) to {loki_api_endpoint} with params: {params}")
            async with session.get(loki_api_endpoint, params=params, headers=headers, timeout=timeout) as response:
                # Check for non-OK status codes first
                if response.status >= 400:
                    # Raise for specific retryable errors, log others
                    if response.status in [429, 500, 502, 503, 504]:
                         response.raise_for_status() # Raise to trigger retry
                    else:
                         # Log non-retryable client/server errors and return None
                         error_text = await response.text()
                         logging.error(f"[Loki Client] HTTP error {response.status}: {error_text[:500]}")
                         return None # Indicate non-retryable error
                # If status is OK, parse JSON
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e: # Catch aiohttp specific errors and timeout
            last_exception = e
            retries += 1
            logging.warning(f"[Loki Client] Request error ({type(e).__name__}). Retrying ({retries}/{max_retries}) in {delay}s...")
            if retries > max_retries:
                logging.error(f"[Loki Client] Loki request failed after {max_retries} retries: {last_exception}")
                return None # Indicate failure after retries
            await asyncio.sleep(delay)
            delay *= 2 # Exponential backoff
        except json.JSONDecodeError as e:
             logging.error(f"[Loki Client] Error decoding Loki JSON response: {e}")
             return None # Return None on JSON decode error
        except Exception as e:
             logging.error(f"[Loki Client] Unexpected error during Loki request: {e}", exc_info=True)
             return None # Return None for other unexpected errors

    # Should only reach here if retries were exhausted
    logging.error(f"[Loki Client] Loki request failed definitively after retries: {last_exception}")
    return None

# Chuyển thành hàm async
async def scan_loki_for_suspicious_logs(session, loki_url, start_time, end_time, namespaces_to_scan, loki_scan_min_level):
    """Scans Loki for suspicious logs asynchronously."""
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
    logging.info(f"[Loki Client] Scanning Loki (Level >= {loki_scan_min_level} or keywords) in {len(namespaces_to_scan)} namespaces (async)...")
    logging.debug(f"[Loki Client] Scan LogQL Query: {logql_query}")
    logging.debug(f"[Loki Client] Scan Time Range: {start_time.isoformat()} to {end_time.isoformat()}")

    suspicious_logs_by_pod = {}
    headers = {'Accept': 'application/json'}
    # Sử dụng hàm request async với retry
    data = await _make_loki_request_async(session, loki_api_endpoint, params, headers)

    if data:
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

# Chuyển thành hàm async
async def query_loki_for_pod(session, loki_url, namespace, pod_name, start_time, end_time, query_limit):
    """Queries detailed logs for a specific pod asynchronously."""
    loki_api_endpoint = f"{loki_url}/loki/api/v1/query_range"
    logql_query = f'{{namespace="{namespace}", pod="{pod_name}"}}'
    params = {
        'query': logql_query,
        'start': int(start_time.timestamp() * 1e9),
        'end': int(end_time.timestamp() * 1e9),
        'limit': query_limit,
        'direction': 'forward'
    }
    logging.info(f"[Loki Client] Querying detailed logs for pod '{namespace}/{pod_name}' from {start_time.isoformat()} to {end_time.isoformat()} (async)")
    logging.debug(f"[Loki Client] Detail LogQL Query: {logql_query}")

    headers = {'Accept': 'application/json'}
    data = await _make_loki_request_async(session, loki_api_endpoint, params, headers, timeout=60)

    log_entries = []
    if data:
        parsed_entries = _parse_loki_response(data)
        filtered_entries = [entry for entry in parsed_entries if start_time <= entry['timestamp'] <= end_time]
        filtered_entries.sort(key=lambda x: x.get('timestamp', datetime.min.replace(tzinfo=timezone.utc)))
        log_entries = filtered_entries

    logging.info(f"[Loki Client] Received {len(log_entries)} detailed log entries from Loki for pod '{namespace}/{pod_name}'.")
    return log_entries

