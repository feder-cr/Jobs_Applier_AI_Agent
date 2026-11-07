# Writing a plugin

A plugin tells the model how one job site's application form works. It is a Markdown file: a front matter between two `---` lines, then the instructions in plain prose. The model reads the instructions when it fills a form on that site.

A plugin is a prompt, not code: it explains the site to the model.

## Where a plugin lives

- `src/aihawk/plugins/<name>.md`: the bundled plugins. Add yours here for a pull request.
- `plugins/<name>.md` in your AIHawk folder: yours alone. A file with the same name as a bundled one replaces it.

AIHawk reads the plugins when it starts, so restart it after you change one.

## Front matter

| Field | What it does |
|---|---|
| `name` | The name AIHawk shows while it fills the form. Default: the file name without `.md`. |
| `hosts` | The sites the plugin is for, as a list, such as `[jobs.lever.co]`. A posting matches when its host is one of these or ends with `.` and one of them, so `jobs.eu.lever.co` matches too. Required unless `when` is `always`. |
| `apply_path` | Added to the posting's path to reach the form, once: `/apply` turns `https://jobs.lever.co/acme/1` into `https://jobs.lever.co/acme/1/apply`. A link that already ends with it is left as it is. The query string is kept. Without it, the posting's own link is the form's link. |
| `when` | Only `always`. The body is then added to every form's instructions, beside the site's own plugin. Such a plugin has no `hosts` and does not make a site count as one AIHawk can apply to. Three are bundled: `gmail.md` and `outlook.md` for a code a site sends by email, and `menus.md`, which tells the model how each kind of list is worked on any site. Say in a site's plugin only what differs from those, and never repeat how a list is worked or where to look in a mailbox. |

A file that does not parse (no front matter, `hosts` missing, a `when` other than `always`, empty instructions) stops AIHawk at start with a message that names the plugin. Save the file as UTF-8. A byte-order mark at its start, as some Windows editors write, is fine: AIHawk skips it.

## The order of sections

The bundled plugins follow roughly this order, which is the one to use:

1. How the site's pages are built: one page or several, whether the form is on the posting or at `/apply`, what loads late, what to read first, what blocks the form (a closed posting, a captcha, a sign-in).
2. The basic fields, by id or by label.
3. The CV upload: which input to use, and what not to click (for example "Apply with LinkedIn" or "Autofill").
4. Questions the company adds, and the lists that behave in a way `menus.md` does not cover.
5. The send control: its selector and its label.
6. `After sending:`, last.

Keep each paragraph to one subject. Name selectors in backticks and button labels in quotes, as they appear on the page.

## How to say how the site's pages are built

Start with what the model will see. Say whether the form is on the same page as the posting, and whether it needs a step first. Say what to do if the form looks empty: the bundled Workable plugin says "The form loads a moment after the page: if it looks empty, read the page again." If a posting can be closed, say what a closed posting shows and what to report.

## How to say "After sending:"

The last paragraph starts with `After sending:`. It says how the site shows that the application was received, which e-mail usually follows, and the rule that decides it:

> After sending: read the page: if the form is still there, the application was not received.

No code reads this paragraph; the model does. Say what the confirmation looks like for this site, and say that the form still being on the page means nothing was received. Do not rely on one exact phrase, because the wording is set per job board.

The model reports one of two results when it finishes a form: `ready`, every required field filled, or `blocked`. Status `ready` needs the send control's selector, so always name that control. The rules every form gets already cover a captcha puzzle, a login wall, a closed posting and a required fact about the candidate that the CV does not give, so say `blocked` only for what is this site's own.

## When the model is stuck

A plugin earns its keep in the moments the form does not behave. The model is told, for every site, never to repeat an action that left the page as it was; the plugin says what to do instead on this site. Write it as the failure the model will see, then the way out, and say where it was seen. What real forms have shown so far:

- A click "not actionable" or "would have landed elsewhere": something covers the control. Name the cover and how to dismiss it (a cookie banner: the "Decline all" button inside `div[aria-label='Cookie Consent']` on Workable).
- browser_type refused with "not an input": the id sits on a wrapper. Name the control itself (`input#question_<uuid>` or `textarea#question_<uuid>` on SmartRecruiters).
- An upload refused, "the click landed on an icon": name the input that took the file on a real form (`#description_input_<id>` on Workable).
- A key that scrolls nothing: the form scrolls inside its own container. Say to reach a field with `scrollIntoView()` through browser_evaluate (SmartRecruiters), not with PageDown.
- A page that redraws itself after every click without changing address (Workday): say to read the page again after each one.
- A code or a link sent by email: say what this site's message looks like, its sender and its subject, if you have seen one. Where to look for it is the mailbox plugins' business, and the model's.

