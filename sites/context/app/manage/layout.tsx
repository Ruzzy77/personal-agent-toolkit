import { requireOwnerUser } from "../../lib/owner-service";
import Navigation from "./navigation";
import "./management.css";
export const metadata = { title: "관리 · Workspace" };
export default async function ManagementLayout({ children }: { children: React.ReactNode }) {
  await requireOwnerUser("/manage");
  return (
    <div className="toolkit-admin">
      <Navigation />
      {children}
    </div>
  );
}
