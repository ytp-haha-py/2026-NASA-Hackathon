# GitHub setup and collaboration state

This document records what was actually applied to the repository and what still requires a
maintainer/admin action. It is not a claim that protections exist unless verified below.

## Bootstrap exception

The repository was empty (no commits, no default branch) at project start. The maintainer
explicitly authorized a one-time initial bootstrap push to `main`. After that push, the repository
policy is:

`Issue -> feat/<issue>-short-name or fix/<issue>-short-name -> PR -> review -> required CI -> merge`

No further direct pushes to `main`, no force pushes, no auto-merge, and no self-approval.

## Bootstrap record

- Tracking Issue: [#1](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/1).
- Initial MVP commit: `be4fc4f96bd351b3ea51a9042a02ec393a092995`.
- Branch: `main` (one-time direct bootstrap push explicitly authorized by a maintainer amendment;
  `origin/main` was genuinely empty before this work).
- PR for the bootstrap: none, by that amendment. Every later change requires
  Issue -> branch -> PR -> review -> CI -> merge.
- CI/Actions runs are observed after the push and reported in the handoff; this file is not evidence
  that a run succeeded.
- No secret, large raster, local database, or cache is included in the bootstrap commit.

## Verified GitHub state

Checked with `gh` on 2026-11-14 (repo `ytp-haha-py/2026-NASA-Hackathon`):

- Repository is **private**.
- Authenticated account `moon-jam` has `push` and `triage` permission, but **not** `admin` or
  `maintain` (`gh api repos/... --jq .permissions`).
- GitHub rulesets API returned HTTP 403: *"Upgrade to GitHub Pro or make this repository public to
  enable this feature."* Private repositories on the Free plan cannot use rulesets.
- Branch protection could not be configured with the available token/permissions.
- Squash merge is enabled; merge commits and rebase merge are also enabled.
- `CODEOWNERS` was intentionally **not** added: no independent reviewer/team handle is confirmed,
  and inventing collaborators would be misleading.

**Conclusion: `main` protection is NOT active.** A repository admin must apply it after making the
repository eligible (public, or GitHub Pro/Team) or after granting admin rights.

## Required status checks

The CI workflow generates these exact stable job/check names:

- `backend-tests`
- `frontend-checks`
- `integration-smoke`

`data-validation` (offline snapshot/manifest checks) is path-scoped and should be added as a required
check only after confirming it triggers on the relevant data PRs. `data-smoke` (network) and
`data-publish` (protected environment + R2 secrets) are opt-in/non-blocking for ordinary PRs.

They must be observed at least once (on the bootstrap push or a PR) before being marked required.

## Maintainer steps to activate protection

1. Make the repository public, upgrade the plan, or use an account with admin rights.
2. Settings -> Rules -> New branch ruleset (or Settings -> Branches -> Add branch protection rule).
3. Target `main`.
4. Require a pull request before merging.
   - Required approvals: **1**.
   - Dismiss stale approvals when new commits are pushed when available.
   - Require conversation resolution.
5. Require status checks to pass before merging; add:
   `backend-tests`, `frontend-checks`, `integration-smoke`.
   Optionally add `data-validation` only if the ruleset correctly handles its path filter.
6. Block force pushes and prevent branch deletion.
7. Do not add bypass actors unless strictly necessary; do not grant the author self-approval.
8. Enable squash merging as the preferred merge method (currently enabled) and optionally
   "Automatically delete head branches".
9. Add `CODEOWNERS` only after confirming real collaborator handles. A code owner cannot approve
   their own PR.
10. Verify with:

```bash
gh api repos/ytp-haha-py/2026-NASA-Hackathon/rulesets
gh api repos/ytp-haha-py/2026-NASA-Hackathon/branches/main/protection
```

Record the observed output here after applying.

## Data publication and untrusted PRs

Container publication (`.github/workflows/publish.yml`) is gated on a successful CI run on `main`
(`workflow_run`) or a version tag; it never runs after a failed CI run. Data publication is a
separate manual, protected-environment workflow (`.github/workflows/data-publish.yml`) that runs
trusted `main` code only and requires R2 write credentials. Do not expose those credentials to
pull-request workflows. See `docs/DATA_GOVERNANCE.md` and `docs/R2_SETUP.md`.

## Protected data-publish environment (manual)

The `data-publish` workflow is a template and has never been run. A maintainer must:

1. Settings -> Environments -> New environment `data-publish`.
2. Add required reviewers (at least one maintainer) and restrict deployment branches to `main`.
3. Add environment secrets: `R2_ENDPOINT`, `R2_BUCKET`, `R2_REGION`, `R2_ACCESS_KEY_ID`,
   `R2_SECRET_ACCESS_KEY` (values from the R2 token in `docs/R2_SETUP.md`).
4. Keep the R2 token scoped to the one bucket (Object Read & Write) and rotate it if exposed.
5. Run the workflow manually from `main`, verify uploaded object keys/checksums, then record the
   observed output in `docs/DATA_ARCHITECTURE.md`.

Do not add these secrets to repository-level PR workflows. Do not run the publish workflow on a PR
or an untrusted branch. Until the environment and secrets exist, `data-publish` cannot run and live
R2 remains unverified.

## Reviewer and merge expectations

- Every future PR needs an approval from someone other than the author.
- Maintainers squash-merge only after required checks are green and conversations are resolved.
- If no independent reviewer is available, the maintainer must assign one; do not bypass review.
- The bootstrap push is a one-time exception, not precedent for future changes.

## Notes about template/workflow timing

Issue/PR templates and workflows committed in the bootstrap are active on `main` immediately after
the push. Required-check enforcement still has to be configured by an admin as above; the files
alone do not protect the branch.
