<!--
  COMMITTED AND PUBLIC. Deliberately almost empty: AGENTS.md is the single
  source of instructions for every assistant, and Claude Code does not read
  AGENTS.md, so it is imported below. Anything true of the repo goes in
  AGENTS.md, not here. Only Claude-Code-specific behaviour belongs under the
  heading. Machine facts go in .status/local-environment.md (gitignored).
-->

@AGENTS.md

# Claude Code

`AGENTS.md`, imported above, is the whole instruction set: commands, layout, the files the pipeline works with, conventions, the rules that are easy to get wrong, consumers, and working agreements.

- `CLAUDE.local.md` (gitignored) holds notes about this machine's Claude Code setup and imports `.status/local-environment.md`, which is where machine facts live for every tool, not just this one.
- After changing either import, run `/context` and confirm the file appears under **Memory files**.
