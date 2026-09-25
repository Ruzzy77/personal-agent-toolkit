export const layoutWidthPresets:ReadonlyArray<Readonly<{value:12|8|6|4;label:string}>>;
export type ArtifactLike={id:string};
export type WorkLike<T extends ArtifactLike=ArtifactLike>={artifacts:T[];surfaceLayout?:{order:string[];spans:Record<string,number>}};
export function orderedArtifacts<T extends ArtifactLike>(work:WorkLike<T>):T[];
export function artifactSpan(work:WorkLike,id:string):number;
export function validSurfaceLayout(layout:unknown):boolean;
export function setArtifactSpan<T extends ArtifactLike>(work:WorkLike<T>,id:string,span:number):WorkLike<T>;
export function moveArtifact<T extends ArtifactLike>(work:WorkLike<T>,id:string,step:number):WorkLike<T>;
