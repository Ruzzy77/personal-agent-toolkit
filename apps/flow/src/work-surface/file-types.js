const fileTypes={
 pdf:'PDF',txt:'텍스트',md:'Markdown',markdown:'Markdown',csv:'CSV',tsv:'TSV',json:'JSON',
 yaml:'YAML',yml:'YAML',html:'HTML',htm:'HTML',css:'CSS',js:'JavaScript',jsx:'JSX',
 ts:'TypeScript',tsx:'TSX',mjs:'JavaScript',svg:'SVG',xml:'XML'
};

const mediaTypes={
 mp3:{kind:'audio',mime:'audio/mpeg'},wav:{kind:'audio',mime:'audio/wav'},
 ogg:{kind:'audio',mime:'audio/ogg'},m4a:{kind:'audio',mime:'audio/mp4'},
 aac:{kind:'audio',mime:'audio/aac'},mp4:{kind:'video',mime:'video/mp4'},
 webm:{kind:'video',mime:'video/webm'}
};

function workspaceExtension(path){
 if(typeof path!=='string'||!path||path.startsWith('/')||path.includes('\\')||/[\u0000-\u001f]/.test(path))return null;
 const parts=path.split('/');
 if(parts.some(part=>!part||part.startsWith('.')))return null;
 const name=parts.at(-1);
 return name.includes('.')?name.split('.').at(-1).toLowerCase():'';
}

export function workspaceFileType(path){
 const extension=workspaceExtension(path);
 return extension!==null&&Object.hasOwn(fileTypes,extension)?fileTypes[extension]:null;
}

export function workspaceMediaType(path){
 const extension=workspaceExtension(path);
 return extension!==null&&Object.hasOwn(mediaTypes,extension)?mediaTypes[extension]:null;
}

const imageTypes={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',gif:'image/gif'};

export function workspaceFilePresentation(path){
 const extension=workspaceExtension(path);
 if(extension===null)return null;
 if(Object.hasOwn(imageTypes,extension))return {viewer:'image',label:'이미지',responseType:imageTypes[extension],maxBytes:20*1024*1024};
 const media=workspaceMediaType(path);
 if(media)return {viewer:media.kind,label:media.kind==='audio'?'오디오':'영상',responseType:media.mime,maxBytes:20*1024*1024};
 const type=workspaceFileType(path);
 if(!type)return null;
 return type==='PDF'
  ?{viewer:'pdf',label:type,responseType:'application/pdf',maxBytes:20*1024*1024}
  :{viewer:'text',label:type,responseType:'application/json',maxBytes:512*1024};
}
