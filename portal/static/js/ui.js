const loadingSpinner = document.getElementById('loading-spinner');
const loadingOverlay = document.getElementById('loading-overlay');
const sidebarItems = document.querySelectorAll('.sidebar-item');

const incidentModal = document.getElementById('incident-modal');
const modalTimestamp = document.getElementById('modal-timestamp');
const modalSeverity = document.getElementById('modal-severity');
const modalEnvironmentName = document.getElementById('modal-environment-name');
const modalEnvironmentType = document.getElementById('modal-environment-type');
const modalResourceName = document.getElementById('modal-resource-name');
const modalResourceType = document.getElementById('modal-resource-type');
const modalInitialReasons = document.getElementById('modal-initial-reasons');
const modalSummary = document.getElementById('modal-summary');
const modalRootCause = document.getElementById('modal-root-cause');
const modalTroubleshootingSteps = document.getElementById('modal-troubleshooting-steps');
const modalSampleLogs = document.getElementById('modal-sample-logs');
const modalEnvironmentContext = document.getElementById('modal-environment-context');
const modalInputPrompt = document.getElementById('modal-input-prompt');
const modalRawAiResponse = document.getElementById('modal-raw-ai-response');

const usersTableBody = document.getElementById('users-table-body');
const usersListErrorElem = document.getElementById('users-list-error');
const editUserModal = document.getElementById('edit-user-modal');
const editUserModalCloseButton = document.getElementById('edit-user-modal-close-button');
const editUserForm = document.getElementById('edit-user-form');
const editUserIdInput = document.getElementById('edit-user-id');
const editUsernameInput = document.getElementById('edit-username');
const editFullnameInput = document.getElementById('edit-fullname');
const editRoleSelect = document.getElementById('edit-role');
const editUserStatus = document.getElementById('edit-user-status');
const saveUserChangesButton = document.getElementById('save-user-changes-button');
const createUserModal = document.getElementById('create-user-modal');
const createUserModalCloseButton = document.getElementById('create-user-modal-close-button');
const createUserForm = document.getElementById('create-user-form');
const createUserStatus = document.getElementById('create-user-status');
const passwordMatchError = document.getElementById('password-match-error');

const agentStatusTableBody = document.getElementById('agent-status-table-body');
const agentStatusErrorElem = document.getElementById('agent-status-error');
const agentScanIntervalInput = document.getElementById('agent-scan-interval');
const agentLokiScanLevelSelect = document.getElementById('agent-loki-scan-level');

const agentEnvInfoLoading = document.getElementById('agent-env-info-loading');
const agentEnvHostnameSpan = document.getElementById('agent-env-hostname');
const agentEnvOsSpan = document.getElementById('agent-env-os');
const agentEnvKernelSpan = document.getElementById('agent-env-kernel');
const agentEnvUptimeSpan = document.getElementById('agent-env-uptime');
const agentEnvK8sVersionSpan = document.getElementById('agent-env-k8s-version');
const agentEnvNodeCountSpan = document.getElementById('agent-env-node-count');


const agentRestartThresholdInput = document.getElementById('agent-restart-threshold');


const agentCpuThresholdInput = document.getElementById('agent-cpu-threshold');
const agentMemThresholdInput = document.getElementById('agent-mem-threshold');
const agentDiskThresholdsTextarea = document.getElementById('agent-disk-thresholds');
const agentMonitoredServicesTextarea = document.getElementById('agent-monitored-services');
const agentMonitoredLogsTextarea = document.getElementById('agent-monitored-logs');
const agentLogScanKeywordsTextarea = document.getElementById('agent-log-scan-keywords');
const agentLogScanRangeInput = document.getElementById('agent-log-scan-range');
const agentLogContextMinutesInput = document.getElementById('agent-log-context-minutes');


const addAgentModal = document.getElementById('add-agent-modal');
const addAgentModalCloseButton = document.getElementById('add-agent-modal-close-button');
const addAgentModalUnderstandButton = document.getElementById('add-agent-modal-understand-button');


