import {env} from "cloudflare:test";
import {expect,it} from "vitest";
import {flowResourceRead,flowResourceSearch} from "../src/flow-resources";
import {SenseService} from "../src/sense";
import {CorpusDocumentsService} from "../src/corpus-documents";
import {JournalService} from "personal-agent-journal-service/service";
import {LibraryService} from "personal-agent-library-service/service";
import type {Env,Principal} from "../src/types";
const runtime=env as unknown as Env;
async function fixture(){
 const principal:Principal={ownerId:"flow-"+crypto.randomUUID(),scopes:new Set(["sense.read","corpus.read","corpus.write","library.read"]),clientId:"test",auth:"site"};
 const sense=new SenseService(runtime.STATE_DB,principal.ownerId);
 await sense.importProfile({schema_version:2,sections:[
  {id:"ordinary",purpose:"한국어 기준",text:"검색공통 표현 😀 기준",origins:["user_set"],sensitivity:"ordinary"},
  {id:"private",purpose:"검색공통 비공개",text:"민감한 사용자 맥락",origins:["user_set"],sensitivity:"sensitive"}
 ]});
 const documents=new CorpusDocumentsService(runtime,principal),space_id="flow-docs";
 await documents.spaceCreate({space_id,display_name:"Toolkit 설계"});
 for(let i=0;i<4;i++)await documents.documentCreate({space_id,document_id:"document-"+i,title:"검색공통 문서 "+i,body_markdown:"문서 본문 😀".repeat(100)});
 return {principal,documents,sense,space_id};
}
it("pages original search across services without exposing sensitive context or copying bodies",async()=>{
 const {principal}=await fixture();
 const library=new LibraryService({DB:runtime.LIBRARY_DB,MEDIA:runtime.LIBRARY_MEDIA});
 const id="research:2026-09-26:11";
 await library.createIssue({id,source_html:"<!doctype html><html><head><title>검색공통 발간물</title></head><body><h1>검색공통 발간물</h1><p class=\"lead\">도입문</p><article><p>본문</p></article></body></html>",references:[]});
 let cursor:string|null=null;const values:unknown[]=[];let pages=0;
 do{
  const result=await flowResourceSearch(runtime,principal,{query:"검색공통",limit:2,...(cursor?{cursor}:{})});
  expect(result.items.length).toBeLessThanOrEqual(2);expect(result.partial).toBe(false);
  values.push(...result.items);cursor=result.nextCursor;expect(++pages).toBeLessThan(20);
 }while(cursor);
 const output=JSON.stringify(values);
 expect(output).toContain("한국어 기준");expect(output).toContain("검색공통 문서 3");expect(output).toContain("검색공통 발간물");
 expect(output).not.toContain("비공개");expect(output).not.toContain("민감한");expect(output).not.toContain("문서 본문");
 expect(new Set(values.map(value=>JSON.stringify((value as {reference:unknown}).reference))).size).toBe(values.length);
 const first=await flowResourceSearch(runtime,principal,{query:"검색공통",limit:1});
 await expect(flowResourceSearch(runtime,principal,{query:"다른 검색",cursor:first.nextCursor,limit:1})).rejects.toThrow();
});
it("reads one canonical version in Unicode chunks and rejects a stale version",async()=>{
 const {principal}=await fixture(),reference={kind:"context",locator:{product:"corpus",spaceId:"flow-docs",documentId:"document-0"}};
 const first=await flowResourceRead(runtime,principal,{reference,max_chars:9});
 expect(Array.from(first.body).length).toBe(9);expect(first.title).toBe("검색공통 문서 0");expect(first.version).toBe("1");
 const second=await flowResourceRead(runtime,principal,{reference,start_char:first.nextStartChar,expected_version:first.version,max_chars:9});
 expect(first.body+second.body).toBe(Array.from("문서 본문 😀".repeat(100)).slice(0,18).join(""));
 await expect(flowResourceRead(runtime,principal,{reference,expected_version:"999"})).rejects.toMatchObject({status:409});
 const denied={...principal,scopes:new Set(["sense.read"])};
 await expect(flowResourceRead(runtime,denied,{reference})).rejects.toMatchObject({status:403});
 const section={kind:"context",locator:{product:"sense",sectionId:"ordinary"}};
 const full=await flowResourceRead(runtime,principal,{reference:section,max_chars:1000});
 expect(full.body).toContain("검색공통 표현");expect(full.version).not.toBe("");
 const privateRead=await flowResourceRead(runtime,principal,{reference:{kind:"context",locator:{product:"sense",sectionId:"private"}}});
 expect(privateRead.body).toBe("민감한 사용자 맥락");
});
it("reports an unavailable source separately without hiding other search results",async()=>{
 const {principal}=await fixture();
 const partial=await flowResourceSearch({...runtime,LIBRARY_DB:{prepare(){throw new Error("offline")}} as unknown as D1Database},principal,{query:"검색공통",limit:20});
 expect(partial.partial).toBe(true);expect(partial.unavailable).toContain("library");expect(partial.items.some(value=>value.title==="한국어 기준")).toBe(true);
});


it("searches long Korean titles literally and keeps only the latest record without removing older access", async () => {
  const principal: Principal = {
    ownerId: "journal-flow-test",
    scopes: new Set(["journal.read"]),
    clientId: "test",
    auth: "site",
  };
  const journal = new JournalService(runtime.JOURNAL_DB, () => new Date("2026-09-26T00:00:00Z"));
  const actor = {kind: "owner" as const, id: "test", scopes: new Set(["journal.write"]), auth: "oauth" as const};
  const input = {
    sourceKind: "test", sourceKey: crypto.randomUUID(), sourceRef: null, sourceVersion: null,
    projectKey: "flow-dedup", title: "Toolkit 통합 관리 배포 후 클라이언트 확인 및 100%_자료 검색", summary: "이전 주 기록",
    lane: "direct" as const, responsibility: "user" as const, dueAt: null,
    durableOutcome: null, corpusTargetSpace: null, occurredAt: null,
  };
  const [first] = await journal.ingestItems([{...input, weekId: "2026-09-14", idempotencyKey: crypto.randomUUID()}], actor);
  const [latest] = await journal.ingestItems([{...input, summary: "이번 주 기록", weekId: "2026-09-21", idempotencyKey: crypto.randomUUID()}], actor);
  if (!first || !latest) throw new Error("Missing fixture records");
  expect(latest.item.id).not.toBe(first.item.id);
  expect(latest.item.logicalItemId).toBe(first.item.logicalItemId);
  const result = await flowResourceSearch(runtime, principal, {query: "Toolkit 통합 관리 배포 후 클라이언트 확인 및 100%_자료 검색", limit: 1});
  expect(result.items).toHaveLength(1);
  expect(result.items[0]!.reference).toEqual({kind: "journal-item", id: latest.item.id});
  if (result.nextCursor) {
    const tail = await flowResourceSearch(runtime, principal, {query: "Toolkit 통합 관리 배포 후 클라이언트 확인 및 100%_자료 검색", limit: 1, cursor: result.nextCursor});
    expect(tail.items).toHaveLength(0);
    expect(tail.nextCursor).toBeNull();
  }
  const old = await flowResourceRead(runtime, principal, {reference: {kind: "journal-item", id: first.item.id}});
  expect(JSON.parse(old.body).item.summary).toBe("이전 주 기록");
});
