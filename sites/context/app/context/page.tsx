import { requireOwnerUser } from "../../lib/owner-service";
import ContextWorkspace from "./workspace";
export const dynamic="force-dynamic";
export default async function ContextPage(){await requireOwnerUser("/context");return <ContextWorkspace/>;}
