import { redirect } from "next/navigation";
import { requireOwnerUser } from "../lib/owner-service";
export const dynamic="force-dynamic";
export default async function Home(){await requireOwnerUser("/");redirect("/flow");}
