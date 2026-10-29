#!/usr/bin/env node
// capture_cdp.js — Автономный снимок веб-страницы или CLI-вывода через Edge/Chrome CDP (Headless).
// Требуется Node >= 22: глобальные fetch и WebSocket без флагов.
//
// Использование:
//   node capture_cdp.js --file path/to/page.html --out path/to/shot.png [--size 1280,900] [--wait 300] [--full-page]
//   node capture_cdp.js --url http://127.0.0.1:8000 --out path/to/shot.png
//   node capture_cdp.js --cli "py app.py" --out path/to/shot.png [--title "Запуск программы"]
//                        [--timeout 60000] [--stdin "входные данные"]
//
// Для --cli команда выполняется по-настоящему, её stdout/stderr декодируются как
// UTF-8 с откатом на cp1251/cp866, ANSI-цвета переносятся в HTML, а кадр
// растягивается на всю высоту вывода (до 8000 px). В кадре печатается код
// возврата команды.
"use strict";

const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");
const net = require("net");
const { pathToFileURL } = require("url");

const DEFAULT_SIZE = "1280,900";
const DEFAULT_WAIT_MS = 300;
const DEFAULT_TIMEOUT_MS = 60000;
const MAX_CAPTURE_HEIGHT = 8000;
const BOOLEAN_FLAGS = new Set(["no-headless", "full-page"]);

function parseArgs(argv) {
  const opts = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next !== undefined && !next.startsWith("--") && !BOOLEAN_FLAGS.has(key)) {
        opts[key] = next;
        i++;
      } else {
        opts[key] = true;
      }
    }
  }
  return opts;
}

function isExecutable(file) {
  try {
    return Boolean(file) && fs.statSync(file).isFile();
  } catch (_) {
    return false;
  }
}

function fromPath(name) {
  const extensions = process.platform === "win32" ? [".exe", ""] : [""];
  for (const directory of String(process.env.PATH || "").split(path.delimiter)) {
    if (!directory) continue;
    for (const extension of extensions) {
      const candidate = path.join(directory, name + extension);
      if (isExecutable(candidate)) return candidate;
    }
  }
  return null;
}

function findBrowser(customPath) {
  for (const candidate of [customPath, process.env.CHROME_PATH, process.env.EDGE_PATH]) {
    if (isExecutable(candidate)) return candidate;
  }

  const localAppData = process.env.LOCALAPPDATA || "";
  const candidates = [
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    path.join(localAppData, "Microsoft", "Edge", "Application", "msedge.exe"),
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    path.join(localAppData, "Google", "Chrome", "Application", "chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
  ];

  for (const candidate of candidates) {
    if (isExecutable(candidate)) return candidate;
  }
  for (const name of ["msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome"]) {
    const found = fromPath(name);
    if (found) return found;
  }
  throw new Error("Не найден Microsoft Edge или Google Chrome; задайте CHROME_PATH или EDGE_PATH");
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
  const startedAt = Date.now();
  for (;;) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) return await response.json();
    } catch (_) {
      // браузер ещё поднимается
    }
    if (Date.now() - startedAt > timeoutMs) {
      throw new Error("Браузер CDP не ответил на HTTP-запрос за " + timeoutMs + " мс");
    }
    await sleep(250);
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

const ANSI_BASE_COLORS = [
  "#4E555B", "#FF5C57", "#5AF78E", "#F3F99D",
  "#57C7FF", "#FF6AC1", "#9AEDFE", "#F1F1F0",
  "#7F8C98", "#FF6E67", "#5AF78E", "#F3F99D",
  "#57C7FF", "#FF6AC1", "#9AEDFE", "#F1F1F0",
];

function color256(index) {
  if (index < 16) return ANSI_BASE_COLORS[index];
  if (index < 232) {
    const value = index - 16;
    const red = Math.floor(value / 36) * 51;
    const green = Math.floor((value % 36) / 6) * 51;
    const blue = (value % 6) * 51;
    return `rgb(${red},${green},${blue})`;
  }
  const gray = 8 + (index - 232) * 10;
  return `rgb(${gray},${gray},${gray})`;
}

function ansiStyle(state) {
  const parts = [];
  if (state.fg) parts.push(`color:${state.fg}`);
  if (state.bg) parts.push(`background-color:${state.bg}`);
  if (state.bold) parts.push("font-weight:600");
  if (state.dim) parts.push("opacity:0.7");
  if (state.italic) parts.push("font-style:italic");
  if (state.underline) parts.push("text-decoration:underline");
  return parts.join(";");
}

