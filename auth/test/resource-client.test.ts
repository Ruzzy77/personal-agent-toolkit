import { describe, it } from "vitest";

import {
  extractBearerToken,
  validateBearerRequest,
  type AuthServiceBinding,
} from "../src/resource-client";

const LIBRARY = "https://library.example.test/api/mcp";

describe("resource client", () => {
  it("extracts a case-insensitive Bearer scheme", ({ expect }) => {
    const request = new Request(LIBRARY, {
      headers: { Authorization: "bearer opaque-token" },
    });
    expect(extractBearerToken(request)).toBe("opaque-token");
  });

  it("rejects malformed authorization headers before RPC", async ({
    expect,
  }) => {
    let calls = 0;
    const auth: AuthServiceBinding = {
      async validateAccessToken() {
        calls += 1;
        return { ok: false, code: "invalid_token", status: 401 };
      },
    };

    const result = await validateBearerRequest(
      new Request(LIBRARY, {
        headers: { Authorization: "Bearer token with spaces" },
      }),
      auth,
      LIBRARY,
      ["library.read"],
    );
    expect(result).toEqual({
      ok: false,
      code: "invalid_token",
      status: 401,
    });
    expect(calls).toBe(0);
  });

  it("passes only the token and policy inputs to the private binding", async ({
    expect,
  }) => {
    const observed: unknown[][] = [];
    const auth: AuthServiceBinding = {
      async validateAccessToken(...args) {
        observed.push(args);
        return { ok: false, code: "insufficient_scope", status: 403 };
      },
    };

    const result = await validateBearerRequest(
      new Request(LIBRARY, {
        headers: { Authorization: "Bearer opaque-token" },
      }),
      auth,
      LIBRARY,
      ["library.write"],
    );
    expect(observed).toEqual([
      ["opaque-token", LIBRARY, ["library.write"]],
    ]);
    expect(result).toEqual({
      ok: false,
      code: "insufficient_scope",
      status: 403,
    });
  });
});
