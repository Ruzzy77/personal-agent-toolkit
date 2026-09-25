import { activeContentSources, contentImageSlot, materializeContentImages, replaceContentImages, validContentDraft } from "@personal-agent/flow-surface/content-assets";
import { flowContentForWeb, type FlowComposition } from "./flow-content.ts";

export type PendingImage = {root:string;path:string;version:string};
export type PendingImages = Record<string,PendingImage>;
export const imageSlot = contentImageSlot;
export { activeContentSources };

export function contentDraftValid(composition:FlowComposition,sources:PendingImages):boolean {
  return validContentDraft(composition,sources);
}

export function contentPreview(workspaceId:string,composition:FlowComposition,sources:PendingImages,fileUrl:(source:PendingImage)=>string):FlowComposition {
  return replaceContentImages(flowContentForWeb(workspaceId,composition),sources,fileUrl);
}

export async function importContentImages(
  composition:FlowComposition,sources:PendingImages,importImage:(source:PendingImage)=>Promise<string>
):Promise<FlowComposition> {
  return materializeContentImages(composition,sources,importImage,source=>JSON.stringify(source));
}
