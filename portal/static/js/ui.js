// portal/static/js/ui.js

// --- DOM Element References (Cached for performance) ---
const loadingSpinner = document.getElementById('loading-spinner');
const loadingOverlay = document.getElementById('loading-overlay');
const sidebarItems = document.querySelectorAll('.sidebar-item');
const contentSections = document.querySelectorAll('.content-section');

// Incident Modal Elements
const incidentModal = document.getElementById('incident-modal');
// const modalOverlay = document.querySelector('.modal-overlay'); // Can be ambiguous if multiple modals use different overlays
const modalTimestamp = document.getElementById('modal-timestamp');
const modalPodKey = document.getElementById('modal-pod-key');
const modalSeverity = document.getElementById('modal-severity');
const modalInitialReasons = document.getElementById('modal-initial-reasons');
const modalSummary = document.getElementById('modal-summary');
const modalRootCause = document.getElementById('modal-root-cause');
const modalTroubleshootingSteps = document.getElementById('modal-troubleshooting-steps');
const modalSampleLogs = document.getElementById('modal-sample-logs');
const modalK8sContext = document.getElementById('modal-k8s-context');
const modalInputPrompt = document.getElementById('modal-input-prompt');
const modalRawAiResponse = document.getElementById('modal-raw-ai-response');

// User Management Elements
const usersTableBody = document.getElementById('users-table-body');
const usersListErrorElem = document.getElementById('users-list-error');

// Edit User Modal Elements
const editUserModal = document.getElementById('edit-user-modal');
const editUserModalCloseButton = document.getElementById('edit-user-modal-close-button');
const editUserForm = document.getElementById('edit-user-form');
const editUserIdInput = document.getElementById('edit-user-id');
const editUsernameInput = document.getElementById('edit-username');
const editFullnameInput = document.getElementById('edit-fullname');
const editRoleSelect = document.getElementById('edit-role');
const editUserStatus = document.getElementById('edit-user-status');
const saveUserChangesButton = document.getElementById('save-user-changes-button');

// Create User Modal Elements
const createUserModal = document.getElementById('create-user-modal');
const createUserModalCloseButton = document.getElementById('create-user-modal-close-button');
const createUserForm = document.getElementById('create-user-form');
const createUserStatus = document.getElementById('create-user-status');
const passwordMatchError = document.getElementById('password-match-error');

// Agent Monitoring Elements
const agentStatusTableBody = document.getElementById('agent-status-table-body');
const agentStatusErrorElem = document.getElementById('agent-status-error');
const configAgentK8sVersionSpan = document.getElementById('config-agent-k8s-version');
const configAgentNodeCountSpan = document.getElementById('config-agent-node-count');
const agentScanIntervalInput = document.getElementById('agent-scan-interval');
const agentRestartThresholdInput = document.getElementById('agent-restart-threshold');
const agentLokiScanLevelSelect = document.getElementById('agent-loki-scan-level');

// Add Agent Modal Elements
const addAgentModal = document.getElementById('add-agent-modal');
const addAgentModalCloseButton = document.getElementById('add-agent-modal-close-button');
const addAgentModalUnderstandButton = document.getElementById('add-agent-modal-understand-button');


// --- General UI Functions ---

/**
 * Shows the loading indicator (spinner and overlay).
 */
export function showLoading() {
    if (loadingOverlay) loadingOverlay.classList.remove('hidden');
    if (loadingSpinner) loadingSpinner.classList.remove('hidden');
}

/**
 * Hides the loading indicator.
 */
export function hideLoading() {
    if (loadingOverlay) loadingOverlay.classList.add('hidden');
    if (loadingSpinner) loadingSpinner.classList.add('hidden');
}

/**
 * Formats an ISO date string into a Vietnamese date and time string.
 * @param {string} isoString - The ISO date string.
 * @returns {string} Formatted date string or 'N/A'.
 */
