/* Qk-Trans 前端逻辑:自动翻译、引擎/方向切换、状态轮询、历史记录。 */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  engine: "lite",
  direction: "auto",
  debounceTimer: null,
  translating: false,
  lastRequestId: 0,
};

const HISTORY_KEY = "qktrans_history";
const HISTORY_MAX = 50;
const AUTO_DELAY_MS = 600;

/* ---------- 初始化 ---------- */

window.addEventListener("DOMContentLoaded", () => {
  bindEngineSeg();
  bindDirSeg();
  bindInput();
  bindButtons();
  initTabs();
  initFileTab();
  refreshStatus();
  setInterval(refreshStatus, 15000);
});

/* ---------- 标签页切换 ---------- */

function initTabs() {
  $("tabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-tab]");
    if (!btn) return;
    document.querySelectorAll("#tabs button").forEach((b) =>
      b.classList.toggle("active", b === btn)
    );
    const showFile = btn.dataset.tab === "file";
    $("textTab").classList.toggle("hidden", showFile);
    $("fileTab").classList.toggle("hidden", !showFile);
  });
}

/* ---------- 状态轮询 ---------- */

async function refreshStatus() {
  const dot = $("statusDot");
  const text = $("statusText");
  const llmBtn = document.querySelector('#engineSeg button[data-engine="llm"]');
  let ok = false;
  try {
    const resp = await fetch("/api/status");
    if (!resp.ok) throw new Error();
    const data = await resp.json();
    ok = true;

    const lite = data.engines.lite || {};
    const llm = data.engines.llm || {};

    // LLM 引擎可用性 → 按钮置灰与提示
    if (llm.available) {
      llmBtn.disabled = false;
      llmBtn.title = `模型:${llm.model}`;
    } else {
      llmBtn.disabled = true;
      llmBtn.title = llm.hint || "Ollama 不可用";
      if (state.engine === "llm") selectEngine("lite");
    }

    const parts = [];
    parts.push(lite.available ? `轻量引擎就绪${lite.device ? "(" + lite.device + ")" : ""}` : "轻量引擎未安装");
    parts.push(llm.available ? "大模型就绪" : "大模型未启用");
    text.textContent = parts.join(" · ");

    dot.className = "dot " + (lite.available ? "ok" : llm.available ? "warn" : "err");
  } catch {
    text.textContent = "无法连接本地服务,请确认 start.bat 仍在运行";
    dot.className = "dot err";
    llmBtn.disabled = false;
  }
}

/* ---------- 引擎 / 方向切换 ---------- */

function bindEngineSeg() {
  $("engineSeg").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-engine]");
    if (!btn || btn.disabled) return;
    selectEngine(btn.dataset.engine);
  });
}

function selectEngine(engine) {
  state.engine = engine;
  document.querySelectorAll("#engineSeg button").forEach((b) =>
    b.classList.toggle("active", b.dataset.engine === engine)
  );
  scheduleTranslate(0);
}

function bindDirSeg() {
  $("dirSeg").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-dir]");
    if (!btn) return;
    state.direction = btn.dataset.dir;
    document.querySelectorAll("#dirSeg button").forEach((b) =>
      b.classList.toggle("active", b.dataset.dir === state.direction)
    );
    scheduleTranslate(0);
  });
}

/* ---------- 输入与翻译 ---------- */

function bindInput() {
  $("input").addEventListener("input", () => {
    const value = $("input").value;
    $("inputCount").textContent = `${value.length} 字符`;
    scheduleTranslate(AUTO_DELAY_MS);
  });
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      scheduleTranslate(0);
    }
  });
}

function scheduleTranslate(delayMs) {
  clearTimeout(state.debounceTimer);
  const text = $("input").value.trim();
  if (!text) {
    renderOutput("");
    $("meta").textContent = "";
    $("inputLabel").textContent = "输入";
    return;
  }
  state.debounceTimer = setTimeout(translate, delayMs);
}

