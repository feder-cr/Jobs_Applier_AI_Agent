# AIHawk

Give it your resume, get the jobs that fit, select them and apply. Runs in your terminal, with a browser you can watch.

Needs Python 3.11 or newer.

```sh
pip install git+https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk
aihawk resume.pdf
```

```
 AIHawk  Ada Rossi · Python Engineer, Backend Developer · Milano, Italy      [scoring 12/40]
 ┌───┬───────┬───────┬──────────┬─────────────────────────┬──────────┬────────┐
 │   │ Score │ Apply │ Company  │ Role                    │ Location │ Status │
 │ ● │ 4.8   │ auto  │ Acme     │ Senior Python Engineer  │ Milan    │        │
 │   │ 4.5   │ auto  │ Beta     │ Backend Developer       │ Remote   │ sent   │
 │   │ 4.1   │ link  │ Gamma    │ Python Developer        │ Rome     │        │
 └───┴───────┴───────┴──────────┴─────────────────────────┴──────────┴────────┘
```

The first time, it asks for an [OrcaRouter](https://orcarouter.ai) key and keeps it in `~/.aihawk`. After that, `aihawk` alone reopens your list.

## Keys

| Key | What it does |
|---|---|
| space | select a job |
| enter | the score, the gaps and the reasons |
| a | send the selected jobs, or the current one, after one yes |
| s | the jobs sent, and what each page said |
| o | open the posting in your browser |
| m | score more jobs |
| r | give a new resume |
| l | open the browser to sign in; press Enter in the terminal when done |
| q | quit |

## What it does

- **Reads** your resume, writes a search plan from it, and takes your contact details from it.
- **Finds** the postings whose titles fit the plan, in a job database published as a release: 10,223,753 postings in the manifest of 2026-11-04. Postings older than 60 days are left out by default. The daily refresh is scheduled and has not run yet.
- **Shortlists** the best 100 by title: Jev, the decision model, answers six questions about each, and the best 25 are kept.
- **Scores** each shortlisted job from 1 to 5 against your resume, with its gaps.
- **Applies**: writes a CV for the job and fills the form in a visible browser through [invisible-playwright-mcp](https://github.com/feder-cr/invisible_playwright_mcp). What a form asks and your files do not say, it asks you, and remembers.

## Sending

The model fills the form and names the submit button. It is told not to send, and clicks that would send are refused. You select the jobs and press `a` and Enter: that one yes sends the selection, and AIHawk clicks the control the model named. As a last resort the model can run a script, which could send a form without asking; see [SECURITY.md](SECURITY.md). Each send keeps what the page said in `applied.csv` and `data/proofs/`; `s` lists them.

## Sites

Plugins cover Ashby, BambooHR, Breezy, Greenhouse, JazzHR, Lever, Paylocity, Personio, Recruitee, SmartRecruiters, Teamtailor and Workable. Other postings are listed for applying by hand. The Gmail and Outlook plugins take a code or a link from your mailbox, read only; they have not been tried on a real mailbox yet.

A plugin is a Markdown file in [`src/aihawk/plugins/`](src/aihawk/plugins) that explains one site's form to the model. Anyone can add a site with a file and no code: see [docs/plugins.md](docs/plugins.md). Your own go in `~/.aihawk/plugins/`.

## Plan and models

The search plan is an editable graph in `filters.plan` of `~/.aihawk/aihawk.yaml`; restart `aihawk` after editing it.

Models go through OrcaRouter: `model` (default `deepseek/deepseek-v4.1-flash`) writes the CV and fills forms; `quick_model` (default `google/gemini-3.5-flash`) writes the plan; `decision_model` (default `typesafe/jev-1.14`, which is Jev) shortlists the jobs; `judge_model` (default `deepseek/deepseek-v4.1-flash`) scores them. `base_url` can name another gateway, which must serve OpenAI's chat API and also the decision model's `/systemone` call.

## More

- The [wiki](https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk/wiki): how matching works, sites and limits.
- [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md).

The job database merges public job databases. Their sources, licences and the postings' links are in [NOTICE](NOTICE); each posting belongs to its employer. The job report's shape is adapted from [career-ops](https://github.com/santifer/career-ops) (MIT).

[MIT](LICENSE)

The former Dots project is at [feder-cr/invisible_dots](https://github.com/feder-cr/invisible_dots).
