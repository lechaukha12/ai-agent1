// portal/static/js/settings.js
import {
    // Keep API functions for Telegram and AI
    fetchTelegramConfigApi, saveTelegramConfigApi,
    fetchAiConfigApi, saveAiConfigApi
} from './api.js';
import { showLoading, hideLoading, showStatusMessage, hideStatusMessage } from './ui.js';

// --- DOM Elements (Settings Specific - Global Only) ---
// Removed elements related to global general/namespace settings

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


// --- Removed Namespace Settings Functions ---
// export async function renderNamespaceList() { ... } // Removed
// export async function saveMonitoredNamespaces() { ... } // Removed

// --- Removed General Settings Functions ---
// export async function loadGeneralSettings() { ... } // Removed
// export async function saveGeneralSettings() { ... } // Removed


// --- Telegram Settings (Keep as is) ---
export async function loadTelegramSettings() {
    if (!telegramTokenInput || !telegramChatIdInput || !enableTelegramToggle) return;
     showLoading();
     try {
         const config = await fetchTelegramConfigApi();
         telegramTokenInput.value = ''; // Never show token
         telegramTokenInput.placeholder = config.has_token ? '******** (Đã lưu, nhập để thay đổi)' : 'Chưa cấu hình';
         telegramChatIdInput.value = config.telegram_chat_id || '';
         enableTelegramToggle.checked = config.enable_telegram_alerts || false;
     } catch (error) {
         showStatusMessage(saveTelegramConfigStatus, `Lỗi tải cấu hình Telegram: ${error.message}`, 'error');
         hideStatusMessage(saveTelegramConfigStatus, 5000);
     } finally {
         hideLoading();
     }
}

export async function saveTelegramSettings() {
    if (!saveTelegramConfigButton || !saveTelegramConfigStatus || !telegramTokenInput || !telegramChatIdInput || !enableTelegramToggle) return;
    showLoading();
    saveTelegramConfigButton.disabled = true;
    showStatusMessage(saveTelegramConfigStatus, 'Đang lưu...', 'info');

    const enableAlerts = enableTelegramToggle.checked;
    const configData = {
        telegram_bot_token: telegramTokenInput.value, // Send new token or empty string
        telegram_chat_id: telegramChatIdInput.value.trim(),
        enable_telegram_alerts: enableAlerts
    };
    telegramTokenInput.value = ''; // Clear field immediately

    if (!configData.telegram_chat_id) {
        alert("Telegram Chat ID không được để trống.");
        hideLoading();
        saveTelegramConfigButton.disabled = false;
        if(saveTelegramConfigStatus) saveTelegramConfigStatus.classList.add('hidden'); // Hide status message on validation error
        return;
    }

    try {
        await saveTelegramConfigApi(configData);
        showStatusMessage(saveTelegramConfigStatus, 'Đã lưu thành công!', 'success');
        telegramTokenInput.placeholder = '******** (Đã lưu, nhập để thay đổi)';
    } catch (error) {
        showStatusMessage(saveTelegramConfigStatus, `Lỗi: ${error.message}`, 'error');
        telegramTokenInput.placeholder = 'Lỗi lưu, vui lòng thử lại';
    } finally {
        hideLoading();
        saveTelegramConfigButton.disabled = false;
        hideStatusMessage(saveTelegramConfigStatus);
    }
}


// --- AI Settings (Keep as is) ---
export async function loadAiSettings() {
     if (!enableAiToggle || !aiProviderSelect || !aiModelIdentifierInput || !aiApiKeyInput || !aiProviderOptionsDiv) return;
     showLoading();
     try {
         const config = await fetchAiConfigApi();
         enableAiToggle.checked = config.enable_ai_analysis;
         aiProviderSelect.value = config.ai_provider;
         aiModelIdentifierInput.value = config.ai_model_identifier;
         aiApiKeyInput.value = ''; // Never show key
         const requiresApiKey = config.ai_provider !== 'none' && config.ai_provider !== 'local';
         aiApiKeyInput.placeholder = requiresApiKey ? (config.has_api_key ? '******** (Đã lưu, nhập để thay đổi)' : 'Chưa cấu hình') : 'Không cần thiết'; // Check if key exists
         aiProviderOptionsDiv.classList.toggle('hidden', !enableAiToggle.checked);
     } catch (error) {
         showStatusMessage(saveAiConfigStatus, `Lỗi tải cấu hình AI: ${error.message}`, 'error');
         hideStatusMessage(saveAiConfigStatus, 5000);
     } finally {
         hideLoading();
     }
}

export async function saveAiSettings() {
    if (!saveAiConfigButton || !saveAiConfigStatus || !enableAiToggle || !aiProviderSelect || !aiModelIdentifierInput || !aiApiKeyInput) return;
    showLoading();
    saveAiConfigButton.disabled = true;
    showStatusMessage(saveAiConfigStatus, 'Đang lưu...', 'info');

    const configData = {
        enable_ai_analysis: enableAiToggle.checked,
        ai_provider: aiProviderSelect.value,
        ai_model_identifier: aiModelIdentifierInput.value.trim(),
        ai_api_key: aiApiKeyInput.value // Send new key or empty string
    };
    aiApiKeyInput.value = ''; // Clear field immediately

    // Validation
    if (configData.enable_ai_analysis && configData.ai_provider !== 'local' && configData.ai_provider !== 'none' && !configData.ai_model_identifier) {
         alert("Model Identifier là bắt buộc đối với provider này.");
         hideLoading(); saveAiConfigButton.disabled = false;
         if(saveAiConfigStatus) saveAiConfigStatus.classList.add('hidden'); // Hide status
         return;
    }

    try {
        await saveAiConfigApi(configData);
        showStatusMessage(saveAiConfigStatus, 'Đã lưu thành công!', 'success');
        const requiresApiKey = configData.ai_provider !== 'none' && configData.ai_provider !== 'local';
        // Assume save was successful, placeholder reflects saved state (even if key wasn't changed)
        aiApiKeyInput.placeholder = requiresApiKey ? '******** (Đã lưu, nhập để thay đổi)' : 'Không cần thiết';
    } catch (error) {
        showStatusMessage(saveAiConfigStatus, `Lỗi: ${error.message}`, 'error');
        aiApiKeyInput.placeholder = 'Lỗi lưu, vui lòng thử lại';
    } finally {
        hideLoading();
        saveAiConfigButton.disabled = false;
        hideStatusMessage(saveAiConfigStatus);
    }
}

export function handleAiToggleChange() {
    if(aiProviderOptionsDiv && enableAiToggle) {
        aiProviderOptionsDiv.classList.toggle('hidden', !enableAiToggle.checked);
    }
}