async function translate() {
  const text = $("input").value.trim();
  if (!text) return;

  const requestId = ++state.lastRequestId;
  const output = $("output");
  output.classList.remove("error");
  output.classList.add("pending");
  $("meta").textContent = "翻译中…";

  const started = performance.now();
  try {
    const resp = await fetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        engine: state.engine,
        direction: state.direction,
      }),
    });
    const data = await resp.json();
    if (requestId !== state.lastRequestId) return; // 已有更新的请求,丢弃过期结果

    if (!resp.ok) {
      showError(data.detail || `请求失败(${resp.status})`);
      return;
    }

    renderOutput(data.translation);
    const dirLabel = data.direction === "zh2en" ? "中→英" : "英→中";
    const engineLabel = data.engine === "lite" ? "轻量" : "大模型";
    const total = Math.round(performance.now() - started);
    $("meta").textContent = `${dirLabel} · ${engineLabel} · ${data.elapsed_ms} ms`;
    if (state.direction === "auto") {
      $("inputLabel").textContent = `输入 · 检测为${data.direction === "zh2en" ? "中文" : "英文"}`;
    } else {
      $("inputLabel").textContent = "输入";
    }
    saveHistory(text, data.translation, dirLabel, engineLabel);
  } catch {
    if (requestId !== state.lastRequestId) return;
    showError("请求失败:无法连接本地服务");
  } finally {
    if (requestId === state.lastRequestId) output.classList.remove("pending");
  }
}

function renderOutput(text) {
  const output = $("output");
  output.classList.remove("error");
  if (!text) {
    output.innerHTML = '<span class="placeholder">译文将显示在这里</span>';
    return;
  }
  output.textContent = text;
}

function showError(message) {
  const output = $("output");
  output.classList.add("error");
  output.classList.remove("pending");
  output.textContent = `⚠ ${message}`;
  $("meta").textContent = "";
}

/* ---------- 按钮 ---------- */

function bindButtons() {
  $("clearBtn").addEventListener("click", () => {
    $("input").value = "";
    $("inputCount").textContent = "0 字符";
    renderOutput("");
    $("meta").textContent = "";
    $("inputLabel").textContent = "输入";
    $("input").focus();
  });

  $("copyBtn").addEventListener("click", async () => {
    const text = $("output").textContent;
    if (!text || $("output").querySelector(".placeholder")) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // 非安全上下文(如 file://)下的兜底
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    const btn = $("copyBtn");
    btn.textContent = "已复制";
    setTimeout(() => (btn.textContent = "复制"), 1200);
  });

  $("historyBtn").addEventListener("click", () => {
    $("historyPanel").classList.toggle("hidden");
    renderHistory();
  });
  $("historyCloseBtn").addEventListener("click", () =>
    $("historyPanel").classList.add("hidden")
  );
  $("historyClearBtn").addEventListener("click", () => {
    localStorage.removeItem(HISTORY_KEY);
    renderHistory();
  });
}

/* ---------- 文件翻译 ---------- */

const FILE_EXTS = [".txt", ".md", ".pdf", ".docx", ".srt", ".vtt"];
const fileState = { file: null, running: false, pollTimer: null };

const show = (el) => el.classList.remove("hidden");
const hide = (el) => el.classList.add("hidden");

function formatSize(bytes) {
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + " KB";
  return bytes + " B";
}

function initFileTab() {
  const dz = $("dropzone");
  const input = $("fileInput");

  dz.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files[0]) setFile(input.files[0]);
  });
  ["dragover", "dragenter"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.remove("drag");
    })
  );
  dz.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files[0];
    if (f) setFile(f);
  });

  $("fileRemove").addEventListener("click", () => setFile(null));
  $("fileBtn").addEventListener("click", startFileTranslate);
  $("previewToggle").addEventListener("click", () => {
    const hidden = $("filePreview").classList.toggle("hidden");
    $("previewToggle").textContent = hidden ? "展开预览" : "收起预览";
  });
}

function setFile(f) {
  fileState.file = f || null;
  $("fileInput").value = "";
  $("fileChip").classList.toggle("hidden", !f);
  hide($("fileProgress"));
  hide($("fileResult"));
  hide($("fileError"));
  updateFileBtn();
  if (!f) return;
  $("fileName").textContent = f.name;
  $("fileSize").textContent = formatSize(f.size);
  if (f.size > 20 * 1024 * 1024) {
    showFileError("文件超过 20MB 上限,请拆分后再试。");
    return;
  }
  const ext = "." + (f.name.split(".").pop() || "").toLowerCase();
  if (!FILE_EXTS.includes(ext)) {
    showFileError("暂不支持该格式,支持:txt / md / pdf / docx / srt / vtt");
  }
}

function updateFileBtn() {
  $("fileBtn").disabled = !fileState.file || fileState.running;
}

function showFileError(message) {
  hide($("fileResult"));
  show($("fileError"));
  $("fileError").textContent = `⚠ ${message}`;
}

