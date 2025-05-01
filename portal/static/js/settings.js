// portal/static/js/settings.js
import {
    fetchAvailableNamespaces, fetchMonitoredNamespaces, saveMonitoredNamespacesApi,
    fetchGeneralConfigApi, saveGeneralConfigApi,
    fetchTelegramConfigApi, saveTelegramConfigApi,
    fetchAiConfigApi, saveAiConfigApi
} from './api.js';
import { showLoading, hideLoading, showStatusMessage, hideStatusMessage } from './ui.js';

// --- DOM Elements (Settings Specific) ---
const namespaceListDiv = document.getElementById('namespace-list');
const namespaceLoadingText = document.getElementById('namespace-loading-text');
const saveNsConfigButton = document.getElementById('save-ns-config-button');
const saveNsConfigStatus = document.getElementById('save-ns-config-status'); // Exported from ui.js

const scanIntervalInput = document.getElementById('scan-interval');
const restartThresholdInput = document.getElementById('restart-threshold');
const lokiScanLevelSelect = document.getElementById('loki-scan-level');
const alertCooldownInput = document.getElementById('alert-cooldown');
const alertLevelsInput = document.getElementById('alert-levels');
const saveGeneralConfigButton = document.getElementById('save-general-config-button');
const saveGeneralConfigStatus = document.getElementById('save-general-config-status'); // Exported from ui.js

const telegramTokenInput = document.getElementById('telegram-token');
const telegramChatIdInput = document.getElementById('telegram-chat-id');
const saveTelegramConfigButton = document.getElementById('save-telegram-config-button');
const saveTelegramConfigStatus = document.getElementById('save-telegram-config-status'); // Exported from ui.js

const enableAiToggle = document.getElementById('enable-ai-toggle');
const aiProviderOptionsDiv = document.getElementById('ai-provider-options');
const aiProviderSelect = document.getElementById('ai-provider-select');
const aiModelIdentifierInput = document.getElementById('ai-model-identifier');
const aiApiKeyInput = document.getElementById('ai-api-key');
const saveAiConfigButton = document.getElementById('save-ai-config-button');
const saveAiConfigStatus = document.getElementById('save-ai-config-status'); // Exported from ui.js


// --- Namespace Settings ---

/**
 * Renders the list of namespaces with checkboxes.
 */
export async function renderNamespaceList() {
    if (!namespaceListDiv || !namespaceLoadingText) return;
    showLoading();
    namespaceListDiv.innerHTML = '';
    namespaceLoadingText.classList.remove('hidden');
    try {
        const [availableNamespaces, monitoredNamespaces] = await Promise.all([
            fetchAvailableNamespaces(),
            fetchMonitoredNamespaces()
        ]);
        namespaceLoadingText.classList.add('hidden');
        if (availableNamespaces === null || monitoredNamespaces === null) return; // Error handled in API

        if (availableNamespaces.length === 0) {
            namespaceListDiv.innerHTML = `<p class="text-gray-500 dark:text-gray-400 col-span-full text-center py-4">Không tìm thấy namespace nào.</p>`;
        } else {
            availableNamespaces.forEach(ns => {
                const isChecked = monitoredNamespaces.includes(ns);
                const div = document.createElement('div');
                div.className = 'namespace-item text-sm flex items-center';
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox'; checkbox.id = `ns-${ns}`; checkbox.value = ns; checkbox.checked = isChecked;
                checkbox.className = 'form-checkbox h-4 w-4 text-indigo-600 transition duration-150 ease-in-out rounded dark:bg-gray-700 border-gray-300 dark:border-gray-600 focus:ring-indigo-500';
                const label = document.createElement('label');
                label.htmlFor = `ns-${ns}`; label.textContent = ns;
                label.className = 'ml-2 text-gray-700 dark:text-gray-300 cursor-pointer select-none';
                div.appendChild(checkbox); div.appendChild(label);
                namespaceListDiv.appendChild(div);
            });
        }
    } catch (error) {
        console.error("Error rendering namespace list:", error);
        namespaceListDiv.innerHTML = `<p class="text-red-500 col-span-full text-center py-4">Lỗi hiển thị danh sách namespace.</p>`;
        namespaceLoadingText.classList.add('hidden');
    } finally {
        hideLoading();
    }
}

/**
 * Handles saving the selected monitored namespaces.
 */
