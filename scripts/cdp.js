#!/usr/bin/env node
// CDP-управление Edge (Chrome DevTools Protocol) для лабораторных работ.
// Node >= 22 (глобальный WebSocket), внешних зависимостей нет.
//
// Режимы:
//   node cdp.js launch [--port 9222] [--url U] [--size 1280,900] [--headless]
//         запускает Edge с --remote-debugging-port и ждёт готовности HTTP-эндпоинта
//   node cdp.js run [действия...] [--port 9222] [--match substr] [--timeout 15000]
//         подключается к открытой вкладке (или создаёт новую через --url) и
//         выполняет действия в порядке следования аргументов:
//           --url U            открыть URL в текущей/новой вкладке (ждёт загрузки)
//           --file path        открыть локальный файл; путь безопасно преобразуется в file:/// URL
//           --eval "код"       выполнить JS в странице, напечатать результат
//           --eval-file f.js   то же из файла
//           --nowait-eval "код" отправить JS не дожидаясь ответа (для alert/confirm/prompt)
//           --click "селектор" настоящий щелчок мыши по элементу (Input.dispatchMouseEvent);
//                              доверенное событие с user activation — открывает popup,
//                              не блокируемый popup-блокироватором (в отличие от .click())
//           --shot out.png     снимок viewport страницы (Page.captureScreenshot)
//           --os-shot out.png  снимок реального окна Edge через shot.ps1 (для alert/confirm/
//                              prompt: снимок делается при живом CDP-соединении, т.к. Edge
//                              закрывает диалог при отключении последнего DevTools-клиента);
//                              дополнительно: --os-shot-process msedge | --os-shot-title "подстрока"
//           --wait N           пауза N мс
//           --dialog-action accept|dismiss[:текст]  дождаться открытого JS-диалога
//                              и обработать его прямо в очереди действий
//                              (текст после ':' — для prompt); можно несколько раз
//           --dialog-wait      ждать открытия JS-диалога (без обработки)
//           --on-dialog keep|accept|dismiss  что делать с открытым диалогом в конце run
//                              (keep: только сообщить и оставить открытым — для снимка ОС-окна)
//           --on-dialog-text T текст для prompt при accept
//   node cdp.js dialog --action accept|dismiss [--text T] [--port 9222]
//         обработать уже открытый JS-диалог (Page.handleJavaScriptDialog)
//   node cdp.js close [--match substr] [--url U] [--port 9222]
//         закрыть подходящие вкладки
//   node cdp.js quit [--port 9222]
//         закрыть браузер целиком
//   node cdp.js reset [--port 9222]
//         закрыть CDP-браузер, если он доступен, и удалить временный профиль
"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");
const { pathToFileURL } = require("url");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const DEFAULT_PORT = 9222;
const PROFILE_DIR = path.join(os.tmpdir(), "edge-cdp-profile");

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next !== undefined && !next.startsWith("--") &&
          !["pb", "headless"].includes(key)) {
        out[key] = next; i++;
      } else {
        out[key] = true;
      }
    } else out._.push(a);
  }
  return out;
}

function httpGet(url) {
  return fetch(url).then(r => r.json());
}
function httpPut(url) {
  return fetch(url, { method: "PUT" }).then(r => r.json());
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitReady(port, timeoutMs = 20000) {
  const t0 = Date.now();
  for (;;) {
    try { return await httpGet(`http://127.0.0.1:${port}/json/version`); }
    catch (e) {
      if (Date.now() - t0 > timeoutMs) throw new Error("Edge CDP не отвечает: " + e.message);
      await sleep(300);
    }
  }
}

async function listTargets(port) {
  return httpGet(`http://127.0.0.1:${port}/json/list`);
}

async function pickTarget(port, opts) {
  const targets = await listTargets(port);
  const pages = targets.filter(t => t.type === "page");
  if (!pages.length) throw new Error("нет открытых вкладок");
  if (opts.match) {
    const found = pages.find(t => t.url.includes(opts.match));
    if (!found) throw new Error("вкладка не найдена по --match: " + opts.match);
    return found;
  }
  return pages[0];
}

class CDP {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.ws.addEventListener("message", ev => this._onMessage(JSON.parse(ev.data)));
    this.id = 0;
    this.pending = new Map();
    this.listeners = [];
  }
  _onMessage(msg) {
    if (msg.id !== undefined) {
      const p = this.pending.get(msg.id);
      if (p) { this.pending.delete(msg.id);
        msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result); }
    } else {
      for (const l of this.listeners) l(msg.method, msg.params);
    }
  }
  ready() {
    return new Promise((res, rej) => {
      this.ws.addEventListener("open", () => res());
      this.ws.addEventListener("error", e => rej(new Error("WebSocket: " + e.message)));
    });
  }
  on(fn) { this.listeners.push(fn); }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  sendNoWait(method, params = {}) {
    this.ws.send(JSON.stringify({ id: ++this.id, method, params }));
  }
  close() { this.ws.close(); }
}

