// portal/static/js/main.js
import * as api from './api.js'; // API call functions
import * as ui from './ui.js'; // UI manipulation functions
import * as charts from './charts.js'; // Chart rendering functions
import * as settings from './settings.js'; // Settings specific functions

// --- State Variables ---
let currentIncidentPage = 1;
let totalIncidentPages = 1;
let currentPodFilter = '';
let currentSeverityFilter = '';
let currentStatsDays = 1;
let currentIncidentStartDate = null;
let currentIncidentEndDate = null;
let incidentsDataCache = {};
let usersDataCache = {};
let currentConfiguringAgentId = null;
let currentUserRole = 'user';

// --- DOM Element References ---
// Dashboard Charts & Stats
const lineChartCtx = document.getElementById('statsChart')?.getContext('2d');
const lineChartErrorElem = document.getElementById('line-chart-error');
const lineChartNoDataElem = document.getElementById('line-chart-no-data');
const topPodsCtx = document.getElementById('topPodsChart')?.getContext('2d');
const topPodsErrorElem = document.getElementById('top-pods-error');
const topPodsNoDataElem = document.getElementById('top-pods-no-data');
const namespacePieCtx = document.getElementById('namespacePieChart')?.getContext('2d');
const namespacePieErrorElem = document.getElementById('namespace-pie-error');
const namespacePieNoDataElem = document.getElementById('namespace-pie-no-data');
const severityPieCtx = document.getElementById('severityPieChart')?.getContext('2d');
const severityPieErrorElem = document.getElementById('severity-pie-error');
const severityPieNoDataElem = document.getElementById('severity-pie-no-data');
const totalIncidentsElem = document.getElementById('total-incidents');
const totalGeminiCallsElem = document.getElementById('total-gemini-calls');
const totalTelegramAlertsElem = document.getElementById('total-telegram-alerts');
const timeRangeButtons = document.querySelectorAll('.time-range-btn');

// Incidents Page Elements
const incidentsTableBody = document.getElementById('incidents-table-body');
const podFilterInput = document.getElementById('pod-filter');
const severityFilterSelect = document.getElementById('severity-filter');
const filterButton = document.getElementById('filter-button');
const refreshIncidentsButton = document.getElementById('refresh-incidents-button');
const paginationControls = document.getElementById('pagination-controls');
const paginationInfo = document.getElementById('pagination-info');
const prevPageButton = document.getElementById('prev-page');
const nextPageButton = document.getElementById('next-page');

// Settings Page Elements (Global)
const saveTelegramConfigButton = document.getElementById('save-telegram-config-button');
const saveAiConfigButton = document.getElementById('save-ai-config-button');
const enableAiToggle = document.getElementById('enable-ai-toggle');

// General UI Elements
const sidebarItems = document.querySelectorAll('.sidebar-item');
const modalCloseButton = document.getElementById('modal-close-button'); // Incident modal close
const incidentModalElement = document.getElementById('incident-modal'); // Reference for incident modal overlay click

// Agent Monitoring Page Elements (Use the new ID 'agent-content')
const agentContentSection = document.getElementById('agent-content'); // Changed ID
const agentStatusTableBody = document.getElementById('agent-status-table-body');
const agentStatusErrorElem = document.getElementById('agent-status-error');
const refreshAgentStatusButton = document.getElementById('refresh-agent-status-button');
const addAgentButton = document.getElementById('add-agent-button'); // New button reference

// Agent Configuration Section Elements
const agentConfigSection = document.getElementById('agent-config-section');
const configAgentIdSpan = document.getElementById('config-agent-id');
const agentConfigTabs = document.querySelectorAll('.agent-config-tab');
const agentConfigTabContents = document.querySelectorAll('.agent-config-tab-content');
const agentScanIntervalInput = document.getElementById('agent-scan-interval');
const agentRestartThresholdInput = document.getElementById('agent-restart-threshold');
const agentLokiScanLevelSelect = document.getElementById('agent-loki-scan-level');
const saveAgentGeneralConfigButton = document.getElementById('save-agent-general-config-button');
const saveAgentGeneralConfigStatus = document.getElementById('save-agent-general-config-status');
const agentNamespaceListDiv = document.getElementById('agent-namespace-list');
const agentNamespaceLoadingText = document.getElementById('agent-namespace-loading-text');
const saveAgentNsConfigButton = document.getElementById('save-agent-ns-config-button');
const saveAgentNsConfigStatus = document.getElementById('save-agent-ns-config-status');
const closeAgentConfigButton = document.getElementById('close-agent-config-button');

// User Management Page Elements
const usersTableBody = document.getElementById('users-table-body');
const usersListErrorElem = document.getElementById('users-list-error');
const openCreateUserModalButton = document.getElementById('open-create-user-modal-button');
const createUserModal = document.getElementById('create-user-modal');
const createUserModalCloseButton = document.getElementById('create-user-modal-close-button');
const createUserForm = document.getElementById('create-user-form');
const createUserButton = document.getElementById('create-user-button');
const createUserStatus = document.getElementById('create-user-status');
const newPasswordInput = document.getElementById('new-password');
const confirmPasswordInput = document.getElementById('confirm-password');
const passwordMatchError = document.getElementById('password-match-error');
const editUserModal = document.getElementById('edit-user-modal');
const editUserModalCloseButton = document.getElementById('edit-user-modal-close-button');
const editUserForm = document.getElementById('edit-user-form');
const editUserStatus = document.getElementById('edit-user-status');
const saveUserChangesButton = document.getElementById('save-user-changes-button');

