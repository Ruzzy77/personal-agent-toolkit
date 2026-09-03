import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { chatGPTUserFromHeaders, type ChatGPTUser } from "@/app/chatgpt-user";

export type { ChatGPTUser } from "@/app/chatgpt-user";

const SIGN_IN_PATH = "/signin-with-chatgpt";
const SIGN_OUT_PATH = "/signout-with-chatgpt";
const CALLBACK_PATH = "/callback";

export async function getChatGPTUser(): Promise<ChatGPTUser | null> {
  return chatGPTUserFromHeaders(await headers());
}

export async function requireChatGPTUser(returnTo: string): Promise<ChatGPTUser> {
  const user = await getChatGPTUser();
  if (user) return user;
  redirect(chatGPTSignInPath(returnTo));
}

export async function requireChatGPTApiUser(): Promise<Response | null> {
  if (await getChatGPTUser()) return null;
  return Response.json(
    { error: "authentication_required" },
    { status: 401, headers: { "Cache-Control": "private, no-store" } },
  );
}

function chatGPTSignInPath(returnTo: string): string {
  return `${SIGN_IN_PATH}?return_to=${encodeURIComponent(safeReturnPath(returnTo))}`;
}

function safeReturnPath(value: string): string {
  if (!value.startsWith("/") || value.startsWith("//")) return "/";
  try {
    const url = new URL(value, "https://app.local");
    if (
      url.origin !== "https://app.local"
      || [SIGN_IN_PATH, SIGN_OUT_PATH, CALLBACK_PATH].includes(url.pathname)
    ) {
      return "/";
    }
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return "/";
  }
}
