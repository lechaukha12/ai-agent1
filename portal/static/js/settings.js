// portal/static/js/settings.js
import {
    // API functions for Telegram and AI (Global settings)
    fetchTelegramConfigApi, saveTelegramConfigApi,
    fetchAiConfigApi, saveAiConfigApi
} from './api.js';
import { showLoading, hideLoading, showStatusMessage, hideStatusMessage } from './ui.js';

// --- DOM Elements (Global Settings Specific) ---
const enableTelegramToggle = document.getElementById('enable-telegram-toggle');
const telegramTokenInput = document.getElementById('telegram-token');
const telegramChatIdInput = document.getElementById('telegram-chat-id');
const saveTelegramConfigButton = document.getElementById('save-telegram-config-button');
const saveTelegramConfigStatus = document.getElementById('save-telegram-config-status');

const enableAiToggle = document.getElementById('enable-ai-toggle');
const aiProviderOptionsDiv = document.getElementById('ai-provider-options');
const aiProviderSelect = document.getElementById('ai-provider-select');
const aiModelIdentifierInput = document.getElementById('ai-model-identifier');
const aiApiKeyInput = document.getElementById('ai-api-key');
const saveAiConfigButton = document.getElementById('save-ai-config-button');
const saveAiConfigStatus = document.getElementById('save-ai-config-status');

// --- Telegram Settings Functions ---
/**
 * Loads global Telegram configuration from the API and updates the UI.
 */
export async function loadTelegramSettings() {
    // Ensure all required elements are present
    if (!telegramTokenInput || !telegramChatIdInput || !enableTelegramToggle || !saveTelegramConfigStatus) {
        console.warn("Telegram settings elements not found. Skipping load.");
        return;
    }
     showLoading();
     // Clear previous status
     saveTelegramConfigStatus.classList.add('hidden');
     saveTelegramConfigStatus.textContent = '';
     try {
         const config = await fetchTelegramConfigApi();
         telegramTokenInput.value = ''; // Always clear token field for security
         // Update placeholder based on whether a token is already configured (info from API)
         telegramTokenInput.placeholder = config.has_token ? '******** (Đã lưu, nhập để thay đổi)' : 'Chưa cấu hình';
         telegramChatIdInput.value = config.telegram_chat_id || '';
         enableTelegramToggle.checked = config.enable_telegram_alerts || false;
     } catch (error) {
         console.error("Error loading Telegram settings:", error);
         showStatusMessage(saveTelegramConfigStatus, `Lỗi tải cấu hình Telegram: ${error.message}`, 'error');
         // Don't auto-hide error messages
         // hideStatusMessage(saveTelegramConfigStatus, 5000);
     } finally {
         hideLoading();
     }
}

/**
 * Saves the global Telegram configuration via API call.
 */
export async function saveTelegramSettings() {
    // Ensure all required elements are present
    if (!saveTelegramConfigButton || !saveTelegramConfigStatus || !telegramTokenInput || !telegramChatIdInput || !enableTelegramToggle) {
        console.warn("Telegram settings elements not found. Skipping save.");
        return;
    }
    showLoading();
    saveTelegramConfigButton.disabled = true;
    showStatusMessage(saveTelegramConfigStatus, 'Đang lưu...', 'info');

    const enableAlerts = enableTelegramToggle.checked;
    const newBotToken = telegramTokenInput.value; // Get potentially new token
    const chatId = telegramChatIdInput.value.trim();

    // Basic validation for Chat ID
    if (!chatId) {
        alert("Telegram Chat ID không được để trống.");
        hideLoading();
        saveTelegramConfigButton.disabled = false;
        if(saveTelegramConfigStatus) saveTelegramConfigStatus.classList.add('hidden'); // Hide status message
        return;
    }

    const configData = {
        // Only include the token if the user entered a new one
        ...(newBotToken && { telegram_bot_token: newBotToken }),
        telegram_chat_id: chatId,
        enable_telegram_alerts: enableAlerts
    };

    // Clear the token input field immediately after reading its value
    telegramTokenInput.value = '';

    try {
        await saveTelegramConfigApi(configData);
        showStatusMessage(saveTelegramConfigStatus, 'Đã lưu thành công!', 'success');
        // Update placeholder to indicate a token is saved (even if it wasn't changed in this save)
        telegramTokenInput.placeholder = '******** (Đã lưu, nhập để thay đổi)';
    } catch (error) {
        console.error("Error saving Telegram settings:", error);
        showStatusMessage(saveTelegramConfigStatus, `Lỗi: ${error.message}`, 'error');
        // Revert placeholder if save failed, assuming previous state might still be valid
        // This requires knowing the previous state, which fetchTelegramConfigApi provides in 'has_token'
        // We might need to re-fetch or store the 'has_token' state. For simplicity, keep the error placeholder.
        telegramTokenInput.placeholder = 'Lỗi lưu, vui lòng thử lại';
    } finally {
        hideLoading();
        saveTelegramConfigButton.disabled = false;
        // Auto-hide success/info messages, keep error messages visible
        if (!saveTelegramConfigStatus.textContent.startsWith('Lỗi')) {
            hideStatusMessage(saveTelegramConfigStatus);
        }
    }
}


