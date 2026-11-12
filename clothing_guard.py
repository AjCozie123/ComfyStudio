"""Keep the subject's clothing when an edit isn't about clothing.

Some edits (and some LoRAs) tend to strip or change clothes even when the prompt only asks to turn, pose, light,
or resize something. When the user's own words don't mention clothing, the app adds one short instruction to keep
it, and, for engines that use a negative prompt, a few negative words. When the prompt IS about clothing (remove,
take off, change the outfit, "wearing ...", nude ...), nothing is added, so it never fights what was asked.
"""
from __future__ import annotations

import re

# the prompt is about clothing / undressing: leave it alone
_ABOUT_CLOTHES = re.compile(
    r"\b(remov\w*|take (?:off|away)|taking off|took off|without (?:her|his|their|the|a|any)?\s*\w*|undress\w*|strip\w*|"
    r"naked|nude|nudity|topless|bottomless|unclothed|bare[- ]?(?:chest\w*|breast\w*|skin|body)|"
    r"no (?:clothes|clothing|top|bra|shirt|dress|panties|underwear)|(?:change|swap|replace|switch) (?:her|his|their|the) "
    r"(?:outfit|clothes|clothing|dress|top|shirt|bra|bikini|lingerie|pants|skirt|jacket)|dress (?:her|him|them) (?:in|up)|"
    r"(?:put|puts) (?:her|him|them) in|wear\w*|outfit|costume|lingerie|swimsuit|bikini|bra|panties|underwear|"
    r"unbutton\w*|unzip\w*|pull\w* (?:down|up|off|aside) (?:her|his|their|the)\b)", re.I)
# a garment is described (text to image): keep the person dressed in it
_GARMENT = re.compile(
    r"\b(dress|shirt|t-shirt|blouse|top|jacket|coat|hoodie|sweater|jumper|jeans|trousers|pants|shorts|skirt|suit|"
    r"uniform|gown|robe|corset|leggings|tank top|crop top|bodysuit|jumpsuit|overalls|cardigan|vest)\b", re.I)

NEG_WORDS = "nude, naked, topless, undressed, missing clothing, exposed body"

# headwear that covers the WHOLE head: the hair must disappear inside it (models tend to leave hair poking out)
_FULL_HEAD = re.compile(
    r"\b(full[- ]?(?:face|head)\s+mask|(?:superhero|spider[- ]?man|spiderman|batman|deadpool|venom|ninja|luchador|"
    r"wrestling|ski|gimp|latex|rubber|mascot|horse|animal)\s+(?:mask|hood|head)|balaclava|helmet|motorcycle helmet|"
    r"space helmet|knight'?s? helm|gas mask|hazmat hood|full[- ]head|head[- ]?to[- ]?toe (?:suit|costume)|"
    r"(?:suit|costume|outfit) (?:and|with) (?:a |the )?(?:full )?(?:mask|hood|helmet)|hood up|hooded mask)\b", re.I)
# small coverings where hair normally shows: leave alone
_PARTIAL = re.compile(r"\b(eye mask|half[- ]?mask|domino mask|masquerade mask|face mask|surgical mask|sleep mask|"
                      r"mouth mask|bandana|cap|beanie|hat|headband|visor)\b", re.I)


def headgear_clause(user_prompt, mode, style=""):
    """One line so a full mask / helmet hides the hair, or '' when the prompt adds no full-head covering."""
    t = user_prompt or ""
    if not _FULL_HEAD.search(t) or (_PARTIAL.search(t) and not re.search(r"full|helmet|balaclava|whole head", t, re.I)):
        return ""
    if style == "plain":
        return "It covers the whole head: no hair shows outside it."
    who = "the person in <Picture 1>" if mode == "edit" else "the person"
    return ("The mask / headwear covers the whole head of %s: all of the hair is hidden inside it, "
            "with no hair, ears or skin showing around its edges." % who)


def apply_headgear(prompt_sent, user_prompt, mode, style=""):
    c = headgear_clause(user_prompt, mode, style)
    if not c or "covers the whole head" in (prompt_sent or "").lower():
        return prompt_sent, False
    return (prompt_sent or "").strip().rstrip(" .") + ". " + c, True


def about_clothes(text):
    return bool(_ABOUT_CLOTHES.search(text or ""))


def clause(user_prompt, mode, style=""):
    """The sentence to add (or '' when none is needed). mode: 'edit' or 't2i'. style 'plain' = one short line."""
    t = user_prompt or ""
    if not t.strip() or about_clothes(t):
        return ""
    if mode == "edit":
        if style == "plain":           # Krea 2 Edit: trained on one short instruction
            return "Keep their clothing exactly the same."
        return ("Keep the clothing of the person in <Picture 1> exactly the same: same garments, same coverage, "
                "same colours and fit.")
    if _GARMENT.search(t):              # a new picture that describes clothes
        return "The person stays fully dressed in the clothing described."
    return ""


def apply(prompt_sent, user_prompt, mode, style=""):
    c = clause(user_prompt, mode, style)
    if not c or c.split(":")[0].lower() in (prompt_sent or "").lower():
        return prompt_sent, False
    return (prompt_sent or "").strip().rstrip(" .") + ". " + c, True


def negative(neg, user_prompt, mode):
    """Negative words for engines that use one (CFG above 1)."""
    if not clause(user_prompt, mode) and not (mode == "edit" and not about_clothes(user_prompt) and (user_prompt or "").strip()):
        return neg
    base = (neg or "").strip().rstrip(",")
    if "topless" in base.lower():
        return neg
    return (base + ", " if base else "") + NEG_WORDS
