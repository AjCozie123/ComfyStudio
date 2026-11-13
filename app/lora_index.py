"""What each LoRA on this PC is for, so the app can pick LoRAs from the words in a prompt.

Works for anyone's LoRAs, labelled or not. For every LoRA it collects:
  * the model family it was made for: the .safetensors header (ss_base_model_version / modelspec.architecture),
    else the folder or file name. A LoRA whose family can't be told is never auto-picked.
  * its trigger words: the header (modelspec.trigger_phrase, ss_tag_frequency ...), plus the info files other tools
    keep next to LoRAs (LoRA Manager <name>.metadata.json, Civitai Helper <name>.civitai.info, A1111 <name>.json)
    and a lora_cards.json in the LoRA folder ({"folder/file.safetensors": {"title", "triggers", "tags", ...}})
  * the phrases it can be picked by (its trigger words, its name read as words, its title), what kind of LoRA it is,
    and a starting strength (the maker's recommended weight when one is written down, else one by kind)
  * which LoRAs are copies of each other (the _comfy copy, the training checkpoints of one run, v1/v2), so only one
    of them is used
Speed LoRAs (turbo / lightning / distilled), tool LoRAs (control, inpaint, relight, swaps, edits) and sliders (where
the sign of the strength matters) are never auto-picked: they belong to a workflow, not to the words of a prompt.
The matching itself runs in the page (static/lora_match.js) so the picks update while you type.
Header reads are cached in data/lora_index.json by path + size + date.
"""
from __future__ import annotations

import json, os, re, struct

# family keys shared with the app's engines
FAMILY_WORDS = [
    ("minimax_h3", ("minimax", "h3_", "h3-", "_h3", "-h3", "mmh3")),
    ("krea2", ("krea2", "krea_2", "krea-2", "krea 2")),
    ("qwen_image", ("qwen_image", "qwen-image", "qwenimage", "qwen image", "qwen")),
    ("flux2", ("flux2", "flux.2", "flux-2", "flux_2", "klein")),
    ("flux1", ("flux1", "flux.1", "flux-1", "flux_1", "flux-dev", "flux_dev", "flux")),
    ("moss", ("moss", "kromoss")),
    ("sdxl", ("sdxl", "sd_xl", "pony", "illustrious", "noobai")),
    ("sd15", ("sd15", "sd1.5", "sd_1.5", "v1-5")),
    ("ltx", ("ltx",)),
    ("wan", ("wan2", "wan_2", "wan-2", "wan21", "wan22", "wan2.1", "wan2.2", "wan ")),
    ("hunyuan", ("hunyuan",)),
    ("z_image", ("z_image", "z-image", "zimage")),
]
ENGINE_FAMILY = {  # engine card family -> key
    "MiniMax H3": "minimax_h3", "MiniMax H3 Fast": "minimax_h3", "Krea 2": "krea2", "Qwen Image": "qwen_image",
    "FLUX.2 Klein": "flux2", "FLUX.3 Klein": "flux2", "FLUX.1": "flux1", "Flux": "flux1", "MOSS": "moss",
    "SDXL": "sdxl", "LTX Video": "ltx", "Wan": "wan", "Hunyuan Video": "hunyuan", "Hunyuan": "hunyuan",
    "Klein": "flux2", "Moss": "moss",
}

# words in LoRA names that say nothing about what the LoRA does (model names, versions, trainer words)
NAME_NOISE = set("""lora loras model models v1 v2 v3 v4 v5 v6 final rank step steps epoch fp16 bf16 fp8 int8 comfyui comfy
diffusers kohya ostris aitoolkit safetensors base dev pro version new old test copy official standard mlx by the and for
of with flux flux1 flux2 klein klein9b kleinbase kleinbase9b 9b 4b 22b 19b f2k f2k9b f2k9bv1 k9b k9 kl fk fluxklein
fluxklein9b flux2klein flux2k9b krea krea2 kr2 kr qwen qwen2 ltx ltx2 ltx23 minimax mmh3 h3 mm minmaxh3 sdxl pony moss
wan hunyuan zimage am tk ema pruned resized avg prodigy adamw patched extracted refined lora_ helper enhancer
turbo lightning lightx2v distilled distill dmd dmd2 lcm hyper fast speed ckpt redmond""".split())

# words too ordinary to pick a LoRA on their own (they can still pick one as part of a longer phrase)
GENERIC = set("""a an the this that his her their its my your our of in on at to by for from with and or as into onto over under
make made making change changed turn add put give keep set use show let be is are was it them him she he they image picture
photo photos photograph photography pic shot shots camera phone selfie still frame scene view style styled look looks
looking real realistic realism natural raw high quality detail details detailed sharp ultra hyper super best good nice
person people woman women man men girl girls boy boys lady guy female male young old adult character subject someone
face head hair eye eyes nose mouth lip lips ear ears neck shoulder shoulders arm arms hand hands finger fingers leg legs
foot feet back front side body skin chest belly waist hip hips thigh thighs knee knees
light lights lighting dark darker bright brighter shadow shadows color colour colors tone tones warm cool soft hard
size big bigger large larger small smaller tiny huge long short tall wide thin thick fat slim full half
standing sitting lying walking running holding wearing posing pose poses down up left right top bottom close closer
inside outside indoor outdoor room house street city road beach field forest park garden sky water sea night day
morning evening sunset sunrise rain snow background foreground white black red blue green yellow pink purple orange
brown grey gray gold silver new fake only through see wet dry dirty clean direction motion move moving
clothes clothing outfit dress shirt top pants jeans skirt shorts jacket coat hat shoe shoes boots bag
bikini swimsuit lingerie underwear bra panties socks gloves glasses sunglasses
crotch cleavage bust abs
muscle muscles muscular curvy thick thickness slim facial expression covered bounce bouncing crowded crowd zoom
micro mini cinematic film movie vintage retro
anatomy beautiful pretty cute hot sexy model art artistic painting drawing render illustration
nsfw sex sexual porn porno explicit erotic erotica xxx lewd horny naughty seductive sensual provocative""".split())
# explicit body words can pick a LoRA now ("pussy close-up"), but they are still too common in training captions and
# tag lists to become a trigger or a tag phrase on their own
BODY_WORDS = set("breast breasts boob boobs ass butt buttock buttocks nipple nipples penis vagina pussy".split())
_STOP_TAGS = GENERIC | BODY_WORDS