function print(obj) { console.log(typeof obj === "string" ? obj : JSON.stringify(obj)); }

function fileUrl(filePath) {
  return pathToFileURL(path.resolve(String(filePath))).href;
}

// --- запуск Edge ------------------------------------------------------------

async function cmdLaunch(opts) {
  const port = Number(opts.port || DEFAULT_PORT);
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${PROFILE_DIR}`,
    "--no-first-run", "--no-default-browser-check", "--noerrdialogs",
    `--window-size=${opts.size || "1280,900"}`,
  ];
  if (opts.headless) args.push("--headless=new");
  if (opts.url) args.push(opts.url);
  const child = spawn(EDGE, args, { detached: true, stdio: "ignore" });
  child.unref();
  const v = await waitReady(port);
  print("READY " + v.Browser + " port=" + port + " pid=" + child.pid);
}

// --- run ---------------------------------------------------------------------

async function cmdRun(opts) {
  const port = Number(opts.port || DEFAULT_PORT);
  await waitReady(port);
  let target;
  const initialUrl = opts.file ? fileUrl(opts.file) : opts.url;
  if (initialUrl) {
    // URL, созданный pathToFileURL, уже экранирован и не должен кодироваться повторно.
    const u = opts.file ? initialUrl : encodeURI(initialUrl);
    const pages = (await listTargets(port)).filter(t => t.type === "page");
    target = pages.find(t => t.url.startsWith(u)) ||
             pages.find(t => t.url === "about:blank");
    if (!target) {
      // вкладку создаём ПУСТОЙ и грузим страницу одним Page.navigate ниже:
      // создание вкладки сразу с URL порождает двойную загрузку страницы,
      // из-за которой теряются диалоги (alert/prompt/confirm), открытые при загрузке
      target = await httpPut(`http://127.0.0.1:${port}/json/new?about:blank`);
    }
  } else {
    target = await pickTarget(port, opts);
  }
  print("TARGET " + target.url);
  const cdp = new CDP(target.webSocketDebuggerUrl);
  await cdp.ready();

  let dialogState = null;   // {type, message, defaultPrompt}
  let dialogWaiters = [];
  cdp.on((method, params) => {
    if (method === "Page.javascriptDialogClosed") {
      dialogState = null;   // диалог закрыт — следующий --dialog-action будет ждать нового
      return;
    }
    if (method === "Page.javascriptDialogOpening") {
      dialogState = params;
      print(`DIALOG OPEN: type=${params.type} message=${JSON.stringify(params.message)}` +
            (params.defaultPrompt ? ` defaultPrompt=${JSON.stringify(params.defaultPrompt)}` : ""));
      dialogWaiters.forEach(w => w());
      dialogWaiters = [];
    }
  });
  await Promise.race([cdp.send("Page.enable"), sleep(3000)]);
  await Promise.race([cdp.send("Runtime.enable"), sleep(3000)]).catch(() => {});

  const evals = [];   // очередь действий в порядке argv
  function push(kind, value) { evals.push({ kind, value }); }

  // разбираем аргументы-действия в порядке их следования
  const raw = process.argv.slice(3);
  for (let i = 0; i < raw.length; i++) {
    const a = raw[i];
    if (a === "--url") { push("url", raw[++i]); }
    else if (a === "--file") { push("file", fileUrl(raw[++i])); }
    else if (a === "--eval") { push("eval", raw[++i]); }
    else if (a === "--eval-file") { push("evalfile", raw[++i]); }
    else if (a === "--nowait-eval") { push("nowait-eval", raw[++i]); }
    else if (a === "--click") { push("click", raw[++i]); }
    else if (a === "--shot") { push("shot", raw[++i]); }
    else if (a === "--os-shot") { push("osshot", raw[++i]); }
    else if (a === "--os-shot-process") { i++; }   // значение обрабатывает parseArgs
    else if (a === "--os-shot-title") { i++; }
    else if (a === "--wait") { push("wait", Number(raw[++i])); }
    else if (a === "--dialog-action") { push("dialogaction", raw[++i]); }
    else if (a === "--dialog-wait") { push("dialog-wait", null); }
  }
  const onErrorDialog = opts["on-dialog"] || "keep";   // keep | accept | dismiss

  const doEval = async (expression, awaitResponse) => {
    if (awaitResponse) {
      try {
        const r = await cdp.send("Runtime.evaluate",
          { expression, returnByValue: true, awaitPromise: true });
        if (r.exceptionDetails)
          print("EVAL ERROR " + JSON.stringify(r.exceptionDetails.exception?.description || r.exceptionDetails.text));
        else
          print("EVAL " + JSON.stringify(r.result.value));
      } catch (e) {
        // ответ не пришёл (например, поток заблокирован открытым диалогом) — это нормально
        print("EVAL (без ответа: " + e.message + ")");
      }
    } else {
      cdp.sendNoWait("Runtime.evaluate", { expression, returnByValue: true });
      print("EVAL-SENT (ответа не ждём)");
    }
  };

  for (const act of evals) {
    switch (act.kind) {
      case "url":
      case "file": {
        const u = act.kind === "file" ? act.value : encodeURI(act.value);
        const nav = cdp.send("Page.navigate", { url: u });
        // дождаться load или диалога
        await Promise.race([nav, sleep(opts.timeout || 15000)]);
        await new Promise(res => {
          let done = false;
          const t = setTimeout(() => { if (!done) { done = true; res(); } }, opts.timeout || 15000);
          cdp.on((m) => { if (m === "Page.loadEventFired" && !done) { done = true; clearTimeout(t); res(); } });
        });
        print("NAV " + u);
        break;
      }
      case "eval": await doEval(act.value, true); break;
      case "click": {
        // координаты центра элемента в viewport
        const r = await cdp.send("Runtime.evaluate", {
          expression:
            `(function(){var e=document.querySelector(${JSON.stringify(act.value)});` +
            `if(!e)return null;var b=e.getBoundingClientRect();` +
            `return JSON.stringify({x:b.left+b.width/2,y:b.top+b.height/2});})()`,
          returnByValue: true,
        });
        if (!r.result || r.result.value == null) {
          print("CLICK элемент не найден: " + act.value);
          break;
        }
        const { x, y } = JSON.parse(r.result.value);
        // доверенный щелчок мыши (user activation) — два события: нажатие и отпускание
        const base = { x, y, button: "left", clickCount: 1 };
        await cdp.send("Input.dispatchMouseEvent", { ...base, type: "mousePressed" });
        await cdp.send("Input.dispatchMouseEvent", { ...base, type: "mouseReleased" });
        print("CLICK " + act.value + " @ " + x + "," + y);
        break;
      }
      case "evalfile": await doEval(fs.readFileSync(act.value, "utf8"), true); break;
      case "nowait-eval": await doEval(act.value, false); break;
      case "shot": {
        await sleep(200);   // дать браузеру отрисоваться
        const r = await cdp.send("Page.captureScreenshot", { format: "png" });
        fs.writeFileSync(act.value, Buffer.from(r.data, "base64"));
        print("SHOT " + act.value);
        break;
      }
      case "osshot": {
        // снимок реального окна Edge через shot.ps1 — НЕ разрывая CDP-соединение
        // (Edge закрывает JS-диалог при отключении последнего DevTools-клиента)
        await sleep(300);
        const psArgs = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        path.join(__dirname, "shot.ps1"), "-Out", act.value,
                        "-Process", String(opts["os-shot-process"] || "msedge")];
        if (opts["os-shot-title"]) {
          psArgs.push("-Title"); psArgs.push(String(opts["os-shot-title"]));
          psArgs.splice(psArgs.indexOf("-Process"), 2);
        }
        const r = require("child_process").spawnSync("powershell.exe", psArgs,
          { stdio: ["ignore", "pipe", "pipe"], encoding: "utf8" });
        print((r.stdout || "").trim() || ("OSSHOT " + act.value));
        if (r.status !== 0) print("OSSHOT WARNING: powershell exit=" + r.status + " " + (r.stderr || "").trim());
        break;
      }
      case "wait": await sleep(act.value); print("WAIT " + act.value); break;
      case "dialogaction": {
        // дождаться открытия диалога (если ещё не открыт)
        if (!dialogState) {
          await Promise.race([
            new Promise(res => dialogWaiters.push(res)),
            sleep(opts.timeout || 15000),
          ]);
        }
        if (!dialogState) { print("DIALOG не открылся за таймаут"); break; }
        // значение вида accept[:текст] / dismiss[:текст]
        const parts = String(act.value).split(":");
        const action = parts.shift();
        const text = parts.join(":");
        const params = { accept: action === "accept" };
        if (text) params.promptText = text;
        await cdp.send("Page.handleJavaScriptDialog", params);
        print("DIALOG HANDLED " + action + (text ? " text=" + JSON.stringify(text) : ""));
        break;
      }
      case "dialog-wait": {
        if (!dialogState) {
          await Promise.race([
            new Promise(res => dialogWaiters.push(res)),
            sleep(opts.timeout || 15000),
          ]);
        }
        if (!dialogState) print("DIALOG не открылся за таймаут");
        break;
      }
    }
  }

  // обработка диалога по --on-dialog
  if (dialogState && onErrorDialog !== "keep") {
    const params = { accept: onErrorDialog === "accept" };
    if (opts["on-dialog-text"]) params.promptText = opts["on-dialog-text"];
    await cdp.send("Page.handleJavaScriptDialog", params);
    print("DIALOG HANDLED " + onErrorDialog);
  } else if (dialogState) {
    print("DIALOG оставлен открытым (--on-dialog=keep); закрыть: node cdp.js dialog --action accept|dismiss");
  }
  cdp.close();
}