export function showLoading() {
    if (loadingOverlay) loadingOverlay.classList.remove('hidden');
}
export function hideLoading() {
    if (loadingOverlay) loadingOverlay.classList.add('hidden');
}
export function formatVietnameseDateTime(isoString) {
     if (!isoString) return 'N/A';
     try {
         const dateObj = new Date(isoString);
         if (isNaN(dateObj.getTime())) {
             const altDate = new Date(isoString.replace(' ', 'T') + 'Z');
             if (isNaN(altDate.getTime())) {
                 throw new Error(`Invalid date: ${isoString}`);
             }
             Object.assign(dateObj, altDate);
         }
         const options = {
             day: '2-digit', month: '2-digit', year: 'numeric',
             hour: '2-digit', minute: '2-digit', second: '2-digit',
             hour12: false,
             timeZone: 'Asia/Ho_Chi_Minh'
         };
         return new Intl.DateTimeFormat('vi-VN', options).format(dateObj);
     } catch (e) {
         console.error("DEBUG: Error formatting date:", isoString, e);
         return 'Lỗi định dạng';
     }
}
export function setText(element, text, isHtml = false) {
    if (element) {
        const na_html = '<i class="text-gray-400">N/A</i>';
        const content = (text === null || text === undefined || String(text).trim() === '') ? na_html : text;
         if (isHtml) {
             element.innerHTML = content;
         } else {
             if (content === na_html) {
                 element.innerHTML = na_html;
             } else {
                 element.textContent = String(text);
             }
         }
    }
}
export function getSeverityClass(severity) {
    const s = severity ? severity.toLowerCase() : 'unknown';
    if (s === 'critical') return 'severity-critical';
    if (s === 'error') return 'severity-error';
    if (s === 'warning') return 'severity-warning';
    if (s === 'info') return 'severity-info';
    return 'severity-unknown';
}
export function createSeverityBadge(severity) {
    const badgeClass = getSeverityClass(severity);
    const safeSeverity = document.createElement('span');
    safeSeverity.textContent = severity || 'UNKNOWN';
    return `<span class="severity-badge ${badgeClass}">${safeSeverity.innerHTML}</span>`;
}
export function showStatusMessage(statusElement, message, type = 'info') {
    if (!statusElement) return;
    statusElement.textContent = message;
    let baseClass = 'text-sm mr-4';
    let typeClass = '';
    if (type === 'success') typeClass = ' text-green-600';
    else if (type === 'error') typeClass = ' text-red-600';
    else typeClass = ' text-blue-600';
    statusElement.className = baseClass + typeClass;
    statusElement.classList.remove('hidden');
}
export function hideStatusMessage(statusElement, delay = 3000) {
    if (!statusElement) return;
    setTimeout(() => {
        if (statusElement) {
             statusElement.classList.add('hidden');
             statusElement.textContent = '';
        }
    }, delay);
}


export function openModal(incidentData) {
    if (!incidentData || !incidentModal) {
        console.error(`DEBUG: Incident data not provided or modal element missing.`);
        return;
    }
    console.debug("Opening incident modal with data:", incidentData);
    try {
        setText(modalTimestamp, formatVietnameseDateTime(incidentData.timestamp));
        setText(modalSeverity, createSeverityBadge(incidentData.severity), true);
        setText(modalEnvironmentName, incidentData.environment_name);
        setText(modalEnvironmentType, incidentData.environment_type);
        setText(modalResourceName, incidentData.resource_name);
        setText(modalResourceType, incidentData.resource_type);
        setText(modalInitialReasons, incidentData.initial_reasons);
        setText(modalSummary, incidentData.summary);

        setText(modalRootCause, incidentData.root_cause);
        setText(modalTroubleshootingSteps, incidentData.troubleshooting_steps);
        setText(modalSampleLogs, incidentData.sample_logs);
        setText(modalEnvironmentContext, incidentData.environment_context);
        setText(modalInputPrompt, incidentData.input_prompt);
        setText(modalRawAiResponse, incidentData.raw_ai_response);

        const aiAttempted = !!incidentData.input_prompt;
        const aiSections = incidentModal.querySelectorAll('.ai-details');
        aiSections.forEach(section => {
            section.style.display = aiAttempted ? 'block' : 'none';
        });

        incidentModal.classList.add('modal-visible');
        document.body.style.overflow = 'hidden';

    } catch (error) {
        console.error("DEBUG: Error occurred inside openModal:", error);
        alert("Lỗi hiển thị chi tiết sự cố.");
    }
}
export function closeModal() {
    if (incidentModal) {
        incidentModal.classList.remove('modal-visible');
    }
    document.body.style.overflow = '';
}