KIND_DEFAULT_STRENGTH = {"character": 1.0, "style": 0.8, "clothing": 0.85, "body": 0.75, "pose": 0.8, "concept": 0.85,
                         "quality": 0.7, "nsfw": 0.8}

_RE_UTILITY = re.compile(r"turbo|lightning|lightx2v|distill|dmd2?|\blcm\b|hyper[-_ ]?sd|\d+[-_ ]?steps?\b|speed|"
                         r"\bfast\b|accelerat", re.I)
_RE_SLIDER = re.compile(r"slider|loraholic", re.I)
_RE_TOOL = re.compile(r"control|depth|canny|refcontrol|pose[-_ ]?transfer|\bic[-_ ]lora|inpaint|outpaint|uncrop|"
                      r"relight|delight|controllight|restore|upscal|consist|swap|bfs_|\bbfs\b|anything2real|anime2real|"
                      r"real2anime|change[-_ ]scene|\bedit\b|_edit|edit_|remover?\b|remove[-_ ]|multiple[-_ ]angles|"
                      r"cameraman|camera[-_ ]control|dolly|jib|motion[-_ ]?(booster|track)|ref2va|ref2v|fl2v|"
                      r"transition|henshin|union|\bhdr\b|viggle|\bfix\b|\bsft\b|id[-_ ]lora|sun[-_ ]direction|"
                      r"edit(?!orial)|reference|zoom|"
                      r"matching[-_ ]?pose|maching[-_ ]?pose|mannequin|inflate|infl8|optimizer|\.pt$", re.I)
_RE_QUALITY = re.compile(r"realism|realistic|real[-_ ]?skin|ultra[-_ ]?real|detailer|\bdetail|dpo\b|fidelity|enhance|"
                         r"crisp|refusal|uncensored|aio\b|nsfw[-_ ]?aio|sharpen", re.I)
_RE_STYLE = re.compile(r"style|anime|illustrat|painting|film|photograph|cinemat|redmond|analog|aesthetic|comic|cartoon|"
                       r"claymation|ghibli|vintage|retro|\d0s|grainy|horror|thriller|sci[-_ ]?fi|neon|cyber|pencil|"
                       r"simpsons|spider[-_ ]verse|3d ?render|snapshot|cctv|nokia|iphone|phone[-_ ]photo|instagram|"
                       r"candid|selfie|playboy|melancholy|vanguard", re.I)
_RE_CLOTHING = re.compile(r"bikini|lingerie|dress|saree|sari\b|kameez|salwar|sandal|shoe|outfit|cloth|towel|panties|"
                          r"thong|sheer|see[-_ ]?through|transparent|cosplay|uniform|stocking|corset|swimsuit", re.I)
_RE_POSE = re.compile(r"pose|posing|lying|kneel|squat|bent|from[-_ ]behind|rear[-_ ]view|face[-_ ]?down", re.I)
_FOLDER_KIND = [  # folder words -> kind
    (re.compile(r"turbo|lightning|speed", re.I), "utility"),
    (re.compile(r"slider", re.I), "slider"),
    (re.compile(r"body ?control|anatomy|body", re.I), "body"),
    (re.compile(r"control|edit|relight|upscal|swap|anything2real|restore", re.I), "tool"),
    (re.compile(r"face lora|people|character|person|celeb", re.I), "character"),
    (re.compile(r"style|anime|art", re.I), "style"),
    (re.compile(r"cloth|outfit|wear", re.I), "clothing"),
    (re.compile(r"pose", re.I), "pose"),
    (re.compile(r"anatomy|body", re.I), "body"),
    (re.compile(r"realism|quality", re.I), "quality"),
]
NEVER_AUTO = {"utility", "tool", "slider"}

# ---------------------------------------------------------------- adult (NSFW) wording
# Prompts and LoRA names say the same adult thing in many ways. Each group is one meaning: its first word is the
# meaning's key, the rest are slang, synonyms and multi-word ways of saying it. The page matches a prompt word to a LoRA
# phrase when both are in one group (static/lora_match.js). English only, like GENERIC.
_PRON = ("", "her ", "his ", "their ", "the ", "my ", "your ", "any ", "a ")


def _px(*phrases):
    """'without {p}clothes' -> 'without clothes', 'without her clothes', 'without his clothes' ..."""
    out = []
    for ph in phrases:
        for p in (_PRON if "{p}" in ph else ("",)):
            t = " ".join(ph.replace("{p}", p).split())
            if t not in out:
                out.append(t)
    return out


