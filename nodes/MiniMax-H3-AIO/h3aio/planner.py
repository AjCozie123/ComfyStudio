"""Seamless Planner: one long story -> one correctly formatted H3 prompt per clip.

Rules come from MiniMax's official prompt guides (MiniMaxAI/MiniMax-H3 docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md
and _ref_en.md) plus Aitrepreneur's tested extension wording. No language model: the same story always gives
the same plan, and the plan output shows exactly what every clip receives.

Story -> beats:
  * H3 section labels are recognised. subject_definitions / summary / retention_analysis / overall_soundscape /
    non_diegetic_music are global and go into every clip (soundscape and music describe the whole video).
  * The time-ordered body is cut into beats, in this order of preference:
      - times written in the story ("[Shot 2] At 00:05.000", "5-10s:", "[0s-5s]", "at 12s", "00:15"),
      - explicit breaks (a line of ---, or "Clip 2:" / "Part 2:" / "Scene 2:"),
      - otherwise sentences (never inside <d>...</d>), spread evenly over the clips.
Beats -> clips:
  * Each beat goes to the clip whose time window holds it.
  * Only a real cut (a [Shot N] in the story) gets a timestamp; the guides reserve "At MM:SS.mmm" for cut times.
    Its time is rewritten relative to the clip (later clips begin with the carried-over context, so new content
    starts after it). Other timed beats continue inside the current shot.
  * The style / opening composition goes at the start of [Shot 1] (text modes) or as a sentence before [Shot 1]
    (reference modes), as the guides require.
  * Clips after the first state, inside [Shot 1], that they continue with no cut from the carried-over opening
    (authoritative for pose, motion, camera, lighting, voice) and what that opening shows; speakers keep their
    (Sx) ID and identity description. Every clip but the last ends on a lead-in to the next clip's first beat.
  * Carry-over and lead-in sentences never contain dialogue, so H3 never says a line twice.
"""

import math
import re

GLOBAL_LABELS = ("subject_definitions", "summary", "retention_analysis", "overall_soundscape", "non_diegetic_music")
BODY_LABELS = ("integrated_multimodal_description", "detailed_description")
LABEL_RE = re.compile(r"^\s*(" + "|".join(GLOBAL_LABELS + BODY_LABELS) + r")\s*:\s*", re.I | re.M)
INSTRUCTION_RE = re.compile(r"^\s*(For the target video, at 0\.00 seconds.*?|How the reference pictures align.*?)\n", re.I)

TIME_RE = re.compile(
    r"(?P<shot>\[\s*shot\s*\d+\s*\]\s*)?"
    r"(?:\bat\s+)?"
    r"[\[(]?\s*"
    r"(?:(?P<m>\d{1,2}):(?P<s>\d{1,2}(?:\.\d+)?)"
    r"|(?P<a>\d+(?:\.\d+)?)\s*s?\s*(?:-|–|to)\s*(?P<b>\d+(?:\.\d+)?)\s*s(?:ec(?:onds?)?)?\b"
    r"|(?:at\s+)?(?P<x>\d+(?:\.\d+)?)\s*s(?:ec(?:onds?)?)?\b)"
    r"\s*[\])]?\s*[:,\-–]?\s*",
    re.I)
SHOT_ONLY_RE = re.compile(r"\[\s*shot\s*(\d+)\s*\]\s*", re.I)
BREAK_RE = re.compile(r"^\s*(?:-{3,}|(?:clip|part|scene|segment)\s*\d+\s*[:.\-])\s*", re.I | re.M)
SENT_RE = re.compile(r"(?<=[.!?\x00])\s+(?=[A-Z\[(<\x00])")
DIALOGUE_RE = re.compile(r"<d>.*?</d>", re.S)
SPEAKER_RE = re.compile(r"\((S\d+(?:,\s*S\d+)*)\)")
CUT_WORDS_RE = re.compile(r"^(the\s+)?(camera|shot|scene)\s+(cuts|switches|changes|transitions)\b", re.I)

CARRY = ("The shot continues seamlessly with no cut, reset or re-establishment from the carried-over opening frames, which "
         "are authoritative for pose, motion, camera position and movement, lighting, environment, voice and ambience")
REF_SUMMARY = ("The target video continues seamlessly, with no cut, from its carried-over opening frames (the previous "
               "clip's final moment); reference images keep identity and appearance only, never pulling pose, "
               "framing or lighting back toward them.")


# ---------------- parsing ----------------