function decodeOutput(buffer) {
  if (!buffer || !buffer.length) return "";
  for (const encoding of ["utf-8", "windows-1251", "ibm866"]) {
    try {
      return new TextDecoder(encoding, { fatal: true }).decode(buffer);
    } catch (_) {
      // try the next plausible Windows code page
    }
  }
  return buffer.toString("latin1");
}

function ansiToHtml(text) {
  const source = String(text).replace(/\u001b\][^\u0007]*\u0007/g, "");
  const pattern = /\u001b\[([0-9;?]*)([A-Za-z])/g;
  const state = { fg: null, bg: null, bold: false, dim: false, italic: false, underline: false };
  const reset = () => {
    state.fg = null;
    state.bg = null;
    state.bold = false;
    state.dim = false;
    state.italic = false;
    state.underline = false;
  };
  const emit = (segment) => {
    if (!segment) return "";
    const style = ansiStyle(state);
    return style ? `<span style="${style}">${escapeHtml(segment)}</span>` : escapeHtml(segment);
  };
  let html = "";
  let lastIndex = 0;
  let match;
  while ((match = pattern.exec(source))) {
    html += emit(source.slice(lastIndex, match.index));
    lastIndex = pattern.lastIndex;
    if (match[2] !== "m") continue;
    const codes = match[1] === "" ? [0] : match[1].split(";").map(Number);
    for (let i = 0; i < codes.length; i += 1) {
      const code = codes[i];
      if (code === 0) reset();
      else if (code === 1) state.bold = true;
      else if (code === 2) state.dim = true;
      else if (code === 3) state.italic = true;
      else if (code === 4) state.underline = true;
      else if (code === 22) { state.bold = false; state.dim = false; }
      else if (code === 23) state.italic = false;
      else if (code === 24) state.underline = false;
      else if (code === 39) state.fg = null;
      else if (code === 49) state.bg = null;
      else if (code >= 30 && code <= 37) state.fg = ANSI_BASE_COLORS[code - 30];
      else if (code >= 90 && code <= 97) state.fg = ANSI_BASE_COLORS[code - 90 + 8];
      else if (code >= 40 && code <= 47) state.bg = ANSI_BASE_COLORS[code - 40];
      else if (code >= 100 && code <= 107) state.bg = ANSI_BASE_COLORS[code - 100 + 8];
      else if (code === 38 || code === 48) {
        const target = code === 38 ? "fg" : "bg";
        if (codes[i + 1] === 5 && i + 2 < codes.length) {
          state[target] = color256(codes[i + 2]);
          i += 2;
        } else if (codes[i + 1] === 2 && i + 4 < codes.length) {
          state[target] = `rgb(${codes[i + 2]},${codes[i + 3]},${codes[i + 4]})`;
          i += 4;
        }
      }
    }
  }
  html += emit(source.slice(lastIndex));
  return html;
}

function generateCliTerminalHtml(command, output, options = {}) {
  const settings = typeof options === "string" ? { title: options } : options;
  const displayTitle = settings.title || `Терминал — ${command}`;
  const timedOut = Boolean(settings.timedOut);
  const exitCode = settings.exitCode === undefined || settings.exitCode === null ? null : settings.exitCode;
  let statusText = "";
  if (timedOut) {
    statusText = `остановлено по таймауту (${settings.timeoutSeconds || DEFAULT_TIMEOUT_MS / 1000} с)`;
  } else if (exitCode !== null) {
    statusText = `код возврата: ${exitCode}`;
  }
  const statusClass = timedOut || (exitCode !== null && exitCode !== 0) ? "status-bad" : "status-ok";
  return `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>${escapeHtml(displayTitle)}</title>
  <style>
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; background: #141414; }
    body {
      padding: 24px;
      font-family: 'Cascadia Code', Consolas, 'Courier New', monospace;
      color: #e0e0e0;
    }
    .window {
      width: 100%;
      background: #1e1e1e;
      border: 1px solid #333333;
      border-radius: 8px;
      overflow: hidden;
    }
    .title-bar {
      background: #252526;
      padding: 10px 14px;
      display: flex;
      align-items: center;
      border-bottom: 1px solid #333333;
    }
    .title-text {
      margin-left: 4px;
      font-size: 13px;
      color: #9cdcfe;
      font-weight: 500;
      overflow-wrap: anywhere;
    }
    .terminal-body {
      padding: 18px 20px;
      font-size: 14px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .prompt { color: #4ec9b0; font-weight: 600; }
    .cmd-text { color: #ffffff; font-weight: bold; }
    .output-text { margin-top: 10px; color: #cccccc; }
    .meta {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 8px 20px;
      border-top: 1px solid #333333;
      background: #191919;
      font-size: 12px;
      color: #8a8a8a;
    }
    .status-ok { color: #5AF78E; }
    .status-bad { color: #FF5C57; }
  </style>
</head>
<body>
  <div class="window">
    <div class="title-bar"><div class="title-text">${escapeHtml(displayTitle)}</div></div>
    <div class="terminal-body">
      <div><span class="prompt">PS &gt; </span><span class="cmd-text">${escapeHtml(command)}</span></div>
      <div class="output-text">${ansiToHtml(output || "(команда выполнена без вывода)")}</div>
    </div>
    <div class="meta">
      <span class="${statusClass}">${escapeHtml(statusText)}</span>
    </div>
  </div>
</body>
</html>`;
}

