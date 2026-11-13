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
      .replace(/[’']/g, "");
    return t.split(/[^a-z0-9]+/).filter(Boolean);
  }
  // prompt -> [{w: stem, raw, i}] without filler words, so "dark and light" = "dark light"
  function promptWords(prompt) {
    const raw = words(prompt);
    const out = [];
    raw.forEach((w, i) => { if (!FILLER.has(w)) out.push({ w: stem(w), raw: w, i }); });
    return { list: out, raw };
  }
  function phraseWords(text) { return words(text).filter((w) => !FILLER.has(w)).map(stem); }

  // is the word at raw index i ruled out by a "no / without / remove ..." just before it (same clause)?
  function negated(raw, i) {
    for (let k = i - 1; k >= Math.max(0, i - 4); k--) {
      const w = raw[k];
      if (NEGATORS.indexOf(w) >= 0) return true;
      if (NEG_PHRASES.some((p) => raw[k] === p[0] && raw[k + 1] === p[1])) return true;
      if (w === "but" || w === "then" || w === "and" && k < i - 2) break;
    }
    return false;
  }

  // where the whole phrase appears in order (first position), or -1
  function findSeq(list, pw) {
    outer: for (let s = 0; s + pw.length <= list.length; s++) {
      for (let j = 0; j < pw.length; j++) if (list[s + j].w !== pw[j]) continue outer;
      return s;
    }
    return -1;
  }

  function scoreItem(it, P, generic) {
    let best = 0, why = "", via = "";
    const isGeneric = (w) => generic.has(w) || generic.has(w + "s") || w.length < 3;
    (it.phrases || []).forEach((ph) => {
      const text = ph[0], src = ph[1];
      const pw = phraseWords(text);
      if (!pw.length) return;
      const at = findSeq(P.list, pw);
      let s = 0;
      if (at >= 0) {
        if (negated(P.raw, P.list[at].i)) return;
        const allGeneric = pw.every(isGeneric);
        if (src === "trigger") s = pw.length === 1 && allGeneric ? 2 : 10;
        else if (src === "name" || src === "title") s = pw.length >= 2 ? 7 : (allGeneric ? 0 : 6);
        else s = pw.length >= 2 ? 3 : (allGeneric ? 0 : 2);   // tags: supporting evidence only
      } else if ((src === "name" || src === "title") && pw.length >= 2) {
        // most of a longer name: 2+ of its words, one of them distinctive ("2007 phone camera")
        const hits = pw.filter((w) => P.list.some((x) => x.w === w && !negated(P.raw, x.i)));
        const distinct = hits.filter((w) => !isGeneric(w));
        if (hits.length >= 2 && distinct.length >= 1 && hits.length >= pw.length - 1) s = 4;
      }
      if (s > best) { best = s; why = text; via = src; }
    });
    // several tag hits add up a little (a style LoRA tagged "documentary, street, monochrome")
    return { score: best, why, via };
  }

  function round05(x) { return Math.round(x * 20) / 20; }

  /* items: the server's LoRA list. fam: the engine's family key. opts.dismissed: Set of group keys (or names) the
     person turned down. opts.generic: ordinary words (from the server). Returns [{name, strength, why, via, kind,
     group, score, add_trigger, auto: true}] best first, at most 3. */
  function pick(prompt, items, fam, opts) {
    opts = opts || {};
    const generic = opts.generic instanceof Set ? opts.generic : new Set(opts.generic || []);
    const dismissed = opts.dismissed || new Set();
    const P = promptWords(prompt);
    if (!P.list.length || !fam) return [];
    const promptLow = " " + P.raw.join(" ") + " ";
    const found = [];
    (items || []).forEach((it) => {
      if (it.family !== fam || it.auto === false) return;
      if (dismissed.has(it.group) || dismissed.has(it.name)) return;
      const r = scoreItem(it, P, generic);
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
    const ranked = Object.values(byGroup).sort((a, b) => b.score - a.score || (b.it.mtime || 0) - (a.it.mtime || 0));
    const chosen = [];
    const kinds = {};
    for (const f of ranked) {
      if (chosen.length >= 3) break;
      const k = f.it.kind || "concept";
      if ((k === "style" || k === "character") && kinds[k]) continue;   // two styles / two people fight each other
      kinds[k] = (kinds[k] || 0) + 1;
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

  const api = { pick, stem, words, negated: (p, i) => negated(words(p), i) };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.LoraMatch = api;
})(typeof window !== "undefined" ? window : this);
