# ai-agent1/app/ai_providers.py
import google.generativeai as genai
import requests
import json
import re
import logging
import threading
import os

# --- Global State for AI Providers ---
gemini_model_instance = None
last_used_gemini_api_key = None
last_used_gemini_model_id = None
model_calls_counter = 0
ai_counter_lock = threading.Lock()

# --- Rule-Based Fallback Logic (Moved from main.py) ---
def determine_severity_from_rules(initial_reasons, log_batch):
    """Determines a fallback severity based on initial reasons and log keywords."""
    severity = "INFO" # Default severity
    reasons_upper = initial_reasons.upper() if initial_reasons else ""

    # Prioritize K8s reasons for critical/error states
    if "OOMKILLED" in reasons_upper or "FAILED" in reasons_upper:
        severity = "CRITICAL"
    elif "ERROR" in reasons_upper or "CRASHLOOPBACKOFF" in reasons_upper:
         severity = "ERROR"
    elif "UNSCHEDULABLE" in reasons_upper or "IMAGEPULLBACKOFF" in reasons_upper or "BACKOFF" in reasons_upper or "WAITING" in reasons_upper:
        severity = "WARNING"

    # If still INFO or WARNING, check logs for higher severity keywords
    if severity in ["INFO", "WARNING"]:
        critical_keywords = ["CRITICAL", "ALERT", "EMERGENCY", "PANIC", "FATAL"]
        error_keywords = ["ERROR", "EXCEPTION", "DENIED", "REFUSED", "UNAUTHORIZED"]
        warning_keywords = ["WARNING", "WARN", "TIMEOUT", "UNABLE", "SLOW"]

        for entry in log_batch[:20]: # Check first 20 relevant logs
            log_upper = entry['message'].upper()
            if any(kw in log_upper for kw in critical_keywords):
                severity = "CRITICAL"; break
            if any(kw in log_upper for kw in error_keywords):
                severity = "ERROR"; # Continue checking for critical
            if severity == "INFO" and any(kw in log_upper for kw in warning_keywords):
                 severity = "WARNING"

    logging.info(f"[Rule Based] Severity determined: {severity}")
    return severity

def get_default_analysis(severity, initial_reasons):
     """Generates a default analysis structure when AI is disabled or fails."""
     summary = f"Phát hiện sự cố tiềm ẩn. Lý do ban đầu: {initial_reasons or 'Không có'}. Mức độ ước tính: {severity}."
     # Add specific note about AI status
     if severity != "INFO": # Only add AI note if it's not just INFO
        summary += " (Phân tích AI bị tắt hoặc thất bại)."
     root_cause = "Không có phân tích AI."
     troubleshooting_steps = "Kiểm tra ngữ cảnh Kubernetes và log chi tiết thủ công."
     return {"severity": severity, "summary": summary, "root_cause": root_cause, "troubleshooting_steps": troubleshooting_steps}


# --- Internal API Call Helpers ---
def _call_gemini_api(api_key, model_identifier, prompt):
    """Internal function to call the Gemini API."""
    global gemini_model_instance, last_used_gemini_api_key, last_used_gemini_model_id
    try:
        needs_reinit = (
            not gemini_model_instance or
            last_used_gemini_api_key != api_key or
            last_used_gemini_model_id != model_identifier
        )

        if needs_reinit:
            logging.info(f"[AI Provider] Initializing/Re-initializing Gemini client for model {model_identifier}")
            if not api_key: raise ValueError("Cannot initialize Gemini client: API key is missing.")
            genai.configure(api_key=api_key)
            gemini_model_instance = genai.GenerativeModel(model_identifier)
            last_used_gemini_api_key = api_key
            last_used_gemini_model_id = model_identifier
            logging.info("[AI Provider] Gemini client initialized/re-initialized successfully.")

        logging.debug(f"[AI Provider] Generating content with model '{model_identifier}'...")
        response = gemini_model_instance.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(temperature=0.2, max_output_tokens=500),
            request_options={'timeout': 90}
        )
        logging.debug("[AI Provider] Content generation successful.")
        return response
    except Exception as e:
        logging.error(f"[AI Provider] Error calling Gemini API: {e}", exc_info=True)
        gemini_model_instance = None
        last_used_gemini_api_key = None
        last_used_gemini_model_id = None
        raise

def _call_local_api(endpoint_url, prompt):
    """Internal function to call the local AI endpoint."""
    try:
        response = requests.post(endpoint_url, json={"prompt": prompt}, timeout=120)
        response.raise_for_status()
        return response
    except requests.exceptions.RequestException as e:
        logging.error(f"[AI Provider] Error calling local AI endpoint {endpoint_url}: {e}")
        raise