// Add Agent Modal Element
const addAgentModal = document.getElementById('add-agent-modal');


// --- Data Loading Functions ---

/**
 * Loads the status of active agents from the backend API and renders the table.
 */
async function loadAgentStatus() {
    if (!agentStatusTableBody || !agentStatusErrorElem) {
        console.warn("Agent status table elements not found. Skipping load.");
        return;
    }
    ui.showLoading();
    // Update colspan to 5 since 2 columns were removed
    agentStatusTableBody.innerHTML = `<tr><td colspan="5" class="text-center py-6 text-gray-500">Đang tải trạng thái agent...</td></tr>`;
    agentStatusErrorElem.classList.add('hidden');
    try {
        const data = await api.fetchAgentStatus();
        const agents = data.active_agents || [];
        // Pass the callback function to handle configure button clicks
        // The callback now receives an object with clusterInfo
        ui.renderAgentStatusTable(agents, (agentId, clusterName, clusterInfo) => {
            handleConfigureAgentClick(agentId, clusterName, clusterInfo);
        }, currentUserRole);
    } catch (error) {
        console.error("DEBUG: Failed to load agent status:", error);
        ui.showAgentStatusError(`Lỗi tải trạng thái agent: ${error.message}`);
    } finally {
        ui.hideLoading();
    }
}

/**
 * Loads the list of users from the backend API and renders the user management table.
 */
async function loadUserManagementData() {
    console.log("Loading user management data...");
    if (!usersTableBody) return;
    ui.showLoading();
    usersTableBody.innerHTML = `<tr><td colspan="4" class="text-center py-6 text-gray-500">Đang tải danh sách user...</td></tr>`;
    if (usersListErrorElem) usersListErrorElem.classList.add('hidden');
    usersDataCache = {};
    try {
        const users = await api.fetchUsers();
        users.forEach(user => { usersDataCache[user.id] = user; });
        ui.renderUserTable(users, handleEditUserClick);
    } catch (error) {
        console.error("Failed to load users:", error);
        ui.showUserListError(`Lỗi tải danh sách user: ${error.message}`);
    } finally {
        ui.hideLoading();
    }
}

/**
 * Loads data specific to the currently active section.
 * @param {string} activeSectionId - The ID of the section to load data for (e.g., 'dashboard', 'agent').
 */
function loadActiveSectionData(activeSectionId) {
    console.log(`DEBUG: Loading data for section: ${activeSectionId}`);
    // Hide agent config section when switching main sections
    if (agentConfigSection) agentConfigSection.classList.add('hidden');
    currentConfiguringAgentId = null; // Reset configuring agent ID

    // Load data based on the active section ID
    switch (activeSectionId) {
        case 'dashboard':
            loadDashboardData();
            break;
        case 'incidents':
            loadIncidentsData(true); // Force reload incident data
            break;
        case 'agent': // Use the new section ID 'agent'
            loadAgentStatus();
            break;
        case 'settings':
            // Load all global settings
            settings.loadTelegramSettings();
            settings.loadAiSettings();
            break;
        case 'user-management':
            loadUserManagementData();
            break;
        default:
            // Fallback to dashboard if section ID is unknown
            console.warn(`Unhandled section ID: ${activeSectionId}`);
            loadDashboardData();
    }
}

/**
 * Loads data for the dashboard, including stats and charts.
 */
