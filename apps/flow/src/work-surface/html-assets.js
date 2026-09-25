import {validHtmlArtifact,standaloneHtml} from './html-artifact.js';
const identity=value=>value;
export async function readHtmlAssets(artifact,resolveMediaUrl=identity,signal){
 if(!validHtmlArtifact(artifact))throw new Error('작업물 형식을 확인해 주세요.');
 const values=await Promise.all(artifact.assets.map(async item=>{
  const url=resolveMediaUrl(item.src);
  if(!url||!/^\/api\/flow(?:-media\/[a-z0-9._-]+)?\/assets\/[a-f0-9]{64}\.[a-z0-9]+$/.test(url))throw new Error('등록된 파일을 찾지 못했습니다.');
  const response=await fetch(url,{signal,credentials:'same-origin'});
  if(!response.ok)throw new Error('작업물에 필요한 파일을 열지 못했습니다.');
  const blob=await response.blob();
  if(blob.size>64*1024*1024)throw new Error('파일 크기가 표시 한도를 넘었습니다.');
  const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('파일을 읽지 못했습니다.'));reader.readAsDataURL(blob)});
  return [item.name,data];
 }));
 return Object.fromEntries(values);
}
export async function exportHtmlArtifact(artifact,resolveMediaUrl=identity){
 return standaloneHtml(artifact.html,await readHtmlAssets(artifact,resolveMediaUrl));
}
