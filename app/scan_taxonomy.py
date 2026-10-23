"""Hierarchical electronics taxonomy used before any marketplace request.

The old catalogue was a flat list of 34 category pages. That is enough to scan,
but it cannot answer "which product types am I allowed to buy?" A flat list also
made it too easy for a category to disappear when a new marketplace branch was
added.

This module is deliberately data-driven:

* every node has a stable key and a parent, so the dashboard can render groups,
  subgroups and leaves without hard-coding a product list;
* every leaf carries the real marketplace branch identifiers that collect it;
* a leaf may map to several marketplace branches, and several leaves may share
  one broad branch (for example all phone brands share Vinted's phone page);
* adding a marketplace branch is one mapping entry, not a scanner rewrite.

The taxonomy includes the full Vinted UK electronics tree discovered from its
public catalogue, the Gumtree electronics branches and the relevant eBay UK
taxonomy branches. Product types FlipIntel cannot value yet are still shown and
labelled honestly rather than silently omitted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class Category:
    """A real marketplace browse branch (kept here to avoid an import cycle)."""

    marketplace: str
    category_id: str
    label: str
    path: str = ""
    parent_id: str | None = None
    coverage: str = "full"
    priority: int = 1

    @property
    def key(self) -> tuple[str, str]:
        return (self.marketplace, self.category_id)


@dataclass(frozen=True)
class MarketMapping:
    """One real marketplace browse branch."""

    marketplace: str
    category_id: str
    label: str
    path: str = ""
    priority: int = 2
    coverage: str = "full"
    broad: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.marketplace, self.category_id)


@dataclass(frozen=True)
class ScanNode:
    key: str
    label: str
    parent: str | None
    order: int
    description: str = ""
    valued: bool = True
    default_enabled: bool = False
    mappings: tuple[MarketMapping, ...] = field(default_factory=tuple)
    keywords: tuple[str, ...] = field(default_factory=tuple)
    kinds: tuple[str, ...] = field(default_factory=tuple)

    @property
    def level(self) -> int:
        return self.key.count("/")


def _v(category_id: str, label: str, path: str, *, priority: int = 2, broad: bool = False) -> MarketMapping:
    return MarketMapping("vinted", category_id, label, path, priority, broad=broad)


def _g(category_id: str, label: str, path: str, *, priority: int = 2, broad: bool = True) -> MarketMapping:
    return MarketMapping("gumtree", category_id, label, path, priority, broad=broad)


def _e(category_id: str, label: str, *, priority: int = 2) -> MarketMapping:
    return MarketMapping("ebay", category_id, label, priority=priority)


_M: dict[str, tuple[MarketMapping, ...]] = {
    # --- PC components -----------------------------------------------------
    "gpu": (_v("3602", "Graphics cards", "/catalog/3602-electronics_comp_graphicscards", priority=3), _e("27386", "Graphics/Video Cards", priority=3)),
    "cpu": (_v("3599", "Processors", "/catalog/3599-electronics_comp_cpus", priority=3), _e("164", "CPUs/Processors", priority=3)),
    "ram": (_v("3603", "Memory (RAM)", "/catalog/3603-electronics_comp_ram"), _e("170083", "Memory (RAM)")),
    "mobo": (_v("3600", "Motherboards", "/catalog/3600-electronics_comp_motherboards"), _e("1244", "Motherboards")),
    "cpu-combo": (_v("3601", "Motherboard & CPU combos", "/catalog/3601-electronics_compparts_cpucombos"), _e("131511", "Motherboard & CPU Combos")),
    "sound-card": (_v("3605", "Internal sound cards", "/catalog/3605-electronics_compparts_soundcards"), _e("44980", "Sound Cards (Internal)")),
    "capture": (_v("3606", "Video capture & TV tuner cards", "/catalog/3606-electronics_compparts_vcc"), _e("3761", "Video Capture & TV Tuner Cards")),
    "ssd": (_v("3607", "Internal SSDs", "/catalog/3607-electronics_compparts_internalstorage"), _e("182085", "Hard Drives (HDD, SSD & NAS)")),
    "hdd": (_v("3605", "Internal hard drives", "/catalog/3605-electronics_compparts_internalstorage"), _e("182085", "Hard Drives (HDD, SSD & NAS)")),
    "psu": (_v("3608", "Computer power supplies", "/catalog/3608-electronics_compparts_power"), _e("42017", "Power Supplies")),
    "case": (_v("3598", "Computer cases", "/catalog/3598-electronics_compparts_cases"), _e("175674", "Computer Cases & Accessories")),
    "cooling": (_v("3604", "Computer cooling & fans", "/catalog/3604-electronics_compparts_cooling"), _e("42000", "Fans, Heatsinks & Cooling")),
    "net-adapter": (_v("3647", "Network adaptors", "/catalog/3647-electronics_compnet_adaptors"), _e("45002", "USB Wi-Fi Adapters/Dongles")),
    "expansion": (_e("182088", "Interface/Add-On Cards"), _e("42190", "Laptop Add-On Cards")),
    "other-component": (_v("3611", "Other components & parts", "/catalog/3611-electronics_compparts_other"), _e("16145", "Other Components & Parts")),
    "repair-tools": (_v("3609", "Computer repair tools", "/catalog/3609-electronics_compparts_tools")),
    "laptop-part": (_v("3610", "Laptop replacement parts", "/catalog/3610-electronics_compparts_laptops"), _e("182089", "Laptop Replacement Parts")),
    # --- phones ------------------------------------------------------------
    "phones": (_v("3661", "Mobile phones", "/catalog/3661-electronics_phones_mobilephones", priority=3, broad=True), _e("9355", "Mobile & Smart Phones", priority=3)),
    "phone-parts": (_v("3662", "Mobile phone parts & accessories", "/catalog/3662-electronics_phones_accessories"), _e("43304", "Mobile Phone Parts")),
    "landline": (_v("3663", "Landline phones", "/catalog/3663-electronics_phones_landlines"), _e("33929", "Cordless Phones & Handsets")),
    "radio-comms": (_v("3665", "Radio communication", "/catalog/3665-electronics_phones_radio"), _e("1500", "Radio Communication Equipment")),
    "dummy-phone": (_v("3666", "Dummy mobile phones", "/catalog/3666-electronics_phones_dummy"), _e("136699", "Dummy Mobile Phones")),
    "fax": (_v("3664", "Fax machines", "/catalog/3664-electronics_phones_fax")),
    # --- computers ---------------------------------------------------------
    "windows-laptop": (_v("3580", "Laptops", "/catalog/3580-electronics_comp_laptops", priority=3), _e("177", "PC Laptops & Netbooks", priority=3)),
    "macbook": (_v("3580", "Laptops", "/catalog/3580-electronics_comp_laptops", priority=3), _e("111422", "Apple Laptops", priority=3)),
    "chromebook": (_v("3580", "Laptops", "/catalog/3580-electronics_comp_laptops", priority=2)),
    "desktop": (_v("3581", "Desktop computers", "/catalog/3581-electronics_comp_desktops", priority=3), _e("179", "PC Desktops & All-in-Ones", priority=3)),
    "apple-desktop": (_e("111418", "Apple Desktops & All-in-Ones", priority=3)),
    "server": (_e("175698", "Enterprise Networking, Servers")),
    "dock": (_v("3586", "Docking stations & USB hubs", "/catalog/3586-electronics_comp_docks"), _e("3709", "Laptop Docking Stations")),
    "laptop-bag": (_v("3624", "Laptop bags & cases", "/catalog/3624-electronics_complap_cases"), _e("31519", "Laptop Cases & Bags")),
    "laptop-charger": (_v("3626", "Laptop chargers", "/catalog/3626-electronics_complap_chargers"), _e("31510", "Laptop Power Adapters/Chargers")),
    "computer-speaker": (_v("3591", "Computer speakers", "/catalog/3591-electronics_comp_speakers"), _e("3702", "Computer Speakers")),
    "computer-acc": (_v("3623", "Other computer accessories", "/catalog/3623-electronics_compacc_other"), _e("31534", "Other Laptop & Desktop Accs")),
    # --- consoles ----------------------------------------------------------
    "console": (_v("3025", "Consoles", "/catalog/3025-electronics_vg_consoles", priority=3, broad=True), _e("139971", "Video Game Consoles", priority=3)),
    "console-acc": (_v("3024", "Console accessories", "/catalog/3024-electronics_vg_accessories"), _e("54968", "Video Game Accessories")),
    "controller": (_v("3570", "Controllers", "/catalog/3570-electronics_vg_controllers"), _e("117042", "Controllers & Attachments")),
    "gaming-headset": (_v("3571", "Gaming headsets", "/catalog/3571-electronics_vg_headsets"), _e("171821", "Headsets")),
    "vr": (_v("3576", "Virtual reality", "/catalog/3576-electronics_vg_vr"), _e("183068", "PC & Console VR Headsets")),
    "sim-racing": (_v("3575", "Simulators", "/catalog/3575-electronics_vg_simulators")),
    "games": (_v("3026", "Games", "/catalog/3026-electronics_vg_games", broad=True), _e("139973", "Video Games")),
    # --- tablets and displays ---------------------------------------------
    "tablets": (_v("3728", "Tablets", "/catalog/3728-electronics_tab_tablets", priority=2, broad=True), _e("171485", "Tablets & eBook Readers", priority=2)),
    "ereader": (_v("3729", "e-Readers", "/catalog/3729-electronics_tab_ereaders", priority=2)),
    "drawing-tablet": (_v("3730", "Digital notepads", "/catalog/3730-electronics_tab_digitalnotepads"), _v("3597", "Touch & stylus pads", "/catalog/3597-electronics_comp_touchpads"), _e("3676", "Graphics Tablets/Boards & Pens")),
    "pda": (_v("3731", "PDAs", "/catalog/3731-electronics_tab_pdas"), _e("38331", "PDAs")),
    "tablet-acc": (_v("3732", "Tablet accessories", "/catalog/3732-electronics_tab_accessories"), _e("176970", "Tablet & eBook Reader Accs")),
    "monitor": (_v("3637", "Monitors", "/catalog/3637-electronics_comp_monitors", priority=2), _e("80053", "Monitors", priority=2)),
    "monitor-acc": (_v("3639", "Monitor arms", "/catalog/3639-electronics_compmon_arms"), _e("51052", "Monitor Mounts & Stands")),
    "projector": (_v("3739", "Projectors", "/catalog/3739-electronics_tv_projectors"), _e("25321", "Projectors")),
    # --- peripherals -------------------------------------------------------
    "mouse": (_v("3588", "Mice", "/catalog/3588-electronics_comp_mice"), _e("23160", "Mice, Trackballs & Touchpads")),
    "keyboard": (_v("3587", "Keyboards & accessories", "/catalog/3587-electronics_comp_keyboardsacc"), _e("33963", "Keyboards & Keypads")),
    "mousepad": (_v("3589", "Mouse pads", "/catalog/3589-electronics_comp_mousepads"), _e("23895", "Mouse Mats & Wrist Rests")),
    "headphones": (_v("3678", "Headphones & earbuds", "/catalog/3678-electronics_audio_headphones"), _e("112529", "Headphones")),
    "microphone": (_v("3592", "Computer microphones", "/catalog/3592-electronics_comp_microphones"), _e("29946", "Microphones")),
    "audio-interface": (_e("123445", "Audio/MIDI Interfaces")),
    "webcam": (_v("3593", "Webcams", "/catalog/3593-electronics_comp_webcams"), _e("4616", "Webcams")),
    "speaker": (_v("3681", "Portable speakers", "/catalog/3681-electronics_audio_portablespeakers"), _e("111694", "Audio Docks & Mini Speakers")),
    # --- cameras -----------------------------------------------------------
    "camera": (_v("3060", "Cameras", "/catalog/3060-electronics_cam_cameras"), _e("31388", "Digital Cameras")),
    "camcorder": (_e("11724", "Camcorders")),
    "lens": (_v("3061", "Lenses", "/catalog/3061-electronics_cam_lenses"), _e("3323", "Lenses")),
    "drone": (_v("3716", "Camera drones & accessories", "/catalog/3716-electronics_cam_drones"), _e("179697", "Camera Drones")),
    "tripod": (_v("3067", "Tripods & monopods", "/catalog/3067-electronics_cam_tripods"), _e("30093", "Tripods & Monopods")),
    "flash": (_v("3062", "Flashes", "/catalog/3062-electronics_cam_flashes"), _e("48515", "Flashes")),
    "studio": (_v("3066", "Studio equipment", "/catalog/3066-electronics_cam_studio_equip"), _e("30078", "Lighting & Studio")),
    "camera-acc": (_v("3059", "Camera accessories", "/catalog/3059-electronics_cam_accessories"), _e("15200", "Camera, Drone & Photo Accessories")),
    "camera-part": (_v("3717", "Camera replacement parts", "/catalog/3717-electronics_cam_parts"), _e("182074", "Replacement Parts & Tools")),
    "film-photo": (_e("69323", "Film Photography")),
    # --- audio -------------------------------------------------------------
    "home-audio": (_v("3683", "Home audio systems", "/catalog/3683-electronics_audio_homesystems"), _e("14969", "Home Audio & HiFi Separates")),
    "amplifier": (_e("14970", "Amplifiers & Pre-Amps")),
    "turntable": (_e("48647", "Record Players/Turntables")),
    "radio": (_v("3680", "Portable radios", "/catalog/3680-electronics_audio_portableradios"), _e("96954", "Portable AM/FM Radios")),
    "mp3": (_v("3679", "Handheld music players", "/catalog/3679-electronics_audio_handheld"), _e("73839", "iPods & MP3 Players")),
    "smart-speaker": (_v("3682", "Smart speakers", "/catalog/3682-electronics_audio_smartspeakers")),
    "audio-acc": (_v("3686", "Audio device accessories", "/catalog/3686-electronics_audio_accessories"), _e("56169", "Portable Audio Accessories")),
    "audio-part": (_v("3687", "Home audio & hi-fi parts", "/catalog/3687-electronics_audio_parts"), _e("71582", "TV & Home Audio Parts")),
    # --- television --------------------------------------------------------
    "television": (_v("3738", "Televisions", "/catalog/3738-electronics_tv_televisions"), _e("11071", "TVs")),
    "streaming": (_v("3740", "Streaming devices", "/catalog/3740-electronics_tv_streaming"), _e("168058", "Internet TV & Media Streamers")),
    "bluray": (_v("3746", "Blu-ray players", "/catalog/3746-electronics_tv_bluray"), _e("175711", "DVD & Blu-ray Players")),
    "dvd": (_v("3747", "DVD players", "/catalog/3747-electronics_tv_dvd")),
    "home-cinema": (_v("3745", "Home cinema systems", "/catalog/3745-electronics_tv_homecinema"), _e("72406", "Home Cinema Systems")),
    "tv-acc": (_v("3750", "TV & home cinema accessories", "/catalog/3750-electronics_tv_accessories"), _e("14961", "TV & Home Audio Accessories")),
    "aerial": (_v("3741", "Television antennas", "/catalog/3741-electronics_tv_antennas"), _e("175835", "Aerials")),
    "tv-receiver": (_v("3744", "Television receivers", "/catalog/3744-electronics_tv_receivers"), _e("14981", "Receivers")),
    "video-other": (_v("3749", "Other video playback devices", "/catalog/3749-electronics_tv_otherplayback")),
    # --- networking --------------------------------------------------------
    "router": (_v("3642", "Routers", "/catalog/3642-electronics_compnet_routers"), _e("11183", "Wired Routers")),
    "mesh": (_v("3643", "Mesh systems", "/catalog/3643-electronics_compnet_mesh"), _e("44995", "Wireless Routers")),
    "switch": (_e("182091", "Switches & Hubs")),
    "access-point": (_e("175709", "Wireless Access Points")),
    "modem": (_v("3645", "Modems", "/catalog/3645-electronics_compnet_modems"), _e("58297", "Modems")),
    "repeater": (_v("3644", "Network repeaters", "/catalog/3644-electronics_compnet_repeaters"), _e("61816", "Boosters, Extenders & Antennas")),
    "hotspot": (_v("3646", "Mobile hotspots", "/catalog/3646-electronics_compnet_hotspots"), _e("175710", "Mobile Broadband Devices")),
    "satellite-internet": (_v("3648", "Satellite internet receivers", "/catalog/3648-electronics_compnet_satreceivers")),
    "smart-home": (_v("2995", "Other devices & accessories", "/catalog/2995-electronics_accessories", broad=True), _e("162", "Other Computers & Networking")),
    # --- storage -----------------------------------------------------------
    "external-drive": (_e("182085", "Hard Drives (HDD, SSD & NAS)")),
    "nas": (_e("182093", "Network Storage Disk Arrays")),
    "memory-card": (_v("3063", "Memory cards", "/catalog/3063-electronics_cam_memory_cards"), _e("18871", "Memory Cards")),
    "usb-flash": (_e("51071", "USB Flash Drives")),
    "optical-media": (_v("3583", "Blank media", "/catalog/3583-electronics_comp_blankmedia"), _e("80133", "Blank Media & Accessories")),
    "drive-enclosure": (_e("158817", "Drive Enclosures & Docks")),
    "storage-acc": (_v("3621", "Memory card readers", "/catalog/3621-electronics_compacc_memreaders"), _e("43448", "Memory Card Readers & Adapters")),
    # --- wearables ---------------------------------------------------------
    "smartwatch": (_v("3035", "Smartwatches", "/catalog/3035-electronics_wear_smartwatches"), _e("178893", "Smart Watches")),
    "fitness-tracker": (_v("3031", "Fitness trackers", "/catalog/3031-electronics_wear_fitness_trackers")),
    "smart-ring": (_v("3034", "Smart rings", "/catalog/3034-electronics_wear_smart_rings")),
    "smart-glasses": (_v("3033", "Smart glasses", "/catalog/3033-electronics_wear_smart_glasses"), _e("178894", "Smart Glasses")),
    "watch-band": (_v("3032", "Replacement bands", "/catalog/3032-electronics_wear_replacement_bands"), _e("182068", "Watch Straps")),
    # --- other electronics -------------------------------------------------
    "cable": (_v("3006", "Cables", "/catalog/3006-electronics_acc_cables"), _e("182094", "Computer Cables & Connectors")),
    "charger": (_v("3008", "Chargers", "/catalog/3008-electronics_acc_chargers"), _v("3005", "Adaptors", "/catalog/3005-electronics_acc_adaptors"), _e("48618", "Battery Chargers")),
    "power-bank": (_v("3792", "Power banks", "/catalog/3792-electronics_acc_powerbanks")),
    "battery": (_v("3794", "Batteries & power supplies", "/catalog/3794-electronics_acc_batteries"), _e("48446", "Multipurpose Batteries & Power")),
    "power-strip": (_v("3793", "Surge protectors & power strips", "/catalog/3793-electronics_acc_powerstrips"), _e("96907", "Surge Protectors, Power Strips")),
    "gps": (_v("3789", "GPS & satellite navigation", "/catalog/3789-electronics_acc_gps")),
    "three-d": (_v("3788", "3D printing & scanning", "/catalog/3788-electronics_acc_threed"), _e("183062", "3D Printers & Supplies")),
    "printer": (_v("3595", "Printers & accessories", "/catalog/3595-electronics_comp_printersacc"), _e("171961", "Printers, Scanners & Supplies")),
    "scanner": (_v("3596", "Scanners & accessories", "/catalog/3596-electronics_comp_scannersacc"), _e("11205", "Scanners")),
    "personal-care": (_v("3569", "Beauty & personal care electronics", "/catalog/3569-electronics_beauty", broad=True)),
    "other": (_v("3013", "Other accessories", "/catalog/3013-electronics_acc_other"), _e("162", "Other Computers & Networking")),
    "packaging": (_e("182174", "Manuals, Inserts & Box Art"), _e("182175", "Original Game Cases & Boxes")),
    "consumable": (_e("183065", "3D Printer Consumables"), _e("4201", "Film")),
}

# A single mapping is easy to write as ``(_v(...))`` by mistake; normalise it
# once so every leaf sees a tuple.
_M = {
    key: value if isinstance(value, tuple) else (value,)
    for key, value in _M.items()
}

# Gumtree exposes broad branches rather than a deep electronics tree. The
# branch is still the smallest request that can contain the selected products;
# the scanner classifies individual cards before enrichment.
_GUMTREE_PATHS = {
    "computers-software": "/for-sale/computers-software",
    "phones": "/for-sale/phones",
    "video-games-consoles": "/for-sale/video-games-consoles",
    "cameras-studio-equipment": "/for-sale/cameras-studio-equipment",
    "stereos-audio": "/for-sale/stereos-audio",
    "tv-dvd-cameras": "/for-sale/tv-dvd-cameras",
    "music-instruments": "/for-sale/music-instruments",
    "miscellaneous-goods": "/for-sale/miscellaneous-goods",
}


def _gumtree_branch(key: str) -> str | None:
    if key in {
        "peripherals/headphones", "peripherals/microphones",
        "peripherals/audio-interfaces", "peripherals/speakers",
    }:
        return "stereos-audio"
    if key.startswith(("pc-components/", "computers-laptops/", "networking-smart-home/", "storage-media/")):
        return "computers-software"
    if key.startswith(("phones-mobile/", "wearables/")):
        return "phones"
    if key.startswith(("gaming-consoles/",)):
        return "video-games-consoles"
    if key.startswith(("cameras-photography/",)):
        return "cameras-studio-equipment"
    if key.startswith(("audio-hifi/",)):
        return "stereos-audio"
    if key.startswith(("tv-home-cinema/",)) or key == "tablets-displays/projectors":
        return "tv-dvd-cameras"
    if key.startswith(("peripherals/",)):
        return "computers-software"
    if key in {
        "tablets-displays/monitors", "tablets-displays/portable-monitors",
        "tablets-displays/monitor-accessories", "tablets-displays/tablets",
        "tablets-displays/ipads", "tablets-displays/android-tablets",
        "tablets-displays/ereaders", "tablets-displays/drawing-tablets",
        "tablets-displays/pdas", "tablets-displays/tablet-accessories",
    }:
        return "computers-software"
    if key.startswith(("other-electronics/",)):
        return "miscellaneous-goods"
    return None


def _n(
    key: str,
    label: str,
    parent: str | None,
    order: int,
    *,
    description: str = "",
    valued: bool = True,
    default: bool = False,
    mappings: Iterable[MarketMapping] = (),
    keywords: tuple[str, ...] = (),
    kinds: tuple[str, ...] = (),
) -> ScanNode:
    return ScanNode(
        key=key,
        label=label,
        parent=parent,
        order=order,
        description=description,
        valued=valued,
        default_enabled=default,
        mappings=tuple(mappings),
        keywords=keywords,
        kinds=kinds,
    )


def _build_nodes() -> list[ScanNode]:
    nodes: list[ScanNode] = []

    def group(key: str, label: str, parent: str | None, order: int, description: str = "") -> None:
        nodes.append(_n(key, label, parent, order, description=description, valued=False))

    def leaf(
        key: str,
        label: str,
        parent: str,
        order: int,
        mapping_key: str | None = None,
        *,
        description: str = "",
        valued: bool = True,
        default: bool = False,
        mappings: Iterable[MarketMapping] = (),
        keywords: tuple[str, ...] = (),
        kinds: tuple[str, ...] = (),
    ) -> None:
        chosen = list(mappings) or list(_M.get(mapping_key or "", ()))
        gumtree_branch = _gumtree_branch(key)
        if gumtree_branch and not any(item.marketplace == "gumtree" for item in chosen):
            chosen.append(
                _g(
                    gumtree_branch,
                    {
                        "computers-software": "Computers & software",
                        "phones": "Phones, mobile phones & telecoms",
                        "video-games-consoles": "Video games & consoles",
                        "cameras-studio-equipment": "Cameras, camcorders & studio equipment",
                        "stereos-audio": "Audio & stereo",
                        "tv-dvd-cameras": "TV, DVD, Blu-ray & videos",
                        "music-instruments": "Musical instruments & DJ equipment",
                        "miscellaneous-goods": "Other goods",
                    }[gumtree_branch],
                    _GUMTREE_PATHS[gumtree_branch],
                    broad=True,
                )
            )
        nodes.append(
            _n(
                key,
                label,
                parent,
                order,
                description=description,
                valued=valued,
                default=default,
                mappings=chosen,
                keywords=keywords,
                kinds=kinds,
            )
        )

    # =====================================================================
    # PC components
    # =====================================================================
    group("pc-components", "PC Components", None, 10, "Individual parts and upgrades, not complete systems.")
    group("pc-components/core", "Core components", "pc-components", 10)
    leaf("pc-components/graphics-cards", "Graphics cards", "pc-components/core", 10, "gpu", kinds=("gpu",), default=True)
    leaf("pc-components/processors", "Processors", "pc-components/core", 20, "cpu", kinds=("cpu",), default=True)
    leaf("pc-components/motherboards", "Motherboards", "pc-components/core", 30, "mobo", kinds=("motherboard",), default=True)
    leaf("pc-components/memory", "Memory (RAM)", "pc-components/core", 40, "ram", kinds=("ram",), default=True)
    leaf("pc-components/cpu-motherboard-combos", "CPU & motherboard combos", "pc-components/core", 50, "cpu-combo", default=True)
    leaf("pc-components/sound-cards", "Sound cards", "pc-components/core", 60, "sound-card", default=True)
    leaf("pc-components/capture-cards", "Capture & TV tuner cards", "pc-components/core", 70, "capture", default=True)
    group("pc-components/storage", "Storage", "pc-components", 20)
    leaf("pc-components/ssds", "Internal SSDs", "pc-components/storage", 10, "ssd", kinds=("ssd",), default=True)
    leaf("pc-components/hdds", "Internal hard drives", "pc-components/storage", 20, "hdd", kinds=("ssd",), default=True)
    leaf("pc-components/other-storage", "Other internal storage", "pc-components/storage", 30, "other-component", default=True)
    group("pc-components/power-cooling", "Power, cases & cooling", "pc-components", 30)
    leaf("pc-components/power-supplies", "Power supplies", "pc-components/power-cooling", 10, "psu", default=True)
    leaf("pc-components/cases", "Computer cases", "pc-components/power-cooling", 20, "case", default=True)
    leaf("pc-components/cooling", "Cooling & fans", "pc-components/power-cooling", 30, "cooling", default=True)
    group("pc-components/expansion", "Expansion & networking", "pc-components", 40)
    leaf("pc-components/network-adapters", "Network cards & adapters", "pc-components/expansion", 10, "net-adapter", default=True)
    leaf("pc-components/expansion-cards", "Expansion cards", "pc-components/expansion", 20, "expansion", default=True)
    leaf("pc-components/other-components", "Other PC components", "pc-components/expansion", 30, "other-component", default=True)
    leaf("pc-components/repair-tools", "Repair tools", "pc-components/expansion", 40, "repair-tools", valued=False, default=True)

    # =====================================================================
    # Phones and mobile devices
    # =====================================================================
    group("phones-mobile", "Phones & Mobile Devices", None, 20, "Handsets, feature phones and mobile telecoms.")
    group("phones-mobile/smartphones", "Smartphones", "phones-mobile", 10)
    leaf("phones-mobile/iphones", "Apple iPhones", "phones-mobile/smartphones", 10, "phones", valued=True, keywords=("iphone",))
    leaf("phones-mobile/samsung", "Samsung Galaxy", "phones-mobile/smartphones", 20, "phones", valued=True, keywords=("samsung", "galaxy"))
    leaf("phones-mobile/pixel", "Google Pixel", "phones-mobile/smartphones", 30, "phones", valued=True, keywords=("pixel", "google"))
    leaf("phones-mobile/xiaomi", "Xiaomi, Redmi & Poco", "phones-mobile/smartphones", 40, "phones", valued=True, keywords=("xiaomi", "redmi", "poco"))
    leaf("phones-mobile/other-android", "Other Android phones", "phones-mobile/smartphones", 50, "phones", valued=True)
    leaf("phones-mobile/feature-phones", "Feature phones", "phones-mobile/smartphones", 60, "phones", valued=False, keywords=("feature phone", "dumbphone", "nokia 3310"))
    leaf("phones-mobile/other-phones", "Other phones", "phones-mobile/smartphones", 70, "phones", valued=False)
    group("phones-mobile/other-devices", "Other mobile devices", "phones-mobile", 20)
    leaf("phones-mobile/landlines", "Landline & cordless phones", "phones-mobile/other-devices", 10, "landline", valued=False)
    leaf("phones-mobile/radio-comms", "Radio communication", "phones-mobile/other-devices", 20, "radio-comms", valued=False)
    leaf("phones-mobile/dummy-phones", "Dummy & display phones", "phones-mobile/other-devices", 30, "dummy-phone", valued=False)
    leaf("phones-mobile/fax", "Fax & office telecom", "phones-mobile/other-devices", 40, "fax", valued=False)
    leaf("phones-mobile/parts-accessories", "Phone parts & accessories", "phones-mobile/other-devices", 50, "phone-parts", valued=False)

    # =====================================================================
    # Computers and laptops
    # =====================================================================
    group("computers-laptops", "Computers & Laptops", None, 30, "Complete portable and desktop systems.")
    group("computers-laptops/laptops", "Laptops", "computers-laptops", 10)
    leaf("computers-laptops/windows-laptops", "Windows laptops", "computers-laptops/laptops", 10, "windows-laptop", kinds=("laptop",))
    leaf("computers-laptops/macbooks", "MacBooks", "computers-laptops/laptops", 20, "macbook", kinds=("laptop",), keywords=("macbook", "mac book"))
    leaf("computers-laptops/chromebooks", "Chromebooks", "computers-laptops/laptops", 30, "chromebook", kinds=("laptop",), keywords=("chromebook",))
    leaf("computers-laptops/gaming-laptops", "Gaming laptops", "computers-laptops/laptops", 40, "windows-laptop", kinds=("laptop",), keywords=("gaming laptop", "rtx laptop"))
    leaf("computers-laptops/other-laptops", "Other laptops", "computers-laptops/laptops", 50, "windows-laptop", kinds=("laptop",))
    group("computers-laptops/desktops", "Desktop systems", "computers-laptops", 20)
    leaf("computers-laptops/mini-pcs", "Mini PCs", "computers-laptops/desktops", 10, "desktop", kinds=("pc",), keywords=("mini pc", "mini-pc", "nuc"))
    leaf("computers-laptops/office-desktops", "Office desktops", "computers-laptops/desktops", 20, "desktop", kinds=("pc",))
    leaf("computers-laptops/gaming-desktops", "Gaming desktops", "computers-laptops/desktops", 30, "desktop", kinds=("pc",), keywords=("gaming pc", "gaming desktop", "rtx"))
    leaf("computers-laptops/workstations", "Workstations", "computers-laptops/desktops", 40, "desktop", kinds=("pc",), keywords=("workstation", "xeon", "threadripper"))
    leaf("computers-laptops/servers", "Servers & enterprise", "computers-laptops/desktops", 50, "server", valued=False, keywords=("server", "rack"))
    leaf("computers-laptops/other-computers", "Other complete computers", "computers-laptops/desktops", 60, "desktop", kinds=("pc",))
    group("computers-laptops/accessories", "Computer accessories", "computers-laptops", 30)
    leaf("computers-laptops/docks-hubs", "Docking stations & hubs", "computers-laptops/accessories", 10, "dock", valued=False)
    leaf("computers-laptops/laptop-bags", "Laptop bags, cases & stands", "computers-laptops/accessories", 20, "laptop-bag", valued=False)
    leaf("computers-laptops/laptop-chargers", "Laptop chargers", "computers-laptops/accessories", 30, "laptop-charger", valued=False)
    leaf("computers-laptops/computer-speakers", "Computer speakers", "computers-laptops/accessories", 40, "computer-speaker", valued=False)
    leaf("computers-laptops/laptop-parts", "Laptop replacement parts", "computers-laptops/accessories", 50, "laptop-part", valued=False)
    leaf("computers-laptops/other-accessories", "Other computer accessories", "computers-laptops/accessories", 60, "computer-acc", valued=False)

    # =====================================================================
    # Gaming and consoles
    # =====================================================================
    group("gaming-consoles", "Gaming & Consoles", None, 40, "Console hardware, handhelds and gaming equipment.")
    group("gaming-consoles/consoles", "Consoles", "gaming-consoles", 10)
    leaf("gaming-consoles/playstation", "PlayStation", "gaming-consoles/consoles", 10, "console", kinds=("console",), keywords=("playstation", "ps5", "ps4", "ps3", "ps2", "ps1", "psp", "vita"))
    leaf("gaming-consoles/xbox", "Xbox", "gaming-consoles/consoles", 20, "console", kinds=("console",), keywords=("xbox",))
    leaf("gaming-consoles/nintendo", "Nintendo", "gaming-consoles/consoles", 30, "console", kinds=("console",), keywords=("nintendo", "switch", "wii", "gamecube", "game boy", "ds"))
    leaf("gaming-consoles/steam-deck-handhelds", "Steam Deck & handhelds", "gaming-consoles/consoles", 40, "console", kinds=("console",), keywords=("steam deck", "rog ally", "legion go", "handheld"))
    leaf("gaming-consoles/retro", "Retro consoles", "gaming-consoles/consoles", 50, "console", kinds=("console",), keywords=("retro", "n64", "snes", "mega drive", "genesis", "dreamcast"))
    leaf("gaming-consoles/other-consoles", "Other consoles", "gaming-consoles/consoles", 60, "console", kinds=("console",))
    leaf("gaming-consoles/games", "Games", "gaming-consoles/consoles", 70, "games", valued=False)
    group("gaming-consoles/gaming-gear", "Gaming gear", "gaming-consoles", 20)
    leaf("gaming-consoles/gaming-headsets", "Gaming headsets", "gaming-consoles/gaming-gear", 10, "gaming-headset", valued=False)
    leaf("gaming-consoles/vr", "VR equipment", "gaming-consoles/gaming-gear", 20, "vr", valued=False)
    leaf("gaming-consoles/sim-racing", "Sim racing & flight", "gaming-consoles/gaming-gear", 30, "sim-racing", valued=False)
    leaf("gaming-consoles/accessories", "Gaming accessories", "gaming-consoles/gaming-gear", 40, "console-acc", valued=False)

    # =====================================================================
    # Tablets and displays
    # =====================================================================
    group("tablets-displays", "Tablets & Displays", None, 50, "Tablets, e-readers, monitors and projectors.")
    group("tablets-displays/tablets", "Tablets & e-readers", "tablets-displays", 10)
    leaf("tablets-displays/ipads", "Apple iPads", "tablets-displays/tablets", 10, "tablets", kinds=("tablet",), keywords=("ipad",))
    leaf("tablets-displays/android-tablets", "Android tablets", "tablets-displays/tablets", 20, "tablets", kinds=("tablet",))
    leaf("tablets-displays/ereaders", "E-readers", "tablets-displays/tablets", 30, "ereader", valued=False)
    leaf("tablets-displays/drawing-tablets", "Drawing tablets & pen displays", "tablets-displays/tablets", 40, "drawing-tablet", valued=False)
    leaf("tablets-displays/pdas", "PDAs", "tablets-displays/tablets", 50, "pda", valued=False)
    leaf("tablets-displays/tablet-accessories", "Tablet accessories", "tablets-displays/tablets", 60, "tablet-acc", valued=False)
    group("tablets-displays/displays", "Displays", "tablets-displays", 20)
    leaf("tablets-displays/monitors", "Monitors", "tablets-displays/displays", 10, "monitor", kinds=("monitor",))
    leaf("tablets-displays/portable-monitors", "Portable monitors", "tablets-displays/displays", 20, "monitor", kinds=("monitor",), keywords=("portable monitor", "usb monitor"))
    leaf("tablets-displays/projectors", "Projectors", "tablets-displays/displays", 30, "projector", valued=False)
    leaf("tablets-displays/monitor-accessories", "Monitor accessories", "tablets-displays/displays", 40, "monitor-acc", valued=False)

    # =====================================================================
    # Peripherals
    # =====================================================================
    group("peripherals", "Peripherals & Accessories", None, 60, "Input devices, audio peripherals and small accessories.")
    group("peripherals/input", "Input & control", "peripherals", 10)
    leaf("peripherals/mice", "Mice", "peripherals/input", 10, "mouse", valued=False, default=True)
    leaf("peripherals/keyboards", "Keyboards", "peripherals/input", 20, "keyboard", valued=False, default=True)
    leaf("peripherals/mousepads", "Mousepads", "peripherals/input", 30, "mousepad", valued=False)
    leaf("peripherals/controllers", "Controllers & gamepads", "peripherals/input", 40, "controller", valued=False)
    group("peripherals/audio", "Audio peripherals", "peripherals", 20)
    leaf("peripherals/headphones", "Headphones & headsets", "peripherals/audio", 10, "headphones", valued=False)
    leaf("peripherals/microphones", "Microphones", "peripherals/audio", 20, "microphone", valued=False, default=True)
    leaf("peripherals/audio-interfaces", "Audio interfaces", "peripherals/audio", 30, "audio-interface", valued=False)
    leaf("peripherals/webcams", "Webcams", "peripherals/audio", 40, "webcam", valued=False, default=True)
    group("peripherals/other", "Other peripherals", "peripherals", 30)
    leaf("peripherals/speakers", "Speakers", "peripherals/other", 10, "speaker", valued=False)
    leaf("peripherals/capture-cards", "Capture cards", "peripherals/other", 20, "capture", valued=False)
    leaf("peripherals/hubs-docks", "USB hubs & docks", "peripherals/other", 30, "dock", valued=False)
    leaf("peripherals/other-peripherals", "Other peripherals", "peripherals/other", 40, "computer-acc", valued=False)

    # =====================================================================
    # Cameras and photography
    # =====================================================================
    group("cameras-photography", "Cameras & Photography", None, 70, "Cameras, lenses, drones and studio equipment.")
    leaf("cameras-photography/cameras", "Cameras", "cameras-photography", 10, "camera", valued=False)
    leaf("cameras-photography/camcorders", "Camcorders", "cameras-photography", 20, "camcorder", valued=False)
    leaf("cameras-photography/lenses", "Lenses", "cameras-photography", 30, "lens", valued=False)
    leaf("cameras-photography/drones", "Drones", "cameras-photography", 40, "drone", valued=False)
    leaf("cameras-photography/tripods-stabilisers", "Tripods & stabilisers", "cameras-photography", 50, "tripod", valued=False)
    leaf("cameras-photography/flashes-lighting", "Flashes & lighting", "cameras-photography", 60, "flash", valued=False)
    leaf("cameras-photography/studio", "Studio equipment", "cameras-photography", 70, "studio", valued=False)
    leaf("cameras-photography/accessories", "Camera accessories", "cameras-photography", 80, "camera-acc", valued=False)
    leaf("cameras-photography/parts", "Camera parts & repairs", "cameras-photography", 90, "camera-part", valued=False)
    leaf("cameras-photography/film", "Film photography", "cameras-photography", 100, "film-photo", valued=False)

    # =====================================================================
    # Audio
    # =====================================================================
    group("audio-hifi", "Audio & Hi-Fi", None, 80, "Home audio, personal audio and hi-fi separates.")
    leaf("audio-hifi/home-systems", "Home audio systems", "audio-hifi", 10, "home-audio", valued=False)
    leaf("audio-hifi/amplifiers", "Amplifiers & receivers", "audio-hifi", 20, "amplifier", valued=False)
    leaf("audio-hifi/speakers", "Speakers", "audio-hifi", 30, "speaker", valued=False)
    leaf("audio-hifi/turntables", "Turntables & vinyl", "audio-hifi", 40, "turntable", valued=False)
    leaf("audio-hifi/radios", "Radios", "audio-hifi", 50, "radio", valued=False)
    leaf("audio-hifi/players", "Music players", "audio-hifi", 60, "mp3", valued=False)
    leaf("audio-hifi/smart-speakers", "Smart speakers", "audio-hifi", 70, "smart-speaker", valued=False)
    leaf("audio-hifi/accessories", "Audio accessories", "audio-hifi", 80, "audio-acc", valued=False)
    leaf("audio-hifi/parts", "Audio parts", "audio-hifi", 90, "audio-part", valued=False)

    # =====================================================================
    # TV and home cinema
    # =====================================================================
    group("tv-home-cinema", "TV & Home Cinema", None, 90, "Televisions, projectors and video playback.")
    leaf("tv-home-cinema/televisions", "Televisions", "tv-home-cinema", 10, "television", valued=False)
    leaf("tv-home-cinema/streaming", "Streaming devices", "tv-home-cinema", 20, "streaming", valued=False)
    leaf("tv-home-cinema/players", "Blu-ray & DVD players", "tv-home-cinema", 30, "bluray", valued=False)
    leaf("tv-home-cinema/home-cinema", "Home cinema systems", "tv-home-cinema", 40, "home-cinema", valued=False)
    leaf("tv-home-cinema/aerials", "Aerials & satellite", "tv-home-cinema", 50, "aerial", valued=False)
    leaf("tv-home-cinema/receivers", "TV receivers & boxes", "tv-home-cinema", 60, "tv-receiver", valued=False)
    leaf("tv-home-cinema/accessories", "TV accessories", "tv-home-cinema", 70, "tv-acc", valued=False)
    leaf("tv-home-cinema/other", "Other video equipment", "tv-home-cinema", 80, "video-other", valued=False)

    # =====================================================================
    # Networking and smart home
    # =====================================================================
    group("networking-smart-home", "Networking & Smart Home", None, 100, "Home networking, enterprise networking and smart devices.")
    leaf("networking-smart-home/routers", "Routers", "networking-smart-home", 10, "router", kinds=("networking",))
    leaf("networking-smart-home/mesh", "Mesh Wi-Fi", "networking-smart-home", 20, "mesh", kinds=("networking",))
    leaf("networking-smart-home/switches", "Network switches", "networking-smart-home", 30, "switch", kinds=("networking",))
    leaf("networking-smart-home/access-points", "Access points", "networking-smart-home", 40, "access-point", kinds=("networking",))
    leaf("networking-smart-home/modems", "Modems", "networking-smart-home", 50, "modem", kinds=("networking",))
    leaf("networking-smart-home/adapters", "Network adapters", "networking-smart-home", 60, "net-adapter", kinds=("networking",))
    leaf("networking-smart-home/repeaters", "Boosters & repeaters", "networking-smart-home", 70, "repeater", kinds=("networking",))
    leaf("networking-smart-home/hotspots", "Mobile hotspots", "networking-smart-home", 80, "hotspot", kinds=("networking",))
    leaf("networking-smart-home/satellite", "Satellite internet", "networking-smart-home", 90, "satellite-internet", kinds=("networking",))
    leaf("networking-smart-home/smart-home", "Smart home devices", "networking-smart-home", 100, "smart-home", valued=False)
    leaf("networking-smart-home/enterprise", "Enterprise networking", "networking-smart-home", 110, "server", valued=False)
    leaf("networking-smart-home/other", "Other networking", "networking-smart-home", 120, "other", valued=False)

    # =====================================================================
    # Storage and media
    # =====================================================================
    group("storage-media", "Storage & Media", None, 110, "External drives, NAS, cards and removable media.")
    leaf("storage-media/external-drives", "External drives", "storage-media", 10, "external-drive", valued=False)
    leaf("storage-media/nas", "NAS & network storage", "storage-media", 20, "nas", valued=False)
    leaf("storage-media/memory-cards", "Memory cards", "storage-media", 30, "memory-card", valued=False)
    leaf("storage-media/usb-flash", "USB flash drives", "storage-media", 40, "usb-flash", valued=False)
    leaf("storage-media/optical", "Optical & blank media", "storage-media", 50, "optical-media", valued=False)
    leaf("storage-media/enclosures", "Drive enclosures & caddies", "storage-media", 60, "drive-enclosure", valued=False)
    leaf("storage-media/accessories", "Storage accessories", "storage-media", 70, "storage-acc", valued=False)

    # =====================================================================
    # Wearables
    # =====================================================================
    group("wearables", "Wearables", None, 120, "Smartwatches, trackers and smart glasses.")
    leaf("wearables/smartwatches", "Smartwatches", "wearables", 10, "smartwatch", valued=False)
    leaf("wearables/fitness-trackers", "Fitness trackers", "wearables", 20, "fitness-tracker", valued=False)
    leaf("wearables/smart-rings", "Smart rings", "wearables", 30, "smart-ring", valued=False)
    leaf("wearables/smart-glasses", "Smart glasses", "wearables", 40, "smart-glasses", valued=False)
    leaf("wearables/bands-cases", "Watch bands & cases", "wearables", 50, "watch-band", valued=False)
    leaf("wearables/other", "Other wearables", "wearables", 60, "other", valued=False)

    # =====================================================================
    # Other electronics
    # =====================================================================
    group("other-electronics", "Other Electronics", None, 130, "Accessories, power, tools and products without a dedicated group.")
    leaf("other-electronics/cables", "Cables & adapters", "other-electronics", 10, "cable", valued=False)
    leaf("other-electronics/chargers", "Chargers & power adapters", "other-electronics", 20, "charger", valued=False)
    leaf("other-electronics/power-banks", "Power banks", "other-electronics", 30, "power-bank", valued=False)
    leaf("other-electronics/batteries", "Batteries", "other-electronics", 40, "battery", valued=False)
    leaf("other-electronics/power-strips", "Power strips & surge protection", "other-electronics", 50, "power-strip", valued=False)
    leaf("other-electronics/gps", "GPS & navigation", "other-electronics", 60, "gps", valued=False)
    leaf("other-electronics/3d-printing", "3D printers & scanners", "other-electronics", 70, "three-d", valued=False)
    leaf("other-electronics/3d-consumables", "3D printing consumables", "other-electronics", 80, "consumable", valued=False)
    leaf("other-electronics/printers-scanners", "Printers & scanners", "other-electronics", 90, "printer", valued=False)
    leaf("other-electronics/personal-care", "Personal care electronics", "other-electronics", 100, "personal-care", valued=False)
    leaf("other-electronics/spare-parts", "Spare parts & repairs", "other-electronics", 110, "other-component", valued=False)
    leaf("other-electronics/packaging", "Packaging & manuals", "other-electronics", 120, "packaging", valued=False)
    leaf("other-electronics/consumables", "Consumables", "other-electronics", 130, "consumable", valued=False)
    leaf("other-electronics/other", "Other electronics", "other-electronics", 140, "other", valued=False)

    return nodes


NODES: tuple[ScanNode, ...] = tuple(_build_nodes())
NODE_BY_KEY: dict[str, ScanNode] = {node.key: node for node in NODES}
CHILDREN: dict[str | None, list[ScanNode]] = {}
for _node in NODES:
    CHILDREN.setdefault(_node.parent, []).append(_node)
for _children in CHILDREN.values():
    _children.sort(key=lambda item: (item.order, item.label))
LEAVES: tuple[ScanNode, ...] = tuple(node for node in NODES if not CHILDREN.get(node.key))
LEAF_KEYS: tuple[str, ...] = tuple(node.key for node in LEAVES)


def is_group(key: str) -> bool:
    return bool(CHILDREN.get(key))


def descendants(key: str) -> list[ScanNode]:
    node = NODE_BY_KEY.get(key)
    if not node:
        return []
    if not CHILDREN.get(key):
        return [node]
    result: list[ScanNode] = []
    for child in CHILDREN.get(key, []):
        result.extend(descendants(child.key))
    return result


def expand(keys: Iterable[str]) -> set[str]:
    """Turn any mix of group and leaf keys into explicit enabled leaves."""
    enabled: set[str] = set()
    for raw in keys:
        key = str(raw or "").strip()
        if not key or key not in NODE_BY_KEY:
            continue
        for node in descendants(key):
            if not CHILDREN.get(node.key):
                enabled.add(node.key)
    return enabled


def group_state(key: str, enabled: set[str]) -> str:
    """all | some | none for a group, used by the dashboard checkboxes."""
    if not CHILDREN.get(key):
        return "all" if key in enabled else "none"
    leaves = {node.key for node in descendants(key) if not CHILDREN.get(node.key)}
    if not leaves:
        return "none"
    selected = len(leaves & enabled)
    if selected == len(leaves):
        return "all"
    return "some" if selected else "none"


def all_leaf_keys() -> tuple[str, ...]:
    return LEAF_KEYS


def default_leaf_keys() -> tuple[str, ...]:
    return tuple(node.key for node in LEAVES if node.default_enabled)


def node_payload(node: ScanNode, enabled: set[str]) -> dict[str, Any]:
    mappings = node.mappings
    return {
        "key": node.key,
        "label": node.label,
        "parent": node.parent,
        "level": node.level,
        "description": node.description,
        "valued": node.valued,
        "default_enabled": node.default_enabled,
        "enabled": node.key in enabled if not CHILDREN.get(node.key) else group_state(node.key, enabled) == "all",
        "state": group_state(node.key, enabled),
        "is_group": bool(CHILDREN.get(node.key)),
        "mapped_marketplaces": sorted({item.marketplace for item in mappings}),
        "mapping_count": len(mappings),
        "mappings": [
            {
                "marketplace": item.marketplace,
                "category_id": item.category_id,
                "label": item.label,
                "path": item.path,
                "broad": item.broad,
            }
            for item in mappings
        ],
        "keywords": list(node.keywords),
        "kinds": list(node.kinds),
    }


def tree_payload(enabled: set[str]) -> list[dict[str, Any]]:
    return [node_payload(node, enabled) for node in NODES]


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    slug: str
    name: str
    description: str
    categories: tuple[str, ...]
    min_price: float = 0.0
    max_price: float = 100.0
    marketplaces: tuple[str, ...] = ("vinted", "gumtree", "ebay")


def _leaves_under(*prefixes: str) -> tuple[str, ...]:
    result: list[str] = []
    for prefix in prefixes:
        for node in descendants(prefix):
            if not CHILDREN.get(node.key):
                result.append(node.key)
    return tuple(dict.fromkeys(result))


def _preset(
    slug: str,
    name: str,
    description: str,
    categories: Iterable[str],
    *,
    min_price: float = 0.0,
    max_price: float = 100.0,
    marketplaces: tuple[str, ...] = ("vinted", "gumtree", "ebay"),
) -> Preset:
    return Preset(slug, name, description, tuple(dict.fromkeys(categories)), min_price, max_price, marketplaces)


BUILTIN_PRESETS: tuple[Preset, ...] = (
    _preset("budget-electronics", "Budget Electronics", "Small components and peripherals under the £100 starter budget.", default_leaf_keys(), max_price=100),
    _preset("pc-components", "PC Components", "Individual PC parts and upgrades, not complete systems.", [*_leaves_under("pc-components"), "computers-laptops/laptop-parts"], max_price=100),
    _preset("graphics-cards", "Graphics Cards", "Graphics cards only.", ["pc-components/graphics-cards"], max_price=100),
    _preset("phones", "Phones", "Smartphones, feature phones and phone parts.", _leaves_under("phones-mobile"), max_price=250),
    _preset("laptops", "Laptops", "Laptops only, including MacBooks and Chromebooks.", _leaves_under("computers-laptops/laptops"), max_price=500),
    _preset("complete-pcs", "Complete PCs", "Complete desktop systems and workstations.", _leaves_under("computers-laptops/desktops"), max_price=500),
    _preset("consoles", "Consoles", "Console hardware and gaming gear.", _leaves_under("gaming-consoles"), max_price=300),
    _preset("tablets-displays", "Tablets & Displays", "Tablets, e-readers, monitors and projectors.", _leaves_under("tablets-displays"), max_price=300),
    _preset("gaming-peripherals", "Gaming Peripherals", "Mice, keyboards, headsets, controllers and capture gear.", [*_leaves_under("peripherals"), "gaming-consoles/gaming-headsets", "gaming-consoles/accessories"], max_price=150),
    _preset("photography-audio", "Photography & Audio", "Cameras, lenses, audio and studio equipment.", [*_leaves_under("cameras-photography"), *_leaves_under("audio-hifi"), "peripherals/headphones", "peripherals/audio-interfaces", "peripherals/microphones"], max_price=300),
    _preset("all-electronics", "All Electronics", "Every supported electronics category in the taxonomy.", all_leaf_keys(), min_price=0, max_price=25000),
)

BUILTIN_PRESET_BY_SLUG: dict[str, Preset] = {preset.slug: preset for preset in BUILTIN_PRESETS}


# ---------------------------------------------------------------------------
# Marketplace catalogue generated from the mappings
# ---------------------------------------------------------------------------


def _catalogue() -> dict[str, tuple[Category, ...]]:
    grouped: dict[str, dict[str, Category]] = {}
    for node in LEAVES:
        for item in node.mappings:
            bucket = grouped.setdefault(item.marketplace, {})
            if item.category_id in bucket:
                # Keep the first (usually most specific) label. Several leaves
                # may legitimately share a broad branch.
                continue
            bucket[item.category_id] = Category(
                marketplace=item.marketplace,
                category_id=item.category_id,
                label=item.label,
                path=item.path,
                coverage="partial" if item.broad else item.coverage,
                priority=item.priority,
            )
    return {
        marketplace: tuple(sorted(bucket.values(), key=lambda row: row.label.lower()))
        for marketplace, bucket in grouped.items()
    }


MARKETPLACE_CATALOGUES: dict[str, tuple[Category, ...]] = _catalogue()


def mapped_category_ids(marketplace: str, enabled: Iterable[str] | None = None) -> list[str]:
    """Distinct marketplace branch IDs needed for a configuration.

    One branch is requested once even when several leaves share it. This is the
    smallest request set the current mapping can express without downloading a
    whole marketplace.
    """
    enabled_set = expand(enabled) if enabled is not None else None
    if enabled_set is not None and not enabled_set:
        return []
    ids: list[str] = []
    for node in LEAVES:
        if enabled_set is not None and node.key not in enabled_set:
            continue
        for item in node.mappings:
            if item.marketplace == marketplace and item.category_id not in ids:
                ids.append(item.category_id)
    return ids


def marketplace_coverage(enabled: Iterable[str] | None = None) -> dict[str, Any]:
    enabled_set = expand(enabled) if enabled is not None else None
    report: dict[str, Any] = {}
    for marketplace in ("vinted", "gumtree", "ebay"):
        all_ids = mapped_category_ids(marketplace)
        active_ids = mapped_category_ids(marketplace, enabled_set) if enabled_set is not None else all_ids
        mapped_leaves = [
            node.key for node in LEAVES if any(item.marketplace == marketplace for item in node.mappings)
        ]
        report[marketplace] = {
            "discovered_categories": len(all_ids),
            "mapped_leaves": len(mapped_leaves),
            "selected_categories": len(active_ids),
            "categories": [
                {
                    "category_id": category.category_id,
                    "label": category.label,
                    "path": category.path,
                    "broad": any(
                        item.broad
                        for node in LEAVES
                        for item in node.mappings
                        if item.marketplace == marketplace and item.category_id == category.category_id
                    ),
                }
                for category in MARKETPLACE_CATALOGUES.get(marketplace, ())
            ],
            "selected_ids": active_ids,
        }
    unmapped = [
        node.key
        for node in LEAVES
        if not node.mappings
    ]
    unsupported = [node.key for node in LEAVES if not node.valued]
    # A leaf with a mapping is collectable even if valuation is not implemented.
    report["summary"] = {
        "leaves": len(LEAVES),
        "groups": len(NODES) - len(LEAVES),
        "valued_leaves": len(LEAVES) - len(unsupported),
        "unvalued_leaves": len(unsupported),
        "unmapped_leaves": len(unmapped),
        "unmapped_keys": unmapped,
        "unvalued_keys": unsupported,
        "total_requests_all": {
            marketplace: report[marketplace]["discovered_categories"]
            for marketplace in ("vinted", "gumtree", "ebay")
        },
        "total_requests_selected": {
            marketplace: report[marketplace]["selected_categories"]
            for marketplace in ("vinted", "gumtree", "ebay")
        },
    }
    return report


def category_to_leaf(marketplace: str, category_id: str) -> list[str]:
    return [
        node.key
        for node in LEAVES
        if any(item.marketplace == marketplace and item.category_id == str(category_id) for item in node.mappings)
    ]


# ---------------------------------------------------------------------------
# Lightweight classification before enrichment
# ---------------------------------------------------------------------------

_PERIPHERAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("peripherals/mousepads", re.compile(r"\b(mouse\s*pads?|mouse\s*mats?|desk\s*mats?)\b", re.I)),
    ("peripherals/mice", re.compile(r"\b(mouse|gaming\s+mouse|trackball)\b", re.I)),
    ("peripherals/keyboards", re.compile(r"\b(keyboard|keypad|mechanical\s+keyboard|keycap)\b", re.I)),
    ("peripherals/microphones", re.compile(r"\b(microphone|mic|condenser\s+mic|dynamic\s+mic)\b", re.I)),
    ("peripherals/audio-interfaces", re.compile(r"\b(audio\s+interface|sound\s+interface|midi\s+interface)\b", re.I)),
    ("peripherals/webcams", re.compile(r"\b(webcam|web\s*cam)\b", re.I)),
    ("peripherals/headphones", re.compile(r"\b(headphones?|headset|earbuds?|earphones?|in-ear)\b", re.I)),
    ("peripherals/controllers", re.compile(r"\b(controller|gamepad|joy-?con|dualsense|dualshock|xbox\s+controller)\b", re.I)),
    ("peripherals/capture-cards", re.compile(r"\b(capture\s+card|elgato|avermedia)\b", re.I)),
    ("peripherals/hubs-docks", re.compile(r"\b(usb\s+hub|docking\s+station|dock|thunderbolt\s+hub)\b", re.I)),
    ("peripherals/speakers", re.compile(r"\b(speakers?|soundbar)\b", re.I)),
)

_ACCESSORY_PATTERNS = re.compile(
    r"\b(case|cover|skin|protector|sleeve|bag|backpack|stand|bracket|mount|"
    r"cable|lead|charger|adapter|adaptor|caddy|enclosure|dock|stylus|pen|"
    r"replacement|spare|parts?|box\s+only|manual|packaging|empty\s+box)\b",
    re.I,
)


def classify_listing(
    ident: Any,
    title: str,
    source_category: str | None = None,
    marketplace: str = "",
) -> str:
    """Return the most specific taxonomy leaf for a listing.

    Identification remains authoritative for hardware it already understands.
    The taxonomy only adds the product-class level, and falls back to the
    marketplace branch that supplied the listing. An unknown listing is kept in
    "Other electronics" rather than silently assigned to a valuable category.
    """
    text = (title or "").lower()
    flags = set(getattr(ident, "flags", []) or [])
    category = str(getattr(ident, "category", "other") or "other").lower()
    key = str(getattr(ident, "product_key", "") or "")
    attrs = getattr(ident, "attrs", {}) or {}

    if "console_game" in flags or re.search(r"\b(game|games)\s+(bundle|lot)\b", text):
        return "gaming-consoles/games"
    if "storage_media" in flags or re.search(
        r"\b(micro\s*sd|microsd|sd\s*card|memory\s+card|cfexpress|usb\s+flash|pen\s+drive)\b", text
    ):
        return "storage-media/memory-cards"

    # A complete device named alongside a case, cable or spare part is still the
    # device; a title whose subject is the accessory belongs to the accessory
    # branch. Identification already separates those cases, so only use this
    # hint when it did not produce a product key.
    accessory = "accessory_or_box" in flags or "parts_only" in flags
    if accessory and category in {"other", "console", "phone", "tablet"}:
        for leaf_key, pattern in _PERIPHERAL_PATTERNS:
            if pattern.search(text) and not re.search(r"\b(for|fits|compatible)\b", text):
                return leaf_key
        if category == "phone" or re.search(r"\b(iphone|samsung|pixel|phone)\b", text):
            return "phones-mobile/parts-accessories"
        if category == "console" or re.search(r"\b(ps[45]|xbox|switch|steam\s+deck)\b", text):
            return "gaming-consoles/accessories"
        if re.search(r"\b(mouse|keyboard|headset|controller)\b", text):
            return "peripherals/other-peripherals"

    if category == "gpu":
        return "pc-components/graphics-cards"
    if category == "cpu":
        return "pc-components/processors"
    if category == "ram":
        return "pc-components/memory"
    if category == "motherboard":
        return "pc-components/motherboards"
    if category == "monitor":
        if re.search(r"\bprojector\b", text):
            return "tablets-displays/projectors"
        if re.search(r"\bportable\s+monitor\b", text):
            return "tablets-displays/portable-monitors"
        return "tablets-displays/monitors"
    if category == "ssd":
        if re.search(r"\b(external|portable)\b", text):
            return "storage-media/external-drives"
        if re.search(r"\b(hdd|hard\s+drive|mechanical)\b", text):
            return "pc-components/hdds"
        return "pc-components/ssds"
    if category == "laptop":
        if re.search(r"\b(macbook|mac\s+book)\b", text):
            return "computers-laptops/macbooks"
        if re.search(r"\bchromebook\b", text):
            return "computers-laptops/chromebooks"
        if re.search(r"\b(gaming|rtx|gtx|radeon)\b", text):
            return "computers-laptops/gaming-laptops"
        return "computers-laptops/windows-laptops"
    if category == "pc":
        if re.search(r"\b(mini\s*pc|mini-pc|nuc|micro\s*pc)\b", text):
            return "computers-laptops/mini-pcs"
        if re.search(r"\b(workstation|xeon|threadripper|epyc)\b", text):
            return "computers-laptops/workstations"
        if re.search(r"\b(server|rack)\b", text):
            return "computers-laptops/servers"
        if re.search(r"\b(gaming|rtx|gtx|radeon)\b", text):
            return "computers-laptops/gaming-desktops"
        return "computers-laptops/office-desktops"
    if category == "console":
        if key.startswith(("console:ps", "console:playstation", "console:psp", "console:vita")):
            return "gaming-consoles/playstation"
        if key.startswith("console:xbox"):
            return "gaming-consoles/xbox"
        if key.startswith("console:switch") or key.startswith("console:wii") or key.startswith("console:nintendo"):
            return "gaming-consoles/nintendo"
        if key.startswith(("console:steam-deck", "console:rog-ally", "console:legion-go")):
            return "gaming-consoles/steam-deck-handhelds"
        if key.startswith(("console:n64", "console:snes", "console:gamecube", "console:dreamcast", "console:genesis", "console:nes")):
            return "gaming-consoles/retro"
        return "gaming-consoles/other-consoles"
    if category == "phone":
        brand = key.split(":", 1)[1].split("-", 1)[0] if ":" in key else ""
        if brand == "iphone" or "iphone" in text:
            return "phones-mobile/iphones"
        if brand in {"galaxy", "samsung"} or "samsung" in text or "galaxy" in text:
            return "phones-mobile/samsung"
        if brand in {"pixel", "google"} or "pixel" in text:
            return "phones-mobile/pixel"
        if brand in {"xiaomi", "redmi", "poco"} or re.search(r"\b(xiaomi|redmi|poco)\b", text):
            return "phones-mobile/xiaomi"
        if re.search(r"\b(feature\s+phone|dumbphone|nokia\s+3310)\b", text):
            return "phones-mobile/feature-phones"
        return "phones-mobile/other-android"
    if category == "tablet":
        if "ipad" in text or key.startswith("tablet:ipad"):
            return "tablets-displays/ipads"
        if re.search(r"\b(kindle|e-?reader|kobo)\b", text) or key.startswith("tablet:kindle"):
            return "tablets-displays/ereaders"
        if re.search(r"\b(drawing|graphics?\s+tablet|pen\s+display|wacom)\b", text):
            return "tablets-displays/drawing-tablets"
        if key.startswith("tablet:pda"):
            return "tablets-displays/pdas"
        return "tablets-displays/android-tablets"
    if category == "networking":
        if re.search(r"\b(mesh|deco|orbi|eero)\b", text):
            return "networking-smart-home/mesh"
        if re.search(r"\b(switch|switches)\b", text):
            return "networking-smart-home/switches"
        if re.search(r"\b(access\s+point|ap\b|unifi)\b", text):
            return "networking-smart-home/access-points"
        if re.search(r"\bmodem\b", text):
            return "networking-smart-home/modems"
        if re.search(r"\b(repeater|extender|booster)\b", text):
            return "networking-smart-home/repeaters"
        if re.search(r"\b(hotspot|mifi)\b", text):
            return "networking-smart-home/hotspots"
        if re.search(r"\b(adapter|adaptor|dongle)\b", text):
            return "networking-smart-home/adapters"
        return "networking-smart-home/routers"

    # Peripherals are commonly left as "other" by hardware identification.
    if re.search(r"\b(mouse|keyboard|mousepad|headset|headphone|earbud|microphone|webcam|controller|gamepad|capture\s+card|usb\s+hub|speaker)\b", text):
        for leaf_key, pattern in _PERIPHERAL_PATTERNS:
            if pattern.search(text):
                return leaf_key

    # Product classes not yet parsed into a product key still need an honest
    # taxonomy home so they can be collected only when explicitly enabled.
    if re.search(r"\b(camera|dslr|mirrorless|camcorder)\b", text):
        return "cameras-photography/camcorders" if "camcorder" in text else "cameras-photography/cameras"
    if re.search(r"\blens\b", text):
        return "cameras-photography/lenses"
    if re.search(r"\bdrone\b", text):
        return "cameras-photography/drones"
    if re.search(r"\b(tripod|monopod|gimbal|stabiliser|stabilizer)\b", text):
        return "cameras-photography/tripods-stabilisers"
    if re.search(r"\b(flash|speedlight|softbox|studio\s+light)\b", text):
        return "cameras-photography/flashes-lighting"
    if re.search(r"\b(turntable|record\s+player|vinyl)\b", text):
        return "audio-hifi/turntables"
    if re.search(r"\b(amplifier|receiver|pre-?amp)\b", text):
        return "audio-hifi/amplifiers"
    if re.search(r"\b(soundbar|home\s+cinema)\b", text):
        return "tv-home-cinema/home-cinema"
    if re.search(r"\b(television|\btv\b|oled\s+tv|qled\s+tv)\b", text):
        return "tv-home-cinema/televisions"
    if re.search(r"\b(projector)\b", text):
        return "tablets-displays/projectors"
    if re.search(r"\b(streaming\s+stick|fire\s+stick|chromecast|roku)\b", text):
        return "tv-home-cinema/streaming"
    if re.search(r"\b(blu-?ray|dvd\s+player)\b", text):
        return "tv-home-cinema/players"
    if re.search(r"\b(smartwatch|smart\s+watch|apple\s+watch|galaxy\s+watch)\b", text):
        return "wearables/smartwatches"
    if re.search(r"\b(fitbit|fitness\s+tracker)\b", text):
        return "wearables/fitness-trackers"
    if re.search(r"\b(smart\s+ring|oura\s+ring)\b", text):
        return "wearables/smart-rings"
    if re.search(r"\b(smart\s+glasses|ray-?ban\s+meta)\b", text):
        return "wearables/smart-glasses"
    if re.search(r"\b(external\s+(hard\s+)?drive|portable\s+ssd|external\s+ssd)\b", text):
        return "storage-media/external-drives"
    if re.search(r"\b(nas|network\s+storage)\b", text):
        return "storage-media/nas"
    if re.search(r"\b(power\s*bank)\b", text):
        return "other-electronics/power-banks"
    if re.search(r"\b(charger|charging\s+brick|power\s+adapter)\b", text):
        return "other-electronics/chargers"
    if re.search(r"\b(cable|lead|adapter|adaptor)\b", text):
        return "other-electronics/cables"
    if re.search(r"\b(battery|batteries|rechargeable)\b", text):
        return "other-electronics/batteries"
    if re.search(r"\b(3d\s+printer|3d\s+scanner)\b", text):
        return "other-electronics/3d-printing"
    if re.search(r"\b(filament|resin)\b", text):
        return "other-electronics/3d-consumables"
    if re.search(r"\b(printer|scanner)\b", text):
        return "other-electronics/printers-scanners"

    # Last resort: use the branch that supplied the listing. A broad branch
    # often maps to several leaves, so only choose it when unambiguous.
    if marketplace and source_category:
        candidates = category_to_leaf(marketplace, source_category)
        if len(candidates) == 1:
            return candidates[0]
    return "other-electronics/other"


# ---------------------------------------------------------------------------
# Return the taxonomy catalogue in the shape app.categories expects
# ---------------------------------------------------------------------------


def catalogue_for(marketplace: str) -> tuple[Category, ...]:
    return MARKETPLACE_CATALOGUES.get(marketplace, ())


def category_for_id(marketplace: str, category_id: str) -> Category | None:
    for category in catalogue_for(marketplace):
        if category.category_id == str(category_id):
            return category
    return None