async function loadDashboardData() {
    ui.showLoading();
    // Hide error/no-data messages for charts initially
    if(lineChartErrorElem) lineChartErrorElem.classList.add('hidden');
    if(topPodsErrorElem) topPodsErrorElem.classList.add('hidden');
    if(namespacePieErrorElem) namespacePieErrorElem.classList.add('hidden');
    if(severityPieErrorElem) severityPieErrorElem.classList.add('hidden');
    if(lineChartNoDataElem) lineChartNoDataElem.classList.add('hidden');
    if(topPodsNoDataElem) topPodsNoDataElem.classList.add('hidden');
    if(namespacePieNoDataElem) namespacePieNoDataElem.classList.add('hidden');
    if(severityPieNoDataElem) severityPieNoDataElem.classList.add('hidden');

    try {
        // Fetch statistics based on the selected time range (currentStatsDays)
        const statsData = await api.fetchStats(currentStatsDays);

        // Update dashboard summary cards
        ui.setText(totalIncidentsElem, statsData.totals?.incidents ?? '0');
        ui.setText(totalGeminiCallsElem, statsData.totals?.model_calls ?? '0');
        ui.setText(totalTelegramAlertsElem, statsData.totals?.telegram_alerts ?? '0');

        // --- Render Charts ---
        const chartDays = Math.max(currentStatsDays, 7);
        let statsForLineChart = statsData;
        if (chartDays !== currentStatsDays) {
            try {
                statsForLineChart = await api.fetchStats(chartDays);
            } catch (lineChartError) {
                console.error("DEBUG: Failed fetch for line chart:", lineChartError);
            }
        }
        charts.renderLineChart(lineChartCtx, lineChartErrorElem, lineChartNoDataElem, statsForLineChart.daily_stats_for_chart);
        charts.renderTopPodsBarChart(topPodsCtx, topPodsErrorElem, topPodsNoDataElem, statsData.top_problematic_pods || {});
        charts.renderNamespacePieChart(namespacePieCtx, namespacePieErrorElem, namespacePieNoDataElem, statsData.namespace_distribution || {});

        if (currentStatsDays === 1) {
            charts.renderSeverityPieChart(severityPieCtx, severityPieErrorElem, severityPieNoDataElem, statsData.severity_distribution_today || {});
            if (severityPieNoDataElem) ui.setText(severityPieNoDataElem, `Không có sự cố hôm nay.`);
        } else {
            charts.clearSeverityPieChart(severityPieNoDataElem, `Xem theo ngày để thấy phân loại mức độ.`);
        }
    } catch (error) {
        console.error("DEBUG: Failed to load dashboard data:", error);
        ui.setText(totalIncidentsElem, 'Lỗi');
        ui.setText(totalGeminiCallsElem, 'Lỗi');
        ui.setText(totalTelegramAlertsElem, 'Lỗi');
        charts.renderLineChart(lineChartCtx, lineChartErrorElem, lineChartNoDataElem, []);
        charts.renderTopPodsBarChart(topPodsCtx, topPodsErrorElem, topPodsNoDataElem, {});
        charts.renderNamespacePieChart(namespacePieCtx, namespacePieErrorElem, namespacePieNoDataElem, {});
        charts.clearSeverityPieChart(severityPieNoDataElem, "Lỗi tải dữ liệu.");
        if(lineChartErrorElem) lineChartErrorElem.classList.remove('hidden');
        if(topPodsErrorElem) topPodsErrorElem.classList.remove('hidden');
        if(namespacePieErrorElem) namespacePieErrorElem.classList.remove('hidden');
        if(severityPieErrorElem) severityPieErrorElem.classList.remove('hidden');
    } finally {
        ui.hideLoading();
    }
}

/**
 * Loads incident data based on current filters and pagination, then renders the table.
 * @param {boolean} forceReload - If true, forces fetching data even if table has content.
 */
async function loadIncidentsData(forceReload = false) {
    const tableBody = document.getElementById('incidents-table-body');
    if (!tableBody) { console.error("Incidents table body not found!"); return; }

    const tableBodyContent = tableBody.innerHTML.trim() || '';
    if (!forceReload && tableBodyContent && !tableBodyContent.includes('Đang tải dữ liệu') && !tableBodyContent.includes('Không tìm thấy')) {
        return;
    }

    ui.showLoading();
    tableBody.innerHTML = `<tr><td colspan="5" class="text-center py-6 text-gray-500">Đang tải dữ liệu...</td></tr>`;
    if (paginationControls) paginationControls.classList.add('hidden');
    incidentsDataCache = {};

    try {
        const data = await api.fetchIncidents(
            currentIncidentPage,
            currentPodFilter,
            currentSeverityFilter,
            currentIncidentStartDate,
            currentIncidentEndDate
        );
        const incidents = data.incidents;
        const pagination = data.pagination;
        totalIncidentPages = pagination.total_pages;

        tableBody.innerHTML = '';

        if (incidents.length === 0) {
            tableBody.innerHTML = `<tr><td colspan="5" class="text-center py-6 text-gray-500">Không tìm thấy sự cố nào khớp.</td></tr>`;
        } else {
            incidents.forEach(incident => {
                incidentsDataCache[incident.id] = incident;
                const row = document.createElement('tr');
                row.setAttribute('data-incident-id', incident.id);
                row.classList.add('cursor-pointer', 'hover:bg-gray-100');

                row.addEventListener('click', (event) => {
                    const clickedRow = event.currentTarget;
                    const incidentId = clickedRow.getAttribute('data-incident-id');
                    const incidentData = incidentsDataCache[incidentId];
                    if (incidentData) {
                        ui.openModal(incidentData);
                    } else {
                        console.error(`Incident data for ID ${incidentId} not found in cache!`);
                    }
                });

                const severityUpper = incident.severity ? incident.severity.toUpperCase() : '';
                if (severityUpper === 'CRITICAL') row.classList.add('bg-red-50');
                else if (severityUpper === 'ERROR') row.classList.add('bg-orange-50');
                else if (severityUpper === 'WARNING') row.classList.add('bg-yellow-50');

                const createCell = (content, isHtml = false, allowWrap = false) => {
                    const cell = document.createElement('td');
                    cell.className = 'px-4 py-3 text-sm text-gray-700 align-top';
                    ui.setText(cell, content, isHtml);
                    if (!isHtml) { cell.title = cell.textContent || ''; }
                    cell.classList.toggle('whitespace-normal', allowWrap);
                    cell.classList.toggle('whitespace-nowrap', !allowWrap);
                    cell.classList.toggle('overflow-hidden', !allowWrap);
                    cell.classList.toggle('text-ellipsis', !allowWrap);
                    return cell;
                };

                row.appendChild(createCell(ui.formatVietnameseDateTime(incident.timestamp)));
                row.appendChild(createCell(incident.pod_key)); // Giữ nguyên hiển thị pod_key, nhưng cột đã đổi tên
                row.appendChild(createCell(ui.createSeverityBadge(incident.severity), true));
                row.appendChild(createCell(incident.summary, false, true));
                row.appendChild(createCell(incident.initial_reasons, false, true));
                tableBody.appendChild(row);
            });
        }

        if (paginationControls) {
            if (totalIncidentPages > 0) {
                if (paginationInfo) ui.setText(paginationInfo, `Trang ${pagination.page} / ${totalIncidentPages} (Tổng: ${pagination.total_items})`);
                if (prevPageButton) prevPageButton.disabled = pagination.page <= 1;
                if (nextPageButton) nextPageButton.disabled = pagination.page >= totalIncidentPages;
                paginationControls.classList.remove('hidden');
            } else {
                paginationControls.classList.add('hidden');
            }
        }
    } catch (error) {
        console.error('DEBUG: Failed to load incidents data:', error);
        if (tableBody) tableBody.innerHTML = `<tr><td colspan="5" class="text-center py-6 text-red-500">Lỗi tải dữ liệu sự cố: ${error.message}.</td></tr>`;
        if (paginationControls) paginationControls.classList.add('hidden');
    } finally {
        ui.hideLoading();
    }
}

