<!--
  COMMITTED AND PUBLIC. Deliberately almost empty: AGENTS.md is the single
  source of instructions for every assistant and is imported below. Anything
  true of the repo goes in AGENTS.md, not here. Machine facts go in
  .status/local-environment.md (gitignored), imported below as well; a clone
  without that file loads cleanly.
-->

@AGENTS.md

@.status/local-environment.md

# Claude Code

`AGENTS.md`, imported above, is the whole instruction set: commands, layout, the files the pipeline works with, conventions, the rules that are easy to get wrong, consumers, and working agreements. `.status/local-environment.md`, also imported, holds this machine's paths and quirks for every tool. Path-scoped rules in `.claude/rules/` load when a session touches files matching their `paths:` globs. After changing an import, run `/context` and confirm the file appears under **Memory files**.
