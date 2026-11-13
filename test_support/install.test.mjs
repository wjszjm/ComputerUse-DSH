import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {prepareInstall, applyInstall} from '../scripts/manage-install.mjs';

const packageSource = fileURLToPath(new URL('../dsh-wdc-computer-use',import.meta.url));
const name = 'dsh-wdc-computer-use';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(),'computeruse-install-test-'));
  t.after(() => {
    const parent=fs.realpathSync(os.tmpdir());
    const target=fs.realpathSync(root);
    assert.equal(path.dirname(target),parent);
    assert.match(path.basename(target),/^computeruse-install-test-/u);
    fs.rmSync(target,{recursive:true,force:true});
  });
  const profile = path.join(root,'profile');
  const runtime = path.join(root,'runtime');
  const destination = path.join(profile,'node_modules',name);
  const manifest = path.join(profile,'package.json');
  fs.mkdirSync(profile);
  fs.mkdirSync(runtime);
  const original = {dependencies:{other:'1.2.3'},dsh:{profile:{bundles:['other']}}};
  fs.writeFileSync(manifest,JSON.stringify(original));
  return {profile,runtime,destination,manifest,original,options:{profileDir:profile,runtimeDir:runtime,sourceDir:packageSource,pythonPath:process.execPath}};
}

test('installer previews, applies, and repairs an isolated profile preserving configuration', t => {
  const {options,manifest,runtime,destination,original} = fixture(t);
  const plan = prepareInstall({...options,mode:'install'});
  assert.equal(fs.existsSync(destination),false);
  assert.equal(fs.existsSync(path.join(runtime,'config.json')),false);
  assert.ok(plan.preview.updates.includes('normalize_bundle_registration'));
  applyInstall(plan);
  assert.equal(JSON.parse(fs.readFileSync(path.join(runtime,'config.json'))).read_only,true);
  const patch = path.join(destination,'cordis.patch.yml');
  const custom = fs.readFileSync(patch,'utf8').replace('"ask"','"read-only"');
  fs.writeFileSync(patch,custom);
  const customPython='{"read_only":true,"max_image_edge":900}\n';
  fs.writeFileSync(path.join(runtime,'config.json'),customPython);
  fs.writeFileSync(path.join(destination,'failures.log'),'retained test entry\n');
  const upgrade = prepareInstall({...options,mode:'upgrade',pythonPath:undefined});
  applyInstall(upgrade);
  fs.unlinkSync(patch);
  fs.unlinkSync(path.join(runtime,'config.json'));
  fs.unlinkSync(path.join(destination,'lib','index.js'));
  fs.writeFileSync(manifest,JSON.stringify(original));
  const repair = prepareInstall({...options,mode:'repair',pythonPath:undefined});
  applyInstall(repair);
  assert.equal(fs.readFileSync(patch,'utf8'),custom);
  assert.equal(fs.readFileSync(path.join(runtime,'config.json'),'utf8'),customPython);
  assert.equal(fs.readFileSync(path.join(destination,'failures.log'),'utf8'),'retained test entry\n');
  const result = JSON.parse(fs.readFileSync(manifest));
  assert.deepEqual(result.dsh.profile.bundles,['other',name]);
  assert.equal(result.dependencies.other,'1.2.3');
  assert.deepEqual(prepareInstall({...options,mode:'repair'}).preview.updates,[]);
});

test('failed post-install verification rolls back files and registration', t => {
  const {options,profile,manifest,runtime,destination} = fixture(t);
  const before = fs.readFileSync(manifest);
  const plan = prepareInstall({...options,mode:'install'});
  assert.throws(()=>applyInstall(plan,()=>({})), {code:'installation_verification_failed'});
  assert.deepEqual(fs.readFileSync(manifest),before);
  assert.equal(fs.existsSync(destination),false);
  assert.deepEqual(fs.readdirSync(runtime),[]);
  assert.deepEqual(fs.readdirSync(profile),['package.json']);
});

test('installer rejects overwriting an unrelated dependency and a changed profile', t => {
  const {options,manifest,original,destination} = fixture(t);
  const plan=prepareInstall({...options,mode:'install'});
  original.dependencies[name]='^1.1.0';
  fs.writeFileSync(manifest,JSON.stringify(original));
  assert.throws(()=>applyInstall(plan),{code:'installation_changed_since_preview'});
  assert.throws(()=>prepareInstall({...options,mode:'install'}),{code:'different_dependency_source'});
  assert.equal(fs.existsSync(destination),false);
});

test('failed upgrade restores the previous plugin bytes and its local settings', t => {
  const {options,profile,runtime,destination,manifest} = fixture(t);
  applyInstall(prepareInstall({...options,mode:'install'}));
  const pluginManifest=path.join(destination,'package.json');
  const previous=JSON.parse(fs.readFileSync(pluginManifest));
  previous.version='1.1.0';
  fs.writeFileSync(pluginManifest,JSON.stringify(previous));
  const patch=path.join(destination,'cordis.patch.yml');
  const custom=fs.readFileSync(patch,'utf8')+'# local setting\n';
  fs.writeFileSync(patch,custom);
  const profileBefore=fs.readFileSync(manifest);
  const receipt=path.join(runtime,'plugin-config.json');
  const receiptBefore=fs.readFileSync(receipt);
  const upgrade=prepareInstall({...options,mode:'upgrade',pythonPath:undefined});
  assert.throws(()=>applyInstall(upgrade,()=>({})),{code:'installation_verification_failed'});
  assert.equal(JSON.parse(fs.readFileSync(pluginManifest)).version,'1.1.0');
  assert.equal(fs.readFileSync(patch,'utf8'),custom);
  assert.deepEqual(fs.readFileSync(manifest),profileBefore);
  assert.deepEqual(fs.readFileSync(receipt),receiptBefore);
  assert.equal(fs.readdirSync(profile).some(name=>name.startsWith('.computeruse-dsh-')),false);
});