ADULT_CONCEPTS = [
    ["nude", "naked", "nudity", "nudes", "nudist", "nekkid", "unclothed", "disrobed", "in the buff", "birthday suit",
     "au naturel", "bare naked", "stark naked", "fully nude", "completely nude", "completely naked", "totally naked",
     "not wearing anything", "wearing nothing", "with nothing on", "has nothing on"]
    + _px("no {p}clothes", "no {p}clothing", "without {p}clothes", "without {p}clothing", "clothes off", "clothing off"),
    ["topless", "bare breasted", "tits out", "boobs out", "breasts out", "titties out", "no top"] + _px("without {p}top"),
    ["bottomless", "pantyless", "pantiless"] + _px("no {p}panties", "without {p}panties", "no {p}underwear",
                                                   "without {p}underwear", "no {p}knickers", "without {p}knickers"),
    ["undress", "undresses", "undressing", "undressed", "disrobe", "disrobing", "strips", "stripping", "striptease",
     "strip tease", "stripper", "strip off", "strip down", "strips off", "strips down", "strip naked", "strips naked",
     "stripped naked", "nudify", "nudified", "nudifying", "unclothe", "unclothing"],
    ["braless", "bra less"] + _px("no {p}bra", "without {p}bra"),
    ["nsfw", "not safe for work", "xxx", "porn", "porno", "pornographic", "pornography", "uncensored", "x rated",
     "rated x", "hardcore", "explicit", "adult content"],
    ["sex", "intercourse", "fucking", "fuck", "fucked", "fucks", "having sex", "making love", "make love", "makes love",
     "shagging", "screwing", "coitus", "sexual intercourse", "lovemaking"],
    ["breast", "breasts", "tits", "titties", "titty", "boobs", "boobies", "boob", "knockers", "bosom", "bosoms", "tatas"],
    ["nipple", "nipples"],
    ["areola", "areolas", "areolae"],
    ["pokies", "nipple poke", "nipples poking", "pointy nipples"],
    ["pussy", "pussies", "vagina", "vaginas", "vulva", "cunt", "twat", "labia", "coochie", "cooch", "cooter"],
    ["clitoris", "clit"],
    ["penis", "penises", "cock", "cocks", "dick", "dicks", "phallus", "dong", "schlong"],
    ["testicles", "testicle", "scrotum", "ballsack", "nutsack"],
    ["ass", "asses", "butt", "butts", "buttocks", "buttock", "booty", "bootie", "arse", "bum", "derriere", "tush"],
    ["anus", "asshole", "butthole", "rectum"],
    ["anal", "anal sex", "ass fucking", "assfucking", "butt sex", "sodomy"],
    ["blowjob", "blow job", "blowjobs", "bj", "fellatio", "giving head", "gives head", "give head", "cocksucking",
     "cock sucking", "dick sucking", "sucking him off", "sucks him off"]
    + _px("sucking {p}cock", "sucking {p}dick", "sucking {p}penis", "sucks {p}cock", "sucks {p}dick", "sucks {p}penis",
          "suck {p}cock", "suck {p}dick"),
    ["deepthroat", "deep throat", "deepthroating", "deep throating", "throat fucking", "throatfuck", "face fucking",
     "facefuck", "irrumatio"],
    ["oral", "oral sex"],
    ["cunnilingus", "pussy licking", "licking pussy", "eating pussy", "eating her out", "eats her out",
     "going down on her", "goes down on her"] + _px("licking {p}pussy", "licks {p}pussy", "eating {p}pussy", "eats {p}pussy"),
    ["handjob", "hand job", "handjobs", "jerking him off", "jerks him off"]
    + _px("stroking {p}cock", "strokes {p}cock", "stroking {p}dick", "strokes {p}dick"),
    ["footjob", "foot job", "footjobs"],
    ["titjob", "titsjob", "tit job", "tits job", "titfuck", "tit fuck", "tittyfuck", "titty fuck", "paizuri"],
    ["masturbation", "masturbating", "masturbate", "masturbates", "touching herself", "touches herself",
     "playing with herself", "plays with herself", "pleasuring herself", "pleasures herself", "jerking off",
     "jacking off", "wanking", "jerks off", "jacks off"],
    ["fingering", "fingers herself", "fingering herself", "finger fucking", "fingerfucking", "fingered"],
    ["dildo", "dildos", "sex toy", "sex toys", "vibrator", "vibrators"],
    ["strapon", "strap on"],
    ["cum", "semen", "sperm", "jizz", "spunk", "cumming", "ejaculate", "ejaculating", "ejaculation", "ejaculates"],
    ["cumshot", "cum shot", "cumshots", "money shot"],
    ["creampie", "creampied", "creampies"],
    ["bukkake", "gokkun"],
    ["doggystyle", "doggy style", "doggy", "doggie style", "doggy position"],
    ["cowgirl", "cowgirl position", "riding him", "rides him"] + _px("riding {p}cock", "riding {p}dick", "rides {p}cock"),
    ["missionary", "missionary position"],
    ["spooning", "spoon position"],
    ["gangbang", "gang bang", "group sex", "orgy"],
    ["threesome", "3some"],
    ["futanari", "futa", "dickgirl", "shemale"],
    ["bdsm", "bondage", "shibari"],
    ["penetration", "penetrated", "penetrating", "penetrates", "insertion"],
    ["orgasm", "orgasms", "orgasming", "climax", "climaxing"],
    ["squirting", "squirts"],
    ["cameltoe", "camel toe"],
    ["facesitting", "face sitting", "sitting on his face", "sits on his face"],
    ["rimming", "rimjob", "rim job", "anilingus"],
    ["upskirt", "up skirt"],
    ["downblouse", "down blouse"],
]
# size words: a LoRA made to enlarge a body part is only picked when the prompt asks for it
ADULT_SIZES = {
    "big": ["large", "bigger", "larger"],
    "huge": ["hyper", "gigantic", "giant", "massive", "enormous", "humongous", "colossal", "immense", "oversized"],
    "small": ["tiny", "little", "petite", "flat", "smaller", "itty bitty"],
}
ADULT_BODY = ["breast", "nipple", "areola", "ass", "penis", "pussy", "testicles", "thigh", "hip", "lip", "clitoris", "anus"]
ADULT_IMPLIES = {"undress": ["nude"], "topless": ["nude"], "bottomless": ["nude"]}
# negated, these mean "no adult LoRAs" ("no nudity", "not naked", "not nsfw")
ADULT_NUDEISH = ["nude", "topless", "bottomless", "undress", "nsfw"]
# a word that isn't adult when followed by one of these ("nude lipstick" is a colour, "naked eye", "a cock crowing")
ADULT_SKIP = {
    "nude": ["lipstick", "lip", "lips", "makeup", "heels", "heel", "shoes", "pumps", "color", "colour", "colors", "tone",
             "tones", "palette", "nails", "nail", "polish", "beige", "dress", "stockings", "pantyhose", "tights", "bra",
             "underwear", "gloss", "shade", "shades", "eyeshadow", "blush", "leotard", "bodysuit", "mesh", "sandals",
             "boots", "gown", "swimsuit", "bikini", "lingerie", "panties", "thong", "top", "suit", "manicure",
             "toned", "tinted", "lining", "lined", "rose", "pink", "peach", "brown", "taupe", "coloured", "colored",
             "hued", "fabric", "satin", "silk", "slip", "skirt", "trousers", "pants", "shorts", "jacket", "coat",
             "cardigan", "sweater", "knit", "blazer", "blouse", "camisole", "corset", "socks", "gloves", "liner"],
    "naked": ["eye", "eyes", "flame", "flames", "truth", "bulb", "lightbulb", "wire", "wires", "tree", "trees",
              "branches", "branch", "ambition", "aggression"],
    "nothing": ["but"],
    "strip": ["of", "club", "clubs", "mall", "malls", "light", "lights", "steak", "poker", "search", "lighting", "led"],
    "topless": ["man", "men", "guy", "guys", "male", "boy", "boys", "dude", "bodybuilder", "surfer", "swimmer"],
    "cock": ["crow", "crowing", "crows", "fight", "fighting", "rooster", "pit", "tail", "tails"],
    "doggy": ["bag", "door", "paddle"],
    "cowgirl": ["hat", "boots"],
    "bum": ["bag"],
    "booty": ["shorts", "call"],
    "dick": ["tracy"],
}
ADULT_UNDRESS = {   # "take off her bra", "she pulls her panties down", "removes her top", "with her bra off"
    "verbs": ["remove", "removes", "removed", "removing", "strip", "strips", "stripped", "stripping", "unbutton",
              "unbuttons", "unbuttoned", "unbuttoning", "unzip", "unzips", "unzipped", "unzipping", "unhook", "unhooks",
              "unhooked", "unhooking", "unclasp", "unclasps", "unclasped", "unclasping", "shed", "sheds", "shedding",
              "lose", "loses", "losing", "ditch", "ditches", "ditched", "ditching", "drop", "drops", "dropped",
              "dropping", "lift", "lifts", "lifted", "lifting", "discard", "discards", "discarded", "discarding"],
    "pverbs": ["take", "takes", "took", "taking", "taken", "pull", "pulls", "pulled", "pulling", "slip", "slips",
               "slipped", "slipping", "peel", "peels", "peeled", "peeling", "tear", "tears", "tore", "tearing", "torn",
               "rip", "rips", "ripped", "ripping", "yank", "yanks", "yanked", "yanking", "push", "pushes", "pushed",
               "pushing", "tug", "tugs", "tugged", "tugging", "get", "gets", "got", "getting"],
    "particles": ["off", "down", "aside", "away", "open"],
    "garments": ["clothes", "clothing", "everything", "outfit", "dress", "bra", "top", "shirt", "tshirt", "blouse",
                 "bikini", "swimsuit", "lingerie", "panties", "underwear", "knickers", "thong", "pants", "trousers",
                 "jeans", "shorts", "skirt", "towel", "robe", "bathrobe", "nightgown", "nightie", "gown", "leotard",
                 "corset", "bodysuit", "sweater", "hoodie", "jumpsuit", "kimono", "uniform", "bottoms", "undies",
                 "briefs", "boxers", "bodice", "garments", "negligee", "camisole"],
    "bottoms": ["panties", "pants", "trousers", "jeans", "shorts", "underwear", "knickers", "thong", "bottoms", "undies",
                "briefs", "boxers"],
    "full": ["clothes", "clothing", "everything", "garments"],
    "owners": ["her", "his", "their", "my", "your", "with"],
    "cancel": ["replace", "replaces", "replaced", "replacing", "swap", "swaps", "swapped", "swapping", "change",
               "changes", "changed", "changing", "instead", "new", "different", "another", "recolor", "recolour"],
}
# these keep adult LoRAs out entirely (so do "no nudity", "not naked" ...)
ADULT_SFW = ["safe for work", "family friendly", "fully clothed", "fully dressed", "non nude", "nonnude",
             "stays clothed", "stay clothed", "remains clothed", "clothes stay on", "clothes stays on",
             "clothing stays on", "keeps clothes on"] + _px("keep {p}clothes on", "keep {p}clothing on",
                                                             "keeps {p}clothes on", "keeping {p}clothes on")