/**
 * Loads all global settings (Telegram, AI).
 */
function loadAllSettings() {
    settings.loadTelegramSettings();
    settings.loadAiSettings();
}

// --- Event Handlers ---

/**
 * Handles the click event for the "Configure" button on an agent row.
 * Fetches and displays the agent's configuration form.
 * @param {string} agentId - The ID of the agent to configure.
 * @param {string} clusterName - The name of the cluster the agent belongs to.
 * @param {object} clusterInfo - Object containing { k8sVersion, nodeCount }.
 */
async function handleConfigureAgentClick(agentId, clusterName, clusterInfo) { // Receive clusterInfo object
    console.log(`Configure button clicked for agent: ${agentId} (Cluster: ${clusterName})`);
    if (!agentConfigSection || !configAgentIdSpan) return; // Ensure elements exist

    currentConfiguringAgentId = agentId; // Store the agent ID being configured
    ui.showLoading();
    // Hide any previous status messages
    if(saveAgentGeneralConfigStatus) saveAgentGeneralConfigStatus.classList.add('hidden');
    if(saveAgentNsConfigStatus) saveAgentNsConfigStatus.classList.add('hidden');

    resetAgentConfigForms(); // Clear previous form data
    ui.setText(configAgentIdSpan, `${agentId} (${clusterName || 'N/A'})`); // Display agent ID/name

    // Get k8sVersion and nodeCount directly from the passed clusterInfo object
    const k8sVersion = clusterInfo?.k8sVersion || 'N/A';
    const nodeCount = clusterInfo?.nodeCount !== null ? clusterInfo.nodeCount : 'N/A';
    console.log(`Received cluster info: K8s=${k8sVersion}, Nodes=${nodeCount}`);

    try {
        // Fetch the specific agent's configuration from the backend
        const agentConfig = await api.fetchAgentConfig(agentId);
        console.log("Fetched agent config:", agentConfig);

        // Populate the form fields with the fetched config and cluster info
        ui.populateAgentConfigForm(agentConfig, { k8sVersion, nodeCount }); // Pass clusterInfo object

        // Render the namespace selection list for this agent
        await renderAgentNamespaceList(agentConfig.monitored_namespaces || []);

        // Show the configuration section and switch to the general tab
        agentConfigSection.classList.remove('hidden');
        switchAgentConfigTab('agent-general'); // Default to general tab

    } catch (error) {
        // Handle errors fetching or displaying the configuration
        console.error(`Error loading configuration for agent ${agentId}:`, error);
        alert(`Không thể tải cấu hình cho agent ${agentId}: ${error.message}`);
        agentConfigSection.classList.add('hidden'); // Hide section on error
    } finally {
        ui.hideLoading();
    }
}


/**
 * Resets the agent configuration forms (General and Namespaces) to default states.
 */
function resetAgentConfigForms() {
     // Reset general settings inputs
     if (agentScanIntervalInput) agentScanIntervalInput.value = '30'; // Default value
     if (agentRestartThresholdInput) agentRestartThresholdInput.value = '5'; // Default value
     if (agentLokiScanLevelSelect) agentLokiScanLevelSelect.value = 'INFO'; // Default value

     // Clear namespace list and show loading text
     if (agentNamespaceListDiv) agentNamespaceListDiv.innerHTML = '';
     if (agentNamespaceLoadingText) {
         ui.setText(agentNamespaceLoadingText, 'Chọn agent để tải namespace...');
         agentNamespaceLoadingText.classList.remove('hidden');
     }

     // Reset cluster info display
     ui.populateAgentConfigForm({}, { k8sVersion: 'N/A', nodeCount: 'N/A' });
 }

/**
 * Fetches available namespaces and renders the checklist for agent configuration.
 * @param {string[]} monitoredNamespaces - Array of namespaces currently monitored by the agent.
 */
