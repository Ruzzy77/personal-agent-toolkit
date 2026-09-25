export type ResourceReference =
  | {kind:'user-context';id:string}
  | {kind:'journal-item';id:string}
  | {kind:'library-issue';id:string}
  | {kind:'design-recipe';id:string}
  | {kind:'uikit-asset';id:string;revision:string}
  | {kind:'host-file';root:string;path:string}
  | {kind:'context';locator:
      | {product:'sense';sectionId:string;skill?:true}
      | {product:'corpus';spaceId:string;documentId:string}
      | {product:'context-item';spaceId:string;itemId:string}
      | {product:'context-skill';spaceId:string}
      | {product:'source';spaceId:string;readRef:string}};
export function validContextLocator(value:unknown):boolean;
export function validResourceReference(value:unknown):value is ResourceReference;
export function resourceReferenceKey(value:ResourceReference):string;
export function validLinkedResources(value:unknown):value is ResourceReference[];
