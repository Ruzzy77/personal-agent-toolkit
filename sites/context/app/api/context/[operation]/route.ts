import { proxyOwnerRequest } from "../../../../lib/owner-service";
export const dynamic="force-dynamic";
const operations = new Set([
  "sense_read",
  "sense_revise",
  "sense_skill_revise",
  "corpus_space_list",
  "corpus_space_get",
  "corpus_space_search",
  "corpus_context_items_revise",
  "corpus_context_skill_revise",
  "corpus_file_read",
  "corpus_space_create",
  "corpus_workspace_resolve",
  "corpus_document_create",
  "corpus_document_list",
  "corpus_document_read",
  "corpus_document_revise",
  "corpus_document_restore",
]);
export async function POST(request:Request,context:{params:Promise<{operation:string}>}) {
  const {operation}=await context.params;
  if(!operations.has(operation))return Response.json({error:"operation_not_found"},{status:404});
  return proxyOwnerRequest(request,"/site/v1/"+operation);
}