export function setActiveSection(targetId, loadSectionDataCallback) {
    console.debug(`Activating section: ${targetId}`);
    const contentSectionsNodeList = document.querySelectorAll('.content-section');

    let targetSectionFound = false;
    let targetSectionElement = null;

    contentSectionsNodeList.forEach(section => {
        if (section.id === `${targetId}-content`) {
            targetSectionFound = true;
            targetSectionElement = section;
            section.classList.remove('hidden');
            console.debug(`Section ${targetId}-content made visible.`);
        } else {
            section.classList.add('hidden');
        }
    });

    if (!targetSectionFound) {
        console.warn(`DEBUG: Target section '${targetId}-content' not found. Falling back to dashboard.`);
        const dashboardSection = document.getElementById('dashboard-content');
        if (dashboardSection) {
            dashboardSection.classList.remove('hidden');
            targetId = 'dashboard';
            targetSectionElement = dashboardSection;
        } else {
            console.error("Dashboard section not found either!");
            return;
        }
    }

    const sidebarItemsNodeList = document.querySelectorAll('.sidebar-item');
    sidebarItemsNodeList.forEach(item => {
        item.classList.toggle('active', item.getAttribute('href') === `#${targetId}`);
    });

    if (typeof loadSectionDataCallback === 'function') {
        requestAnimationFrame(() => {
            if (targetSectionElement && !targetSectionElement.classList.contains('hidden')) {
                console.debug(`Calling loadSectionDataCallback for ${targetId} inside rAF`);
                loadSectionDataCallback(targetId);
            } else {
                console.warn(`Target section ${targetId} was hidden again before data load callback.`);
            }
        });
    } else {
        console.warn("loadSectionDataCallback is not a function in setActiveSection");
    }
}



export function renderAgentStatusTable(agents, configureCallback, currentUserRole) {
    if (!agentStatusTableBody || !agentStatusErrorElem) {
        console.warn("Agent status table elements not found.");
        return;
    }
    agentStatusTableBody.innerHTML = '';
    agentStatusErrorElem.classList.add('hidden');

    if (!agents || agents.length === 0) {
        agentStatusTableBody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-gray-500">Không có agent nào đang hoạt động.</td></tr>`;
        return;
    }

    agents.forEach(agent => {
        const row = document.createElement('tr');
        row.setAttribute('data-agent-id', agent.agent_id);
        row.setAttribute('data-env-name', agent.environment_name || '');
        row.setAttribute('data-env-type', agent.environment_type || 'unknown');
        try {
            row.setAttribute('data-env-info', JSON.stringify(agent.environment_info || {}));
        } catch (e) {
            console.error(`Failed to stringify env_info for agent ${agent.agent_id}`, e);
            row.setAttribute('data-env-info', '{}');
        }


        const createCell = (content, isHtml = false, cssClass = null) => {
            const cell = document.createElement('td');
            cell.className = `px-4 py-3 text-sm text-gray-700 align-middle whitespace-nowrap ${cssClass || ''}`;
            setText(cell, content, isHtml);
            return cell;
        };

        row.appendChild(createCell(`${agent.environment_name || 'N/A'} / ${agent.agent_id || 'N/A'}`));
        row.appendChild(createCell(agent.environment_type || 'unknown'));
        row.appendChild(createCell(`<span class="severity-badge severity-info">Active</span>`, true));
        row.appendChild(createCell(formatVietnameseDateTime(agent.last_seen_timestamp)));
        row.appendChild(createCell(agent.agent_version || 'N/A'));

        const actionCell = document.createElement('td');
        actionCell.className = 'px-4 py-3 text-sm text-gray-700 align-middle';
        const actionContainer = document.createElement('div');
        actionContainer.className = 'flex items-center space-x-2';

        if (currentUserRole === 'admin') {
            const configButton = document.createElement('button');
            configButton.textContent = 'Cấu hình';
            configButton.className = 'text-indigo-600 hover:text-indigo-900 hover:underline text-xs font-medium whitespace-nowrap configure-agent-btn';
            configButton.onclick = () => {
                if (typeof configureCallback === 'function') {
                    let envInfo = {};
                    try {
                        envInfo = JSON.parse(row.getAttribute('data-env-info') || '{}');
                    } catch (e) {
                        console.error(`Failed to parse env_info for agent ${agent.agent_id}`, e);
                    }
                    configureCallback(
                        row.getAttribute('data-agent-id'),
                        row.getAttribute('data-env-name'),
                        row.getAttribute('data-env-type'),
                        envInfo
                    );
                }
            };
            actionContainer.appendChild(configButton);
        } else {
             setText(actionContainer, '<i class="text-gray-400">N/A</i>', true);
        }
        actionCell.appendChild(actionContainer);
        row.appendChild(actionCell);

        agentStatusTableBody.appendChild(row);
    });
}

