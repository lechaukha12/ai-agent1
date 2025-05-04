import * as api from './api.js';
import * as ui from './ui.js';
import * as charts from './charts.js';
import * as settings from './settings.js';

let currentIncidentPage = 1;
let totalIncidentPages = 1;
let currentResourceFilter = '';
let currentSeverityFilter = '';
let currentEnvironmentFilter = '';
let currentEnvTypeFilter = '';
let currentStatsDays = 1;
let incidentsDataCache = {};
let usersDataCache = {};
let currentConfiguringAgentId = null;
let currentUserRole = 'user';

const sidebarItems = document.querySelectorAll('.sidebar-item');
const contentSections = document.querySelectorAll('.content-section');

const totalIncidentsElem = document.getElementById('total-incidents');
const totalGeminiCallsElem = document.getElementById('total-gemini-calls');
const totalTelegramAlertsElem = document.getElementById('total-telegram-alerts');
const timeRangeButtons = document.querySelectorAll('.time-range-btn');
const dashboardEnvironmentFilter = document.getElementById('dashboard-environment-filter');
const dashboardEnvTypeFilter = document.getElementById('dashboard-env-type-filter');

const incidentsTableBody = document.getElementById('incidents-table-body');
const resourceFilterInput = document.getElementById('resource-filter');
const severityFilterSelect = document.getElementById('severity-filter');
const environmentFilterSelect = document.getElementById('environment-filter');
const envTypeFilterSelect = document.getElementById('env-type-filter');
const filterButton = document.getElementById('filter-button');
const refreshIncidentsButton = document.getElementById('refresh-incidents-button');
const paginationControls = document.getElementById('pagination-controls');
const paginationInfo = document.getElementById('pagination-info');
const prevPageButton = document.getElementById('prev-page');
const nextPageButton = document.getElementById('next-page');

const saveTelegramConfigButton = document.getElementById('save-telegram-config-button');
const saveAiConfigButton = document.getElementById('save-ai-config-button');
const enableAiToggle = document.getElementById('enable-ai-toggle');

const modalCloseButton = document.getElementById('modal-close-button');
const incidentModalElement = document.getElementById('incident-modal');

const agentContentSection = document.getElementById('agent-content');
const agentStatusTableBody = document.getElementById('agent-status-table-body');
const agentStatusErrorElem = document.getElementById('agent-status-error');
const refreshAgentStatusButton = document.getElementById('refresh-agent-status-button');
const addAgentButton = document.getElementById('add-agent-button');

const agentConfigSection = document.getElementById('agent-config-section');
const configAgentIdSpan = document.getElementById('config-agent-id');
const configAgentEnvNameSpan = document.getElementById('config-agent-env-name');
const configAgentEnvTypeSpan = document.getElementById('config-agent-env-type');
const agentConfigTabs = document.querySelectorAll('.agent-config-tab');
const agentConfigTabContents = document.querySelectorAll('.agent-config-tab-content');
const saveAgentGeneralConfigButton = document.getElementById('save-agent-general-config-button');
const saveAgentGeneralConfigStatus = document.getElementById('save-agent-general-config-status');
const agentNamespaceListDiv = document.getElementById('agent-namespace-list');
const agentNamespaceLoadingText = document.getElementById('agent-namespace-loading-text');
const saveAgentNsConfigButton = document.getElementById('save-agent-ns-config-button');
const saveAgentNsConfigStatus = document.getElementById('save-agent-ns-config-status');
const closeAgentConfigButton = document.getElementById('close-agent-config-button');

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

const addAgentModal = document.getElementById('add-agent-modal');


async function loadAgentStatus() {
    if (!agentStatusTableBody || !agentStatusErrorElem) {
        console.warn("Agent status table elements not found. Skipping load.");
        return;
    }
    ui.showLoading();
    agentStatusTableBody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-gray-500">Đang tải trạng thái agent...</td></tr>`;
    agentStatusErrorElem.classList.add('hidden');
    try {
        const data = await api.fetchAgentStatus();
        const agents = data.active_agents || [];
        ui.renderAgentStatusTable(agents, handleConfigureAgentClick, currentUserRole);
        await loadEnvironmentFilterOptions();
    } catch (error) {
        console.error("DEBUG: Failed to load agent status:", error);
        ui.showAgentStatusError(`Lỗi tải trạng thái agent: ${error.message}`);
    } finally {
        ui.hideLoading();
    }
}

