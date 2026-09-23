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

3. On every push to the PR, the sync workflow runs the action in `sync` mode. It fetches each
   referenced commit from the registry, extracts the referenced path, validates it (has
   `SKILL.md`, no symlinks, frontmatter `name` matches the key), and replaces `skills/<name>/`
   in the plugin with that content. A local copy the developer committed is overwritten, and
   the action reports if the two differed. It writes a lock file to
   `<plugin>/com.akerbp.marketplace/skills.lock.json` (repo, path, ref, content hash), bumps
   the patch version in `plugin.json` when vendored content changed, and commits the result
   back to the PR branch.

4. The check workflow runs in `check` mode on every PR head, including the commit the sync
   pushed, and is a required status check on `main`. It passes only when every referenced
   skill is present, at the pinned commit, and byte-identical to the registry. A hand edit
   pushed after the sync therefore fails the check, and the next sync run replaces it anyway.

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

See `examples/workflows/`:

- `sync-skills.yml`: on `pull_request` touching `plugins/**`, syncs and commits back to the PR
  branch. It checks out and pushes with a GitHub App token rather than `GITHUB_TOKEN`, for two
  reasons: the App can read the registry repository, and a push made with an App token
  triggers workflows, so the required check runs against the commit the sync created.
  `GITHUB_TOKEN` pushes deliberately do not trigger workflows.
- `check-skills.yml`: on `pull_request`, verifies the final state. Configure it as a required
  status check in branch protection for `main`.

The sync commits as an append by default; set `COMMIT_MODE: amend` in the workflow to fold the
sync into the PR's last commit with a force-push instead. Pull requests from forks are skipped,
since the workflow cannot push to them; the marketplace is internal, so branches should live in
the repository.

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
