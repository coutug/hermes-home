# hermes-home

This repository stores the non-secret Hermes home configuration for this environment and is the canonical source of truth.

Legacy paths such as `/opt/data/config.yaml`, `/opt/data/.env`, and `/opt/data/auth.json` are kept as symlinks to files in this repo so Hermes continues to work while this repo remains the single place to edit configuration.

## Contents

- `config.yaml` — primary Hermes configuration
- `.env.example` — names of environment variables used by this setup
- `.gitignore` — excludes secrets, runtime data, and editor noise
- `profiles/home/config.yaml` — home profile overrides
- `skills/` — tracked skill stubs or documentation
- `profiles/` — profile-specific configuration stubs

## Rules

- Do not commit secrets, tokens, session data, or logs.
- Store credentials outside this repository, for example in `~/.hermes/.env`.
- If a variable is needed at runtime, document it in `.env.example` without values.
- Commit each meaningful configuration change so diffs stay easy to review.
