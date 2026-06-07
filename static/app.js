const dashboardConfig = window.DASHBOARD_CONFIG || {};
const REFRESH_INTERVAL_MS = Number(dashboardConfig.refreshIntervalMs) || 3000;
const MAX_POINTS = Number(dashboardConfig.maxChartPoints) || 20;

const charts = {};
const historyState = {
    page: 1,
    perPage: Number(dashboardConfig.historyDefaultPerPage) || 20,
    totalPages: 1,
    totalRecords: 0,
};

const chartThemes = {
    temperature: { label: "Temperature (°C)", border: "#dd5b00", fill: "rgba(221, 91, 0, 0.08)" },
    humidity: { label: "Humidity (%)", border: "#62aef0", fill: "rgba(98, 174, 240, 0.10)" },
    light: { label: "Light Level", border: "#0075de", fill: "rgba(0, 117, 222, 0.08)" },
    distance: { label: "Distance (cm)", border: "#2a9d99", fill: "rgba(42, 157, 153, 0.09)" },
};

function $(id) {
    return document.getElementById(id);
}

function renderLucideIcons() {
    // Lucide is loaded from CDN in index.html. If the network is unavailable,
    // the dashboard still works and simply falls back to plain text/layout.
    if (window.lucide?.createIcons) {
        window.lucide.createIcons({
            attrs: {
                "aria-hidden": "true",
                focusable: "false",
            },
        });
    }
}

function statusTextForSensor(type, value) {
    switch (type) {
        case "temperature":
            if (value >= 30) return "Warm room temperature";
            if (value <= 25) return "Cool and comfortable";
            return "Comfort range";
        case "humidity":
            if (value >= 72) return "High humidity";
            if (value <= 58) return "Dry air level";
            return "Normal humidity";
        case "light":
            if (value >= 600) return "Bright environment";
            if (value <= 260) return "Low light detected";
            return "Balanced light level";
        case "distance":
            if (value <= 15) return "Object is very close";
            if (value >= 45) return "Object is far";
            return "Safe distance";
        default:
            return "Live mock value";
    }
}

function onOffBadge(value) {
    const normalized = String(value).toUpperCase();
    const cls = normalized === "ON" || normalized === "OPEN" || normalized === "AUTO" || normalized === "TRUE"
        ? "badge-on"
        : "badge-off";
    return `<span class="badge ${cls}">${normalized}</span>`;
}

function boolModeLabel(autoMode) {
    return autoMode ? "ON" : "OFF";
}

function showToast(message, type = "success") {
    const container = $("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast ${type === "error" ? "error" : ""}`;
    toast.textContent = message;
    container.appendChild(toast);

    window.setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateY(10px) scale(0.98)";
        toast.addEventListener("transitionend", () => toast.remove(), { once: true });
    }, 2800);
}

async function fetchJSON(url, options = {}) {
    const response = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        ...options,
    });

    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.message || `Request failed: ${response.status}`);
    }
    return data;
}

function setToggleState(id, checked, text) {
    const toggle = $(id);
    if (toggle) toggle.checked = checked;
    const label = $(`${id}Text`);
    if (label) label.textContent = text;
}

function updateCurrentUI(data) {
    $("temperatureValue").textContent = data.temperature.toFixed ? data.temperature.toFixed(1) : data.temperature;
    $("humidityValue").textContent = data.humidity;
    $("lightValue").textContent = data.light;
    $("distanceValue").textContent = data.distance;

    $("temperatureStatus").textContent = statusTextForSensor("temperature", Number(data.temperature));
    $("humidityStatus").textContent = statusTextForSensor("humidity", Number(data.humidity));
    $("lightStatus").textContent = statusTextForSensor("light", Number(data.light));
    $("distanceStatus").textContent = statusTextForSensor("distance", Number(data.distance));

    $("doorValue").textContent = data.door_status;
    $("ledValue").textContent = data.led_status;
    $("buzzerValue").textContent = data.buzzer_status;
    $("autoValue").textContent = boolModeLabel(data.auto_mode);

    $("doorStatus").textContent = data.door_status === "OPEN" ? "Door is currently open" : "Door is currently closed";
    $("ledStatus").textContent = data.led_status === "ON" ? "Lighting output enabled" : "Lighting output disabled";
    $("buzzerStatus").textContent = data.buzzer_status === "ON" ? "Alert sound enabled" : "Alert sound disabled";
    $("autoStatus").textContent = data.auto_mode ? "Automatic decisions enabled" : "Manual control enabled";

    setToggleState("ledToggle", data.led_status === "ON", data.led_status === "ON" ? "ON — lighting output enabled" : "OFF — lighting output disabled");
    setToggleState("buzzerToggle", data.buzzer_status === "ON", data.buzzer_status === "ON" ? "ON — alert sound enabled" : "OFF — alert sound disabled");
    setToggleState("doorToggle", data.door_status === "OPEN", data.door_status === "OPEN" ? "OPEN — servo door open" : "CLOSED — servo door closed");
    setToggleState("autoToggle", Boolean(data.auto_mode), data.auto_mode ? "ON — automatic decisions" : "OFF — manual control");

    $("connectionBadge").textContent = data.connection;
    $("lastUpdated").textContent = data.last_updated;
    $("systemModeBadge").outerHTML = `<span id="systemModeBadge" class="badge ${data.auto_mode ? "badge-on" : "badge-off"}">${data.auto_mode ? "Auto" : "Manual"}</span>`;
}

