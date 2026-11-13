import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {checkInstallation} from '../dsh-wdc-computer-use/lib/check-install.js';
import {pluginName, versionLabel} from '../dsh-wdc-computer-use/lib/version.js';

const patchName = 'cordis.patch.yml';
const manifestName = 'package.json';
const receiptName = 'plugin-config.json';
const readOnlyConfig = {
  read_only:true,allow_input:false,allow_shell:false,allow_launch:false,save_screenshots:false,audit_log:false,
};
const sourceFiles = [
  manifestName, 'README.md', 'lib/check-install.js', 'lib/client-entry.js', 'lib/client-policy.js',
  'lib/client.js', 'lib/failure-log.js', 'lib/feedback-session.js', 'lib/index.js',
  'lib/native-feedback-host.js', 'lib/permissions.js', 'lib/tool-catalog.js', 'lib/version.js',
  'lib/visual-feedback.js', 'lib/settings.js',
];
const digest = data => createHash('sha256').update(data).digest('hex');

function fail(code) {
  throw Object.assign(new Error(code), {code});
}

function entry(file) {
  const stat = fs.lstatSync(file, {throwIfNoEntry:false});
  if (stat?.isSymbolicLink()) fail('linked_path');
  return stat;
}

function read(file) {
  const stat = entry(file);
  if (!stat) return undefined;
  if (!stat.isFile() || stat.nlink !== 1) fail('unexpected_file');
  return fs.readFileSync(file);
}

function json(bytes) {
  return JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/u, ''));
}

function jsonBytes(value) {
  return Buffer.from(JSON.stringify(value, null, 2) + '\n');
}

function samePath(left, right) {
  return process.platform === 'win32' ? left.toLowerCase() === right.toLowerCase() : left === right;
}

function within(parent, child) {
  const relative = path.relative(parent, child);
  return relative === '' || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith('..' + path.sep));
}

function directory(dir) {
  if (!entry(dir)?.isDirectory()) fail('directory_unavailable');
  return fs.realpathSync(dir);
}

function inspectTree(dir) {
  for (const name of fs.readdirSync(dir)) {
    const child = path.join(dir, name);
    const stat = entry(child);
    if (stat.isDirectory()) inspectTree(child);
    else if (!stat.isFile() || stat.nlink !== 1) fail('unexpected_file');
  }
}

function dependencyDir(profile, spec) {
  if (typeof spec !== 'string' || !spec.startsWith('file:') || !spec.slice(5)) return null;
  return spec.startsWith('file://') ? fileURLToPath(spec) : path.resolve(profile, spec.slice(5));
}

function keepSettings(runtime, profile, python, existing) {
  const configFile = path.join(runtime, 'config.json');
  const receiptFile = path.join(runtime, receiptName);
  const prior = read(receiptFile);
  const record = prior ? json(prior) : null;
  if (record && (record.format !== 1 || typeof record.profile !== 'string' ||
      !samePath(record.profile, profile))) fail('runtime_owned_by_other_profile');
  if (record && (typeof record.patch !== 'string' || !record.patch.trim())) fail('invalid_runtime_receipt');
  const saved = record ? Buffer.from(record.patch, 'utf8') : undefined;
  if (saved && !existing && digest(saved) !== record.patchSha256) fail('invalid_runtime_receipt');
  const configOriginal = read(configFile);
  const savedConfig = typeof record?.pythonConfig === 'string' ? Buffer.from(record.pythonConfig,'utf8') : undefined;
  if (!configOriginal && savedConfig && digest(savedConfig) !== record.pythonConfigSha256) fail('invalid_runtime_receipt');
  const config = configOriginal ?? savedConfig;
  if (config) {
    const parsed = json(config);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) fail('invalid_python_configuration');
  }
  if (existing || saved) return {
    patch:existing ?? saved, prior, receiptFile, configFile, configOriginal,
    config:config ?? jsonBytes(readOnlyConfig),
  };
  if (!python) fail('python_required_for_new_installation');
  if (configOriginal) fail('existing_python_config_requires_preserved_patch');
  const patch = jsonBytes([{insert:[{
    id:'wdc-computer-use', name:pluginName,
    config:{
      python, cwd:runtime, statusPath:path.join(runtime, 'status.json'), stopFile:path.join(runtime, 'stop.flag'),
      permissionMode:'ask',
    },
  }]}]);
  return {patch, prior, receiptFile, configFile, configOriginal, config:jsonBytes(readOnlyConfig), fresh:true};
}