def _split_labels(text):
    """-> (instruction line or '', globals {label: text}, body, style line or '')"""
    instruction = ""
    m = INSTRUCTION_RE.match(text)
    if m:
        instruction, text = m.group(1).strip(), text[m.end():]
    parts = LABEL_RE.split(text)
    head, globs, body = parts[0].strip(), {}, []
    for label, content in zip(parts[1::2], parts[2::2]):
        label = label.lower()
        if label in GLOBAL_LABELS:
            globs[label] = content.strip()
        else:
            body.append(content.strip())
    body = "\n".join(body)
    style = ""
    if not body:
        body = head
    elif head:
        style = head
    m = re.search(r"\[\s*shot\s*1\s*\]", body, re.I)
    if m and body[:m.start()].strip():
        style = (style + " " + body[:m.start()].strip()).strip()
        body = body[m.start():]
    return instruction, globs, body, style


def _sentences(text):
    tags = []

    def keep(m):
        tags.append(m.group(0))
        return f"\x00{len(tags) - 1}\x00"
    protected = DIALOGUE_RE.sub(keep, text)
    out = [s.strip() for s in SENT_RE.split(protected) if s.strip()]
    return [re.sub(r"\x00(\d+)\x00", lambda m: tags[int(m.group(1))], s) for s in out]


def _timed_beats(body):
    """[(seconds, is_cut, text)] from the times written in the story, or None when it has none."""
    marks = []
    for m in TIME_RE.finditer(body):
        raw = body[:m.start()]
        if raw.strip() and not re.search(r"(?:[.!?\]>\"]|\n)\s*$", raw):
            continue   # "waits 5 seconds" mid-sentence is not a time mark
        if m.group("m") is not None:
            t = int(m.group("m")) * 60 + float(m.group("s"))
        elif m.group("a") is not None:
            t = float(m.group("a"))
        else:
            t = float(m.group("x"))
        marks.append((m.start(), m.end(), t, bool(m.group("shot"))))
    if not marks or (len(marks) < 2 and marks[0][2] == 0):
        return None
    beats = []
    first = SHOT_ONLY_RE.sub("", body[:marks[0][0]]).strip()
    if first:
        beats.append((0.0, False, first))
    for i, (s, e, t, shot) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        text = SHOT_ONLY_RE.sub("", body[e:end]).strip()
        if text:
            beats.append((t, shot and t > 0, text))
    return beats


def _break_blocks(body):
    blocks = [b.strip() for b in BREAK_RE.split(body) if b.strip()]
    return blocks if len(blocks) > 1 else None


def _speakers(text):
    """(Sx) -> the identity phrase written before its first appearance ('the young woman with curly hair')."""
    out = {}
    for m in SPEAKER_RE.finditer(text):
        sid = m.group(1)
        if sid in out or "," in sid:
            continue
        before = re.split(r"[.!?:;\]]\s*|\n|</d>", text[:m.start()])[-1].strip(" ,")
        words = before.split()
        phrase = " ".join(words[-12:])
        phrase = re.sub(r"^(and|then|while|as|when)\s+", "", phrase, flags=re.I)
        if phrase:
            out[sid] = _lower_first(phrase)
    return out


# ---------------- carry-over helpers ----------------

SPEECH_TAIL_RE = re.compile(r"[,;]?\s*(?:and\s+|then\s+|while\s+)?\b(?:says|said|asks|shouts|whispers|replies|"
                            r"exclaims|sings|calls out|answers)\b[^.!?]*$", re.I)
CUT_LEAD_RE = re.compile(r"^(?:the\s+)?(?:camera|shot|scene)\s+(?:cuts|switches|changes|transitions)\s+to\s+"
                         r"(?:a new angle (?:as|where)\s+)?", re.I)


def _no_dialogue(sentence):
    """A sentence that can be repeated in another clip: no spoken words, no 'says', no cut phrasing."""
    text = DIALOGUE_RE.sub("", sentence)
    text = SPEAKER_RE.sub("", text)
    text = SPEECH_TAIL_RE.sub("", text.strip())
    text = CUT_LEAD_RE.sub("", text.strip())
    text = re.sub(r"\s+", " ", text).strip(" :,")
    return text if len(text.split()) >= 3 else ""


def _pick(beat_texts, last):
    sents = [x for t in beat_texts for x in _sentences(SHOT_ONLY_RE.sub("", t))]
    if last:
        sents = sents[::-1]
    for prefer_clean in (True, False):
        for sent in sents:
            clean = _no_dialogue(sent)
            if clean and (not prefer_clean or not DIALOGUE_RE.search(sent)):
                return clean.rstrip(".") + "."
    return ""


def _lower_first(text):
    return text[0].lower() + text[1:] if text[:1].isupper() and not text[1:2].isupper() else text


def _drop_pictures(text):
    text = re.sub(r"\s*(?:shown |seen )?(?:in|from) <Picture \d+>", "", text)
    text = re.sub(r"<Picture \d+>", "the image", text)
    return text


def _ts(seconds):
    return f"{int(seconds // 60):02d}:{seconds % 60:06.3f}"


# ---------------- plan ----------------

