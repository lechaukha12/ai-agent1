import {
    fetchTelegramConfigApi, saveTelegramConfigApi,
    fetchAiConfigApi, saveAiConfigApi
} from './api.js';
import { showLoading, hideLoading, showStatusMessage, hideStatusMessage } from './ui.js';

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

const modelIdField = document.querySelector('.model-id-field');
const apiKeyField = document.querySelector('.api-key-field');
// --- Thêm tham chiếu đến trường Local Endpoint ---
const localEndpointField = document.querySelector('.local-endpoint-field');
const localEndpointInput = document.getElementById('local-gemini-endpoint');
// ---------------------------------------------

export async function loadTelegramSettings() {
    if (!telegramTokenInput || !telegramChatIdInput || !enableTelegramToggle || !saveTelegramConfigStatus) {
        console.warn("Telegram settings elements not found. Skipping load.");
        return;
    }
     showLoading();
     saveTelegramConfigStatus.classList.add('hidden');
     saveTelegramConfigStatus.textContent = '';
     try {
         const config = await fetchTelegramConfigApi();
         telegramTokenInput.value = '';
         telegramTokenInput.placeholder = config.has_token ? '******** (Đã lưu, nhập để thay đổi)' : 'Chưa cấu hình';
         telegramChatIdInput.value = config.telegram_chat_id || '';
         enableTelegramToggle.checked = config.enable_telegram_alerts || false;
     } catch (error) {
         console.error("Error loading Telegram settings:", error);
         showStatusMessage(saveTelegramConfigStatus, `Lỗi tải cấu hình Telegram: ${error.message}`, 'error');
     } finally {
         hideLoading();
     }
}

export async function saveTelegramSettings() {
    if (!saveTelegramConfigButton || !saveTelegramConfigStatus || !telegramTokenInput || !telegramChatIdInput || !enableTelegramToggle) {
        console.warn("Telegram settings elements not found. Skipping save.");
        return;
    }
    showLoading();
    saveTelegramConfigButton.disabled = true;
    showStatusMessage(saveTelegramConfigStatus, 'Đang lưu...', 'info');

    const enableAlerts = enableTelegramToggle.checked;
    const newBotToken = telegramTokenInput.value;
    const chatId = telegramChatIdInput.value.trim();

    if (!chatId) {
        alert("Telegram Chat ID không được để trống.");
        hideLoading();
        saveTelegramConfigButton.disabled = false;
        if(saveTelegramConfigStatus) saveTelegramConfigStatus.classList.add('hidden');
        return;
    }

    const configData = {
        ...(newBotToken && { telegram_bot_token: newBotToken }),
        telegram_chat_id: chatId,
        enable_telegram_alerts: enableAlerts
    };

    telegramTokenInput.value = '';

    try {
        await saveTelegramConfigApi(configData);
        showStatusMessage(saveTelegramConfigStatus, 'Đã lưu thành công!', 'success');
        telegramTokenInput.placeholder = '******** (Đã lưu, nhập để thay đổi)';
    } catch (error) {
        console.error("Error saving Telegram settings:", error);
        showStatusMessage(saveTelegramConfigStatus, `Lỗi: ${error.message}`, 'error');
        telegramTokenInput.placeholder = 'Lỗi lưu, vui lòng thử lại';
    } finally {
        hideLoading();
        saveTelegramConfigButton.disabled = false;
        if (!saveTelegramConfigStatus.textContent.startsWith('Lỗi')) {
            hideStatusMessage(saveTelegramConfigStatus);
        }
    }
}


export async function loadAiSettings() {
     // Thêm kiểm tra localEndpointField
     if (!enableAiToggle || !aiProviderSelect || !aiModelIdentifierInput || !aiApiKeyInput || !aiProviderOptionsDiv || !saveAiConfigStatus || !modelIdField || !apiKeyField || !localEndpointField || !localEndpointInput) {
        console.warn("AI settings elements not found. Skipping load.");
        return;
     }
     showLoading();
     saveAiConfigStatus.classList.add('hidden');
     saveAiConfigStatus.textContent = '';
     try {
         const config = await fetchAiConfigApi();
         enableAiToggle.checked = config.enable_ai_analysis || false;
         // --- Đảm bảo giá trị 'none' không được chọn mặc định ---
         aiProviderSelect.value = (config.ai_provider && config.ai_provider !== 'none') ? config.ai_provider : 'gemini'; // Default về gemini nếu là none hoặc không có
         // -----------------------------------------------------
         aiModelIdentifierInput.value = config.ai_model_identifier || '';
         aiApiKeyInput.value = '';
         // --- Load giá trị cho Local Endpoint ---
         localEndpointInput.value = config.local_gemini_endpoint || '';
         // -------------------------------------

         handleAiToggleChange();
         handleAiProviderChange();

     } catch (error) {
         console.error("Error loading AI settings:", error);
         showStatusMessage(saveAiConfigStatus, `Lỗi tải cấu hình AI: ${error.message}`, 'error');
     } finally {
         hideLoading();
     }
}

