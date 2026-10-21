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
    r"broken|dead|no\s+display|no\s+output|artefacts?|artifacts?|water\s+damage|"
    r"won'?t\s+(?:post|boot|power)|does\s+not\s+(?:post|boot|work)|"
    r"screen\s+(?:damage|broken|cracked)|bent\s+pins|burn(?:ed|t)\s+connector|"
    r"coil\s+whine\s+only|as\s+seen|untested|damaged|no\s+power|won'?t\s+power|"
    r"no\s+boot|liquid\s+damage)\b",
    re.I,
)
FAULT_NEGATION_RE = re.compile(
    r"\b(not\s+faulty|no\s+faults?|fully\s+working|tested\s+working|"
    r"working\s+perfectly|excellent\s+working\s+order|no\s+damage)\b",
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
    r"battery|skin|cover|protector|decal|sticker|replacement|spares?|spare\s+parts|"
    r"faceplates?|shell|bezel|hinge|fan\s+only|cooler\s+only|empty\s+box|boxes|cases?|"
    r"thumb\s*grips?|joy[-\s]?con|game\s+cartridge|splitter|\bhdmi\b|docking\s+station|"
    r"joystick|thumbstick|analog\s+stick|heatsink|\bhub\b|\b6\s*in\s*1\b|"
    r"flash\s+drive|usb\s+stick|pen\s+drive)\b",
    re.I,
)
CONSOLE_ACCESSORY_RE = re.compile(
    r"\b(controller|gamepad|headset|headphones|charging\s+dock|faceplate|"
    r"carry\s+case|travel\s+case|console\s+cover|disc\s+drive\s+only|case|"
    r"thumb\s*grips?|joy[-\s]?con|game\s+cartridge|game\s+disc|video\s+game|"
    r"game\s+only|screen\s+protector|joystick|thumbstick|analog\s+stick|heatsink|"
    r"\bhub\b|\b6\s*in\s*1\b|\bhdmi\b|splitter)\b",
    re.I,
)
ACCESSORY_STRONG_RE = re.compile(
    r"\b(backpack|rucksack|carry\s+bag|carry\s+case|travel\s+case|sleeve|screws?|"
    r"screwdriver|support\s+bracket|cable\s+bundle|water\s*block|controller|gamepad|"
    r"headset|headphones|keyboard|mouse|mousepad|monitor\s+stand|riser\s+cable|"
    r"replacement\s+\w+|spare\s+parts?|charging\s+dock|docking\s+station|boxes|cases?|"
    r"power\s+supply\s+unit|psu|disc\s+drive\s+only|faceplates?|flash\s+drive|"
    r"usb\s+stick|pen\s+drive|joystick|thumbstick|analog\s+stick|heatsink|"
    r"\bhub\b|\b6\s*in\s*1\b)\b",
    re.I,
)
CONSOLE_GAME_RE = re.compile(
    r"\b(video\s+game|game|games|us\s+version|eu\s+version|region\s+free|steelbook|"
    r"disc\s+only|cartridge|dlc|key\s+code|digital\s+code)\b",
    re.I,
)
CONSOLE_CUE_RE = re.compile(
    r"\b(console|bundle|disc|digital|slim|pro|oled|lite|v2|1tb|2tb|512gb|825gb|"
    r"joy[-\s]?cons?|controllers?|includes?)\b|\+\s*\d+\s*games",
    re.I,
)
WANTED_RE = re.compile(
    r"\b(wanted|looking\s+to\s+buy|looking\s+for|want\s+to\s+buy|buying|wtb|"
    r"will\s+pay|wanted\s+ad)\b",
    re.I,
)
CAUTION_RE = re.compile(
    r"\b(read\s+(?:the\s+)?description|sold\s+as\s+seen|no\s+returns?|"
    r"missing|no\s+(?:box|cables?|psu|power\s+supply)|cosmetic|"
    r"scratch(?:es|ed)?|dent(?:ed)?|pixel\s+defect|screen\s+burn)\b",
    re.I,
)

