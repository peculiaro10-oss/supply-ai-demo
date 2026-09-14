"use strict";
// frontend/ is the only editable source. www/ is a disposable build output.
//
// BUILD-001: every build states which backend it targets. There is no default —
// an unstated target used to mean "production", which is how a QA build could
// silently point at the live service.
const fs=require('fs'), path=require('path'), os=require('os');
const {sourceManifest,verify}=require('./verify-native-bundle');
const buildTarget=require('./build-target-config');
const root=path.resolve(__dirname,'..'), source=path.join(root,'frontend'), output=path.join(root,'www');

let target;
try { target=buildTarget.targetFromArgs(); }
catch (error) { console.error(os.EOL + error.message + os.EOL); process.exit(1); }

const manifest=sourceManifest(target); // validate source BEFORE replacing generated output
if (path.dirname(output)!==root || path.basename(output)!=='www') throw new Error('Unsafe output path');
fs.rmSync(output,{recursive:true,force:true});
fs.cpSync(source,output,{recursive:true});
// Replace the checked-in same-origin placeholder with this build's declared target.
fs.writeFileSync(path.join(output,buildTarget.EMITTED_FILE), buildTarget.renderTargetFile(target));
fs.writeFileSync(path.join(output,'build-manifest.json'),JSON.stringify(manifest));
verify(false, target);
console.log('Build target: ' + target.name + ' -> ' + (target.apiBaseUrl || '(same origin)'));