async function renderAgentNamespaceList(monitoredNamespaces = []) {
    if (!agentNamespaceListDiv || !agentNamespaceLoadingText) return; // Ensure elements exist

    // Clear previous list and show loading text
    agentNamespaceListDiv.innerHTML = '';
    agentNamespaceLoadingText.classList.remove('hidden');
    ui.setText(agentNamespaceLoadingText, 'Đang tải danh sách namespace khả dụng...');

    try {
        // Fetch the list of all available namespaces from the API
        const availableNamespaces = await api.fetchAvailableNamespaces();
        agentNamespaceLoadingText.classList.add('hidden'); // Hide loading text

        if (availableNamespaces.length === 0) {
            // Display message if no namespaces are found
            agentNamespaceListDiv.innerHTML = `<p class="text-gray-500 col-span-full text-center py-4">Không tìm thấy namespace nào.</p>`;
        } else {
            // Create a checkbox and label for each available namespace
            availableNamespaces.forEach(ns => {
                const isChecked = monitoredNamespaces.includes(ns); // Check if this namespace is currently monitored
                const div = document.createElement('div');
                div.className = 'namespace-item text-sm flex items-center';

                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.id = `agent-ns-${ns}`; // Unique ID for the checkbox
                checkbox.value = ns;
                checkbox.checked = isChecked;
                checkbox.className = 'form-checkbox h-4 w-4 text-indigo-600 transition duration-150 ease-in-out rounded border-gray-300 focus:ring-indigo-500';

                const label = document.createElement('label');
                label.htmlFor = `agent-ns-${ns}`; // Link label to checkbox
                label.textContent = ns;
                label.className = 'ml-2 text-gray-700 cursor-pointer select-none';

                div.appendChild(checkbox);
                div.appendChild(label);
                agentNamespaceListDiv.appendChild(div);
            });
        }
    } catch (error) {
        // Handle errors fetching or rendering namespaces
        console.error("Error rendering agent namespace list:", error);
        agentNamespaceListDiv.innerHTML = `<p class="text-red-500 col-span-full text-center py-4">Lỗi hiển thị danh sách namespace.</p>`;
        agentNamespaceLoadingText.classList.add('hidden');
    }
}

/**
 * Switches the visible tab in the agent configuration section.
 * @param {string} targetTabId - The ID of the tab content to show (e.g., 'agent-general').
 */
function switchAgentConfigTab(targetTabId) {
    // Update tab button styles
    agentConfigTabs.forEach(tab => {
        const isTarget = tab.getAttribute('data-tab') === targetTabId;
        // Apply active styles (border, text color)
        tab.classList.toggle('border-indigo-500', isTarget);
        tab.classList.toggle('text-indigo-600', isTarget);
        // Apply inactive styles
        tab.classList.toggle('border-transparent', !isTarget);
        tab.classList.toggle('text-gray-500', !isTarget);
        tab.classList.toggle('hover:text-gray-700', !isTarget);
        tab.classList.toggle('hover:border-gray-300', !isTarget);
    });
    // Show/hide tab content panes
    agentConfigTabContents.forEach(content => {
        content.classList.toggle('hidden', content.id !== `${targetTabId}-tab`);
    });
}

/**
 * Handles saving the general configuration settings for the currently selected agent.
 */
async function handleSaveAgentGeneralConfig() {
    // Ensure an agent is selected and elements exist
    if (!currentConfiguringAgentId || !saveAgentGeneralConfigButton || !saveAgentGeneralConfigStatus) return;

    ui.showLoading();
    saveAgentGeneralConfigButton.disabled = true; // Disable button during save
    ui.showStatusMessage(saveAgentGeneralConfigStatus, 'Đang lưu...', 'info'); // Show saving message

    let configData;
    try {
        // Read and validate form values
        const scanInterval = parseInt(agentScanIntervalInput?.value ?? '0');
        const restartThreshold = parseInt(agentRestartThresholdInput?.value ?? '0');
        const scanLevel = agentLokiScanLevelSelect?.value ?? 'INFO';

        if (isNaN(scanInterval) || scanInterval < 10) {
            throw new Error("Tần suất quét phải là số >= 10.");
        }
        if (isNaN(restartThreshold) || restartThreshold < 1) {
            throw new Error("Ngưỡng khởi động lại phải là số >= 1.");
        }

        // Prepare data payload
        configData = {
            scan_interval_seconds: scanInterval,
            restart_count_threshold: restartThreshold,
            loki_scan_min_level: scanLevel,
        };

        // Call API to save the configuration
        await api.saveAgentGeneralConfig(currentConfiguringAgentId, configData);
        ui.showStatusMessage(saveAgentGeneralConfigStatus, 'Đã lưu thành công!', 'success'); // Show success

    } catch (error) {
        // Handle validation or API errors
        console.error(`Error saving general config for agent ${currentConfiguringAgentId}:`, error);
        if (error.message.includes("phải là số")) {
             // Show specific validation error in alert
             alert(`Lỗi dữ liệu nhập: ${error.message}`);
             // Clear status message if it was just a validation error
             ui.showStatusMessage(saveAgentGeneralConfigStatus, '', 'info');
             if(saveAgentGeneralConfigStatus) saveAgentGeneralConfigStatus.classList.add('hidden');
        } else {
             // Show API or other errors in the status message area
             ui.showStatusMessage(saveAgentGeneralConfigStatus, `Lỗi: ${error.message}`, 'error');
        }
    } finally {
        ui.hideLoading();
        saveAgentGeneralConfigButton.disabled = false; // Re-enable button
        // Hide status message after a delay, unless it's an error
        if (saveAgentGeneralConfigStatus && !saveAgentGeneralConfigStatus.classList.contains('hidden') && !saveAgentGeneralConfigStatus.textContent.startsWith('Lỗi')) {
            ui.hideStatusMessage(saveAgentGeneralConfigStatus);
        }
    }
}

