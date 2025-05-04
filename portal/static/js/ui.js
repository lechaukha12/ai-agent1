// portal/static/js/ui.js

// --- DOM Element References (Assume they exist as defined in main.js) ---
const loadingSpinner = document.getElementById('loading-spinner');
const loadingOverlay = document.getElementById('loading-overlay');
const sidebarItems = document.querySelectorAll('.sidebar-item');
// contentSections will be queried inside setActiveSection

// Incident Modal Elements
const incidentModal = document.getElementById('incident-modal');
const modalTimestamp = document.getElementById('modal-timestamp');
const modalSeverity = document.getElementById('modal-severity');
const modalEnvironmentName = document.getElementById('modal-environment-name'); // New
const modalEnvironmentType = document.getElementById('modal-environment-type'); // New
const modalResourceName = document.getElementById('modal-resource-name');       // New
const modalResourceType = document.getElementById('modal-resource-type');       // New
const modalInitialReasons = document.getElementById('modal-initial-reasons');
const modalSummary = document.getElementById('modal-summary');
const modalRootCause = document.getElementById('modal-root-cause');
const modalTroubleshootingSteps = document.getElementById('modal-troubleshooting-steps');
const modalSampleLogs = document.getElementById('modal-sample-logs');
const modalEnvironmentContext = document.getElementById('modal-environment-context'); // Renamed ID
const modalInputPrompt = document.getElementById('modal-input-prompt');
const modalRawAiResponse = document.getElementById('modal-raw-ai-response');

// User Management Elements
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

// Agent Status/Config Elements
const agentStatusTableBody = document.getElementById('agent-status-table-body');
const agentStatusErrorElem = document.getElementById('agent-status-error');
const configAgentK8sVersionSpan = document.getElementById('config-agent-k8s-version');
const configAgentNodeCountSpan = document.getElementById('config-agent-node-count');
const agentScanIntervalInput = document.getElementById('agent-scan-interval');
const agentRestartThresholdInput = document.getElementById('agent-restart-threshold');
const agentLokiScanLevelSelect = document.getElementById('agent-loki-scan-level');
const configAgentOsInfoSpan = document.getElementById('config-agent-os-info'); // Placeholder for Linux/Win
const k8sSpecificElements = document.querySelectorAll('.agent-k8s-only'); // Select K8s specific elements
const linuxSpecificElements = document.querySelectorAll('.agent-linux-only'); // Select Linux specific elements (example)
// Add selectors for other agent types if needed

// Add Agent Modal Elements
const addAgentModal = document.getElementById('add-agent-modal');
const addAgentModalCloseButton = document.getElementById('add-agent-modal-close-button');
const addAgentModalUnderstandButton = document.getElementById('add-agent-modal-understand-button');


