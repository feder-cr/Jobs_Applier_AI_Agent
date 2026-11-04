# AIHawk

Find the jobs that fit your CV and apply to them.

Give it your resume. It shows the jobs that fit you best, right in your terminal; select the ones you want and apply.

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
  space select · enter details · a apply · o open link · m more jobs · r new resume · q quit
```

The first time, it asks for an [OrcaRouter](https://orcarouter.ai) key. After that, `aihawk` alone reopens your list.

## How it works

| Step | What happens |
|---|---|
| **Read** | The model reads your resume and works out which roles and places to search, and your contact details. |
| **Find** | It searches a database rebuilt every day from public job databases. You scrape nothing: a light index is downloaded once a day and searched on your computer. |
| **Judge** | Each job is scored from 1 to 5 against your resume, with its gaps. Press enter to see why. |
| **Apply** | It writes a CV for the job, every fact from yours, and fills the form in a visible browser through [invisible-playwright-mcp](https://github.com/feder-cr/invisible_playwright_mcp). What a form asks and it does not know, it asks you, and remembers. |

The model never sends an application. It fills the form and names the submit button; AIHawk clicks it after you press `y`. A click by the model on anything that would send the form is refused.

`aihawk --auto-submit` sends without asking. An automated application in your name can be wrong, and sites can close accounts for it: read the forms first, and use it only when you accept that.

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

To add a site, add a file. No code is needed, and your own go in `~/.aihawk/plugins/`. A plugin only explains a site: it cannot make the model send a form, write what your files do not say, or get around a captcha or a login.

## Models

AIHawk talks to one gateway, [OrcaRouter](https://orcarouter.ai), with `orcarouter/auto` as the default model. Change `model` in `~/.aihawk/aihawk.yaml`, or point `base_url` at any endpoint compatible with OpenAI's API that supports tool calling and structured output.

## Limits

- AIHawk applies where a plugin exists: Greenhouse, Lever, Ashby, Recruitee, Workable, Breezy and Paylocity. Other postings are found and listed, to apply to by hand.
- Forms behind a login or a captcha come back as blocked; AIHawk does not try to get around them.
- The tailored PDF uses a Latin font, so CVs in other scripts need your own PDF.

## Thanks

The job database is built from the public databases listed in [NOTICE](NOTICE); each posting belongs to its employer and links back to it. The scoring rubric's shape is adapted from [career-ops](https://github.com/santifer/career-ops) (MIT).

The Dots project that used to live here moved to [feder-cr/invisible_dots](https://github.com/feder-cr/invisible_dots).

## License

[MIT](LICENSE)
