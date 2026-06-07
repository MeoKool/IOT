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

// The backend keeps the earlier generic chart keys for compatibility, but the
// UI presents them as water-tank signals.
const chartThemes = {
    temperature: { label: "Water Level (%)", border: "#0ea5e9", fill: "rgba(14, 165, 233, 0.10)" },
    humidity: { label: "Backup Float (%)", border: "#14b8a6", fill: "rgba(20, 184, 166, 0.10)" },
    light: { label: "Analog A0", border: "#f59e0b", fill: "rgba(245, 158, 11, 0.10)" },
    distance: { label: "Ultrasonic Distance (cm)", border: "#8b5cf6", fill: "rgba(139, 92, 246, 0.10)" },
};

function $(id) {
    return document.getElementById(id);
}

function renderLucideIcons() {
    if (window.lucide?.createIcons) {
        window.lucide.createIcons({
            attrs: {
                "aria-hidden": "true",
                focusable: "false",
            },
        });
    }
}

function readSensorValue(data, ...keys) {
    for (const key of keys) {
        const value = data[key];
        if (value !== null && value !== undefined && value !== "") {
            return Number(value);
        }
    }
    return null;
}

function readWaterLevel(data) {
    return readSensorValue(data, "water_level", "temperature");
}

function readFloatLevel(data) {
    return readSensorValue(data, "float_level", "humidity");
}

function readAnalogValue(data) {
    return readSensorValue(data, "analog_value", "light");
}

function readDistance(data) {
    return readSensorValue(data, "distance_cm", "distance");
}

function readValveStatus(data) {
    return data.valve_status ?? data.door_status ?? "CLOSED";
}

function readPumpStatus(data) {
    return data.pump_status ?? data.led_status ?? "OFF";
}

function readAlarmStatus(data) {
    return data.alarm_status ?? data.buzzer_status ?? "OFF";
}

function statusTextForSensor(type, value) {
    switch (type) {
        case "waterLevel":
            if (value >= 95) return "Overflow risk — alarm threshold";
            if (value >= 80) return "Tank nearly full";
            if (value <= 15) return "Low level — pump should refill";
            return "Safe operating level";
        case "floatLevel":
            if (value >= 95) return "Backup float confirms high level";
            if (value <= 15) return "Backup float confirms low level";
            return "Backup confirmation normal";
        case "analog":
            if (value >= 900) return "Analog value near full scale";
            if (value <= 160) return "Analog value near empty";
            return "Analog A0 in normal range";
        case "distance":
            if (value <= 8) return "Water surface close to sensor";
            if (value >= 42) return "Water surface far from sensor";
            return "Ultrasonic distance stable";
        default:
            return "Live water-tank value";
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

const TOAST_AUTO_CLOSE_MS = 3000;

function showToast(message, type = "success") {
    const container = $("toastContainer");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast ${type === "error" ? "error" : ""}`;
    toast.textContent = message;
    container.appendChild(toast);

    // Toast stays visible for 3 seconds, then fades out and removes itself.
    window.setTimeout(() => {
        toast.classList.add("is-hiding");
        window.setTimeout(() => toast.remove(), 260);
    }, TOAST_AUTO_CLOSE_MS);
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
    const waterLevel = readWaterLevel(data);
    const floatLevel = readFloatLevel(data);
    const analogValue = readAnalogValue(data);
    const distance = readDistance(data);
    const hasRealSensorData = data.has_data !== false;
    const valveStatus = readValveStatus(data);
    const pumpStatus = readPumpStatus(data);
    const alarmStatus = readAlarmStatus(data);

    const waitingText = "Waiting for real Arduino data";
    $("temperatureValue").textContent = waterLevel === null ? "--" : waterLevel.toFixed(1);
    $("humidityValue").textContent = floatLevel === null ? "--" : floatLevel;
    $("lightValue").textContent = analogValue === null ? "--" : analogValue;
    $("distanceValue").textContent = distance === null ? "--" : distance;

    $("temperatureStatus").textContent = hasRealSensorData && waterLevel !== null ? statusTextForSensor("waterLevel", waterLevel) : waitingText;
    $("humidityStatus").textContent = hasRealSensorData && floatLevel !== null ? statusTextForSensor("floatLevel", floatLevel) : waitingText;
    $("lightStatus").textContent = hasRealSensorData && analogValue !== null ? statusTextForSensor("analog", analogValue) : waitingText;
    $("distanceStatus").textContent = hasRealSensorData && distance !== null ? statusTextForSensor("distance", distance) : waitingText;

    $("doorValue").textContent = valveStatus;
    $("ledValue").textContent = pumpStatus;
    $("buzzerValue").textContent = alarmStatus;
    $("autoValue").textContent = boolModeLabel(data.auto_mode);

    $("doorStatus").textContent = valveStatus === "OPEN" ? "Valve is open" : "Valve is closed";
    $("ledStatus").textContent = pumpStatus === "ON" ? "Pump output enabled" : "Pump output disabled";
    $("buzzerStatus").textContent = alarmStatus === "ON" ? "Overflow alarm active" : "Alarm output disabled";
    $("autoStatus").textContent = data.auto_mode ? "Automatic tank logic enabled" : "Manual tank control enabled";

    setToggleState("ledToggle", pumpStatus === "ON", pumpStatus === "ON" ? "ON — pump running" : "OFF — pump stopped");
    setToggleState("buzzerToggle", alarmStatus === "ON", alarmStatus === "ON" ? "ON — alarm active" : "OFF — alarm standby");
    setToggleState("doorToggle", valveStatus === "OPEN", valveStatus === "OPEN" ? "OPEN — valve/servo open" : "CLOSED — valve/servo closed");
    setToggleState("autoToggle", Boolean(data.auto_mode), data.auto_mode ? "ON — automatic tank logic" : "OFF — manual tank control");

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
                    beginAtZero: true,
                    suggestedMax: key === "light" ? 1023 : key === "distance" ? 50 : 100,
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
                <td>${Number(record.water_level ?? record.temperature).toFixed(1)}%</td>
                <td>${record.float_level ?? record.humidity}%</td>
                <td>${record.analog_value ?? record.light}</td>
                <td>${record.distance_cm ?? record.distance} cm</td>
                <td>${onOffBadge(record.valve_status ?? record.door_status)}</td>
                <td>${onOffBadge(record.pump_status ?? record.led_status)}</td>
                <td>${onOffBadge(record.alarm_status ?? record.buzzer_status)}</td>
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
        throw error;
    }
}

async function sendAutoMode(auto) {
    try {
        const result = await fetchJSON("/api/auto", {
            method: "POST",
            body: JSON.stringify({ auto }),
        });

        showToast(result.message || (auto ? "Auto tank mode enabled" : "Manual tank mode enabled"));
        if (result.data) updateCurrentUI(result.data);
        await Promise.all([loadHistory(), loadChartData()]);
    } catch (error) {
        showToast(error.message, "error");
        throw error;
    }
}

function bindControls() {
    const toggleCommands = {
        led: (checked) => checked ? "PUMP_ON" : "PUMP_OFF",
        buzzer: (checked) => checked ? "ALARM_ON" : "ALARM_OFF",
        door: (checked) => checked ? "VALVE_OPEN" : "VALVE_CLOSE",
    };

    document.querySelectorAll("[data-toggle-control]").forEach((toggle) => {
        const card = toggle.closest(".control-toggle");

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

    // Poll the Flask API for real rows inserted by the Raspberry Pi
    // Serial/Bluetooth/MQTT ingestion pipeline.
    window.setInterval(refreshDashboard, REFRESH_INTERVAL_MS);
});