export function showAgentStatusError(message) {
    if (agentStatusTableBody) agentStatusTableBody.innerHTML = '';
    if (agentStatusErrorElem) {
        setText(agentStatusErrorElem, message || 'Lỗi tải trạng thái agent.');
        agentStatusErrorElem.classList.remove('hidden');
    }
}

function formatUptime(seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return 'N/A';
    seconds = Number(seconds);
    const d = Math.floor(seconds / (3600*24));
    const h = Math.floor(seconds % (3600*24) / 3600);
    const m = Math.floor(seconds % 3600 / 60);
    const s = Math.floor(seconds % 60);

    const dDisplay = d > 0 ? d + (d === 1 ? " day, " : " days, ") : "";
    const hDisplay = h > 0 ? h + (h === 1 ? " hour, " : " hours, ") : "";
    const mDisplay = m > 0 ? m + (m === 1 ? " minute, " : " minutes, ") : "";
    const sDisplay = s + (s === 1 ? " second" : " seconds");

    // Show more detail for shorter uptimes
    if (d > 0) return dDisplay + hDisplay.replace(", ", "");
    if (h > 0) return hDisplay + mDisplay.replace(", ", "");
    if (m > 0) return mDisplay + sDisplay;
    return sDisplay;
}

export function populateAgentConfigForm(agentConfig, environmentName, environmentType, environmentInfo) {
    const configAgentEnvNameSpan = document.getElementById('config-agent-env-name');
    const configAgentEnvTypeSpan = document.getElementById('config-agent-env-type');

    setText(configAgentEnvNameSpan, environmentName || 'N/A');
    setText(configAgentEnvTypeSpan, environmentType || 'unknown');

    if (agentEnvInfoLoading) agentEnvInfoLoading.classList.add('hidden');
    document.querySelectorAll('.agent-env-info-item').forEach(el => el.classList.add('hidden'));

    if (environmentType === 'kubernetes') {
        const k8sInfo = environmentInfo?.kubernetes_info || {};
        setText(agentEnvK8sVersionSpan, k8sInfo.k8s_version);
        setText(agentEnvNodeCountSpan, k8sInfo.node_count !== undefined ? String(k8sInfo.node_count) : null);
        if (agentEnvK8sVersionSpan?.parentElement) agentEnvK8sVersionSpan.parentElement.classList.remove('hidden');
        if (agentEnvNodeCountSpan?.parentElement) agentEnvNodeCountSpan.parentElement.classList.remove('hidden');
    } else if (environmentType === 'linux') {
        const sysInfo = environmentInfo || {};
        setText(agentEnvHostnameSpan, sysInfo.hostname);
        setText(agentEnvOsSpan, `${sysInfo.os_name || ''} ${sysInfo.os_release || ''}`);
        setText(agentEnvKernelSpan, sysInfo.kernel_version);
        setText(agentEnvUptimeSpan, formatUptime(sysInfo.uptime_seconds));
        if (agentEnvHostnameSpan?.parentElement) agentEnvHostnameSpan.parentElement.classList.remove('hidden');
        if (agentEnvOsSpan?.parentElement) agentEnvOsSpan.parentElement.classList.remove('hidden');
        if (agentEnvKernelSpan?.parentElement) agentEnvKernelSpan.parentElement.classList.remove('hidden');
        if (agentEnvUptimeSpan?.parentElement) agentEnvUptimeSpan.parentElement.classList.remove('hidden');
    }

    if (agentScanIntervalInput) agentScanIntervalInput.value = agentConfig.scan_interval_seconds ?? 30;
    if (agentLokiScanLevelSelect) agentLokiScanLevelSelect.value = agentConfig.loki_scan_min_level ?? 'INFO';


    if (agentRestartThresholdInput) agentRestartThresholdInput.value = agentConfig.restart_count_threshold ?? 5;


    if (agentCpuThresholdInput) agentCpuThresholdInput.value = agentConfig.cpu_threshold_percent ?? 90.0;
    if (agentMemThresholdInput) agentMemThresholdInput.value = agentConfig.mem_threshold_percent ?? 90.0;
    if (agentDiskThresholdsTextarea) {
        try {
            agentDiskThresholdsTextarea.value = JSON.stringify(agentConfig.disk_thresholds || {'/': 90.0}, null, 2);
        } catch (e) {
            agentDiskThresholdsTextarea.value = '{"/": 90.0}';
            console.error("Error stringifying disk_thresholds", e);
        }
    }
    if (agentMonitoredServicesTextarea) {
        agentMonitoredServicesTextarea.value = (agentConfig.monitored_services || []).join('\n');
    }
    if (agentMonitoredLogsTextarea) {
         try {
            agentMonitoredLogsTextarea.value = JSON.stringify(agentConfig.monitored_logs || [], null, 2);
        } catch (e) {
            agentMonitoredLogsTextarea.value = '[]';
            console.error("Error stringifying monitored_logs", e);
        }
    }
    if (agentLogScanKeywordsTextarea) {
        agentLogScanKeywordsTextarea.value = (agentConfig.log_scan_keywords || []).join(', ');
    }
    if (agentLogScanRangeInput) agentLogScanRangeInput.value = agentConfig.log_scan_range_minutes ?? 5;
    if (agentLogContextMinutesInput) agentLogContextMinutesInput.value = agentConfig.log_context_minutes ?? 30;


    updateAgentConfigVisibility(environmentType);
}

