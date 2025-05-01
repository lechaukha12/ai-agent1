// portal/static/js/ui.js
// Added console.log statements for debugging modal issues

// --- DOM Element Selectors ---
const loadingSpinner = document.getElementById('loading-spinner');
const loadingOverlay = document.getElementById('loading-overlay');
const sidebarItems = document.querySelectorAll('.sidebar-item');
const contentSections = document.querySelectorAll('.content-section');
const incidentModal = document.getElementById('incident-modal');
const modalOverlay = document.querySelector('.modal-overlay');
// Modal Content Elements
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
// Status message elements
export const saveGeneralConfigStatus = document.getElementById('save-general-config-status');
export const saveTelegramConfigStatus = document.getElementById('save-telegram-config-status');
export const saveNsConfigStatus = document.getElementById('save-ns-config-status');
export const saveAiConfigStatus = document.getElementById('save-ai-config-status');


// --- Utility Functions ---

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
             // timeZone: 'Asia/Ho_Chi_Minh'
         };
         return dateObj.toLocaleString('vi-VN', options);
     } catch (e) {
         console.error("DEBUG: Error formatting date:", isoString, e); // DEBUG
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
             element.textContent = (content === na_html) ? 'N/A' : text;
             if (element.textContent === 'N/A' && content === na_html) {
                 element.innerHTML = content;
             }
         }
    } else {
        // DEBUG: Log if an element is unexpectedly null
        // console.warn("DEBUG: setText called with null element for text:", text);
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
    return `<span class="severity-badge ${badgeClass}">${severity || 'UNKNOWN'}</span>`;
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
    setTimeout(() => { statusElement.classList.add('hidden'); }, delay);
}


// --- Modal Functions ---

export function openModal(incidentData) {
    console.log("DEBUG: ui.openModal called with data:", incidentData); // DEBUG
    if (!incidentData || !incidentModal) {
        console.error(`DEBUG: Incident data not provided or modal element missing.`);
        return;
    }

    try {
        // Populate modal fields
        setText(modalTimestamp, formatVietnameseDateTime(incidentData.timestamp));
        setText(modalPodKey, incidentData.pod_key);
        setText(modalSeverity, createSeverityBadge(incidentData.severity), true);
        setText(modalInitialReasons, incidentData.initial_reasons);
        setText(modalSummary, incidentData.summary);
        setText(modalRootCause, incidentData.root_cause);
        setText(modalTroubleshootingSteps, incidentData.troubleshooting_steps);
        setText(modalSampleLogs, incidentData.sample_logs);
        setText(modalK8sContext, incidentData.k8s_context);

        // Conditionally show AI fields
        const aiEnabledForIncident = !!incidentData.input_prompt || !!incidentData.raw_ai_response;
        console.log("DEBUG: AI fields enabled for this incident?", aiEnabledForIncident); // DEBUG
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

        // Show the modal
        console.log("DEBUG: Attempting to add 'modal-visible' class"); // DEBUG
        incidentModal.classList.add('modal-visible');
        document.body.style.overflow = 'hidden';
        console.log("DEBUG: 'modal-visible' class added, body overflow hidden."); // DEBUG

    } catch (error) {
        console.error("DEBUG: Error occurred inside openModal:", error); // DEBUG
    }
}

export function closeModal() {
    console.log("DEBUG: closeModal called"); // DEBUG
    if (incidentModal) {
        incidentModal.classList.remove('modal-visible');
        console.log("DEBUG: 'modal-visible' class removed."); // DEBUG
    }
    document.body.style.overflow = '';
}


// --- Navigation Logic ---

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
        const isActive = item.getAttribute('href') === '#' + targetId;
        item.classList.toggle('active', isActive);
        item.classList.toggle('text-gray-700', !isActive);
        item.classList.toggle('hover:bg-gray-100', !isActive);
    });

    if (typeof loadSectionDataCallback === 'function') {
        loadSectionDataCallback(targetId);
    }
}
