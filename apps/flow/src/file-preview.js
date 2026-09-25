import {workspaceMediaType} from './work-surface/composition-editor.js';

export function filePreviewKind(name){
 if(/\.(?:png|jpe?g|webp|gif)$/i.test(name))return 'image';
 const media=workspaceMediaType(name);
 if(media)return media.kind;
 if(/\.pdf$/i.test(name))return 'pdf';
 if(/\.html?$/i.test(name))return 'html';
 return 'text';
}

export function fileContentUrl(workspaceId,path){
 return '/api/flow/files/content?workspaceId='+encodeURIComponent(workspaceId)+'&path='+encodeURIComponent(path);
}
