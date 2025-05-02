// portal/static/js/api.js

/**
 * Fetches statistics data from the backend.
 * @param {number} days - The number of days to fetch stats for.
 * @returns {Promise<object>} - A promise that resolves with the stats data or rejects with an error.
 */
export async function fetchStats(days = 1) {
    try {
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
 * Fetches the list of available namespaces (still needed for selection).
 * @returns {Promise<string[]>} - A promise that resolves with an array of namespace names or rejects with an error.
 */
export async function fetchAvailableNamespaces() {
    try {
        const response = await fetch('/api/namespaces');
        if (!response.ok) { throw new Error(`HTTP error! status: ${response.status}`); }
        const data = await response.json();
        if (data.error) { throw new Error(`API Error: ${data.error}`); }
        return Array.isArray(data) ? data : [];
    } catch (error) {
        console.error('Lỗi khi lấy danh sách namespace có sẵn:', error);
        throw error;
    }
}

/**
 * Fetches the status of active agents.
 * @returns {Promise<object>} - A promise that resolves with { active_agents: [] } or rejects with an error.
 */
export async function fetchAgentStatus() {
    try {
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


// --- Global Config APIs (Telegram & AI remain global) ---

/**
 * Fetches the Telegram configuration (Chat ID and token existence).
 * @returns {Promise<object>} - A promise that resolves with the config object { telegram_chat_id: string, has_token: boolean, enable_telegram_alerts: boolean } or rejects with an error.
 */
export async function fetchTelegramConfigApi() {
    try {
        const response = await fetch('/api/config/telegram');
        if (!response.ok) { throw new Error(`HTTP error! status: ${response.status}`); }
        const config = await response.json();
        if (config.error) { throw new Error(`API Error: ${config.error}`); }
        config.enable_telegram_alerts = config.enable_telegram_alerts === true || config.enable_telegram_alerts === 'true';
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
         const response = await fetch('/api/config/ai');
         if (!response.ok) { throw new Error(`HTTP error! status: ${response.status}`); }
         const config = await response.json();
         if (config.error) { throw new Error(`API Error: ${config.error}`); }
         config.enable_ai_analysis = config.enable_ai_analysis === true || config.enable_ai_analysis === 'true';
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


// --- Agent-Specific Config APIs ---

/**
 * Fetches the configuration for a specific agent.
 * @param {string} agentId - The ID of the agent.
 * @returns {Promise<object>} - A promise that resolves with the agent's config object or rejects with an error.
 * @throws {Error} If the API call fails.
 */
export async function fetchAgentConfig(agentId) {
    const endpoint = `/api/agents/${encodeURIComponent(agentId)}/config`;
    console.log(`Fetching config for agent: ${agentId}`); // DEBUG
    try {
        const response = await fetch(endpoint);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const config = await response.json();
        if (config.error) {
            throw new Error(`API Error: ${config.error}`);
        }
        return {
            scan_interval_seconds: config.scan_interval_seconds ?? 30,
            restart_count_threshold: config.restart_count_threshold ?? 5,
            loki_scan_min_level: config.loki_scan_min_level ?? 'INFO',
            monitored_namespaces: Array.isArray(config.monitored_namespaces) ? config.monitored_namespaces : [],
        };
    } catch (error) {
        console.error(`Error fetching config for agent ${agentId}:`, error);
        throw error;
    }
}

/**
 * Saves the general configuration for a specific agent.
 * @param {string} agentId - The ID of the agent.
 * @param {object} configData - Object containing general settings like { scan_interval_seconds, restart_count_threshold, loki_scan_min_level }.
 * @returns {Promise<object>} - A promise that resolves with the success message or rejects with an error.
 * @throws {Error} If the API call fails.
 */
export async function saveAgentGeneralConfig(agentId, configData) {
    const endpoint = `/api/agents/${encodeURIComponent(agentId)}/config/general`;
    console.log(`Saving general config for agent ${agentId}:`, configData); // DEBUG
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configData),
        });
        const result = await response.json();
        if (!response.ok || result.error) {
            throw new Error(result.error || `HTTP error! status: ${response.status}`);
        }
        console.log(`General config saved for agent ${agentId}`);
        return result;
    } catch (error) {
        console.error(`Error saving general config for agent ${agentId}:`, error);
        throw error;
    }
}

/**
 * Saves the monitored namespaces for a specific agent.
 * @param {string} agentId - The ID of the agent.
 * @param {string[]} namespaces - An array of namespace names to monitor.
 * @returns {Promise<object>} - A promise that resolves with the success message or rejects with an error.
 * @throws {Error} If the API call fails.
 */
export async function saveAgentMonitoredNamespaces(agentId, namespaces) {
    const endpoint = `/api/agents/${encodeURIComponent(agentId)}/config/namespaces`;
     console.log(`Saving namespaces for agent ${agentId}:`, namespaces); // DEBUG
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ namespaces: namespaces }),
        });
        const result = await response.json();
        if (!response.ok || result.error) {
            throw new Error(result.error || `HTTP error! status: ${response.status}`);
        }
        console.log(`Namespaces saved for agent ${agentId}`);
        return result;
    } catch (error) {
        console.error(`Error saving namespaces for agent ${agentId}:`, error);
        throw error;
    }
}

// --- User Management APIs ---

/**
 * Fetches the list of all users.
 * Requires admin privileges.
 * @returns {Promise<Array<object>>} A promise that resolves with an array of user objects or rejects with an error.
 */
export async function fetchUsers() {
    try {
        const response = await fetch('/api/users');
        if (!response.ok) {
            if (response.status === 403) {
                throw new Error("Bạn không có quyền xem danh sách người dùng.");
            }
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const users = await response.json();
        if (users.error) {
            throw new Error(`API Error: ${users.error}`);
        }
        return Array.isArray(users) ? users : [];
    } catch (error) {
        console.error('Lỗi khi lấy danh sách user:', error);
        throw error;
    }
}

/**
 * Creates a new user.
 * Requires admin privileges.
 * @param {object} userData - Object containing { username, password, fullname, role }.
 * @returns {Promise<object>} A promise that resolves with the success message and new user data, or rejects with an error.
 */
export async function createUser(userData) {
    // Sends JSON data
    try {
        const response = await fetch('/api/users', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json', // Send JSON
            },
            body: JSON.stringify(userData), // Stringify the user data object
        });
        const result = await response.json();
        if (!response.ok || result.error) {
            throw new Error(result.error || `HTTP error! status: ${response.status}`);
        }
        console.log("User created successfully via API:", result.user);
        return result; // Contains { message: "...", user: {...} } on success
    } catch (error) {
        console.error('Lỗi khi tạo user:', error);
        throw error;
    }
}

/**
 * Updates an existing user's information (fullname and role).
 * Requires admin privileges.
 * @param {number} userId - The ID of the user to update.
 * @param {object} userData - Object containing { fullname, role }. Fields are optional.
 * @returns {Promise<object>} A promise that resolves with the success message and updated user data, or rejects with an error.
 */
export async function updateUser(userId, userData) {
    try {
        const response = await fetch(`/api/users/${userId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(userData),
        });
        const result = await response.json();
        if (!response.ok || result.error) {
            throw new Error(result.error || `HTTP error! status: ${response.status}`);
        }
        console.log(`User ${userId} updated successfully via API:`, result.user);
        return result; // Contains { message: "...", user: {...} } on success
    } catch (error) {
        console.error(`Lỗi khi cập nhật user ${userId}:`, error);
        throw error;
    }
}
