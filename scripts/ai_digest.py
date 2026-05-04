#!/usr/bin/env python3
"""AI news RSS digest for Hermes cron.

Fetches trusted AI feeds, deduplicates by URL, ranks by practical signal,
tags items, persists seen URLs, and prints a compact French Markdown digest.
No third-party dependencies.
"""
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
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

FEEDS = [
    {"name": "Google AI Blog", "url": "https://blog.google/technology/ai/rss/", "source_tag": "google"},
    {"name": "Google DeepMind Blog", "url": "https://deepmind.google/blog/rss.xml", "source_tag": "deepmind"},
    {"name": "MIT News — AI", "url": "https://news.mit.edu/topic/mitartificial-intelligence2-rss.xml", "source_tag": "research"},
    {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml", "source_tag": "open-source"},
    {"name": "Import AI", "url": "https://jack-clark.net/feed/", "source_tag": "analysis"},
    # The Batch has no working RSS feed. Scrape article cards from public page.
    {"name": "The Batch", "url": "https://www.deeplearning.ai/the-batch/", "source_tag": "newsletter", "mode": "scrape-batch"},
    {"name": "The Decoder", "url": "https://the-decoder.com/feed/", "source_tag": "media"},
]

DEFAULT_STATE = Path(os.environ.get("AI_DIGEST_STATE", "/opt/data/hermes-home/cache/ai_digest_state.json"))
USER_AGENT = "Hermes AI Digest/1.0 (+https://github.com/coutug/hermes-home)"
NOW = dt.datetime.now(dt.timezone.utc)

TAG_RULES = [
    ("model", r"\b(model|llm|language model|foundation model|gpt|gemini|claude|llama|mistral|qwen|reasoning)\b"),
    ("release", r"\b(release|launch|introduc|announc|available|preview|beta|general availability|ga\b|api)\b"),
    ("research", r"\b(research|paper|benchmark|eval|dataset|training|post-training|alignment|inference|agent)\b"),
    ("product", r"\b(product|chatgpt|workspace|assistant|copilot|studio|enterprise|developer|platform)\b"),
    ("open-source", r"\b(open source|open-source|hugging face|transformers|diffusers|datasets|weights|checkpoint)\b"),
    ("multimodal", r"\b(multimodal|vision|image|video|audio|speech|voice|robot|robotics)\b"),
    ("safety", r"\b(safety|security|policy|privacy|risk|responsible|red team|misuse|evaluation)\b"),
    ("infrastructure", r"\b(infrastructure|gpu|tpu|chip|compute|serving|latency|throughput|cloud|datacenter)\b"),
]

PRIORITY = {
    "safety": 90,
    "release": 85,
    "model": 80,
    "product": 60,
    "research": 55,
    "open-source": 50,
    "multimodal": 45,
    "infrastructure": 40,
    "google": 30,
    "deepmind": 30,
    "analysis": 30,
    "newsletter": 25,
    "media": 20,
    "research_source": 25,
    "research": 55,
}

NOISE_PATTERNS = [
    r"\bwebinar\b",
    r"\bsponsored\b",
    r"\bregister now\b",
    r"\bdiscount\b",
    r"\bconference agenda\b",
]

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


def infer_impact(title: str) -> str:
    lower = title.lower()
    if re.search(r"safety|security|privacy|risk|policy|red team", lower):
        return "Signal sécurité/gouvernance: vérifier impact usage, données et conformité."
    if re.search(r"release|launch|available|api|model|gpt|gemini|llama|claude", lower):
        return "Signal produit/modèle: vérifier capacités, prix, API et cas d'usage."
    if re.search(r"research|paper|benchmark|dataset|training|eval", lower):
        return "Signal recherche: vérifier percée, benchmark et reproductibilité."
    if re.search(r"open source|hugging face|transformers|weights", lower):
        return "Signal open-source: vérifier artefacts, licence et possibilité de test."
    return "Signal IA à évaluer pour usage produit, dev ou recherche."


def short_sentence(text: str, title: str = "", limit: int = 210) -> str:
    text = strip_html(text)
    if not text or re.fullmatch(r"(Editors?|Authors?|By):?\s+.+", text, re.I):
        return infer_impact(title)
    parts = re.split(r"(?<=[.!?])\s+", text)
    first = parts[0].strip()
    if re.fullmatch(r"(Editors?|Authors?|By):?\s+.+", first, re.I) and len(parts) > 1:
        first = parts[1].strip()
    if len(first) > limit:
        first = first[: limit - 1].rsplit(" ", 1)[0] + "…"
    return first or infer_impact(title)


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
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            parsed = dt.datetime.strptime(value[:25], fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc)
        except Exception:
            continue
    return None


def tag_item(title: str, summary: str, source_tag: str) -> list[str]:
    hay = f"{title} {summary}".lower()
    tags = []
    for tag, pattern in TAG_RULES:
        if re.search(pattern, hay, re.I):
            tags.append(tag)
    if source_tag not in tags:
        tags.append(source_tag)
    return sorted(set(tags), key=lambda t: (-PRIORITY.get(t, 0), t))


def score_item(title: str, summary: str, tags: Iterable[str]) -> int:
    hay = f"{title} {summary}".lower()
    score = sum(PRIORITY.get(t, 0) for t in set(tags))
    if re.search(r"\b(new model|frontier|state-of-the-art|sota|reasoning|agent|api|general availability|available now)\b", hay):
        score += 35
    if re.search(r"\b(safety|security|privacy|policy|misuse|red team)\b", hay):
        score += 30
    if re.search(r"\b(open weights|open-source|dataset|benchmark|github|hugging face)\b", hay):
        score += 20
    if any(re.search(pattern, hay) for pattern in NOISE_PATTERNS):
        score -= 50
    return score


def item_key(url: str, title: str) -> str:
    base = (url or title).strip().lower()
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]


def fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5"})
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


def parse_batch_page(feed: dict) -> list[Item]:
    """Scrape The Batch page because DeepLearning.AI exposes no working RSS feed."""
    raw = fetch(feed["url"], timeout=20).decode("utf-8", "ignore")
    links: dict[str, str] = {}
    for match in re.finditer(r'href=["\']([^"\']*the-batch[^"\']*)["\']', raw, re.I):
        url = html.unescape(match.group(1)).split("?")[0]
        if not url.startswith("http"):
            url = "https://www.deeplearning.ai" + url
        if any(part in url for part in ("/tag/", "/page/", "/author/", "/category/", "/about/")):
            continue
        if url.rstrip("/") == feed["url"].rstrip("/"):
            continue
        slug = url.rstrip("/").split("/")[-1]
        if not (slug.startswith("issue-") or len(slug) > 18):
            continue
        title = "The Batch " + slug.replace("-", " ").title() if slug.startswith("issue-") else slug.replace("-", " ").title()
        links[url] = title
    items: list[Item] = []
    for url, title in list(links.items())[:30]:
        summary = infer_impact(title)
        tags = tag_item(title, summary, feed["source_tag"])
        score = score_item(title, summary, tags)
        items.append(Item(title, url, feed["name"], None, summary, tags, score, item_key(url, title)))
    return items


def parse_feed(feed: dict) -> list[Item]:
    if feed.get("mode") == "scrape-batch":
        return parse_batch_page(feed)
    raw = fetch(feed["url"])
    root = ET.fromstring(raw)
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
        summary = first_text(entry, ["description", "summary", "content", "{*}summary", "{*}content"])
        published_raw = first_text(entry, ["pubDate", "published", "updated", "{*}published", "{*}updated"])
        published_dt = parse_date(published_raw)
        published = published_dt.date().isoformat() if published_dt else None
        if not title or not url:
            continue
        compact_summary = short_sentence(summary, title)
        tags = tag_item(title, compact_summary, feed["source_tag"])
        score = score_item(title, compact_summary, tags)
        items.append(Item(title, url, feed["name"], published, compact_summary, tags, score, item_key(url, title)))
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
    cutoff = (NOW - dt.timedelta(days=180)).date().isoformat()
    state["sent"] = {k: v for k, v in state.get("sent", {}).items() if str(v.get("sent_at", "9999")) >= cutoff}
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))


def pick_diverse(candidates: list[Item], max_items: int, per_source_cap: int = 3) -> list[Item]:
    """Pick high-score items while avoiding one source dominating digest."""
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
        if item in picked:
            continue
        picked.append(item)
        if len(picked) >= max_items:
            break
    return picked