export function formatVietnameseDateTime(isoString) {
     if (!isoString) return 'N/A'; // Return 'N/A' if input is null or empty
     try {
         const dateObj = new Date(isoString);
         // Check if the date object is valid
         if (isNaN(dateObj.getTime())) {
             throw new Error(`Invalid date: ${isoString}`);
         }
         // Options for Vietnamese locale formatting
         const options = {
             day: '2-digit', month: '2-digit', year: 'numeric',
             hour: '2-digit', minute: '2-digit', second: '2-digit',
             hour12: false, // Use 24-hour format
             // timeZone: 'Asia/Ho_Chi_Minh' // Optional: Specify timezone if needed, otherwise uses browser default
         };
         return dateObj.toLocaleString('vi-VN', options);
     } catch (e) {
         // Log error and return an error string
         console.error("DEBUG: Error formatting date:", isoString, e);
         return 'Lỗi định dạng';
     }
}

/**
 * Safely sets the text content or inner HTML of an element.
 * Displays 'N/A' if the text is null, undefined, or empty.
 * @param {HTMLElement} element - The DOM element to update.
 * @param {string|null|undefined} text - The text or HTML string to set.
 * @param {boolean} [isHtml=false] - Whether the text should be treated as HTML.
 */
export function setText(element, text, isHtml = false) {
    if (element) {
        const na_html = '<i class="text-gray-400">N/A</i>'; // Italicized N/A
        // Determine the content to display: use N/A if text is empty/null/undefined
        const content = (text === null || text === undefined || String(text).trim() === '') ? na_html : text;

         if (isHtml) {
             // Set innerHTML if content is HTML or the fallback N/A HTML
             element.innerHTML = content;
         } else {
             // Set textContent for plain text, but use innerHTML for the N/A fallback
             if (content === na_html) {
                 element.innerHTML = na_html;
             } else {
                 element.textContent = text; // Use original text here
             }
         }
    }
}

/**
 * Returns the CSS class name corresponding to a severity level.
 * @param {string} severity - The severity level (e.g., 'CRITICAL', 'warning').
 * @returns {string} CSS class name.
 */
export function getSeverityClass(severity) {
    const s = severity ? severity.toLowerCase() : 'unknown';
    if (s === 'critical') return 'severity-critical';
    if (s === 'error') return 'severity-error';
    if (s === 'warning') return 'severity-warning';
    if (s === 'info') return 'severity-info';
    return 'severity-unknown'; // Default for unknown or null severity
}

/**
 * Creates an HTML string for a severity badge.
 * @param {string} severity - The severity level.
 * @returns {string} HTML string for the badge.
 */
export function createSeverityBadge(severity) {
    const badgeClass = getSeverityClass(severity);
    // Sanitize severity text before inserting into HTML
    const safeSeverity = document.createElement('span');
    safeSeverity.textContent = severity || 'UNKNOWN'; // Display UNKNOWN if severity is null/empty
    // Return the complete HTML badge string
    return `<span class="severity-badge ${badgeClass}">${safeSeverity.innerHTML}</span>`;
}

/**
 * Displays a status message in a designated element with appropriate styling.
 * @param {HTMLElement} statusElement - The DOM element to display the message in.
 * @param {string} message - The message text.
 * @param {'info'|'success'|'error'} [type='info'] - The type of message (determines color).
 */
export function showStatusMessage(statusElement, message, type = 'info') {
    if (!statusElement) return; // Exit if element doesn't exist
    statusElement.textContent = message; // Set the message text
    // Base classes for the status message
    let className = 'text-sm mr-4';
    // Add color class based on message type
    if (type === 'success') className += ' text-green-600';
    else if (type === 'error') className += ' text-red-600';
    else className += ' text-blue-600'; // Default to blue for info
    statusElement.className = className; // Apply the classes
    statusElement.classList.remove('hidden'); // Make the element visible
}

