import { requireChatGPTUser } from "@/app/chatgpt-auth";
import DesignGallery, { type Catalog } from "@/app/design-gallery";
import { designRequest } from "@/lib/design";

export default async function Home() {
  await requireChatGPTUser("/");
  const catalog = await designRequest<Catalog>("/api/v1/catalog");
  return <DesignGallery catalog={catalog} />;
}
