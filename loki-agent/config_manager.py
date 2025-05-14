# -*- coding: utf-8 -*-
import os
import json
import logging
import time
import aiohttp
import asyncio

# --- Các giá trị mặc định nếu API không trả về ---
DEFAULT_SCAN_INTERVAL_SECONDS = 60
DEFAULT_LOG_SCAN_RANGE_MINUTES = 5
DEFAULT_LOKI_QUERY_LIMIT = 500
# ---------------------------------------------

_current_agent_config = {}
_last_config_refresh_time = 0
CONFIG_REFRESH_INTERVAL_SECONDS = 60 # Tần suất lấy lại config từ API
_config_refresh_lock = asyncio.Lock()

async def get_config(session, obs_engine_url, agent_id, force_refresh=False):
    """Lấy cấu hình hoạt động cho Loki Agent từ ObsEngine API."""
    global _current_agent_config, _last_config_refresh_time
    now = time.time()

    async with _config_refresh_lock:
        needs_refresh = force_refresh or not _current_agent_config or (now - _last_config_refresh_time >= CONFIG_REFRESH_INTERVAL_SECONDS)

        if not needs_refresh:
            return _current_agent_config.copy()

        logging.info("[Config Manager] Refreshing Loki Agent configuration from API...")

        fetched_config = await _fetch_config_from_api_async(session, obs_engine_url, agent_id)

        if fetched_config and isinstance(fetched_config, dict):
            # Validate và sử dụng cấu hình từ API
            validated_config = {
                "obs_engine_url": obs_engine_url, # Giữ lại URL đã biết
                "agent_id": agent_id, # Giữ lại ID đã biết
                "environment_name": os.environ.get("ENVIRONMENT_NAME", "loki-environment"), # Lấy từ ENV
                "loki_url": fetched_config.get("loki_url"), # Bắt buộc phải có từ API
                "logql_queries": fetched_config.get("logql_queries", []), # Danh sách các query config
                "scan_interval_seconds": int(fetched_config.get("scan_interval_seconds", DEFAULT_SCAN_INTERVAL_SECONDS)),
                "log_scan_range_minutes": int(fetched_config.get("log_scan_range_minutes", DEFAULT_LOG_SCAN_RANGE_MINUTES)),
                "loki_query_limit": int(fetched_config.get("loki_query_limit", DEFAULT_LOKI_QUERY_LIMIT)),
            }

            # Kiểm tra các trường bắt buộc
            if not validated_config["loki_url"]:
                logging.error("[Config Manager] Loki URL is missing in the configuration received from API.")
                # Giữ lại config cũ nếu có, nếu không thì trả về None để báo lỗi
                return _current_agent_config.copy() if _current_agent_config else None

            if not isinstance(validated_config["logql_queries"], list):
                 logging.error("[Config Manager] 'logql_queries' received from API is not a list.")
                 validated_config["logql_queries"] = [] # Sử dụng list rỗng

            # Validate từng query config trong list (đơn giản)
            valid_queries = []
            for q_conf in validated_config["logql_queries"]:
                if isinstance(q_conf, dict) and q_conf.get("query"):
                    valid_queries.append({
                        "query": q_conf["query"],
                        "service_name": q_conf.get("service_name") # Cho phép tùy chọn service_name
                        # Thêm các trường khác cho query config nếu cần (ví dụ: keywords riêng)
                    })
                else:
                    logging.warning(f"Ignoring invalid query configuration entry from API: {q_conf}")
            validated_config["logql_queries"] = valid_queries


            _current_agent_config = validated_config
            _last_config_refresh_time = now
            logging.info("[Config Manager] Loki Agent configuration refreshed/loaded successfully.")
            logging.info(f"[Config Manager] Effective config: LokiURL={_current_agent_config.get('loki_url')}, Queries={len(_current_agent_config.get('logql_queries',[]))}, Interval={_current_agent_config.get('scan_interval_seconds')}")

        else:
            logging.error("[Config Manager] Failed to fetch valid configuration from API. Using last known config if available.")
            # Không cập nhật _last_config_refresh_time để thử lại sớm hơn
            # Trả về config cũ nếu có, nếu không thì trả về None
            return _current_agent_config.copy() if _current_agent_config else None

    return _current_agent_config.copy()

async def _fetch_config_from_api_async(session, obs_engine_url, agent_id, max_retries=2, initial_delay=5):
    """Hàm helper để gọi API lấy config (tương tự các agent khác)."""
    if not obs_engine_url or not agent_id: return None

    endpoint = f"{obs_engine_url}/api/agents/{agent_id}/config"
    retries = 0
    delay = initial_delay
    last_exception = None

    while retries <= max_retries:
        try:
            logging.info(f"[Config Manager] Fetching config from ObsEngine API (Attempt {retries+1}): {endpoint}")
            async with session.get(endpoint, timeout=15) as response:
                if response.status >= 400:
                    if response.status in [429, 500, 502, 503, 504]:
                        response.raise_for_status()
                    else:
                        error_text = await response.text()
                        logging.error(f"[Config Manager] HTTP error {response.status} fetching config: {error_text[:200]}")
                        return None
                config_data = await response.json()
                logging.info(f"[Config Manager] Successfully fetched config from API for agent {agent_id}.")
                return config_data
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exception = e
            retries += 1
            logging.warning(f"[Config Manager] Request error fetching config ({type(e).__name__}). Retrying ({retries}/{max_retries}) in {delay}s...")
            if retries > max_retries:
                logging.error(f"[Config Manager] Config fetch failed after {max_retries} retries: {last_exception}")
                return None
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay *= 2
        except json.JSONDecodeError as e:
            logging.error(f"[Config Manager] Error decoding JSON config from API: {e}")
            return None
        except Exception as e:
            logging.error(f"[Config Manager] Unexpected error fetching config from API: {e}", exc_info=True)
            return None

    logging.error(f"[Config Manager] Config fetch failed definitively after retries: {last_exception}")
    return None

def get_last_config():
    """Trả về config cuối cùng đã load thành công (dùng khi có lỗi)."""
    return _current_agent_config.copy()
