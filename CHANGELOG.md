# Changelog

## Unreleased

- Added [docs/sites.md](docs/sites.md): each site's status from real forms and what stops it; the README links to it.
- Added [docs/data-sources.md](docs/data-sources.md), each source with its licence and how to ask for its removal, and [docs/configuration.md](docs/configuration.md).
- A CV written in a script its font cannot draw is no longer uploaded with blanks: your own CV goes instead, and you are told.
- A model error that a smaller batch cannot cure stops at once and says why, instead of ranking by title almost silently.
- The job database is shown with its real date, with a warning when it is over 7 days old, and a search emptied by the age limit says so.
- After a new resume is loaded, no CV written from the old one is sent. Jobs already sent keep theirs.
- A posting the judge could not read is no longer passed over for good: a later search judges it.
- After a send, the page is read once it has changed and stopped changing, up to 30 seconds, and the verdict sees its address: a slow confirmation page is no longer judged on the old form.
- A posting whose title or company looks like screen markup, such as `[/x]`, no longer stops the screen.
- A CV that cannot be written fails that job alone, and the others in the selection still go.
- A database of your own, named by `database` in `aihawk.yaml`, can no longer make AIHawk read a file on your computer: a posting whose address is not `http` or `https` is left out of a search. The published database already left those out.
- A full disk while the job index downloads says the disk is full.
- A job whose source published no text is scored on the text of its own page where that can be read, which also finds postings that have closed.
- The README shows real captures of the screen.
- Sending says, per job, how it went, and the screen says what to do next. The list fits an 80-column terminal, the index download shows its progress, and a job's details can select it or open it.
- One command starts AIHawk, and everything else is on the screen: `e` opens the settings.
- A form no longer stops to ask. The model fills every required field from what the CV says and implies, and a written field it has nothing for says to see the attached CV.
- The checks on the model's calls are gone, for far less code: it is told not to send, and nothing holds it to that. See SECURITY.md.
- A setting of the wrong type in `aihawk.yaml` is one plain line instead of a traceback.

## 1.0.0 - 2026-11-04

The first version of AIHawk in this repository. Give it a resume (PDF, Word or text) and it writes a search plan from it, lists the matching jobs from the published job database, scores the best ones against the CV through OrcaRouter, and fills their application forms in a visible browser. It sends nothing until you select the jobs and press `a` and Enter. Sites are covered by plugins: Markdown files in `src/aihawk/plugins/`, described in [docs/plugins.md](docs/plugins.md).

This repository was the earlier AIHawk, then the Dots project, and is AIHawk again: this version is a different program from both, does not read their files, and the stars date from the earlier AIHawk. Dots now lives at [feder-cr/invisible_dots](https://github.com/feder-cr/invisible_dots).
