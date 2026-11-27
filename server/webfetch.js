// Fetch a public web page: readable text, title, image and schema.org JSON-LD (port of webfetch.py).
// Uses HTMLRewriter, which parses in native code, so even big pages cost almost no Worker CPU time.
import { UserError, unescapeHtml } from "./recipe.js";

const MAX_BYTES = 4 * 1024 * 1024;
const MAX_JSONLD = 200_000;
const UA = "Mozilla/5.0 (compatible; nibble/1.0)";
const adapters = [];
export const addAdapter = (name, match, fetchFn) => adapters.push({ name, match, fetch: fetchFn });

export function checkUrl(url) {
  let u;
  try { u = new URL(url); } catch { throw new UserError("only public http(s) links can be fetched"); }
  if (!["http:", "https:"].includes(u.protocol) || !u.hostname) throw new UserError("only public http(s) links can be fetched");
  if (u.port && !["80", "443"].includes(u.port)) throw new UserError("only standard ports can be fetched");
  const h = u.hostname.toLowerCase();
  // Workers can't reach private networks anyway; refuse obvious private names and IP literals up front.
  if (h === "localhost" || h.endsWith(".local") || h.endsWith(".internal") || /^[\d.]+$/.test(h) || h.includes(":")) throw new UserError(`${h} is not a public address`);
  return u.toString();
}

export async function get(url, accept = "text/html,application/xhtml+xml") {
  checkUrl(url);
  const res = await fetch(url, { headers: { "User-Agent": UA, Accept: accept, "Accept-Language": "en-GB,en;q=0.8" }, redirect: "follow", signal: AbortSignal.timeout(20000) });
  if (res.url) checkUrl(res.url);
  if (!res.ok) throw new UserError(`HTTP ${res.status}`);
  return res;
}

export async function getJson(url) { return (await get(url, "application/json")).json(); }

async function parsePage(res, url, maxText) {
  let title = "", ogImage = null;
  const ld = [];
  const out = [];
  let skip = 0;
  const rewriter = new HTMLRewriter()
    .on("title", { text(t) { title += t.text; } })
    .on('meta[property="og:image"]', { element(e) { if (!ogImage) ogImage = e.getAttribute("content"); } })
    .on('script[type="application/ld+json"]', { element() { ld.push(""); }, text(t) { ld[ld.length - 1] += t.text; } })
    .on("title, script, style, noscript, svg, nav, footer, header, form, iframe, aside", { element(e) { skip++; e.onEndTag(() => { skip--; }); } })
    .on("p, li, br, div, h1, h2, h3, h4, h5, h6, tr, section, article", { element(e) { out.push("\n"); e.onEndTag?.(() => out.push("\n")); } })
    .on("*", { text(t) { if (!skip) out.push(t.text); } });
  const body = rewriter.transform(new Response(res.body.pipeThrough(limit(MAX_BYTES))));
  await body.arrayBuffer();
  const text = out.join("").replace(/[ \t\r\f\v]+/g, " ").replace(/\n\s*\n+/g, "\n").trim();
  const jsonld = []; let size = 0;
  for (const blob of ld) {
    let obj; try { obj = JSON.parse(blob.trim()); } catch { continue; }
    size += blob.length; if (size > MAX_JSONLD) break;
    jsonld.push(obj);
  }
  return { url, title: unescapeHtml(title).trim(), image: ogImage, jsonld, text: text.slice(0, maxText), text_truncated: text.length > maxText };
}

function limit(max) {
  let n = 0;
  return new TransformStream({ transform(chunk, c) { n += chunk.byteLength; if (n > max) c.terminate(); else c.enqueue(chunk); } });
}

export async function fetchPage(url, maxText = 15000) {
  checkUrl(url);
  for (const a of adapters) if (a.match(url)) { const out = await a.fetch(url); out.source ??= a.name; return out; }
  const res = await get(url);
  const ctype = res.headers.get("content-type") ?? "";
  if (!ctype.includes("html") && !ctype.includes("xml")) throw new UserError(`that link is not a web page (${ctype || "unknown type"})`);
  return parsePage(res, res.url || url, maxText);
}
