#!/usr/bin/env node
// capture_cdp.js — Автономный снимок веб-страницы или CLI-вывода через Edge/Chrome CDP (Headless).
// Не требует сторонних библиотек (Node >= 18/20/22 со встроенным fetch и WebSocket).
//
// Использование:
//   node capture_cdp.js --file path/to/page.html --out path/to/shot.png [--size 1280,900] [--wait 300]
//   node capture_cdp.js --url http://127.0.0.1:8000 --out path/to/shot.png
//   node capture_cdp.js --cli "py app.py" --out path/to/shot.png [--title "Запуск программы"]
"use strict";

const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");
const net = require("net");
const { pathToFileURL } = require("url");

const DEFAULT_SIZE = "1280,900";
const DEFAULT_WAIT_MS = 300;

function parseArgs(argv) {
  const opts = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next !== undefined && !next.startsWith("--")) {
        opts[key] = next;
        i++;
      } else {
        opts[key] = true;
      }
    }
  }
  return opts;
}

function findBrowser(customPath) {
  if (customPath && fs.existsSync(customPath)) return customPath;

  const candidates = [
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, "Microsoft", "Edge", "Application", "msedge.exe"),
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, "Google", "Chrome", "Application", "chrome.exe"),
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error("Не найден исполняемый файл Microsoft Edge или Google Chrome");
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
    server.on("error", reject);
  });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitHttpReady(port, timeoutMs = 20000) {
  const t0 = Date.now();
  for (;;) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (r.ok) return await r.json();
    } catch (_) {
      if (Date.now() - t0 > timeoutMs) {
        throw new Error("Браузер CDP не ответил на HTTP-запрос за " + timeoutMs + " мс");
      }
      await sleep(250);
    }
  }
}

async function removeDirectoryWithRetries(dirPath, attempts = 4, delayMs = 400) {
  if (!fs.existsSync(dirPath)) return;
  for (let i = 1; i <= attempts; i++) {
    try {
      fs.rmSync(dirPath, { recursive: true, force: true });
      if (!fs.existsSync(dirPath)) return;
    } catch (_) {
      if (i < attempts) await sleep(delayMs);
    }
  }
}

function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function generateCliTerminalHtml(command, output, title) {
  const displayTitle = title || `Терминал — ${command}`;
  return `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>${escapeHtml(displayTitle)}</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 24px;
      background: #141414;
      font-family: 'Cascadia Code', Consolas, 'Courier New', monospace;
      color: #e0e0e0;
      display: flex;
      justify-content: center;
      align-items: flex-start;
      min-height: 100vh;
    }
    .window {
      width: 100%;
      max-width: 1230px;
      background: #1e1e1e;
      border: 1px solid #333333;
      border-radius: 8px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
      overflow: hidden;
    }
    .title-bar {
      background: #252526;
      padding: 10px 14px;
      display: flex;
      align-items: center;
      border-bottom: 1px solid #333333;
      user-select: none;
    }
    .controls {
      display: flex;
      gap: 8px;
    }
    .dot {
      width: 12px;
      height: 12px;
      border-radius: 50%;
      display: inline-block;
    }
    .dot-close { background: #ff5f56; }
    .dot-min { background: #ffbd2e; }
    .dot-max { background: #27c93f; }
    .title-text {
      margin-left: 14px;
      font-size: 13px;
      color: #9cdcfe;
      font-weight: 500;
    }
    .terminal-body {
      padding: 18px 20px;
      font-size: 14px;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-all;
    }
    .prompt {
      color: #4ec9b0;
      font-weight: 600;
    }
    .cmd-text {
      color: #ffffff;
      font-weight: bold;
    }
    .output-text {
      margin-top: 8px;
      color: #cccccc;
    }
  </style>
</head>
<body>
  <div class="window">
    <div class="title-bar">
      <div class="controls">
        <span class="dot dot-close"></span>
        <span class="dot dot-min"></span>
        <span class="dot dot-max"></span>
      </div>
      <div class="title-text">${escapeHtml(displayTitle)}</div>
    </div>
    <div class="terminal-body">
      <div><span class="prompt">PS &gt; </span><span class="cmd-text">${escapeHtml(command)}</span></div>
      <div class="output-text">${escapeHtml(output)}</div>
    </div>
  </div>
</body>
</html>`;
}

class CDPClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.id = 0;
    this.pending = new Map();
  }

  ready() {
    return new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", (e) => reject(new Error("WebSocket error: " + e.message)), { once: true });
      this.ws.addEventListener("message", (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.id !== undefined && this.pending.has(msg.id)) {
            const { resolve, reject } = this.pending.get(msg.id);
            this.pending.delete(msg.id);
            if (msg.error) reject(new Error(msg.error.message));
            else resolve(msg.result);
          }
        } catch (_) {}
      });
    });
  }

  send(method, params = {}) {
    const curId = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(curId, { resolve, reject });
      this.ws.send(JSON.stringify({ id: curId, method, params }));
    });
  }

  close() {
    try {
      this.ws.close();
    } catch (_) {}
  }
}

