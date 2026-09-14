"use strict";
// BUILD-001 — regenerates the checked-in frontend/js/build-target.js from
// scripts/build-targets.json.
//
// The tracked copy is the same-origin ("web") rendering: it is what the ordinary
// web deployment serves out of frontend/, and it is what the parity verifier
// requires the source tree to contain. Run this after editing the registry or
// the renderer; the verifier fails closed if the two ever disagree.
const fs = require('fs'), path = require('path');
const buildTarget = require('./build-target-config');

const file = path.join(buildTarget.root, 'frontend', buildTarget.EMITTED_FILE);
const expected = buildTarget.renderTargetFile(buildTarget.sourceTargetName());
const current = fs.existsSync(file) ? fs.readFileSync(file) : null;

if (current && current.equals(expected)) {
    console.log('frontend/' + buildTarget.EMITTED_FILE + ' already matches source target "' + buildTarget.sourceTargetName() + '"');
    return;
}
fs.writeFileSync(file, expected);
console.log((current ? 'Updated' : 'Created') + ' frontend/' + buildTarget.EMITTED_FILE +
    ' for source target "' + buildTarget.sourceTargetName() + '"');
