import { requireChatGPTUser } from "@/app/chatgpt-auth";
import Manager from "./manager";
export default async function ManagementPage() {
  await requireChatGPTUser("/manage");
  return <Manager />;
}
