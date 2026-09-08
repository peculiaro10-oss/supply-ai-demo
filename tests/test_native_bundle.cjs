// Negative bundle tests operate ONLY on a disposable fixture copy.
'use strict';
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert'),cp=require('child_process');
const root=path.resolve(__dirname,'..'),tmp=fs.mkdtempSync(path.join(os.tmpdir(),'cauldra-bundle-test-'));
function run(){return cp.spawnSync(process.execPath,['scripts/verify-native-bundle.js'],{cwd:tmp,encoding:'utf8'});}
try{
 for(const dir of ['frontend','www','scripts','android/app/src/main'])fs.cpSync(path.join(root,dir),path.join(tmp,dir),{recursive:true});
 fs.copyFileSync(path.join(root,'android/app/build.gradle'),path.join(tmp,'android/app/build.gradle'));
 for(const f of ['package.json','package-lock.json','capacitor.config.json'])fs.copyFileSync(path.join(root,f),path.join(tmp,f));
 assert.equal(run().status,0,'Prepare the source bundle before running this test');
 for(const rel of ['frontend/index.html','frontend/js/app.js','www/css/base.css','android/app/src/main/assets/public/js/payments.js','www/build-manifest.json','capacitor.config.json']){
  const file=path.join(tmp,rel),original=fs.readFileSync(file);
  try{fs.appendFileSync(file,'\n ');if(rel.endsWith('.json'))fs.writeFileSync(file,'{}');assert.notEqual(run().status,0,rel+' drift not detected');}
  finally{fs.writeFileSync(file,original);}
 }
 const extra=path.join(tmp,'android/app/src/main/assets/public/stale.js');fs.writeFileSync(extra,'old');assert.notEqual(run().status,0);fs.unlinkSync(extra);
 const plugins=path.join(tmp,'android/app/src/main/assets/capacitor.plugins.json'),original=fs.readFileSync(plugins);fs.writeFileSync(plugins,'[]');assert.notEqual(run().status,0);fs.writeFileSync(plugins,original);
 assert.equal(run().status,0);console.log('PASS: source, www, Android, manifest, config, extra-file and plugin drift all fail nonzero; restored fixture passes.');
}finally{
 if(path.dirname(tmp)!==path.resolve(os.tmpdir())||!path.basename(tmp).startsWith('cauldra-bundle-test-'))throw new Error('Unsafe fixture cleanup');
 fs.rmSync(tmp,{recursive:true,force:true});
}
