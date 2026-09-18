import { afterEach, describe, expect, it, vi } from "vitest";
import { dispatchSkill } from "./skills";

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body };
}

describe("dispatchSkill", () => {
  it("posts args and identity to the internal route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, parts: [], proof: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await dispatchSkill(
      "data_qa.metric_query",
      { metrics: ["GP"] },
      { sub: "dev|local", roles: ["Poseidon:Sales"] },
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/internal/v1/skills/data_qa.metric_query/dispatch");
    expect(init.method).toBe("POST");
    expect(init.headers["content-type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({
      args: { metrics: ["GP"] },
      identity: { sub: "dev|local", roles: ["Poseidon:Sales"] },
    });
  });

  it("sends only sub and roles, never the rest of an Identity", async () => {
    // `email`/`name` are the caller's to hold, not Python's to receive: the
    // route builds a UserContext with both None (`api/internal.py`), so sending
    // them would imply a contract this seam does not have. `Pick` is a
    // compile-time shape only -- a full Identity passed at runtime carries
    // every field, so the narrowing has to happen in the body construction.
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, parts: [], proof: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await dispatchSkill("data_qa.metric_query", {}, {
      sub: "sf|CAROL",
      roles: ["Poseidon:Sales"],
      email: "carol@example.com",
      name: "Carol",
    } as never);

    expect(JSON.parse(fetchMock.mock.calls[0][1].body).identity).toEqual({
      sub: "sf|CAROL",
      roles: ["Poseidon:Sales"],
    });
  });

  it("returns the SkillResult envelope the route answered with", async () => {
    const envelope = {
      ok: false,
      parts: [{ kind: "text", payload: { markdown: "nope" } }],
      proof: [],
      artifacts: [],
      error: { type: "about:blank", title: "skill failure", detail: "boom", status: 500 },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(envelope)));

    // `ok: false` is a real answer, not a transport failure: the route returns
    // it inside a 200 and this client must hand it back rather than throw.
    await expect(
      dispatchSkill("data_qa.metric_query", {}, { sub: "dev|local", roles: [] }),
    ).resolves.toEqual(envelope);
  });

  it("throws on a non-200 so a failure is never mistaken for an empty result", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, text: async () => "boom" }),
    );
    await expect(
      dispatchSkill("data_qa.metric_query", {}, { sub: "dev|local", roles: [] }),
    ).rejects.toThrow(/500/);
  });

  it("puts the response body in the thrown message, not just the status", async () => {
    // The route's 404 and 501 details name the offending skill id and the
    // misconfigured backend (`api/internal.py`); dropping the body would turn
    // both into an indistinguishable "404".
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        text: async () => '{"detail":"unknown skill \'does_not.exist\'"}',
      }),
    );
    await expect(
      dispatchSkill("does_not.exist", {}, { sub: "dev|local", roles: [] }),
    ).rejects.toThrow(/does_not\.exist/);
  });

  it("encodes the skill id so a path separator cannot escape the route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, parts: [], proof: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await dispatchSkill("../../health/live", {}, { sub: "dev|local", roles: [] });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "http://localhost:8000/internal/v1/skills/..%2F..%2Fhealth%2Flive/dispatch",
    );
  });
});
