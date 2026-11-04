---
name: lever
hosts: [jobs.lever.co]
apply_path: /apply
---
The form is one page. Upload the CV to `input[name="resume"]` first: Lever may fill some fields from it, so read the page again afterwards and correct anything that differs from the CV.

Fields are named: `name`, `email`, `phone`, `location`, `org` for the current company. Questions a company adds come after them, often in `name="cards[...]"` fields, and a required one is marked on the page.

The current location field suggests places as you type: pick one of the suggestions.

The submit control is `#btn-submit`, "Submit application". Some forms show a captcha when sending: if the page asks for one, report the form as blocked.
