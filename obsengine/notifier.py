import requests
import logging
import threading
from datetime import datetime, timezone
import os

try:
    from zoneinfo import ZoneInfo
except ImportError:
    logging.error("zoneinfo module not found. Please use Python 3.9+ or install pytz.")
    ZoneInfo = lambda tz_str: timezone.utc

telegram_alerts_counter = 0
notifier_lock = threading.Lock()

try:
    HCM_TZ = ZoneInfo(os.environ.get("TZ", "Asia/Ho_Chi_Minh"))
except Exception as e:
    logging.warning(f"[Notifier] Could not load timezone '{os.environ.get('TZ', 'Asia/Ho_Chi_Minh')}': {e}. Defaulting display to UTC.")
    HCM_TZ = timezone.utc


def send_telegram_alert(bot_token, chat_id, alert_data, ai_enabled):
    global telegram_alerts_counter

    if not bot_token or not chat_id:
        logging.warning("[Notifier] Telegram Bot Token or Chat ID not provided. Skipping alert.")
        return False

    with notifier_lock:
        telegram_alerts_counter += 1
        current_alert_count = telegram_alerts_counter

    logging.info(f"[Notifier] Attempting to send Telegram alert #{current_alert_count} for {alert_data.get('pod_key', 'N/A')}")
    telegram_api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    summary = alert_data.get('summary', 'N/A')
    if not ai_enabled and "Phân tích AI thất bại" not in summary and "Phân tích AI bị tắt" not in summary:
        severity = alert_data.get('severity', 'UNKNOWN')
        initial_reasons = alert_data.get('initial_reasons', 'Không có')
        summary = f"Phát hiện sự cố {severity}. Lý do: {initial_reasons}. (AI tắt)"

    message_lines = [
        f"🚨 *Cảnh báo K8s/Log (Pod: {alert_data.get('pod_key', 'N/A')})* 🚨",
        f"*Mức độ:* `{alert_data.get('severity', 'UNKNOWN')}`",
        f"*Tóm tắt:* {summary}"
    ]

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

    sample_logs_str = alert_data.get('sample_logs', '-')
    sanitized_logs = sample_logs_str.replace('`', "'").replace('*', '').replace('_', '')

    message_lines.extend([
        f"*Lý do phát hiện ban đầu:* {alert_data.get('initial_reasons', 'N/A')}",
        f"*Thời gian phát hiện:* `{alert_data.get('alert_time', 'N/A')}`",
        f"*Log mẫu (nếu có):*\n```\n{sanitized_logs}\n```",
        "\n_Vui lòng kiểm tra chi tiết trên dashboard hoặc Loki/Kubernetes._"
    ])

    message = "\n".join(message_lines)

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
            with notifier_lock:
                if telegram_alerts_counter > 0: telegram_alerts_counter -= 1
    except requests.exceptions.RequestException as e:
        logging.error(f"[Notifier] Error sending Telegram alert for {alert_data.get('pod_key')}: {e}")
        with notifier_lock:
            if telegram_alerts_counter > 0: telegram_alerts_counter -= 1
    except Exception as e:
        logging.error(f"[Notifier] An unexpected error occurred during Telegram send for {alert_data.get('pod_key')}: {e}", exc_info=True)
        with notifier_lock:
            if telegram_alerts_counter > 0: telegram_alerts_counter -= 1

    return alert_sent_successfully

def get_and_reset_telegram_alerts():
    global telegram_alerts_counter
    with notifier_lock:
        alerts = telegram_alerts_counter
        telegram_alerts_counter = 0
        return alerts
