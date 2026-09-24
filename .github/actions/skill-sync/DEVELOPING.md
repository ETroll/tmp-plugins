# Developing skill-sync-action

## Layout

```
action.yml                     composite action: inputs -> env vars -> python3 sync_skills.py
sync_skills.py                 all logic, stdlib + git + tar only, no dependencies to install
README.md                      usage docs
examples/workflows/            reference workflow, copy into the marketplace repo's .github/workflows/
```

## sync_skills.py flow

Per `plugin.json` matched by `SKILL_SYNC_GLOB`, `process_plugin()`:

1. `validate_references` — checks the manifest's extension block, resolves the `path`→`source`
   alias, defaults `dest`, rejects bad names/paths/overlapping `dest`s.
2. Loads `<plugin>/<namespace>/skills.lock.json` if it exists.

Then it branches:

- **sync**: removes the vendored copy for any locked skill that's now dropped or whose `dest`
  moved. For each current reference: `resolve_ref` (short SHA -> full, via the GitHub API, or a
  bare clone for `file://` test servers) -> `fetch_skill` (`git init` + shallow `fetch` by SHA +
  `git archive | tar --strip-components`) -> `assert_no_symlinks` -> `find_skill_md` + case
  normalization -> `content_hash` -> copy if it changed from what's on disk. Then writes the lock
  file, rewrites `ref` to the full SHA in `plugin.json` if `expand-refs`, bumps the patch version
  if anything changed.
- **check**: compares each reference against its lock entry (`repo`/`source`/`dest`/`ref`) and
  `content_hash()` of what's already vendored. Reports drift, touches nothing, makes no network
  call at all. That's load-bearing: check is the required status check on `main`, so it can't
  depend on the registry being reachable or credentials being valid.

## Gotchas found while building this

- **`find_skill_md` must scan the whole directory before deciding.** An early exact-match
  return lets `SKILL.md` and a stray `skill.md` coexist in the same commit undetected — the
  ambiguity check never runs. Registry contributors on Windows can end up with either casing
  (case-insensitive, case-preserving filesystem, awkward to rename case-only), so both need
  handling, but only after confirming there isn't more than one.
- **Workflow `concurrency` belongs on the `sync` job, not the whole workflow.** `sync`'s own
  push re-triggers the workflow. If `check` shares the concurrency group, that retrigger cancels
  the *previous* run's `check` job outright — `needs`/`if: always()` only cover a skipped or
  failed dependency, not an external cancellation.
- **`check`'s checkout has to fetch the branch by name** (`ref: head.ref`), not rely on the
  default `pull_request` merge-ref checkout — otherwise it verifies the tree from before `sync`
  pushed, and fails on every fresh PR push regardless of whether sync worked.
- **`actions/create-github-app-token@v1` has no aggregate `permissions:` input.** It's
  per-resource: `permission-contents: write`, etc. A made-up aggregate key is silently ignored,
  so the token ends up with the installation's full permissions instead of the intended cap.
- **Renaming the `namespace` input orphans the old `<plugin>/<old-namespace>/` directory.**
  `process_plugin` only ever looks at the *current* `NAMESPACE`, so it has no way to know a
  differently-named directory is leftover state from a previous config — it's neither cleaned up
  nor flagged by either mode. Delete it by hand after a namespace rename.

## Testing locally

No test suite yet. Run this from inside `.github/actions/skill-sync/` — it builds a throwaway
registry and marketplace under `/tmp`, mirroring `plugins/hello-plugin` in this repo:

```bash
ACTION_DIR="$PWD"
WORK=$(mktemp -d) && cd "$WORK"

mkdir reg && cd reg && git init -q && mkdir -p demoskill
printf -- '---\nname: greet\n---\n# demo\n' > demoskill/SKILL.md
git add -A && git -c user.email=t@t -c user.name=t commit -qm one
cd .. && git clone -q --bare reg reg.git
mkdir -p srv/ETroll && ln -s "$WORK/reg.git" srv/ETroll/temp-skill-repo.git

FULL_SHA=$(git -C reg rev-parse HEAD)
mkdir -p plugins/hello-plugin
cat > plugins/hello-plugin/plugin.json << EOF
{
  "\$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "hello-plugin",
  "version": "1.0.0",
  "extensions": {
    "com.akerbp.skillreposync": {
      "skills": {
        "demoskill": {
          "repo": "ETroll/temp-skill-repo",
          "source": "demoskill",
          "dest": "skills/greet",
          "ref": "$FULL_SHA"
        }
      }
    }
  }
}
EOF

export SKILL_SYNC_SERVER="file://$WORK/srv" REGISTRY_TOKEN=
SKILL_SYNC_MODE=sync  python3 "$ACTION_DIR/sync_skills.py"
SKILL_SYNC_MODE=check python3 "$ACTION_DIR/sync_skills.py"
```

Worth checking by hand after any change to `process_plugin`: first sync, check passes, a hand
edit fails check, a new ref produces a new copy plus a version bump, re-running sync is a no-op,
removing the last reference cleans up the copy/lock/empty namespace dir, a branch name as `ref`
is rejected, overlapping `dest`s are rejected, a moved `dest` drops the old copy.

Pyright should stay clean on `sync_skills.py`.

## TODO

- A pytest suite around `process_plugin`, `file://` registry as a fixture.
- Validate `plugin.json`/`mcp.json` against the official JSON schemas in `check` mode.
- Scheduled "propose updates" mode — see README.
