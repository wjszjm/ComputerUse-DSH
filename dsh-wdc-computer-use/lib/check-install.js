import {readFileSync, realpathSync, statSync} from 'node:fs';
import path from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {pluginName, versionLabel} from './version.js';

function readJson(file) {
  try {
    return {value:JSON.parse(readFileSync(file, 'utf8').replace(/^\uFEFF/u, '')), state:'read'};
  } catch (error) {
    return {state:error.code === 'ENOENT' ? 'missing' : 'unreadable'};
  }
}

function packageVersion(manifest) {
  return manifest?.name === pluginName ? versionLabel(manifest.version) : null;
}

function isFile(file) {
  try { return statSync(file,{throwIfNoEntry:false})?.isFile() === true; }
  catch { return false; }
}

function processState(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return 'unknown';
  try {
    process.kill(pid, 0);
    return 'present';
  } catch (error) {
    return error.code === 'ESRCH' ? 'absent' : 'unknown';
  }
}

function sameDirectory(a, b) {
  if (typeof a !== 'string') return false;
  try {
    const dirs = [a, b].map(dir => realpathSync(dir));
    return process.platform === 'win32' ? dirs[0].toLowerCase() === dirs[1].toLowerCase() : dirs[0] === dirs[1];
  } catch {
    return false;
  }
}

export function checkInstallation(profileDir, statusFile) {
  const profile = readJson(path.join(profileDir, 'package.json'));
  const installedDir = path.join(profileDir, 'node_modules', pluginName);
  const installed = readJson(path.join(installedDir, 'package.json'));
  const dependency = profile.value?.dependencies?.[pluginName];
  const bundles = profile.value?.dsh?.profile?.bundles;
  const bundleCount = Array.isArray(bundles) ? bundles.filter(name => name === pluginName).length : 0;
  const localDependency = typeof dependency === 'string' && dependency.startsWith('file:');
  let source = {state:'not-applicable'};
  if (localDependency) {
    try {
      const specifier = dependency.slice(5);
      if (!specifier) throw new Error('Empty file dependency');
      const dir = specifier.startsWith('//') ? fileURLToPath(dependency) : path.resolve(profileDir, specifier);
      source = readJson(path.join(dir, 'package.json'));
    } catch {
      source = {state:'unreadable'};
    }
  }

  const status = readJson(statusFile);
  const snapshot = status.value;
  const versions = snapshot?.versions;
  const snapshotVersion = versionLabel(versions?.nodePlugin);
  const installedVersion = packageVersion(installed.value);
  const sourceVersion = packageVersion(source.value);
  const issues = [];
  if (profile.state !== 'read') issues.push('profile_manifest_unavailable');
  if (bundleCount !== 1) issues.push(bundleCount === 0 ? 'bundle_missing' : 'bundle_duplicate');
  if (typeof dependency !== 'string' || !dependency.trim()) issues.push('dependency_missing');
  if (!installedVersion) issues.push('installed_package_unavailable');
  if (installedVersion && !isFile(path.join(installedDir,'cordis.patch.yml'))) {
    issues.push('plugin_configuration_missing');
  }
  if (localDependency && !sourceVersion) issues.push('source_package_unavailable');
  if (sourceVersion && installedVersion && sourceVersion !== installedVersion) issues.push('source_version_mismatch');

  let runtimeState = status.state;
  if (status.state === 'read') {
    if (!snapshotVersion) runtimeState = 'unversioned';
    else if (!sameDirectory(versions.pluginDirectory, installedDir)) runtimeState = 'different_package';
    else if (snapshot.stage === 'disposed') runtimeState = 'disposed';
    else if (snapshot.stage === 'registered' && snapshot.ok === true) runtimeState = 'registered';
    else runtimeState = 'not_registered';
  }
  const reportedBy = processState(snapshot?.pid);
  if (runtimeState === 'registered' && reportedBy === 'absent') runtimeState = 'stale';
  if (runtimeState === 'registered' && reportedBy === 'unknown') runtimeState = 'process_unverified';
  if (runtimeState !== 'registered') issues.push('runtime_' + runtimeState);
  if (runtimeState === 'registered' && installedVersion && installedVersion !== snapshotVersion) issues.push('runtime_version_mismatch');

  const python = runtimeState === 'registered' ? versions.pythonService : null;
  const catalog = ['matched', 'mismatch'].includes(python?.catalog) ? python.catalog : 'unchecked';
  if (catalog === 'mismatch') issues.push('python_catalog_mismatch');
  return {
    ok:issues.length === 0,
    bundleCount,
    dependency:localDependency ? 'file' : typeof dependency === 'string' && dependency.trim() ? 'other' : 'missing',
    sourceVersion,
    installedVersion,
    runtime:{state:runtimeState, reportedVersion:snapshotVersion, process:reportedBy},
    python:{lastHandshakeVersion:versionLabel(python?.version), catalog},
    capabilities:runtimeState === 'registered' ? {
      tools:snapshot.compatibility?.tools === true,
      attachments:snapshot.compatibility?.attachments === true,
      approval:snapshot.compatibility?.approval === true,
      permissionPresets:snapshot.permissionIntegration?.hostPresetsAvailable === true,
      directCommands:snapshot.permissionIntegration?.directCommandsAvailable === true,
    } : null,
    issues,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    const {values} = parseArgs({options:{profile:{type:'string'}, status:{type:'string'}}});
    if (!values.profile || !values.status) throw new Error('Missing arguments');
    const result = checkInstallation(values.profile, values.status);
    console.log(JSON.stringify(result, null, 2));
    if (!result.ok) process.exitCode = 1;
  } catch {
    console.error('Usage: node lib/check-install.js --profile <DSH profile directory> --status <plugin statusPath>');
    process.exitCode = 2;
  }
}
