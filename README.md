# hermes-home

This repository stores the non-secret Hermes home configuration for this environment.

## Contents

- `config.yaml` — primary Hermes configuration
- `.gitignore` — excludes secrets, runtime data, and editor noise
- `skills/` — tracked skill stubs or documentation
- `profiles/` — profile-specific configuration stubs

## Rules

- Do not commit secrets, tokens, session data, or logs.
- Store credentials outside this repository, for example in `~/.hermes/.env`.
- Commit each meaningful configuration change so diffs stay easy to review.