/**
 * Handles saving the monitored namespace selection for the currently selected agent.
 */
async function handleSaveAgentNamespaces() {
    // Ensure an agent is selected and elements exist
    if (!currentConfiguringAgentId || !saveAgentNsConfigButton || !saveAgentNsConfigStatus || !agentNamespaceListDiv) return;

    ui.showLoading();
    saveAgentNsConfigButton.disabled = true; // Disable button during save
    ui.showStatusMessage(saveAgentNsConfigStatus, 'Đang lưu...', 'info'); // Show saving message

    // Get selected namespaces from checkboxes
    const selectedNamespaces = [];
    agentNamespaceListDiv.querySelectorAll('input[type="checkbox"]:checked').forEach(checkbox => {
        selectedNamespaces.push(checkbox.value);
    });

    try {
        // Call API to save the selected namespaces
        await api.saveAgentMonitoredNamespaces(currentConfiguringAgentId, selectedNamespaces);
        ui.showStatusMessage(saveAgentNsConfigStatus, 'Đã lưu thành công!', 'success'); // Show success

    } catch (error) {
        // Handle API errors
        console.error(`Error saving namespaces for agent ${currentConfiguringAgentId}:`, error);
        ui.showStatusMessage(saveAgentNsConfigStatus, `Lỗi: ${error.message}`, 'error');
    } finally {
        ui.hideLoading();
        saveAgentNsConfigButton.disabled = false; // Re-enable button
        ui.hideStatusMessage(saveAgentNsConfigStatus); // Hide status message after delay
    }
}

/**
 * Handles closing the agent configuration section.
 */
function handleCloseAgentConfig() {
    if (agentConfigSection) {
        agentConfigSection.classList.add('hidden'); // Hide the section
    }
    currentConfiguringAgentId = null; // Reset the currently configured agent ID
}


/**
 * Handles the click event for the "Edit" button on a user row.
 * Opens the edit user modal if the current user is an admin.
 * @param {object} user - The user data object for the row clicked.
 */
function handleEditUserClick(user) {
    console.log("Edit button clicked for user:", user);
    if (currentUserRole === 'admin') {
        ui.openEditUserModal(user); // Open the modal with user data
    } else {
        alert("Bạn không có quyền chỉnh sửa người dùng."); // Show alert if not admin
    }
}


/**
 * Handles the submission of the create user form.
 * Validates input and calls the API to create a new user.
 * @param {Event} event - The form submission event.
 */
async function handleCreateUserSubmit(event) {
    event.preventDefault(); // Prevent default form submission
    if (!createUserForm || !createUserButton || !createUserStatus) return; // Ensure elements exist

    // --- Input Validation ---
    const password = newPasswordInput?.value;
    const confirmPassword = confirmPasswordInput?.value;

    // Check if passwords match
    if (password !== confirmPassword) {
        if (passwordMatchError) passwordMatchError.classList.remove('hidden'); // Show mismatch error
        if (confirmPasswordInput) confirmPasswordInput.focus(); // Focus confirm password field
        return; // Stop submission
    } else {
        if (passwordMatchError) passwordMatchError.classList.add('hidden'); // Hide mismatch error
    }

    // Check password length
     if (password.length < 6) {
         alert("Mật khẩu phải có ít nhất 6 ký tự.");
         if (newPasswordInput) newPasswordInput.focus(); // Focus password field
         return; // Stop submission
     }
    // --- End Input Validation ---

    ui.showLoading();
    createUserButton.disabled = true; // Disable button during creation
    ui.showStatusMessage(createUserStatus, 'Đang tạo user...', 'info'); // Show creating message

    // Prepare user data payload
    const userData = {
        username: createUserForm.elements['username'].value,
        password: password,
        fullname: createUserForm.elements['fullname'].value,
        role: createUserForm.elements['role'].value
    };

    try {
        // Call API to create the user
        const result = await api.createUser(userData);
        ui.showStatusMessage(createUserStatus, result.message || 'Tạo user thành công!', 'success'); // Show success
        ui.closeCreateUserModal(); // Close the modal on success
        await loadUserManagementData(); // Reload the user list to show the new user

    } catch (error) {
        // Handle API errors
        console.error("Error creating user:", error);
        ui.showStatusMessage(createUserStatus, `Lỗi: ${error.message}`, 'error');
    } finally {
        ui.hideLoading();
        createUserButton.disabled = false; // Re-enable button
        // Hide status message after delay, unless it's an error
        if (!createUserStatus.textContent.startsWith('Lỗi')) {
            ui.hideStatusMessage(createUserStatus);
        }
    }
}