// --- dialog -------------------------------------------------------------------

async function cmdDialog(opts) {
  const port = Number(opts.port || DEFAULT_PORT);
  await waitReady(port);
  const pages = (await listTargets(port)).filter(t => t.type === "page");
  // диалог может быть в любой вкладке (первой может быть edge://newtab) — пробуем все
  const candidates = opts.match
    ? pages.filter(t => t.url.includes(opts.match))
    : pages;
  if (!candidates.length) throw new Error("нет вкладок для --match: " + opts.match);
  const params = { accept: opts.action === "accept" };
  if (opts.text) params.promptText = opts.text;
  let handled = false;
  for (const t of candidates) {
    const cdp = new CDP(t.webSocketDebuggerUrl);
    try {
      await cdp.ready();
      await cdp.send("Page.handleJavaScriptDialog", params);
      print("DIALOG HANDLED " + opts.action + " (вкладка " + t.url + ")");
      handled = true;
      cdp.close();
      break;
    } catch (e) {
      // в этой вкладке диалога нет — пробуем следующую
    } finally {
      try { cdp.close(); } catch (_) {}
    }
  }
  if (!handled) { print("DIALOG ERROR: открытый диалог не найден"); process.exitCode = 2; }
}

// --- close / quit --------------------------------------------------------------