def plan(story, clip_seconds, clip_count, context_seconds, reference_mode=False):
    """-> list of clip prompts (clip 1 first); clip_count includes the first clip."""
    instruction, globs, body, style = _split_labels(story.strip())
    speakers = _speakers(body)

    clips = [[] for _ in range(clip_count)]       # per clip: [(local seconds or None, is_cut, text)]
    beats = _timed_beats(body)
    if beats:
        for t, cut, text in beats:
            k = min(clip_count - 1, int(t // clip_seconds + 1e-9)) if clip_seconds > 0 else 0
            local = t - k * clip_seconds + (context_seconds if k else 0)
            clips[k].append((local, cut, text))
    else:
        blocks = _break_blocks(body)
        if blocks and len(blocks) >= clip_count:
            per = len(blocks) / clip_count
            for i, b in enumerate(blocks):
                clips[min(clip_count - 1, int(i / per))].append((None, False, SHOT_ONLY_RE.sub("", b).strip()))
        else:
            sents = _sentences(SHOT_ONLY_RE.sub("", " ".join(blocks) if blocks else body))
            total = sum(len(s) for s in sents) or 1
            acc = 0
            for s in sents:
                clips[min(clip_count - 1, int(acc / total * clip_count))].append((None, False, s))
                acc += len(s)
    for k in range(clip_count):
        if not clips[k]:
            clips[k] = [(None, False, "The action continues naturally from the previous moment, with small natural "
                                      "movement and the same camera.")]

    prompts = []
    for k, beats_k in enumerate(clips):
        shots, shot_no = [[]], 1
        for local, cut, text in beats_k:
            text = text.strip()
            if cut and local is not None and (k > 0 or local > 0.05):
                shot_no += 1
                if not CUT_WORDS_RE.match(text):
                    if re.match(r"^(she|he|they|it|we|i|you|her|his|their)\b", text, re.I):
                        text = "the camera cuts to a new angle as " + _lower_first(text)
                    else:
                        text = "the camera cuts to " + _lower_first(text)
                shots.append([f"[Shot {shot_no}] At {_ts(local)}, {_lower_first(text)}"])
            else:
                shots[-1].append(text)

        opening = []
        if k > 0:
            state = _pick([t for _, _, t in clips[k - 1]], last=True)
            opening.append(CARRY + ".")
            if state:
                opening.append(f"It opens on the moment the previous clip ended: {_lower_first(state)}")
            voices = [f"{phrase} ({sid})" for sid, phrase in speakers.items()
                      if any(sid in t for _, _, t in beats_k)]
            if voices:
                opening.append("The same speakers continue: " + "; ".join(voices) + ".")
        first_shot = " ".join(opening + shots[0])
        if k + 1 < clip_count:
            lead = _pick([t for _, _, t in clips[k + 1]], last=False)
            if lead and len(shots) == 1:
                first_shot += f" The clip ends just as the next moment begins: {_lower_first(lead)}"
            elif lead:
                shots[-1].append(f"The clip ends just as the next moment begins: {_lower_first(lead)}")

        if reference_mode:
            lead_style = style or "The target video keeps the same visual style throughout."
            body_k = lead_style + "\n[Shot 1] " + first_shot + "".join("\n" + " ".join(s) for s in shots[1:])
            parts = []
            if "subject_definitions" in globs:
                parts.append("subject_definitions:\n" + globs["subject_definitions"])
            summary = globs.get("summary", "")
            if k > 0:
                summary = (summary + " " if summary else "[reference generation] ") + REF_SUMMARY
            if summary:
                parts.append("summary:\n" + summary)
            if "retention_analysis" in globs:
                parts.append("retention_analysis:\n" + globs["retention_analysis"])
            parts.append("detailed_description:\n" + body_k)
        else:
            if k > 0:
                first_shot = _drop_pictures(first_shot)
                shots = [[_drop_pictures(x) for x in sh] for sh in shots]
            shot1 = (style.rstrip(". ") + ". " if style else "") + first_shot
            body_k = "[Shot 1] " + shot1 + "".join(" " + " ".join(s) for s in shots[1:])
            parts = [f"integrated_multimodal_description: {body_k}"]
        parts.append("overall_soundscape: " + globs.get("overall_soundscape", "Natural ambience matching the scene "
                                                                               "continues throughout."))
        parts.append("non_diegetic_music: " + globs.get("non_diegetic_music", "N/A"))
        text = "\n\n".join(parts)
        if k == 0 and instruction:
            text = instruction + "\n\n" + text
        prompts.append(text)
    return prompts


def clip_layout(total_seconds, clip_seconds, max_clips):
    """-> (clip_count, [new seconds of each extra clip]); the last clip takes the remainder (at least 1 s)."""
    count = max(1, min(max_clips, math.ceil(total_seconds / clip_seconds - 1e-6)))
    extras = [max(1.0, min(clip_seconds, total_seconds - k * clip_seconds)) for k in range(1, count)]
    return count, extras