# --- Internal Analysis Execution ---
def _execute_ai_analysis(provider, api_key, model_id, local_endpoint_url, prompt):
    """
    Executes the call to the specified AI provider and handles response parsing.
    Separated to manage counter logic cleanly.
    """
    global model_calls_counter
    analysis_result = None
    raw_response_text = None
    analysis_failed = False
    call_attempted = False

    try:
        if provider == "gemini":
            call_attempted = True
            if not api_key: raise ValueError("Gemini provider selected but API key is missing.")
            if not model_id: raise ValueError("Gemini provider selected but model identifier is missing.")
            with ai_counter_lock: model_calls_counter += 1
            logging.info(f"[AI Provider] Calling Gemini API (Model: {model_id}) - Call count: {model_calls_counter}")
            gemini_response = _call_gemini_api(api_key, model_id, prompt)

            if not gemini_response.parts:
                logging.warning(f"[AI Provider] Gemini response has no parts. Raw response obj: {gemini_response}")
                raw_response_text = str(gemini_response)
                analysis_failed = True
                summary = "Gemini không trả về nội dung."
                try:
                    finish_reason = gemini_response.candidates[0].finish_reason if gemini_response.candidates else "UNKNOWN"
                    safety_ratings = gemini_response.candidates[0].safety_ratings if gemini_response.candidates else []
                    logging.warning(f"[AI Provider] Gemini Finish Reason: {finish_reason}, Safety Ratings: {safety_ratings}")
                    if finish_reason.name == 'SAFETY': summary = "Phản hồi bị chặn bởi bộ lọc an toàn Gemini."
                    elif finish_reason.name == 'MAX_TOKENS': summary = "Phản hồi Gemini bị cắt do đạt giới hạn token."
                    else: summary = f"Gemini không trả về nội dung (Lý do: {finish_reason.name})."
                except Exception as inner_e: logging.error(f"Error extracting finish reason: {inner_e}")
                analysis_result = {"severity": "WARNING", "summary": summary, "root_cause": "N/A", "troubleshooting_steps": "Kiểm tra cấu hình Gemini hoặc prompt."}
            else:
                raw_response_text = gemini_response.text.strip()
                cleaned_response_text = raw_response_text
                if cleaned_response_text.startswith("```json"): cleaned_response_text = cleaned_response_text.strip("```json").strip("`").strip()
                elif cleaned_response_text.startswith("```"): cleaned_response_text = cleaned_response_text.strip("```").strip()
                match = re.search(r'\{.*\}', cleaned_response_text, re.DOTALL)
                json_string_to_parse = match.group(0) if match else cleaned_response_text
                try:
                    analysis_result = json.loads(json_string_to_parse)
                    if not isinstance(analysis_result, dict): raise ValueError("Parsed response is not a dictionary.")
                except (json.JSONDecodeError, ValueError) as json_err:
                    logging.warning(f"[AI Provider] Failed to decode/validate Gemini JSON: {json_err}. Raw: {raw_response_text}")
                    analysis_result = None; analysis_failed = True

        elif provider == "local":
            call_attempted = True
            if not local_endpoint_url: raise ValueError("Local provider selected but endpoint URL is not configured.")
            with ai_counter_lock: model_calls_counter += 1
            logging.info(f"[AI Provider] Calling local AI endpoint ({local_endpoint_url}) - Call count: {model_calls_counter}")
            local_response = _call_local_api(local_endpoint_url, prompt)
            raw_response_text = local_response.text
            try:
                analysis_result = local_response.json()
                if not isinstance(analysis_result, dict): raise ValueError("Parsed response is not a dictionary.")
            except (json.JSONDecodeError, ValueError) as json_err:
                logging.warning(f"[AI Provider] Local API response is not valid JSON: {json_err}. Raw: {raw_response_text}")
                analysis_result = None; analysis_failed = True

        else:
             logging.warning(f"[AI Provider] Unsupported AI provider '{provider}' or provider is 'none'. Skipping AI call.")
             analysis_failed = True # Mark as failed if provider is invalid/none

    except Exception as e:
        logging.error(f"[AI Provider] Error during AI analysis call (Provider: {provider}): {e}", exc_info=True)
        analysis_failed = True
        if call_attempted: # Decrement only if call was actually tried
            with ai_counter_lock:
                if model_calls_counter > 0: model_calls_counter -= 1
                logging.warning(f"[AI Provider] Decremented call counter due to error. Current count: {model_calls_counter}")

    return analysis_result, raw_response_text, analysis_failed

