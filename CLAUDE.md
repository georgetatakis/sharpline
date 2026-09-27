# Instructions for Claude Code — read this before doing anything else

## This file itself

This file (`CLAUDE.md`) is local-only. It must be listed in `.gitignore` and must
never be staged, committed, or pushed to GitHub. If you ever see it show up in
`git status` as untracked-but-about-to-be-added, or in a diff, stop and flag it —
it should be invisible to git entirely, not just unstaged.

## Git rules — follow exactly, no exceptions

1. **Never run `git add`, `git commit`, `git push`, or any other git command that
   changes repo state.** You may run read-only git commands (`git status`, `git diff`,
   `git log`) to check things, but you never commit or push, ever.
2. **Never add a `Co-authored-by: Claude` trailer, or any Claude/Anthropic
   attribution, to any commit message you draft.** If you write a suggested commit
   message for George to use, it must credit only georgetatakis as the author —
   no co-author line, no "Generated with Claude Code" footer, nothing.
3. **George runs every commit himself**, after reviewing the diff. Your job ends at
   writing/editing the code for the current step.

## Required output format after every step

When you finish implementing a step, your reply must end with these three sections,
in this exact order:

### What this step did
Plain description of what was built/changed and why, referencing the actual files
touched.

### What's next
One or two sentences on what the next step in the plan will do, before it's started.

### Git commands
The exact commands George should type himself to stage and commit this step —
printed as a code block, not executed by you. Example format:

```bash
git add .
git commit -m "Add Docker Compose with Postgres, FastAPI, and React skeletons"
```

Write a real, specific commit message for the actual step completed — not a
placeholder.

## If you ever violate the git rules above

If you notice you've run a git command you shouldn't have, say so immediately and
stop. Do not try to quietly fix it by running more git commands.
