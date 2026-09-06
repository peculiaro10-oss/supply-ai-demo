#!/usr/bin/env node
// Regenerates www/ (the Capacitor webDir — see capacitor.config.json) from
// frontend/, the actual source of truth for the web app. www/ is a build
// output: nothing under it should ever be hand-edited, and it is safe (and
// expected) to delete and recreate it at any time — see MOBILE_PACKAGING.md.
//
// Pure Node (fs.rmSync/fs.cpSync, both built in since Node 16.7/14.14) so
// this runs identically on Windows/macOS/Linux with no extra dependency and
// no shell-specific syntax (no rm -rf / robocopy branching).
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "frontend");
const DEST = path.join(ROOT, "www");

if (!fs.existsSync(SRC)) {
    console.error(`[build-www] frontend/ not found at ${SRC} — nothing to copy.`);
    process.exit(1);
}

fs.rmSync(DEST, { recursive: true, force: true });
fs.cpSync(SRC, DEST, { recursive: true });

console.log(`[build-www] www/ regenerated from frontend/ (${SRC} -> ${DEST}).`);
