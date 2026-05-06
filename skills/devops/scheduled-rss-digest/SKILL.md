---
name: scheduled-rss-digest
description: "Build and operate scheduled RSS/Atom digest jobs in Hermes: fetch feeds, dedupe/cache URLs, rank/tag items, render concise Markdown, test with cronjob, and commit repo changes safely."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [rss, cron, digest, automation, discord, hermes]
---

# Scheduled RSS Digest

Use when user wants recurring information digest from RSS/Atom feeds delivered to Discord/Telegram/etc. via Hermes cron.

## Approach

1. Confirm destination and cadence if not obvious.
   - If current chat/thread is target, use `deliver='origin'` or omit `deliver`.
   - For named channel/person, list send targets first.
2. Put reusable script under `/opt/data/hermes-home/scripts/<topic>_digest.py`.
   - Cron `script` paths are relative to `~/hermes-home/scripts/`.
   - Script stdout becomes context injected into future cron run prompt.
3. Script should do the boring parts deterministically:
   - fetch RSS/Atom with stdlib or existing approved tool;
   - parse RSS `<item>` and Atom `<entry>`;
   - dedupe by stable key: normalized URL preferred, fallback title hash;
   - persist sent/seen cache under ignored runtime path, e.g. `/opt/data/hermes-home/cache/<topic>_digest_state.json`;
   - tag items by keyword rules;
   - score/rank high-signal items;
   - cap output count;
   - render final Markdown digest.
4. Cron prompt should be narrow:
   - return exactly script digest if it starts with expected heading;
   - do not invent extra items;
   - do not ask questions;
   - deliver “no new items” message if script says so.
5. Create cron:
   ```python
   cronjob(action='create', name='<topic> digest', schedule='0 8 * * 1,3,5', deliver='origin', script='<topic>_digest.py', prompt='<self-contained prompt>')
   ```

## Product Rules

Good digest format:

```md
## <Topic> — digest — YYYY-MM-DD

### À lire maintenant
- [tag1, tag2] [Title](URL)
  Impact: one sentence.

### Bruit faible / optionnel
- [tag] [Title](URL)

### Top action
- concrete next check.

_Dédupe active. Sources lues: N items, U uniques, C nouveaux._
```

Use source-specific tags plus topic tags. Keep max 5–8 items unless user asks for more.

## Testing

Run while implementing:

```bash
python3 -m py_compile scripts/<topic>_digest.py
scripts/<topic>_digest.py --dry-run --json --max-items 3
scripts/<topic>_digest.py --dry-run --max-items 5
```

Verify cache behavior with temp state:

```bash
tmp_state=$(mktemp) && rm -f "$tmp_state"
scripts/<topic>_digest.py --state "$tmp_state" --max-items 3 >/tmp/first.md
scripts/<topic>_digest.py --state "$tmp_state" --max-items 8 >/tmp/second.md
cat /tmp/second.md
rm -f "$tmp_state"
```

Expected second output: no backlog spam if no new items.

After cron create, run once and verify:

```python
cronjob(action='run', job_id='<job_id>')
cronjob(action='list')  # last_status should become ok, last_delivery_error null
```

## Pitfalls Learned