/**
 * Hides a status message element after a specified delay.
 * @param {HTMLElement} statusElement - The DOM element containing the status message.
 * @param {number} [delay=3000] - Delay in milliseconds before hiding.
 */
export function hideStatusMessage(statusElement, delay = 3000) {
    if (!statusElement) return;
    // Set a timeout to hide the element
    setTimeout(() => {
        statusElement.classList.add('hidden');
        statusElement.textContent = ''; // Clear the text
        }, delay);
}

// --- Incident Modal Functions ---
/**
 * Opens the incident detail modal and populates it with data.
 * @param {object} incidentData - The data for the incident to display.
 */
export function openModal(incidentData) {
    // Check if data and modal element exist
    if (!incidentData || !incidentModal) {
        console.error(`DEBUG: Incident data not provided or modal element missing.`);
        return;
    }
    try {
        // Populate modal fields using the setText helper
        setText(modalTimestamp, formatVietnameseDateTime(incidentData.timestamp));
        setText(modalPodKey, incidentData.pod_key);
        setText(modalSeverity, createSeverityBadge(incidentData.severity), true); // Use HTML for badge
        setText(modalInitialReasons, incidentData.initial_reasons);
        setText(modalSummary, incidentData.summary);
        setText(modalRootCause, incidentData.root_cause);
        setText(modalTroubleshootingSteps, incidentData.troubleshooting_steps);
        setText(modalSampleLogs, incidentData.sample_logs);
        setText(modalK8sContext, incidentData.k8s_context);

        // Determine if AI analysis was performed for this incident
        const aiEnabledForIncident = !!incidentData.input_prompt || !!incidentData.raw_ai_response;

        // Show/hide AI-related sections based on whether AI was used
        const rootCauseSection = modalRootCause?.closest('.modal-section');
        const stepsSection = modalTroubleshootingSteps?.closest('.modal-section');
        const promptSection = modalInputPrompt?.closest('.modal-section');
        const responseSection = modalRawAiResponse?.closest('.modal-section');

        if (rootCauseSection) rootCauseSection.style.display = aiEnabledForIncident ? 'block' : 'none';
        if (stepsSection) stepsSection.style.display = aiEnabledForIncident ? 'block' : 'none';
        if (promptSection) promptSection.style.display = aiEnabledForIncident ? 'block' : 'none';
        if (responseSection) responseSection.style.display = aiEnabledForIncident ? 'block' : 'none';

        // Populate AI prompt and response fields (will be hidden if AI wasn't used)
        setText(modalInputPrompt, incidentData.input_prompt);
        setText(modalRawAiResponse, incidentData.raw_ai_response);

        // Make the modal visible
        incidentModal.classList.add('modal-visible');
        document.body.style.overflow = 'hidden'; // Prevent background scrolling

    } catch (error) {
        // Log any errors during modal population
        console.error("DEBUG: Error occurred inside openModal:", error);
    }
}

/**
 * Closes the incident detail modal.
 */
export function closeModal() {
    if (incidentModal) {
        incidentModal.classList.remove('modal-visible'); // Hide the modal
    }
    document.body.style.overflow = ''; // Restore background scrolling
}

// --- Section Activation ---
/**
 * Sets the active section in the UI, hides others, and calls a callback to load data.
 * @param {string} targetId - The ID of the section to activate (e.g., 'dashboard').
 * @param {function} loadSectionDataCallback - Callback function to load data for the section.
 */
export function setActiveSection(targetId, loadSectionDataCallback) {
    // Hide all content sections first
    contentSections.forEach(section => {
        section.classList.add('hidden');
    });
    // Show the target section (append '-content' to the ID)
    const targetSection = document.getElementById(targetId + '-content');
    if (targetSection) {
        targetSection.classList.remove('hidden');
    } else {
        // Fallback to dashboard if target not found
        console.warn(`DEBUG: Target section '${targetId}-content' not found.`);
        document.getElementById('dashboard-content')?.classList.remove('hidden');
        targetId = 'dashboard'; // Update targetId for sidebar styling
    }
    // Update sidebar item styles
    sidebarItems.forEach(item => {
        const href = item.getAttribute('href');
        // Check href against the potentially updated targetId
        const isActive = href === '#' + targetId;
        item.classList.toggle('active', isActive); // Add/remove 'active' class (CSS handles the style)
    });
    // Load data for the newly active section
    if (typeof loadSectionDataCallback === 'function') {
        loadSectionDataCallback(targetId);
    }
}

