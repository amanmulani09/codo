<div align="center">

# 🐾 Codo

### AI code reviews on every pull request

Codo reviews your pull requests the moment they open — catching bugs, security
issues, and risky changes, and leaving clear inline comments right where they
matter. Like having a senior engineer look over every PR, in seconds.

</div>

---

## What Codo does

- **Reviews every PR automatically.** Open a pull request (or push new commits)
  and Codo posts a review within seconds — no button to click.
- **Comments inline, where it counts.** Findings appear as comments on the exact
  lines that need attention, with a suggested fix you can apply in one click.
- **Summarizes the change.** Each PR gets a short walkthrough and a call-out of
  the riskiest parts, so reviewers know where to focus.
- **Shows a status check.** Codo adds a check to the PR so you can see at a glance
  whether it found anything.
- **Signal over noise.** Codo focuses on real problems — correctness bugs,
  security holes, resource leaks, broken error handling — and stays quiet about
  style nitpicks unless you ask for them.

## What a review looks like

When you open a PR, Codo adds:

1. **A summary comment** — a short walkthrough of the change and the main risks.
2. **Inline comments** — one per issue, on the affected line, tagged by severity:
   - 🔴 **Critical** — likely bugs, security issues, data loss
   - 🟡 **Warning** — probable problems worth a second look
   - 🔵 **Nit** — minor suggestions (off by default)

   Where possible, a comment includes a ready-to-apply **suggested fix**.
3. **A status check** — "No issues found" or "N finding(s)".

When you push new commits, Codo re-reviews and updates its comments.

## Getting started

Codo installs as a **GitHub App** — no code changes, no CI setup.

1. **Install Codo** on your account or organization.
2. **Choose the repositories** you want it to review (all repos, or select ones).
3. **Open a pull request.** Codo reviews it automatically and posts its feedback.

That's it. There's nothing to configure to get your first review.

> **Open source is free.** Codo reviews public repositories at no cost — enable it
> on your OSS projects and it just works.

## Configuring Codo (optional)

To tailor Codo to your team, add a file named **`.aicodereview.yaml`** to the
root of your repository. Everything is optional — the defaults are sensible.

```yaml
enabled: true              # set false to pause Codo on this repo
tone: concise              # concise | detailed
review:
  nits: false              # true to also get minor style suggestions
  path_filters:            # extra paths for Codo to ignore
    - "dist/**"
    - "**/*.generated.ts"
severity_threshold: warning  # only post comments at or above this level
                             # (nit | warning | critical)
```

| Setting | What it does | Default |
|---|---|---|
| `enabled` | Turn Codo on or off for this repo | `true` |
| `tone` | How detailed the summaries are | `concise` |
| `review.nits` | Include minor style/nit suggestions | `false` |
| `review.path_filters` | Extra file globs to skip (on top of the built-in ones) | — |
| `severity_threshold` | Hide findings below this severity | `warning` |

Codo already ignores lockfiles, generated bundles, vendored code, and binary
files automatically — so you only need `path_filters` for project-specific paths.

## Plans

| Plan | Best for | What's included |
|---|---|---|
| **Free / OSS** | Public & open-source projects | Unlimited reviews on public repos |
| **Pro** | Teams with private code | Reviews on private repos, configuration, priority processing |
| **Team** | Larger organizations | Everything in Pro, higher volume, org-wide controls |

Manage your subscription through the GitHub Marketplace listing or your Codo
billing page.

## Frequently asked questions

**Does Codo change my code?**
No. Codo only reads your pull requests and leaves comments. Suggested fixes are
applied only if *you* click "Apply".

**What permissions does it need?**
Read access to your code and pull requests, and permission to post review
comments and a status check. Nothing more.

**Will it spam my PRs?**
No. Codo aims for a handful of high-value comments and cleans up its previous
comments when it re-reviews after new commits.

**Which languages does it support?**
Any language — Codo reviews the diff, so it works across your whole codebase.

**Can I turn it off for a repo?**
Yes — set `enabled: false` in `.aicodereview.yaml`, or remove the repo from the
GitHub App's access list.

## Support

- Questions or issues: open an issue in this repository.
- Feature requests and feedback are welcome.

---

<div align="center">
<sub>Codo is powered by Claude.</sub>
</div>
