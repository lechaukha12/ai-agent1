// portal/static/js/charts.js
// Simplified chart rendering for light mode only

// --- Chart Instances (Updated Names) ---
let lineChartInstance = null;
let topResourcesChartInstance = null; // Renamed from topPodsChartInstance
let environmentPieInstance = null;    // Renamed from namespacePieInstance
let severityPieInstance = null;

// --- Chart Colors (Light Mode - Keep as is) ---
const lightGridColor = 'rgba(229, 231, 235, 0.7)'; // gray-200
const lightTextColor = 'rgb(75, 85, 99)'; // gray-600
const lightTooltipBg = 'rgba(255, 255, 255, 0.9)';
const lightTooltipTitle = '#1f2937'; // gray-800
const lightTooltipBody = '#374151'; // gray-700
const lightTooltipBorder = '#e5e7eb'; // gray-200
const lightPieBorder = '#ffffff';
const chartColors = ['#ef4444','#f97316','#eab308','#22c55e','#06b6d4','#3b82f6','#8b5cf6','#ec4899','#6b7280'];
const chartHoverColors = chartColors.map(color => `${color}E6`);


// --- Chart Rendering Functions ---

/**
 * Renders the daily trend line chart.
 * @param {CanvasRenderingContext2D} ctx - The canvas context.
 * @param {HTMLElement} errorElem - The error message element.
 * @param {HTMLElement} noDataElem - The no-data message element.
 * @param {Array<object>} dailyStats - Array of daily statistics objects.
 */
export function renderLineChart(ctx, errorElem, noDataElem, dailyStats) {
     if (!ctx || !errorElem || !noDataElem) {
         console.warn("Line chart elements missing.");
         return;
     }
     errorElem.classList.add('hidden');
     noDataElem.classList.add('hidden');

     if (!dailyStats || dailyStats.length < 1) {
         noDataElem.classList.remove('hidden');
         if (lineChartInstance) { lineChartInstance.destroy(); lineChartInstance = null; }
         return;
     }

     // Ensure data is sorted by date
     try {
        dailyStats.sort((a, b) => new Date(a.date) - new Date(b.date));
     } catch(e) {
        console.error("Error sorting daily stats:", e);
        errorElem.textContent = "Lỗi sắp xếp dữ liệu.";
        errorElem.classList.remove('hidden');
        if (lineChartInstance) { lineChartInstance.destroy(); lineChartInstance = null; }
        return;
     }

     const labels = dailyStats.map(stat => stat.date);
     const incidentData = dailyStats.map(stat => stat.incident_count || 0); // Default to 0 if null/undefined
     const geminiData = dailyStats.map(stat => stat.model_calls || 0);
     const telegramData = dailyStats.map(stat => stat.telegram_alerts || 0);

     const chartData = {
         labels: labels,
         datasets: [
             { label: 'Sự Cố', data: incidentData, borderColor: 'rgb(239, 68, 68)', backgroundColor: 'rgba(239, 68, 68, 0.1)', tension: 0.2, fill: true, pointBackgroundColor: 'rgb(239, 68, 68)', pointRadius: 3, pointHoverRadius: 5 },
             { label: 'Gọi Model', data: geminiData, borderColor: 'rgb(59, 130, 246)', backgroundColor: 'rgba(59, 130, 246, 0.1)', tension: 0.2, fill: true, pointBackgroundColor: 'rgb(59, 130, 246)', pointRadius: 3, pointHoverRadius: 5 },
             { label: 'Cảnh báo Telegram', data: telegramData, borderColor: 'rgb(34, 197, 94)', backgroundColor: 'rgba(34, 197, 94, 0.1)', tension: 0.2, fill: true, pointBackgroundColor: 'rgb(34, 197, 94)', pointRadius: 3, pointHoverRadius: 5 }
         ]
     };
     const chartConfig = {
         type: 'line', data: chartData,
         options: {
             responsive: true, maintainAspectRatio: false,
             scales: {
                 y: { beginAtZero: true, ticks: { precision: 0, color: lightTextColor }, grid: { color: lightGridColor } },
                 x: { ticks: { color: lightTextColor, maxRotation: 0, autoSkip: true, maxTicksLimit: 10 }, grid: { display: false } }
             },
             plugins: {
                 legend: { position: 'bottom', labels: { color: lightTextColor, usePointStyle: true, boxWidth: 8 } },
                 tooltip: { mode: 'index', intersect: false, backgroundColor: lightTooltipBg, titleColor: lightTooltipTitle, bodyColor: lightTooltipBody, borderColor: lightTooltipBorder, borderWidth: 1 }
             },
             interaction: { mode: 'nearest', axis: 'x', intersect: false }
         }
     };
     if (lineChartInstance) { lineChartInstance.destroy(); }
     lineChartInstance = new Chart(ctx, chartConfig);
}

