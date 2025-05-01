// portal/static/js/main.js
// Added console.log statements for debugging modal issues
import * as api from './api.js';
import * as ui from './ui.js';
import * as charts from './charts.js';
import * as settings from './settings.js';

// === State Variables ===
let currentIncidentPage = 1;
let totalIncidentPages = 1;
let currentPodFilter = '';
let currentSeverityFilter = '';
let currentStatsDays = 1;
let currentIncidentStartDate = null;
let currentIncidentEndDate = null;
let incidentsDataCache = {}; // Cache for modal data

// === DOM Element Selectors ===
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
// Incidents Tab
const incidentsTableBody = document.getElementById('incidents-table-body');
const podFilterInput = document.getElementById('pod-filter');
const severityFilterSelect = document.getElementById('severity-filter');
const filterButton = document.getElementById('filter-button');
const refreshIncidentsButton = document.getElementById('refresh-incidents-button');
const paginationControls = document.getElementById('pagination-controls');
const paginationInfo = document.getElementById('pagination-info');
const prevPageButton = document.getElementById('prev-page');
const nextPageButton = document.getElementById('next-page');
// Dashboard Tab
const totalIncidentsElem = document.getElementById('total-incidents');
const totalGeminiCallsElem = document.getElementById('total-gemini-calls');
const totalTelegramAlertsElem = document.getElementById('total-telegram-alerts');
const timeRangeButtons = document.querySelectorAll('.time-range-btn');
// Settings Tab
const saveNsConfigButton = document.getElementById('save-ns-config-button');
const saveGeneralConfigButton = document.getElementById('save-general-config-button');
const saveTelegramConfigButton = document.getElementById('save-telegram-config-button');
const saveAiConfigButton = document.getElementById('save-ai-config-button');
const enableAiToggle = document.getElementById('enable-ai-toggle');
// General UI
const sidebarItems = document.querySelectorAll('.sidebar-item');
const modalCloseButton = document.getElementById('modal-close-button');
const modalOverlay = document.querySelector('.modal-overlay');


// === Data Loading and Rendering Logic ===

function loadActiveSectionData(activeSectionId) {
    console.log(`DEBUG: Loading data for section: ${activeSectionId}`); // DEBUG
    switch (activeSectionId) {
        case 'dashboard': loadDashboardData(); break;
        case 'incidents': loadIncidentsData(true); break;
        case 'settings': loadAllSettings(); break;
    }
}

async function loadDashboardData() {
    ui.showLoading();
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
            if (severityPieNoDataElem) severityPieNoDataElem.textContent = `Không có sự cố hôm nay.`;
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
    } finally {
        ui.hideLoading();
    }
}

