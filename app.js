const state = {
  projects: [],
  activeProject: null,
  activeJob: null,
  pollTimer: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = body.detail || body.error || message;
    } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}

function badge(element, label, type) {
  element.textContent = label;
  element.className = `badge ${type}`;
}

async function refreshEngine() {
  try {
    const engine = await api("/api/engine");
    $("engineTitle").textContent = engine.ready ? "Music engine ready" : "Preparing music engine…";
    $("engineMessage").textContent = engine.message;
    badge(
      $("engineBadge"),
      engine.ready ? "ENGINE READY" : engine.model === "error" ? "ENGINE ERROR" : "ENGINE LOADING",
      engine.ready ? "success" : engine.model === "error" ? "danger" : "warning"
    );
    $("generateBtn").disabled = !engine.ready || !state.activeProject;
  } catch (error) {
    badge($("engineBadge"), "APP ERROR", "danger");
    $("engineMessage").textContent = error.message;
  }
}

async function loadProjects(selectLast = false) {
  state.projects = await api("/api/projects");
  const select = $("projectSelect");
  const previous = state.activeProject;
  select.innerHTML = "";

  if (!state.projects.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No projects yet";
    select.appendChild(option);
    state.activeProject = null;
    $("projectHint").textContent = "Create your first project to begin.";
  } else {
    state.projects.forEach((project) => {
      const option = document.createElement("option");
      option.value = project.id;
      option.textContent = `${project.name} · ${project.completed_count || 0} completed`;
      select.appendChild(option);
    });
    const candidate = selectLast ? state.projects[0].id : previous || state.projects[0].id;
    select.value = state.projects.some((item) => item.id === candidate)
      ? candidate
      : state.projects[0].id;
    state.activeProject = select.value;
    $("projectHint").textContent = "All tracks, prompts and exports are saved inside this project.";
  }
  await refreshEngine();
}

async function createProject() {
  const name = $("newProjectName").value.trim();
  const notes = $("newProjectNotes").value.trim();
  if (!name) return;
  await api("/api/projects", {
    method: "POST",
    body: JSON.stringify({ name, notes }),
  });
  await loadProjects(true);
  $("projectDialog").close();
}

function renderResults(job) {
  const results = $("results");
  results.innerHTML = "";
  const outputs = job.result?.outputs || [];
  outputs.forEach((output) => {
    const card = document.createElement("div");
    card.className = "result-card";
    const meta = output.analysis || {};
    card.innerHTML = `
      <div>
        <h3>Variation ${output.variation}</h3>
        <p class="muted">${meta.duration || "—"} sec · ${meta.size_mb || "—"} MB · 24-bit WAV master</p>
        <audio controls preload="metadata" src="${output.preview_url}"></audio>
      </div>
      <div class="downloads">
        <a class="button primary" href="${output.master_url}" download>Final Master WAV</a>
        <a class="button secondary" href="${output.original_url}" download>Original WAV</a>
        <a class="button secondary" href="${output.preview_url}" download>Preview MP3</a>
      </div>
    `;
    results.appendChild(card);
  });
}

async function pollJob(jobId) {
  clearTimeout(state.pollTimer);
  try {
    const job = await api(`/api/jobs/${jobId}`);
    state.activeJob = job;
    $("jobCard").classList.remove("hidden");
    $("jobTitle").textContent = job.title;
    $("progressBar").style.width = `${job.progress || 0}%`;
    $("jobMessage").textContent = job.message || "";
    badge(
      $("jobStatus"),
      job.status.replaceAll("_", " ").toUpperCase(),
      job.status === "completed" ? "success" : job.status === "failed" ? "danger" : "warning"
    );

    if (job.status === "failed") {
      $("jobError").textContent = job.error || "Unknown generation error.";
      $("jobError").classList.remove("hidden");
      $("generateBtn").disabled = false;
      await loadLibrary();
      return;
    }

    $("jobError").classList.add("hidden");
    if (job.status === "completed") {
      renderResults(job);
      $("generateBtn").disabled = false;
      await loadProjects();
      await loadLibrary();
      return;
    }
    state.pollTimer = setTimeout(() => pollJob(jobId), 2500);
  } catch (error) {
    $("jobMessage").textContent = error.message;
    state.pollTimer = setTimeout(() => pollJob(jobId), 5000);
  }
}

