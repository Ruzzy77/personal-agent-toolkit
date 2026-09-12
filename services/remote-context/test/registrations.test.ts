import { env } from "cloudflare:test";
import { describe, it, expect } from "vitest";
import { CorpusDocumentsService } from "../src/corpus-documents";
import {
  RegistrationManagementService,
  finishRegistrationDetach,
} from "../src/registration-management";
import { CorpusService } from "../src/corpus";
import type { Env, Principal } from "../src/types";
const runtime = {
  ...(env as unknown as Env),
  CORPUS_MANAGEMENT_WRITE_ENABLED: "true",
};
async function fixture() {
  const owner = `detach_${crypto.randomUUID()}`,
    principal: Principal = {
      ownerId: owner,
      scopes: new Set(["corpus.read", "corpus.write"]),
      clientId: "isolated",
      auth: "site",
    };
  const docs = new CorpusDocumentsService(runtime, principal);
  await docs.spaceCreate({
    space_id: "retirement",
    display_name: "Isolated retirement",
  });
  const now = new Date().toISOString();
  await runtime.STATE_DB.prepare(
    "INSERT INTO sync_devices(owner_id,device_id,display_name,credential_id,status,capabilities_json,created_at,updated_at) VALUES(?,'device','Device',?,'active','[\"registration.detach\"]',?,?)",
  )
    .bind(owner, owner, now, now)
    .run();
  await runtime.STATE_DB.prepare(
    "INSERT INTO corpus_connections(owner_id,space_id,connection_id,display_name,roles_json,access_scope,permission,index_mode,device_id,generation,updated_at) VALUES(?,'retirement','work','Work','[\"work\"]','remote_allowed','read_write','not_indexed','device',3,?)",
  )
    .bind(owner, now)
    .run();
  return {
    owner,
    principal,
    docs,
    management: new RegistrationManagementService(runtime, principal),
  };
}
describe("Sync registration retirement", () => {
  it("does not expose or detach a local-only Space through a known locator", async () => {
    const f = await fixture();
    await runtime.STATE_DB.prepare(
      "UPDATE corpus_spaces SET access_scope='local_only' WHERE owner_id=?",
    )
      .bind(f.owner)
      .run();
    await expect(
      f.management.list({ space_id: "retirement" }),
    ).rejects.toMatchObject({ code: "space_not_found" });
    await expect(
      f.management.detach("connection", {
        space_id: "retirement",
        connection_id: "work",
        expected_generation: 3,
        idempotency_key: crypto.randomUUID(),
      }),
    ).rejects.toMatchObject({ code: "space_not_found" });
    expect(
      (
        await runtime.STATE_DB.prepare(
          "SELECT count(*) AS count FROM corpus_registration_detachments WHERE owner_id=?",
        )
          .bind(f.owner)
          .first<{ count: number }>()
      )?.count,
    ).toBe(0);
  });

  it("remains pending offline, blocks new jobs and old publication, and completes only on the exact durable local acknowledgement", async () => {
    const f = await fixture();
    const request = {
      space_id: "retirement",
      connection_id: "work",
      expected_generation: 3,
      idempotency_key: crypto.randomUUID(),
    };
    const result = await f.management.detach("connection", request);
    expect(result.state).toBe("pending");
    expect(await f.management.detach("connection", request)).toEqual(result);
    await expect(
      new CorpusService(runtime, f.principal).fileList({
        space_id: "retirement",
        connection_id: "work",
      }),
    ).rejects.toBeDefined();
    await expect(
      runtime.STATE_DB.prepare(
        "UPDATE corpus_connections SET configuration_state='ready' WHERE owner_id=?",
      )
        .bind(f.owner)
        .run(),
    ).rejects.toThrow("registration_detached");
    await finishRegistrationDetach(
      runtime,
      f.owner,
      "different-device",
      String(result.job_id),
      true,
      {
        state: "detached",
        generation: 3,
        registration_key: "retirement:work",
        filesystem_changed: false,
        local_jobs_drained: true,
      },
    );
    expect(
      (await f.management.list({ space_id: "retirement" })).detachments,
    ).toEqual(
      expect.arrayContaining([expect.objectContaining({ state: "pending" })]),
    );
    await finishRegistrationDetach(
      runtime,
      f.owner,
      "device",
      String(result.job_id),
      true,
      {
        state: "detached",
        generation: 3,
        registration_key: "retirement:work",
        filesystem_changed: false,
        local_jobs_drained: true,
      },
    );
    expect(
      (await f.management.list({ space_id: "retirement" })).connections,
    ).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ configuration_state: "detached" }),
      ]),
    );
  });
  it("rejects an old generation without changing any registration or queuing a job", async () => {
    const f = await fixture();
    await expect(
      f.management.detach("connection", {
        space_id: "retirement",
        connection_id: "work",
        expected_generation: 2,
        idempotency_key: crypto.randomUUID(),
      }),
    ).rejects.toMatchObject({ code: "management_conflict" });
    expect(
      (await f.management.list({ space_id: "retirement" })).connections,
    ).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          configuration_state: "ready",
          generation: 3,
        }),
      ]),
    );
    expect(
      await runtime.STATE_DB.prepare("SELECT 1 FROM sync_jobs WHERE owner_id=?")
        .bind(f.owner)
        .first(),
    ).toBeNull();
  });
});
