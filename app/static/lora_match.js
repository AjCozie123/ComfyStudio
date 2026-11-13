/* Auto LoRA picking: which of your LoRAs fit a prompt, and how strong each should be.

   The server describes every LoRA (lora_index.py): its model family, what kind it is, the phrases it can be picked by
   (trigger words, its name read as words, its title, its tags) and a starting strength. This file decides, for one
   prompt, which LoRAs to use:
     * a phrase only counts as whole words, in order ("greek sandals"); plurals count ("tattoo" = "tattoos")
     * a single ordinary word ("skin", "dark", "light", "size", "back") never picks a LoRA on its own; it can still
       be part of a longer phrase ("wet skin")
     * a phrase the prompt rules out doesn't count: "no tattoos", "remove the jacket", "without glasses"
     * a name phrase can also match when 2+ of its words are in the prompt and at least one is distinctive
       ("2007 phone camera" -> Phone_Photography_2007)
     * copies of one LoRA (its _comfy copy, the checkpoints of one training run, v1/v2) count once: the newest is used
     * at most 3 LoRAs, at most one style and one character, so they don't fight
     * strength: the maker's recommended weight when known, else one by kind (style 0.8, character 1.0 ...), a little
       lower for a weaker match, and lower again when 2 or 3 are stacked (the usual advice is 0.6-0.9 each)
     * when a LoRA was picked by its name and it has a trigger word the prompt doesn't use, the trigger is returned so
       the server can put it in front of the prompt sent to the model (your prompt box isn't changed)
   Adult (NSFW) wording, when the server sends its word lists (opts.concepts ...):
     * slang and synonyms count as the same thing on both sides ("tits" = "boobs" = "breasts", "blow job" = "bj" =
       "blowjob", "naked" = "nude"), so a LoRA named one way is found by a prompt that says it another way
     * undressing counts as nudity, not as a "no": "take off her bra", "she strips", "remove her clothes"
     * a real "no" still blocks: "no nudity", "not naked", "keep her clothes on", "sfw" keep adult LoRAs out
     * a LoRA name with maker / quality words around one adult word ("<maker>_ImprovedNudity") is found by that word
     * one LoRA per adult word of the prompt, so "cum" doesn't stack three cum LoRAs
     * a LoRA made to enlarge (huge / gigantic ...) or shrink isn't picked for "small ..." / "big ..." the other way,
       nor for a bare body word
   Works in the page (window.LoraMatch) and in Node for tests (module.exports). */
