import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const root=dirname(dirname(fileURLToPath(import.meta.url)));
const {htmlPreviewDocument}=await import('../src/work-surface/html-preview.js');

test('shared Markdown reader renders content without executable links or external images',async()=>{
 const folder=await mkdtemp(join(root,'.markdown-test-'));
 try{
  const bundled=await build({
   entryPoints:[join(root,'src/work-surface/MarkdownContent.jsx')],
   bundle:true,platform:'node',format:'esm',jsx:'automatic',packages:'external',write:false,
   plugins:[{name:'ignore-css',setup(plugin){
    plugin.onResolve({filter:/\.css$/},args=>({path:args.path,namespace:'empty-css'}));
    plugin.onLoad({filter:/.*/,namespace:'empty-css'},()=>({contents:'',loader:'js'}));
   }}],
  });
  const file=join(folder,'renderer.mjs');
  await writeFile(file,bundled.outputFiles[0].text);
  const {MarkdownContent,safeMarkdownHref}=await import(pathToFileURL(file));
  assert.equal(safeMarkdownHref('javascript:alert(1)'),null);
  assert.equal(safeMarkdownHref('https://example.org/report'),'https://example.org/report');
  const body='# 현장 기록\n\n## 확인 사항\n\n- **표면** 확인\n\n| 항목 | 값 |\n| --- | --- |\n| 조명 | 정면 |\n\n[안전](https://example.org) [위험](javascript:alert(1)) ![외부 이미지](https://example.org/x.png) <script>alert(1)</script>';
  const html=renderToStaticMarkup(React.createElement(MarkdownContent,{body,title:'현장 기록',headingLevel:4}));
  assert.doesNotMatch(html,/<h4>현장 기록<\/h4>/);
  assert.match(html,/<h5>확인 사항<\/h5>/);
  assert.match(html,/<table>/);
  assert.match(html,/<strong>표면<\/strong>/);
  assert.match(html,/<a href="https:\/\/example.org"/);
  assert.doesNotMatch(html,/javascript:|<img|<script>/);
  assert.match(html,/외부 이미지/);
  const local=renderToStaticMarkup(React.createElement(MarkdownContent,{
   body:'![표지](./assets/banner.png)',
   resolveImageSrc:src=>src==='./assets/banner.png'?'/api/flow/files/content?workspaceId=workspace&amp;path=banner.png':null,
  }));
  assert.match(local,/<img src="\/api\/flow\/files\/content/);
  assert.match(local,/alt="표지"/);
 }finally{await rm(folder,{recursive:true,force:true});}
});

test('HTML preview keeps document styles but blocks active and external content',()=>{
 const html=htmlPreviewDocument('<!doctype html><html><head><style>h1{color:red}</style></head><body><h1>화면</h1><script>alert(1)</script></body></html>');
 assert.ok(html.startsWith('<!doctype html><meta http-equiv="Content-Security-Policy"'));
 assert.match(html,/default-src 'none'/);
 assert.match(html,/script-src 'none'/);
 assert.match(html,/img-src data:/);
 assert.match(html,/style-src 'unsafe-inline'/);
 assert.match(html,/<h1>화면<\/h1>/);
 assert.ok(htmlPreviewDocument('').startsWith('<!doctype html><meta http-equiv="Content-Security-Policy"'));
 assert.equal(htmlPreviewDocument(null),'');
});



test('shared file viewers and the composer extend without format-specific add menus',async()=>{
 const folder=await mkdtemp(join(root,'.file-view-test-'));
 try{
  const bundled=await build({
   entryPoints:[join(root,'src/work-surface/index.js')],bundle:true,platform:'node',format:'cjs',jsx:'automatic',external:['react','react-dom'],write:false,
   plugins:[{name:'ignore-css',setup(plugin){
    plugin.onResolve({filter:/\.css$/},args=>({path:args.path,namespace:'empty-css'}));
    plugin.onLoad({filter:/.*/,namespace:'empty-css'},()=>({contents:'',loader:'js'}));
   }}],
  });
  const file=join(folder,'renderer.cjs');await writeFile(file,bundled.outputFiles[0].text);
  const {FileBlock,CompositionEditor,EditorLayout,SurfaceHeader,workspaceFilePresentation,stackComposition}=await import(pathToFileURL(file));
  const render=(path,context={})=>renderToStaticMarkup(React.createElement(FileBlock,{
   block:{id:'file',kind:'file',content:{name:path,href:'/api/flow/files/content?workspaceId=workspace&path='+path}},context,
  }));
  assert.match(render('photo.png'),/<img[^>]*alt="photo.png"/);
  assert.match(render('meeting.m4a'),/<audio/);
  assert.match(render('movie.mp4'),/<video/);
  assert.match(render('report.pdf'),/PDF를 여는 중/);
  assert.match(render('memo.md'),/파일을 여는 중/);
  assert.doesNotMatch(render('model.cad'),/ws-file-preview/);
  const extended=render('model.cad',{
   resolveFilePresentation:path=>path.endsWith('.cad')?{viewer:'model',label:'도면'}:workspaceFilePresentation(path),
   filePreviewRenderers:{model:()=>React.createElement('div',{'data-viewer':'model'},'도면 보기')},
  });
  assert.match(extended,/data-viewer="model"/);
  const menu=renderToStaticMarkup(React.createElement(CompositionEditor,{
   composition:stackComposition([{id:'text',kind:'text',content:{paragraphs:['본문']}}]),onChange:()=>{},onPick:()=>{},onAddFile:()=>{},
  }));
  assert.match(menu,/aria-expanded="true"/);
  assert.match(menu,/>파일 추가</);assert.match(menu,/>내용 추가</);
  assert.doesNotMatch(menu,/>영상 선택<|>오디오 선택<|추가할 콘텐츠/);
  const composition=stackComposition([{id:'first',kind:'text',content:{heading:'첫 내용',paragraphs:['본문']}},{id:'second',kind:'text',content:{heading:'다음 내용',paragraphs:['내용']}}]);
  const initial=JSON.stringify(composition);
  const editor=renderToStaticMarkup(React.createElement(CompositionEditor,{composition,onChange:()=>{throw new Error('view changed content')},onPick:()=>{}}));
  assert.equal((editor.match(/aria-expanded="true"/g)||[]).length,0);
  assert.equal((editor.match(/aria-expanded="false"/g)||[]).length,2);
  assert.doesNotMatch(editor,/배치 조정|열 너비|윗줄로 이동|순서 올리기|순서 내리기|다음 줄로/);
  assert.doesNotMatch(menu,/배치 조정|열 너비|윗줄로 이동|순서 올리기|순서 내리기|다음 줄로/);
  assert.ok(editor.indexOf('heading-first')<editor.indexOf('heading-second'));
  assert.match(editor,/aria-controls="[^"]+fields-first"/);
  assert.match(editor,/aria-labelledby="[^"]+heading-second" hidden/);
  assert.equal(JSON.stringify(composition),initial);
  const frame=renderToStaticMarkup(React.createElement(EditorLayout,{preview:'표시 내용'},'입력 내용'));
  assert.match(frame,/flow-editor-fields/);assert.match(frame,/aria-label="미리보기"/);
  assert.ok(frame.indexOf('입력 내용')<frame.indexOf('표시 내용'));
  const heading=renderToStaticMarkup(React.createElement(SurfaceHeader,{title:React.createElement('h2',null,'작업 제목'),meta:'자료',actions:React.createElement('button',null,'배치')}));
  assert.match(heading,/<h2>작업 제목<\/h2>/);assert.match(heading,/flow-surface-heading-actions/);

 }finally{await rm(folder,{recursive:true,force:true});}
});
