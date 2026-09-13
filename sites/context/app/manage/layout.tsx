import Navigation from "./navigation";
import "./management.css";
export const metadata = { title: "관리 · Workspace" };
export default function ManagementLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="toolkit-admin">
      <Navigation />
      {children}
    </div>
  );
}
