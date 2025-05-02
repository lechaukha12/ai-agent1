// portal/static/js/ui.js

const loadingSpinner = document.getElementById('loading-spinner');
const loadingOverlay = document.getElementById('loading-overlay');
const sidebarItems = document.querySelectorAll('.sidebar-item');
const contentSections = document.querySelectorAll('.content-section');
const incidentModal = document.getElementById('incident-modal');
const modalOverlay = document.querySelector('.modal-overlay');
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
const clusterInfoLoading = document.getElementById('cluster-info-loading');
const clusterInfoError = document.getElementById('cluster-info-error');
const clusterInfoContent = document.getElementById('cluster-info-content');
const clusterK8sVersion = document.getElementById('cluster-k8s-version');
const clusterPlatform = document.getElementById('cluster-platform');
const clusterTotalNodes = document.getElementById('cluster-total-nodes');
const clusterCpuCapacity = document.getElementById('cluster-cpu-capacity');
const clusterMemoryCapacity = document.getElementById('cluster-memory-capacity');
const clusterOsImage = document.getElementById('cluster-os-image');
const clusterKernelVersion = document.getElementById('cluster-kernel-version');
const clusterKubeletVersion = document.getElementById('cluster-kubelet-version');


export function showLoading() {
    if (loadingSpinner) loadingSpinner.classList.remove('hidden');
    if (loadingOverlay) loadingOverlay.classList.remove('hidden');
}

export function hideLoading() {
    if (loadingSpinner) loadingSpinner.classList.add('hidden');
    if (loadingOverlay) loadingOverlay.classList.add('hidden');
}

export function formatVietnameseDateTime(isoString) {
     if (!isoString) return 'N/A';
     try {
         const dateObj = new Date(isoString);
         if (isNaN(dateObj.getTime())) { throw new Error(`Invalid date: ${isoString}`); }
         const options = {
             day: '2-digit', month: '2-digit', year: 'numeric',
             hour: '2-digit', minute: '2-digit', second: '2-digit',
             hour12: false,
         };
         return dateObj.toLocaleString('vi-VN', options);
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
                 element.textContent = text;
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
    let className = 'text-sm mr-4';
    if (type === 'success') className += ' text-green-600';
    else if (type === 'error') className += ' text-red-600';
    else className += ' text-blue-600';
    statusElement.className = className;
    statusElement.classList.remove('hidden');
}

export function hideStatusMessage(statusElement, delay = 3000) {
    if (!statusElement) return;
    setTimeout(() => {
        statusElement.classList.add('hidden');
        statusElement.textContent = '';
        }, delay);
}


export function openModal(incidentData) {
    if (!incidentData || !incidentModal) {
        console.error(`DEBUG: Incident data not provided or modal element missing.`);
        return;
    }

    try {
        setText(modalTimestamp, formatVietnameseDateTime(incidentData.timestamp));
        setText(modalPodKey, incidentData.pod_key);
        setText(modalSeverity, createSeverityBadge(incidentData.severity), true);
        setText(modalInitialReasons, incidentData.initial_reasons);
        setText(modalSummary, incidentData.summary);
        setText(modalRootCause, incidentData.root_cause);
        setText(modalTroubleshootingSteps, incidentData.troubleshooting_steps);
        setText(modalSampleLogs, incidentData.sample_logs);
        setText(modalK8sContext, incidentData.k8s_context);

        const aiEnabledForIncident = !!incidentData.input_prompt || !!incidentData.raw_ai_response;
        const rootCauseSection = modalRootCause?.closest('.modal-section');
        const stepsSection = modalTroubleshootingSteps?.closest('.modal-section');
        const promptSection = modalInputPrompt?.closest('.modal-section');
        const responseSection = modalRawAiResponse?.closest('.modal-section');

        if (rootCauseSection) rootCauseSection.style.display = aiEnabledForIncident ? 'block' : 'none';
        if (stepsSection) stepsSection.style.display = aiEnabledForIncident ? 'block' : 'none';
        if (promptSection) promptSection.style.display = aiEnabledForIncident ? 'block' : 'none';
        if (responseSection) responseSection.style.display = aiEnabledForIncident ? 'block' : 'none';

        setText(modalInputPrompt, incidentData.input_prompt);
        setText(modalRawAiResponse, incidentData.raw_ai_response);

        incidentModal.classList.add('modal-visible');
        document.body.style.overflow = 'hidden';

    } catch (error) {
        console.error("DEBUG: Error occurred inside openModal:", error);
    }
}

export function closeModal() {
    if (incidentModal) {
        incidentModal.classList.remove('modal-visible');
    }
    document.body.style.overflow = '';
}


export function setActiveSection(targetId, loadSectionDataCallback) {
    contentSections.forEach(section => {
        section.classList.add('hidden');
    });

    const targetSection = document.getElementById(targetId + '-content');
    if (targetSection) {
        targetSection.classList.remove('hidden');
    } else {
        console.warn(`DEBUG: Target section '${targetId}-content' not found.`);
        document.getElementById('dashboard-content')?.classList.remove('hidden');
        targetId = 'dashboard';
    }

    sidebarItems.forEach(item => {
        const href = item.getAttribute('href');
        const isActive = href === '#' + targetId;
        item.classList.toggle('active', isActive);
        item.classList.toggle('bg-blue-100', isActive);
        item.classList.toggle('text-blue-700', isActive);
        item.classList.toggle('text-gray-700', !isActive);
        item.classList.toggle('hover:bg-gray-100', !isActive);
    });


    if (typeof loadSectionDataCallback === 'function') {
        loadSectionDataCallback(targetId);
    }
}

export function renderUserTable(users, editUserCallback) {
    if (!usersTableBody) {
        console.error("User table body element not found.");
        return;
    }
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

export function clearCreateUserForm() {
    const form = document.getElementById('create-user-form');
    if (form) {
        form.reset();
    }
    const passwordError = document.getElementById('password-match-error');
    if (passwordError) passwordError.classList.add('hidden');
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

// --- NEW: Render Cluster Info ---
export function renderClusterInfo(data) {
    if (!clusterInfoLoading || !clusterInfoError || !clusterInfoContent) return;

    clusterInfoLoading.classList.add('hidden');
    clusterInfoError.classList.add('hidden');
    clusterInfoContent.classList.remove('hidden');

    setText(clusterK8sVersion, data.kubernetes_version);
    setText(clusterPlatform, data.platform);
    setText(clusterTotalNodes, data.total_nodes);
    setText(clusterCpuCapacity, data.total_cpu_capacity);
    setText(clusterMemoryCapacity, data.total_memory_capacity);
    setText(clusterOsImage, data.os_image_sample);
    setText(clusterKernelVersion, data.kernel_version_sample);
    setText(clusterKubeletVersion, data.kubelet_version_sample);
}

export function showClusterInfoLoading() {
    if (!clusterInfoLoading || !clusterInfoError || !clusterInfoContent) return;
    clusterInfoLoading.classList.remove('hidden');
    clusterInfoError.classList.add('hidden');
    clusterInfoContent.classList.add('hidden');
}

export function showClusterInfoError(message) {
    if (!clusterInfoLoading || !clusterInfoError || !clusterInfoContent) return;
    clusterInfoLoading.classList.add('hidden');
    clusterInfoError.classList.remove('hidden');
    clusterInfoContent.classList.add('hidden');
    setText(clusterInfoError, message || 'Lỗi tải thông tin cluster.');
}
