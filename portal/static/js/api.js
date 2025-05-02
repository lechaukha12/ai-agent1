// portal/static/js/api.js

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
        return data;
    } catch (error) {
        console.error('Error fetching agent status:', error);
        throw error;
    }
}


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


export async function fetchAgentConfig(agentId) {
    const endpoint = `/api/agents/${encodeURIComponent(agentId)}/config`;
    console.log(`Fetching config for agent: ${agentId}`);
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

export async function saveAgentGeneralConfig(agentId, configData) {
    const endpoint = `/api/agents/${encodeURIComponent(agentId)}/config/general`;
    console.log(`Saving general config for agent ${agentId}:`, configData);
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

export async function saveAgentMonitoredNamespaces(agentId, namespaces) {
    const endpoint = `/api/agents/${encodeURIComponent(agentId)}/config/namespaces`;
     console.log(`Saving namespaces for agent ${agentId}:`, namespaces);
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

export async function createUser(userData) {
    try {
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

// --- NEW: Fetch Cluster Info ---
export async function fetchClusterInfo() {
    try {
        const response = await fetch('/api/cluster/info');
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
        return data;
    } catch (error) {
        console.error('Error fetching cluster info:', error);
        throw error;
    }
}
