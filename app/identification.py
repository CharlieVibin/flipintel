"""Automatic hardware model identification.

Turns a messy marketplace title into a canonical product key (used to group
comparable listings) plus category, condition and risk flags. Rules are
deliberately conservative: it is better to skip an ambiguous listing than to
label a £40 accessory as an RTX 4070.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .utils import clamp, lower_text, normalise_text, tokenise

# Value relative to a "good used" example. Used to normalise comparable prices.
CONDITION_FACTORS: dict[str, float] = {
    "new": 1.20,
    "open_box": 1.10,
    "like_new": 1.05,
    "excellent": 1.02,
    "very_good": 0.98,
    "good": 1.00,
    "used": 0.90,
    "fair": 0.80,
    "poor": 0.62,
    "faulty": 0.30,
    "unknown": 0.95,
}

CONDITION_LABELS: dict[str, str] = {
    "new": "New",
    "open_box": "Open box",
    "like_new": "Like new",
    "excellent": "Excellent",
    "very_good": "Very good",
    "good": "Good",
    "used": "Used",
    "fair": "Fair",
    "poor": "Poor",
    "faulty": "Faulty",
    "unknown": "Used (unstated)",
}

FAULT_RE = re.compile(
    r"\b(faulty|spares?\s*(?:or|/)?\s*repairs?|for\s+parts|not\s+working|"
    r"broken|no\s+display|no\s+output|artefacts?|artifacts?|water\s+damage|"
    r"won'?t\s+(?:post|boot|power)|does\s+not\s+(?:post|boot|work)|"
    r"screen\s+(?:damage|broken|cracked)|bent\s+pins|burn(?:ed|t)\s+connector|"
    r"coil\s+whine\s+only|damaged|no\s+power|won'?t\s+power|"
    r"no\s+boot|liquid\s+damage|working\s+with\s+(?:some\s+)?issues?|"
    r"(?:has|with|some|minor)\s+issues?|issue\s+with|needs?\s+(?:a\s+)?repair|"
    r"repair\s+needed|sold\s+as\s+(?:seen|is)|as[-\s]?is|for\s+repair|"
    r"not\s+fully\s+working|partially\s+working|fault(?:s|y)?\b|"
    r"problem(?:s)?\b)\b",
    re.I,
)
FAULT_NEGATION_RE = re.compile(
    r"\b(not\s+faulty|no\s+faults?|fully\s+working|tested\s+working|"
    r"working\s+perfectly|excellent\s+working\s+order|no\s+damage|"
    r"no\s+bent\s+pins|pins\s+are\s+(?:fine|good|straight)|fully\s+functional|"
    r"no\s+(?:issues?|problems?|faults?))\b",
    re.I,
)
ACCESSORY_RE = re.compile(
    r"\b(box\s+only|empty\s+box|packaging\s+only|water\s*block|backplate\s+only|"
    r"cooler\s+only|heatsink\s+only|cable\s+only|bracket\s+only|manual\s+only|"
    r"no\s+gpu|gpu\s+not\s+included|without\s+gpu|replica|clone|fake|"
    r"picture\s+only|stock\s+photo\s+only|for\s+spares\s+only)\b",
    re.I,
)
ACCESSORY_TITLE_RE = re.compile(
    r"\b(backpack|rucksack|carry\s+bag|carry\s+case|travel\s+case|sleeve|laptop\s+bag|"
    r"charger|charging\s+dock|power\s+supply|psu|keyboard|mouse|mousepad|headset|"
    r"headphones|earphones|screws?|screwdriver|stand|bracket|cable|adapter|dock|"
    r"battery\s+only|replacement\s+battery|skins?|covers?|protectors?|decals?|stickers?|replacement|spares?|spare\s+parts|"
    r"faceplates?|shell|bezel|hinge|fan\s+only|cooler\s+only|empty\s+box|boxes|cases?|"
    r"nudient|casely|badge|pin\s+badge|bodyguardz|caudabe|velvet\s+caviar|loopy|"
    r"thumb\s*grips?|joy[-\s]?con|game\s+cartridge|splitter|docking\s+station|"
    r"joystick|thumbstick|analog\s+stick|heatsink|\bhub\b|\b6\s*in\s*1\b|"
    r"flash\s+drive|usb\s+stick|pen\s+drive|enclosure|caddy|housing|digitizer|"
    r"screen\s+assembly|replacement\s+screen|glass\s+only|box\s+only|"
    r"(?:\d+x)?cases?|camera\s+(?:lens\s+)?protection|screen\s+protectors?|"
    r"box\s+(?:only|for)|empty\s+box|"
    r"packaging(?:\s+set)?|retail\s+packaging|otterbox|spigen|caseology|casetify|"
    r"\bmous\b|dbrand|\bwrap\b|holder|back\s+glass|rear\s+glass|"
    r"cooling\s+fan|fan\s+only|mid\s*frame|midframe|chassis|casing|oled\s+assembly|"
    r"\betui\b|hülle|funda|coque|hoesje|swivel\s+base|"
    r"logic\s+board|charging\s+port|sim\s+tray|camera\s+module|privacy\s+(?:screen|filter)|"        r"tempered\s+glass|alligator|crocodile|phone\s+case|silicone\s+case|leather\s+case|"
    r"replacement\s+shell|housing\s+only|frame\s+only|lead\s+for|charging\s+cable|"
    r"accessor(?:y|ies)|stylus|\bpen\b|\bpencils?\b|protect(?:or|er)s?|kids|\btoy\b|"
    r"\betui\b|hülle|funda|coque|hoesje|\bbags?\b|swivel\s+base|carry\s+bag|"
    r"screen\s+only|portfolio|osmo|winnie\s+the\s+pooh|piglet|\bpooh\b|"
    r"controller\s+camera|motion\s+sensor|psvr|ps\s+vr|camera\s+v\d|"
    r"(?:wifi|wi-fi|wireless|network|power|usb|hdmi|controller|button)\s+board|"
    r"charging\s+coil|antenna\s+assembly|(?:wifi|wi-fi|bluetooth)\s+(?:card|module|antenna)|"
    r"phonecase|case\s*20\d{2}|case\s+for|"
    r"clips?|holsters?|board\s+only)\b",
    re.I,
)
CONTRACT_RE = re.compile(
    r"\b(?:per|/)\s*month\b|\bmonthly\s+(?:plan|payment|contract)\b|"
    r"\b(?:contract|finance|instalments?|installments?)\b",
    re.I,
)
MULTI_ITEM_RE = re.compile(
    r"\b(?:iphone|ipad|macbook|ps5|xbox|switch|steam\s+deck)\b.*"
    r"\b(?:and|&|\+)\b.*\b(?:iphone|ipad|macbook|ps5|xbox|switch|steam\s+deck)\b",
    re.I,
)
COMPATIBILITY_RE = re.compile(
    r"\b(?:for|fits?|compatible\s+with|suitable\s+for|replacement\s+for|to\s+fit)\s+"
    r"(?:the\s+)?(?:nvidia|geforce|amd|radeon|intel|rtx|gtx|rx|ps[45]|playstation|"
    r"xbox|switch|steam\s+deck|macbook|ipad|iphone|rtx\s*\d{3,4})\b",
    re.I,
)
OEM_RE = re.compile(r"\b(oem|tray\s+cpu|bulk\s+cpu)\b", re.I)
ES_RE = re.compile(r"\b(engineering\s+sample|qualification\s+sample|\bqs\b|\bes\s+cpu\b)\b", re.I)
REFURBISHED_RE = re.compile(
    r"\b(?:refurbished|refurb|renewed|reconditioned|seller\s+refurbished)\b", re.I
)

# ---------------------------------------------------------------------------
# Sold-item resolution: what is being sold, not which names appear
# ---------------------------------------------------------------------------
# A marketplace title describes two things at once: the item on sale and the
# device it fits ("SanDisk microSD Express Card for Nintendo Switch 2").
# Reading every recognisable name as the product is what turned a memory card
# into a console and a complete desktop into a loose processor. These rules
# separate the subject of the title from its compatibility qualifiers, and they
# describe product classes rather than listing every accessory ever sold.
COMPATIBILITY_TAIL_RE = re.compile(
    r"\b(?:compatible\s+with|compatible\s+for|compatibility\s+with|fits?|fitting|"
    r"suitable\s+for|designed\s+for|made\s+for|works?\s+with|use\s+with|"
    r"replacement\s+for|upgrade\s+for|for)\s+"
    r"(?:the\s+|a\s+|an\s+)?"
    r"(?:nintendo|switch|ps[1-5]\b|playstation|xbox|steam\s+deck|macbook|ipad|iphone|"
    r"surface|galaxy|pixel|oneplus|xiaomi|rtx|gtx|rx\s*\d|nvidia|geforce|amd|radeon|"
    r"intel|dell|hp\b|lenovo|asus|msi|acer|samsung|google)\b",
    re.I,
)
# Removable/expandable storage media is a product class in its own right. It is
# never the console, phone or camera it is advertised as compatible with.
MEDIA_CARD_RE = re.compile(
    r"\b(?:micro\s*sd|microsd|sd\s*card|sdxc|sduc|memory\s+card|express\s+card|"
    r"cfexpress|compact\s*flash|memory\s+stick|usb\s+flash\s+drive|"
    r"flash\s+memory\s+card|storage\s+card)\b",
    re.I,
)
# Nouns that describe assembled hardware rather than a component. They are the
# difference between "Dell Pro Max Tower ... Ultra 5" (a computer) and "Intel
# Core Ultra 5 235 CPU" (a processor).
DEVICE_CHASSIS_RE = re.compile(
    r"\b(?:tower|desktop|small\s+form\s+factor|sff|mini\s*pc|micro\s*pc|"
    r"all[-\s]?in[-\s]?one|workstation|chromebook|ultrabook|"
    r"2[-\s]?in[-\s]?1|touchscreen|convertible|"
    r"optiplex|precision|elitedesk|prodesk|thinkcentre|thinkstation|"
    r"pro\s+max\s+tower|pro\s+micro(?:\s+plus)?|micro\s+form\s+factor|mff|"
    r"surface\s+(?:pro|go|laptop|book)|latitude|elitebook|probook|"
    r"inspiron|vostro|ideacentre|aspire\s+(?:tc|desktop)|pavilion\s+desktop)\b",
    re.I,
)
SYSTEM_RAM_RE = re.compile(
    r"\b\d{1,3}\s*gb\s+(?:ddr[45]\s+)?(?:ram|memory)\b|"
    r"\b(?:ram|memory)\s*[:\-]?\s*\d{1,3}\s*gb\b|"
    r"\bddr[45][^|,;]{0,18}\b\d{1,3}\s*gb\b|"
    r"\b\d{1,3}\s*gb\s+ddr[45]\b",
    re.I,
)
SYSTEM_STORAGE_RE = re.compile(
    r"\b(?:\d{3,4}\s*gb|\d(?:\.\d)?\s*tb)\s+"
    r"(?:nvme|ssd|hdd|pcie|storage|solid\s+state)\b|"
    r"\b(?:nvme|ssd|hdd)\b[^|,;]{0,12}\b(?:\d{3,4}\s*gb|\d(?:\.\d)?\s*tb)\b",
    re.I,
)
# Explicit evidence that a *component* is the item on sale. It overrides the
# RAM+storage pattern, which otherwise looks identical on a boxed CPU listing.
COMPONENT_SALE_RE = re.compile(
    r"\b(?:cpu|processor|socket|lga\s*\d{3,4}\s*\w*|am[45]\b|tray\s+cpu|"
    r"graphics\s+card|gpu|gddr[567]x?|founders\s+edition|pcie\s*[45](\.0)?|"
    r"vram|bare\s+drive|internal\s+ssd|nvme\s+drive|heatsink\s+only)\b",
    re.I,
)
CONSOLE_HARDWARE_NOUN_RE = re.compile(
    r"\b(?:console|handheld|controller|joy[-\s]?cons?|charging\s+dock|"
    r"disc\s+edition|digital\s+edition|blu[-\s]?ray\s+edition)\b",
    re.I,
)


def subject_phrase(title: str) -> str:
    """The part of a title that describes the item itself.

    "SanDisk microSD Express Card for Nintendo Switch 2" sells a card; the
    Switch 2 appears only as a compatibility target. Model matching must run on
    the subject, otherwise any accessory inherits the value of the device it
    fits. The full title is still used for condition and risk signals.
    """
    match = COMPATIBILITY_TAIL_RE.search(title)
    if not match:
        return title
    head = title[: match.start()].strip(" -–—,;:|\t")
    # "For Nintendo Switch 2 controller" has no subject before the qualifier;
    # keep the full title so the accessory noun can still be read.
    return head if head else title


def system_assembly_evidence(title: str) -> bool:
    """True when the title describes an assembled machine, not a spare part.

    A processor or graphics card named alongside memory, storage, a chassis or
    an operating system is a specification of the machine being sold. This is
    the structural distinction behind the Surface Pro and Dell desktop
    misclassifications; it does not rely on a list of specific models.
    """
    # Compact configuration lists write the two capacities together with no
    # nouns at all: "Ultra 5 235 16GB 512GB Win11 Pro".
    bare_config = bool(
        re.search(r"\b\d{1,3}\s*gb\s+(?:ddr[45]\s+)?\d{3,4}\s*gb\b", title, re.I)
    )
    ram = bool(SYSTEM_RAM_RE.search(title)) or bare_config
    storage = bool(SYSTEM_STORAGE_RE.search(title)) or bare_config
    chassis_match = DEVICE_CHASSIS_RE.search(title)
    chassis = bool(chassis_match) or bool(SCREEN_RE.search(title))
    # "Desktop RAM" and "desktop hard drive" describe a form factor, not a
    # chassis. Treating that word as a machine made every DDR4 module look like
    # a complete computer, so no RAM comparable could ever be accepted.
    component_noun = bool(
        re.search(
            r"\b(?:ram|memory|ddr[345]|dimm|sodimm|so-dimm|ssd|nvme|hdd|"
            r"hard\s+drive|graphics\s+card|gpu|cpu|processor|motherboard)\b",
            title,
            re.I,
        )
    )
    if (
        component_noun
        and chassis_match
        and chassis_match.group(0).lower() == "desktop"
        and not re.search(
            r"\b(?:desktop\s+(?:pc|computer)|gaming\s+desktop|desktop\s+tower|"
            r"tower\s+pc|complete\s+pc|pc\s+tower)\b",
            title,
            re.I,
        )
    ):
        chassis = bool(SCREEN_RE.search(title))
    os_cue = bool(re.search(r"\b(?:windows|win\s*1[01]|macos|chrome\s*os)\b", title, re.I))
    component_only = bool(COMPONENT_SALE_RE.search(title))
    if component_only and not chassis:
        # "Intel Core i5-12400F CPU, supports 32GB DDR5" is a processor listing.
        return False
    # Memory alone ("32GB DDR5 desktop RAM") is the part being sold. A machine
    # is only inferred when storage is also part of the stated configuration.
    component = bool(_cpu(title.lower()) or _gpu(title.lower()))
    device_cue = component or chassis or os_cue
    return bool(
        # Memory and storage alone can be a component bundle ("8GB DDR4 RAM and
        # 256GB NVMe SSD"), so one device cue must also be present.
        (ram and storage and device_cue)
        # Configuration lists often omit the word RAM: "32GB, 5.5TB Storage".
        or (storage and chassis and component)
    )


def _identity_level(score: float) -> str:
    if score >= 0.85:
        return "strong"
    if score >= 0.68:
        return "moderate"
    if score >= 0.45:
        return "limited"
    return "insufficient"


def identity_level_for(score: float) -> str:
    """Describe a stored identification score without re-reading the title."""
    return _identity_level(clamp(float(score or 0.0), 0.0, 1.0))


def identity_reliability(
    kind: str,
    product_key: str | None,
    attrs: dict[str, Any],
    flags: list[str],
    condition: str,
) -> tuple[float, str, list[str]]:
    """Score how well the title supports *this exact product identity*.

    The score is built from what was actually parsed, not from the product
    class. A console key with no capacity or edition, or a phone with no
    storage, is a family guess and stays limited however familiar the name is.
    This is why a correctly-read accessory cannot inherit a high score: it has
    no identity at all, and a wrongly-read one is caught upstream.
    """
    if not product_key:
        return 0.0, "insufficient", ["no_exact_model"]
    basis = ["exact_model"]
    score = 0.55
    if kind == "console" and product_key.startswith("console:steam-deck"):
        display = str(attrs.get("display") or "").lower()
        storage = attrs.get("storage_gb")
        if display in {"lcd", "oled"}:
            basis.append("display_parsed")
            score = 0.78
        else:
            score = 0.58
        if storage:
            basis.append("capacity_parsed")
            score += 0.10
        if display in {"lcd", "oled"} and storage:
            score = 0.90
    elif kind == "console":
        specifics = {
            "console:ps5-pro", "console:ps5-slim", "console:ps5-slim-digital",
            "console:ps5-digital",
            "console:ps4-pro", "console:ps4-slim", "console:switch-2",
            "console:switch-oled", "console:switch-lite",
            "console:xbox-one-x", "console:xbox-one-s", "console:xbox-360-s",
            "console:xbox-360-elite", "console:rog-ally-x", "console:new-3ds-xl",
            "console:3ds-xl", "console:new-3ds", "console:2ds-xl", "console:dsi-xl",
            "console:ds-lite", "console:gameboy-advance-sp", "console:gameboy-color",
            "console:gameboy-pocket", "console:wii-u", "console:master-system",
            "console:playstation-portal", "console:legion-go", "console:rog-ally",
        }
        score = 0.80 if product_key in specifics else 0.62
        if attrs.get("storage_gb"):
            basis.append("capacity_parsed")
            score += 0.05
    elif kind == "gpu":
        score = 0.74
        if attrs.get("vram_gb"):
            basis.append("vram_parsed")
            score += 0.09
        if attrs.get("suffix"):
            basis.append("variant_parsed")
            score += 0.04
    elif kind == "cpu":
        score = 0.82
        basis.append("model_number")
        if attrs.get("suffix"):
            score += 0.04
    elif kind == "phone":
        score = 0.60
        if attrs.get("storage_gb"):
            basis.append("capacity_parsed")
            score = 0.82
    elif kind == "tablet":
        score = 0.60
        if attrs.get("storage_gb"):
            basis.append("capacity_parsed")
            score = 0.80
        if attrs.get("chip") or attrs.get("generation"):
            basis.append("generation_parsed")
            score += 0.04
    elif kind == "laptop":
        score = 0.52
        if attrs.get("gpu_key") or attrs.get("cpu_key"):
            basis.append("cpu_or_gpu_parsed")
            score += 0.12
        if attrs.get("storage_gb") or attrs.get("ram_gb"):
            basis.append("spec_parsed")
            score += 0.08
    elif kind == "pc":
        score = 0.55
        if attrs.get("gpu_key") or attrs.get("cpu_key"):
            basis.append("cpu_or_gpu_parsed")
            score += 0.08
    elif kind in {"ram", "ssd"}:
        score = 0.68
        if attrs.get("speed_mhz") or attrs.get("kit_count") or attrs.get("capacity_gb"):
            basis.append("capacity_parsed")
            score += 0.08
    elif kind in {"monitor", "motherboard", "networking"}:
        score = 0.70
    else:
        score = 0.40

    if "model_unclear" in flags:
        score = min(score, 0.25)
        basis.append("model_ambiguous")
    if "variant_unclear" in flags:
        score = min(score, 0.55)
        basis.append("variant_unknown")
    if "contents_incomplete" in flags:
        score -= 0.03
    if condition in {"fair", "poor", "faulty"}:
        score -= 0.05
    score = clamp(score, 0.0, 1.0)
    return round(score, 3), _identity_level(score), basis
CONSOLE_BUNDLE_RE = re.compile(
    r"(?:\b(?:with|plus|and)\b|\+)\s+(?:an?\s+)?(?:"
    r"\d+\s+(?:extra\s+)?games?"
    r"|(?:\d+\s+)?(?:extra|additional|spare|second|2nd)\s+controllers?"
    r"|[2-9]\d*\s+controllers?"
    r"|(?:a\s+)?(?:headset|charging\s+dock|memory\s+card)\b"
    r")"
    # Extra hardware changes what the bundle is worth. Two controllers, an Edge
    # pad, a disc drive or a headset are not part of a bare console sale, and
    # including those asks in a used-console pool inflates the estimate by the
    # accessory's own resale value.
    r"|\b(?:2|two|3|three)\s*(?:x\s*)?(?:dualsense|dualshock|controllers?)\b"
    r"|\b(?:extra|additional|spare|second|2nd)\s+controllers?\b"
    r"|\bedge\s+controller\b"
    r"|\b(?:with|plus|includes?|including|\+)\s+(?:an?\s+)?(?:disc|optical|blu[-\s]?ray)\s+drive\b"
    r"|\b(?:with|including|includes?|comes\s+with)\b[^|;]{0,40}\b(?:headset|headphones)\b",
    re.I,
)
ACCESSORY_NOUN_RE = re.compile(
    r"\b(?:cases?|covers?|protectors?|skins?|sleeves?|bags?|chargers?|cables?|"
    r"docks?|stands?|brackets?|holders?|clips?|holsters?|adapters?|stylus|pens?)\b",
    re.I,
)
CONSOLE_ACCESSORY_RE = re.compile(
    r"\b(controllers?|gamepads?|headsets?|headphones|charging\s+docks?|faceplates?|"
    r"carry\s+cases?|travel\s+cases?|console\s+covers?|disc\s+drive\s+only|cases?|"
    r"thumb\s*grips?|joy[-\s]?cons?|game\s+cartridges?|game\s+discs?|video\s+games?|"
    r"blu[-\s]?ray\s+drive|bluray\s+drive|"
    r"games?\s+only|kinect|screen\s+protectors?|joysticks?|thumbsticks?|analog\s+sticks?|heatsinks?|"
    r"\bhub\b|\b6\s*in\s*1\b|\bhdmi\b|splitters?)\b",
    re.I,
)
ACCESSORY_STRONG_RE = re.compile(
    r"\b(backpack|rucksack|carry\s+bag|carry\s+case|travel\s+case|sleeve|screws?|"
    r"screwdriver|support\s+bracket|cable\s+bundle|water\s*block|controller|gamepad|"
    r"headset|headphones|keyboard|mouse|mousepad|monitor\s+stand|riser\s+cable|"
    r"replacement\s+\w+|spare\s+parts?|charging\s+dock|docking\s+station|boxes|cases?|"
    r"power\s+supply\s+unit|psu|disc\s+drive\s+only|faceplates?|flash\s+drive|"
    r"usb\s+stick|pen\s+drive|joystick|thumbstick|analog\s+stick|heatsink|kinect|"
    r"\bhub\b|\b6\s*in\s*1\b)\b",
    re.I,
)
CONSOLE_GAME_RE = re.compile(
    r"\b(video\s+game|game|games|us\s+version|eu\s+version|region\s+free|steelbook|"
    r"disc\s+only|cartridge|dlc|key\s+code|digital\s+code)\b",
    re.I,
)
# Titles that name a game but no hardware still mention a console platform.
# Without a hardware cue they must never become console comparables.
GAME_TITLE_RE = re.compile(
    r"\b(?:naruto|helldivers|wolverine|assassin'?s?\s+creed|watch\s*dogs|spider[-\s]?man|"
    r"god\s+of\s+war|the\s+last\s+of\s+us|horizon\s+(?:zero|forbidden)|ghost\s+of\s+tsushima|"
    r"gran\s+turismo|gran\s+theft\s+auto|\bgta\b|red\s+dead|elden\s+ring|dark\s+souls|"
    r"bloodborne|hogwarts\s+legacy|resident\s+evil|silent\s+hill|metal\s+gear|"
    r"final\s+fantasy|cyberpunk|witcher|batman|lego\s+\w+|minecraft|fortnite|"
    r"call\s+of\s+duty|battlefield|fifa|\bea\s+fc\b|football\s+manager|just\s+dance|"
    r"mario|zelda|pok[eé]mon|animal\s+crossing|splatoon|kirby|metroid|sonic|"
    r"halo|forza|starfield|gears\s+of\s+war|destiny|diablo|overwatch|"
    r"pragmata|valhalla|mirage|origins|odyssey|ragnarok|returnal|ratchet\s*&\s*clank|"
    r"crash\s+bandicoot|tekken|street\s+fighter|mortal\s+kombat|nba\s*2k|madden|"
    r"the\s+crew|\bufc\b|wwe\s*2k|ea\s+sports|need\s+for\s+speed|gran\s+turismo|"
    r"pokopia|pro\s+evolution\s+soccer|\bpes\b|efootball|e\s*football|"
    r"tony\s+hawk|pro\s+skater|undisputed|\bfc\s*2[4-9]\b)\b",
    re.I,
)
CONSOLE_HARDWARE_RE = re.compile(
    r"\b(?:console|console\s+only|disc\s+edition|digital\s+edition|slim|pro|oled|"
    r"1tb|2tb|512gb|825gb|with\s+(?:an?\s+)?(?:extra\s+)?controllers?|"
    r"with\s+(?:\d+\s+)?(?:extra\s+)?games?|"
    r"includes?\s+(?:an?\s+)?controllers?|boxed\s+console|complete\s+console)\b",
    re.I,
)
CONSOLE_HARDWARE_STRONG_RE = re.compile(
    r"\b(?:console|boxed\s+console|complete\s+console|disc\s+edition|"
    r"digital\s+edition|1tb|2tb|512gb|825gb|"
    r"with\s+(?:an?\s+)?(?:extra\s+)?controllers?|"
    r"with\s+(?:\d+\s+)?(?:extra\s+)?games?|"
    r"includes?\s+(?:an?\s+)?controllers?|"
    r"(?:ps5|ps4|playstation\s*[45]|xbox\s+(?:series\s+[xs]|one|360))\s*"
    r"(?:pro|slim|digital|disc\s+edition))\b",
    re.I,
)
MULTI_GPU_RE = re.compile(
    r"\b(?:rtx|gtx|rx|arc)\s*\d{3,4}(?:\s*(?:ti|super|xt|xtx))?\b", re.I
)
PARTS_ONLY_RE = re.compile(
    r"\b(?:mid\s*frame|midframe|chassis|rear\s+glass|back\s+glass|screen\s+assembly|"
    r"replacement\s+screen|lcd\s+assembly|logic\s+board|charging\s+port|sim\s+tray|"
    r"camera\s+module|parts\s+only|spares?\s+only|frame\s+only|shell\s+only|"
    r"motherboard\s+only|bare\s+board|for\s+parts|faulty)\b",
    re.I,
)
BOX_ONLY_RE = re.compile(
    r"\b(?:empty|original|retail|full|packaging)\s+box\b|"
    r"\bbox\s+(?:only|for)\b|\bpackaging\s+only\b",
    re.I,
)
BUNDLED_ACCESSORY_RE = re.compile(
    # Only an explicit "with / includes" phrase proves that the device is the
    # item being sold. A bare "&" or "plus" is not enough: "iPhone 14 Pro Case,
    # MagSafe Ring & Portable Charger" and "iPhone 15 plus case lot" are
    # accessory listings whose model name was read as a phone. "plus" is also
    # a model suffix ("16 Plus"), so it can never join a bundle.
    r"\b(?:with|including|includes?|inc\.?|comes\s+with|boxed\s+with|supplied\s+with)\b"
    r"[^|;]{0,60}\b(?:cases?|box|boxes|chargers?|cables?|docks?|covers?|"
    r"sleeves?|bags?|controllers?|gamepads?|headsets?|keyboard|mouse|stylus|"
    r"pen|pencil|accessor(?:y|ies)|screen\s+protectors?|stands?|brackets?|faceplates?)\b",
    re.I,
)
CONSOLE_CUE_RE = re.compile(
    # "disc" alone is game media; only "disc edition" describes hardware.
    r"\b(console|bundle|disc\s+edition|digital\s+edition|slim|pro|oled|lite|v2|1tb|2tb|512gb|825gb|"
    r"\d{3}\s*gb|boxed|tested|working|complete|condition|handheld|charger|power\s+supply|"
    r"memory\s+card|joy[-\s]?cons?|controllers?|includes?)\b|\+\s*\d+\s*games",
    re.I,
)
WANTED_RE = re.compile(
    r"\b(wanted|looking\s+to\s+buy|looking\s+for|want\s+to\s+buy|buying|wtb|"
    r"will\s+pay|wanted\s+ad)\b",
    re.I,
)
CONDITION_CONTRADICTION_RE = re.compile(
    r"\b(?:brand\s+new|new\s+sealed|sealed|bnib|new\s+in\s+box)\b",
    re.I,
)
USED_CONTRADICTION_RE = re.compile(
    r"\b(?:used|opened|faulty|not\s+working|missing|scratched?|dent(?:ed)?|"
    r"spares?\s+or\s+repairs?|read\s+description)\b",
    re.I,
)
CAUTION_RE = re.compile(
    r"\b(read\s+(?:the\s+)?description|sold\s+as\s+seen|as\s+seen|untested|no\s+returns?|"
    r"missing|no\s+(?:box|cables?|psu|power\s+supply)|cosmetic|"
    r"scratch(?:es|ed)?|dent(?:ed)?|crack(?:ed|s)?|chipped|pixel\s+defect|"
    r"screen\s+burn|\bdead\b)\b",
    re.I,
)
PHONE_PART_RE = re.compile(
    r"\b(motherboard|logic\s+board|screen\s+assembly|charging\s+port|sim\s+tray|"
    r"rear\s+glass|back\s+glass|frame\s+only|shell\s+only|battery\s+only|lcd)\b",
    re.I,
)
PLATFORM_RE = re.compile(
    r"\b(?:ps[1-5]|playstation\s*[1-5]|xbox(?:\s*(?:360|one|series\s*[xs]))?|"
    r"nintendo(?:\s*(?:ds|3ds|2ds|64|gamecube|wii\s*u?|switch))?|switch|"
    r"game\s*boy|gba|gbc|snes|sega|mega\s*drive|dreamcast|saturn|"
    r"ps\s*vita|psp|gamecube)\b",
    re.I,
)
GAME_MEDIA_RE = re.compile(
    r"\b(?:game|games|cartridge|disc\s+only|manual|inlay|box\s+art|"
    r"cib|complete\s+in\s+box|pal|ntsc|region\s+free|steelbook|"
    r"video\s+game|pegi|esrb)\b",
    re.I,
)
# These cues describe actual hardware. "Boxed", "complete", "working" and
# "manual" are common on games too and must not turn a cartridge into a console.
GAME_HARDWARE_CUE_RE = re.compile(
    # "Bundle" is not hardware: "PS3 games bundle" is a bundle of games. A
    # console-with-games bundle is handled by CONSOLE_BUNDLE_RE instead.
    r"\b(?:console|handheld|boxed\s+console|complete\s+console|"
    r"controllers?|includes?\s+(?:an?\s+)?controllers?|1tb|2tb|512gb|825gb|"
    r"\d{3}\s*gb)\b",
    re.I,
)

GPU_RE = re.compile(
    r"\b(?:nvidia\s+|geforce\s+)?(rtx|gtx)\s*([1-5]\d{3})\s*"
    r"((?:(?:ti|super)\s*){1,2})?\b",
    re.I,
)
# Marketplace sellers often put the model before the family ("4060 RTX") or
# omit the family entirely ("MSI GeForce 5090"). These forms are unambiguous
# when Nvidia/GeForce is present, so identify them instead of losing a card to
# keyword-only discovery.
GPU_MODEL_THEN_BRAND_RE = re.compile(
    r"\b([1-5]\d{3})\s*(ti|super)?\s*(rtx|gtx)\b", re.I
)
GPU_GEFORCE_BARE_RE = re.compile(
    r"\bgeforce\s+(?:rtx\s*)?(\d{3,4})\s*(ti|super)?\b", re.I
)
GPU_NVIDIA_BARE_RE = re.compile(
    r"\bnvidia\s+(\d{3,4})\s*(ti|super)?\b", re.I
)
GPU_LEGACY_RE = re.compile(r"\b(gtx|gt)\s*(\d{3,4})\s*(ti|super)?\b", re.I)
AMD_GPU_RE = re.compile(
    r"\b(?:amd\s+|radeon\s+)?(rx)\s*(\d{3,4})\s*(xtx|xt|gre)?\b", re.I
)
ARC_GPU_RE = re.compile(r"\b(?:intel\s+)?arc\s*([ab])\s*(\d{3})\b", re.I)
RYZEN_RE = re.compile(
    r"\b(?:amd\s+)?ryzen\s*([3579])\s*(\d{4})\s*(x3d|xt|ge|g|x|f)?\b", re.I
)
# Compact marketing form: "Ryzen 5600X" means tier 5, model 5600X. It must not
# be read as a "Ryzen 5" with model 600 or as no CPU at all.
RYZEN_COMPACT_RE = re.compile(
    r"\b(?:amd\s+)?ryzen\s*([3579])(\d{3})\s*(x3d|xt|ge|g|x|f)?\b", re.I
)
INTEL_RE = re.compile(
    r"\b(?:intel\s+)?(?:core\s+)?i([3579])[\s-]*(\d{4,5})\s*(ks|kf|k|f|t|h|hk|hx|u|g\d?)?\b",
    re.I,
)
ULTRA_RE = re.compile(
    r"\b(?:intel\s+)?(?:core\s+)?ultra\s*([579])\s*(\d{3})\s*(ks|kf|k|f|t)?\b", re.I
)
DDR_RE = re.compile(r"\bddr\s*([45])\b", re.I)
CAPACITY_GB_RE = re.compile(r"\b(\d{1,3})\s*gb\b", re.I)
KIT_RE = re.compile(r"\b(\d)\s*[x×]\s*(\d{1,3})\s*gb\b", re.I)
CAPACITY_TB_RE = re.compile(r"\b(\d{1,2}(?:\.\d)?)\s*tb\b", re.I)
SIZE_GB_RE = re.compile(r"\b(\d{2,4})\s*gb\b", re.I)
MOBO_CHIPSET_RE = re.compile(
    r"\b(x870e?|x670e?|b650e?|a620|b550|a520|x570|x470|b450|b350|a320|x370|"
    r"x399|z890|z790|z690|z590|z490|z390|z370|z270|z170|b760|b660|b560|b460|"
    r"h770|h670|h610|h510|h410|x299|x99|trx40|trx50|wrx80)(?:[a-z]{1,2})?\b",
    re.I,
)
MOBO_BRAND_OR_MODEL_RE = re.compile(
    r"\b(?:asus|msi|gigabyte|aorus|asrock|biostar|evga|nzxt|prime|tuf|rog\s+strix|"
    r"tomahawk|bazooka|mortar|steel\s+legend|gaming\s+plus|pro\s+[bzx]\d{3})\b",
    re.I,
)
STORAGE_BRANDS = (
    "sk hynix", "samsung", "crucial", "kingston", "seagate", "sandisk", "corsair",
    "sabrent", "kioxia", "toshiba", "western digital", "wd", "lexar", "teamgroup",
    "transcend", "adata", "intel", "silicon power", "sp", "pny", "netac", "fanxiang",
    "union memory", "hynix", "verbatim", "integral", "origin",
)
SSD_MODEL_RE = re.compile(
    r"\b(9[89]0\s*(?:pro|evo|plus)|970\s*(?:evo|pro|plus)|sn\s?\d{3,4}x?|"
    r"a55|a60|a80|kc3000|nv[12]|p[35]\s*(?:plus|pro)?|mx500|bx500|t500|t700|"
    r"mp600(?:\s+pro)?|rocket\s*\d?|firecuda\s*\d+|nvme\s*\d?|sx\s*\d{3,4})\b",
    re.I,
)
NETWORK_CUE_RE = re.compile(
    r"\b(router|mesh\s+(?:wifi|wi-fi|system|network)|wifi\s+(?:router|system|mesh)|"
    r"wi-fi\s+(?:router|system|mesh)|access\s+point|network\s+switch|ethernet\s+switch|"
    r"unifi|dream\s+machine|cloud\s+gateway|udm|uxg|usw|orbi|deco|eero|nighthawk|"
    r"gateway\s+router)\b",
    re.I,
)
NETWORK_BRANDS = (
    "ubiquiti", "unifi", "netgear", "tp-link", "tplink", "asus", "eero",
    "linksys", "zyxel", "mikrotik", "google nest", "amazon eero", "deco",
)
NETWORK_MODEL_RE = re.compile(
    r"\b(udm[- ]?(?:pro|se)?|uxg[- ]?(?:lite|max)?|usw[- ]?\w+|u[67][- ]?\w+|"
    r"rbk\d{3,4}|rbr\d{3,4}|rax\d{2,3}|r7\d{3}|r8\d{3}|rt[- ]?[ab]\w{2,8}|"
    r"xe\d{2,3}|xd\d{2,3}|be\d{3,4}|deco[- ]?\w+|orbi[- ]?\w+|eero(?:\s+\d+)?|"
    r"gs\d{2,4}(?:-\w+)?|ms\d{2,4}(?:-\w+)?|cr[s]?\d{3,4})\b",
    re.I,
)
MONITOR_BRAND_RE = re.compile(
    r"\b(asus|acer|dell|hp|lg|samsung|msi|benq|viewsonic|aoc|gigabyte|alienware|"
    r"lenovo|philips|iiyama|xiaomi|huawei|aoc|electriq|kooorui|hanns\.?g)\b",
    re.I,
)
MONITOR_MODEL_RE = re.compile(
    r"(?<![a-z])([a-z]{2,5}[- ]?\d{3,4}[a-z0-9-]{0,8})",
    re.I,
)
CONSOLE_PATTERNS = (
    ("console:playstation-portal", "PlayStation Portal", re.compile(
        r"\b(?:playstation\s+portal|ps5\s+portal|ps\s+portal)\b", re.I
    )),
    ("console:ps5-pro", "PlayStation 5 Pro", re.compile(r"\b(?:ps5|playstation\s*5)\s*pro\b", re.I)),
    ("console:ps5-slim-digital", "PlayStation 5 Slim Digital", re.compile(
        r"\b(?:ps5|playstation\s*5)\s*slim\s*(?:digital|disc[- ]?less)\b|"
        r"\b(?:ps5|playstation\s*5)\s*(?:digital|disc[- ]?less)\s*slim\b",
        re.I,
    )),
    ("console:ps5-slim", "PlayStation 5 Slim", re.compile(r"\b(?:ps5|playstation\s*5)\s*slim\b", re.I)),
    ("console:ps5-digital", "PlayStation 5 Digital", re.compile(r"\b(?:ps5|playstation\s*5)\s*(?:digital|disc[- ]?less)\b", re.I)),
    ("console:ps5", "PlayStation 5", re.compile(r"\b(?:ps5|playstation\s*5)\b", re.I)),
    ("console:ps4-pro", "PlayStation 4 Pro", re.compile(r"\b(?:ps4|playstation\s*4)\s*pro\b", re.I)),
    ("console:ps4-slim", "PlayStation 4 Slim", re.compile(r"\b(?:ps4|playstation\s*4)\s*slim\b", re.I)),
    ("console:ps4", "PlayStation 4", re.compile(r"\b(?:ps4|playstation\s*4)\b", re.I)),
    ("console:ps3", "PlayStation 3", re.compile(r"\b(?:ps3|playstation\s*3)\b", re.I)),
    ("console:ps2", "PlayStation 2", re.compile(r"\b(?:ps2|playstation\s*2)\b", re.I)),
    ("console:ps1", "PlayStation 1", re.compile(r"\b(?:ps1|playstation\s*(?:1|one)|ps\s+one)\b", re.I)),
    ("console:xbox-series-x", "Xbox Series X", re.compile(r"\bxbox\s+series\s+x\b", re.I)),
    ("console:xbox-series-s", "Xbox Series S", re.compile(r"\bxbox\s+series\s+s\b", re.I)),
    ("console:xbox-one-x", "Xbox One X", re.compile(r"\bxbox\s+one\s+x\b", re.I)),
    ("console:xbox-one-s", "Xbox One S", re.compile(r"\bxbox\s+one\s+s\b", re.I)),
    ("console:xbox-one", "Xbox One", re.compile(r"\bxbox\s+one\b", re.I)),
    ("console:xbox-360-s", "Xbox 360 S", re.compile(r"\bxbox\s*360\s*(?:s|slim|e)\b", re.I)),
    ("console:xbox-360-elite", "Xbox 360 Elite", re.compile(r"\bxbox\s*360\s*elite\b", re.I)),
    ("console:xbox-360", "Xbox 360", re.compile(r"\bxbox\s*360\b", re.I)),
    ("console:switch-2", "Nintendo Switch 2", re.compile(r"\b(?:nintendo\s+)?switch\s*2\b", re.I)),
    ("console:switch-oled", "Nintendo Switch OLED", re.compile(r"\b(?:nintendo\s+)?switch\s*oled\b", re.I)),
    ("console:switch-lite", "Nintendo Switch Lite", re.compile(r"\bswitch\s*lite\b", re.I)),
    ("console:switch", "Nintendo Switch", re.compile(r"\b(?:nintendo\s+switch|switch\s+console|switch\s+v2)\b", re.I)),
    ("console:steam-deck-oled", "Steam Deck OLED", re.compile(r"\bsteam\s+deck\s*oled\b", re.I)),
    ("console:steam-deck", "Steam Deck", re.compile(r"\bsteam\s+deck\b", re.I)),
    ("console:rog-ally-x", "ROG Ally X", re.compile(r"\brog\s+ally\s*x\b", re.I)),
    ("console:rog-ally", "ROG Ally", re.compile(r"\brog\s+ally\b", re.I)),
    ("console:legion-go", "Lenovo Legion Go", re.compile(r"\blegion\s+go\b", re.I)),
    ("console:new-3ds-xl", "New Nintendo 3DS XL", re.compile(
        r"\bnew\s+(?:nintendo\s+)?3ds\s*xl\b", re.I
    )),
    ("console:3ds-xl", "Nintendo 3DS XL", re.compile(r"\b(?:nintendo\s+)?3ds\s*xl\b", re.I)),
    ("console:new-3ds", "New Nintendo 3DS", re.compile(r"\bnew\s+(?:nintendo\s+)?3ds\b", re.I)),
    ("console:3ds", "Nintendo 3DS", re.compile(r"\b(?:nintendo\s+)?3ds\b", re.I)),
    ("console:2ds-xl", "Nintendo 2DS XL", re.compile(r"\b(?:nintendo\s+)?2ds\s*xl\b", re.I)),
    ("console:2ds", "Nintendo 2DS", re.compile(r"\b(?:nintendo\s+)?2ds\b", re.I)),
    ("console:dsi-xl", "Nintendo DSi XL", re.compile(r"\b(?:nintendo\s+)?dsi\s*xl\b", re.I)),
    ("console:dsi", "Nintendo DSi", re.compile(r"\b(?:nintendo\s+)?dsi\b", re.I)),
    ("console:ds-lite", "Nintendo DS Lite", re.compile(r"\b(?:nintendo\s+)?ds\s*lite\b", re.I)),
    ("console:ds", "Nintendo DS", re.compile(r"\b(?:nintendo\s+)?ds\b", re.I)),
    ("console:gameboy-advance-sp", "Game Boy Advance SP", re.compile(
        r"\bgame\s*boy\s*advance\s*sp\b", re.I
    )),
    ("console:gameboy-advance", "Game Boy Advance", re.compile(
        r"\b(?:game\s*boy\s*advance|gba)\b", re.I
    )),
    ("console:gameboy-color", "Game Boy Color", re.compile(
        r"\b(?:game\s*boy\s*color|gbc)\b", re.I
    )),
    ("console:gameboy-pocket", "Game Boy Pocket", re.compile(
        r"\bgame\s*boy\s*pocket\b", re.I
    )),
    ("console:gameboy", "Game Boy", re.compile(r"\bgame\s*boy\b", re.I)),
    ("console:gamecube", "Nintendo GameCube", re.compile(r"\bgamecube\b", re.I)),
    ("console:wii-u", "Nintendo Wii U", re.compile(r"\b(?:nintendo\s+)?wii\s*u\b", re.I)),
    ("console:wii", "Nintendo Wii", re.compile(r"\b(?:nintendo\s+)?wii\b", re.I)),
    ("console:n64", "Nintendo 64", re.compile(r"\b(?:nintendo\s*64|n64)\b", re.I)),
    ("console:snes", "Super Nintendo", re.compile(r"\b(?:snes|super\s+nintendo)\b", re.I)),
    ("console:mega-drive", "Sega Mega Drive", re.compile(
        r"\b(?:sega\s*)?(?:mega\s*drive|genesis)\b", re.I
    )),
    ("console:master-system", "Sega Master System", re.compile(
        r"\bsega\s*master\s*system\b", re.I
    )),
    ("console:dreamcast", "Sega Dreamcast", re.compile(r"\bdreamcast\b", re.I)),
    ("console:saturn", "Sega Saturn", re.compile(r"\bsega\s*saturn\b", re.I)),
    ("console:game-gear", "Sega Game Gear", re.compile(r"\bgame\s*gear\b", re.I)),
    ("console:vita", "PlayStation Vita", re.compile(r"\b(?:ps\s*vita|playstation\s*vita)\b", re.I)),
    ("console:psp", "PlayStation Portable", re.compile(r"\b(?:psp|playstation\s*portable)\b", re.I)),
)
GENERIC_CONSOLE_KEYS = {
    "console:ps5", "console:ps4", "console:ps3", "console:ps2", "console:ps1",
    "console:xbox-series-x", "console:xbox-series-s", "console:xbox-one",
    "console:xbox-360", "console:switch", "console:switch-2", "console:ds",
    "console:3ds", "console:2ds", "console:gameboy", "console:gamecube",
    "console:wii", "console:n64", "console:snes", "console:dreamcast",
    "console:vita", "console:psp",
}
LAPTOP_RE = re.compile(
    r"\b(laptop|notebook|macbook|chromebook|elitebook|probook|latitude|precision|"
    r"spectre|envy|pavilion|yoga)\b",
    re.I,
)
# Model-line cues catch listings that omit the word "laptop" but are clearly a
# notebook (e.g. "MSI Thin GF63 RTX 4060").
LAPTOP_MODEL_PATTERNS: tuple[tuple[str, str, re.Pattern], ...] = (
    ("acer-nitro", "Acer Nitro", re.compile(r"\b(?:acer\s+nitro|nitro\s*(?:5|v|16|17))\b", re.I)),
    ("acer-predator", "Acer Predator", re.compile(
        r"\b(?:acer\s+predator|predator\s+(?:helios|triton|neo))\b", re.I
    )),
    ("asus-tuf-laptop", "ASUS TUF Gaming laptop", re.compile(r"\btuf\s+(?:gaming\s+)?(?:a1[457]|f1[57])\b", re.I)),
    ("asus-rog-strix-laptop", "ASUS ROG Strix laptop", re.compile(r"\brog\s+strix\s+(?:g1[4678]|scar|18)\b", re.I)),
    ("asus-zephyrus", "ASUS ROG Zephyrus", re.compile(r"\bzephyrus\b", re.I)),
    ("msi-katana", "MSI Katana", re.compile(r"\bmsi\s+katana\b", re.I)),
    ("msi-gf63", "MSI GF63", re.compile(r"\bgf63\b", re.I)),
    ("msi-thin", "MSI Thin", re.compile(r"\bmsi\s+thin\b", re.I)),
    ("msi-stealth", "MSI Stealth", re.compile(r"\bmsi\s+stealth\b", re.I)),
    ("msi-raider", "MSI Raider", re.compile(r"\bmsi\s+raider\b", re.I)),
    ("msi-vector", "MSI Vector", re.compile(r"\bmsi\s+vector\b", re.I)),
    ("msi-sword", "MSI Sword", re.compile(r"\bmsi\s+sword\b", re.I)),
    ("msi-cyborg", "MSI Cyborg", re.compile(r"\bmsi\s+cyborg\b", re.I)),
    ("msi-pulse", "MSI Pulse", re.compile(r"\bmsi\s+pulse\b", re.I)),
    ("lenovo-legion", "Lenovo Legion", re.compile(r"\b(?:lenovo\s+)?legion\s+(?:5|7|pro|slim)\b", re.I)),
    ("lenovo-loq", "Lenovo LOQ", re.compile(r"\bloq\b", re.I)),
    ("lenovo-ideapad", "Lenovo IdeaPad", re.compile(r"\bideapad\b", re.I)),
    ("lenovo-thinkpad", "Lenovo ThinkPad", re.compile(r"\bthinkpad\b", re.I)),
    ("dell-g15", "Dell G15", re.compile(r"\bdell\s+g1[56]\b", re.I)),
    ("razer-blade", "Razer Blade", re.compile(
        r"\b(?:razer\s+blade|blade\s+(?:14|15|16|17))\b", re.I
    )),
    ("surface-laptop", "Microsoft Surface Laptop", re.compile(r"\bsurface\s+laptop\b", re.I)),
    ("hp-elitebook", "HP EliteBook", re.compile(r"\belitebook\b", re.I)),
    ("hp-probook", "HP ProBook", re.compile(r"\bprobook\b", re.I)),
    ("hp-omen", "HP OMEN laptop", re.compile(r"\b(?:hp\s+)?omen\b", re.I)),
    ("hp-victus", "HP Victus laptop", re.compile(r"\b(?:hp\s+)?victus\b", re.I)),
    ("hp-zbook", "HP ZBook laptop", re.compile(r"\b(?:hp\s+)?zbook\b", re.I)),
    ("hp-spectre", "HP Spectre", re.compile(r"\b(?:hp\s+)?spectre\b", re.I)),
    ("hp-envy", "HP Envy", re.compile(r"\b(?:hp\s+)?envy\b", re.I)),
    ("dell-latitude", "Dell Latitude", re.compile(r"\blatitude\b", re.I)),
    ("dell-xps", "Dell XPS", re.compile(r"\bxps\s*(?:13|14|15|16|17)?\b", re.I)),
    ("lenovo-yoga", "Lenovo Yoga", re.compile(r"\b(?:lenovo\s+)?yoga\b", re.I)),
    ("lenovo-x1-carbon", "Lenovo ThinkPad X1 Carbon", re.compile(
        r"\b(?:lenovo\s+)?(?:thinkpad\s+)?x1\s*carbon\b", re.I
    )),
    ("lenovo-miix", "Lenovo Miix", re.compile(r"\b(?:lenovo\s+)?miix\b", re.I)),
    ("hp-elite-dragonfly", "HP Elite Dragonfly", re.compile(
        r"\b(?:hp\s+)?elite\s+dragonfly\b", re.I
    )),
    ("asus-vivobook", "ASUS VivoBook", re.compile(r"\b(?:asus\s+)?vivobook\b", re.I)),
    ("asus-zenbook", "ASUS ZenBook", re.compile(r"\b(?:asus\s+)?zenbook\b", re.I)),
    ("panasonic-toughbook", "Panasonic Toughbook", re.compile(
        r"\b(?:panasonic\s+)?toughbook\b", re.I
    )),
    ("samsung-galaxy-book", "Samsung Galaxy Book", re.compile(
        r"\b(?:samsung\s+)?galaxy\s+book\b", re.I
    )),
    ("acer-aspire", "Acer Aspire", re.compile(
        r"\b(?:acer\s+aspire|aspire\s+(?:3|5|7|v|e|one))\b", re.I
    )),
    ("acer-swift", "Acer Swift", re.compile(
        r"\b(?:acer\s+swift|swift\s+(?:1|3|5|7|edge|x))\b", re.I
    )),
    ("dell-inspiron", "Dell Inspiron", re.compile(r"\binspiron\b", re.I)),
    ("dell-vostro", "Dell Vostro", re.compile(r"\bvostro\b", re.I)),
    ("surface-book", "Microsoft Surface Book", re.compile(r"\bsurface\s+book\b", re.I)),
)
LAPTOP_CUE_RE = re.compile(
    r"\b(" + "|".join(pattern.pattern for _, _, pattern in LAPTOP_MODEL_PATTERNS) + r")",
    re.I,
)
PC_RE = re.compile(
    r"\b(gaming\s+pc|desktop\s+pc|pc\s+tower|full\s+pc|custom\s+pc|"
    r"computer\s+tower|workstation|gaming\s+rig|complete\s+pc|pc\s+setup|"
    r"desktop\s+computer|mini\s*pc|micro\s*pc|tiny\s*pc|sff\s+pc|"
    r"gaming\s+computer|gaming\s+desktop|desktop\s+tower|computer\s+pc|"
    r"pc\s+desktop|tower\s+pc|pc\s+(?:bundle|with|and)|\bcomplete\s+pc\b|"
    r"optiplex|thinkcentre|thinkstation|elitedesk|prodesk|esprimo|"
    r"alienware\s+aurora|ideacentre|all[-\s]?in[-\s]?one)\b",
    re.I,
)
SYSTEM_BRAND_RE = re.compile(
    r"\b(?:dell|hp|lenovo|acer|asus|msi|alienware|gigabyte)\b[^|,]{0,35}"
    r"\b(?:pc|desktop|computer|aio|all[-\s]?in[-\s]?one|workstation)\b",
    re.I,
)
SCREEN_RE = re.compile(
    r"\b(\d{2})(?:\.\d)?\s*-?\s*(?:inch|in\b|[\"”″])", re.I
)
RES_RE = re.compile(
    r"\b(4k|uhd|1440p|2k|qhd|1080p|fhd|full\s*hd)\b", re.I
)
HZ_RE = re.compile(r"\b(\d{2,3})\s*hz\b", re.I)
IPHONE_RE = re.compile(
    r"\biphone\s*(\d{1,2})\s*(pro\s+max|pro|plus|mini)?\b", re.I
)
IPAD_RE = re.compile(
    r"\bipad\s*(pro|air|mini)?\s*(m\d)?\s*(\d{1,2}(?:\.\d)?)?\s*(?:inch|in)?\b", re.I
)
MACBOOK_RE = re.compile(r"\bmacbook\s*(air|pro)?\s*(m\d(?:\s*(?:pro|max))?)?\b", re.I)
BRANDS = ("asus", "msi", "gigabyte", "evga", "sapphire", "powercolor", "xfx",
          "zotac", "palit", "pny", "inno3d", "aorus", "dell", "hp", "lenovo",
          "acer", "razer", "apple", "samsung", "corsair", "kingston", "crucial",
          "sabrent", "wd", "seagate", "samsung", "nzxt")


@dataclass
class Identification:
    product_key: str | None
    product_name: str | None
    category: str
    kind: str
    confidence: float
    condition: str = "unknown"
    condition_factor: float = 0.95
    is_faulty: bool = False
    is_wanted: bool = False
    is_bundle: bool = False
    flags: list[str] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    # How strongly the title supports the exact identity, separate from the
    # valuation's price evidence. Never presented as a probability.
    identity_level: str = "insufficient"
    identity_basis: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_key": self.product_key,
            "product_name": self.product_name,
            "category": self.category,
            "kind": self.kind,
            "confidence": round(self.confidence, 3),
            "identity_level": self.identity_level,
            "identity_basis": self.identity_basis,
            "condition": self.condition,
            "condition_factor": self.condition_factor,
            "is_faulty": self.is_faulty,
            "is_wanted": self.is_wanted,
            "is_bundle": self.is_bundle,
            "flags": self.flags,
            "attrs": self.attrs,
        }


def detect_condition(text: str) -> str:
    t = lower_text(text)
    if re.search(r"\b(spares?\s*(?:or|/)?\s*repairs?|for\s+parts|faulty|not\s+working|broken|dead)\b", t):
        return "faulty"
    # A cracked or chipped screen/back still works but is materially less
    # valuable. Valuing it as "used" makes a damaged handset look like a
    # bargain against good-condition comparables.
    if re.search(r"\b(?:cracked?|chipped)\b", t):
        return "fair"
    if re.search(
        r"\b(brand\s+new|new\s+sealed|sealed|unopened|new\s+in\s+box|bnib|new\s+with\s+tags|"
        r"new\s+without\s+tags|new\s+with\s+box|new\s+other)\b",
        t,
    ):
        return "new"
    if re.search(r"\b(open\s*box|opened\s+but\s+unused)\b", t):
        return "open_box"
    if re.search(r"\b(like\s+new|mint|immaculate|as\s+new)\b", t):
        return "like_new"
    if re.search(r"\b(excellent\s+condition|excellent)\b", t):
        return "excellent"
    if re.search(r"\b(very\s+good|great\s+condition|great)\b", t):
        return "very_good"
    if re.search(r"\b(good\s+condition|good|boxed)\b", t):
        return "good"
    if re.search(r"\b(fair|well\s+used|signs\s+of\s+use|used)\b", t):
        return "used"
    if re.search(r"\b(poor|heavily\s+used|rough)\b", t):
        return "poor"
    return "unknown"


def _nvidia_family(number: str) -> str:
    """Infer the marketing family from an Nvidia model number.

    Only used when the seller wrote "GeForce 5090" or "Nvidia 980 Ti" without
    the RTX/GTX prefix. A bare 3-digit card in the 700 range is treated as GT
    (GT 730) rather than GTX, which keeps those inexpensive cards in their own
    comparable pool.
    """
    if len(number) == 4:
        return "rtx" if number[0] in {"2", "3", "4", "5"} else "gtx"
    return "gtx" if int(number) >= 780 else "gt"


def _gpu(text: str) -> tuple[str, str, dict[str, Any]] | None:
    laptop = bool(re.search(r"\b(?:laptop|notebook|mobile|max-q)\b", text, re.I))
    match = GPU_RE.search(text)
    if match:
        prefix = match.group(1).lower()
        number = match.group(2)
        suffix_raw = (match.group(3) or "").lower().strip()
        if suffix_raw:
            parts = ["ti" if p == "ti" else "super" for p in suffix_raw.split()]
            if "ti" in parts and "super" in parts:
                parts = ["ti", "super"]
            suffix = "-".join(parts)
            label_suffix = " ".join("Ti" if p == "ti" else "Super" for p in parts)
        else:
            suffix, label_suffix = "", ""
        key = f"gpu:{prefix}-{number}" + (f"-{suffix}" if suffix else "")
        brand = "NVIDIA GeForce"
        name = f"{brand} {prefix.upper()} {number}" + (f" {label_suffix}" if label_suffix else "")
        full = text[match.start():match.end() + 24]
        vram_match = re.search(r"\b(\d{1,2})\s*g(?:b)?\b", full)
        vram = int(vram_match.group(1)) if vram_match else None
        if vram:
            key += f"-{vram}gb"
        if laptop:
            key += ":laptop"
        generation = number[:2] if prefix == "rtx" and number.startswith(("40", "50")) else (
            number[:2] if prefix == "gtx" and number.startswith("16") else number[:2]
        )
        attrs = {
            "brand": "NVIDIA", "family": prefix.upper(), "model": number,
            "suffix": label_suffix or None, "generation": generation, "vram_gb": vram,
            "form_factor": "laptop" if laptop else "desktop",
        }
        return key, name, attrs
    match = GPU_MODEL_THEN_BRAND_RE.search(text)
    if match:
        number, suffix_raw, prefix = match.group(1), (match.group(2) or "").lower(), match.group(3).lower()
        key = f"gpu:{prefix}-{number}" + (f"-{suffix_raw}" if suffix_raw else "")
        name = f"NVIDIA GeForce {prefix.upper()} {number}" + (f" {suffix_raw.title()}" if suffix_raw else "")
        full = text[match.start():match.end() + 24]
        vram_match = re.search(r"\b(\d{1,2})\s*g(?:b)?\b", full)
        vram = int(vram_match.group(1)) if vram_match else None
        if vram:
            key += f"-{vram}gb"
        if laptop:
            key += ":laptop"
        return key, name, {
            "brand": "NVIDIA", "family": prefix.upper(), "model": number,
            "suffix": suffix_raw or None, "generation": number[:2], "vram_gb": vram,
            "form_factor": "laptop" if laptop else "desktop",
        }
    match = GPU_GEFORCE_BARE_RE.search(text) or GPU_NVIDIA_BARE_RE.search(text)
    if match:
        number, suffix_raw = match.group(1), (match.group(2) or "").lower()
        prefix = _nvidia_family(number)
        key = f"gpu:{prefix}-{number}" + (f"-{suffix_raw}" if suffix_raw else "")
        name = f"NVIDIA GeForce {prefix.upper()} {number}" + (f" {suffix_raw.title()}" if suffix_raw else "")
        full = text[match.start():match.end() + 24]
        vram_match = re.search(r"\b(\d{1,2})\s*g(?:b)?\b", full)
        vram = int(vram_match.group(1)) if vram_match else None
        if vram:
            key += f"-{vram}gb"
        if laptop:
            key += ":laptop"
        return key, name, {
            "brand": "NVIDIA", "family": prefix.upper(), "model": number,
            "suffix": suffix_raw or None, "generation": number[:2], "vram_gb": vram,
            "form_factor": "laptop" if laptop else "desktop",
        }
    match = GPU_LEGACY_RE.search(text)
    if match:
        prefix, number, suffix_raw = match.group(1).lower(), match.group(2), (match.group(3) or "").lower()
        key = f"gpu:{prefix}-{number}" + (f"-{suffix_raw}" if suffix_raw else "")
        name = f"NVIDIA GeForce {prefix.upper()} {number}" + (f" {suffix_raw.title()}" if suffix_raw else "")
        full = text[match.start():match.end() + 24]
        vram_match = re.search(r"\b(\d{1,2})\s*g(?:b)?\b", full)
        vram = int(vram_match.group(1)) if vram_match else None
        if vram:
            key += f"-{vram}gb"
        if laptop:
            key += ":laptop"
        return key, name, {
            "brand": "NVIDIA", "family": prefix.upper(), "model": number,
            "suffix": suffix_raw or None, "generation": number[:2], "vram_gb": vram,
            "form_factor": "laptop" if laptop else "desktop",
        }
    match = AMD_GPU_RE.search(text)
    if match:
        number = match.group(2)
        suffix_raw = (match.group(3) or "").lower()
        vram_match = re.search(r"\b(\d{1,2})\s*gb\b", text[match.start():match.end() + 24])
        vram = int(vram_match.group(1)) if vram_match else None
        key = f"gpu:rx-{number}" + (f"-{suffix_raw}" if suffix_raw else "")
        if vram:
            key += f"-{vram}gb"
        if laptop:
            key += ":laptop"
        name = f"AMD Radeon RX {number}" + (f" {suffix_raw.upper()}" if suffix_raw else "")
        return key, name, {
            "brand": "AMD", "model": number, "suffix": suffix_raw or None,
            "generation": number[:2], "vram_gb": vram,
            "form_factor": "laptop" if laptop else "desktop",
        }
    match = ARC_GPU_RE.search(text)
    if match:
        family = match.group(1).lower()
        number = match.group(2)
        key = f"gpu:arc-{family}{number}" + (":laptop" if laptop else "")
        name = f"Intel Arc {family.upper()}{number}"
        return key, name, {
            "brand": "Intel", "model": number,
            "form_factor": "laptop" if laptop else "desktop",
        }
    return None


def _cpu(text: str) -> tuple[str, str, dict[str, Any]] | None:
    match = RYZEN_RE.search(text)
    compact = False
    if not match:
        match = RYZEN_COMPACT_RE.search(text)
        compact = match is not None
    if match:
        tier = match.group(1)
        number = f"{tier}{match.group(2)}" if compact else match.group(2)
        suffix = (match.group(3) or "").lower()
        key = f"cpu:amd:ryzen-{tier}-{number}" + suffix
        name = f"AMD Ryzen {tier} {number}" + (suffix.upper() if suffix else "")
        return key, name, {"brand": "AMD", "tier": tier, "model": number, "suffix": suffix or None}
    match = ULTRA_RE.search(text)
    if match:
        tier, number, suffix = match.group(1), match.group(2), (match.group(3) or "").lower()
        key = f"cpu:intel:ultra-{tier}-{number}" + (f"-{suffix}" if suffix else "")
        name = f"Intel Core Ultra {tier} {number}" + (suffix.upper() if suffix else "")
        return key, name, {"brand": "Intel", "tier": tier, "model": number}
    match = INTEL_RE.search(text)
    if match:
        tier, number, suffix = match.group(1), match.group(2), (match.group(3) or "").lower()
        key = f"cpu:intel:core-i{tier}-{number}" + (f"-{suffix}" if suffix else "")
        name = f"Intel Core i{tier}-{number}" + (suffix.upper() if suffix else "")
        return key, name, {"brand": "Intel", "tier": tier, "model": number, "suffix": suffix or None}
    # Budget desktop and mini-PC chips. Without these, "Stone NT310-H Pentium
    # 3550M 8GB RAM 128GB SSD" was filed as the 128GB SSD it contains and
    # valued at £221 against storage comparables.
    match = re.search(
        r"\b(pentium|celeron)\s+(?:gold\s+|silver\s+)?([a-z]?\d{3,5}[a-z]{0,2})\b",
        text,
    )
    if match:
        family, number = match.group(1).lower(), match.group(2).lower()
        return (
            f"cpu:intel:{family}-{number}",
            f"Intel {family.title()} {number.upper()}",
            {"brand": "Intel", "tier": family, "model": number},
        )
    match = re.search(r"\bathlon\s+(?:silver\s+|gold\s+)?(\d{3,4}[a-z]{0,2})\b", text)
    if match:
        number = match.group(1).lower()
        return (
            f"cpu:amd:athlon-{number}",
            f"AMD Athlon {number.upper()}",
            {"brand": "AMD", "tier": "athlon", "model": number},
        )
    return None


def _ram(text: str) -> tuple[str, str, dict[str, Any]] | None:
    ddr = DDR_RE.search(text)
    if not ddr:
        return None
    capacity: int | None = None
    kit = KIT_RE.search(text)
    if kit:
        capacity = int(kit.group(1)) * int(kit.group(2))
    else:
        for match in CAPACITY_GB_RE.finditer(text):
            value = int(match.group(1))
            if value in (8, 16, 24, 32, 48, 64, 96, 128):
                capacity = value
                break
    if not capacity:
        return None
    gen = ddr.group(1)
    speed_match = re.search(r"\b([2-8]\d{3})\s*mhz\b", text)
    speed = int(speed_match.group(1)) if speed_match else None
    kit_count = int(kit.group(1)) if kit else None
    kit_size = int(kit.group(2)) if kit else None
    if not kit and re.search(r"\bsingle\s+(?:stick|module|dimm)\b", text, re.I):
        # A single 16GB module is not the same sellable product as a 2x8GB
        # dual-channel kit even though both total 16GB.
        kit_count, kit_size = 1, capacity
    if re.search(r"\b(ecc|registered|server|rdimm|lrdimm)\b", text):
        form = "ecc"
    elif re.search(r"\b(sodimm|so-dimm|laptop|notebook)\b", text):
        form = "laptop"
    else:
        form = "desktop" if re.search(r"\b(desktop|dimm|udimm)\b", text) else ""
    key = f"ram:ddr{gen}:{capacity}gb"
    if speed:
        key += f":{speed}"
    if kit_count and kit_size:
        key += f":{kit_count}x{kit_size}"
    if form:
        key += f":{form}"
    name = f"{capacity}GB DDR{gen}" + (f" {speed}MHz" if speed else "") + " RAM"
    return key, name, {
        "ddr": gen, "capacity_gb": capacity, "speed_mhz": speed,
        "kit_count": kit_count, "kit_size_gb": kit_size, "form_factor": form or None,
    }


def _ssd(text: str) -> tuple[str, str, dict[str, Any]] | None:
    if not re.search(r"\b(ssd|nvme|solid\s+state|m\.2)\b", text):
        return None
    if re.search(r"\b(flash\s+drive|usb\s+stick|pen\s+drive)\b", text):
        return None
    if re.search(r"\b(enclosure|caddy|case\s+only|no\s+drive|empty\s+box)\b", text) and not re.search(
        r"\b(with|includes?|inc\.?)\s+(?:a\s+)?(?:\d+\s*(?:gb|tb)|drive)\b", text
    ):
        return None
    capacity: float | None = None
    tb = CAPACITY_TB_RE.search(text)
    if tb:
        capacity = float(tb.group(1)) * 1024
    else:
        for match in SIZE_GB_RE.finditer(text):
            value = int(match.group(1))
            if value in (120, 128, 240, 250, 256, 480, 500, 512, 960, 1000, 2000, 4000):
                capacity = value
                break
    if not capacity:
        return None
    brand = next((name for name in STORAGE_BRANDS if re.search(rf"\b{re.escape(name)}\b", text)), None)
    model_match = SSD_MODEL_RE.search(text)
    model = " ".join(model_match.group(1).lower().split()) if model_match else None
    model = model.replace(" ", "-") if model else None
    if brand == "western digital":
        brand = "wd"
    if re.search(r"\b(external|portable|usb)\b", text):
        interface = "usb"
        form = "external"
    else:
        nvme = bool(re.search(r"\b(nvme|m\.2|m2|pcie|pcle)\b", text))
        interface = "nvme" if nvme else ("sata" if re.search(r"\b(sata|2\.5)\b", text) else "ssd")
        if re.search(r"\b(m\.2|m2)\b", text):
            form = "m2"
        elif re.search(r"\b2\.5\b", text):
            form = "2.5"
        else:
            form = "internal" if re.search(r"\binternal\b", text) else "unspecified"
    if interface == "ssd" and form == "unspecified" and not brand and not model:
        # "512GB SSD" alone says nothing about NVMe vs SATA, 2.5" vs M.2 or
        # the model family. Keeping it ungrouped is safer than averaging
        # fundamentally different drives.
        return None
    pcie = re.search(r"\b(?:pcie|pcle)\s*([345])(?:\.0)?\b|\bgen\s*([345])\b", text)
    pcie_gen = (pcie.group(1) or pcie.group(2)) if pcie else None
    if capacity >= 1000:
        cap_label = f"{round(capacity / 1000):g}tb" if capacity % 1000 == 0 else f"{capacity / 1024:g}tb"
    else:
        cap_label = f"{int(capacity)}gb"
    key = f"ssd:{interface}:{form}:{cap_label}"
    if brand:
        key += f":{brand.replace(' ', '-')}"
    if model:
        key += f":{model}"
    if pcie_gen:
        key += f":pcie{pcie_gen}"
    dialect = {"nvme": "NVMe", "usb": "Portable", "sata": "SATA", "ssd": ""}[interface]
    label = " ".join(part for part in (brand.title() if brand else "", model.upper() if model else "",
                                        cap_label.upper(), dialect, form.upper() if form != "unspecified" else "", "SSD") if part)
    return key, label, {
        "interface": interface,
        "form_factor": form,
        "capacity_gb": int(capacity),
        "brand": brand,
        "model": model,
        "pcie_gen": pcie_gen,
    }


def _hdd(text: str) -> tuple[str, str, dict[str, Any]] | None:
    if not re.search(r"\b(hdd|hard\s+drive|hard\s+disk|mechanical\s+drive)\b", text):
        return None
    if re.search(r"\b(enclosure|caddy|case\s+only|no\s+drive)\b", text) and not re.search(
        r"\b(with|includes?|inc\.?)\s+(?:a\s+)?(?:\d+\s*(?:gb|tb)|drive)\b", text
    ):
        return None
    capacity: float | None = None
    tb = CAPACITY_TB_RE.search(text)
    if tb:
        capacity = float(tb.group(1)) * 1024
    else:
        for match in SIZE_GB_RE.finditer(text):
            value = int(match.group(1))
            if value in (500, 1000, 2000, 3000, 4000, 6000, 8000, 12000):
                capacity = value
                break
    if not capacity:
        return None
    external = bool(re.search(r"\b(external|portable|usb)\b", text))
    cap_label = f"{round(capacity / 1000):g}TB" if capacity >= 1000 and capacity % 1000 == 0 else (
        f"{capacity / 1024:g}TB" if capacity >= 1000 else f"{int(capacity)}GB"
    )
    key = f"hdd:{'external' if external else 'internal'}:{cap_label.lower()}"
    name = f"{cap_label} " + ("Portable " if external else "Internal ") + "HDD"
    return key, name, {"interface": "usb" if external else "sata", "capacity_gb": int(capacity)}


def _to_gb(value: str, unit: str) -> int:
    return int(value) * 1024 if unit.lower() == "tb" else int(value)


def _steam_internal_capacity(text: str) -> int | None:
    """Steam Deck capacity, ignoring SD-card storage mentioned in the title."""
    for match in re.finditer(r"\b(\d{1,4})\s*(gb|tb)\b", text, re.I):
        value = _to_gb(match.group(1), match.group(2))
        if not 8 <= value <= 4096:
            continue
        neighbourhood = text[max(0, match.start() - 20) : match.end() + 20]
        if re.search(r"\b(?:micro\s*sd|microsd|sd\s*card|memory\s*card)\b", neighbourhood, re.I):
            continue
        return value
    return None


def _steam_deck(text: str) -> tuple[str, str, dict[str, Any]] | None:
    """Valve Steam Deck needs its own identity: LCD/OLED and storage are price-critical."""
    if not re.search(r"\bsteam\s+deck\b", text, re.I):
        return None
    if re.search(r"\b(?:non[- ]?oled|not\s+oled)\b", text, re.I):
        display = "lcd"
    elif re.search(r"\boled\b", text, re.I):
        display = "oled"
    elif re.search(r"\blcd\b", text, re.I):
        display = "lcd"
    else:
        display = "unknown"

    upgrade_from_to = re.search(
        r"\b(\d{1,4})\s*(gb|tb)\s*(?:ssd|nvme|storage)?\s*(?:upgraded?|upgrade)\s*"
        r"(?:to|with)\s*(\d{1,4})\s*(gb|tb)\b",
        text,
        re.I,
    )
    upgrade_to = re.search(
        r"\b(?:upgraded?|upgrade)\s*(?:to|with)?\s*(?:a\s*)?(\d{1,4})\s*(gb|tb)\b",
        text,
        re.I,
    )
    factory_storage: int | None = None
    storage: int | None = None
    origin: str = "unstated"
    if upgrade_from_to:
        factory_storage = _to_gb(upgrade_from_to.group(1), upgrade_from_to.group(2))
        storage = _to_gb(upgrade_from_to.group(3), upgrade_from_to.group(4))
        origin = "upgraded"
    elif upgrade_to:
        storage = _to_gb(upgrade_to.group(1), upgrade_to.group(2))
        origin = "upgraded"
    else:
        storage = _steam_internal_capacity(text)

    if display == "unknown" and storage in {64, 256}:
        # Valve never sold a 64GB or 256GB OLED; the capacity proves LCD.
        display = "lcd"
    if storage and origin != "upgraded":
        if display == "lcd" and storage >= 1024:
            origin = "upgraded"
        elif display == "lcd" and storage in {64, 256, 512}:
            origin = "factory"
        elif display == "oled" and storage in {512, 1024}:
            origin = "factory"

    base = "console:steam-deck"
    if display == "oled":
        base += "-oled"
    elif display == "lcd":
        base += "-lcd"
    key = base
    if storage:
        label = f"{storage // 1024}tb" if storage >= 1024 and storage % 1024 == 0 else f"{storage}gb"
        key += f":{label}"
    if origin == "upgraded" and storage:
        key += ":upgraded"

    display_label = {"oled": "OLED", "lcd": "LCD"}.get(display, "")
    cap_label = ""
    if storage:
        cap_label = f"{storage // 1024}TB" if storage >= 1024 and storage % 1024 == 0 else f"{storage}GB"
    name = "Valve Steam Deck" + (f" {display_label}" if display_label else "") + (
        f" {cap_label}" if cap_label else ""
    ) + (" (upgraded storage)" if origin == "upgraded" else "")
    attrs: dict[str, Any] = {
        "family": "steam-deck",
        "display": display,
        "storage_origin": origin,
    }
    if storage:
        attrs["storage_gb"] = storage
    if factory_storage:
        attrs["factory_storage_gb"] = factory_storage
    return key, name, attrs


def _console(text: str) -> tuple[str, str, dict[str, Any]] | None:
    """Match consoles without swallowing games or compatibility mentions."""
    # A listing that starts with a component ("RAM 64GB ... (PS5 Pro
    # exchange)") only names the console in passing. Component-first titles
    # are never the console itself.
    if re.match(r"^(?:ram|memory|ddr[45]|ssd|nvme|hdd|hard\s+drive)\b", text, re.I):
        return None
    steam = _steam_deck(text)
    if steam:
        return steam
    if (
        CONSOLE_GAME_RE.search(text)
        and not CONSOLE_CUE_RE.search(text)
        and not CONSOLE_HARDWARE_RE.search(text)
    ):
        return None
    if GAME_TITLE_RE.search(text) and not CONSOLE_HARDWARE_STRONG_RE.search(text):
        # "Naruto PS5" and "Xbox Series X Pragmata" name a game, not hardware.
        return None
    for key, name, pattern in CONSOLE_PATTERNS:
        if not pattern.search(text):
            continue
        # Generic patterns need either a console cue (console, disc, bundle,
        # storage, controllers) or a short listing title. "Spider-Man 2 PS5" is
        # a game; "Sony PS5" is a console.
        if key in GENERIC_CONSOLE_KEYS:
            if not CONSOLE_CUE_RE.search(text) and not CONSOLE_HARDWARE_RE.search(text):
                # "Xbox Series X" is a model name, not extra qualifiers.
                platform_words = set(tokenise(name)) | {
                    "sony", "microsoft", "nintendo", "the", "series"
                }
                allowed_extra = {
                    "console", "boxed", "tested", "working", "complete",
                    "condition", "good", "used", "new", "mint", "edition",
                    "disc", "digital", "slim", "pro", "oled", "lite",
                    "v2", "handheld", "charger", "with", "and", "1tb", "2tb",
                    "500gb", "825gb", "512gb", "256gb", "128gb", "64gb",
                    "white", "black", "grey", "gray", "silver", "gold",
                    "red", "blue", "green", "purple", "pink", "carbon",
                }
                # Bare platform names such as "Nintendo DS" need a tight title.
                # Any unexplained word ("Singstar", "Skyrim", "MW2") is a game
                # title or an accessory, never hardware evidence of its own.
                extra = [t for t in tokenise(text) if t not in platform_words]
                # A named console part ("PS5 Blu-ray drive") must still resolve
                # to the console so it can be flagged as an accessory; the
                # guard below only rejects unexplained game titles.
                console_part_cue = bool(re.search(
                    r"\b(disc\s+drive|optical\s+drive|blu[-\s]?ray\s+drive|"
                    r"hard\s+drive|hdd|power\s+brick|av\s+cable|faceplates?|"
                    r"shell|bezel|hdmi\s+port|heatsink|memory\s+card|swivel\s+base)\b",
                    text,
                    re.I,
                ))
                if not console_part_cue and (
                    len(extra) > 2 or not set(extra) <= allowed_extra
                ):
                    continue
        capacity = None
        cap_match = re.search(r"\b(64|128|256|512|825|1000)\s*gb\b", text, re.I)
        if cap_match:
            capacity = int(cap_match.group(1))
        else:
            tb_match = re.search(r"\b(1|2)\s*tb\b", text, re.I)
            if tb_match:
                capacity = int(tb_match.group(1)) * 1024
        display = "oled" if re.search(r"\boled\b", text, re.I) else (
            "lcd" if key.startswith("console:switch") and not re.search(
                r"\boled\b", text, re.I
            ) else None
        )
        edition = None
        if key.startswith("console:ps5"):
            if "digital" in key or re.search(r"\b(?:digital|disc[- ]?less)\b", text, re.I):
                edition = "digital"
            elif re.search(r"\b(?:disc|blu[- ]?ray)\s*(?:edition|console|drive)?\b", text, re.I):
                edition = "disc"
        revision = "pro" if "-pro" in key else "slim" if "-slim" in key else None
        attrs: dict[str, Any] = {
            "platform": key.split(":", 1)[1].split("-", 1)[0],
            "model": key.split(":", 1)[1],
            "display": display,
            "edition": edition,
            "revision": revision,
        }
        if capacity:
            attrs["storage_gb"] = capacity
        return key, name, attrs
    return None


def _network_lock(text: str) -> str | None:
    if re.search(r"\b(?:unlocked|factory\s+unlocked|sim\s+free)\b", text, re.I):
        return "unlocked"
    if re.search(r"\b(?:network\s+locked|locked\s+to|carrier\s+locked)\b", text, re.I):
        return "locked"
    return None


def _phone(text: str, details: str = "") -> tuple[str, str, dict[str, Any]] | None:
    # The model and category come from the title. Storage may be stated only in
    # the description ("128GB, unlocked"), which is reliable once the exact
    # handset is already known and avoids leaving hundreds of variants unclear.
    storage_text = details if details and not _phone_storage(text) else text
    network_lock = _network_lock(text)
    match = IPHONE_RE.search(text)
    if match:
        model = match.group(1)
        variant = (match.group(2) or "").lower()
        storage = _phone_storage(storage_text)
        key = f"phone:iphone-{model}" + (f"-{variant.replace(' ', '-')}" if variant else "")
        key += f"-{storage}gb" if storage else ""
        name = f"Apple iPhone {model}" + (f" {variant.title()}" if variant else "")
        if storage:
            name += f" {storage}GB"
        return key, name, {"storage_gb": storage, "network_lock": network_lock}
    match = re.search(r"\b(samsung\s+)?galaxy\s+(s|note)\s*(\d{2})\s*(ultra|plus|\+)?\b", text, re.I)
    if match:
        family = match.group(2).lower()
        number = match.group(3)
        variant = (match.group(4) or "").lower().replace("+", "plus")
        storage = _phone_storage(storage_text)
        key = f"phone:galaxy-{family}{number}" + (f"-{variant}" if variant else "")
        key += f"-{storage}gb" if storage else ""
        name = f"Samsung Galaxy {family.upper()}{number}" + (f" {variant.title()}" if variant else "")
        if storage:
            name += f" {storage}GB"
        return key, name, {"storage_gb": storage, "network_lock": network_lock}
    match = re.search(r"\bgoogle\s+pixel\s*(\d{1,2})\s*(pro)?\b", text, re.I)
    if match:
        storage = _phone_storage(storage_text)
        key = f"phone:pixel-{match.group(1)}" + ("-pro" if match.group(2) else "")
        key += f"-{storage}gb" if storage else ""
        name = f"Google Pixel {match.group(1)}" + (" Pro" if match.group(2) else "")
        if storage:
            name += f" {storage}GB"
        return key, name, {"storage_gb": storage, "network_lock": network_lock}
    return None


def _phone_storage(text: str) -> int | None:
    """Find phone capacity, including the common bare form "iPhone 13 128".

    The bare number must follow the model name; reading any 3-digit number
    would turn screen sizes and model years into capacities.
    """
    # Older handsets are routinely resold at 8/16/32GB. Those capacities were
    # previously ignored, which left working iPhone 6/7 and iPad listings with
    # no storage variant and pushed them into the manual review queue.
    storage = _storage(text, legacy=True)
    if storage:
        return storage
    # "256G" is common phone shorthand. A bare boundary after the digits does
    # not match it because G is a word character, so accept the suffix here
    # rather than leaving a phone that states its capacity marked as unclear.
    match = re.search(
        r"\b(?:iphone|galaxy|pixel)\b[^|,;\n]{0,50}?\b(64|128|256|512|1024)\s*g\b",
        text,
        re.I,
    )
    if match:
        return int(match.group(1))
    match = re.search(
        r"\b(?:iphone|galaxy|pixel)\b[^|,;\n]{0,50}?\b(64|128|256|512|1024)\b",
        text,
        re.I,
    )
    return int(match.group(1)) if match else None


STORAGE_GB = (64, 128, 256, 512, 1024)
LEGACY_STORAGE_GB = (8, 16, 32)


def _storage(text: str, legacy: bool = False) -> int | None:
    allowed = STORAGE_GB + (LEGACY_STORAGE_GB if legacy else ())
    for match in re.finditer(r"\b(\d{1,4})\s*gb\b", text):
        value = int(match.group(1))
        if value in allowed:
            return value
    tb = CAPACITY_TB_RE.search(text)
    if tb:
        return int(float(tb.group(1)) * 1024)
    return None


def _tablet(text: str, details: str = "") -> tuple[str | None, str | None, dict[str, Any]] | None:
    if "ipad" not in text:
        return _non_apple_tablet(text, details)
    match = re.search(r"\bipad\s*(pro|air|mini)?\s*(m\d)?\b", text)
    family = (match.group(1) or "").lower() if match else ""
    chip = (match.group(2) or "").lower() if match else ""
    if not chip:
        chip_match = re.search(r"\b(m[1-4]|a1[2-6])\b", text)
        chip = chip_match.group(1) if chip_match else ""
    gen_match = re.search(
        r"\bipad\b[^0-9]{0,28}\(?\s*(\d{1,2})(?:st|nd|rd|th)?\s*(?:gen|generation)\b",
        text,
    )
    if not gen_match:
        # "iPad gen 8" and "iPad 8th gen" are the same generation.
        gen_match = re.search(
            r"\bipad\b[^0-9]{0,28}\bgen\s*(\d{1,2})\b",
            text,
        )
    if not gen_match:
        gen_match = re.search(r"\bipad\s*mini\s*(\d{1,2})\b", text)
    if not gen_match:
        # "iPad 2" is a second-generation iPad; do not confuse it with the
        # "10.9" screen size or a 128GB storage figure.
        gen_match = re.search(
            r"\bipad\s*\(?([1-9]|10)\)?(?!\d)\s*(?=\d{2,4}\s*gb|$|\s*[-,(])",
            text,
        )
    generation = gen_match.group(1) if gen_match else ""
    screen = re.search(r"\b(8\.3|10\.2|10\.9|11|12\.9|13)\s*(?:inch|in\b|\"|(?=m[1-4]\b))", text)
    screen_size = screen.group(1) if screen else ""
    storage = _storage(text, legacy=True) or _storage(details, legacy=True)
    attrs = {
        "storage_gb": storage, "generation": generation or None,
        "screen_in": float(screen_size) if screen_size else None,
    }
    # Any bare iPad spans wildly different generations. Without a chip or
    # generation, refuse to invent a comparable product key.
    if not chip and not generation:
        return None, None, attrs
    key = "tablet:ipad" + (f"-{family}" if family else "") + (
        f"-{chip}" if chip else f"-gen{generation}" if generation else ""
    )
    if screen_size:
        key += f"-{screen_size.replace('.', '')}in"
    if storage:
        key += f"-{storage}gb"
    name = "Apple iPad" + (f" {family.title()}" if family else "") + (
        f" {chip.upper()}" if chip else f" {generation} gen" if generation else ""
    ) + (f" {screen_size}\"" if screen_size else "")
    return key, name, attrs


def _non_apple_tablet(
    text: str, details: str = ""
) -> tuple[str | None, str | None, dict[str, Any]] | None:
    """Identify exact non-Apple tablet families that are actually resold.

    Generic "tablet" titles stay ungrouped. A comparable pool that mixes a
    Galaxy Tab A7 with a Tab S9 Ultra would invent value, so the model line and
    generation are part of the key. Storage is included when the title or
    description states it; otherwise the caller marks the variant unclear.
    """
    storage = _storage(text) or _storage(details)

    match = re.search(
        r"\b(?:samsung\s+)?galaxy\s+tab\s+"
        r"(s\d{1,2}|a\s?\d{1,2}|e|active\s*\d?|pro)\s*"
        r"(ultra|plus|\+|lite|fe)?\b",
        text,
        re.I,
    )
    if match:
        model = re.sub(r"\s+", "", match.group(1).lower())
        variant = (match.group(2) or "").lower().replace("+", "plus")
        key = f"tablet:galaxy-tab-{model}" + (f"-{variant}" if variant else "")
        if storage:
            key += f"-{storage}gb"
        label = f"Samsung Galaxy Tab {model.upper()}" + (f" {variant.title()}" if variant else "")
        if storage:
            label += f" {storage}GB"
        return key, label, {"storage_gb": storage, "model": model, "variant": variant or None}

    match = re.search(
        r"\b(?:lenovo\s+)?(yoga\s+tab|tab)\s*([a-z]?\d{1,2})\s*(plus|pro)?\b",
        text,
        re.I,
    )
    if match:
        family, model, variant = match.group(1).lower(), match.group(2).lower(), (match.group(3) or "").lower()
        family_slug = "yoga-tab" if "yoga" in family else "tab"
        key = f"tablet:lenovo-{family_slug}-{model}" + (f"-{variant}" if variant else "")
        if storage:
            key += f"-{storage}gb"
        label = f"Lenovo {'Yoga Tab' if family_slug == 'yoga-tab' else 'Tab'} {model.upper()}" + (
            f" {variant.title()}" if variant else ""
        )
        if storage:
            label += f" {storage}GB"
        return key, label, {"storage_gb": storage, "model": model, "variant": variant or None}

    match = re.search(
        r"\b(?:amazon\s+)?(?:fire|kindle\s+fire)\s*(hd\s*)?(\d{1,2})\b",
        text,
        re.I,
    )
    if match:
        hd = (match.group(1) or "").strip().lower()
        model = f"{hd}{match.group(2)}" if hd else match.group(2)
        key = f"tablet:fire-{model}"
        if storage:
            key += f"-{storage}gb"
        label = f"Amazon Fire {'HD ' if hd else ''}{match.group(2)}"
        if storage:
            label += f" {storage}GB"
        return key, label, {"storage_gb": storage, "model": model}

    match = re.search(
        r"\bsurface\s+(pro|go)\s*(x|\d{1,2})?\s*(plus|\+)?(?![a-z0-9])",
        text,
        re.I,
    )
    if match:
        family = match.group(1).lower()
        model = match.group(2) or ""
        variant = (match.group(3) or "").lower().replace("+", "plus")
        tail = text[match.end():]
        if model and re.match(r"\s*(?:[\"”]|inch|in\b)", tail):
            # "Surface Pro 13\"" is a screen size, not a model generation.
            return None
        if not model:
            # A bare Surface Pro spans many generations and price bands.
            return None
        key = f"tablet:surface-{family}-{model}" + (f"-{variant}" if variant else "")
        if storage:
            key += f"-{storage}gb"
        label = f"Microsoft Surface {family.title()} {model.upper()}" + (
            f" {variant.title()}" if variant else ""
        )
        if storage:
            label += f" {storage}GB"
        return key, label, {
            "storage_gb": storage, "model": model, "variant": variant or None,
        }

    match = re.search(
        r"\b(xiaomi|redmi|oneplus)\s+pad\s*(\d{1,2})?\s*(pro)?\b",
        text,
        re.I,
    )
    if match:
        brand, model, variant = match.group(1).lower(), match.group(2) or "", (match.group(3) or "").lower()
        key = f"tablet:{brand}-pad" + (f"-{model}" if model else "") + (f"-{variant}" if variant else "")
        if storage:
            key += f"-{storage}gb"
        label = f"{brand.title()} Pad" + (f" {model}" if model else "") + (f" {variant.title()}" if variant else "")
        if storage:
            label += f" {storage}GB"
        return key, label, {"storage_gb": storage, "model": model or None, "variant": variant or None}

    return None


def _monitor(text: str) -> tuple[str, str, dict[str, Any]] | None:
    if not re.search(r"\b(monitor|display|screen)\b", text):
        return None
    # A tablet or laptop title can quote a screen size ("iPad Air 11\""). The
    # device name settles the class; the screen is a specification, not a
    # monitor.
    if re.search(
        r"\b(?:ipad|iphone|macbook|galaxy\s+tab|surface\s+(?:pro|laptop)|"
        r"thinkpad|pixel\s+\d{1,2}|steam\s+deck|switch\s+oled)\b",
        text,
        re.I,
    ):
        return None
    if re.search(r"\b(laptop|phone|tablet|pc|console)\b", text) and not re.search(r"\bmonitor\b", text):
        return None
    size = SCREEN_RE.search(text)
    resolution = RES_RE.search(text)
    hz = HZ_RE.search(text)
    # Refresh rate alone does not identify a monitor: 144Hz/240Hz panels span
    # 1080p to 4K and wildly different values.
    if not (size or resolution):
        return None
    size_label = f"{size.group(1)}in" if size else ""
    res_raw = " ".join((resolution.group(1) if resolution else "").lower().split())
    res_label = {
        "uhd": "4K", "4k": "4K", "2k": "1440p", "qhd": "1440p",
        "fhd": "1080p", "full hd": "1080p",
    }.get(res_raw, res_raw)
    hz_label = f"{hz.group(1)}Hz" if hz else ""
    brand_match = MONITOR_BRAND_RE.search(text)
    brand = brand_match.group(1).lower().replace(".", "") if brand_match else None
    model = None
    model_match = MONITOR_MODEL_RE.search(text)
    if model_match:
        candidate = model_match.group(1).lower().replace(" ", "-")
        if candidate != brand and not re.match(r"^\d", candidate) and "hz" not in candidate:
            model = candidate
    key_bits = [b for b in (size_label, res_label, hz_label) if b]
    if not key_bits:
        return None
    key = "monitor:" + "-".join(b.lower() for b in key_bits)
    if brand:
        key += f":{brand}"
    if model:
        key += f":{model}"
    name = " ".join(b for b in (brand.title() if brand else "", model.upper() if model else "",
                                 size_label, res_label, hz_label) if b) + " monitor"
    return key, name, {"size_in": int(size.group(1)) if size else None,
                       "resolution": res_label or None,
                       "refresh_hz": int(hz.group(1)) if hz else None,
                       "brand": brand, "model": model}


def _network(text: str) -> tuple[str | None, str | None, dict[str, Any], float]:
    """Identify routers, mesh systems, access points and switches.

    A brand alone is not a comparable product. Model lines such as Orbi RBK752
    and Deco XE75 span very different price bands, so only group when a model
    token is present. Generic "wifi router" listings stay tracked but unvalued.
    """
    if not NETWORK_CUE_RE.search(text):
        return None, None, {}, 0.0
    if re.search(r"\b(antenna|cable|bracket|mount|power\s+supply|poe\s+injector|cover|case)\b", text):
        return None, None, {}, 0.0
    brand = next((name for name in NETWORK_BRANDS if re.search(rf"\b{re.escape(name)}\b", text)), None)
    brand_label = {
        "ubiquiti": "Ubiquiti", "unifi": "Ubiquiti UniFi", "tp-link": "TP-Link",
        "tplink": "TP-Link", "google nest": "Google Nest", "amazon eero": "Amazon eero",
    }.get(brand or "", brand.title() if brand else "")
    model_match = NETWORK_MODEL_RE.search(text)
    model = " ".join(model_match.group(1).lower().replace("_", "-").split()) if model_match else ""
    if not model:
        if "dream machine" in text:
            model = "dream-machine"
        elif "cloud gateway" in text:
            model = "cloud-gateway"
        elif re.search(r"\bunifi\b", text) and not re.search(r"\b(router|switch|access point|gateway)\b", text):
            model = "unifi"
    if not model:
        return None, None, {"brand": brand}, 0.32
    model_slug = re.sub(r"[^a-z0-9]+", "-", model).strip("-")
    key = f"network:{re.sub(r'[^a-z0-9]+', '-', (brand or 'generic')).strip('-')}:{model_slug}"
    name = " ".join(part for part in (brand_label, model.upper().replace("-", " ")) if part)
    attrs = {"brand": brand, "model": model_slug}
    return key, name, attrs, 0.72 if brand else 0.58


def _laptop(text: str, gpu: tuple | None, cpu: tuple | None) -> tuple[str | None, str | None, dict[str, Any], float]:
    attrs: dict[str, Any] = {}
    if gpu:
        attrs["gpu_key"], attrs["gpu_name"] = gpu[0], gpu[1]
    if cpu:
        attrs["cpu_key"], attrs["cpu_name"] = cpu[0], cpu[1]
    mac = MACBOOK_RE.search(text)
    if mac and "macbook" in text:
        family = (mac.group(1) or "base").lower()
        chip = (mac.group(2) or "").replace(" ", "-").lower()
        if not chip:
            chip_match = re.search(r"\bm([1-4])\s*(pro|max|ultra)?\b", text)
            chip = (f"m{chip_match.group(1)}" + (f"-{chip_match.group(2)}" if chip_match.group(2) else "")) if chip_match else ""
        # A year in the listing may belong to bundled software ("Office 2024"),
        # so only accept a year close to the MacBook wording.
        year_match = re.search(r"macbook[^|,]{0,50}\b(20(?:1[0-9]|2[0-3]))\b", text)
        year = year_match.group(1) if year_match else ""
        if not chip and not year:
            # Intel-era MacBooks span a decade of very different values.
            return None, None, attrs, 0.4
        screen = re.search(
            r"\b(12|13|14|15|16|17)\s*-?\s*(?:inch|in\b|\"|(?=m[1-4]\b)|$)", text
        )
        screen_size = int(screen.group(1)) if screen else None
        ram_match = re.search(r"\b(8|16|18|24|32|36|48|64|96|128)\s*gb\s*(?:ram|memory|unified)", text)
        if not ram_match:
            # Apple listings often say just "18GB 512GB". The smaller value is
            # unified memory; the larger is storage.
            sizes = [int(m.group(1)) for m in re.finditer(r"\b(8|16|18|24|32|36|48|64|96|128)\s*gb\b", text)]
            if sizes and len(sizes) > 1:
                ram_value = min(sizes)
            elif sizes and (not _storage(text) or sizes[0] not in (64, 128, 256, 512, 1024)):
                ram_value = sizes[0]
            else:
                ram_value = None
        else:
            ram_value = int(ram_match.group(1))
        key = f"laptop:macbook-{family}" + (f"-{chip}" if chip else f"-{year}")
        if screen_size:
            key += f"-{screen_size}in"
        family_label = "" if family == "base" else f" {family.title()}"
        name = f"Apple MacBook{family_label}" + (f" {chip.upper()}" if chip else f" ({year})")
        if screen_size:
            name += f" {screen_size}\""
        attrs["storage_gb"] = _storage(text)
        attrs["ram_gb"] = ram_value
        attrs["screen_in"] = screen_size
        attrs["year"] = year or None
        attrs["chip"] = chip or None
        return key, name, attrs, 0.88 if chip else 0.68

    model_slug = model_name = None
    for slug, label, pattern in LAPTOP_MODEL_PATTERNS:
        if pattern.search(text):
            model_slug, model_name = slug, label
            break
    gpu_slug = gpu[0].split(":", 1)[1] if gpu else None
    if model_slug:
        if gpu_slug:
            key = f"laptop:{model_slug}:{gpu_slug}"
            return key, model_name + f" {gpu[1]} laptop", attrs, 0.82
        if cpu:
            cpu_slug = cpu[0].split(":", 1)[1]
            key = f"laptop:{model_slug}:{cpu_slug}"
            return key, model_name + f" {cpu[1]} laptop", attrs, 0.64
        # Model line without a spec cannot be valued reliably across variants.
        return None, None, attrs, 0.45
    if gpu:
        key = f"laptop:gpu:{gpu_slug}"
        name = f"{gpu[1]} gaming laptop"
        return key, name, attrs, 0.65
    if cpu:
        key = f"laptop:cpu:{cpu[0].split(':', 1)[1]}"
        name = f"{cpu[1]} laptop"
        return key, name, attrs, 0.52
    # A brand alone is not a comparable product. Keep the listing for market
    # tracking, but do not invent a valuation from unrelated laptops.
    return None, None, attrs, 0.28


def _pc(text: str, gpu: tuple | None, cpu: tuple | None) -> tuple[str | None, str | None, dict[str, Any], float]:
    attrs: dict[str, Any] = {}
    if gpu:
        attrs["gpu_key"], attrs["gpu_name"] = gpu[0], gpu[1]
    if cpu:
        attrs["cpu_key"], attrs["cpu_name"] = cpu[0], cpu[1]
    if gpu:
        return f"pc:gpu:{gpu[0].split(':', 1)[1]}", f"Gaming PC with {gpu[1]}", attrs, 0.74
    if cpu:
        return f"pc:cpu:{cpu[0].split(':', 1)[1]}", f"Desktop PC with {cpu[1]}", attrs, 0.52
    return None, None, attrs, 0.28


def identify_listing(title: str, description: str = "", marketplace: str = "") -> Identification:
    title_clean = normalise_text(title)
    desc_clean = normalise_text(description or "")
    # Model and category cues come from the title only: descriptions often
    # mention compatibility ("works with PS5", "4K display") and would otherwise
    # misclassify accessories as consoles or monitors.
    #
    # The subject is the title with a compatibility qualifier removed. A title
    # is allowed to name the device an accessory fits; that mention must never
    # become the accessory's identity.
    subject_clean = normalise_text(subject_phrase(title_clean))
    subject_text = subject_clean or title_clean
    text = lower_text(subject_text)
    full_text = lower_text(f"{title_clean} {desc_clean[:600]}")
    cleaned_fault_text = FAULT_NEGATION_RE.sub(" ", full_text)

    flags: list[str] = []
    if not title_clean:
        return Identification(None, None, "other", "other", 0.0, is_wanted=True)

    wanted = bool(WANTED_RE.search(text)) and not re.search(r"\b(for\s+sale|selling|sale)\b", text)
    faulty = bool(FAULT_RE.search(cleaned_fault_text)) or bool(
        re.search(r"\bparts\b", title_clean, re.I)
        and not re.search(r"\b(with|includes?|inc\.?)\s+(spare\s+)?parts\b", title_clean, re.I)
    )
    accessory = bool(ACCESSORY_RE.search(title_clean))
    caution = bool(CAUTION_RE.search(full_text))
    parts_only = bool(PARTS_ONLY_RE.search(title_clean))
    # Phone/tablet part cues only apply to those devices. "Steam Deck LCD" and
    # "PS5 LCD" are product descriptions, not replacement phone screens.
    phone_part = bool(PHONE_PART_RE.search(title_clean)) and bool(re.search(
        r"\b(iphone|ipad|galaxy|pixel|macbook|laptop)\b",
        title_clean,
        re.I,
    ))
    if phone_part:
        parts_only = True
        accessory = True
    empty_title = bool(re.match(r"^empty\b", title_clean, re.I))
    # A named game stays a game even when the title mentions a disc/digital
    # edition or "PS5 Pro Enhanced". Only an explicit console or bundled
    # controller turns it back into console hardware (e.g. "PS5 console with
    # Spider-Man 2").
    console_game_media = bool(
        re.search(
            r"\b(?:console|ps[1-5]|playstation\s*[1-5]?|xbox|switch)\s+games?\b",
            title_clean,
            re.I,
        )
        and not CONSOLE_BUNDLE_RE.search(title_clean)
    )
    platform_game = bool(
        (GAME_TITLE_RE.search(title_clean) or console_game_media)
        and not re.search(
            r"\b(console(?!\s+games?)|boxed\s+console|complete\s+console|"
            r"with\s+(?:an?\s+)?controllers?|includes?\s+(?:an?\s+)?controllers?)\b",
            title_clean,
            re.I,
        )
        # "PS5 with 2 games" is a console bundle; "PS5 games bundle" is games.
        and not CONSOLE_BUNDLE_RE.search(title_clean)
    )
    # A platform name plus game media is a game, not the console. This catches
    # the long tail ("Crash Nitro Kart PlayStation 2 game") without relying on
    # a list of every game title ever sold.
    if (
        not platform_game
        and PLATFORM_RE.search(title_clean)
        and GAME_MEDIA_RE.search(title_clean)
        and not GAME_HARDWARE_CUE_RE.search(title_clean)
        and not CONSOLE_BUNDLE_RE.search(title_clean)
    ):
        platform_game = True
    game_bundle = bool(
        re.search(r"\b(?:ps[45]|playstation|xbox|switch)\b", title_clean, re.I)
        and re.search(r"\bgame(?:s)?\s+bundle\b|\bbundle\s+of\s+games?\b", title_clean, re.I)
    )
    if faulty:
        flags.append("faulty_or_parts")
    if parts_only:
        flags.append("parts_only")
    if accessory or empty_title:
        flags.append("accessory_or_box")
    if platform_game or game_bundle:
        flags.append("console_game")
    if caution:
        flags.append("condition_caution")
    condition_contradiction = bool(
        CONDITION_CONTRADICTION_RE.search(title_clean)
        and USED_CONTRADICTION_RE.search(desc_clean)
    ) or bool(
        re.search(r"\b(genuine|authentic|official)\b", title_clean, re.I)
        and re.search(r"\b(replica|clone|fake|copy|unofficial)\b", desc_clean, re.I)
    )
    if condition_contradiction:
        flags.append("condition_contradiction")
    if wanted:
        flags.append("wanted_ad")
    refurbished = bool(REFURBISHED_RE.search(title_clean))
    if refurbished:
        flags.append("refurbished")

    condition = detect_condition(full_text)
    if faulty and condition != "faulty":
        condition = "faulty"
    condition_factor = CONDITION_FACTORS.get(condition, 0.95)

    gpu = _gpu(text)
    cpu = _cpu(text)
    console = _console(text)
    console_accessory = bool(console) and bool(CONSOLE_ACCESSORY_RE.search(title_clean))
    title_accessory = bool(ACCESSORY_TITLE_RE.search(title_clean))
    strong_accessory = bool(ACCESSORY_STRONG_RE.search(title_clean))
    # "PS5 with controller" is a complete console; "PS5 controller" is not.
    if console and re.search(r"\b(?:with|includes?|inc\.?|plus|\+)\b.*\bcontrollers?\b", title_clean, re.I):
        console_accessory = False
        strong_accessory = False
        title_accessory = False
    # "Xbox Series X console & controller" names the console itself. A bare
    # "Xbox Series X controller" does not, and is already caught above.
    if (
        console
        and re.search(r"\bconsoles?\b", title_clean, re.I)
        and not re.search(
            r"\b(?:disc|optical|blu[-\s]?ray)\s+drive\b|\bfaceplates?\b",
            title_clean, re.I,
        )
        # "PS5 console covers" says console only to name the accessory. The
        # word "console" must not launder a cover, case, skin or stand into
        # console hardware.
        and not re.search(
            r"\bconsole\s+(?:covers?|cases?|skins?|shells?|stands?|brackets?|"
            r"bags?|sleeves?|faceplates?|docks?)\b",
            title_clean, re.I,
        )
    ):
        console_accessory = False
        strong_accessory = False
        title_accessory = False
    compatibility = bool(COMPATIBILITY_RE.search(title_clean)) or bool(
        title_accessory and re.search(r"\b(compatible|fits?)\b", title_clean, re.I)
    )
    contract = bool(CONTRACT_RE.search(title_clean))
    multi_item = bool(MULTI_ITEM_RE.search(title_clean))
    box_mention = bool(re.search(r"\bbox\b", title_clean, re.I))
    box_included = bool(re.search(
        r"\b(?:with|including|includes?|inc\.?|comes\s+with|boxed|unopened|sealed|brand\s+new|"
        r"in\s+(?:its\s+|the\s+)?(?:original\s+)?box)\b[^|;]{0,35}\b(?:box|packaging)\b",
        title_clean, re.I,
    ))
    # "Boxed" and "with box" describe a complete item. Only packaging-only
    # titles (empty box / box only / trailing "... box") are accessories.
    box_only = bool(BOX_ONLY_RE.search(title_clean)) or bool(
        box_mention and not box_included and re.search(
            r"\bbox\b\s*$"
            # "iPhone 11 Pro box space grey" names the colour after the word
            # box; it is packaging, not a complete handset.
            r"|\bbox\b[^|,;]{0,24}\b(?:space\s+gr[ae]y|black|white|blue|red|silver|"
            r"gold|purple|green|pink|graphite|titanium)\b\s*$",
            title_clean,
            re.I,
        )
    )
    if box_only:
        accessory = True
        flags.append("accessory_or_box")
    if empty_title or platform_game or game_bundle or parts_only:
        accessory = True
    if contract:
        accessory = True
        flags.append("contract_or_finance")
    if multi_item:
        accessory = True
        flags.append("multi_item")
    if compatibility and (gpu or cpu or console):
        # "RTX 4080 compatible bracket" mentions the expensive model only to
        # describe compatibility. It is not an RTX 4080.
        accessory = True
        flags.append("compatibility_accessory")
    console_part = bool(
        console
        and re.search(
            r"\b(disc\s+drive|optical\s+drive|blu[-\s]?ray\s+drive|bluray\s+drive|"
            r"hard\s+drive|hdd|power\s+brick|av\s+cable|faceplate|shell|bezel|"
            r"hdmi\s+port|heatsink|memory\s+card)\b",
            title_clean, re.I,
        )
        and not re.search(r"\b(console|with|includes?|inc\.?|disc\s+edition)\b", title_clean, re.I)
    )
    if console_part:
        accessory = True
        flags.append("accessory_or_box")
    if console_accessory or strong_accessory or (title_accessory and not gpu and not cpu):
        accessory = True
        flags.append("accessory_or_box")
    # System-shape cues are read from the title only. Descriptions often mention
    # "gaming PC" or "laptop" when selling a loose graphics card.
    title_gpu = _gpu(subject_text)
    title_cpu = _cpu(subject_text)
    cpu_family = re.search(
        r"\b(ryzen\s*[3579]|core\s*i[3579]|i[3579][\s-]?\d{0,5}|intel\s+core|"
        r"core\s+ultra\s*[3579]|ultra\s*[3579]|athlon)\b",
        title_clean,
        re.I,
    )
    explicit_laptop = bool(LAPTOP_RE.search(title_clean)) and not bool(
        re.search(r"\byoga\s+tab\b", title_clean, re.I)
    )
    monitor_ident = _monitor(text)
    tablet_ident = _tablet(text, full_text)
    # An explicitly named tablet family is the device being sold, even when the
    # title also states a processor, memory and storage ("Surface Pro 7+
    # i5-1135G7 8GB 256GB"). Those are its specifications, not loose parts.
    # Any tablet family that produced an exact key is the device being sold.
    # The key parser already refuses generic "tablet" wording, so this does
    # not reopen the door to a PC title that merely mentions an iPad.
    tablet_explicit = bool(tablet_ident and tablet_ident[0])
    # A monitor title may contain "PC" or a system brand ("Dell ... Computer
    # Monitor"). Only let a system cue override the monitor when the title also
    # carries actual system specifications such as a CPU, RAM or storage.
    monitor_system_spec = bool(
        _cpu(text) or _gpu(text) or _ram(text) or _ssd(text) or _hdd(text)
    )
    # A complete desktop or tablet is not the processor named in its own spec
    # list. This is the structural version of that rule; the component branches
    # below are skipped when the title describes assembled hardware.
    monitor_wins = (
        bool(monitor_ident)
        and not explicit_laptop
        and not tablet_explicit
        and not bool(console)
        and not bool(PC_RE.search(title_clean))
        and not (monitor_system_spec and bool(SYSTEM_BRAND_RE.search(title_clean)))
    )
    pc_cue = bool(PC_RE.search(title_clean)) or bool(SYSTEM_BRAND_RE.search(title_clean))
    if re.search(r"\btower\b", title_clean, re.I) and (
        cpu or cpu_family or _ram(text) or _gpu(text)
    ):
        pc_cue = True
    if not pc_cue and re.search(r"\baio\b", title_clean, re.I) and re.search(
        r"\b(pc|computer|windows|win\s*1[01])\b", title_clean, re.I
    ):
        pc_cue = True
    system_combo = bool(title_gpu and (title_cpu or cpu_family))
    # A model line can name a laptop, a desktop tower, a monitor or a spare
    # part. These cues decide which one it is before the model list is trusted.
    laptop_excluded = bool(
        not re.search(r"\b(?:laptop|notebook)\b", title_clean, re.I)
        and re.search(
            r"\b(?:monitor|desktop|tower|25l|30l|35l|40l|45l|"
            r"top\s+lid|bottom\s+case|palmrest|bezel|webcam|chassis|shell\s+only)\b",
            title_clean,
            re.I,
        )
    )
    # "Dell Pro Max 16 - Intel Ultra 7 - 64GB DDR5 Ram" is a mobile
    # workstation. A system brand plus a processor and a standalone screen size
    # identifies a laptop even when the word "laptop" is absent; the 64GB is
    # its installed memory, not a DIMM for sale.
    branded_laptop_spec = bool(
        re.search(r"\b(?:dell|hp|lenovo|acer|asus|msi|alienware|gigabyte)\b", title_clean, re.I)
        and not re.search(
            r"\b(?:desktop|tower|optiplex|precision\s+\d{4}\s+(?:tower|desktop)|workstation)\b",
            title_clean,
            re.I,
        )
        and (cpu or cpu_family)
        and re.search(r"\b1[2-8](?:\.\d)?(?:\s*(?:inch|in|\"|”|-inch))?\b", title_clean, re.I)
    )
    # "16GB DDR4 SODIMM laptop RAM" describes a memory module, not a laptop.
    # The word "laptop" is a form-factor attribute of the RAM, so a standalone
    # memory listing must not be swallowed by the laptop branch.
    laptop_memory_listing = bool(
        DDR_RE.search(title_clean)
        and re.search(r"\b(?:ram|memory|dimm|so-?dimm|udimm)\b", title_clean, re.I)
        and not re.search(
            r"\b(?:macbook|thinkpad|elitebook|probook|latitude|inspiron|ideapad|"
            r"legion|omen|victus|zephyrus|katana|nitro|predator|notebook\s+\d|"
            r"laptop\s+\d)\b",
            title_clean,
            re.I,
        )
    )
    is_laptop = (
        explicit_laptop
        or branded_laptop_spec
        or (bool(LAPTOP_CUE_RE.search(title_clean)) and not pc_cue)
    ) and not monitor_wins and not laptop_excluded and not laptop_memory_listing and not bool(
        re.search(r"\byoga\s+tab\b", title_clean, re.I)
    )
    system_assembly = system_assembly_evidence(subject_text)
    # "Memory stick" is also how sellers describe a RAM module. DDR, DIMM and
    # explicit RAM wording keep that listing in the memory category.
    ram_wording = bool(DDR_RE.search(title_clean)) or bool(
        re.search(r"\b(?:ram|dimm|so-?dimm)\b", title_clean, re.I)
    )
    media_card = (
        bool(MEDIA_CARD_RE.search(title_clean))
        and not ram_wording
        and not any(
            (
                explicit_laptop,
                monitor_wins,
                bool(console and CONSOLE_HARDWARE_NOUN_RE.search(subject_text)),
            )
        )
    )
    handheld_console = bool(
        console
        and console[0]
        and (
            str(console[0]).startswith("console:steam-deck")
            or console[0]
            in {
                "console:rog-ally", "console:rog-ally-x", "console:legion-go",
                "console:switch", "console:switch-2", "console:switch-oled",
                "console:switch-lite", "console:vita", "console:psp", "console:3ds",
                "console:3ds-xl", "console:new-3ds", "console:new-3ds-xl", "console:2ds",
                "console:2ds-xl", "console:ds", "console:ds-lite", "console:dsi",
                "console:dsi-xl",
            }
        )
    )
    # A GPU plus RAM is still a component bundle, not a complete computer.
    # Only infer a system when there is an actual system cue or several
    # independent system components alongside the GPU.
    system_component_cue = bool(re.search(
        r"\b(pc|computer|desktop|tower|system|build|motherboard|mobo|psu|case|chassis|"
        r"windows|win\s*1[01])\b",
        title_clean,
        re.I,
    ))
    is_pc = (
        pc_cue
        or system_assembly
        or system_combo
        or bool(title_gpu and _ram(title_clean) and system_component_cue)
        # "3070 FE | Ryzen 9 | 32GB DDR4 | ... pc" names a complete machine
        # without the usual "gaming PC" phrase. A bare "PC Monitor" has no
        # component specs and is still left to the monitor parser.
        or bool(
            re.search(r"\bpc\b(?!\s*[a-z]{0,5}\d{3,4}\b)", title_clean, re.I)
            and (gpu or cpu or _ram(text) or _ssd(text))
            # "Digital PC SN720 NVMe SSD" is a drive model, not a computer.
            and not (
                re.search(r"\b(?:nvme|ssd|hdd)\b", title_clean, re.I)
                and not re.search(
                    r"\b(?:gaming|desktop|tower|computer|windows|win\s*1[01]|ram|ddr[45])\b",
                    title_clean, re.I,
                )
            )
        )
        # A CPU sold with RAM, storage and a Windows licence is a complete
        # machine (often a business laptop or mini PC), not a loose processor.
        or bool(
            (cpu or cpu_family)
            and re.search(r"\b\d{1,3}\s*gb\s+(?:ram|ddr[45])\b", title_clean, re.I)
            and re.search(
                r"\b(?:\d{3,4}\s*gb|1\s*tb)\s+(?:nvme|ssd|hdd)\b",
                title_clean,
                re.I,
            )
            and re.search(r"\b(?:windows|win\s*1[01])\b", title_clean, re.I)
        )
    ) and not is_laptop and not monitor_wins and not handheld_console
    has_gpu_context = bool(gpu) and bool(
        re.search(r"\b(rtx|gtx|rx|radeon|geforce|nvidia|amd|arc|gpu|graphics|card)\b", text)
    )
    # A laptop mainboard names the laptop model for compatibility; it is a
    # replacement part, not a working laptop or a loose processor.
    laptop_part = bool(
        is_laptop
        and re.search(
            r"\b(?:motherboard|mainboard|logic\s+board|palmrest|keyboard\s+only)\b",
            title_clean,
            re.I,
        )
    )

    category = "other"
    kind = "other"
    product_key: str | None = None
    product_name: str | None = None
    confidence = 0.0
    attrs: dict[str, Any] = {}

    if platform_game:
        # A console platform named on a game listing must not fall through to a
        # laptop line ("Crash Nitro Kart" matched Acer Nitro) or a GPU.
        category, kind = "other", "other"
        product_key, product_name = None, None
        confidence = 0.0
    elif monitor_wins:
        category, kind = "monitor", "monitor"
        product_key, product_name, attrs = monitor_ident
        confidence = 0.64
    elif laptop_part:
        category, kind = "motherboard", "other"
        flags.append("parts_only")
        confidence = 0.0
    elif handheld_console and console:
        category, kind = "console", "console"
        product_key, product_name, attrs = console
        confidence = 0.8
    elif tablet_explicit and not explicit_laptop and not console:
        category, kind = "tablet", "tablet"
        product_key, product_name, attrs = tablet_ident  # type: ignore[misc]
        confidence = 0.0
    elif is_laptop:
        category, kind = "laptop", "laptop"
        product_key, product_name, attrs, confidence = _laptop(text, gpu, cpu)
        if "macbook" in text:
            category = "laptop"
    elif is_pc:
        category, kind = "pc", "pc"
        product_key, product_name, attrs, confidence = _pc(text, gpu, cpu)
    elif media_card:
        # A memory card, USB flash drive or other removable media is its own
        # product class. It has no supported comparable pool, so it is kept for
        # market tracking but never valued against the device it fits.
        category, kind = "other", "other"
        product_key, product_name = None, "Removable storage media"
        flags.append("storage_media")
        confidence = 0.0
    elif has_gpu_context:
        category, kind = "gpu", "gpu"
        product_key, product_name, attrs = gpu
        confidence = 0.93
    elif cpu:
        category, kind = "cpu", "cpu"
        product_key, product_name, attrs = cpu
        confidence = 0.9
    elif _ram(text):
        category, kind = "ram", "ram"
        product_key, product_name, attrs = _ram(text)
        confidence = 0.84
    elif _hdd(text):
        # Check HDD first: titles such as "external hard drive, NOT an SSD"
        # contain the word SSD but describe a mechanical drive.
        category, kind = "ssd", "ssd"
        product_key, product_name, attrs = _hdd(text)
        confidence = 0.78
    elif _ssd(text):
        category, kind = "ssd", "ssd"
        product_key, product_name, attrs = _ssd(text)
        confidence = 0.82
    elif console:
        category, kind = "console", "console"
        product_key, product_name, attrs = console
        confidence = 0.9
    elif _network(text)[0] or (NETWORK_CUE_RE.search(text) and re.search(r"\b(router|switch|access point|mesh|unifi|deco|orbi|eero)\b", text)):
        category, kind = "networking", "networking"
        product_key, product_name, attrs, confidence = _network(text)
    elif _phone(text, full_text):
        category, kind = "phone", "phone"
        product_key, product_name, attrs = _phone(text, full_text)
        confidence = 0.82
    elif _tablet(text, full_text) and _tablet(text, full_text)[0]:
        category, kind = "tablet", "tablet"
        product_key, product_name, attrs = _tablet(text, full_text)
        confidence = 0.8
    elif MOBO_CHIPSET_RE.search(text) and (
        re.search(r"\b(motherboard|mobo|mainboard|board)\b", text)
        or MOBO_BRAND_OR_MODEL_RE.search(text)
    ):
        category, kind = "motherboard", "motherboard"
        chipset = MOBO_CHIPSET_RE.search(text).group(1).lower()
        socket = "am5" if chipset.startswith(("b650", "x670", "x870", "a620")) else (
            "am4" if chipset.startswith(("b550", "x570", "x470", "b450", "b350", "a320", "x370", "a520")) else "lga"
        )
        product_key = f"mobo:{socket}:{chipset}"
        product_name = f"{chipset.upper()} motherboard"
        attrs = {"chipset": chipset, "socket": socket}
        confidence = 0.72
    elif re.search(r"\b(graphic|gpu|rtx|gtx|radeon|geforce)\b", text) and not gpu:
        category, kind = "gpu", "gpu"
        confidence = 0.2
        flags.append("model_unclear")
    elif re.search(r"\b(ram|memory|ddr[45])\b", text) and not re.search(
        r"\b(ipad|tablet|surface|galaxy\s+tab|laptop|phone|console)\b", text
    ):
        category = "ram"
        kind = "ram"
        flags.append("model_unclear")
    elif re.search(r"\b(ssd|nvme)\b", text):
        category, kind = "ssd", "ssd"
        flags.append("model_unclear")

    platform_bundle = bool(re.search(
        r"\b(?:game|games)\s+bundle\b|\bbundle\s+of\s+(?:\d+\s+)?games\b",
        text,
        re.I,
    ))
    if (
        category == "other"
        and kind == "other"
        and PLATFORM_RE.search(text)
        and (not platform_game or platform_bundle)
    ):
        # Console accessories ("Xbox One Kinect") and game bundles belong to
        # the console taxonomy even when they have no product key. They remain
        # unvaluable, which is exactly what prevents a £5 Kinect sensor or a
        # £3 game from being priced as a console.
        category, kind = "console", "console"
        confidence = 0.0
    if category == "other" and kind == "other":
        confidence = 0.0

    # Storage and memory define the price of phones, tablets and MacBooks far
    # more than the model line alone. When a listing omits them, the item is
    # still identified but cannot be an automatic recommendation: a "MacBook
    # Air M2" may be an 8/256 base model or a much more valuable 16/512.
    if kind == "phone" and not attrs.get("storage_gb"):
        flags.append("variant_unclear")
    if kind == "tablet" and product_key and not attrs.get("storage_gb"):
        flags.append("variant_unclear")
    if kind == "console" and product_key and str(product_key).startswith("console:steam-deck"):
        if str(attrs.get("display") or "unknown") not in {"lcd", "oled"} or not attrs.get(
            "storage_gb"
        ):
            flags.append("variant_unclear")
    if kind == "laptop" and product_key and product_key.startswith("laptop:macbook"):
        if not attrs.get("storage_gb") or not attrs.get("ram_gb"):
            flags.append("variant_unclear")
        # 2020 MacBook Air/Pro models exist with both Intel and M1 chips.
        if str(attrs.get("year") or "") >= "2020" and not attrs.get("chip"):
            flags.append("variant_unclear")

    # An incomplete console ("console only", "no controller or cables") is still
    # the right product, but it is not worth the same as a complete one. Flag it
    # so the valuation can discount the missing accessories instead of treating
    # the item as a full retail bundle.
    if product_key and kind in {"console", "phone", "tablet", "laptop"} and re.search(
        r"\b(?:console|unit|device)\s+only\b"
        r"|\b(?:no|without|missing|doesn'?t\s+(?:come\s+)?with|does\s+not\s+(?:come\s+)?with|"
        r"not\s+included|lacks)\b[^.|,;]{0,40}\b(?:controllers?|pads?|cables?|chargers?|"
        r"power\s+supply|dock|docks)\b",
        full_text,
        re.I,
    ):
        flags.append("contents_incomplete")

    iphone_models = set(
        re.findall(r"\biphone\s*(\d{1,2})(?:\s*(pro|plus|mini))?", text, re.I)
    )
    if "iphone" in text:
        # Listings such as "iPhone 17 pro max / 18 pro max" name more than one
        # model while only writing "iPhone" once.
        iphone_models |= set(
            re.findall(r"\b(1[0-9])\s*(?:pro\s*max|pro|plus|mini|max)\b", text)
        )
        # Compatibility/case listings often say "iPhone 14 13 12 11 ..." with
        # no repeated "iPhone". They must not be valued as one handset.
        if re.search(
            r"\biphone\b[^|]{0,80}?\b1[0-9]\b"
            r"(?:\s*(?:,|/|&|and)?\s*\b1[0-9]\b){1,}",
            text,
        ):
            iphone_models.add(("multi", ""))
    multi_model = len({model[0] if isinstance(model, tuple) else model for model in iphone_models}) > 1
    if multi_model:
        flags.append("multiple_models")
    multiple_gpus = len(MULTI_GPU_RE.findall(text)) >= 2
    if multiple_gpus:
        flags.append("multiple_gpus")
    component_bundle = bool(
        MOBO_CHIPSET_RE.search(text)
        and (gpu or cpu or _ram(text))
        and not is_pc
        and not is_laptop
    )
    if component_bundle:
        flags.append("component_bundle")

    # A complete device that happens to include a case, charger or box is not
    # an accessory. Keep the device identity when the accessory is explicitly
    # bundled, but never when the title is packaging-only, a console part, a
    # compatibility item or a multi-item lot.
    bundled_accessory_match = BUNDLED_ACCESSORY_RE.search(title_clean)
    bundled_accessory = bool(bundled_accessory_match)
    # "Pixel 8 Phone Case with ring holder" mentions "with" only after the
    # case has already been named: the accessory is the product being sold.
    # A bundled accessory must come after the joiner phrase.
    accessory_named_first = bool(
        bundled_accessory_match
        and ACCESSORY_NOUN_RE.search(title_clean[: bundled_accessory_match.start()])
    )
    # A title that sells a lot of cases, covers or accessories is an accessory
    # listing even when it mentions "with" or a device model.
    accessory_lot = bool(re.search(
        r"\b(?:cases?|covers?|protectors?|skins?)\s*(?:lot|bundle|job\s*lot|multipack|multi\s*pack)\b"
        r"|\b(?:lot|bundle|job\s*lot)\s+of\s+(?:\d+\s+)?(?:cases?|covers?|protectors?|accessories)\b"
        r"|\baccessories\s+(?:only|lot)\b",
        title_clean,
        re.I,
    ))
    # A motherboard sold with its own heatsink, I/O shield or fan is still the
    # motherboard. Those are included parts, not a different product class.
    board_with_parts = bool(
        product_key
        and str(product_key).startswith(("motherboard:", "mobo:"))
        and re.search(r"\b(?:motherboard|mainboard|mobo)\b", title_clean, re.I)
        and not parts_only
        and not box_only
        and not re.search(r"\b(?:empty|original)\s+box\b|\bbox\s+only\b", title_clean, re.I)
    )
    keep_bundled_device = bool(
        accessory
        and product_key
        and (
            (bundled_accessory and not accessory_named_first)
            # A motherboard with its own cooling parts needs no "with" phrase.
            or board_with_parts
        )
        and not accessory_lot
        and not console_part
        and not compatibility
        and not multi_item
        and not contract
        and not empty_title
        and not parts_only
    )
    # A monitor naming its own ports ("HDMI, DVI-D, VGA") is not an accessory.
    if product_key and str(product_key).startswith("monitor:"):
        port_only = bool(
            re.search(r"\b(?:monitor|display)\b", title_clean, re.I)
            and not re.search(
                r"\b(?:cable|adapter|splitter|stand|bracket|mount|remote|case|cover)\b",
                title_clean, re.I,
            )
        )
        if port_only:
            keep_bundled_device = True
            flags = [flag for flag in flags if flag != "accessory_or_box"]
    if keep_bundled_device:
        accessory = False
        flags = [flag for flag in flags if flag != "accessory_or_box"]
    if accessory:
        product_key, product_name = None, None
        confidence = 0.0
    if multi_model or multiple_gpus:
        # Two phones or two graphics cards are a bundle, not one comparable.
        product_key, product_name = None, None
        confidence = 0.0
    if wanted:
        product_key, product_name = None, None
        confidence = 0.0
    if ES_RE.search(title_clean):
        product_key, product_name = None, None
        confidence = 0.0
        flags.append("engineering_sample")
    if OEM_RE.search(title_clean):
        flags.append("oem")
        confidence = min(confidence, 0.55)

    if caution and confidence:
        confidence = max(0.1, confidence - 0.12)
        flags.append("confidence_reduced")
    if faulty and confidence:
        confidence = max(0.1, confidence - 0.25)
    if is_laptop and gpu:
        flags.append("laptop_gpu")
    if is_pc:
        flags.append("complete_system")
    if system_assembly and not is_pc and not is_laptop and not tablet_explicit:
        # Memory, storage and a chassis were stated but no assembled machine
        # identity was reached; keep the component but record that its context
        # looked like a full system.
        flags.append("system_context")
    bundle = bool(re.search(
        r"\b(bundle|job\s*lot|joblot|lot\s+of|multi[-\s]?pack|pair\s+of|full\s+setup|with\s+monitor|"
        r"aio\s+cooler)\b",
        title_clean, re.I,
    ))
    # "64GB (2x32) DDR5" is two modules, and the parsed key omits that layout,
    # so it cannot be valued as one 64GB stick. When the layout *is* in the key
    # ("2x16GB" parses to ram:...:2x16), the kit is an exact product of its own
    # and is not a bundle.
    if re.search(
        r"\b(?:[2-9]\d*\s*x\s*\d{1,3})(?!\s*gb)\b|kit\s+of\s+[2-9]|"
        r"[2-9]\s*(?:sticks?|modules?)\b",
        title_clean,
        re.I,
    ) and re.search(r"\b(?:ram|memory|ddr[345]|dimm|sodimm)\b", title_clean, re.I):
        bundle = True
    if console and CONSOLE_BUNDLE_RE.search(title_clean):
        bundle = True
    if console and (
        re.search(r"\b(?:with|plus|includes?|and|\+)\b.*\bgames?\b", title_clean, re.I)
        or re.search(r"\bgames?\s*/\s*(?:console|bundle)", title_clean, re.I)
    ):
        bundle = True
    if multi_item or multi_model or multiple_gpus or component_bundle:
        bundle = True
    if bundle:
        flags.append("bundle")

    # Final calibration. The class of the item never decides the score: what was
    # actually parsed does. A familiar product name with no variant is a family
    # guess, and an item with no identity cannot score at all.
    if not product_key:
        confidence = 0.0
    identity_conf, identity_level, identity_basis = identity_reliability(
        kind, product_key, attrs, flags, condition
    )
    if product_key and refurbished:
        # Refurbished stock is a separate market and must not share a price
        # snapshot with ordinary used listings.
        product_key = f"{product_key}:refurbished"
        if product_name:
            product_name = f"{product_name} (refurbished)"
        attrs["refurbished"] = True
    if product_key:
        confidence = min(identity_conf, 1.0)

    return Identification(
        product_key=product_key,
        product_name=product_name,
        category=category,
        kind=kind,
        confidence=clamp(confidence, 0.0, 1.0),
        condition=condition,
        condition_factor=condition_factor,
        is_faulty=faulty,
        is_wanted=wanted,
        is_bundle=bundle,
        flags=list(dict.fromkeys(flags)),
        attrs=attrs,
        identity_level=identity_level,
        identity_basis=identity_basis,
    )