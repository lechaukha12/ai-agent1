# ai-agent1/app/notifier.py
import requests
import logging
import threading
from datetime import datetime, timezone # Import timezone directly
import os # <<< THÊM DÒNG NÀY

try:
    from zoneinfo import ZoneInfo
except ImportError:
    logging.error("zoneinfo module not found. Please use Python 3.9+ or install pytz.")
    # Fallback or handle appropriately if Python < 3.9 is a target
    ZoneInfo = lambda tz_str: timezone.utc # Simple fallback to UTC

# --- Global State for Notifier ---
telegram_alerts_counter = 0
notifier_lock = threading.Lock() # Lock specifically for notifier state (e.g., counter)

# --- Timezone (copied from main for formatting consistency) ---
# It might be better to pass timezone info or formatted time string instead of duplicating
try:
    # Assume HCM timezone is generally desired for alerts, get from env if possible
    # Or rely on the formatted time string passed in alert_data
    HCM_TZ = ZoneInfo(os.environ.get("TZ", "Asia/Ho_Chi_Minh"))
except Exception as e:
    logging.warning(f"[Notifier] Could not load timezone '{os.environ.get('TZ', 'Asia/Ho_Chi_Minh')}': {e}. Defaulting display to UTC.")
    HCM_TZ = timezone.utc

# --- Telegram Alert Function ---

def send_telegram_alert(bot_token, chat_id, alert_data, ai_enabled):
    """
    Sends a formatted alert message to the specified Telegram chat.

    Args:
        bot_token (str): The Telegram Bot Token.
        chat_id (str): The target Telegram Chat ID.
        alert_data (dict): A dictionary containing alert details
                           (pod_key, severity, summary, root_cause,
                            troubleshooting_steps, initial_reasons,
                            alert_time, sample_logs).
        ai_enabled (bool): Whether AI analysis is currently enabled in the agent config.
    """
    global telegram_alerts_counter

    if not bot_token or not chat_id:
        logging.warning("[Notifier] Telegram Bot Token or Chat ID not provided. Skipping alert.")
        return False # Indicate failure

    # --- Increment counter *only if* sending is attempted ---
    with notifier_lock:
        telegram_alerts_counter += 1
        current_alert_count = telegram_alerts_counter

    logging.info(f"[Notifier] Attempting to send Telegram alert #{current_alert_count} for {alert_data.get('pod_key', 'N/A')}")
    telegram_api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # Determine the summary to use based on AI status
    summary = alert_data.get('summary', 'N/A')
    if not ai_enabled and "Phân tích AI thất bại" not in summary and "Phân tích AI bị tắt" not in summary:
        severity = alert_data.get('severity', 'UNKNOWN')
        initial_reasons = alert_data.get('initial_reasons', 'Không có')
        summary = f"Phát hiện sự cố {severity}. Lý do: {initial_reasons}. (AI tắt)"

    # Base message parts
    message_lines = [
        f"🚨 *Cảnh báo K8s/Log (Pod: {alert_data.get('pod_key', 'N/A')})* 🚨",
        f"*Mức độ:* `{alert_data.get('severity', 'UNKNOWN')}`",
        f"*Tóm tắt:* {summary}"
    ]

    # Conditionally add AI-specific sections
    if ai_enabled:
        root_cause = alert_data.get('root_cause', '')
        steps = alert_data.get('troubleshooting_steps', '')
        default_root_cause = "Không có phân tích AI."
        default_steps = "Kiểm tra ngữ cảnh Kubernetes và log chi tiết thủ công."
        ai_meaningful = root_cause not in ["N/A", default_root_cause] or steps not in ["N/A", default_steps]

        if ai_meaningful:
             if root_cause and root_cause != "N/A":
                 message_lines.append(f"*Nguyên nhân gốc có thể:*\n{root_cause}")
             if steps and steps != "N/A":
                 message_lines.append(f"*Đề xuất khắc phục:*\n{steps}")

    # Add remaining common parts
    sample_logs_str = alert_data.get('sample_logs', '-')
    sanitized_logs = sample_logs_str.replace('`', "'").replace('*', '').replace('_', '') # Basic sanitization

    message_lines.extend([
        f"*Lý do phát hiện ban đầu:* {alert_data.get('initial_reasons', 'N/A')}",
        f"*Thời gian phát hiện:* `{alert_data.get('alert_time', 'N/A')}`", # Use pre-formatted time
        f"*Log mẫu (nếu có):*\n```\n{sanitized_logs}\n```", # Use code block
        "\n_Vui lòng kiểm tra chi tiết trên dashboard hoặc Loki/Kubernetes._"
    ])

    message = "\n".join(message_lines)

    # Truncate message if it exceeds Telegram limit
    max_len = 4096
    truncated_message = message
    if len(message.encode('utf-8')) > max_len:
        safe_truncate_pos = message.rfind('\n', 0, max_len - 100)
        if safe_truncate_pos == -1: safe_truncate_pos = max_len - 100
        truncated_message = message[:safe_truncate_pos] + "\n\n_[... message truncated ...]_"
        logging.warning(f"[Notifier] Alert message for {alert_data.get('pod_key')} exceeded Telegram limit and was truncated.")

    payload = {
        'chat_id': chat_id,
        'text': truncated_message,
        'parse_mode': 'Markdown'
    }
    alert_sent_successfully = False
    try:
        response = requests.post(telegram_api_url, json=payload, timeout=20)
        response.raise_for_status()
        response_data = response.json()
        if response_data.get('ok'):
            logging.info(f"[Notifier] Sent alert to Telegram for {alert_data.get('pod_key')}. Response OK.")
            alert_sent_successfully = True
        else:
            logging.error(f"[Notifier] Telegram API returned error for {alert_data.get('pod_key')}: {response_data.get('description')}")
            # Decrement counter if sending failed according to Telegram
            with notifier_lock:
                if telegram_alerts_counter > 0: telegram_alerts_counter -= 1
    except requests.exceptions.RequestException as e:
        logging.error(f"[Notifier] Error sending Telegram alert for {alert_data.get('pod_key')}: {e}")
        # Decrement counter if sending failed due to request error
        with notifier_lock:
            if telegram_alerts_counter > 0: telegram_alerts_counter -= 1
    except Exception as e:
        logging.error(f"[Notifier] An unexpected error occurred during Telegram send for {alert_data.get('pod_key')}: {e}", exc_info=True)
        # Decrement counter on unexpected error too
        with notifier_lock:
            if telegram_alerts_counter > 0: telegram_alerts_counter -= 1

    return alert_sent_successfully # Return status

def get_and_reset_telegram_alerts():
    """Gets the current telegram alert count and resets it to zero."""
    global telegram_alerts_counter
    with notifier_lock:
        alerts = telegram_alerts_counter
        telegram_alerts_counter = 0
        return alerts
