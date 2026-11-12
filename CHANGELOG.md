# Changelog

## Unreleased

- A new OrcaRouter account has no credit, and the first thing AIHawk asks of it, reading the resume, was refused with "Something failed: Error code: 402". The key is now checked for credit when it is entered, and AIHawk says so before the resume, with a key that opens the site and one that checks again. When a request is refused for the account or the key, the screen says which it is (no credit, the key's own limit, its spending limit, a model it may not use) and what to press; the resume is remembered, and a key is never forgotten for it.
- The jobs show up as they come: the first chunk is searched and read as soon as it is here, and the list grows after each chunk, with the rows you are reading held in place and the jobs you selected kept. The wait line counts megabytes and says about how long is left.
- The key is tried when you paste it, and a key OrcaRouter does not accept is asked for again; Ctrl+C copies selected text instead of leaving, and Ctrl+O at the key question opens the address. Finished credit stops the scoring and the sending with a plain line, and no job is set aside for it.
- A form that gets no answer from the AI service waits 20 to 60 seconds and goes on, instead of failing after 14. The browser's download is said on the screen and starts in the background once the jobs are all here. On Linux without a desktop or the browser's libraries, an application says so at once.
- An unexpected error shows one plain line and writes its details to `errors.log` in the data folder, keys masked.
- The install needs no git: the one-line command brings AIHawk's code as an archive, 7 MB where the clone of its repository was 180, and the first start is about a minute shorter. A start with no connection opens the copy already on the computer.
- A request the AI service does not answer (no connection, too busy, down) no longer sets the jobs aside for good: they are asked again after a pause, and after four tries the screen says to press the right arrow. Jobs an earlier version set aside this way are read again. A search that finds nothing says so, an empty list says which key comes next, and a new search lists its own jobs only.
- Sending says which job it is on, ends with one summary line, and marks each job in the list: ✓ sent, ? sent but not confirmed, ✗ not sent, with the reason in plain words. Quitting while applications are sent asks first. The key, resume, mail and send questions say what they are for, errors drop their technical tails, and the log shows three lines.
- AIHawk tells its makers, anonymously, how it is used: how a start went and where it stopped, how many jobs a search found, and how each application ended, with the tokens each model used and no price. Nothing about you or the jobs goes with it, and a dry run is not counted. The README lists every event. The badge counts the applications sent from these events.
- Jev alone scores the jobs: for each posting, whether it is in line with the resume, and how sure the answer is is the score. The list comes in pages of 50, the next one read while you read, and a posting Jev gave no answer for is set aside, not asked again and again.
- The job database comes from Hugging Face, in chunks of complete postings, newest first. A search looks back 15 days by default (`max_age_days`), only the chunks those days need are downloaded, all before the first search, and after that only what changed.

- Space and Enter both select a job, and `i` opens it: its role, company, place and the posting's own text, as paragraphs, with nothing else. `s`, the applications, is shown at the bottom; an application's details say how it went in plain words, and the models are no longer in the settings.
- The one-command install runs on Python 3.14: on 3.15 PyYAML has no Windows build yet, and the install stopped asking for a C++ compiler.

- Added [docs/sites.md](docs/sites.md): each site's status from real forms and what stops it; the README links to it.
- Added [docs/data-sources.md](docs/data-sources.md), each source with its licence and how to ask for its removal, and [docs/configuration.md](docs/configuration.md).
- A model error that a smaller batch cannot cure stops at once and says why, instead of ranking by title almost silently.
- The screen shows how many jobs the whole database holds.
- After a send, the page is read once it has changed and stopped changing, up to 30 seconds, and the verdict sees its address: a slow confirmation page is no longer read as the old form.
- A posting whose title or company looks like screen markup, such as `[/x]`, no longer stops the screen.
- A database of your own, named by `database` in `aihawk.yaml`, can no longer make AIHawk read a file on your computer: a posting whose address is not `http` or `https` is left out of a search. The published database already left those out.
- A full disk while the job database downloads says the disk is full.
- A job whose source published no text is scored on the text of its own page where that can be read, which also finds postings that have closed.
- The README shows real captures of the screen.
- Sending says, per job, how it went, and the screen says what to do next. The list fits an 80-column terminal, the job download shows its progress, and a job's details can select it or open it.
- One command starts AIHawk, and everything else is on the screen: `e` opens the settings.
- A form no longer stops to ask. The model fills every required field from what the CV says and implies, and a written field it has nothing for says to see the attached CV.
- The checks on the model's calls are gone, for far less code: it is told not to send, and nothing holds it to that. See SECURITY.md.
- Less to carry: your own CV goes with every form, in place of one written for each job, and there is one gateway, one key variable and one folder.
- A form no longer runs out of steps pressing a key that scrolls nothing: the model is told not to repeat what changed nothing.
- What goes wrong in the browser now reaches the screen as itself, a refused key included, in place of "unhandled errors in a TaskGroup". A page that says nothing yet after the send is read again when it changes, and a click that breaks is never called failed, because it may have gone.
- Every posting is tried. A plugin helps the model where there is one, and there is no list of sites AIHawk will not touch.
- A setting of the wrong type in `aihawk.yaml` is one plain line instead of a traceback.

## 1.0.0 - 2026-11-04

The first version of AIHawk in this repository. Give it a resume (PDF, Word or text) and it writes a search plan from it, lists the matching jobs from the published job database, scores the best ones against the CV through OrcaRouter, and fills their application forms in a visible browser. It sends nothing until you select the jobs and press `a` and Enter. Sites are covered by plugins: Markdown files in `src/aihawk/plugins/`, described in [docs/plugins.md](docs/plugins.md).

This repository was the earlier AIHawk, then the Dots project, and is AIHawk again: this version is a different program from both, does not read their files, and the stars date from the earlier AIHawk. Dots now lives at [feder-cr/invisible_dots](https://github.com/feder-cr/invisible_dots).
