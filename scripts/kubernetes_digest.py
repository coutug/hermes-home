#!/usr/bin/env python3
"""Kubernetes ecosystem RSS digest for Hermes cron.

Fetches trusted Kubernetes/cloud-native feeds, deduplicates by URL, ranks by operational
signal, tags items, persists sent URLs, and prints a compact French Markdown digest.
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
import textwrap
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

FEEDS = [
    {
        "name": "Kubernetes Blog",
        "url": "https://kubernetes.io/feed.xml",
        "source_tag": "kubernetes",
    },
    {
        "name": "CNCF Blog",
        "url": "https://www.cncf.io/feed/",
        "source_tag": "cncf",
    },
    {
        "name": "LWKD",
        "url": "https://lwkd.info/feed.xml",
        "source_tag": "lwkd",
    },
    {
        "name": "Kubernetes Releases",
        "url": "https://github.com/kubernetes/kubernetes/releases.atom",
        "source_tag": "release",
    },
    {
        "name": "The New Stack Kubernetes",
        "url": "https://thenewstack.io/category/kubernetes/feed/",
        "source_tag": "industry",
    },
]

DEFAULT_STATE = Path(os.environ.get("KUBE_DIGEST_STATE", "/opt/data/hermes-home/cache/kubernetes_digest_state.json"))
USER_AGENT = "Hermes Kubernetes Digest/1.0 (+https://github.com/coutug/hermes-home)"
NOW = dt.datetime.now(dt.timezone.utc)

TAG_RULES = [
    # Keep security narrow: generic release posts mention "security" often.
    ("security", r"\b(cve-?\d{4}|vulnerab|exploit|security advisory|critical severity|high severity|runc|containerd)\b"),
    ("release", r"\b(release|v?\d+\.\d+\.\d+|changelog|version|upgrade|stable|ga\b)\b"),
    ("networking", r"\b(network|sig network|cilium|envoy|ingress|gateway api|service mesh|dns|ebpf)\b"),
    ("storage", r"\b(storage|sig storage|csi|volume|persistentvolume|snapshot|selinuxmount)\b"),
    ("observability", r"\b(observability|prometheus|grafana|opentelemetry|logging|metrics|tracing)\b"),
    ("platform", r"\b(platform|cluster|node|scheduler|operator|controller|autoscal|multi-cluster|feature gate)\b"),
    ("tooling", r"\b(helm|argo|flux|kubectl|kustomize|kyverno|gatekeeper|istio|knative)\b"),
]

PRIORITY = {
    "security": 100,
    "release": 80,
    "platform": 55,
    "networking": 50,
    "storage": 45,
    "observability": 40,
    "tooling": 30,
    "cncf": 20,
    "industry": 10,
    "lwkd": 35,
    "kubernetes": 30,
}

NOISE_PATTERNS = [
    r"\bwebinar\b",
    r"\bsponsored\b",
    r"\bregister now\b",
    r"\bdiscount\b",
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


def short_sentence(text: str, title: str = "", limit: int = 190) -> str:
    text = strip_html(text)
    # RSS feeds often expose byline as first sentence; skip weak summaries.
    if not text or re.fullmatch(r"(Editors?|Authors?):?\s+.+", text, re.I):
        return infer_impact(title)
    first = re.split(r"(?<=[.!?])\s+", text)[0]
    first = first.strip()
    if re.fullmatch(r"(Editors?|Authors?):?\s+.+", first, re.I) and len(re.split(r"(?<=[.!?])\s+", text)) > 1:
        first = re.split(r"(?<=[.!?])\s+", text)[1].strip()
    if len(first) > limit:
        first = first[: limit - 1].rsplit(" ", 1)[0] + "…"
    return first or infer_impact(title)


def infer_impact(title: str) -> str:
    lower = title.lower()
    if re.search(r"cve|vulnerab|security advisory|exploit", lower):
        return "Signal sécurité: vérifier exposition et versions affectées."
    if re.search(r"v?\d+\.\d+\.\d+|release|changelog", lower):
        return "Signal release: vérifier changelog, upgrade notes et compatibilité."
    if "gateway api" in lower or "network" in lower:
        return "Signal réseau: vérifier impact ingress/service mesh/Gateway API."
    if "selinux" in lower or "volume" in lower or "storage" in lower:
        return "Signal storage/runtime: vérifier feature gates et changements de comportement."
    return "Signal Kubernetes/cloud-native à évaluer."


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
    # Keep stable order by priority, then name.
    return sorted(set(tags), key=lambda t: (-PRIORITY.get(t, 0), t))


def score_item(title: str, summary: str, tags: Iterable[str]) -> int:
    hay = f"{title} {summary}".lower()
    score = sum(PRIORITY.get(t, 0) for t in set(tags))
    if re.search(r"\b(cve-\d{4}|critical|high severity|security advisory)\b", hay):
        score += 60
    if re.search(r"\b(deprecat|breaking|removed|upgrade required|must upgrade)\b", hay):
        score += 35
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
    # namespace agnostic fallback
    wanted = {n.split('}')[-1] for n in names}
    for child in elem.iter():
        if child is elem:
            continue
        if child.tag.split('}')[-1] in wanted and child.text:
            return child.text.strip()
    return ""


def first_link(elem: ET.Element) -> str:
    # RSS <link>text</link>
    link = first_text(elem, ["link"])
    if link:
        return link
    # Atom <link href="...">
    for child in elem.iter():
        if child.tag.split('}')[-1] == "link":
            href = child.attrib.get("href")
            rel = child.attrib.get("rel", "alternate")
            if href and rel in ("alternate", ""):
                return href
    return ""


def parse_feed(feed: dict) -> list[Item]:
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
        items.append(Item(
            title=title,
            url=url,
            source=feed["name"],
            published=published,
            summary=compact_summary,
            tags=tags,
            score=score,
            key=item_key(url, title),
        ))
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
    # prune sent older than 180 days
    cutoff = (NOW - dt.timedelta(days=180)).date().isoformat()
    state["sent"] = {k: v for k, v in state.get("sent", {}).items() if str(v.get("sent_at", "9999")) >= cutoff}
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))


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
        if item.key in unique:
            if item.score > unique[item.key].score:
                unique[item.key] = item
        else:
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
    picked = candidates[:max_items]

    if bootstrap_silent and not sent:
        # Seed all currently visible feed entries as sent, no digest spam.
        for item in unique.values():
            sent[item.key] = {"url": item.url, "title": item.title, "sent_at": NOW.date().isoformat()}
        picked = []

    if not dry_run:
        # Mark every current candidate as seen, not only picked. Prevent backlog spam
        # where lower-priority old items leak into later digests.
        for item in candidates:
            sent[item.key] = {
                "url": item.url,
                "title": item.title,
                "sent_at": NOW.date().isoformat(),
                "delivered": item.key in {picked_item.key for picked_item in picked},
            }
        state.setdefault("runs", []).append({
            "ran_at": NOW.isoformat(),
            "picked": len(picked),
            "candidates_seen": len(candidates),
            "errors": errors,
        })
        state["runs"] = state["runs"][-30:]
        save_state(state_path, state)

    meta = {
        "fetched": len(all_items),
        "unique": len(unique),
        "new_candidates": len(candidates),
        "picked": len(picked),
        "errors": errors,
        "dry_run": dry_run,
        "state_path": str(state_path),
    }
    return picked, meta


def render(items: list[Item], meta: dict, weekly: bool = False) -> str:
    today = NOW.astimezone().date().isoformat()
    title = "Veille Kubernetes — Top 5 semaine" if weekly else "Veille Kubernetes — digest 3x/semaine"
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

    must_read = [i for i in items if i.score >= 80]
    optional = [i for i in items if i.score < 80]
    if not must_read:
        must_read, optional = items[: min(3, len(items))], items[min(3, len(items)):]

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

    action = next((i for i in items if "security" in i.tags), None) or next((i for i in items if "release" in i.tags), None) or items[0]
    lines.append("")
    lines.append("### Top action")
    if "security" in action.tags:
        lines.append(f"- Vérifier exposition/versions liées à: {action.title}")
    elif "release" in action.tags:
        lines.append(f"- Vérifier notes d'upgrade/changelog: {action.title}")
    else:
        lines.append(f"- Scanner pertinence pour notre stack: {action.title}")

    if meta.get("errors"):
        lines.append("")
        lines.append("### Sources avec erreur")
        for err in meta["errors"]:
            lines.append(f"- {err['source']}: `{err['error']}`")

    lines.append("")
    lines.append(f"_Dédupe active. Sources lues: {meta.get('fetched', 0)} items, {meta.get('unique', 0)} uniques, {meta.get('new_candidates', 0)} nouveaux._")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate Kubernetes RSS digest Markdown")
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
