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

After sending: the "Success!" shown after the CV upload is the upload, not the send, so read the page after submitting. After a send the address moves to one ending in `/thanks` and says "Application submitted!". It can take more than a few seconds to appear. If the form is still on the page, the application was not received.