// --- Agent Monitoring UI ---
/**
 * Renders the agent status table.
 * **Updated:** Does not render K8s Version and Node Count columns. Stores this info in data attributes.
 * @param {Array} agents - Array of agent objects.
 * @param {Function} configureCallback - Callback function when configure button is clicked. Takes (agentId, clusterName, clusterInfo).
 * @param {string} currentUserRole - Role of the current user ('admin' or 'user').
 */
export function renderAgentStatusTable(agents, configureCallback, currentUserRole) {
    if (!agentStatusTableBody || !agentStatusErrorElem) {
        console.warn("Agent status table elements not found.");
        return;
    }
    agentStatusTableBody.innerHTML = ''; // Clear previous content
    agentStatusErrorElem.classList.add('hidden'); // Hide error message

    if (!agents || agents.length === 0) {
        // Display message if no agents are active, adjust colspan
        agentStatusTableBody.innerHTML = `<tr><td colspan="5" class="text-center py-6 text-gray-500">Không có agent nào đang hoạt động.</td></tr>`;
        return;
    }

    // Create a row for each agent
    agents.forEach(agent => {
        const row = document.createElement('tr');
        row.setAttribute('data-agent-id', agent.agent_id);
        // Store cluster info as data attributes on the row for later retrieval
        row.setAttribute('data-k8s-version', agent.k8s_version || 'N/A');
        row.setAttribute('data-node-count', agent.node_count !== null ? agent.node_count : 'N/A');

        // Helper to create table cells
        const createCell = (content, isHtml = false, cssClass = null) => {
            const cell = document.createElement('td');
            cell.className = 'px-4 py-3 text-sm text-gray-700 align-middle whitespace-nowrap';
            if(cssClass) cell.classList.add(cssClass);
            setText(cell, content, isHtml); // Use helper to set content
            return cell;
        };

        // Append cells for agent info (excluding K8s Version and Node Count)
        row.appendChild(createCell(`${agent.agent_id || 'N/A'} / ${agent.cluster_name || 'N/A'}`));
        row.appendChild(createCell(`<span class="severity-badge severity-info">Active</span>`, true)); // Status badge
        row.appendChild(createCell(formatVietnameseDateTime(agent.last_seen_timestamp)));
        row.appendChild(createCell(agent.agent_version || 'N/A'));

        // Action cell (Configure button)
        const actionCell = document.createElement('td');
        actionCell.className = 'px-4 py-3 text-sm text-gray-700 align-middle';
        const actionContainer = document.createElement('div');
        actionContainer.className = 'flex items-center space-x-2';

        // Only show Configure button for admins
        if (currentUserRole === 'admin') {
            const configButton = document.createElement('button');
            configButton.textContent = 'Cấu hình';
            configButton.className = 'text-indigo-600 hover:text-indigo-900 hover:underline text-xs font-medium whitespace-nowrap configure-agent-btn';
            // Add click listener to call the configure callback
            configButton.onclick = () => {
                if (typeof configureCallback === 'function') {
                    // Retrieve cluster info from data attributes
                    const k8sVersion = row.getAttribute('data-k8s-version');
                    const nodeCount = row.getAttribute('data-node-count');
                    // Pass agent details and cluster info object to the callback
                    configureCallback(agent.agent_id, agent.cluster_name, { k8sVersion, nodeCount });
                }
            };
            actionContainer.appendChild(configButton);
        } else {
             // Show N/A if user is not admin
             setText(actionContainer, '<i class="text-gray-400">N/A</i>', true);
        }
        actionCell.appendChild(actionContainer);
        row.appendChild(actionCell);

        // Append the completed row to the table body
        agentStatusTableBody.appendChild(row);
    });
}

