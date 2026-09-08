/* The event-colour rules. Colour is decoration in this grid — every fact it
 * hints at is also written out in text — but it is decoration with rules, and
 * two of them are load-bearing: red stays reserved for attention states, and a
 * commitment keeps its colour week to week.
 *
 * Run by scripts/test.sh via `node --test`. */
"use strict";
const test = require("node:test");
const assert = require("node:assert");

const EventColor = require("../static/evcolor.js");

const TITLES = [
  "Dentist", "Standup", "Gym", "Interview — Acme Corp", "1:1 with Sam",
  "Flight to Boston", "Mom's birthday", "", "a", "🎃 Halloween party",
  "Very long title that goes on well past what a card can show",
];

function hueFrom(color) {
  const m = /^hsl\((\d+),/.exec(color);
  assert.ok(m, `expected an hsl() colour, got ${color}`);
  return Number(m[1]);
}

test("the same event is the same colour every time it is drawn", () => {
  for (const title of TITLES) {
    const first = EventColor.of({ title, status: "confirmed" });
    for (let i = 0; i < 5; i++) {
      assert.strictEqual(EventColor.of({ title, status: "confirmed" }), first,
        `"${title}" changed colour between renders`);
    }
  }
});

test("colour comes from the title, not from position or any other field", () => {
  const a = EventColor.of({ title: "Gym", status: "confirmed", starts_at: "2026-01-01T09:00" });
  const b = EventColor.of({ title: "Gym", status: "confirmed", starts_at: "2026-06-14T18:30",
                            conflict: true, all_day: true, from_google: true });
  assert.strictEqual(a, b);
});

test("red is reserved for attention states, never generated", () => {
  // --imp (#ff7a7a) marks overlaps and things needing your yes. A routine
  // appointment landing on the same hue would read as a warning.
  for (const title of TITLES) {
    const hue = hueFrom(EventColor.of({ title, status: "confirmed" }));
    assert.ok(hue >= EventColor.HUE_FLOOR, `${title}: hue ${hue} below floor`);
    assert.ok(hue < EventColor.HUE_FLOOR + EventColor.HUE_SPAN,
      `${title}: hue ${hue} above ceiling`);
    assert.ok(hue > 20 && hue < 345, `${title}: hue ${hue} is in the reserved red band`);
  }
});

test("no title ever escapes the reserved band, over a wide sweep", () => {
  for (let i = 0; i < 5000; i++) {
    const hue = EventColor.hueOf("event-" + i);
    assert.ok(hue >= 25 && hue <= 344, `hue ${hue} out of range for event-${i}`);
  }
});

test("status wins over identity: a hold is amber whatever it is called", () => {
  for (const title of TITLES) {
    assert.strictEqual(EventColor.of({ title, status: "pending" }),
                       EventColor.STATUS_COLOR.pending);
    assert.strictEqual(EventColor.of({ title, status: "countered" }),
                       EventColor.STATUS_COLOR.countered);
  }
});

test("pending and countered are told apart from each other", () => {
  assert.notStrictEqual(EventColor.STATUS_COLOR.pending, EventColor.STATUS_COLOR.countered);
});

test("a confirmed event never borrows a status colour", () => {
  const reserved = new Set(Object.values(EventColor.STATUS_COLOR));
  for (const title of TITLES) {
    assert.ok(!reserved.has(EventColor.of({ title, status: "confirmed" })));
  }
});

test("an event with nothing on it still gets a usable colour", () => {
  // A missing title, a missing status, a missing event: the grid must draw
  // something rather than emit "undefined" into a style attribute.
  for (const input of [{}, { title: "" }, { title: null }, undefined, null]) {
    const color = EventColor.of(input);
    assert.match(color, /^hsl\(\d+, \d+%, \d+%\)$/, `bad colour for ${JSON.stringify(input)}`);
  }
});

test("different titles mostly get different colours", () => {
  // Not a guarantee — a hash has collisions — but a day's worth of events
  // coming out one colour would defeat the point of colouring them at all.
  const seen = new Set(TITLES.map((t) => EventColor.of({ title: t, status: "confirmed" })));
  assert.ok(seen.size >= TITLES.length - 1,
    `${TITLES.length} titles collapsed to ${seen.size} colours`);
});
