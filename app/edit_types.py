"""What kind of edit a prompt asks for, so the app's automatic helper lines only go where they fit.

Categories follow the editing benchmarks and vendor guides (GEdit-Bench, ImgEdit, AnyEdit, Emu Edit, ICE-Bench,
KRIS-Bench, Complex-Edit; BFL Kontext / FLUX.2, Google, Qwen, OpenAI, MiniMax H3 hosts). Notes and sources:
research_notes/Face swap adherence and prompt layers/edit_taxonomy.md.

Each helper line ("keep the same clothing", "keep the original lighting", "keep the face") protects something the
benchmarks say a faithful edit keeps. But a line that protects the very thing being edited fights the request
(e.g. "same clothing, same coverage" on a reveal or an outfit change, "same lighting" on "make it night"), so
`vetoes()` names the lines each edit type must not get. This is a veto only: it never adds text.
"""
from __future__ import annotations

import re

_PIC = r"(?:<\s*picture\s*(\d)\s*>|\b(?:picture|image|photo|pic|img)\s*(\d))"

# "keep the background", "while preserving her face", "don't change anything else": not an edit of their own
_KEEP_SPAN = re.compile(
    r"\b(?:keep|keeping|preserve|preserving|maintain|maintaining|retain|retaining|leave|leaving|without changing|"
    r"(?:in|with|at|of) the same|matching|match(?:es)? the|stays?|staying|unchanged|as it is|do not|don'?t|never|"
    r"don'?t change|do not change|don'?t touch|do not touch|same (?:face|pose|background|outfit|clothes|person))\b"
    r"[^.;!?\n]*", re.I)