/**
 * Displays an error message in the agent status section.
 * @param {string} message - The error message to display.
 */
export function showAgentStatusError(message) {
    if (agentStatusTableBody) agentStatusTableBody.innerHTML = ''; // Clear table content
    if (agentStatusErrorElem) {
        setText(agentStatusErrorElem, message || 'Lỗi tải trạng thái agent.');
        agentStatusErrorElem.classList.remove('hidden'); // Show error message element
    }
}

/**
 * Populates the agent configuration form with fetched data.
 * @param {object} agentConfig - Configuration data for the agent.
 * @param {object} clusterInfo - Object containing { k8sVersion, nodeCount }.
 */
export function populateAgentConfigForm(agentConfig, clusterInfo) {
    // Populate general settings
    if (agentScanIntervalInput) agentScanIntervalInput.value = agentConfig.scan_interval_seconds ?? 30;
    if (agentRestartThresholdInput) agentRestartThresholdInput.value = agentConfig.restart_count_threshold ?? 5;
    if (agentLokiScanLevelSelect) agentLokiScanLevelSelect.value = agentConfig.loki_scan_min_level ?? 'INFO';
    // Populate cluster info display within the config section
    if (configAgentK8sVersionSpan) setText(configAgentK8sVersionSpan, clusterInfo?.k8sVersion || 'N/A');
    if (configAgentNodeCountSpan) setText(configAgentNodeCountSpan, clusterInfo?.nodeCount !== null ? String(clusterInfo.nodeCount) : 'N/A'); // Ensure it's a string or N/A
}


// --- Add Agent Modal Functions ---
/**
 * Opens the "Add Agent" instructional modal.
 */
export function openAddAgentModal() {
    if (!addAgentModal) return;
    addAgentModal.classList.remove('hidden');
    addAgentModal.classList.add('flex'); // Use flex to enable centering
    document.body.style.overflow = 'hidden'; // Prevent background scroll
}

/**
 * Closes the "Add Agent" instructional modal.
 */
export function closeAddAgentModal() {
    if (!addAgentModal) return;
    addAgentModal.classList.add('hidden');
    addAgentModal.classList.remove('flex');
    document.body.style.overflow = ''; // Restore background scroll
}

// Add event listeners for the new modal's close buttons
if (addAgentModalCloseButton) {
    addAgentModalCloseButton.addEventListener('click', closeAddAgentModal);
}
if (addAgentModalUnderstandButton) {
    addAgentModalUnderstandButton.addEventListener('click', closeAddAgentModal);
}


// --- User Management UI ---
/**
 * Renders the user management table.
 * @param {Array} users - Array of user objects.
 * @param {Function} editUserCallback - Callback function when edit button is clicked.
 */
