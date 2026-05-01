# hermes-home

This repository stores the non-secret Hermes home configuration for this environment and is the canonical source of truth.

Set `HERMES_HOME=/opt/data/hermes-home` so Hermes reads configuration directly from this repository.

Legacy paths such as `/opt/data/config.yaml`, `/opt/data/.env`, and `/opt/data/auth.json` were previously used during migration, but the repo is now the canonical source of truth.

## Contents

- `config.yaml` — primary Hermes configuration
- `SOUL.md` — base personality/system persona override
- `.env.example` — names of environment variables used by this setup
- `.gitignore` — excludes secrets, runtime data, and editor noise
- `profiles/home/config.yaml` — home profile overrides
- `skills/` — tracked skill tree available to Hermes
- `profiles/` — profile-specific configuration stubs

## Runtime-only files present locally but ignored by Git

These may physically exist under `/opt/data/hermes-home` when Hermes runs, but they are intentionally not published:

- `.env`
- `auth.json`
- `auth.lock`
- `sessions/`
- `logs/`
- `memories/`
- `cache/`
- `state.db*`
- platform state such as `channel_directory.json`, `gateway_state.json`, and `discord_threads.json`

## Rules

- Do not commit secrets, tokens, session data, or logs.
- Store credentials outside this repository, for example in `~/.hermes/.env`.
- If a variable is needed at runtime, document it in `.env.example` without values.
- Commit each meaningful configuration change so diffs stay easy to review.
