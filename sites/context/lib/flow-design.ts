import type { DesignRecipeRecord } from "./flow-resources";

const DESIGN_ID=/^[a-z0-9][a-z0-9-]{0,63}$/;
const object=(value:unknown):value is Record<string,unknown>=>Boolean(value)&&typeof value==="object"&&!Array.isArray(value);
const stringRecord=(value:unknown):value is Record<string,string>=>object(value)&&Object.values(value).every(item=>typeof item==="string");

export function designRecipesFromCatalog(value:unknown):DesignRecipeRecord[] {
  if(!object(value)||!Array.isArray(value.recipes))throw new Error("Design 목록을 읽을 수 없습니다.");
  return value.recipes.flatMap((item):DesignRecipeRecord[]=>{
    if(!object(item)||typeof item.id!=="string"||!DESIGN_ID.test(item.id)||
      typeof item.name!=="string"||typeof item.description!=="string"||
      typeof item.version!=="string"||typeof item.status!=="string")return [];
    const gallery=object(item.gallery)?{
      ...(typeof item.gallery.korean_name==="string"?{korean_name:item.gallery.korean_name}:{}),
      ...(typeof item.gallery.purpose==="string"?{purpose:item.gallery.purpose}:{}),
    }:undefined;
    const profiles=object(item.profiles)?Object.fromEntries(
      Object.entries(item.profiles).filter((entry):entry is [string,string[]]=>Array.isArray(entry[1])&&entry[1].every(path=>typeof path==="string"))
    ):undefined;
    return [{
      id:item.id,name:item.name,description:item.description,version:item.version,
      status:item.status,selection_ready:item.selection_ready===true,
      templates:stringRecord(item.templates)?item.templates:{},
      ...(gallery?{gallery}:{}),
      ...(profiles?{profiles}:{}),
    }];
  });
}
