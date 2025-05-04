// portal/static/js/api.js

/**
 * Fetches statistics data from the backend API.
 * @param {number} days - The number of days to fetch stats for (1, 7, or 30).
 * @param {string} [environmentFilter=''] - Filter stats by environment name.
 * @param {string} [envTypeFilter=''] - Filter stats by environment type.
 * @returns {Promise<object>} - A promise that resolves to the statistics data object.
 * @throws {Error} - Throws an error if the fetch or API call fails.
 */
export async function fetchStats(days = 1, environmentFilter = '', envTypeFilter = '') {
    try {
        // Add new filter parameters to the URL
        let url = `/api/stats?days=${days}&environment=${encodeURIComponent(environmentFilter)}&env_type=${encodeURIComponent(envTypeFilter)}`;
        console.debug(`Fetching stats from: ${url}`); // Debug log
        const response = await fetch(url);
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
 * Fetches incident data from the backend API with pagination and filtering.
 * @param {number} [page=1] - The page number to fetch.
 * @param {string} [resourceFilter=''] - Filter incidents by resource name (contains).
 * @param {string} [severityFilter=''] - Filter incidents by severity level.
 * @param {string} [environmentFilter=''] - Filter incidents by environment name (exact match).
 * @param {string} [envTypeFilter=''] - Filter incidents by environment type (exact match).
 * @param {string|null} [startDate=null] - Start date filter (ISO format string).
 * @param {string|null} [endDate=null] - End date filter (ISO format string).
 * @returns {Promise<object>} - A promise that resolves to the incident data object including incidents and pagination info.
 * @throws {Error} - Throws an error if the fetch or API call fails.
 */
export async function fetchIncidents(page = 1, resourceFilter = '', severityFilter = '', environmentFilter = '', envTypeFilter = '', startDate = null, endDate = null) {
    try {
        // Update URL parameters to match backend expectations
        let url = `/api/incidents?limit=20&page=${page}`;
        url += `&resource=${encodeURIComponent(resourceFilter)}`; // Use 'resource' instead of 'pod'
        url += `&severity=${encodeURIComponent(severityFilter)}`;
        url += `&environment=${encodeURIComponent(environmentFilter)}`; // Add environment filter
        url += `&env_type=${encodeURIComponent(envTypeFilter)}`; // Add environment type filter

        if (startDate) {
            url += `&start_date=${startDate}`;
        }
        if (endDate) {
            url += `&end_date=${endDate}`;
        }
        console.debug(`Fetching incidents from: ${url}`); // Debug log

        const response = await fetch(url);
        if (!response.ok) {
            let errorMsg = `HTTP error! status: ${response.status}`;
            try {
                const errorData = await response.json();
                errorMsg = errorData.error || errorMsg;
            } catch (e) { /* Ignore if response is not JSON */ }
            throw new Error(errorMsg);
        }
        const data = await response.json();
        if (data.error) {
            throw new Error(`API Error: ${data.error}`);
        }
        // No changes needed to the returned structure, backend handles it
        return data;
    } catch (error) {
        console.error('Lỗi fetch incidents:', error);
        throw error;
    }
}

/**
 * Fetches the list of available Kubernetes namespaces (still needed for K8s agent config).
 * @returns {Promise<string[]>} - A promise that resolves to an array of namespace names.
 * @throws {Error} - Throws an error if the fetch or API call fails.
 */
export async function fetchAvailableNamespaces() {
    try {
        console.debug("Fetching available K8s namespaces..."); // Debug log
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
 * Fetches the status of all active agents.
 * @returns {Promise<object>} - A promise that resolves to the agent status data.
 * @throws {Error} - Throws an error if the fetch or API call fails.
 */
export async function fetchAgentStatus() {
    try {
        console.debug("Fetching agent status..."); // Debug log
        const response = await fetch('/api/agents/status');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        if (data.error) {
            throw new Error(`API Error: ${data.error}`);
        }
        // Ensure the backend returns environment_type and environment_info
        return data;
    } catch (error) {
        console.error('Error fetching agent status:', error);
        throw error;
    }
}

/**
 * Fetches the configuration for a specific agent.
 * @param {string} agentId - The ID of the agent.
 * @returns {Promise<object>} - A promise that resolves to the agent's configuration object.
 * @throws {Error} - Throws an error if the fetch or API call fails.
 */
export async function fetchAgentConfig(agentId) {
    const endpoint = `/api/agents/${encodeURIComponent(agentId)}/config`;
    console.debug(`Fetching config for agent: ${agentId} from ${endpoint}`); // Debug log
    try {
        const response = await fetch(endpoint);
        if (!response.ok) {
            let errorMsg = `HTTP error! status: ${response.status}`;
             try {
                 const errorData = await response.json();
                 errorMsg = errorData.error || errorMsg;
             } catch (e) { /* Ignore if response is not JSON */ }
            throw new Error(errorMsg);
        }
        const config = await response.json();
        if (config.error) {
            throw new Error(`API Error: ${config.error}`);
        }
        // Ensure backend returns necessary fields like environment_type, environment_info
        return {
            scan_interval_seconds: config.scan_interval_seconds ?? 30,
            restart_count_threshold: config.restart_count_threshold ?? 5, // Keep for K8s
            loki_scan_min_level: config.loki_scan_min_level ?? 'INFO',
            monitored_namespaces: Array.isArray(config.monitored_namespaces) ? config.monitored_namespaces : [], // Keep for K8s
            environment_type: config.environment_type ?? 'unknown', // Expect this from backend
            environment_info: config.environment_info ?? {}, // Expect this from backend
            // Add other potential config fields here
        };
    } catch (error) {
        console.error(`Error fetching config for agent ${agentId}:`, error);
        throw error;
    }
}

/**
 * Saves general configuration settings for a specific agent.
 * @param {string} agentId - The ID of the agent.
 * @param {object} configData - The configuration data to save.
 * @returns {Promise<object>} - A promise that resolves to the API response.
 * @throws {Error} - Throws an error if the save operation fails.
 */
export async function saveAgentGeneralConfig(agentId, configData) {
    const endpoint = `/api/agents/${encodeURIComponent(agentId)}/config/general`;
    console.debug(`Saving general config for agent ${agentId}:`, configData); // Debug log
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
 * Saves the list of monitored namespaces for a specific K8s agent.
 * @param {string} agentId - The ID of the K8s agent.
 * @param {string[]} namespaces - An array of namespace names to monitor.
 * @returns {Promise<object>} - A promise that resolves to the API response.
 * @throws {Error} - Throws an error if the save operation fails.
 */
export async function saveAgentMonitoredNamespaces(agentId, namespaces) {
    // This function remains specific to K8s agents
    const endpoint = `/api/agents/${encodeURIComponent(agentId)}/config/namespaces`;
     console.debug(`Saving namespaces for agent ${agentId}:`, namespaces); // Debug log
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

// --- NEW: Fetch distinct environment names ---
/**
 * Fetches a list of distinct environment names from active agents.
 * @returns {Promise<string[]>} - A promise that resolves to an array of unique environment names.
 * @throws {Error} - Throws an error if the fetch or API call fails.
 */
export async function fetchEnvironments() {
    try {
        console.debug("Fetching distinct environment names..."); // Debug log
        // We can reuse the agent status endpoint and extract names client-side
        const data = await fetchAgentStatus();
        const agents = data.active_agents || [];
        const environmentNames = [...new Set(agents.map(agent => agent.environment_name).filter(Boolean))];
        environmentNames.sort(); // Sort alphabetically
        console.debug("Distinct environments found:", environmentNames);
        return environmentNames;
    } catch (error) {
        console.error('Error fetching distinct environments:', error);
        // Return empty array on error to avoid breaking UI
        return [];
    }
}


// --- Global Config APIs (No changes needed in structure) ---

export async function fetchTelegramConfigApi() {
    try {
        console.debug("Fetching Telegram config..."); // Debug log
        const response = await fetch('/api/config/telegram'); // Uses GET implicitly
        if (!response.ok) { throw new Error(`HTTP error! status: ${response.status}`); }
        const config = await response.json();
        if (config.error) { throw new Error(`API Error: ${config.error}`); }
        // Ensure boolean conversion
        config.enable_telegram_alerts = config.enable_telegram_alerts === true || config.enable_telegram_alerts === 'true';
        // Backend should return 'has_token' based on ENV var
        return config;
    } catch (error) {
        console.error('Lỗi khi lấy cấu hình Telegram qua API:', error);
        throw error;
    }
}

export async function saveTelegramConfigApi(configData) {
    try {
        console.debug("Saving Telegram config:", { ...configData, telegram_bot_token: '********' }); // Debug log
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

export async function fetchAiConfigApi() {
     try {
         console.debug("Fetching AI config..."); // Debug log
         const response = await fetch('/api/config/ai'); // Uses GET implicitly
         if (!response.ok) { throw new Error(`HTTP error! status: ${response.status}`); }
         const config = await response.json();
         if (config.error) { throw new Error(`API Error: ${config.error}`); }
         // Ensure boolean conversion and defaults
         config.enable_ai_analysis = config.enable_ai_analysis === true || config.enable_ai_analysis === 'true';
         config.ai_provider = config.ai_provider || 'none';
         config.ai_model_identifier = config.ai_model_identifier || '';
          // Backend should return 'has_api_key' based on ENV var
         return config;
     } catch (error) {
         console.error('Lỗi khi lấy cấu hình AI qua API:', error);
         throw error;
     }
}

export async function saveAiConfigApi(configData) {
    try {
        console.debug("Saving AI config:", { ...configData, ai_api_key: '********' }); // Debug log
        const response = await fetch('/api/config/ai', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configData),
        });
        const result = await response.json();
        if (!response.ok || result.error) { throw new Error(result.error || `HTTP error! status: ${response.status}`); }
        console.log("AI config saved via API (key hidden)");
        return result;
    } catch (error) {
        console.error('Lỗi khi lưu cấu hình AI qua API:', error);
        throw error;
    }
}


// --- User Management APIs (No changes needed) ---

export async function fetchUsers() {
    try {
        console.debug("Fetching users..."); // Debug log
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

export async function createUser(userData) {
    try {
        console.debug("Creating user:", { ...userData, password: '***' }); // Debug log
        const response = await fetch('/api/users', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(userData),
        });
        const result = await response.json();
        if (!response.ok || result.error) {
            throw new Error(result.error || `HTTP error! status: ${response.status}`);
        }
        console.log("User created successfully via API:", result.user);
        return result;
    } catch (error) {
        console.error('Lỗi khi tạo user:', error);
        throw error;
    }
}

export async function updateUser(userId, userData) {
    try {
        console.debug(`Updating user ${userId}:`, userData); // Debug log
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
        return result;
    } catch (error) {
        console.error(`Lỗi khi cập nhật user ${userId}:`, error);
        throw error;
    }
}

// --- Cluster Info API (Removed as Portal should get info via Agent Status) ---
// export async function fetchClusterInfo() { ... } // Removed
