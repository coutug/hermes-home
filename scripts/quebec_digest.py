#!/usr/bin/env python3
"""Québec/Canada news RSS digest for Hermes cron. No third-party deps."""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

FEEDS = [
    {"name": "Radio-Canada Québec", "url": "https://ici.radio-canada.ca/rss/4159", "source_tag": "public", "region": "quebec"},
    {"name": "La Presse — Actualités", "url": "https://www.lapresse.ca/actualites/rss", "source_tag": "presse", "region": "quebec"},
    {"name": "La Presse — Politique", "url": "https://www.lapresse.ca/actualites/politique/rss", "source_tag": "politique", "region": "quebec"},
    {"name": "Noovo Info", "url": "https://www.noovo.info/arc/outboundfeeds/rss/", "source_tag": "tv", "region": "quebec"},
    {"name": "Journal de Montréal", "url": "https://www.journaldemontreal.com/rss.xml", "source_tag": "presse", "region": "quebec"},
    {"name": "Global News Montréal", "url": "https://globalnews.ca/montreal/feed/", "source_tag": "media-en", "region": "montreal"},
    {"name": "CityNews Montréal", "url": "https://montreal.citynews.ca/feed/", "source_tag": "media-en", "region": "montreal"},
    {"name": "National Post Canada", "url": "https://nationalpost.com/category/news/canada/feed/", "source_tag": "canada", "region": "canada"},
]

DEFAULT_STATE = Path(os.environ.get("QUEBEC_DIGEST_STATE", "/opt/data/hermes-home/cache/quebec_digest_state.json"))
USER_AGENT = "Hermes Quebec Digest/1.0 (+https://github.com/coutug/hermes-home)"
NOW = dt.datetime.now(dt.timezone.utc)

TAG_RULES = [
    ("alerte", r"\b(urgence|fusillade|meurtre|mort|décès|incendie|accident|évacuation|inondation|tempête|panne majeure|lockdown|missing|dead|fire|flood)\b"),
    ("politique", r"\b(caq|plq|pq|qs|legault|assemblée nationale|ministre|gouvernement|élection|budget|ottawa|parlement|trudeau|poilievre|carney|bloc|libéral|conservateur|ndp|policy|minister|election)\b"),
    ("économie", r"\b(économie|inflation|taux|emploi|chômage|entreprise|marché|budget|taxe|impôt|logement|immobilier|hydro-québec|economic|jobs|housing)\b"),
    ("justice", r"\b(cour|tribunal|juge|procès|accusé|arrestation|police|spvm|sq|rcmp|crime|court|trial|charged|arrested)\b"),
    ("santé", r"\b(santé|hôpital|médecin|urgence|chsld|ciuss|ciusss|covid|vaccin|health|hospital)\b"),
    ("transport", r"\b(transport|stm|exo|rtl|train|métro|rem|route|pont|trafic|circulation|transit)\b"),
    ("éducation", r"\b(école|cégep|université|enseignant|étudiant|education|school|university)\b"),
    ("environnement", r"\b(climat|environnement|énergie|forêt|feu de forêt|pollution|environment|climate|energy)\b"),
]

PRIORITY = {
    "alerte": 110,
    "politique": 85,
    "économie": 70,
    "santé": 65,
    "justice": 60,
    "transport": 55,
    "environnement": 50,
    "éducation": 45,
    "public": 35,
    "public-en": 30,
    "politique_source": 30,
    "canada": 25,
    "presse": 25,
    "tv": 20,
    "media-en": 15,
}

NOISE_PATTERNS = [r"\b(horoscope|loterie|recette|vedette|télé|sport|hockey|canadiens|sponsored|publicité)\b"]

@dataclass
class Item:
    title: str
    url: str
    source: str
    published: str | None
    summary: str
    tags: list[str]
    score: int
    key: str


def strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def short_sentence(text: str, title: str = "", limit: int = 220) -> str:
    text = strip_html(text)
    if not text:
        return infer_impact(title)
    parts = re.split(r"(?<=[.!?])\s+", text)
    first = parts[0].strip()
    if len(first) > limit:
        first = first[: limit - 1].rsplit(" ", 1)[0] + "…"
    return first or infer_impact(title)


def infer_impact(title: str) -> str:
    lower = title.lower()
    if re.search(r"urgence|incendie|accident|mort|meurtre|fusillade|évacuation|missing|dead|fire", lower):
        return "Actualité urgente: vérifier impact local, sécurité publique et services touchés."
    if re.search(r"budget|gouvernement|élection|ministre|ottawa|assemblée|policy|minister", lower):
        return "Signal politique: suivre décision, échéancier et impact Québec/Canada."
    if re.search(r"logement|emploi|inflation|taxe|impôt|hydro|housing|jobs", lower):
        return "Signal socio-économique: vérifier impact citoyens, coûts et services."
    return "Actualité Québec/Canada à surveiller."


