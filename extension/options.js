const $ = (id) => document.getElementById(id);

async function load() {
  const data = await chrome.storage.local.get(["port"]);
  $("port").value = data.port || 8765;
}

function status(text) {
  $("status").textContent = text;
}

async function testPort(port) {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/health`, {
      signal: AbortSignal.timeout(1500),
    });
    if (!response.ok) return false;
    const data = await response.json();
    return data && ["PaperWhale", "NPL", "Nexus Paper Library"].some(name => (data.app || "").includes(name));
  } catch {
    return false;
  }
}

$("save").addEventListener("click", async () => {
  const port = Number($("port").value);
  if (!port || port < 1 || port > 65535) {
    status("端口不合法");
    return;
  }
  await chrome.storage.local.set({ port });
  status("已保存，端口 " + port);
});

$("test").addEventListener("click", async () => {
  const port = Number($("port").value) || 8765;
  status("正在测试…");
  status((await testPort(port)) ? `连接成功：http://127.0.0.1:${port}` : "连不上，请确认 NPL 正在运行");
});

document.addEventListener("DOMContentLoaded", load);