export function updateAgentConfigVisibility(environmentType = 'unknown') {
    console.debug(`Updating config visibility for type: ${environmentType}`);

    const allTabs = document.querySelectorAll('.agent-config-tab');
    let firstVisibleTab = 'agent-general';

    allTabs.forEach(tab => {
        const tabType = tab.getAttribute('data-tab');
        let shouldShow = false;

        if (tabType === 'agent-general') {
            shouldShow = true;
        } else if (tabType === 'agent-k8s' && environmentType === 'kubernetes') {
            shouldShow = true;
            firstVisibleTab = 'agent-k8s';
        } else if (tabType === 'agent-linux' && environmentType === 'linux') {
            shouldShow = true;
            firstVisibleTab = 'agent-linux';
        } else if (tabType === 'agent-windows' && environmentType === 'windows') {
            shouldShow = true;
            firstVisibleTab = 'agent-windows';
        } else if (tabType === 'agent-docker' && environmentType === 'docker') {
            shouldShow = true;
            firstVisibleTab = 'agent-docker';
        }

        tab.classList.toggle('hidden', !shouldShow);
    });

    const tabsContainer = document.querySelector('nav[aria-label="Tabs"]');
    if (tabsContainer) {
        const firstVisibleTabButton = tabsContainer.querySelector(`.agent-config-tab[data-tab="${firstVisibleTab}"]`);
        if(firstVisibleTabButton && !firstVisibleTabButton.classList.contains('hidden')) {
             // Activate the determined first visible tab
             allTabs.forEach(t => {
                 const isTarget = t.getAttribute('data-tab') === firstVisibleTab;
                 t.classList.toggle('border-indigo-500', isTarget);
                 t.classList.toggle('text-indigo-600', isTarget);
                 t.classList.toggle('border-transparent', !isTarget);
                 t.classList.toggle('text-gray-500', !isTarget);
                 t.classList.toggle('hover:text-gray-700', !isTarget);
                 t.classList.toggle('hover:border-gray-300', !isTarget);
             });
        } else {
             // Fallback to activate general if the determined first tab is somehow hidden
             const generalTabButton = tabsContainer.querySelector('.agent-config-tab[data-tab="agent-general"]');
             if (generalTabButton) {
                 firstVisibleTab = 'agent-general';
                 allTabs.forEach(t => {
                     const isTarget = t.getAttribute('data-tab') === firstVisibleTab;
                     t.classList.toggle('border-indigo-500', isTarget);
                     t.classList.toggle('text-indigo-600', isTarget);
                     t.classList.toggle('border-transparent', !isTarget);
                     t.classList.toggle('text-gray-500', !isTarget);
                     t.classList.toggle('hover:text-gray-700', !isTarget);
                     t.classList.toggle('hover:border-gray-300', !isTarget);
                 });
             }
        }
    }


    const allContents = document.querySelectorAll('.agent-config-tab-content');
    allContents.forEach(content => {
        content.classList.toggle('hidden', content.id !== `${firstVisibleTab}-tab`);
    });

}


export function openAddAgentModal() {
    if (!addAgentModal) return;
    addAgentModal.classList.remove('hidden');
    addAgentModal.classList.add('flex');
    document.body.style.overflow = 'hidden';
}
export function closeAddAgentModal() {
    if (!addAgentModal) return;
    addAgentModal.classList.add('hidden');
    addAgentModal.classList.remove('flex');
    document.body.style.overflow = '';
}
if (addAgentModalCloseButton) {
    addAgentModalCloseButton.addEventListener('click', closeAddAgentModal);
}
if (addAgentModalUnderstandButton) {
    addAgentModalUnderstandButton.addEventListener('click', closeAddAgentModal);
}