GPU_RE = re.compile(
    r"\b(?:nvidia\s+|geforce\s+)?(rtx|gtx)\s*([1-5]\d{3})\s*"
    r"((?:(?:ti|super)\s*){1,2})?\b",
    re.I,
)
AMD_GPU_RE = re.compile(
    r"\b(?:amd\s+|radeon\s+)?(rx)\s*(\d{3,4})\s*(xtx|xt|gre)?\b", re.I
)
ARC_GPU_RE = re.compile(r"\b(?:intel\s+)?arc\s*([ab])\s*(\d{3})\b", re.I)
RYZEN_RE = re.compile(
    r"\b(?:amd\s+)?ryzen\s*([3579])\s*(\d{4})\s*(x3d|xt|ge|g|x)?\b", re.I
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
CAPACITY_TB_RE = re.compile(r"\b(\d(?:\.\d)?)\s*tb\b", re.I)
SIZE_GB_RE = re.compile(r"\b(\d{2,4})\s*gb\b", re.I)
MOBO_CHIPSET_RE = re.compile(
    r"\b(x870e?|x670e?|b650e?|a620|b550|a520|x570|b450|z890|z790|z690|b760|"
    r"b660|h770|h610|x299|trx40)\b",
    re.I,
)
CONSOLE_PATTERNS = (
    ("console:ps5-pro", "PlayStation 5 Pro", re.compile(r"\b(?:ps5|playstation\s*5)\s*pro\b", re.I)),
    ("console:ps5-slim", "PlayStation 5 Slim", re.compile(r"\b(?:ps5|playstation\s*5)\s*slim\b", re.I)),
    ("console:ps5-digital", "PlayStation 5 Digital", re.compile(r"\b(?:ps5|playstation\s*5)\s*(?:digital|disc[- ]?less)\b", re.I)),
    ("console:ps5", "PlayStation 5", re.compile(r"\b(?:ps5|playstation\s*5)\b", re.I)),
    ("console:xbox-series-x", "Xbox Series X", re.compile(r"\bxbox\s+series\s+x\b", re.I)),
    ("console:xbox-series-s", "Xbox Series S", re.compile(r"\bxbox\s+series\s+s\b", re.I)),
    ("console:switch-2", "Nintendo Switch 2", re.compile(r"\b(?:nintendo\s+)?switch\s*2\b", re.I)),
    ("console:switch-oled", "Nintendo Switch OLED", re.compile(r"\b(?:nintendo\s+)?switch\s*oled\b", re.I)),
    ("console:switch-lite", "Nintendo Switch Lite", re.compile(r"\bswitch\s*lite\b", re.I)),
    ("console:switch", "Nintendo Switch", re.compile(r"\b(?:nintendo\s+switch|switch\s+console|switch\s+v2)\b", re.I)),
    ("console:steam-deck-oled", "Steam Deck OLED", re.compile(r"\bsteam\s+deck\s*oled\b", re.I)),
    ("console:steam-deck", "Steam Deck", re.compile(r"\bsteam\s+deck\b", re.I)),
    ("console:rog-ally-x", "ROG Ally X", re.compile(r"\brog\s+ally\s*x\b", re.I)),
    ("console:rog-ally", "ROG Ally", re.compile(r"\brog\s+ally\b", re.I)),
    ("console:legion-go", "Lenovo Legion Go", re.compile(r"\blegion\s+go\b", re.I)),
)
LAPTOP_RE = re.compile(r"\b(laptop|notebook|macbook|chromebook)\b", re.I)
# Model-line cues catch listings that omit the word "laptop" but are clearly a
# notebook (e.g. "MSI Thin GF63 RTX 4060").
LAPTOP_MODEL_PATTERNS: tuple[tuple[str, str, re.Pattern], ...] = (
    ("acer-nitro", "Acer Nitro", re.compile(r"\b(?:acer\s+)?nitro\s*(?:5|v|16|17)?\b", re.I)),
    ("acer-predator", "Acer Predator", re.compile(r"\b(?:acer\s+)?predator\s*(?:helios|triton|neo)?\b", re.I)),
    ("asus-tuf-laptop", "ASUS TUF Gaming laptop", re.compile(r"\btuf\s+(?:gaming\s+)?(?:a1[457]|f1[57])\b", re.I)),
    ("asus-rog-strix-laptop", "ASUS ROG Strix laptop", re.compile(r"\brog\s+strix\s+(?:g1[4678]|scar|18)\b", re.I)),
    ("asus-zephyrus", "ASUS ROG Zephyrus", re.compile(r"\bzephyrus\b", re.I)),
    ("msi-katana", "MSI Katana", re.compile(r"\bkatana\b", re.I)),
    ("msi-gf63", "MSI GF63", re.compile(r"\bgf63\b", re.I)),
    ("msi-thin", "MSI Thin", re.compile(r"\bmsi\s+thin\b", re.I)),
    ("lenovo-loq", "Lenovo LOQ", re.compile(r"\bloq\b", re.I)),
    ("lenovo-ideapad", "Lenovo IdeaPad", re.compile(r"\bideapad\b", re.I)),
    ("lenovo-thinkpad", "Lenovo ThinkPad", re.compile(r"\bthinkpad\b", re.I)),
    ("dell-g15", "Dell G15", re.compile(r"\bdell\s+g1[56]\b", re.I)),
    ("razer-blade", "Razer Blade", re.compile(r"\bblade\s*(?:14|15|16|17)?\b", re.I)),
    ("surface-laptop", "Microsoft Surface Laptop", re.compile(r"\bsurface\s+laptop\b", re.I)),
)
LAPTOP_CUE_RE = re.compile(
    r"\b(" + "|".join(pattern.pattern for _, _, pattern in LAPTOP_MODEL_PATTERNS) + r")",
    re.I,
)
PC_RE = re.compile(
    r"\b(gaming\s+pc|desktop\s+pc|pc\s+tower|full\s+pc|custom\s+pc|"
    r"computer\s+tower|workstation|gaming\s+rig|complete\s+pc|pc\s+setup|"
    r"desktop\s+computer|mini\s*pc|micro\s*pc|tiny\s*pc|sff\s+pc|"
    r"optiplex|thinkcentre|elitedesk|prodesk|alienware\s+aurora)\b",
    re.I,
)
SCREEN_RE = re.compile(r"\b(\d{2})(?:\.\d)?\s*(?:inch|in\b|\")", re.I)
RES_RE = re.compile(r"\b(4k|uhd|1440p|2k|qhd|1080p|fhd)\b", re.I)
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_key": self.product_key,
            "product_name": self.product_name,
            "category": self.category,
            "kind": self.kind,
            "confidence": round(self.confidence, 3),
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


def _gpu(text: str) -> tuple[str, str, dict[str, Any]] | None:
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
        full = text[match.start():match.end() + 18]
        vram_match = re.search(r"\b(\d{1,2})\s*gb\b", full)
        attrs = {"brand": prefix.upper(), "model": number, "suffix": label_suffix or None}
        if vram_match:
            attrs["vram_gb"] = int(vram_match.group(1))
        return key, name, attrs
    match = AMD_GPU_RE.search(text)
    if match:
        number = match.group(2)
        suffix_raw = (match.group(3) or "").lower()
        key = f"gpu:rx-{number}" + (f"-{suffix_raw}" if suffix_raw else "")
        name = f"AMD Radeon RX {number}" + (f" {suffix_raw.upper()}" if suffix_raw else "")
        return key, name, {"brand": "AMD", "model": number, "suffix": suffix_raw or None}
    match = ARC_GPU_RE.search(text)
    if match:
        family = match.group(1).lower()
        number = match.group(2)
        key = f"gpu:arc-{family}{number}"
        name = f"Intel Arc {family.upper()}{number}"
        return key, name, {"brand": "Intel", "model": number}
    return None


def _cpu(text: str) -> tuple[str, str, dict[str, Any]] | None:
    match = RYZEN_RE.search(text)
    if match:
        tier, number, suffix = match.group(1), match.group(2), (match.group(3) or "").lower()
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
    return None


def _ram(text: str) -> tuple[str, str, dict[str, Any]] | None:
    ddr = DDR_RE.search(text)
    if not ddr:
        return None
    if not re.search(r"\b(ram|memory|dimm|kit)\b", text):
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
    speed_match = re.search(r"\b(\d{4})\s*mhz\b", text)
    speed = int(speed_match.group(1)) if speed_match else None
    key = f"ram:ddr{gen}:{capacity}gb"
    name = f"{capacity}GB DDR{gen}" + (f" {speed}MHz" if speed else "") + " RAM"
    return key, name, {"ddr": gen, "capacity_gb": capacity, "speed_mhz": speed}


def _ssd(text: str) -> tuple[str, str, dict[str, Any]] | None:
    if not re.search(r"\b(ssd|nvme|solid\s+state|m\.2)\b", text):
        return None
    if re.search(r"\b(flash\s+drive|usb\s+stick|pen\s+drive)\b", text):
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
    if re.search(r"\b(external|portable|usb)\b", text):
        interface = "usb"
    else:
        interface = "nvme" if re.search(r"\b(nvme|m\.2|pcie)\b", text) else ("sata" if "sata" in text else "ssd")
    if capacity >= 960:
        cap_label = f"{round(capacity / 1000):g}TB" if capacity % 1000 == 0 else f"{capacity / 1024:g}TB"
    else:
        cap_label = f"{int(capacity)}GB"
    key = f"ssd:{interface}:{cap_label.lower()}"
    name = f"{cap_label} " + ("Portable " if interface == "usb" else "NVMe " if interface == "nvme" else "SATA " if interface == "sata" else "") + "SSD"
    return key, name, {"interface": interface, "capacity_gb": int(capacity)}


def _console(text: str) -> tuple[str, str, dict[str, Any]] | None:
    """Match consoles without swallowing games or compatibility mentions."""
    if CONSOLE_GAME_RE.search(text) and not CONSOLE_CUE_RE.search(text):
        return None
    for key, name, pattern in CONSOLE_PATTERNS:
        if not pattern.search(text):
            continue
        # Generic patterns need either a console cue (console, disc, bundle,
        # storage, controllers) or a short listing title. "Spider-Man 2 PS5" is
        # a game; "Sony PS5" is a console.
        if key in {"console:ps5", "console:switch", "console:xbox-series-x", "console:xbox-series-s"}:
            if not CONSOLE_CUE_RE.search(text):
                tokens = [t for t in tokenise(text) if t not in {"sony", "microsoft", "nintendo", "the"}]
                limit = 4 if key.startswith("console:xbox") else 2
                if len(tokens) > limit:
                    continue
        return key, name, {}
    return None


def _phone(text: str) -> tuple[str, str, dict[str, Any]] | None:
    match = IPHONE_RE.search(text)
    if match:
        model = match.group(1)
        variant = (match.group(2) or "").lower()
        storage = _storage(text)
        key = f"phone:iphone-{model}" + (f"-{variant.replace(' ', '-')}" if variant else "")
        key += f"-{storage}gb" if storage else ""
        name = f"Apple iPhone {model}" + (f" {variant.title()}" if variant else "")
        return key, name, {"storage_gb": storage}
    match = re.search(r"\b(samsung\s+)?galaxy\s+(s|note)\s*(\d{2})\s*(ultra|plus|\+)?\b", text, re.I)
    if match:
        family = match.group(2).lower()
        number = match.group(3)
        variant = (match.group(4) or "").lower().replace("+", "plus")
        key = f"phone:galaxy-{family}{number}" + (f"-{variant}" if variant else "")
        name = f"Samsung Galaxy {family.upper()}{number}" + (f" {variant.title()}" if variant else "")
        return key, name, {"storage_gb": _storage(text)}
    match = re.search(r"\bgoogle\s+pixel\s*(\d{1,2})\s*(pro)?\b", text, re.I)
    if match:
        key = f"phone:pixel-{match.group(1)}" + ("-pro" if match.group(2) else "")
        name = f"Google Pixel {match.group(1)}" + (" Pro" if match.group(2) else "")
        return key, name, {"storage_gb": _storage(text)}
    return None


def _storage(text: str) -> int | None:
    for match in re.finditer(r"\b(\d{2,4})\s*gb\b", text):
        value = int(match.group(1))
        if value in (64, 128, 256, 512, 1024):
            return value
    tb = CAPACITY_TB_RE.search(text)
    if tb:
        return int(float(tb.group(1)) * 1024)
    return None


def _tablet(text: str) -> tuple[str | None, str | None, dict[str, Any]] | None:
    if "ipad" not in text:
        return None
    match = re.search(r"\bipad\s*(pro|air|mini)?\s*(m\d)?\b", text)
    family = (match.group(1) or "").lower() if match else ""
    chip = (match.group(2) or "").lower() if match else ""
    if not chip:
        chip_match = re.search(r"\b(m[1-4])\b", text)
        chip = chip_match.group(1) if chip_match else ""
    gen_match = re.search(
        r"\bipad\s*(?:pro|air|mini)?\s*(\d{1,2})(?:st|nd|rd|th)?\s*(?:gen|generation)\b",
        text,
    )
    if not gen_match:
        gen_match = re.search(r"\bipad\s*mini\s*(\d{1,2})\b", text)
    generation = gen_match.group(1) if gen_match else ""
    attrs = {"storage_gb": _storage(text), "generation": generation or None}
    # A bare "iPad Pro/Air/mini" spans wildly different generations. Without
    # a chip or generation, refuse to invent a comparable product key.
    if family in {"pro", "air", "mini"} and not chip and not generation:
        return None, None, attrs
    key = "tablet:ipad" + (f"-{family}" if family else "") + (
        f"-{chip}" if chip else f"-gen{generation}" if generation else ""
    )
    name = "Apple iPad" + (f" {family.title()}" if family else "") + (
        f" {chip.upper()}" if chip else f" {generation} gen" if generation else ""
    )
    return key, name, attrs


def _monitor(text: str) -> tuple[str, str, dict[str, Any]] | None:
    if not re.search(r"\b(monitor|display|screen)\b", text):
        return None
    if re.search(r"\b(laptop|phone|tablet|pc|console)\b", text) and not re.search(r"\bmonitor\b", text):
        return None
    size = SCREEN_RE.search(text)
    resolution = RES_RE.search(text)
    hz = HZ_RE.search(text)
    if not (size or resolution or hz):
        return None
    size_label = f"{size.group(1)}in" if size else ""
    res_raw = (resolution.group(1) if resolution else "").lower()
    res_label = {"uhd": "4K", "4k": "4K", "2k": "1440p", "qhd": "1440p", "fhd": "1080p"}.get(res_raw, res_raw)
    hz_label = f"{hz.group(1)}Hz" if hz else ""
    key_bits = [b for b in (size_label, res_label, hz_label) if b]
    if not key_bits:
        return None
    key = "monitor:" + "-".join(b.lower() for b in key_bits)
    name = " ".join(b for b in (size_label, res_label, hz_label) if b) + " monitor"
    return key, name, {"size_in": int(size.group(1)) if size else None,
                       "resolution": res_label or None,
                       "refresh_hz": int(hz.group(1)) if hz else None}


def _laptop(text: str, gpu: tuple | None, cpu: tuple | None) -> tuple[str | None, str | None, dict[str, Any], float]:
    attrs: dict[str, Any] = {}
    if gpu:
        attrs["gpu_key"], attrs["gpu_name"] = gpu[0], gpu[1]
    if cpu:
        attrs["cpu_key"], attrs["cpu_name"] = cpu[0], cpu[1]
    mac = MACBOOK_RE.search(text)
    if mac and "macbook" in text:
        family = (mac.group(1) or "pro").lower()
        chip = (mac.group(2) or "").replace(" ", "-").lower()
        if not chip:
            chip_match = re.search(r"\b(m[1-4])\b", text)
            chip = chip_match.group(1) if chip_match else ""
        year_match = re.search(r"\b(20(?:1[0-9]|2[0-6]))\b", text)
        year = year_match.group(1) if year_match else ""
        if not chip and not year:
            # Intel-era MacBooks span a decade of very different values.
            return None, None, attrs, 0.4
        key = f"laptop:macbook-{family}" + (f"-{chip}" if chip else f"-{year}")
        name = f"Apple MacBook {family.title()}" + (f" {chip.upper()}" if chip else f" ({year})")
        attrs["storage_gb"] = _storage(text)
        attrs["year"] = year or None
        return key, name, attrs, 0.86 if chip else 0.68

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
    text = lower_text(title_clean)
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
    if faulty:
        flags.append("faulty_or_parts")
    if accessory:
        flags.append("accessory_or_box")
    if caution:
        flags.append("condition_caution")
    if wanted:
        flags.append("wanted_ad")

    condition = detect_condition(full_text)
    if faulty and condition != "faulty":
        condition = "faulty"
    condition_factor = CONDITION_FACTORS.get(condition, 0.95)

    gpu = _gpu(text)
    cpu = _cpu(text)
    console = _console(text)
    console_accessory = bool(CONSOLE_ACCESSORY_RE.search(title_clean))
    title_accessory = bool(ACCESSORY_TITLE_RE.search(title_clean))
    strong_accessory = bool(ACCESSORY_STRONG_RE.search(title_clean))
    if console_accessory or strong_accessory or (title_accessory and not gpu and not cpu):
        accessory = True
        flags.append("accessory_or_box")
    # System-shape cues are read from the title only. Descriptions often mention
    # "gaming PC" or "laptop" when selling a loose graphics card.
    title_gpu = _gpu(title_clean)
    title_cpu = _cpu(title_clean)
    cpu_family = re.search(
        r"\b(ryzen\s*[3579]|core\s*i[3579]|i[3579][\s-]?\d{0,5}|intel\s+core|athlon)\b",
        title_clean,
        re.I,
    )
    explicit_laptop = bool(LAPTOP_RE.search(title_clean))
    pc_cue = bool(PC_RE.search(title_clean))
    system_combo = bool(title_gpu and (title_cpu or cpu_family))
    is_laptop = explicit_laptop or (bool(LAPTOP_CUE_RE.search(title_clean)) and not pc_cue)
    is_pc = (pc_cue or system_combo or bool(title_gpu and _ram(title_clean))) and not is_laptop
    has_gpu_context = bool(gpu) and bool(
        re.search(r"\b(rtx|gtx|rx|radeon|geforce|arc|gpu|graphics|card)\b", text)
    )

    category = "other"
    kind = "other"
    product_key: str | None = None
    product_name: str | None = None
    confidence = 0.0
    attrs: dict[str, Any] = {}

    if is_laptop:
        category, kind = "laptop", "laptop"
        product_key, product_name, attrs, confidence = _laptop(text, gpu, cpu)
        if "macbook" in text:
            category = "laptop"
    elif is_pc:
        category, kind = "pc", "pc"
        product_key, product_name, attrs, confidence = _pc(text, gpu, cpu)
    elif has_gpu_context:
        category, kind = "gpu", "gpu"
        product_key, product_name, attrs = gpu
        confidence = 0.93
    elif cpu and re.search(r"\b(cpu|processor|ryzen|core\s+i[3579]|intel|amd)\b", text):
        category, kind = "cpu", "cpu"
        product_key, product_name, attrs = cpu
        confidence = 0.9
    elif _ram(text):
        category, kind = "ram", "ram"
        product_key, product_name, attrs = _ram(text)
        confidence = 0.84
    elif _ssd(text):
        category, kind = "ssd", "ssd"
        product_key, product_name, attrs = _ssd(text)
        confidence = 0.82
    elif console:
        category, kind = "console", "console"
        product_key, product_name, attrs = console
        confidence = 0.9
    elif _monitor(text):
        category, kind = "monitor", "monitor"
        product_key, product_name, attrs = _monitor(text)
        confidence = 0.64
    elif _phone(text):
        category, kind = "phone", "phone"
        product_key, product_name, attrs = _phone(text)
        confidence = 0.82
    elif _tablet(text):
        category, kind = "tablet", "tablet"
        product_key, product_name, attrs = _tablet(text)
        confidence = 0.8
    elif MOBO_CHIPSET_RE.search(text) and re.search(r"\b(motherboard|mobo|mainboard|board)\b", text):
        category, kind = "motherboard", "motherboard"
        chipset = MOBO_CHIPSET_RE.search(text).group(1).lower()
        socket = "am5" if chipset.startswith(("b650", "x670", "x870", "a620")) else (
            "am4" if chipset.startswith(("b550", "x570", "b450", "a520")) else "lga"
        )
        product_key = f"mobo:{socket}:{chipset}"
        product_name = f"{chipset.upper()} motherboard"
        attrs = {"chipset": chipset, "socket": socket}
        confidence = 0.72
    elif re.search(r"\b(graphic|gpu|rtx|gtx|radeon|geforce)\b", text) and not gpu:
        category, kind = "gpu", "gpu"
        confidence = 0.2
        flags.append("model_unclear")
    elif re.search(r"\b(ram|memory|ddr[45])\b", text):
        category = "ram"
        kind = "ram"
        flags.append("model_unclear")
    elif re.search(r"\b(ssd|nvme)\b", text):
        category, kind = "ssd", "ssd"
        flags.append("model_unclear")

    if category == "other" and kind == "other":
        confidence = 0.0

    if accessory:
        product_key, product_name = None, None
        confidence = 0.0
    if wanted:
        product_key, product_name = None, None
        confidence = 0.0

    if caution and confidence:
        confidence = max(0.1, confidence - 0.12)
        flags.append("confidence_reduced")
    if faulty and confidence:
        confidence = max(0.1, confidence - 0.25)
    if is_laptop and gpu:
        flags.append("laptop_gpu")
    if is_pc:
        flags.append("complete_system")
    bundle = bool(re.search(
        r"\b(bundle|job\s*lot|joblot|lot\s+of|multi[-\s]?pack|pair\s+of|full\s+setup|with\s+monitor)\b",
        title_clean, re.I,
    ))
    if bundle:
        flags.append("bundle")

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
        flags=flags,
        attrs=attrs,
    )
