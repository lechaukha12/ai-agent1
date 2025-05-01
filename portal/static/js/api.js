// portal/static/js/api.js

/**
 * Fetches statistics data from the backend.
 * @param {number} days - The number of days to fetch stats for.
 * @returns {Promise<object>} - A promise that resolves with the stats data or rejects with an error.
 */
export async function fetchStats(days = 1) {
    try {
        // Calls the BE API, which in turn calls the ObsEngine API
        const response = await fetch(`/api/stats?days=${days}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const stats = await response.json();
        if (stats.error) {
            throw new Error(`API Error: ${stats.error}`);
        }
        return stats;
    } catch (error) {
        console.error('Error fetching stats:', error);
        throw error;
    }
}

/**
 * Fetches incidents data from the backend with pagination and filtering.
 * @param {number} page - The page number to fetch.
 * @param {string} podFilter - The filter string for pod/namespace.
 * @param {string} severityFilter - The filter string for severity.
 * @param {string|null} startDate - The start date in ISO format (UTC).
 * @param {string|null} endDate - The end date in ISO format (UTC).
 * @returns {Promise<object>} - A promise that resolves with the incidents data (including pagination) or rejects with an error.
 */
export async function fetchIncidents(page = 1, podFilter = '', severityFilter = '', startDate = null, endDate = null) {
    try {
        // Calls the BE API, which calls the ObsEngine API
        let url = `/api/incidents?limit=20&page=${page}&pod=${encodeURIComponent(podFilter)}&severity=${encodeURIComponent(severityFilter)}`;
        if (startDate) {
            url += `&start_date=${startDate}`;
        }
        if (endDate) {
            url += `&end_date=${endDate}`;
        }

        const response = await fetch(url);
        if (!response.ok) {
            let errorMsg = `HTTP error! status: ${response.status}`;
            try {
                const errorData = await response.json();
                errorMsg = errorData.error || errorMsg;
            } catch (e) { /* Ignore */ }
            throw new Error(errorMsg);
        }
        const data = await response.json();
        if (data.error) {
            throw new Error(`API Error: ${data.error}`);
        }
        return data;
    } catch (error) {
        console.error('Lỗi fetch incidents:', error);
        throw error;
    }
}

/**
 * Fetches the list of available namespaces.
 * @returns {Promise<string[]>} - A promise that resolves with an array of namespace names or rejects with an error.
 */
export async function fetchAvailableNamespaces() {
    try {
        // Calls the BE API, which calls the ObsEngine API
        const response = await fetch('/api/namespaces');
        if (!response.ok) { throw new Error(`HTTP error! status: ${response.status}`); }
        const data = await response.json();
        if (data.error) { throw new Error(`API Error: ${data.error}`); }
        return Array.isArray(data) ? data : []; // Ensure it returns an array
    } catch (error) {
        console.error('Lỗi khi lấy danh sách namespace có sẵn:', error);
        throw error;
    }
}

/**
 * Fetches the list of currently monitored namespaces.
 * @returns {Promise<string[]>} - A promise that resolves with an array of monitored namespace names or rejects with an error.
 */
export async function fetchMonitoredNamespaces() {
     try {
        // Calls the BE API, which calls the ObsEngine API
        const response = await fetch('/api/config/monitored_namespaces');
        if (!response.ok) { throw new Error(`HTTP error! status: ${response.status}`); }
         const data = await response.json();
         if (data.error) { throw new Error(`API Error: ${data.error}`); }
         // Ensure data is an array, handle potential stringified JSON from older versions if necessary
         let monitored = [];
         if (Array.isArray(data)) {
             monitored = data;
         } else if (typeof data === 'string') {
             try {
                 monitored = JSON.parse(data);
                 if (!Array.isArray(monitored)) monitored = [];
             } catch (e) {
                 console.warn("Could not parse monitored_namespaces string as JSON:", data);
                 monitored = [];
             }
         }
         return monitored;
    } catch (error) {
        console.error('Lỗi khi lấy danh sách namespace đang giám sát:', error);
        throw error;
    }
}

/**
 * Saves the list of monitored namespaces.
 * @param {string[]} selectedNamespaces - An array of namespace names to monitor.
 * @returns {Promise<object>} - A promise that resolves with the success message or rejects with an error.
 */
export async function saveMonitoredNamespacesApi(selectedNamespaces) {
    try {
        // Calls the BE API, which calls the ObsEngine API
        const response = await fetch('/api/config/monitored_namespaces', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ namespaces: selectedNamespaces }),
        });
        const result = await response.json();
        if (!response.ok || result.error) {
            throw new Error(result.error || `HTTP error! status: ${response.status}`);
        }
        console.log("Namespace config saved via API:", selectedNamespaces);
        return result;
    } catch (error) {
        console.error('Lỗi khi lưu cấu hình namespace qua API:', error);
        throw error;
    }
}

/**
 * Fetches the general agent configuration.
 * @returns {Promise<object>} - A promise that resolves with the config object or rejects with an error.
 */
export async function fetchGeneralConfigApi() {
    try {
        // Calls the BE API, which calls the ObsEngine API
        const response = await fetch('/api/config/general');
        if (!response.ok) { throw new Error(`HTTP error! status: ${response.status}`); }
        const config = await response.json();
        if (config.error) { throw new Error(`API Error: ${config.error}`); }
        // Defaults might be handled by BE/ObsEngine now, but keep client-side defaults as fallback
        config.scan_interval_seconds = config.scan_interval_seconds || 30;
        config.restart_count_threshold = config.restart_count_threshold || 5;
        config.loki_scan_min_level = config.loki_scan_min_level || 'INFO';
        config.alert_cooldown_minutes = config.alert_cooldown_minutes || 30;
        // Use the string version if available, otherwise fallback
        config.alert_severity_levels = config.alert_severity_levels_str || config.alert_severity_levels || 'WARNING,ERROR,CRITICAL';
        return config;
    } catch (error) {
        console.error('Lỗi khi lấy cấu hình chung qua API:', error);
        throw error;
    }
}

/**
 * Saves the general agent configuration.
 * @param {object} configData - The configuration data object.
 * @returns {Promise<object>} - A promise that resolves with the success message or rejects with an error.
 */
export async function saveGeneralConfigApi(configData) {
    try {
        // Calls the BE API, which calls the ObsEngine API
        const response = await fetch('/api/config/general', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configData),
        });
        const result = await response.json();
        if (!response.ok || result.error) { throw new Error(result.error || `HTTP error! status: ${response.status}`); }
        console.log("General config saved via API:", configData);
        return result;
    } catch (error) {
        console.error('Lỗi khi lưu cấu hình chung qua API:', error);
        throw error;
    }
}

/**
 * Fetches the Telegram configuration (Chat ID and token existence).
 * @returns {Promise<object>} - A promise that resolves with the config object { telegram_chat_id: string, has_token: boolean, enable_telegram_alerts: boolean } or rejects with an error.
 */
export async function fetchTelegramConfigApi() {
    try {
        // Calls the BE API, which calls the ObsEngine API
        const response = await fetch('/api/config/telegram');
        if (!response.ok) { throw new Error(`HTTP error! status: ${response.status}`); }
        const config = await response.json();
        if (config.error) { throw new Error(`API Error: ${config.error}`); }
        // Ensure boolean value for toggle
        config.enable_telegram_alerts = config.enable_telegram_alerts === true;
        return config;
    } catch (error) {
        console.error('Lỗi khi lấy cấu hình Telegram qua API:', error);
        throw error;
    }
}

/**
 * Saves the Telegram configuration.
 * @param {object} configData - Object containing { telegram_bot_token: string|null, telegram_chat_id: string, enable_telegram_alerts: boolean }. Token can be null/empty if not changing.
 * @returns {Promise<object>} - A promise that resolves with the success message or rejects with an error.
 */
export async function saveTelegramConfigApi(configData) {
    try {
        // Calls the BE API, which calls the ObsEngine API
        const response = await fetch('/api/config/telegram', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configData),
        });
        const result = await response.json();
        if (!response.ok || result.error) { throw new Error(result.error || `HTTP error! status: ${response.status}`); }
        console.log("Telegram config saved via API (token hidden)");
        return result;
    } catch (error) {
        console.error('Lỗi khi lưu cấu hình Telegram qua API:', error);
        throw error;
    }
}

/**
 * Fetches the AI configuration.
 * @returns {Promise<object>} - A promise that resolves with the AI config object or rejects with an error.
 */
export async function fetchAiConfigApi() {
     try {
        // Calls the BE API, which calls the ObsEngine API
         const response = await fetch('/api/config/ai');
         if (!response.ok) { throw new Error(`HTTP error! status: ${response.status}`); }
         const config = await response.json();
         if (config.error) { throw new Error(`API Error: ${config.error}`); }
         // Ensure boolean value for toggle
         config.enable_ai_analysis = config.enable_ai_analysis === true;
         config.ai_provider = config.ai_provider || 'none';
         config.ai_model_identifier = config.ai_model_identifier || '';
         return config;
     } catch (error) {
         console.error('Lỗi khi lấy cấu hình AI qua API:', error);
         throw error;
     }
}

/**
 * Saves the AI configuration.
 * @param {object} configData - The AI configuration data object.
 * @returns {Promise<object>} - A promise that resolves with the success message or rejects with an error.
 */
export async function saveAiConfigApi(configData) {
    try {
        // Calls the BE API, which calls the ObsEngine API
        const response = await fetch('/api/config/ai', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configData),
        });
        const result = await response.json();
        if (!response.ok || result.error) { throw new Error(result.error || `HTTP error! status: ${response.status}`); }
        console.log("AI config saved via API:", { ...configData, ai_api_key: '********' });
        return result;
    } catch (error) {
        console.error('Lỗi khi lưu cấu hình AI qua API:', error);
        throw error;
    }
}

// --- ADDED: Function to fetch Agent Status ---
/**
 * Fetches the status of active agents.
 * @returns {Promise<object>} - A promise that resolves with { active_agents: [] } or rejects with an error.
 */
export async function fetchAgentStatus() {
    try {
        // Calls the BE API, which calls the ObsEngine API
        const response = await fetch('/api/agents/status');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        if (data.error) {
            throw new Error(`API Error: ${data.error}`);
        }
        return data; // Expected format: { active_agents: [...] }
    } catch (error) {
        console.error('Error fetching agent status:', error);
        throw error;
    }
}
// --------------------------------------------
