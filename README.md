# AIHawk

Find the jobs that fit your CV and apply to them.

Give it your resume. It lists the jobs that fit you best, in your terminal. You select the ones you want, and it fills each application in a browser window you can watch. Nothing is sent until you say yes.

Python 3.11 or newer.

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
  space select · enter details · a apply · o open link · m more jobs · r new resume · l sign in · q quit
```

The first time, AIHawk asks for an [OrcaRouter](https://orcarouter.ai) key and keeps it in its folder on this computer (`~/.aihawk` by default). `ORCAROUTER_API_KEY` works too. After that, `aihawk` alone reopens your list.

## Keys

| Key | What it does |
|---|---|
| space | select a job, then move down |
| enter | the score, the gaps and the reasons |
| a | apply to the selected jobs, or to the current one |
| o | open the posting in your browser |
| m | score the next batch of jobs |
| r | give a new resume |
| l | open the browser to sign in; press enter there when done |
| q | quit |

## How it works

| Step | What happens |
|---|---|
| **Read** | The model reads your resume, writes a search plan (below) and takes your contact details from it. |
| **Find** | It searches a job index on your computer. A scheduled workflow rebuilds the index daily from public job databases, and AIHawk downloads it once a day. Postings older than 60 days are left out, unless their source gave no date. |
| **Judge** | Each job is scored from 1 to 5 against your resume, with its gaps. |
| **Apply** | The model writes a CV for the job from your resume and answers, then fills the form in a visible browser window through [invisible-playwright-mcp](https://github.com/feder-cr/invisible_playwright_mcp). What a form asks and your files do not say, it asks you, and remembers. |

Until the workflow's first run publishes a release, there are no jobs to search.

## Sending

The model never sends an application. It fills the form and names the submit button; AIHawk clicks it after you press `y`. While the model works, clicks on anything that would send the form, and the Enter key, are refused.

`aihawk --auto-submit` sends without asking. An application sent in your name can be wrong, and sites can close accounts over it: read the forms first.

## The search plan

The plan is a graph in `filters.plan` of `~/.aihawk/aihawk.yaml`. The model writes it from your resume, and you can edit it; restart `aihawk` to use the change. A node is one facet of the job, such as the role or a skill, with a weight for how telling it is and the title wordings that stand for it, each with its own weight. An arc scales a node, or the whole title (`*`), when a phrase is in the title or is not, so a neighbouring trade can be pushed down.

```yaml
filters:
  plan:
    nodes:
      - id: role
        weight: 1
        forms:
          - {text: Backend Developer, weight: 1}
          - {text: Python Developer, weight: 0.9}
    arcs:
      - when: DevOps
        then:
          - {target: "*", multiplier: 0.2}
```

## Sites

AIHawk fills forms on these sites, each described by a plugin in [`src/aihawk/plugins/`](src/aihawk/plugins): Ashby, BambooHR, Breezy, Greenhouse, JazzHR, Lever, Paylocity, Personio, Recruitee, SmartRecruiters, Teamtailor and Workable. Other postings are found and listed, to apply to by hand.

A plugin is a Markdown file. Its front matter names the site and its hosts, and its text is added to the model's instructions for that site's forms:

```markdown
---
name: lever
hosts: [jobs.lever.co]
apply_path: /apply
---
The form is one page, under the heading "Submit your application".
```

Anyone can add a site with a file, and no code. Your own plugins go in a `plugins/` folder beside `aihawk.yaml` (`~/.aihawk/plugins/` by default), and win over a bundled plugin of the same name. A plugin only explains a site: it cannot make the model send a form, write what your files do not say, or get around a captcha or a login.

The Gmail and Outlook plugins add instructions for a code or link that a form sends by email. Reading a live mailbox has not been tried.

## Models

- `model` (default `z-ai/glm-5.3-flash`): writes the CV for each job and fills the forms.
- `quick_model` (default `google/gemini-3.5-flash`): writes the search plan, rates job titles and scores jobs.
- `base_url` (default `https://api.orcarouter.ai/v1`): the gateway.

Set them in `~/.aihawk/aihawk.yaml`. Any endpoint compatible with OpenAI's API that supports tool calling and structured output can take the place of OrcaRouter.

## Limits

- Forms behind a login or a captcha come back as blocked. AIHawk does not try to get around them.
- The tailored PDF uses a Latin font, so a CV in another script needs your own PDF.

## Thanks

The job database merges public job databases. Each source, its licence and the posting links are in [NOTICE](NOTICE), and each posting belongs to its employer. The shape of the job report is adapted from [career-ops](https://github.com/santifer/career-ops) (MIT).

The Dots project that used to live in this repository is now at [feder-cr/invisible_dots](https://github.com/feder-cr/invisible_dots).

## License

[MIT](LICENSE)
