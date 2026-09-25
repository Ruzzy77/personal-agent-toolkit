const previewPolicy="default-src 'none'; script-src 'none'; connect-src 'none'; img-src data:; media-src 'none'; font-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-src 'none'";

export function htmlPreviewDocument(body){
 if(typeof body!=='string')return '';
 const document=body.replace(/^\uFEFF/,'');
 const doctype=/^(\s*<!doctype[^>]*>\s*)/i.exec(document);
 const opening=doctype?doctype[0]:'<!doctype html>';
 const rest=doctype?document.slice(doctype[0].length):document;
 return opening+'<meta http-equiv="Content-Security-Policy" content="'+previewPolicy+'">'+rest;
}