export async function saveMonitoredNamespaces() {
    if (!saveNsConfigButton || !saveNsConfigStatus) return;
    showLoading();
    saveNsConfigButton.disabled = true;
    showStatusMessage(saveNsConfigStatus, 'Đang lưu...', 'info');

    const selectedNamespaces = [];
    if (namespaceListDiv) {
        namespaceListDiv.querySelectorAll('input[type="checkbox"]:checked').forEach(checkbox => {
            selectedNamespaces.push(checkbox.value);
        });
    }

    try {
        await saveMonitoredNamespacesApi(selectedNamespaces);
        showStatusMessage(saveNsConfigStatus, 'Đã lưu thành công!', 'success');
    } catch (error) {
        showStatusMessage(saveNsConfigStatus, `Lỗi: ${error.message}`, 'error');
    } finally {
        hideLoading();
        saveNsConfigButton.disabled = false;
        hideStatusMessage(saveNsConfigStatus);
    }
}

// --- General Settings ---

/**
 * Fetches and populates the general settings form.
 */
export async function loadGeneralSettings() {
    if (!scanIntervalInput || !restartThresholdInput || !lokiScanLevelSelect || !alertCooldownInput || !alertLevelsInput) return;
     showLoading();
     try {
         const config = await fetchGeneralConfigApi();
         scanIntervalInput.value = config.scan_interval_seconds;
         restartThresholdInput.value = config.restart_count_threshold;
         lokiScanLevelSelect.value = config.loki_scan_min_level;
         alertCooldownInput.value = config.alert_cooldown_minutes;
         alertLevelsInput.value = config.alert_severity_levels;
     } catch (error) {
         showStatusMessage(saveGeneralConfigStatus, `Lỗi tải cấu hình chung: ${error.message}`, 'error');
         hideStatusMessage(saveGeneralConfigStatus, 5000); // Hide error after 5s
     } finally {
         hideLoading();
     }
}

/**
 * Handles saving the general agent settings.
 */
export async function saveGeneralSettings() {
    if (!saveGeneralConfigButton || !saveGeneralConfigStatus || !scanIntervalInput || !restartThresholdInput || !lokiScanLevelSelect || !alertCooldownInput || !alertLevelsInput) return;
    showLoading();
    saveGeneralConfigButton.disabled = true;
    showStatusMessage(saveGeneralConfigStatus, 'Đang lưu...', 'info');

    let configData;
    try {
        // --- Validation ---
        const scanInterval = parseInt(scanIntervalInput.value);
        const restartThreshold = parseInt(restartThresholdInput.value);
        const alertCooldown = parseInt(alertCooldownInput.value);
        const alertLevelsStr = alertLevelsInput.value;
        const scanLevel = lokiScanLevelSelect.value;

        if (isNaN(scanInterval) || scanInterval < 10) throw new Error("Tần suất quét phải là số >= 10.");
        if (isNaN(restartThreshold) || restartThreshold < 1) throw new Error("Ngưỡng khởi động lại phải là số >= 1.");
        if (isNaN(alertCooldown) || alertCooldown < 1) throw new Error("Thời gian chờ cảnh báo lại phải là số >= 1.");
        if (!alertLevelsStr || alertLevelsStr.trim() === '') throw new Error("Mức độ gửi cảnh báo không được để trống.");

        const validLevels = ["INFO", "WARNING", "ERROR", "CRITICAL"];
        const inputLevels = alertLevelsStr.split(',').map(s => s.trim().toUpperCase()).filter(s => s);
        if (inputLevels.length === 0) throw new Error("Mức độ gửi cảnh báo không hợp lệ.");
        for (const level of inputLevels) {
             if (!validLevels.includes(level)) throw new Error(`Mức độ "${level}" không hợp lệ.`);
        }
        // --- End Validation ---

        configData = {
            scan_interval_seconds: scanInterval,
            restart_count_threshold: restartThreshold,
            loki_scan_min_level: scanLevel,
            alert_cooldown_minutes: alertCooldown,
            alert_severity_levels: inputLevels.join(',')
        };

        await saveGeneralConfigApi(configData);
        showStatusMessage(saveGeneralConfigStatus, 'Đã lưu thành công!', 'success');

    } catch (error) {
        // If validation error, show alert; otherwise show status message
        if (error.message.includes("phải là số") || error.message.includes("không được để trống") || error.message.includes("không hợp lệ")) {
             alert(`Lỗi dữ liệu nhập: ${error.message}`);
             showStatusMessage(saveGeneralConfigStatus, '', 'info'); // Clear status
             saveGeneralConfigStatus.classList.add('hidden');
        } else {
             showStatusMessage(saveGeneralConfigStatus, `Lỗi: ${error.message}`, 'error');
        }
    } finally {
        hideLoading();
        saveGeneralConfigButton.disabled = false;
        // Hide success/error message after delay only if it wasn't a validation alert
        if (!saveGeneralConfigStatus.classList.contains('hidden') && !alert.caller) {
            hideStatusMessage(saveGeneralConfigStatus);
        }
    }
}