function updateInterpreter(settings, python) {
  if (!python || settings.fresh) return;
  // A new interpreter is an explicit upgrade. Preserve the surrounding
  // configuration and reject unfamiliar YAML instead of silently replacing it.
  const raw = settings.patch.toString('utf8');
  let document;
  try { document = JSON.parse(raw); } catch { /* YAML bundle from older versions. */ }
  if (Array.isArray(document)) {
    const rows=document.flatMap(item=>Array.isArray(item?.insert)?item.insert:[]);
    const matches=rows.filter(row=>row.id==='wdc-computer-use'&&row.name===pluginName);
    if (matches.length !== 1 || !matches[0].config || typeof matches[0].config !== 'object') fail('invalid_plugin_configuration');
    matches[0].config.python=python;
    settings.patch=jsonBytes(document);
    return;
  }
  const lines = raw.split(/(?<=\n)/u);
  const candidates=lines.map((line,index)=>/^\s+python:\s*(?:["'][^\r\n]+|[^\s|>][^\r\n]*)\r?\n?$/u.test(line)?index:-1).filter(index=>index>=0);
  if (candidates.length!==1 || !/^\s+name:\s+['"]?dsh-wdc-computer-use['"]?\s*$/mu.test(raw)) {
    fail('unsupported_python_configuration');
  }
  const index=candidates[0];
  const original=lines[index];
  const prefix=/^\s+python:\s*/u.exec(original)[0];
  lines[index]=prefix+JSON.stringify(python)+(original.endsWith('\r\n')?'\r\n':original.endsWith('\n')?'\n':'');
  settings.patch=Buffer.from(lines.join(''));
}

function checkPython(python) {
  const clean = spawnSync(python, ['-I','-B','-c',
    'import sys, importlib.util; assert sys.version_info >= (3, 9); assert all(importlib.util.find_spec(x) for x in ("wdc", "mcp", "mss", "PIL", "pyautogui", "pyperclip"))',
  ], {windowsHide:true, encoding:'utf8', timeout:15000, stdio:'ignore'});
  if (clean.status !== 0) fail('python_environment_unavailable');
}

function checkNode() {
  const [major,minor] = process.versions.node.split('.').map(Number);
  if (major < 22 || (major === 22 && minor < 19)) fail('node_version_unsupported');
}

function requireDesktopStopped() {
  if (process.platform !== 'win32') fail('windows_required');
  const shell = path.join(process.env.SystemRoot || 'C:\\Windows','System32','WindowsPowerShell','v1.0','powershell.exe');
  const checked = spawnSync(shell, ['-NoProfile','-NonInteractive','-Command',
    "if (Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue) { exit 1 }",
  ], {windowsHide:true, timeout:10000, stdio:'ignore'});
  if (checked.status !== 0) fail(checked.status === 1 ? 'dsh_running' : 'dsh_process_check_failed');
}

export function prepareInstall({mode, profileDir, sourceDir, runtimeDir, pythonPath}) {
  if (!['install', 'upgrade', 'repair'].includes(mode)) fail('invalid_mode');
  const profile = directory(profileDir);
  const source = directory(sourceDir);
  const runtime = directory(runtimeDir);
  if (within(profile,runtime) || within(runtime,profile) || within(source,runtime) || within(runtime,source) ||
      within(source,profile) || within(profile,source)) fail('overlapping_directories');
  const sourcePackage = json(read(path.join(source, manifestName)));
  const version = versionLabel(sourcePackage.version);
  if (sourcePackage.name !== pluginName || !version ||
      sourcePackage.dsh?.bundle?.patch !== './cordis.patch.yml') fail('invalid_source_package');
  directory(path.join(source,'lib'));
  const files = new Map(sourceFiles.map(file => [file, read(path.join(source, file))]));
  if ([...files.values()].some(data => !data?.length)) fail('incomplete_source_package');
  const manifestFile = path.join(profile, manifestName);
  const manifestOriginal = read(manifestFile);
  if (!manifestOriginal) fail('profile_manifest_missing');
  const manifest = json(manifestOriginal);
  const bundles = manifest?.dsh?.profile?.bundles;
  const dependencies = manifest?.dependencies;
  if (!Array.isArray(bundles) || (dependencies !== undefined &&
      (!dependencies || typeof dependencies !== 'object' || Array.isArray(dependencies)))) fail('invalid_profile_manifest');
  const nodeModules = path.join(profile,'node_modules');
  if (entry(nodeModules) && !entry(nodeModules).isDirectory()) fail('invalid_node_modules');
  const destination = path.join(nodeModules,pluginName);
  const installedStat = entry(destination);
  let installedVersion = null;
  if (installedStat) {
    if (!installedStat.isDirectory()) fail('invalid_installed_package');
    inspectTree(destination);
    const installed = read(path.join(destination,manifestName));
    const oldPackage = installed ? json(installed) : null;
    installedVersion = versionLabel(oldPackage?.version);
    if (oldPackage && (oldPackage.name !== pluginName || !installedVersion)) fail('invalid_installed_package');
    if (!oldPackage && mode !== 'repair') fail('invalid_installed_package');
  }
  if (mode === 'install' && installedStat) fail('already_installed');
  if (mode === 'upgrade' && !installedVersion) fail('installed_package_unavailable');
  if (mode === 'repair' && installedVersion && installedVersion !== version) fail('repair_requires_matching_source');
  const existingDependency = dependencies?.[pluginName];
  const dependencyPath = dependencyDir(profile, existingDependency);
  const resolvedDependency = dependencyPath && fs.existsSync(dependencyPath) ? fs.realpathSync(dependencyPath) : dependencyPath;
  if (existingDependency !== undefined && (!resolvedDependency || !samePath(resolvedDependency,source))) {
    fail('different_dependency_source');
  }
  const patchFile = path.join(destination,patchName);
  const existingPatch = read(patchFile);
  if (existingPatch && !existingPatch.toString('utf8').trim()) fail('empty_installed_patch');
  const bundledPython = path.join(path.dirname(source),'.venv','Scripts','python.exe');
  const python = pythonPath ? path.resolve(pythonPath) :
    mode === 'install' && fs.existsSync(bundledPython) ? bundledPython : null;
  if (python && !fs.statSync(python,{throwIfNoEntry:false})?.isFile()) fail('python_environment_unavailable');
  const settings = keepSettings(runtime,profile,python,existingPatch);
  if (mode !== 'install' && settings.fresh) fail('preserved_plugin_configuration_required');
  if (mode === 'upgrade') updateInterpreter(settings,python);
  files.set(patchName,settings.patch);
  const oldLog = read(path.join(destination,'failures.log'));
  if (oldLog) files.set('failures.log',oldLog);
  const backup = jsonBytes({
    format:1, profile, version, patch:settings.patch.toString('utf8'), patchSha256:digest(settings.patch),
    ...(settings.config ? {pythonConfig:settings.config.toString('utf8'), pythonConfigSha256:digest(settings.config)} : {}),
  });
  const bundleCount = bundles.filter(name => name === pluginName).length;
  const updates = [];
  if (bundleCount !== 1) updates.push('normalize_bundle_registration');
  if (!existingDependency) updates.push('restore_file_dependency');
  if (!existingPatch) updates.push('restore_plugin_configuration');
  if (!installedStat || [...files].some(([name,data]) => !read(path.join(destination,name))?.equals(data))) {
    updates.push('sync_plugin_files');
  }
  if (!settings.prior?.equals(backup)) updates.push('save_repair_configuration');
  if (settings.config && !settings.configOriginal) {
    updates.push(settings.fresh ? 'create_read_only_python_configuration' : 'restore_python_configuration');
  }

  if (bundleCount !== 1 || !existingDependency) {
    let found = false;
    manifest.dsh.profile.bundles = bundles.filter(name => {
      if (name !== pluginName) return true;
      if (found) return false;
      found = true;
      return true;
    });
    if (!found) manifest.dsh.profile.bundles.push(pluginName);
    manifest.dependencies = {...dependencies,[pluginName]:existingDependency ?? 'file:' + source.replaceAll('\\','/')};
  }
  return {
    preview:{mode,version,installedVersion,updates},
    profile,source,nodeModules,destination,files,installedStat,manifestFile,manifestOriginal,
    manifestNext:bundleCount === 1 && existingDependency ? manifestOriginal : jsonBytes(manifest),
    settings,backup,python,existingPatch,oldLog,
  };
}

export function applyInstall(plan, verify = checkInstallation) {
  if (!plan.preview.updates.length) return plan.preview;
  const {profile,nodeModules,destination,files,manifestFile,manifestOriginal,manifestNext,settings} = plan;
  const lock = path.join(profile,'.computeruse-dsh-install.lock');
  const fd = fs.openSync(lock,'wx',0o600);
  let temp, staged, oldDir, installedMoved=false, installedNew=false, manifestChanged=false;
  let receiptChanged=false, configCreated=false, createdModules=false, rollbackFailed=false;
  try {
    if (!read(manifestFile)?.equals(manifestOriginal) ||
        (entry(nodeModules) && !entry(nodeModules).isDirectory()) ||
        !sameContents(read(path.join(destination,patchName)),plan.existingPatch) ||
        !sameContents(read(path.join(destination,'failures.log')),plan.oldLog) ||
        !sameContents(read(settings.receiptFile),settings.prior) ||
        !sameContents(read(settings.configFile),settings.configOriginal) ||
        !sameEntry(entry(destination),plan.installedStat)) fail('installation_changed_since_preview');
    if (!entry(nodeModules)) {
      fs.mkdirSync(nodeModules);
      createdModules=true;
    } else directory(nodeModules);
    temp=fs.mkdtempSync(path.join(profile,'.computeruse-dsh-stage-'));
    fs.writeFileSync(path.join(temp,'previous-profile.json'),manifestOriginal,{flag:'wx',mode:0o600});
    staged=path.join(temp,'candidate');
    oldDir=path.join(temp,'previous');
    if (plan.installedStat) {
      inspectTree(destination);
      fs.cpSync(destination,staged,{recursive:true,errorOnExist:true,force:false});
    }
    fs.mkdirSync(path.join(staged,'lib'),{recursive:true});
    for (const [name,data] of files) fs.writeFileSync(path.join(staged,name),data,{mode:0o600});
    if (settings.config && !settings.configOriginal) {
      const configFd=fs.openSync(settings.configFile,'wx',0o600);
      configCreated=true;
      try {
        fs.writeFileSync(configFd,settings.config);
      } finally {fs.closeSync(configFd);}
    }
    if (plan.installedStat) {
      fs.renameSync(destination,oldDir);
      installedMoved=true;
    }
    fs.renameSync(staged,destination);
    installedNew=true;
    if (!manifestNext.equals(manifestOriginal)) {
      replaceFile(manifestFile,manifestNext,temp);
      manifestChanged=true;
    }
    for (const [name,data] of files) {
      if (!read(path.join(destination,name))?.equals(data)) fail('installed_file_mismatch');
    }
    const report=verify(profile,path.join(path.dirname(settings.receiptFile),'status.json'));
    if (report.installedVersion !== plan.preview.version || report.sourceVersion !== plan.preview.version ||
        report.bundleCount !== 1 || report.dependency !== 'file' ||
        report.issues?.includes('plugin_configuration_missing')) fail('installation_verification_failed');
    if (!settings.prior?.equals(plan.backup)) {
      replaceFile(settings.receiptFile,plan.backup);
      receiptChanged=true;
    }
    return plan.preview;
  } catch (error) {
    try {
      if (receiptChanged) {
        if (settings.prior) replaceFile(settings.receiptFile,settings.prior);
        else fs.unlinkSync(settings.receiptFile);
      }
      if (manifestChanged) replaceFile(manifestFile,manifestOriginal,temp);
      if (installedNew) fs.renameSync(destination,staged);
      if (installedMoved) fs.renameSync(oldDir,destination);
      if (configCreated) fs.unlinkSync(settings.configFile);
      if (createdModules) fs.rmdirSync(nodeModules);
    } catch {
      rollbackFailed=true;
      fail('rollback_incomplete');
    }
    throw error;
  } finally {
    try {
      if (!samePath(fs.realpathSync(profile),profile)) fail('profile_changed');
      if (temp && !rollbackFailed) {
        if (path.dirname(fs.realpathSync(temp)) !== profile || !path.basename(temp).startsWith('.computeruse-dsh-stage-')) {
          fail('unexpected_staging_directory');
        }
        inspectTree(temp);
        fs.rmSync(temp,{recursive:true});
      }
    } finally {
      fs.closeSync(fd);
      fs.unlinkSync(lock);
    }
  }
}

function sameContents(left,right) {
  return left === undefined ? right === undefined : right !== undefined && left.equals(right);
}

function sameEntry(left,right) {
  return left === undefined ? right === undefined : right !== undefined &&
    left.dev === right.dev && left.ino === right.ino && left.mtimeMs === right.mtimeMs;
}

function replaceFile(file,data,stage) {
  const parent = stage ?? fs.mkdtempSync(path.join(path.dirname(file),'.computeruse-dsh-write-'));
  const pending = path.join(parent,'next.json');
  try {
    fs.writeFileSync(pending,data,{flag:'wx',mode:0o600});
    if (!read(pending)?.equals(data)) fail('staged_file_mismatch');
    fs.renameSync(pending,file);
  } finally {
    if (entry(pending)) fs.unlinkSync(pending);
    if (!stage) fs.rmdirSync(parent);
  }
}

function run() {
  checkNode();
  const {values} = parseArgs({options:{
    mode:{type:'string'},profile:{type:'string'},runtime:{type:'string'},source:{type:'string'},
    python:{type:'string'},apply:{type:'boolean'},
  }});
  if (!values.mode || !values.profile || !values.runtime) fail('missing_arguments');
  const sourceDir=values.source ?? fileURLToPath(new URL('../dsh-wdc-computer-use',import.meta.url));
  const plan=prepareInstall({mode:values.mode,profileDir:values.profile,runtimeDir:values.runtime,sourceDir,pythonPath:values.python});
  if (values.apply && plan.preview.updates.length) {
    requireDesktopStopped();
    if (plan.python) checkPython(plan.python);
    applyInstall(plan);
  }
  console.log(JSON.stringify({...plan.preview,applied:values.apply === true},null,2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {run();}
  catch (error) {
    console.error(JSON.stringify({ok:false,code:error.code ?? 'installation_failed'}));
    process.exitCode=1;
  }
}
