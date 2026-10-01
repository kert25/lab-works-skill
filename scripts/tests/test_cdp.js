#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { profileDir, removeProfileWithRetries } = require("../cdp.js");

async function run() {
  const custom = profileDir({ "profile-dir": "custom-profile" });
  assert.strictEqual(custom, path.resolve("custom-profile"));

  const absent = path.join(os.tmpdir(), "lab-works-skill-cdp-absent");
  fs.rmSync(absent, { recursive: true, force: true });
  assert.strictEqual(await removeProfileWithRetries(absent, 3), "absent");

  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "lab-works-skill-cdp-"));
  let attempts = 0;
  const removeOnSecondAttempt = (target, options) => {
    attempts++;
    if (attempts === 1) throw new Error("simulated temporary lock");
    fs.rmSync(target, options);
  };
  assert.strictEqual(await removeProfileWithRetries(profile, 3, removeOnSecondAttempt), "removed");
  assert.strictEqual(attempts, 2);
  assert.ok(!fs.existsSync(profile));

  console.log("CDP helper tests passed");
}

run().catch(error => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