export function renderUserTable(users, editUserCallback) {
    if (!usersTableBody) { console.error("User table body element not found."); return; }
    if (usersListErrorElem) usersListErrorElem.classList.add('hidden'); // Hide error
    usersTableBody.innerHTML = ''; // Clear previous content

    if (!users || users.length === 0) {
        usersTableBody.innerHTML = `<tr><td colspan="4" class="text-center py-6 text-gray-500">Không có người dùng nào.</td></tr>`;
        return;
    }

    // Create a row for each user
    users.forEach(user => {
        const row = document.createElement('tr');
        row.setAttribute('data-user-id', user.id);

        // Username cell
        const userCell = document.createElement('td');
        userCell.className = 'px-4 py-2 text-sm text-gray-900 font-medium';
        setText(userCell, user.username);
        row.appendChild(userCell);

        // Fullname cell
        const nameCell = document.createElement('td');
        nameCell.className = 'px-4 py-2 text-sm text-gray-600';
        setText(nameCell, user.fullname); // Use setText to handle potential null/empty
        row.appendChild(nameCell);

        // Role cell
        const roleCell = document.createElement('td');
        roleCell.className = 'px-4 py-2 text-sm text-gray-600';
        setText(roleCell, user.role);
        row.appendChild(roleCell);

        // Action cell (Edit button)
        const actionCell = document.createElement('td');
        actionCell.className = 'px-4 py-2 text-sm text-gray-600';
        const editButton = document.createElement('button');
        editButton.textContent = 'Sửa';
        editButton.className = 'text-indigo-600 hover:text-indigo-900 hover:underline text-xs font-medium';
        // Add click listener to call the edit callback
        editButton.onclick = (event) => {
            event.stopPropagation(); // Prevent row click event if any
            if (typeof editUserCallback === 'function') {
                editUserCallback(user); // Pass the user object to the callback
            }
        };
        actionCell.appendChild(editButton);
        row.appendChild(actionCell);

        usersTableBody.appendChild(row);
    });
}

/**
 * Displays an error message in the user list section.
 * @param {string} message - The error message.
 */
export function showUserListError(message) {
    if (usersTableBody) usersTableBody.innerHTML = ''; // Clear table
    if (usersListErrorElem) {
        usersListErrorElem.textContent = message;
        usersListErrorElem.classList.remove('hidden'); // Show error element
    }
}

/**
 * Opens the Create User modal.
 */
export function openCreateUserModal() {
    if (!createUserModal) return;
    clearCreateUserForm(); // Clear form before opening
    createUserModal.classList.remove('hidden');
    createUserModal.classList.add('flex'); // Use flex for centering
    document.body.style.overflow = 'hidden';
    document.getElementById('new-username')?.focus(); // Focus username field
}

/**
 * Closes the Create User modal.
 */
export function closeCreateUserModal() {
    if (!createUserModal) return;
    createUserModal.classList.add('hidden');
    createUserModal.classList.remove('flex');
    document.body.style.overflow = '';
    // Clear status message
    if (createUserStatus) {
        createUserStatus.classList.add('hidden');
        createUserStatus.textContent = '';
    }
}

/**
 * Clears the Create User form fields and hides error/status messages.
 */
export function clearCreateUserForm() {
    if (createUserForm) { createUserForm.reset(); } // Reset form fields
    if (passwordMatchError) passwordMatchError.classList.add('hidden'); // Hide password mismatch error
    if (createUserStatus) createUserStatus.classList.add('hidden'); // Hide status message
}

/**
 * Opens the Edit User modal and populates it with user data.
 * @param {object} userData - The data of the user to edit.
 */
export function openEditUserModal(userData) {
    if (!editUserModal || !editUserForm) return;
    populateEditUserModal(userData); // Fill form with user data
    editUserModal.classList.remove('hidden');
    editUserModal.classList.add('flex'); // Use flex for centering
    document.body.style.overflow = 'hidden';
}

/**
 * Closes the Edit User modal.
 */
export function closeEditUserModal() {
    if (!editUserModal) return;
    editUserModal.classList.add('hidden');
    editUserModal.classList.remove('flex');
    document.body.style.overflow = '';
    // Clear status message
    if (editUserStatus) {
        editUserStatus.classList.add('hidden');
        editUserStatus.textContent = '';
    }
}

/**
 * Populates the Edit User modal form fields.
 * @param {object} userData - The user data.
 */
export function populateEditUserModal(userData) {
    if (!editUserIdInput || !editUsernameInput || !editFullnameInput || !editRoleSelect) return;
    // Fill form fields, username is read-only
    editUserIdInput.value = userData.id || '';
    editUsernameInput.value = userData.username || '';
    editFullnameInput.value = userData.fullname || '';
    editRoleSelect.value = userData.role || 'user';
}
