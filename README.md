# AIHawk

Find the jobs that fit your CV and apply to them.

Give it your resume. It lists the jobs that fit you best, right in your terminal. You select the ones you want, and it fills each application in a visible browser window. It asks before sending anything. Needs Python 3.11 or newer.

```sh
pip install git+https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk
aihawk resume.pdf
```

```
 AIHawk  Ada Rossi · Python Engineer, Backend Developer · Milano, Italy      [judging 12/40]
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
| a | apply to the selected jobs, or the current one |
| o | open the posting in your browser |
| m | score more jobs |
| r | give a new resume |
| l | open the browser to sign in; press enter there when done |
| q | quit |

## What it does

- **Reads** your resume and writes a search plan from it, and takes your contact details.
- **Finds** postings in a job database published as a release: `jobs-latest` points at a build with 10,223,753 postings (built once by hand on 2026-11-04). The daily refresh starts once its workflow is on main. Postings older than 60 days are left out by default.
- **Judges** the postings that fit best, six questions each, with Jev through OrcaRouter.
- **Scores** each job from 1 to 5 against your resume, with its gaps.
- **Applies**: writes a CV for the job and fills the form in a visible browser through [invisible-playwright-mcp](https://github.com/feder-cr/invisible_playwright_mcp). What a form asks and your files do not say, it asks you, and remembers.

## Sending

The model fills the form and names the submit button; AIHawk clicks it only after you press `y`. As a last resort the model can run script, which could send a form without asking.

`aihawk --auto-submit` sends without asking. A wrong application sent in your name can get an account closed: read the forms first.

## Sites

Plugins cover Ashby, BambooHR, Breezy, Greenhouse, JazzHR, Lever, Paylocity, Personio, Recruitee, SmartRecruiters, Teamtailor and Workable. Other postings are listed for applying by hand.

A plugin is a Markdown file in [`src/aihawk/plugins/`](src/aihawk/plugins): a prompt that explains one site's form. Anyone can add a site with a file and no code. Your own go in `~/.aihawk/plugins/`.

## Plan and models

The search plan is an editable graph in `filters.plan` of `~/.aihawk/aihawk.yaml`; restart `aihawk` after editing it.

Models go through OrcaRouter: `model` (default `z-ai/glm-5.3-flash`) writes the CV and fills forms; `quick_model` (default `google/gemini-3.5-flash`) writes the plan; `decision_model` (default `typesafe/jev-1.14`) shortlists the jobs; `judge_model` (default `deepseek/deepseek-v4.1-flash`) scores them. Any OpenAI-compatible endpoint with tool calling and structured output can replace OrcaRouter through `base_url`.

## Thanks

The job database merges public job databases. Their sources, licences and the postings' links are in [NOTICE](NOTICE); each posting belongs to its employer. The job report's shape is adapted from [career-ops](https://github.com/santifer/career-ops) (MIT).

The former Dots project is at [feder-cr/invisible_dots](https://github.com/feder-cr/invisible_dots).

## License

[MIT](LICENSE)
