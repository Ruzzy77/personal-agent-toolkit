import test from "node:test";
import assert from "node:assert/strict";
import { issueTags, catalogTags, filterIssues } from "../lib/library-catalog.js";
const items = [
  {id:"daily:a", title:"첫 글", date:"2026-09-20", collection:"daily"},
  {id:"research:b", title:"다음 글", date:"2026-09-19", collection:"research", tags:["학습"]},
  {id:"custom:c", title:"독립 분류", date:"2026-09-18", collection:"custom"},
];
const marks = {"daily:a":{tags:["다시 읽기", "일간"]}};
test("Library labels are item tags; filters are derived from actual items and personal tags",()=>{
  assert.deepEqual(issueTags(items[0], marks["daily:a"]), ["일간","다시 읽기"]);
  assert.deepEqual(new Set(catalogTags(items,marks)), new Set(["일간","연구","학습","custom","다시 읽기"]));
  assert.deepEqual(catalogTags([],{}), []);
  assert.equal(catalogTags(items,marks).includes("요약"),false);
});
test("Library search and tag filters compose without hardcoded publication categories",()=>{
  assert.deepEqual(filterIssues(items, marks, "첫", "다시 읽기").map(x=>x.id), ["daily:a"]);
  assert.deepEqual(filterIssues(items, marks, "", "학습").map(x=>x.id), ["research:b"]);
  assert.deepEqual(filterIssues(items, marks, "독립", "custom").map(x=>x.id), ["custom:c"]);
  assert.deepEqual(filterIssues(items, marks, "다음", "일간"), []);
  assert.equal(filterIssues(items, marks, "2026-09").length, 3);
});
