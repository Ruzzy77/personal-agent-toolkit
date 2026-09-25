#!/usr/bin/env node
import {readFile,writeFile,rename,mkdir} from 'node:fs/promises';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..'),index=join(root,'dist/client/index.html');
let html=await readFile(index,'utf8');
const script=html.match(/<script type="module" crossorigin src="([^"]+)"\s*><\/script>/);
const style=html.match(/<link rel="stylesheet" crossorigin href="([^"]+)">/);
if(!script||!style)throw Error('독립 HTML에 필요한 화면 자산을 찾지 못했습니다.');
const js=(await readFile(join(root,'dist/client',script[1].replace(/^\//,'')),'utf8')).replace(/<\/script/gi,'<\\/script');
const css=(await readFile(join(root,'dist/client',style[1].replace(/^\//,'')),'utf8')).replace(/<\/style/gi,'<\\/style');
html=html.replace(script[0],'<script type="module">'+js+'</script>').replace(style[0],'<style>'+css+'</style>');
const output=process.env.TOOLKIT_FLOW_EXPORT_HTML;if(!output)throw Error('TOOLKIT_FLOW_EXPORT_HTML is required');
const target=resolve(output),temp=target+'.tmp';
await mkdir(dirname(target),{recursive:true});
await writeFile(temp,html);
await rename(temp,target);
console.log('Updated '+target);
