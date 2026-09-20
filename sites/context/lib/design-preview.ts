type PreviewRecipe = { profiles?: Record<string,string[]>; templates: Record<string,string> };
export function designPreviewPath(recipe: PreviewRecipe): string | null {
  return [...(recipe.profiles?.preview ?? []), ...Object.values(recipe.templates)].find(path =>
    /\.html?$/i.test(path) && !path.startsWith("/") && !path.includes(":") && !path.split("/").includes("..")
  ) ?? null;
}
export function isolatedDesignPreview(source: string): string {
  const policy = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\' data:; img-src data: blob:; font-src data:; base-uri \'none\'; form-action \'none\'">';
  return /<head\b[^>]*>/i.test(source) ? source.replace(/<head\b[^>]*>/i, head => head + policy) : policy + source;
}
