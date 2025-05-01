// portal/static/js/charts.js
// Simplified chart rendering for light mode only

// --- Chart Instances ---
let lineChartInstance = null;
let topPodsChartInstance = null;
let namespacePieInstance = null;
let severityPieInstance = null;

// --- Chart Colors (Light Mode) ---
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

export function renderLineChart(ctx, errorElem, noDataElem, dailyStats) {
     if (!ctx) return;
     errorElem.classList.add('hidden');
     noDataElem.classList.add('hidden');

     if (!dailyStats || dailyStats.length < 1) {
         noDataElem.classList.remove('hidden');
         if (lineChartInstance) { lineChartInstance.destroy(); lineChartInstance = null; }
         return;
     }

     dailyStats.sort((a, b) => new Date(a.date) - new Date(b.date));
     const labels = dailyStats.map(stat => stat.date);
     const incidentData = dailyStats.map(stat => stat.incident_count);
     const geminiData = dailyStats.map(stat => stat.model_calls);
     const telegramData = dailyStats.map(stat => stat.telegram_alerts);

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

function renderGenericPieChart(ctx, errorElem, noDataElem, currentInstance, chartData, title, type = 'doughnut') {
    if (!ctx) return null;
    errorElem.classList.add('hidden');
    noDataElem.classList.add('hidden');

    const labels = Object.keys(chartData);
    const data = Object.values(chartData);
    const total = data.reduce((sum, value) => sum + value, 0);

    if (labels.length === 0 || total === 0) {
        noDataElem.classList.remove('hidden');
        if (currentInstance) { currentInstance.destroy(); currentInstance = null; }
        return null;
    }

    const pieChartData = {
        labels: labels,
        datasets: [{
            label: title, data: data,
            backgroundColor: chartColors.slice(0, labels.length),
            hoverBackgroundColor: chartHoverColors.slice(0, labels.length),
            hoverOffset: 8, borderWidth: 1, borderColor: lightPieBorder
        }]
    };

    const pieChartConfig = {
        type: type, data: pieChartData,
        options: {
            responsive: true, maintainAspectRatio: false,
            cutout: type === 'doughnut' ? '60%' : '0%',
            plugins: {
                legend: { position: 'bottom', labels: { color: lightTextColor, padding: 15, usePointStyle: true, boxWidth: 10 } },
                title: { display: false },
                tooltip: {
                    backgroundColor: lightTooltipBg, titleColor: lightTooltipTitle, bodyColor: lightTooltipBody,
                    borderColor: lightTooltipBorder, borderWidth: 1,
                    callbacks: {
                        label: function(context) {
                            let label = context.label || '';
                            if (label) { label += ': '; }
                            if (context.parsed !== null) {
                                const currentTotal = context.chart.data.datasets[0].data.reduce((sum, value) => sum + value, 0);
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
    if (currentInstance) { currentInstance.destroy(); }
    return new Chart(ctx, pieChartConfig);
}

export function renderNamespacePieChart(ctx, errorElem, noDataElem, chartData) {
    namespacePieInstance = renderGenericPieChart(ctx, errorElem, noDataElem, namespacePieInstance, chartData, 'Phân bố Namespace');
}

export function renderSeverityPieChart(ctx, errorElem, noDataElem, chartData) {
    severityPieInstance = renderGenericPieChart(ctx, errorElem, noDataElem, severityPieInstance, chartData, 'Phân loại Mức độ');
}

export function clearSeverityPieChart(noDataElem, message = "Chọn 'Hôm nay' để xem.") {
     if(severityPieInstance) {
         severityPieInstance.destroy();
         severityPieInstance = null;
     }
     if (noDataElem) {
        noDataElem.textContent = message;
        noDataElem.classList.remove('hidden');
     }
     const errorElem = document.getElementById('severity-pie-error');
     if (errorElem) errorElem.classList.add('hidden');
}

export function renderTopPodsBarChart(ctx, errorElem, noDataElem, chartData) {
    if (!ctx) return;
    errorElem.classList.add('hidden');
    noDataElem.classList.add('hidden');

    const labels = Object.keys(chartData);
    const data = Object.values(chartData);

    if (labels.length === 0) {
        noDataElem.classList.remove('hidden');
        if (topPodsChartInstance) { topPodsChartInstance.destroy(); topPodsChartInstance = null; }
        return;
    }

    const barChartData = {
        labels: labels,
        datasets: [{
            label: 'Số lượng sự cố', data: data,
            backgroundColor: chartColors.slice(0, labels.length),
            hoverBackgroundColor: chartHoverColors.slice(0, labels.length),
            borderRadius: 4, borderWidth: 0
        }]
    };
    const barChartConfig = {
        type: 'bar', data: barChartData,
        options: {
            indexAxis: 'y', responsive: true, maintainAspectRatio: false,
            scales: {
                x: { beginAtZero: true, ticks: { precision: 0, color: lightTextColor }, grid: { color: lightGridColor, borderDash: [2, 3] } },
                y: { ticks: { color: lightTextColor }, grid: { display: false } }
            },
            plugins: {
                legend: { display: false }, title: { display: false },
                tooltip: {
                     backgroundColor: lightTooltipBg, titleColor: lightTooltipTitle, bodyColor: lightTooltipBody,
                     borderColor: lightTooltipBorder, borderWidth: 1
                 }
            }
        }
    };
    if (topPodsChartInstance) { topPodsChartInstance.destroy(); }
    topPodsChartInstance = new Chart(ctx, barChartConfig);
}

export function destroyAllCharts() {
    if (lineChartInstance) { lineChartInstance.destroy(); lineChartInstance = null; }
    if (topPodsChartInstance) { topPodsChartInstance.destroy(); topPodsChartInstance = null; }
    if (namespacePieInstance) { namespacePieInstance.destroy(); namespacePieInstance = null; }
    if (severityPieInstance) { severityPieInstance.destroy(); severityPieInstance = null; }
}
