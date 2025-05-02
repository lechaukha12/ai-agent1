// portal/static/js/main.js
import * as api from './api.js';
import * as ui from './ui.js';
import * as charts from './charts.js';
import * as settings from './settings.js';

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
// Charts
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
// Incidents
const incidentsTableBody = document.getElementById('incidents-table-body');
const podFilterInput = document.getElementById('pod-filter');
const severityFilterSelect = document.getElementById('severity-filter');
const filterButton = document.getElementById('filter-button');
const refreshIncidentsButton = document.getElementById('refresh-incidents-button');
const paginationControls = document.getElementById('pagination-controls');
const paginationInfo = document.getElementById('pagination-info');
const prevPageButton = document.getElementById('prev-page');
const nextPageButton = document.getElementById('next-page');
// Dashboard Stats
const totalIncidentsElem = document.getElementById('total-incidents');
const totalGeminiCallsElem = document.getElementById('total-gemini-calls');
const totalTelegramAlertsElem = document.getElementById('total-telegram-alerts');
const timeRangeButtons = document.querySelectorAll('.time-range-btn');
// Settings (Global)
const saveTelegramConfigButton = document.getElementById('save-telegram-config-button');
const saveAiConfigButton = document.getElementById('save-ai-config-button');
const enableAiToggle = document.getElementById('enable-ai-toggle');
// General UI
const sidebarItems = document.querySelectorAll('.sidebar-item');
const modalCloseButton = document.getElementById('modal-close-button'); // Incident modal close
const modalOverlay = document.querySelector('.modal-overlay'); // Incident modal overlay
// Agent Monitoring
const agentStatusTableBody = document.getElementById('agent-status-table-body');
const agentStatusErrorElem = document.getElementById('agent-status-error');
const refreshAgentStatusButton = document.getElementById('refresh-agent-status-button');
// Agent Config Section
const agentConfigSection = document.getElementById('agent-config-section');
const configAgentIdSpan = document.getElementById('config-agent-id');
const agentConfigTabs = document.querySelectorAll('.agent-config-tab');
const agentConfigTabContents = document.querySelectorAll('.agent-config-tab-content');
// Agent General Config Form
const agentScanIntervalInput = document.getElementById('agent-scan-interval');
const agentRestartThresholdInput = document.getElementById('agent-restart-threshold');
const agentLokiScanLevelSelect = document.getElementById('agent-loki-scan-level');
const saveAgentGeneralConfigButton = document.getElementById('save-agent-general-config-button');
const saveAgentGeneralConfigStatus = document.getElementById('save-agent-general-config-status');
// Agent Namespace Config Form
const agentNamespaceListDiv = document.getElementById('agent-namespace-list');
const agentNamespaceLoadingText = document.getElementById('agent-namespace-loading-text');
const saveAgentNsConfigButton = document.getElementById('save-agent-ns-config-button');
const saveAgentNsConfigStatus = document.getElementById('save-agent-ns-config-status');
// Agent Config Close Button
const closeAgentConfigButton = document.getElementById('close-agent-config-button');
// User Management
const usersTableBody = document.getElementById('users-table-body');
const usersListErrorElem = document.getElementById('users-list-error');
// Create User Modal Elements
const openCreateUserModalButton = document.getElementById('open-create-user-modal-button'); // Button to open the modal
const createUserModal = document.getElementById('create-user-modal'); // The modal itself
const createUserModalCloseButton = document.getElementById('create-user-modal-close-button'); // Close button inside the modal
const createUserForm = document.getElementById('create-user-form');
const createUserButton = document.getElementById('create-user-button'); // Submit button inside the modal
const createUserStatus = document.getElementById('create-user-status');
const newPasswordInput = document.getElementById('new-password');
const confirmPasswordInput = document.getElementById('confirm-password');
const passwordMatchError = document.getElementById('password-match-error');
// Edit User Modal Elements
const editUserModal = document.getElementById('edit-user-modal');
const editUserModalCloseButton = document.getElementById('edit-user-modal-close-button');
const editUserForm = document.getElementById('edit-user-form');
const editUserStatus = document.getElementById('edit-user-status');
const saveUserChangesButton = document.getElementById('save-user-changes-button');

// --- Data Loading Functions ---