_W = {
    # identity from another picture
    "HEADSWAP": r"\bhead[- ]?swap\w*|\bswap\w* (?:the |their |her |his )?heads?\b|\b(?:whole|full|entire) head\b"
                r"|\bhead (?:and|&) (?:face|hair)\b|\bface (?:and|&) hair\b|\bhair (?:and|&) face\b"
                r"|\b(?:look|looks|looking) (?:exactly )?like (?:the \w+ (?:from|in) )?" + _PIC,
    "FACESWAP": r"\bface[- ]?swap\w*|\bswap\w* (?:the |their |her |his )?faces?\b|\bdeepfake\b"
                r"|\bface (?:of|from|in|like) (?:the \w+ (?:from|in) )?" + _PIC + r"|\b" + _PIC + r"'?s face\b"
                r"|\bidentity (?:of|from) |\bsame person as\b|\bmake (?:it|her|him) (?:me|him|her)\b|\bgive (?:her|him|them) my face\b",
    "TRYON": r"\b(?:outfit|clothes|clothing|dress|top|shirt|jacket|garments?|costume|uniform|bikini|suit|look)s? (?:from|in|of|shown in) " + _PIC
             + r"|\btry[- ]?on\b|\bswap (?:outfits|clothes)\b|\bsteal the fit\b|\boutfit transfer\b|\bwardrobe swap\b",
    "COMPOSITE": r"\b(?:person|people|man|woman|girl|boy|guy|lady|dog|cat|subject|product|car)\s+from\s+" + _PIC
                 + r"|\b(?:combine|merge) (?:these|them|both)\b|\bboth of them\b|\bgroup photo\b|\binto (?:the scene of )?" + _PIC,
    # people
    "CLOTHES": r"\b(?:outfit|clothes|clothing|dress(?:ed|es)?|shirt|t-shirt|blouse|jacket|coat|hoodie|sweater|jumper|jeans|trousers|"
               r"pants|shorts|skirt|bikini|swimsuit|lingerie|bra|underwear|suit|uniform|costume|sleeves?|hat|cap|glasses|sunglasses|top(?! of)|"
               r"scarf|tie|necklace|earrings?|jewell?ery|shoes|boots|heels|socks|gloves|leggings|sweater|crop top|hijab|"
               r"night(?:gown|dress|ie)|pyjamas|pajamas|robe|gown|thong|panties|stockings|corset|saree|sari|kimono|mask|wearing|wears?)\b",
    "HAIR": r"\b(?:hair|hairstyle|haircut|bangs|fringe|bob|pixie|buzz ?cut|bald|ponytail|braids?|bun|curls?|curly|straighten\w*|"
            r"blonde?|brunette|redhead|highlights|ombre|beard|moustache|mustache|stubble|clean-shaven|shave\w*)\b",
    "EXPR": r"\b(?:smil\w*|grin\w*|laugh\w*|sad|angry|surprised|shocked|frown\w*|serious face|neutral expression|expression|"
            r"close (?:her|his|their) eyes|open (?:her|his|their) (?:eyes|mouth)|eyes (?:open|closed|shut)|wink\w*|pout\w*|"
            r"smirk\w*|cry\w*|raise\w* (?:her|his) eyebrows|look(?:ing)? at the camera|eye contact)\b",
    "RETOUCH": r"\b(?:retouch\w*|beautif\w*|smooth (?:her |his )?skin|blemish\w*|acne|wrinkles?|spots|scars?|whiten\w* teeth|"
               r"makeup|make-up|lipstick|eyeliner|mascara|blush|glam|airbrush\w*|facetune|tan(?:ned)?|red[- ]eye|eye bags)\b",
    "AGE": r"\b(?:older|younger|age (?:him|her|them) (?:up|down)|aged? \d+|as a (?:kid|child|baby|teen)|elderly|de-?age\w*|"
           r"gender ?swap|genderbend\w*|as a (?:woman|man))\b",
    "BODY": r"\b(?:thick(?:er)?|slim(?:mer)?|thin(?:ner)?|skinn(?:y|ier)|curv(?:y|ier)|muscular|buff|ripped|abs|lose weight|"
            r"gain weight|fat(?:ter)?|chubb(?:y|ier)|taller|short(?:er)? (?:body|legs)|broader shoulders|wider hips|"
            r"bigger (?:arms|butt|ass|hips|thighs|chest|breasts?|boobs|tits)|smaller (?:waist|breasts?)|slimmer waist|athletic build|"
            r"bulk up|petite|plus-size|hourglass|bbw|heavier|lighter build"
            r"|(?:ass|butt|bum|hips|thighs|breasts?|boobs|tits|chest|waist|belly|stomach|legs|arms|penis|cock|dick)\b[^.]{0,30}"
            r"\b(?:huge|big|bigger|wide|wider|large|larger|small|smaller|thick|thicker|thin|thinner|long|longer|round|rounder|perkier|saggy|saggier)"
            r"|(?:huge|bigger|wider|larger|smaller|thicker|thinner|longer|rounder|perkier|massive)\s+(?:\w+\s+)?"
            r"(?:ass|butt|bum|hips|thighs|breasts?|boobs|tits|chest|waist|belly|legs|arms|penis|cock|dick))\b",
    "POSE": r"\b(?:make (?:her|him|them) (?:sit|stand|kneel|lie|jump|run|wave|dance|point|lean)|change (?:the|her|his) pose|"
            r"different pose|strike a pose|cross(?:ed|es)? (?:her|his|their) (?:arms|legs)|hands? on (?:her|his) hips|arms up|"
            r"turn (?:her|his) head|look\w* over (?:her|his) shoulder|sitting|kneeling|lying down|leaning|walking|mid-stride|"
            r"cross-legged|legs crossed|hands in (?:her|his) pockets|kiss\w*|hug\w*|cuddl\w*|drink\w*|eat\w*|"
            r"hold\w*(?: up)?|bent over|bend\w*|squat\w*|spread\w*|tongue out|sticking (?:her|his|their) tongue|"
            r"lying on|laying on|on (?:her|his|their) knees|reaching|stretch\w*|pointing)\b",
    # camera / frame
    "VIEW": r"\b(?:from behind|back view|from the (?:back|side)|side profile|three-quarter view|"
            r"bird'?s[- ]eye|top-down|low angle|worm'?s[- ]eye|rotate the camera|orbit\w*|turn the camera|rotate \d+|other side|"
            r"front view|face the camera|different angle|turn (?:her|him|them) around|show (?:her|him|them) from|"
            r"(?:back|front|side)[- ]facing|facing (?:away|the camera|camera)|turn (?:her|his|their|him|them) (?:back|front|body)|"
            r"(?:her|his|their) back (?:is )?(?:to|towards?) (?:the )?camera)\b",
    "FRAME": r"\b(?:zoom\w* in|close-?up|headshot|crop\w*|tighter|reframe|recompose|centre (?:him|her)|center (?:him|her)|"
             r"make it (?:16:9|9:16|vertical|portrait|landscape|square)|waist up|medium shot)\b",
    "OUTPAINT": r"\b(?:extend\w*|expand\w*|outpaint\w*|uncrop\w*|widen the canvas|zoom\w* (?:the camera )?out|show (?:more|the rest|her legs|his legs|her feet|"
                r"his feet|full body|the full)|full body|zoom\w* out|pull back|wider shot|reveal(?:s|ed)?\b|revealing (?:her|his|their|the)|"
                r"uncover\w*|unmask\w*|"
                r"complete the cut-off)\b",
    # objects
    "REMOVE": r"\b(?:remov\w*|delet\w*|eras\w*|get rid of|take (?:out|away)|lose the|ditch the|clean up|clear the|make (?:the |it )?\w* ?disappear|"
              r"no more|without the|edit out|photoshop out|scrub|declutter|empty the)\b",
    "ADD": r"\b(?:add\w*|insert\w*|put (?:a|an|some|the \w+ (?:on|in|next to|beside) the)|place (?:a|an|some)|stick (?:a|an)|"
           r"give (?:her|him|them) (?:a|an|some)|throw in|drop in|include|there should be|must have|"
           r"make (?:her|him|them) hold|holding (?:a|an))\b",
    "REPLACE": r"\b(?:replace\w*|swap \w+ (?:for|with)|swap out|switch \w+ to|instead of|turn (?:the|this|that|it) \w* ?into|"
               r"make the \w+ an? |trade the|sub in)\b",
    "MOVE": r"\b(?:move|shift|slide|nudge|drag|swap positions|make (?:the|it) (?:bigger|smaller|larger|tiny|huge)|scale (?:up|down)|"
            r"enlarge|shrink|rotate the \w+|flip|mirror|duplicate|clone)\b",
    "COUNT": r"\b(?:add (?:two|three|\d+) more|make it (?:two|three|\d+)|only one|fewer|double the|halve|a crowd of|leave just one)\b",
    "COLOR": r"\b(?:recolou?r\w*|repaint\w*|dye\w*|tint\w*|colou?r of|make (?:it|the \w+|her \w+|his \w+) (?:red|blue|green|black|white|"
             r"pink|purple|yellow|orange|brown|grey|gray|navy|gold|silver)|turn (?:it )?(?:red|blue|green|black|white|pink)|#[0-9a-f]{6}"
             r"|change (?:the|her|his|their) (?:\w+ ){1,2}to (?:\w+ )?(?:red|blue|green|black|white|pink|purple|yellow|orange|brown|"
             r"grey|gray|navy|gold|silver|beige|cream))\b",
    "MATERIAL": r"\b(?:made (?:out )?of|out of (?:wood|gold|glass|marble|metal|leather|stone)|chrome|matte|glossy|velvet|denim|"
                r"knitted|crystal|rusty|weathered|change the texture)\b",
    "EXTRACT": r"\b(?:cut (?:her|him|it|them) out|isolate|extract|remove the background|transparent background|no background|"
               r"white background|sticker|just the subject|mask out)\b",
    "TEXT": r"\b(?:text|caption|title|sign|label|spelling|typo|font|translate|watermark|logo)\b|[\"“][^\"”]{1,60}[\"”]",
    # scene / look
    "BG": r"\b(?:background|backdrop|scenery|location|put (?:her|him|them) (?:on|in|at) (?:a|an|the) \w+|teleport\w*|"
          r"take (?:her|him|them) somewhere|bokeh|blurr?(?:ed|y) background)\b",
    "LIGHT": r"\b(?:(?:change|fix|improve|adjust|make|turn|set|add|use|darken|brighten|soften)\s+(?:the\s+|her\s+|his\s+)?"
             r"(?:\w+\s+){0,2}(?:lighting|light)\b(?![- ]?(?:blue|brown|green|pink|grey|gray|purple|yellow|beige|red|"
             r"colou?r\w*|tone\w*|wood))|(?:lighting|light) (?:to|from the (?:left|right|side|window|back))|"
             r"relight\w*|brighter|darker|too dark|underexposed|overexposed|rim light|backlit|backlight\w*|softbox|"
             r"dramatic lighting|moody lighting|neon (?:light|glow)|harsh shadows|softer shadows|candlelit|candlelight)\b",
    "ENV": r"\b(?:night(?:time|s)?(?!\s*(?:gown|dress|wear|ie|club|stand))|at sunset|sunrise|dawn|dusk|golden hour|blue hour|daytime|"
           r"rain(?:y|ing|s)?|snow(?:y|ing|s)?|winter|autumn|in (?:the )?(?:fall|spring|summer)|foggy|fog|misty|stormy|storm|"
           r"cloudy|sunny|clear sky|overcast|rainbow|lightning)\b",
    "TONE": r"\b(?:warmer|cooler|saturat\w*|desaturat\w*|black and white|b&w|grayscale|greyscale|sepia|vintage (?:filter|look)|"
            r"film (?:look|grain)|kodak|colou?r grad\w*|teal and orange|contrast|hdr|exposure|vibrant|muted tones|white balance|"
            r"colou?r correct\w*|filter|faded)\b",
    "STYLE": r"\b(?:anime|ghibli|cartoon\w*|pixar|3d render|oil painting|watercolou?r|pencil sketch|sketch|line art|comic|manga|"
             r"pixel art|low poly|claymation|lego|vaporwave|ukiyo-e|van gogh|pop art|in the style of|art style|stylis\w*|"
             r"styliz\w*|toonify|8-bit|(?:make|turn|convert|render) (?:it|this|her|him) (?:into |as )?(?:a |an )?(?:painting|illustration|drawing)|"
             r"photorealistic|make it (?:real|realistic))\b",
    "RESTORE": r"\b(?:restor\w*|repair\w*|scratches|creases|denois\w*|remove (?:the )?(?:noise|grain|blur)|sharpen\w*|unblur\w*|"
               r"deblur\w*|enhance|upscal\w*|\bhd\b|4k|higher resolution|clearer|jpeg artifacts|fix (?:the |her |his )?(?:hands|face|old photo)|"
               r"de-?pixel\w*)\b",
    "COLORIZE": r"\b(?:coloris\w*|coloriz\w*|add colou?r|make it colou?r|in colou?r|bring this old photo to life)\b",
    "CHARREF": r"\b(?:character sheet|turnaround|model sheet|reference sheet|expression sheet|multiple angles|t-pose|oc sheet)\b",
}
_RX = {k: re.compile(v, re.I) for k, v in _W.items()}