# --- Main Public Function ---
def perform_analysis(log_batch, k8s_context, initial_reasons, config, prompt_template):
    """
    Performs AI analysis or rule-based analysis based on configuration.

    Args:
        log_batch (list): List of log entry dictionaries.
        k8s_context (str): Formatted Kubernetes context string.
        initial_reasons (str): Initial reasons for investigation.
        config (dict): The current agent configuration dictionary.
        prompt_template (str): The template string for the AI prompt.

    Returns:
        tuple: (dict, str or None, str or None):
               - Final analysis result dictionary (guaranteed to have required keys).
               - The final prompt used for AI (or None if AI disabled).
               - Raw response text from AI (or None if AI disabled/failed).
    """
    analysis_result = None
    final_prompt = None
    raw_response_text = None
    analysis_failed = False # Default to not failed

    # --- Check if AI Analysis is Enabled ---
    if not config.get('enable_ai_analysis', False):
        logging.info("[AI Analysis] AI analysis is disabled by configuration.")
        analysis_failed = True # Mark as "failed" to trigger fallback
    else:
        # --- AI Analysis Enabled: Prepare Prompt ---
        namespace = "unknown"; pod_name = "unknown_pod"
        if log_batch and log_batch[0].get('labels'):
            labels = log_batch[0].get('labels', {}); namespace = labels.get('namespace', namespace); pod_name = labels.get('pod', pod_name)
        elif k8s_context:
            match_ns = re.search(r"Pod:\s*([\w.-]+)/", k8s_context); match_pod = re.search(r"Pod:\s*[\w.-]+/([\w.-]+)\n", k8s_context);
            if match_ns: namespace = match_ns.group(1);
            if match_pod: pod_name = match_pod.group(1)

        log_text = "N/A"
        if log_batch:
            limited_logs = [f"[{entry['timestamp'].isoformat()}] {entry['message'][:500]}" for entry in log_batch[:15]];
            log_text = "\n".join(limited_logs)

        try:
            final_prompt = prompt_template.format(
                namespace=namespace, pod_name=pod_name,
                k8s_context=k8s_context[:10000], log_text=log_text[:20000]
            )
        except KeyError as e:
            logging.error(f"[AI Analysis] Missing placeholder in PROMPT_TEMPLATE: {e}. Using default prompt structure.")
            final_prompt = f"Phân tích pod {namespace}/{pod_name}. Ngữ cảnh K8s: {k8s_context[:10000]}. Logs: {log_text[:20000]}. Chỉ trả lời bằng JSON với khóa 'severity', 'summary', 'root_cause', 'troubleshooting_steps'."

        # --- Call AI Provider ---
        provider = config.get('ai_provider', 'none')
        api_key = config.get('ai_api_key', '')
        model_id = config.get('ai_model_identifier', '')
        local_endpoint = config.get('local_gemini_endpoint') # Get endpoint from config if passed

        analysis_result, raw_response_text, analysis_failed = _execute_ai_analysis(
            provider, api_key, model_id, local_endpoint, final_prompt
        )

    # --- Fallback if AI failed or was disabled ---
    if analysis_failed or analysis_result is None:
        if analysis_result is None and not analysis_failed:
             logging.warning(f"[AI Analysis] AI analysis function returned successfully but result is None (likely parsing failed). Falling back.")
        elif analysis_failed:
             logging.warning(f"[AI Analysis] AI analysis failed or provider unsupported/misconfigured. Falling back to rule-based analysis.")

        severity = determine_severity_from_rules(initial_reasons, log_batch)
        analysis_result = get_default_analysis(severity, initial_reasons)
        # Raw response text might be available even if parsing failed, keep it if so.
        # If AI was disabled from start, raw_response_text will be None.

    # --- Ensure result format ---
    if not isinstance(analysis_result, dict):
         logging.error(f"[AI Analysis] CRITICAL: Analysis result is not a dictionary after processing. Final fallback.")
         severity = determine_severity_from_rules(initial_reasons, log_batch)
         analysis_result = get_default_analysis(severity, initial_reasons)
         raw_response_text = raw_response_text or "[Fallback due to invalid result type]"
    else:
         analysis_result.setdefault("severity", "WARNING")
         analysis_result.setdefault("summary", "N/A")
         analysis_result.setdefault("root_cause", "N/A")
         analysis_result.setdefault("troubleshooting_steps", "N/A")

    return analysis_result, final_prompt, raw_response_text


def get_and_reset_model_calls():
    """Gets the current model call count and resets it to zero."""
    global model_calls_counter
    with ai_counter_lock:
        calls = model_calls_counter
        model_calls_counter = 0
        return calls
