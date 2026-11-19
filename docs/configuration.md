# Configuration

Press `e` in AIHawk to change these on the screen. AIHawk reads `aihawk.yaml` in its folder, `~/.aihawk` by default. A missing file, or a missing key, takes the default below. Restart `aihawk` after you edit the file.

Loading a resume rewrites this file, and the rewrite drops the comments you put in it, so keep your notes elsewhere.

## A trial that sends nothing

With the environment variable `AIHAWK_DRY_RUN` set to anything, an application stops once the form is filled and the send control is found: the job is recorded as blocked, with a screenshot, and nothing reaches the employer. Every step the model took on the page is written to `data/trace.log`, with the account password hidden; `AIHAWK_TRACE` set alone writes that file for real applications too. For trying a site or a plugin with a test CV.

## AI providers

The default provider is OrcaRouter, which uses the three model settings below and an OrcaRouter API key. To use an eligible ChatGPT plan, open Settings (`e`), turn on **Use ChatGPT plan**, then choose **ChatGPT account** and connect in the browser. The sign-in asks permission for AIHawk to use plan usage. After connecting, choose a model visible to that account under **ChatGPT model**. The same selected model handles resume parsing, job scoring, and form filling. AIHawk does not fall back to OrcaRouter if ChatGPT usage is unavailable or reaches its limit; it stops and tells you how to reconnect or manage usage. **Manage ChatGPT usage** opens [ChatGPT Settings](https://chatgpt.com/settings/usage).

Credentials are kept in the operating system's secure credential store. The connection is local to this AIHawk installation; it does not reuse Codex's private session. Disconnect from **ChatGPT account** in Settings. The subscription-backed integration is subject to account eligibility, model availability, and usage limits.

## macOS application browser

On macOS, application flows use a visible Google Chrome window through the pinned Microsoft Playwright MCP package. Install Node.js 20 or later (which includes `npx`) and Google Chrome. AIHawk downloads the connector with `npx` the first time it applies, so that first application needs an internet connection. Browser sign-ins persist in `~/.aihawk/browser-profile`, or in `browser-profile` inside a custom AIHawk workspace. This is regular Chrome; job sites may detect it more readily than the patched Firefox backend used on Windows and Linux. Search and scoring do not need these browser prerequisites.

## What it costs

With OrcaRouter, AIHawk counts the tokens of every model call and prices them with the gateway's own list, read once per start. The details of each application, on `s`, say what it cost. Jev's calls, which score the jobs, are not counted: they go through another endpoint and cost under a cent per hundred postings. ChatGPT plan requests do not use OrcaRouter pricing or spend endpoints; check **Manage ChatGPT usage** for account usage information.

## The accounts it made

A site that needs a candidate account, such as Workday, gets one made with your email and the `account_password` from your answers. Which sites have one is kept in the `accounts` table of `data/jobs.db`, by host, so the next form on that site signs in instead of registering again. Nothing asks you to sign in by hand.

## Your answers

What the forms are told about you beyond the CV (email, phone, location, links, and anything you add) lives in the `answers` table of `data/jobs.db`, the SQLite file in the AIHawk folder. Press `e`, then "Your answers", to see, change or add them; an answer you gave is kept when a new resume is read. `email` and `mail_provider` (gmail or outlook) are the mailbox AIHawk opens for you to sign in to, asked the first time a form needs a site's code (not before), and again from "Your mail" in Settings; a site's code can only be read from a mailbox you are signed in to. AIHawk makes an `account_password` the first time it starts and keeps it there: the one password it uses on job sites that need an account, such as Workday, so you can sign in to those sites yourself. It is kept sealed, encrypted with a key in `data/secret.key`, and shown plain under "Your answers". The `answers.yaml` of earlier versions is read once, brought into the table and renamed `answers.yaml.imported`.

| Key | Default | What it does |
|---|---|---|
| `goals` | empty | What you are looking for, in your own words. Jev reads it. |
| `database` | `https://huggingface.co/datasets/feder-cr/jobs/resolve/main/library/manifest.json` | Where the job database is: the URL of its `manifest.json`, or the path of a local one. AIHawk downloads into `data/library` the chunks the search's days need, the newest jobs first; after that, only what changed. |
| `max_age_days` | `15` | How many days back a search looks; "Posted within (days)" in Settings. AIHawk downloads only the part of the database these days need, all of it before the first search. The database holds the last 30 days, so a larger number finds nothing more. A posting its source does not date is dated by the day the database first saw it. |
| `model` | `deepseek/deepseek-v4.1-flash` | Fills the application forms. |
| `quick_model` | `google/gemini-3.5-flash` | Writes the search plan from your CV, and the other short calls of a search. |
| `decision_model` | `typesafe/jev-1.14` | Jev, which answers for each posting whether it is in line with you; how sure it is makes the score, from 1 to 5. |
| `chatgpt_host_id` | generated on first ChatGPT sign-in | Stable, opaque host identifier required for ChatGPT plan usage. AIHawk manages this value. |
| `chatgpt_email` | empty | The verified ChatGPT account label shown after connecting. AIHawk manages this value; tokens stay in the operating system's credential store. |
| `provider` | `orcarouter` | The AI provider: `orcarouter` or `chatgpt`. The ChatGPT option uses an eligible signed-in ChatGPT plan and requires a selected model. |
| `chatgpt_model` | empty | A model visible to the connected ChatGPT account. AIHawk offers the visible models after you connect. |
| `filters.include` | empty | Title words to search for. With no `plan`, each word is one title. AIHawk asks for a resume until there is a CV and at least one of these. Reading a resume writes this and `plan`, and the Settings screen does not offer them: a different search comes from a different resume. |
| `filters.exclude` | empty | Whole words: a posting whose title has one of them is left out. |
| `filters.locations` | empty | Places a posting's location must name, as whole words. |
| `filters.countries` | empty | Countries. A posting in one of them passes. With `locations`, a posting that names one of those places passes only if its country is empty or one of these. |
| `filters.cities` | empty | Cities. A posting whose location names one ranks higher; others are not left out. |
| `filters.plan` | empty | The title plan, with `nodes` and `arcs`. Each node has an `id`, a `weight` and `forms`, the wordings with their weights; a node of weight 0 only conditions the arcs. Each arc has `when` or `unless` (a node or a phrase in the title) and `then`, a list of `target` and `multiplier`; the target `*` is the whole title. Empty: the plan is `filters.include`. |