/**
 * Handles the submission of the edit user form.
 * Validates input and calls the API to update user details.
 * @param {Event} event - The form submission event.
 */
async function handleEditUserSubmit(event) {
    event.preventDefault(); // Prevent default form submission
    if (!editUserForm || !saveUserChangesButton || !editUserStatus) return; // Ensure elements exist

    // Get user ID from hidden input
    const userId = document.getElementById('edit-user-id')?.value;
    if (!userId) {
        console.error("User ID not found in edit form.");
        ui.showStatusMessage(editUserStatus, 'Lỗi: Không tìm thấy ID người dùng.', 'error');
        ui.hideStatusMessage(editUserStatus); // Hide error after delay
        return; // Stop submission
    }

    ui.showLoading();
    saveUserChangesButton.disabled = true; // Disable button during update
    ui.showStatusMessage(editUserStatus, 'Đang lưu thay đổi...', 'info'); // Show saving message

    // Prepare data payload with only the fields to update
    const userDataToUpdate = {
        fullname: document.getElementById('edit-fullname')?.value,
        role: document.getElementById('edit-role')?.value
    };

    try {
        // Call API to update the user
        const result = await api.updateUser(userId, userDataToUpdate);
        ui.showStatusMessage(editUserStatus, result.message || 'Cập nhật thành công!', 'success'); // Show success
        ui.closeEditUserModal(); // Close the modal on success
        await loadUserManagementData(); // Reload user list to reflect changes

    } catch (error) {
        // Handle API errors
        console.error(`Error updating user ${userId}:`, error);
        ui.showStatusMessage(editUserStatus, `Lỗi: ${error.message}`, 'error');
    } finally {
        ui.hideLoading();
        saveUserChangesButton.disabled = false; // Re-enable button
        // Hide status message after delay, unless it's an error
        if (!editUserStatus.textContent.startsWith('Lỗi')) {
             ui.hideStatusMessage(editUserStatus);
        }
    }
}


/**
 * Applies UI changes based on the current user's role (admin or user).
 * Hides/shows admin-only sections and disables relevant buttons.
 */
function applyRolePermissions() {
    const isAdmin = (currentUserRole === 'admin');
    console.log(`Applying permissions for role: ${currentUserRole}, isAdmin: ${isAdmin}`);

    // Hide/show admin-only sidebar items
    document.querySelector('a[href="#settings"]')?.parentElement?.classList.toggle('hidden', !isAdmin);
    document.querySelector('a[href="#user-management"]')?.parentElement?.classList.toggle('hidden', !isAdmin);

    // Disable configuration save buttons if not admin
    if (saveTelegramConfigButton) saveTelegramConfigButton.disabled = !isAdmin;
    if (saveAiConfigButton) saveAiConfigButton.disabled = !isAdmin;
    if (saveAgentGeneralConfigButton) saveAgentGeneralConfigButton.disabled = !isAdmin;
    if (saveAgentNsConfigButton) saveAgentNsConfigButton.disabled = !isAdmin;

    // Disable user management action buttons if not admin
    if (createUserButton) createUserButton.disabled = !isAdmin; // Disable the submit button inside create modal
    if (saveUserChangesButton) saveUserChangesButton.disabled = !isAdmin; // Disable the submit button inside edit modal
    if (openCreateUserModalButton) openCreateUserModalButton.disabled = !isAdmin; // Disable the button to open the create modal
    if (addAgentButton) addAgentButton.disabled = !isAdmin; // Disable add agent button if not admin

    // Note: The edit buttons within the user table are handled in ui.renderUserTable
}


