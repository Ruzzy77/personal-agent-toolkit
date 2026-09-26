import type {ResourceReference} from './resource-reference.js';

export type ReferenceSource={id:string;title:string;reference?:ResourceReference;filePath?:string;live?:boolean;root?:string};
export type ReferencingWork={sourceIds:string[];linkedResources?:ResourceReference[]};
export type WorkReference<T extends ReferenceSource=ReferenceSource>={
 id:string;title:string;source?:T;reference:ResourceReference|null;
 sourceIds:string[];linkedReferences:ResourceReference[];missing?:boolean;
};
export function sourceReference(source:ReferenceSource|undefined,root?:string):ResourceReference|null;
export function referenceTitle(reference:ResourceReference):string;
export function workReferences<T extends ReferenceSource>(work:ReferencingWork,sources?:T[],root?:string):WorkReference<T>[];
export function disconnectWorkReference(work:ReferencingWork,item:WorkReference):{sourceIds:string[];linkedResources:ResourceReference[]};
