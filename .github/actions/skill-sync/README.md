# skill-sync-action

Copies pinned skills from the Aker BP skill registry into Agent Plugins 1.0 packages, so that
each plugin in the marketplace is self-contained from the harness's point of view while the
source of truth for shared skills stays in the registry.

## How it works

The marketplace repository contains a real copy of every skill a plugin uses, because harnesses
fetch plugins with a plain git checkout. The registry is the source of truth for shared skills;
this action keeps the copies in the marketplace in sync with it, inside the pull request where
a plugin is added or changed.

1. Each `plugin.json` declares the registry skills it needs under an Aker BP extension
   namespace. This is the mechanism the Agent Plugin Specification provides for client- or
   organisation-specific data: harnesses ignore namespaces they do not implement, and the
   manifest stays conformant.

   ```json
   {
     "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
     "name": "expres-reservoir",
     "version": "1.0.0",
     "extensions": {
       "com.akerbp.marketplace": {
         "skills": {
           "tnav": {
             "repo": "ETroll/skill-registry",
             "source": "skills/expres/tnav",
             "dest": "skills/tnav",
             "ref": "d58c4715e41760a041dbd19c54474dafafbca79b"
           }
         }
       }
     }
   }
   ```

   `source` is a directory in the registry; `dest` is where it lands, relative to the plugin
   root, and defaults to `skills/<key>` when omitted. The two do not need to share a folder
   structure. A `dest` outside `skills/` (for example `references/glossary`) is allowed for
   shared content that is not itself a skill.

2. A developer opens a pull request with the plugin as a whole: manifest, `mcp.json`, plugin-own
   skills, and, if they like, a local working copy of the registry skill they developed and
   tested against. Nothing about the PR is special.

3. On every push to the PR, the `sync` job runs the action in `sync` mode. It fetches each
   referenced commit from the registry, extracts the referenced path, validates it (has
   `SKILL.md`, no symlinks, frontmatter `name` matches the key), and replaces `skills/<name>/`
   in the plugin with that content. A local copy the developer committed is overwritten, and
   the action reports if the two differed. It writes a lock file to
   `<plugin>/com.akerbp.marketplace/skills.lock.json` (repo, path, ref, content hash), bumps
   the patch version in `plugin.json` when vendored content changed, and commits the result
   back to the PR branch.

4. The `check` job runs in `check` mode after `sync` completes (`needs: sync`, `if: always()`),
   re-checking out the branch so it sees the commit `sync` just pushed rather than the tree as
   it was when the workflow was triggered. It is the required status check on `main`, and it
   passes only when every referenced skill is present, at the pinned commit, and byte-identical
   to the registry. A hand edit pushed after the sync therefore fails the check, and the next
   sync run replaces it anyway.

   `check` is not redundant with `sync` succeeding, and it is not a "does the source exist"
   sanity check either. `sync` is a side-effecting, network-dependent step (fetch, write,
   commit, push) that can succeed without anyone reviewing the result. `check` is read-only and
   idempotent: it never contacts the registry, it only compares the committed manifest, lock
   file, and vendored files against each other. That makes it safe to trust as the merge gate
   even when `sync` silently misbehaves — a push that failed, a hand-edit applied after sync
   ran, or a manifest `ref` changed without a corresponding sync.

   `sync` and `check` used to be two workflows independently triggered by the same
   `pull_request` event. `check` would routinely run and fail before `sync` had a chance to
   fetch and push — a false "not synced yet" failure on every fresh push, since it read the
   tree from before sync fixed it. Chaining them into one workflow's two jobs removes that race.

5. The reviewer approves the PR as a complete package. `main` is branch-protected, so this is
   the only way a skill copy reaches the marketplace.

## Rules enforced

- `ref` must be a commit SHA. An abbreviated SHA (7 characters or more, as shown in the GitHub
  UI) is accepted: the sync resolves it through the GitHub API and, by default, writes the full
  40-character SHA back into `plugin.json` so the merged manifest is unambiguous. Branch names
  and tags are rejected.
- `source` and `dest` must be relative directory paths without `..`. `dest` may not be
  `skills` itself, a reserved package file, or inside the extension directory, and no two
  destinations in one plugin may nest inside each other.