# A worn item named in a "take off / remove" is a clothing edit, not an object removal; a "remove the background" is
# a cut-out; "remove blemishes" is retouching; "remove the noise" is a restore.
_TAKE_OFF = re.compile(r"\b(?:take|took|taking|pull\w*) (?:off|away) (?:her|his|their|the)?\s*\w*|\b(?:remov\w*|without) (?:her|his|their) \w+", re.I)
_NOBODY = re.compile(r"\b(?:empty|nobody|no one|no person|no people|with no (?:person|people|one))\b", re.I)
_PERSON_GONE = re.compile(r"\b(?:remov\w*|delet\w*|eras\w*|get rid of|take out|edit out) (?:the |that |this )?(?:\w+ )?"
                          r"(?:person|people|man|men|woman|women|girl|boy|guy|lady|kid|child|photobomber|crowd|tourists?)\b", re.I)


def strip_keeps(text: str) -> str:
    """The prompt without its "keep ..." clauses (they describe what stays, not an edit)."""
    return _KEEP_SPAN.sub(" ", text or "")


def classify(text: str, n_images: int = 1) -> list:
    """Edit categories the prompt asks for, most specific first. [] when nothing is recognised."""
    t = strip_keeps(text)
    hits = [k for k, rx in _RX.items() if rx.search(t)]
    out = list(hits)
    two_pics = int(n_images or 0) >= 2 or bool(re.search(r"(?:picture|image|photo|pic|img)\s*[2-9]", t, re.I))
    # precedence (taxonomy 4.4): identity from another picture beats plain hair / clothes / remove words
    if "HEADSWAP" in out and "FACESWAP" in out:
        out.remove("FACESWAP")
    if ("HEADSWAP" in out or "FACESWAP" in out) and not two_pics:
        out = [c for c in out if c not in ("HEADSWAP", "FACESWAP")]
    if "HEADSWAP" in out or "FACESWAP" in out:
        out = [c for c in out if c not in ("HAIR", "COMPOSITE")]
    if "TRYON" in out:
        out = [c for c in out if c != "CLOTHES"]
    if "EXTRACT" in out and "REMOVE" in out:
        out.remove("REMOVE")
    if "REMOVE" in out and _TAKE_OFF.search(t) and _RX["CLOTHES"].search(t):
        out.remove("REMOVE")                       # "take off her jacket" = clothes
    if "RESTORE" in out and "REMOVE" in out and re.search(r"remove (?:the )?(?:noise|grain|blur|scratches)", t, re.I):
        out.remove("REMOVE")
    if "COLORIZE" in out and "COLOR" in out:
        out.remove("COLOR")
    if "ENV" in out and "LIGHT" in out and not re.search(r"\blight(?:ing)?\b", t, re.I):
        out.remove("LIGHT")
    # "show her legs / full body" is an outpaint; "show her from behind" is a view
    if "VIEW" in out and "OUTPAINT" in out and not re.search(r"\b(?:extend|reveal|full body|legs|feet|rest)\b", t, re.I):
        out.remove("OUTPAINT")
    return out