async function loadAgentStatus() {
    if (!agentStatusTableBody || !agentStatusErrorElem) {
        console.warn("Agent status table elements not found. Skipping load.");
        return;
    }
    ui.showLoading();
    agentStatusTableBody.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-gray-500">Đang tải trạng thái agent...</td></tr>`;
    agentStatusErrorElem.classList.add('hidden');
    try {
        const data = await api.fetchAgentStatus();
        const agents = data.active_agents || [];
        ui.renderAgentStatusTable(agents, (agentId, clusterName, clickedRow) => {
            handleConfigureAgentClick(agentId, clusterName, clickedRow);
        }, currentUserRole);
    } catch (error) {
        console.error("DEBUG: Failed to load agent status:", error);
        ui.showAgentStatusError(`Lỗi tải trạng thái agent: ${error.message}`);
    } finally {
        ui.hideLoading();
    }
}

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

function loadActiveSectionData(activeSectionId) {
    console.log(`DEBUG: Loading data for section: ${activeSectionId}`);
    if (agentConfigSection) agentConfigSection.classList.add('hidden');
    currentConfiguringAgentId = null;
    switch (activeSectionId) {
        case 'dashboard': loadDashboardData(); break;
        case 'incidents': loadIncidentsData(true); break;
        case 'kubernetes-monitoring': loadAgentStatus(); break;
        case 'settings': settings.loadTelegramSettings(); settings.loadAiSettings(); break;
        case 'user-management': loadUserManagementData(); break;
        default: console.warn(`Unhandled section ID: ${activeSectionId}`); loadDashboardData();
    }
}

