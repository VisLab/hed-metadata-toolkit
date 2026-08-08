# hed-metadata-toolkit

**`AGENTS.md` at the repository root is the instruction set for this project. Read it before answering, and follow it.** It covers the commands, the layout, the files the pipeline reads and writes, the conventions, the rules that are easy to get wrong, the consumer repositories, and the working agreements.

This file is a pointer and duplicates nothing. One source, several pointers: a rule stated in two files is a rule that will disagree with itself.

Machine-specific facts - interpreter, local paths, the shared cache location - are in `.status/local-environment.md`. That file is gitignored, so it is absent from clones and from CI; read it when it is there and ignore its absence when it is not. No committed file in this repository may contain a local path or a drive letter: the repo is public.