- A `dest` directly under `skills/` is loaded as a skill by the harness, so it must contain a
  `SKILL.md`, and if that file declares a `name` it must equal the destination folder name.
  Looked up case-insensitively in the registry (`skill.md`, `Skill.md`, etc. are all accepted --
  Windows contributors can't cleanly rename a file to change only its case), but always
  normalized to exactly `SKILL.md` in the vendored copy, since harness discovery requires that
  exact name. A commit with more than one case-variant of the file in the same directory is
  rejected as ambiguous.
- `dest` is owned by the reference: whatever is there is replaced by the registry version. To
  keep a plugin-own skill under that name, drop the reference.
- Removing a reference, or changing its `dest`, removes the old copy on the next sync.
- `version` must be `MAJOR.MINOR.PATCH` for the bump to work. Set `bump-version: "false"`
  to manage versions manually.

## Inputs

| Input            | Default                     | Description                                             |
| ---------------- | --------------------------- | ------------------------------------------------------- |
| `mode`           | `sync`                      | `sync` or `check`                                       |
| `plugins-glob`   | `plugins/*/plugin.json`     | Which manifests to process                              |
| `registry-token` | `${{ github.token }}`       | Token with read access to the registry repository       |
| `namespace`      | `com.akerbp.marketplace`    | Extension namespace holding the skill references        |
| `bump-version`   | `true`                      | Bump patch version on change                            |
| `expand-refs`    | `true`                      | Rewrite abbreviated SHAs in `plugin.json` to full SHAs  |
| `github-server`  | `https://github.com`        | For GitHub Enterprise Server installations              |

## Outputs

| Output            | Description                                             |
| ----------------- | ------------------------------------------------------- |
| `changed`         | `true` when sync modified at least one plugin           |
| `changed-plugins` | Newline-separated plugin directories that were modified |

## Wiring it into the marketplace

See `examples/workflows/sync-and-check-skills.yml`: one workflow, two sequential jobs, on
`pull_request` touching `plugins/**`.

- `sync` fetches, writes, and commits back to the PR branch. It checks out and pushes with a
  token stored in the `REGISTRY_PAT` repository secret rather than `GITHUB_TOKEN`, for two
  reasons: that token can read the registry repository, and a push made with it triggers
  workflows, so a fresh run of this workflow happens against the commit `sync` just created.
  `GITHUB_TOKEN` pushes deliberately do not trigger workflows. Skipped on fork PRs, since the
  workflow cannot push to them; the marketplace is internal, so branches should live in the
  repository.
- `check` (`needs: sync`, `if: always()`) re-checks out the branch — by name, so it picks up the
  commit `sync` just pushed — and verifies the final state. Configure the `check` job as the
  required status check in branch protection for `main`. It still runs, and still needs to pass,
  on fork PRs where `sync` was skipped.

The sync commits as an append by default; set `COMMIT_MODE: amend` in the workflow to fold the
sync into the PR's last commit with a force-push instead.

### Authenticating `sync`: PAT vs. GitHub App

`sync` needs a token that can (a) read the registry repository and (b) push to the marketplace
in a way that triggers workflows — `GITHUB_TOKEN` fails (b) by design. Two options:

- **Personal Access Token** (what `examples/workflows/` and this repository currently use).
  Fine-grained recommended: `contents:write` on the marketplace repo, `contents:read` on the
  registry repo. Store it as the `REGISTRY_PAT` repository secret. Use this when nobody on the
  team can register a GitHub App for the target org — the common blocker, since that usually
  needs org-owner permissions.

  Tradeoffs worth planning for: a fine-grained PAT can be set to expire (and many orgs enforce a
  maximum lifetime, commonly one year, via policy) — if it does, it needs manual rotation before
  it lapses. It is also issued under a personal GitHub account, so the automation breaks if that
  account loses access to either repo. Consider a dedicated service/bot account to own the token
  rather than an individual's account, if your org allows provisioning one.

- **GitHub App**. Prefer this if you can register one: it is an org-owned identity, independent
  of any individual's account, and does not expire on a PAT's schedule. Install it on both the
  marketplace and registry repos with `contents: write` / `contents: read` respectively, then
  swap the checkout/`registry-token` steps for `actions/create-github-app-token@v1` (app ID +
  private key as an org/repo variable and secret) in place of `secrets.REGISTRY_PAT`.

Either way, the rest of the design — the two-job structure, `needs`/`if: always()` sequencing,
and why `check` matters independently of `sync` — is unaffected; only how `sync` authenticates
changes.

## Running locally

```
SKILL_SYNC_MODE=check python3 sync_skills.py
REGISTRY_TOKEN=<token> SKILL_SYNC_MODE=sync python3 sync_skills.py
```

The script uses only the Python standard library plus `git` and `tar`.

## Not yet included

A scheduled "propose updates" mode that opens one pull request per plugin when the registry
has newer commits on `main` for a referenced path. The lock file already carries everything
needed for that (repo, path, ref), so it is a small addition to this script.