def collect(max_items: int, lookback_days: int, state_path: Path, dry_run: bool, bootstrap_silent: bool = False) -> tuple[list[Item], dict]:
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
        if item.published:
            parsed = parse_date(item.published)
            if parsed and parsed < cutoff:
                continue
        candidates.append(item)

    candidates.sort(key=lambda i: (i.score, i.published or "0000-00-00", i.title), reverse=True)
    picked = pick_diverse(candidates, max_items=max_items, per_source_cap=3)

    if bootstrap_silent and not sent:
        for item in unique.values():
            sent[item.key] = {"url": item.url, "title": item.title, "sent_at": NOW.date().isoformat(), "delivered": False}
        picked = []

    if not dry_run:
        delivered_keys = {picked_item.key for picked_item in picked}
        for item in candidates:
            sent[item.key] = {"url": item.url, "title": item.title, "sent_at": NOW.date().isoformat(), "delivered": item.key in delivered_keys}
        state.setdefault("runs", []).append({"ran_at": NOW.isoformat(), "picked": len(picked), "candidates_seen": len(candidates), "errors": errors})
        state["runs"] = state["runs"][-30:]
        save_state(state_path, state)

    meta = {"fetched": len(all_items), "unique": len(unique), "new_candidates": len(candidates), "picked": len(picked), "errors": errors, "dry_run": dry_run, "state_path": str(state_path)}
    return picked, meta


def render(items: list[Item], meta: dict, weekly: bool = False) -> str:
    today = NOW.astimezone().date().isoformat()
    title = "Veille IA — Top 5 semaine" if weekly else "Veille IA — digest 3x/semaine"
    lines = [f"## {title} — {today}", ""]

    if not items:
        lines.append("Aucun nouvel item fort depuis dernier digest.")
        if meta.get("errors"):
            lines.append("")
            lines.append("### Sources avec erreur")
            for err in meta["errors"]:
                lines.append(f"- {err['source']}: `{err['error']}`")
        lines.append("")
        lines.append(f"_Sources lues: {meta.get('fetched', 0)} items, {meta.get('unique', 0)} uniques._")
        return "\n".join(lines)

    must_read = [i for i in items if i.score >= 120]
    optional = [i for i in items if i.score < 120]
    if not must_read:
        must_read, optional = items[: min(4, len(items))], items[min(4, len(items)):]

    lines.append("### À lire maintenant")
    for item in must_read:
        tag_str = ", ".join(item.tags[:3])
        published = f" — {item.published}" if item.published else ""
        lines.append(f"- [{tag_str}] [{item.title}]({item.url})")
        lines.append(f"  Impact: {item.summary}{published}")

    if optional:
        lines.append("")
        lines.append("### Bruit faible / optionnel")
        for item in optional:
            tag_str = ", ".join(item.tags[:2])
            lines.append(f"- [{tag_str}] [{item.title}]({item.url})")

    action = next((i for i in items if "safety" in i.tags), None) or next((i for i in items if "release" in i.tags), None) or next((i for i in items if "open-source" in i.tags), None) or items[0]
    lines.append("")
    lines.append("### Top action")
    if "safety" in action.tags:
        lines.append(f"- Vérifier risques sécurité/gouvernance: {action.title}")
    elif "release" in action.tags or "model" in action.tags:
        lines.append(f"- Évaluer capacité/prix/API et cas d'usage: {action.title}")
    elif "open-source" in action.tags:
        lines.append(f"- Vérifier licence, poids/code et possibilité de test: {action.title}")
    else:
        lines.append(f"- Scanner pertinence pour produit/dev/recherche: {action.title}")

    if meta.get("errors"):
        lines.append("")
        lines.append("### Sources avec erreur")
        for err in meta["errors"]:
            lines.append(f"- {err['source']}: `{err['error']}`")

    lines.append("")
    lines.append(f"_Sources: Google AI, DeepMind, MIT News AI, Hugging Face, Import AI, The Batch, The Decoder. Dédupe active. {meta.get('fetched', 0)} items lus, {meta.get('unique', 0)} uniques, {meta.get('new_candidates', 0)} nouveaux._")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate AI RSS digest Markdown")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--max-items", type=int, default=8)
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true", help="do not update sent cache")
    parser.add_argument("--weekly", action="store_true", help="render weekly title; also limits to top 5 unless --max-items supplied")
    parser.add_argument("--bootstrap-silent", action="store_true", help="on empty state, mark current feed entries sent and emit no items")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = parser.parse_args(argv)

    if args.weekly and args.max_items == parser.get_default("max_items"):
        args.max_items = 5

    items, meta = collect(args.max_items, args.lookback_days, args.state, args.dry_run, args.bootstrap_silent)
    if args.json:
        print(json.dumps({"items": [asdict(i) for i in items], "meta": meta}, ensure_ascii=False, indent=2))
    else:
        print(render(items, meta, weekly=args.weekly))
    return 0 if not meta.get("errors") or items else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