/**
 * Generic function to render a pie or doughnut chart.
 * @param {CanvasRenderingContext2D} ctx - The canvas context.
 * @param {HTMLElement} errorElem - The error message element.
 * @param {HTMLElement} noDataElem - The no-data message element.
 * @param {Chart|null} currentInstance - The current Chart.js instance (or null).
 * @param {object} chartData - Data object (key: label, value: count).
 * @param {string} title - The dataset label.
 * @param {string} [type='doughnut'] - Chart type ('doughnut' or 'pie').
 * @returns {Chart|null} - The new Chart.js instance or null if no data/error.
 */
function renderGenericPieChart(ctx, errorElem, noDataElem, currentInstance, chartData, title, type = 'doughnut') {
    if (!ctx || !errorElem || !noDataElem) {
        console.warn("Pie chart elements missing for:", title);
        return null;
    }
    errorElem.classList.add('hidden');
    noDataElem.classList.add('hidden');

    // Ensure chartData is an object
    if (typeof chartData !== 'object' || chartData === null) {
        console.warn(`Invalid chartData for ${title}:`, chartData);
        chartData = {}; // Use empty object to avoid errors
    }

    const labels = Object.keys(chartData);
    const data = Object.values(chartData);
    const total = data.reduce((sum, value) => sum + (Number(value) || 0), 0); // Ensure values are numbers

    if (labels.length === 0 || total === 0) {
        noDataElem.classList.remove('hidden');
        if (currentInstance) { currentInstance.destroy(); currentInstance = null; }
        return null;
    }

    const pieChartData = {
        labels: labels,
        datasets: [{
            label: title, data: data,
            backgroundColor: chartColors.slice(0, labels.length), // Use predefined colors
            hoverBackgroundColor: chartHoverColors.slice(0, labels.length),
            hoverOffset: 8, borderWidth: 1, borderColor: lightPieBorder
        }]
    };

    const pieChartConfig = {
        type: type, data: pieChartData,
        options: {
            responsive: true, maintainAspectRatio: false,
            cutout: type === 'doughnut' ? '60%' : '0%', // Adjust cutout for doughnut
            plugins: {
                legend: { position: 'bottom', labels: { color: lightTextColor, padding: 15, usePointStyle: true, boxWidth: 10 } },
                title: { display: false }, // Keep title display off, use HTML header
                tooltip: {
                    backgroundColor: lightTooltipBg, titleColor: lightTooltipTitle, bodyColor: lightTooltipBody,
                    borderColor: lightTooltipBorder, borderWidth: 1,
                    callbacks: {
                        label: function(context) {
                            let label = context.label || '';
                            if (label) { label += ': '; }
                            if (context.parsed !== null) {
                                // Recalculate total inside callback to be safe
                                const currentTotal = context.chart.data.datasets[0].data.reduce((sum, value) => sum + (Number(value) || 0), 0);
                                const percentage = currentTotal > 0 ? ((context.parsed / currentTotal) * 100).toFixed(1) + '%' : '0%';
                                label += `${context.parsed} (${percentage})`;
                            }
                            return label;
                        }
                    }
                }
            }
        }
    };
    // Destroy previous instance before creating new one
    if (currentInstance) { currentInstance.destroy(); }
    // Create and return the new instance
    try {
        return new Chart(ctx, pieChartConfig);
    } catch (e) {
        console.error(`Error creating chart "${title}":`, e);
        errorElem.textContent = `Lỗi vẽ biểu đồ: ${title}`;
        errorElem.classList.remove('hidden');
        return null;
    }
}

/**
 * Renders the Environment Distribution pie chart.
 * @param {CanvasRenderingContext2D} ctx - The canvas context.
 * @param {HTMLElement} errorElem - The error message element.
 * @param {HTMLElement} noDataElem - The no-data message element.
 * @param {object} chartData - Data object (key: environment name, value: count).
 */
export function renderEnvironmentPieChart(ctx, errorElem, noDataElem, chartData) { // Renamed function
    environmentPieInstance = renderGenericPieChart(ctx, errorElem, noDataElem, environmentPieInstance, chartData, 'Phân bố Môi trường');
}