export function renderUserTable(users, editUserCallback) {
    if (!usersTableBody) { console.error("User table body element not found."); return; }
    if (usersListErrorElem) usersListErrorElem.classList.add('hidden');
    usersTableBody.innerHTML = '';

    if (!users || users.length === 0) {
        usersTableBody.innerHTML = `<tr><td colspan="4" class="text-center py-6 text-gray-500">Không có người dùng nào.</td></tr>`;
        return;
    }

    users.forEach(user => {
        const row = document.createElement('tr');
        row.setAttribute('data-user-id', user.id);

        const userCell = document.createElement('td');
        userCell.className = 'px-4 py-2 text-sm text-gray-900 font-medium';
        setText(userCell, user.username);
        row.appendChild(userCell);

        const nameCell = document.createElement('td');
        nameCell.className = 'px-4 py-2 text-sm text-gray-600';
        setText(nameCell, user.fullname);
        row.appendChild(nameCell);

        const roleCell = document.createElement('td');
        roleCell.className = 'px-4 py-2 text-sm text-gray-600';
        setText(roleCell, user.role);
        row.appendChild(roleCell);

        const actionCell = document.createElement('td');
        actionCell.className = 'px-4 py-2 text-sm text-gray-600';
        const editButton = document.createElement('button');
        editButton.textContent = 'Sửa';
        editButton.className = 'text-indigo-600 hover:text-indigo-900 hover:underline text-xs font-medium';
        editButton.onclick = (event) => {
            event.stopPropagation();
            if (typeof editUserCallback === 'function') {
                editUserCallback(user);
            }
        };
        actionCell.appendChild(editButton);
        row.appendChild(actionCell);

        usersTableBody.appendChild(row);
    });
}
export function showUserListError(message) {
    if (usersTableBody) usersTableBody.innerHTML = '';
    if (usersListErrorElem) {
        usersListErrorElem.textContent = message;
        usersListErrorElem.classList.remove('hidden');
    }
}
export function openCreateUserModal() {
    if (!createUserModal) return;
    clearCreateUserForm();
    createUserModal.classList.remove('hidden');
    createUserModal.classList.add('flex');
    document.body.style.overflow = 'hidden';
    document.getElementById('new-username')?.focus();
}
export function closeCreateUserModal() {
    if (!createUserModal) return;
    createUserModal.classList.add('hidden');
    createUserModal.classList.remove('flex');
    document.body.style.overflow = '';
    if (createUserStatus) {
        createUserStatus.classList.add('hidden');
        createUserStatus.textContent = '';
    }
}
export function clearCreateUserForm() {
    if (createUserForm) { createUserForm.reset(); }
    if (passwordMatchError) passwordMatchError.classList.add('hidden');
    if (createUserStatus) createUserStatus.classList.add('hidden');
}
export function openEditUserModal(userData) {
    if (!editUserModal || !editUserForm) return;
    populateEditUserModal(userData);
    editUserModal.classList.remove('hidden');
    editUserModal.classList.add('flex');
    document.body.style.overflow = 'hidden';
}
export function closeEditUserModal() {
    if (!editUserModal) return;
    editUserModal.classList.add('hidden');
    editUserModal.classList.remove('flex');
    document.body.style.overflow = '';
    if (editUserStatus) {
        editUserStatus.classList.add('hidden');
        editUserStatus.textContent = '';
    }
}
export function populateEditUserModal(userData) {
    if (!editUserIdInput || !editUsernameInput || !editFullnameInput || !editRoleSelect) return;
    editUserIdInput.value = userData.id || '';
    editUsernameInput.value = userData.username || '';
    editFullnameInput.value = userData.fullname || '';
    editRoleSelect.value = userData.role || 'user';
}


export function populateEnvironmentFilter(environments) {
    const incidentFilter = document.getElementById('environment-filter');
    const dashboardFilter = document.getElementById('dashboard-environment-filter');
    const filters = [incidentFilter, dashboardFilter].filter(Boolean);

    if (filters.length === 0) return;

    filters.forEach(filterSelect => {
        const currentValue = filterSelect.value;
        while (filterSelect.options.length > 1) {
            filterSelect.remove(1);
        }
        environments.forEach(env => {
            const option = document.createElement('option');
            option.value = env;
            option.textContent = env;
            filterSelect.appendChild(option);
        });
        filterSelect.value = currentValue;
    });
}
