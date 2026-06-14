const bridge = window.AstrBotPluginPage;
const PLUGIN_NAME = "astrbot_plugin_mind_control";

let statusTimer = null;

function showStatus(msg, isError = false) {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = "status " + (isError ? "error" : "success");
    el.style.display = "block";

    if (statusTimer) clearTimeout(statusTimer);
    statusTimer = setTimeout(() => {
        el.style.display = "none";
    }, 3000);
}

async function loadCurrentMode() {
    try {
        const data = await bridge.apiGet(`${PLUGIN_NAME}/current`);
        if (!data) return;

        document.getElementById("current-mode").textContent = data.mode || "control";

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
        const data = await bridge.apiGet(`${PLUGIN_NAME}/presets`);
        const container = document.getElementById("custom-presets-list");

        if (!data || !data.custom || data.custom.length === 0) {
            container.innerHTML = '<div class="empty-hint">暂无自定义预设</div>';
            return;
        }

        let html = "";
        for (const p of data.custom) {
            html += `
                <div class="preset-item">
                    <div class="preset-name">${p.name}</div>
                    <div style="margin:8px 0;">
                        <button class="secondary" onclick="deletePreset('${p.name}')">删除</button>
                    </div>
                </div>
            `;
        }
        container.innerHTML = html;
    } catch (e) {
        showStatus("加载自定义预设失败: " + e.message, true);
    }
}

async function addPreset() {
    const name = document.getElementById("new-name").value.trim();
    const enter = document.getElementById("new-enter").value.trim();
    const afterglow = document.getElementById("new-afterglow").value.trim();
    const exit = document.getElementById("new-exit").value.trim();

    if (!name) {
        showStatus("请输入预设名称", true);
        return;
    }

    try {
        await bridge.apiPost(`${PLUGIN_NAME}/preset/add`, {
            name, enter, afterglow, exit
        });
        showStatus("添加成功");

        // 清空表单
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
        await bridge.apiPost(`${PLUGIN_NAME}/preset/delete`, { name });
        showStatus("删除成功");
        await loadCustomPresets();
    } catch (e) {
        showStatus("删除失败: " + e.message, true);
    }
}

async function init() {
    await bridge.ready();

    // 应用主题
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

    await loadCurrentMode();
    await loadCustomPresets();
}

window.addPreset = addPreset;
window.deletePreset = deletePreset;

init();