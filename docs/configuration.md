# Configuration

Press `e` in AIHawk to change these on the screen. AIHawk reads `aihawk.yaml` in its folder, `~/.aihawk` by default. A missing file, or a missing key, takes the default below. Restart `aihawk` after you edit the file.

Loading a resume rewrites this file, and the rewrite drops the comments you put in it, so keep your notes elsewhere.

## A trial that sends nothing

With the environment variable `AIHAWK_DRY_RUN` set to anything, an application stops once the form is filled and the send control is found: the job is recorded as blocked, with a screenshot, and nothing reaches the employer. Every step the model took on the page is written to `data/trace.log`, with the account password hidden; `AIHAWK_TRACE` set alone writes that file for real applications too. For trying a site or a plugin with a test CV.

## What it costs

AIHawk counts the tokens of every model call and prices them with the gateway's own list, read once per start. A sent application shows its cost in its details, the log says what each application and each scoring cost, and Settings shows what the session has spent. The shortlist model's calls are not counted: they go through another endpoint and cost under a cent per hundred postings.

## Your answers

What the forms are told about you beyond the CV (email, phone, location, links, and anything you add) lives in the `answers` table of `data/jobs.db`, the SQLite file in the AIHawk folder. Press `e`, then "Your answers", to see, change or add them; an answer you gave is kept when a new resume is read. `email` and `mail_provider` (gmail or outlook) are the mailbox AIHawk opens for you to sign in to, asked once after the resume and again from "Your mail" in Settings; a site's code can only be read from a mailbox you are signed in to. AIHawk makes an `account_password` the first time it starts and keeps it there: the one password it uses on job sites that need an account, such as Workday, so you can sign in to those sites yourself. The `answers.yaml` of earlier versions is read once, brought into the table and renamed `answers.yaml.imported`.

| Key | Default | What it does |
|---|---|---|
| `name` | empty | Your name for this search, shown at the top of the list. |
| `goals` | empty | What you are looking for, in your own words. The decision model and the judge read it. |
| `database` | `https://github.com/feder-cr/Jobs_Applier_AI_Agent/releases/download/jobs-latest/manifest.json` | Where the job database is: the URL of its `manifest.json`, or the path of a local one. The files it names sit beside it, unless its `base_url` says otherwise. |
| `max_age_days` | `60` | Postings posted more than this many days ago are left out. A posting with no date is kept. |
| `model` | `deepseek/deepseek-v4.1-flash` | Fills the application forms. |
| `quick_model` | `google/gemini-3.5-flash` | Writes the search plan from your CV, and the other short calls of a search. |
| `judge_model` | `deepseek/deepseek-v4.1-flash` | Writes the score and the report of each shortlisted posting. |
| `decision_model` | `typesafe/jev-1.14` | Jev, which answers the six questions that shortlist the postings. |
| `filters.include` | empty | Title words to search for. With no `plan`, each word is one title. AIHawk asks for a resume until there is a CV and at least one of these. Reading a resume writes this and `plan`, and the Settings screen does not offer them: a different search comes from a different resume. |
| `filters.exclude` | empty | Whole words: a posting whose title has one of them is left out. |
| `filters.locations` | empty | Places a posting's location must name, as whole words. |
| `filters.countries` | empty | Countries. A posting in one of them passes. With `locations`, a posting that names one of those places passes only if its country is empty or one of these. |
| `filters.cities` | empty | Cities. A posting whose location names one ranks higher; others are not left out. |
| `filters.plan` | empty | The title plan, with `nodes` and `arcs`. Each node has an `id`, a `weight` and `forms`, the wordings with their weights; a node of weight 0 only conditions the arcs. Each arc has `when` or `unless` (a node or a phrase in the title) and `then`, a list of `target` and `multiplier`; the target `*` is the whole title. Empty: the plan is `filters.include`. |