def person_removed(text: str) -> bool:
    t = text or ""
    return bool(_PERSON_GONE.search(strip_keeps(t)) or _NOBODY.search(t))


# what a removal verb acts on: "remove the champagne glass out of her hand" -> "champagne glass out of her hand"
_REMOVE_OBJ = re.compile(r"\b(?:remov\w*|delet\w*|eras\w*|get rid of|take (?:off|out|away)|edit out)\s+"
                         r"(?:the |her |his |their |a |an |that |this )?([^.,;:!?\n]{1,40})", re.I)


def removes_non_garment(text: str) -> bool:
    """A removal whose target is not something worn (the "remove only <garment>" line would name the wrong thing)."""
    for m in _REMOVE_OBJ.finditer(strip_keeps(text)):
        head = " ".join(m.group(1).split()[:3])
        if not _RX["CLOTHES"].search(head):
            return True
    return False


def is_compound(cats: list) -> bool:
    core = [c for c in cats if c not in ("LIGHT", "TEXT")]
    return len(core) >= 3


# which automatic helper lines each edit type must NOT get (they protect the very thing being edited)
_VETO = {
    "keep_clothing": {"CLOTHES", "TRYON", "OUTPAINT", "BODY", "VIEW", "EXTRACT", "STYLE", "CHARREF", "AGE", "COMPOSITE"},
    "only_garment": {"TRYON", "OUTPAINT", "BODY", "VIEW", "STYLE", "CHARREF", "HEADSWAP", "FACESWAP", "AGE", "COMPOSITE"},
    "keep_quality": {"STYLE", "TONE", "RESTORE", "COLORIZE", "ENV", "LIGHT", "EXTRACT", "CHARREF"},
    "keep_face": {"FACESWAP", "HEADSWAP", "EXPR", "RETOUCH", "AGE", "STYLE", "CHARREF"},
}


def vetoes(text: str, n_images: int = 1) -> dict:
    """{guard name: reason} for the helper lines that would fight this edit."""
    cats = classify(text, n_images)
    out = {}
    for guard, bad in _VETO.items():
        hit = [c for c in cats if c in bad]
        if hit:
            out[guard] = hit[0]
    if person_removed(text):
        out["keep_clothing"] = "REMOVE person"       # "keep their clothing" tells the model the person stays
    # a removal-only line ("remove only what is named, keep everything else") must not swallow other edits
    if "REMOVE" in cats and [c for c in cats if c not in ("REMOVE", "LIGHT", "TEXT", "CLOTHES")]:
        out["only_target"] = "compound"
    elif "REMOVE" in cats and "CLOTHES" in cats and removes_non_garment(text):
        out["only_target"] = "removes an object, not clothing"
    elif "REMOVE" not in cats and not _TAKE_OFF.search(strip_keeps(text)):
        out["only_target"] = "no removal asked"   # "do not remove ..." / a replace is not a removal
    return out