# words around an adult word in a LoRA name or trigger that don't change what it does (maker / quality / filler words)
ADULT_IGNORABLE = """improved improve better best realistic real enhanced enhancer enhance ultimate perfect detailed detail
details hd pro plus super master god gods helper lora aio beta alpha revamped fix fixed diffusion edition pack collection
concept nsfw sfw sex porn she he has have is are was there it this that very photo photograph photographic image
picture closeup closeups close up shot min max maxh3 lo ra""".split()
# general adult LoRAs whose name says only "nsfw / uncensored / all-in-one" are found by these words
GENERAL_ADULT = ("nude", "nsfw")

_ADULT_EXPLICIT = set("""nude nudes naked nudity nsfw porn porno pornographic xxx hentai uncensored sex sexual penis
penises cock cocks dick dicks pussy pussies vagina vulva labia clit clitoris nipple nipples areola areolas areolae cum
cumshot cumshots semen sperm jizz creampie blowjob blowjobs fellatio deepthroat handjob footjob titjob titsjob paizuri
anal anus dildo dildos masturbation masturbating fingering orgasm squirting bukkake gokkun futanari futa shemale bdsm
bondage missionary cowgirl doggy doggystyle gangbang threesome orgy strapon penetration genitals genital testicles
scrotum erect flaccid topless bottomless lewd cunnilingus rimming nudify undress undressed undressing striptease
ahegao irrumatio facesitting""".split())
_ADULT_GLUED = ("nsfw", "porn", "hentai", "uncensor", "nipple", "areola", "blowjob", "cumshot", "creampie", "deepthroat",
                "bukkake", "gangbang", "masturbat", "dildo", "pussy", "penis", "vagina", "nudity", "naked", "nudes",
                "futanari", "handjob", "footjob", "titjob", "titsjob", "fellatio", "cunnilingus", "penetrat")