def parse_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        pass
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def tag_item(title: str, summary: str, source_tag: str, region: str) -> list[str]:
    hay = f"{title} {summary}".lower()
    tags = []
    for tag, pattern in TAG_RULES:
        if re.search(pattern, hay, re.I):
            tags.append(tag)
    if region not in tags:
        tags.append(region)
    mapped_source = "politique_source" if source_tag == "politique" else source_tag
    if mapped_source not in tags:
        tags.append(mapped_source)
    return sorted(set(tags), key=lambda t: (-PRIORITY.get(t, 0), t))


def score_item(title: str, summary: str, tags: Iterable[str]) -> int:
    hay = f"{title} {summary}".lower()
    score = sum(PRIORITY.get(t, 0) for t in set(tags))
    if re.search(r"\b(important|majeur|urgent|breaking|dernière heure|crise|grève|strike)\b", hay):
        score += 35
    if re.search(r"\b(québec|montréal|montreal|canada|ottawa|saguenay|estrie|laval|longueuil|gatineau)\b", hay):
        score += 20
    if any(re.search(pattern, hay) for pattern in NOISE_PATTERNS):
        score -= 80
    return score


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    keep = [(k, v) for k, v in query if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), urllib.parse.urlencode(keep), ""))


def item_key(url: str, title: str) -> str:
    return hashlib.sha256((canonical_url(url) or title).strip().lower().encode()).hexdigest()[:20]


RELEVANCE_RE = re.compile(
    r"\b(québec|quebec|canada|canadien|canadian|montréal|montreal|ottawa|laval|longueuil|gatineau|sherbrooke|trois-rivières|saguenay|rimouski|estrie|outaouais|montérégie|laurentides|lanaudière|mauricie|abitibi|gaspésie|spvm|sq|rcmp|grc|hydro-québec|caq|plq|pq|qs|bloc|legault)\b",
    re.I,
)


def is_relevant(item: Item) -> bool:
    hay = f"{item.title} {item.summary}"
    return bool(RELEVANCE_RE.search(hay)) or item.source in {"CBC Politics"}


def fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5", "Connection": "close"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status}")
        return resp.read(2_000_000)


def first_text(elem: ET.Element, names: list[str]) -> str:
    for name in names:
        found = elem.find(name)
        if found is not None and found.text:
            return found.text.strip()
    wanted = {n.split('}')[-1] for n in names}
    for child in elem.iter():
        if child is elem:
            continue
        if child.tag.split('}')[-1] in wanted and child.text:
            return child.text.strip()
    return ""


def first_link(elem: ET.Element) -> str:
    link = first_text(elem, ["link"])
    if link:
        return link
    for child in elem.iter():
        if child.tag.split('}')[-1] == "link":
            href = child.attrib.get("href")
            rel = child.attrib.get("rel", "alternate")
            if href and rel in ("alternate", ""):
                return href
    return ""


def parse_feed(feed: dict) -> list[Item]:
    root = ET.fromstring(fetch(feed["url"]))
    root_name = root.tag.split('}')[-1].lower()
    if root_name == "rss":
        entries = root.findall(".//item")
    elif root_name == "feed":
        entries = [e for e in root if e.tag.split('}')[-1] == "entry"]
    else:
        entries = root.findall(".//item") or root.findall(".//{*}entry")
    items: list[Item] = []
    for entry in entries:
        title = strip_html(first_text(entry, ["title", "{*}title"]))
        url = first_link(entry)
        summary_raw = first_text(entry, ["description", "summary", "content", "{*}summary", "{*}content"])
        published_raw = first_text(entry, ["pubDate", "published", "updated", "{*}published", "{*}updated"])
        published_dt = parse_date(published_raw)
        published = published_dt.date().isoformat() if published_dt else None
        if not title or not url:
            continue
        url = canonical_url(url)
        summary = short_sentence(summary_raw, title)
        tags = tag_item(title, summary, feed["source_tag"], feed["region"])
        score = score_item(title, summary, tags)
        items.append(Item(title, url, feed["name"], published, summary, tags, score, item_key(url, title)))
    return items


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {"sent": {}, "runs": []}
    except json.JSONDecodeError:
        return {"sent": {}, "runs": [], "warning": "state_corrupt_reset"}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cutoff = (NOW - dt.timedelta(days=120)).date().isoformat()
    state["sent"] = {k: v for k, v in state.get("sent", {}).items() if str(v.get("sent_at", "9999")) >= cutoff}
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))


