# How we build Yvoke for Claude

This repository is built mostly by Claude, with a person deciding what to build and approving what ships.
This page says how work moves from an idea to merged code, and which file holds what at each step. It
follows Anthropic's [AI-native SDLC playbook](https://academy.claude.com/courses/ai-native-sdlc-playbook):
capture intent, write requirements and design, plan before coding, and give Claude a feedback loop.

The repository is the source of truth. A decision that is not written in one of these files has not been
made.

## 1. Where things live

```text
AGENTS.md                      rules and verification commands for every agent (CLAUDE.md imports it)
.claude/                       the agent harness: settings, hooks, skills
docs/
├── sdlc.md                    this page
├── specs/                     what is built today, one file per area; updated by the PR that changes it
└── tasks/
    └── v1/                    one folder per release
        ├── intent.md          why, for whom, use cases, constraints, open questions
        ├── requirements.md    what the release must do
        ├── design.md          architecture, decisions (D-xx), notes for implementers
        ├── plan.md            the release's tasks (P0-01 …) with checkboxes
        └── P1-08/             one folder per task, created when work starts
            ├── plan.md        files, order of work, risks, proof
            └── findings.md    spikes only: what was tried and what was learned
```

Two kinds of document, kept apart on purpose:

- **Release documents** (`docs/tasks/<release>/`) describe the change we intend to make. They are written
  before the work and stay as a record afterwards.
- **Specs** (`docs/specs/`) describe the system as it is now. Nothing goes in a spec until the code for it
  is merged, so specs and code never disagree. A reader who wants to know how the plugin behaves today
  reads the specs, not the release documents.

## 2. The flow

### Step 1. Intent (once per release)

The product owner describes the problem to Claude in plain words, and they iterate until it is clear.
Claude then writes `docs/tasks/<release>/intent.md` with these sections: **Problem**, **Goal**, **Users**,
**Use cases**, **Scope**, **Constraints**, **Open questions**, plus author, status and date at the top.
The product owner corrects it and merges it. A new release (v2, v3) starts with a new folder and an
intent.

### Step 2. Requirements and design (once per release)

Claude reads the intent and writes `requirements.md` (what must be true, per surface, each line naming the
tasks that deliver it) and `design.md` (architecture, constraints, decisions, implementer notes, changes
needed in other repositories), then `plan.md` (phased tasks with IDs, owners, sizes and **Done when**).

Open questions become decisions `D-xx` in `design.md`. Claude raises them **one at a time**, each with a
short recommendation, and waits for the product owner's answer before the next. The answer and its date are
written under the decision in the same pull request.

### Step 3. Task plan (once per task)

When a task starts, Claude runs the `start-task` skill. It creates `docs/tasks/<release>/<ID>/plan.md` in
plan mode, from the release's requirements, design and `AGENTS.md`:

- **Files that change**: each file and what changes in it.
- **Order of work**: steps small enough to verify one at a time, tests first.
- **Risks**: what could break, and what was considered and rejected.
- **Proof**: the tests that show **Done when** holds, and the commands that run them.

The plan is good enough when someone who never saw the conversation could carry it out from the plan
alone.

**Who approves the plan:** the product owner approves every task's plan, whatever its size, in the task's
draft pull request before any code is written (decided 2026-10-05).

### Step 4. Build (tests first)

- Red, then green, then refactor. A test counts only once it has been seen failing.
- **A bug fix starts with a failing regression test**, committed on its own. Then `start-task` turns on the
  test freeze: it records the test's commit on the task plan's **Regression test** line, and while that plan
  is in progress a hook refuses edits to existing test files, so the fix cannot pass by weakening a test.
  The plan is committed, so the freeze holds in every session (decided 2026-10-05). If the test itself is
  wrong, Claude stops and asks.
- Claude runs the verification commands from `AGENTS.md` after each step and before saying anything is
  done, and shows their output.
- If the work has to differ from the task plan, Claude updates `plan.md` in the same commit as the change.

### Step 5. Pull request

One task, one branch, one pull request, opened as a draft as soon as there is something to see.
`finish-task` closes it out:

1. Runs every verification command and puts the output in the pull request.
2. Updates `docs/specs/` for the behaviour the task delivered.
3. Ticks the task's box in the release `plan.md` with the PR link, and corrects `requirements.md` or
   `design.md` if the work showed them wrong.
4. Sets the task plan's status to done, which turns off the test freeze, after checking that no existing
   test changed since the regression-test commit.
5. Marks the pull request ready for review once CI is green.

The product owner reviews and merges. Claude never merges.

## 3. Working in parallel

Tasks without a dependency between them (`⛔` marks in `plan.md`) can run at the same time, each in its
own Claude thread, branch and pull request. Server tasks (`server`) are done in `yvoke-dev/yvoke-web` with
the same flow.

Spikes (P0-04 to P0-08) need the Claude Desktop **Code** tab, a signed-in Claude Code and a real screen.
A cloud session cannot draw mod UI, so spikes run in a Claude Code session on a developer's machine. Their
findings go in the task folder's `findings.md`.

## 4. The feedback loop

Claude can only be trusted to finish work it can check by itself. So:

- Every check is one command with a clear pass or fail, listed in `AGENTS.md` with what a healthy run
  prints.
- CI runs the same commands on every push, on the pinned minimum Claude Code version and on the latest
  release (P0-02).
- UI is tested by mounting it on both `terminal` and `desktop` surfaces in `claude plugin test`, so most
  UI checks need no screen.
- Measure: how often a pull request is green on its first CI run, and how many review rounds it needs.
