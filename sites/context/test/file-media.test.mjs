import {workspaceMediaType,workspaceFilePresentation,workspaceFileBlock} from "@personal-agent/flow-surface/composition-editor";
import test from "node:test";
import assert from "node:assert/strict";
import { fileMediaKind } from "../lib/file-media.ts";

test("playable files use explicit browser media types",()=>{
  assert.equal(fileMediaKind("video/mp4"),"video");
  assert.equal(fileMediaKind("video/webm"),"video");
  assert.equal(fileMediaKind("audio/mpeg"),"audio");
  assert.equal(fileMediaKind("audio/x-wav"),"audio");
  assert.equal(fileMediaKind("audio/ogg; codecs=opus"),"audio");
  assert.equal(fileMediaKind("image/svg+xml"),null);
  assert.equal(fileMediaKind("application/octet-stream"),null);
  assert.equal(fileMediaKind("text/html"),null);
});

test('Flow audio selection uses the same formats as file playback',()=>{
  assert.deepEqual(workspaceMediaType('meeting.m4a'),{kind:'audio',mime:'audio/mp4'});
  assert.deepEqual(workspaceMediaType('voice.aac'),{kind:'audio',mime:'audio/aac'});
  assert.equal(fileMediaKind(workspaceMediaType('meeting.m4a').mime),'audio');
  assert.equal(fileMediaKind(workspaceMediaType('voice.aac').mime),'audio');
});


test('Web file insertion uses the shared reader and size limits',()=>{
 for(const [path,viewer] of [['photo.jpg','image'],['recording.m4a','audio'],['movie.mp4','video'],['report.pdf','pdf'],['note.md','text']]){
  assert.equal(workspaceFilePresentation(path).viewer,viewer);
  assert.equal(workspaceFileBlock(path,'/api/flow/files/content?workspaceId=workspace&path='+path,'file').kind,'file');
 }
 assert.equal(workspaceFilePresentation('note.md').maxBytes,512*1024);
 assert.equal(workspaceFilePresentation('photo.jpg').maxBytes,20*1024*1024);
});
