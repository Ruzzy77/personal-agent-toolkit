import { chatGPTUserFromHeaders } from '@personal-agent/site-runtime';
export const dynamic = 'force-dynamic';
export async function GET(request: Request) {
  const user = chatGPTUserFromHeaders(request.headers);
  if (!user) return Response.json({ error: 'authentication_required' }, { status: 401 });
  return Response.json({ userId: user.userId }, { headers: { 'Cache-Control': 'private, no-store' } });
}