async function loadIncidentsData(forceReload = false) {
    const tableBodyContent = incidentsTableBody?.innerHTML.trim() || '';
    if (!forceReload && tableBodyContent && !tableBodyContent.includes('Đang tải dữ liệu') && !tableBodyContent.includes('Không tìm thấy')) {
        console.log("DEBUG: Incidents table already populated, skipping fetch."); // DEBUG
        return;
    }
    ui.showLoading();
    if (incidentsTableBody) incidentsTableBody.innerHTML = `<tr><td colspan="5" class="text-center py-6 text-gray-500">Đang tải dữ liệu...</td></tr>`;
    if (paginationControls) paginationControls.classList.add('hidden');
    incidentsDataCache = {}; // Clear cache

    try {
        const data = await api.fetchIncidents(currentIncidentPage, currentPodFilter, currentSeverityFilter, currentIncidentStartDate, currentIncidentEndDate);
        const incidents = data.incidents;
        const pagination = data.pagination;
        totalIncidentPages = pagination.total_pages;

        if (incidentsTableBody) {
            incidentsTableBody.innerHTML = '';
            if (incidents.length === 0) {
                incidentsTableBody.innerHTML = `<tr><td colspan="5" class="text-center py-6 text-gray-500">Không tìm thấy sự cố nào khớp.</td></tr>`;
            } else {
                console.log(`DEBUG: Rendering ${incidents.length} incidents.`); // DEBUG
                incidents.forEach(incident => {
                    incidentsDataCache[incident.id] = incident; // Cache data
                    const row = document.createElement('tr');
                    row.setAttribute('data-incident-id', incident.id);
                    row.classList.add('cursor-pointer', 'hover:bg-gray-100');

                    // --- DEBUG: Add click listener ---
                    row.addEventListener('click', (event) => {
                        const clickedRow = event.currentTarget; // Get the row that was clicked
                        const incidentId = clickedRow.getAttribute('data-incident-id');
                        console.log(`DEBUG: Row clicked! Incident ID: ${incidentId}`); // DEBUG
                        if (!incidentId) {
                            console.error("DEBUG: Clicked row is missing data-incident-id attribute!");
                            return;
                        }
                        const incidentData = incidentsDataCache[incidentId];
                        console.log("DEBUG: Incident data from cache:", incidentData); // DEBUG
                        if (incidentData) {
                            ui.openModal(incidentData); // Call openModal from ui.js
                        } else {
                            console.error(`DEBUG: Incident data for ID ${incidentId} not found in cache! Cache content:`, incidentsDataCache);
                        }
                    });
                    // --- End DEBUG ---


                    const severityUpper = incident.severity ? incident.severity.toUpperCase() : '';
                    if (severityUpper === 'CRITICAL') row.classList.add('bg-red-50');
                    else if (severityUpper === 'ERROR') row.classList.add('bg-orange-50');
                    else if (severityUpper === 'WARNING') row.classList.add('bg-yellow-50');

                    const createCell = (content, isHtml = false, allowWrap = false) => {
                        const cell = document.createElement('td');
                        cell.className = 'px-4 py-3 text-sm text-gray-700 align-top';
                        ui.setText(cell, content, isHtml);
                        cell.title = cell.textContent;
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
                    incidentsTableBody.appendChild(row);
                });
            }
        }

        if (paginationControls) {
            if (totalIncidentPages > 0) {
                if (paginationInfo) paginationInfo.textContent = `Trang ${pagination.page} / ${totalIncidentPages} (Tổng: ${pagination.total_items})`;
                if (prevPageButton) prevPageButton.disabled = pagination.page <= 1;
                if (nextPageButton) nextPageButton.disabled = pagination.page >= totalIncidentPages;
                paginationControls.classList.remove('hidden');
            } else {
                paginationControls.classList.add('hidden');
            }
        }
    } catch (error) {
        console.error('DEBUG: Failed to load incidents data:', error);
        if (incidentsTableBody) incidentsTableBody.innerHTML = `<tr><td colspan="5" class="text-center py-6 text-red-500">Lỗi tải dữ liệu sự cố: ${error.message}.</td></tr>`;
        if (paginationControls) paginationControls.classList.add('hidden');
    } finally {
        ui.hideLoading();
    }
}

function loadAllSettings() {
    settings.renderNamespaceList();
    settings.loadGeneralSettings();
    settings.loadTelegramSettings();
    settings.loadAiSettings();
}

// === Event Listener Setup ===
document.addEventListener('DOMContentLoaded', () => {
    // Set Initial Date Range
    const today = new Date();
    currentIncidentStartDate = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate(), 0, 0, 0, 0)).toISOString();
    currentIncidentEndDate = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate(), 23, 59, 59, 999)).toISOString();

    // Activate Initial Section
    ui.setActiveSection('dashboard', loadActiveSectionData);

    // Sidebar Navigation
    sidebarItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = item.getAttribute('href')?.substring(1);
            if(targetId) ui.setActiveSection(targetId, loadActiveSectionData);
        });
    });

    // Incident Filtering & Refresh
    if (filterButton) filterButton.addEventListener('click', () => {
        currentPodFilter = podFilterInput?.value || '';
        currentSeverityFilter = severityFilterSelect?.value || '';
        currentIncidentPage = 1;
        loadIncidentsData(true);
    });
    if (podFilterInput) podFilterInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') filterButton?.click(); });
    if (refreshIncidentsButton) refreshIncidentsButton.addEventListener('click', () => { loadIncidentsData(true); });

    // Pagination
    if (prevPageButton) prevPageButton.addEventListener('click', () => {
        if (currentIncidentPage > 1) { currentIncidentPage--; loadIncidentsData(true); }
    });
    if (nextPageButton) nextPageButton.addEventListener('click', () => {
        if (currentIncidentPage < totalIncidentPages) { currentIncidentPage++; loadIncidentsData(true); }
    });

    // Dashboard Time Range
    timeRangeButtons.forEach(button => {
        button.addEventListener('click', () => {
            const days = parseInt(button.getAttribute('data-days'));
            currentStatsDays = days;

            const endDate = new Date(); const startDate = new Date();
            startDate.setDate(endDate.getDate() - days + 1);
            currentIncidentStartDate = new Date(Date.UTC(startDate.getUTCFullYear(), startDate.getUTCMonth(), startDate.getUTCDate(), 0, 0, 0, 0)).toISOString();
            currentIncidentEndDate = new Date(Date.UTC(endDate.getUTCFullYear(), endDate.getUTCMonth(), endDate.getUTCDate(), 23, 59, 59, 999)).toISOString();

            loadDashboardData();

            if (document.getElementById('incidents-content')?.classList.contains('hidden') === false) {
                currentIncidentPage = 1; loadIncidentsData(true);
            }

            timeRangeButtons.forEach(btn => {
                 btn.classList.remove('bg-blue-500', 'text-white');
                 btn.classList.add('bg-gray-300', 'text-gray-700');
                 btn.disabled = false;
            });
            button.classList.add('bg-blue-500', 'text-white');
            button.classList.remove('bg-gray-300', 'text-gray-700');
            button.disabled = true;
        });
         if (button.getAttribute('data-days') === '1') button.click();
         else button.disabled = false;
    });

    // Settings Save Buttons
    if (saveNsConfigButton) saveNsConfigButton.addEventListener('click', settings.saveMonitoredNamespaces);
    if (saveGeneralConfigButton) saveGeneralConfigButton.addEventListener('click', settings.saveGeneralSettings);
    if (saveTelegramConfigButton) saveTelegramConfigButton.addEventListener('click', settings.saveTelegramSettings);
    if (saveAiConfigButton) saveAiConfigButton.addEventListener('click', settings.saveAiSettings);

    // AI Toggle Change Listener
    if (enableAiToggle) enableAiToggle.addEventListener('change', settings.handleAiToggleChange);

    // Modal Close Listeners
    if (modalCloseButton) modalCloseButton.addEventListener('click', ui.closeModal);
    if (modalOverlay) modalOverlay.addEventListener('click', (event) => { if (event.target === modalOverlay) ui.closeModal(); });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && document.getElementById('incident-modal')?.classList.contains('modal-visible')) ui.closeModal(); });

    console.log("DEBUG: main.js loaded and event listeners attached."); // DEBUG

}); // End DOMContentLoaded