- Feed summaries often start with bylines like `Editors: ...`; strip/skip them or infer impact from title.
- Broad keyword `security` causes false positives because release notes mention security often. Prefer narrow security patterns: `CVE`, `vulnerab`, `exploit`, `security advisory`, severity words.
- Tag/score from final compact summary can reduce false positives versus raw full feed content.
- Mark all current candidates as `seen`, not only delivered/picked, or old lower-priority backlog will drip-feed forever.
- `--max-items 0` can be useful to sync/seed state after manual cron run if script logic changed.
- Do not commit runtime state (`cache/*.json`) or cron outputs. Add ignores for `cron/output/`, `__pycache__/`, `*.py[cod]` if missing.
- Cron run is async-ish: `cronjob(action='run')` may schedule immediate run; call `cronjob(action='list')` after current time/short wait to verify `last_status`.
- If `last_error` is `RuntimeError: Codex Responses request 'model' must be a non-empty string.`, update the job to pin both provider and model, e.g. `cronjob(action='update', job_id='<id>', model={'provider':'openai-codex','model':'gpt-5.5'})`, then run it once and verify `last_status: ok`.
- Avoid verifying cron delivery with local `hermes cron tick` when messaging credentials are only available in the platform/API runtime. It can execute the job but set `last_delivery_error: "platform 'discord' not configured/enabled"`. Prefer `cronjob(action='run')` + `cronjob(action='list')`, or direct `send_message` test to raw channel ID for Discord target reachability. If a local tick mutates `cron/jobs.json` with a false delivery error, revert runtime cron metadata before committing.
- If user asks to move delivery to a named Discord channel, call `send_message(action='list')` first. If channel is absent but user provides raw channel ID, update cron with `deliver='discord:<channel_id>'`.
- After delivery target change, run cron once and verify `deliver` value plus `last_delivery_error: null`. Commit `cron/jobs.json` target change.
- When one source dominates ranked output (e.g. OpenAI feed with many high-score AI posts), add a diverse picker with a per-source cap (usually max 3/source) before filling remaining slots. This keeps digests useful across all chosen sources.
- For new digest topics, verify candidate feeds with live HTTP/XML checks before coding. Good source set = authoritative/vendor primary sources + research/open-source source; avoid only industry-news feeds unless user wants market noise.
- Initial AI digest source set that worked: OpenAI News `https://openai.com/news/rss.xml`, Google AI Blog `https://blog.google/technology/ai/rss/`, Google DeepMind `https://deepmind.google/blog/rss.xml`, MIT News AI `https://news.mit.edu/topic/mitartificial-intelligence2-rss.xml`, Hugging Face Blog `https://huggingface.co/blog/feed.xml`.
- User-preferred AI digest source set from later refinement: Google AI Blog `https://blog.google/technology/ai/rss/`, Google DeepMind `https://deepmind.google/blog/rss.xml`, MIT News AI `https://news.mit.edu/topic/mitartificial-intelligence2-rss.xml`, Hugging Face Blog `https://huggingface.co/blog/feed.xml`, Import AI `https://jack-clark.net/feed/`, The Decoder `https://the-decoder.com/feed/`, and The Batch homepage `https://www.deeplearning.ai/the-batch/`.
- The Batch/DeepLearning.AI had no working RSS in tests (`/the-batch/feed/`, `/feed/`, `/blog/feed/`, `/the-batch/rss/`, `/rss/` all 404). Reusable fallback: scrape public `/the-batch/` page for `href` values containing `the-batch`, ignore `/tag/`, `/page/`, `/author/`, `/category/`, `/about/`, then build titles from slugs (`issue-351` → `The Batch Issue 351`). Verify scrape count and output quality; exclude nav/tag/date links.
- For reliable The Batch summaries, fetch each article page. Use JSON-LD `application/ld+json` Article fields for `headline` and `datePublished`; fall back to OpenGraph/meta description. Strip scripts/styles/nav/footer, extract `<p>` text, skip boilerplate (`subscribe`, `share`, `community forum`, `new course`, `reading time`, etc.), then prefer the first concrete news paragraph matching starts like `What’s new`, `The latest`, `A new`, `Researchers`, or `<Org> released/introduced`. If none, fall back to paragraphs containing `why it matters`, `security implications`, `benchmark`, `data center`, `emissions`, `open weights`, `regulatory`, `frontier model`, then metadata description. Add ISO-8601 date parsing with `datetime.fromisoformat(value.replace("Z", "+00:00"))` because JSON-LD dates may include milliseconds and timezone offsets (e.g. `2026-05-01T09:46:57.000-07:00`). Watch Python regex quoting: prefer double-quoted raw strings when regex char classes include both `"` and `'`.
- For local/regional news digests, verify feeds in a small standalone script first and drop unstable feeds before cron creation. Québec/Canada working feeds in testing: Radio-Canada Québec `https://ici.radio-canada.ca/rss/4159`, Radio-Canada Montréal `https://ici.radio-canada.ca/rss/1000524`, La Presse Actualités `https://www.lapresse.ca/actualites/rss`, La Presse Politique `https://www.lapresse.ca/actualites/politique/rss`, Noovo Info `https://www.noovo.info/arc/outboundfeeds/rss/`, Journal de Montréal `https://www.journaldemontreal.com/rss.xml`, Global News Montréal `https://globalnews.ca/montreal/feed/`, CityNews Montréal `https://montreal.citynews.ca/feed/`, National Post Canada `https://nationalpost.com/category/news/canada/feed/`. User-refined Québec/Canada source set: Radio-Canada Québec, La Presse Actualités, La Presse Politique, Noovo Info, Journal de Montréal, Global News Montréal, CityNews Montréal, National Post Canada.
- Québec/Canada feeds rejected during testing: Radio-Canada Politique `404`, Le Devoir tested RSS URLs `404`, Noovo `/rss.xml` `404` but Arc outbound feed works, TVA RSS parsed invalid XML/redirect loop, CTV Montréal RSS `404`, Montreal Gazette local category `404` though site-level feed works, The Canadian Press `429`, Government Québec RSS `404`, Assemblée nationale RSS timeout, CBC Montréal/Politics can pass quick checks but timed out repeatedly in full digest runs; leave as optional sources until stable.
- For news digests, canonicalize URLs before hashing: strip `utm_*` query params and trailing slash path. Some publishers (La Presse) duplicate same article across category feeds with different UTM params; without canonicalization duplicates appear.
- For regional news relevance, do not rely on URL text alone because every URL may contain the region slug. Filter relevance using title + summary keywords only, then allow explicitly broad feeds (e.g. national politics) if desired.
- If a source repeatedly times out only during full digest but quick one-off HTTP check succeeds (CBC case), either add retries/per-source timeouts or drop it from initial cron; stable no-error cron is better than theoretically broader coverage.

## Repo Hygiene

When adding scripts/jobs under `/opt/data/hermes-home`, load/follow `hermes-home-git-maintenance`:

```bash
cd /opt/data/hermes-home
git status --short --untracked-files=all
HERMES_HOME=/opt/data/hermes-home /opt/hermes/.venv/bin/hermes config check
git add .gitignore cron/jobs.json scripts/<topic>_digest.py skills/devops/scheduled-rss-digest/SKILL.md
git commit -m "chore: add <topic> digest cron"
# push using HERMES_HOME_GH_TOKEN askpass
```

Never stage caches, logs, credentials, or cron output files.