def pick_diverse(candidates: list[Item], max_items: int, per_source_cap: int = 2) -> list[Item]:
    if max_items <= 0:
        return []
    picked: list[Item] = []
    counts: dict[str, int] = {}
    for item in candidates:
        if counts.get(item.source, 0) >= per_source_cap:
            continue
        picked.append(item)
        counts[item.source] = counts.get(item.source, 0) + 1
        if len(picked) >= max_items:
            return picked
    for item in candidates:
        if item not in picked:
            picked.append(item)
            if len(picked) >= max_items:
                break
    return picked


def collect(max_items: int, lookback_days: int, state_path: Path, dry_run: bool) -> tuple[list[Item], dict]:
    state = load_state(state_path)
    sent = state.setdefault("sent", {})
    errors = []
    all_items: list[Item] = []
    cutoff = NOW - dt.timedelta(days=lookback_days)
    for feed in FEEDS:
        try:
            all_items.extend(parse_feed(feed))
        except Exception as exc:
            errors.append({"source": feed["name"], "error": str(exc)[:160]})
    unique: dict[str, Item] = {}
    for item in all_items:
        if item.key not in unique or item.score > unique[item.key].score:
            unique[item.key] = item
    candidates = []
    for item in unique.values():
        if item.key in sent:
            continue
        if not is_relevant(item):
            continue
        if item.published:
            parsed = parse_date(item.published)
            if parsed and parsed < cutoff:
                continue
        candidates.append(item)
    candidates.sort(key=lambda i: (i.score, i.published or "0000-00-00", i.title), reverse=True)
    picked = pick_diverse(candidates, max_items=max_items, per_source_cap=2)
    if not dry_run:
        delivered = {i.key for i in picked}
        for item in candidates:
            sent[item.key] = {"url": item.url, "title": item.title, "sent_at": NOW.date().isoformat(), "delivered": item.key in delivered}
        state.setdefault("runs", []).append({"ran_at": NOW.isoformat(), "picked": len(picked), "candidates_seen": len(candidates), "errors": errors})
        state["runs"] = state["runs"][-30:]
        save_state(state_path, state)
    meta = {"fetched": len(all_items), "unique": len(unique), "new_candidates": len(candidates), "picked": len(picked), "errors": errors, "dry_run": dry_run, "state_path": str(state_path)}
    return picked, meta


def render(items: list[Item], meta: dict) -> str:
    today = NOW.astimezone().date().isoformat()
    lines = [f"## Actualités Québec/Canada — digest 3x/semaine — {today}", ""]
    if not items:
        lines.append("Aucun nouvel item fort depuis dernier digest.")
        if meta.get("errors"):
            lines += ["", "### Sources avec erreur"]
            for err in meta["errors"]:
                lines.append(f"- {err['source']}: `{err['error']}`")
        lines += ["", f"_Sources lues: {meta.get('fetched', 0)} items, {meta.get('unique', 0)} uniques._"]
        return "\n".join(lines)

    must_read = [i for i in items if i.score >= 105]
    optional = [i for i in items if i.score < 105]
    if not must_read:
        must_read, optional = items[: min(5, len(items))], items[min(5, len(items)):]
    lines.append("### À lire maintenant")
    for item in must_read:
        tags = ", ".join(item.tags[:3])
        published = f" — {item.published}" if item.published else ""
        lines.append(f"- [{tags}] [{item.title}]({item.url})")
        lines.append(f"  Impact: {item.summary}{published}")
    if optional:
        lines += ["", "### À surveiller"]
        for item in optional:
            tags = ", ".join(item.tags[:2])
            lines.append(f"- [{tags}] [{item.title}]({item.url})")
    action = next((i for i in items if "alerte" in i.tags), None) or next((i for i in items if "politique" in i.tags), None) or items[0]
    lines += ["", "### Top action"]
    if "alerte" in action.tags:
        lines.append(f"- Vérifier impact immédiat local/services: {action.title}")
    elif "politique" in action.tags:
        lines.append(f"- Suivre décision, réactions et impacts Québec/Canada: {action.title}")
    else:
        lines.append(f"- Scanner pertinence citoyenne/locale: {action.title}")
    if meta.get("errors"):
        lines += ["", "### Sources avec erreur"]
        for err in meta["errors"]:
            lines.append(f"- {err['source']}: `{err['error']}`")
    lines += ["", f"_Sources: Radio-Canada Québec, La Presse, Noovo, Journal de Montréal, Global News Montréal, CityNews Montréal, National Post Canada. Dédupe active. {meta.get('fetched', 0)} items lus, {meta.get('unique', 0)} uniques, {meta.get('new_candidates', 0)} nouveaux._"]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate Québec/Canada news digest Markdown")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--max-items", type=int, default=8)
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    items, meta = collect(args.max_items, args.lookback_days, args.state, args.dry_run)
    if args.json:
        print(json.dumps({"items": [asdict(i) for i in items], "meta": meta}, ensure_ascii=False, indent=2))
    else:
        print(render(items, meta))
    return 0 if not meta.get("errors") or items else 2

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
