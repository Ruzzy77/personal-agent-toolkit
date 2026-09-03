import { headers } from 'next/headers';
import { redirect } from 'next/navigation';

import { chatGPTUserFromHeaders, type ChatGPTUser } from '@/app/chatgpt-user';

export type { ChatGPTUser } from '@/app/chatgpt-user';

const SIGN_IN_PATH = '/signin-with-chatgpt';
const SIGN_OUT_PATH = '/signout-with-chatgpt';
const CALLBACK_PATH = '/callback';

export async function getChatGPTUser(): Promise<ChatGPTUser | null> {
  const requestHeaders = await headers();
  return chatGPTUserFromHeaders(requestHeaders);
}

export async function requireChatGPTUser(
  returnTo: string,
): Promise<ChatGPTUser> {
  const user = await getChatGPTUser();
  if (user) return user;

  redirect(chatGPTSignInPath(returnTo));
}

export async function requireChatGPTApiUser(): Promise<Response | null> {
  if (await getChatGPTUser()) return null;

  return Response.json(
    {
      ok: false,
      error: {
        code: 'authentication_required',
        message: 'ChatGPT 로그인이 필요합니다.',
      },
    },
    {
      status: 401,
      headers: { 'Cache-Control': 'private, no-store' },
    },
  );
}

export function chatGPTSignInPath(returnTo: string): string {
  const safeReturnTo = safeRelativeReturnPath(returnTo);
  return `${SIGN_IN_PATH}?return_to=${encodeURIComponent(safeReturnTo)}`;
}

export function chatGPTSignOutPath(returnTo = '/'): string {
  const safeReturnTo = safeRelativeReturnPath(returnTo);
  return `${SIGN_OUT_PATH}?return_to=${encodeURIComponent(safeReturnTo)}`;
}

function safeRelativeReturnPath(value: string): string {
  if (!value.startsWith('/') || value.startsWith('//')) return '/';

  let url: URL;
  try {
    url = new URL(value, 'https://app.local');
  } catch {
    return '/';
  }
  if (url.origin !== 'https://app.local') return '/';
  if (isReservedAuthPath(url.pathname)) return '/';

  return `${url.pathname}${url.search}${url.hash}`;
}

function isReservedAuthPath(pathname: string): boolean {
  return (
    pathname === SIGN_IN_PATH ||
    pathname === SIGN_OUT_PATH ||
    pathname === CALLBACK_PATH
  );
}