async function loadEnvironmentFilterOptions() {
    try {
        const environments = await api.fetchEnvironments();
        ui.populateEnvironmentFilter(environments);
    } catch (error) {
        console.error("Failed to load environment filter options:", error);
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
    const targetSection = document.getElementById(activeSectionId + '-content');
    if (targetSection) {
        if (contentSections) {
            contentSections.forEach(section => {
                section.classList.toggle('hidden', section.id !== targetSection.id);
            });
        } else {
             console.error("contentSections variable is not defined!");
             document.querySelectorAll('.content-section').forEach(s => s.classList.add('hidden'));
             targetSection.classList.remove('hidden');
        }
    } else {
        console.warn(`Target section '${activeSectionId}-content' not found.`);
        return;
    }

    if (agentConfigSection && activeSectionId !== 'agent') {
         agentConfigSection.classList.add('hidden');
         currentConfiguringAgentId = null;
    }


    switch (activeSectionId) {
        case 'dashboard':
            loadDashboardData();
            break;
        case 'incidents':
            loadIncidentsData(true);
            break;
        case 'agent':
            loadAgentStatus();
            break;
        case 'settings':
            loadAllSettings();
            break;
        case 'user-management':
            loadUserManagementData();
            break;
        default:
            console.warn(`Unhandled section ID: ${activeSectionId}, loading dashboard.`);
            loadDashboardData();
    }
}

async function loadDashboardData() {
    ui.showLoading();
    charts.destroyAllCharts();

    const incidentsOverTimeCtx = document.getElementById('incidents-over-time-chart')?.getContext('2d');
    const severityDistributionCtx = document.getElementById('severity-distribution-chart')?.getContext('2d');
    const environmentDistributionCtx = document.getElementById('environment-distribution-chart')?.getContext('2d');
    const topResourcesCtx = document.getElementById('top-resources-chart')?.getContext('2d');

    const incidentsOverTimeErrorElem = document.getElementById('incidents-over-time-error');
    const incidentsOverTimeNoDataElem = document.getElementById('incidents-over-time-no-data');
    const severityDistributionErrorElem = document.getElementById('severity-distribution-error');
    const severityDistributionNoDataElem = document.getElementById('severity-distribution-no-data');
    const environmentDistributionErrorElem = document.getElementById('environment-distribution-error');
    const environmentDistributionNoDataElem = document.getElementById('environment-distribution-no-data');
    const topResourcesErrorElem = document.getElementById('top-resources-error');
    const topResourcesNoDataElem = document.getElementById('top-resources-no-data');

    let missingElements = [];
    if (!incidentsOverTimeCtx) missingElements.push('incidents-over-time-chart');
    if (!severityDistributionCtx) missingElements.push('severity-distribution-chart');
    if (!environmentDistributionCtx) missingElements.push('environment-distribution-chart');
    if (!topResourcesCtx) missingElements.push('top-resources-chart');

    if (missingElements.length > 0) {
        console.error(`Dashboard Error: Canvas element(s) not found: ${missingElements.join(', ')}.`);
        const dashboardContent = document.getElementById('dashboard-content');
         if (dashboardContent && !dashboardContent.querySelector('.chart-error-persistent')) {
             const errorMsg = document.createElement('p');
             errorMsg.className = 'chart-error-persistent text-red-600 text-center font-semibold my-4';
             errorMsg.textContent = `Lỗi tải biểu đồ: Không tìm thấy element (${missingElements.join(', ')}). Vui lòng thử làm mới trang.`;
             const header = dashboardContent.querySelector('header');
             if (header) {
                 header.insertAdjacentElement('afterend', errorMsg);
             } else {
                 dashboardContent.insertAdjacentElement('afterbegin', errorMsg);
             }
         }
        ui.hideLoading();
        return;
    } else {
        const persistentError = document.querySelector('.chart-error-persistent');
        if (persistentError) persistentError.remove();
    }

    [incidentsOverTimeErrorElem, severityDistributionErrorElem, environmentDistributionErrorElem, topResourcesErrorElem,
     incidentsOverTimeNoDataElem, severityDistributionNoDataElem, environmentDistributionNoDataElem, topResourcesNoDataElem]
        .forEach(el => el?.classList.add('hidden'));

    const envFilter = dashboardEnvironmentFilter?.value || '';
    const envTypeFilter = dashboardEnvTypeFilter?.value || '';

    try {
        const statsData = await api.fetchStats(currentStatsDays, envFilter, envTypeFilter);

        ui.setText(totalIncidentsElem, statsData.totals?.incidents ?? '0');
        ui.setText(totalGeminiCallsElem, statsData.totals?.model_calls ?? '0');
        ui.setText(totalTelegramAlertsElem, statsData.totals?.telegram_alerts ?? '0');

        const chartDays = Math.max(currentStatsDays, 7);
        let statsForLineChart = statsData;
        if (chartDays !== currentStatsDays) {
            try {
                statsForLineChart = await api.fetchStats(chartDays, envFilter, envTypeFilter);
            } catch (lineChartError) {
                console.error("Failed to fetch extended data for line chart:", lineChartError);
                statsForLineChart = statsData;
                if (incidentsOverTimeErrorElem) {
                    ui.setText(incidentsOverTimeErrorElem, "Lỗi tải dữ liệu mở rộng.");
                    incidentsOverTimeErrorElem.classList.remove('hidden');
                }
            }
        }

        charts.renderLineChart(incidentsOverTimeCtx, incidentsOverTimeErrorElem, incidentsOverTimeNoDataElem, statsForLineChart.daily_stats_for_chart);
        charts.renderSeverityPieChart(severityDistributionCtx, severityDistributionErrorElem, severityDistributionNoDataElem, statsData.severity_distribution_today || {});
        charts.renderEnvironmentPieChart(environmentDistributionCtx, environmentDistributionErrorElem, environmentDistributionNoDataElem, statsData.environment_distribution || {});
        charts.renderTopResourcesBarChart(topResourcesCtx, topResourcesErrorElem, topResourcesNoDataElem, statsData.top_problematic_resources || {});

        if (currentStatsDays !== 1) {
            charts.clearSeverityPieChart(severityDistributionNoDataElem, `Xem theo ngày để thấy phân loại mức độ.`);
        } else if (Object.keys(statsData.severity_distribution_today || {}).length === 0 && severityDistributionNoDataElem) {
            ui.setText(severityDistributionNoDataElem, `Không có sự cố hôm nay.`);
            severityDistributionNoDataElem.classList.remove('hidden');
        }

    } catch (error) {
        console.error("Failed to load dashboard data:", error);
        ui.setText(totalIncidentsElem, 'Lỗi');
        ui.setText(totalGeminiCallsElem, 'Lỗi');
        ui.setText(totalTelegramAlertsElem, 'Lỗi');
        charts.destroyAllCharts();
        [incidentsOverTimeErrorElem, severityDistributionErrorElem, environmentDistributionErrorElem, topResourcesErrorElem]
            .forEach(el => { if(el) { ui.setText(el, "Lỗi tải dữ liệu."); el.classList.remove('hidden'); } });
    } finally {
        ui.hideLoading();
    }
}


async function loadIncidentsData(forceReload = false) {
    if (!incidentsTableBody) { console.error("Incidents table body not found!"); return; }

    const tableBodyContent = incidentsTableBody.innerHTML.trim();
    if (!forceReload && tableBodyContent && !tableBodyContent.includes('Đang tải dữ liệu') && !tableBodyContent.includes('Không tìm thấy')) {
        console.debug("Incidents table already populated, skipping reload.");
        return;
    }

    ui.showLoading();
    incidentsTableBody.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-gray-500">Đang tải dữ liệu...</td></tr>`;
    if (paginationControls) paginationControls.classList.add('hidden');
    incidentsDataCache = {};

    currentResourceFilter = resourceFilterInput?.value || '';
    currentSeverityFilter = severityFilterSelect?.value || '';
    currentEnvironmentFilter = environmentFilterSelect?.value || '';
    currentEnvTypeFilter = envTypeFilterSelect?.value || '';

    try {
        const data = await api.fetchIncidents(
            currentIncidentPage,
            currentResourceFilter,
            currentSeverityFilter,
            currentEnvironmentFilter,
            currentEnvTypeFilter,
            null,
            null
        );
        const incidents = data.incidents;
        const pagination = data.pagination;
        totalIncidentPages = pagination.total_pages;

        incidentsTableBody.innerHTML = '';

        if (!incidents || incidents.length === 0) {
            incidentsTableBody.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-gray-500">Không tìm thấy sự cố nào khớp.</td></tr>`;
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

                const createCell = (content, isHtml = false, allowWrap = false, cssClass = null) => {
                    const cell = document.createElement('td');
                    cell.className = `px-4 py-3 text-sm text-gray-700 align-top ${cssClass || ''}`;
                    ui.setText(cell, content, isHtml);
                    if (!isHtml && typeof content === 'string') { cell.title = content; }
                    cell.classList.toggle('whitespace-normal', allowWrap);
                    cell.classList.toggle('whitespace-nowrap', !allowWrap);
                    cell.classList.toggle('overflow-hidden', !allowWrap);
                    cell.classList.toggle('text-ellipsis', !allowWrap);
                    return cell;
                };

                row.appendChild(createCell(ui.formatVietnameseDateTime(incident.timestamp)));
                row.appendChild(createCell(incident.environment_name));
                row.appendChild(createCell(incident.environment_type));
                row.appendChild(createCell(incident.resource_name));
                row.appendChild(createCell(ui.createSeverityBadge(incident.severity), true));
                row.appendChild(createCell(incident.summary, false, true));
                row.appendChild(createCell(incident.initial_reasons, false, true));
                incidentsTableBody.appendChild(row);
            });
        }

        if (paginationControls && totalIncidentPages > 0) {
            if (paginationInfo) ui.setText(paginationInfo, `Trang ${pagination.page} / ${totalIncidentPages} (Tổng: ${pagination.total_items})`);
            if (prevPageButton) prevPageButton.disabled = pagination.page <= 1;
            if (nextPageButton) nextPageButton.disabled = pagination.page >= totalIncidentPages;
            paginationControls.classList.remove('hidden');
        } else if (paginationControls) {
            paginationControls.classList.add('hidden');
        }
    } catch (error) {
        console.error('DEBUG: Failed to load incidents data:', error);
        if (incidentsTableBody) incidentsTableBody.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-red-500">Lỗi tải dữ liệu sự cố: ${error.message}.</td></tr>`;
        if (paginationControls) paginationControls.classList.add('hidden');
    } finally {
        ui.hideLoading();
    }
}

