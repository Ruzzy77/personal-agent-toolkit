import { blankParagraphs, contentFromBlank } from "@personal-agent/flow-surface/blank-content";
import type { FlowArtifact } from "./flow-content.ts";

export function blankTextReady(artifact:FlowArtifact):boolean {
  if(artifact.kind!=="blank"||!artifact.title.trim()||typeof artifact.draftText!=="string")return false;
  const paragraphs=blankParagraphs(artifact.draftText);
  return Boolean(paragraphs?.length);
}

export function blankTextContent(artifact:FlowArtifact):FlowArtifact {
  if(!blankTextReady(artifact))throw new Error("처음 넣을 내용을 확인해 주세요.");
  return contentFromBlank(artifact as FlowArtifact & {kind:"blank"}) as FlowArtifact;
}
