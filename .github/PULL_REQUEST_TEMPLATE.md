## What this changes and why

## How it was tested

- [ ] `ruff check .`
- [ ] `pytest -m "not browser"`
- [ ] `pytest -m browser`, if the change touches the browser, the forms or `apply.py`
- [ ] For a plugin: tried on a real posting's form. Say which site, and what the form did, including whether the confirmation showed.

## Checks

- [ ] No personal data in the change: no CV, name, e-mail address, phone number, sign-in or key. Fixtures are invented.
- [ ] Nothing here lets the model send a form or an e-mail without the person's yes, or weakens a rule in CONTRIBUTING.md.