(function (root) {
  "use strict";
  const FILLER = new Set(["a", "an", "the", "of", "and", "&", "with", "in", "on", "at", "to", "by", "for", "her", "his",
    "their", "its", "my", "your", "some", "style", "lora"]);
  const NEGATORS = ["no", "not", "without", "remove", "removing", "removed", "delete", "erase", "less", "never", "dont",
    "don't", "instead", "except", "minus", "lose"];
  const NEG_PHRASES = [["take", "off"], ["get", "rid"], ["take", "away"]];

  function stem(w) {
    if (w.length > 4 && /ies$/.test(w)) return w.slice(0, -3) + "y";
    if (w.length > 4 && /(sses|shes|ches|xes)$/.test(w)) return w.slice(0, -2);
    if (w.length > 3 && /s$/.test(w) && !/(ss|us|is)$/.test(w)) return w.slice(0, -1);
    return w;
  }
  function words(text) {
    const t = String(text || "")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .toLowerCase()
      .replace(/[\u2019']/g, "");
    return t.split(/[^a-z0-9]+/).filter(Boolean);
  }
  const stems = (text) => words(text).map(stem);

  /* ---------- the server's word lists, prepared once ---------- */
  const PREP = typeof WeakMap !== "undefined" ? new WeakMap() : null;
  function prep(opts) {
    const key = opts && opts.concepts;
    if (!Array.isArray(key) || !key.length) return null;
    if (PREP && PREP.has(key)) return PREP.get(key);
    const L = { single: new Map(), multi: [], canon: new Set(), implies: new Map(), body: new Set(), big: new Set(),
      small: new Set(), ignore: new Set(), skip: new Map(), und: null, nudeish: new Set(), parts: new Set(),
      phraseCache: new Map(), bias: PREP ? new WeakMap() : null };
    const group = (g, concept) => {
      const list = (g || []).map(stems).filter((x) => x.length);
      if (!list.length) return null;
      const canon = list[0].join("");
      list.forEach((ws) => {
        if (ws.length === 1) { if (!L.single.has(ws[0])) L.single.set(ws[0], canon); if (concept) L.parts.add(ws[0]); }
        else L.multi.push([ws, canon]);
      });
      if (concept) L.canon.add(canon);
      return canon;
    };
    key.forEach((g) => group(g, true));
    const sizes = opts.sizes || {};
    Object.keys(sizes).forEach((k) => {
      const c = group([k].concat(sizes[k] || []), false);
      if (c) (k === "small" ? L.small : L.big).add(c);
    });
    group(["sfw"].concat(opts.sfw || []), false);
    L.multi.sort((a, b) => b[0].length - a[0].length);
    Object.keys(opts.implies || {}).forEach((k) => L.implies.set(stems(k).join(""), (opts.implies[k] || []).map((x) => stems(x).join(""))));
    (opts.body || []).forEach((w) => L.body.add(stems(w).join("")));
    (opts.ignorable || []).forEach((w) => L.ignore.add(stems(w).join("")));
    (opts.nudeish || []).forEach((w) => L.nudeish.add(stems(w).join("")));
    const skip = opts.skip || {};
    Object.keys(skip).forEach((k) => L.skip.set(stem(k), new Set((skip[k] || []).map(stem))));
    const u = opts.undress;
    if (u) {
      const S = (a) => new Set((a || []).map(stem));
      L.und = { verbs: S(u.verbs), pverbs: S(u.pverbs), particles: S(u.particles), garments: S(u.garments),
        bottoms: S(u.bottoms), cancel: S(u.cancel), owners: S(u.owners), full: S(u.full) };
    }
    if (PREP) PREP.set(key, L);
    return L;
  }

  /* "take off her red bra", "she pulls her panties down", "clothes off": spans that mean she is undressing */
  const MALE = new Set(["he", "his", "him", "himself", "man", "men", "guy", "guys", "boy", "boys", "male", "males",
    "husband", "boyfriend", "dude", "gentleman"]);
  const FEMALE = new Set(["she", "her", "hers", "herself", "woman", "women", "girl", "girls", "female", "females",
    "lady", "ladies", "wife", "girlfriend"]);
  function maleOnly(raw, a, b) {
    let m = false;
    for (let k = a; k < b; k++) { if (FEMALE.has(raw[k])) return false; if (MALE.has(raw[k])) m = true; }
    return m;
  }
  function undressSpans(raw, st, L, isNeg) {
    const out = new Map(), U = L.und;
    if (!U) return out;
    const n = st.length;
    for (let i = 0; i < n; i++) {
      const w = st[i];
      if (!U.verbs.has(w) && !U.pverbs.has(w)) continue;
      if (isNeg(i)) continue;   // "don't take off her bra", "without removing her top"
      let g = -1;
      for (let k = i + 1; k <= Math.min(n - 1, i + 5); k++) {
        if (U.garments.has(st[k])) { g = k; break; }
        if (U.cancel.has(st[k]) || st[k] === "and" || st[k] === "but") break;
      }
      if (g < 0) continue;
      let end = g + 1;
      if (U.pverbs.has(w)) {
        let ok = false;
        for (let k = i + 1; k <= Math.min(n - 1, g + 2); k++) if (U.particles.has(st[k])) { ok = true; if (k >= g) end = k + 1; }
        if (!ok) continue;
      } else if (end < n && U.particles.has(st[end])) end++;
      let cancel = false;
      for (let k = g + 1; k <= Math.min(n - 1, g + 6); k++) if (U.cancel.has(st[k])) cancel = true;
      if (cancel) continue;
      if (maleOnly(raw, i + 1, Math.min(n, g + 4))) continue;   // "remove the t-shirt the man is wearing": shirtless, not nudity
      out.set(i, { end, canon: "undress" });
      i = end - 1;
    }
    // "with her bra off", "clothes off", "panties down" (no verb)
    for (let g = 0; g < n - 1; g++) {
      if (!U.garments.has(st[g])) continue;
      const nx = st[g + 1];
      if (!(nx === "off" || (nx === "down" && U.bottoms.has(st[g])))) continue;
      if ([...out.entries()].some(([s, v]) => s <= g && g < v.end)) continue;
      const prev = g > 0 ? st[g - 1] : "";
      if (!(U.full.has(st[g]) || U.owners.has(prev))) continue;
      if (maleOnly(raw, Math.max(0, g - 2), g)) continue;
      let s = g;
      if (U.owners.has(prev)) s = g - 1;
      if (isNeg(s)) continue;
      out.set(s, { end: g + 2, canon: "undress" });
    }
    return out;
  }

  /* text -> tokens. s1: one token per word (filler dropped), with the word's shared meaning as an alternative;
     s2: the same, but known multi-word expressions ("blow job", "in the buff", "take off her bra") are one token.
     For a LoRA phrase (isPrompt false) a token is its shared meaning only. */
  function tokenize(text, L, isPrompt) {
    const raw = words(text);
    const st = raw.map(stem);
    if (!L) {
      const s1 = [];
      raw.forEach((w, i) => { if (!FILLER.has(w)) s1.push({ w: st[i], alts: [], i, raw: w }); });
      return { raw, st, s1, s2: s1 };
    }
    for (let i = 0; i < st.length - 1; i++) {   // "nude lipstick" is a colour, "naked eye" isn't nudity
      const sk = L.skip.get(st[i]);
      if (sk && sk.has(st[i + 1])) st[i] = st[i] + "~";
    }
    const alt = (w) => {
      const a = [];
      const c = L.single.get(w);
      if (c && c !== w) a.push(c);
      (L.implies.get(c || w) || []).forEach((x) => { if (a.indexOf(x) < 0) a.push(x); });
      return a;
    };
    const one = (i) => {
      const c = L.single.get(st[i]);
      return isPrompt ? { w: st[i], alts: alt(st[i]), i, raw: raw[i] } : { w: c || st[i], alts: [], i, raw: raw[i] };
    };
    const s1 = [];
    raw.forEach((w, i) => { if (!FILLER.has(w)) s1.push(one(i)); });
    const isNeg = (i) => negated(raw, i, L);
    const und = undressSpans(raw, st, L, isNeg);
    const s2 = [];
    for (let i = 0; i < st.length;) {
      let m = und.get(i) || null;
      if (!m) {
        for (const [seq, canon] of L.multi) {
          if (seq.length > st.length - i) continue;
          let ok = true;
          for (let j = 0; j < seq.length; j++) if (st[i + j] !== seq[j]) { ok = false; break; }
          if (ok) { m = { end: i + seq.length, canon }; break; }
        }
      }
      if (m) {
        s2.push({ w: m.canon, alts: isPrompt ? (L.implies.get(m.canon) || []).slice() : [], i, raw: raw[i], span: m.end - i });
        i = m.end;
        continue;
      }
      if (!FILLER.has(raw[i])) s2.push(one(i));
      i++;
    }
    if (isPrompt) {   // the size said just before each word ("huge tits", "small breasts")
      [s1, s2].forEach((s) => s.forEach((t, k) => {
        for (let b = k - 1; b >= Math.max(0, k - 3); b--) {
          const x = s[b], f = (set) => set.has(x.w) || x.alts.some((a) => set.has(a));
          if (f(L.small)) { t.size = "small"; break; }
          if (f(L.big)) { t.size = "big"; break; }
        }
      }));
    }
    return { raw, st, s1, s2 };
  }

  // a LoRA phrase as shared-meaning words; glued maker words ("nudemaster" = nude + master) come apart
  function phraseForm(text, L) {
    if (!L) {
      const pw = words(text).filter((w) => !FILLER.has(w)).map(stem);
      return { pw, n: pw.length };
    }
    const hit = L.phraseCache.get(text);
    if (hit) return hit;
    const T = tokenize(text, L, false);
    const pw = [];
    T.s2.forEach((t) => {
      if (!t.span && t.w.length >= 6 && !L.single.has(t.w) && !L.canon.has(t.w)) {
        const parts = split(t.w, L);
        if (parts) { parts.forEach((p) => pw.push(L.single.get(p) || p)); return; }
      }
      pw.push(t.w);
    });
    const n = T.s1.length;   // words as written (so "pussy licking" still counts as a 2-word name)
    const r = { pw, n: Math.max(n, pw.length) };
    L.phraseCache.set(text, r);
    return r;
  }
  function split(w, L) {   // only when every part is known and one of them is an adult word
    const known = (p) => p.length >= 3 && (L.parts.has(p) || L.ignore.has(p));
    const best = [];
    best[0] = [];
    for (let e = 1; e <= w.length; e++) {
      for (let s = Math.max(0, e - 12); s < e; s++) {
        if (best[s] && known(w.slice(s, e))) { best[e] = best[s].concat([w.slice(s, e)]); break; }
      }
    }
    const parts = best[w.length];
    return parts && parts.length >= 2 && parts.some((p) => L.parts.has(p)) ? parts : null;
  }

  // is the word at raw index i ruled out by a "no / without / remove ..." just before it (same clause)?
  // "remove her bra, nude": the "remove" is about the bra, so a word after a piece of clothing isn't ruled out
  function negated(raw, i, L) {
    const gar = L && L.und ? L.und.garments : null;
    for (let k = i - 1; k >= Math.max(0, i - 4); k--) {
      const w = raw[k];
      if (gar && gar.has(stem(w))) return false;
      if (NEGATORS.indexOf(w) >= 0) return true;
      if (NEG_PHRASES.some((p) => raw[k] === p[0] && raw[k + 1] === p[1])) return true;
      if (w === "but" || w === "then" || w === "and" && k < i - 2) break;
    }
    return false;
  }

  const eq = (t, w) => t.w === w || (t.alts.length > 0 && t.alts.indexOf(w) >= 0);
  // where the whole phrase appears in order (first position) in one token stream, or -1
  function findSeq(list, pw) {
    outer: for (let s = 0; s + pw.length <= list.length; s++) {
      for (let j = 0; j < pw.length; j++) if (!eq(list[s + j], pw[j])) continue outer;
      return s;
    }
    return -1;
  }

  function itemBias(it, L) {
    if (!L) return "";
    if (L.bias && L.bias.has(it)) return L.bias.get(it);
    let big = false, small = false;
    (it.phrases || []).forEach((ph) => phraseForm(ph[0], L).pw.forEach((w) => { if (L.big.has(w)) big = true; if (L.small.has(w)) small = true; }));
    const b = big && !small ? "big" : small && !big ? "small" : "";
    if (L.bias) L.bias.set(it, b);
    return b;
  }

  function scoreItem(it, P, generic, L) {
    let best = 0, why = "", via = "", cpos = [], hits = 0;   // cpos: the adult meanings this match used
    const isGeneric = (w) => generic.has(w) || generic.has(w + "s") || w.length < 3;
    const streams = P.s2 === P.s1 ? [P.s1] : [P.s1, P.s2];
    const all = streams.length > 1 ? P.s1.concat(P.s2) : P.s1;
    const present = (w) => all.find((x) => eq(x, w) && !negated(P.raw, x.i, L)) || null;
    const bias = itemBias(it, L);
    const isConcept = (w) => L && L.canon.has(w) && !isGeneric(w);
    (it.phrases || []).forEach((ph) => {
      const text = ph[0], src = ph[1];
      const F = phraseForm(text, L), pw = F.pw;
      if (!pw.length) return;
      let s = 0, toks = null, exact = false;
      for (const list of streams) {
        const at = findSeq(list, pw);
        if (at < 0 || negated(P.raw, list[at].i, L)) continue;
        const allGeneric = pw.every(isGeneric);
        let v;
        if (src === "trigger") v = F.n === 1 && allGeneric ? 2 : 10;
        else if (src === "name" || src === "title") v = F.n >= 2 ? 7 : (allGeneric ? 0 : 6);
        else if (src === "concept") v = 4;   // a general adult LoRA, by what it's for ("nude", "nsfw")
        else v = F.n >= 2 ? 3 : (allGeneric ? 0 : 2);   // tags: supporting evidence only
        if (v > s) { s = v; toks = list.slice(at, at + pw.length); exact = true; }
      }
      if (!s && (src === "name" || src === "title") && pw.length >= 2) {
        // most of a longer name: 2+ of its words, one of them distinctive ("2007 phone camera")
        const found = pw.map(present);
        const hw = pw.filter((w, k) => found[k]);
        const distinct = hw.filter((w) => !isGeneric(w));
        if (hw.length >= 2 && distinct.length >= 1 && hw.length >= pw.length - 1) { s = 4; toks = found.filter(Boolean); }
      }
      if (L && (src === "name" || src === "title" || src === "trigger")) {
        // an adult word with only maker / quality words around it: "<maker> improved nudity", "she has a hairy pussy"
        const eff = pw.filter((w, k) => !L.ignore.has(w) && pw.indexOf(w) === k);
        if (eff.length && eff.length < pw.length && eff.some(isConcept)) {
          const found = eff.map(present);
          const v = eff.length >= 2 ? 5 : 4;
          if (found.every(Boolean) && v > s) { s = v; toks = found; exact = false; }
        }
      }
      if (s && L && !exact) {   // part of "small saggy tits" is not "large and saggy tits"
        const other = (w) => (L.big.has(w) ? L.small : L.small.has(w) ? L.big : null);
        pw.forEach((w) => { const o = other(w); if (o && !present(w) && all.some((t) => o.has(t.w) || t.alts.some((a) => o.has(a)))) s = 0; });
      }
      if (s && L && bias && !pw.some((w) => L.big.has(w) || L.small.has(w))) {
        // a LoRA that makes a body part bigger (or smaller) needs the prompt to say so
        // a bare body word ("tits") needs the size too; "wide hips" or "thick thighs" is enough on its own
        const bare = pw.filter((w) => !L.ignore.has(w)).every((w) => L.body.has(w));
        (toks || []).forEach((t) => {
          if (!pw.some((w) => L.body.has(w) && eq(t, w))) return;
          if (t.size ? t.size !== bias : bare) s = 0;
        });
      }
      if (s) hits++;
      if (s > best) {
        best = s; why = text; via = src;
        cpos = L ? pw.filter((w, k) => (isConcept(w) || (src === "concept" && L.canon.has(w))) && pw.indexOf(w) === k
          && (toks || []).some((t) => eq(t, w))) : [];
      }
    });
    return { score: best, why, via, cpos, hits };
  }

  function round05(x) { return Math.round(x * 20) / 20; }

  /* items: the server's LoRA list. fam: the engine's family key. opts.dismissed: Set of group keys (or names) the
     person turned down. opts.generic: ordinary words; opts.concepts / sizes / body / implies / skip / undress / sfw /
     ignorable / nudeish: the adult word lists (all from the server, /api/loras/index). Returns [{name, strength, why,
     via, kind, group, score, add_trigger, auto: true}] best first, at most 3. */
  function pick(prompt, items, fam, opts) {
    opts = opts || {};
    const generic = opts.generic instanceof Set ? opts.generic : new Set(opts.generic || []);
    const dismissed = opts.dismissed || new Set();
    const L = prep(opts);
    const P = tokenize(prompt, L, true);
    if (!P.s1.length || !fam) return [];
    const promptLow = " " + P.raw.join(" ") + " ";
    // "sfw", "keep her clothes on", "no nudity", "not naked": no adult LoRAs at all
    let sfw = false;
    if (L) {
      sfw = P.s2.some((t) => t.w === "sfw" && !negated(P.raw, t.i, L))
        || P.s1.concat(P.s2).some((t) => (L.nudeish.has(t.w) || t.alts.some((a) => L.nudeish.has(a))) && negated(P.raw, t.i, L));
    }
    const found = [];
    (items || []).forEach((it) => {
      if (it.family !== fam || it.auto === false) return;
      if (dismissed.has(it.group) || dismissed.has(it.name)) return;
      if (sfw && it.nsfw) return;
      const r = scoreItem(it, P, generic, L);
      if (r.score < 4) return;
      found.push(Object.assign({ it }, r));
    });
    // one per group: the best match, then the newest file
    const byGroup = {};
    found.forEach((f) => {
      const g = f.it.group || f.it.name;
      const cur = byGroup[g];
      if (!cur || f.score > cur.score || (f.score === cur.score && (f.it.mtime || 0) > (cur.it.mtime || 0))) byGroup[g] = f;
    });
    const tie = (f) => (f.cpos.length ? f.hits : 0);
    // every word of the prompt that says one of these meanings ("pussy licking" and "cunnilingus" are one place)
    const where = (canons) => {
      const out = new Set();
      if (!canons.length) return out;
      P.s1.concat(P.s2).forEach((t) => {
        if (canons.some((c) => eq(t, c))) for (let k = 0; k < (t.span || 1); k++) out.add(t.i + k);
      });
      return out;
    };
    const ranked = Object.values(byGroup).sort((a, b) => b.score - a.score || tie(b) - tie(a) || (b.it.mtime || 0) - (a.it.mtime || 0));
    const cap = Math.max(1, Math.min(6, parseInt(opts.max, 10) || 3));   // how many Auto-pick may load (your setting)
    const chosen = [];
    const kinds = {};
    const claimed = new Set();
    for (const f of ranked) {
      if (chosen.length >= cap) break;
      const k = f.it.kind || "concept";
      if ((k === "style" || k === "character") && kinds[k]) continue;   // two styles / two people fight each other
      const at = where(f.cpos);
      if ([...at].some((p) => claimed.has(p))) continue;   // one LoRA per adult word ("cum" doesn't load three)
      kinds[k] = (kinds[k] || 0) + 1;
      at.forEach((p) => claimed.add(p));
      chosen.push(f);
    }
    const stack = chosen.length >= 3 ? 0.75 : chosen.length === 2 ? 0.85 : 1;
    return chosen.map((f) => {
      const it = f.it;
      const base = Number(it.strength) || 0.85;
      const conf = f.score >= 7 ? 1 : 0.9;
      let s = base * conf * (it.kind === "character" ? Math.max(stack, 0.9) : stack);
      // below ~0.6 most LoRAs barely show; only go lower when the maker recommends it
      s = Math.max(s, Math.min(base, 0.6));
      s = Math.min(1.2, Math.max(0.3, round05(s)));
      // a trigger word the prompt doesn't have yet (short ones only: long ones are instructions for edit LoRAs)
      let add = "";
      if (f.via !== "trigger") {
        const trig = (it.triggers || []).find((t) => t && t.split(/\s+/).length <= 4 && t.length <= 40);
        const has = (it.triggers || []).some((t) => promptLow.indexOf(" " + words(t).join(" ") + " ") >= 0);
        if (trig && !has) add = trig;
      }
      return { name: it.name, strength: s, why: f.why, via: f.via, kind: it.kind, group: it.group, score: f.score,
               add_trigger: add, folder: it.folder, auto: true };
    });
  }

  const api = { pick, stem, words, negated: (p, i, opts) => negated(words(p), i, prep(opts || {})),
                tokens: (p, opts) => tokenize(p, prep(opts || {}), true) };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.LoraMatch = api;
})(typeof window !== "undefined" ? window : this);
