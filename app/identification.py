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
    r"battery|skin|cover|protector|decal|sticker|replacement|spares?|spare\s+parts|"
    r"faceplates?|shell|bezel|hinge|fan\s+only|cooler\s+only|empty\s+box|boxes|cases?|"
    r"nudient|casely|badge|pin\s+badge|"
    r"thumb\s*grips?|joy[-\s]?con|game\s+cartridge|splitter|\bhdmi\b|docking\s+station|"
    r"joystick|thumbstick|analog\s+stick|heatsink|\bhub\b|\b6\s*in\s*1\b|"
    r"flash\s+drive|usb\s+stick|pen\s+drive|enclosure|caddy|housing|digitizer|"
    r"screen\s+assembly|replacement\s+screen|glass\s+only|box\s+only|"
    r"(?:\d+x)?cases?|camera\s+(?:lens\s+)?protection|screen\s+protectors?|"
    r"box\s+(?:only|for)|(?:empty|original|retail|full)\s+(?:original\s+)?box|"
    r"packaging(?:\s+set)?|retail\s+packaging|otterbox|spigen|caseology|casetify|"
    r"\bmous\b|dbrand|\bwrap\b|holder|back\s+glass|rear\s+glass|"
    r"cooling\s+fan|\bfan\b|mid\s*frame|midframe|chassis|casing|lcd|oled\s+assembly|"
    r"logic\s+board|charging\s+port|sim\s+tray|camera\s+module|privacy\s+(?:screen|filter)|"
    r"tempered\s+glass|alligator|crocodile|phone\s+case|silicone\s+case|leather\s+case|"
    r"replacement\s+shell|housing\s+only|frame\s+only|lead\s+for|charging\s+cable|"
    r"accessor(?:y|ies)|stylus|\bpen\b|\bpencil\b|protect(?:or|er)|kids|\btoy\b|"
    r"screen\s+only|portfolio|osmo|winnie\s+the\s+pooh|piglet|\bpooh\b|"
    r"controller\s+camera|motion\s+sensor|psvr|ps\s+vr|camera\s+v\d|"
    r"(?:wifi|wi-fi|wireless|network|power|usb|hdmi|controller|button)\s+board|"
    r"charging\s+coil|antenna\s+assembly|(?:wifi|wi-fi|bluetooth)\s+(?:card|module|antenna)|"
    r"phonecase|case\s*20\d{2}|case\s+for|"
    r"board\s+only)\b",
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
CONSOLE_BUNDLE_RE = re.compile(
    r"\b(?:with|plus|and|\+)\s+(?:an?\s+)?(?:"
    r"\d+\s+(?:extra\s+)?games?"
    r"|(?:\d+\s+)?(?:extra|additional|spare|second|2nd)\s+controllers?"
    r"|\d+\s+controllers?"
    r"|(?:a\s+)?(?:headset|charging\s+dock|memory\s+card)\b"
    r")",
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
    r"scratch(?:es|ed)?|dent(?:ed)?|pixel\s+defect|screen\s+burn|\bdead\b)\b",
    re.I,
)
PHONE_PART_RE = re.compile(
    r"\b(motherboard|logic\s+board|screen\s+assembly|charging\s+port|sim\s+tray|"
    r"rear\s+glass|back\s+glass|frame\s+only|shell\s+only|battery\s+only|lcd)\b",
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
# Compact marketing form: "Ryzen 5600X" means tier 5, model 5600X. It must not
# be read as a "Ryzen 5" with model 600 or as no CPU at all.
RYZEN_COMPACT_RE = re.compile(
    r"\b(?:amd\s+)?ryzen\s*([3579])(\d{3})\s*(x3d|xt|ge|g|x)?\b", re.I
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
    ("console:ps5-slim", "PlayStation 5 Slim", re.compile(r"\b(?:ps5|playstation\s*5)\s*slim\b", re.I)),
    ("console:ps5-digital", "PlayStation 5 Digital", re.compile(r"\b(?:ps5|playstation\s*5)\s*(?:digital|disc[- ]?less)\b", re.I)),
    ("console:ps5", "PlayStation 5", re.compile(r"\b(?:ps5|playstation\s*5)\b", re.I)),
    ("console:ps4-pro", "PlayStation 4 Pro", re.compile(r"\b(?:ps4|playstation\s*4)\s*pro\b", re.I)),
    ("console:ps4-slim", "PlayStation 4 Slim", re.compile(r"\b(?:ps4|playstation\s*4)\s*slim\b", re.I)),
    ("console:ps4", "PlayStation 4", re.compile(r"\b(?:ps4|playstation\s*4)\b", re.I)),
    ("console:xbox-series-x", "Xbox Series X", re.compile(r"\bxbox\s+series\s+x\b", re.I)),
    ("console:xbox-series-s", "Xbox Series S", re.compile(r"\bxbox\s+series\s+s\b", re.I)),
    ("console:xbox-one-x", "Xbox One X", re.compile(r"\bxbox\s+one\s+x\b", re.I)),
    ("console:xbox-one-s", "Xbox One S", re.compile(r"\bxbox\s+one\s+s\b", re.I)),
    ("console:xbox-one", "Xbox One", re.compile(r"\bxbox\s+one\b", re.I)),
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
LAPTOP_RE = re.compile(
    r"\b(laptop|notebook|macbook|chromebook|elitebook|probook|latitude|precision|"
    r"spectre|envy|pavilion|yoga)\b",
    re.I,
)
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
    ("razer-blade", "Razer Blade", re.compile(r"\bblade\s*(?:14|15|16|17)?\b", re.I)),
    ("surface-laptop", "Microsoft Surface Laptop", re.compile(r"\bsurface\s+laptop\b", re.I)),
    ("hp-elitebook", "HP EliteBook", re.compile(r"\belitebook\b", re.I)),
    ("hp-probook", "HP ProBook", re.compile(r"\bprobook\b", re.I)),
    ("hp-spectre", "HP Spectre", re.compile(r"\b(?:hp\s+)?spectre\b", re.I)),
    ("hp-envy", "HP Envy", re.compile(r"\b(?:hp\s+)?envy\b", re.I)),
    ("dell-latitude", "Dell Latitude", re.compile(r"\blatitude\b", re.I)),
    ("dell-xps", "Dell XPS", re.compile(r"\bxps\s*(?:13|14|15|16|17)?\b", re.I)),
    ("lenovo-yoga", "Lenovo Yoga", re.compile(r"\b(?:lenovo\s+)?yoga\b", re.I)),
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
    r"pc\s+desktop|tower\s+pc|"
    r"optiplex|thinkcentre|elitedesk|prodesk|alienware\s+aurora|ideacentre|"
    r"all[-\s]?in[-\s]?one)\b",
    re.I,
)
SYSTEM_BRAND_RE = re.compile(
    r"\b(?:dell|hp|lenovo|acer|asus|msi|alienware|gigabyte)\b[^|,]{0,35}"
    r"\b(?:pc|desktop|computer|aio|all[-\s]?in[-\s]?one|workstation)\b",
    re.I,
)
SCREEN_RE = re.compile(r"\b(\d{2})(?:\.\d)?\s*-?\s*(?:inch|in\b|\")", re.I)
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
        full = text[match.start():match.end() + 24]
        vram_match = re.search(r"\b(\d{1,2})\s*gb\b", full)
        vram = int(vram_match.group(1)) if vram_match else None
        if vram:
            key += f"-{vram}gb"
        generation = number[:2] if prefix == "rtx" and number.startswith(("40", "50")) else (
            number[:2] if prefix == "gtx" and number.startswith("16") else number[:2]
        )
        attrs = {
            "brand": "NVIDIA", "family": prefix.upper(), "model": number,
            "suffix": label_suffix or None, "generation": generation, "vram_gb": vram,
        }
        return key, name, attrs
    match = AMD_GPU_RE.search(text)
    if match:
        number = match.group(2)
        suffix_raw = (match.group(3) or "").lower()
        vram_match = re.search(r"\b(\d{1,2})\s*gb\b", text[match.start():match.end() + 24])
        vram = int(vram_match.group(1)) if vram_match else None
        key = f"gpu:rx-{number}" + (f"-{suffix_raw}" if suffix_raw else "")
        if vram:
            key += f"-{vram}gb"
        name = f"AMD Radeon RX {number}" + (f" {suffix_raw.upper()}" if suffix_raw else "")
        return key, name, {
            "brand": "AMD", "model": number, "suffix": suffix_raw or None,
            "generation": number[:2], "vram_gb": vram,
        }
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
    speed_match = re.search(r"\b([2-8]\d{3})\s*mhz\b", text)
    speed = int(speed_match.group(1)) if speed_match else None
    kit_count = int(kit.group(1)) if kit else None
    kit_size = int(kit.group(2)) if kit else None
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


def _console(text: str) -> tuple[str, str, dict[str, Any]] | None:
    """Match consoles without swallowing games or compatibility mentions."""
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
        if key in {
            "console:ps5", "console:ps4", "console:switch", "console:switch-2",
            "console:xbox-series-x", "console:xbox-series-s", "console:xbox-one",
        }:
            if not CONSOLE_CUE_RE.search(text) and not CONSOLE_HARDWARE_RE.search(text):
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
        storage = _phone_storage(text)
        key = f"phone:iphone-{model}" + (f"-{variant.replace(' ', '-')}" if variant else "")
        key += f"-{storage}gb" if storage else ""
        name = f"Apple iPhone {model}" + (f" {variant.title()}" if variant else "")
        if storage:
            name += f" {storage}GB"
        return key, name, {"storage_gb": storage}
    match = re.search(r"\b(samsung\s+)?galaxy\s+(s|note)\s*(\d{2})\s*(ultra|plus|\+)?\b", text, re.I)
    if match:
        family = match.group(2).lower()
        number = match.group(3)
        variant = (match.group(4) or "").lower().replace("+", "plus")
        storage = _phone_storage(text)
        key = f"phone:galaxy-{family}{number}" + (f"-{variant}" if variant else "")
        key += f"-{storage}gb" if storage else ""
        name = f"Samsung Galaxy {family.upper()}{number}" + (f" {variant.title()}" if variant else "")
        if storage:
            name += f" {storage}GB"
        return key, name, {"storage_gb": storage}
    match = re.search(r"\bgoogle\s+pixel\s*(\d{1,2})\s*(pro)?\b", text, re.I)
    if match:
        storage = _phone_storage(text)
        key = f"phone:pixel-{match.group(1)}" + ("-pro" if match.group(2) else "")
        key += f"-{storage}gb" if storage else ""
        name = f"Google Pixel {match.group(1)}" + (" Pro" if match.group(2) else "")
        if storage:
            name += f" {storage}GB"
        return key, name, {"storage_gb": storage}
    return None


def _phone_storage(text: str) -> int | None:
    """Find phone capacity, including the common bare form "iPhone 13 128".

    The bare number must follow the model name; reading any 3-digit number
    would turn screen sizes and model years into capacities.
    """
    storage = _storage(text)
    if storage:
        return storage
    match = re.search(
        r"\b(?:iphone|galaxy|pixel)\b[^|,;\n]{0,50}?\b(64|128|256|512|1024)\b",
        text,
        re.I,
    )
    return int(match.group(1)) if match else None


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
    storage = _storage(text)
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


def _monitor(text: str) -> tuple[str, str, dict[str, Any]] | None:
    if not re.search(r"\b(monitor|display|screen)\b", text):
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
    res_raw = (resolution.group(1) if resolution else "").lower()
    res_label = {"uhd": "4K", "4k": "4K", "2k": "1440p", "qhd": "1440p", "fhd": "1080p"}.get(res_raw, res_raw)
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
    parts_only = bool(PARTS_ONLY_RE.search(title_clean))
    phone_part = bool(PHONE_PART_RE.search(title_clean)) and bool(re.search(
        r"\b(iphone|ipad|galaxy|pixel|macbook|laptop|ps5|ps4|xbox|switch|steam\s+deck)\b",
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
    platform_game = bool(
        GAME_TITLE_RE.search(title_clean)
        and not re.search(
            r"\b(console|boxed\s+console|complete\s+console|"
            r"with\s+(?:an?\s+)?controllers?|includes?\s+(?:an?\s+)?controllers?)\b",
            title_clean,
            re.I,
        )
    )
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
    # "PS5 with controller" is a complete console; "PS5 controller" is not.
    if console and re.search(r"\b(?:with|includes?|inc\.?|plus|\+)\b.*\bcontrollers?\b", title_clean, re.I):
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
        r"\b(?:with|including|includes?|inc\.?|comes\s+with|in\s+original)\s+(?:the\s+)?(?:original\s+)?box\b",
        title_clean, re.I,
    ))
    if box_mention and not box_included:
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
            r"faceplate|shell|bezel|hdmi\s+port|heatsink)\b",
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
    title_gpu = _gpu(title_clean)
    title_cpu = _cpu(title_clean)
    cpu_family = re.search(
        r"\b(ryzen\s*[3579]|core\s*i[3579]|i[3579][\s-]?\d{0,5}|intel\s+core|athlon)\b",
        title_clean,
        re.I,
    )
    explicit_laptop = bool(LAPTOP_RE.search(title_clean))
    pc_cue = bool(PC_RE.search(title_clean)) or bool(SYSTEM_BRAND_RE.search(title_clean))
    if not pc_cue and re.search(r"\baio\b", title_clean, re.I) and re.search(
        r"\b(pc|computer|windows|win\s*1[01])\b", title_clean, re.I
    ):
        pc_cue = True
    system_combo = bool(title_gpu and (title_cpu or cpu_family))
    is_laptop = explicit_laptop or (bool(LAPTOP_CUE_RE.search(title_clean)) and not pc_cue)
    # A GPU plus RAM is still a component bundle, not a complete computer.
    # Only infer a system when there is an actual system cue or several
    # independent system components alongside the GPU.
    system_component_cue = bool(re.search(
        r"\b(pc|computer|desktop|tower|system|build|motherboard|mobo|psu|case|chassis)\b",
        title_clean,
        re.I,
    ))
    is_pc = (
        pc_cue
        or system_combo
        or bool(title_gpu and _ram(title_clean) and system_component_cue)
    ) and not is_laptop
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
    elif _monitor(text):
        category, kind = "monitor", "monitor"
        product_key, product_name, attrs = _monitor(text)
        confidence = 0.64
    elif _network(text)[0] or (NETWORK_CUE_RE.search(text) and re.search(r"\b(router|switch|access point|mesh|unifi|deco|orbi|eero)\b", text)):
        category, kind = "networking", "networking"
        product_key, product_name, attrs, confidence = _network(text)
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

    # Storage and memory define the price of phones, tablets and MacBooks far
    # more than the model line alone. When a listing omits them, the item is
    # still identified but cannot be an automatic recommendation: a "MacBook
    # Air M2" may be an 8/256 base model or a much more valuable 16/512.
    if kind == "phone" and not attrs.get("storage_gb"):
        flags.append("variant_unclear")
    if kind == "tablet" and product_key and not attrs.get("storage_gb"):
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
    bundle = bool(re.search(
        r"\b(bundle|job\s*lot|joblot|lot\s+of|multi[-\s]?pack|pair\s+of|full\s+setup|with\s+monitor|"
        r"aio\s+cooler)\b",
        title_clean, re.I,
    ))
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
    )