async function cmdClose(opts) {
  const port = Number(opts.port || DEFAULT_PORT);
  const targets = (await listTargets(port)).filter(t => t.type === "page");
  const match = opts.match || opts.url;
  const victims = match ? targets.filter(t => (opts.match ? t.url.includes(opts.match) : t.url.startsWith(encodeURI(opts.url)))) : targets;
  for (const v of victims) {
    await fetch(`http://127.0.0.1:${port}/json/close/${v.id}`);
    print("CLOSED " + v.url);
  }
}

async function cmdQuit(opts) {
  const port = Number(opts.port || DEFAULT_PORT);
  const v = await waitReady(port);
  if (!v.webSocketDebuggerUrl) throw new Error("browser webSocketDebuggerUrl не найден");
  const cdp = new CDP(v.webSocketDebuggerUrl);
  await cdp.ready();
  await cdp.send("Browser.close");
  print("BROWSER CLOSED");
  cdp.close();
}

async function cmdReset(opts) {
  const port = Number(opts.port || DEFAULT_PORT);
  try {
    await cmdQuit({ port });
    await sleep(500);
  } catch (error) {
    // Браузер мог уже завершиться или порт быть занят другим процессом.
    print("RESET: CDP browser unavailable (" + error.message + ")");
  }
  try {
    fs.rmSync(PROFILE_DIR, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
    print("PROFILE REMOVED " + PROFILE_DIR);
  } catch (error) {
    throw new Error("не удалось удалить временный профиль: " + error.message);
  }
}

(async () => {
  const [mode, ...rest] = process.argv.slice(2);
  const opts = parseArgs(process.argv.slice(3));
  try {
    switch (mode) {
      case "launch": await cmdLaunch(opts); break;
      case "run": await cmdRun(opts); break;
      case "dialog": await cmdDialog(opts); break;
      case "close": await cmdClose(opts); break;
      case "quit": await cmdQuit(opts); break;
      case "reset": await cmdReset(opts); break;
      default: throw new Error("неизвестный режим: " + mode + " (launch|run|dialog|close|quit|reset)");
    }
  } catch (e) {
    console.error("ERROR " + e.message);
    process.exit(1);
  }
})();
