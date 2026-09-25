"use client";

import { useEffect, useState } from "react";
import { DesignPreview } from "../design/design-preview";
import { designPreviewPath } from "../../lib/design-preview";
import { readDesignRecipes } from "../../lib/flow-design-client";
import type { DesignRecipeRecord } from "../../lib/flow-resources";

export function FlowDesignReference({id}:{id:string}) {
  const [recipe,setRecipe]=useState<DesignRecipeRecord|null>(null);
  const [error,setError]=useState(false);
  useEffect(()=>{
    const controller=new AbortController();
    void readDesignRecipes(controller.signal)
      .then(items=>{
        if(controller.signal.aborted)return;
        const found=items.find(item=>item.id===id);
        if(found)setRecipe(found);else setError(true);
      })
      .catch(()=>{if(!controller.signal.aborted)setError(true)});
    return()=>controller.abort();
  },[id]);
  if(error)return <p className="flow-muted" role="alert">Design 레시피를 열지 못했습니다. 원본에서 다시 확인해 주세요.</p>;
  if(!recipe)return <p className="flow-muted" role="status">Design 레시피를 불러오는 중입니다.</p>;
  const preview=designPreviewPath(recipe);
  const href=preview?`/api/design/files/${encodeURIComponent(recipe.id)}/${preview.split("/").map(encodeURIComponent).join("/")}`:null;
  return <article className="flow-design-reference">
    <p>{recipe.description}</p>
    {href?<div className="flow-design-preview"><DesignPreview href={href} title={recipe.name+" 미리보기"}/></div>
      :<p className="flow-muted">이 레시피에는 HTML 미리보기가 없습니다.</p>}
  </article>;
}