_ADULT_BODY_WORDS = set("""ass butt buttocks booty breast breasts tits titties boobs pawg thicc bimbo busty cleavage saggy
macromastia cellulite voluptuous curvy hip hips waist thigh thighs thighgap bust lingerie bikini microbikini thong panties
pokies sexy seductive downblouse upskirt cameltoe""".split())
_ADULT_WEAK = {"nsfw", "porn", "porno", "sex", "sexual", "uncensored", "xxx"}
_RE_REALISM = re.compile(r"realism|realistic|real[-_ ]?skin|ultra[-_ ]?real|detail|fidelity|enhance|crisp|sharpen|dpo\b", re.I)
_RE_DOGGY = re.compile(r"dogg(?:y|ie)[-_ ]?style", re.I)
_HYPER_BODY = {"breasts", "breast", "ass", "butt", "booty", "thighs", "thigh", "hips", "lips", "tits", "boobs", "penis",
               "cock", "dick", "nipples", "areola", "areolas"}


def adult_level(texts, weak=True):
    """'explicit' (nudity, sex, genitals ...), 'body' (body shape, lingerie, sexy ...) or '' for some names / words."""
    level = ""
    for t in texts:
        for w in _split_words(str(t or "")):
            if w in _ADULT_EXPLICIT and (weak or w not in _ADULT_WEAK):
                return "explicit"
            if len(w) >= 5 and any(g in w for g in _ADULT_GLUED) and (weak or not any(g in w for g in ("nsfw", "porn", "uncensor"))):
                return "explicit"
            if w in _ADULT_BODY_WORDS:
                level = "body"
    return level


def _pystem(w):   # the page's stem() (static/lora_match.js)
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and re.search(r"(sses|shes|ches|xes)$", w):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not re.search(r"(ss|us|is)$", w):
        return w[:-1]
    return w


_CONCEPT_OF = {}
for _g in ADULT_CONCEPTS:
    for _w in _g:
        if " " not in _w:
            _CONCEPT_OF.setdefault(_pystem(_w), _pystem(_g[0]))


def _adult_concepts(words):
    """The adult meanings in a list of name words ('xynsfw' and 'pornmix' count as nsfw)."""
    out = set()
    for w in words:
        c = _CONCEPT_OF.get(_pystem(w))
        if c:
            out.add(c)
        elif any(g in w for g in ("nsfw", "porn", "uncensor")):
            out.add("nsfw")
        elif any(g in w for g in ("nudity", "nudes", "naked")):
            out.add("nude")
    return out

MAX_HEADER = 32 * 1024 * 1024


def read_meta(path):
    """The __metadata__ dict of a .safetensors file ({} if none or not safetensors)."""
    if not path.lower().endswith(".safetensors"):
        return {}
    try:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n <= 0 or n > MAX_HEADER:
                return {}
            head = json.loads(f.read(n).decode("utf-8", "replace"))
        meta = head.get("__metadata__") or {}
        return {k: v for k, v in meta.items() if isinstance(v, str)}
    except Exception:
        return {}


def family_of(text):
    t = (text or "").lower()
    for key, words in FAMILY_WORDS:
        if any(w in t for w in words):
            return key
    return None


def _stem_name(name):
    return re.sub(r"\.(safetensors|ckpt|pt|bin)$", "", os.path.basename(name.replace("\\", "/")), flags=re.I)


