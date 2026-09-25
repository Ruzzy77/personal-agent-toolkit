import test from 'node:test';
import assert from 'node:assert/strict';
import {isolatedUIKitPreview,UIKIT_PREVIEW_CSP} from '../lib/uikit-preview.ts';
test('UIKit previews isolate inline documents and block network frame navigation',()=>{
 const source='<!doctype html><h1>한글 자료</h1><script>document.title="Ready"</script>';
 const result=isolatedUIKitPreview(source);
 assert.match(result,/<iframe title="UIKit 자료" sandbox="allow-scripts"/);
 assert.equal((result.match(/<iframe/g)||[]).length,1);
 assert.ok(result.includes('&lt;script&gt;'));assert.ok(result.includes('&quot;Ready&quot;'));
 assert.ok(!result.includes('<script>'));
 assert.match(UIKIT_PREVIEW_CSP,/connect-src 'none'/);
 assert.match(UIKIT_PREVIEW_CSP,/frame-src 'none'/);
 assert.match(UIKIT_PREVIEW_CSP,/form-action 'none'/);
 assert.doesNotMatch(UIKIT_PREVIEW_CSP,/allow-same-origin|allow-top-navigation/);
});
