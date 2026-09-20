import { requireOwnerUser } from "@/lib/owner-service";
import { redirect } from "next/navigation";
import { LibraryReader } from "@/components/library/library-reader";
export const dynamic = "force-dynamic";
export default async function ReaderPage({ searchParams }: { searchParams: Promise<{path?:string}> }) {
  const { path } = await searchParams;
  await requireOwnerUser("/library/read" + (path ? "?path=" + encodeURIComponent(path) : ""));
  if (!path || !/^\/editions\/[a-z0-9/_-]+$/i.test(path)) redirect("/library");
  return <LibraryReader path={path} />;
}
