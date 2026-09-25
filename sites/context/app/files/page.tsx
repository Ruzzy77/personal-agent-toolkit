import { requireOwnerUser } from "../../lib/owner-service";
import { WorkspaceFiles } from "../workspace-files";
export const dynamic="force-dynamic";
export default async function FilesPage(){await requireOwnerUser("/files");return <WorkspaceFiles/>;}