Give the cost when you know it: "seen on a real form, 40 steps lost" tells the model how much the shortcut is worth.

## Menus and dropdowns

`menus.md` reaches the model on every form. It names the five kinds of list, the test that tells them apart, how to check that a choice was taken, and the two ways past a click that does not land. A site's plugin adds only what is that site's own: an id pattern such as Paylocity's `#<id>__listbox__option__N`, or a list that hides its rows in a shadow root, as SmartRecruiters does.

## What belongs in a plugin

Write only what the model cannot work out for itself. A fact it cannot derive stays: where a control is, what it is called, that a key is refused, that an id is a uuid, that a button has no id at all. A decision it can make by reading the page goes: which field to fill first, which search to try, how many times to read the page. Writing those down pins it to one road even when the road is wrong, fills the prompt at every step, and stops it thinking.

Belongs: "Do not click "Apply with LinkedIn"; the CV goes in `#resume-upload-input`." It names a trap and a selector the page does not show.

Does not belong: "Fill the name field with the candidate's name." The model reads the label and knows.

Two things are not road but boundary, and they stay. What the model must not do, because a real person's accounts and name are at stake. And when to stop, because steps cost money it cannot see.

## What the code already decides

- A form has no limit on steps, and every step costs. A shortcut that saves steps is worth a sentence, with its cost: "seen on a real form, 40 steps lost".
- A form may reach a mailbox twice. The third arrival ends it. Do not promise the model more looks than that.
- Every form already gets these rules, so do not write them again: never sign in or register through Google, LinkedIn or another service; stay on the form's own site and the mailbox; a closed posting is reported, not hunted elsewhere; a fact about the candidate that neither the CV nor the answers give is never written, and a required field asking for one ends the form.
- The plugins with `when: always`, `menus.md`, `gmail.md` and `outlook.md`, are added to every form. A site plugin that contradicts them leaves the model with two answers.

## A real example

`src/aihawk/plugins/lever.md`, as it is in this version:

```markdown
---
name: lever
hosts: [jobs.lever.co, jobs.eu.lever.co]
apply_path: /apply
---
The form is one page, under the heading "Submit your application". A closed posting shows "Sorry, we couldn't find anything here" (a 404 page): report the form as blocked.

Upload the CV first. The "ATTACH RESUME/CV" link wraps the file input `#resume-upload-input` (`input[name="resume"]`). Lever may fill some fields from it, so read the page again afterwards and correct anything that differs from the CV.

Fields are named: `name`, `email`, `phone`, `location`, `org` for the current company. Questions a company adds come after them, often in `name="cards[...]"` fields. A required field has a ✱ after its label.

The location field, `#location-input`, whose suggestions are `#location-0`, `#location-1` and so on, suggests places as you type: pick one of the suggestions. The field may then show it shortened, such as "Milan, ITA", which is correct.

Some forms show a captcha when sending, from an hCaptcha widget with no size that is already on the page. Tick the box once and read the page. A puzzle instead of a box ends the form: call finish_form with status blocked and say the captcha became a puzzle.

The submit control is `#btn-submit`, "Submit application".

After sending: the "Success!" shown after the CV upload is the upload, not the send, so read the page after submitting. After a send the address moves to one ending in `/thanks` and says "Application submitted!". It can take more than a few seconds, so read the page once more before you conclude anything from a page that still shows the form. Lever usually emails a confirmation with the candidate's name, the company and the posting title. If the form is still on the page, the application was not received.
```

## Try a plugin without touching the repository

Put your file in `~/.aihawk/plugins/` (on Windows, `%USERPROFILE%\.aihawk\plugins`), and restart AIHawk. A file with the name of a bundled plugin replaces it.

## Test a plugin without sending anything to an employer

AIHawk fills a form only after your yes, and that yes sends it. So do not test a plugin on a live posting unless you mean to apply. Test it in one of these ways:

- **A dry run on a real posting.** Set `AIHAWK_DRY_RUN` in the environment. The form is filled and the send control is named, and nothing is sent: the job is recorded as blocked, with a screenshot, and every step the model took goes to `data/trace.log`.
- **Unit tests.** `tests/test_plugins.py` shows how a plugin is checked: `fill_form` runs against `ScriptedModel` and `ScriptedBrowser` from `tests/fakes.py`, with no network and no send. Run `pytest -m "not browser"`.
- **A local page.** `tests/test_real_browser.py` serves a form from `127.0.0.1` and runs the real browser on it. Copy a site's form structure into such a page to check the selectors, with `pytest -m browser`.
- **The batch screen.** If you select a job and press `a` by mistake, Esc at the confirmation sends nothing and says "Nothing was sent."

When you test on a real posting on purpose, say which one in the pull request, and what the form did, including whether the confirmation page showed.
