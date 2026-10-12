import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { streamTurn } from "./turns";

beforeEach(() => {
  vi.stubEnv("POSEIDON_INTERNAL_TOKEN", "test-internal-token");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("streamTurn", () => {
  it("posts identity, text and client_turn_key to the internal turns route with the token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await streamTurn("c1", { text: "hi", client_turn_key: "k" }, { sub: "dev|local", roles: ["Poseidon:Sales"] });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/internal/v1/conversations/c1/turns");
    expect(JSON.parse(init.body)).toEqual({ identity: { sub: "dev|local", roles: ["Poseidon:Sales"] }, text: "hi", client_turn_key: "k" });
    expect(init.headers["x-poseidon-internal-token"]).toBe("test-internal-token");
  });

  // The equality the live walk's trace-id check rests on: Python adopts a
  // well-formed inbound `X-Trace-Id` (`api/app.py`'s trace middleware gates on
  // exactly this shape) and mints its own otherwise, so a turn whose log line
  // carries the id Next sent is only meaningful if Next always sends one.
  it("sends a fresh 32-hex trace id so Python's log line correlates with this turn", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await streamTurn("c1", { text: "hi", client_turn_key: null }, { sub: "dev|local", roles: [] });
    expect(fetchMock.mock.calls[0][1].headers["x-trace-id"]).toMatch(/^[0-9a-f]{32}$/);
  });

  // Only `sub` and `roles`: `_TurnRequest.identity` is Python's `_Identity`,
  // the same two-field model `dispatch_skill` takes, and `Pick` narrows the
  // type without narrowing the object handed over at runtime.
  it("sends only sub and roles, never the rest of an Identity", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await streamTurn("c1", { text: "hi", client_turn_key: null }, {
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

  it("encodes the conversation id so a path separator cannot escape the route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await streamTurn("../../health/live", { text: "hi", client_turn_key: null }, { sub: "dev|local", roles: [] });
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:8000/internal/v1/conversations/..%2F..%2Fhealth%2Flive/turns",
    );
  });

  it("honours ANALYTICS_ORIGIN, like every other internal call", async () => {
    vi.stubEnv("ANALYTICS_ORIGIN", "http://analytics.internal:9999");
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await streamTurn("c1", { text: "hi", client_turn_key: null }, { sub: "dev|local", roles: [] });
    expect(new URL(fetchMock.mock.calls[0][0] as string).origin).toBe("http://analytics.internal:9999");
  });

  it("forwards the caller's abort signal so the idle timeout can cut the upstream request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await streamTurn("c1", { text: "hi", client_turn_key: null }, { sub: "dev|local", roles: [] }, controller.signal);
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it("throws before calling fetch when POSEIDON_INTERNAL_TOKEN is unset", async () => {
    vi.stubEnv("POSEIDON_INTERNAL_TOKEN", "");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      streamTurn("c1", { text: "hi", client_turn_key: null }, { sub: "dev|local", roles: [] }),
    ).rejects.toThrow(/POSEIDON_INTERNAL_TOKEN is unset/);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