async function loadDashboardData() {
    ui.showLoading();
    if(lineChartErrorElem) lineChartErrorElem.classList.add('hidden');
    if(topPodsErrorElem) topPodsErrorElem.classList.add('hidden');
    if(namespacePieErrorElem) namespacePieErrorElem.classList.add('hidden');
    if(severityPieErrorElem) severityPieErrorElem.classList.add('hidden');
    if(lineChartNoDataElem) lineChartNoDataElem.classList.add('hidden');
    if(topPodsNoDataElem) topPodsNoDataElem.classList.add('hidden');
    if(namespacePieNoDataElem) namespacePieNoDataElem.classList.add('hidden');
    if(severityPieNoDataElem) severityPieNoDataElem.classList.add('hidden');
    try {
        const statsData = await api.fetchStats(currentStatsDays);
        ui.setText(totalIncidentsElem, statsData.totals?.incidents ?? '0');
        ui.setText(totalGeminiCallsElem, statsData.totals?.model_calls ?? '0');
        ui.setText(totalTelegramAlertsElem, statsData.totals?.telegram_alerts ?? '0');
        const chartDays = Math.max(currentStatsDays, 7);
        let statsForLineChart = statsData;
        if (chartDays !== currentStatsDays) {
            try { statsForLineChart = await api.fetchStats(chartDays); }
            catch (lineChartError) { console.error("DEBUG: Failed fetch for line chart:", lineChartError); }
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
        ui.setText(totalIncidentsElem, 'Lỗi'); ui.setText(totalGeminiCallsElem, 'Lỗi'); ui.setText(totalTelegramAlertsElem, 'Lỗi');
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

async function loadIncidentsData(forceReload = false) {
    const tableBody = document.getElementById('incidents-table-body');
    if (!tableBody) { console.error("Incidents table body not found!"); return; }
    const tableBodyContent = tableBody.innerHTML.trim() || '';
    if (!forceReload && tableBodyContent && !tableBodyContent.includes('Đang tải dữ liệu') && !tableBodyContent.includes('Không tìm thấy')) { return; }
    ui.showLoading();
    tableBody.innerHTML = `<tr><td colspan="5" class="text-center py-6 text-gray-500">Đang tải dữ liệu...</td></tr>`;
    if (paginationControls) paginationControls.classList.add('hidden');
    incidentsDataCache = {};
    try {
        const data = await api.fetchIncidents(currentIncidentPage, currentPodFilter, currentSeverityFilter, currentIncidentStartDate, currentIncidentEndDate);
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
                    if (incidentData) ui.openModal(incidentData);
                    else console.error(`Incident data for ID ${incidentId} not found in cache!`);
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
                row.appendChild(createCell(incident.pod_key));
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

function loadAllSettings() {
    settings.loadTelegramSettings();
    settings.loadAiSettings();
}

// --- Event Handlers ---

async function handleConfigureAgentClick(agentId, clusterName, clickedRow) {
    console.log(`Configure button clicked for agent: ${agentId} (Cluster: ${clusterName})`);
    if (!agentConfigSection || !configAgentIdSpan || !clickedRow) return;
    currentConfiguringAgentId = agentId;
    ui.showLoading();
    if(saveAgentGeneralConfigStatus) saveAgentGeneralConfigStatus.classList.add('hidden');
    if(saveAgentNsConfigStatus) saveAgentNsConfigStatus.classList.add('hidden');
    resetAgentConfigForms();
    ui.setText(configAgentIdSpan, `${agentId} (${clusterName || 'N/A'})`);
    let k8sVersion = 'N/A';
    let nodeCount = 'N/A';
    try {
        // Use more specific selectors based on added classes
        const versionCell = clickedRow.querySelector('td.agent-k8s-version-cell');
        const countCell = clickedRow.querySelector('td.agent-node-count-cell');
        if (versionCell) k8sVersion = versionCell.textContent || 'N/A';
        if (countCell) nodeCount = countCell.textContent || 'N/A';
        console.log(`Extracted from row: K8s=${k8sVersion}, Nodes=${nodeCount}`);
    } catch (e) {
        console.error("Error extracting cluster info from table row:", e);
    }
    try {
        const agentConfig = await api.fetchAgentConfig(agentId);
        console.log("Fetched agent config:", agentConfig);
        ui.populateAgentConfigForm(agentConfig, { k8sVersion, nodeCount });
        await renderAgentNamespaceList(agentConfig.monitored_namespaces || []);
        agentConfigSection.classList.remove('hidden');
        switchAgentConfigTab('agent-general');
    } catch (error) {
        console.error(`Error loading configuration for agent ${agentId}:`, error);
        alert(`Không thể tải cấu hình cho agent ${agentId}: ${error.message}`);
        agentConfigSection.classList.add('hidden');
    } finally {
        ui.hideLoading();
    }
}


function resetAgentConfigForms() {
     if (agentScanIntervalInput) agentScanIntervalInput.value = '30';
     if (agentRestartThresholdInput) agentRestartThresholdInput.value = '5';
     if (agentLokiScanLevelSelect) agentLokiScanLevelSelect.value = 'INFO';
     if (agentNamespaceListDiv) agentNamespaceListDiv.innerHTML = '';
     if (agentNamespaceLoadingText) {
         ui.setText(agentNamespaceLoadingText, 'Chọn agent để tải namespace...');
         agentNamespaceLoadingText.classList.remove('hidden');
     }
     ui.populateAgentConfigForm({}, { k8sVersion: 'N/A', nodeCount: 'N/A' });
 }


async function renderAgentNamespaceList(monitoredNamespaces = []) {
    if (!agentNamespaceListDiv || !agentNamespaceLoadingText) return;
    agentNamespaceListDiv.innerHTML = '';
    agentNamespaceLoadingText.classList.remove('hidden');
    ui.setText(agentNamespaceLoadingText, 'Đang tải danh sách namespace khả dụng...');
    try {
        const availableNamespaces = await api.fetchAvailableNamespaces();
        agentNamespaceLoadingText.classList.add('hidden');
        if (availableNamespaces.length === 0) {
            agentNamespaceListDiv.innerHTML = `<p class="text-gray-500 col-span-full text-center py-4">Không tìm thấy namespace nào.</p>`;
        } else {
            availableNamespaces.forEach(ns => {
                const isChecked = monitoredNamespaces.includes(ns);
                const div = document.createElement('div');
                div.className = 'namespace-item text-sm flex items-center';
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.id = `agent-ns-${ns}`;
                checkbox.value = ns;
                checkbox.checked = isChecked;
                checkbox.className = 'form-checkbox h-4 w-4 text-indigo-600 transition duration-150 ease-in-out rounded border-gray-300 focus:ring-indigo-500';
                const label = document.createElement('label');
                label.htmlFor = `agent-ns-${ns}`;
                label.textContent = ns;
                label.className = 'ml-2 text-gray-700 cursor-pointer select-none';
                div.appendChild(checkbox);
                div.appendChild(label);
                agentNamespaceListDiv.appendChild(div);
            });
        }
    } catch (error) {
        console.error("Error rendering agent namespace list:", error);
        agentNamespaceListDiv.innerHTML = `<p class="text-red-500 col-span-full text-center py-4">Lỗi hiển thị danh sách namespace.</p>`;
        agentNamespaceLoadingText.classList.add('hidden');
    }
}

function switchAgentConfigTab(targetTabId) {
    agentConfigTabs.forEach(tab => {
        const isTarget = tab.getAttribute('data-tab') === targetTabId;
        tab.classList.toggle('border-indigo-500', isTarget);
        tab.classList.toggle('text-indigo-600', isTarget);
        tab.classList.toggle('border-transparent', !isTarget);
        tab.classList.toggle('text-gray-500', !isTarget);
        tab.classList.toggle('hover:text-gray-700', !isTarget);
        tab.classList.toggle('hover:border-gray-300', !isTarget);
    });
    agentConfigTabContents.forEach(content => {
        content.classList.toggle('hidden', content.id !== `${targetTabId}-tab`);
    });
}

async function handleSaveAgentGeneralConfig() {
    if (!currentConfiguringAgentId || !saveAgentGeneralConfigButton || !saveAgentGeneralConfigStatus) return;
    ui.showLoading();
    saveAgentGeneralConfigButton.disabled = true;
    ui.showStatusMessage(saveAgentGeneralConfigStatus, 'Đang lưu...', 'info');
    let configData;
    try {
        const scanInterval = parseInt(agentScanIntervalInput?.value ?? '0');
        const restartThreshold = parseInt(agentRestartThresholdInput?.value ?? '0');
        const scanLevel = agentLokiScanLevelSelect?.value ?? 'INFO';
        if (isNaN(scanInterval) || scanInterval < 10) throw new Error("Tần suất quét phải là số >= 10.");
        if (isNaN(restartThreshold) || restartThreshold < 1) throw new Error("Ngưỡng khởi động lại phải là số >= 1.");
        configData = {
            scan_interval_seconds: scanInterval,
            restart_count_threshold: restartThreshold,
            loki_scan_min_level: scanLevel,
        };
        await api.saveAgentGeneralConfig(currentConfiguringAgentId, configData);
        ui.showStatusMessage(saveAgentGeneralConfigStatus, 'Đã lưu thành công!', 'success');
    } catch (error) {
        console.error(`Error saving general config for agent ${currentConfiguringAgentId}:`, error);
        if (error.message.includes("phải là số")) {
             alert(`Lỗi dữ liệu nhập: ${error.message}`);
             ui.showStatusMessage(saveAgentGeneralConfigStatus, '', 'info');
             if(saveAgentGeneralConfigStatus) saveAgentGeneralConfigStatus.classList.add('hidden');
        } else {
             ui.showStatusMessage(saveAgentGeneralConfigStatus, `Lỗi: ${error.message}`, 'error');
        }
    } finally {
        ui.hideLoading();
        saveAgentGeneralConfigButton.disabled = false;
        if (saveAgentGeneralConfigStatus && !saveAgentGeneralConfigStatus.classList.contains('hidden') && !saveAgentGeneralConfigStatus.textContent.startsWith('Lỗi')) {
            ui.hideStatusMessage(saveAgentGeneralConfigStatus);
        }
    }
}

async function handleSaveAgentNamespaces() {
    if (!currentConfiguringAgentId || !saveAgentNsConfigButton || !saveAgentNsConfigStatus || !agentNamespaceListDiv) return;
    ui.showLoading();
    saveAgentNsConfigButton.disabled = true;
    ui.showStatusMessage(saveAgentNsConfigStatus, 'Đang lưu...', 'info');
    const selectedNamespaces = [];
    agentNamespaceListDiv.querySelectorAll('input[type="checkbox"]:checked').forEach(checkbox => {
        selectedNamespaces.push(checkbox.value);
    });
    try {
        await api.saveAgentMonitoredNamespaces(currentConfiguringAgentId, selectedNamespaces);
        ui.showStatusMessage(saveAgentNsConfigStatus, 'Đã lưu thành công!', 'success');
    } catch (error) {
        console.error(`Error saving namespaces for agent ${currentConfiguringAgentId}:`, error);
        ui.showStatusMessage(saveAgentNsConfigStatus, `Lỗi: ${error.message}`, 'error');
    } finally {
        ui.hideLoading();
        saveAgentNsConfigButton.disabled = false;
        ui.hideStatusMessage(saveAgentNsConfigStatus);
    }
}

function handleCloseAgentConfig() {
    if (agentConfigSection) {
        agentConfigSection.classList.add('hidden');
    }
    currentConfiguringAgentId = null;
}


function handleEditUserClick(user) {
    console.log("Edit button clicked for user:", user);
    if (currentUserRole === 'admin') {
        ui.openEditUserModal(user);
    } else {
        alert("Bạn không có quyền chỉnh sửa người dùng.");
    }
}


async function handleCreateUserSubmit(event) {
    event.preventDefault();
    if (!createUserForm || !createUserButton || !createUserStatus) return;
    const password = newPasswordInput?.value;
    const confirmPassword = confirmPasswordInput?.value;
    if (password !== confirmPassword) {
        if (passwordMatchError) passwordMatchError.classList.remove('hidden');
        if (confirmPasswordInput) confirmPasswordInput.focus();
        return;
    } else {
        if (passwordMatchError) passwordMatchError.classList.add('hidden');
    }
     if (password.length < 6) {
         alert("Mật khẩu phải có ít nhất 6 ký tự.");
         if (newPasswordInput) newPasswordInput.focus();
         return;
     }
    ui.showLoading();
    createUserButton.disabled = true;
    ui.showStatusMessage(createUserStatus, 'Đang tạo user...', 'info');
    const userData = {
        username: createUserForm.elements['username'].value,
        password: password,
        fullname: createUserForm.elements['fullname'].value,
        role: createUserForm.elements['role'].value
    };
    try {
        const result = await api.createUser(userData);
        ui.showStatusMessage(createUserStatus, result.message || 'Tạo user thành công!', 'success');
        // --- MODIFIED: Close modal on success ---
        ui.closeCreateUserModal();
        // --- END MODIFICATION ---
        await loadUserManagementData(); // Reload user list
    } catch (error) {
        console.error("Error creating user:", error);
        ui.showStatusMessage(createUserStatus, `Lỗi: ${error.message}`, 'error');
    } finally {
        ui.hideLoading();
        createUserButton.disabled = false;
        // Hide status unless it's an error
        if (!createUserStatus.textContent.startsWith('Lỗi')) {
            ui.hideStatusMessage(createUserStatus);
        }
    }
}


async function handleEditUserSubmit(event) {
    event.preventDefault();
    if (!editUserForm || !saveUserChangesButton || !editUserStatus) return;
    const userId = document.getElementById('edit-user-id')?.value;
    if (!userId) {
        console.error("User ID not found in edit form.");
        ui.showStatusMessage(editUserStatus, 'Lỗi: Không tìm thấy ID người dùng.', 'error');
        ui.hideStatusMessage(editUserStatus);
        return;
    }
    ui.showLoading();
    saveUserChangesButton.disabled = true;
    ui.showStatusMessage(editUserStatus, 'Đang lưu thay đổi...', 'info');
    const userDataToUpdate = {
        fullname: document.getElementById('edit-fullname')?.value,
        role: document.getElementById('edit-role')?.value
    };
    try {
        const result = await api.updateUser(userId, userDataToUpdate);
        ui.showStatusMessage(editUserStatus, result.message || 'Cập nhật thành công!', 'success');
        ui.closeEditUserModal();
        await loadUserManagementData();
    } catch (error) {
        console.error(`Error updating user ${userId}:`, error);
        ui.showStatusMessage(editUserStatus, `Lỗi: ${error.message}`, 'error');
    } finally {
        ui.hideLoading();
        saveUserChangesButton.disabled = false;
        if (!editUserStatus.textContent.startsWith('Lỗi')) {
             ui.hideStatusMessage(editUserStatus);
        }
    }
}


function applyRolePermissions() {
    const isAdmin = (currentUserRole === 'admin');
    console.log(`Applying permissions for role: ${currentUserRole}, isAdmin: ${isAdmin}`);
    document.querySelector('a[href="#settings"]')?.parentElement?.classList.toggle('hidden', !isAdmin);
    document.querySelector('a[href="#user-management"]')?.parentElement?.classList.toggle('hidden', !isAdmin);
    // Disable buttons based on role
    if (saveTelegramConfigButton) saveTelegramConfigButton.disabled = !isAdmin;
    if (saveAiConfigButton) saveAiConfigButton.disabled = !isAdmin;
    if (saveAgentGeneralConfigButton) saveAgentGeneralConfigButton.disabled = !isAdmin;
    if (saveAgentNsConfigButton) saveAgentNsConfigButton.disabled = !isAdmin;
    if (createUserButton) createUserButton.disabled = !isAdmin; // Disable the submit button inside modal
    if (saveUserChangesButton) saveUserChangesButton.disabled = !isAdmin;
    if (openCreateUserModalButton) openCreateUserModalButton.disabled = !isAdmin; // Disable the button to open the modal
}


// --- Initialization and Event Listeners ---
document.addEventListener('DOMContentLoaded', () => {
    currentUserRole = document.body.dataset.userRole || 'user';
    applyRolePermissions();
    ui.setActiveSection('dashboard', loadActiveSectionData);

    // Sidebar navigation
    sidebarItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = item.getAttribute('href')?.substring(1);
            if ((targetId === 'settings' || targetId === 'user-management') && currentUserRole !== 'admin') {
                console.warn("Attempted to navigate to admin section without permission.");
                return;
            }
            if(targetId) ui.setActiveSection(targetId, loadActiveSectionData);
        });
    });

    // Incident filtering and pagination
    if (filterButton) filterButton.addEventListener('click', () => {
        currentPodFilter = podFilterInput?.value || '';
        currentSeverityFilter = severityFilterSelect?.value || '';
        currentIncidentPage = 1;
        loadIncidentsData(true);
    });
    if (podFilterInput) podFilterInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') filterButton?.click(); });
    if (refreshIncidentsButton) refreshIncidentsButton.addEventListener('click', () => { loadIncidentsData(true); });
    if (prevPageButton) prevPageButton.addEventListener('click', () => {
        if (currentIncidentPage > 1) { currentIncidentPage--; loadIncidentsData(true); }
    });
    if (nextPageButton) nextPageButton.addEventListener('click', () => {
        if (currentIncidentPage < totalIncidentPages) { currentIncidentPage++; loadIncidentsData(true); }
    });

    // Dashboard time range selection
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

    // Settings page listeners
    if (saveTelegramConfigButton) saveTelegramConfigButton.addEventListener('click', settings.saveTelegramSettings);
    if (saveAiConfigButton) saveAiConfigButton.addEventListener('click', settings.saveAiSettings);
    if (enableAiToggle) enableAiToggle.addEventListener('change', settings.handleAiToggleChange);

    // Agent configuration listeners
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

    // User management listeners
    if (createUserForm) createUserForm.addEventListener('submit', handleCreateUserSubmit);
    if (editUserForm) editUserForm.addEventListener('submit', handleEditUserSubmit);
    // --- ADDED: Listener for opening create user modal ---
    if (openCreateUserModalButton) {
        openCreateUserModalButton.addEventListener('click', ui.openCreateUserModal);
    }
    // --- END ADDED ---

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

    // Modal closing listeners
    if (modalCloseButton) modalCloseButton.addEventListener('click', ui.closeModal); // Incident modal
    if (editUserModalCloseButton) editUserModalCloseButton.addEventListener('click', ui.closeEditUserModal); // Edit user modal
    // --- ADDED: Listener for closing create user modal ---
    if (createUserModalCloseButton) createUserModalCloseButton.addEventListener('click', ui.closeCreateUserModal);
    // --- END ADDED ---
    // Close modals on Escape key press
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
             if (document.getElementById('incident-modal')?.classList.contains('modal-visible')) ui.closeModal();
             if (editUserModal && !editUserModal.classList.contains('hidden')) ui.closeEditUserModal();
             // --- ADDED: Close create user modal on Escape ---
             if (createUserModal && !createUserModal.classList.contains('hidden')) ui.closeCreateUserModal();
             // --- END ADDED ---
        }
    });
    // Close modal if clicking outside the content area (optional, adjust selectors if needed)
    // if (modalOverlay) modalOverlay.addEventListener('click', (event) => { if (event.target === modalOverlay) ui.closeModal(); }); // Incident modal overlay
    // if (editUserModal) editUserModal.addEventListener('click', (event) => { if (event.target === editUserModal) ui.closeEditUserModal(); }); // Edit user modal overlay
    // --- ADDED: Close create user modal on overlay click ---
    if (createUserModal) createUserModal.addEventListener('click', (event) => { if (event.target === createUserModal) ui.closeCreateUserModal(); });
    // --- END ADDED ---


    console.log("DEBUG: main.js loaded and event listeners attached.");

});