def _split_words(text):
    """'2000sCrimeThriller_Klein9b' -> ['2000s', 'crime', 'thriller', 'klein9b'] (lowercase)."""
    t = re.sub(r"%20", " ", text or "")
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", t)
    t = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", t)
    t = re.sub(r"(\d)([A-Z][a-z]{2,})", r"\1 \2", t)   # '9B20Sling' -> '9B20 Sling'
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", t) if w]


def name_words(name):
    """The words of a LoRA's file name that say what it does ('Greek_Sandals_V4__Klein__epoch_3' -> greek sandals)."""
    out = []
    # family words glued into a name ('micro-bikiniflux2v2') come apart
    stem = re.sub(r"(?i)(flux\.?[12]?|klein|krea[ _]?2?|qwen|minimax|mmh3|ltx)", " ", _stem_name(name))
    ws = _split_words(stem)
    for k, w in enumerate(ws):
        if w == "hyper" and k + 1 < len(ws) and ws[k + 1] in _HYPER_BODY:   # 'Hyper_Breasts' is a size, not speed
            out.append(w)
            continue
        if w in NAME_NOISE or len(w) < 2:
            continue
        if re.fullmatch(r"\d{2,4}s", w) or re.fullmatch(r"(19|20)\d\d", w):   # 1980s, 90s, 2007: keep
            out.append(w)
            continue
        if re.fullmatch(r"3d|2d|2k|4k|8k", w):
            out.append(w)
            continue
        if re.fullmatch(r"(v|e|s|r|ep|step|rank|epoch|c|ckpt)?\d+[a-z]?\d*", w) or re.fullmatch(r"\d+(steps?|p|k)", w) or re.fullmatch(r"(bf|fp|int)\d+", w):
            continue
        if len(w) >= 12 and re.search(r"\d", w) and re.search(r"[a-z]", w):   # hashes like CN19PKQ6VAVYXG...
            continue
        if len(w) == 2 and not re.fullmatch(r"3d|2d|tv|ai", w):
            continue
        out.append(w)
    return out[:6]


def _tokens(name):  # kept for older clients: single distinctive words of the name
    return [w for w in name_words(name) if len(w) >= 4 and w not in GENERIC][:6]


def _clean_trigger(t):
    t = re.sub(r"\s+", " ", str(t or "")).strip().strip(",.;")
    return t if 2 <= len(t) <= 80 else ""


def _triggers(meta):
    trig = []
    for k in ("modelspec.trigger_phrase", "trigger_phrase", "trigger_words", "ss_trigger_words", "activation_text"):
        v = meta.get(k)
        if v:
            trig += [x.strip() for x in re.split(r"[,;\n]", v) if x.strip()]
    # training tags: the most frequent tag is often the trigger word (only if it isn't an ordinary word)
    tf = meta.get("ss_tag_frequency")
    if tf:
        try:
            counts = {}
            for ds in json.loads(tf).values():
                for tag, c in (ds or {}).items():
                    counts[tag.strip()] = counts.get(tag.strip(), 0) + int(c)
            for tag, _c in sorted(counts.items(), key=lambda x: -x[1])[:2]:
                if tag.lower() not in _STOP_TAGS and len(tag) >= 3 and len(tag.split()) <= 6:
                    trig.append(tag)
        except Exception:
            pass
    return _uniq(trig, 6)


def _uniq(items, n):
    seen, out = set(), []
    for t in items:
        t = _clean_trigger(t)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:n]


def _num(v):
    try:
        f = float(v)
        return f if 0.1 <= f <= 1.5 else None
    except (TypeError, ValueError):
        return None


def strength_hint(*texts):
    """A recommended weight written in a name or description ('0.5-1.0str', 'strength: 0.8', 'weight 0.6~0.8')."""
    for t in texts:
        t = str(t or "")
        m = re.search(r"(\d(?:\.\d+)?)\s*[-~]\s*(\d(?:\.\d+)?)\s*str", t, re.I)
        if m and _num(m.group(1)) and _num(m.group(2)):
            return round((float(m.group(1)) + float(m.group(2))) / 2, 2)
        m = re.search(r"(?:strength|weight)s?\s*(?:of|:|=|at|around|between)?\s*(\d(?:\.\d+)?)(?:\s*(?:-|~|to)\s*(\d(?:\.\d+)?))?",
                      t, re.I)
        if m and _num(m.group(1)):
            a = float(m.group(1))
            b = _num(m.group(2)) if m.group(2) else None
            return round((a + b) / 2, 2) if b else a
    return None


