---
name: hermes-home-git-maintenance
description: "Maintain the user's /opt/data/hermes-home configuration repo safely: inspect config/skill changes, respect explicit permission for new skills, avoid secrets, commit/push every configuration change."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, config, git, skills, safety]
---

# Hermes Home Git Maintenance

Use when the user asks to update the Hermes config repo, change Hermes configuration, add/remove skills, or sync `/opt/data/hermes-home` with GitHub.

## Rules

1. Never install/create new skills unless the user explicitly permits that specific skill/action.
2. Every configuration change must be reflected in the Git repo with a commit and push.
3. Never commit secrets or runtime state: `.env`, `auth.json`, `state.db*`, `sessions/`, `logs/`, `memories/`, `gateway_state.json`, `gateway.pid`, `gateway.lock`, caches.
4. Use `HERMES_HOME_GH_TOKEN` from environment only for GitHub auth; never write or print its value.
5. Before committing, inspect `git status --short --untracked-files=all` and stage only intended files.

## Standard workflow

```bash
cd /opt/data/hermes-home
git status --short --untracked-files=all
git diff -- <intended-files>
```

If untracked skill files appear without explicit permission, remove them or leave unstaged. If user asked to remove skills, stage deletions.

Verify ignored secrets/runtime files:

```bash
git check-ignore -v .env auth.json state.db 2>/dev/null || true
```

Commit and push with askpass:

```bash
cd /opt/data/hermes-home
git add <intended-files>
git commit -m "chore: concise message"
askpass=$(mktemp)
cat > "$askpass" <<'EOF'
#!/bin/sh
case "$1" in
  *Username*) printf "%s\n" "x-access-token" ;;
  *Password*) printf "%s\n" "$HERMES_HOME_GH_TOKEN" ;;
  *) printf "%s\n" "" ;;
esac
EOF
chmod 700 "$askpass"
GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 git push origin main
rm -f "$askpass"
git status --short --untracked-files=all
```

## Verification

- Config syntax: `HERMES_HOME=/opt/data/hermes-home /opt/hermes/.venv/bin/hermes config check`
- Skill available: `skill_view(<name>)` after creation/update.
- Repo clean after push, except intentionally ignored runtime files.

## Pitfalls learned

- Hermes may recreate runtime directories/files after cleanup; `.gitignore` must cover them.
- Skill creation is itself a repo/config change and needs user permission plus commit/push.
- An untracked skill directory can appear from prior experimentation; do not commit it unless explicitly permitted.