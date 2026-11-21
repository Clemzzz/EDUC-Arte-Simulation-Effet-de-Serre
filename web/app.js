(() => {
  const POLL_INTERVAL_MS = 1000;

  const state = {
    lampOn: true,
    wetPaper: true,
    speed: 1,
    timerId: null,
  };

  const els = {
    modeBadge: document.getElementById("mode-badge"),
    modeText: document.getElementById("mode-text"),
    tempValue: document.getElementById("temp-value"),
    humValue: document.getElementById("hum-value"),
    elapsedValue: document.getElementById("elapsed-value"),
    simPanel: document.getElementById("sim-panel"),
    btnLamp: document.getElementById("btn-lamp"),
    lampState: document.getElementById("lamp-state"),
    btnPaper: document.getElementById("btn-paper"),
    paperState: document.getElementById("paper-state"),
    btnReset: document.getElementById("btn-reset"),
    speedButtons: Array.from(document.querySelectorAll(".btn-speed")),
  };

  function formatDuration(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    const hrs = Math.floor(total / 3600);
    const mins = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return hrs > 0 ? `${pad(hrs)}:${pad(mins)}:${pad(secs)}` : `${pad(mins)}:${pad(secs)}`;
  }

  function setStatus(kind, text) {
    els.modeBadge.className = `status-pill badge-${kind}`;
    els.modeText.textContent = text;
  }

  function render(data) {
    els.tempValue.textContent = data.temperature_c.toFixed(1);
    els.humValue.textContent = data.humidity_pct.toFixed(1);
    els.elapsedValue.textContent = formatDuration(data.elapsed_s);

    const isSim = data.mode === "simulation";
    setStatus(isSim ? "sim" : "live", isSim ? "Simulation active" : "ESP32 connecté");
    els.simPanel.classList.toggle("hidden", !isSim);

    if (!isSim) return;

    state.lampOn = Boolean(data.lamp_on);
    state.wetPaper = Boolean(data.wet_paper);
    state.speed = Number(data.speed) || 1;

    els.btnLamp.classList.toggle("active", state.lampOn);
    els.btnLamp.setAttribute("aria-pressed", String(state.lampOn));
    els.lampState.textContent = state.lampOn ? "Allumée" : "Éteinte";

    els.btnPaper.classList.toggle("active", state.wetPaper);
    els.btnPaper.setAttribute("aria-pressed", String(state.wetPaper));
    els.paperState.textContent = state.wetPaper ? "Présent" : "Absent";

    for (const btn of els.speedButtons) {
      const isSelected = Number(btn.dataset.speed) === state.speed;
      btn.classList.toggle("active", isSelected);
      btn.setAttribute("aria-pressed", String(isSelected));
    }
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return response.json();
  }

  async function poll() {
    try {
      const data = await fetchJson("/api/data");
      render(data);
    } catch (_err) {
      setStatus("error", "Connexion perdue");
    }
  }

  async function updateConfig(patch) {
    try {
      const updated = await fetchJson("/api/sim/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      render(updated);
    } catch (_err) {
      setStatus("error", "Erreur de commande");
    }
  }

  async function resetSim() {
    try {
      const updated = await fetchJson("/api/sim/reset", { method: "POST" });
      render(updated);
    } catch (_err) {
      setStatus("error", "Erreur de réinitialisation");
    }
  }

  function startPolling() {
    if (state.timerId !== null) return;
    poll();
    state.timerId = window.setInterval(poll, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (state.timerId === null) return;
    window.clearInterval(state.timerId);
    state.timerId = null;
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      stopPolling();
    } else {
      startPolling();
    }
  });

  els.btnLamp.addEventListener("click", () => updateConfig({ lamp_on: !state.lampOn }));
  els.btnPaper.addEventListener("click", () => updateConfig({ wet_paper: !state.wetPaper }));
  els.btnReset.addEventListener("click", resetSim);

  for (const btn of els.speedButtons) {
    btn.addEventListener("click", () => updateConfig({ speed: Number(btn.dataset.speed) }));
  }

  startPolling();
})();