def sidecar_info(full):
    """Trigger words, tags, recommended weight and description from the info files other tools keep next to a LoRA."""
    base = re.sub(r"\.(safetensors|ckpt|pt|bin)$", "", full, flags=re.I)
    info = {"triggers": [], "tags": [], "weight": None, "about": "", "title": ""}
    for path, kind in ((base + ".metadata.json", "lm"), (base + ".civitai.info", "civ"), (base + ".json", "a1111")):
        try:
            if not os.path.isfile(path) or os.path.getsize(path) > 4 * 1024 * 1024:
                continue
            d = json.load(open(path, encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        if kind == "lm":   # ComfyUI LoRA Manager
            civ = d.get("civitai") or {}
            info["triggers"] += list(civ.get("trainedWords") or [])
            info["tags"] += [t for t in (d.get("tags") or []) if isinstance(t, str)]
            tips = d.get("usage_tips")
            try:
                tips = json.loads(tips) if isinstance(tips, str) else (tips or {})
            except ValueError:
                tips = {}
            info["weight"] = info["weight"] or _num((tips or {}).get("strength"))
            info["about"] = info["about"] or str(d.get("modelDescription") or civ.get("description") or "")
            info["title"] = info["title"] or str((civ.get("model") or {}).get("name") or "")
        elif kind == "civ":   # Civitai Helper (A1111 / Forge)
            info["triggers"] += list(d.get("trainedWords") or [])
            info["tags"] += [t for t in ((d.get("model") or {}).get("tags") or []) if isinstance(t, str)]
            info["about"] = info["about"] or str(d.get("description") or "")
            info["title"] = info["title"] or str((d.get("model") or {}).get("name") or "")
        else:   # A1111 / Forge user metadata
            at = d.get("activation text") or d.get("activation_text") or ""
            info["triggers"] += [x for x in re.split(r"[,;\n]", str(at)) if x.strip()]
            info["weight"] = info["weight"] or _num(d.get("preferred weight"))
            info["about"] = info["about"] or str(d.get("description") or d.get("notes") or "")
    return info


def load_cards(lora_dirs):
    """lora_cards.json files in the LoRA folders: {"sub/file.safetensors": {title, triggers, tags, about}}."""
    cards = {}
    for d in lora_dirs:
        p = os.path.join(d, "lora_cards.json")
        try:
            if os.path.isfile(p):
                for k, v in (json.load(open(p, encoding="utf-8")) or {}).items():
                    if isinstance(v, dict):
                        cards[k.replace("\\", "/").lower()] = v
        except (OSError, ValueError):
            pass
    return cards


def kind_of(name, folder, tags, about="", triggers=(), adult="", adult_name=""):
    """What sort of LoRA this is: utility / tool / slider (never auto) or character / style / clothing / pose / body /
    quality / nsfw / concept. adult: adult_level() of everything known about it; adult_name: of its file name."""
    stem = _stem_name(name)
    low_tags = " ".join(t.lower() for t in tags or [])
    words = name_words(name)
    if _RE_UTILITY.search(stem) and not words:   # a speed LoRA; "facial-krea-turbo" is a concept
        return "utility"
    if _RE_SLIDER.search(stem) or "slider" in low_tags:
        return "slider"
    if _RE_TOOL.search(stem):
        return "tool"
    if _RE_CLOTHING.search(stem):          # a clear clothing name beats the folder it was filed in
        return "clothing"
    for rx, kind in _FOLDER_KIND:
        if rx.search(folder or ""):
            return kind
    explicit = adult == "explicit"
    if re.search(r"\bcharacter|celebrit|\bperson\b", low_tags):
        return "nsfw" if explicit else "character"   # a 'casting / character sheet' of nudes isn't one person
    if _RE_QUALITY.search(stem):
        # 'uncensored', 'nsfw aio', 'massive realistic breast' are content; 'realism' with an nsfw tag stays quality
        if explicit and (adult_name == "explicit" or not _RE_REALISM.search(stem)):
            return "nsfw"
        if adult_name:
            return "nsfw" if explicit else "body"
        return "quality"
    if _RE_STYLE.search(_RE_DOGGY.sub(" ", stem)):   # 'doggy style' is a position, not a style
        return "style"
    if re.search(r"\bstyle\b|\banime\b|\bart ?style", low_tags):
        return "nsfw" if explicit else "style"
    if _RE_CLOTHING.search(stem) or re.search(r"cloth|outfit|fashion", low_tags):
        return "clothing"
    if _RE_POSE.search(stem) or "pose" in low_tags:
        return "pose"
    # a LoRA named after its one made-up trigger word ('Elowen', 'xq7person') is a person, unless what it's about is
    # adult content or a body shape (then it would block a real person's LoRA, as only one person is used)
    if len(words) == 1 and any(words[0].startswith(t.lower()) or t.lower().startswith(words[0])
                               for t in triggers if len(t.split()) == 1 and len(t) >= 4):
        return "nsfw" if explicit else ("body" if adult == "body" else "character")
    return "nsfw" if explicit else "concept"


def group_key(family, name, words):
    """LoRAs that are copies of one another share a key (Back Pose_MLX / _MLX_comfy, the checkpoints of one training
    run, Cinematic_V1 / Cinematic_krea2_c1-st5000)."""
    return "%s|%s" % (family or "", " ".join(words) if words else _stem_name(name).lower())


def describe(rec, card, side):
    """Everything the page needs to auto-pick this LoRA (added to the cached header facts)."""
    name, folder = rec["name"], rec.get("folder") or ""
    card = card or {}
    title = re.sub(r"\((?:krea ?2|flux[^)]*|klein[^)]*|qwen[^)]*)\)|\[[^\]]*\]|\bfor (?:krea ?2|flux[\w. ]*|klein[\w ]*)",
                   "", str(card.get("title") or side.get("title") or ""), flags=re.I).strip(" -_|")
    triggers = _uniq(list(rec.get("triggers") or []) + list(card.get("triggers") or []) + side["triggers"], 8)
    tags = [t for t in (list(card.get("tags") or []) + side["tags"]) if isinstance(t, str)][:16]
    about = str(card.get("about") or side.get("about") or "")
    words = name_words(name)
    # adult content? from the name, folder, trigger words, title and tags (not the free description). A LoRA tagged
    # both "sfw" and "nsfw" is a general one: its tags alone don't make it adult.
    dual = any(str(t).strip().lower() == "sfw" for t in list(tags) + list(triggers))
    raw_name = _split_words(_stem_name(name))
    adult_name = adult_level(raw_name)
    adult = adult_level([folder, title] + raw_name) or adult_level(list(triggers) + list(tags), weak=not dual)
    kind = kind_of(name, folder, tags, about, triggers, adult, adult_name)
    # "3D Render Style. 3DRenderAF." is two triggers
    phrases = [[p.strip(), "trigger"] for t in triggers for p in re.split(r"\.\s+|\.$", t) if len(p.strip()) >= 2]
    if words:
        phrases.append([" ".join(words), "name"])
    # names made of parts ('Krea 2 - Microbikini - Sling Bikini'): each part is a phrase of its own
    parts = [p for p in re.split(r"\s+-\s+|__+", _stem_name(name)) if p.strip()]
    if len(parts) > 1:
        for part in parts:
            pw = name_words(part)
            if pw and pw != words:
                phrases.append([" ".join(pw), "name"])
    if title:
        tw = [w for w in _split_words(title) if w not in NAME_NOISE and not re.fullmatch(r"v?\d+(\.\d+)*", w)]
        if tw and tw != words:
            phrases.append([" ".join(tw[:6]), "title"])
    for t in tags:
        tl = t.strip().lower()
        if tl and tl not in _STOP_TAGS and tl not in ("style", "lora", "concept", "character", "clothing", "base model"):
            phrases.append([tl, "tag"])
    # a general adult LoRA ('<maker>_NSFW_AIO', '<maker>_Uncensored'): its name says nothing but "nsfw", so it is found
    # by "nude" / "nsfw" in a prompt (weakly: any LoRA named for what the prompt says comes first)
    ign = set(ADULT_IGNORABLE) | NAME_NOISE
    rest = [w for w in raw_name if w not in ign and not _adult_concepts([w]) and not re.fullmatch(r"v?\d+[a-z]?\d*", w)]
    if adult == "explicit" and _adult_concepts(raw_name) == {"nsfw"} and not rest and \
            not (_adult_concepts(_split_words(" ".join(triggers + [title]))) - {"nude", "nsfw", "sex", "topless"}):
        phrases = phrases[:16 - len(GENERAL_ADULT)] + [[w, "concept"] for w in GENERAL_ADULT]
    hint = side.get("weight") or strength_hint(_stem_name(name), about)
    strength = hint if hint else KIND_DEFAULT_STRENGTH.get(kind, 0.85)
    auto = bool(rec.get("family")) and kind not in NEVER_AUTO and name.lower().endswith(".safetensors")
    # one training run saved at several steps -> one group (the page keeps the latest step)
    series = re.sub(r"[_\-.]?(?:e|ep|epoch|step|s)?\d{2,}$", "", _stem_name(name), flags=re.I).lower()
    return {
        "kind": kind, "auto": auto, "strength": round(float(strength), 2), "phrases": phrases[:16], "triggers": triggers,
        "title": title[:80], "group": group_key(rec.get("family"), name, words) if words else
        "%s|%s/%s" % (rec.get("family") or "", folder.lower(), series),
        "weight_from": "maker" if hint else "kind", "nsfw": adult == "explicit",
    }


def build(names, lora_dirs, data_dir):
    """[{name, folder, family, base, triggers, tokens, kind, auto, strength, phrases, group, ...}] for the LoRA names
    ComfyUI lists."""
    cache_p = os.path.join(data_dir, "lora_index.json")
    try:
        cache = json.load(open(cache_p, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    cards = load_cards(lora_dirs)
    out, fresh = [], {}
    for name in names:
        full = next((os.path.join(d, name) for d in lora_dirs if os.path.isfile(os.path.join(d, name))), None)
        key = name
        sig = None
        mtime = 0
        if full:
            st = os.stat(full)
            sig = "%d|%d" % (st.st_size, int(st.st_mtime))
            mtime = int(st.st_mtime)
        hit = cache.get(key)
        if hit and hit.get("sig") == sig and hit.get("v") == 4:
            rec = hit
        else:
            meta = read_meta(full) if full else {}
            base = meta.get("ss_base_model_version") or meta.get("modelspec.architecture") or meta.get("ss_sd_model_name") or ""
            fam = family_of(base) or family_of(os.path.dirname(name)) or family_of(os.path.basename(name))
            rec = {"name": name, "folder": os.path.dirname(name).replace("\\", "/"), "family": fam, "base": base[:80],
                   "triggers": _triggers(meta), "sig": sig, "v": 4}
        fresh[key] = rec
        side = sidecar_info(full) if full else {"triggers": [], "tags": [], "weight": None, "about": "", "title": ""}
        card = cards.get(name.replace("\\", "/").lower())
        item = {k: rec.get(k) for k in ("name", "folder", "family", "base")}
        item.update(describe(rec, card, side))
        item["tokens"] = _tokens(name)
        item["mtime"] = mtime
        out.append(item)
    try:
        json.dump(fresh, open(cache_p, "w", encoding="utf-8"))
    except OSError:
        pass
    return out


def client_lists():
    """Word lists the page's matcher uses (one source of truth)."""
    return {"generic": sorted(GENERIC), "concepts": ADULT_CONCEPTS, "sizes": ADULT_SIZES, "body": ADULT_BODY,
            "implies": ADULT_IMPLIES, "nudeish": ADULT_NUDEISH, "skip": ADULT_SKIP, "undress": ADULT_UNDRESS,
            "sfw": ADULT_SFW, "ignorable": ADULT_IGNORABLE}