// --- General UI Functions ---
export function showLoading() {
    if (loadingOverlay) loadingOverlay.classList.remove('hidden');
    // if (loadingSpinner) loadingSpinner.classList.remove('hidden'); // Spinner is inside overlay now
}
export function hideLoading() {
    if (loadingOverlay) loadingOverlay.classList.add('hidden');
    // if (loadingSpinner) loadingSpinner.classList.add('hidden');
}
export function formatVietnameseDateTime(isoString) {
     if (!isoString) return 'N/A';
     try {
         const dateObj = new Date(isoString);
         if (isNaN(dateObj.getTime())) {
             // Try parsing common alternative format if ISO fails
             const altDate = new Date(isoString.replace(' ', 'T') + 'Z'); // Assuming UTC if no timezone
             if (isNaN(altDate.getTime())) {
                 throw new Error(`Invalid date: ${isoString}`);
             }
             // Use the alternative object if valid
             Object.assign(dateObj, altDate);
         }
         const options = {
             day: '2-digit', month: '2-digit', year: 'numeric',
             hour: '2-digit', minute: '2-digit', second: '2-digit',
             hour12: false, // Use 24-hour format
             timeZone: 'Asia/Ho_Chi_Minh' // Explicitly set timezone for display
         };
         // Use Intl.DateTimeFormat for better locale handling
         return new Intl.DateTimeFormat('vi-VN', options).format(dateObj);
     } catch (e) {
         console.error("DEBUG: Error formatting date:", isoString, e);
         return 'Lỗi định dạng';
     }
}
export function setText(element, text, isHtml = false) {
    if (element) {
        const na_html = '<i class="text-gray-400">N/A</i>';
        // Treat empty string as N/A as well
        const content = (text === null || text === undefined || String(text).trim() === '') ? na_html : text;
         if (isHtml) {
             element.innerHTML = content;
         } else {
             if (content === na_html) {
                 element.innerHTML = na_html; // Use innerHTML for the italic N/A
             } else {
                 element.textContent = text; // Use textContent for plain text
             }
         }
    } else {
        // console.warn("Attempted to set text on a null element.");
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
    safeSeverity.textContent = severity || 'UNKNOWN'; // Ensure text content is safe
    // Use template literal for cleaner HTML string construction
    return `<span class="severity-badge ${badgeClass}">${safeSeverity.innerHTML}</span>`;
}
export function showStatusMessage(statusElement, message, type = 'info') {
    if (!statusElement) return;
    statusElement.textContent = message;
    let baseClass = 'text-sm mr-4'; // Base class
    let typeClass = '';
    if (type === 'success') typeClass = ' text-green-600';
    else if (type === 'error') typeClass = ' text-red-600';
    else typeClass = ' text-blue-600'; // Default to info
    statusElement.className = baseClass + typeClass; // Combine classes
    statusElement.classList.remove('hidden');
}
export function hideStatusMessage(statusElement, delay = 3000) {
    if (!statusElement) return;
    // Ensure the element exists before setting timeout
    setTimeout(() => {
        if (statusElement) { // Check again inside timeout in case element is removed
             statusElement.classList.add('hidden');
             statusElement.textContent = '';
        }
    }, delay);
}

// --- Incident Modal Functions ---
/**
 * Opens the incident detail modal and populates it with data.
 * @param {object} incidentData - The incident data object from the API.
 */
export function openModal(incidentData) {
    if (!incidentData || !incidentModal) {
        console.error(`DEBUG: Incident data not provided or modal element missing.`);
        return;
    }
    console.debug("Opening incident modal with data:", incidentData); // Debug log
    try {
        // Populate new fields
        setText(modalTimestamp, formatVietnameseDateTime(incidentData.timestamp));
        setText(modalSeverity, createSeverityBadge(incidentData.severity), true);
        setText(modalEnvironmentName, incidentData.environment_name);
        setText(modalEnvironmentType, incidentData.environment_type);
        setText(modalResourceName, incidentData.resource_name);
        setText(modalResourceType, incidentData.resource_type);
        setText(modalInitialReasons, incidentData.initial_reasons);
        setText(modalSummary, incidentData.summary);

        // Populate AI and Context fields
        setText(modalRootCause, incidentData.root_cause);
        setText(modalTroubleshootingSteps, incidentData.troubleshooting_steps);
        setText(modalSampleLogs, incidentData.sample_logs);
        setText(modalEnvironmentContext, incidentData.environment_context); // Use new context field
        setText(modalInputPrompt, incidentData.input_prompt);
        setText(modalRawAiResponse, incidentData.raw_ai_response);

        // Show/hide AI sections based on whether AI analysis was attempted/successful
        // A simple check: if input_prompt exists, AI was likely attempted.
        const aiAttempted = !!incidentData.input_prompt;
        const aiSections = incidentModal.querySelectorAll('.ai-details');
        aiSections.forEach(section => {
            section.style.display = aiAttempted ? 'block' : 'none';
        });

        // Show the modal
        incidentModal.classList.add('modal-visible');
        document.body.style.overflow = 'hidden'; // Prevent background scrolling

    } catch (error) {
        console.error("DEBUG: Error occurred inside openModal:", error);
        alert("Lỗi hiển thị chi tiết sự cố."); // User-friendly error
    }
}
export function closeModal() {
    if (incidentModal) {
        incidentModal.classList.remove('modal-visible');
    }
    document.body.style.overflow = ''; // Restore background scrolling
}

// --- Section Activation ---
/**
 * Sets the active section in the UI and triggers data loading for it.
 * Waits for the next animation frame after making the section visible before loading data.
 * @param {string} targetId - The ID of the section to activate (e.g., 'dashboard').
 * @param {Function} loadSectionDataCallback - The function to call to load data for the section.
 */
export function setActiveSection(targetId, loadSectionDataCallback) {
    console.debug(`Activating section: ${targetId}`);
    const contentSectionsNodeList = document.querySelectorAll('.content-section'); // Get NodeList inside function

    let targetSectionFound = false;
    let targetSectionElement = null; // Store the target element

    // First, hide all sections and find the target section
    contentSectionsNodeList.forEach(section => {
        if (section.id === `${targetId}-content`) {
            targetSectionFound = true;
            targetSectionElement = section; // Store the element
            // Make the target section visible *immediately*
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
            targetId = 'dashboard'; // Update targetId for callback and sidebar
            targetSectionElement = dashboardSection; // Update target element
        } else {
            console.error("Dashboard section not found either!");
            return; // Exit if fallback also fails
        }
    }

    // Update sidebar active state
    const sidebarItemsNodeList = document.querySelectorAll('.sidebar-item'); // Get NodeList inside function
    sidebarItemsNodeList.forEach(item => {
        item.classList.toggle('active', item.getAttribute('href') === `#${targetId}`);
    });

    // Load data for the newly active section *after* the next animation frame
    if (typeof loadSectionDataCallback === 'function') {
        requestAnimationFrame(() => {
            // Double check the target section is still the intended one and visible
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


// --- Agent Monitoring UI ---
/**
 * Renders the agent status table with updated columns.
 * @param {Array} agents - Array of agent objects from the API.
 * @param {Function} configureCallback - Callback for the configure button click. Receives (agentId, environmentName, environmentType, environmentInfo).
 * @param {string} currentUserRole - The role of the currently logged-in user.
 */
export function renderAgentStatusTable(agents, configureCallback, currentUserRole) {
    if (!agentStatusTableBody || !agentStatusErrorElem) {
        console.warn("Agent status table elements not found.");
        return;
    }
    agentStatusTableBody.innerHTML = ''; // Clear previous content
    agentStatusErrorElem.classList.add('hidden');

    if (!agents || agents.length === 0) {
        agentStatusTableBody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-gray-500">Không có agent nào đang hoạt động.</td></tr>`; // Updated colspan
        return;
    }

    agents.forEach(agent => {
        const row = document.createElement('tr');
        // Store necessary data attributes for the configure callback
        row.setAttribute('data-agent-id', agent.agent_id);
        row.setAttribute('data-env-name', agent.environment_name || '');
        row.setAttribute('data-env-type', agent.environment_type || 'unknown');
        // Store environment_info as a JSON string for easy retrieval
        try {
            row.setAttribute('data-env-info', JSON.stringify(agent.environment_info || {}));
        } catch (e) {
            console.error(`Failed to stringify env_info for agent ${agent.agent_id}`, e);
            row.setAttribute('data-env-info', '{}');
        }


        const createCell = (content, isHtml = false, cssClass = null) => {
            const cell = document.createElement('td');
            // Base classes + optional class
            cell.className = `px-4 py-3 text-sm text-gray-700 align-middle whitespace-nowrap ${cssClass || ''}`;
            setText(cell, content, isHtml);
            return cell;
        };

        // Populate new table structure
        row.appendChild(createCell(`${agent.environment_name || 'N/A'} / ${agent.agent_id || 'N/A'}`));
        row.appendChild(createCell(agent.environment_type || 'unknown')); // Display environment type
        row.appendChild(createCell(`<span class="severity-badge severity-info">Active</span>`, true)); // Assuming all listed are active
        row.appendChild(createCell(formatVietnameseDateTime(agent.last_seen_timestamp)));
        row.appendChild(createCell(agent.agent_version || 'N/A'));

        // Action Cell with Configure Button (conditionally shown)
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
                    // Pass all necessary info to the callback
                    configureCallback(
                        row.getAttribute('data-agent-id'),
                        row.getAttribute('data-env-name'),
                        row.getAttribute('data-env-type'),
                        envInfo // Pass the parsed environment info object
                    );
                }
            };
            actionContainer.appendChild(configButton);
        } else {
             setText(actionContainer, '<i class="text-gray-400">N/A</i>', true); // Show N/A for non-admins
        }
        actionCell.appendChild(actionContainer);
        row.appendChild(actionCell);

        agentStatusTableBody.appendChild(row);
    });
}

export function showAgentStatusError(message) {
    if (agentStatusTableBody) agentStatusTableBody.innerHTML = ''; // Clear table on error
    if (agentStatusErrorElem) {
        setText(agentStatusErrorElem, message || 'Lỗi tải trạng thái agent.');
        agentStatusErrorElem.classList.remove('hidden');
    }
}
/**
 * Populates the agent configuration form with fetched data.
 * @param {object} agentConfig - Configuration object fetched from the API.
 * @param {string} environmentName - The name of the environment.
 * @param {string} environmentType - The type of the environment (e.g., 'kubernetes', 'linux').
 * @param {object} environmentInfo - Additional info about the environment.
 */
export function populateAgentConfigForm(agentConfig, environmentName, environmentType, environmentInfo) {
    // Get references inside the function to ensure elements exist
    const configAgentEnvNameSpan = document.getElementById('config-agent-env-name');
    const configAgentEnvTypeSpan = document.getElementById('config-agent-env-type');
    const agentScanIntervalInput = document.getElementById('agent-scan-interval');
    const agentLokiScanLevelSelect = document.getElementById('agent-loki-scan-level');
    const configAgentK8sVersionSpan = document.getElementById('config-agent-k8s-version');
    const configAgentNodeCountSpan = document.getElementById('config-agent-node-count');
    const agentRestartThresholdInput = document.getElementById('agent-restart-threshold');
    const configAgentOsInfoSpan = document.getElementById('config-agent-os-info');

    // Update Title
    // setText(configAgentIdSpan, agentConfig.agentId || 'N/A'); // agentId already set in main.js
    setText(configAgentEnvNameSpan, environmentName || 'N/A');
    setText(configAgentEnvTypeSpan, environmentType || 'unknown');

    // Populate General Settings
    if (agentScanIntervalInput) agentScanIntervalInput.value = agentConfig.scan_interval_seconds ?? 30;
    if (agentLokiScanLevelSelect) agentLokiScanLevelSelect.value = agentConfig.loki_scan_min_level ?? 'INFO';

    // Populate K8s Specific Settings (if applicable)
    const k8sInfo = environmentInfo?.kubernetes_info || {}; // Get K8s info if present
    setText(configAgentK8sVersionSpan, k8sInfo.k8s_version || 'N/A');
    setText(configAgentNodeCountSpan, k8sInfo.node_count !== undefined ? String(k8sInfo.node_count) : 'N/A');
    if (agentRestartThresholdInput) agentRestartThresholdInput.value = agentConfig.restart_count_threshold ?? 5;

    // Populate Linux Specific Settings (Example - if applicable)
    const osInfo = environmentInfo?.os_info || {}; // Assuming OS info might be here for Linux
    setText(configAgentOsInfoSpan, osInfo.pretty_name || 'N/A'); // Example field

    // Populate other agent-type specific fields as needed

    // Update visibility based on type AFTER populating
    updateAgentConfigVisibility(environmentType);
}

/**
 * Shows/hides agent configuration sections based on the environment type.
 * @param {string} environmentType - The type of the agent's environment.
 */
export function updateAgentConfigVisibility(environmentType = 'unknown') {
    console.debug(`Updating config visibility for type: ${environmentType}`);

    // --- Manage Tabs ---
    const allTabs = document.querySelectorAll('.agent-config-tab');
    let firstVisibleTab = 'agent-general'; // Default visible tab

    allTabs.forEach(tab => {
        const tabType = tab.getAttribute('data-tab');
        let shouldShow = false;

        if (tabType === 'agent-general') {
            shouldShow = true; // Always show general tab
        } else if (tabType === 'agent-namespaces' && environmentType === 'kubernetes') {
            shouldShow = true;
            // Make namespaces default for K8s if general is default (optional UX choice)
            // firstVisibleTab = firstVisibleTab === 'agent-general' ? 'agent-namespaces' : firstVisibleTab;
        } else if (tabType === 'agent-linux-specific' && environmentType === 'linux') {
            shouldShow = true;
            // firstVisibleTab = firstVisibleTab === 'agent-general' ? 'agent-linux-specific' : firstVisibleTab;
        }
        // Add more 'else if' conditions for other agent types and their tabs

        tab.classList.toggle('hidden', !shouldShow);
    });

    // Activate the first relevant visible tab (or default to general)
    const tabsContainer = document.querySelector('nav[aria-label="Tabs"]');
    if (tabsContainer) {
        // Find the first *visible* tab button to activate
        const firstVisibleTabButton = tabsContainer.querySelector('.agent-config-tab:not(.hidden)');
        if(firstVisibleTabButton) {
            firstVisibleTab = firstVisibleTabButton.getAttribute('data-tab') || 'agent-general';
        }

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


    // --- Manage Tab Content and Fields ---
    const allContents = document.querySelectorAll('.agent-config-tab-content');
    allContents.forEach(content => {
        content.classList.toggle('hidden', content.id !== `${firstVisibleTab}-tab`);
    });

    // Show/hide specific fields within the general tab or other tabs
    const k8sSpecificElementsNodeList = document.querySelectorAll('.agent-k8s-only');
    const linuxSpecificElementsNodeList = document.querySelectorAll('.agent-linux-only');

    k8sSpecificElementsNodeList.forEach(el => el.classList.toggle('hidden', environmentType !== 'kubernetes'));
    linuxSpecificElementsNodeList.forEach(el => el.classList.toggle('hidden', environmentType !== 'linux'));
    // Add logic for other types...
}


// --- Add Agent Modal Functions ---
export function openAddAgentModal() {
    if (!addAgentModal) return;
    addAgentModal.classList.remove('hidden');
    addAgentModal.classList.add('flex'); // Use flex for centering
    document.body.style.overflow = 'hidden';
}
export function closeAddAgentModal() {
    if (!addAgentModal) return;
    addAgentModal.classList.add('hidden');
    addAgentModal.classList.remove('flex');
    document.body.style.overflow = '';
}
// Add event listeners for the Add Agent modal close buttons
if (addAgentModalCloseButton) {
    addAgentModalCloseButton.addEventListener('click', closeAddAgentModal);
}
if (addAgentModalUnderstandButton) {
    addAgentModalUnderstandButton.addEventListener('click', closeAddAgentModal);
}

// --- User Management UI (No changes needed from previous versions) ---
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
                editUserCallback(user); // Pass the full user object
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

// --- NEW: Function to populate environment filter dropdowns ---
/**
 * Populates the environment filter dropdowns.
 * @param {string[]} environments - Array of environment names.
 */
export function populateEnvironmentFilter(environments) {
    const incidentFilter = document.getElementById('environment-filter');
    const dashboardFilter = document.getElementById('dashboard-environment-filter');
    const filters = [incidentFilter, dashboardFilter].filter(Boolean); // Filter out null elements

    if (filters.length === 0) return;

    filters.forEach(filterSelect => {
        // Clear existing options except the first one ("Tất cả Môi trường")
        while (filterSelect.options.length > 1) {
            filterSelect.remove(1);
        }
        // Add new options
        environments.forEach(env => {
            const option = document.createElement('option');
            option.value = env;
            option.textContent = env;
            filterSelect.appendChild(option);
        });
    });
}