async function capture(options) {
  const browserPath = findBrowser(options.browser);
  const outPath = path.resolve(options.out);
  const size = options.size || DEFAULT_SIZE;
  const [widthStr, heightStr] = size.split(",");
  const width = parseInt(widthStr, 10) || 1280;
  const height = parseInt(heightStr, 10) || 900;
  const waitMs = parseInt(options.wait, 10) || DEFAULT_WAIT_MS;

  let targetUrl = options.url;
  let tempHtmlPath = null;

  if (options.cli) {
    const cmdResult = spawnSync(options.cli, {
      shell: true,
      encoding: "utf-8",
      maxBuffer: 10 * 1024 * 1024,
    });
    const stdout = cmdResult.stdout || "";
    const stderr = cmdResult.stderr || "";
    const fullOutput = stdout + (stderr ? (stdout ? "\n" : "") + stderr : "");
    const htmlContent = generateCliTerminalHtml(options.cli, fullOutput || "(команда выполнена без вывода)", options.title);
    tempHtmlPath = path.join(os.tmpdir(), `cli-terminal-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.html`);
    fs.writeFileSync(tempHtmlPath, htmlContent, "utf-8");
    targetUrl = pathToFileURL(tempHtmlPath).href;
  } else if (options.file) {
    const resolvedFile = path.resolve(options.file);
    if (!fs.existsSync(resolvedFile)) {
      throw new Error(`Файл не найден: ${resolvedFile}`);
    }
    targetUrl = pathToFileURL(resolvedFile).href;
  }

  if (!targetUrl) {
    throw new Error("Необходимо указать --file, --url или --cli");
  }

  const port = options.port ? parseInt(options.port, 10) : await getFreePort();
  const profileDir = path.join(os.tmpdir(), `edge-shot-prof-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`);

  const browserArgs = [
    `--remote-debugging-port=${port}`,
    "--remote-allow-origins=*",
    `--user-data-dir=${profileDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--noerrdialogs",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--hide-scrollbars",
    `--window-size=${width},${height}`,
    "about:blank",
  ];

  if (!options["no-headless"]) {
    browserArgs.push("--headless=new");
  }

  const child = spawn(browserPath, browserArgs, { stdio: "ignore", detached: true });
  child.unref();

  let versionInfo = null;
  let client = null;
  let browserClient = null;

  try {
    versionInfo = await waitHttpReady(port);
    const targetsRes = await fetch(`http://127.0.0.1:${port}/json/list`);
    const targets = await targetsRes.json();
    const pageTarget = targets.find((t) => t.type === "page") || targets[0];

    if (!pageTarget || !pageTarget.webSocketDebuggerUrl) {
      throw new Error("Не найдена страница для подключения CDP");
    }

    client = new CDPClient(pageTarget.webSocketDebuggerUrl);
    await client.ready();

    await client.send("Page.enable");
    await client.send("Runtime.enable").catch(() => {});

    // Навигация к целевому URL
    const navPromise = client.send("Page.navigate", { url: targetUrl });
    await Promise.race([navPromise, sleep(15000)]);

    if (waitMs > 0) {
      await sleep(waitMs);
    }

    // Снимок в формате PNG
    const shot = await client.send("Page.captureScreenshot", { format: "png" });
    if (!shot || !shot.data) {
      throw new Error("CDP не вернул данные снимка экрана");
    }

    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, Buffer.from(shot.data, "base64"));
    console.log(`OK ${outPath} (${width}x${height})`);
    return outPath;
  } finally {
    if (client) client.close();

    if (versionInfo && versionInfo.webSocketDebuggerUrl) {
      try {
        browserClient = new CDPClient(versionInfo.webSocketDebuggerUrl);
        await browserClient.ready();
        await browserClient.send("Browser.close");
      } catch (_) {}
      if (browserClient) browserClient.close();
    }

    // Завершаем процесс, если он ещё активен
    try {
      if (child && !child.killed) child.kill();
    } catch (_) {}

    await sleep(400);
    await removeDirectoryWithRetries(profileDir);

    if (tempHtmlPath) {
      try {
        fs.unlinkSync(tempHtmlPath);
      } catch (_) {}
    }
  }
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (!opts.out || (!opts.file && !opts.url && !opts.cli)) {
    console.error("Использование:");
    console.error("  node capture_cdp.js --file <path.html> --out <shot.png> [--size 1280,900] [--wait 300]");
    console.error("  node capture_cdp.js --url <http://...> --out <shot.png>");
    console.error("  node capture_cdp.js --cli '<command>' --out <shot.png> [--title <Title>]");
    process.exit(1);
  }

  try {
    await capture(opts);
  } catch (err) {
    console.error("ERROR capture_cdp:", err.message);
    process.exit(1);
  }
}

if (require.main === module) {
  main();
} else {
  module.exports = { capture, findBrowser, parseArgs, generateCliTerminalHtml };
}
