"use strict";
// frontend/ is the only editable source. www/ is a disposable build output.
const fs=require('fs'), path=require('path');
const {sourceManifest,verify}=require('./verify-native-bundle');
const root=path.resolve(__dirname,'..'), source=path.join(root,'frontend'), output=path.join(root,'www');
const manifest=sourceManifest(); // validate source BEFORE replacing generated output
if (path.dirname(output)!==root || path.basename(output)!=='www') throw new Error('Unsafe output path');
fs.rmSync(output,{recursive:true,force:true});
fs.cpSync(source,output,{recursive:true});
fs.writeFileSync(path.join(output,'build-manifest.json'),JSON.stringify(manifest));
verify(false);
