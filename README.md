# AIHawk

Find the jobs that fit your CV and apply to them.

AIHawk searches a job database rebuilt every day from every public job database we know of, judges each job against your CV, writes a CV for the job, and fills in the application form in a visible browser. It sends nothing until you say yes.

```sh
pip install git+https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk
export ORCAROUTER_API_KEY=sk-orca-...

mkdir my-search && cd my-search
aihawk init            # writes aihawk.yaml, cv.md and answers.yaml: fill them in
aihawk scan            # searches the database; uses no model, scrapes nothing
aihawk score           # judges the new jobs against your CV and goals
aihawk list            # the best first
aihawk apply --top 5   # fills the forms and asks before each send
```

## How it works

| Step | What happens |
|---|---|
| **Find** | Queries the daily database in place, keeping the titles and locations you asked for, without downloading the whole of it. |
| **Judge** | Scores each job from 1 to 5 by reasoning about your CV against the posting, and reports the gaps. |
| **Tailor** | Reorders and rephrases your CV for the job. Every fact must already be in your CV. |
| **Apply** | Fills the form through [invisible-playwright-mcp](https://github.com/feder-cr/invisible_playwright_mcp) with what your CV and answers file say. What they do not say comes back to you as a question, never as a guess. |

The model never sends an application. It fills the form and names the submit button; AIHawk clicks it after your yes. A click by the model on anything that would send the form is refused.

`aihawk apply --auto-submit` sends without asking. An automated application in your name can be wrong, and sites can close accounts for it: read the forms first, and use it only when you accept that.

## Plugins

Each site AIHawk can apply on is a Markdown file in [`src/aihawk/plugins/`](src/aihawk/plugins): a prompt that tells the model how that site's form works.

```markdown
---
name: lever
hosts: [jobs.lever.co]
apply_path: /apply
---
The form is one page. Upload the CV to `input[name="resume"]` first...
```

To add a site, add a file. No code is needed, and your own go in a `plugins/` folder beside your `aihawk.yaml`. A plugin only explains a site: it cannot make the model send a form, write what your files do not say, or get around a captcha or a login.

## Models

AIHawk talks to one gateway, [OrcaRouter](https://orcarouter.ai), with `orcarouter/auto` as the default model. Change `model` in `aihawk.yaml`, or point `base_url` at any endpoint compatible with OpenAI's API that supports tool calling and structured output.

## Limits

- AIHawk applies where a plugin exists: Greenhouse, Lever, Ashby, Recruitee, Workable, Breezy and Paylocity. Other postings are found and listed, to apply to by hand.
- Forms behind a login or a captcha come back as blocked; AIHawk does not try to get around them.
- The tailored PDF uses a Latin font, so CVs in other scripts need your own PDF.

## Thanks

The job database is built from the public databases listed in [NOTICE](NOTICE); each posting belongs to its employer and links back to it. The scoring rubric's shape is adapted from [career-ops](https://github.com/santifer/career-ops) (MIT).

The Dots project that used to live here moved to [feder-cr/invisible_dots](https://github.com/feder-cr/invisible_dots).

## License

[MIT](LICENSE)
