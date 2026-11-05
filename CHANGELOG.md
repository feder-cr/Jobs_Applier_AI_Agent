# Changelog

## Unreleased

- Added [docs/sites.md](docs/sites.md): each site's status from real forms and what stops it; the README links to it.
- Added [docs/data-sources.md](docs/data-sources.md), each source with its licence and how to ask for its removal, and [docs/configuration.md](docs/configuration.md).
- A CV written in a script its font cannot draw is no longer uploaded with blanks: your own CV goes instead, and you are told.
- A model error that a smaller batch cannot cure stops at once and says why, instead of ranking by title almost silently.
- The job database is shown with its real date, with a warning when it is over 7 days old, and a search emptied by the age limit says so.
- The send check knows more languages' send words, and no longer refuses "Confirm email address", "Complete your profile" or "Send code".

## 1.0.0 - 2026-11-04

The first version of AIHawk in this repository. Give it a resume (PDF, Word or text) and it writes a search plan from it, lists the matching jobs from the published job database, scores the best ones against the CV through OrcaRouter, and fills their application forms in a visible browser. It sends nothing until you select the jobs and press `a` and Enter. Sites are covered by plugins: Markdown files in `src/aihawk/plugins/`, described in [docs/plugins.md](docs/plugins.md).

This repository was the earlier AIHawk, then the Dots project, and is AIHawk again: this version is a different program from both, does not read their files, and the stars date from the earlier AIHawk. Dots now lives at [feder-cr/invisible_dots](https://github.com/feder-cr/invisible_dots).
