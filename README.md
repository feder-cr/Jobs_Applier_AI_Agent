# AIHawk

Give it your resume, get the jobs that fit, select them and apply. Runs in your terminal, with a browser you can watch. Needs Python 3.11 or newer.

```sh
pipx install git+https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk
```

Or `uv tool install git+https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk`. Or, in a virtual environment (on Windows, `aihawk-env\Scripts\pip` and `aihawk-env\Scripts\aihawk`):

```sh
python -m venv aihawk-env
aihawk-env/bin/pip install git+https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk
aihawk-env/bin/aihawk resume.pdf
```

The resume can be anywhere on your disk: a PDF, a `.docx` or a text file. A scanned PDF has no text to read; export it with text, or save it as `.docx`. The list below is a mockup with invented data, not a capture.

```
 AIHawk  Ada Rossi · Python Engineer, Backend Developer · Milano, Italy      [scoring 12/40]
 ┌───┬───────┬───────┬──────────┬─────────────────────────┬──────────┬────────┐
 │   │ Score │ Apply │ Company  │ Role                    │ Location │ Status │
 │ ● │ 4.8   │ auto  │ Acme     │ Senior Python Engineer  │ Milan    │        │
 │   │ 4.5   │ auto  │ Beta     │ Backend Developer       │ Remote   │ sent   │
 │   │ 4.1   │ link  │ Gamma    │ Python Developer        │ Rome     │        │
 └───┴───────┴───────┴──────────┴─────────────────────────┴──────────┴────────┘
```

The first time, AIHawk asks for an [OrcaRouter](https://orcarouter.ai) key and keeps it in `~/.aihawk/key`; after that, `aihawk` alone reopens your list. To change the key, edit or delete that file, or set `ORCAROUTER_API_KEY`, which wins over the file. If OrcaRouter refuses a key from the file, AIHawk forgets it and asks again.

The first search with each new job database downloads its job index, 672 MB, into `~/.aihawk/data/index`; a search reads it on your computer. The descriptions of the jobs AIHawk judges are read from the release over the internet.

**Your data.** Your CV, your answers, the postings' texts and the pages' contents, screenshots included, go to the model gateway OrcaRouter and its providers; an application goes to the employer's site. The rest stays in `~/.aihawk`: the CV, the answers, the key (`key`), the proofs (`data/proofs/`), `applied.csv`, the job index and the browser profile with your sign-ins. You need a paid OrcaRouter key; a form costs cents in model calls.

**Responsibility.** Applications go out in your name, and you answer for what is sent. Job sites have their own terms, and some do not allow automated applications: read them before you select their jobs. AIHawk asks once, with a yes, before it sends the selection; as a last resort the model can run a script in the page, which could send a form without asking (see [SECURITY.md](SECURITY.md)).

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
| q | quit (Ctrl+C quits too) |

## What it does

- **Reads** your resume, writes a search plan from it, and takes your contact details from it.
- **Finds** the postings whose titles fit the plan, in a job database published as a release: 10,223,753 postings in the manifest of 2026-11-04, built once by hand. Postings older than 60 days are left out by default. The daily rebuild, [`jobs-database.yml`](.github/workflows/jobs-database.yml), is on main and has never run; it needs GitHub Actions working.
- **Shortlists** the 100 postings with the best title fit: Jev, the decision model, answers six questions about each, and the best 25 are kept.
- **Scores** each shortlisted job from 1 to 5 against your resume, with its gaps.
- **Applies**: writes a CV for the job and fills the form in a visible browser through [invisible-playwright-mcp](https://github.com/feder-cr/invisible_playwright_mcp). What a form asks and your files do not say, it asks you, and remembers.

## Sending

The model fills the form and names the submit button; it is told not to send, and clicks that would send are refused. Your `a` and Enter is the one yes: AIHawk clicks the control the model named. Each send keeps what the page said in `applied.csv` and `data/proofs/`; `s` lists them.

## Sites

Sites vary a lot: Greenhouse and Ashby have one confirmed real send each, the others fill forms without sending, stop at a captcha or at questions, or need an account. [docs/sites.md](docs/sites.md) gives each site's status and what stops it. Other postings are listed for applying by hand. The Gmail and Outlook plugins take a code or a link from your mailbox. They tell the model to read only, which is an instruction, not a guarantee: the code refuses only clicks that look like sending, Enter, and visits to sites other than the form's and the mailbox's. A click that deletes or archives mail is not refused. They have not been tried on a real mailbox yet.

A plugin is a Markdown file in [`src/aihawk/plugins/`](src/aihawk/plugins) that explains one site's form to the model. Add a site with a file and no code: see [docs/plugins.md](docs/plugins.md). Your own go in `~/.aihawk/plugins/`.

## Plan and models

Models go through OrcaRouter: `model` (default `deepseek/deepseek-v4.1-flash`) writes the CV and fills forms; `quick_model` (default `google/gemini-3.5-flash`) writes the plan; `decision_model` (default `typesafe/jev-1.14`, which is Jev) shortlists the jobs; `judge_model` (default `deepseek/deepseek-v4.1-flash`) scores them. `base_url` can name another gateway; it must serve OpenAI's chat API and the decision model's own endpoint, `/systemone`.

- **Update:** `pipx upgrade aihawk`, or `uv tool upgrade aihawk`.
- **Uninstall:** `pipx uninstall aihawk` (or `uv tool uninstall aihawk`), then delete `~/.aihawk`, which holds your CV, key and sign-ins.
- **The search:** the plan is `filters.plan` in `~/.aihawk/aihawk.yaml`; restart `aihawk` after editing it.

More: the [wiki](https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk/wiki), [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md). The job database merges public sources, whose licences and posting links are in [NOTICE](NOTICE); each posting belongs to its employer. The report's shape is adapted from [career-ops](https://github.com/santifer/career-ops) (MIT).

[MIT](LICENSE). The former Dots project is at [feder-cr/invisible_dots](https://github.com/feder-cr/invisible_dots).
