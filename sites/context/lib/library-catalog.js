const COLLECTION_LABELS = { daily: "일간", digest: "요약", research: "연구" };

export function issueTags(item, mark = {}) {
  const collection = COLLECTION_LABELS[item.collection] || item.koreanLabel || item.collection;
  return [...new Set([collection, ...(item.tags || []), ...(mark.tags || [])].filter(tag => typeof tag === "string" && tag.trim()).map(tag => tag.trim()))];
}
export function catalogTags(items, marks = {}) {
  return [...new Set(items.flatMap(item => issueTags(item, marks[item.id])))].sort((a, b) => a.localeCompare(b, "ko"));
}
export function filterIssues(items, marks = {}, query = "", tag = "") {
  const needle = query.trim().toLocaleLowerCase("ko");
  return items.filter(item => {
    const tags = issueTags(item, marks[item.id]);
    return (!tag || tags.includes(tag)) && (!needle || [item.title, item.date, item.id, ...tags].join(" ").toLocaleLowerCase("ko").includes(needle));
  });
}
