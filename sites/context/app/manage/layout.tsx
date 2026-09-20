import { requireOwnerUser } from "../../lib/owner-service";
import Navigation from "./navigation";
import "./management.css";
export const metadata = { title: "자료 관리" };
export default async function ManagementLayout({ children }: { children: React.ReactNode }) {
  await requireOwnerUser("/manage");
  return (
    <div className="toolkit-admin">
      <Navigation />
      {children}
    </div>
  );
}