// --- Telegram Settings ---

/**
 * Fetches and populates the Telegram settings form.
 */
export async function loadTelegramSettings() {
    if (!telegramTokenInput || !telegramChatIdInput) return;
     showLoading();
     try {
         const config = await fetchTelegramConfigApi();
         telegramTokenInput.value = ''; // Never show token
         telegramTokenInput.placeholder = config.has_token ? '******** (Đã lưu, nhập để thay đổi)' : 'Chưa cấu hình';
         telegramChatIdInput.value = config.telegram_chat_id || '';
     } catch (error) {
         showStatusMessage(saveTelegramConfigStatus, `Lỗi tải cấu hình Telegram: ${error.message}`, 'error');
         hideStatusMessage(saveTelegramConfigStatus, 5000);
     } finally {
         hideLoading();
     }
}

/**
 * Handles saving the Telegram settings.
 */
export async function saveTelegramSettings() {
    if (!saveTelegramConfigButton || !saveTelegramConfigStatus || !telegramTokenInput || !telegramChatIdInput) return;
    showLoading();
    saveTelegramConfigButton.disabled = true;
    showStatusMessage(saveTelegramConfigStatus, 'Đang lưu...', 'info');

    const configData = {
        telegram_bot_token: telegramTokenInput.value, // Send new token or empty string
        telegram_chat_id: telegramChatIdInput.value.trim()
    };
    telegramTokenInput.value = ''; // Clear field immediately

    if (!configData.telegram_chat_id) {
        alert("Telegram Chat ID không được để trống.");
        hideLoading();
        saveTelegramConfigButton.disabled = false;
        saveTelegramConfigStatus.classList.add('hidden');
        return;
    }

    try {
        await saveTelegramConfigApi(configData);
        showStatusMessage(saveTelegramConfigStatus, 'Đã lưu thành công!', 'success');
        telegramTokenInput.placeholder = '******** (Đã lưu, nhập để thay đổi)'; // Update placeholder
    } catch (error) {
        showStatusMessage(saveTelegramConfigStatus, `Lỗi: ${error.message}`, 'error');
        telegramTokenInput.placeholder = 'Lỗi lưu, vui lòng thử lại';
    } finally {
        hideLoading();
        saveTelegramConfigButton.disabled = false;
        hideStatusMessage(saveTelegramConfigStatus);
    }
}


// --- AI Settings ---

/**
 * Fetches and populates the AI settings form.
 */
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
         aiApiKeyInput.placeholder = requiresApiKey ? '******** (Đã lưu, nhập để thay đổi)' : 'Không cần thiết';
         aiProviderOptionsDiv.classList.toggle('hidden', !enableAiToggle.checked);
     } catch (error) {
         showStatusMessage(saveAiConfigStatus, `Lỗi tải cấu hình AI: ${error.message}`, 'error');
         hideStatusMessage(saveAiConfigStatus, 5000);
     } finally {
         hideLoading();
     }
}

/**
 * Handles saving the AI settings.
 */
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
         hideLoading(); saveAiConfigButton.disabled = false; saveAiConfigStatus.classList.add('hidden'); return;
    }

    try {
        await saveAiConfigApi(configData);
        showStatusMessage(saveAiConfigStatus, 'Đã lưu thành công!', 'success');
        const requiresApiKey = configData.ai_provider !== 'none' && configData.ai_provider !== 'local';
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

/**
 * Handles the change event for the AI enable toggle.
 */
export function handleAiToggleChange() {
    if(aiProviderOptionsDiv && enableAiToggle) {
        aiProviderOptionsDiv.classList.toggle('hidden', !enableAiToggle.checked);
    }
}
