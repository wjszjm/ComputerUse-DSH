import metadata from '../package.json' with {type:'json'};

export const pluginName = metadata.name;
export const pluginVersion = metadata.version;

export function versionLabel(value) {
  return typeof value === 'string' && value.length <= 64 &&
    /^\d+\.\d+\.\d+(?:-[\w.-]+)?(?:\+[\w.-]+)?$/u.test(value) ? value : null;
}
