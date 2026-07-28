import { parseSseChunk } from "./sse";

test("parses enveloped events (ignoring id: lines) and keeps the incomplete tail", () => {
  const raw =
    'id: 1\nevent: accepted\ndata: {"turn_id":"t1","message_id":"m1","event_seq":1,"turn_index":1}\n\n' +
    'id: 2\nevent: token\ndata: {"turn_id":"t1","message_id":"m1","event_seq":2,"text":"Hello"}\n\n' +
    "id: 3\nevent: token\ndata: {\"te";
  const { events, rest } = parseSseChunk(raw);
  expect(events).toEqual([
    { name: "accepted", data: { turn_id: "t1", message_id: "m1", event_seq: 1, turn_index: 1 } },
    { name: "token", data: { turn_id: "t1", message_id: "m1", event_seq: 2, text: "Hello" } },
  ]);
  expect(rest).toBe('id: 3\nevent: token\ndata: {"te');
});

test("returns no events for a bare fragment", () => {
  const { events, rest } = parseSseChunk("event: to");
  expect(events).toEqual([]);
  expect(rest).toBe("event: to");
});
