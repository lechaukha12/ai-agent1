# ai-agent1/app/ai_providers.py
import google.generativeai as genai
import requests
import json
import re
import logging
import threading
import os # <<< THÊM DÒNG NÀY

# --- Global State for AI Providers ---
gemini_model_instance = None
last_used_gemini_api_key = None
last_used_gemini_model_id = None
model_calls_counter = 0
ai_counter_lock = threading.Lock() # Lock để bảo vệ counter khi truy cập từ nhiều thread

# --- Helper Functions (Moved & Corrected) ---

def _call_gemini_api(api_key, model_identifier, prompt):
    """
    Internal function to call the Gemini API, handling client initialization and re-initialization.
    Use leading underscore to indicate it's primarily for internal use within this module.
    """
    global gemini_model_instance, last_used_gemini_api_key, last_used_gemini_model_id
    try:
        needs_reinit = (
            not gemini_model_instance or
            last_used_gemini_api_key != api_key or # Compare with last used key
            last_used_gemini_model_id != model_identifier # Compare with last used model
        )

        if needs_reinit:
            logging.info(f"[AI Provider] Initializing/Re-initializing Gemini client for model {model_identifier}")
            if not api_key:
                raise ValueError("Cannot initialize Gemini client: API key is missing.")

            genai.configure(api_key=api_key)
            gemini_model_instance = genai.GenerativeModel(model_identifier)

            last_used_gemini_api_key = api_key
            last_used_gemini_model_id = model_identifier
            logging.info("[AI Provider] Gemini client initialized/re-initialized successfully.")

        logging.debug(f"[AI Provider] Generating content with model '{model_identifier}'...")
        response = gemini_model_instance.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2, max_output_tokens=500
            ),
            request_options={'timeout': 90}
        )
        logging.debug("[AI Provider] Content generation successful.")
        return response # Return the full response object

    except Exception as e:
        logging.error(f"[AI Provider] Error calling Gemini API: {e}", exc_info=True)
        # Reset state on error
        gemini_model_instance = None
        last_used_gemini_api_key = None
        last_used_gemini_model_id = None
        raise

def _call_local_api(endpoint_url, prompt):
    """
    Internal function to call the local AI endpoint.
    Use leading underscore to indicate it's primarily for internal use within this module.
    """
    try:
        response = requests.post(endpoint_url, json={"prompt": prompt}, timeout=120)
        response.raise_for_status()
        # Always return the response object to handle JSON parsing later
        return response
    except requests.exceptions.RequestException as e:
        logging.error(f"[AI Provider] Error calling local AI endpoint {endpoint_url}: {e}")
        raise

# --- Main Analysis Function ---

def call_ai_analysis(provider, api_key, model_id, local_endpoint_url, prompt):
    """
    Calls the appropriate AI provider based on configuration and returns the analysis result.

    Args:
        provider (str): The AI provider ('gemini', 'local', 'none', etc.).
        api_key (str): The API key for the provider (if required).
        model_id (str): The model identifier for the provider (if required).
        local_endpoint_url (str): The URL for the 'local' provider.
        prompt (str): The prompt to send to the AI model.

    Returns:
        tuple: (dict or None, str or None, bool):
               - Parsed JSON analysis result (dict) or None if parsing failed/error occurred.
               - Raw response text (str) or None if error occurred before getting text.
               - Boolean indicating if the analysis failed (True if failed, False otherwise).
    """
    global model_calls_counter
    analysis_result = None
    raw_response_text = None
    analysis_failed = False
    call_attempted = False # Flag to track if a call was genuinely attempted

    try:
        if provider == "gemini":
            call_attempted = True
            if not api_key: raise ValueError("Gemini provider selected but API key is missing.")
            if not model_id: raise ValueError("Gemini provider selected but model identifier is missing.")

            # Increment counter *before* the call attempt for this provider
            with ai_counter_lock: model_calls_counter += 1
            logging.info(f"[AI Provider] Calling Gemini API (Model: {model_id}) - Call count: {model_calls_counter}")

            gemini_response = _call_gemini_api(api_key, model_id, prompt)

            # Handle Gemini response parsing and potential errors
            if not gemini_response.parts:
                logging.warning(f"[AI Provider] Gemini response has no parts. Raw response obj: {gemini_response}")
                raw_response_text = str(gemini_response)
                analysis_failed = True
                summary = "Gemini không trả về nội dung."
                try: # Try to get more details about why it failed
                    finish_reason = gemini_response.candidates[0].finish_reason if gemini_response.candidates else "UNKNOWN"
                    safety_ratings = gemini_response.candidates[0].safety_ratings if gemini_response.candidates else []
                    logging.warning(f"[AI Provider] Gemini Finish Reason: {finish_reason}, Safety Ratings: {safety_ratings}")
                    if finish_reason.name == 'SAFETY': summary = "Phản hồi bị chặn bởi bộ lọc an toàn Gemini."
                    elif finish_reason.name == 'MAX_TOKENS': summary = "Phản hồi Gemini bị cắt do đạt giới hạn token."
                    else: summary = f"Gemini không trả về nội dung (Lý do: {finish_reason.name})."
                except Exception as inner_e: logging.error(f"Error extracting finish reason: {inner_e}")
                # Create a simple result indicating the issue
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
                    analysis_result = None
                    analysis_failed = True

        elif provider == "local":
            call_attempted = True
            if not local_endpoint_url: raise ValueError("Local provider selected but endpoint URL is not configured.")

            # Increment counter *before* the call attempt for this provider
            with ai_counter_lock: model_calls_counter += 1
            logging.info(f"[AI Provider] Calling local AI endpoint ({local_endpoint_url}) - Call count: {model_calls_counter}")

            local_response = _call_local_api(local_endpoint_url, prompt)
            raw_response_text = local_response.text # Get raw text first

            try: # Attempt to parse JSON from the raw text
                analysis_result = local_response.json()
                if not isinstance(analysis_result, dict): raise ValueError("Parsed response is not a dictionary.")
            except (json.JSONDecodeError, ValueError) as json_err:
                logging.warning(f"[AI Provider] Local API response is not valid JSON: {json_err}. Raw: {raw_response_text}")
                analysis_result = None
                analysis_failed = True # JSON parsing failed

        # elif provider == "openai": ... # Add other providers here

        else: # Includes 'none' or unsupported providers
            logging.warning(f"[AI Provider] Unsupported AI provider '{provider}' or provider is 'none'. Skipping AI call.")
            analysis_failed = True
            # No call attempted, so no counter handling needed here

    except Exception as e:
        logging.error(f"[AI Provider] Error during AI analysis call (Provider: {provider}): {e}", exc_info=True)
        analysis_failed = True
        # Decrement counter *only if* a call was attempted but failed
        if call_attempted:
            with ai_counter_lock:
                if model_calls_counter > 0: model_calls_counter -= 1
                logging.warning(f"[AI Provider] Decremented call counter due to error. Current count: {model_calls_counter}")


    # Return the parsed result (or None), the raw text (or None), and the failure status
    return analysis_result, raw_response_text, analysis_failed


def get_and_reset_model_calls():
    """Gets the current model call count and resets it to zero."""
    global model_calls_counter
    with ai_counter_lock:
        calls = model_calls_counter
        model_calls_counter = 0
        return calls
