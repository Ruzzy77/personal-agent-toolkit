import { requireChatGPTUser } from '@/app/chatgpt-auth';
import { App } from '@/src/App';

export const dynamic = 'force-dynamic';

export default async function Home() {
  await requireChatGPTUser('/');
  return <App />;
}