/**
 * Renders the Severity Distribution pie chart.
 * @param {CanvasRenderingContext2D} ctx - The canvas context.
 * @param {HTMLElement} errorElem - The error message element.
 * @param {HTMLElement} noDataElem - The no-data message element.
 * @param {object} chartData - Data object (key: severity level, value: count).
 */
export function renderSeverityPieChart(ctx, errorElem, noDataElem, chartData) {
    severityPieInstance = renderGenericPieChart(ctx, errorElem, noDataElem, severityPieInstance, chartData, 'Phân loại Mức độ');
}

/**
 * Clears the severity pie chart and displays a message.
 * @param {HTMLElement} noDataElem - The no-data message element.
 * @param {string} [message="Chọn 'Hôm nay' để xem."] - The message to display.
 */
export function clearSeverityPieChart(noDataElem, message = "Chọn 'Hôm nay' để xem.") {
     if(severityPieInstance) {
         severityPieInstance.destroy();
         severityPieInstance = null;
     }
     if (noDataElem) {
        noDataElem.textContent = message;
        noDataElem.classList.remove('hidden');
     }
     // Ensure error message is also hidden
     const errorElem = document.getElementById('severity-pie-error');
     if (errorElem) errorElem.classList.add('hidden');
}

/**
 * Renders the Top Problematic Resources bar chart.
 * @param {CanvasRenderingContext2D} ctx - The canvas context.
 * @param {HTMLElement} errorElem - The error message element.
 * @param {HTMLElement} noDataElem - The no-data message element.
 * @param {object} chartData - Data object (key: resource name, value: count).
 */
export function renderTopResourcesBarChart(ctx, errorElem, noDataElem, chartData) { // Renamed function
    if (!ctx || !errorElem || !noDataElem) {
        console.warn("Top Resources chart elements missing.");
        return;
    }
    errorElem.classList.add('hidden');
    noDataElem.classList.add('hidden');

    if (typeof chartData !== 'object' || chartData === null) {
        console.warn("Invalid chartData for Top Resources:", chartData);
        chartData = {};
    }

    const labels = Object.keys(chartData);
    const data = Object.values(chartData);

    if (labels.length === 0) {
        noDataElem.classList.remove('hidden');
        if (topResourcesChartInstance) { topResourcesChartInstance.destroy(); topResourcesChartInstance = null; }
        return;
    }

    const barChartData = {
        labels: labels,
        datasets: [{
            label: 'Số lượng sự cố', data: data,
            backgroundColor: chartColors.slice(0, labels.length), // Use predefined colors
            hoverBackgroundColor: chartHoverColors.slice(0, labels.length),
            borderRadius: 4, borderWidth: 0
        }]
    };
    const barChartConfig = {
        type: 'bar', data: barChartData,
        options: {
            indexAxis: 'y', // Keep horizontal bars
            responsive: true, maintainAspectRatio: false,
            scales: {
                x: { beginAtZero: true, ticks: { precision: 0, color: lightTextColor }, grid: { color: lightGridColor, borderDash: [2, 3] } },
                y: { ticks: { color: lightTextColor }, grid: { display: false } }
            },
            plugins: {
                legend: { display: false }, title: { display: false }, // Keep titles off, use HTML
                tooltip: {
                     backgroundColor: lightTooltipBg, titleColor: lightTooltipTitle, bodyColor: lightTooltipBody,
                     borderColor: lightTooltipBorder, borderWidth: 1
                 }
            }
        }
    };
    // Destroy previous instance before creating new one
    if (topResourcesChartInstance) { topResourcesChartInstance.destroy(); }
    // Create and assign the new instance
    try {
        topResourcesChartInstance = new Chart(ctx, barChartConfig);
    } catch(e) {
        console.error("Error creating Top Resources chart:", e);
        errorElem.textContent = "Lỗi vẽ biểu đồ Top Resources.";
        errorElem.classList.remove('hidden');
        topResourcesChartInstance = null;
    }
}

/**
 * Destroys all active chart instances.
 */
export function destroyAllCharts() {
    if (lineChartInstance) { lineChartInstance.destroy(); lineChartInstance = null; }
    if (topResourcesChartInstance) { topResourcesChartInstance.destroy(); topResourcesChartInstance = null; }
    if (environmentPieInstance) { environmentPieInstance.destroy(); environmentPieInstance = null; }
    if (severityPieInstance) { severityPieInstance.destroy(); severityPieInstance = null; }
    console.debug("All chart instances destroyed.");
}
