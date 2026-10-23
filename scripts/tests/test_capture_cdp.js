#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { parseArgs, findBrowser, generateCliTerminalHtml, capture } = require("../capture_cdp.js");

async function run() {
  // 1. Тест парсинга аргументов
  const parsed = parseArgs(["--file", "test.html", "--out", "shot.png", "--no-headless", "--wait", "500"]);
  assert.strictEqual(parsed.file, "test.html");
  assert.strictEqual(parsed.out, "shot.png");
  assert.strictEqual(parsed["no-headless"], true);
  assert.strictEqual(parsed.wait, "500");

  // 2. Тест поиска браузера
  const browser = findBrowser();
  assert.ok(typeof browser === "string" && fs.existsSync(browser), "Браузер Edge/Chrome должен существовать");

  // 3. Тест генератора HTML терминала
  const html = generateCliTerminalHtml("echo 'test'", "result output", "Тестовый запуск");
  assert.ok(html.includes("echo &#39;test&#39;"));
  assert.ok(html.includes("result output"));
  assert.ok(html.includes("Тестовый запуск"));

  // 4. Интеграционный тест снятия скриншота с временного HTML-файла
  const tempHtml = path.join(os.tmpdir(), `test-capture-${Date.now()}.html`);
  const tempPng = path.join(os.tmpdir(), `test-capture-${Date.now()}.png`);
  fs.writeFileSync(tempHtml, "<html><body style='background:#222;color:#fff;'><h1>CDP Test</h1></body></html>", "utf-8");

  try {
    const saved = await capture({
      file: tempHtml,
      out: tempPng,
      size: "800,600",
      wait: 200,
    });
    assert.strictEqual(saved, path.resolve(tempPng));
    assert.ok(fs.existsSync(tempPng), "PNG файл должен быть создан");
    const stat = fs.statSync(tempPng);
    assert.ok(stat.size > 1000, "PNG файл должен содержать данные");
  } finally {
    try { fs.unlinkSync(tempHtml); } catch (_) {}
    try { fs.unlinkSync(tempPng); } catch (_) {}
  }

  console.log("capture_cdp tests passed successfully");
}

run().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
