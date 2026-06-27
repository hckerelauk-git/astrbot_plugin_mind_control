const bridge = window.AstrBotPluginPage;

let statusTimer = null;
let isDark = true;

const MODE_NAMES = {
    control: "控制", pet: "宠物化", teacher: "师徒",
    shy: "害羞", tsundere: "傲娇",
};
const CURVE_NAMES = {
    flat: "固定", ramp_up: "增强", decay: "消退", wave: "波动",
};

function showStatus(msg, isError = false) {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = "status " + (isError ? "error" : "success");
    el.style.display = "block";
    if (statusTimer) clearTimeout(statusTimer);
    statusTimer = setTimeout(() => { el.style.display = "none"; }, 3000);
}

async function loadCurrentMode() {
    try {
        const data = await bridge.apiGet("current");
        if (!data) return;

        const mode = data.mode || "control";
        const modeName = MODE_NAMES[mode] || mode;

        document.getElementById("topbar-title").textContent = "当前模式";
        document.getElementById("stat-mode").textContent = modeName;
        document.getElementById("stat-sensitivity").textContent = data.sensitivity ?? "-";
        document.getElementById("stat-curve").textContent = CURVE_NAMES[data.curve] || data.curve || "-";
        document.getElementById("current-mode").textContent = modeName;
        document.getElementById("mode-select").value = MODE_NAMES[mode] ? mode : "";

        const tag = document.getElementById("current-mode-tag");
        tag.textContent = data.is_custom ? "自定义" : "内置";

        const prompts = data.prompts || {};
        document.getElementById("prompt-enter").textContent = prompts.enter || "(空)";
        document.getElementById("prompt-afterglow").textContent = prompts.afterglow || "(空)";
        document.getElementById("prompt-exit").textContent = prompts.exit || "(空)";
    } catch (e) {
        showStatus("加载当前模式失败: " + e.message, true);
    }
}

async function loadCustomPresets() {
    try {
        const data = await bridge.apiGet("presets");
        const container = document.getElementById("custom-presets-list");
        const count = data?.custom?.length || 0;
        document.getElementById("stat-presets").textContent = count;

        if (!data || !data.custom || data.custom.length === 0) {
            container.innerHTML = '<div class="empty-hint">暂无自定义预设</div>';
            return;
        }

        let html = "";
        for (const p of data.custom) {
            html += `
                <div class="preset-item">
                    <div class="preset-header">
                        <div class="preset-name">${escapeHtml(p.name)}</div>
                        <span class="preset-badge">自定义</span>
                    </div>
                    <div class="prompt-block">
                        <div class="prompt-label">进入提示词</div>
                        <div class="prompt-text">${escapeHtml(p.enter || "(空)")}</div>
                    </div>
                    <div class="prompt-block">
                        <div class="prompt-label">余韵提示词</div>
                        <div class="prompt-text">${escapeHtml(p.afterglow || "(空)")}</div>
                    </div>
                    <div class="prompt-block">
                        <div class="prompt-label">退出提示词</div>
                        <div class="prompt-text">${escapeHtml(p.exit || "(空)")}</div>
                    </div>
                    <div style="margin-top:14px;">
                        <button class="btn btn-danger" data-name="${escapeAttr(p.name)}" onclick="deletePreset(this.getAttribute('data-name'))">删除</button>
                    </div>
                </div>
            `;
        }
        container.innerHTML = html;
    } catch (e) {
        showStatus("加载自定义预设失败: " + e.message, true);
    }
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function escapeAttr(text) {
    return String(text).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function addPreset() {
    const name = document.getElementById("new-name").value.trim();
    const enter = document.getElementById("new-enter").value.trim();
    const afterglow = document.getElementById("new-afterglow").value.trim();
    const exit = document.getElementById("new-exit").value.trim();

    if (!name) { showStatus("请输入预设名称", true); return; }

    try {
        await bridge.apiPost("preset/add", { name, enter, afterglow, exit });
        showStatus("添加成功");
        document.getElementById("new-name").value = "";
        document.getElementById("new-enter").value = "";
        document.getElementById("new-afterglow").value = "";
        document.getElementById("new-exit").value = "";
        await loadCustomPresets();
    } catch (e) {
        showStatus("添加失败: " + e.message, true);
    }
}

async function deletePreset(name) {
    if (!confirm(`确定删除预设「${name}」吗？`)) return;
    try {
        await bridge.apiPost("preset/delete", { name });
        showStatus("删除成功");
        await loadCustomPresets();
    } catch (e) {
        showStatus("删除失败: " + e.message, true);
    }
}

function switchSection(section) {
    document.querySelectorAll(".sidebar-nav .nav-item").forEach(el => el.classList.remove("active"));
    document.querySelector(`.sidebar-nav .nav-item[data-section="${section}"]`)?.classList.add("active");

    document.querySelectorAll(".section").forEach(el => el.classList.remove("active"));
    document.getElementById(`section-${section}`)?.classList.add("active");

    const titles = { current: "当前模式", presets: "自定义预设" };
    document.getElementById("topbar-title").textContent = titles[section] || "";
}

function toggleTheme() {
    isDark = !isDark;
    document.documentElement.setAttribute("data-theme", isDark ? "dark" : "");
}

async function init() {
    await bridge.ready();

    if (bridge.getContext()?.isDark) {
        document.documentElement.setAttribute("data-theme", "dark");
    }

    bridge.onContext((ctx) => {
        if (ctx.isDark) {
            document.documentElement.setAttribute("data-theme", "dark");
        } else {
            document.documentElement.removeAttribute("data-theme");
        }
    });

    document.querySelectorAll(".sidebar-nav .nav-item").forEach(el => {
        el.addEventListener("click", () => switchSection(el.dataset.section));
    });

    document.getElementById("theme-toggle")?.addEventListener("click", toggleTheme);
    document.getElementById("btn-refresh")?.addEventListener("click", () => {
        loadCurrentMode();
        loadCustomPresets();
    });

    await loadCurrentMode();
    await loadCustomPresets();
}

window.addPreset = addPreset;
window.deletePreset = deletePreset;

init();