async function createJob() {
  if (!state.activeProject) {
    $("projectDialog").showModal();
    return;
  }
  const prompt = $("prompt").value.trim();
  if (prompt.length < 10) {
    alert("Write a clear music direction first.");
    return;
  }
  $("generateBtn").disabled = true;
  $("results").innerHTML = "";
  $("jobCard").classList.remove("hidden");
  $("jobError").classList.add("hidden");
  $("progressBar").style.width = "2%";
  $("jobMessage").textContent = "Creating production job…";

  try {
    const job = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.activeProject,
        title: $("title").value.trim() || "Untitled Track",
        prompt,
        instrumental: true,
        duration: Number($("duration").value),
        bpm: Number($("bpm").value),
        key_scale: $("keyScale").value,
        time_signature: "4",
        variations: Number($("variations").value),
        auto_master: $("autoMaster").checked,
      }),
    });
    pollJob(job.id);
  } catch (error) {
    $("generateBtn").disabled = false;
    $("jobError").textContent = error.message;
    $("jobError").classList.remove("hidden");
  }
}

async function loadLibrary() {
  const jobs = await api("/api/jobs");
  const container = $("libraryContent");
  container.innerHTML = "";
  if (!jobs.length) {
    container.innerHTML = '<p class="muted">No production jobs yet.</p>';
    return;
  }
  jobs.forEach((job) => {
    const item = document.createElement("div");
    item.className = "list-item";
    item.innerHTML = `
      <div class="section-title">
        <div>
          <strong>${job.title}</strong>
          <p class="muted">${job.created_at} · ${job.settings.duration}s · ${job.settings.bpm || "Auto"} BPM</p>
        </div>
        <span class="badge ${job.status === "completed" ? "success" : job.status === "failed" ? "danger" : "warning"}">${job.status.toUpperCase()}</span>
      </div>
    `;
    if (job.status === "completed") {
      item.style.cursor = "pointer";
      item.addEventListener("click", () => {
        document.querySelector('[data-target="producer"]').click();
        pollJob(job.id);
      });
    }
    container.appendChild(item);
  });
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((panel) => panel.classList.remove("active-panel"));
    button.classList.add("active");
    $(button.dataset.target).classList.add("active-panel");
    if (button.dataset.target === "library") loadLibrary();
  });
});

$("projectSelect").addEventListener("change", (event) => {
  state.activeProject = event.target.value || null;
  refreshEngine();
});
$("newProjectBtn").addEventListener("click", () => $("projectDialog").showModal());
$("createProjectBtn").addEventListener("click", (event) => {
  event.preventDefault();
  createProject();
});
$("generateBtn").addEventListener("click", createJob);
$("retryEngineBtn").addEventListener("click", async () => {
  await api("/api/engine/retry", { method: "POST", body: "{}" });
  refreshEngine();
});
$("refreshLibraryBtn").addEventListener("click", loadLibrary);
$("runDiagnosticsBtn").addEventListener("click", async () => {
  $("diagnosticsOutput").textContent = "Running…";
  try {
    $("diagnosticsOutput").textContent = JSON.stringify(await api("/api/diagnostics"), null, 2);
  } catch (error) {
    $("diagnosticsOutput").textContent = error.message;
  }
});
$("loadLogsBtn").addEventListener("click", async () => {
  $("logsOutput").textContent = "Loading…";
  try {
    const logs = await api("/api/logs");
    $("logsOutput").textContent = `ACE-STEP\n${logs.ace_step}\n\nNEO NILE\n${logs.neo_nile}`;
  } catch (error) {
    $("logsOutput").textContent = error.message;
  }
});

(async function boot() {
  await loadProjects();
  await loadLibrary();
  await refreshEngine();
  setInterval(refreshEngine, 5000);
})();