// --- AI Settings Functions ---
/**
 * Loads global AI configuration from the API and updates the UI.
 */
export async function loadAiSettings() {
     // Ensure all required elements are present
     if (!enableAiToggle || !aiProviderSelect || !aiModelIdentifierInput || !aiApiKeyInput || !aiProviderOptionsDiv || !saveAiConfigStatus) {
        console.warn("AI settings elements not found. Skipping load.");
        return;
     }
     showLoading();
     // Clear previous status
     saveAiConfigStatus.classList.add('hidden');
     saveAiConfigStatus.textContent = '';
     try {
         const config = await fetchAiConfigApi();
         enableAiToggle.checked = config.enable_ai_analysis || false; // Default to false if missing
         aiProviderSelect.value = config.ai_provider || 'none'; // Default to 'none'
         aiModelIdentifierInput.value = config.ai_model_identifier || '';
         aiApiKeyInput.value = ''; // Always clear API key field
         // Determine if API key is needed for the selected provider
         const requiresApiKey = config.ai_provider && config.ai_provider !== 'none' && config.ai_provider !== 'local';
         // Update placeholder based on provider and if a key is already configured
         aiApiKeyInput.placeholder = requiresApiKey
            ? (config.has_api_key ? '******** (Đã lưu, nhập để thay đổi)' : 'Chưa cấu hình')
            : 'Không cần thiết';
         // Show/hide provider options based on the toggle state
         handleAiToggleChange(); // Call handler to set initial visibility
     } catch (error) {
         console.error("Error loading AI settings:", error);
         showStatusMessage(saveAiConfigStatus, `Lỗi tải cấu hình AI: ${error.message}`, 'error');
         // hideStatusMessage(saveAiConfigStatus, 5000); // Don't auto-hide error
     } finally {
         hideLoading();
     }
}

/**
 * Saves the global AI configuration via API call.
 */
export async function saveAiSettings() {
    // Ensure all required elements are present
    if (!saveAiConfigButton || !saveAiConfigStatus || !enableAiToggle || !aiProviderSelect || !aiModelIdentifierInput || !aiApiKeyInput) {
        console.warn("AI settings elements not found. Skipping save.");
        return;
    }
    showLoading();
    saveAiConfigButton.disabled = true;
    showStatusMessage(saveAiConfigStatus, 'Đang lưu...', 'info');

    const enableAi = enableAiToggle.checked;
    const provider = aiProviderSelect.value;
    const modelId = aiModelIdentifierInput.value.trim();
    const newApiKey = aiApiKeyInput.value; // Get potentially new API key

    // Basic Validation
    const requiresModelId = enableAi && provider !== 'local' && provider !== 'none';
    if (requiresModelId && !modelId) {
         alert("Model Identifier là bắt buộc đối với provider đã chọn.");
         hideLoading(); saveAiConfigButton.disabled = false;
         if(saveAiConfigStatus) saveAiConfigStatus.classList.add('hidden'); // Hide status
         return;
    }

    const configData = {
        enable_ai_analysis: enableAi,
        ai_provider: provider,
        ai_model_identifier: modelId,
        // Only include the API key if the user entered a new one
        ...(newApiKey && { ai_api_key: newApiKey })
    };

    // Clear the API key input field immediately
    aiApiKeyInput.value = '';

    try {
        await saveAiConfigApi(configData);
        showStatusMessage(saveAiConfigStatus, 'Đã lưu thành công!', 'success');
        // Update placeholder based on the saved provider
        const requiresApiKeyAfterSave = provider !== 'none' && provider !== 'local';
        // Assume save was successful, placeholder reflects saved state (even if key wasn't changed)
        aiApiKeyInput.placeholder = requiresApiKeyAfterSave ? '******** (Đã lưu, nhập để thay đổi)' : 'Không cần thiết';
    } catch (error) {
        console.error("Error saving AI settings:", error);
        showStatusMessage(saveAiConfigStatus, `Lỗi: ${error.message}`, 'error');
        // Revert placeholder based on provider, assuming previous key state might still be valid
        const requiresApiKey = provider !== 'none' && provider !== 'local';
        aiApiKeyInput.placeholder = requiresApiKey ? 'Lỗi lưu, vui lòng thử lại' : 'Không cần thiết';
    } finally {
        hideLoading();
        saveAiConfigButton.disabled = false;
        // Auto-hide success/info messages, keep error messages visible
        if (!saveAiConfigStatus.textContent.startsWith('Lỗi')) {
            hideStatusMessage(saveAiConfigStatus);
        }
    }
}

/**
 * Handles the change event of the AI analysis toggle switch.
 * Shows or hides the AI provider configuration options.
 */
export function handleAiToggleChange() {
    if(aiProviderOptionsDiv && enableAiToggle) {
        const showOptions = enableAiToggle.checked;
        aiProviderOptionsDiv.classList.toggle('hidden', !showOptions);
        console.debug(`AI options visibility toggled: ${showOptions ? 'Shown' : 'Hidden'}`);
    }
}