async function startFileTranslate() {
  const f = fileState.file;
  if (!f || fileState.running) return;

  const fd = new FormData();
  fd.append("file", f);
  fd.append("engine", state.engine);
  fd.append("direction", state.direction);
  fd.append("bilingual", $("bilingualToggle").checked);

  fileState.running = true;
  updateFileBtn();
  hide($("fileResult"));
  hide($("fileError"));
  show($("fileProgress"));
  $("progressFill").style.width = "0%";
  $("progressText").textContent = "上传中…";

  try {
    const resp = await fetch("/api/file/translate", { method: "POST", body: fd });
    const data = await resp.json();
    if (!resp.ok) {
      showFileError(data.detail || `上传失败(${resp.status})`);
      return;
    }
    pollFileJob(data.job_id);
  } catch {
    showFileError("上传失败:无法连接本地服务");
  } finally {
    fileState.running = false;
    updateFileBtn();
  }
}

function pollFileJob(jobId) {
  clearInterval(fileState.pollTimer);
  fileState.pollTimer = setInterval(async () => {
    let job;
    try {
      const resp = await fetch(`/api/file/status/${jobId}`);
      if (!resp.ok) throw new Error();
      job = await resp.json();
    } catch {
      return; // 网络抖动时继续等下一轮
    }
    if (job.state === "running" || job.state === "queued") {
      const pct = Math.round((job.progress || 0) * 100);
      $("progressFill").style.width = pct + "%";
      $("progressText").textContent =
        job.state === "queued" ? "排队中…" : `翻译中 ${pct}%`;
      return;
    }
    clearInterval(fileState.pollTimer);
    hide($("fileProgress"));
    if (job.state === "error") {
      showFileError(job.error || "翻译失败");
      return;
    }
    // done
    const dirLabel = job.direction === "zh2en" ? "中→英" : "英→中";
    const engineLabel = state.engine === "lite" ? "轻量" : "大模型";
    $("fileResultInfo").textContent =
      `${dirLabel} · ${job.segments} 段 · ${engineLabel}引擎 · 输出:${job.out_name}`;
    $("downloadBtn").href = `/api/file/download/${jobId}`;
    show($("fileResult"));
    $("filePreview").classList.remove("hidden");
    $("previewToggle").textContent = "收起预览";
    loadPreview(jobId);
  }, 1000);
}

/* ---------- 译文预览 ---------- */

async function loadPreview(jobId) {
  const previewEl = $("filePreview");
  previewEl.innerHTML = "";
  let data;
  try {
    const resp = await fetch(`/api/file/preview/${jobId}`);
    if (!resp.ok) throw new Error();
    data = await resp.json();
  } catch {
    previewEl.textContent = "预览加载失败,请直接下载译文文件。";
    return;
  }
  for (const [src, dst] of data.pairs) {
    const p1 = document.createElement("div");
    p1.className = "p-src";
    p1.textContent = src;
    const p2 = document.createElement("div");
    p2.className = "p-dst";
    p2.textContent = dst;
    previewEl.append(p1, p2);
  }
  if (!data.pairs.length) {
    previewEl.textContent = "(没有可预览的内容)";
  }
  if (data.truncated) {
    const note = document.createElement("div");
    note.className = "p-note";
    note.textContent =
      `内容较长,预览仅显示前 ${data.pairs.length} 段,完整内容请下载译文文件。`;
    previewEl.appendChild(note);
  }
}

/* ---------- 历史记录(localStorage,最多 50 条) ---------- */

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY)) || [];
  } catch {
    return [];
  }
}

function saveHistory(src, dst, dirLabel, engineLabel) {
  const list = loadHistory();
  if (list.length && list[0].src === src && list[0].dst === dst) return;
  list.unshift({
    ts: Date.now(),
    src,
    dst,
    tag: `${dirLabel} · ${engineLabel}`,
  });
  localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, HISTORY_MAX)));
}

function renderHistory() {
  const wrap = $("historyList");
  const list = loadHistory();
  if (!list.length) {
    wrap.innerHTML = '<div class="history-empty">暂无历史记录</div>';
    return;
  }
  wrap.innerHTML = "";
  for (const item of list) {
    const div = document.createElement("div");
    div.className = "history-item";

    const src = document.createElement("div");
    src.className = "src";
    src.textContent = item.src;
    const dst = document.createElement("div");
    dst.className = "dst";
    dst.textContent = item.dst;
    const tag = document.createElement("div");
    tag.className = "tag";
    tag.textContent = item.tag;

    div.append(src, dst, tag);
    div.addEventListener("click", () => {
      $("input").value = item.src;
      $("inputCount").textContent = `${item.src.length} 字符`;
      $("historyPanel").classList.add("hidden");
      scheduleTranslate(0);
    });
    wrap.appendChild(div);
  }
}
