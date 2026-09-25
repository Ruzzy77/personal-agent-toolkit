import { ownerFetch } from "./owner-client";
import { designRecipesFromCatalog } from "./flow-design";

export async function readDesignRecipes(signal:AbortSignal) {
  const response=await ownerFetch("/api/design/catalog",{signal});
  if(!response.ok)throw new Error("Design 목록을 읽을 수 없습니다.");
  const payload:unknown=await response.json();
  if(!payload||typeof payload!=="object"||Array.isArray(payload))throw new Error("Design 목록을 읽을 수 없습니다.");
  return designRecipesFromCatalog((payload as {catalog?:unknown}).catalog);
}
