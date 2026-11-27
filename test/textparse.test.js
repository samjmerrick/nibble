// The JS reader must give exactly what the original Python reader (legacy/server/textparse.py) gave on these texts.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseText } from "../server/textparse.js";
import * as R from "../server/recipe.js";

const samples = JSON.parse(readFileSync(new URL("./samples.json", import.meta.url)));
const expected = JSON.parse(readFileSync(new URL("./expected.json", import.meta.url)));

samples.forEach((text, i) => test(`sample ${i} matches the Python reader`, () => {
  const d = parseText(text);
  const { title, meta } = R.normalise(d);
  assert.deepEqual(d, expected[i].d);
  assert.equal(R.render(title, meta), expected[i].md);
}));
