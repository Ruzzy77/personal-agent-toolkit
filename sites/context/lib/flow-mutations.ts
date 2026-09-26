type Call=<T>(name:string,input:unknown)=>Promise<T>;
/** Keep a receipt key after an uncertain response; never generate a new write on retry. */
export function createFlowMutator(call:Call){
 const attempts=new Map<string,string>();
 return async<T>(name:string,input:Record<string,unknown>):Promise<T>=>{
  const signature=JSON.stringify([name,input]),key=attempts.get(signature)||crypto.randomUUID();
  attempts.set(signature,key);
  const result=await call<T>(name,{...input,idempotency_key:key});
  attempts.delete(signature);return result;
 };
}