function runCli(command, options = {}) {
  const timeoutMs = Number(options.timeout) > 0 ? Number(options.timeout) : DEFAULT_TIMEOUT_MS;
  const startedAt = Date.now();
  const result = spawnSync(command, {
    shell: true,
    input: options.stdin !== undefined ? String(options.stdin) : undefined,
    timeout: timeoutMs,
    killSignal: "SIGTERM",
    maxBuffer: 20 * 1024 * 1024,
    encoding: null,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
      PYTHONUNBUFFERED: "1",
      NO_COLOR: "1",
      FORCE_COLOR: "0",
      TERM: "dumb",
    },
  });
  const stdout = decodeOutput(result.stdout);
  const stderr = decodeOutput(result.stderr);
  const timedOut = Boolean(result.error && result.error.code === "ETIMEDOUT");
  if (timedOut && result.pid) {
    try {
      spawnSync("taskkill", ["/PID", String(result.pid), "/T", "/F"], { stdio: "ignore" });
    } catch (_) {
      // процесс мог завершиться сам
    }
  }
  return {
    output: stdout + (stderr ? (stdout ? "\n" : "") + stderr : ""),
    exitCode: typeof result.status === "number" ? result.status : null,
    timedOut,
    durationMs: Date.now() - startedAt,
    error: result.error ? result.error.message : null,
  };
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
  let cliResult = null;

  if (options.cli) {
    cliResult = runCli(options.cli, options);
    const htmlContent = generateCliTerminalHtml(options.cli, cliResult.output, {
      title: options.title,
      exitCode: cliResult.exitCode,
      timedOut: cliResult.timedOut,
      timeoutSeconds: Math.round((Number(options.timeout) || DEFAULT_TIMEOUT_MS) / 1000),
    });
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

    // Снимок в формате PNG. Для CLI и --full-page кадр растягивается на весь
    // документ, иначе длинный вывод молча обрезался бы по высоте окна.
    let shot;
    if (options.cli || options["full-page"]) {
      const metrics = await client.send("Page.getLayoutMetrics");
      const content = metrics.cssContentSize || metrics.contentSize || {};
      const pageWidth = Math.max(width, Math.ceil(Number(content.width) || width));
      const minimumHeight = options.cli ? 360 : height;
      const pageHeight = Math.min(
        Math.max(Math.ceil(Number(content.height) || minimumHeight), minimumHeight),
        MAX_CAPTURE_HEIGHT,
      );
      shot = await client.send("Page.captureScreenshot", {
        format: "png",
        captureBeyondViewport: true,
        clip: { x: 0, y: 0, width: pageWidth, height: pageHeight, scale: 1 },
      });
    } else {
      shot = await client.send("Page.captureScreenshot", { format: "png" });
    }
    if (!shot || !shot.data) {
      throw new Error("CDP не вернул данные снимка экрана");
    }

    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, Buffer.from(shot.data, "base64"));
    const suffix = cliResult ? ` exit=${cliResult.exitCode}${cliResult.timedOut ? " timeout" : ""}` : "";
    console.log(`OK ${outPath} (${width}x${height})${suffix}`);
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
    console.error("  node capture_cdp.js --file <path.html> --out <shot.png> [--full-page]");
    console.error("  node capture_cdp.js --url <http://...> --out <shot.png> [--full-page]");
    console.error("  node capture_cdp.js --cli '<command>' --out <shot.png> [--timeout 60000] [--stdin <text>]");
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
  module.exports = {
  capture, findBrowser, parseArgs, generateCliTerminalHtml, decodeOutput, ansiToHtml, runCli,
};
}
