#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { parseArgs, findBrowser, generateCliTerminalHtml, capture, decodeOutput, ansiToHtml } = require("../capture_cdp.js");

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

  // 3b. Кириллица из UTF-8 и из cp1251 декодируется без потерь
  assert.strictEqual(decodeOutput(Buffer.from("Привет", "utf8")), "Привет");
  assert.strictEqual(decodeOutput(Buffer.from([0xcf, 0xf0, 0xe8, 0xe2, 0xe5, 0xf2])), "Привет");

  // 3c. ANSI-цвета превращаются в HTML без управляющих последовательностей
  const ansi = ansiToHtml("\u001b[32mOK\u001b[0m и \u001b[31mFAIL\u001b[0m");
  assert.ok(ansi.includes("OK") && ansi.includes("FAIL"));
  assert.ok(!ansi.includes("\u001b["));

  // 3c2. Фон, 256-цветный режим и начертания
  const ansiBg = ansiToHtml("\u001b[41mERR\u001b[0m");
  assert.ok(ansiBg.includes("background-color:#FF5C57"));
  const ansi256 = ansiToHtml("\u001b[38;5;196mred\u001b[0m");
  assert.ok(ansi256.includes("rgb(255,0,0)"));
  const ansiTrue = ansiToHtml("\u001b[38;2;1;2;3mtrue\u001b[0m");
  assert.ok(ansiTrue.includes("rgb(1,2,3)"));
  const ansiStyled = ansiToHtml("\u001b[1;3;4mstyled\u001b[0m");
  assert.ok(ansiStyled.includes("font-weight:600"));
  assert.ok(ansiStyled.includes("font-style:italic"));
  assert.ok(ansiStyled.includes("text-decoration:underline"));

  // 3d. В кадре виден код возврата, без лишних подписей поверх вывода
  const htmlExit = generateCliTerminalHtml("py app.py", "вывод", { exitCode: 0 });
  assert.ok(htmlExit.includes("код возврата: 0"));
  assert.ok(!htmlExit.includes("отрисован автоматически"));

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
