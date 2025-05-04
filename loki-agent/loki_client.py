# -*- coding: utf-8 -*-
import aiohttp
import json
import logging
import re
from datetime import datetime, timezone, timedelta
import asyncio
import random

logger = logging.getLogger(__name__)

def _parse_loki_response(response_data, query_identifier):
    """Phân tích phản hồi từ Loki API."""
    log_entries = []
    stream_labels = {} # Lưu trữ labels của stream đầu tiên tìm thấy

    if isinstance(response_data, dict) and 'data' in response_data and 'result' in response_data['data']:
        results = response_data['data']['result']
        if not results:
            logger.debug(f"Loki query '{query_identifier}' returned no results.")
            return log_entries, stream_labels # Trả về list rỗng và dict rỗng

        for stream in results:
            # Lấy label của stream đầu tiên làm đại diện (nếu có)
            if not stream_labels and isinstance(stream.get('stream'), dict):
                 stream_labels = stream.get('stream', {})

            for value_pair in stream.get('values', []):
                 if not isinstance(value_pair, (list, tuple)) or len(value_pair) != 2:
                     logger.warning(f"Invalid log entry format for '{query_identifier}': {value_pair}. Skipping.")
                     continue
                 timestamp_ns, log_line = value_pair
                 try:
                    # Chuyển đổi timestamp nano giây sang datetime UTC
                    ts_seconds = int(timestamp_ns) / 1e9
                    ts = datetime.fromtimestamp(ts_seconds, tz=timezone.utc)
                    log_entries.append({
                        "timestamp": ts.isoformat(), # Chuyển sang ISO format để gửi đi
                        "message": log_line.strip(),
                        # Không cần gửi kèm labels trong từng log entry nữa
                        # "labels": stream.get('stream', {})
                    })
                 except (ValueError, TypeError) as e:
                    logger.warning(f"Could not parse timestamp '{timestamp_ns}' or log line for '{query_identifier}': {e}. Log: {log_line[:100]}")
                    continue
    elif isinstance(response_data, dict) and response_data.get('status') == 'error':
         logger.error(f"[Loki Client] Loki API returned error for query '{query_identifier}': {response_data.get('message', 'Unknown error')}")
    else:
         logger.warning(f"[Loki Client] Unexpected Loki response format for query '{query_identifier}': {str(response_data)[:200]}")

    # Sắp xếp log theo thời gian tăng dần
    log_entries.sort(key=lambda x: x.get('timestamp', ''))
    return log_entries, stream_labels

async def _make_loki_request_async(session, loki_api_endpoint, params, headers, query_identifier, timeout=90, max_retries=2, initial_delay=2):
    """Hàm helper để thực hiện request Loki async với retry."""
    retries = 0
    delay = initial_delay
    last_exception = None

    while retries <= max_retries:
        try:
            log_params = {k:v for k,v in params.items() if k != 'query'} # Không log query dài
            log_params['query_preview'] = params.get('query', '')[:100] + '...' if params.get('query') else 'N/A'
            logger.debug(f"Making async Loki request (Attempt {retries + 1}) for '{query_identifier}' to {loki_api_endpoint} with params: {log_params}")

            async with session.get(loki_api_endpoint, params=params, headers=headers, timeout=timeout) as response:
                if response.status >= 400:
                    if response.status in [429, 500, 502, 503, 504]:
                         response.raise_for_status()
                    else:
                         error_text = await response.text()
                         logger.error(f"[Loki Client] HTTP error {response.status} for query '{query_identifier}': {error_text[:500]}")
                         return None
                # Đọc JSON nếu thành công
                try:
                    return await response.json()
                except json.JSONDecodeError as e:
                    logger.error(f"[Loki Client] Error decoding Loki JSON response for query '{query_identifier}': {e}")
                    return None # Trả về None nếu không parse được JSON
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exception = e
            retries += 1
            logger.warning(f"[Loki Client] Request error for query '{query_identifier}' ({type(e).__name__}). Retrying ({retries}/{max_retries}) in {delay}s...")
            if retries > max_retries:
                logger.error(f"[Loki Client] Loki request failed for query '{query_identifier}' after {max_retries} retries: {last_exception}")
                return None
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay *= 2
        except Exception as e:
             logger.error(f"[Loki Client] Unexpected error during Loki request for query '{query_identifier}': {e}", exc_info=True)
             return None

    logger.error(f"[Loki Client] Loki request failed definitively for query '{query_identifier}' after retries: {last_exception}")
    return None

async def execute_loki_query(session, loki_url, logql_query, start_time, end_time, limit, query_identifier=""):
    """Thực thi một truy vấn LogQL và trả về kết quả."""
    loki_api_endpoint = f"{loki_url.rstrip('/')}/loki/api/v1/query_range"
    params = {
        'query': logql_query,
        'start': int(start_time.timestamp() * 1e9),
        'end': int(end_time.timestamp() * 1e9),
        'limit': limit,
        'direction': 'forward' # Lấy log từ cũ đến mới
    }
    logger.info(f"Querying Loki for '{query_identifier}' from {start_time.isoformat()} to {end_time.isoformat()}")

    headers = {'Accept': 'application/json'}
    data = await _make_loki_request_async(session, loki_api_endpoint, params, headers, query_identifier)

    logs = []
    labels = {}
    if data:
        logs, labels = _parse_loki_response(data, query_identifier)

    logger.info(f"Received {len(logs)} log entries from Loki for query '{query_identifier}'.")
    return {"query_identifier": query_identifier, "logs": logs, "labels": labels}

