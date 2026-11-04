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

After sending: the "Success!" shown after the CV upload is the upload, not the send. No confirmation text was recorded after a real Lever send, so read the page after submitting. Lever usually emails a confirmation with the candidate's name, the company and the posting title (documentation only). If the form is still on the page, the application was not received.
