import crypto from "node:crypto";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import {
  exportJWK,
  importPKCS8,
  SignJWT,
} from "jose";
import { Request, Response } from "miniflare";
import { defineProject } from "vitest/config";

const GOOGLE_CLIENT_ID = "google-client-for-tests";
const KEY_ID = "personal-agent-auth-test-key";
const keypair = crypto.generateKeyPairSync("rsa", {
  modulusLength: 2048,
  publicKeyEncoding: { type: "spki", format: "pem" },
  privateKeyEncoding: { type: "pkcs8", format: "pem" },
});
const privateKey = await importPKCS8(keypair.privateKey, "RS256");
const publicKey = crypto.createPublicKey(keypair.publicKey);
const publicJwk = await exportJWK(publicKey);

function base64UrlSha256(value: string): string {
  return crypto
    .createHash("sha256")
    .update(value)
    .digest("base64url");
}

async function googleOutbound(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (
    request.method === "GET" &&
    url.href === "https://www.googleapis.com/oauth2/v3/certs"
  ) {
    return Response.json({
      keys: [
        {
          ...publicJwk,
          alg: "RS256",
          kid: KEY_ID,
          use: "sig",
        },
      ],
    });
  }

  if (
    request.method === "POST" &&
    url.href === "https://oauth2.googleapis.com/token"
  ) {
    const body = new URLSearchParams(await request.text());
    const code = body.get("code") ?? "";
    const [account, nonce, expectedChallenge] = code.split(".");
    const verifier = body.get("code_verifier") ?? "";
    if (
      body.get("client_id") !== GOOGLE_CLIENT_ID ||
      body.get("client_secret") !== "google-secret-for-tests" ||
      body.get("redirect_uri") !==
        "https://auth.example.test/oauth/google/callback" ||
      expectedChallenge !== base64UrlSha256(verifier)
    ) {
      return Response.json({ error: "invalid_grant" }, { status: 400 });
    }

    const owner = account !== "other";
    const idToken = await new SignJWT({
      email: owner ? "owner@example.test" : "other@example.test",
      email_verified: true,
      name: owner ? "Owner" : "Other",
      nonce,
    })
      .setProtectedHeader({ alg: "RS256", kid: KEY_ID })
      .setIssuer("https://accounts.google.com")
      .setAudience(GOOGLE_CLIENT_ID)
      .setSubject(owner ? "google-sub-owner-123" : "google-sub-other-456")
      .setIssuedAt()
      .setExpirationTime("5m")
      .sign(privateKey);
    return Response.json({
      access_token: "unused-google-access-token",
      expires_in: 3600,
      id_token: idToken,
      token_type: "Bearer",
    });
  }

  return new Response("Not found", { status: 404 });
}

export default defineProject({
  plugins: [
    cloudflareTest({
      wrangler: {
        configPath: "./wrangler.google.test.jsonc",
      },
      miniflare: {
        outboundService: googleOutbound,
      },
    }),
  ],
  test: {
    include: ["test/google-oauth-flow.test.ts"],
    maxWorkers: 1,
  },
});
