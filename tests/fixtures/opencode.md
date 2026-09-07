# OpenCode host

AgentsMD supports the OpenCode **1.18.29** CLI interface on POSIX hosts. The
adapter checks that exact version and its required flags before a model run.
Other versions need compatibility proof before this support claim expands.
Provider availability is independent of host support; select an exact
`provider/model` for each run. No model becomes a global default.

## Instructions and Skills

OpenCode composes project `AGENTS.md` found while walking up from its working
directory with global `$XDG_CONFIG_HOME/opencode/AGENTS.md`, defaulting to
`~/.config/opencode/AGENTS.md`. `CLAUDE.md` is a project fallback, and
`~/.claude/CLAUDE.md` is a global fallback when the OpenCode global file is
absent. Configured `instructions` add further sources. AgentsMD does not edit
that configuration. See the official [instruction rules](https://opencode.ai/docs/rules/).

OpenCode discovers `~/.agents/skills/<name>/SKILL.md` and loads Skills on demand.
Reuse that shared installation; this integration creates no Skill tree.
Verify available names with `opencode debug skill` in the intended repository.
The host also discovers project Skills and its own config Skill paths; avoid
installing a second copy of a shared Skill there. See official
[Skill discovery](https://opencode.ai/docs/skills/). Host-native frontmatter
differs: OpenCode ignores unrecognized fields, so user-only planning invocation
continues to depend on the canonical operating contract.

The global contract requires the full Project Direction triad before project
work. OpenCode does not run the Codex Project Direction lifecycle hook. A
fresh session must read those files and applicable project instructions. The
bounded adapter includes this obligation in its handoff prompt.

## Install, update, status and uninstall

Use `AGENTSMD_DIR` for an extracted released artifact or stable canonical
checkout of a released tag. The source must be a regular `AGENTS.md` outside
plugin caches. Release identity is verified through the distribution workflow;
the link command verifies path ownership and bytes, not GitHub release status.

```sh
"$AGENTSMD_DIR/bin/agentsmd-opencode" install --source "$AGENTSMD_DIR/AGENTS.md"
"$AGENTSMD_DIR/bin/agentsmd-opencode" status --source "$AGENTSMD_DIR/AGENTS.md"
"$AGENTSMD_DIR/bin/agentsmd-opencode" update --source "$AGENTSMD_DIR/AGENTS.md"
```

Commands emit JSON including source and target hashes when readable. Ownership
means a symlink's absolute lexical destination exactly matches the explicitly
supplied canonical source. Status distinguishes `missing`, `regular-file`,
`other-path`, `broken-link`, `divergent-link`, and `owned-link`. A broken link
also reports whether its destination is owned. Only a healthy owned link
returns status exit code zero. A regular file containing identical bytes is
still user-owned and preserved.

Install creates a missing link or verifies an already-correct link. Update can
repair a broken owned link. When an extracted release changes the source path,
identify the previous canonical release explicitly:

```sh
"$AGENTSMD_DIR/bin/agentsmd-opencode" update \
  --source "$AGENTSMD_DIR/AGENTS.md" --previous-source "$PREVIOUS_AGENTSMD_DIR/AGENTS.md"
"$AGENTSMD_DIR/bin/agentsmd-opencode" uninstall --source "$AGENTSMD_DIR/AGENTS.md"
```

Update refuses any target that is not the exact previous owned link. Uninstall
removes only the exact supplied owned link, including a broken owned link.
There is no force replacement option. Resolve an unrelated target deliberately
outside this command. Source files, shared Skills, `opencode.json`,
`opencode.jsonc`, preferences and credentials remain in place. Start a fresh
OpenCode session after a supported install or update.

## Bounded implementation run

The coordinator supplies the complete Issue scope, accepted decisions, exact
base and dependency state, and granted authority in a prompt file. Repository
root and starting HEAD must match before dispatch. The base must be an exact
available commit. The output directory must not exist.

```sh
"$AGENTSMD_DIR/bin/agentsmd-opencode" run \
  --cwd "$ISSUE_WORKSPACE" \
  --issue https://github.com/OWNER/REPO/issues/NUMBER \
  --base "$EXACT_BASE_SHA" --head "$EXACT_HEAD_SHA" \
  --model "$EXACT_PROVIDER_MODEL" --permissions configured \
  --prompt-file "$TASK_PROMPT" --output "$NEW_RECEIPT_DIRECTORY" --timeout 900
```

`configured` preserves OpenCode's configured permissions with closed stdin.
`auto` adds `--auto` only when that tool mode has been granted; explicit OpenCode
denials remain effective. Neither mode is a sandbox or a grant of production,
credential, merge, release or installation authority. Use an exclusive task
workspace and scoped host permissions. The adapter inherits the host environment
and lets OpenCode use its existing authentication; it never reads credential
files or writes configuration. See the official [CLI contract](https://opencode.ai/docs/cli/).

Each run starts a new session and writes a private directory containing raw JSON
events, stderr, extracted text and `receipt.json`. Raw model/tool output can
contain private task data, so review it before publishing. The receipt records
Issue, working directory, exact base and before/after HEAD, requested model and
permission mode, prompt hashes, version, timestamps, process exit status,
session IDs, result, working-tree status and evidence hashes. It does not dump
the environment. Timeout terminates the process group and records failure.
Malformed events, host errors, missing session/text evidence and nonzero exit
status cannot produce a candidate. A candidate is an unreviewed implementer
result. The coordinator verifies scope, model/session evidence, exact-SHA proof,
review and external authority independently.

## Proof and remaining host differences

`python3 -m unittest tests.test_opencode -v` tests isolated HOME/XDG ownership,
config and credential preservation, lifecycle transitions, explicit CLI
arguments, stale-head refusal, malformed events, errors and timeout. It uses a
fake CLI and spends no model quota. The full repository and versionctl suites
remain the deterministic release gates.

Issue #78 separately requires one requested fresh-session fixture using the
real CLI and an authorized model. Record version, selected model, global and
project instruction composition, full Project Direction hashes, shared Skill
discovery, fixture read/write result and exact HEAD. That fixture proves only
the exercised integration. User-owned behavioral Live Verification through
ordinary work on real projects remains separately pending until user evidence
exists, as defined by `AGENTS.override.md`.

OpenCode does not reproduce Codex app-native task coordination, memory,
connectors, computer/browser UI tools or lifecycle hooks. Host-native tools and
permissions remain OpenCode's responsibility; AgentsMD supplies the shared
operating contract and the narrow CLI handoff.
