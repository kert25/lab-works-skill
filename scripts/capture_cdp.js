#!/usr/bin/env node
// capture_cdp.js — Автономный снимок веб-страницы через Edge/Chrome CDP (Headless).
// Требуется Node >= 22: глобальные fetch и WebSocket без флагов.
//
// Использование:
//   node capture_cdp.js --file path/to/page.html --out path/to/shot.png [--size 1280,900] [--wait 300] [--full-page]
//   node capture_cdp.js --url http://127.0.0.1:8000 --out path/to/shot.png
//
// Снимается только существующая страница. Снимок вывода команды здесь не
// делается: рисовать окно терминала нельзя, а вывод приводится текстом либо
// снимается с реального окна через shot.ps1.
"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");
const net = require("net");
const { pathToFileURL } = require("url");

const DEFAULT_SIZE = "1280,900";
const DEFAULT_WAIT_MS = 300;
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

  if (options.file) {
    const resolvedFile = path.resolve(options.file);
    if (!fs.existsSync(resolvedFile)) {
      throw new Error(`Файл не найден: ${resolvedFile}`);
    }
    targetUrl = pathToFileURL(resolvedFile).href;
  }

  if (!targetUrl) {
    throw new Error("Необходимо указать --file или --url");
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
    if (options["full-page"]) {
      const metrics = await client.send("Page.getLayoutMetrics");
      const content = metrics.cssContentSize || metrics.contentSize || {};
      const pageWidth = Math.max(width, Math.ceil(Number(content.width) || width));
      const pageHeight = Math.min(
        Math.max(Math.ceil(Number(content.height) || height), height),
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
  }
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (!opts.out || (!opts.file && !opts.url)) {
    console.error("Использование:");
    console.error("  node capture_cdp.js --file <path.html> --out <shot.png> [--full-page]");
    console.error("  node capture_cdp.js --url <http://...> --out <shot.png> [--full-page]");
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
  capture, findBrowser, parseArgs,
};
}