async function loadCurrent() {
    try {
        const data = await fetchJSON("/api/current");
        updateCurrentUI(data);
    } catch (error) {
        showToast(error.message, "error");
    }
}

async function loadMqttStatus() {
    try {
        const status = await fetchJSON("/api/mqtt/status");
        const broker = $("mqttBroker");
        const dataTopic = $("mqttDataTopic");
        const controlTopic = $("mqttControlTopic");
        const statusBadge = $("mqttStatusBadge");

        if (broker) broker.textContent = `${status.host}:${status.port}`;
        if (dataTopic) dataTopic.textContent = status.data_topic;
        if (controlTopic) controlTopic.textContent = status.control_topic;
        if (statusBadge) {
            const label = !status.enabled
                ? "Disabled"
                : status.connected
                    ? "Connected"
                    : "Offline";
            statusBadge.className = `badge ${status.connected ? "badge-on" : "badge-off"}`;
            statusBadge.textContent = label;
            statusBadge.title = status.last_error || "MQTT broker status";
        }
    } catch (error) {
        // Keep the dashboard usable even if the status endpoint is unreachable.
        console.warn("MQTT status unavailable:", error.message);
    }
}

function createChart(canvasId, key) {
    const theme = chartThemes[key];
    const ctx = $(canvasId);

    return new Chart(ctx, {
        type: "line",
        data: {
            labels: [],
            datasets: [
                {
                    label: theme.label,
                    data: [],
                    borderColor: theme.border,
                    backgroundColor: theme.fill,
                    borderWidth: 2.5,
                    fill: true,
                    pointRadius: 2.5,
                    pointHoverRadius: 5,
                    tension: 0.38,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 450 },
            plugins: {
                legend: {
                    labels: {
                        color: "#615d59",
                        boxWidth: 12,
                        boxHeight: 12,
                        usePointStyle: true,
                    },
                },
                tooltip: {
                    backgroundColor: "rgba(255, 255, 255, 0.98)",
                    borderColor: "#e6e6e6",
                    borderWidth: 1,
                    padding: 12,
                    titleColor: "#000000",
                    bodyColor: "#31302e",
                },
            },
            scales: {
                x: {
                    ticks: { color: "#615d59", maxTicksLimit: 6 },
                    grid: { color: "rgba(0, 0, 0, 0.045)" },
                },
                y: {
                    beginAtZero: key === "light" || key === "distance",
                    ticks: { color: "#615d59" },
                    grid: { color: "rgba(0, 0, 0, 0.045)" },
                },
            },
        },
    });
}

function initializeCharts() {
    charts.temperature = createChart("temperatureChart", "temperature");
    charts.humidity = createChart("humidityChart", "humidity");
    charts.light = createChart("lightChart", "light");
    charts.distance = createChart("distanceChart", "distance");
}

function trimToLatest(values) {
    return values.slice(-MAX_POINTS);
}

async function loadChartData() {
    try {
        const data = await fetchJSON("/api/chart-data");
        ["temperature", "humidity", "light", "distance"].forEach((key) => {
            charts[key].data.labels = trimToLatest(data.labels);
            charts[key].data.datasets[0].data = trimToLatest(data[key]);
            charts[key].update();
        });
    } catch (error) {
        showToast(error.message, "error");
    }
}

function updateHistoryPaginationUI(meta) {
    historyState.page = meta.page || 1;
    historyState.perPage = meta.per_page || historyState.perPage;
    historyState.totalPages = meta.total_pages || 1;
    historyState.totalRecords = meta.total_records || 0;

    const start = historyState.totalRecords === 0 ? 0 : ((historyState.page - 1) * historyState.perPage) + 1;
    const end = Math.min(historyState.page * historyState.perPage, historyState.totalRecords);

    if ($("historySummary")) {
        $("historySummary").textContent = historyState.totalRecords
            ? `${start}-${end} of ${historyState.totalRecords} records`
            : "0 records";
    }
    if ($("historyPageInfo")) {
        $("historyPageInfo").textContent = `Page ${historyState.page} / ${historyState.totalPages}`;
    }
    if ($("historyPrev")) {
        $("historyPrev").disabled = !meta.has_prev;
    }
    if ($("historyNext")) {
        $("historyNext").disabled = !meta.has_next;
    }
    if ($("historyPageSize")) {
        $("historyPageSize").value = String(historyState.perPage);
    }
}

function renderHistory(records, meta = {}) {
    const body = $("historyBody");
    updateHistoryPaginationUI({
        page: meta.page || historyState.page,
        per_page: meta.per_page || historyState.perPage,
        total_records: meta.total_records ?? records.length,
        total_pages: meta.total_pages || 1,
        has_prev: Boolean(meta.has_prev),
        has_next: Boolean(meta.has_next),
    });

    if (!records.length) {
        body.innerHTML = `<tr><td colspan="9" class="empty-state">No records available</td></tr>`;
        return;
    }

    body.innerHTML = records.map((record) => {
        const autoText = record.auto_mode ? "ON" : "OFF";
        return `
            <tr>
                <td>${record.time}</td>
                <td>${record.temperature} °C</td>
                <td>${record.humidity}%</td>
                <td>${record.light}</td>
                <td>${record.distance} cm</td>
                <td>${onOffBadge(record.door_status)}</td>
                <td>${onOffBadge(record.led_status)}</td>
                <td>${onOffBadge(record.buzzer_status)}</td>
                <td>${onOffBadge(autoText)}</td>
            </tr>
        `;
    }).join("");
}

async function loadHistory() {
    try {
        const params = new URLSearchParams({
            page: String(historyState.page),
            per_page: String(historyState.perPage),
        });
        const data = await fetchJSON(`/api/history?${params.toString()}`);

        // Backward compatible fallback if an older backend returns a raw array.
        if (Array.isArray(data)) {
            renderHistory(data, {
                page: 1,
                per_page: data.length || historyState.perPage,
                total_records: data.length,
                total_pages: 1,
                has_prev: false,
                has_next: false,
            });
            return;
        }

        if (data.records.length === 0 && data.total_records > 0 && historyState.page > data.total_pages) {
            historyState.page = data.total_pages;
            await loadHistory();
            return;
        }

        renderHistory(data.records, data);
    } catch (error) {
        showToast(error.message, "error");
    }
}

async function sendControlCommand(command) {
    try {
        const result = await fetchJSON("/api/control", {
            method: "POST",
            body: JSON.stringify({ command }),
        });

        showToast(result.message || `Command sent: ${command}`);
        if (result.data) updateCurrentUI(result.data);
        await Promise.all([loadHistory(), loadChartData()]);
    } catch (error) {
        showToast(error.message, "error");
    }
}

async function sendAutoMode(auto) {
    try {
        const result = await fetchJSON("/api/auto", {
            method: "POST",
            body: JSON.stringify({ auto }),
        });

        showToast(result.message || (auto ? "Auto mode enabled" : "Auto mode disabled"));
        if (result.data) updateCurrentUI(result.data);
        await Promise.all([loadHistory(), loadChartData()]);
    } catch (error) {
        showToast(error.message, "error");
    }
}

function bindControls() {
    const toggleCommands = {
        led: (checked) => checked ? "LED_ON" : "LED_OFF",
        buzzer: (checked) => checked ? "BUZZER_ON" : "BUZZER_OFF",
        door: (checked) => checked ? "DOOR_OPEN" : "DOOR_CLOSE",
    };

    document.querySelectorAll("[data-toggle-control]").forEach((toggle) => {
        const card = toggle.closest(".control-toggle");

        // Make the whole card behave like a switch, not just the hidden checkbox.
        // preventDefault avoids the browser label's native double-toggle behavior.
        card?.addEventListener("click", (event) => {
            if (event.target === toggle || toggle.disabled) return;
            event.preventDefault();
            toggle.checked = !toggle.checked;
            toggle.dispatchEvent(new Event("change", { bubbles: true }));
        });

        toggle.addEventListener("change", async () => {
            const control = toggle.dataset.toggleControl;
            toggle.disabled = true;

            try {
                if (control === "auto") {
                    await sendAutoMode(toggle.checked);
                } else {
                    await sendControlCommand(toggleCommands[control](toggle.checked));
                }
            } catch (error) {
                // sendControlCommand/sendAutoMode already show the toast. Refreshing here
                // restores the switch to the persisted backend state if a request fails.
                await loadCurrent();
            } finally {
                toggle.disabled = false;
            }
        });
    });
}

function bindHistoryPagination() {
    $("historyPrev")?.addEventListener("click", async () => {
        if (historyState.page <= 1) return;
        historyState.page -= 1;
        await loadHistory();
    });

    $("historyNext")?.addEventListener("click", async () => {
        if (historyState.page >= historyState.totalPages) return;
        historyState.page += 1;
        await loadHistory();
    });

    $("historyPageSize")?.addEventListener("change", async (event) => {
        historyState.perPage = Number(event.target.value) || 20;
        historyState.page = 1;
        await loadHistory();
    });
}

async function refreshDashboard() {
    await Promise.all([loadCurrent(), loadChartData(), loadHistory(), loadMqttStatus()]);
}

document.addEventListener("DOMContentLoaded", async () => {
    renderLucideIcons();
    initializeCharts();
    bindControls();
    bindHistoryPagination();
    await refreshDashboard();

    // UI-only polling for the mock dashboard. Later, this can be replaced by
    // Server-Sent Events/WebSocket, or backed by real SQLite rows written by the
    // Raspberry Pi Bluetooth/Serial ingestion process.
    window.setInterval(refreshDashboard, REFRESH_INTERVAL_MS);
});