// --- Initialization and Event Listeners ---
document.addEventListener('DOMContentLoaded', () => {
    // Get user role from body data attribute (set in index.html)
    currentUserRole = document.body.dataset.userRole || 'user';
    applyRolePermissions(); // Apply role-based UI restrictions

    // Set the initial active section to 'dashboard' and load its data
    ui.setActiveSection('dashboard', loadActiveSectionData);

    // --- Sidebar Navigation Listener ---
    sidebarItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault(); // Prevent default anchor link behavior
            const targetId = item.getAttribute('href')?.substring(1); // Get section ID from href

            // Prevent navigation to admin sections if user is not admin
            if ((targetId === 'settings' || targetId === 'user-management') && currentUserRole !== 'admin') {
                console.warn("Attempted to navigate to admin section without permission.");
                return; // Do nothing
            }

            // Set the clicked section as active and load its data
            if(targetId) {
                // Use the correct section ID ('agent') when loading data
                ui.setActiveSection(targetId, loadActiveSectionData);
            }
        });
    });

    // --- Incident Page Listeners ---
    if (filterButton) {
        filterButton.addEventListener('click', () => {
            currentPodFilter = podFilterInput?.value || '';
            currentSeverityFilter = severityFilterSelect?.value || '';
            currentIncidentPage = 1;
            loadIncidentsData(true);
        });
    }
    if (podFilterInput) {
        podFilterInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') filterButton?.click(); });
    }
    if (refreshIncidentsButton) {
        refreshIncidentsButton.addEventListener('click', () => { loadIncidentsData(true); });
    }
    if (prevPageButton) {
        prevPageButton.addEventListener('click', () => {
            if (currentIncidentPage > 1) { currentIncidentPage--; loadIncidentsData(true); }
        });
    }
    if (nextPageButton) {
        nextPageButton.addEventListener('click', () => {
            if (currentIncidentPage < totalIncidentPages) { currentIncidentPage++; loadIncidentsData(true); }
        });
    }

    // --- Dashboard Page Listeners ---
    timeRangeButtons.forEach(button => {
        button.addEventListener('click', () => {
            const days = parseInt(button.getAttribute('data-days'));
            currentStatsDays = days;
            loadDashboardData();
            timeRangeButtons.forEach(btn => {
                 btn.classList.remove('bg-blue-500', 'text-white');
                 btn.classList.add('bg-gray-300', 'text-gray-700');
                 btn.disabled = false;
            });
            button.classList.add('bg-blue-500', 'text-white');
            button.classList.remove('bg-gray-300', 'text-gray-700');
            button.disabled = true;
        });
         if (button.getAttribute('data-days') === '1') { button.click(); }
         else { button.disabled = false; }
    });

    // --- Settings Page Listeners ---
    if (saveTelegramConfigButton) saveTelegramConfigButton.addEventListener('click', settings.saveTelegramSettings);
    if (saveAiConfigButton) saveAiConfigButton.addEventListener('click', settings.saveAiSettings);
    if (enableAiToggle) enableAiToggle.addEventListener('change', settings.handleAiToggleChange);

    // --- Agent Configuration Listeners ---
    if (agentConfigTabs) {
        agentConfigTabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const targetTabId = tab.getAttribute('data-tab');
                if (targetTabId) { switchAgentConfigTab(targetTabId); }
            });
        });
    }
    if (saveAgentGeneralConfigButton) saveAgentGeneralConfigButton.addEventListener('click', handleSaveAgentGeneralConfig);
    if (saveAgentNsConfigButton) saveAgentNsConfigButton.addEventListener('click', handleSaveAgentNamespaces);
    if (closeAgentConfigButton) closeAgentConfigButton.addEventListener('click', handleCloseAgentConfig);
    if (refreshAgentStatusButton) refreshAgentStatusButton.addEventListener('click', loadAgentStatus);
    // Listener for the "Add Agent" button
    if (addAgentButton) {
        addAgentButton.addEventListener('click', ui.openAddAgentModal);
    }


    // --- User Management Listeners ---
    if (createUserForm) createUserForm.addEventListener('submit', handleCreateUserSubmit);
    if (editUserForm) editUserForm.addEventListener('submit', handleEditUserSubmit);
    if (openCreateUserModalButton) {
        openCreateUserModalButton.addEventListener('click', ui.openCreateUserModal);
    }
    // Password confirmation validation
    if (confirmPasswordInput) {
        confirmPasswordInput.addEventListener('input', () => {
            if (newPasswordInput?.value !== confirmPasswordInput.value) {
                if (passwordMatchError) passwordMatchError.classList.remove('hidden');
            } else {
                if (passwordMatchError) passwordMatchError.classList.add('hidden');
            }
        });
    }
     if (newPasswordInput) {
         newPasswordInput.addEventListener('input', () => {
             if (confirmPasswordInput?.value && newPasswordInput.value !== confirmPasswordInput.value) {
                 if (passwordMatchError) passwordMatchError.classList.remove('hidden');
             } else {
                 if (passwordMatchError) passwordMatchError.classList.add('hidden');
             }
         });
     }

    // --- Modal Closing Listeners ---
    if (modalCloseButton) modalCloseButton.addEventListener('click', ui.closeModal); // Incident modal
    if (editUserModalCloseButton) editUserModalCloseButton.addEventListener('click', ui.closeEditUserModal); // Edit user modal
    if (createUserModalCloseButton) createUserModalCloseButton.addEventListener('click', ui.closeCreateUserModal); // Create user modal
    // Note: Add Agent modal close listeners are now inside ui.js

    // Close modals on Escape key press
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
             if (incidentModalElement?.classList.contains('modal-visible')) ui.closeModal();
             if (editUserModal && !editUserModal.classList.contains('hidden')) ui.closeEditUserModal();
             if (createUserModal && !createUserModal.classList.contains('hidden')) ui.closeCreateUserModal();
             // Check if addAgentModal exists and is visible before trying to close
             if (addAgentModal && addAgentModal.classList.contains('flex')) ui.closeAddAgentModal();
        }
    });

    // Close modals on overlay click
    if (incidentModalElement) {
        incidentModalElement.addEventListener('click', (event) => {
            if (event.target === incidentModalElement) ui.closeModal();
        });
    }
    if (editUserModal) {
        editUserModal.addEventListener('click', (event) => {
            if (event.target === editUserModal) ui.closeEditUserModal();
        });
    }
    if (createUserModal) {
        createUserModal.addEventListener('click', (event) => {
            if (event.target === createUserModal) ui.closeCreateUserModal();
        });
    }
    // Add overlay click listener for Add Agent modal (already in ui.js, ensure it works)


    console.log("DEBUG: main.js loaded and event listeners attached.");

}); // End DOMContentLoaded
