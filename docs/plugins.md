# Writing a plugin

A plugin tells the model how one job site's application form works. It is a Markdown file: a front matter between two `---` lines, then the instructions in plain prose. The model reads the instructions when it fills a form on that site.

A plugin is a prompt, not code. It cannot change what AIHawk allows. The model is told not to send a form, clicks that would send it are refused, it writes nothing that the CV and the answers do not say, and it does not get past a captcha or a login. Those rules are in `src/aihawk/apply.py`, out of a plugin's reach.

## Where a plugin lives

- `src/aihawk/plugins/<name>.md`: the bundled plugins. Add yours here for a pull request.
- `plugins/<name>.md` in your AIHawk folder: yours alone. A file with the same name as a bundled one replaces it.

AIHawk reads the plugins when it starts, so restart it after you change one.

## Front matter

| Field | What it does |
|---|---|
| `name` | The name AIHawk shows while it fills the form. Default: the file name without `.md`. |
| `hosts` | The sites the plugin is for, as a list, such as `[jobs.lever.co]`. A posting matches when its host is one of these or ends with `.` and one of them, so `eu.jobs.lever.co` matches too. Required unless `when` is `always`. |
| `apply_path` | Added to the posting's path to reach the form, once: `/apply` turns `https://jobs.lever.co/acme/1` into `https://jobs.lever.co/acme/1/apply`. A link that already ends with it is left as it is. The query string is kept. Without it, the posting's own link is the form's link. |
| `when` | Only `always`. The body is then added to every form's instructions, beside the site's own plugin. Such a plugin has no `hosts` and does not make a site count as one AIHawk can apply to. |
| `reaches` | Only in a `when: always` plugin: the sites the model may visit with it, such as `[mail.google.com]`. A site plugin's `reaches` is read and ignored. |

A file that does not parse (no front matter, `hosts` missing, a `when` other than `always`, empty instructions) stops AIHawk at start with a message that names the plugin. Save the file as UTF-8. A byte-order mark at its start, as some Windows editors write, is fine: AIHawk skips it.

## The order of sections

The bundled plugins follow roughly this order, which is the one to use:

1. How the site's pages are built: one page or several, whether the form is on the posting or at `/apply`, what loads late, what to read first, what blocks the form (a closed posting, a captcha, a sign-in).
2. The basic fields, by id or by label.
3. The CV upload: which input to use, and what not to click (for example "Apply with LinkedIn" or "Autofill").
4. Questions the company adds, and the fields that are lists or menus: how to choose and how to check that the choice was taken.
5. The send control: its selector and its label.
6. `After sending:`, last.

Keep each paragraph to one subject. Name selectors in backticks and button labels in quotes, as they appear on the page.

## How to say how the site's pages are built

Start with what the model will see. Say whether the form is on the same page as the posting, and whether it needs a step first. Say what to do if the form looks empty: the bundled Workable plugin says "The form loads a moment after the page: if it looks empty, read the page again." If a posting can be closed, say what a closed posting shows and what to report.

## How to say "After sending:"

The last paragraph starts with `After sending:`. It says how the site shows that the application was received, which e-mail usually follows, and the rule that decides it:

> After sending: read the page: if the form is still there, the application was not received.

No code reads this paragraph; the model does. Say what the confirmation looks like for this site, and say that the form still being on the page means nothing was received. Do not rely on one exact phrase, because the wording is set per job board.

The model reports one of three results when it finishes a form: `ready` (every required field is filled from the CV and the answers), `needs_input` (a question for the person) or `blocked` (the form cannot be filled, for example because of a captcha). Say in the plugin when `blocked` is the right answer.

## A real example

`src/aihawk/plugins/lever.md`, as it is in this version:

```markdown
---
name: lever
hosts: [jobs.lever.co]
apply_path: /apply
---
The form is one page, under the heading "Submit your application". A closed posting shows "Sorry, we couldn't find anything here" (a 404 page): report the form as blocked.

Upload the CV first. The "ATTACH RESUME/CV" link wraps the file input `#resume-upload-input` (input[name="resume"]). Lever may fill some fields from it, so read the page again afterwards and correct anything that differs from the CV.

Fields are named: `name`, `email`, `phone`, `location`, `org` for the current company. Questions a company adds come after them, often in `name="cards[...]"` fields. A required field has a ✱ after its label.

The location field, `#location-input`, suggests places as you type: pick one of the suggestions.

Some forms show a captcha when sending: if the page asks for one, report the form as blocked.

The submit control is `#btn-submit`, "Submit application".

After sending: the "Success!" shown after the CV upload is the upload, not the send, so read the page after submitting. Lever usually emails a confirmation with the candidate's name, the company and the posting title. If the form is still on the page, the application was not received.
```

## Try a plugin without touching the repository

Use a folder of your own as the AIHawk folder:

```sh
mkdir -p ~/aihawk-trial/plugins
# write ~/aihawk-trial/plugins/mysite.md
aihawk --home ~/aihawk-trial
```

On Windows, use `%USERPROFILE%\aihawk-trial\plugins` and the same command. The `AIHAWK_HOME` environment variable does the same as `--home`. The trial folder gets its own key, CV and answers, so your real search is not affected. Delete the folder when you are done.

## Test a plugin without sending anything to an employer

AIHawk fills a form only after your yes, and that yes sends it. So do not test a plugin on a live posting unless you mean to apply. Test it in one of these ways:

- **Unit tests.** `tests/test_plugins.py` shows how a plugin is checked: `fill_form` runs against `ScriptedModel` and `ScriptedBrowser` from `tests/fakes.py`, with no network and no send. Run `pytest -m "not browser"`.
- **A local page.** `tests/test_real_browser.py` serves a form from `127.0.0.1` and runs the real browser on it. Copy a site's form structure into such a page to check the selectors, with `pytest -m browser`.
- **The batch screen.** If you select a job and press `a` by mistake, Esc at the confirmation sends nothing and says "Nothing was sent."

When you test on a real posting on purpose, say which one in the pull request, and what the form did, including whether the confirmation page showed.
