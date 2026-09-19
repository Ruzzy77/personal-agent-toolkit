import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
const values=new Map();
globalThis.__ownerKV={async get(key,type){const value=values.get(key);return value===undefined?null:type==="json"?JSON.parse(value):value;},async put(key,value){values.set(key,value);},async delete(key){values.delete(key);}};
globalThis.__cookieValue="";
registerHooks({resolve(specifier,context,nextResolve){
 if(specifier==="cloudflare:workers")return {url:"data:text/javascript,export const env={WEB_SESSION_KV:globalThis.__ownerKV}",shortCircuit:true};
 if(specifier==="next/headers")return {url:"data:text/javascript,export async function cookies(){return {get(){return {value:globalThis.__cookieValue}}}}",shortCircuit:true};
 return nextResolve(specifier,context);
}});
process.env.APP_ORIGIN="https://personal-agent-toolkit.hiyaq77.workers.dev";
process.env.AUTH_ISSUER="https://personal-agent-auth.hiyaq77.workers.dev";
process.env.CONTEXT_SERVICE_URL="https://personal-agent-context.hiyaq77.workers.dev";
process.env.OAUTH_CLIENT_ID="https://personal-agent-toolkit.hiyaq77.workers.dev/oauth-client.json";
const auth=await import("../lib/owner-session.ts");
const origin=process.env.APP_ORIGIN, issuer=process.env.AUTH_ISSUER;
test("owner OAuth binds state, keeps credentials server-side and persists token rotation",async()=>{
 const fetchOriginal=globalThis.fetch;let refreshCount=0;
 try{
  const login=await auth.startOwnerLogin("/\\outside.example");
  const authorization=new URL(login.headers.get("Location"));
  const state=authorization.searchParams.get("state");
  const flow=JSON.parse(values.get("personal-agent-web:flow:"+state));
  assert.equal(flow.returnTo,"/");
  assert.equal(authorization.searchParams.get("code_challenge_method"),"S256");
  const binding=/__Host-personal-agent-flow=([^;]+)/.exec(login.headers.get("Set-Cookie"))[1];
  const callback=new URL("/auth/callback",origin);callback.searchParams.set("state",state);callback.searchParams.set("code","one-time-code");callback.searchParams.set("iss",issuer);
  await assert.rejects(auth.completeOwnerLogin(new Request(callback)),/expired/);
  globalThis.fetch=async(input,init)=>{
   if(String(input).includes("/oauth/token")){
    const body=new URLSearchParams(init.body);
    if(body.get("grant_type")==="refresh_token"){refreshCount++;assert.equal(body.get("refresh_token"),"unit-refresh");return Response.json({access_token:"unit-access-new",refresh_token:"unit-refresh-new",expires_in:900});}
    assert.equal(body.get("code_verifier"),flow.verifier);
    return Response.json({access_token:"unit-access",refresh_token:"unit-refresh",expires_in:900});
   }
   return Response.json({ok:true,result:{owner_id:"owner-unit"}});
  };
  const completed=await auth.completeOwnerLogin(new Request(callback,{headers:{Cookie:"__Host-personal-agent-flow="+binding}}));
  assert.equal(completed.status,302);assert.equal(completed.headers.get("Location"),"/");
  const setCookie=completed.headers.get("Set-Cookie");
  for(const value of ["HttpOnly","Secure","Path=/","SameSite=Lax"])assert.ok(setCookie.includes(value));
  assert.ok(!setCookie.includes("unit-access")&&!setCookie.includes("unit-refresh"));
  const sid=/__Host-personal-agent-owner=([^;]+)/.exec(setCookie)[1];
  const request=new Request(origin+"/api/identity",{headers:{Cookie:"__Host-personal-agent-owner="+sid}});
  let session=await auth.ownerSession(request);assert.equal(session.ownerId,"owner-unit");
  assert.equal(auth.csrfMatches(session,request),true);
  assert.equal(auth.csrfMatches(session,new Request(origin+"/api/test",{method:"POST"})),false);
  assert.equal(auth.csrfMatches(session,new Request(origin+"/api/test",{method:"POST",headers:{Origin:origin,"X-Toolkit-CSRF":session.csrf}})),true);
  assert.equal(auth.csrfMatches(session,new Request(origin+"/api/test",{method:"POST",headers:{Origin:"https://outside.example","X-Toolkit-CSRF":session.csrf}})),false);
  const key="personal-agent-web:session:"+sid;
  const expired=JSON.parse(values.get(key));expired.expiresAt=0;values.set(key,JSON.stringify(expired));
  const sessions=await Promise.all(Array.from({length:5},()=>auth.ownerSession(request)));
  assert.deepEqual(await Promise.all(sessions.map(item=>auth.ownerAccessToken(item))),Array(5).fill("unit-access-new"));
  assert.equal(refreshCount,1);
  assert.equal(JSON.parse(values.get(key)).refreshToken,"unit-refresh-new");
  session=await auth.ownerSession(request);assert.equal(await auth.ownerAccessToken(session),"unit-access-new");
  assert.equal(refreshCount,1);
  await auth.deleteOwnerSession(request);assert.equal(await auth.ownerSession(request),null);
  await assert.rejects(auth.completeOwnerLogin(new Request(callback,{headers:{Cookie:"__Host-personal-agent-flow="+binding}})),/expired/);
 }finally{globalThis.fetch=fetchOriginal;values.clear();}
});
test("expired owner sessions and invalid return destinations are refused",async()=>{
 const id="a".repeat(43);
 values.set("personal-agent-web:session:"+id,JSON.stringify({validUntil:0,ownerId:"old"}));
 assert.equal(await auth.ownerSession(new Request(origin,{headers:{Cookie:"__Host-personal-agent-owner="+id}})),null);
 for(const path of ["//outside.example","/\\outside.example","/\n/evil"]){
  const response=await auth.startOwnerLogin(path);
  const state=new URL(response.headers.get("Location")).searchParams.get("state");
  assert.equal(JSON.parse(values.get("personal-agent-web:flow:"+state)).returnTo,"/");
 }
 values.clear();
});
