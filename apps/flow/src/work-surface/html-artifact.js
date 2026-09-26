export const HTML_ASSET=/^\/api\/flow\/assets\/([a-f0-9]{64})\.(png|jpg|webp|gif|mp3|wav|ogg|mp4|webm|m4a|aac|pdf)$/;
export const HTML_LIMIT=2*1024*1024;
export function validHtmlArtifact(artifact){
 return typeof artifact?.html==='string'&&artifact.html.trim().length>0&&new TextEncoder().encode(artifact.html).length<=HTML_LIMIT&&
  Array.isArray(artifact.assets)&&artifact.assets.length<=24&&
  artifact.assets.every(a=>a&&typeof a.name==='string'&&/^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/.test(a.name)&&!a.name.split('/').some(p=>!p||p==='..'||p==='.')&&typeof a.src==='string'&&HTML_ASSET.test(a.src))&&
  new Set(artifact.assets.map(a=>a.name)).size===artifact.assets.length;
}
export const artifactPolicy="default-src 'none'; script-src 'unsafe-inline'; connect-src 'none'; img-src data: blob:; media-src data: blob:; font-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-src 'none'; object-src 'none'; worker-src 'none'";
const json=value=>JSON.stringify(value).replaceAll('<','\\u003c');
function preparedDocument(html,assets){
 const doc=new DOMParser().parseFromString(html,'text/html');
 doc.querySelectorAll('base,meta[http-equiv],script[src],link,iframe,object,embed').forEach(node=>node.remove());
 const policy=doc.createElement('meta');policy.httpEquiv='Content-Security-Policy';policy.content=artifactPolicy;doc.head.prepend(policy);
 doc.documentElement.lang=doc.documentElement.lang||'ko';
 const mapped=new Map(Object.entries(assets));
 for(const element of doc.querySelectorAll('[src],[poster],[href]')){
  for(const attr of ['src','poster','href']){
   const value=element.getAttribute(attr);
   if(mapped.has(value))element.setAttribute(attr,mapped.get(value));
  }
 }
 doc.querySelectorAll('[srcset]').forEach(node=>{
  const value=node.getAttribute('srcset');
  if(!value.includes('data:'))node.setAttribute('srcset',value.split(',').map(candidate=>{const parts=candidate.trim().split(/\s+/);return [mapped.get(parts[0])||parts[0],...parts.slice(1)].join(' ')}).join(', '));
 });
 const replaceAsset=value=>value.replace(/url\(\s*(['"]?)([^)'"]+)\1\s*\)/g,(match,quote,name)=>mapped.has(name)?'url("'+mapped.get(name)+'")':match);
 doc.querySelectorAll('style').forEach(node=>node.textContent=replaceAsset(node.textContent));
 doc.querySelectorAll('[style]').forEach(node=>node.setAttribute('style',replaceAsset(node.getAttribute('style'))));
 return doc;
}
export function standaloneHtml(html,assets){
 const doc=preparedDocument(html,assets);
 return '<!doctype html>'+doc.documentElement.outerHTML;
}
export function htmlArtifactDocument(html,assets,channel,theme='light'){
 const doc=preparedDocument(html,assets);
 doc.documentElement.dataset.theme=theme;
 const script=doc.createElement('script');
 script.textContent='(()=>{const channel='+json(channel)+';let previous=0,previousHeading;const size=()=>{const hasHeading=[...document.querySelectorAll("h1,[role=heading]")].some(e=>(e.tagName==="H1"||e.getAttribute("aria-level")==="1")&&e.textContent.trim()&&e.getClientRects().length&&getComputedStyle(e).visibility!=="hidden");const height=Math.ceil(Math.max(document.body.scrollHeight,document.body.getBoundingClientRect().height));if(height!==previous||hasHeading!==previousHeading){previous=height;previousHeading=hasHeading;parent.postMessage({type:"flow-artifact-size",channel,height,hasHeading},"*")}};new ResizeObserver(size).observe(document.body);new MutationObserver(size).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:["hidden","style","class"]});addEventListener("load",size);addEventListener("message",e=>{if(e.source===parent&&e.data?.channel===channel&&["light","dark"].includes(e.data.theme)){document.documentElement.dataset.theme=e.data.theme;size()}});size()})();';
 doc.body.append(script);
 return '<!doctype html>'+doc.documentElement.outerHTML;
}
// A trusted outer frame blocks child navigation. srcdoc needs no network source;
// the artifact cannot replace its own frame with a network URL.
export function htmlArtifactContainer(document,channel){
 const policy=artifactPolicy;
 const script='(()=>{const channel='+json(channel)+';const frame=document.querySelector("iframe");frame.srcdoc='+json(document)+';addEventListener("message",e=>{if(e.data?.channel!==channel)return;if(e.source===frame.contentWindow&&e.data.type==="flow-artifact-size"&&Number.isFinite(e.data.height))parent.postMessage({type:e.data.type,channel,height:e.data.height,hasHeading:e.data.hasHeading===true},"*");else if(e.source===parent&&["light","dark"].includes(e.data.theme))frame.contentWindow.postMessage({channel,theme:e.data.theme},"*")})})();';
 return '<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="'+policy+'"><style>html,body,iframe{margin:0;width:100%;height:100%;border:0;display:block;overflow:hidden}</style></head><body><iframe title="작업물 내용" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe><script>'+script+'</script></body></html>';
}
