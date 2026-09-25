export function initialFiles(refs){
 const source=id=>refs.find(r=>r.id===id).body;
 return [
  {id:'research-folder',path:'연구 발표',type:'directory'},
  {id:'research-input',path:'연구 발표/자료',type:'directory'},
  {id:'research-output',path:'연구 발표/결과물',type:'directory'},
  {id:'expression-folder',path:'표현',type:'directory'},
  {id:'work-output',path:'작업 결과',type:'directory'},
  {id:'observation-file',sourceId:'observation',path:'연구 발표/자료/현장 관찰 메모.md',type:'file',content:source('observation')},
  {id:'questions-file',sourceId:'questions',path:'연구 발표/자료/비교할 조건과 질문.md',type:'file',content:source('questions')},
  {id:'sample-data',path:'연구 발표/자료/비교 조건.csv',type:'file',content:'제품,촬영 조건,살펴볼 오류\n제품 A,정면 조명,표면 반사\n제품 A,측면 조명,경계 흐림\n제품 B,정면 조명,미세 흠집\n'},
  {id:'writing-file',sourceId:'writing',path:'표현/문장을 덜어내는 기준.txt',type:'file',content:source('writing')}
 ];
}
export const basename=path=>path.split('/').at(-1);
export const dirname=path=>path.split('/').slice(0,-1).join('/');
export function workspaceFiles(state){
 const files=[...(state.files||[])],paths=new Set(files.map(file=>file.path));
 for(const work of state.works) for(const artifact of work.artifacts.filter(a=>a.kind==='document')){
  const folder=work.id==='research'?'연구 발표/결과물/':'작업 결과/';
  const stem=(artifact.title.trim()||'제목 없는 문서').replace(/[\\/]/g,'-');
  let path=folder+stem+'.md',n=2;
  while(paths.has(path)){path=folder+stem+' ('+(n++)+').md'}
  paths.add(path);
  files.push({id:'work-file-'+artifact.id,workId:work.id,artifactId:artifact.id,type:'file',path,
   content:'# '+artifact.title+'\n\n'+artifact.blocks.map(b=>'## '+b.heading+'\n\n'+b.text).join('\n\n'),blocks:artifact.blocks});
 }
 return files;
}
export function fileSource(file){
 return {id:file.artifactId||file.workId||file.sourceId||'file:'+file.id,fileId:file.id,workId:file.workId,artifactId:file.artifactId,
  title:basename(file.path).replace(/\.[^.]+$/,''),path:file.path,kind:file.workId?'결과물':'자료',collection:'파일에서 보관',
  body:file.content||'',blocks:file.blocks};
}
export function folderEntries(files,path,query='',descending=false){
 return files.filter(f=>dirname(f.path)===path&&basename(f.path).toLocaleLowerCase().includes(query.toLocaleLowerCase()))
  .sort((a,b)=>(a.type===b.type?0:a.type==='directory'?-1:1)||(descending?-1:1)*basename(a.path).localeCompare(basename(b.path),'ko'));
}
export function folderError(files,path,name){
 const n=name.trim();
 if(!n)return '폴더 이름을 입력해 주세요.';
 if(/[\\/]/.test(n)||n==='.'||n==='..'||/[\u0000-\u001f]/.test(n))return '폴더 이름에는 경로 기호를 사용할 수 없습니다.';
 if(files.some(f=>f.path===(path?path+'/':'')+n))return '같은 이름의 파일이나 폴더가 있습니다.';
 return '';
}
export function fileKind(file){return file.type==='directory'?'폴더':({md:'Markdown',txt:'텍스트',csv:'CSV'})[basename(file.path).split('.').at(-1)]||'파일'}
export function fileSize(file){const n=new TextEncoder().encode(file.content||'').length;return file.type==='directory'?'—':n<1024?n+' B':(n/1024).toFixed(1)+' KB'}
