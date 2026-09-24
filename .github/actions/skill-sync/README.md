# skill-sync-action

Vendors skills from a registry repo into Agent Plugins 1.0 packages, so each plugin stays
self-contained for harnesses (a plain git checkout) while the registry stays the source of truth.

Only works on a plugin that already conforms to the
[Agent Plugins](https://github.com/agentplugins/agent-plugins-spec) 1.0 (and, once released,
1.1) format — this isn't a standalone tool, it reads and writes one `extensions` entry (see
[§8](https://github.com/agentplugins/agent-plugins-spec/blob/main/spec/1.0.0.md#8-client-extensions))
in an otherwise ordinary `plugin.json`.

## Example

`plugins/hello-plugin/plugin.json` in this repo:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "hello-plugin",
  "version": "1.0.0",
  "extensions": {
    "com.akerbp.skillreposync": {
      "skills": {
        "demoskill": {
          "repo": "ETroll/temp-skill-repo",
          "source": "demoskill",
          "dest": "skills/greet",
          "ref": "3d182e4"
        }
      }
    }
  }
}
```

`sync` mode fetches `demoskill/` at that commit from `ETroll/temp-skill-repo`, writes it to
`plugins/hello-plugin/skills/greet/`, records it in
`plugins/hello-plugin/com.akerbp.skillreposync/skills.lock.json`, and bumps `version` to `1.0.1`.
`check` mode later confirms `skills/greet/` still matches that lock entry byte for byte.

## Reference fields

| Field | Required | Notes |
| --- | --- | --- |
| `repo` | yes | `owner/repo` |
| `source` (alias `path`) | yes | directory in the registry |
| `dest` | no | relative to the plugin root, defaults to `skills/<key>` |
| `ref` | yes | commit SHA, 7–40 hex chars — no branches or tags |

A `dest` directly under `skills/` is loaded as a skill by the harness: it needs a `SKILL.md`
(matched case-insensitively, always normalized to that exact name on copy — see "gotchas" in
`DEVELOPING.md`), and if that file declares `name`, it must match the dest folder. A `dest`
outside `skills/` works too, for shared content that isn't itself a skill.

## Rules

- `ref` is always a commit SHA; abbreviated is fine and gets expanded to the full 40 chars on
  sync by default (`expand-refs`).
- `source`/`dest` are relative paths, no `..`. `dest` can't be `skills` itself, a reserved file,
  or nested inside another reference's `dest` in the same plugin.
- `dest` belongs to the reference — sync overwrites whatever's there, including a local working
  copy a developer committed. Drop the reference to keep a plugin-own skill under that name.
- Vendored content can't contain symlinks; sync rejects the whole reference if it does.
- A commit with more than one case-variant of `SKILL.md` in the same directory (e.g. both
  `SKILL.md` and `skill.md`) is rejected as ambiguous.
- `version` must be `MAJOR.MINOR.PATCH` for the auto-bump, or set `bump-version: "false"`.

## Wiring it up

Copy `action.yml` + `sync_skills.py` to `.github/actions/skill-sync/` in the marketplace repo,
and `examples/workflows/sync-and-check-skills.yml` to `.github/workflows/`.

That workflow runs two jobs on `pull_request`:

- **sync** fetches, vendors, and commits back to the PR branch.
- **check** (`needs: sync`) re-verifies the pushed commit — offline, no registry calls — and is
  the required status check on `main`.

They're one workflow, not two independently-triggered ones, because `check` would otherwise
routinely fail on a fresh push before `sync` had caught up.

### Auth

- **One GitHub App**, if `SKILL_REGISTRY_APP_ID`/`_KEY` are set. Installing an App is a one-time
  choice each repo's owner makes — no per-side split needed *when both repos share an owner*
  (`SKILL_REGISTRY_APP_REPOS` lists both). A token from one `create-github-app-token` call is
  scoped to one owner's installation though, so if the registry is under a different owner, set
  `SKILL_REGISTRY_APP_OWNER`/`_OWNER_REPOS` too — that adds a second call, same App, targeting
  that owner instead.
- Otherwise, **two PATs**: `MARKETPLACE_PAT` (write, this repo) and `REGISTRY_PAT` (read, the
  registry) — a PAT belongs to whoever created it, not to an owner, so the two sides more often
  need separate ones regardless of org layout.

Either way, never `GITHUB_TOKEN`: its pushes don't trigger workflows, which the
`check`-waits-for-`sync` design needs.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `mode` | `sync` | `sync` or `check` |
| `plugins-glob` | `plugins/*/plugin.json` | Which manifests to process |
| `registry-token` | `${{ github.token }}` | Token with read access to the registry |
| `namespace` | `com.akerbp.skillreposync` | Extension namespace holding the skill references |
| `bump-version` | `true` | Bump patch version on change |
| `expand-refs` | `true` | Rewrite abbreviated SHAs to full |
| `github-server` | `https://github.com` | For GitHub Enterprise Server |

## Outputs

| Output | Description |
| --- | --- |
| `changed` | `true` when sync modified at least one plugin |
| `changed-plugins` | Newline-separated plugin directories that were modified |

## Local testing

```bash
SKILL_SYNC_MODE=check python3 sync_skills.py
REGISTRY_TOKEN=<token> SKILL_SYNC_MODE=sync python3 sync_skills.py
```

For a fake registry (no GitHub access needed) and the full recipe, see `DEVELOPING.md`.

## Not yet built

Scheduled "propose updates": open one PR per plugin when the registry has newer commits for a
locked path. The lock file already has everything needed (`repo`, `source`, `ref`).