function loadAllSettings() {
    settings.loadTelegramSettings();
    settings.loadAiSettings();
}


async function handleConfigureAgentClick(agentId, environmentName, environmentType, environmentInfo) {
    console.log(`Configure button clicked for agent: ${agentId} (Env: ${environmentName}, Type: ${environmentType})`);
    if (!agentConfigSection || !configAgentIdSpan) return;

    currentConfiguringAgentId = agentId;
    ui.showLoading();
    const statuses = [saveAgentGeneralConfigStatus, saveAgentNsConfigStatus];
    statuses.forEach(el => { if (el) el.classList.add('hidden'); });

    ui.setText(configAgentIdSpan, agentId);

    try {
        const agentConfig = await api.fetchAgentConfig(agentId);
        console.log("Fetched agent config:", agentConfig);

        ui.populateAgentConfigForm(
            { ...agentConfig, agentId: agentId },
            environmentName,
            environmentType,
            environmentInfo
        );

        if (environmentType === 'kubernetes') {
            await renderAgentNamespaceList(agentConfig.monitored_namespaces || []);
             const nsButton = document.querySelector('.agent-config-tab[data-tab="agent-k8s"]');
             if(nsButton) nsButton.classList.remove('hidden');
             const nsContent = document.getElementById('agent-k8s-tab');
             if(nsContent) nsContent.classList.remove('hidden');
        } else {
             if(agentNamespaceListDiv) agentNamespaceListDiv.innerHTML = '';
             if(agentNamespaceLoadingText) agentNamespaceLoadingText.classList.add('hidden');
             const nsButton = document.querySelector('.agent-config-tab[data-tab="agent-k8s"]');
             if(nsButton) nsButton.classList.add('hidden');
             const nsContent = document.getElementById('agent-k8s-tab');
             if(nsContent) nsContent.classList.add('hidden');
        }

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


async function renderAgentNamespaceList(monitoredNamespaces = []) {
    if (!agentNamespaceListDiv || !agentNamespaceLoadingText) return;

    agentNamespaceListDiv.innerHTML = '';
    agentNamespaceLoadingText.classList.remove('hidden');
    ui.setText(agentNamespaceLoadingText, 'Đang tải danh sách namespace khả dụng...');

    try {
        const availableNamespaces = await api.fetchAvailableNamespaces();
        agentNamespaceLoadingText.classList.add('hidden');

        if (availableNamespaces.length === 0) {
            agentNamespaceListDiv.innerHTML = `<p class="text-gray-500 col-span-full text-center py-4">Không tìm thấy namespace nào (K8s).</p>`;
        } else {
            availableNamespaces.forEach(ns => {
                const isChecked = monitoredNamespaces.includes(ns);
                const div = document.createElement('div');
                div.className = 'namespace-item text-sm flex items-center';

                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.id = `agent-ns-${ns.replace(/[^a-zA-Z0-9-]/g, '-')}`;
                checkbox.value = ns;
                checkbox.checked = isChecked;
                checkbox.className = 'form-checkbox h-4 w-4 text-indigo-600 transition duration-150 ease-in-out rounded border-gray-300 focus:ring-indigo-500';

                const label = document.createElement('label');
                label.htmlFor = checkbox.id;
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
        const isHidden = tab.classList.contains('hidden');
        if (!isHidden) {
            tab.classList.toggle('border-indigo-500', isTarget);
            tab.classList.toggle('text-indigo-600', isTarget);
            tab.classList.toggle('border-transparent', !isTarget);
            tab.classList.toggle('text-gray-500', !isTarget);
            tab.classList.toggle('hover:text-gray-700', !isTarget);
            tab.classList.toggle('hover:border-gray-300', !isTarget);
        }
    });
    agentConfigTabContents.forEach(content => {
        content.classList.toggle('hidden', content.id !== `${targetTabId}-tab`);
    });
    console.debug(`Switched agent config tab to: ${targetTabId}`);
}


async function handleSaveAgentGeneralConfig() {
    if (!currentConfiguringAgentId || !saveAgentGeneralConfigButton || !saveAgentGeneralConfigStatus) return;

    ui.showLoading();
    saveAgentGeneralConfigButton.disabled = true;
    ui.showStatusMessage(saveAgentGeneralConfigStatus, 'Đang lưu...', 'info');

    let configData = {};
    try {
        const scanInterval = parseInt(document.getElementById('agent-scan-interval')?.value ?? '0');
        const scanLevel = document.getElementById('agent-loki-scan-level')?.value ?? 'INFO';

        if (isNaN(scanInterval) || scanInterval < 10) {
            throw new Error("Tần suất quét phải là số >= 10.");
        }
        configData.scan_interval_seconds = scanInterval;
        configData.loki_scan_min_level = scanLevel;

        const restartThresholdInput = document.getElementById('agent-restart-threshold');
        const k8sTab = document.getElementById('agent-k8s-tab');
        if (restartThresholdInput && k8sTab && !k8sTab.classList.contains('hidden')) {
             const restartThreshold = parseInt(restartThresholdInput.value ?? '0');
             if (isNaN(restartThreshold) || restartThreshold < 1) {
                 throw new Error("Ngưỡng khởi động lại phải là số >= 1.");
             }
             configData.restart_count_threshold = restartThreshold;
        }

        await api.saveAgentGeneralConfig(currentConfiguringAgentId, configData);
        ui.showStatusMessage(saveAgentGeneralConfigStatus, 'Đã lưu thành công!', 'success');

    } catch (error) {
        console.error(`Error saving general config for agent ${currentConfiguringAgentId}:`, error);
        if (error.message.includes("phải là số")) {
             alert(`Lỗi dữ liệu nhập: ${error.message}`);
             if(saveAgentGeneralConfigStatus) saveAgentGeneralConfigStatus.classList.add('hidden');
        } else {
             ui.showStatusMessage(saveAgentGeneralConfigStatus, `Lỗi: ${error.message}`, 'error');
        }
    } finally {
        ui.hideLoading();
        saveAgentGeneralConfigButton.disabled = false;
        if (saveAgentGeneralConfigStatus && !saveAgentGeneralConfigStatus.textContent.startsWith('Lỗi')) {
            ui.hideStatusMessage(saveAgentGeneralConfigStatus);
        }
    }
}


async function handleSaveAgentNamespaces() {
    if (!currentConfiguringAgentId || !saveAgentNsConfigButton || !saveAgentNsConfigStatus || !agentNamespaceListDiv) return;

    const nsTabContent = document.getElementById('agent-k8s-tab');
    if (!nsTabContent || nsTabContent.classList.contains('hidden')) {
        console.warn("Attempted to save namespaces for a non-K8s agent or hidden tab.");
        return;
    }

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
        if (saveAgentNsConfigStatus && !saveAgentNsConfigStatus.textContent.startsWith('Lỗi')) {
            ui.hideStatusMessage(saveAgentNsConfigStatus);
        }
    }
}


function handleCloseAgentConfig() {
    if (agentConfigSection) {
        agentConfigSection.classList.add('hidden');
    }
    currentConfiguringAgentId = null;
    console.debug("Agent config section closed.");
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
        ui.closeCreateUserModal();
        await loadUserManagementData();

    } catch (error) {
        console.error("Error creating user:", error);
        ui.showStatusMessage(createUserStatus, `Lỗi: ${error.message}`, 'error');
    } finally {
        ui.hideLoading();
        createUserButton.disabled = false;
        if (createUserStatus && !createUserStatus.textContent.startsWith('Lỗi')) {
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
        if (editUserStatus && !editUserStatus.textContent.startsWith('Lỗi')) {
             ui.hideStatusMessage(editUserStatus);
        }
    }
}



function applyRolePermissions() {
    const isAdmin = (currentUserRole === 'admin');
    console.log(`Applying permissions for role: ${currentUserRole}, isAdmin: ${isAdmin}`);

    document.querySelector('a[href="#settings"]')?.parentElement?.classList.toggle('hidden', !isAdmin);
    document.querySelector('a[href="#user-management"]')?.parentElement?.classList.toggle('hidden', !isAdmin);

    [saveTelegramConfigButton, saveAiConfigButton, saveAgentGeneralConfigButton, saveAgentNsConfigButton, createUserButton, saveUserChangesButton]
        .forEach(btn => { if(btn) btn.disabled = !isAdmin; });

    if (openCreateUserModalButton) openCreateUserModalButton.disabled = !isAdmin;
    if (addAgentButton) addAgentButton.disabled = !isAdmin;
}



document.addEventListener('DOMContentLoaded', async () => {
    console.log("DOM fully loaded and parsed.");
    currentUserRole = document.body.dataset.userRole || 'user';
    applyRolePermissions();

    await loadEnvironmentFilterOptions();
    ui.setActiveSection('dashboard', loadActiveSectionData);

    sidebarItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = item.getAttribute('href')?.substring(1);
            if (!targetId) return;
            if ((targetId === 'settings' || targetId === 'user-management') && currentUserRole !== 'admin') {
                console.warn("Attempted to navigate to admin section without permission.");
                return;
            }
            ui.setActiveSection(targetId, loadActiveSectionData);
        });
    });

    if (filterButton) {
        filterButton.addEventListener('click', () => {
            currentIncidentPage = 1;
            loadIncidentsData(true);
        });
    }
    if (resourceFilterInput) {
        resourceFilterInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') filterButton?.click(); });
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
    [environmentFilterSelect, envTypeFilterSelect, severityFilterSelect].forEach(select => {
         if (select) {
             select.addEventListener('change', () => {
                 filterButton?.click();
             });
         }
     });


    timeRangeButtons.forEach(button => {
        button.addEventListener('click', () => {
            const days = parseInt(button.getAttribute('data-days'));
            if (currentStatsDays !== days) {
                currentStatsDays = days;
                loadDashboardData();
                timeRangeButtons.forEach(btn => {
                     btn.classList.toggle('bg-blue-500', btn === button);
                     btn.classList.toggle('text-white', btn === button);
                     btn.classList.toggle('bg-gray-300', btn !== button);
                     btn.classList.toggle('text-gray-700', btn !== button);
                     btn.disabled = (btn === button);
                });
            }
        });
         if (button.getAttribute('data-days') === '1') {
             button.classList.add('bg-blue-500', 'text-white');
             button.classList.remove('bg-gray-300', 'text-gray-700');
             button.disabled = true;
         } else {
             button.disabled = false;
         }
    });
     [dashboardEnvironmentFilter, dashboardEnvTypeFilter].forEach(select => {
         if (select) {
             select.addEventListener('change', loadDashboardData);
         }
     });


    if (saveTelegramConfigButton) saveTelegramConfigButton.addEventListener('click', settings.saveTelegramSettings);
    if (saveAiConfigButton) saveAiConfigButton.addEventListener('click', settings.saveAiSettings);
    if (enableAiToggle) enableAiToggle.addEventListener('change', settings.handleAiToggleChange);

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
    if (addAgentButton) {
        addAgentButton.addEventListener('click', ui.openAddAgentModal);
    }


    if (createUserForm) createUserForm.addEventListener('submit', handleCreateUserSubmit);
    if (editUserForm) editUserForm.addEventListener('submit', handleEditUserSubmit);
    if (openCreateUserModalButton) {
        openCreateUserModalButton.addEventListener('click', ui.openCreateUserModal);
    }
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

    if (modalCloseButton) modalCloseButton.addEventListener('click', ui.closeModal);
    if (editUserModalCloseButton) editUserModalCloseButton.addEventListener('click', ui.closeEditUserModal);
    if (createUserModalCloseButton) createUserModalCloseButton.addEventListener('click', ui.closeCreateUserModal);

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
             if (incidentModalElement?.classList.contains('modal-visible')) ui.closeModal();
             if (editUserModal && !editUserModal.classList.contains('hidden')) ui.closeEditUserModal();
             if (createUserModal && !createUserModal.classList.contains('hidden')) ui.closeCreateUserModal();
             if (addAgentModal && addAgentModal.classList.contains('flex')) ui.closeAddAgentModal();
        }
    });

    [incidentModalElement, editUserModal, createUserModal, addAgentModal].forEach(modal => {
        if (modal) {
            modal.addEventListener('click', (event) => {
                if (event.target === modal) {
                    if (modal.id === 'incident-modal') ui.closeModal();
                    else if (modal.id === 'edit-user-modal') ui.closeEditUserModal();
                    else if (modal.id === 'create-user-modal') ui.closeCreateUserModal();
                    else if (modal.id === 'add-agent-modal') ui.closeAddAgentModal();
                }
            });
        }
    });

    console.log("DEBUG: main.js loaded and event listeners attached.");

});