export async function saveAiSettings() {
    // Thêm kiểm tra localEndpointField
    if (!saveAiConfigButton || !saveAiConfigStatus || !enableAiToggle || !aiProviderSelect || !aiModelIdentifierInput || !aiApiKeyInput || !localEndpointField || !localEndpointInput) {
        console.warn("AI settings elements not found. Skipping save.");
        return;
    }
    showLoading();
    saveAiConfigButton.disabled = true;
    showStatusMessage(saveAiConfigStatus, 'Đang lưu...', 'info');

    const enableAi = enableAiToggle.checked;
    const provider = aiProviderSelect.value;
    const modelId = aiModelIdentifierInput.value.trim();
    const newApiKey = aiApiKeyInput.value;
    // --- Lấy giá trị Local Endpoint ---
    const localEndpointUrl = localEndpointInput.value.trim();
    // ---------------------------------

    // Validation dựa trên provider
    const requiresModelId = enableAi && provider !== 'local';
    const requiresApiKey = enableAi && provider !== 'local';
    const requiresLocalEndpoint = enableAi && provider === 'local';

    if (requiresModelId && !modelId) {
         alert("Model Identifier là bắt buộc đối với provider đã chọn.");
         hideLoading(); saveAiConfigButton.disabled = false;
         if(saveAiConfigStatus) saveAiConfigStatus.classList.add('hidden');
         return;
    }
    // --- Thêm validation cho Local Endpoint ---
    if (requiresLocalEndpoint && !localEndpointUrl) {
         alert("Local Model Endpoint URL là bắt buộc khi chọn provider 'Local Model'.");
         hideLoading(); saveAiConfigButton.disabled = false;
         if(saveAiConfigStatus) saveAiConfigStatus.classList.add('hidden');
         return;
    }
    // -----------------------------------------

    const configData = {
        enable_ai_analysis: enableAi,
        ai_provider: provider,
        // Chỉ gửi các trường cần thiết dựa trên provider
        ...(requiresModelId && { ai_model_identifier: modelId }),
        ...(newApiKey && requiresApiKey && { ai_api_key: newApiKey }), // Chỉ gửi key nếu nhập mới VÀ provider cần key
        ...(requiresLocalEndpoint && { local_gemini_endpoint: localEndpointUrl }) // Gửi local endpoint nếu cần
    };

    aiApiKeyInput.value = ''; // Luôn xóa key sau khi đọc

    try {
        await saveAiConfigApi(configData);
        showStatusMessage(saveAiConfigStatus, 'Đã lưu thành công!', 'success');
        handleAiProviderChange();

    } catch (error) {
        console.error("Error saving AI settings:", error);
        showStatusMessage(saveAiConfigStatus, `Lỗi: ${error.message}`, 'error');
        aiApiKeyInput.placeholder = requiresApiKey ? 'Lỗi lưu, vui lòng thử lại' : 'Không cần thiết';
    } finally {
        hideLoading();
        saveAiConfigButton.disabled = false;
        if (!saveAiConfigStatus.textContent.startsWith('Lỗi')) {
            hideStatusMessage(saveAiConfigStatus);
        }
    }
}

export function handleAiToggleChange() {
    // Thêm kiểm tra localEndpointField
    if(aiProviderOptionsDiv && enableAiToggle && modelIdField && apiKeyField && localEndpointField) {
        const showOptions = enableAiToggle.checked;
        aiProviderOptionsDiv.classList.toggle('hidden', !showOptions);
        console.debug(`AI options visibility toggled: ${showOptions ? 'Shown' : 'Hidden'}`);
        if (showOptions) {
            handleAiProviderChange();
        } else {
            // Nếu tắt AI, ẩn tất cả các trường con
            modelIdField.classList.add('hidden');
            apiKeyField.classList.add('hidden');
            localEndpointField.classList.add('hidden'); // Ẩn luôn trường local endpoint
        }
    }
}

export function handleAiProviderChange() {
    // Thêm kiểm tra localEndpointField
    if (!aiProviderSelect || !modelIdField || !apiKeyField || !aiApiKeyInput || !localEndpointField) return;

    const selectedProvider = aiProviderSelect.value;
    const isAiEnabled = enableAiToggle.checked;

    console.debug(`AI Provider changed to: ${selectedProvider}, AI Enabled: ${isAiEnabled}`);

    let showModelId = false;
    let showApiKey = false;
    let showLocalEndpoint = false; // Biến mới
    let apiKeyPlaceholder = 'Không cần thiết';

    if (isAiEnabled) {
        if (selectedProvider === 'gemini') {
            showModelId = true;
            showApiKey = true;
            showLocalEndpoint = false; // Ẩn local endpoint
             apiKeyPlaceholder = aiApiKeyInput.placeholder.includes('********') ? '******** (Đã lưu, nhập để thay đổi)' : 'Chưa cấu hình';
        } else if (selectedProvider === 'local') {
            showModelId = false; // Ẩn model ID
            showApiKey = false; // Ẩn API Key
            showLocalEndpoint = true; // Hiện local endpoint
            apiKeyPlaceholder = 'Không cần thiết';
        }
        // Các provider khác (chưa hỗ trợ) sẽ không thay đổi gì (vẫn ẩn theo mặc định)
    }

    modelIdField.classList.toggle('hidden', !showModelId);
    apiKeyField.classList.toggle('hidden', !showApiKey);
    localEndpointField.classList.toggle('hidden', !showLocalEndpoint); // Cập nhật visibility cho local endpoint
    aiApiKeyInput.placeholder = apiKeyPlaceholder;

    console.debug(`Visibility updated - Model ID: ${showModelId}, API Key: ${showApiKey}, Local Endpoint: ${showLocalEndpoint}`);
}

if (aiProviderSelect) {
    aiProviderSelect.addEventListener('change', handleAiProviderChange);
}
