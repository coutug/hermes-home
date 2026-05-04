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
