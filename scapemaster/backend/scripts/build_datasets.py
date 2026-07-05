"""Build the committed ScapeMaster entity datasets from authoritative sources.

This is a DEV-TIME tool (never runs during gameplay). It fetches:

  * items    — the OSRS Wiki real-time prices API (/mapping for names, alch
               values, buy limits; /24h + /latest for a GE price snapshot),
               joined onto the curated allowlist below. Release years come from
               each item's wiki page infobox.
  * quests   — the complete quest list parsed from the wiki's rendered
               Quests/Free-to-play and Quests/Members tables, cross-checked
               against the wiki's own {{Globals}} totals.
  * monsters — infobox fields (combat, hitpoints, max hit, slayer level/xp,
               release) parsed from each curated monster's wiki page. Pages
               whose versions disagree on a field drop that field (or the whole
               monster) rather than guessing — an ambiguous fact is never
               committed.
  * skills   — the 23+ skills with release years and members flags from each
               skill page's infobox (the count is discovered, not hardcoded —
               new skills like Sailing appear automatically).

Anti-hallucination rule: nothing numeric in the output is hand-written. The
allowlists below carry only NAMES and editorial fame tiers; every stat comes
from the wiki or the prices API at build time.

Data (c) Jagex, via the OSRS Wiki (CC BY-SA 3.0) — see docs/DATA_SOURCES.md.

Usage:
    python scripts/build_datasets.py            # writes backend/app/data/*.json
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "data"

WIKI_API = "https://oldschool.runescape.wiki/api.php"
PRICES_API = "https://prices.runescape.wiki/api/v1/osrs"
USER_AGENT = "ScapeMaster fan quiz dataset build (github.com/technobacon/F1-stats)"
_REQUEST_GAP_S = 1.0  # be polite: one request per second
_last_request = 0.0


def _get(url: str) -> bytes:
    global _last_request
    wait = _REQUEST_GAP_S - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = resp.read()
    _last_request = time.monotonic()
    return body


def _get_json(url: str) -> dict:
    return json.loads(_get(url))


def _wiki(params: dict) -> dict:
    qs = urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    return _get_json(f"{WIKI_API}?{qs}")


# ── Curated allowlists (names + editorial fame tiers ONLY — no numbers) ──────
# fame_tier: 1 = iconic (everyone who played knows it), 2 = staple,
#            3 = known-to-regulars. Feeds difficulty weighting and gating.
ITEM_ALLOWLIST: list[tuple[str, int]] = [
    # Weapons & signature drops
    ("Abyssal whip", 1), ("Dragon scimitar", 1), ("Rune scimitar", 1),
    ("Dragon dagger", 1), ("Dragon claws", 1), ("Granite maul", 2),
    ("Dragon warhammer", 1), ("Elder maul", 2), ("Abyssal dagger", 2),
    ("Abyssal bludgeon", 2), ("Saradomin sword", 2), ("Zamorakian spear", 2),
    ("Staff of the dead", 2), ("Toxic staff (uncharged)", 2),
    ("Armadyl godsword", 1), ("Bandos godsword", 1), ("Saradomin godsword", 1),
    ("Zamorak godsword", 1), ("Godsword shard 1", 3),
    ("Twisted bow", 1), ("Toxic blowpipe (empty)", 1), ("Magic shortbow", 1),
    ("Rune crossbow", 1), ("Armadyl crossbow", 2), ("Dragon hunter crossbow", 1),
    ("Zaryte crossbow", 2), ("Craw's bow (u)", 3), ("Twisted buckler", 2),
    ("Dark bow", 2), ("Heavy ballista", 2), ("Toktz-xil-ak", 3),
    ("Ghrazi rapier", 1), ("Scythe of vitur (uncharged)", 1),
    ("Sanguinesti staff (uncharged)", 2), ("Justiciar chestguard", 2),
    ("Avernic defender hilt", 2), ("Osmumten's fang", 1), ("Lightbearer", 2),
    ("Tumeken's shadow (uncharged)", 1), ("Elidinis' ward", 2),
    ("Kodai wand", 2), ("Dragon hunter lance", 1), ("Inquisitor's mace", 2),
    ("Nightmare staff", 2), ("Harmonised orb", 2), ("Voidwaker", 1),
    ("Soulreaper axe", 2), ("Blade of saeldor (inactive)", 2),
    ("Bow of faerdhinen (inactive)", 2), ("Dragon 2h sword", 2),
    ("Dragon mace", 3), ("Dragon battleaxe", 3), ("Dragon halberd", 3),
    ("Tzhaar-ket-om", 3), ("Leaf-bladed battleaxe", 3),
    ("Sarachnis cudgel", 3), ("Zamorakian hasta", 3), 
    # Armour
    ("Rune platebody", 1), ("Rune platelegs", 1), ("Rune full helm", 1),
    ("Rune kiteshield", 2), ("Dragon boots", 1), ("Dragon platelegs", 2),
    ("Dragon chainbody", 2), ("Dragon full helm", 2), ("Dragonfire shield", 1),
    ("Bandos chestplate", 1), ("Bandos tassets", 1), ("Bandos boots", 3),
    ("Armadyl chestplate", 1), ("Armadyl chainskirt", 1), ("Armadyl helmet", 2),
    ("Torva full helm", 2), ("Torva platebody", 2), ("Torva platelegs", 2),
    ("Ancestral hat", 2), ("Ancestral robe top", 2), ("Ancestral robe bottom", 2),
    ("Masori mask", 2), ("Masori body", 2), ("Masori chaps", 2),
    ("Zaryte vambraces", 2), ("Primordial boots", 1), ("Pegasian boots", 2),
    ("Eternal boots", 2), ("Guardian boots", 3), ("Spectral spirit shield", 2),
    ("Arcane spirit shield", 1), ("Elysian spirit shield", 1),
    ("Blessed spirit shield", 3), ("Black d'hide body", 2),
    ("Green d'hide body", 2), ("Mystic robe top", 2), ("Ahrim's robetop", 2),
    ("Dharok's greataxe", 1), ("Dharok's platebody", 2), ("Karil's crossbow", 2),
    ("Verac's flail", 3), ("Guthan's warspear", 2), ("Torag's hammers", 3),
    ("Obsidian platebody", 3), ("Obsidian cape", 3),
    # Jewellery & accessories
    ("Amulet of fury", 1), ("Amulet of glory", 1), ("Amulet of power", 2),
    ("Amulet of torture", 1), ("Necklace of anguish", 2), ("Occult necklace", 1),
    ("Tormented bracelet", 2), ("Berserker ring", 1), ("Archers ring", 2),
    ("Seers ring", 3), ("Warrior ring", 3), ("Ring of suffering", 2),
    ("Ring of the gods", 3), ("Treasonous ring", 3), ("Tyrannical ring", 3),
    ("Ultor ring", 2), ("Bellator ring", 2), ("Magus ring", 2),
    ("Venator ring", 2), ("Ring of wealth", 2), ("Ring of dueling(8)", 2),
    ("Amulet of eternal glory", 3), ("Dragonstone", 2), ("Zenyte", 2),
    ("Zenyte shard", 2), ("Onyx", 2), ("Uncut onyx", 3),
    # Runes & ammo
    ("Nature rune", 1), ("Law rune", 1), ("Death rune", 1), ("Blood rune", 1),
    ("Soul rune", 2), ("Chaos rune", 2), ("Cosmic rune", 2), ("Wrath rune", 2),
    ("Astral rune", 2), ("Rune arrow", 2), ("Dragon arrow", 2),
    ("Amethyst arrow", 3), ("Dragon dart", 2), ("Rune dart", 3),
    ("Dragon bolts", 3), ("Ruby dragon bolts (e)", 3), ("Steel cannonball", 1),
    ("Red chinchompa", 2), ("Black chinchompa", 2),
    # Resources & consumables
    ("Coal", 1), ("Runite ore", 1), ("Adamantite ore", 2), ("Gold ore", 2),
    ("Iron ore", 2), ("Runite bar", 2), ("Adamantite bar", 3),
    ("Gold bar", 2), ("Steel bar", 2), ("Amethyst", 3),
    ("Yew logs", 1), ("Magic logs", 1), ("Redwood logs", 2), ("Mahogany logs", 2),
    ("Teak logs", 2), ("Willow logs", 2), ("Oak logs", 2), ("Logs", 2),
    ("Yew longbow", 2), ("Magic longbow", 2), ("Bow string", 2), ("Flax", 2),
    ("Feather", 2), ("Shark", 1), ("Raw shark", 2), ("Lobster", 1),
    ("Raw lobster", 2), ("Anglerfish", 2), ("Manta ray", 2), ("Dark crab", 3),
    ("Cooked karambwan", 2), ("Monkfish", 2), ("Swordfish", 2), ("Tuna", 3),
    ("Trout", 3), ("Salmon", 3), ("Bread", 3), ("Cake", 3),
    ("Prayer potion(4)", 1), ("Super restore(4)", 1), ("Saradomin brew(4)", 1),
    ("Stamina potion(4)", 1), ("Super combat potion(4)", 1),
    ("Ranging potion(4)", 2), ("Antifire potion(4)", 3), ("Zamorak brew(4)", 3),
    ("Grimy ranarr weed", 1), ("Ranarr weed", 1), ("Grimy snapdragon", 2),
    ("Snapdragon", 2), ("Torstol", 2), ("Grimy avantoe", 3), ("Ranarr seed", 2),
    ("Snapdragon seed", 3), ("Magic seed", 2), ("Yew seed", 3),
    ("Palm tree seed", 3), ("Dragon bones", 1), ("Big bones", 1),
    ("Superior dragon bones", 2), ("Wyvern bones", 3), ("Bones", 2),
    ("Pure essence", 2), ("Wine of zamorak", 2), ("Grapes", 3),
    ("Jug of wine", 3), ("Chocolate cake", 3), ("Purple sweets", 3),
    # Boss/skilling uniques & oddities
    ("Zulrah's scales", 2), ("Tanzanite fang", 2), ("Magic fang", 2),
    ("Serpentine visage", 2), ("Kraken tentacle", 2),
    ("Trident of the seas (full)", 2), ("Dragon pickaxe", 1),
    ("Dragon axe", 1), ("Dragon harpoon", 2), ("Rune pickaxe", 2),
    ("Rune axe", 2), ("Old school bond", 1), ("Rune 2h sword", 2),
    ("Rune battleaxe", 3), ("Elder chaos top", 3), ("Dark fishing bait", 3),
    ("Mystic air staff", 3),
    ("Ahrim's staff", 3), ("Black mask (10)", 2), ("Slayer's staff", 3),
    ("Broad bolts", 3), ("Imbued heart", 1), ("Eternal gem", 3),
     
    ("Gnome scarf", 3), ("3rd age full helmet", 2),
    ("3rd age pickaxe", 2), ("Gilded platebody", 3), ("Bucket helm (g)", 3),
]

# Monster wiki page titles. is_boss is definitional (editorial), never numeric.
MONSTER_LIST: list[tuple[str, bool]] = [
    # Bosses
    ("Zulrah", True), ("Vorkath", True), ("TzTok-Jad", True), ("TzKal-Zuk", True),
    ("Corporeal Beast", True), ("King Black Dragon", True), ("Kalphite Queen", True),
    ("Giant Mole", True), ("Sarachnis", True), ("Scurrius", True),
    ("Cerberus", True), ("Abyssal Sire", True), ("Kraken", True),
    ("Thermonuclear smoke devil", True), ("Alchemical Hydra", True),
    ("Dagannoth Rex", True), ("Dagannoth Prime", True), ("Dagannoth Supreme", True),
    ("General Graardor", True), ("K'ril Tsutsaroth", True), ("Kree'arra", True),
    ("Commander Zilyana", True), ("Nex", True), ("Zalcano", True),
    ("The Nightmare", True), ("Phantom Muspah", True), ("Duke Sucellus", True),
    ("The Leviathan", True), ("The Whisperer", True), ("Vardorvis", True),
    ("Callisto", True), ("Venenatis", True), ("Chaos Elemental", True),
    ("Scorpia", True), ("Obor", True), ("Bryophyta", True), ("Skotizo", True),
    ("Zebak", True), ("Ba-Ba", True), ("Kephri", True), ("Akkha", True),
    ("Tekton", True), ("Araxxor", True),
    ("The Hueycoatl", True), ("Amoxliatl", True), ("Sol Heredit", True),
    ("Grotesque Guardians", True), ("Vet'ion", True), ("Dharok the Wretched", False),
    ("Ahrim the Blighted", False), ("Karil the Tainted", False),
    # Slayer & overworld monsters
    ("Abyssal demon", False), ("Gargoyle", False), ("Nechryael", False),
    ("Dust devil", False), ("Kurask", False), ("Turoth", False),
    ("Cave horror", False), ("Basilisk", False), ("Hellhound", False),
    ("Black demon", False), ("Greater demon", False), ("Lesser demon", False),
    ("Blue dragon", False), ("Red dragon", False), ("Black dragon", False),
    ("Green dragon", False), ("Rune dragon", False), ("Adamant dragon", False),
    ("Lava dragon", False), ("Skeletal Wyvern", False), ("Smoke devil", False),
    ("Demonic gorilla", False), ("Lizardman shaman", False), ("Ankou", False),
    ("Aberrant spectre", False), ("Bloodveld", False), ("Mutated Bloodveld", False),
    ("Jelly", False), ("Wyrm", False), ("Drake", False), ("Hydra", False),
    ("Banshee", False), ("Cockatrice", False), ("Crawling Hand", False),
    ("Hill Giant", False), ("Moss giant", False), ("Fire giant", False),
    ("Ice giant", False), ("Cow", False), ("Chicken", False), ("Imp", False),
    ("Unicorn", False), ("Goblin", False), ("Cave kraken", False),
    ("Dark beast", False), ("Waterfiend", False), ("Brutal black dragon", False),
]

SKILL_PAGES = [
    "Attack", "Strength", "Defence", "Hitpoints", "Ranged", "Prayer", "Magic",
    "Runecraft", "Construction", "Agility", "Herblore", "Thieving", "Crafting",
    "Fletching", "Slayer", "Hunter", "Mining", "Smithing", "Fishing", "Cooking",
    "Firemaking", "Woodcutting", "Farming", "Sailing",
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ── Wiki page fetching + infobox parsing ─────────────────────────────────────
def fetch_wikitext(titles: list[str]) -> dict[str, str]:
    """Batch-fetch page wikitext (50 titles per request, following redirects)."""
    out: dict[str, str] = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        data = _wiki({
            "action": "query", "prop": "revisions", "rvprop": "content",
            "rvslots": "main", "redirects": "1", "titles": "|".join(batch),
        })
        redirect_back = {r["to"]: r["from"] for r in data["query"].get("redirects", [])}
        for page in data["query"]["pages"]:
            if "missing" in page or not page.get("revisions"):
                continue
            title = page["title"]
            text = page["revisions"][0]["slots"]["main"]["content"]
            out[title] = text
            if title in redirect_back:
                out[redirect_back[title]] = text
    return out


def _infobox_values(wikitext: str, field: str) -> list[str]:
    """All values of an infobox field, covering both the plain (`|combat = x`)
    and versioned (`|combat1 = x`, `|combat2 = y`) forms."""
    values = []
    for m in re.finditer(
        rf"^\s*\|\s*{re.escape(field)}(\d*)\s*=\s*(.*?)\s*$", wikitext, re.MULTILINE
    ):
        v = m.group(2).strip()
        if v:
            values.append(v)
    return values


def _clean_int(raw: str) -> int | None:
    """A value that is a single unambiguous integer (commas allowed), else None."""
    txt = re.sub(r"<[^>]+>", " ", raw)             # strip html tags/comments
    txt = re.sub(r"\{\{[^}]*\}\}", " ", txt)       # strip templates
    txt = txt.replace(",", " ").strip()
    nums = re.findall(r"-?\d+", txt)
    leftover = re.sub(r"-?\d+", "", txt).strip(" .()")
    if len(nums) == 1 and not leftover:
        return int(nums[0])
    return None


def _clean_number(raw: str) -> float | None:
    txt = re.sub(r"<[^>]+>", " ", raw)
    txt = re.sub(r"\{\{[^}]*\}\}", " ", txt).replace(",", " ").strip()
    nums = re.findall(r"-?\d+(?:\.\d+)?", txt)
    leftover = re.sub(r"-?\d+(?:\.\d+)?", "", txt).strip(" .()")
    if len(nums) == 1 and not leftover:
        return float(nums[0])
    return None


def _consensus_int(wikitext: str, field: str) -> int | None:
    """The field's value when every version of the infobox agrees on a single
    clean integer; None (fact omitted) on any disagreement or messy markup."""
    parsed = {_clean_int(v) for v in _infobox_values(wikitext, field)}
    parsed.discard(None)
    return parsed.pop() if len(parsed) == 1 else None


def _consensus_number(wikitext: str, field: str) -> float | None:
    parsed = {_clean_number(v) for v in _infobox_values(wikitext, field)}
    parsed.discard(None)
    return parsed.pop() if len(parsed) == 1 else None


def _release_year(wikitext: str) -> int | None:
    """Year from the FIRST |release= field (the page subject's own release)."""
    values = _infobox_values(wikitext, "release")
    for v in values:
        m = re.search(r"\[\[(\d{4})\]\]", v) or re.search(r"\b(19|20)(\d{2})\b", v)
        if m:
            return int(m.group(0).strip("[]")) if m.re.pattern.startswith("\\[\\[") else int(m.group(0))
    return None


def _members_flag(wikitext: str) -> int | None:
    values = _infobox_values(wikitext, "members")
    flags = set()
    for v in values:
        vl = v.strip().lower()
        if vl.startswith("yes"):
            flags.add(1)
        elif vl.startswith("no"):
            flags.add(0)
    return flags.pop() if len(flags) == 1 else None


# ── Builders ─────────────────────────────────────────────────────────────────
def build_items() -> tuple[list[dict], list[dict]]:
    print("Fetching prices API /mapping ...")
    mapping = _get_json(f"{PRICES_API}/mapping")
    by_name: dict[str, dict] = {}
    for row in mapping:
        # On a duplicate name keep the lowest id (the canonical item).
        cur = by_name.get(row["name"])
        if cur is None or row["id"] < cur["id"]:
            by_name[row["name"]] = row

    print("Fetching prices API /24h and /latest ...")
    day = _get_json(f"{PRICES_API}/24h")["data"]
    latest = _get_json(f"{PRICES_API}/latest")["data"]

    names = [n for n, _fame in ITEM_ALLOWLIST]
    missing = [n for n in names if n not in by_name]
    if missing:
        sys.exit(f"ITEM ALLOWLIST NAMES NOT IN MAPPING (fix the list): {missing}")

    print(f"Fetching wiki release years for {len(names)} items ...")
    pages = fetch_wikitext(names)

    curated, items = [], []
    for name, fame in ITEM_ALLOWLIST:
        m = by_name[name]
        item_id = m["id"]
        d = day.get(str(item_id), {})
        l = latest.get(str(item_id), {})
        hi = d.get("avgHighPrice") or l.get("high")
        lo = d.get("avgLowPrice") or l.get("low")
        ge_price = round((hi + lo) / 2) if hi and lo else (hi or lo)
        ge_volume = (d.get("highPriceVolume") or 0) + (d.get("lowPriceVolume") or 0)
        release_year = _release_year(pages.get(name, ""))
        curated.append({
            "item_id": item_id, "name": name, "fame_tier": fame,
            "release_year": release_year,
        })
        items.append({
            "item_id": item_id, "name": name,
            "members": 1 if m.get("members") else 0,
            "buy_limit": m.get("limit"), "value": m.get("value"),
            "low_alch": m.get("lowalch"), "high_alch": m.get("highalch"),
            "ge_price": ge_price, "ge_volume": ge_volume,
            "release_year": release_year, "fame_tier": fame,
        })
    return curated, items


_QUEST_ROW_RE = re.compile(r'<tr data-rowid="([^"]+)">(.*?)</tr>', re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)(?=<td|$)", re.DOTALL)


def _strip_html(fragment: str) -> str:
    txt = re.sub(r"<[^>]+>", "", fragment)
    return re.sub(r"\s+", " ", txt).strip()


def _parse_quest_table(page: str, members: int) -> list[dict]:
    html = _wiki({"action": "parse", "page": page, "prop": "text"})["parse"]["text"]
    quests = []
    for name, row in _QUEST_ROW_RE.findall(html):
        name = unescape(name)
        # Recipe for Disaster is officially ONE quest (10 QP); the wiki table
        # also lists its ten subquests as "Recipe for Disaster/..." rows.
        if "/" in name:
            continue
        tds = _TD_RE.findall(row)
        if len(tds) < 5:
            continue
        difficulty = _strip_html(tds[2]).lower()
        qp = _clean_int(_strip_html(tds[4]))
        series = _strip_html(tds[5]) if len(tds) > 5 else ""
        series = re.sub(r",?\s*#\d+.*$", "", series).strip()
        if series.lower() in ("n/a", "none", "-", ""):
            series = None
        ym = re.search(r'title="(\d{4})"', tds[6]) if len(tds) > 6 else None
        if qp is None:
            continue
        quests.append({
            "quest_id": _slug(name), "name": name, "difficulty": difficulty,
            "quest_points": qp, "members": members,
            "release_year": int(ym.group(1)) if ym else None,
            "series": series,
        })
    return quests


def build_quests() -> list[dict]:
    print("Fetching quest tables ...")
    quests = _parse_quest_table("Quests/Free-to-play", members=0)
    quests += _parse_quest_table("Quests/Members", members=1)

    # Cross-check against the wiki's own {{Globals}} counters.
    expand = _wiki({
        "action": "expandtemplates", "prop": "wikitext",
        "text": "{{Globals|quests}}|{{Globals|quest points}}",
    })["expandtemplates"]["wikitext"]
    wiki_count, wiki_qp = (int(x) for x in expand.split("|"))
    got_qp = sum(q["quest_points"] for q in quests)
    if len(quests) != wiki_count or got_qp != wiki_qp:
        sys.exit(
            f"QUEST CROSS-CHECK FAILED: parsed {len(quests)} quests / {got_qp} QP, "
            f"wiki Globals say {wiki_count} quests / {wiki_qp} QP"
        )
    print(f"  parsed {len(quests)} quests, {got_qp} QP (matches wiki Globals)")
    return quests


def build_monsters() -> list[dict]:
    titles = [t for t, _b in MONSTER_LIST]
    print(f"Fetching {len(titles)} monster pages ...")
    pages = fetch_wikitext(titles)
    monsters, dropped = [], []
    for title, is_boss in MONSTER_LIST:
        text = pages.get(title)
        if not text:
            dropped.append((title, "page missing"))
            continue
        combat = _consensus_int(text, "combat")
        hitpoints = _consensus_int(text, "hitpoints")
        if combat is None or hitpoints is None:
            dropped.append((title, "ambiguous combat/hitpoints"))
            continue
        monsters.append({
            "monster_id": _slug(title), "name": title,
            "combat_level": combat, "hitpoints": hitpoints,
            "max_hit": _consensus_int(text, "max hit"),
            "slayer_level": _consensus_int(text, "slaylvl") or 1,
            "slayer_xp": _consensus_number(text, "slayxp"),
            "release_year": _release_year(text),
            "is_boss": 1 if is_boss else 0,
        })
    for title, why in dropped:
        print(f"  dropped {title}: {why}")
    print(f"  kept {len(monsters)} monsters")
    return monsters


def build_skills() -> list[dict]:
    print(f"Fetching {len(SKILL_PAGES)} skill pages ...")
    pages = fetch_wikitext(SKILL_PAGES)
    skills = []
    for title in SKILL_PAGES:
        text = pages.get(title)
        if not text:
            sys.exit(f"SKILL PAGE MISSING: {title}")
        skills.append({
            "skill_id": _slug(title), "name": title,
            "members": _members_flag(text) or 0,
            "release_year": _release_year(text),
        })
    return skills


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    curated, items = build_items()
    quests = build_quests()
    monsters = build_monsters()
    skills = build_skills()

    (DATA_DIR / "curated_items.json").write_text(json.dumps(curated, indent=1, ensure_ascii=False))
    (DATA_DIR / "items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False))
    (DATA_DIR / "quests.json").write_text(json.dumps(quests, indent=1, ensure_ascii=False))
    (DATA_DIR / "monsters.json").write_text(json.dumps(monsters, indent=1, ensure_ascii=False))
    (DATA_DIR / "skills.json").write_text(json.dumps(skills, indent=1, ensure_ascii=False))
    print(f"Wrote {len(items)} items, {len(quests)} quests, "
          f"{len(monsters)} monsters, {len(skills)} skills -> {DATA_DIR}")


if __name__ == "__main__":
    main()
