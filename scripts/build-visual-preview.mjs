import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const source=readFileSync(path.join(root,'dsh-wdc-computer-use/lib/visual-feedback.js'),'utf8').replace(/^export /gm,'');
const template=readFileSync(path.join(root,'dsh-wdc-computer-use/preview/visual-feedback.html'),'utf8');
const marker='/*__VISUAL_FEEDBACK_SOURCE__*/';
if(template.split(marker).length!==2)throw Error('Missing or repeated preview source marker');
const fragment=template.replace(marker,()=>source);
const output=path.join(root,'docs/visual-feedback-preview.html');
const standalone=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>电脑操作提示模拟预览</title><style>html{color-scheme:light dark}body{margin:0;padding:24px;background:light-dark(#f6f8fb,#171d26)}main{max-width:960px;margin:auto}@media(max-width:420px){body{padding:12px}}</style></head><body><main>${fragment}</main></body></html>`;
mkdirSync(path.dirname(output),{recursive:true});writeFileSync(output,standalone);
const index=process.argv.indexOf('--fragment');
if(index>=0){const target=path.resolve(process.argv[index+1]);mkdirSync(path.dirname(target),{recursive:true});writeFileSync(target,fragment);}
console.log(output);
