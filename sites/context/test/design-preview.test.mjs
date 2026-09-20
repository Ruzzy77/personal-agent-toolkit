import test from "node:test";
import assert from "node:assert/strict";
import {designPreviewPath,isolatedDesignPreview} from "../lib/design-preview.ts";
test("design preview uses registered preview or template paths, never an invented styleguide",()=>{
 assert.equal(designPreviewPath({profiles:{preview:["templates/document.html"]},templates:{document:"other.html"}}),"templates/document.html");
 assert.equal(designPreviewPath({templates:{document:"templates/document.html"}}),"templates/document.html");
 assert.equal(designPreviewPath({templates:{bad:"../secret.html",external:"https://example.com/a.html"}}),null);
 assert.equal(designPreviewPath({templates:{}}),null);
});
test("static design preview denies scripts, network, forms and base URL changes",()=>{
 const result=isolatedDesignPreview('<html><head><style>body{color:red}</style></head><body>preview</body></html>');
 assert.ok(result.indexOf("Content-Security-Policy")<result.indexOf("<style>"));
 assert.ok(result.includes("default-src 'none'"));
 assert.ok(result.includes("form-action 'none'"));
 assert.ok(result.includes("base-uri 'none'"));
 assert.ok(result.includes("style-src 'unsafe-inline' data:"));
});
