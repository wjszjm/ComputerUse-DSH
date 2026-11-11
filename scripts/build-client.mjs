import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {TOOL_CATALOG} from '../dsh-wdc-computer-use/lib/tool-catalog.js';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const lib=path.join(root,'dsh-wdc-computer-use/lib');
// No third-party build dependency: the browser entry uses only injected services.
const policy=fs.readFileSync(path.join(lib,'client-policy.js'),'utf8').replace(/^export /gm,'');
const entry=fs.readFileSync(path.join(lib,'client-entry.js'),'utf8').replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'');
const bundle=`window.__ModuleLoader__.load({id:'dsh-wdc-computer-use',factory:()=>{\nconst TOOL_CATALOG=${JSON.stringify({tools:TOOL_CATALOG.tools.map(({name})=>({name}))})};\n${policy}\n${entry}\nreturn {inject,apply};\n}});\n`;
fs.writeFileSync(path.join(lib,'client.js'),bundle);
