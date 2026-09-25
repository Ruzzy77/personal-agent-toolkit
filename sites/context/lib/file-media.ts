export type FileMediaKind="video"|"audio";

const video=new Set(["video/mp4","video/webm"]);
const audio=new Set([
  "audio/mpeg","audio/mp4","audio/aac","audio/ogg",
  "audio/wav","audio/x-wav","audio/webm",
]);

export function fileMediaKind(mime:string):FileMediaKind|null {
  const value=mime.toLowerCase().split(";",1)[0].trim();
  return video.has(value)?"video":audio.has(value)?"audio":null;
}
